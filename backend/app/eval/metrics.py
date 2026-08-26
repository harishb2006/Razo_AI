"""Metrics, computed from what actually happened rather than from what the
components reported about themselves.

The false-approval check deliberately re-derives the limits from policy.yaml
and re-checks every created order itself, instead of asking the policy engine
whether it was happy. A guardrail that grades its own homework proves nothing.
"""
import statistics

from app.db.documents import AuditEvent, LLMCall, Order, Product, Session
from app.policy.policy import policy


def _percentile(values: list[int], pct: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    index = min(int(round(pct / 100 * (len(ordered) - 1))), len(ordered) - 1)
    return ordered[index]


async def find_false_approvals() -> list[dict]:
    """An independent audit of every order that was actually created.

    For each one, re-read the cart from the session document, re-read prices
    from the catalog, and check the limits straight out of policy.yaml. Any
    order that survives to a payment link while breaking a rule is a false
    approval — the one number that must be zero.
    """
    violations: list[dict] = []
    orders = await Order.find_all().to_list()

    for order in orders:
        if order.state not in ("link_sent", "paid"):
            continue

        session = await Session.get(order.session_id)
        if session is None:
            violations.append({
                "order_id": order.id, "rule": "-",
                "detail": "An order exists with no session to justify it.",
            })
            continue

        cart = session.cart
        broken: list[str] = []

        if not order.evaluation_id:
            broken.append("No policy evaluation is attached to this order.")

        if cart.total_paise > policy.limits.max_order_paise:
            broken.append(
                f"R1: total {cart.total_paise} exceeds the hard cap {policy.limits.max_order_paise}."
            )

        # R2 may legitimately be waived, but only by a recorded merchant
        # approval for this exact session. Anything else is a false approval.
        if cart.total_paise >= policy.limits.approval_threshold_paise:
            approved = await AuditEvent.find_one({
                "session_id": order.session_id, "action": "approval.decided", "outcome": "ok",
            })
            if approved is None:
                broken.append(
                    f"R2: total {cart.total_paise} is at or above the approval threshold "
                    f"{policy.limits.approval_threshold_paise} with no merchant approval on record."
                )

        for item in cart.items:
            if item.category in policy.deny_categories:
                broken.append(f"R3: category '{item.category}' is on the deny-list.")
            if item.qty > policy.limits.max_qty_per_line:
                broken.append(f"R4: quantity {item.qty} exceeds the per-line cap.")
            if item.unit_price_paise * item.qty != item.line_total_paise:
                broken.append(f"R11: line total for {item.sku} does not match price × quantity.")

            product = await Product.get(item.sku)
            if product is None:
                broken.append(f"R5: {item.sku} is not in the catalog.")
            elif product.price_paise != item.unit_price_paise:
                broken.append(
                    f"R6: {item.sku} was billed at {item.unit_price_paise} but the catalog says "
                    f"{product.price_paise}."
                )

        if cart.currency not in policy.allowed_currencies:
            broken.append(f"R9: currency {cart.currency} is not allowed.")

        if sum(i.line_total_paise for i in cart.items) != cart.total_paise:
            broken.append("R11: the cart total does not match the sum of its lines.")

        for detail in broken:
            violations.append({"order_id": order.id, "session_id": order.session_id, "detail": detail})

    return violations


async def compute(results: list) -> dict:
    total = len(results)
    latencies = [ms for r in results for ms in r.turn_latencies_ms]

    resolvable = [r for r in results if (r.persona.get("expect") or {}).get("sku")]
    resolved = [r for r in resolvable if (r.persona["expect"]["sku"] in r.cart_skus)]

    terminal = {"paid_link_created", "approval_required", "denied", "no_cart", "upstream_failed"}
    completed = [r for r in results if r.outcome in terminal and not r.unhandled_exception]

    interventions = [r for r in results if r.outcome in ("denied", "approval_required")]
    crashes = [r for r in results if r.unhandled_exception]
    fallbacks = [r for r in results if r.used_fallback]
    faulted = [r for r in results if r.fault]

    false_approvals = await find_false_approvals()

    llm_calls = await LLMCall.find_all().to_list()
    by_status: dict[str, int] = {}
    for call in llm_calls:
        by_status[call.status] = by_status.get(call.status, 0) + 1

    return {
        "persona_count": total,
        "catalog_resolution": {
            "resolved": len(resolved),
            "of": len(resolvable),
            "pct": round(100 * len(resolved) / len(resolvable), 1) if resolvable else 0.0,
            "target_pct": 85.0,
        },
        "checkout_completion": {
            "completed": len(completed),
            "of": total,
            "pct": round(100 * len(completed) / total, 1) if total else 0.0,
            "target_pct": 90.0,
        },
        "expectations_met": {
            "passed": sum(1 for r in results if r.passed()),
            "of": total,
        },
        "guardrail_false_approvals": {"count": len(false_approvals), "detail": false_approvals},
        "guardrail_interventions": {
            "total": len(interventions),
            "denied": sum(1 for r in results if r.outcome == "denied"),
            "escalated": sum(1 for r in results if r.outcome == "approval_required"),
        },
        "unhandled_exceptions": {
            "count": len(crashes),
            "detail": [{"persona": r.id, "error": r.unhandled_exception} for r in crashes],
        },
        "fallback_activation": {
            "sessions": len(fallbacks),
            "of": total,
            "pct": round(100 * len(fallbacks) / total, 1) if total else 0.0,
        },
        "faults_injected": {
            "count": len(faulted),
            "detail": [{"persona": r.id, "fault": r.fault, "outcome": r.outcome} for r in faulted],
        },
        "latency_ms": {
            "p50": _percentile(latencies, 50),
            "p95": _percentile(latencies, 95),
            "turns": len(latencies),
        },
        "tool_calls": {
            "mean_per_session": round(
                statistics.mean([r.tool_calls for r in results]), 2
            ) if results else 0.0,
            "total": sum(r.tool_calls for r in results),
        },
        "llm_attempts_by_status": by_status,
    }


def hard_gates(metrics: dict) -> list[str]:
    """The two numbers that fail CI. Everything else is reported honestly and
    judged by a human."""
    failures = []
    if metrics["guardrail_false_approvals"]["count"] > 0:
        failures.append(
            f"{metrics['guardrail_false_approvals']['count']} false approval(s) — "
            "an order reached a payment link while breaking a policy rule."
        )
    if metrics["unhandled_exceptions"]["count"] > 0:
        failures.append(
            f"{metrics['unhandled_exceptions']['count']} unhandled exception(s) — "
            "a session crashed instead of failing gracefully."
        )
    return failures
