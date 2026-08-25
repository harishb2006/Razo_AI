from fastapi import APIRouter

from app.services.checkout_service import checkout as run_checkout

router = APIRouter(prefix="/checkout", tags=["checkout"])


@router.post("/{session_id}")
async def checkout(session_id: str):
    """Real checkout — used directly by the external buyer-agent and by the
    chat `request_checkout` tool alike, so there is exactly one code path
    from a cart to a payment, an escalation, or a refusal."""
    return await run_checkout(session_id)
