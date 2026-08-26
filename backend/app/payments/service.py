import hashlib
from datetime import datetime, timezone

from pymongo.errors import DuplicateKeyError
from ulid import ULID

from app.audit.service import audit, audit_safe
from app.config import settings
from app.db.documents import Order, Product, Session
from app.domain.intent import OrderIntent
from app.domain.money import inr
from app.errors import RazoError
from app.payments.razorpay_client import get_razorpay_client
from app.policy import verdict as verdict_signing
from app.policy.types import Verdict
from app.services.policy_service import cart_to_intent


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _idempotency_key(session_id: str, cart_version: int, intent_hash: str) -> str:
    return hashlib.sha256(f"{session_id}|{cart_version}|{intent_hash}".encode()).hexdigest()


def order_view(order: Order) -> dict:
    return {
        "order_id": order.id,
        "state": order.state,
        "amount_paise": order.amount_paise,
        "currency": order.currency,
        "payment_link_url": order.payment_link_url,
        "razorpay_order_id": order.razorpay_order_id,
    }


async def _release_stock(reserved: list[tuple[str, int]]) -> None:
    for sku, qty in reserved:
        await Product.get_motor_collection().update_one({"_id": sku}, {"$inc": {"stock.reserved": -qty}})


async def _reserve_stock(lines) -> list[tuple[str, int]]:
    """Atomic conditional $inc per line, mirroring CartService's guard —
    two concurrent checkouts cannot both reserve the last unit. Rolls back
    whatever it already reserved if a later line fails."""
    reserved: list[tuple[str, int]] = []
    try:
        for line in lines:
            result = await Product.get_motor_collection().update_one(
                {
                    "_id": line.sku, "active": True,
                    "$expr": {"$gte": [{"$subtract": ["$stock.available", "$stock.reserved"]}, line.qty]},
                },
                {"$inc": {"stock.reserved": line.qty}},
            )
            if result.modified_count == 0:
                raise RazoError(
                    "OUT_OF_STOCK", 409, f"{line.sku} is no longer available in that quantity.",
                    detail={"sku": line.sku},
                )
            reserved.append((line.sku, line.qty))
    except RazoError:
        await _release_stock(reserved)
        raise
    return reserved


class PaymentService:
    """The only route from a verdict to real money (tenet T1). Every
    precondition below raises rather than proceeding — there is no partial
    trust of a verdict."""

    async def execute(self, session: Session, verdict: Verdict) -> dict:
        if verdict.decision != "ALLOW" or verdict.token is None:
            raise RazoError("VERDICT_INVALID", 500, "Internal error: no valid approval to act on.")

        now = datetime.now(timezone.utc)
        if not verdict_signing.verify(
            settings.verdict_signing_key or settings.api_key, verdict.token, verdict.intent_hash,
            verdict.evaluation_id, now,
        ):
            raise RazoError("VERDICT_INVALID", 500, "The approval token is invalid or has expired.")

        # Re-derive the intent from the *live* session document — the token
        # was signed against a cart that may have been mutated since.
        live_session = await Session.get(session.id)
        if live_session is None:
            raise RazoError("SESSION_NOT_FOUND", 404, "I couldn't find that session.")
        live_intent: OrderIntent = await cart_to_intent(live_session)
        if live_intent.hash() != verdict.intent_hash:
            raise RazoError(
                "PRICE_MISMATCH", 409, "The cart changed since it was approved — please check out again.",
            )

        idempotency_key = _idempotency_key(live_session.id, live_intent.cart_version, live_intent.hash())
        existing = await Order.find_one(Order.idempotency_key == idempotency_key)
        if existing is not None:
            return order_view(existing)

        reserved = await _reserve_stock(live_intent.lines)

        order_id = str(ULID())
        actor_key = live_session.actor_ref or live_session.id
        order = Order(
            id=order_id, session_id=live_session.id, actor_key=actor_key,
            evaluation_id=verdict.evaluation_id, amount_paise=live_intent.total_paise,
            currency=live_intent.currency, state="creating", idempotency_key=idempotency_key,
            created_at=_now(), updated_at=_now(),
        )
        try:
            await order.insert()
        except DuplicateKeyError:
            # Lost a concurrent race for the same key. The winner's order
            # already carries the reservation, so ours has to go back —
            # otherwise the stock is silently held by nothing.
            await _release_stock(reserved)
            existing = await Order.find_one(Order.idempotency_key == idempotency_key)
            if existing is not None:
                return order_view(existing)
            raise

        client = get_razorpay_client()
        notes = {
            "session_id": live_session.id, "evaluation_id": verdict.evaluation_id,
            "policy_version": verdict.policy_version,
        }
        try:
            rzp_order = await client.create_order(order.amount_paise, order.currency, receipt=order.id, notes=notes)
            await audit.record(
                actor="payments", action="payment.order_created", session_id=live_session.id,
                subject={"type": "order", "id": order.id},
                output={"razorpay_order_id": rzp_order["id"], "amount_paise": order.amount_paise},
                reason=f"Created a Razorpay order for {inr(order.amount_paise)} against evaluation "
                       f"{verdict.evaluation_id}.",
            )
            link = await client.create_payment_link(order.amount_paise, order.currency, rzp_order["id"], notes=notes)
        except RazoError as e:
            order.state = "upstream_failed"
            order.failure_code = e.code
            order.updated_at = _now()
            await order.save()
            await audit_safe(
                actor="payments", action="payment.failed", session_id=live_session.id,
                subject={"type": "order", "id": order.id},
                output={"error_code": e.code},
                reason=f"Razorpay did not accept the request: {e.user_message} The cart is saved and the "
                       "order can be retried under the same idempotency key.",
                outcome="failed",
            )
            raise

        order.razorpay_order_id = rzp_order["id"]
        order.payment_link_id = link["id"]
        order.payment_link_url = link.get("short_url") or link.get("url")
        order.state = "link_sent"
        order.updated_at = _now()
        await order.save()

        await Session.get_motor_collection().update_one({"_id": live_session.id}, {"$set": {"cart.state": "locked"}})

        await audit.record(
            actor="payments", action="payment.link_created", session_id=live_session.id,
            subject={"type": "order", "id": order.id},
            output={"payment_link_url": order.payment_link_url, "amount_paise": order.amount_paise},
            reason=f"Issued a payment link for {inr(order.amount_paise)}. {verdict.reason_summary}",
        )

        return order_view(order)


payment_service = PaymentService()
