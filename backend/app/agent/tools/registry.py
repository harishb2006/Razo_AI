import logging
from dataclasses import dataclass
from typing import Awaitable, Callable

from pydantic import BaseModel

from app.agent.llm.base import ToolSpecDict
from app.agent.tools.schemas import (
    AddToCartArgs, CheckPolicyArgs, GetCartArgs, GetProductArgs, RecommendArgs, RequestCheckoutArgs,
    SearchCatalogArgs, UpdateCartItemArgs,
)
from app.audit.service import audit
from app.db.documents import Session
from app.services.cart_service import cart_service
from app.services.catalog_service import catalog_service
from app.services.checkout_service import checkout
from app.services.policy_service import evaluate_cart, verdict_to_dict

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    args_model: type[BaseModel]
    handler: Callable[..., Awaitable[dict]]


async def _suggestions_for(session_id: str, sku: str) -> dict:
    """Recommendations ride along on a tool result the model is already being
    handed, rather than costing a second LLM round trip to go and ask for. It
    keeps the suggestion deterministic — it is computed whether or not the
    model thinks to look — and keeps a turn inside its latency budget."""
    # Growth is a nicety bolted onto a tool the buyer actually asked for, so
    # the whole of it is guarded: a suggestion must never be the reason a
    # search or an add-to-cart fails.
    try:
        session = await Session.get(session_id)
        in_cart = frozenset(i.sku for i in session.cart.items) if session else frozenset()
        result = await catalog_service.recommend(sku, exclude_skus=in_cart)

        picks = (result["upgrades"] + result["pairs_with"])[:2]
        if not picks:
            return {}

        await audit.record(
            actor="agent", action="growth.suggested", session_id=session_id,
            input={"anchor_sku": sku},
            output={"suggested": [p["sku"] for p in picks]},
            reason=f"Offered {len(picks)} suggestion(s) alongside {result['anchor']['title']}. Each is a "
                   "live catalog product at its catalog price; the buyer still has to accept it, and it "
                   "is still subject to the rulebook.",
        )
        return {"suggestions": [
            {"sku": p["sku"], "title": p["title"], "price_display": p["price_display"],
             "price_paise": p["price_paise"], "category": p["category"], "why": p["why"]}
            for p in picks
        ]}
    except Exception:
        log.warning("Growth suggestions unavailable for %s; continuing without them.", sku, exc_info=True)
        return {}


async def _search_catalog(session_id: str, query: str | None = None, **args) -> dict:
    page = await catalog_service.search(q=query, **args)
    await audit.record(
        actor="catalog", action="catalog.searched", session_id=session_id,
        input={"query": query, **args},
        output={"result_count": len(page.items)},
        reason=f"Searched the catalog for {query!r} and found {len(page.items)} match(es). "
               "Prices in the results come from the catalog of record.",
    )
    result = page.model_dump()
    if page.items:
        result.update(await _suggestions_for(session_id, page.items[0].sku))
    return result


async def _get_product(session_id: str, sku: str) -> dict:
    view = await catalog_service.get(sku)
    return view.model_dump()


async def _recommend(session_id: str, sku: str, limit: int = 2) -> dict:
    """Growth, held to the same standard as everything else: suggestions are
    real catalog rows at catalog prices, and accepting one still goes through
    add_to_cart and the rulebook."""
    session = await Session.get(session_id)
    in_cart = frozenset(i.sku for i in session.cart.items) if session else frozenset()
    result = await catalog_service.recommend(sku, exclude_skus=in_cart, limit=limit)
    await audit.record(
        actor="agent", action="growth.suggested", session_id=session_id,
        input={"anchor_sku": sku},
        output={"upgrades": [u["sku"] for u in result["upgrades"]],
                "pairs_with": [p["sku"] for p in result["pairs_with"]]},
        reason=f"Offered {len(result['upgrades'])} upgrade(s) and {len(result['pairs_with'])} pairing(s) "
               f"for {result['anchor']['title']}. Every suggestion is a live catalog product at its "
               "catalog price; the buyer still has to accept it and it is still subject to the rulebook.",
    )
    return result


async def _add_to_cart(session_id: str, sku: str, qty: int) -> dict:
    result = await cart_service.add(session_id, sku, qty)
    result.update(await _suggestions_for(session_id, sku))
    return result


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
        ToolSpec(
            "recommend",
            "Given a SKU the buyer is interested in or has just added, return real catalog products "
            "worth suggesting: `upgrades` (a better item in the same category) and `pairs_with` "
            "(something commonly bought alongside it). Use this to grow the basket. Suggestions are "
            "candidates only — the buyer must accept one and add_to_cart still applies.",
            RecommendArgs, _recommend,
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
