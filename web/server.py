"""Flask web UI for the Sierra Outfitters agent, with a stub login.

A thin presentation layer over the same agent core the CLI uses. The browser is
just another I/O channel: this server holds the OpenAI client, the message
history, and the tool registry, and calls run_turn() once per user message --
exactly what chat_loop() does for the terminal. The agent package is untouched.

Authentication is a deliberate stub (any known customer email + password "1234").
What matters is the *authorization* boundary: once a session is signed in, its
ToolRegistry is bound to that email (identity=...), and order tools are stamped
with it server-side. The model can never act on another customer's orders,
regardless of what it's asked. See IMPROVEMENT.md §2.2.

Note the web reply is NOT passed through render.to_terminal(): the terminal
render pass strips markdown because a CLI can't display it, but a browser can, so
the frontend renders the model's markdown natively. Display formatting is a
per-channel boundary concern, not something the agent owns.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from flask import Flask, jsonify, redirect, request, session

from agent.auth import check_login
from agent.client import build_client
from agent.loop import run_turn
from agent.prompts import authenticated_greeting, authenticated_prompt
from agent.stores import OrderStore, ProductCatalog
from agent.tools import ToolRegistry

DATA_DIR = Path(__file__).parent.parent / "data"
EVALS_DIR = Path(__file__).parent.parent / "evals"
HERE = Path(__file__).parent

app = Flask(__name__)
# Signs the session cookie. A dev default is fine here; a real deployment would
# set SECRET_KEY from the environment.
app.secret_key = os.environ.get("SECRET_KEY", "sierra-dev-secret-change-me")

# Stores are read-only and shared across sessions; load once.
client = build_client()
ORDERS = OrderStore(DATA_DIR / "CustomerOrders.json")
CATALOG = ProductCatalog(DATA_DIR / "ProductCatalog.json")

# Per-signed-in-customer state: {email: {"registry": ..., "messages": [...]}}.
# One process, in memory. A real deployment would key this by session id and
# persist it; see IMPROVEMENT.md.
SESSIONS: dict[str, dict] = {}


def _new_state(email: str) -> dict:
    """Fresh chat state for a customer: an identity-bound registry + history."""
    registry = ToolRegistry(ORDERS, CATALOG, identity=email)
    messages = [{"role": "system", "content": authenticated_prompt(email)}]
    return {"registry": registry, "messages": messages}


def _state_for(email: str) -> dict:
    state = SESSIONS.get(email)
    if state is None:
        state = SESSIONS[email] = _new_state(email)
    return state


def _chips_for(new_messages: list[dict]) -> list[str]:
    """Friendly labels for whatever tools the model called this turn.

    The tool trace already lives in the message history -- we just read it back,
    no extra instrumentation (the same trick the live evals use).
    """
    chips: list[str] = []
    for message in new_messages:
        for call in message.get("tool_calls") or []:
            name = call["function"]["name"]
            try:
                args = json.loads(call["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            if name == "get_order_status":
                order = str(args.get("order_number", "")).strip()
                chips.append(f"📦 Looked up order {order}".rstrip())
            elif name == "list_my_orders":
                chips.append("🗂️ Listed your orders")
            elif name == "recommend_products":
                chips.append("🔍 Searched the catalog")
            elif name == "generate_early_risers_code":
                chips.append("🎟️ Checked Early Risers eligibility")
            elif name == "request_handoff":
                chips.append("🤝 Connected you to a teammate")
            else:
                chips.append(f"🛠️ {name}")
    return chips


# -- auth --------------------------------------------------------------------


@app.get("/login")
def login_page():
    if session.get("email"):
        return redirect("/")
    return (HERE / "login.html").read_text()


@app.post("/login")
def login():
    body = request.json or {}
    email = (body.get("email") or "").strip()
    password = body.get("password") or ""
    if not check_login(ORDERS, email, password):
        return jsonify({"ok": False, "error": "Unknown email or wrong password."}), 401
    session["email"] = email
    _state_for(email)
    return jsonify({"ok": True})


@app.post("/logout")
def logout():
    email = session.pop("email", None)
    SESSIONS.pop(email, None)  # drop this customer's chat state
    return jsonify({"ok": True})


# -- app ---------------------------------------------------------------------


@app.get("/")
def index():
    if not session.get("email"):
        return redirect("/login")
    return (HERE / "index.html").read_text()


@app.get("/dashboard")
def dashboard():
    # Open on purpose: an internal view over synthetic eval data, not customer
    # data. It renders the last `python -m evals` run — a snapshot, not live.
    return (HERE / "dashboard.html").read_text()


@app.get("/dashboard/data")
def dashboard_data():
    results = EVALS_DIR / "results.json"
    if not results.exists():
        return jsonify({"missing": True})
    return jsonify(json.loads(results.read_text()))


@app.get("/greeting")
def greeting():
    email = session.get("email")
    if not email:
        return jsonify({"error": "unauthorized"}), 401
    return jsonify({"greeting": authenticated_greeting(email), "email": email})


@app.post("/chat")
def chat():
    email = session.get("email")
    if not email:
        return jsonify({"error": "unauthorized"}), 401

    user_input = ((request.json or {}).get("message") or "").strip()
    if not user_input:
        return jsonify({"reply": "", "tools": []})

    state = _state_for(email)
    messages = state["messages"]
    messages.append({"role": "user", "content": user_input})
    first_new = len(messages)
    reply = run_turn(client, state["registry"], messages)
    chips = _chips_for(messages[first_new:])
    return jsonify({"reply": reply, "tools": chips})


@app.post("/reset")
def reset():
    email = session.get("email")
    if not email:
        return jsonify({"error": "unauthorized"}), 401
    SESSIONS[email] = _new_state(email)
    return jsonify({"ok": True})
