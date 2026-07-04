"""Offline verification for the options distribution core.

Run: python tests/verify_distribution.py
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import distribution as dist  # noqa: E402


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


AS_OF = date(2026, 7, 4)
EXP_30 = "2026-08-03"
EXP_92 = "2026-10-04"
EXP_180 = "2026-12-31"


def make_chain(expiry=EXP_92, strikes=None, spot=100.0, call_iv=0.32, put_iv=0.36, noisy=False):
    strikes = strikes or list(range(60, 141, 5))
    rows = []
    t = (date.fromisoformat(expiry) - AS_OF).days / 365
    for strike in strikes:
        call = dist.black_scholes_price(spot, strike, t, 0.02, call_iv, "call")
        put = dist.black_scholes_price(spot, strike, t, 0.02, put_iv, "put")
        for typ, mid, iv in (("call", call, call_iv), ("put", put, put_iv)):
            spread = max(0.02, mid * (0.10 if not noisy else 0.25))
            rows.append(
                {
                    "expiration": expiry,
                    "strike": strike,
                    "type": typ,
                    "bid": max(0.01, mid - spread / 2),
                    "ask": max(0.02, mid + spread / 2),
                    "mid": mid,
                    "volume": 10,
                    "open_interest": 100,
                    "iv": iv,
                }
            )
    return rows


print("\n=== expiry/filter/forward ===")
chain = make_chain(EXP_30) + make_chain(EXP_92) + make_chain(EXP_180)
options = dist.normalize_option_rows(chain)
expiry, dte, quality = dist.pick_target_expiry(options, as_of=AS_OF)
check("AC1 picks expiry closest to 90d inside 45-150d", expiry.isoformat() == EXP_92, f"dte={dte} quality={quality}")
filtered = dist.filter_option_chain_rows(options, option_type="call")
check("AC2 filter keeps liquid calls and excludes puts", filtered and all(o.option_type == "call" for o in filtered))
fwd, source = dist.estimate_forward(options, spot=100.0, expiry=expiry, as_of=AS_OF, risk_free_rate=0.02)
check("AC3 put-call parity forward is used when puts exist", source == "put_call_parity")
check("AC4 forward is near spot for synthetic fixture", 97.0 < fwd < 103.0, f"forward={fwd:.2f}")


print("\n=== full distribution ===")
payload = dist.analyze_options_distribution(
    chain,
    spot=100.0,
    as_of=AS_OF,
    symbol="TEST",
    target=120.0,
    risk_free_rate=0.02,
    skew_history=[-0.03, 0.00, 0.02, 0.04, 0.05],
)
rnd = payload["rnd"]
check("AC5 status ok for liquid chain", payload["status"] == "ok", f"status={payload['status']}")
check("AC6 RND has normalized positive densities", len(rnd["densities"]) >= 8 and min(rnd["densities"]) >= 0)
area = 0.0
xs = rnd["strikes"]
ys = rnd["densities"]
for i in range(1, len(xs)):
    area += 0.5 * (ys[i - 1] + ys[i]) * (xs[i] - xs[i - 1])
check("AC7 RND area normalizes to 1", abs(area - 1.0) < 1e-6, f"area={area:.6f}")
check("AC8 implied range uses ATM straddle", payload["implied_range"]["straddle_price"] > 0)
check("AC9 target probability is bounded", 0.0 <= payload["prob_of_target"]["prob_above"] <= 1.0)
check("AC10 skew computes 25-delta put/call IVs", payload["skew"]["put_iv"] and payload["skew"]["call_iv"])
check("AC11 term structure compares nearest two expiries", payload["term_structure"]["near_expiry"] == EXP_30)
check("AC12 signal uses allowed vocabulary", payload["signal"] in {"bullish", "neutral", "bearish", "euphoric", "unknown"})


print("\n=== degraded and honest-empty behavior ===")
thin = make_chain(strikes=[90, 95, 100, 105, 110])
thin_payload = dist.analyze_options_distribution(thin, spot=100.0, as_of=AS_OF, symbol="THIN", target=120.0)
check("AC13 fewer than 8 strikes degrades instead of faking RND", thin_payload["status"] == "degraded")
check("AC14 degraded chain still returns ATM implied range", thin_payload["implied_range"]["straddle_price"] is not None)
check("AC15 degraded target probability is skipped honestly", thin_payload["prob_of_target"]["prob_above"] is None)
bad_rows = make_chain(noisy=True)
bad_payload = dist.analyze_options_distribution(bad_rows, spot=100.0, as_of=AS_OF, symbol="WIDE")
check("AC16 wide spreads are excluded and degrade", bad_payload["status"] == "degraded")
empty_payload = dist.analyze_options_distribution([], spot=100.0, as_of=AS_OF, symbol="EMPTY")
check("AC17 no chain returns honest_empty", empty_payload["status"] == "honest_empty")
check("AC18 honest_empty signal is unknown", empty_payload["signal"] == "unknown")


print("\n" + "=" * 72)
print(f"RESULT: {_passed} passed, {_failed} failed")
print("=" * 72)
raise SystemExit(1 if _failed else 0)
