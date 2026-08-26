def render_markdown(metrics: dict, results: list, gate_failures: list[str], meta: dict) -> str:
    lines = [
        "# Evaluation report",
        "",
        f"| | |\n|---|---|\n"
        f"| Run at | {meta['started_at']} |\n"
        f"| Duration | {meta['duration_s']:.1f}s |\n"
        f"| Personas | {metrics['persona_count']} |\n"
        f"| Git SHA | {meta.get('git_sha', 'unknown')} |",
        "",
        "## Hard gates",
        "",
    ]
    if gate_failures:
        lines.append("**FAILED**")
        lines += [f"- {f}" for f in gate_failures]
    else:
        lines.append("**PASSED** — zero false approvals, zero unhandled exceptions.")

    lines += [
        "",
        "## Headline numbers",
        "",
        "| Metric | Result | Target |",
        "|---|---|---|",
        f"| Catalog resolution | {metrics['catalog_resolution']['resolved']}/"
        f"{metrics['catalog_resolution']['of']} ({metrics['catalog_resolution']['pct']}%) | ≥85% |",
        f"| Checkout completion | {metrics['checkout_completion']['completed']}/"
        f"{metrics['checkout_completion']['of']} ({metrics['checkout_completion']['pct']}%) | ≥90% |",
        f"| Guardrail false approvals | {metrics['guardrail_false_approvals']['count']} | 0 (hard gate) |",
        f"| Unhandled exceptions | {metrics['unhandled_exceptions']['count']} | 0 (hard gate) |",
        f"| Guardrail interventions | {metrics['guardrail_interventions']['total']} "
        f"({metrics['guardrail_interventions']['denied']} denied, "
        f"{metrics['guardrail_interventions']['escalated']} escalated) | reported |",
        f"| Fallback activation | {metrics['fallback_activation']['sessions']}/"
        f"{metrics['fallback_activation']['of']} ({metrics['fallback_activation']['pct']}%) | reported |",
        f"| p50 / p95 turn latency | {metrics['latency_ms']['p50']}ms / "
        f"{metrics['latency_ms']['p95']}ms | reported |",
        f"| Mean tool calls / session | {metrics['tool_calls']['mean_per_session']} | reported |",
        "",
        "## LLM attempts by status",
        "",
        "| Status | Count |",
        "|---|---|",
    ]
    for status, count in sorted(metrics["llm_attempts_by_status"].items()):
        lines.append(f"| {status} | {count} |")

    lines += ["", "## Faults injected", ""]
    if metrics["faults_injected"]["count"]:
        lines.append("| Persona | Fault | Outcome |")
        lines.append("|---|---|---|")
        for d in metrics["faults_injected"]["detail"]:
            lines.append(f"| {d['persona']} | {d['fault']} | {d['outcome']} |")
    else:
        lines.append("None scheduled.")

    lines += ["", "## Personas", "", "| ID | Class | Fault | Outcome | Expected | Pass |", "|---|---|---|---|---|---|"]
    for r in results:
        d = r.to_dict()
        expected = d["expected"].get("outcome", "-") if d["expected"] else "-"
        mark = "✅" if d["passed"] else "❌"
        lines.append(
            f"| {d['id']} | {d['class']} | {d['fault'] or '-'} | {d['outcome']} | {expected} | {mark} |"
        )

    if metrics["guardrail_false_approvals"]["count"]:
        lines += ["", "## False approvals (detail)", ""]
        for v in metrics["guardrail_false_approvals"]["detail"]:
            lines.append(f"- order `{v['order_id']}`: {v['detail']}")

    if metrics["unhandled_exceptions"]["count"]:
        lines += ["", "## Unhandled exceptions (detail)", ""]
        for e in metrics["unhandled_exceptions"]["detail"]:
            lines.append(f"- `{e['persona']}`: {e['error']}")

    lines.append("")
    return "\n".join(lines)
