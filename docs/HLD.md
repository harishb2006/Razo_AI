# High-Level Design (HLD) — Razo_AI Merchant Commerce Agent

| Field | Value |
|---|---|
| Document | High-Level Design |
| System | Razo_AI — Merchant Commerce Agent |
| Program | Razorpay AI Buildathon 2026 · Track 01 |
| Version | 2.0 (MongoDB + React) |
| Date | 2026-08-22 |
| Status | Approved for build |
| Related | [LLD.md](LLD.md) · [ARCHITECTURE.md](ARCHITECTURE.md) · [../README.md](../README.md) |

---

## 1. Purpose and scope

### 1.1 Purpose
Define the system-level architecture of Razo_AI: a commerce agent that makes a Razorpay merchant transactable by **both** a human buyer over chat **and** an external AI buyer-agent over HTTP, while keeping every money movement **bounded, explainable and auditable**.

### 1.2 In scope
Catalog service, conversational agent, deterministic guardrail engine, Razorpay test-mode payment execution, human-approval path, append-only audit trail, resilience layer, synthetic evaluation harness, React buyer chat + merchant console, external buyer-agent client.

### 1.3 Out of scope
Live-mode payments, merchant onboarding/KYC, multi-tenant isolation, shipping/logistics, refunds and disputes beyond stub handlers, production-grade authn/authz (a static API key is used), horizontal scaling.

### 1.4 Design tenets

| # | Tenet | Consequence in the design |
|---|---|---|
| T1 | **The LLM proposes, a deterministic function disposes** | No code path lets model output reach the Razorpay client. `PolicyEngine.evaluate()` is a mandatory, LLM-free chokepoint. |
| T2 | **Prices come from the database, never from the model** | The `add_to_cart` tool signature has no price parameter. The server re-prices from `products` on every mutation. |
| T3 | **Every autonomous action is explainable in words** | `reason` is a required, non-defaulted field on every audit document. |
| T4 | **Fail loudly to the log, gracefully to the buyer** | Every failure-taxonomy entry has a user-facing next step and a structured audit record. |
| T5 | **Free tier only, reproducible in three env vars** | MongoDB Atlas M0, free LLM tiers, Razorpay test mode. Offline mode runs the full suite with zero keys. |
| T6 | **Determinism where it is testable** | Policy engine, pricing and state machines are pure functions, unit-tested with no network and no LLM. |

---

## 2. Requirements traceability

| Req | Component | Verification |
|---|---|---|
| FR1 Agent-readable catalog | Catalog Service · `GET /api/v1/catalog/*` · `/.well-known/agent-catalog.json` | Contract test + external buyer-agent run |
| FR2 NL search + cart building | Agent Orchestrator + Tool Layer | 24-persona batch |
| FR3 Guardrail before payment | Policy Engine (chokepoint) | `tests/test_policy_engine.py`, LLM-free, DB-free |
| FR4 Real test-mode order/link | Payment Service → Razorpay Orders + Payment Links | Live test-mode smoke test |
| FR5 Human-approval routing | Approval Service + React merchant console | Persona class `threshold_*` |
| FR6 Structured audit trail | Audit Service · append-only, hash-chained Mongo collection | `GET /api/v1/audit` + chain verifier |
| FR7 Graceful failure handling | Resilience Layer + failure taxonomy §8 | Fault-injection suite |
| FR8 Batch metrics | Eval Harness | `make eval` → `reports/metrics.md` |
| NFR Explainability | required `reason` field + `/audit/session/{id}/explain` | Review + schema validator |
| NFR Boundedness | Import restriction: `agent/**` cannot import `payments/**` | `tests/test_architecture.py` (AST call-graph) |
| NFR Auditability | Mongo collection, indexed and queryable, hash-chained, insert-only DB role | Chain verification test |
| NFR Resilience | Retry + jitter, circuit breaker, provider failover, deterministic fallback | Fault injection |
| NFR Reproducibility | `.env.example`, `make bootstrap`, seeded catalog, `OFFLINE_MODE=true` | Cold-clone run in CI |

---

## 3. System context

**Actors**
- **Human buyer** — React chat app.
- **External AI buyer-agent** — a separate Python process that discovers the catalog, resolves a cart and drives checkout over HTTP. The literal proof of "transactable by an AI buyer end to end."
- **Merchant operator** — React console: approval inbox, audit timeline, policy view.
- **Judge/panel** — read-only inspection of the audit trail and metrics.

**External systems**
- **MongoDB Atlas M0** — free shared cluster, 512 MB.
- **Razorpay test mode** — Orders API, Payment Links API, webhooks.
- **LLM providers** — Gemini (primary), Groq (fallback), deterministic `echo` provider (offline/CI).

See [ARCHITECTURE.md §1](ARCHITECTURE.md#1-system-context).

---

## 4. Logical architecture

A **React SPA** talking to a **single FastAPI process**, organised as a layered modular monolith with enforced import direction.

```
 Frontend    React 18 · Vite · TypeScript · Tailwind · TanStack Query
                 |  HTTPS / JSON
 L5 Interface    REST API v1 · MCP server (stretch)
 L4 Application  Agent Orchestrator · Session Manager · Approval Service · Eval Harness
 L3 Domain       Policy Engine · Cart & Pricing · Catalog · State Machines
 L2 Integration  LLM Router (+providers) · Razorpay Client · Resilience primitives
 L1 Persistence  MongoDB Atlas M0 · Repositories · Audit hash-chain writer
```

**Imports may only point downward.** `payments` exposes a single entry point, `PaymentService.execute(intent, verdict)`, which raises unless it is handed an `ALLOW` verdict with a valid signed token. The agent layer cannot construct a verdict; only `PolicyEngine` can. A test walks the AST call graph and fails the build if `agent/**` ever imports `payments/**`.

### 4.1 Components

| # | Component | Responsibility | Explicitly not responsible for |
|---|---|---|---|
| C1 | **Catalog Service** | Product documents, text + facet search, stock, agent-readable manifest and JSON Schema | Pricing a cart |
| C2 | **Agent Orchestrator** | Bounded tool-calling loop (≤6 iterations, ≤20 s), intent → tools → reply | Calling Razorpay; setting prices |
| C3 | **Tool Layer** | Typed, validated tools exposed to the LLM, scoped to one session | Bypassing the policy chokepoint |
| C4 | **LLM Router** | Provider abstraction, retry/backoff, circuit breaker, failover chain, token/latency accounting | Business logic |
| C5 | **Cart & Pricing** | Server-side re-pricing, quantity/stock validation, totals in paise | Trusting any model-supplied number |
| C6 | **Policy Engine** | Deterministic evaluation of an `OrderIntent` → `ALLOW` / `REQUIRE_APPROVAL` / `DENY` + reasons | Any LLM call; any network I/O |
| C7 | **Approval Service** | Escalation records, merchant decisions, TTL expiry, re-evaluate-then-resume | Deciding policy itself — it re-invokes C6 |
| C8 | **Payment Service** | Razorpay orders and payment links, idempotency, webhook ingestion and signature verification | Acting without a valid `ALLOW` verdict token |
| C9 | **Audit Service** | Append-only, hash-chained, timestamped event log; query and explain endpoints | Updating or deleting anything |
| C10 | **Resilience Layer** | Retry with jitter, circuit breaker, timeouts, outbound rate shaping, deterministic fallback | — |
| C11 | **Eval Harness** | 24 synthetic personas, fault injection, metrics, report generation | — |
| C12 | **React Buyer App** | Chat UI, cart panel, policy-verdict banner, payment-link handoff | Any business rule — display only |
| C13 | **React Merchant Console** | Approval inbox, audit timeline, live metrics, policy view | Re-implementing policy |
| C14 | **External Buyer-Agent** (stretch) | Independent process transacting against C1/C3 as an outsider | Sharing any in-process state with the server |

Component diagram: [ARCHITECTURE.md §2](ARCHITECTURE.md#2-components).

---

## 5. Key flows

### 5.1 Happy path
Buyer message → orchestrator → LLM proposes `search_catalog`, then `add_to_cart(sku, qty)` → cart re-priced from MongoDB → LLM proposes `request_checkout` → orchestrator builds an `OrderIntent` **from persisted documents only** → Policy Engine returns `ALLOW` + signed verdict token → Payment Service creates a Razorpay test-mode order and payment link under an idempotency key → audit written at every step → buyer receives link plus a plain-English explanation.

### 5.2 Escalation path
Policy returns `REQUIRE_APPROVAL`. **No payment call is made and no token is issued.** An approval document is created with a 30-minute TTL; the merchant console shows it with the full finding list. On approve, the intent is **rebuilt and re-evaluated** — stock and prices can move during the approval window — and only then executed. On reject or expiry, the cart is released and the buyer told why.

### 5.3 Denial path
`DENY` → no payment call, and the buyer gets the complete list of violated rules with a suggested remedy.

### 5.4 Degraded path — the required handled failure
Primary provider 429/timeout → retry ×2 with exponential backoff and jitter → circuit opens → Groq fallback → if that fails, **deterministic non-LLM path**: Mongo text search on the raw message plus a templated reply, labelled `mode: "degraded"`. The buyer never sees a stack trace. The audit records provider, attempt count, latency and which fallback was taken. **Guardrails are unaffected by degradation** — a degraded turn still goes through the full rulebook at checkout.

Sequences: [ARCHITECTURE.md §3–§5](ARCHITECTURE.md#3-normal-purchase).

---

## 6. Guardrail model

An `OrderIntent` is evaluated against eleven rules. **Evaluation is total** — every rule runs, all findings are collected, and the most restrictive outcome wins. The audit therefore shows *all* reasons, not just the first hit.

| ID | Rule | Default | Verdict on breach |
|---|---|---|---|
| R1 | Per-order hard cap | ₹25,000 | `DENY` |
| R2 | Approval threshold | ₹5,000 | `REQUIRE_APPROVAL` |
| R3 | Category deny-list | `gift_card`, `crypto`, `alcohol`, `tobacco` | `DENY` |
| R4 | Per-line quantity cap | 10 | `DENY` |
| R5 | Stock availability | must be in stock | `DENY` |
| R6 | Price integrity — line price vs current catalog | exact match | `DENY` |
| R7 | Session spend velocity | ₹40,000 / rolling 24 h | `REQUIRE_APPROVAL` |
| R8 | Order frequency | 5 / hour / session | `DENY` |
| R9 | Currency allow-list | `INR` | `DENY` |
| R10 | Buyer-agent mandate scope | cart must fit declared budget + categories | `DENY` |
| R11 | Cart integrity — recomputed total matches | exact | `DENY` |

Precedence: `DENY` > `REQUIRE_APPROVAL` > `ALLOW`. Policy is versioned data (`policy.yaml`, hashed into every evaluation document), so a judge can tie any verdict to an exact policy revision.

Autonomous buyers get tighter limits than humans — `buyer_agent.max_order_paise` defaults to ₹10,000 against the human ₹25,000. Deliberate, and a good panel talking point.

---

## 7. Data architecture — MongoDB

**Store:** MongoDB Atlas **M0 free tier** (512 MB, shared, replica set, no credit card). Driver: Motor (async) with Beanie ODM over Pydantic v2 models — the same models used for API validation.

**Thirteen collections:** `products`, `sessions`, `messages`, `policies`, `policy_evaluations`, `approvals`, `orders`, `payments`, `audit_events`, `counters`, `idempotency_keys`, `llm_calls`, `eval_runs`.

Two document-model decisions that earn MongoDB its place here rather than merely tolerating it:

- **Stock is embedded in the product document.** Reserving stock is one atomic conditional update (`$inc` guarded by a filter on available quantity) — no separate inventory table, no cross-document race.
- **The cart is embedded in the session document.** A cart is small, bounded, and belongs to exactly one session, so every cart mutation is a single-document atomic update with an optimistic-concurrency version guard. Two concurrent `add_to_cart` calls cannot interleave into a corrupt cart.

`messages`, `audit_events` and `llm_calls` stay separate collections precisely because they grow without bound — embedding them would push documents toward the 16 MB limit.

### 7.1 Replacing what a relational schema gave us

Dropping SQL costs three guarantees. Each is replaced deliberately, not waved away — this is the honest answer when the panel asks why MongoDB.

| Guarantee | SQL mechanism | MongoDB mechanism here |
|---|---|---|
| No order without a policy verdict | `NOT NULL` foreign key | `$jsonSchema` validator on `orders` with `evaluation_id` in `required`, `validationAction: "error"`. The server rejects the insert. |
| Audit log cannot be edited or deleted | `UPDATE`/`DELETE` triggers | A **second Atlas database user** holding a custom role with only `find` and `insert` on `audit_events`. The audit writer uses that connection; it has no update or delete privilege at all. Plus the hash chain for tamper evidence. |
| No double-charge | `UNIQUE` constraint | Unique index on `orders.idempotency_key` — a duplicate insert raises `DuplicateKeyError`, which the service catches and replays the stored response. |
| Value ranges (qty > 0, price > 0) | `CHECK` constraints | `$jsonSchema` `minimum` / `exclusiveMinimum` on the same collections |
| Atomic multi-step writes | transactions | Atlas M0 is a replica set, so **multi-document transactions are available** and are used for the checkout write set |

**Transactions matter and are used.** The checkout write — mark cart `locked`, insert `policy_evaluations`, insert `orders`, decrement stock — runs inside one Mongo transaction so a mid-sequence failure cannot leave an order with no evaluation or stock decremented against an order that was never created.

`audit_events` is deliberately written **outside** that transaction, immediately and unconditionally. If the business transaction aborts, the audit entry recording the attempt and its failure must still survive. Losing the record of a failure is worse than an orphaned log line.

ER-style diagram: [ARCHITECTURE.md §6](ARCHITECTURE.md#6-data-model). Full schemas, validators and indexes: [LLD §4](LLD.md#4-data-model-mongodb).

---

## 8. Failure taxonomy

| Class | Trigger | Handling | Buyer-facing next step |
|---|---|---|---|
| F1 LLM rate limit | HTTP 429 | backoff ×2 → failover → degraded mode | "Still here — searching the catalog directly." |
| F2 LLM timeout | > 12 s | as F1 | as above |
| F3 Malformed tool call | Pydantic validation fails | one repair prompt, then degraded | transparent, no crash |
| F4 Out of stock | stock check / R5 | suggest nearest in-stock alternative | "X is out of stock; Y is available." |
| F5 Price drift | R6 mismatch | `DENY`, re-quote from catalog | "Price changed to ₹N — confirm?" |
| F6 Razorpay 5xx / network | HTTP status | 3 idempotent retries, then save cart and notify | "Payment provider is slow; your cart is saved." |
| F7 Razorpay 4xx | HTTP status | no retry, audit, escalate | specific reason |
| F8 Payment declined / link expired | webhook | mark order `failed`, offer a fresh link | "Payment didn't go through — here's a new link." |
| F9 Approval TTL expiry | 30-min sweeper | close as `expired`, release cart | "Approval window closed; start again?" |
| F10 Agent loop runaway | > 6 iterations or > 20 s | halt with a best-effort summary | honest partial answer |
| F11 **Mongo unavailable / M0 throttled** | driver `ServerSelectionTimeoutError` | 3 retries with backoff; readiness probe flips unhealthy; catalog served from an in-memory snapshot loaded at boot so browsing survives | "Can't take orders this moment — browsing still works." |
| F12 Mongo write conflict | `WriteConflict` on a transaction | retry the transaction up to 3× (standard Mongo pattern) | invisible to the buyer |

**F1 is the designated demo failure** — it is the most likely thing to actually happen on a free tier, which makes demonstrating it honest rather than staged.

---

## 9. Cross-cutting concerns

- **Security.** `X-API-Key` on write endpoints; CORS restricted to the frontend origin; Razorpay keys and the Mongo connection string only in env, never logged (a regex redaction filter on the logger); webhook HMAC-SHA256 verified on the **raw body before parsing**; queries built from typed Beanie models, never string concatenation, so no `$where` or operator-injection surface. Prompt-injection containment: catalog text reaches the model as data, and no tool accepts a model-authored price or amount.
- **Observability.** Structured JSON logs keyed by `trace_id` (`session_id:turn`); `/health` liveness and `/health/ready` (Mongo ping + provider probe); per-turn latency, tokens and provider persisted in `llm_calls`.
- **Idempotency.** Every payment-creating call carries `idempotency_key = sha256(session_id|cart_version|intent_hash)`, enforced by a unique index and replayed on duplicate.
- **Config.** `pydantic-settings`; three required env vars (`MONGODB_URI`, `RAZORPAY_KEY_ID`/`SECRET`, `GEMINI_API_KEY`). `OFFLINE_MODE=true` runs against `mongodb-memory-server`-equivalent (`mongomock-motor`) with fake payments and the `echo` provider — zero keys, zero network, full test suite.
- **Time and money.** All timestamps UTC ISO-8601 `Z`. Money is **integer paise** end to end — and on MongoDB this is load-bearing: BSON `double` would silently introduce float error, so all amount fields are declared `bsonType: "long"` in the schema validators and the API rejects any non-integer amount.

---

## 10. Deployment — free tier only

| Concern | Choice | Free-tier limit | Why it is enough |
|---|---|---|---|
| Frontend | Vercel / Netlify / Cloudflare Pages | 100 GB bandwidth/mo | Static React bundle |
| Backend | Local `uvicorn`; optional Render Free / Fly.io | 512 MB, sleeps when idle | Demo is a video plus a local judge run |
| Database | **MongoDB Atlas M0** | 512 MB storage, shared CPU, 500 connections | Dataset is a few MB; one demo user |
| LLM primary | Gemini Flash / Flash-Lite | RPM/RPD caps, no card | Strong tool-calling; the caps are exactly the failure we handle |
| LLM fallback | Groq | free, rate-limited | Sub-second responses keep the demo watchable |
| LLM offline | in-repo `echo` provider | free | CI and judges without keys |
| Payments | Razorpay **test mode** | free | Mandated by the track |
| CI | GitHub Actions | 2,000 min/mo | Lint, unit, contract tests |
| Webhook tunnel | `cloudflared` quick tunnel | free | Webhook demo only |
| Keep-alive | UptimeRobot | 50 monitors | Stops the free host and Atlas idling before the demo |

**Total recurring cost: ₹0.** No component requires a credit card.

**Atlas M0 specifics to respect:** connection pool capped at 10 (`maxPoolSize=10`) so we never approach the 500-connection ceiling; no `$search` (Atlas Search) dependency — a plain compound **text index** is used, which is available on M0; storage stays a few MB against the 512 MB cap; and the cluster is pinged every five minutes before the demo window so it is warm.

Deployment diagram: [ARCHITECTURE.md §7](ARCHITECTURE.md#7-deployment).

---

## 11. Frontend architecture

Two React routes in one Vite app, sharing one generated API client.

| Area | Choice | Rationale |
|---|---|---|
| Build | Vite + React 18 + TypeScript | Fast HMR; a 14-day budget cannot afford a slow toolchain |
| Styling | Tailwind CSS | No design system to build; consistent output on video |
| Server state | TanStack Query | Polling the approval inbox and audit feed for free; no Redux needed |
| Client state | React `useState`/`useReducer` | The app is genuinely small |
| API types | `openapi-typescript` generated from FastAPI's OpenAPI schema | The frontend cannot drift from the backend contract |
| Routing | React Router — `/` buyer chat, `/console` merchant | Two surfaces, one bundle |
| Realtime | Polling every 3 s on the console; SSE for chat token streaming is a stretch item | Polling is free and cannot fail live |

**The frontend contains no business rules.** It renders the verdict the backend returned — decision, findings, reason — and never computes a total, a limit or an eligibility itself. This matters for the demo: what appears on screen is provably the server's decision, not a UI approximation of it.

Three screens carry the pitch: the **chat** (with a policy banner that turns amber on escalation and red on denial), the **approval inbox** (full cart, every finding, approve/reject), and the **audit timeline** (the "explain this session" narrative, rendered as a vertical timeline with the reason on each entry).

---

## 12. Evaluation strategy

24 synthetic persona sessions across six classes — exact match, vague, multi-item, over-cap denial, approval threshold, and adversarial (prompt injection, price manipulation, banned category, out of stock). Each is replayed through the real pipeline against a seeded catalog; four runs additionally inject faults (429, timeout, Razorpay 500, stock race).

Reported: catalog resolution accuracy, checkout completion rate, **guardrail false approvals (hard gate: 0)**, guardrail interventions, **unhandled exceptions (hard gate: 0)**, p50/p95 turn latency, fallback activation rate, mean tool calls per session. Output is committed to `reports/metrics.md` so the numbers are inspectable rather than claimed. Both hard gates fail CI.

---

## 13. Architecture decision records

| ADR | Decision | Rationale | Rejected alternative |
|---|---|---|---|
| ADR-1 | Deterministic policy engine as a hard chokepoint | The stated rubric bar; testable with the LLM unplugged | LLM self-critique guardrail — unfalsifiable |
| ADR-2 | Modular monolith, one backend process | 14-day budget; judges run one command | Microservices — needless ops cost |
| ADR-3 | **MongoDB Atlas M0** | Free forever with no card; flexible documents fit carts, findings arrays and audit payloads without migrations; embedding cart-in-session makes cart mutation single-document atomic; M0 is a replica set so transactions are available | SQLite — no hosted story for a React frontend on Vercel; Postgres free tiers — sleep aggressively and need a card |
| ADR-4 | `$jsonSchema` validators + unique indexes + a restricted DB role, to replace FK/trigger guarantees | Keeps "the database itself rejects an order with no verdict" true on a document store | Application-level checks only — a weaker claim under questioning |
| ADR-5 | Cart embedded in session; stock embedded in product | Turns the two highest-contention writes into single-document atomic updates | Separate carts/inventory collections — reintroduces cross-document races with no upside at this scale |
| ADR-6 | Server-side re-pricing; tool signature has no price argument | Removes the entire class of model-invented-price exploits at the type level | Trust model output and validate loosely |
| ADR-7 | Hash-chained audit log written by an insert-only DB user | Tamper evidence plus tamper resistance; roughly an hour of work and a strong panel answer | Plain append collection |
| ADR-8 | Multi-provider LLM router with a non-LLM final fallback | Free-tier limits are a certainty, not a risk | Single provider plus retry |
| ADR-9 | React SPA, backend-owned rules | Credible on video; generated types prevent contract drift; no rule is duplicated in the UI | Server-rendered templates — weaker demo, and the merchant console genuinely needs interactivity |
| ADR-10 | Integer paise, `bsonType: long` | BSON doubles would introduce silent float error in the money path | BSON `Decimal128` — heavier, and unnecessary for integer paise |
| ADR-11 | Second, out-of-process buyer agent | Literal answer to "transactable by an AI buyer end to end" | Simulating the buyer in-process — proves nothing |

---

## 14. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Free-tier LLM limits mid-demo | High | Failover chain, degraded mode, pre-recorded backup clip |
| Atlas M0 slow or unreachable during the demo | Medium | F11 handling, keep-alive ping, in-memory catalog snapshot, local Mongo fallback for the recording |
| M0 has no Atlas Search | Low | Plain text index plus deterministic re-ranking in Python — also more reproducible for the eval |
| Losing SQL's referential integrity | Medium | ADR-4: schema validators, unique indexes, restricted role, transactions |
| Frontend eats days 12–13 | Medium | Tailwind, no component library, three screens only; the console is cut before the chat |
| Scope creep | High | MVP frozen; C14 and the fuller dashboard are cut first |
| Prompt injection via catalog text | Medium | Catalog content is data-only; tools take SKUs not prices; R6 and R11 catch tampering |

---

## 15. Build sequence

| Days | Deliverable | Exit criterion |
|---|---|---|
| 1–2 | FastAPI skeleton, Atlas M0, Beanie models, validators, indexes, seeded catalog | External `curl` resolves a product |
| 3–4 | LLM router, tool layer, orchestrator | Chat builds a correct cart |
| 5–6 | **Policy engine + unit tests** | An over-cap cart is denied with the LLM stubbed out entirely |
| 7–8 | Razorpay orders/links, idempotency, webhooks, approvals | A real test-mode link is paid end to end |
| 9 | Audit service, hash chain, restricted role, explain endpoint | `make verify-audit` passes |
| 10 | Resilience layer, fault injection | A 429 storm produces zero crashes |
| 11 | Eval harness, 24 personas, metrics report | `reports/metrics.md` generated |
| 12 | React chat app | Demo is watchable |
| 13 | React merchant console, external buyer-agent, repo polish | Two agents transact with no shared state |
| 14 | Video, rehearsal, submit | README checklist complete |
