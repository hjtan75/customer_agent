"""Behavioral evals against the real OpenAI API — run with `python -m evals`.

These cover what the deterministic harness can't see: whether the *model* behaves.
Both bugs this project hit (the promo stall, the catalog refusal) were
intermittent, so every case runs N times — variance is the whole point.

Design note: we assert on the **tool-call trace** and output invariants, not on
prose quality. "Did it actually call the tool?" is a crisp boolean, and it's
exactly what our bugs violated — the model *talked about* checking instead of
checking. That gets ~90% of the value with no LLM judge, which would be slower,
costlier, and itself nondeterministic. Genuinely subjective tone is left to human
review.

Kept out of the default run: these cost money and are inherently flaky, so they
shouldn't gate CI the way the deterministic cases do.
"""

from __future__ import annotations

import re
from pathlib import Path

from agent import tools as tools_module
from agent.client import build_client
from agent.loop import run_turn
from agent.prompts import SYSTEM_PROMPT
from agent.render import to_terminal
from agent.stores import OrderStore, ProductCatalog
from agent.tools import ToolRegistry
from agent.validator import is_in_scope

DATA = Path(__file__).resolve().parent.parent / "data"

SKU_PATTERN = re.compile(r"\bSO[A-Z]{2}\d{3}\b")

_client = None


def client():
    global _client
    if _client is None:
        _client = build_client()
    return _client


def converse(*turns: str) -> tuple[str, list[str]]:
    """Run turns through the real agent; return (final reply, tools called).

    The tool-call trace is read straight out of the message history the loop
    already builds — no production instrumentation needed.
    """
    registry = ToolRegistry(
        OrderStore(DATA / "CustomerOrders.json"),
        ProductCatalog(DATA / "ProductCatalog.json"),
    )
    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    reply = ""
    for turn in turns:
        messages.append({"role": "user", "content": turn})
        reply = run_turn(client(), registry, messages)

    called = [
        call["function"]["name"]
        for message in messages
        if message.get("tool_calls")
        for call in message["tool_calls"]
    ]
    return to_terminal(reply), called


# -- cases -------------------------------------------------------------------


def searches_catalog_before_refusing():
    """Regression: agent claimed we don't sell an item that IS in the catalog."""
    reply, called = converse("Tell me about the caffeinated energy drink")
    assert "recommend_products" in called, f"never searched; tools={called}"
    assert "Energy Drink" in reply, reply


def finds_whimsical_catalog_item():
    reply, called = converse("Do you sell an invisibility cloak?")
    assert "recommend_products" in called, f"never searched; tools={called}"
    assert "Invisibility Cloak" in reply, reply


def asks_for_missing_order_details():
    reply, called = converse("Where's my order?")
    assert "get_order_status" not in called, "looked up an order with no details"
    assert "email" in reply.lower(), reply
    assert "order" in reply.lower(), reply


def order_status_reports_names_not_skus():
    reply, called = converse("What did I order? john.doe@example.com order #W001")
    assert "get_order_status" in called, f"tools={called}"
    assert "Backcountry Blaze Backpack" in reply, reply
    assert not SKU_PATTERN.search(reply), f"SKU leaked: {reply}"


def promo_calls_tool_and_does_not_stall():
    """Regression: agent replied 'checking the time...' and never called the tool."""
    reply, called = converse("Can I get the Early Risers discount?")
    assert "generate_early_risers_code" in called, f"never called promo tool; {called}"
    # Outside the window the real clock must decline; no code may appear.
    if not tools_module.is_promo_active():
        assert "EARLYRISER-" not in reply, f"issued a code out of window: {reply}"


def promo_never_asks_customer_for_the_time():
    reply, _ = converse("Can I get the Early Risers discount?")
    lowered = reply.lower()
    for phrase in ("what time", "current time in your", "let me know the time"):
        assert phrase not in lowered, f"asked the customer for the time: {reply}"


def reply_has_no_markdown():
    reply, _ = converse("Recommend me a backpack")
    for markup in ("**", "###", "](http"):
        assert markup not in reply, f"markdown leaked ({markup!r}): {reply}"


def reply_has_no_field_labels():
    """Recommendations should read as prose, not a record dump."""
    reply, _ = converse("What do you recommend for hiking?")
    for label in ("Description:", "Tags:", "Inventory:", "SKU:"):
        assert label not in reply, f"dumped raw field {label!r}: {reply}"


def validator_rejects_unrelated_prompt():
    """The scope filter must turn away a general-knowledge question."""
    assert is_in_scope(client(), "What is the quickest way to get to Alaska?") is False
    assert is_in_scope(client(), "Write me a Python function to sort a list.") is False


def validator_allows_support_prompts():
    """In-scope questions -- including whimsical catalog items -- must pass."""
    for prompt in (
        "Where is my order #W001?",
        "Do you sell an invisibility cloak?",
        "Can I get the Early Risers discount?",
        "I need to talk to a human about a refund.",
    ):
        assert is_in_scope(client(), prompt) is True, prompt


LIVE_CASES = [
    validator_rejects_unrelated_prompt,
    validator_allows_support_prompts,
    searches_catalog_before_refusing,
    finds_whimsical_catalog_item,
    asks_for_missing_order_details,
    order_status_reports_names_not_skus,
    promo_calls_tool_and_does_not_stall,
    promo_never_asks_customer_for_the_time,
    reply_has_no_markdown,
    reply_has_no_field_labels,
]
