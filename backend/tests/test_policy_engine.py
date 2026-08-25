"""Policy engine unit tests — pure, no LLM key, no Mongo, no network at all.
This is the artifact that proves the rulebook is real: it can be run with
the AI completely unplugged."""
from datetime import datetime, timezone

from app.domain.intent import IntentLine, OrderIntent
from app.policy.clock import FixedClock
from app.policy.engine import PolicyEngine
from app.policy.policy import load_policy
from app.policy.types import ProductSnapshot, RuleContext

CLOCK = FixedClock(datetime(2026, 1, 1, tzinfo=timezone.utc))


def make_engine() -> PolicyEngine:
    return PolicyEngine(load_policy(), signing_key="test-signing-key", token_ttl_s=120, clock=CLOCK)


def make_intent(
    total_paise: int,
    *,
    lines: tuple[IntentLine, ...] | None = None,
    channel: str = "human_chat",
    mandate: dict | None = None,
    currency: str = "INR",
) -> OrderIntent:
    if lines is None:
        lines = (IntentLine("RZ-1", 1, total_paise, 1, "footwear", total_paise),)
    return OrderIntent(
        session_id="s1", cart_version=1, channel=channel, lines=lines,
        total_paise=total_paise, currency=currency, mandate=mandate, created_at="2026-01-01T00:00:00Z",
    )


def snapshot_for(intent: OrderIntent, *, available: int = 100, active: bool = True) -> dict[str, ProductSnapshot]:
    return {
        l.sku: ProductSnapshot(price_paise=l.unit_price_paise, version=l.product_version, available=available, active=active)
        for l in intent.lines
    }


def make_ctx(intent: OrderIntent, **overrides) -> RuleContext:
    kwargs = {"catalog_snapshot": snapshot_for(intent), "session_24h_spend_paise": 0, "orders_last_hour": 0}
    kwargs.update(overrides)
    return RuleContext(**kwargs)


# --- R1: hard per-order cap -------------------------------------------------

def test_over_hard_cap_is_denied_with_no_llm_no_db_no_network():
    engine = make_engine()
    intent = make_intent(total_paise=3_000_000)  # ₹30,000, above the ₹25,000 cap
    verdict = engine.evaluate(intent, make_ctx(intent))
    assert verdict.decision == "DENY"
    r1 = next(f for f in verdict.findings if f.rule_id == "R1")
    assert r1.outcome == "deny"
    assert verdict.token is None


def test_exactly_at_hard_cap_passes_r1():
    engine = make_engine()
    intent = make_intent(total_paise=2_500_000)  # exactly ₹25,000
    verdict = engine.evaluate(intent, make_ctx(intent))
    r1 = next(f for f in verdict.findings if f.rule_id == "R1")
    assert r1.outcome == "pass"


def test_one_paise_over_hard_cap_denies():
    engine = make_engine()
    intent = make_intent(total_paise=2_500_001)
    verdict = engine.evaluate(intent, make_ctx(intent))
    assert verdict.decision == "DENY"


# --- R2: approval threshold --------------------------------------------------

def test_at_approval_threshold_requires_approval():
    engine = make_engine()
    intent = make_intent(total_paise=500_000)  # exactly ₹5,000
    verdict = engine.evaluate(intent, make_ctx(intent))
    assert verdict.decision == "REQUIRE_APPROVAL"
    assert verdict.token is None


def test_below_approval_threshold_allows():
    engine = make_engine()
    intent = make_intent(total_paise=499_999)
    verdict = engine.evaluate(intent, make_ctx(intent))
    assert verdict.decision == "ALLOW"
    assert verdict.token is not None


# --- merchant approval on file ------------------------------------------------

def test_merchant_approval_satisfies_the_threshold_rules():
    """Re-evaluation after the merchant approves must not escalate the same
    cart a second time, or the approval loop never terminates."""
    engine = make_engine()
    intent = make_intent(total_paise=859_800)
    ctx = make_ctx(intent, session_24h_spend_paise=3_900_000, merchant_approved=True)
    verdict = engine.evaluate(intent, ctx)
    assert verdict.decision == "ALLOW"
    assert verdict.token is not None
    assert {f.outcome for f in verdict.findings if f.rule_id in ("R2", "R7")} == {"pass"}


def test_merchant_approval_does_not_override_a_denial():
    """A merchant can waive their own threshold; they cannot waive the hard
    cap, stock, or price integrity."""
    engine = make_engine()
    intent = make_intent(total_paise=3_000_000)
    verdict = engine.evaluate(intent, make_ctx(intent, merchant_approved=True))
    assert verdict.decision == "DENY"
    assert verdict.token is None


# --- R3: category deny-list --------------------------------------------------

def test_denied_category_is_denied():
    engine = make_engine()
    intent = make_intent(100_000, lines=(IntentLine("RZ-GC", 1, 100_000, 1, "gift_card", 100_000),))
    verdict = engine.evaluate(intent, make_ctx(intent))
    assert verdict.decision == "DENY"
    r3 = next(f for f in verdict.findings if f.rule_id == "R3")
    assert r3.outcome == "deny"


# --- R4: per-line quantity cap -----------------------------------------------

def test_qty_over_line_cap_is_denied():
    engine = make_engine()
    intent = make_intent(100_000, lines=(IntentLine("RZ-1", 11, 10_000, 1, "footwear", 110_000),))
    verdict = engine.evaluate(intent, make_ctx(intent))
    r4 = next(f for f in verdict.findings if f.rule_id == "R4")
    assert r4.outcome == "deny"


# --- R5: stock ----------------------------------------------------------------

def test_out_of_stock_is_denied():
    engine = make_engine()
    intent = make_intent(100_000)
    ctx = make_ctx(intent, catalog_snapshot=snapshot_for(intent, available=0))
    verdict = engine.evaluate(intent, ctx)
    assert verdict.decision == "DENY"
    r5 = next(f for f in verdict.findings if f.rule_id == "R5")
    assert r5.outcome == "deny"


# --- R6: price integrity -------------------------------------------------------

def test_price_drift_since_quote_is_denied():
    engine = make_engine()
    intent = make_intent(100_000)
    ctx = make_ctx(intent, catalog_snapshot={"RZ-1": ProductSnapshot(price_paise=999_00, version=1, available=100, active=True)})
    verdict = engine.evaluate(intent, ctx)
    assert verdict.decision == "DENY"
    r6 = next(f for f in verdict.findings if f.rule_id == "R6")
    assert r6.outcome == "deny"


def test_someone_trying_it_on_with_a_fake_price_is_denied():
    """The '₹99 shoes' attack from the README: the cart line claims a price
    that doesn't match the catalog of record."""
    engine = make_engine()
    fake_line = IntentLine("RZ-SHOE-114", 2, 99_00, 3, "footwear", 198_00)
    intent = make_intent(198_00, lines=(fake_line,))
    real_snapshot = {"RZ-SHOE-114": ProductSnapshot(price_paise=4_299_00, version=3, available=40, active=True)}
    verdict = engine.evaluate(intent, make_ctx(intent, catalog_snapshot=real_snapshot))
    assert verdict.decision == "DENY"


# --- R7: 24h spend velocity ------------------------------------------------

def test_spend_velocity_at_limit_requires_approval():
    engine = make_engine()
    intent = make_intent(100_000)
    ctx = make_ctx(intent, session_24h_spend_paise=3_900_000)  # + 100,000 = 4,000,000 == limit
    verdict = engine.evaluate(intent, ctx)
    r7 = next(f for f in verdict.findings if f.rule_id == "R7")
    assert r7.outcome == "require_approval"


# --- R8: order frequency ----------------------------------------------------

def test_sixth_order_in_an_hour_is_denied():
    engine = make_engine()
    intent = make_intent(100_000)
    ctx = make_ctx(intent, orders_last_hour=5)  # limit is 5/hour
    verdict = engine.evaluate(intent, ctx)
    r8 = next(f for f in verdict.findings if f.rule_id == "R8")
    assert r8.outcome == "deny"


# --- R9: currency --------------------------------------------------------------

def test_non_inr_currency_is_denied():
    engine = make_engine()
    intent = make_intent(100_000, currency="USD")
    verdict = engine.evaluate(intent, make_ctx(intent))
    assert verdict.decision == "DENY"
    r9 = next(f for f in verdict.findings if f.rule_id == "R9")
    assert r9.outcome == "deny"


# --- R10: buyer-agent mandate ------------------------------------------------

def test_buyer_agent_without_mandate_is_denied():
    engine = make_engine()
    intent = make_intent(100_000, channel="buyer_agent", mandate=None)
    verdict = engine.evaluate(intent, make_ctx(intent))
    r10 = next(f for f in verdict.findings if f.rule_id == "R10")
    assert r10.outcome == "deny"


def test_buyer_agent_within_mandate_allows():
    engine = make_engine()
    mandate = {"budget_paise": 1_000_000, "allowed_categories": ["footwear"]}
    intent = make_intent(400_000, channel="buyer_agent", mandate=mandate)
    verdict = engine.evaluate(intent, make_ctx(intent))
    assert verdict.decision == "ALLOW"


def test_buyer_agent_outside_category_scope_is_denied():
    """The stretch-goal demo: an autonomous buyer straying outside its
    mandate gets refused with the category named."""
    engine = make_engine()
    mandate = {"budget_paise": 1_000_000, "allowed_categories": ["footwear", "apparel"]}
    line = IntentLine("RZ-TV-1", 1, 400_000, 1, "electronics", 400_000)
    intent = make_intent(400_000, lines=(line,), channel="buyer_agent", mandate=mandate)
    verdict = engine.evaluate(intent, make_ctx(intent))
    assert verdict.decision == "DENY"
    r10 = next(f for f in verdict.findings if f.rule_id == "R10")
    assert "electronics" in r10.reason


def test_buyer_agent_gets_a_tighter_cap_than_a_human():
    engine = make_engine()
    mandate = {"budget_paise": 20_000_00, "allowed_categories": []}  # generous budget
    intent = make_intent(1_100_000, channel="buyer_agent", mandate=mandate)  # ₹11,000 > buyer_agent cap of ₹10,000
    verdict = engine.evaluate(intent, make_ctx(intent))
    assert verdict.decision == "DENY"


# --- R11: cart integrity -------------------------------------------------------

def test_cart_total_not_matching_line_sum_is_denied():
    engine = make_engine()
    intent = make_intent(999_999, lines=(IntentLine("RZ-1", 1, 100_000, 1, "footwear", 100_000),))
    verdict = engine.evaluate(intent, make_ctx(intent))
    assert verdict.decision == "DENY"
    r11 = next(f for f in verdict.findings if f.rule_id == "R11")
    assert r11.outcome == "deny"


# --- Totality: all 11 rules always run, in every branch ----------------------

def test_all_eleven_rules_always_run_even_on_denial():
    engine = make_engine()
    intent = make_intent(total_paise=30_00_000, lines=(IntentLine("RZ-GC", 1, 30_00_000, 1, "gift_card", 30_00_000),))
    verdict = engine.evaluate(intent, make_ctx(intent))
    assert len(verdict.findings) == 11
    assert {f.rule_id for f in verdict.findings} == {f"R{i}" for i in range(1, 12)}


def test_all_eleven_rules_always_run_on_a_clean_allow():
    engine = make_engine()
    intent = make_intent(100_000)
    verdict = engine.evaluate(intent, make_ctx(intent))
    assert len(verdict.findings) == 11
    assert verdict.decision == "ALLOW"


# --- Determinism, versioning, and the signed token ----------------------------

def test_same_intent_and_clock_gives_byte_identical_findings():
    engine = make_engine()
    intent = make_intent(100_000)
    ctx = make_ctx(intent)
    v1 = engine.evaluate(intent, ctx)
    v2 = engine.evaluate(intent, ctx)
    assert v1.findings == v2.findings
    assert v1.evaluation_id == v2.evaluation_id
    assert v1.intent_hash == v2.intent_hash


def test_verdict_records_policy_version_and_hash():
    engine = make_engine()
    intent = make_intent(100_000)
    verdict = engine.evaluate(intent, make_ctx(intent))
    assert verdict.policy_version == "v1.0.0"


def test_denied_and_escalated_verdicts_carry_no_token():
    engine = make_engine()
    denied = engine.evaluate(make_intent(30_00_000), make_ctx(make_intent(30_00_000)))
    escalated = engine.evaluate(make_intent(500_000), make_ctx(make_intent(500_000)))
    assert denied.token is None
    assert escalated.token is None
