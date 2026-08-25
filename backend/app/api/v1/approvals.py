from fastapi import APIRouter
from pydantic import BaseModel

from app.services.approval_service import approval_service, approval_view

router = APIRouter(prefix="/approvals", tags=["approvals"])


class DecideRequest(BaseModel):
    decision: str  # "approve" | "reject"
    actor: str
    note: str | None = None


@router.get("")
async def list_approvals(state: str | None = None):
    approvals = await approval_service.list(state)
    return [approval_view(a) for a in approvals]


@router.post("/{approval_id}/decide")
async def decide_approval(approval_id: str, req: DecideRequest):
    return await approval_service.decide(approval_id, req.decision, req.actor, req.note)
