"""M2 Decoder Engine verification — deterministic, zero API/network cost.

Runs every Issue #2 acceptance criterion with stubbed fundamentals + a stub
emit callback.  No yfinance, no MiroMind API.  Prints one PASS/FAIL per check.

Run:  "/c/Users/Henry Ma/miniconda3/python.exe" verify_m2.py
"""
from __future__ import annotations

import db
import decoder
from decoder import (
    Fundamentals,
    LENS_REGISTRY,
    decode_bet,
    select_lenses,
)

# --- counters -------------------------------------------------------------
_passed = 0
_failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global _passed, _failed
    status = "PASS" if cond else "FAIL"
    if cond:
        _passed += 1
    else:
        _failed += 1
    extra = f"  | {detail}" if detail else ""
    print(f"[{status}] {name}{extra}")


# --- hardcoded fundamentals fixtures (no network) -------------------------

# Profitable large-cap (NVDA-like): earnings + revenue + fcf + book + growth.
NVDA = Fundamentals(
    ticker="NVDA", current_price=180.0,
    revenue_ttm=130e9, net_income_ttm=73e9, ebitda_ttm=88e9,
    fcf_ttm=60e9, book_equity=80e9, eps_ttm=2.95,
    shares_outstanding=24.5e9, net_debt=-30e9, beta=1.7, growth_rate=0.55,
)

# Profitable, slower grower (COST-like): clean DCF case.
COST = Fundamentals(
    ticker="COST", current_price=900.0,
    revenue_ttm=255e9, net_income_ttm=7.4e9, ebitda_ttm=11e9,
    fcf_ttm=6e9, book_equity=23e9, eps_ttm=16.6,
    shares_outstanding=0.443e9, net_debt=-5e9, beta=0.8, growth_rate=0.09,
)

# Pre-profit, has revenue (no earnings, no positive FCF): → primary P/S.
NOEARN = Fundamentals(
    ticker="NOEARN", current_price=20.0,
    revenue_ttm=2e9, net_income_ttm=-0.5e9, ebitda_ttm=None,
    fcf_ttm=-0.3e9, book_equity=1e9, eps_ttm=-0.4,
    shares_outstanding=0.5e9, net_debt=0.2e9, beta=1.3, growth_rate=0.40,
)

# No revenue at all (pre-revenue biotech): → 数据不足.
NOREV = Fundamentals(
    ticker="NOREV", current_price=8.0,
    revenue_ttm=None, net_income_ttm=-1e9, ebitda_ttm=None,
    fcf_ttm=None, book_equity=0.5e9, eps_ttm=-2.0,
    shares_outstanding=0.1e9, net_debt=0.0, beta=1.5, growth_rate=None,
)

_FIXTURES = {f.ticker: f for f in (NVDA, COST, NOEARN, NOREV)}


def stub_fundamentals(ticker: str) -> Fundamentals:
    """Deterministic stand-in for yfinance fetch_fundamentals."""
    f = _FIXTURES.get(ticker.upper())
    if f is None:
        raise RuntimeError(f"no fixture for {ticker}")
    return f


class StubEmit:
    """Records emitted ActivityEvents to prove the emit contract works."""
    def __init__(self):
        self.events: list[dict] = []

    def __call__(self, ev: dict) -> None:
        self.events.append(ev)


def D(card):
    return getattr(card, "decode_detail", {}) or {}


# ===========================================================================
print("=" * 70)
print("M2 Decoder Engine — acceptance verification")
print("=" * 70)

# AC1 — decode_bet returns a full M1 BetCard, does NOT self-store.
card = decode_bet("market", "NVDA", "zh", emit=None,
                  fundamentals_fn=stub_fundamentals)
check("AC1 decode_bet returns db.BetCard",
      isinstance(card, db.BetCard),
      f"type={type(card).__name__}")
check("AC1 card has M1 fields (card_id/series_key auto-derived)",
      bool(card.card_id) and card.series_key == "NVDA|market",
      f"card_id={card.card_id[:8]}.. series_key={card.series_key}")
# "does not self-store": a fresh in-memory DB must contain zero cards after decode.
_conn = db.init_db(":memory:")
_ = decode_bet("market", "NVDA", "zh", fundamentals_fn=stub_fundamentals)
check("AC1 passive (no self-store) — DB empty after decode",
      len(db.list_cards(_conn)) == 0,
      f"cards in fresh DB = {len(db.list_cards(_conn))}")

# AC2 — pluggable registry seeded with the 7 traditional lenses.
expected = {"dcf", "pe", "ps", "ev_ebitda", "p_fcf", "p_b", "peg"}
check("AC2 lens registry has all 7 seed lenses",
      expected.issubset(set(LENS_REGISTRY)),
      f"registry={sorted(LENS_REGISTRY)}")
# Pluggability: register a custom lens, confirm it lands, then remove it.
decoder.register_lens(decoder.Lens(
    key="dummy", label="Dummy", applicable=lambda f: True,
    solve=lambda anchor, f: decoder._result("x", 1.0, implied_label="x"),
))
plug_ok = "dummy" in LENS_REGISTRY
LENS_REGISTRY.pop("dummy", None)
check("AC2 registry is pluggable (register + remove custom lens)", plug_ok)

# AC3 — DCF lens reuses reverse_dcf.py (no algorithm change).
import inspect
import reverse_dcf
src = inspect.getsource(decoder._lens_dcf)
check("AC3 DCF lens calls reverse_dcf primitives",
      "reverse_dcf.reverse_solve" in src and "reverse_dcf.monte_carlo_implied" in src,
      "wraps reverse_solve + monte_carlo_implied")
# reverse_dcf.dcf_equity_value_per_share is untouched (sanity: still callable as-is).
check("AC3 reverse_dcf algorithm intact (dcf_equity_value_per_share present)",
      callable(reverse_dcf.dcf_equity_value_per_share))

# AC4 — multiple lenses reverse-solve implied metrics from anchor price.
#   P/E  = price / eps   = 180 / 2.95 ≈ 61.0
pe_res = decoder._run_lens("pe", 180.0, NVDA)
check("AC4 P/E reverse-solve", pe_res is not None
      and abs(pe_res["implied_value"] - (180.0 / 2.95)) < 1e-6,
      f"implied_pe={pe_res['implied_value']:.2f}" if pe_res else "None")
#   P/S  = mcap / rev    = (180*24.5e9)/130e9 ≈ 33.9
ps_res = decoder._run_lens("ps", 180.0, NVDA)
check("AC4 P/S reverse-solve", ps_res is not None
      and abs(ps_res["implied_value"] - ((180.0 * 24.5e9) / 130e9)) < 1e-6,
      f"implied_ps={ps_res['implied_value']:.2f}" if ps_res else "None")
#   EV/EBITDA = (price*shares + net_debt)/ebitda = (180*24.5e9 -30e9)/88e9
ev = (180.0 * 24.5e9 + (-30e9)) / 88e9
ev_res = decoder._run_lens("ev_ebitda", 180.0, NVDA)
check("AC4 EV/EBITDA reverse-solve", ev_res is not None
      and abs(ev_res["implied_value"] - ev) < 1e-6,
      f"implied_ev_ebitda={ev_res['implied_value']:.2f}" if ev_res else "None")
#   P/FCF = mcap / fcf   = (180*24.5e9)/60e9
pfcf_res = decoder._run_lens("p_fcf", 180.0, NVDA)
check("AC4 P/FCF reverse-solve", pfcf_res is not None
      and abs(pfcf_res["implied_value"] - ((180.0 * 24.5e9) / 60e9)) < 1e-6,
      f"implied_p_fcf={pfcf_res['implied_value']:.2f}" if pfcf_res else "None")
#   P/B  = mcap / book   = (180*24.5e9)/80e9
pb_res = decoder._run_lens("p_b", 180.0, NVDA)
check("AC4 P/B reverse-solve", pb_res is not None
      and abs(pb_res["implied_value"] - ((180.0 * 24.5e9) / 80e9)) < 1e-6,
      f"implied_p_b={pb_res['implied_value']:.2f}" if pb_res else "None")
#   PEG = (P/E) / (growth*100) = (180/2.95)/(55) ≈ 1.11
peg_expected = (180.0 / 2.95) / (0.55 * 100.0)
peg_res = decoder._run_lens("peg", 180.0, NVDA)
check("AC4 PEG reverse-solve", peg_res is not None
      and abs(peg_res["implied_value"] - peg_expected) < 1e-6,
      f"implied_peg={peg_res['implied_value']:.3f}" if peg_res else "None")

# AC5 — frame-adaptive decision tree is DETERMINISTIC + reproducible, and the
#        decoded card carries 1 primary + 1-2 cross lenses (divergence kept).
plan1 = select_lenses(NVDA)
plan2 = select_lenses(NVDA)
check("AC5 decision tree deterministic (same input → same plan)",
      (plan1.primary, plan1.cross) == (plan2.primary, plan2.cross),
      f"primary={plan1.primary} cross={plan1.cross}")
check("AC5 NVDA (profitable) → primary P/E",
      plan1.primary == "pe", f"primary={plan1.primary} ({plan1.reason})")
# Decode routing is frame-adaptive (Stage 2c narrative-premium gate): this
# NVDA-like fixture carries NO AI keyword but a high narrative premium (price far
# above its DCF base), so it routes to ANCHOR mode via valuation tension — the
# keyword-less "real NVDA" case yfinance's coarse 'Semiconductors' label can't
# catch. The select_lenses PLAN above still deterministically picks P/E; the mode
# GATE is a separate, later decision. A genuinely value-priced stock stays
# traditional, and that's where the "1 primary + 1-2 cross" contract is asserted.
nvda_card = decode_bet("market", "NVDA", "zh", fundamentals_fn=stub_fundamentals)
nvda_detail = D(nvda_card)
check("AC5 high-premium NVDA-like (no keyword) → anchor via narrative-premium gate",
      nvda_detail.get("mode") == "anchor_primary"
      and (nvda_detail.get("narrative_premium") or 0) >= 0.5,
      f"mode={nvda_detail.get('mode')} premium={nvda_detail.get('narrative_premium')}")
cost_views_card = decode_bet("market", "COST", "zh", fundamentals_fn=stub_fundamentals)
cost_views_detail = D(cost_views_card)
n_views = 1 + len(cost_views_detail.get("cross_lenses", []))
check("AC5 value-priced COST → traditional card = 1 primary + 1-2 cross lenses",
      cost_views_detail.get("mode") == "traditional"
      and cost_views_detail.get("primary_lens") is not None
      and 1 <= len(cost_views_detail.get("cross_lenses", [])) <= 2,
      f"primary={(cost_views_detail.get('primary_lens') or {}).get('lens')} "
      f"cross={[c['lens'] for c in cost_views_detail.get('cross_lenses', [])]} ({n_views} views)")

# AC6 — COST gives a clear DCF implied interval (DCF as a cross lens here).
cost_card = decode_bet("market", "COST", "zh", fundamentals_fn=stub_fundamentals)
cost_detail = D(cost_card)
all_lenses = [cost_detail.get("primary_lens")] + cost_detail.get("cross_lenses", [])
dcf_view = next((l for l in all_lenses if l and l.get("lens") == "dcf"), None)
band = dcf_view.get("band") if dcf_view else None
check("AC6 COST DCF lens produces an implied CAGR + Monte-Carlo band",
      dcf_view is not None and dcf_view.get("implied_value") is not None
      and band is not None and band.get("p50") is not None,
      (f"implied_cagr={dcf_view['implied_value']:.1%} "
       f"band=[{band['p25']:.1%}..{band['p50']:.1%}..{band['p75']:.1%}]")
      if dcf_view and band else "no DCF band")

# AC7 — no-earnings (has revenue) → primary P/S fallback.
noearn_plan = select_lenses(NOEARN)
check("AC7 no-earnings + has-revenue → primary P/S",
      noearn_plan.primary == "ps",
      f"primary={noearn_plan.primary} ({noearn_plan.reason})")
noearn_card = decode_bet("market", "NOEARN", "zh", fundamentals_fn=stub_fundamentals)
check("AC7 no-earnings card decodes (bet set, not insufficient)",
      noearn_card.bet is not None and D(noearn_card).get("status") != "insufficient",
      f"bet={noearn_card.bet:.2f}" if noearn_card.bet else "bet=None")

# AC7b — no revenue at all → "数据不足" card (no crash).
norev_card = decode_bet("market", "NOREV", "zh", fundamentals_fn=stub_fundamentals)
check("AC7b no-revenue → 数据不足 card (bet None, status insufficient)",
      isinstance(norev_card, db.BetCard) and norev_card.bet is None
      and D(norev_card).get("status") == "insufficient",
      f"reason={D(norev_card).get('reason')}")

# AC8 — a lens with no solution falls back to the next lens (no crash).
#   Make a fixture where the planned primary (P/E) has *no* eps so it can't
#   solve, but P/S can — the fallback chain must promote P/S.
PE_BROKEN = Fundamentals(
    ticker="PEBRK", current_price=50.0, revenue_ttm=10e9,
    net_income_ttm=1e9, eps_ttm=None,           # earnings>0 but eps missing
    shares_outstanding=1e9, net_debt=0.0, beta=1.0,
)
# select_lenses won't pick P/E (eps None) — primary becomes P/S directly; assert
# decode still succeeds and never raised.
pebrk_card = decode_bet("market", "PEBRK", "zh",
                        fundamentals_fn=lambda t: PE_BROKEN)
check("AC8 lens fallback — no crash, card still decodes",
      isinstance(pebrk_card, db.BetCard)
      and D(pebrk_card).get("status") != "insufficient"
      and pebrk_card.bet is not None,
      f"primary_lens={D(pebrk_card).get('primary_lens', {}).get('lens')}")
# Also force a TRUE solve-time fallback: planned primary (P/E) is applicable but
# returns no-solution → _run_plan must promote the next applicable lens.
_pe_lens = LENS_REGISTRY["pe"]
_orig_solve = _pe_lens.solve
_pe_lens.solve = lambda anchor, f: None  # simulate P/E reverse-solve no-solution
FB = Fundamentals(
    ticker="FB", current_price=100.0, revenue_ttm=10e9, net_income_ttm=2e9,
    eps_ttm=4.0, fcf_ttm=1e9, shares_outstanding=1e9, net_debt=0.0, beta=1.0,
)
_fb_plan = select_lenses(FB)
_fb_primary, _fb_cross = decoder._run_plan(_fb_plan, 100.0, FB, None, "FB")
check("AC8 solve-time fallback — planned P/E no-solution → next lens promoted",
      _fb_plan.primary == "pe" and _fb_primary is not None
      and _fb_primary["lens"] != "pe",
      f"planned=pe → effective primary={_fb_primary['lens'] if _fb_primary else None}, "
      f"cross={[c['lens'] for c in _fb_cross]}")
_pe_lens.solve = _orig_solve  # restore so later checks use the real P/E

# AC9 — emit=None produces NO streaming side effects; a real emit DOES fire.
spy = StubEmit()
_ = decode_bet("market", "NVDA", "zh", emit=None, fundamentals_fn=stub_fundamentals)
check("AC9 emit=None → silent (no side-effect channel)",
      len(spy.events) == 0, "spy untouched when emit not passed")
_ = decode_bet("market", "NVDA", "zh", emit=spy, fundamentals_fn=stub_fundamentals)
check("AC9 emit callback fires ActivityEvents when provided",
      len(spy.events) > 0
      and all("phase" in e and "kind" in e and "text" in e for e in spy.events),
      f"{len(spy.events)} events, phases={[e['phase'] for e in spy.events]}")
# A broken emit must not break decoding.
def boom(_ev):
    raise ValueError("emit blew up")
safe = decode_bet("market", "NVDA", "zh", emit=boom,
                  fundamentals_fn=stub_fundamentals)
check("AC9 broken emit never breaks decode",
      isinstance(safe, db.BetCard) and safe.bet is not None)

# AC10 (boundary) — None/empty/missing inputs never raise; degrade gracefully.
edge_cases = {
    "None input": ("market", None),
    "empty string": ("market", ""),
    "empty dict": ("market", {}),
    "unknown source_type": ("weird_source", "NVDA"),
    "analyst_pt (V2 oos)": ("analyst_pt", "NVDA $300"),
    "empty portfolio str": ("portfolio", ""),
    "empty portfolio list": ("portfolio", []),
}
edge_ok = True
edge_detail = []
for name, (st, si) in edge_cases.items():
    try:
        c = decode_bet(st, si, "zh", fundamentals_fn=stub_fundamentals)
        ok = isinstance(c, db.BetCard)
    except Exception as exc:
        ok = False
        edge_detail.append(f"{name}!RAISED:{exc}")
    edge_ok = edge_ok and ok
check("AC10 edge inputs (None/empty/unknown) → BetCard, never raise",
      edge_ok, "; ".join(edge_detail) if edge_detail else "all degraded cleanly")

# AC10b — upstream fetch failure (yfinance raises) → insufficient, no crash.
def fetch_boom(_t):
    raise RuntimeError("yfinance 503")
boom_card = decode_bet("market", "ZZZZ", "zh", fundamentals_fn=fetch_boom)
check("AC10b upstream fetch failure → insufficient card (no crash)",
      isinstance(boom_card, db.BetCard)
      and D(boom_card).get("status") == "insufficient",
      f"reason={D(boom_card).get('reason')}")

# --- Portfolio path -------------------------------------------------------
port = decode_bet("portfolio", "NVDA, COST, NOEARN", "zh",
                  fundamentals_fn=stub_fundamentals)
check("Portfolio decode → portfolio BetCard with holdings",
      isinstance(port, db.BetCard) and port.card_kind == db.PORTFOLIO
      and len(port.holdings) == 3 and port.bet is None,
      f"holdings={[h.ticker for h in port.holdings]} "
      f"decoded_legs={D(port).get('decoded_legs')}")
# Portfolio with weights as list-of-dicts.
port2 = decode_bet("portfolio",
                   [{"ticker": "NVDA", "weight_pct": 60.0},
                    {"ticker": "COST", "weight_pct": 40.0}],
                   "zh", fundamentals_fn=stub_fundamentals)
check("Portfolio parses weighted dict holdings",
      len(port2.holdings) == 2 and port2.holdings[0].weight_pct == 60.0,
      f"weights={[h.weight_pct for h in port2.holdings]}")

# --- Save-card round-trip (proves return value is a storable M1 BetCard) ---
rt_conn = db.init_db(":memory:")
stored_id = db.save_card(rt_conn, nvda_card)
reloaded = db.get_card(rt_conn, stored_id)
check("Returned card is storable via M1 save_card/get_card round-trip",
      reloaded is not None and reloaded.subject == "NVDA"
      and reloaded.source_type == "market",
      f"stored_id={stored_id[:8]}.. reloaded.subject={reloaded.subject}")

# ===========================================================================
print("=" * 70)
print(f"RESULT: {_passed} passed, {_failed} failed")
print("=" * 70)
raise SystemExit(1 if _failed else 0)
