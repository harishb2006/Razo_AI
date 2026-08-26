from dataclasses import asdict

from app.db.documents import Session
from app.errors import RazoError
from app.payments.service import payment_service
from app.services.approval_service import approval_service, approval_view
from app.services.policy_service import evaluate_cart


async def checkout(session_id: str) -> dict:
    """The only function that turns a verdict into money, an escalation, or
    a refusal — reused by the chat `request_checkout` tool, the REST
    endpoint, and (eventually) the external buyer-agent, so there is exactly
    one place this logic lives."""
    session = await Session.get(session_id)
    if session is None:
        raise RazoError("SESSION_NOT_FOUND", 404, "I couldn't find that session.")
    if not session.cart.items:
        raise RazoError("VALIDATION_FAILED", 422, "Your cart is empty.")
    if session.cart.state != "open":
        raise RazoError("VALIDATION_FAILED", 422, "This cart is already being checked out.")

    intent, verdict = await evaluate_cart(session, purpose="checkout")

    if verdict.decision == "DENY":
        return {
            "status": "denied", "reason": verdict.reason_summary,
            "findings": [asdict(f) for f in verdict.findings if f.outcome != "pass"],
        }

    if verdict.decision == "REQUIRE_APPROVAL":
        approval = await approval_service.create(session, intent, verdict)
        return {"status": "approval_required", "reason": verdict.reason_summary, **approval_view(approval)}

    order = await payment_service.execute(session, verdict)
    return {"status": "paid_link_created", "reason": verdict.reason_summary, **order}
