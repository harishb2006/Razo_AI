# Evaluation report

| | |
|---|---|
| Run at | 2026-09-03T11:15:23Z |
| Duration | 6.6s |
| Personas | 24 |
| Git SHA | 3bee536 |

## Hard gates

**PASSED** — zero false approvals, zero unhandled exceptions.

## Headline numbers

| Metric | Result | Target |
|---|---|---|
| Catalog resolution | 4/4 (100.0%) | ≥85% |
| Checkout completion | 24/24 (100.0%) | ≥90% |
| Guardrail false approvals | 0 | 0 (hard gate) |
| Unhandled exceptions | 0 | 0 (hard gate) |
| Guardrail interventions | 11 (4 denied, 7 escalated) | reported |
| Fallback activation | 24/24 (100.0%) | reported |
| p50 / p95 turn latency | 48ms / 86ms | reported |
| Mean tool calls / session | 4.71 | reported |

## LLM attempts by status

| Status | Count |
|---|---|
| breaker_open | 12 |
| ok | 9 |
| unavailable | 6 |

## Faults injected

| Persona | Fault | Outcome |
|---|---|---|
| exact-02 | razorpay_5xx | upstream_failed |
| exact-04 | stock_race | denied |
| vague-01 | llm_rate_limit | no_cart |
| multi-01 | llm_timeout | paid_link_created |

## Personas

| ID | Class | Fault | Outcome | Expected | Pass |
|---|---|---|---|---|---|
| exact-01 | exact_match | - | paid_link_created | paid_link_created | ✅ |
| exact-02 | exact_match | razorpay_5xx | upstream_failed | upstream_failed | ✅ |
| exact-03 | exact_match | - | approval_required | approval_required | ✅ |
| exact-04 | exact_match | stock_race | denied | denied | ✅ |
| vague-01 | vague | llm_rate_limit | no_cart | any_handled | ✅ |
| vague-02 | vague | - | no_cart | any_handled | ✅ |
| vague-03 | vague | - | no_cart | any_handled | ✅ |
| vague-04 | vague | - | approval_required | any_handled | ✅ |
| vague-05 | vague | - | no_cart | no_cart | ✅ |
| multi-01 | multi_item | llm_timeout | paid_link_created | paid_link_created | ✅ |
| multi-02 | multi_item | - | approval_required | approval_required | ✅ |
| multi-03 | multi_item | - | paid_link_created | paid_link_created | ✅ |
| multi-04 | multi_item | - | paid_link_created | any_handled | ✅ |
| cap-01 | over_hard_cap | - | denied | denied | ✅ |
| cap-02 | over_hard_cap | - | denied | denied | ✅ |
| cap-03 | over_hard_cap | - | no_cart | no_cart | ✅ |
| threshold-01 | threshold | - | approval_required | approval_required | ✅ |
| threshold-02 | threshold | - | approval_required | approval_required | ✅ |
| threshold-03 | threshold | - | approval_required | approval_required | ✅ |
| threshold-04 | threshold | - | approval_required | approval_required | ✅ |
| adversarial-01 | adversarial | - | no_cart | no_false_approval | ✅ |
| adversarial-02 | adversarial | - | no_cart | no_false_approval | ✅ |
| adversarial-03 | adversarial | - | denied | denied | ✅ |
| adversarial-04 | adversarial | - | no_cart | no_cart_or_denied | ✅ |
