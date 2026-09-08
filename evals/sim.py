"""LLM-generated scenario evals: a simulated customer converses with the agent.

Unlike the live evals (fixed prompts, PASS/FAIL on one invariant), these are
multi-turn and exploratory. A second model client — same provider/key, its own
system prompt and history — role-plays a customer with a GOAL and reacts to the
agent's replies. We then measure how the conversation ended.

The customer is NOT the grader. Outcomes are labeled from the agent's tool-call
trace (a handoff is a fact, not an opinion) plus, for scenarios expected to be
solved, a structural ground-truth check. No LLM judge decides pass/fail.

Outcome taxonomy per conversation:
  handoff     — the agent called request_handoff (a human was pulled in)
  solved      — the customer's goal was met and no human was needed
  unresolved  — the customer gave up (or ran out of turns) with no handoff
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from agent.client import MODEL, build_client
from agent.loop import run_turn
from agent.prompts import authenticated_prompt
from agent.stores import OrderStore, ProductCatalog
from agent.tools import ToolRegistry

DATA = Path(__file__).resolve().parent.parent / "data"
SCENARIOS_PATH = Path(__file__).resolve().parent / "scenarios.json"


# The simulated customer. Defaults to the same model the agent uses (Groq's
# Llama 3.3 70B); override with LLM_CUSTOMER_MODEL to role-play with a different one.
CUSTOMER_MODEL = os.environ.get("LLM_CUSTOMER_MODEL", MODEL)
MAX_TURNS = 8
GOAL_MET = "[GOAL_MET]"
GIVE_UP = "[GIVING_UP]"

CUSTOMER_SYSTEM = """You are role-playing a CUSTOMER contacting Summit Outfitters \
support by chat. Stay in character as the customer at all times — never act as \
the support agent, and never break character.

Your persona: {persona}
Your goal: {goal}

How to behave:
- Send short, natural customer messages, one turn at a time.
- React to what the agent actually says. You are already signed in, so the agent \
knows your email — don't recite it. If it asks for an order number you have, give it.
- The moment your goal is fully met, reply with exactly {goal_met} on its own \
line (optionally after a brief thanks).
- If you're going in circles, or the agent has told you a human teammate will \
follow up and you're content with that, reply with exactly {give_up} on its own \
line — but only once the conversation has genuinely reached that point.
- Never output both markers. Don't narrate stage directions; just speak."""

_client = None


def client():
    global _client
    if _client is None:
        _client = build_client()
    return _client


def load_scenarios() -> list[dict]:
    return json.loads(SCENARIOS_PATH.read_text())


def _customer_turn(history: list[dict]) -> str:
    """Ask the customer LLM for its next message; append it to its own history."""
    response = client().chat.completions.create(
        model=CUSTOMER_MODEL, messages=history, temperature=0.8
    )
    text = response.choices[0].message.content or ""
    history.append({"role": "assistant", "content": text})
    return text


def _control_signal(message: str) -> str | None:
    if GOAL_MET in message:
        return "goal_met"
    if GIVE_UP in message:
        return "give_up"
    return None


def _clean(message: str) -> str:
    return message.replace(GOAL_MET, "").replace(GIVE_UP, "").strip()


def _tools_called(agent_messages: list[dict]) -> list[str]:
    return [
        call["function"]["name"]
        for message in agent_messages
        if message.get("tool_calls")
        for call in message["tool_calls"]
    ]


# Products the model tends to invent for outdoor queries but that are NOT in the
# Summit catalog. A recommendation that names any of these is fabricating, so it
# can't count as a genuine "solve" no matter how satisfied the customer sounds.
# This is the crude denylist version of the §1.6 "only real products" check; the
# robust version matches product-like nouns in the reply against real ProductNames.
INVENTED_PRODUCTS = (
    "trekking pole", "hiking pole", "walking pole",
    "multi-tool", "multitool", "multi tool",
    "tent", "sleeping bag", "headlamp", "head lamp",
    "water bottle", "hydration pack", "hydration bladder",
    "compass", "first aid kit", "rain jacket", "hiking boots",
    "trail mix", "carabiner", "camp stove", "trail runners",
)


def names_only_real_products(text: str) -> bool:
    """False if the agent's recommendation mentions a product we don't carry."""
    low = text.lower()
    return not any(term in low for term in INVENTED_PRODUCTS)


# Ground-truth checks for scenarios expected to be SOLVED: the customer saying
# "goal met" isn't enough — the agent must have actually delivered. Keyed by id.
SOLVE_CHECKS = {
    "john-parcel": lambda text, tools: "get_order_status" in tools and "TrackConfirmAction" in text,
    "jane-tracking": lambda text, tools: "get_order_status" in tools and "TrackConfirmAction" in text,
    "ethan-hiking-reco": lambda text, tools: "recommend_products" in tools,
    "diana-promo-info": lambda text, tools: "generate_early_risers_code" in tools,
}


def _classify(scenario: dict, tools: list[str], ended: str | None, agent_text: str) -> str:
    if "request_handoff" in tools:
        return "handoff"
    if not names_only_real_products(agent_text):
        return "fabricated"
    solved = ended == "goal_met"
    check = SOLVE_CHECKS.get(scenario["id"])
    if check is not None:
        solved = solved and check(agent_text, tools)
    return "solved" if solved else "unresolved"


def simulate(scenario: dict) -> dict:
    """Run one simulated conversation and classify how it ended."""
    email = scenario["identity"]
    registry = ToolRegistry(
        OrderStore(DATA / "CustomerOrders.json"),
        ProductCatalog(DATA / "ProductCatalog.json"),
        identity=email,
    )
    agent_messages: list[dict] = [
        {"role": "system", "content": authenticated_prompt(email)}
    ]
    customer_history: list[dict] = [
        {
            "role": "system",
            "content": CUSTOMER_SYSTEM.format(
                persona=scenario["persona"],
                goal=scenario["goal"],
                goal_met=GOAL_MET,
                give_up=GIVE_UP,
            ),
        }
    ]

    transcript: list[dict] = []
    ended: str | None = None
    customer_msg = _customer_turn(customer_history)

    for _ in range(MAX_TURNS):
        ended = _control_signal(customer_msg)
        clean = _clean(customer_msg)
        if clean:
            transcript.append({"role": "customer", "text": clean})
        if ended:
            break

        agent_messages.append({"role": "user", "content": clean})
        agent_reply = run_turn(client(), registry, agent_messages)
        transcript.append({"role": "agent", "text": agent_reply})

        customer_history.append({"role": "user", "content": agent_reply})
        customer_msg = _customer_turn(customer_history)

    tools = _tools_called(agent_messages)
    agent_text = "\n".join(t["text"] for t in transcript if t["role"] == "agent")
    outcome = _classify(scenario, tools, ended, agent_text)

    return {
        "outcome": outcome,
        "turns": sum(1 for t in transcript if t["role"] == "customer"),
        "tools": tools,
        "transcript": transcript,
    }
