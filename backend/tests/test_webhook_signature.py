"""Webhook ingestion: the signature is verified on the raw bytes Razorpay
signed, before anything is parsed or applied."""
import hashlib
import hmac
import json
import os

os.environ["OFFLINE_MODE"] = "True"

import pytest
import pytest_asyncio
from beanie import init_beanie
from mongomock_motor import AsyncMongoMockClient

from app.api.v1.webhooks import razorpay_webhook
from app.config import settings
from app.db.documents import Approval, Message, Order, Payment, Product, Session
from app.errors import RazoError

SECRET = "whsec-test"
NOW = "2026-01-01T00:00:00Z"


class FakeRequest:
    def __init__(self, body: bytes, signature: str):
        self._body = body
        self.headers = {"X-Razorpay-Signature": signature}

    async def body(self) -> bytes:
        return self._body


def sign(body: bytes) -> str:
    return hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


def payment_event(event: str, payment_id: str, order_id: str, amount: int = 49900) -> bytes:
    return json.dumps({
        "event": event,
        "payload": {"payment": {"entity": {
            "id": payment_id, "order_id": order_id, "amount": amount,
            "method": "upi", "error_code": None, "error_description": None,
        }}},
    }).encode()


@pytest_asyncio.fixture
async def db(monkeypatch):
    monkeypatch.setattr(settings, "razorpay_webhook_secret", SECRET)
    client = AsyncMongoMockClient()
    await init_beanie(
        database=client["razo_test"],
        document_models=[Product, Session, Message, Order, Payment, Approval],
    )
    await Order(
        id="ord-1", session_id="s-1", actor_key="s-1", evaluation_id="eval-1",
        razorpay_order_id="order_rzp_1", payment_link_id="plink_1",
        amount_paise=49900, state="link_sent", idempotency_key="idem-1",
        created_at=NOW, updated_at=NOW,
    ).insert()
    yield client


@pytest.mark.asyncio
async def test_forged_signature_is_rejected_and_nothing_is_applied(db):
    body = payment_event("payment.captured", "pay_1", "order_rzp_1")

    with pytest.raises(RazoError) as exc:
        await razorpay_webhook(FakeRequest(body, "deadbeef"))

    assert exc.value.http_status == 400
    assert (await Order.get("ord-1")).state == "link_sent"
    assert await Payment.find_one(Payment.razorpay_payment_id == "pay_1") is None


@pytest.mark.asyncio
async def test_captured_payment_marks_the_order_paid(db):
    body = payment_event("payment.captured", "pay_1", "order_rzp_1")

    await razorpay_webhook(FakeRequest(body, sign(body)))

    assert (await Order.get("ord-1")).state == "paid"
    assert (await Payment.find_one(Payment.razorpay_payment_id == "pay_1")).status == "captured"


@pytest.mark.asyncio
async def test_failed_payment_marks_the_order_failed(db):
    body = payment_event("payment.failed", "pay_2", "order_rzp_1")

    await razorpay_webhook(FakeRequest(body, sign(body)))

    assert (await Order.get("ord-1")).state == "failed"


@pytest.mark.asyncio
async def test_a_redelivered_webhook_is_a_no_op(db):
    """Razorpay retries on any non-200, so ingestion has to be idempotent."""
    body = payment_event("payment.captured", "pay_1", "order_rzp_1")

    await razorpay_webhook(FakeRequest(body, sign(body)))
    await razorpay_webhook(FakeRequest(body, sign(body)))

    assert len(await Payment.find(Payment.razorpay_payment_id == "pay_1").to_list()) == 1


@pytest.mark.asyncio
async def test_expired_link_marks_the_order_expired(db):
    body = json.dumps({
        "event": "payment_link.expired",
        "payload": {"payment_link": {"entity": {"id": "plink_1"}}},
    }).encode()

    await razorpay_webhook(FakeRequest(body, sign(body)))

    assert (await Order.get("ord-1")).state == "expired"
