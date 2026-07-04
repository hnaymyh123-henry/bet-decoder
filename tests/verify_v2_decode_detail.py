"""Verify new decode outputs carry the minimal v4 decode_detail contract.

This is intentionally offline and deterministic. It checks the fresh decoder
output itself, not the db.py read-time v3->v4 compatibility shim.
"""
from __future__ import annotations

import os

os.environ["MIROMIND_API_KEY"] = ""
os.environ["OFFLINE_MODE"] = "1"

import client
import decoder
import orchestrator
from decoder import Fundamentals, decode_bet

_passed = 0
_failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
    extra = f"  | {detail}" if detail else ""
    print(f"[{'PASS' if cond else 'FAIL'}] {name}{extra}")


FIX = {
    "COST": Fundamentals(
        ticker="COST", current_price=900.0, revenue_ttm=255e9, net_income_ttm=7.4e9,
        ebitda_ttm=11e9, fcf_ttm=6e9, book_equity=23e9, eps_ttm=16.6,
        shares_outstanding=0.443e9, net_debt=-5e9, beta=0.8, growth_rate=0.09,
        industry="Discount Stores",
    ),
    "NVDA": Fundamentals(
        ticker="NVDA", current_price=180.0, revenue_ttm=96e9, net_income_ttm=53e9,
        ebitda_ttm=61e9, fcf_ttm=39e9, book_equity=65e9, eps_ttm=2.95,
        shares_outstanding=24.6e9, net_debt=-20e9, beta=1.7, growth_rate=0.28,
        industry="Semiconductors",
    ),
    "WMT": Fundamentals(
        ticker="WMT", current_price=95.0, revenue_ttm=665e9, net_income_ttm=19e9,
        ebitda_ttm=40e9, fcf_ttm=12e9, book_equity=90e9, eps_ttm=2.4,
        shares_outstanding=8.0e9, net_debt=20e9, beta=0.6, growth_rate=0.05,
        industry="Discount Stores",
    ),
}


def fundamentals(ticker: str) -> Fundamentals:
    return FIX[ticker.upper()]


def detail(card) -> dict:
    return getattr(card, "decode_detail", {}) or {}


def assert_v4_shape(label: str, card, *, expect_agentic: bool | None = None) -> dict:
    dd = detail(card)
    panel = dd.get("panel")
    cashflow = panel[0] if isinstance(panel, list) and panel else {}
    reconciliation = dd.get("reconciliation") or {}
    decision = dd.get("decision") or {}
    lineage = dd.get("lineage") or {}
    series_link = dd.get("series_link") or {}

    check(f"{label}: panel is a list", isinstance(panel, list), type(panel).__name__)
    check(f"{label}: no legacy buckets", "buckets" not in dd)
    check(f"{label}: cashflow observation exists",
          cashflow.get("channel") == "cashflow" and cashflow.get("as_of")
          and isinstance(cashflow.get("metrics"), dict),
          f"cashflow={cashflow}")
    check(f"{label}: cashflow observation has v4 signal/status fields",
          cashflow.get("status") in ("ok", "honest_empty")
          and cashflow.get("signal") in ("unknown", "fair", "overvalued", "undervalued", "no_solution")
          and isinstance(cashflow.get("evidence_refs"), list)
          and "cost_credits" in cashflow,
          f"status={cashflow.get('status')} signal={cashflow.get('signal')}")
    check(f"{label}: reconciliation checks[] contract",
          isinstance(reconciliation.get("checks"), list)
          and isinstance(reconciliation.get("term_structure"), dict)
          and isinstance(reconciliation.get("conviction_input"), dict),
          f"keys={sorted(reconciliation.keys())}")
    check(f"{label}: decision exists and is honest when degraded",
          isinstance(decision, dict)
          and decision.get("stance") in ("avoid", "trim", "hold", "accumulate", "strong_buy")
          and isinstance(decision.get("size"), dict)
          and (decision.get("degraded") is True or decision.get("size", {}).get("status")),
          f"decision={decision}")
    check(f"{label}: lineage mirrors BetCard fields",
          lineage.get("derived_from") == card.derived_from
          and lineage.get("derivation_kind") == card.derivation_kind
          and lineage.get("derivation") == card.derivation,
          f"lineage={lineage}")
    check(f"{label}: series_link is explicit",
          series_link.get("series_key") == card.series_key
          and series_link.get("prev_card_id") is None
          and "drift_since_prev" in series_link,
          f"series_link={series_link}")
    if expect_agentic is not None:
        check(f"{label}: agentic flag as expected",
              dd.get("agentic") is expect_agentic if expect_agentic else dd.get("agentic") is not True,
              f"agentic={dd.get('agentic')}")
    return dd


print("=" * 72)
print("V2 decode_detail contract verification")
print("=" * 72)

print("\n=== deterministic traditional decode ===")
cost = decode_bet("market", "COST", "zh", fundamentals_fn=fundamentals)
cost_dd = assert_v4_shape("traditional", cost)
check("traditional keeps v3 primary_lens",
      isinstance(cost_dd.get("primary_lens"), dict), f"keys={sorted(cost_dd.keys())}")
check("traditional decision is not pretending RND is present",
      str((cost_dd.get("decision") or {}).get("size", {}).get("status", "")).startswith("degraded_"),
      f"size={(cost_dd.get('decision') or {}).get('size')}")

print("\n=== anchor decode ===")
nvda = decode_bet("market", "NVDA", "zh", fundamentals_fn=fundamentals)
nvda_dd = assert_v4_shape("anchor", nvda)
check("anchor keeps v3 anchor_mode",
      isinstance(nvda_dd.get("anchor_mode"), dict), f"mode={nvda_dd.get('mode')}")
check("anchor cashflow signal reflects high premium or undervaluation honestly",
      nvda_dd["panel"][0].get("signal") in ("overvalued", "undervalued", "fair", "unknown"),
      f"signal={nvda_dd['panel'][0].get('signal')}")

print("\n=== agentic fallback decode ===")
old_impl = client._CHAT_TOOLS_IMPL
client._CHAT_TOOLS_IMPL = None
try:
    fb = orchestrator.decode_bet_agentic("market", "WMT", "zh", fundamentals_fn=fundamentals)
finally:
    client._CHAT_TOOLS_IMPL = old_impl
fb_dd = assert_v4_shape("agentic fallback", fb, expect_agentic=False)
check("agentic fallback keeps deterministic mode",
      not str(fb_dd.get("mode", "")).startswith("agentic_"), f"mode={fb_dd.get('mode')}")

print("\n" + "=" * 72)
print(f"RESULT: {_passed} passed, {_failed} failed")
print("=" * 72)
raise SystemExit(1 if _failed else 0)
