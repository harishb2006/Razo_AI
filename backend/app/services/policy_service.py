import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from app.audit.service import audit
from app.db.documents import Order, Product, Session
from app.domain.intent import IntentLine, OrderIntent
from app.policy.engine import PolicyEngine
from app.policy.policy import policy as default_policy
from app.policy.types import ProductSnapshot, RuleContext, Verdict
from app.config import settings

_TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def build_engine() -> PolicyEngine:
    signing_key = settings.verdict_signing_key or settings.api_key
    return PolicyEngine(default_policy, signing_key, settings.verdict_token_ttl_s)


engine = build_engine()


async def build_snapshot(skus: list[str]) -> dict[str, ProductSnapshot]:
    products = await Product.find({"_id": {"$in": skus}}).to_list()
    return {
        p.id: ProductSnapshot(
            price_paise=p.price_paise, version=p.version,
            available=p.stock.available - p.stock.reserved, active=p.active,
        )
        for p in products
    }


async def cart_to_intent(session: Session) -> OrderIntent:
    lines = tuple(
        IntentLine(
            sku=i.sku, qty=i.qty, unit_price_paise=i.unit_price_paise,
            product_version=i.product_version, category=i.category, line_total_paise=i.line_total_paise,
        )
        for i in session.cart.items
    )
    return OrderIntent(
        session_id=session.id, cart_version=session.cart.version, channel=session.channel,
        lines=lines, total_paise=session.cart.total_paise, currency=session.cart.currency,
        mandate=session.mandate, created_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


async def spend_context(actor_key: str, now: datetime) -> tuple[int, int]:
    """R7/R8 rate-limit against `actor_key` (session.actor_ref, falling back
    to the session id for anonymous buyers) rather than a single session, so
    the limits hold across a customer's multiple chat sessions in a day."""
    since_24h = (now - timedelta(hours=24)).strftime(_TS_FORMAT)
    since_1h = (now - timedelta(hours=1)).strftime(_TS_FORMAT)
    orders = await Order.find({"actor_key": actor_key, "created_at": {"$gte": since_24h}}).to_list()
    spend_24h_paise = sum(o.amount_paise for o in orders)
    orders_last_hour = sum(1 for o in orders if o.created_at >= since_1h)
    return spend_24h_paise, orders_last_hour


_OUTCOME_FOR = {"ALLOW": "ok", "REQUIRE_APPROVAL": "escalated", "DENY": "denied"}

_PURPOSE_PREFIX = {
    "checkout": "Checkout requested.",
    "preview": "Dry run — the assistant previewed the verdict without creating anything.",
    "approval_recheck": "Re-checked before spending, after the merchant approved.",
}


async def evaluate_cart(
    session: Session, merchant_approved: bool = False, purpose: str = "checkout",
) -> tuple[OrderIntent, Verdict]:
    """Every evaluation is audited here rather than at the call sites, so no
    caller can run the rulebook without the run being recorded — including
    the re-check that happens after a merchant approves."""
    started = time.monotonic()
    intent = await cart_to_intent(session)
    snapshot = await build_snapshot(intent.skus)
    actor_key = session.actor_ref or session.id
    spend_24h_paise, orders_last_hour = await spend_context(actor_key, datetime.now(timezone.utc))
    ctx = RuleContext(
        catalog_snapshot=snapshot, session_24h_spend_paise=spend_24h_paise, orders_last_hour=orders_last_hour,
        merchant_approved=merchant_approved,
    )
    verdict = engine.evaluate(intent, ctx)

    await audit.record(
        actor="policy", action="policy.evaluated", session_id=session.id,
        subject={"type": "cart", "id": f"{session.id}:v{intent.cart_version}"},
        input={
            "intent_hash": verdict.intent_hash, "policy_version": verdict.policy_version,
            "purpose": purpose, "merchant_approved": merchant_approved,
        },
        output={
            "verdict": verdict.decision,
            "findings_count": len(verdict.findings),
            "violations": [f.rule_id for f in verdict.findings if f.outcome != "pass"],
        },
        reason=f"{_PURPOSE_PREFIX.get(purpose, purpose)} All {len(verdict.findings)} rules ran; "
               f"the verdict was {verdict.decision}. {verdict.reason_summary}",
        outcome=_OUTCOME_FOR[verdict.decision],
        latency_ms=int((time.monotonic() - started) * 1000),
    )
    return intent, verdict


def verdict_to_dict(verdict: Verdict, include_token: bool = False) -> dict:
    data = {
        "decision": verdict.decision,
        "findings": [asdict(f) for f in verdict.findings],
        "reason_summary": verdict.reason_summary,
        "policy_version": verdict.policy_version,
        "intent_hash": verdict.intent_hash,
        "evaluation_id": verdict.evaluation_id,
    }
    if include_token and verdict.token:
        data["token"] = asdict(verdict.token)
    return data
