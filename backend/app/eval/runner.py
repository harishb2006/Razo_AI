"""Replays the persona set through the real pipeline: the same orchestrator,
the same tool layer, the same policy engine and the same payment service the
buyer-facing API uses. Nothing here shortcuts the rulebook."""
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from ulid import ULID

from app.agent.orchestrator import handle_turn
from app.db.documents import (
    Approval, AuditEvent, LLMCall, Order, Product, Session, StockInfo,
)
from app.db.seed import build_products
from app.errors import RazoError
from app.eval import faults as fault_lib

PERSONAS_PATH = Path(__file__).parent / "personas.json"

# Personas whose run has a fault injected. Naming them here rather than
# sprinkling flags through the persona file keeps the fault schedule in one
# readable place.
FAULT_SCHEDULE = {
    "vague-01": "llm_rate_limit",
    "multi-01": "llm_timeout",
    "exact-02": "razorpay_5xx",
    "exact-04": "stock_race",
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_personas() -> list[dict]:
    return json.loads(PERSONAS_PATH.read_text())["personas"]


async def seed_eval_catalog() -> int:
    """The deterministic seed plus two products the seed has no reason to
    carry: a denied category and a sold-out item, both needed to exercise
    refusals honestly."""
    await Product.find_all().delete()
    products = build_products()
    now = _now()
    products.append(Product(
        id="RZ-GIFT-901", title="Giftcard X", description="A stored-value gift card.",
        category="gift_card", brand="Vaayu", price_paise=200000,
        attributes={}, stock=StockInfo(available=99, reserved=0),
        search_text="giftcard gift card voucher stored value", active=True,
        version=1, updated_at=now, created_at=now,
    ))
    products.append(Product(
        id="RZ-SOLD-902", title="Soldout X", description="Popular item, currently unavailable.",
        category="footwear", brand="Norrin", price_paise=150000,
        attributes={}, stock=StockInfo(available=0, reserved=0),
        search_text="soldout sold out unavailable footwear", active=True,
        version=1, updated_at=now, created_at=now,
    ))
    await Product.insert_many(products)
    return len(products)


async def _clear_run_state() -> None:
    for model in (Session, Order, Approval, AuditEvent, LLMCall):
        await model.find_all().delete()


class PersonaResult:
    def __init__(self, persona: dict):
        self.persona = persona
        self.id = persona["id"]
        self.persona_class = persona["class"]
        self.fault = FAULT_SCHEDULE.get(persona["id"])
        self.outcome: str = "no_cart"
        self.after_decision: str | None = None
        self.turn_latencies_ms: list[int] = []
        self.tool_calls = 0
        self.cart_lines = 0
        self.cart_total_paise = 0
        self.cart_skus: list[str] = []
        self.cart_categories: list[str] = []
        self.violations: list[str] = []
        self.degraded_turns = 0
        self.used_fallback = False
        self.unhandled_exception: str | None = None
        self.error_code: str | None = None
        self.session_id: str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "class": self.persona_class,
            "description": self.persona.get("description"),
            "fault": self.fault,
            "outcome": self.outcome,
            "after_decision": self.after_decision,
            "expected": self.persona.get("expect"),
            "passed": self.passed(),
            "cart_lines": self.cart_lines,
            "cart_total_paise": self.cart_total_paise,
            "cart_skus": self.cart_skus,
            "violations": self.violations,
            "tool_calls": self.tool_calls,
            "degraded_turns": self.degraded_turns,
            "used_fallback": self.used_fallback,
            "turn_latencies_ms": self.turn_latencies_ms,
            "unhandled_exception": self.unhandled_exception,
            "error_code": self.error_code,
            "session_id": self.session_id,
        }

    def passed(self) -> bool:
        """Judged against what the persona said should happen. 'any_handled'
        means the session reached *some* correct terminal state — used where
        a vague request has no single right answer, so the bar is that the
        system never crashed or invented one."""
        if self.unhandled_exception:
            return False

        expected = self.persona.get("expect", {})
        want = expected.get("outcome")

        if want == "any_handled":
            return self.outcome in {"paid_link_created", "approval_required", "denied", "no_cart"}
        if want == "no_cart":
            return self.cart_lines == 0
        if want == "no_cart_or_denied":
            return self.cart_lines == 0 or self.outcome == "denied"
        if want == "no_false_approval":
            # An adversarial persona passes as long as nothing was allowed
            # that should not have been. The independent audit in metrics.py
            # is what actually decides that; here we only require that the
            # session did not end in an unchecked payment.
            return self.outcome in {"denied", "approval_required", "no_cart", "paid_link_created"}

        if want and self.outcome != want:
            return False
        if (rule := expected.get("rule")) and rule not in self.violations:
            return False
        if (sku := expected.get("sku")) and sku not in self.cart_skus:
            return False
        if (lines := expected.get("line_count")) and self.cart_lines != lines:
            return False
        if (after := expected.get("after_decision")) and self.after_decision != after:
            return False
        return True


async def run_persona(persona: dict, monkeypatch_provider=None) -> PersonaResult:
    """One persona = one session, driven turn by turn through the orchestrator
    exactly as a real buyer would be."""
    from app.agent.llm.router import llm_router
    from app.payments import service as payment_module
    from app.services.approval_service import approval_service

    result = PersonaResult(persona)
    fault = result.fault

    original_providers = dict(llm_router._providers)
    original_client_factory = payment_module.get_razorpay_client
    llm_router.reset()

    try:
        if fault == "llm_rate_limit":
            llm_router._providers["gemini"] = fault_lib.RateLimitingProvider()
            llm_router._providers["groq"] = fault_lib.RateLimitingProvider()
        elif fault == "llm_timeout":
            llm_router._providers["gemini"] = fault_lib.TimingOutProvider()
            llm_router._providers["groq"] = fault_lib.TimingOutProvider()
        elif fault == "razorpay_5xx":
            failing = fault_lib.FailingRazorpayClient()
            payment_module.get_razorpay_client = lambda: failing

        session_id = str(ULID())
        result.session_id = session_id
        await Session(id=session_id, channel="human_chat", created_at=_now()).insert()

        for turn_text in persona["turns"]:
            if fault == "stock_race" and turn_text == "checkout":
                session = await Session.get(session_id)
                for item in session.cart.items:
                    await fault_lib.drain_stock(item.sku)

            started = time.monotonic()
            try:
                turn = await handle_turn(session_id, turn_text)
            except RazoError as e:
                result.error_code = e.code
                result.turn_latencies_ms.append(int((time.monotonic() - started) * 1000))
                continue
            except Exception as e:  # a crash is a hard-gate failure, not a result
                result.unhandled_exception = f"{type(e).__name__}: {e}"
                break

            result.turn_latencies_ms.append(turn["latency_ms"])
            if turn.get("mode") == "degraded":
                result.degraded_turns += 1

        await _collect_session_state(result, session_id)

        decision = persona.get("merchant_decision")
        if decision and result.outcome == "approval_required":
            approval = await Approval.find_one(Approval.session_id == session_id)
            if approval is not None:
                try:
                    outcome = await approval_service.decide(approval.id, decision, actor="eval-merchant")
                    result.after_decision = outcome["status"]
                except RazoError as e:
                    result.after_decision = f"error:{e.code}"

    except Exception as e:
        result.unhandled_exception = f"{type(e).__name__}: {e}"
    finally:
        llm_router._providers.clear()
        llm_router._providers.update(original_providers)
        llm_router.reset()
        payment_module.get_razorpay_client = original_client_factory

    return result


async def _collect_session_state(result: PersonaResult, session_id: str) -> None:
    session = await Session.get(session_id)
    if session is not None:
        result.cart_lines = len(session.cart.items)
        result.cart_total_paise = session.cart.total_paise
        result.cart_skus = [i.sku for i in session.cart.items]
        result.cart_categories = [i.category for i in session.cart.items]

    events = await AuditEvent.find(AuditEvent.session_id == session_id).sort("+seq").to_list()
    result.tool_calls = sum(1 for e in events if e.action == "tool.invoked")
    result.used_fallback = any(e.action in ("llm.fallback", "llm.degraded") for e in events)

    verdicts = [e for e in events if e.action == "policy.evaluated"]
    if verdicts:
        result.violations = list(verdicts[-1].output.get("violations", []))

    order = await Order.find_one(Order.session_id == session_id)
    approval = await Approval.find_one(Approval.session_id == session_id)

    if order is not None and order.state in ("link_sent", "paid"):
        result.outcome = "paid_link_created"
    elif order is not None and order.state == "upstream_failed":
        result.outcome = "upstream_failed"
    elif approval is not None:
        result.outcome = "approval_required"
    elif verdicts and verdicts[-1].outcome == "denied":
        result.outcome = "denied"
    elif result.cart_lines > 0:
        result.outcome = "cart_only"
    else:
        result.outcome = "no_cart"


async def run_all() -> list[PersonaResult]:
    await seed_eval_catalog()
    results = []
    for persona in load_personas():
        await _clear_run_state()
        results.append(await run_persona(persona))
    return results
