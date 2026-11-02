# music_agent — Empório da Música customer-service agent

A text-message customer-service agent for **Empório da Música**, a fictional musical-instrument store in Campo Grande/MS, built for Artefact's AI Engineer technical challenge. It answers customers in Brazilian Portuguese, looks up products, prices, promotions and orders in the store's data, applies the store's policy manual, declines out-of-scope requests and hands cases to the human team when needed.

It runs **entirely locally and for free**: a local LLM (`qwen3:8b` via [Ollama](https://ollama.com)) orchestrates a small set of deterministic Python tools over the store's CSV data and policy manual.

- Design record (discovery findings, alternatives considered, decisions): [docs/DESIGN.md](docs/DESIGN.md)
- Example conversations: [examples/](examples/)
- Evaluation reports with full transcripts: [evals/reports/](evals/reports/)

---

## Quickstart

**Requirements:** Python 3.10+, [Ollama](https://ollama.com/download), ~6 GB free disk. A GPU with ≥6 GB VRAM makes replies take seconds; CPU-only works but is much slower.

```bash
# 1. Model (one-time, ~5 GB)
ollama pull qwen3:8b

# 2. Project
git clone https://github.com/filetii/music_agent.git
cd music_agent
python -m venv .venv
.venv\Scripts\activate          # Windows  (Linux/macOS: source .venv/bin/activate)
pip install -e ".[dev]"

# 3. Chat
emporio            # or: python -m emporio.cli
emporio --debug    # also shows every tool call and its result status
```

Inside the chat: `/save [file]` exports the conversation (with tool calls) to `transcripts/`, `/reset` starts over, `/debug` toggles tool display, `/sair` exits. The first message takes longer while Ollama loads the model.

Configuration is optional — copy `.env.example` to `.env` to change the model (`OLLAMA_MODEL`), Ollama host, context size, the reference date or the tool-step limit. No secrets are needed.

```bash
pytest                         # 90+ unit tests, no LLM needed (~3 s)
python -m evals.run            # 20 end-to-end scenarios against the local model (~6 min on a laptop GPU)
python -m evals.run --model qwen3:4b --only price_takamine_gd20,order_wrong_phone
```

---

## How it works

```mermaid
flowchart LR
  U[Customer<br/>CLI] --> S[Agent session<br/>bounded tool loop + grounding guard]
  S <--> L[LLMClient<br/>Ollama qwen3:8b]
  S --> TR[Tool registry<br/>schema validation + result envelopes]
  TR --> T1[search_products]
  TR --> T2[get_product_details]
  TR --> T3[quote_purchase]
  TR --> T4[get_order_status]
  TR --> T5[get_policy]
  TR --> T6[escalate_to_human]
  T1 & T2 & T3 --> PR[pricing.py<br/>Decimal math]
  T1 & T2 & T3 & T4 --> R[(Store data<br/>CSV → validated objects)]
  T5 & T4 --> P[(policies.md<br/>10 sections)]
  T6 --> H[(handoffs.jsonl)]
```

For each customer message the agent sends the conversation to the model with six tools. The model decides which tools to call (several per turn if needed); the tools answer from the data and the policy manual; the model writes the reply. At most 6 model↔tool round trips per message.

| Tool | What it does |
|---|---|
| `search_products` | Catalog search by text, category, price range, string type (nylon/aço), pickup (eletroacústico) |
| `get_product_details` | One product by name or id: price, active promotion, PIX price, max interest-free installments, availability, specs; alternatives when unavailable; candidates when the name is ambiguous |
| `quote_purchase` | Exact amount for one or more products with a payment method: promotions, PIX, installment rules, Campo Grande delivery fee |
| `get_order_status` | Order status, items, tracking; **requires order number + registered phone**. Delivered orders also return a deadline check (return, defect exchange, preference exchange, legal warranty) and the returns and warranty policy sections |
| `get_policy` | Verbatim sections of the policy manual (hours, contact, payments, returns, shipping, promotions, warranty, privacy…) |
| `escalate_to_human` | Registers the case for the human team and returns a protocol number |

Every tool returns an envelope — `ok`, `not_found`, `ambiguous`, `invalid_input` or `error` — so "this record does not exist" is never confused with "the lookup failed", and tool handlers never raise.

---

## Technical decisions

### Approach: deterministic tools + LLM orchestration (no vector RAG, no framework)

The challenge mixes two kinds of knowledge: **structured operational data** (65 products, 20 orders) and a **short policy manual** (8 pages, ~3k tokens). Several architectures were compared before writing code ([DESIGN.md §2](docs/DESIGN.md#2-architecture-alternatives)): stuffing everything into the prompt, tool calling with the policy in the prompt, tool calling with the policy as a tool, vector RAG, an intent router with fixed flows, text-to-SQL and multi-agent setups.

The chosen design keeps a clear line between what the LLM is good at and what code must guarantee:

| The LLM does | Code guarantees |
|---|---|
| Understand the request, pick tools, resolve follow-ups ("e em 10x?") | Product lookup, filtering, availability rules |
| Read the policy text and explain how it applies | All money math: promotions, PIX, installments, freight (`Decimal`) |
| Write a friendly reply in the store's tone | Identity check before any order data; same answer for wrong phone and missing order |
| Decide when to escalate | Policy deadlines (days elapsed vs limit); policy retrieval for delivered orders; price grounding check |

- **No vector database.** The manual is small and well structured; it is split by its own headings into 10 sections and the model requests sections by id from a table of contents in the system prompt. This is exact, inspectable (every policy lookup is logged and testable) and needs no embedding model. Vector search is the scaling path if the manual grows to many documents.
- **No SQL agent.** The question space is small and known; letting the model write SQL would mean re-deriving business rules on every query and risking personal-data exposure.
- **No agent framework.** The loop is ~50 lines over a tiny `LLMClient` protocol. Every tool call is visible, the loop is unit-tested with a scripted fake model, and there is nothing a framework would add at this scale.
- **Policy deadlines are checked in code — a decision changed by the evals.** The plan was to give the model day counts and let it apply the manual's deadlines, keeping the manual as the only place rules live. The evals showed the 8B model telling a customer 36 days after delivery that the 7-day return right still applied. Delivered-order lookups now return a deadline check (days elapsed, limit, open/expired) for the return, defect-exchange, preference-exchange and legal-warranty deadlines; the model explains it with the policy text. Each coded deadline quotes the manual's wording and a test fails if the manual changes, so code and manual cannot silently drift apart ([DESIGN.md §3.2](docs/DESIGN.md#32-change-of-plan-deadlines-checked-in-code)).

### Model and provider: `qwen3:8b` on Ollama, free and local

Chosen for native tool calling in Ollama, reasonable Brazilian Portuguese, and fitting a 6 GB laptop GPU at 4-bit quantization — so anyone can run the project without an API key or cost. Thinking mode is disabled for latency; `num_ctx` is raised to 8192 because Ollama silently truncates longer prompts (the adapter also trims whole old turns to fit).

The original plan also included a paid model (Claude Opus via the Anthropic API) for comparison; it was dropped because API usage is billed separately from a Claude subscription and the project was kept free ([DESIGN.md §3.1](docs/DESIGN.md#31-change-of-plan-free-models-only)). The `LLMClient` seam means a hosted model is a single new adapter file.

A small local model is the main quality constraint of this project, and much of the design responds to it — see [Evaluation](#evaluation).

### Prompt strategy

The system prompt ([src/emporio/prompts.py](src/emporio/prompts.py)) is built once per session and then frozen; the history is append-only. Sections: persona and tone (taken from the manual's §7.1: "informal mas profissional"), grounding rules (facts only from tools; never compute prices or dates), tool-selection guide with the policy table of contents, scope (instruments only; categories that currently have no products), privacy and prompt-injection rules, escalation rules, known data caveats, and the session's date/time. Instructions are in English; replies are always in Portuguese.

Prompt text alone was not enough for an 8B model, so the reliable fixes went into **tool design**: tools accept product names instead of ids, name searches show unavailable products with a Portuguese status label, products state "nenhuma promoção ativa" explicitly, and delivered-order lookups return the returns/warranty policy together with the facts.

### Reply guard against invented prices and stalled lookups

The first local test showed `qwen3:8b` answering a price question **without calling any tool** and inventing a price and a promotion. The agent now checks every final reply deterministically:

- each `R$` amount must appear in a tool result or in the customer's own messages;
- a reply that promises to look something up ("vou verificar… um momento") must not end the turn without a tool call — another failure the model showed;
- a promotional price must be shown with the original price and the percentage (manual §6.2), and a product reported as unavailable must come with the available alternatives and their prices (manual §7.3). Tools hand the model ready-made text for this ("De R$ 5.999,00 por R$ 5.519,08 (8% de desconto…)"), but the 8B model still dropped it about two times in three until this check was added.

If a check fails, the model gets one corrective retry. If an invented price or a stalled lookup persists, the customer gets a safe fallback reply; the two presentation rules never block an answer that is correct but incomplete. Tool calls are checked too: an order lookup only runs with an order number and phone **the customer actually typed** in the conversation — the model once invented both in a follow-up, and the same check stops it from looking up orders with data the customer never provided (e.g. prompted by an injection). Related robustness choices: unknown tool arguments (the model misspelled `include_unavailable` once and gave up on the error) are dropped with a warning instead of rejecting the call, and a failed model call discards the unanswered turn so the customer can simply resend.

### Conversation and memory

The conversation history lives in the session, so follow-ups work ("Quanto custa o F310?" → "E em 10x?"). Nothing persists across sessions: a verified identity should not outlive the conversation (LGPD), and the CLI has no login. `/save` exports a transcript with the tool trace.

### Interface

A terminal chat: zero UI dependencies and easy to evaluate. `--debug` shows each tool call so the agent's decisions are inspectable.

---

## Data treatment and assumptions

The data and the manual contain inconsistencies; each was handled explicitly rather than left to the model ([full discovery notes](docs/DESIGN.md#1-discovery-what-the-store-gave-us)).

**Reference date — `REFERENCE_DATE=2026-03-25`.** The data ends on 2026-03-22. Evaluated against a real 2026 clock, every pending order would look stuck for months and every return request would be outside every window. The agent therefore treats **25/03/2026 (a Wednesday, three days after the last order)** as "today", so order status and return/warranty reasoning are realistic. Consequences, all verified by tests and evals: order #8 is 25 days late; no delivered order is still inside the 7-day *arrependimento* window (no date can achieve that while keeping every order in the past), so the return example correctly declines and points to the warranty; order #4's 90-day legal warranty ended 3 days earlier. Configurable in `.env`.

| Issue in the data | Treatment |
|---|---|
| Status and stock contradict (`active` with 0 stock; `discontinued` with 13; `coming_soon` already sold) | Availability derived by rule: discontinued → not sold; coming soon → not sold yet; active with stock → available; active without stock → out of stock (+ alternatives) |
| Basses, drums and keyboards (#135–144) have descriptions naming other brands than the product name | Detected at load (known-brand check) and hidden; name and specs are treated as authoritative |
| Order totals differ from current price × quantity | `total_brl` is authoritative; prices are current, not historical |
| No actual delivery date | Estimated delivery is used as the receipt date, and the agent says so |
| Promotions have no dates, only `is_active` | Only active promotions exist for the agent; inactive campaigns are never mentioned as current |
| Categories 6–8 (sopros, cordas orquestrais) have no products | The agent says they are not in the catalog right now |
| Manual: installment minimum is R$100 in a table but tiered in §3.1, with a garbled ≤3x rule | §3.1 tiers applied: up to 3x ≥ R$50, 4–6x ≥ R$80, 7–12x ≥ R$100 per installment |
| Manual: two phone numbers | Both given with the manual's own labels |
| Manual: the 7-day preference exchange has no starting point | Counted from receipt, like the return right |
| Freight outside Campo Grande "calculated by CEP" | Not invented: the agent says it needs a quote |
| Policy PDF tables are lost by text extraction | The manual was transcribed to [data/policies.md](data/policies.md); a test compares it word for word with the PDF, so any changed word or number fails |
| Customer data is personal | Only what the agent needs is committed: the 18 customers who have orders, with id, name and phone |

---

## Evaluation

**Unit tests** (`pytest`, no LLM): data loading and validation, availability rules, search and fuzzy name resolution, every pricing rule (e.g. Takamine GD20 R$ 2.199,00 → R$ 2.089,05 via PIX; Taylor 110e with 8% promotion R$ 5.519,08 and no extra PIX discount; installment tier boundaries), identity check, tool schema validation, policy fidelity to the PDF, and the agent loop with a scripted fake model (tool dispatch, step limit, failures, refusals, grounding guard, append-only history).

**End-to-end scenarios** (`python -m evals.run`): 20 scripted conversations against the real model, each in a fresh session, checked mechanically — which tools must or must not be called and what the reply must or must not contain. They cover catalog search with filters, prices, promotions vs PIX, out-of-stock and coming-soon products, store information, order status, wrong phone, missing phone, returns after the window, the warranty boundary, accessories, off-topic questions, empty categories, follow-ups, two prompt-injection attempts, an expired campaign, a multi-item quote and a complaint.

**Results with `qwen3:8b`** — final run: **20/20 passed**, ~23 s per scenario on a laptop RTX 4050 (6 GB), ~108k tokens for the whole suite ([report with transcripts](evals/reports/ollama_qwen3_8b.md)). How it got there — each step was driven by reading the failing transcripts ([baseline report](evals/reports/01-baseline-qwen3_8b.md)):

| Run | Passed | Main change before the run |
|---|---|---|
| 1 | 11/20 | Baseline: prompt + tools as designed, price guard |
| 2 | 12/20 | `quote_purchase` accepts product names (the model invented ids); name searches show unavailable products; prompt rules for empty categories and complaints |
| 3 | 16/20 | Portuguese availability labels and explicit "nenhuma promoção ativa"; delivered orders return the returns/warranty policy; stricter Black Friday check; complaint scenario made two-turn |
| 4 | 16/20 | Deadline checks moved into code; return/warranty checks tightened (they had passed a wrong answer) |
| 5 | 17/20 | Unknown tool arguments ignored with a warning; guard against "vou verificar" with no tool call; deadline summary lists open/expired deadlines |
| 6 | 18/20 | Order lookups only with an order number and phone the customer typed |
| 7 | **20/20** | Ready-made promotion text + manual §6.2 check; alternatives attached to unavailable search results + manual §7.3 check (the two scenarios then passed 3 out of 3 targeted runs) |

Some checks were also corrected along the way, in both directions: patterns that rejected correct answers were widened (e.g. "não foi encontrado", "6 parcelas"), and checks that accepted wrong answers were tightened (return and warranty scenarios). Runs vary: local generation is not perfectly deterministic, so 20/20 in one run does not mean every scenario passes every time — earlier runs showed individual scenarios flipping between pass and fail.

---

## Example conversations

Recorded with [scripts/record_examples.py](scripts/record_examples.py): scripted customer messages played against the local `qwen3:8b`, replies saved **unedited** together with the tool calls the agent made.

| Conversation | What it shows |
|---|---|
| [01 — violões até R$1000](examples/01-catalogo-violoes-ate-1000.md) | Catalog search with filters, then a recommendation from the previous results |
| [02 — preço e promoção](examples/02-preco-e-promocao.md) | Price lookup, PIX price, installments; an active promotion that does not stack with PIX |
| [03 — devolução de pedido](examples/03-devolucao-pedido.md) | **Non-trivial:** identity check, order data + policy deadlines → return right expired, legal warranty still open |
| [04 — pedido atrasado](examples/04-pedido-atrasado.md) | Late shipment detected from order data (25 days past the estimate), then escalation with a protocol number |
| [05 — loja e fora de escopo](examples/05-loja-e-fora-de-escopo.md) | Address and hours from the policy manual, accessories declined, a discount prompt-injection refused |

The evaluation reports in [evals/reports/](evals/reports/) contain 20 more transcripts, including the failures.

---

## Known limitations and what I would do with more time

- **Small local model.** `qwen3:8b` sometimes skips a needed tool, ignores a tool result field, or adds facts the tools did not return. The reply checks catch invented prices and the failure modes seen in testing, but not every kind of invention (e.g. a made-up recommendation to "contact the carrier"). With more time: evaluate larger local models (e.g. `qwen3:14b` on a bigger GPU) or a hosted model behind the same `LLMClient` seam, and keep turning observed failure modes into checks.
- **Evals are small and single-run.** 20 scenarios with regex checks; local generation is not perfectly deterministic, so one run is a noisy estimate. Next: several runs per scenario, more paraphrases, and an LLM-as-judge for tone and faithfulness.
- **No authentication.** Order lookups require order number + phone, which is reasonable for a chat prototype but not real authentication.
- **Escalation is a local file.** `handoffs.jsonl` stands in for a ticketing system.
- **No holiday calendar, partner stores or freight table** exist in the data, so the agent cannot answer those beyond saying so.
- **CLI only.** A WhatsApp or web front-end would sit on top of `Session.send()`.

---

## Use of AI coding assistants

I built this project with **Claude Code** (Anthropic's agentic coding assistant, running in VS Code with Claude Opus) as a pair programmer, using a phased workflow I set up front so that decisions came before code:

1. **Discovery.** I asked for a full inspection of the data and the policy manual before any proposal. Claude profiled every CSV and read the PDF; together we listed the contradictions (status vs stock, mismatched descriptions, two phone numbers, installment rules, missing delivery dates) and spotted the reference-date problem.
2. **Architecture.** I asked for at least three alternatives compared on reliability, cost, complexity and fit, without picking one early. We compared seven ([DESIGN.md §2](docs/DESIGN.md#2-architecture-alternatives)) and I chose the hybrid "deterministic tools + policy section lookup" design, no framework.
3. **Design.** Tool interfaces, data treatment, prompt structure, repository layout and test strategy were agreed and committed as [docs/DESIGN.md](docs/DESIGN.md) before implementation.
4. **Implementation in small commits.** Claude wrote the code and tests stage by stage (data → pricing → policies → tools → LLM layer → agent → evals); I reviewed each stage. Commits made with the assistant carry a `Co-Authored-By: Claude` trailer.
5. **Evaluation-driven iteration.** Claude built the scenario harness, ran it against the local model and diagnosed failures from the transcripts; we fixed them mostly in tool design rather than prompt text (11/20 → 20/20, see [Evaluation](#evaluation)).

**Decisions that were mine:** the reference date, the architecture, keeping price math in code, the free-only switch (after learning the API is billed separately from my Claude subscription), committing only the customer data the agent needs, and moving deadline checks into code once the evals showed the local model could not apply them reliably.

**What the assistant did:** most of the code, tests and eval harness, and first drafts of the documentation, which I reviewed and edited.

**What I learned from the workflow:** forcing discovery and design before code surfaced the data problems early, and the end-to-end evals were the most valuable part — they showed failure modes (invented prices, invented product ids, misapplied deadlines) that unit tests could not, and they changed two design decisions.
