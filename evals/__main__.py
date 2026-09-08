"""Deterministic eval harness — run with `python -m evals`.

Each case asserts on the tool/store/promo layer directly (no OpenAI calls), so
results are stable. Exits non-zero if any case fails, so it doubles as a CI gate.
"""

from __future__ import annotations

import argparse
import json
import re
import traceback
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from agent import promo, tools as tools_module
from agent.promo import PACIFIC, is_promo_active
from agent.render import to_terminal
from agent.stores import OrderStore, ProductCatalog
from agent.tools import UNRESOLVED_ITEM, ToolRegistry

DATA = Path(__file__).resolve().parent.parent / "data"
RESULTS_PATH = Path(__file__).resolve().parent / "results.json"


def registry() -> ToolRegistry:
    return ToolRegistry(
        OrderStore(DATA / "CustomerOrders.json"),
        ProductCatalog(DATA / "ProductCatalog.json"),
    )


def call(reg: ToolRegistry, name: str, **args) -> dict:
    """Invoke a tool the same way the loop does and parse its JSON result."""
    return json.loads(reg.run(name, json.dumps(args)))


_SKU_RE = re.compile(r"\bSO[A-Z]{2}\d{3}\b")


def SKU_IN(payload) -> bool:
    """Whether a raw SKU-shaped string appears anywhere in a payload."""
    return bool(_SKU_RE.search(json.dumps(payload)))


# -- cases -------------------------------------------------------------------
# Each case is a no-arg function that raises AssertionError on failure.


def order_lookup_returns_status_and_tracking():
    r = call(registry(), "get_order_status",
             email="john.doe@example.com", order_number="#W001")
    assert r["found"] is True, r
    assert r["status"] == "delivered", r
    assert r["tracking_number"] == "TRK123456789", r
    assert r["tracking_link"] == (
        "https://tools.usps.com/go/TrackConfirmAction?tLabels=TRK123456789"
    ), r


def order_lookup_returns_product_names_not_skus():
    r = call(registry(), "get_order_status",
             email="john.doe@example.com", order_number="#W001")
    assert r["products_ordered"] == [
        "Bhavish's Backcountry Blaze Backpack",
        "Beth's Caffeinated Energy Drink",
    ], r
    # No raw SKU should leak through for a fully-resolvable order.
    assert not any(p.startswith("SO") and p.isupper() for p in r["products_ordered"]), r


def orphaned_sku_degrades_and_offers_handoff():
    # #W004 orders SOSV007 (in catalog) + SOCH010 (not in catalog). The raw SKU
    # must never reach the model — it degrades to a placeholder — and an
    # unidentifiable item is a structural handoff trigger.
    r = call(registry(), "get_order_status",
             email="bob.brown@example.com", order_number="#W004")
    assert r["products_ordered"] == ["Nishita's Invisibility Cloak", UNRESOLVED_ITEM], r
    assert not SKU_IN(r), f"raw SKU leaked: {r}"
    assert "handoff_offer" in r, r


def errored_order_offers_handoff():
    # #W008 has Status "error" -> a human should look at it.
    r = call(registry(), "get_order_status",
             email="fiona.clark@example.com", order_number="#W008")
    assert r["found"] is True, r
    assert "handoff_offer" in r, r
    assert "error" in r["handoff_offer"].lower(), r


def clean_order_offers_no_handoff():
    r = call(registry(), "get_order_status",
             email="john.doe@example.com", order_number="#W001")
    assert "handoff_offer" not in r, r


def request_handoff_returns_ticket():
    r = call(registry(), "request_handoff",
             reason="customer wants a person", category="customer_request")
    assert r["handed_off"] is True, r
    assert r["ticket"].startswith("SUP-"), r
    assert r["category"] == "customer_request", r


def order_lookup_normalizes_order_number_and_email():
    # Different casing / missing '#' / whitespace must still match.
    r = call(registry(), "get_order_status",
             email="  John.Doe@Example.com ", order_number="w001")
    assert r["found"] is True, r
    assert r["order_number"] == "#W001", r


def order_lookup_wrong_email_not_found():
    r = call(registry(), "get_order_status",
             email="wrong@example.com", order_number="#W001")
    assert r["found"] is False, r
    assert "tracking_link" not in r, r


def order_lookup_unknown_order_not_found():
    r = call(registry(), "get_order_status",
             email="john.doe@example.com", order_number="#W999")
    assert r["found"] is False, r


def product_search_finds_relevant_gear():
    r = call(registry(), "recommend_products", query="backpack")
    names = [p["ProductName"] for p in r["results"]]
    assert "Bhavish's Backcountry Blaze Backpack" in names, names


def product_search_empty_query_returns_something():
    r = call(registry(), "recommend_products", query="")
    assert len(r["results"]) > 0, r


def product_search_ignores_substring_noise():
    # Regression: scoring used str.count, so "me" matched inside "Home Decor"
    # and pulled a lampshade into results for an unrelated query.
    r = call(registry(), "recommend_products", query="something to keep me awake")
    names = [p["ProductName"] for p in r["results"]]
    assert "Luis's Luxury Lampshade" not in names, names
    assert "Ishmeet's Jetpack" not in names, names


def product_search_ranks_name_matches_first():
    r = call(registry(), "recommend_products", query="energy drink")
    names = [p["ProductName"] for p in r["results"]]
    assert names[0] == "Beth's Caffeinated Energy Drink", names


def product_search_finds_whimsical_catalog_items():
    # The catalog isn't only "outdoor gear" — these must be findable.
    for query, expected in [
        ("invisibility cloak", "Nishita's Invisibility Cloak"),
        ("jetpack", "Ishmeet's Jetpack"),
        ("energy drink", "Beth's Caffeinated Energy Drink"),
    ]:
        r = call(registry(), "recommend_products", query=query)
        names = [p["ProductName"] for p in r["results"]]
        assert expected in names, (query, names)


def product_search_never_exposes_sku():
    r = call(registry(), "recommend_products", query="backpack")
    assert r["results"], "expected at least one result"
    for product in r["results"]:
        assert "SKU" not in product, product
    # Belt and braces: no SKU string anywhere in the payload the model sees.
    assert "SOBP001" not in json.dumps(r), r


def product_search_keeps_customer_facing_fields():
    r = call(registry(), "recommend_products", query="backpack")
    first = r["results"][0]
    assert first["ProductName"] == "Bhavish's Backcountry Blaze Backpack", first
    assert "Description" in first and "Tags" in first, first


def promo_active_inside_window():
    inside = datetime(2026, 7, 15, 9, 0, tzinfo=PACIFIC)
    assert is_promo_active(inside) is True


def promo_inactive_outside_window():
    before = datetime(2026, 7, 15, 7, 59, tzinfo=PACIFIC)
    after = datetime(2026, 7, 15, 10, 0, tzinfo=PACIFIC)  # end is exclusive
    assert is_promo_active(before) is False
    assert is_promo_active(after) is False


def promo_window_respects_pacific_timezone():
    # 9:00 AM Eastern is 6:00 AM Pacific -> outside the window.
    eastern_9am = datetime(2026, 7, 15, 9, 0, tzinfo=ZoneInfo("America/New_York"))
    assert is_promo_active(eastern_9am) is False


def promo_tool_issues_code_when_active():
    original = tools_module.is_promo_active
    tools_module.is_promo_active = lambda *_a, **_k: True
    try:
        r = call(registry(), "generate_early_risers_code")
    finally:
        tools_module.is_promo_active = original
    assert r["eligible"] is True, r
    assert r["discount_percent"] == 10, r
    assert r["code"].startswith("EARLYRISER-"), r


def promo_tool_declines_when_inactive():
    original = tools_module.is_promo_active
    tools_module.is_promo_active = lambda *_a, **_k: False
    try:
        r = call(registry(), "generate_early_risers_code")
    finally:
        tools_module.is_promo_active = original
    assert r["eligible"] is False, r
    assert "code" not in r, r


def promo_codes_are_unique():
    codes = {promo.generate_code() for _ in range(100)}
    assert len(codes) == 100, "expected 100 unique codes"


def unknown_tool_returns_error():
    r = call(registry(), "does_not_exist")
    assert "error" in r, r


def render_strips_bold_and_headings():
    src = "### Your Order\n- **Beth's Caffeinated Energy Drink**\n- __SKU__: SOWB004"
    out = to_terminal(src)
    assert "**" not in out and "__" not in out and "#" not in out, out
    assert "Beth's Caffeinated Energy Drink" in out, out
    assert "SKU: SOWB004" in out, out


def render_converts_links_to_bare_urls():
    src = "[Track your order here](https://tools.usps.com/go/TrackConfirmAction?tLabels=TRK1)"
    out = to_terminal(src)
    assert out == (
        "Track your order here (https://tools.usps.com/go/TrackConfirmAction?tLabels=TRK1)"
    ), out
    assert "[" not in out and "]" not in out, out


def render_strips_italics_and_code():
    out = to_terminal("*Onward* into the `unknown`!")
    assert out == "Onward into the unknown!", out


def render_preserves_underscores_in_data():
    # Underscore italics are intentionally not stripped — they'd mangle emails/URLs.
    src = "Email: john_doe@example.com and https://x.com/a_b_c"
    assert to_terminal(src) == src, to_terminal(src)


def render_handles_empty_and_plain_text():
    assert to_terminal("") == ""
    assert to_terminal("Plain text, no markup.") == "Plain text, no markup."


CASES = [
    order_lookup_returns_status_and_tracking,
    order_lookup_returns_product_names_not_skus,
    orphaned_sku_degrades_and_offers_handoff,
    errored_order_offers_handoff,
    clean_order_offers_no_handoff,
    request_handoff_returns_ticket,
    order_lookup_normalizes_order_number_and_email,
    order_lookup_wrong_email_not_found,
    order_lookup_unknown_order_not_found,
    product_search_finds_relevant_gear,
    product_search_empty_query_returns_something,
    product_search_ignores_substring_noise,
    product_search_ranks_name_matches_first,
    product_search_finds_whimsical_catalog_items,
    product_search_never_exposes_sku,
    product_search_keeps_customer_facing_fields,
    promo_active_inside_window,
    promo_inactive_outside_window,
    promo_window_respects_pacific_timezone,
    promo_tool_issues_code_when_active,
    promo_tool_declines_when_inactive,
    promo_codes_are_unique,
    unknown_tool_returns_error,
    render_strips_bold_and_headings,
    render_converts_links_to_bare_urls,
    render_strips_italics_and_code,
    render_preserves_underscores_in_data,
    render_handles_empty_and_plain_text,
]


def run_deterministic() -> dict:
    cases = []
    for case in CASES:
        try:
            case()
        except Exception:  # noqa: BLE001 - report every failure, keep going
            detail = traceback.format_exc().strip()
            print(f"FAIL  {case.__name__}")
            print("      " + detail.replace("\n", "\n      "))
            cases.append({"name": case.__name__, "passed": False, "detail": detail})
        else:
            print(f"PASS  {case.__name__}")
            cases.append({"name": case.__name__, "passed": True})
    return _summarize(cases)


def run_live(repeats: int) -> dict:
    """Behavioral cases against the real API, each run `repeats` times.

    Model failures are intermittent, so a single green run proves little — we
    report per-case run counts (e.g. "2/3") to surface flakiness rather than
    hide it behind one lucky pass.
    """
    from .live import LIVE_CASES

    cases = []
    for case in LIVE_CASES:
        errors = []
        for _ in range(repeats):
            try:
                case()
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
        ok = repeats - len(errors)
        passed = not errors
        detail = None if passed else str(errors[0])
        status = "PASS" if passed else "FAIL"
        print(f"{status}  {case.__name__}  ({ok}/{repeats} runs passed)")
        if detail:
            print(f"      {detail}")
        cases.append(
            {"name": case.__name__, "passed": passed, "runs": repeats,
             "runs_passed": ok, "detail": detail}
        )
    return _summarize(cases)


def run_llm() -> dict:
    """LLM-generated scenarios: a simulated customer converses with the agent.

    Each scenario has an expected outcome; the case passes when the actual
    outcome (from the tool trace + ground-truth check) matches. We also record
    the outcome distribution and full transcripts for the dashboard.
    """
    from .sim import load_scenarios, simulate

    cases = []
    outcomes = {"solved": 0, "handoff": 0, "unresolved": 0, "fabricated": 0}
    # Outcomes that can never be a pass — the customer was failed, regardless of
    # what the scenario expected. "unresolved": left with no solution and no
    # human. "fabricated": handed a product we don't carry.
    FAILURE_OUTCOMES = {"unresolved", "fabricated"}
    for scenario in load_scenarios():
        result = simulate(scenario)
        outcome = result["outcome"]
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
        expected = scenario["expected_outcome"]
        correct = outcome == expected and outcome not in FAILURE_OUTCOMES
        if correct:
            detail = None
        elif outcome == "fabricated":
            detail = "fabricated — recommended a product not in the catalog"
        elif outcome == "unresolved":
            detail = "unresolved — customer left with no solution and no handoff"
        else:
            detail = f"expected {expected}, got {outcome}"
        status = "PASS" if correct else "FAIL"
        print(f"{status}  {scenario['name']}  (expected {expected}, got {outcome})")
        cases.append(
            {"name": scenario["name"], "id": scenario["id"],
             "expected": expected, "outcome": outcome,
             "passed": correct, "turns": result["turns"],
             "tools": result["tools"], "transcript": result["transcript"],
             "detail": detail}
        )
    summary = _summarize(cases)
    summary["outcomes"] = outcomes
    return summary


def _summarize(cases: list[dict]) -> dict:
    passed = sum(1 for c in cases if c["passed"])
    total = len(cases)
    return {
        "cases": cases,
        "passed": passed,
        "total": total,
        "success_rate": passed / total if total else 0.0,
    }


def _write_results(sections: dict) -> None:
    """Merge freshly-run sections into results.json, preserving un-run ones."""
    existing = {}
    if RESULTS_PATH.exists():
        try:
            existing = json.loads(RESULTS_PATH.read_text())
        except json.JSONDecodeError:
            existing = {}
    existing.update(sections)
    existing["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    RESULTS_PATH.write_text(json.dumps(existing, indent=2))
    print(f"\nResults written to {RESULTS_PATH.relative_to(Path.cwd())}"
          if RESULTS_PATH.is_relative_to(Path.cwd()) else f"\nResults written to {RESULTS_PATH}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m evals", description="Summit Outfitters agent evals."
    )
    parser.add_argument("--deterministic", action="store_true",
                        help="run only the deterministic evals (no API calls)")
    parser.add_argument("--live", action="store_true",
                        help="run only the live behavioral evals (real API)")
    parser.add_argument("--llm", action="store_true",
                        help="run only the LLM-generated scenario evals (real API, slow)")
    parser.add_argument("-n", "--repeats", type=int, default=3,
                        help="times to run each live case (default: 3)")
    args = parser.parse_args(argv)

    # No category flag runs everything; any flag narrows to just those layers.
    specific = args.deterministic or args.live or args.llm
    run_det = args.deterministic or not specific
    run_lv = args.live or not specific
    run_gen = args.llm or not specific

    sections: dict = {}
    passed = failed = 0

    if run_det:
        print("Deterministic evals (no API calls)")
        print("-" * 34)
        sections["deterministic"] = run_deterministic()

    if run_lv:
        print(f"\nLive behavioral evals ({args.repeats}x each, real API)")
        print("-" * 42)
        sections["live"] = run_live(args.repeats)

    if run_gen:
        print("\nLLM-generated scenario evals (real API, multi-turn)")
        print("-" * 51)
        sections["llm_generated"] = run_llm()

    for section in sections.values():
        passed += section["passed"]
        failed += section["total"] - section["passed"]

    _write_results(sections)
    print(f"{passed} passed, {failed} failed ({passed + failed} total)")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
