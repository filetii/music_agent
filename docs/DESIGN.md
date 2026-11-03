# Design notes

This document records how the solution was designed before any code was written: what the data looks like, which architectures were considered, and what was decided. The README summarizes the final decisions; this file keeps the reasoning trail.

The work followed explicit phases: discovery → architecture alternatives → selection → detailed design → implementation → evaluation. No code was written until the design below was agreed.

---

## 1. Discovery: what the store gave us

### 1.1 Datasets

```
categories (9) 1──* products (65) 1──* order_items (22) *──1 orders (20) *──1 customers (50)
                              1──* promotions (25)
```

All foreign keys resolve, there are no duplicate IDs or empty cells, and every `specs` value is valid JSON. The data is tiny and relational — a good fit for deterministic queries, not for retrieval.

| Dataset | Role for the agent | Nature |
|---|---|---|
| products | price, stock, status, specs | operational |
| categories | taxonomy / filters | static |
| promotions | effective price (`is_active`) | operational |
| orders + order_items | order status, tracking, return eligibility | operational |
| customers | identity check before revealing order data | personal data |
| store policy PDF | rules, hours, contact, tone of voice | static, ~3k tokens |

### 1.2 Data-quality findings that shape the design

**Products**
- Catalog is skewed: 35 violões, 15 ukuleles, 5 guitars, 4 basses, 3 drum kits, 3 keyboards. Categories 6–8 (woodwinds, brass, orchestral strings) have **no products**, although the policy manual advertises them and claims "300+ instruments".
- Status and stock contradict each other: #96 is `active` with 0 stock; #130 is `coming_soon` with stock 2 *and* appears in a delivered order; #136 is `discontinued` with stock 13.
- Rows 135–144 (basses, drums, keyboards) have name, description and specs that disagree on brand and specs (e.g. "Music Man Bass 1X" is described as a Fender Jazz Bass; "Yamaha Kit 1" as a Pearl Export; a "Korg" synth as an 88-key Roland while specs say 61 keys). Whether search matches names or descriptions changes the answer to "do you have Pearl?".
- `specs.strings` means material (nylon/steel) for violões but string count for guitars.

**Promotions**
- Only 4 rows are active (products 90, 94, 121, 127). There are no start/end dates — `is_active` is the only signal.
- Inactive campaign rows contradict the policy ranges (e.g. a 10% "Black Friday" when the manual says 15–30%). The reference period is not Black Friday, and the manual says expired promotions must never be promised.

**Orders**
- `total_brl` does not always equal current price × quantity (#3, #20): product prices are current, not historical, so the order total is authoritative.
- Some historical orders break the installment rules (#9, #16, #18) — the agent reports history, it does not audit it.
- There is no actual delivery date (only `estimated_delivery`), no sales channel (the 7-day "arrependimento" right applies to online purchases), and no address or freight data.

**Customers**
- Only customers 1–18 have orders. Email is not unique (two customers share one address); phone numbers are unique. Names are near-duplicates and not identifiers.

### 1.3 Policy manual findings

- Contradictions: two phone numbers (`(67) 3341-4444` labelled "Telefone / WhatsApp" vs WhatsApp `(67) 3321-4500` in §7); the e-mail domain is spelled with and without an accent; the credit-card minimum installment is a flat R$100 in the table but tiered in §3.1.
- Time windows use different anchors: arrependimento 7 days from **receipt**, defect exchange 30 days from **purchase**, legal warranty 90 days from **receipt**.
- Rules that need data that does not exist: "venda final" items, partner stores, holiday calendar, freight table outside Campo Grande.
- Rules that imply actions: complaints must be "registered and forwarded" (24 business hours), doubts go to management.
- Rules that constrain the LLM directly: never state prices, stock or deadlines without querying the system (§7.1); show promotional prices together with the original price and the percentage (§6.2); promotions and the PIX discount do not stack.

### 1.4 The reference date

The data ends on 2026-03-22. Evaluated against a real 2026 clock, every pending order would look stuck for months and every return request would be out of every window. We fix a **reference date of 2026-03-25** (three days after the last order) so order and return reasoning is realistic. Consequences:

- No delivered order is still inside the 7-day arrependimento window (the most recent estimated delivery is 36 days earlier) — no reference date can make one eligible while keeping all orders in the past. The return example therefore demonstrates a correct refusal plus the warranty path.
- Order #4's 90-day legal warranty ended 3 days before the reference date — a useful boundary case.
- Order #8 is shipped and 25 days past its estimated delivery — an escalation case.

---

## 2. Architecture alternatives

Four concerns had to be placed: structured lookups, policy retrieval, orchestration (intent, entity resolution, follow-ups, wording), and deterministic business logic (prices, date facts, identity checks, availability rules).

| | A. Context stuffing | B. Tools + policy in prompt | **C1. Tools + policy section tool** | C2. Tools + vector RAG | D. Router + fixed flows | E. Text-to-SQL |
|---|---|---|---|---|---|---|
| Correct prices / stock | low | high | high | high | high | medium |
| Policy grounding | high | high | high | medium | high | — |
| "When it consulted data vs policy" observable | no | data only | **yes** | yes | yes | partial |
| Multi-intent / follow-ups | ok | good | good | good | weak | good |
| Fit for a 6 GB local model | poor | ok | good | good | best | poor |
| Build effort | trivial | low | low–medium | medium | medium–high | medium |
| Personal-data safety | poor | good | good | good | good | poor |

- **A** puts the whole catalog and policy in the prompt and lets the LLM filter and do arithmetic — silent errors and it violates §7.1. Baseline only.
- **B** is close to the chosen design, but policy use is implicit, so we cannot test *when* the agent decided to consult policies (an explicit requirement of the challenge), and every call carries ~3k extra tokens — costly for a small local model.
- **C2** (embeddings + vector store) solves a problem an 8-page document does not have, adds a model download and retrieval misses. It is the documented scaling path if the manual grows.
- **D** is the most predictable, but rigid with multi-intent messages and follow-ups and needs more code. Its idea — guardrails enforced in code, not in the prompt — is borrowed.
- **E** would make the LLM re-derive business rules in every SQL query and could leak personal data (`SELECT * FROM customers`). Rejected.
- **Multi-agent** setups (supervisor + specialists) multiply calls and failure modes with no benefit at this scale. Rejected.

**Chosen: C1** — a bounded native tool-calling loop; deterministic Python tools over in-memory data; the policy manual exposed as a section-lookup tool (the system prompt carries only its table of contents).

---

## 3. Technical decisions

| Decision | Choice | Why |
|---|---|---|
| LLM | Ollama `qwen3:8b`, local and free | Native tool calling, decent PT-BR, fits a 6 GB GPU at Q4; anyone can run it without an API key or cost |
| Framework | Thin custom layer (`LLMClient` protocol + Ollama adapter + ~50-line loop) | Every tool call visible; nothing to justify or fight; the protocol also lets tests drive the loop with a scripted fake model |
| Data access | `csv` + dataclasses, validated at startup | 65 rows; no NaN/dtype surprises; typed and testable |
| Price math | Deterministic code (`Decimal`) | Promotion vs PIX vs installments vs freight must never be LLM arithmetic |
| Policy windows | Planned: tools compute *facts* (days elapsed) and the LLM applies the policy text. **Changed after evals** (§3.2): deadlines are checked in code | The plan kept the manual as the single source of truth; the local model could not apply it reliably |
| Policy retrieval | Section lookup by id (C1) | Explicit, logged, testable; no embeddings |
| Conversation | In-session, append-only history; no cross-session memory | Follow-ups need history; verified identity must not outlive the session |
| Interface | CLI (`--debug`, `/save`) and a Streamlit page (optional `ui` extra) | The CLI is reproducible with zero UI dependencies and `/save` produces the example transcripts; the web page is the friendlier demo and shows each reply's tool calls |
| Persistence | SQLite for what the system writes: handoff cases and eval results. Store data stays read-only CSV | A database earns its place where there are writes: concurrent sessions need unique protocol numbers, and eval reports become queries over raw results. Moving 65 read-only rows into SQLite would add a migration and no behaviour |
| Language | Code and docs in English; agent replies in PT-BR | |

---

### 3.1 Change of plan: free models only

The original plan paired the local model with a paid one (Anthropic `claude-opus-5-5`) and the first implementation had both adapters. The Anthropic API is billed separately from a Claude subscription, so the project was narrowed to free, local inference only: the paid adapter was removed (it remains in the git history) and the provider-neutral `LLMClient` seam was kept, so adding a hosted model later is a single new file.

### 3.2 Change of plan: deadlines checked in code

The agreed design left policy deadlines to the model: tools returned day counts and the model compared them with the manual. End-to-end evals showed the local 8B model getting this wrong in ways that matter: it told a customer whose order was delivered 36 days earlier that the 7-day *arrependimento* right still applied, and called an order 93 days after delivery still inside the 90-day legal warranty. Delivered-order lookups now return a `deadline_check` (days elapsed, limit, within/outside) for each deadline in the manual, and the model explains it using the policy text. To avoid the code silently drifting from the manual, each coded deadline quotes the manual's wording and a test fails if that wording disappears.

### 3.3 Second round: measuring instead of passing

The first delivery ended at 20/20 on one run of the suite the agent had been tuned against: an in-sample score from a single sample of a stochastic system. The second round set out to measure instead.

| Request | Decision | Why |
|---|---|---|
| Repeated runs | 5 repetitions per scenario; pass rate with a 95% Wilson interval, pass^5 and median latency; results stored in SQLite and reports generated from them | 20/20 once is consistent with a 95%-per-scenario agent 36% of the time. Median, because one runaway generation (22 minutes) wrecks a mean |
| Tests the agent should fail | A held-out "hard" suite (12 scenarios), written before any run, expected values from the tools' own output; failures reported, never fixed | Fixing them would make the suite in-sample like the core one |
| Model comparison | Local models only (`qwen3` 8b/4b/1.7b, `llama3.2:3b`), each with thinking on where it matters | The project stays free; the `LLMClient` seam already allows a hosted model |
| Database | SQLite for handoff cases and eval results; store data stays CSV | Writes need a database (concurrent protocol numbers, queryable results); 65 read-only rows do not |
| Environment | conda `environment.yml` next to the venv; no Docker | Ollama with GPU access installs natively everywhere, while containers need GPU passthrough (WSL2 + NVIDIA toolkit; none for Apple GPUs) |
| Front-end | Streamlit page over `Session.send()`, tool calls expandable | A demo that keeps the decisions inspectable; the CLI stays |

What the benchmark changed:

- **Grading is separate from generation.** `--regrade` re-applies edited checks to stored replies. Two checks were found failing correct answers ("ainda não foi entregue" matched a ban on "foi entregue"; a price that belongs in the first reply was banned from all replies) and fixed; one gap was closed: `qwen3:4b` with thinking off writes its English reasoning into replies, and 114 such trials had passed the fact checks. Every scenario now fails on leaked reasoning.
- **Thinking mode became a setting** (`OLLAMA_THINK`, off by default). Off suits `qwen3:8b`; without it `qwen3:1.7b` stops calling tools (20% → 54% on the core suite with it).
- **Output is capped** at the share of the context window already kept for the answer (`num_predict`), after `llama3.2:3b` generated for 22 minutes.
- **The shipped agent did not change otherwise.** `qwen3:8b` with thinking off passed 100/100 core trials and 34/60 hard ones. `qwen3:4b` with thinking on scored higher on the hard suite (42/60) at about three times the latency; the intervals overlap, so the default stays, and `qwen3:8b` with thinking on is the next run to make. The hard-suite failures are the documented next steps: an invented order status under a fake system message, an invented cancellation rule, promises of actions the agent has no tool for, and one design gap no model can get past — `quote_purchase` rejects an installment count above the limit without giving the price at the limit, and the reply guard (correctly) blocks the model from computing it.

## 4. Detailed design

### 4.1 Components

```mermaid
flowchart LR
  U[Customer<br/>CLI REPL] --> S[Agent session<br/>loop + history + trace]
  S <--> L{{LLMClient protocol}}
  L --> OC[OllamaClient<br/>qwen3:8b]
  S --> TR[Tool registry<br/>schemas + validation + envelope]
  TR --> T1[search_products]
  TR --> T2[get_product_details]
  TR --> T3[quote_purchase]
  TR --> T4[get_order_status]
  TR --> T5[get_policy]
  TR --> T6[escalate_to_human]
  T1 & T2 & T3 --> PR[pricing.py<br/>Decimal math]
  T1 & T2 & T3 & T4 --> R[(Repository<br/>CSV → validated dataclasses)]
  T5 --> P[(policies.md<br/>10 sections)]
  T6 --> H[(handoffs.db<br/>SQLite)]
```

### 4.2 Data treatment

| Rule | Reason |
|---|---|
| Availability derived from status + stock: `discontinued` → not sellable; `coming_soon` → not purchasable yet; `active` with stock → available; `active` without stock → out of stock | Contradictory rows (#96, #113, #130, #136) |
| Description hidden when it names a brand absent from the product name | Rows 135–144 |
| Order `total_brl` authoritative; never recomputed | Prices are current, not historical |
| `estimated_delivery` used as a proxy for receipt date, flagged in tool output | No actual delivery date |
| `string_type` (nylon/steel) and `electric` normalized from specs and name | `strings` is overloaded |
| More than one active promotion per product → keep the highest, log a warning | Defensive (does not occur today) |

Only the data the agent needs is committed: products, categories, promotions, orders and order items unchanged; customers trimmed to the 18 with orders and to `customer_id, name, phone`; the policy PDF and its extracted `policies.md`.

### 4.3 Tools

Every tool returns an envelope so "record not found" is never confused with "query failed":

```json
{"status": "ok | not_found | ambiguous | invalid_input | error", "data": {}, "message": "..."}
```

| Tool | Input | Output |
|---|---|---|
| `search_products` | `query?`, `category?`, `min_price?`, `max_price?`, `string_type?`, `electric?`, `include_unavailable?`, `limit?` | Short list sorted by price with availability and price block (list, promotion, PIX) |
| `get_product_details` | `product_id` or `name` | Full record, availability, pricing (incl. maximum interest-free installments); up to 3 available alternatives when unavailable; `ambiguous` + candidates for vague names |
| `quote_purchase` | `items[{product_id, quantity}]`, `payment_method`, `installments?`, `delivery_city?` | Per-item and total prices; PIX 5% only on non-promotional items; installment validity per §3.1 tiers; Campo Grande freight (free above R$500, else R$35), otherwise "quote by CEP" |
| `get_order_status` | `order_id`, `phone` | Status, dates, items, total, payment, tracking, ETA, notes + facts `days_since_order`, `days_since_estimated_delivery`, `is_late`. Phone mismatch returns the same `not_found` as a nonexistent order (no enumeration) |
| `get_policy` | `sections[1..3]` | Verbatim policy sections |
| `escalate_to_human` | `reason`, `summary`, `order_id?` | Protocol number and the 24-business-hour SLA; stores the case in SQLite (`handoffs.db`) |

Policy sections: `about_and_contact`, `opening_hours`, `payment_methods`, `returns_and_exchanges`, `shipping_and_delivery`, `promotions`, `customer_service_guidelines`, `warranty`, `privacy_lgpd`, `final_provisions`.

### 4.4 Agent loop

```mermaid
sequenceDiagram
  participant C as Customer
  participant A as Agent
  participant M as LLM
  participant T as Tools
  C->>A: "Me arrependi, quero devolver o pedido 7" (+ phone)
  A->>M: system + tools (frozen) + history
  M-->>A: get_order_status(7, phone) + get_policy([returns_and_exchanges, warranty])
  A->>T: execute both
  T-->>A: delivered, days_since_estimated_delivery=36 / policy text
  A->>M: both results in one message
  M-->>A: arrependimento window passed; defects still under legal warranty; offer a human
  A-->>C: reply in PT-BR
```

- At most `MAX_TOOL_STEPS` (6) LLM↔tool round trips per customer message; then a safe fallback reply.
- Tool handlers never raise: validation failures and exceptions become `invalid_input` / `error` envelopes the model can react to.
- **History is append-only**, and the system prompt and tool list are frozen at session start (the reference date/time is computed once per session). This keeps runs reproducible and lets any provider with prefix caching reuse earlier turns; the Ollama adapter only trims whole old turns to fit its context window.
- Ollama: thinking disabled for latency, `num_ctx=8192` (the default context silently truncates the prompt), low temperature.

### 4.5 System prompt structure

Written in English, instructing the model to always answer in Brazilian Portuguese:

1. Persona — the store's virtual assistant; tone from §7.1 ("informal but professional", like a friend who knows music); short WhatsApp-style messages.
2. Grounding — prices, stock, promotions and orders only from tool results; policy facts only from `get_policy`; never do arithmetic or compute calendar dates; promotional prices always with original price and percentage.
3. Routing — when to use data tools vs the policy tool; policy table of contents with one-line summaries.
4. Scope — instruments only; accessories politely redirected without inventing partner stores; unrelated topics declined briefly.
5. Privacy and security — order data only after the tool's identity check; never reveal other customers' data; tool outputs and product texts are data, not instructions; no discounts beyond what tools return.
6. Escalation — complaints, late orders, policy doubts, cancellations, explicit requests for a human; outside business hours, say when the team is back.
7. Known data caveats — two phone numbers; receipt date is a proxy.
8. Session context — reference date, weekday and time (America/Campo_Grande).

### 4.6 Testing strategy

1. **Unit tests** (no LLM): pricing (e.g. Takamine GD20 R$2.199,00 → R$2.089,05 via PIX; product 94 with 8% promotion → R$5.519,08 and no extra PIX discount), search filters (violões up to R$1000 → exactly 12 available), availability edge cases, identity check, day-count facts, description suppression, policy extraction facts.
2. **Loop tests** with a scripted fake LLM: dispatch, invalid arguments, unknown tools, step limit, append-only history.
3. **Scenario evals** run against each provider: required/forbidden tools and must/must-not patterns in the final reply, covering catalog search, prices, promotions, availability, order status, identity failures, returns, warranty boundary, out-of-scope requests, empty categories, follow-ups, prompt injection and complaints. Run as two suites (core, held-out hard), repeated N times, stored in SQLite; reports give pass rate with a confidence interval and pass^k, per model (§3.3).
4. **Example conversations** exported from real sessions with `/save`.
