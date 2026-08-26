"""What the panel actually asks for: show me the log for this session, and
show me that a refusal recorded *why* it was refused."""
import pytest
import pytest_asyncio
from ulid import ULID

from app.audit.chain import verify_chain
from app.audit.explain import explain_session, narrate
from app.db.documents import AuditEvent, CartItem, Product, Session, StockInfo
from app.services.approval_service import approval_service
from app.services.checkout_service import checkout

NOW = "2026-01-01T00:00:00Z"


@pytest_asyncio.fixture
async def seeded(db):
    await Product(
        id="RZ-SHOE-1", title="Trailrunner X", category="footwear", brand="Vaayu",
        price_paise=429900, stock=StockInfo(available=40, reserved=0),
        search_text="trailrunner", version=1, updated_at=NOW, created_at=NOW,
    ).insert()
    await Product(
        id="RZ-SOCK-1", title="Merino Socks", category="apparel", brand="Vaayu",
        price_paise=49900, stock=StockInfo(available=100, reserved=0),
        search_text="socks", version=1, updated_at=NOW, created_at=NOW,
    ).insert()
    yield db


def line(sku: str, qty: int, price: int, category: str) -> CartItem:
    return CartItem(
        sku=sku, qty=qty, unit_price_paise=price, product_version=1,
        category=category, line_total_paise=price * qty,
    )


async def make_session(items: list[CartItem]) -> Session:
    total = sum(i.line_total_paise for i in items)
    session = Session(id=str(ULID()), created_at=NOW)
    session.cart.items = items
    session.cart.subtotal_paise = total
    session.cart.total_paise = total
    session.cart.version = 1
    await session.insert()
    return session


async def actions_for(session_id: str) -> list[str]:
    events = await AuditEvent.find(AuditEvent.session_id == session_id).sort("+seq").to_list()
    return [e.action for e in events]


@pytest.mark.asyncio
async def test_a_successful_purchase_records_the_whole_money_path(seeded):
    session = await make_session([line("RZ-SOCK-1", 1, 49900, "apparel")])

    await checkout(session.id)

    assert await actions_for(session.id) == [
        "policy.evaluated", "payment.order_created", "payment.link_created",
    ]


@pytest.mark.asyncio
async def test_a_refusal_records_every_rule_it_broke(seeded):
    session = await make_session([line("RZ-SHOE-1", 8, 429900, "footwear")])

    await checkout(session.id)

    event = await AuditEvent.find_one(AuditEvent.session_id == session.id)
    assert event.action == "policy.evaluated"
    assert event.outcome == "denied"
    assert "R1" in event.output["violations"]
    assert "exceeds the hard per-order cap" in event.reason


@pytest.mark.asyncio
async def test_a_denied_order_records_no_payment_events(seeded):
    session = await make_session([line("RZ-SHOE-1", 8, 429900, "footwear")])

    await checkout(session.id)

    assert not any(a.startswith("payment.") for a in await actions_for(session.id))


@pytest.mark.asyncio
async def test_the_approval_round_trip_is_fully_traceable(seeded):
    session = await make_session([line("RZ-SHOE-1", 2, 429900, "footwear")])
    escalation = await checkout(session.id)

    await approval_service.decide(escalation["approval_id"], "approve", actor="asha@merchant")

    actions = await actions_for(session.id)
    assert actions == [
        "policy.evaluated",      # first run: above the threshold
        "approval.requested",
        "policy.evaluated",      # re-run before spending anything
        "approval.decided",
        "payment.order_created",
        "payment.link_created",
    ]
    decision = await AuditEvent.find_one(AuditEvent.action == "approval.decided")
    assert decision.actor == "merchant"
    assert "asha@merchant" in decision.reason
    assert "re-run against the live cart" in decision.reason


@pytest.mark.asyncio
async def test_a_rejection_names_who_rejected_it_and_why(seeded):
    session = await make_session([line("RZ-SHOE-1", 2, 429900, "footwear")])
    escalation = await checkout(session.id)

    await approval_service.decide(
        escalation["approval_id"], "reject", actor="asha@merchant", note="Too large for a new buyer.",
    )

    decision = await AuditEvent.find_one(AuditEvent.action == "approval.decided")
    assert decision.outcome == "denied"
    assert "asha@merchant rejected" in decision.reason
    assert "Too large for a new buyer." in decision.reason


@pytest.mark.asyncio
async def test_every_recorded_event_carries_a_reason(seeded):
    session = await make_session([line("RZ-SHOE-1", 2, 429900, "footwear")])
    escalation = await checkout(session.id)
    await approval_service.decide(escalation["approval_id"], "approve", actor="merchant")

    events = await AuditEvent.find_all().to_list()
    assert events
    assert all(e.reason and e.reason.strip() for e in events)


@pytest.mark.asyncio
async def test_the_chain_stays_intact_across_a_real_flow(seeded):
    session = await make_session([line("RZ-SOCK-1", 2, 49900, "apparel")])
    await checkout(session.id)

    events = await AuditEvent.find_all().sort("+seq").to_list()
    assert verify_chain([e.model_dump() for e in events])["ok"] is True


@pytest.mark.asyncio
async def test_explain_renders_a_numbered_story_of_the_session(seeded):
    session = await make_session([line("RZ-SOCK-1", 1, 49900, "apparel")])
    await checkout(session.id)

    explanation = await explain_session(session.id)

    assert explanation["step_count"] == 3
    assert [s["step"] for s in explanation["steps"]] == [1, 2, 3]
    assert "payment link" in explanation["summary"]
    assert all(s["reason"] for s in explanation["steps"])

    text = narrate(explanation)
    assert "The rulebook checked the order." in text
    assert "why:" in text


@pytest.mark.asyncio
async def test_explain_says_plainly_when_an_order_was_refused(seeded):
    session = await make_session([line("RZ-SHOE-1", 8, 429900, "footwear")])
    await checkout(session.id)

    explanation = await explain_session(session.id)

    assert "refused" in explanation["summary"]
