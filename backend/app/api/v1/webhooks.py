import hashlib
import hmac
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Request, Response
from pymongo.errors import DuplicateKeyError
from ulid import ULID

from app.audit.service import audit, audit_safe
from app.config import settings
from app.db.documents import Order, Payment
from app.domain.money import inr
from app.errors import RazoError

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@router.post("/razorpay")
async def razorpay_webhook(request: Request):
    """Reads the *raw* body and verifies HMAC-SHA256 before parsing anything
    — the signature check has to run on exactly the bytes Razorpay signed,
    not on a re-serialized copy. Always returns 200 once recorded, so
    Razorpay does not retry-storm a webhook we've already handled."""
    body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature", "")

    if settings.razorpay_webhook_secret:
        expected = hmac.new(settings.razorpay_webhook_secret.encode(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            await audit_safe(
                actor="webhook", action="webhook.signature_invalid",
                reason="A webhook arrived whose HMAC did not match the shared secret; it was rejected "
                       "without being parsed or applied.",
                outcome="failed",
            )
            raise RazoError("VALIDATION_FAILED", 400, "Invalid webhook signature.")

    event = json.loads(body)
    event_type = event.get("event", "")
    payload = event.get("payload", {})

    await audit.record(
        actor="webhook", action="webhook.received",
        input={"event": event_type},
        reason=f"Razorpay reported '{event_type}'; the signature was verified before parsing.",
    )

    if event_type == "payment.captured":
        await _ingest_payment(payload, status="captured")
    elif event_type == "payment.failed":
        await _ingest_payment(payload, status="failed")
    elif event_type == "payment_link.expired":
        await _mark_link_expired(payload)

    return Response(status_code=200)


async def _ingest_payment(payload: dict, status: str) -> None:
    entity = payload.get("payment", {}).get("entity", {})
    razorpay_payment_id = entity.get("id")
    if not razorpay_payment_id:
        return

    if await Payment.find_one(Payment.razorpay_payment_id == razorpay_payment_id) is not None:
        return  # already ingested — a Razorpay retry must be a no-op, not a double-apply

    order = await Order.find_one(Order.razorpay_order_id == entity.get("order_id"))
    payment = Payment(
        id=str(ULID()), order_id=order.id if order else "", razorpay_payment_id=razorpay_payment_id,
        status=status, method=entity.get("method"), amount_paise=entity.get("amount", 0),
        error_code=entity.get("error_code"), error_description=entity.get("error_description"),
        raw_event=payload, created_at=_now(),
    )
    try:
        await payment.insert()
    except DuplicateKeyError:
        return

    if order is not None:
        order.state = "paid" if status == "captured" else "failed"
        if status == "failed":
            order.failure_code = entity.get("error_code")
        order.updated_at = _now()
        await order.save()

        await audit.record(
            actor="payments",
            action="payment.captured" if status == "captured" else "payment.failed",
            session_id=order.session_id,
            subject={"type": "order", "id": order.id},
            output={"razorpay_payment_id": razorpay_payment_id, "amount_paise": order.amount_paise},
            reason=(
                f"Razorpay confirmed payment of {inr(order.amount_paise)} via "
                f"{entity.get('method') or 'an unknown method'}."
                if status == "captured"
                else f"The payment of {inr(order.amount_paise)} did not go through "
                     f"({entity.get('error_description') or 'no reason given'}); a fresh link can be issued."
            ),
            outcome="ok" if status == "captured" else "failed",
        )


async def _mark_link_expired(payload: dict) -> None:
    link = payload.get("payment_link", {}).get("entity", {})
    order = await Order.find_one(Order.payment_link_id == link.get("id"))
    if order is not None and order.state == "link_sent":
        order.state = "expired"
        order.updated_at = _now()
        await order.save()
