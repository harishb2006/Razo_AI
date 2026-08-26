from fastapi import APIRouter

from app.audit.chain import verify_chain
from app.audit.explain import explain_session
from app.db.documents import AuditEvent

router = APIRouter(prefix="/audit", tags=["audit"])


def _view(event: AuditEvent) -> dict:
    return {
        "id": event.id,
        "seq": event.seq,
        "session_id": event.session_id,
        "trace_id": event.trace_id,
        "actor": event.actor,
        "action": event.action,
        "subject": event.subject,
        "input": event.input,
        "output": event.output,
        "reason": event.reason,
        "outcome": event.outcome,
        "latency_ms": event.latency_ms,
        "hash": event.hash,
        "prev_hash": event.prev_hash,
        "created_at": event.created_at,
    }


@router.get("")
async def query_audit(
    session_id: str | None = None,
    actor: str | None = None,
    action: str | None = None,
    outcome: str | None = None,
    since: str | None = None,
    limit: int = 100,
):
    """FR6: the trail is a queryable database, not console output that
    scrolls away."""
    query: dict = {}
    if session_id:
        query["session_id"] = session_id
    if actor:
        query["actor"] = actor
    if action:
        query["action"] = action
    if outcome:
        query["outcome"] = outcome
    if since:
        query["created_at"] = {"$gte": since}

    events = await AuditEvent.find(query).sort("-seq").limit(min(limit, 500)).to_list()
    return [_view(e) for e in events]


@router.get("/verify")
async def verify_audit_chain():
    """Walks the whole chain and reports whether it is intact, and where it
    first breaks if not."""
    events = await AuditEvent.find_all().sort("+seq").to_list()
    return verify_chain([e.model_dump() for e in events])


@router.get("/session/{session_id}/explain")
async def explain(session_id: str):
    return await explain_session(session_id)
