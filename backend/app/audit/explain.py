from app.db.documents import AuditEvent
from app.domain.money import inr

_HEADLINES = {
    "session.started": "The buyer opened a conversation.",
    "message.received": "The buyer sent a message.",
    "llm.call": "The assistant thought about what to do next.",
    "llm.fallback": "The main AI provider failed, so a backup took over.",
    "llm.degraded": "No AI provider was reachable, so the system searched the catalog directly.",
    "tool.invoked": "The assistant used a tool.",
    "catalog.searched": "The catalog was searched.",
    "cart.item_added": "An item was added to the cart.",
    "cart.item_updated": "A cart line was changed.",
    "cart.repriced": "The cart was re-priced from the catalog.",
    "policy.evaluated": "The rulebook checked the order.",
    "approval.requested": "The order was sent to the merchant for approval.",
    "approval.decided": "The merchant made a decision.",
    "approval.expired": "The approval window closed with no decision.",
    "payment.order_created": "A Razorpay order was created.",
    "payment.link_created": "A payment link was issued to the buyer.",
    "payment.captured": "The payment succeeded.",
    "payment.failed": "The payment did not go through.",
    "webhook.received": "Razorpay reported a payment event.",
    "webhook.signature_invalid": "A webhook arrived with an invalid signature and was rejected.",
    "agent.budget_exhausted": "The assistant ran out of its time budget and answered with what it had.",
    "db.unavailable": "The database was unreachable.",
    "system_error": "Something went wrong unexpectedly.",
    "session.closed": "The conversation ended.",
}

_OUTCOME_WORDS = {
    "ok": "worked",
    "denied": "refused",
    "escalated": "sent for approval",
    "degraded": "ran in degraded mode",
    "failed": "failed",
}


def _line(index: int, event: AuditEvent) -> dict:
    headline = _HEADLINES.get(event.action, event.action)
    return {
        "step": index,
        "at": event.created_at,
        "actor": event.actor,
        "action": event.action,
        "headline": headline,
        "reason": event.reason,
        "outcome": event.outcome,
        "outcome_word": _OUTCOME_WORDS.get(event.outcome, event.outcome),
        "latency_ms": event.latency_ms,
        "seq": event.seq,
    }


async def explain_session(session_id: str) -> dict:
    """Turns the raw log into a numbered plain-English story of what happened
    and why. Nothing here re-derives anything — it only renders the `reason`
    each component recorded at the time it acted."""
    events = await AuditEvent.find(AuditEvent.session_id == session_id).sort("+seq").to_list()
    steps = [_line(i, e) for i, e in enumerate(events, start=1)]

    verdicts = [e for e in events if e.action == "policy.evaluated"]
    payments = [e for e in events if e.action == "payment.link_created"]
    denials = [e for e in events if e.outcome == "denied"]
    escalations = [e for e in events if e.outcome == "escalated"]
    degraded = [e for e in events if e.outcome == "degraded"]

    if payments:
        ending = "The buyer received a payment link."
    elif escalations:
        ending = "The order is waiting on the merchant's approval."
    elif denials:
        ending = "The order was refused, and the buyer was told why."
    else:
        ending = "The conversation did not reach a checkout."

    summary = (
        f"{len(steps)} recorded steps. The rulebook ran {len(verdicts)} time(s); "
        f"{len(denials)} refusal(s) and {len(escalations)} escalation(s). {ending}"
    )
    if degraded:
        summary += f" {len(degraded)} step(s) ran without an AI provider."

    return {
        "session_id": session_id,
        "step_count": len(steps),
        "summary": summary,
        "steps": steps,
    }


def narrate(explanation: dict) -> str:
    """Plain-text rendering, for the CLI and the pitch screen."""
    lines = [f"Session {explanation['session_id']}", explanation["summary"], ""]
    for step in explanation["steps"]:
        lines.append(f"{step['step']:>3}. [{step['actor']}] {step['headline']} ({step['outcome_word']})")
        lines.append(f"     why: {step['reason']}")
    return "\n".join(lines)


def money(paise: int | None) -> str:
    return inr(paise) if paise is not None else "—"
