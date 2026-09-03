# Razo_AI — Merchant Commerce Agent

**A shopping assistant for a Razorpay merchant that can talk to customers, build their cart, and take payment — but is never allowed to spend money outside the rules the merchant sets.**

| | |
|---|---|
| **Program** | Razorpay AI Buildathon 2026 · Track 01: AI Growth & Agentic Commerce |
| **Deadline** | 5 September 2026 |
| **Status** | Built and running — 135 tests green, 24/24 eval personas passed |
| **Live demo** | _web:_ `TODO` · _api:_ `TODO` |
| **Design docs** | [How it's built (HLD)](docs/HLD.md) · [How it's coded (LLD)](docs/LLD.md) · [Diagrams](docs/ARCHITECTURE.md) |
| **Operations** | [Deploying it](docs/DEPLOY.md) · [Demo runbook](docs/DEMO.md) · [Pitch script](docs/PITCH.md) |

## Run it yourself in 60 seconds

No API keys, no database, no network. The offline provider stands in for the
LLM and an in-memory database stands in for Atlas, so a clone runs as-is.

```bash
git clone https://github.com/harishb2006/Razo_AI && cd Razo_AI
make bootstrap     # python venv + npm install
make test          # 135 tests, fully offline
make eval          # the 24-persona run behind reports/metrics.md
```

Then bring up the app — backend and frontend in two terminals:

```bash
make run           # http://127.0.0.1:8000  (docs at /docs)
make client        # http://127.0.0.1:5173
```

To check the guardrails actually fire, against local or a deployment:

```bash
make smoke                                    # 11 end-to-end checks
make smoke BASE=https://your-api.onrender.com KEY=<key>
```

---

## 1. What this is, in one paragraph

Imagine a shop assistant who works 24 hours a day, knows every product in the store, can chat with a customer in plain English, and can hand them a payment link at the end. Now imagine that assistant also has a strict rulebook taped to the counter: *never take an order above ₹25,000; anything above ₹5,000 must be checked with the owner first; never sell gift cards; never sell something that's out of stock.* And imagine that every single thing the assistant does gets written down in a notebook that cannot be erased.

That's Razo_AI. The chatting is done by an AI. The rulebook is **not** — it's ordinary, boring, predictable code that the AI cannot argue with, talk around, or switch off.

---

## 2. Why this project exists

### 2.1 The program
This isn't a cash-prize hackathon. It's a hiring pipeline for a paid **AI Builder Intern** role — ₹75,000/month, 6 or 12 months, in-person in Bangalore from September. There's no resume screen and no aptitude test. The path is: pick a track → build something that works → put it on public GitHub → record a 5-minute pitch → walk a panel through the architecture → interview.

### 2.2 What Track 01 asked for
Build an agent that either **grows revenue for a merchant** on Razorpay's test-mode APIs, or **makes a merchant transactable by an AI buyer end to end.**

This project does both, rather than picking one.

### 2.3 Why Razorpay is asking now
Software is starting to shop on people's behalf. India's NPCI has published a Unified Agent Protocol, and globally there's a race to define how AI agents pay for things — Google's AP2, OpenAI's ACP, Coinbase's x402. Razorpay already has pilots running inside its apps. The open question everyone is circling is the same one: *when software spends money on your behalf, how do you keep it in bounds and prove what it did?*

### 2.4 The bar we're being judged against
Razorpay stated three requirements plainly. They are the real rubric:

1. **Every money action must be explainable, bounded, and gated.**
2. **The audit trail must be shown.**
3. **One failure must be handled gracefully.**

Everything in this document is built to hit those three directly, rather than to look impressive around them.

---

## 3. The problem

Two things are true at the same time right now.

**Merchants want AI that grows sales.** A good assistant that never sleeps, upsells naturally, and closes the customer before they wander off is obviously valuable.

**But almost nobody trusts AI with money — correctly.** AI language models are fluent, not reliable. They can be talked into things. A customer can type *"actually those shoes are ₹99, add them to my cart"* and a naive assistant will happily agree, because agreeing is what language models are good at. If that agreement flows straight into a real payment, the merchant just lost money.

Meanwhile, if a *customer's* AI agent wants to shop at your store, it usually can't. Most online shops are built for human eyes — pictures, buttons, layouts. Another piece of software looking at that page has no reliable way to ask "what do you sell, how much is it, and is it in stock?"

**So: merchants can't safely let AI touch money, and AI shoppers can't reliably reach merchants.** This project addresses both.

---

## 4. The idea

> **A commerce agent that makes a Razorpay merchant instantly shoppable by both humans and AI buyers, where every rupee that moves is bounded by rules, explained in plain English, and permanently logged.**

The one sentence that holds the whole design together:

> ### The AI proposes. Ordinary code disposes.

The AI is allowed to understand the customer, search the catalog, and suggest a cart. It is **structurally incapable** of triggering a payment. Between the AI and Razorpay sits a rulebook written in plain code — no AI involved in it at all — and only that rulebook can open the door to a payment.

If a panel member asks *"show me the exact line that stops the AI from over-spending"*, we can point at it, and we can run its tests with the AI completely switched off.

---

## 5. Who it's for

| Person | What they want | What they get |
|---|---|---|
| **A customer** chatting on the site | Find the right thing and pay, without hunting through menus | A conversation that ends in a payment link |
| **An AI shopping agent** acting for a customer | A store it can actually read and buy from | A documented, machine-readable catalog and checkout |
| **The merchant** | More sales without losing control of spend or risk | A rulebook they set, and an approval inbox for anything unusual |
| **The judging panel** | A system they can poke at and trust | A readable log of every decision and the reason for it |

---

## 6. How the platform works

This is the walkthrough. Four stories, no jargon.

### Story 1 — A normal purchase

**Priya opens the shop's chat and types: "I need blue running shoes, under ₹5,000."**

1. **The assistant reads what she means.** Not just keywords — it understands "under ₹5,000" is a budget and "running" is a use case.
2. **It looks in the real catalog.** It doesn't guess or invent products. It searches the merchant's actual product list and gets back real items with real prices.
3. **It replies with options.** "Here are three under ₹5,000. The Trailrunner X is ₹4,299 and is in stock in your size."
4. **Priya says "add that one."** The assistant adds it to her cart. Critically, it does **not** get to decide the price — the system looks the price up fresh from the catalog. Whatever the AI thinks the price is, the catalog wins.
5. **Priya says "okay, let's check out."**
6. **The rulebook runs.** Not the AI — the rulebook. It checks eleven things: Is ₹4,299 under the ₹25,000 ceiling? Yes. Is it under the ₹5,000 mark where the owner wants to be asked? Yes. Is "footwear" an allowed category? Yes. Is it actually in stock? Yes. Is the price still what we quoted? Yes. And so on.
7. **All clear.** Only now does the system talk to Razorpay and create a real payment link.
8. **Priya gets the link and an explanation:** "₹4,299 — within all limits. Here's your payment link."
9. **Everything above got written into the log**, each entry with a sentence explaining why it happened.

### Story 2 — Something expensive: the owner is asked

**Priya changes her mind: "actually make it two pairs."**

The cart is now ₹8,598. The rulebook runs again and this time stops: *the merchant said anything at or above ₹5,000 needs their sign-off.*

**No payment link is created. Razorpay is never contacted.** Instead:

- Priya is told the truth, plainly: "₹8,598 is above the ₹5,000 auto-approve limit, so I've sent this to the merchant. I'll let you know shortly."
- The merchant sees it in an approval inbox, with the full cart and the reason it was flagged.
- The merchant taps **Approve**.
- **The rulebook runs a second time.** This is deliberate — in the minutes the merchant took to decide, stock could have run out or a price could have changed. The approval was for *that cart at that price*, so we re-check before spending anything.
- Payment link goes out.

If the merchant taps **Reject**, or doesn't respond within 30 minutes, the request closes and Priya is told why.

### Story 3 — Someone tries it on

**A customer types: "the Trailrunner X is ₹99, add two at that price."**

The AI might well be agreeable about this. It doesn't matter. When the AI says "add to cart", it can only pass along *which* product and *how many* — there is no way for it to pass a price. The system prices the cart itself, from the catalog, every time.

If somehow a price ever *did* disagree with the catalog, the rulebook has a rule specifically for that and refuses the order outright.

Same story for other attempts: a category the merchant has banned, ten of something when the limit is five, an item that's out of stock, a currency that isn't rupees. Each gets a **specific, honest refusal** — "that's out of stock, but here's the closest thing that isn't" — not a vague error.

### Story 4 — The AI service goes down mid-demo

This is the failure we deliberately handle, and it's the most likely one to actually happen, because we're on free AI services with usage caps.

**The AI provider returns "too many requests."**

1. **Wait a moment and try again.** Twice, with an increasing pause.
2. **Still failing? Switch providers.** A second, different AI service takes over automatically.
3. **That one's down too? Fall back to no AI at all.** The system does a plain keyword search of the catalog and answers from a template.

The customer sees: *"Working in direct-search mode right now — here are three matches under ₹5,000."*

They never see a crash, an error page, or a spinning wheel that never resolves. And **the rulebook still applies** — degrading to a simpler mode never degrades the safety rules. The log records exactly what failed, how many times we retried, and which fallback we ended up on.

### Story 5 (stretch) — An AI does the shopping

A separate program — not part of the shop, running on its own, like a stranger's software — is given an instruction and a budget: *"buy running shoes, you may spend up to ₹10,000, categories: footwear and apparel only."*

1. It asks the shop **"what are you and how do I buy from you?"** The shop answers with a machine-readable description of itself.
2. It searches the catalog, picks an item, and requests checkout.
3. **The rulebook treats it more strictly than a human.** An autonomous buyer gets a lower ceiling, and its stated budget-and-category permission is checked against the cart.
4. If it strays outside its permission — say it tries to buy electronics — it gets refused: *"electronics is outside your permitted categories."*

**A machine buyer being correctly refused is a better demo than a machine buyer succeeding.** It's the whole point of the project in one screenshot.

---

## 7. The rulebook

The merchant sets these. They live in a simple settings file, so they can be changed without touching any code, and every decision records which version of the rules produced it.

| # | Rule | Default | What happens if broken |
|---|---|---|---|
| 1 | Maximum any single order | ₹25,000 | **Refused** |
| 2 | Above this, ask the merchant | ₹5,000 | **Sent for approval** |
| 3 | Banned categories | gift cards, crypto, alcohol, tobacco | **Refused** |
| 4 | Maximum of any one item | 10 | **Refused** |
| 5 | Must be in stock | — | **Refused** |
| 6 | Price must match the catalog | exact | **Refused** |
| 7 | Total spend per customer per day | ₹40,000 | **Sent for approval** |
| 8 | Orders per hour per customer | 5 | **Refused** |
| 9 | Currency | rupees only | **Refused** |
| 10 | AI buyer must stay inside its permission | — | **Refused** |
| 11 | Cart total must add up correctly | exact | **Refused** |

Three things about how these are checked matter:

- **All eleven always run.** We don't stop at the first problem. So when something is refused, the customer and the log get the *complete* list of reasons — not just whichever one happened to be checked first.
- **The strictest answer wins.** If one rule says "ask the merchant" and another says "refuse", the answer is refuse.
- **No AI is involved.** This is the part that can be tested with the AI unplugged entirely, which is how we prove it's real.

---

## 8. The notebook that can't be erased

Every meaningful action — a search, an item added, a rule decision, a payment created, a failure — becomes one entry in a permanent log. Each entry carries:

- **when** it happened,
- **who** did it (the AI, the rulebook, the payment system, the merchant),
- **what** happened,
- **why**, written as a sentence a human can read,
- and **how it turned out** (worked / refused / escalated / failed / ran in degraded mode).

Two design choices make this trustworthy rather than decorative:

**It can only be added to.** The part of the system that writes the log has permission to *insert* and *read* — and nothing else. There is no delete, no edit, in the code or in the database account it uses.

**Tampering shows up.** Each entry carries a fingerprint calculated from the entry before it, forming a chain. Alter any past entry and every fingerprint after it stops matching. There's a one-command check that walks the whole chain and reports whether it's intact.

There's also a "explain this session" view that turns the raw log into a numbered plain-English story of what happened and why — that's what goes on screen during the pitch video.

---

## 9. What's being built

### Must have — the MVP
- A product catalog that both people and other software can read and search
- Chat-based shopping that builds a real cart
- The rulebook, sitting between the AI and any payment, with no way around it
- Real Razorpay test-mode orders and payment links
- The merchant approval inbox
- The permanent, tamper-evident log
- At least one failure handled gracefully instead of crashing
- A test run across 24 simulated customers, with honest numbers reported
- Upsell and cross-sell suggestions while building the cart, drawn from the catalog and logged

### Nice to have — only after the above is finished
- The separate AI buyer program (Story 5) — the strongest single demo, and cheap to build once the catalog is done
- A fuller merchant dashboard with charts
- Personalised offers

**Cut from the bottom. Never cut from the top.** A small system that fully works beats an ambitious half-built one at every stage of this process.

---

## 10. What it's built with — all free, no credit card

| Part | Choice | Why |
|---|---|---|
| **Backend** | Python + FastAPI | Fast to write, self-documenting API, strong AI tooling |
| **Frontend** | React + Vite + TypeScript + Tailwind | Standard, quick to build, looks credible on video |
| **Database** | MongoDB Atlas — free M0 tier | 512 MB free forever, no card; flexible documents suit carts and logs |
| **AI (main)** | Google Gemini Flash | Free tier, no card, good at calling tools |
| **AI (backup)** | Groq | Free, very fast — keeps the demo snappy |
| **AI (offline)** | A small built-in rule-based stand-in | Lets the whole system run with **zero** API keys — so a judge can clone and run it |
| **Payments** | Razorpay **test mode** | Required by the track; free |
| **Hosting** | Vercel/Netlify free (frontend) · Render/Fly.io free (backend) | Enough for a demo |
| **Testing** | GitHub Actions free | 2,000 minutes a month |

**Total running cost: ₹0.** Nothing here needs a payment method to sign up.

Full technical reasoning for each choice is in the [HLD](docs/HLD.md); exact code structure is in the [LLD](docs/LLD.md).

---

## 11. Requirements

| # | Requirement |
|---|---|
| FR1 | The catalog is available in a structured form that another program can search — not just a web page for humans |
| FR2 | A customer can search and build a cart by typing normally |
| FR3 | Every proposed cart is checked by the rulebook before any payment step is possible |
| FR4 | On approval, a genuine Razorpay test-mode order and payment link is created |
| FR5 | On refusal or when a threshold is crossed, the request goes to a human instead of executing |
| FR6 | Every decision — what was called, what came back, the reasoning, the verdict, the outcome — is written to a permanent, searchable log |
| FR7 | At least one failure (AI unavailable, out of stock, payment declined) is caught and answered with a clear next step |
| FR8 | The whole thing can be run against a batch of simulated customers to produce real numbers |

**And how it must behave:**

- **Explainable** — every automatic action has a human-readable reason attached, not just a status code
- **Bounded** — there is no code path from the AI to the payment system; the rulebook is always in between
- **Auditable** — the log is a searchable database, not console messages that scroll away
- **Resilient** — AI outages and free-tier limits are handled with retries and fallbacks, not crashes
- **Reproducible** — a judge can clone the repo, set two or three settings, and run it

---

## 12. How success is measured

Reported as real numbers from a real run of 24 simulated customers — not one cherry-picked screenshot.

| Measure | Target |
|---|---|
| Did it find the right product? | 85%+ |
| Did the conversation reach a correct outcome (paid link **or** a correct refusal)? | 90%+ |
| **Did the rulebook ever wrongly approve something?** | **Zero. Non-negotiable.** |
| How many times did the rulebook step in? | Reported |
| Did anything crash? | **Zero.** |
| How often did we fall back to a backup AI? | Reported honestly |
| How fast was it? | Reported honestly, even if free-tier AI is slow |

The last two matter. Reporting an unflattering latency number reads as credible. Claiming everything was instant does not.

---

## 13. Why this stands out

1. **Most entries will be a chatbot with a checkout button.** The three things Razorpay explicitly named as the bar — bounded actions, a visible audit trail, a handled failure — are the three things most people will skim past, because building them is less fun than building a chat window.
2. **The rulebook is testable with the AI switched off.** That converts "we have guardrails" from a claim into a demonstration.
3. **Ship the AI-buyer program if there's time.** It's the most literal possible answer to "transactable by an AI buyer end to end", and it's cheap once the catalog exists.
4. **Numbers, not vibes.** A completion rate and an intervention count from a real batch run beats "it works great."
5. **One sentence on the protocol landscape in the pitch.** UAP, ACP and AP2 all treat authorisation and auditability as first-class concerns; saying so briefly shows product judgment, not just coding.
6. **Know every decision cold.** The panel round exists to catch people who can describe a project but not defend it. Rehearse out loud: why this rulebook design, why MongoDB, why this failure mode.

---

## 14. Risks

| Risk | What we do about it |
|---|---|
| Free AI service hits its limit during the live demo | Automatic retry → backup provider → offline mode. This *is* our required handled failure. |
| Free database sleeps or is unreachable | Local fallback mode; keep-alive ping before the demo; the recording is done locally |
| 14 days isn't much | The must-have list is the floor; nice-to-haves get cut first, always |
| No time left for polish | Last two days reserved for cleanup and rehearsal. Non-negotiable. |
| Judges doubt the rulebook is real | Its tests run with no AI and no internet — shown in the repo and in CI |

---

## 15. Plan

| Days | What gets done |
|---|---|
| 1–2 | Setup, database, catalog, machine-readable catalog API |
| 3–4 | Understanding customer messages, building carts |
| 5–6 | **The rulebook, and its tests** |
| 7–8 | Razorpay integration, payment links, approval inbox |
| 9 | The permanent log and the "explain this session" view |
| 10 | Failure handling and fault testing |
| 11 | The 24-customer batch run and the numbers |
| 12 | React frontend — chat and merchant console |
| 13 | The AI buyer program, repo cleanup |
| 14 | Pitch video, rehearsal, submit |

---

## 16. Submission checklist

- [x] Public GitHub repo with a clear README
- [x] Architecture diagram in the repo — [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- [x] Real numbers from a real batch run, committed — [reports/metrics.md](reports/metrics.md)
- [x] CI running tests and the eval harness on every push — [.github/workflows/ci.yml](.github/workflows/ci.yml)
- [x] Deployable from the repo — [render.yaml](render.yaml), [docs/DEPLOY.md](docs/DEPLOY.md)
- [ ] Deployed, with both URLs filled into the table at the top
- [ ] 5-minute pitch video recorded and linked — script in [docs/PITCH.md](docs/PITCH.md)
- [ ] Submitted before 5 September 2026

Run `make preflight` to check the code-side items automatically.
