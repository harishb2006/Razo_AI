from datetime import datetime, timezone

from app.db.documents import Cart, CartItem, Product, Session
from app.errors import RazoError, product_not_found


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def session_not_found(session_id: str) -> RazoError:
    return RazoError("SESSION_NOT_FOUND", 404, "I couldn't find that session.", detail={"session_id": session_id})


def out_of_stock(sku: str, available: int) -> RazoError:
    return RazoError(
        "OUT_OF_STOCK", 409, "That's out of stock — try a smaller quantity.",
        detail={"sku": sku, "available": available},
    )


def _reprice(items: list[CartItem], version: int) -> Cart:
    subtotal = sum(i.line_total_paise for i in items)
    return Cart(
        version=version + 1, state="open", items=items,
        subtotal_paise=subtotal, total_paise=subtotal, currency="INR", updated_at=_now(),
    )


class CartService:
    """Every mutation re-reads price and version from `products` — the tool
    signature that calls this never carries a price argument, so there is no
    code path for the model to invent one (tenet T2)."""

    async def _load_session(self, session_id: str) -> Session:
        session = await Session.get(session_id)
        if session is None:
            raise session_not_found(session_id)
        return session

    async def _load_open_session(self, session_id: str) -> Session:
        session = await self._load_session(session_id)
        if session.cart.state != "open":
            raise RazoError(
                "CART_LOCKED", 409,
                "This cart is being checked out and can't be changed right now.",
                detail={"cart_state": session.cart.state},
            )
        return session

    async def add(self, session_id: str, sku: str, qty: int) -> dict:
        for _ in range(3):
            session = await self._load_open_session(session_id)
            product = await Product.get(sku)
            if product is None or not product.active:
                raise product_not_found(sku)

            existing_qty = next((i.qty for i in session.cart.items if i.sku == sku), 0)
            new_qty = existing_qty + qty
            available = product.stock.available - product.stock.reserved
            if available < new_qty:
                raise out_of_stock(sku, available)

            items = [i for i in session.cart.items if i.sku != sku]
            items.append(CartItem(
                sku=sku, qty=new_qty, unit_price_paise=product.price_paise,
                product_version=product.version, category=product.category,
                line_total_paise=product.price_paise * new_qty,
            ))
            cart = _reprice(items, session.cart.version)

            modified = await self._apply(session_id, session.cart.version, cart)
            if modified:
                return cart.model_dump()
        raise RazoError("WRITE_CONFLICT", 409, "One moment, please try again.", retryable=True)

    async def update_qty(self, session_id: str, sku: str, qty: int) -> dict:
        for _ in range(3):
            session = await self._load_open_session(session_id)
            items = [i for i in session.cart.items if i.sku != sku]

            if qty > 0:
                product = await Product.get(sku)
                if product is None or not product.active:
                    raise product_not_found(sku)
                available = product.stock.available - product.stock.reserved
                if available < qty:
                    raise out_of_stock(sku, available)
                items.append(CartItem(
                    sku=sku, qty=qty, unit_price_paise=product.price_paise,
                    product_version=product.version, category=product.category,
                    line_total_paise=product.price_paise * qty,
                ))

            cart = _reprice(items, session.cart.version)
            modified = await self._apply(session_id, session.cart.version, cart)
            if modified:
                return cart.model_dump()
        raise RazoError("WRITE_CONFLICT", 409, "One moment, please try again.", retryable=True)

    async def get(self, session_id: str) -> dict:
        session = await self._load_session(session_id)
        return session.cart.model_dump()

    @staticmethod
    async def _apply(session_id: str, expected_version: int, cart: Cart) -> bool:
        """Guards on `cart.state` as well as the version: once checkout has
        locked a cart, no tool can mutate it out from under the verdict it
        was evaluated against."""
        result = await Session.get_motor_collection().update_one(
            {"_id": session_id, "cart.version": expected_version, "cart.state": "open"},
            {"$set": {"cart": cart.model_dump()}},
        )
        return result.modified_count == 1


cart_service = CartService()
