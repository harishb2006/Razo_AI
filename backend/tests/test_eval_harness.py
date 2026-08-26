"""Tests the eval harness's own correctness — both hard gates, the
independent false-approval audit, and the two multi-turn bugs the harness
surfaced while it was being built (message ordering, catalog re-ranking)."""
import pytest

from app.agent.orchestrator import handle_turn
from app.db.documents import Order, Product, Session
from app.eval.metrics import compute, find_false_approvals, hard_gates
from app.eval.runner import load_personas, run_persona, seed_eval_catalog


@pytest.mark.asyncio
async def test_personas_load_and_are_well_formed(db):
    personas = load_personas()
    assert len(personas) == 24
    for p in personas:
        assert p["id"]
        assert p["turns"]
        assert "expect" in p


@pytest.mark.asyncio
async def test_the_seed_includes_a_denied_category_and_a_sold_out_item(db):
    await seed_eval_catalog()
    gift = await Product.get("RZ-GIFT-901")
    soldout = await Product.get("RZ-SOLD-902")
    assert gift.category == "gift_card"
    assert soldout.stock.available == 0


@pytest.mark.asyncio
async def test_hard_gates_are_clean_on_a_normal_result_set(db):
    metrics = {"guardrail_false_approvals": {"count": 0}, "unhandled_exceptions": {"count": 0}}
    assert hard_gates(metrics) == []


@pytest.mark.asyncio
async def test_hard_gates_fail_on_a_false_approval():
    metrics = {"guardrail_false_approvals": {"count": 1}, "unhandled_exceptions": {"count": 0}}
    failures = hard_gates(metrics)
    assert len(failures) == 1
    assert "false approval" in failures[0]


@pytest.mark.asyncio
async def test_the_independent_audit_flags_an_order_with_no_evaluation(db):
    """This check does not trust the policy engine's own verdict — it
    re-derives the limits from policy.yaml and re-reads the cart itself."""
    from datetime import datetime, timezone

    from app.db.documents import CartItem

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    session = Session(id="s-bad", created_at=now)
    session.cart.items = [CartItem(
        sku="X", qty=1, unit_price_paise=100, product_version=1,
        category="footwear", line_total_paise=100,
    )]
    session.cart.total_paise = 100
    await session.insert()

    await Order(
        id="o-bad", session_id="s-bad", actor_key="s-bad", evaluation_id="",  # <-- the smoking gun
        amount_paise=100, state="link_sent", idempotency_key="k-bad",
        created_at=now, updated_at=now,
    ).insert()

    violations = await find_false_approvals()
    assert any(v["order_id"] == "o-bad" for v in violations)


@pytest.mark.asyncio
async def test_the_independent_audit_flags_a_denied_category_that_slipped_through(db):
    """Simulates the engine itself being buggy — the audit must not just
    trust that R3 ran; it re-checks the category against policy.yaml."""
    from datetime import datetime, timezone

    from app.db.documents import CartItem, StockInfo

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    await Product(
        id="RZ-GIFT-X", title="Gift", category="gift_card", brand="X",
        price_paise=1000, stock=StockInfo(available=10, reserved=0),
        search_text="gift", version=1, updated_at=now, created_at=now,
    ).insert()

    session = Session(id="s-gift", created_at=now)
    session.cart.items = [CartItem(
        sku="RZ-GIFT-X", qty=1, unit_price_paise=1000, product_version=1,
        category="gift_card", line_total_paise=1000,
    )]
    session.cart.total_paise = 1000
    await session.insert()

    await Order(
        id="o-gift", session_id="s-gift", actor_key="s-gift", evaluation_id="eval-x",
        amount_paise=1000, state="link_sent", idempotency_key="k-gift",
        created_at=now, updated_at=now,
    ).insert()

    violations = await find_false_approvals()
    assert any("R3" in v["detail"] for v in violations if v["order_id"] == "o-gift")


@pytest.mark.asyncio
async def test_a_clean_order_produces_no_false_approvals(db):
    from datetime import datetime, timezone

    from app.db.documents import CartItem, StockInfo

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    await Product(
        id="RZ-OK-1", title="Fine", category="footwear", brand="X", price_paise=1000,
        stock=StockInfo(available=10, reserved=0), search_text="fine",
        version=1, updated_at=now, created_at=now,
    ).insert()
    session = Session(id="s-ok", created_at=now)
    session.cart.items = [CartItem(
        sku="RZ-OK-1", qty=1, unit_price_paise=1000, product_version=1,
        category="footwear", line_total_paise=1000,
    )]
    session.cart.total_paise = 1000
    await session.insert()
    await Order(
        id="o-ok", session_id="s-ok", actor_key="s-ok", evaluation_id="eval-ok",
        amount_paise=1000, state="link_sent", idempotency_key="k-ok",
        created_at=now, updated_at=now,
    ).insert()

    assert await find_false_approvals() == []


@pytest.mark.asyncio
async def test_a_turn_in_the_middle_of_a_multi_turn_session_does_not_act_on_a_prior_turns_tool_result(db):
    """The bug the harness caught: without an explicit turn boundary, a new
    'add X' request could silently re-trigger whatever the *previous* turn's
    add_to_cart call did, instead of searching for the newly named product."""
    from ulid import ULID

    await seed_eval_catalog()
    session_id = str(ULID())
    from datetime import datetime, timezone
    await Session(id=session_id, channel="human_chat",
                  created_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")).insert()

    await handle_turn(session_id, "add the Gripline Pro")
    turn2 = await handle_turn(session_id, "add the Baselayer Pro")

    skus = {i["sku"] for i in turn2["cart"]["items"]}
    assert skus == {"RZ-FOOT-108", "RZ-APPA-112"}


@pytest.mark.asyncio
async def test_catalog_ranking_prefers_the_exact_named_variant_over_a_cheaper_sibling(db):
    """The other bug the harness caught: 'Windshell X' and 'Windshell Pro'
    both matched the token 'windshell' with no way to prefer the one that
    was actually named, so the tie broke toward whichever was cheaper."""
    from app.services.catalog_service import catalog_service

    await seed_eval_catalog()
    page = await catalog_service.search(q="I want the Windshell X", limit=1)

    assert page.items[0].sku == "RZ-APPA-109"


@pytest.mark.asyncio
async def test_the_full_batch_run_produces_report_ready_metrics(db):
    """One real end-to-end pass — not the whole 24, to keep this test fast —
    proving run_persona -> compute -> hard_gates is wired correctly."""
    await seed_eval_catalog()
    persona = next(p for p in load_personas() if p["id"] == "exact-01")

    result = await run_persona(persona)
    metrics = await compute([result])

    assert result.outcome == "paid_link_created"
    assert metrics["persona_count"] == 1
    assert metrics["guardrail_false_approvals"]["count"] == 0
    assert hard_gates(metrics) == []
