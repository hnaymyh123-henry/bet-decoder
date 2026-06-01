"""agent_tools registry verification — deterministic, zero API/network.

Exercises each cheap/pure tool over hardcoded Fundamentals (no yfinance), proves
the what-if reverse-DCF actually MOVES the implied driver, that web-gated tools
honest-empty (never fabricate) when the provider can't search, and that dispatch
never raises on a bad tool name / bad args.

Run:  MIROMIND_API_KEY="" "/c/Users/Henry Ma/miniconda3/python.exe" verify_agent_tools.py
"""
from __future__ import annotations

import json

import agent_tools
import client
import decoder
from agent_tools import ToolContext, dispatch


def _is_json_safe(obj) -> bool:
    """True if obj serializes with NO default= fallback (i.e. dispatch already
    coerced every dataclass/exotic value to a plain JSON type)."""
    try:
        json.dumps(obj)
        return True
    except (TypeError, ValueError):
        return False

_passed = 0
_failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  | {detail}" if detail else ""))


print("=" * 72)
print("agent_tools registry verification")
print("=" * 72)

# COST-like value stock — its reverse DCF SOLVES (implied CAGR in range), so the
# what-if has a number to move.
COST = decoder.Fundamentals(
    ticker="COST", current_price=900.0, revenue_ttm=255e9, net_income_ttm=7.4e9,
    ebitda_ttm=11e9, fcf_ttm=6e9, book_equity=23e9, eps_ttm=16.6,
    shares_outstanding=0.443e9, net_debt=-5e9, beta=0.8, growth_rate=0.09,
    industry="Discount Stores")
AMD = decoder.Fundamentals(
    ticker="AMD", current_price=170.0, revenue_ttm=23e9, net_income_ttm=1.6e9,
    ebitda_ttm=3e9, fcf_ttm=3e9, book_equity=57e9, eps_ttm=1.0,
    shares_outstanding=1.62e9, net_debt=-1e9, beta=1.7, growth_rate=0.3,
    industry="Semiconductors")
ctx = ToolContext(ticker="COST", fundamentals=COST, anchor_price=900.0,
                  fundamentals_fn=lambda t: {"COST": COST, "AMD": AMD}[t.upper()])

# --- AC1: registry + spec ---------------------------------------------------
spec = agent_tools.openai_tools_spec()
check("AC1 openai_tools_spec is well-formed OpenAI function list",
      len(spec) >= 8 and all(s["type"] == "function" and "name" in s["function"]
                             and "parameters" in s["function"] for s in spec),
      f"{len(spec)} tools")
check("AC1 openai_tools_spec(subset) filters by name",
      [s["function"]["name"] for s in agent_tools.openai_tools_spec(["run_lens"])]
      == ["run_lens"])

# --- AC2: cheap tools over the fixture --------------------------------------
gf = dispatch("get_fundamentals", {}, ctx)
check("AC2 get_fundamentals flattens fields + derived predicates",
      gf.get("market_cap") and gf.get("has_revenue") is True
      and gf.get("ticker") == "COST")
check("AC2 classify_subject (COST not an AI composite)",
      dispatch("classify_subject", {}, ctx).get("is_ai_composite") is False)
check("AC2 plan_lenses returns a primary lens",
      dispatch("plan_lenses", {}, ctx).get("primary") in decoder.LENS_REGISTRY)
pe = dispatch("run_lens", {"lens": "pe"}, ctx)
check("AC2 run_lens pe → implied P/E ≈ price/eps",
      pe.get("implied_value") and abs(pe["implied_value"] - 900.0 / 16.6) < 1.0,
      f"implied_pe={pe.get('implied_value')}")
allr = dispatch("run_all_applicable_lenses", {}, ctx)
check("AC2 run_all_applicable_lenses returns multiple results",
      allr.get("count", 0) >= 2, f"count={allr.get('count')}")
anc = dispatch("run_anchor_decompose", {}, ctx)
check("AC2 run_anchor_decompose returns base business value + components",
      isinstance(anc, dict) and "base_business_value" in anc and "components" in anc)

# --- AC3: what-if reverse DCF MOVES the implied driver ----------------------
base = dispatch("whatif_reverse_dcf", {"solve_for": "revenue_cagr_5y", "overrides": {}}, ctx)
hi = dispatch("whatif_reverse_dcf",
              {"solve_for": "revenue_cagr_5y", "overrides": {"wacc": 0.09}}, ctx)
bv, sv = base.get("baseline_implied_value"), hi.get("scenario_implied_value")
check("AC3 whatif solves AND a higher WACC raises the implied CAGR needed",
      isinstance(bv, float) and isinstance(sv, float) and sv > bv,
      f"baseline={bv:.4f} wacc0.09={sv:.4f}" if (bv and sv) else f"bv={bv} sv={sv}")
check("AC3 whatif refuses an unknown solve_for (no crash)",
      dispatch("whatif_reverse_dcf", {"solve_for": "bogus"}, ctx).get("error"))

# --- AC4: compare_subjects (injected fundamentals_fn, no network) -----------
cmp = dispatch("compare_subjects", {"ticker_b": "AMD"}, ctx)
check("AC4 compare_subjects returns both subjects' primary metrics",
      cmp.get("ticker_a") == "COST" and cmp.get("ticker_b") == "AMD"
      and cmp.get("a_primary") is not None and cmp.get("b_primary") is not None)

# --- AC5: web-gated tools honest-empty when provider can't search -----------
_orig = client.WEB_SEARCH_CAPABLE
try:
    client.WEB_SEARCH_CAPABLE = False
    rn = dispatch("research_narrative", {}, ctx)
    ge = dispatch("gather_evidence", {"assumption_text": "AI demand persists"}, ctx)
    check("AC5 research_narrative honest-empty (no fabrication) when web-incapable",
          rn.get("web_grounded") is False and rn.get("coverage") == "unavailable"
          and "full" not in rn)
    check("AC5 gather_evidence honest-empty when web-incapable (no hunter called)",
          ge.get("web_grounded") is False and ge.get("coverage") == "unavailable")
finally:
    client.WEB_SEARCH_CAPABLE = _orig

# --- AC6: dispatch never raises on bad input --------------------------------
check("AC6 unknown tool → {error, available}, no raise",
      dispatch("does_not_exist", {}, ctx).get("error")
      and isinstance(dispatch("does_not_exist", {}, ctx).get("available"), list))
check("AC6 run_lens with missing/bogus lens → error, no raise",
      dispatch("run_lens", {}, ctx).get("error")
      and dispatch("run_lens", {"lens": "zzz"}, ctx).get("error"))
check("AC6 dispatch result is always JSON-safe (no dataclasses leak through)",
      _is_json_safe(dispatch("run_anchor_decompose", {}, ctx)))

print("=" * 72)
print(f"RESULT: {_passed} passed, {_failed} failed")
print("=" * 72)
raise SystemExit(1 if _failed else 0)
