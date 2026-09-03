import asyncio
import logging
from datetime import datetime, timezone

from ulid import ULID

from app.audit.chain import GENESIS_HASH, compute_hash
from app.db.documents import AuditEvent

log = logging.getLogger(__name__)

ACTORS = frozenset({
    "buyer", "agent", "catalog", "cart", "policy", "payments", "merchant", "webhook", "system",
})

# A closed vocabulary: an unrecognised action is a bug, not a new event type
# invented at a call site. Keeping it closed is what makes the audit
# queryable by action rather than by free text.
ACTIONS = frozenset({
    "session.started", "message.received", "llm.call", "llm.fallback", "llm.degraded",
    "tool.invoked", "catalog.searched", "growth.suggested",
    "cart.item_added", "cart.item_updated", "cart.repriced",
    "policy.evaluated", "approval.requested", "approval.decided", "approval.expired",
    "payment.order_created", "payment.link_created", "payment.captured", "payment.failed",
    "webhook.received", "webhook.signature_invalid", "agent.budget_exhausted",
    "db.unavailable", "system_error", "session.closed",
})

OUTCOMES = frozenset({"ok", "denied", "escalated", "degraded", "failed"})


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class AuditService:
    """Every meaningful action becomes one entry here. `reason` is a required
    keyword argument with no default, so the type checker refuses a call site
    that records *what* happened without *why* — that is NFR-explainability
    enforced rather than asserted."""

    def __init__(self):
        self._lock = asyncio.Lock()

    async def record(
        self,
        *,
        actor: str,
        action: str,
        reason: str,
        outcome: str = "ok",
        session_id: str | None = None,
        trace_id: str | None = None,
        subject: dict | None = None,
        input: dict | None = None,
        output: dict | None = None,
        latency_ms: int | None = None,
    ) -> str:
        if actor not in ACTORS:
            raise ValueError(f"Unknown audit actor: {actor}")
        if action not in ACTIONS:
            raise ValueError(f"Unknown audit action: {action}")
        if outcome not in OUTCOMES:
            raise ValueError(f"Unknown audit outcome: {outcome}")
        if not reason or not reason.strip():
            raise ValueError("Audit entries must carry a human-readable reason.")

        # Serialised so two concurrent writers cannot read the same tail and
        # produce two entries claiming the same predecessor.
        async with self._lock:
            seq = await self._next_seq()
            prev_hash = await self._tail_hash()

            doc = {
                "_id": str(ULID()),
                "seq": seq,
                "session_id": session_id,
                "trace_id": trace_id,
                "actor": actor,
                "action": action,
                "subject": subject,
                "input": input,
                "output": output,
                "reason": reason,
                "outcome": outcome,
                "latency_ms": latency_ms,
                "prev_hash": prev_hash,
                "created_at": _now(),
            }
            doc["hash"] = compute_hash(doc)

            from app.db.client import get_audit_collection

            await get_audit_collection().insert_one(doc)
            return doc["_id"]

    @staticmethod
    async def _next_seq() -> int:
        """Atomic $inc on `counters`, so the chain order is gap-free and does
        not depend on insertion timestamps."""
        from app.db.client import get_app_db

        result = await get_app_db().counters.find_one_and_update(
            {"_id": "audit_seq"}, {"$inc": {"value": 1}}, upsert=True, return_document=True,
        )
        # `return_document=True` is ReturnDocument.AFTER; some drivers return
        # the pre-update doc on upsert, so fall back to a read.
        if result is None or "value" not in result:
            result = await get_app_db().counters.find_one({"_id": "audit_seq"})
        return int(result["value"])

    @staticmethod
    async def _tail_hash() -> str:
        last = await AuditEvent.find_all().sort("-seq").limit(1).to_list()
        return last[0].hash if last else GENESIS_HASH


audit = AuditService()


async def audit_safe(**kwargs) -> str | None:
    """For call sites on a failure path, where losing the record is bad but
    masking the original error would be worse. Never raises."""
    try:
        return await audit.record(**kwargs)
    except Exception:
        log.exception("Failed to write audit event: %s", kwargs.get("action"))
        return None
