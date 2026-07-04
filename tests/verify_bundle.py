"""verify_bundle.py — offline verification of the ONE-CALL research bundle.

Covers research_bundle.split_bundle (distribute one envelope → evidence section +
narrative envelope), the offline/no-key honest-empty guard, the decoder
_use_bundle gate (must NOT fire for stub/offline so verify stays per-call), and an
end-to-end decode that forces the bundle path via an injected bundle_researcher.

No network, no LLM, no key. Run: python verify_bundle.py
"""
import json
import os

os.environ["MIROMIND_API_KEY"] = ""
os.environ["OFFLINE_MODE"] = "1"
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

import db  # noqa: E402
import decoder  # noqa: E402
import research_bundle as rb  # noqa: E402

_passed = 0
_failed = 0


def check(name, cond, info=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"[PASS] {name}" + (f"  | {info}" if info else ""))
    else:
        _failed += 1
        print(f"[FAIL] {name}" + (f"  | {info}" if info else ""))


# A canned ONE-CALL bundle: per-assumption evidence + full market narrative.
BUNDLE = {
    "subject": "NVDA",
    "evidence": [
        {"assumption_id": "NVDA_dcf", "assumption_text": "隐含 5y 营收 CAGR ≈ 45%",
         "evidence_items": [
             {"stance": "support", "title": "Datacenter demand strong", "url": "https://www.reuters.com/x",
              "date": "2026-05-20", "publisher": "Reuters", "body_md": "..."},
             {"stance": "refute", "title": "ASIC substitution risk", "url": "https://www.bloomberg.com/y",
              "date": "2026-05-22", "publisher": "Bloomberg", "body_md": "..."}],
         "overall_balance": "lean_support"},
        {"assumption_id": "NVDA_pe", "assumption_text": "隐含 P/E",
         "evidence_items": [
             {"stance": "neutral", "title": "Valuation rich vs history", "url": "https://www.ft.com/z",
              "date": "2026-05-18", "publisher": "FT", "body_md": "..."}],
         "overall_balance": "balanced"},
    ],
    "sentiment_regime": {"label": "optimistic", "rationale": "AI capex still rising",
                         "sources": [{"url": "https://www.cnbc.com/a", "title": "t", "date": "2026-05-19", "publisher": "CNBC"}]},
    "bull_case": [{"claim": "Hyperscaler capex keeps rising", "body_md": "...", "proponents": "GS",
                   "sources": [{"url": "https://www.bloomberg.com/b", "title": "t", "date": "2026-05-20", "publisher": "Bloomberg"}]}],
    "bear_case": [{"claim": "Custom ASIC erodes share", "body_md": "...", "proponents": "Citron",
                   "sources": [{"url": "https://www.reuters.com/c", "title": "t", "date": "2026-05-21", "publisher": "Reuters"}]}],
    "contested_axis": [{"axis": "hyperscaler capex durability", "why_it_matters": "..."}],
    "catalysts": [{"event": "Q2 earnings", "date": "2026-08", "why_it_matters": "...", "sources": []}],
    "assumption_bindings": [
        {"assumption_text": "隐含 5y 营收 CAGR ≈ 45%", "implied_value": "45%",
         "supported_by": "capex bull", "threatened_by": "ASIC bear",
         "where_price_leans": "lean_bull", "note": "薄安全垫"}],
    "headline": "市场为 NVDA 押 45% 增速,情绪偏乐观",
    "coverage": "rich",
}


def _stub_researcher(prompt):
    return {"content": json.dumps(BUNDLE, ensure_ascii=False), "cost_usd": 2.5,
            "usage": {"total_tokens": 1000}, "tool_call_count": 4,
            "search_results": [1, 2, 3]}


# ---------------------------------------------------------------------------
print("\n=== split_bundle: one envelope → evidence section + narrative envelope ===")
assumptions = [{"id": "NVDA_dcf", "metric": "dcf", "human_text": "隐含 5y 营收 CAGR ≈ 45%"},
               {"id": "NVDA_pe", "metric": "pe", "human_text": "隐含 P/E"},
               {"id": "NVDA_p_fcf", "metric": "p_fcf", "human_text": "隐含 P/FCF"}]
env = {"subject": "NVDA", "content": json.dumps(BUNDLE, ensure_ascii=False),
       "coverage": "raw", "_meta": {"cost_usd": 2.5}}
ev_section, narr_env = rb.split_bundle(env, assumptions, ticker="NVDA")
check("AC1 evidence section has one brief per assumption", len(ev_section["briefs"]) == 3,
      f"briefs={len(ev_section['briefs'])}")
check("AC2 matched assumptions → found briefs", ev_section["found_count"] == 2,
      f"found={ev_section['found_count']}")
check("AC3 unmatched assumption (p_fcf) → honest-empty brief",
      any(b.get("status") != "found" for b in ev_section["briefs"]))
check("AC4 evidence section flagged via_bundle + records ONE call",
      ev_section.get("via_bundle") and ev_section["new_hunter_calls"] == 1)
check("AC5 bundle cost flows into evidence cost",
      ev_section["cost"]["actual_new_call_usd"] == 2.5, ev_section["cost"])
import narrative  # noqa: E402
ncard = narrative.build_card_narrative(narr_env)
check("AC6 narrative envelope rebuilds into a rich card narrative",
      ncard.get("coverage") not in (None, "unavailable", "unparseable") and ncard.get("full"),
      f"coverage={ncard.get('coverage')}")
check("AC7 narrative carries bull/bear + regime + bindings",
      len((ncard["full"] or {}).get("bull_case") or []) >= 1
      and (ncard["full"] or {}).get("sentiment_regime"))
check("AC8 source tiering still applied (B-tier press recognized)",
      ((ncard["full"] or {}).get("source_quality") or {}).get("by_tier", {}).get("B", 0) >= 1,
      ((ncard["full"] or {}).get("source_quality") or {}).get("by_tier"))

# ---------------------------------------------------------------------------
print("\n=== honest-empty on unavailable bundle ===")
ev2, nv2 = rb.split_bundle({"content": "", "coverage": "unavailable"}, assumptions, ticker="NVDA")
check("AC9 unavailable bundle → empty briefs (no fabrication)",
      ev2["found_count"] == 0 and len(ev2["briefs"]) == 3)
check("AC10 unavailable bundle → narrative unavailable", nv2.get("coverage") == "unavailable")

# ---------------------------------------------------------------------------
print("\n=== research_bundle offline/no-key guard (zero spend) ===")
envx, hit = rb.research_bundle("TESTZ", current_price=200.0, implied_assumptions="- x",
                              lang="zh", conn=None)  # OFFLINE_MODE=1 → None
check("AC11 offline research_bundle → unavailable, no network/spend",
      envx.get("coverage") == "unavailable")

# ---------------------------------------------------------------------------
print("\n=== decoder._use_bundle gate (must NOT fire for stub/offline) ===")
check("AC12 _use_bundle False when hunter injected", decoder._use_bundle(lambda *a, **k: None, None) is False)
check("AC13 _use_bundle False offline (OFFLINE_MODE=1 here)", decoder._use_bundle(None, None) is False)

# ---------------------------------------------------------------------------
print("\n=== end-to-end decode forced onto the bundle path (injected researcher) ===")
rb.reset_memory_cache()   # ensure the injected stub is actually exercised (no stale cache)
stub = decoder.Fundamentals(
    ticker="NVDA", current_price=216.0, revenue_ttm=200e9, net_income_ttm=70e9,
    ebitda_ttm=110e9, fcf_ttm=90e9, book_equity=60e9, eps_ttm=3.0,
    shares_outstanding=24e9, net_debt=-5e9, beta=2.0, growth_rate=0.6,
    industry="Semiconductors", hist_revenue_cagr=0.7)
card = decoder.decode_bet("market", "NVDA", fundamentals_fn=lambda t: stub,
                          bundle_researcher=_stub_researcher)
dd = card.decode_detail
ev = dd.get("evidence") or {}
mn = dd.get("market_narrative") or {}
check("AC14 bundle path taken (evidence via_bundle)", ev.get("via_bundle") is True,
      f"via_bundle={ev.get('via_bundle')}")
check("AC15 ONE call recorded, not N", ev.get("new_hunter_calls") == 1)
check("AC16 market narrative populated from the SAME call",
      mn.get("coverage") not in (None, "unavailable", "unparseable"),
      f"coverage={mn.get('coverage')}")
check("AC17 cross-check ran (narrative↔evidence)", isinstance(mn.get("cross_check"), list))
check("AC18 card still round-trips through _display", bool(db.build_card_display(card)))

# ---------------------------------------------------------------------------
print("\n" + "=" * 72)
print(f"RESULT: {_passed} passed, {_failed} failed")
print("=" * 72)
raise SystemExit(1 if _failed else 0)
