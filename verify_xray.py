"""verify_xray.py — offline verification of the X-RAY intelligence layer.

Covers base_rates.percentile_of, reverse_dcf.{implied_scenario_probabilities,
implied_cap_years, rank_driver_elasticity, parameterized dcf years},
intelligence.build_xray, and the end-to-end decode → _display['xray'] wiring.

No network, no LLM, no API key (OFFLINE_MODE + empty key) — runs in CI alongside
the other verify_*.py suites. Run: python verify_xray.py
"""
import os

os.environ["MIROMIND_API_KEY"] = ""
os.environ["OFFLINE_MODE"] = "1"
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

import base_rates  # noqa: E402
import db  # noqa: E402
import decoder  # noqa: E402
import intelligence  # noqa: E402
import reverse_dcf as rdcf  # noqa: E402

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


def _data(price=150.0):
    return rdcf.CompanyData(ticker="STUB", current_price=price, revenue_ttm=100e9,
                            fcf_ttm=22e9, shares_outstanding=10e9, net_debt=5e9, beta=1.1)


# ---------------------------------------------------------------------------
print("\n=== base_rates ===")
summ = base_rates.distribution_summary()
check("AC1 universe loaded (n>0)", summ.get("n", 0) > 0, f"n={summ.get('n')}")
extreme = base_rates.percentile_of(0.46, 250e9)
check("AC2 extreme implied CAGR → top-tail", extreme and extreme["verdict"] == "top-tail",
      f"verdict={extreme and extreme['verdict']} pct={extreme and extreme['live']['percentile']}")
check("AC3 extreme carries the verified Mauboussin quote",
      bool(extreme and extreme["authoritative"].get("verified_quote")))
mild = base_rates.percentile_of(0.08, 50e9)
check("AC4 mild implied CAGR → not top-tail", mild and mild["verdict"] != "top-tail",
      f"verdict={mild and mild['verdict']}")
check("AC5 None implied → None", base_rates.percentile_of(None, 1e9) is None)

# ---------------------------------------------------------------------------
print("\n=== reverse_dcf.implied_scenario_probabilities ===")
vals = [40.0, 70.0, 130.0, 250.0]
probs, note = rdcf.implied_scenario_probabilities(vals, 100.0)
check("AC6 inside envelope → weights returned", probs is not None and note == "ok")
check("AC7 weights sum to 1", probs and abs(sum(probs) - 1.0) < 1e-6, f"sum={probs and sum(probs):.6f}")
mean = sum(p * v for p, v in zip(probs or [], vals))
check("AC8 weighted mean ≈ price (mean-matching)", abs(mean - 100.0) < 1e-3, f"mean={mean:.4f}")
p_lo, _ = rdcf.implied_scenario_probabilities(vals, 60.0)
p_hi, _ = rdcf.implied_scenario_probabilities(vals, 200.0)
check("AC9 higher price → more weight on top scenario", p_hi[-1] > p_lo[-1],
      f"lo={p_lo[-1]:.3f} hi={p_hi[-1]:.3f}")
_, n_above = rdcf.implied_scenario_probabilities(vals, 300.0)
_, n_below = rdcf.implied_scenario_probabilities(vals, 10.0)
check("AC10 price above top → (None, above_top)", n_above == "above_top")
check("AC11 price below bottom → (None, below_bottom)", n_below == "below_bottom")

# ---------------------------------------------------------------------------
print("\n=== reverse_dcf.dcf years param + implied_cap_years ===")
a = rdcf.Assumptions(revenue_cagr_5y=0.15, terminal_growth=0.025,
                     terminal_fcf_margin=0.22, wacc=0.09)
d = _data()
check("AC12 years param backward-compatible (default 5)",
      abs(rdcf.dcf_equity_value_per_share(a, d) - rdcf.dcf_equity_value_per_share(a, d, years=5)) < 1e-9)
v3 = rdcf.dcf_equity_value_per_share(a, d, years=3)
v10 = rdcf.dcf_equity_value_per_share(a, d, years=10)
check("AC13 longer horizon → higher value (growth>terminal)", v10 > v3, f"v3={v3:.1f} v10={v10:.1f}")
cap = rdcf.implied_cap_years(_data(price=v10), 0.15, 0.22, 0.09)
check("AC14 implied CAP recovers a sensible horizon", cap is not None and 3 < cap <= 30, f"cap={cap}")
check("AC15 CAP None when sustained ≤ terminal growth",
      rdcf.implied_cap_years(d, 0.01, 0.22, 0.09) is None)

# ---------------------------------------------------------------------------
print("\n=== reverse_dcf.rank_driver_elasticity ===")
elas = rdcf.rank_driver_elasticity(a, d)
check("AC16 ranks all four drivers", len(elas) == 4, f"n={len(elas)}")
check("AC17 sorted by |elasticity| desc",
      all(abs(elas[i]["elasticity"]) >= abs(elas[i + 1]["elasticity"]) for i in range(len(elas) - 1)))
wacc_e = next((e for e in elas if e["driver"] == "wacc"), None)
check("AC18 WACC elasticity is negative (higher WACC → lower value)",
      wacc_e and wacc_e["elasticity"] < 0, f"wacc_elasticity={wacc_e and wacc_e['elasticity']}")

# ---------------------------------------------------------------------------
print("\n=== intelligence.build_xray ===")
x = intelligence.build_xray(data=_data(), fundamentals=None, implied_cagr=0.30,
                            base_margin=0.22, wacc=0.09,
                            implied_rev_5y=100e9 * 1.30 ** 5, implied_market_share=0.4)
for key in ("base_rate", "scenarios", "scenario_probs", "driver_elasticity",
            "load_bearing", "implied_cap", "wwhtbt", "kelly", "headline_zh"):
    check(f"AC19 build_xray has '{key}'", key in x)
check("AC20 four named scenarios", len(x["scenarios"]) == 4)
lb = x["load_bearing"]
check("AC21 load-bearing is an OPERATING driver (not WACC)",
      lb and lb["driver"] in ("revenue_cagr_5y", "terminal_fcf_margin"),
      f"driver={lb and lb['driver']}")
bn = x["scenario_probs"].get("by_name") or {}
check("AC22 scenario probs sum ≈ 1 when solved", (not bn) or abs(sum(bn.values()) - 1.0) < 1e-6)
check("AC23 wwhtbt includes a kill line",
      any(w["kind"] == "kill" for w in x["wwhtbt"]))
# market share > 1 → honest flag, not a fake percent
xf = intelligence.build_xray(data=_data(), fundamentals=None, implied_cagr=0.30,
                             base_margin=0.22, wacc=0.09, implied_market_share=1.24)
check("AC24 implied market share > 100% → honest flag (not a number)",
      any(w["kind"] == "flag" for w in xf["wwhtbt"]))

# ---------------------------------------------------------------------------
print("\n=== end-to-end decode → _display['xray'] ===")
stub = decoder.Fundamentals(
    ticker="STUB", current_price=150.0, revenue_ttm=100e9, net_income_ttm=20e9,
    ebitda_ttm=30e9, fcf_ttm=22e9, book_equity=50e9, eps_ttm=2.0,
    shares_outstanding=10e9, net_debt=5e9, beta=1.1, growth_rate=0.18,
    industry="Software - Application", hist_revenue_cagr=0.20)
card = decoder.decode_bet("market", "STUB", fundamentals_fn=lambda t: stub,
                          hunter=decoder._SKIP_EVIDENCE)
disp = db.build_card_display(card) or {}
check("AC25 decoded card surfaces _display['xray']", bool(disp.get("xray")),
      f"mode={card.decode_detail.get('mode')}")
xx = disp.get("xray") or {}
check("AC26 xray base_rate present on real decode", xx.get("base_rate") is not None)
check("AC27 xray survives decode_detail JSON round-trip",
      isinstance(db._dump_detail(card.decode_detail), str)
      and '"xray"' in db._dump_detail(card.decode_detail))

# ---------------------------------------------------------------------------
print("\n" + "=" * 72)
print(f"RESULT: {_passed} passed, {_failed} failed")
print("=" * 72)
raise SystemExit(1 if _failed else 0)
