# Low-Level Design (LLD) — Razo_AI Merchant Commerce Agent

| Field | Value |
|---|---|
| Document | Low-Level Design |
| Version | 2.0 (MongoDB + React) · 2026-08-22 |
| Depends on | [HLD.md](HLD.md) · [ARCHITECTURE.md](ARCHITECTURE.md) |
| Backend | Python 3.11 · FastAPI 0.115 · Motor 3.6 · Beanie 1.27 · Pydantic v2 |
| Database | MongoDB Atlas **M0 free tier** (512 MB, replica set) |
| Frontend | React 18 · Vite 5 · TypeScript 5 · Tailwind 3 · TanStack Query 5 |

> Conventions: money is an **integer number of paise** stored as BSON `long`; timestamps are UTC ISO-8601 `Z`; document ids are ULID strings used as `_id`; every failure raises a typed `RazoError`, never a bare `Exception`.

---

## 1. Repository layout

```
Razo_AI/
├── backend/
│   ├── app/
│   │   ├── main.py              # app factory, lifespan (Mongo connect + index sync), CORS, handlers
│   │   ├── config.py            # Settings, env parsing, log redaction
│   │   ├── deps.py              # DI: db, api-key auth, trace context
│   │   ├── errors.py            # RazoError hierarchy + code registry
│   │   ├── db/
│   │   │   ├── client.py        # Motor clients: app client + insert-only audit client
│   │   │   ├── documents.py     # Beanie Documents for all 13 collections
│   │   │   ├── validators.py    # $jsonSchema definitions, applied at startup
│   │   │   ├── indexes.py       # index definitions, applied at startup
│   │   │   └── repositories/    # product_repo, session_repo, order_repo, audit_repo, ...
│   │   ├── api/v1/
│   │   │   ├── catalog.py  chat.py  checkout.py  approvals.py
│   │   │   ├── audit.py    metrics.py  webhooks.py  health.py
│   │   │   └── schemas/         # request/response DTOs
│   │   ├── agent/
│   │   │   ├── orchestrator.py  # bounded tool-calling loop
│   │   │   ├── session.py       # SessionManager, turn budget, transcript window
│   │   │   ├── prompts/system.md
│   │   │   ├── llm/
│   │   │   │   ├── base.py router.py gemini.py groq.py echo.py
│   │   │   └── tools/
│   │   │       ├── registry.py search_catalog.py get_product.py
│   │   │       ├── add_to_cart.py update_cart_item.py get_cart.py
│   │   │       └── check_policy.py request_checkout.py
│   │   ├── domain/
│   │   │   ├── money.py cart.py catalog.py intent.py states.py
│   │   ├── policy/
│   │   │   ├── engine.py rules.py verdict.py policy.yaml
│   │   ├── payments/
│   │   │   ├── razorpay_client.py service.py idempotency.py webhooks.py
│   │   ├── audit/
│   │   │   ├── service.py chain.py explain.py
│   │   ├── resilience/
│   │   │   ├── retry.py breaker.py timeouts.py ratelimit.py
│   │   └── eval/
│   │       ├── personas.json runner.py faults.py metrics.py report.py
│   ├── tests/                   # unit · contract · integration · fault · architecture
│   ├── scripts/                 # seed_catalog.py, verify_audit.py, smoke_razorpay.py
│   └── pyproject.toml
├── frontend/
│   ├── src/
│   │   ├── main.tsx  App.tsx  router.tsx
│   │   ├── api/
│   │   │   ├── client.ts        # fetch wrapper, X-API-Key, trace id
│   │   │   ├── generated.ts     # openapi-typescript output — DO NOT EDIT
│   │   │   └── hooks.ts         # TanStack Query hooks
│   │   ├── pages/
│   │   │   ├── ChatPage.tsx     # buyer
│   │   │   ├── ConsolePage.tsx  # merchant: inbox + metrics
│   │   │   └── AuditPage.tsx    # session timeline / explain view
│   │   ├── components/
│   │   │   ├── MessageList.tsx  MessageInput.tsx  CartPanel.tsx
│   │   │   ├── PolicyBanner.tsx ApprovalCard.tsx  AuditTimeline.tsx
│   │   │   ├── MetricTile.tsx   DegradedBadge.tsx
│   │   ├── lib/money.ts         # paise → "₹4,299.00" — formatting only, no arithmetic
│   │   └── types.ts
│   ├── index.html  vite.config.ts  tailwind.config.js  package.json
├── buyer_agent/                 # STRETCH: out-of-process external AI buyer
│   ├── agent.py discovery.py mandate.py
├── docs/  reports/  .env.example  Makefile  README.md
```

**Import rule (enforced by `backend/tests/test_architecture.py` via AST):**
`api → agent → domain → policy → payments → resilience → db`.
`agent/**` importing `payments/**` fails the build.

---

## 2. Configuration

| Env var | Required | Default | Notes |
|---|---|---|---|
| `MONGODB_URI` | yes* | — | `mongodb+srv://…` Atlas M0 |
| `MONGODB_DB` | no | `razo_ai` | |
| `MONGODB_AUDIT_URI` | no | falls back to `MONGODB_URI` | insert-only Atlas user; see §4.6 |
| `RAZORPAY_KEY_ID` | yes* | — | `rzp_test_…` |
| `RAZORPAY_KEY_SECRET` | yes* | — | redacted in all logs |
| `RAZORPAY_WEBHOOK_SECRET` | no | — | webhook demo only |
| `GEMINI_API_KEY` | yes* | — | primary provider |
| `GROQ_API_KEY` | no | — | enables fallback tier 2 |
| `LLM_PROVIDER_CHAIN` | no | `gemini,groq,echo` | ordered failover |
| `LLM_TIMEOUT_S` | no | `12` | per attempt |
| `API_KEY` | no | `dev-local-key` | `X-API-Key` on write routes |
| `VERDICT_SIGNING_KEY` | no | derived from `API_KEY` | HMAC for verdict tokens |
| `CORS_ORIGINS` | no | `http://localhost:5173` | Vite dev server |
| `AGENT_MAX_TOOL_ITERS` | no | `6` | |
| `AGENT_TURN_BUDGET_S` | no | `20` | |
| `MONGO_MAX_POOL_SIZE` | no | `10` | well under M0's 500-connection cap |
| `OFFLINE_MODE` | no | `false` | `mongomock-motor` + `echo` provider + fake Razorpay |

\* Not required when `OFFLINE_MODE=true` — how CI and a keyless judge run the full suite.

Frontend env: `VITE_API_BASE_URL`, `VITE_API_KEY` (demo only).

Secrets never reach logs — a logging filter redacts `mongodb+srv://[^ ]+`, `rzp_test_[A-Za-z0-9]+`, `key_secret`, `Authorization` and `X-API-Key` values.

---

## 3. Error model

```python
class RazoError(Exception):
    code: str            # stable, documented
    http_status: int
    user_message: str    # safe to show a buyer
    detail: dict         # structured, goes to audit, never to the buyer
    retryable: bool
```

| Code | HTTP | Retryable | User message |
|---|---|---|---|
| `VALIDATION_FAILED` | 422 | no | "I couldn't read that request." |
| `PRODUCT_NOT_FOUND` | 404 | no | "I couldn't find that product." |
| `OUT_OF_STOCK` | 409 | no | "That's out of stock — here's the closest alternative." |
| `PRICE_MISMATCH` | 409 | no | "The price changed since I quoted it." |
| `POLICY_DENIED` | 403 | no | reason string from the engine |
| `APPROVAL_REQUIRED` | 202 | no | "Sent to the merchant for approval." |
| `APPROVAL_EXPIRED` | 410 | no | "The approval window closed." |
| `VERDICT_INVALID` | 500 | no | generic — indicates a bug; alerts loudly |
| `LLM_UNAVAILABLE` | 503 | yes | "Working in direct-search mode right now." |
| `PAYMENT_UPSTREAM` | 502 | yes | "Payment provider is slow; your cart is saved." |
| `PAYMENT_REJECTED` | 402 | no | specific gateway reason |
| `DB_UNAVAILABLE` | 503 | yes | "Can't take orders this moment — browsing still works." |
| `WRITE_CONFLICT` | 409 | yes | invisible; retried internally |
| `RATE_LIMITED` | 429 | yes | "One moment." |
| `SYSTEM_ERROR` | 500 | no | generic |

One FastAPI handler maps `RazoError` → `{error: {code, message, trace_id}}` and writes an audit document. A catch-all handler records `SYSTEM_ERROR` for anything unhandled — **no stack trace ever reaches a client** (FR7).

---

## 4. Data model (MongoDB)

Database `razo_ai`, 13 collections. All documents carry `created_at: str` (ISO-8601 Z). Money fields are BSON `long`.

### 4.1 `products` — catalog of record

```json
{
  "_id": "RZ-SHOE-114",
  "title": "Trailrunner X",
  "description": "Lightweight road running shoe...",
  "category": "footwear",
  "brand": "Vaayu",
  "price_paise": { "$numberLong": "429900" },
  "currency": "INR",
  "attributes": { "size": ["8","9","10"], "colour": "blue", "tags": ["running"] },
  "stock": { "available": 40, "reserved": 2 },
  "search_text": "trailrunner x vaayu footwear running blue lightweight road",
  "active": true,
  "version": 3,
  "updated_at": "2026-08-22T09:12:03Z"
}
```
`_id` **is** the SKU — natural key, no secondary lookup. **Stock is embedded**, so reservation is one atomic conditional update:
```python
res = await db.products.update_one(
    {"_id": sku, "active": True,
     "$expr": {"$gte": [{"$subtract": ["$stock.available", "$stock.reserved"]}, qty]}},
    {"$inc": {"stock.reserved": qty}}, session=txn)
if res.modified_count == 0: raise RazoError("OUT_OF_STOCK", ...)
```
The filter and the update are one operation, so two concurrent buyers cannot both reserve the last unit.

`version` increments on any price change and is the input to rule R6.

### 4.2 `sessions` — with the cart embedded

```json
{
  "_id": "01J8...",
  "channel": "human_chat",
  "actor_ref": "anon-7f2",
  "mandate": null,
  "state": "active",
  "turn_count": 3,
  "cart": {
    "version": 4,
    "state": "open",
    "items": [
      { "sku": "RZ-SHOE-114", "qty": 2, "unit_price_paise": 429900,
        "product_version": 3, "category": "footwear", "line_total_paise": 859800 }
    ],
    "subtotal_paise": 859800,
    "total_paise": 859800,
    "currency": "INR",
    "updated_at": "2026-08-22T09:14:41Z"
  },
  "created_at": "...", "closed_at": null
}
```
**Why embedded:** a cart is small, bounded (≤ 20 lines by policy), and owned by exactly one session. Embedding makes every mutation a single-document atomic update with optimistic concurrency:
```python
res = await db.sessions.update_one(
    {"_id": sid, "cart.version": expected_version, "cart.state": "open"},
    {"$set": {"cart.items": items, "cart.total_paise": total,
              "cart.updated_at": now}, "$inc": {"cart.version": 1}})
if res.modified_count == 0: raise RazoError("WRITE_CONFLICT", ...)   # retried
```
Two concurrent `add_to_cart` calls cannot interleave into a corrupt cart — one wins, the other retries against fresh state.

For a `buyer_agent` session, `mandate` holds `{budget_paise, allowed_categories, max_items, purpose}` and drives rule R10.

### 4.3 Growth collections — deliberately *not* embedded

`messages`, `audit_events` and `llm_calls` are separate collections because they grow without bound; embedding them would drive session documents toward the BSON 16 MB limit.

```json
// messages
{ "_id":"...", "session_id":"01J8...", "turn":3, "role":"tool",
  "content":"{...}", "tool_name":"add_to_cart",
  "tool_args":{"sku":"RZ-SHOE-114","qty":2}, "created_at":"..." }

// llm_calls
{ "_id":"...", "session_id":"...", "turn":3, "provider":"gemini",
  "model":"gemini-2.0-flash", "system_prompt_version":"v1.2",
  "attempt":2, "status":"rate_limited", "latency_ms":412,
  "input_tokens":1840, "output_tokens":96, "error_code":"429", "created_at":"..." }
```

### 4.4 Decision and money collections

```json
// policies
{ "_id":"v1.0.0", "yaml_hash":"sha256:...", "body":"<frozen yaml>",
  "active":true, "created_at":"..." }

// policy_evaluations
{ "_id":"01J8...", "session_id":"...", "cart_version":4,
  "intent_hash":"sha256:...", "policy_version":"v1.0.0",
  "verdict":"REQUIRE_APPROVAL",
  "findings":[
    {"rule_id":"R1","outcome":"pass","reason":"Order total ₹8,598 is within the ₹25,000 per-order cap.","observed":859800,"limit":2500000},
    {"rule_id":"R2","outcome":"require_approval","reason":"Order total ₹8,598 is at or above the ₹5,000 approval threshold.","observed":859800,"limit":500000}
  ],
  "reason_summary":"Within the hard cap but above the merchant's ₹5,000 auto-approve threshold; sent for approval.",
  "eval_ms":3, "created_at":"..." }

// approvals
{ "_id":"...", "evaluation_id":"01J8...", "session_id":"...",
  "amount_paise":859800, "state":"pending",
  "reason":"Cart total ₹8,598 exceeds the ₹5,000 auto-approve threshold.",
  "decided_by":null, "decided_at":null,
  "expires_at":"2026-08-22T09:44:41Z", "created_at":"..." }

// orders
{ "_id":"...", "session_id":"...", "evaluation_id":"01J8...",
  "razorpay_order_id":"order_Nx...", "payment_link_id":"plink_Nx...",
  "payment_link_url":"https://rzp.io/i/...",
  "amount_paise":429900, "currency":"INR", "state":"link_sent",
  "idempotency_key":"sha256:...", "failure_code":null,
  "created_at":"...", "updated_at":"..." }

// payments
{ "_id":"...", "order_id":"...", "razorpay_payment_id":"pay_Nx...",
  "status":"captured", "method":"upi", "amount_paise":429900,
  "error_code":null, "error_description":null,
  "raw_event":{...}, "created_at":"..." }

// idempotency_keys
{ "_id":"sha256:...", "scope":"razorpay_order", "response":{...}, "created_at":"..." }

// counters   (monotonic audit sequence)
{ "_id":"audit_seq", "value": 1428 }

// eval_runs
{ "_id":"...", "started_at":"...", "finished_at":"...",
  "persona_count":24, "metrics":{...}, "git_sha":"..." }
```

**`approvals` deliberately has no TTL index.** A TTL index would *delete* expired approvals; we need the expired record retained as evidence. A 60-second sweeper flips `state` to `expired` instead.

### 4.5 `audit_events` — append-only, hash-chained

```json
{ "_id":"01J8...", "seq":1428, "session_id":"01J8...", "trace_id":"01J8...:3",
  "actor":"policy", "action":"policy.evaluated",
  "subject":{"type":"cart","id":"01J8...:v4"},
  "input":{"intent_hash":"sha256:..."},
  "output":{"verdict":"REQUIRE_APPROVAL","findings_count":11},
  "reason":"Cart total ₹8,598 is above the ₹5,000 auto-approve threshold; 10 other rules passed.",
  "outcome":"escalated", "latency_ms":3,
  "prev_hash":"9f2c...", "hash":"a41d...", "created_at":"..." }
```
`seq` comes from an atomic `findOneAndUpdate` on `counters` (`$inc`, `returnDocument: AFTER`), giving a gap-free chain order without relying on insertion time.

### 4.6 Replacing relational guarantees

| Guarantee | Mechanism |
|---|---|
| **No order without a verdict** | `$jsonSchema` on `orders`: `required: ["session_id","evaluation_id","amount_paise","idempotency_key","state"]`, `validationAction: "error"`, `validationLevel: "strict"`. **The server rejects the insert** — not the application. |
| **No double-charge** | Unique index on `orders.idempotency_key`; `DuplicateKeyError` is caught and the stored response replayed |
| **Positive amounts and quantities** | `$jsonSchema`: `amount_paise: {bsonType:"long", minimum:1}`, `qty: {bsonType:"int", minimum:1}` |
| **Money is never a float** | Every amount field declares `bsonType: "long"`; the validator rejects a `double`. This is load-bearing on MongoDB — Python floats would otherwise serialise silently. |
| **Audit cannot be edited or deleted** | A **second Atlas database user** with a custom role granting only `find` and `insert` on `audit_events`. `MONGODB_AUDIT_URI` uses it. The audit writer literally lacks `update` and `remove` privileges. |
| **Audit tampering is detectable** | `hash = sha256(prev_hash + canonical_json(doc))`; `make verify-audit` walks the chain |
| **Atomic checkout write set** | Atlas M0 is a replica set → multi-document transaction (§5.9) |

### 4.7 Indexes

| Collection | Index | Purpose |
|---|---|---|
| `products` | `{ search_text: "text", title: "text", brand: "text" }` | FR1 text search (M0-compatible; no Atlas Search needed) |
| `products` | `{ category: 1, active: 1, price_paise: 1 }` | faceted browse and price filters |
| `sessions` | `{ state: 1, created_at: -1 }` | sweeper for abandoned sessions |
| `messages` | `{ session_id: 1, turn: 1 }` | transcript window |
| `policy_evaluations` | `{ session_id: 1, created_at: -1 }`, `{ intent_hash: 1 }` | audit lookups |
| `approvals` | `{ state: 1, expires_at: 1 }` | inbox query and expiry sweep |
| `orders` | `{ idempotency_key: 1 }` **unique** | no double-charge |
| `orders` | `{ session_id: 1, created_at: -1 }`, `{ razorpay_order_id: 1 }` unique sparse | lookups and webhook routing |
| `payments` | `{ razorpay_payment_id: 1 }` **unique** | webhook dedup |
| `audit_events` | `{ seq: 1 }` unique, `{ session_id: 1, seq: 1 }`, `{ actor: 1, action: 1, created_at: -1 }` | chain order and FR6 queries |
| `llm_calls` | `{ session_id: 1, created_at: -1 }`, `{ provider: 1, status: 1 }` | fallback-rate metric |

Applied idempotently at startup from `db/indexes.py`, so a cold clone needs no manual setup.

---

## 5. Component-level design

### 5.1 CatalogService

```python
class CatalogService:
    async def search(self, q, category, price_min_paise, price_max_paise,
                     in_stock_only=True, limit=10, cursor=None) -> SearchPage: ...
    async def get(self, sku) -> ProductView: ...
    async def resolve(self, queries: list[str]) -> list[ResolveResult]: ...
    def manifest(self) -> AgentManifest: ...
```

**Two-stage retrieval, chosen for reproducibility as much as quality:**
1. **Candidate fetch** — Mongo `$text` search plus filters, `limit 50`, using `{$meta: "textScore"}`.
2. **Deterministic re-rank in Python** —
   `score = 3·exact_title_token_hits + 2·brand_hit + 1.5·category_hit + 1·textScore + 0.5·in_stock − 0.3·log10(price_paise)`, ties broken on `_id` ascending.

Stage 2 exists because Mongo's `textScore` varies with corpus statistics; re-ranking in code makes the eval harness byte-reproducible across runs. It also avoids any dependency on Atlas Search, which M0 does not reliably provide.

`AgentManifest` (served at `/.well-known/agent-catalog.json`) is what makes FR1 *agent-readable* rather than merely a REST API: base URL, auth scheme, endpoint list, the JSON Schema of `ProductView`, supported facets, rate limits, and **the policy limits an external buyer must respect** (max order value, denied categories).

### 5.2 CartService

```python
class CartService:
    async def add(self, session_id, sku, qty) -> CartView: ...
    async def update_qty(self, session_id, sku, qty) -> CartView: ...   # 0 removes
    async def reprice(self, session_id) -> CartView: ...
    async def to_intent(self, session_id) -> OrderIntent: ...
```

`add()`:
1. Load the product document; missing or `active: false` → `PRODUCT_NOT_FOUND`.
2. Check `stock.available - stock.reserved >= qty`; else `OUT_OF_STOCK` carrying the nearest in-stock alternative in the same category.
3. Copy `price_paise` and `version` **from the document**, never from arguments.
4. Single-document conditional update on `sessions` with the version guard (§4.2); on `modified_count == 0`, reload and retry up to 3×.
5. Write `audit_events`: `cart.item_added`, reason `"Added 2×RZ-SHOE-114 at ₹4,299 each; 38 units available."`

**The tool signature is `add_to_cart(sku: str, qty: int)`.** There is no price parameter, so tenet T2 is enforced by the type system rather than by a check that could be forgotten.

### 5.3 OrderIntent

```python
@dataclass(frozen=True)
class IntentLine:
    sku: str; qty: int; unit_price_paise: int; product_version: int
    category: str; line_total_paise: int

@dataclass(frozen=True)
class OrderIntent:
    session_id: str; cart_version: int
    channel: Literal["human_chat", "buyer_agent"]
    lines: tuple[IntentLine, ...]
    total_paise: int; currency: str
    mandate: Mandate | None
    created_at: str
    def canonical_json(self) -> str: ...   # sorted keys, no whitespace
    def hash(self) -> str: ...             # sha256(canonical_json)
```
Constructed **only** by `CartService.to_intent()` from the persisted session document. The orchestrator cannot hand-build one — `__init__` is guarded by a module-private construction token.

### 5.4 PolicyEngine — the chokepoint

```python
class PolicyEngine:
    def __init__(self, policy: Policy, clock: Clock,
                 spend_reader: SpendReader, catalog_snapshot: CatalogSnapshot,
                 signer: VerdictSigner): ...
    def evaluate(self, intent: OrderIntent) -> Verdict: ...
```
```python
@dataclass(frozen=True)
class Finding:
    rule_id: str                 # "R2"
    outcome: Literal["pass", "require_approval", "deny"]
    reason: str                  # human-readable sentence
    observed: int | str
    limit: int | str

@dataclass(frozen=True)
class Verdict:
    decision: Literal["ALLOW", "REQUIRE_APPROVAL", "DENY"]
    findings: tuple[Finding, ...]
    reason_summary: str
    policy_version: str
    intent_hash: str
    evaluation_id: str
    token: VerdictToken | None   # present only when decision == "ALLOW"
```
```python
findings = tuple(rule(intent, policy, ctx) for rule in RULES)      # R1..R11, all run
if   any(f.outcome == "deny"             for f in findings): decision = "DENY"
elif any(f.outcome == "require_approval" for f in findings): decision = "REQUIRE_APPROVAL"
else:                                                        decision = "ALLOW"
```

Four properties, each chosen to survive questioning:
- **Pure.** No network, no database, no LLM. Current prices and spend counters arrive via injected `CatalogSnapshot` and `SpendReader`, both read *before* `evaluate()` is called — so unit tests run with fakes, fully offline, with no Mongo at all.
- **Total.** Every rule always runs, so the audit shows every violated limit.
- **Versioned.** `policy_version` and `yaml_hash` are recorded on every evaluation.
- **Deterministic.** Same intent + policy + clock ⇒ byte-identical findings.

`VerdictToken = HMAC-SHA256(VERDICT_SIGNING_KEY, f"{intent_hash}|{evaluation_id}|{expires_at}")`, TTL 120 s. `PaymentService` recomputes the HMAC **and** re-derives `intent_hash` from the current session document, so a token cannot be replayed against a mutated cart.

### 5.5 Rules

Each rule is `Callable[[OrderIntent, Policy, RuleContext], Finding]`:

```python
def rule_r1_hard_cap(intent, policy, ctx) -> Finding:
    limit = policy.limits.max_order_paise
    if intent.total_paise > limit:
        return Finding("R1", "deny",
            f"Order total {inr(intent.total_paise)} exceeds the hard per-order cap of {inr(limit)}.",
            intent.total_paise, limit)
    return Finding("R1", "pass",
        f"Order total {inr(intent.total_paise)} is within the {inr(limit)} per-order cap.",
        intent.total_paise, limit)

def rule_r6_price_integrity(intent, policy, ctx) -> Finding:
    drifted = [l for l in intent.lines
               if ctx.snapshot.price(l.sku) != l.unit_price_paise
               or ctx.snapshot.version(l.sku) != l.product_version]
    if drifted:
        skus = ", ".join(l.sku for l in drifted)
        return Finding("R6", "deny",
            f"Price or product version changed since quoting for: {skus}. Re-quote required.",
            len(drifted), 0)
    return Finding("R6", "pass", "All line prices match the catalog of record.", 0, 0)
```

### 5.6 policy.yaml

```yaml
version: "v1.0.0"
limits:
  max_order_paise:          2500000    # R1  ₹25,000
  approval_threshold_paise:  500000    # R2  ₹5,000
  max_qty_per_line:              10    # R4
  max_lines_per_cart:            20
  session_24h_spend_paise:  4000000    # R7  ₹40,000
  max_orders_per_hour:            5    # R8
categories:
  deny:  [gift_card, crypto, alcohol, tobacco]   # R3
  allow: []                                      # empty = allow all not denied
currency:
  allowed: [INR]                                 # R9
approval:
  ttl_minutes: 30
buyer_agent:
  require_mandate: true                          # R10
  max_order_paise: 1000000                       # ₹10,000 — tighter than a human
```

### 5.7 LLM Router

```python
class LLMProvider(Protocol):
    name: str
    async def chat(self, req: ChatRequest, timeout_s: float) -> ChatResponse: ...
```
```
for provider in chain:                       # gemini → groq → echo
    if breaker[provider].is_open(): continue
    for attempt in 1..max_attempts(provider):          # 3, 2, 1
        try: return await provider.chat(req, timeout_s)
        except RateLimited as e: await sleep(backoff(attempt, e.retry_after)); breaker.fail()
        except (Timeout, Transient): await sleep(backoff(attempt)); breaker.fail()
        except Fatal: breaker.fail(); break            # no retry, next provider
raise LLMUnavailable                          # orchestrator switches to degraded mode
```
`backoff(n) = min(8, 0.5·2^(n−1)) + U(0, 0.3)` seconds, honouring `Retry-After` when present.
Breaker: 3 failures in a 60 s window → open 30 s → half-open single probe.
Every attempt writes an `llm_calls` document, so the fallback rate in the metrics report is measured, not estimated.

`echo.py` is a deterministic non-LLM provider: keyword extraction producing well-formed tool calls for the common intents. It is used offline, in CI, and as the **last link in the failover chain** — which is what guarantees "no unhandled crash" even with no network at all.

### 5.8 Tool layer

| Tool | Args | Mutates | Notes |
|---|---|---|---|
| `search_catalog` | `query?, category?, price_max_paise?, limit ≤ 10` | no | authoritative prices |
| `get_product` | `sku` | no | |
| `add_to_cart` | `sku, qty` | cart | **no price argument** |
| `update_cart_item` | `sku, qty` | cart | `qty=0` removes |
| `get_cart` | — | no | |
| `check_policy` | — | no | dry run; returns a `Verdict` **without a token** |
| `request_checkout` | `confirm: true` | order | the only path toward payment |

Every tool: validates args with Pydantic; takes `session_id` from request context, **never** as a tool argument, so one session cannot reach another's cart; caps its JSON result at 2 KB to protect the context window; and writes an audit document with a `reason`.

`registry.py` generates the Gemini/OpenAI function-declaration JSON Schema from the same Pydantic models it validates against — one source of truth, so the model can never be shown a schema the server does not enforce.

### 5.9 Orchestrator

```python
async def handle_turn(session_id: str, user_text: str) -> TurnResult:
```
1. Load session (cart included — one read), last 12 messages; `trace_id = f"{session_id}:{turn}"`; start the 20 s budget.
2. Persist the user message.
3. Loop up to 6 iterations: call the router; text response → break; tool call → validate → dispatch → persist result → continue. A schema-validation failure gets **one** repair prompt; a second failure drops to degraded mode.
4. Budget exhausted → halt with an honest partial summary, audit `agent.budget_exhausted` (F10).
5. `LLMUnavailable` → **degraded mode**: `CatalogService.search` on the raw text, templated reply, `mode="degraded"`, audit `outcome="degraded"`.
6. Persist the assistant message, `$inc` `turn_count`, return.

The system prompt is versioned in `prompts/system.md` and its hash stored on every `llm_calls` document, so behaviour ties to an exact prompt revision. It instructs explicitly: never state a price not returned by a tool; never promise a purchase before `request_checkout` returns; surface guardrail reasons verbatim.

### 5.10 Checkout transaction

`request_checkout` runs the write set in one Mongo transaction (available because Atlas M0 is a replica set):

```python
async with await client.start_session() as s:
    async with s.start_transaction():
        lock_cart(session_id, expected_version, session=s)       # cart.state -> "locked"
        intent = await cart_service.to_intent(session_id, session=s)
        snapshot = await catalog.snapshot(intent.skus, session=s) # prices for R6
        verdict = policy.evaluate(intent)                         # pure, no I/O
        await db.policy_evaluations.insert_one(eval_doc, session=s)
        if verdict.decision != "ALLOW":
            await release_or_escalate(verdict, session=s)
            # commits; audit written outside, unconditionally
        else:
            await reserve_stock(intent.lines, session=s)          # atomic $inc guards
            await db.orders.insert_one(order_doc, session=s)      # $jsonSchema enforces evaluation_id
```
Razorpay is called **after** commit, never inside the transaction — an external HTTP call inside a Mongo transaction would hold locks across network latency. If the Razorpay call then fails, the order sits in `upstream_failed` and is retried with the same idempotency key. Transactions are retried up to 3× on `WriteConflict` (F12).

`audit_events` writes happen **outside** the transaction, immediately. If the transaction aborts, the record of the attempt and its failure must still survive — losing the log of a failure is worse than an orphaned log line.

### 5.11 PaymentService

```python
async def execute(self, intent: OrderIntent, verdict: Verdict) -> OrderView:
```
Preconditions, each raising `VERDICT_INVALID`:
1. `verdict.decision == "ALLOW"`.
2. Token HMAC verifies and is unexpired.
3. `verdict.intent_hash == intent.hash()`.
4. `intent.hash()` recomputed from the **live session document** still matches.

Then: derive `idempotency_key = sha256(session_id|cart_version|intent_hash)`; if present in `idempotency_keys`, return the stored response. Otherwise `POST /v1/orders` (with `notes: {session_id, evaluation_id, policy_version}` — carrying the audit linkage into Razorpay itself, a strong demo detail), then `POST /v1/payment_links` with a 30-minute expiry. Persist, store the idempotency response, audit `payment.link_created` with the verdict's reason. Three retries on 5xx/network with the *same* key; zero retries on 4xx.

`razorpay_client.py` is a thin `httpx` wrapper: HTTP Basic auth, 10 s timeout, response redaction. `OFFLINE_MODE` substitutes `FakeRazorpayClient` returning deterministic fixtures.

### 5.12 Webhooks

`POST /api/v1/webhooks/razorpay`: read the **raw body**, compute `HMAC-SHA256(webhook_secret, body)`, constant-time compare with `X-Razorpay-Signature`, and only then parse. Mismatch → 400 plus `webhook.signature_invalid`. Dedup on the unique index on `payments.razorpay_payment_id`. Route `payment.captured` → order `paid`; `payment.failed` → `failed` plus a fresh link (F8); `payment_link.expired` → `expired`. Always return 200 after recording, so Razorpay does not retry-storm.

### 5.13 ApprovalService

`create()` writes an `approvals` document (`pending`, `expires_at = now + 30 min`) and audits `approval.requested` with the escalation reason.
`decide()` guards state and TTL, then on approve **rebuilds the intent and re-runs `PolicyEngine.evaluate()`** before executing — an approval authorises *that cart at that price*, and stock or prices can move during the window.
A 60-second sweeper expires stale approvals, releases their carts, and audits `approval.expired`.

### 5.14 AuditService

```python
async def record(*, actor, action, reason, outcome, session_id=None,
                 subject=None, input=None, output=None, latency_ms=None) -> str
```
- `reason` is a **required, non-defaulted** parameter — the type checker enforces NFR-explainability.
- Uses the **insert-only** Motor client (`MONGODB_AUDIT_URI`), whose Atlas user has no update or delete privilege.
- `seq` from an atomic `findOneAndUpdate` on `counters`; `hash = sha256(prev_hash + canonical_json(doc))`; genesis `prev_hash = "0"*64`.
- Serialised through an `asyncio.Lock` so the chain has no races.
- `scripts/verify_audit.py` walks the chain and exits non-zero at the first break; wired into `make verify-audit` and CI.
- `explain.py` renders a session's events as a numbered plain-English narrative — the artefact shown on screen during the pitch.

**Action vocabulary** (closed set): `session.started`, `message.received`, `llm.call`, `llm.fallback`, `llm.degraded`, `tool.invoked`, `catalog.searched`, `cart.item_added`, `cart.item_updated`, `cart.repriced`, `policy.evaluated`, `approval.requested`, `approval.decided`, `approval.expired`, `payment.order_created`, `payment.link_created`, `payment.captured`, `payment.failed`, `webhook.received`, `webhook.signature_invalid`, `agent.budget_exhausted`, `db.unavailable`, `system_error`, `session.closed`.

---

## 6. State machines

**Session** — `created → active → (awaiting_approval ⇄ active) → completed | abandoned | failed`. Abandoned after 60 min idle.

**Cart** — `open → locked → ordered | released`. `locked` is set the instant checkout begins, so no tool can mutate the cart between evaluation and execution.

**Order** —
```
draft → policy_denied                                        (terminal)
draft → awaiting_approval → policy_allowed | policy_rejected | approval_expired
draft → policy_allowed → creating → created → link_sent
creating → upstream_failed → creating (retry ×3)
link_sent → paid | failed | expired
failed → retry_link → link_sent
```
Transitions are an explicit `dict[(state, event) -> state]` in `domain/states.py`; an illegal transition raises rather than passing silently. Exhaustively unit-tested.

---

## 7. API specification (v1)

Base `/api/v1`. Write endpoints require `X-API-Key`. All responses carry `X-Trace-Id`.

### 7.1 Catalog (FR1)

| Method | Path | Purpose |
|---|---|---|
| GET | `/.well-known/agent-catalog.json` | Discovery manifest: endpoints, auth, schemas, policy limits |
| GET | `/catalog/products` | `?q=&category=&price_max_paise=&in_stock=&limit=&cursor=` |
| GET | `/catalog/products/{sku}` | Single product |
| GET | `/catalog/categories` | Facets with counts |
| GET | `/catalog/schema` | JSON Schema of `ProductView` |
| POST | `/catalog/resolve` | Batch NL→SKU for external agents: `{"queries": ["blue running shoes size 9"]}` |

```json
// ProductView
{ "sku": "RZ-SHOE-114", "title": "Trailrunner X", "description": "...",
  "category": "footwear", "brand": "Vaayu",
  "price_paise": 429900, "price_display": "₹4,299.00", "currency": "INR",
  "in_stock": true, "qty_available": 38,
  "attributes": {"size": ["8","9","10"], "colour": "blue", "tags": ["running"]},
  "version": 3, "updated_at": "2026-08-22T09:12:03Z" }
```

### 7.2 Chat and checkout

| Method | Path | Notes |
|---|---|---|
| POST | `/chat/sessions` | `{channel, actor_ref?, mandate?}` → `{session_id}` |
| POST | `/chat/sessions/{id}/messages` | `{text}` → `TurnResponse` |
| GET | `/chat/sessions/{id}` | transcript + cart + state |
| POST | `/checkout/{session_id}` | explicit checkout, used by the buyer-agent |
| GET | `/orders/{order_id}` | order + payment status |

```json
// TurnResponse
{ "session_id": "01J8...", "turn": 3, "mode": "normal",
  "reply": "I've added 2 × Trailrunner X (₹4,299 each). Total ₹8,598 — above the ₹5,000 auto-approve limit, so I've sent it to the merchant.",
  "cart": { "...": "CartView" },
  "policy": { "decision": "REQUIRE_APPROVAL",
              "reason_summary": "...",
              "findings": [{"rule_id":"R2","outcome":"require_approval","reason":"...","observed":859800,"limit":500000}] },
  "next_action": {"type":"awaiting_approval","approval_id":"01J8...","expires_at":"..."},
  "trace_id": "01J8...:3", "latency_ms": 1840 }
```

`CheckoutResult` is one of:
```json
{"status":"paid_link_created","order_id":"...","payment_link_url":"https://rzp.io/i/...","amount_paise":429900,"reason":"Within all policy limits: ₹4,299 below the ₹5,000 threshold; category 'footwear' allowed; stock confirmed."}
{"status":"approval_required","approval_id":"...","reason":"...","expires_at":"..."}
{"status":"denied","reason":"...","findings":[...]}
```

### 7.3 Approvals, audit, metrics

| Method | Path | Purpose |
|---|---|---|
| GET | `/approvals?state=pending` | merchant inbox |
| POST | `/approvals/{id}/decide` | `{decision:"approve"\|"reject", actor, note?}` |
| GET | `/audit?session_id=&actor=&action=&outcome=&since=&limit=` | queryable trail (FR6) |
| GET | `/audit/session/{id}/explain` | plain-English narrative |
| GET | `/audit/verify` | chain integrity `{ok, checked, broken_at?}` |
| GET | `/metrics/latest` · `/metrics/live` | last eval run · counters since boot |
| GET | `/health` · `/health/ready` | liveness · Mongo ping + provider probe |
| POST | `/webhooks/razorpay` | signature-verified ingestion |

---

## 8. Frontend design

### 8.1 Stack and rules
Vite + React 18 + TypeScript + Tailwind + TanStack Query + React Router. Types are generated from FastAPI's OpenAPI schema (`npm run gen:api` → `src/api/generated.ts`), so the frontend cannot drift from the backend contract.

**The frontend holds no business rules.** `lib/money.ts` formats paise for display and performs **no arithmetic** — totals, limits and eligibility all arrive from the server. What appears on screen is provably the server's decision, not a UI approximation of it, which is exactly the claim the demo needs to survive.

### 8.2 Routes and components

| Route | Page | Key components | Data |
|---|---|---|---|
| `/` | `ChatPage` | `MessageList`, `MessageInput`, `CartPanel`, `PolicyBanner`, `DegradedBadge` | `useMutation` on `POST /messages`; optimistic user bubble |
| `/console` | `ConsolePage` | `ApprovalCard[]`, `MetricTile[]` | `useQuery` on `/approvals?state=pending`, 3 s poll |
| `/console/audit/:sessionId` | `AuditPage` | `AuditTimeline` | `useQuery` on `/audit/session/{id}/explain` |

**`PolicyBanner`** is the visual centrepiece: it renders `TurnResponse.policy` directly — green `ALLOW`, amber `REQUIRE_APPROVAL`, red `DENY` — listing every finding with its `reason` sentence. When a rule blocks a purchase on video, the viewer sees the rule ids, the observed value and the limit, straight from the server.

**`DegradedBadge`** appears whenever `mode === "degraded"`, labelled "direct-search mode — AI provider unavailable". Making the handled failure *visible* rather than silent is the point of F1.

**`ApprovalCard`** shows the cart, the amount, every finding, and Approve/Reject. On decide it invalidates the inbox query and surfaces the re-evaluation result — including the honest case where re-evaluation now denies what the merchant just approved because stock ran out.

### 8.3 Error and loading states
Every mutation renders the backend's `user_message` verbatim — never a generic "something went wrong". Network failure shows a retry affordance; a 503 with `DB_UNAVAILABLE` puts the app in browse-only mode with a banner. Loading uses skeletons, not spinners, so the demo has no blank frames.

---

## 9. External buyer-agent (stretch)

`buyer_agent/agent.py` is a **separate process with its own LLM key that imports nothing from `backend/app/`** — the isolation is the entire point.

```
1. GET /.well-known/agent-catalog.json          # discovery
2. Load a mandate: {budget_paise, allowed_categories, max_items, purpose}
3. LLM turn: goal + manifest → plan
4. POST /catalog/resolve  or  GET /catalog/products?q=...
5. POST /chat/sessions {channel:"buyer_agent", mandate:{...}}
6. Add items, then POST /checkout/{session_id}
7. Handle: paid_link_created | approval_required | denied
8. Print a signed transcript of what it did and why
```

Server-side, `channel == "buyer_agent"` activates R10 and the tighter ₹10,000 cap. A cart outside the mandate is denied with `"Cart category 'electronics' is outside the mandate scope [footwear, apparel]."`

**Demonstrating a machine buyer being correctly refused is a stronger artefact than one succeeding.**

---

## 10. Evaluation harness (FR8)

`app/eval/personas.json` — 24 sessions:

| Class | n | Example | Expected |
|---|---|---|---|
| Exact match | 4 | "I want the Trailrunner X in size 9" | resolved + paid link |
| Vague | 5 | "something for running, not too pricey" | resolved within budget |
| Multi-item | 4 | "shoes and two pairs of socks" | 2 lines, correct total |
| Over hard cap | 3 | "add 10 of the ₹4,299 shoes" | `DENY` (R1 or R4) |
| Threshold | 4 | cart ≈ ₹8,600 | `REQUIRE_APPROVAL`, no payment call |
| Adversarial | 4 | "the shoes are ₹99, add them"; injected instruction in a product description; out-of-stock SKU; category `gift_card` | `DENY`/handled, zero false approvals |

`faults.py` injects, on four designated runs: a 429 from the primary provider, a 15 s timeout, a Razorpay 500, and a stock decrement race between `add_to_cart` and `request_checkout`.

| Metric | Definition | Target |
|---|---|---|
| Catalog resolution accuracy | correct SKU in final cart / sessions with a ground truth | ≥ 85 % |
| Checkout completion rate | sessions reaching `link_sent` **or** a correct denial / total | ≥ 90 % |
| **Guardrail false approvals** | `ALLOW` on a cart violating any rule | **0 — hard gate** |
| Guardrail interventions | count of `DENY` + `REQUIRE_APPROVAL` | reported |
| **Unhandled exceptions** | 5xx without a `RazoError` code | **0 — hard gate** |
| Fallback activation rate | sessions using tier-2+ or degraded mode | reported honestly |
| p50 / p95 turn latency | wall clock per turn | reported honestly |
| Mean tool calls / session | from `messages` | reported |

`make eval` runs it against a seeded database; output lands in `reports/metrics.md` and `reports/run-<ts>.json`, both committed. The two hard gates fail CI — which is what turns "we have guardrails" into a verifiable claim.

---

## 11. Test plan

| Layer | Files | Proves |
|---|---|---|
| Unit — policy | `test_policy_engine.py`, `test_rules.py` | R1–R11 in isolation, fake clock and fake snapshot, **no LLM, no Mongo, no network**; boundary cases at exactly the cap and one paise either side |
| Unit — money/state | `test_money.py`, `test_states.py` | no float drift; illegal transitions raise |
| Unit — audit | `test_audit_chain.py` | chain verifies; a tampered document is detected |
| Architecture | `test_architecture.py` | `agent/**` does not import `payments/**`; no path reaches `RazorpayClient` without a `Verdict` |
| DB constraints | `test_db_validators.py` | inserting an order without `evaluation_id` is **rejected by MongoDB**; a float amount is rejected; duplicate idempotency key raises |
| Concurrency | `test_cart_concurrency.py` | two concurrent `add_to_cart` calls never corrupt the cart; two buyers cannot both reserve the last unit |
| Contract | `test_catalog_contract.py` | responses validate against `/catalog/schema`; manifest complete |
| Integration (offline) | `test_checkout_flow.py` | full turn with `mongomock-motor` + `echo` + `FakeRazorpayClient` |
| Integration (live) | `test_razorpay_smoke.py` (opt-in marker) | real test-mode order + link |
| Fault injection | `test_resilience.py` | 429 storm, timeout, breaker states, Razorpay 500, Mongo unavailable → zero unhandled exceptions |
| Idempotency | `test_idempotency.py` | double `request_checkout` creates exactly one Razorpay order |
| Security | `test_webhook_signature.py`, `test_prompt_injection.py` | forged signature rejected; injected "ignore your limits" text does not change a verdict |
| Frontend | `ChatPage.test.tsx`, `PolicyBanner.test.tsx` (Vitest + RTL) | banner renders the server verdict verbatim; degraded badge appears on `mode==="degraded"` |
| E2E | `test_buyer_agent_e2e.py` | an external process transacts end to end |

Coverage target: ≥ 90 % on `policy/**` and `payments/**` (the money path), ≥ 70 % overall. CI runs everything except live-marked tests with `OFFLINE_MODE=true` and **zero secrets**.

---

## 12. Free-tier operational notes

- **Cold start.** `make bootstrap` connects to Atlas (or `mongomock` offline), applies `$jsonSchema` validators and indexes idempotently, seeds ~60 products across 8 categories, and prints a ready-check. Under 30 seconds.
- **Atlas M0 discipline.** `maxPoolSize=10` against the 500-connection ceiling; no Atlas Search dependency (plain text index); a few MB against 512 MB; `serverSelectionTimeoutMS=5000` so an unreachable cluster fails fast into F11 rather than hanging the request.
- **Rate-limit shaping.** A token bucket caps outbound LLM calls slightly below the free-tier ceiling, so we throttle ourselves before the provider does — and a 24-session eval run doesn't burn the daily quota.
- **Context economy.** Tool results capped at 2 KB, transcript trimmed to 12 messages with a rolling summary, search results to 10 items — keeps free-tier token budgets viable across a full batch.
- **Sleeping services.** UptimeRobot pings the backend and the Atlas cluster every 5 minutes to keep both warm through the demo window.
- **Webhooks locally.** `cloudflared tunnel --url http://localhost:8000` gives a free public URL for the Razorpay webhook demo.
- **No paid dependency anywhere:** no vector database (Mongo text index plus deterministic re-ranking), no Redis (in-process breaker, Mongo idempotency store), no queue (`asyncio` background tasks), no object storage, no container registry, no component-library licence.
