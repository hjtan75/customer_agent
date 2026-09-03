# Sierra Outfitters Agent 🏔️

A chat agent for Sierra Outfitters, built from scratch on the OpenAI SDK (no
agent frameworks) — pointed at any OpenAI-compatible provider, Groq by default.
It handles **order status & tracking**, **product recommendations**, and the
**Early Risers promotion**. Runs as a terminal REPL or a browser UI with a stub
login — both drive the same agent core.

## Setup

Use `python3` to create the virtual environment:

```bash
python3 -m venv .venv        # one-time; creates the .venv/ folder
source .venv/bin/activate    # activate it — your prompt shows (.venv)
pip install -r requirements.txt
cp .env.example .env         # then paste your Groq key into LLM_API_KEY
```

The datasets ship pre-populated in `data/CustomerOrders.json` (Appendix A) and
`data/ProductCatalog.json` (Appendix B).

## Running

With the environment activated (`(.venv)` in your prompt), `python` points at
the venv's interpreter, so either command works:

```bash
python main.py               # start the chat loop
```

- Type your message at the `You:` prompt and press Enter.
- Type `quit` or `exit` (or press Ctrl-C) to leave the chat.

## Web UI

A browser front end over the same agent core, with a stub login:

```bash
python3 -m web                # starts a local server on http://127.0.0.1:8000
```

Open http://127.0.0.1:8000 and sign in as any customer from
`data/CustomerOrders.json` (e.g. `john.doe@example.com`) with password `1234`.

Signed in, the agent already knows your email (it never asks), you can ask "what
orders do I have?" to list them, and you can only ever see your **own** orders —
identity is held server-side and stamped into every order lookup, so the model
can't be talked into reading another account. The login is a deliberate stub; the
authorization boundary is the real part (see `IMPROVEMENT.md` §2.2).

> Uses port 8000, not Flask's default 5000, which macOS reserves for the AirPlay
> Receiver.

## Tests / evals

Three layers, from cheapest and most certain to richest and most exploratory.

```bash
python -m evals                 # everything (deterministic + live + llm)
python -m evals --deterministic # deterministic only — no API calls
python -m evals --live          # live behavioral only
python -m evals --llm           # LLM-generated scenarios only
python -m evals --live -n 5     # run each live case 5 times (default: 3)
```

Each run writes `evals/results.json`, which the **dashboard** reads (see below).
A partial run (e.g. `--deterministic`) updates only its own section and leaves the
others intact.

**Deterministic** (no API calls — fast, free, safe as a CI gate). Covers: order
lookup (status + tracking link), SKU → product-name resolution, orphaned-SKU
degradation + handoff offer, errored-order handoff, the `request_handoff` tool,
email/order-number normalization, not-found paths, product search relevance, SKU
never reaching the model, the 8–10 AM Pacific promo gate (across timezones), promo
issuance/decline, code uniqueness, and markdown stripping.

**Live behavioral** (real API). Asserts on the **tool-call trace** and output
invariants — did it actually call the tool, did it leak a SKU or markdown, did it
ask for missing details — rather than grading prose with an LLM judge. Cases run N
times and report run counts (`3/3`) to surface flakiness.

**LLM-generated scenarios** (real API, multi-turn). A simulated customer — a
second model client on the same provider/key, with a persona and a goal —
converses with the agent, and each conversation is classified
**solved / handoff / unresolved**
from the tool trace plus a ground-truth check (no LLM judge). A scenario passes
when the outcome matches its `expected_outcome`. The catalog lives in
`evals/scenarios.json` (e.g. "John Doe checking his parcel"); add a persona + goal
+ expected outcome to add a case. Note a *handoff* is a correct outcome, not a
failure, when escalation is the right move.

All layers print `PASS`/`FAIL` and exit non-zero on failure. The default hits the
real API, so **use `--deterministic` in CI**. See `evals/` and `PROJECT.md`.

### Eval dashboard

`python3 -m web` then open **http://127.0.0.1:8000/dashboard** — a snapshot (not
live) of the last `python -m evals` run: a success-rate tile per layer, the
solved/handoff/unresolved breakdown, every case with failures and full simulated
transcripts. It's open (no login) — an internal view over synthetic eval data.

## Exiting the environment

When you're done, leave the virtual environment with:

```bash
deactivate                   # returns you to your normal shell
```

To use the agent again later, just re-activate (`source .venv/bin/activate`) —
you don't need to recreate the venv or reinstall dependencies.

## How it works

The agent is a plain chat loop with hand-rolled tool dispatch:

```
user input → model → (tool call?) → run local tool → feed result back → reply
```

- `agent/loop.py` — the read/reason/act loop and message history.
- `agent/validator.py` — a one-token model call that rejects out-of-scope
  prompts before the main agent runs.
- `agent/tools.py` — a `ToolRegistry`: JSON schemas the model sees + the local
  functions they map to. Adding a capability is one method + one schema.
- `agent/stores.py` — `OrderStore` / `ProductCatalog`, a thin data layer over
  the JSON so storage stays swappable.
- `agent/promo.py` — Early Risers time-window + code generation.
- `agent/prompts.py` — Sierra brand voice and behavioural rules.
- `agent/client.py` — LLM client + model config (provider via `LLM_BASE_URL`).
- `web/` — a Flask front end (`server.py`) + pages (`login.html`, `index.html`)
  that call the same `run_turn()` the CLI does. The browser is just another I/O
  channel; the agent package is untouched.

## Key design decisions

- **Tools do the knowing, the model does the talking.** Anything factual —
  order lookups, product data, current Pacific time, promo eligibility — is
  resolved in code. The model only phrases results, so it can't invent an order
  or hand out a discount at the wrong hour.
- **The model asks for missing info** (email + order number) rather than a
  hard-coded form flow — cleaner conversation design and easy to extend. In the
  web UI the session is authenticated, so the email is dropped from the tool
  schema and the agent only asks for the order number.
- **Identity is a boundary, not a tool argument.** When signed in, the
  authenticated email is held on the registry and stamped into every order lookup
  server-side — the model can't supply or override it, so a session can only ever
  reach its own orders. Prompt for tone, code for the guarantee.
- **Extensibility seam = the tool registry.** New capabilities drop in without
  touching the loop.
- **No database / no MCP.** Two small static files → in-memory dicts behind a
  store interface. The interface is where a DB would slot in if this scaled.

## Data shape assumed

Order fields: `Email`, `OrderNumber`, `Status`, `TrackingNumber`,
`ProductsOrdered`. Product fields: `ProductName`, `Description`, `Tags`
(+ whatever else the catalogue carries). Adjust `stores.py` if the real
appendix field names differ.
