"""Offline verification for the deterministic Trade Plan core.

Run from repo root:
    python tests/verify_trade_plan.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import trade_plan  # noqa: E402


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


def rnd():
    return {
        "prices": [70, 85, 100, 115, 130],
        "probs": [0.10, 0.20, 0.30, 0.25, 0.15],
    }


def base_panel(**overrides):
    panel = [
        {
            "channel": "cashflow",
            "horizon": "5y",
            "status": "ok",
            "signal": "undervalued",
            "metrics": {
                "anchor_price": 100.0,
                "baseline": 125.0,
                "xray": {
                    "base_rate": {"live": {"percentile": 80}},
                    "scenario_probs": {"by_name": {"bear": 0.1, "base": 0.2, "bull": 0.5, "moonshot": 0.2}, "note": "ok"},
                    "wwhtbt": [{"kind": "kill", "value": "revenue growth breaks below base-rate median for 2 quarters"}],
                },
            },
        },
        {
            "channel": "distribution",
            "horizon": "1-3m",
            "status": "ok",
            "signal": "neutral",
            "metrics": {"spot": 100.0, "rnd": rnd()},
        },
        {
            "channel": "revision",
            "horizon": "quarterly",
            "status": "ok",
            "metrics": {
                "est_rev_90d": 0.08,
                "price_rev_90d": 0.12,
                "data_source": {"point_in_time": True},
            },
        },
        {
            "channel": "altitude",
            "horizon": "mixed",
            "status": "ok",
            "metrics": {"decomposition": {"company_pct": 0.60}},
        },
    ]
    for channel, patch in overrides.items():
        for item in panel:
            if item["channel"] == channel:
                original_metrics = item.get("metrics", {})
                item.update(patch)
                if "metrics" in patch:
                    merged = {**original_metrics, **patch["metrics"]}
                    item["metrics"] = merged
    return panel


print("\n=== reconciliation ===")
indexed = trade_plan.panel_by_channel(base_panel())
check("panel_by_channel indexes the panel list",
      set(indexed) == {"cashflow", "distribution", "revision", "altitude"})
check("signal extraction reads explicit and derived signals",
      trade_plan.extract_signals(indexed)["revision"] == "improving")

cold = base_panel(
    distribution={"status": "honest_empty", "signal": "unknown", "metrics": {"rnd": None}},
    revision={"metrics": {"est_rev_90d": 0.08, "price_rev_90d": 0.12, "data_source": {"point_in_time": False}}},
    altitude={"metrics": {"decomposition": {}}},
)
rec = trade_plan.build_reconciliation(cold)
ci = rec["conviction_input"]
check("cold-start consensus check is skipped, not aligned",
      any(c["check"] == "price_vs_fundamentals" and c["verdict"] == "skipped" for c in rec["checks"]),
      str(rec["checks"]))
check("multiple cold-start skips cap conviction below high",
      trade_plan.compute_conviction(rec, cold) == "medium",
      f"ci={ci} conviction={trade_plan.compute_conviction(rec, cold)}")

contradiction_panel = base_panel(
    cashflow={"signal": "overvalued"},
    distribution={"signal": "euphoric"},
)
rec2 = trade_plan.build_reconciliation(contradiction_panel)
check("overvalued plus euphoric options is contradiction",
      rec2["conviction_input"]["contradiction_count"] == 1,
      str(rec2["checks"]))
check("any contradiction forces low conviction",
      trade_plan.compute_conviction(rec2, contradiction_panel) == "low")
check("MVP short signal downgrades to trim when position exists",
      trade_plan.determine_stance(rec2, "medium", "euphoric", "improving", "overvalued", True) == "trim")
check("MVP short signal downgrades to avoid when no position exists",
      trade_plan.determine_stance(rec2, "medium", "euphoric", "improving", "overvalued", False) == "avoid")

print("\n=== p_win / base rate ===")
xray_100 = {"base_rate": {"live": {"percentile": 80}}, "scenario_probs": {"by_name": {"bull": 0.4, "moonshot": 0.2}, "note": "ok"}}
xray_01 = {"base_rate": {"live": {"percentile": 0.80}}, "scenario_probs": {"by_name": {"bull": 0.4, "moonshot": 0.2}, "note": "ok"}}
check("base-rate percentile 0-100 converts to 0-1",
      trade_plan.extract_base_rate_pct01(xray_100) == 0.8)
check("base-rate percentile already 0-1 remains 0-1",
      trade_plan.extract_base_rate_pct01(xray_01) == 0.8)
above_top = {"scenario_probs": {"note": "above_top", "by_name": {"bull": 0.8, "moonshot": 0.2}}}
check("above_top forces p_win to zero",
      trade_plan.your_p_win(above_top) == 0.0)

print("\n=== trade plan ===")
missing_rnd_panel = base_panel(distribution={"metrics": {"rnd": None}})
plan = trade_plan.build_trade_plan(missing_rnd_panel, {"has_position": False})
check("missing RND degrades sizing honestly",
      plan and plan["size"]["status"] == "degraded_no_rnd" and plan["edge"]["status"] == "degraded_no_rnd",
      str(plan and {"size": plan["size"], "edge": plan["edge"]}))
check("trade plan carries self-falsification from WWHTBT KILL",
      plan and "revenue growth breaks" in plan["self_falsification"])

low_edge_panel = base_panel(
    cashflow={
        "signal": "overvalued",
        "metrics": {
            "xray": {
                "base_rate": {"live": {"percentile": 95}},
                "scenario_probs": {"by_name": {"bull": 0.01, "moonshot": 0.0}, "note": "ok"},
                "wwhtbt": [{"kind": "kill", "value": "growth thesis broken"}],
            }
        },
    },
    distribution={"signal": "euphoric", "metrics": {"rnd": rnd()}},
)
plan2 = trade_plan.build_trade_plan(low_edge_panel, {"has_position": True})
check("low conviction plus edge under 2pct gates target weight to zero",
      plan2 and plan2["conviction"] == "low" and plan2["stance"] == "avoid" and plan2["size"]["target_weight"] == 0.0,
      str(plan2 and {"conviction": plan2["conviction"], "stance": plan2["stance"], "size": plan2["size"], "edge": plan2["edge"]}))

print("\n" + "=" * 72)
print(f"RESULT: {_passed} passed, {_failed} failed")
print("=" * 72)
raise SystemExit(1 if _failed else 0)
