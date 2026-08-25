from datetime import datetime, timedelta, timezone

from ulid import ULID

from app.db.documents import Approval, Session
from app.domain.intent import OrderIntent
from app.errors import RazoError
from app.payments.service import payment_service
from app.policy.policy import policy as default_policy
from app.policy.types import Verdict
from app.services.policy_service import evaluate_cart

_TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def _now() -> str:
    return datetime.now(timezone.utc).strftime(_TS_FORMAT)


def _parse(ts: str) -> datetime:
    return datetime.strptime(ts, _TS_FORMAT).replace(tzinfo=timezone.utc)


def approval_view(approval: Approval) -> dict:
    return {
        "approval_id": approval.id,
        "session_id": approval.session_id,
        "amount_paise": approval.amount_paise,
        "state": approval.state,
        "reason": approval.reason,
        "expires_at": approval.expires_at,
        "decided_by": approval.decided_by,
        "decided_at": approval.decided_at,
        "created_at": approval.created_at,
    }


class ApprovalService:
    """The merchant's approve/reject inbox. Deciding never assigns policy
    itself — approve re-invokes PolicyEngine.evaluate() before spending
    anything, because stock or prices can move during the decision window."""

    async def create(self, session: Session, intent: OrderIntent, verdict: Verdict) -> Approval:
        now = datetime.now(timezone.utc)
        expires_at = (now + timedelta(minutes=default_policy.approval_ttl_minutes)).strftime(_TS_FORMAT)
        approval = Approval(
            id=str(ULID()), session_id=session.id, evaluation_id=verdict.evaluation_id,
            intent_hash=verdict.intent_hash, amount_paise=intent.total_paise, state="pending",
            reason=verdict.reason_summary, expires_at=expires_at, created_at=now.strftime(_TS_FORMAT),
        )
        await approval.insert()
        # Lock the cart for the decision window so it cannot be grown while
        # the merchant is deciding; the intent_hash check in decide() is the
        # authoritative guard, this just stops the situation arising.
        await Session.get_motor_collection().update_one(
            {"_id": session.id}, {"$set": {"state": "awaiting_approval", "cart.state": "locked"}},
        )
        return approval

    async def _expire_if_stale(self, approval: Approval) -> Approval:
        if approval.state == "pending" and datetime.now(timezone.utc) > _parse(approval.expires_at):
            approval.state = "expired"
            await approval.save()
            await Session.get_motor_collection().update_one(
                {"_id": approval.session_id, "state": "awaiting_approval"},
                {"$set": {"state": "active", "cart.state": "open"}},
            )
        return approval

    async def list(self, state: str | None = None) -> list[Approval]:
        pending = await Approval.find(Approval.state == "pending").to_list()
        for approval in pending:
            await self._expire_if_stale(approval)
        query = Approval.find(Approval.state == state) if state else Approval.find_all()
        return await query.sort("-created_at").to_list()

    async def decide(self, approval_id: str, decision: str, actor: str, note: str | None = None) -> dict:
        approval = await Approval.get(approval_id)
        if approval is None:
            raise RazoError("APPROVAL_NOT_FOUND", 404, "No such approval.")

        approval = await self._expire_if_stale(approval)
        if approval.state == "expired":
            raise RazoError("APPROVAL_EXPIRED", 410, "The approval window closed.")
        if approval.state != "pending":
            raise RazoError("APPROVAL_EXPIRED", 410, "This approval has already been decided.")

        now = _now()
        if decision == "reject":
            approval.state = "rejected"
            approval.decided_by = actor
            approval.decided_at = now
            await approval.save()
            await Session.get_motor_collection().update_one(
                {"_id": approval.session_id}, {"$set": {"state": "active", "cart.state": "open"}},
            )
            return {"status": "rejected", "reason": note or "Rejected by the merchant."}

        if decision != "approve":
            raise RazoError("VALIDATION_FAILED", 422, "decision must be 'approve' or 'reject'.")

        session = await Session.get(approval.session_id)
        if session is None:
            raise RazoError("SESSION_NOT_FOUND", 404, "I couldn't find that session.")

        # Re-evaluate — the merchant approved *this cart at this price*, and
        # stock or prices can have moved while they were deciding. Their
        # approval satisfies R2/R7; every other rule still has to pass.
        intent, verdict = await evaluate_cart(session, merchant_approved=True)

        # The waiver applies to the cart the merchant actually saw. If the
        # cart differs at all, the approval does not carry over to it.
        if intent.hash() != approval.intent_hash:
            approval.state = "expired"
            approval.decided_by = actor
            approval.decided_at = now
            await approval.save()
            await Session.get_motor_collection().update_one(
                {"_id": session.id}, {"$set": {"state": "active", "cart.state": "open"}},
            )
            return {
                "status": "denied",
                "reason": "The cart changed after it was sent for approval, so that approval no longer "
                          "applies. Please check out again to get a fresh decision.",
            }

        approval.state = "approved"
        approval.decided_by = actor
        approval.decided_at = now
        await approval.save()

        if verdict.decision == "ALLOW":
            order = await payment_service.execute(session, verdict)
            return {"status": "paid_link_created", "reason": verdict.reason_summary, **order}
        if verdict.decision == "REQUIRE_APPROVAL":
            new_approval = await self.create(session, intent, verdict)
            return {
                "status": "approval_required", "reason": verdict.reason_summary,
                **approval_view(new_approval),
            }
        await Session.get_motor_collection().update_one(
            {"_id": session.id}, {"$set": {"state": "active", "cart.state": "open"}},
        )
        return {"status": "denied", "reason": verdict.reason_summary}


approval_service = ApprovalService()
