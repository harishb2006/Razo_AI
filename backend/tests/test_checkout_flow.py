"""Checkout flow end to end against mongomock + the fake Razorpay client —
no keys, no network. Proves the three verdict paths and that the payment
service refuses anything but a freshly-valid ALLOW."""
import os

os.environ["OFFLINE_MODE"] = "True"

import pytest
import pytest_asyncio
from beanie import init_beanie
from mongomock_motor import AsyncMongoMockClient
from ulid import ULID

from app.db.documents import Approval, CartItem, Message, Order, Payment, Product, Session, StockInfo
from app.errors import RazoError
from app.payments.service import payment_service
from app.services.approval_service import approval_service
from app.services.cart_service import cart_service
from app.services.checkout_service import checkout
from app.services.policy_service import evaluate_cart

NOW = "2026-01-01T00:00:00Z"


@pytest_asyncio.fixture
async def db():
    client = AsyncMongoMockClient()
    await init_beanie(
        database=client["razo_test"],
        document_models=[Product, Session, Message, Order, Payment, Approval],
    )
    await Product(
        id="RZ-SHOE-1", title="Trailrunner X", category="footwear", brand="Vaayu",
        price_paise=429900, stock=StockInfo(available=40, reserved=0),
        search_text="trailrunner running shoe", version=1, updated_at=NOW, created_at=NOW,
    ).insert()
    await Product(
        id="RZ-SOCK-1", title="Merino Socks", category="apparel", brand="Vaayu",
        price_paise=49900, stock=StockInfo(available=100, reserved=0),
        search_text="merino socks", version=1, updated_at=NOW, created_at=NOW,
    ).insert()
    yield client


async def make_session(items: list[CartItem], **kwargs) -> Session:
    total = sum(i.line_total_paise for i in items)
    session = Session(id=str(ULID()), created_at=NOW, **kwargs)
    session.cart.items = items
    session.cart.subtotal_paise = total
    session.cart.total_paise = total
    session.cart.version = 1
    await session.insert()
    return session


def line(sku: str, qty: int, price: int, category: str) -> CartItem:
    return CartItem(
        sku=sku, qty=qty, unit_price_paise=price, product_version=1,
        category=category, line_total_paise=price * qty,
    )


@pytest.mark.asyncio
async def test_under_threshold_creates_a_payment_link(db):
    session = await make_session([line("RZ-SOCK-1", 1, 49900, "apparel")])

    result = await checkout(session.id)

    assert result["status"] == "paid_link_created"
    assert result["payment_link_url"].startswith("https://rzp.io/i/")
    order = await Order.find_one(Order.session_id == session.id)
    assert order.state == "link_sent"
    assert order.evaluation_id  # FR: no order without a verdict attached


@pytest.mark.asyncio
async def test_above_threshold_escalates_and_never_calls_razorpay(db):
    session = await make_session([line("RZ-SHOE-1", 2, 429900, "footwear")])

    result = await checkout(session.id)

    assert result["status"] == "approval_required"
    assert await Order.find_one(Order.session_id == session.id) is None
    assert (await Session.get(session.id)).state == "awaiting_approval"


@pytest.mark.asyncio
async def test_over_hard_cap_is_denied_with_reasons(db):
    session = await make_session([line("RZ-SHOE-1", 8, 429900, "footwear")])

    result = await checkout(session.id)

    assert result["status"] == "denied"
    assert any(f["rule_id"] == "R1" for f in result["findings"])
    assert await Order.find_one(Order.session_id == session.id) is None


@pytest.mark.asyncio
async def test_merchant_approval_re_evaluates_then_pays(db):
    session = await make_session([line("RZ-SHOE-1", 2, 429900, "footwear")])
    escalation = await checkout(session.id)

    result = await approval_service.decide(escalation["approval_id"], "approve", actor="merchant")

    assert result["status"] == "paid_link_created"
    assert (await Order.find_one(Order.session_id == session.id)).state == "link_sent"


@pytest.mark.asyncio
async def test_approval_re_evaluation_denies_if_stock_ran_out_meanwhile(db):
    """The merchant approved *that cart at that price* — stock moving during
    the decision window must flip the outcome, not be waved through."""
    session = await make_session([line("RZ-SHOE-1", 2, 429900, "footwear")])
    escalation = await checkout(session.id)

    product = await Product.get("RZ-SHOE-1")
    product.stock.available = 1
    await product.save()

    result = await approval_service.decide(escalation["approval_id"], "approve", actor="merchant")

    assert result["status"] == "denied"
    assert await Order.find_one(Order.session_id == session.id) is None


@pytest.mark.asyncio
async def test_rejecting_an_approval_releases_the_cart(db):
    session = await make_session([line("RZ-SHOE-1", 2, 429900, "footwear")])
    escalation = await checkout(session.id)

    result = await approval_service.decide(escalation["approval_id"], "reject", actor="merchant")

    assert result["status"] == "rejected"
    assert (await Session.get(session.id)).cart.state == "open"
    assert await Order.find_one(Order.session_id == session.id) is None


@pytest.mark.asyncio
async def test_deciding_twice_is_refused(db):
    session = await make_session([line("RZ-SHOE-1", 2, 429900, "footwear")])
    escalation = await checkout(session.id)
    await approval_service.decide(escalation["approval_id"], "reject", actor="merchant")

    with pytest.raises(RazoError) as exc:
        await approval_service.decide(escalation["approval_id"], "approve", actor="merchant")
    assert exc.value.code == "APPROVAL_EXPIRED"


@pytest.mark.asyncio
async def test_payment_service_refuses_a_non_allow_verdict(db):
    session = await make_session([line("RZ-SHOE-1", 8, 429900, "footwear")])
    _, verdict = await evaluate_cart(session)
    assert verdict.decision == "DENY"

    with pytest.raises(RazoError) as exc:
        await payment_service.execute(session, verdict)
    assert exc.value.code == "VERDICT_INVALID"


@pytest.mark.asyncio
async def test_payment_service_refuses_a_verdict_whose_cart_has_since_changed(db):
    """A signed ALLOW is bound to one exact cart — mutating the cart after
    evaluation must not be executable against the stale token."""
    session = await make_session([line("RZ-SOCK-1", 1, 49900, "apparel")])
    _, verdict = await evaluate_cart(session)
    assert verdict.decision == "ALLOW"

    session.cart.items = [line("RZ-SHOE-1", 1, 429900, "footwear")]
    session.cart.total_paise = 429900
    session.cart.version = 2
    await session.save()

    with pytest.raises(RazoError) as exc:
        await payment_service.execute(session, verdict)
    assert exc.value.code == "PRICE_MISMATCH"


@pytest.mark.asyncio
async def test_checkout_is_idempotent(db):
    session = await make_session([line("RZ-SOCK-1", 1, 49900, "apparel")])
    _, verdict = await evaluate_cart(session)

    first = await payment_service.execute(session, verdict)
    second = await payment_service.execute(session, verdict)

    assert first["order_id"] == second["order_id"]
    assert len(await Order.find(Order.session_id == session.id).to_list()) == 1


@pytest.mark.asyncio
async def test_successful_checkout_reserves_stock(db):
    session = await make_session([line("RZ-SOCK-1", 3, 49900, "apparel")])

    await checkout(session.id)

    assert (await Product.get("RZ-SOCK-1")).stock.reserved == 3


@pytest.mark.asyncio
async def test_a_cart_awaiting_approval_is_locked_against_further_additions(db):
    session = await make_session([line("RZ-SHOE-1", 2, 429900, "footwear")])
    await checkout(session.id)

    with pytest.raises(RazoError) as exc:
        await cart_service.add(session.id, "RZ-SOCK-1", 1)
    assert exc.value.code == "CART_LOCKED"


@pytest.mark.asyncio
async def test_an_approval_does_not_carry_over_to_a_cart_that_changed(db):
    """Otherwise a buyer could grow the cart while the merchant is deciding
    and have the threshold waived on a total the merchant never saw."""
    session = await make_session([line("RZ-SHOE-1", 2, 429900, "footwear")])
    escalation = await checkout(session.id)

    grown = await Session.get(session.id)
    grown.cart.items = [line("RZ-SHOE-1", 5, 429900, "footwear")]
    grown.cart.total_paise = 429900 * 5
    grown.cart.subtotal_paise = 429900 * 5
    await grown.save()

    result = await approval_service.decide(escalation["approval_id"], "approve", actor="merchant")

    assert result["status"] == "denied"
    assert await Order.find_one(Order.session_id == session.id) is None


@pytest.mark.asyncio
async def test_a_lost_idempotency_race_does_not_leak_reserved_stock(db):
    session = await make_session([line("RZ-SOCK-1", 2, 49900, "apparel")])
    _, verdict = await evaluate_cart(session)

    await payment_service.execute(session, verdict)
    reserved_after_first = (await Product.get("RZ-SOCK-1")).stock.reserved
    await payment_service.execute(session, verdict)

    assert (await Product.get("RZ-SOCK-1")).stock.reserved == reserved_after_first == 2


@pytest.mark.asyncio
async def test_empty_cart_cannot_check_out(db):
    session = await make_session([])

    with pytest.raises(RazoError) as exc:
        await checkout(session.id)
    assert exc.value.code == "VALIDATION_FAILED"
