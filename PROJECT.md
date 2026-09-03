# Sierra Outfitters Agent — Project Reference 🏔️

A chat agent for **Sierra Outfitters** (an outdoor retailer), built from scratch
on the OpenAI chat-completions API with **no agent frameworks** — just a message
history, a hand-rolled tool registry, and a dispatch loop. It runs as a terminal
REPL or a browser UI (Flask), both driving the same agent core. This document is
the durable reference for how the project is structured, why, and how to run and
extend it.

---

## What it does

Both front ends require a (stub) login first, then offer:

1. **Order Status & Tracking** — looks up an order and returns its status plus a
   USPS tracking link. Once signed in the agent already knows the customer's
   email, so it only needs the order number.
2. **Product Recommendations** — answers gear questions from the static catalog.
3. **Early Risers Promotion** — issues a unique 10% discount code, but only when
   the customer explicitly asks *and* the current time is 8:00–10:00 AM Pacific.
4. **List my orders** — enumerates the signed-in customer's orders (no order
   number needed).
5. **Human handoff** — escalates to a support teammate on explicit request, or
   offers one when it hits a wall it can't resolve (errored order, an item it
   can't identify, an out-of-scope ask like a refund).

Everything is wrapped in the Sierra Outfitters brand voice (outdoorsy, warm,
tasteful emojis, "Onward into the unknown!").

**Authentication is a deliberate stub** (any known customer email + password
`1234`). What's real is the *authorization boundary*: once signed in, the agent's
identity is server-held and stamped into every order tool call, so it can only
ever act on that customer's own orders — the model can't be talked into another
account.

---

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then paste your OPENAI_API_KEY into .env
python main.py                # terminal: sign in (any customer email + '1234'), 'quit' to exit
python3 -m web                # web UI on http://127.0.0.1:8000 (login page first)
```

Sign in as any customer from `data/CustomerOrders.json` (e.g.
`john.doe@example.com`) with password `1234`. The two static datasets live in
`data/CustomerOrders.json` (Appendix A) and `data/ProductCatalog.json`
(Appendix B).

---

## Architecture

```
user input
   │
   ▼
main.py ──► agent/loop.py ──► OpenAI chat API
                  │                  │
                  │           (tool call?)
                  │                  │
                  ▼                  ▼
          message history     agent/tools.py (ToolRegistry)
                                     │
                    ┌────────────────┼────────────────┐
                    ▼                ▼                ▼
            agent/stores.py   agent/stores.py   agent/promo.py
             (OrderStore)     (ProductCatalog)  (time gate + code)
```

The loop is the whole agent: read user input → send to the model → if the model
requests a tool, run it locally and feed the JSON result back → repeat until the
model returns a plain text reply.

### Files

| File | Responsibility |
|------|----------------|
| `main.py` | Entry point; wires data paths into the chat loop. |
| `agent/loop.py` | Read/reason/act loop, message history, tool-round guard, CLI login. |
| `agent/tools.py` | `ToolRegistry`: JSON schemas the model sees + local handlers. |
| `agent/stores.py` | `OrderStore` / `ProductCatalog` — thin data layer over JSON. |
| `agent/auth.py` | Stub login (`check_login`), shared by CLI and web. |
| `agent/promo.py` | Early Risers time-window check + code generation. |
| `agent/render.py` | Strips markdown from model output for plain-terminal display. |
| `agent/prompts.py` | Brand voice + behavioral rules; authenticated prompt/greeting variants. |
| `agent/client.py` | OpenAI client construction + model selection. |
| `web/server.py` | Flask app: login, sessions, `/chat`, `/dashboard`, over the same `run_turn`. |
| `web/*.html` | Login page, chat UI, eval dashboard (self-contained). |
| `evals/__main__.py` | Unified runner (deterministic + live + llm); writes `results.json`. |
| `evals/live.py` | Live behavioral cases (fixed prompts). |
| `evals/sim.py` + `scenarios.json` | Simulated-customer harness + scenario catalog. |
| `data/*.json` | Static datasets (Appendix A & B). |

---

## Key design decisions & trade-offs

- **Tools do the knowing; the model does the talking.** Everything factual —
  order lookups, catalog data, the current Pacific time, promo eligibility — is
  resolved in *code*, not the prompt. The model only phrases the tool's result,
  so it cannot invent an order, fabricate a product, or hand out a discount at
  the wrong hour. This is the core safety property.

- **The extensibility seam is the tool registry.** Adding a capability =
  one handler method + one JSON schema + one entry in the `_handlers` dict.
  Nothing in the loop changes. This is deliberately the thing that's easy to
  extend live during the interview.

- **The model gathers missing info conversationally** rather than through a
  hard-coded form flow. When *not* signed in (there's an anonymous CLI code path)
  it asks for email + order number; when signed in, the email is dropped from the
  tool schema and it only asks for the order number.

- **Identity is a boundary, not a tool argument.** When a session is
  authenticated (`ToolRegistry(identity=…)`), the `get_order_status` schema omits
  the `email` field entirely, and dispatch *stamps* the session email over
  whatever the model supplied. So even if the model invents an email argument, a
  session can only ever reach its own orders. Prompt for tone, code for the
  guarantee — the same split as the promo gate.

- **Handoff = code detects, model acts.** Structural walls (an errored order, an
  item whose SKU isn't in the catalog, a shipped order with no tracking) are
  detected in code and surfaced as a `handoff_offer` in the tool result; the
  model does the asking and only calls `request_handoff` once the customer
  accepts (explicit requests hand off immediately). Detection stays reliable;
  phrasing and confirmation stay conversational.

- **The promo time-gate lives in code, not the prompt.** `promo.is_promo_active()`
  is the single source of truth for the 8–10 AM Pacific window, so the model
  can't be talked into issuing a code early. Codes use `secrets.token_hex` so
  they're unique and unguessable.

- **Static JSON behind a store interface, no database.** For two small files an
  in-memory dict is the right call. The `OrderStore` / `ProductCatalog`
  interface is where a real DB or search index would slot in if this scaled —
  callers use `.find()` / `.search()`, never `json.load`.

- **Keyword search over embeddings.** `ProductCatalog.search()` is a simple
  scored keyword scan. The LLM does the reasoning about which results fit the
  customer, so semantic search is overkill at this catalog size. Swapping in
  embeddings is a localized change if the catalog grows.

- **A tool-round cap (`MAX_TOOL_ROUNDS`)** guards against a pathological loop of
  tool calls in a single turn.

---

## Conversation-design notes (things that were tuned)

The behavioral bugs worth knowing about, since they shaped the prompt:

- **"Pretend to act" stall.** Early on, when asked for the promo outside the
  window, the model would reply *"Checking the current time… one moment 🧭"* and
  stop — never calling the tool, leaving the customer with no answer. Fixed by a
  prompt rule forbidding "checking / one moment / hold on" narration: it must
  call the tool and deliver the result in the same turn.

- **Asking the customer for the time.** The model would sometimes ask the
  *customer* what time it was, because the promo tool was framed only as
  "issue a code." Fixed by (a) reframing the tool description as
  *check-eligibility-and-issue* — it reads the Pacific clock itself — and
  (b) a prompt rule: "You do NOT know the current time; never ask the customer."

Both are verified across repeated runs (see "Testing").

---

## Optimizations made

Concrete improvements applied on top of the initial scaffold:

- **SKU → product-name mapping in order status.** Orders store products as SKUs
  (`SOBP001`), which are meaningless to a customer. `ProductCatalog` builds a SKU
  index at load time and exposes `name_for_sku()` / `has_sku()`, and the
  order-status tool maps each ordered SKU to its product name before replying. A
  SKU that isn't in the catalog **degrades to a neutral placeholder** ("an item we
  can't identify…") rather than exposing the raw identifier — so the model never
  sees, and so can't recite, a SKU — and that same condition is a structural
  handoff trigger. *(Files: `agent/stores.py`, `agent/tools.py`.)*

- **O(1) indexed lookups.** Both stores build dictionaries at construction time —
  `OrderStore` indexes orders by normalized order number, `ProductCatalog` indexes
  products by normalized SKU — so lookups are constant-time rather than scanning
  the list on every call.

- **Forgiving, normalized matching.** Emails, order numbers, and SKUs are
  lowercased/stripped (and a leading `#` removed) before comparison, so
  `#W001`, `w001`, and ` W001 ` all resolve to the same order.

- **Whole-word search with weighted fields.** Scoring used `str.count`, which
  counts *substrings* — so the term "me" matched inside "Ho**me** Decor" and a
  query like "something to keep me awake" returned a lampshade and a jetpack.
  Search now tokenizes to whole words, drops stopwords, and weights name (×4) and
  tag (×2) hits above description hits. Queries with no lexical overlap now
  return **nothing** rather than confident junk; the prompt tells the model to
  retry with different keywords before concluding we don't stock an item, which
  covers the recall gap without reaching for embeddings.

- **Agent no longer refuses items it does stock.** The "stay on-topic" rule made
  the model reject "caffeinated energy drink" as un-outdoorsy — from its own
  assumptions, without ever searching — while hallucinating products we don't
  carry. The catalogue is deliberately whimsical (jetpack, invisibility cloak,
  lampshade), so the model's mental image of "outdoor retailer" was the wrong
  oracle. Fixed by making the catalogue the explicit source of truth, requiring a
  search before any "we don't carry that," and scoping "on-topic" to subject
  matter rather than product category.

- **Recommendations read as prose, not a record dump.** The model recited
  `Description:` / `Tags:` labels verbatim. A prompt rule now asks it to describe
  products in its own words with a reason they fit. Tags stay in the payload
  (they genuinely help it judge relevance) — it's just told not to recite them.
  Verified by a live eval asserting no raw field labels appear.

- **Internal fields never reach the model.** `recommend_products` used to hand the
  model the whole catalog record, so it recited `SKU: SOBP001` at customers. The
  tool now projects results down to customer-facing fields
  (`CUSTOMER_FACING_FIELDS` in `agent/tools.py`). Filtering the *payload* rather
  than adding a "don't mention the SKU" prompt rule is deterministic — the model
  can't leak what it never sees — and it costs fewer tokens. Same principle as
  the SKU → name mapping: the tool layer decides what's customer-facing.

- **Plain-text output for the CLI.** The model kept emitting markdown (`**bold**`,
  `### headings`, `[text](url)`), which renders as literal noise in a terminal.
  Fixed in two layers: a prompt rule asking for plain text, **plus**
  `agent/render.py`, which strips residual markup at the display boundary. The
  prompt is a request; the render pass is a guarantee — LLMs drift back into
  markdown, so the deterministic layer is what actually holds. Underscore
  italics are deliberately left alone so emails/URLs aren't mangled. Rendering
  happens only at print time; message history keeps the model's original text.

- **Promo eligibility resolved in one tool round.** Reframing the promo tool as
  *check-and-issue* (it reads the Pacific clock itself) means eligibility is
  decided in a single tool call — no extra round-trip to ask for or fetch the
  time. *(See the conversation-design notes above.)*

---

## Testing

Three layers, from cheapest/most-certain to richest/most-exploratory.
`python -m evals` runs all three; `--deterministic`, `--live`, and `--llm` each
narrow to one layer. Every run writes `evals/results.json` (a partial run updates
only its own section), which the **dashboard** renders. The default hits the real
API — use `--deterministic` for a free, hermetic run.

### Deterministic eval harness (`python -m evals --deterministic`)

A dependency-free harness in `evals/` asserts on the tool/store/promo layer with
**no OpenAI calls**, so it's fast, free, and stable enough to be a CI gate (it
exits non-zero on any failure). 28 cases cover:

- order lookup returns the right status + tracking link
- SKU → **product-name** resolution; orphaned SKU degrades to a placeholder (no
  raw SKU) and raises a handoff offer; errored order raises a handoff offer; a
  clean order raises none; `request_handoff` returns a ticket
- email / order-number normalization (`#W001`, `w001`, ` W001 ` all match)
- wrong-email and unknown-order → not found
- product search returns relevant gear (and non-empty on empty query)
- the 8–10 AM **Pacific** promo gate — including a cross-timezone case (9 AM
  Eastern ≠ in-window) and the exclusive 10:00 boundary
- promo tool issues a code when active / declines when inactive
- promo code uniqueness, unknown-tool error handling, and markdown stripping

### Live behavioral evals (`python -m evals --live`)

`evals/live.py` covers what the deterministic layer can't see: whether the
*model* behaves. Every bug this project hit was intermittent (the promo stall
failed ~1 run in 3), so **each case runs N times** — variance is the point — and
the runner reports per-case run counts rather than hiding flakiness behind one
lucky pass. Opt-in via `--live` / `--only-live`, kept out of the default run
because it costs money and is inherently flaky.

**Why no LLM judge.** The cases assert on the **tool-call trace** and output
invariants, not prose quality: *did it actually call `recommend_products`?*, *did
a SKU or markdown leak?*, *did it ask for the email instead of inventing an
order?* "Did it call the tool" is a crisp boolean, and it's exactly what our two
bugs violated — the model *talked about* checking instead of checking. That gets
most of the value with none of an LLM judge's cost, latency, or nondeterminism.
Genuinely subjective tone ("is this on-brand?") is left to human review — the
lowest-value, highest-cost thing to automate.

**Trade-off:** running both by default means the plain `python -m evals` costs
money and can flake on network/model variance. That's a deliberate call — the
live layer is the one that catches the bugs that actually shipped, so it should
be the path of least resistance, not an opt-in people forget. CI pins
`--deterministic` to stay free and hermetic.

The trace needs no production instrumentation: the loop already records tool
calls in the message history, so the eval just reads them back out.

Cases: searches the catalog before refusing; finds whimsical items; asks for
missing order details; reports product names not SKUs; calls the promo tool
without stalling; never asks the customer for the time; no markdown; no raw
field labels.

### LLM-generated scenarios (`python -m evals --llm`)

Where the live evals send a *fixed* prompt, these are **multi-turn and
adaptive**: a simulated customer — a second ChatGPT client on the same API key,
with a persona and a goal (`evals/scenarios.json`, e.g. "John Doe checking his
parcel") — converses with the real agent and reacts to its replies. Each
conversation is classified **solved / handoff / unresolved** from the tool trace
plus a per-scenario ground-truth check (still no LLM judge), and a scenario passes
when the outcome matches its `expected_outcome`.

The point is different from the other layers: not asserting a known invariant but
**measuring the distribution of outcomes** and discovering paths no one scripted.
A conversation the agent botches becomes a candidate fixed eval case — sim
discovers, deterministic/live lock it down. Note a *handoff* is a correct outcome,
not a failure, when escalation is the right move.

### Dashboard (`/dashboard`)

`python3 -m web` → `http://127.0.0.1:8000/dashboard` renders the last
`results.json` — a success-rate tile per layer, the solved/handoff/unresolved
breakdown, and every case with failures and full simulated transcripts. It's a
**snapshot, not live** (it never triggers a run), and open (no login) since it's
an internal view over synthetic eval data.

---

## Data notes

- Datasets were repaired from the raw appendix paste (missing/doubled commas and
  multi-line string values) using `json-repair`, then formatted with `jq`.
- **Known inconsistency in the source data:** three SKUs referenced in orders
  (`SOBN008`, `SOCH010`, `SOGK009`) do not exist in the product catalog — they
  touch 4 of the 10 orders. This is realistic (order history outlives the
  catalog), so the agent degrades honestly: order status replaces an unresolvable
  SKU with a neutral placeholder (never the raw identifier) and **offers a human
  handoff**, rather than crashing, dropping the item, or leaking the SKU.

**Order fields:** `CustomerName`, `Email`, `OrderNumber`, `ProductsOrdered`,
`Status`, `TrackingNumber`.
**Product fields:** `ProductName`, `SKU`, `Inventory`, `Description`, `Tags`.

Tracking link format: `https://tools.usps.com/go/TrackConfirmAction?tLabels={trackingNumber}`

---

## Ideas for extension (natural next steps)

- Persist issued promo codes so a customer can't farm multiple in one window —
  the identity to key a grant on now exists (login), only persistence is missing.
- Replace the stub login with real auth (hashed credentials, OTP/session token);
  the authorization boundary already in place doesn't change.
- Add inventory/stock-aware recommendations ("only 14 left!").
- Swap keyword search for embeddings if the catalog grows.
- Streaming responses for a snappier feel.

See **`IMPROVEMENT.md`** for the deep-dive on known gaps and design extensions
(write actions, authorization, escalation, prompt injection, observability, and
the honest "when is MCP / a database actually right" discussion).
