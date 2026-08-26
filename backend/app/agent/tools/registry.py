from dataclasses import dataclass
from typing import Awaitable, Callable

from pydantic import BaseModel

from app.agent.llm.base import ToolSpecDict
from app.agent.tools.schemas import (
    AddToCartArgs, CheckPolicyArgs, GetCartArgs, GetProductArgs, RequestCheckoutArgs, SearchCatalogArgs,
    UpdateCartItemArgs,
)
from app.audit.service import audit
from app.db.documents import Session
from app.services.cart_service import cart_service
from app.services.catalog_service import catalog_service
from app.services.checkout_service import checkout
from app.services.policy_service import evaluate_cart, verdict_to_dict


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    args_model: type[BaseModel]
    handler: Callable[..., Awaitable[dict]]


async def _search_catalog(session_id: str, query: str | None = None, **args) -> dict:
    page = await catalog_service.search(q=query, **args)
    await audit.record(
        actor="catalog", action="catalog.searched", session_id=session_id,
        input={"query": query, **args},
        output={"result_count": len(page.items)},
        reason=f"Searched the catalog for {query!r} and found {len(page.items)} match(es). "
               "Prices in the results come from the catalog of record.",
    )
    return page.model_dump()


async def _get_product(session_id: str, sku: str) -> dict:
    view = await catalog_service.get(sku)
    return view.model_dump()


async def _add_to_cart(session_id: str, sku: str, qty: int) -> dict:
    return await cart_service.add(session_id, sku, qty)


async def _update_cart_item(session_id: str, sku: str, qty: int) -> dict:
    return await cart_service.update_qty(session_id, sku, qty)


async def _get_cart(session_id: str) -> dict:
    return await cart_service.get(session_id)


async def _check_policy(session_id: str) -> dict:
    """Dry run only — never returns a signed token, so this can never be
    used to authorize a payment. It just previews the verdict."""
    session = await Session.get(session_id)
    if session is None:
        return {"error": "SESSION_NOT_FOUND", "message": "I couldn't find that session."}
    if not session.cart.items:
        return {"decision": "ALLOW", "reason_summary": "Cart is empty.", "findings": []}
    _, verdict = await evaluate_cart(session, purpose="preview")
    return verdict_to_dict(verdict, include_token=False)


async def _request_checkout(session_id: str, confirm: bool = True) -> dict:
    """The only tool that can lead to money. It never sees or returns a
    signed token to the model — checkout() consumes the verdict internally
    and only ever hands back an outcome: paid, escalated, or denied."""
    return await checkout(session_id)


TOOLS: dict[str, ToolSpec] = {
    spec.name: spec for spec in [
        ToolSpec(
            "search_catalog",
            "Search the merchant's product catalog. Prices and stock come from here, never from the model.",
            SearchCatalogArgs, _search_catalog,
        ),
        ToolSpec("get_product", "Fetch a single product by SKU.", GetProductArgs, _get_product),
        ToolSpec(
            "add_to_cart",
            "Add a product to the buyer's cart by SKU and quantity. There is no price argument — "
            "the price is always looked up fresh from the catalog.",
            AddToCartArgs, _add_to_cart,
        ),
        ToolSpec(
            "update_cart_item",
            "Change the quantity of a line already in the cart. qty=0 removes it.",
            UpdateCartItemArgs, _update_cart_item,
        ),
        ToolSpec("get_cart", "Read the current cart.", GetCartArgs, _get_cart),
        ToolSpec(
            "check_policy",
            "Preview whether the current cart would be allowed, need merchant approval, or be denied — "
            "without creating a payment. Use this before telling the buyer their order is confirmed.",
            CheckPolicyArgs, _check_policy,
        ),
        ToolSpec(
            "request_checkout",
            "The only path toward payment. Runs the merchant's rulebook against the cart and, depending "
            "on the verdict, either creates a real Razorpay payment link, sends the order to the merchant "
            "for approval, or refuses it outright with the reasons. Only call this when the buyer has "
            "clearly confirmed they want to check out.",
            RequestCheckoutArgs, _request_checkout,
        ),
    ]
}


def tool_specs_json() -> list[ToolSpecDict]:
    return [
        {"name": t.name, "description": t.description, "parameters": t.args_model.model_json_schema()}
        for t in TOOLS.values()
    ]
