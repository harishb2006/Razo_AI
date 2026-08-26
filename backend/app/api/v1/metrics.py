from fastapi import APIRouter

from app.db.documents import AuditEvent, EvalRun, LLMCall, Order

router = APIRouter(prefix="/metrics", tags=["metrics"])


@router.get("/latest")
async def latest_eval_run():
    """The most recent `make eval` run, exactly as committed to
    reports/metrics.md — so the console can show it without re-running
    anything."""
    run = await EvalRun.find_all().sort("-started_at").limit(1).to_list()
    if not run:
        return {"available": False}
    r = run[0]
    return {
        "available": True,
        "run_id": r.id,
        "started_at": r.started_at,
        "finished_at": r.finished_at,
        "persona_count": r.persona_count,
        "git_sha": r.git_sha,
        "metrics": r.metrics,
    }


@router.get("/live")
async def live_counters():
    """Counters since boot — cheap, free of any batch run, and safe to poll
    from the merchant console."""
    orders = await Order.find_all().to_list()
    events = await AuditEvent.find_all().to_list()
    llm_calls = await LLMCall.find_all().to_list()

    return {
        "orders_total": len(orders),
        "orders_paid_or_sent": sum(1 for o in orders if o.state in ("paid", "link_sent")),
        "orders_failed": sum(1 for o in orders if o.state in ("failed", "upstream_failed")),
        "audit_events_total": len(events),
        "policy_evaluations": sum(1 for e in events if e.action == "policy.evaluated"),
        "denials": sum(1 for e in events if e.outcome == "denied"),
        "escalations": sum(1 for e in events if e.outcome == "escalated"),
        "degraded_events": sum(1 for e in events if e.outcome == "degraded"),
        "llm_calls_total": len(llm_calls),
        "llm_calls_by_status": {
            status: sum(1 for c in llm_calls if c.status == status)
            for status in sorted({c.status for c in llm_calls})
        },
    }
