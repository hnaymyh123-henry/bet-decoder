"""Provider-agnostic options distribution core.

The module is deliberately offline and deterministic.  Callers pass plain option
chain rows from any provider; field names are normalized at the boundary.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from math import erf, exp, isfinite, log, sqrt
from typing import Any, Iterable, Mapping

import numpy as np
from scipy.interpolate import PchipInterpolator
from scipy.optimize import brentq


MIN_RND_STRIKES = 8
HIGH_QUALITY_STRIKES = 15
MAX_SPREAD_PCT = 0.15
_trapz = getattr(np, "trapezoid", None) or np.trapz  # numpy 2.x compat
TARGET_DTE = 90
PRIMARY_DTE_MIN = 45
PRIMARY_DTE_MAX = 150
FALLBACK_DTE_MIN = 21


@dataclass(frozen=True)
class NormalizedOption:
    strike: float
    expiry: date
    option_type: str
    bid: float
    ask: float
    mid: float
    volume: float | None = None
    open_interest: float | None = None
    implied_vol: float | None = None
    raw: Mapping[str, Any] | None = None


def analyze_options_distribution(
    rows: Iterable[Mapping[str, Any]],
    *,
    spot: float | None,
    as_of: date | str | datetime | None = None,
    symbol: str | None = None,
    instrument_type: str = "stock",
    role: str = "company",
    target: float | None = None,
    risk_free_rate: float = 0.0,
    skew_history: Iterable[float] | None = None,
) -> dict[str, Any]:
    """Build the SPEC_B distribution channel payload from option-chain rows."""

    as_of_date = _coerce_date(as_of) or date.today()
    options = normalize_option_rows(rows)
    instrument = {"type": instrument_type, "symbol": symbol, "role": role}
    if not options or not spot or spot <= 0:
        return _empty_payload(instrument, "missing_option_chain_or_spot")

    expiry_info = pick_target_expiry(options, as_of=as_of_date)
    if not expiry_info:
        return _empty_payload(instrument, "no_expiry_at_or_beyond_21_dte")

    expiry, dte, expiry_quality = expiry_info
    expiry_options = [o for o in options if o.expiry == expiry]
    calls = filter_option_chain_rows(expiry_options, option_type="call")
    puts = filter_option_chain_rows(expiry_options, option_type="put")

    implied_range = compute_implied_range(expiry_options, spot=spot, expiry=expiry, as_of=as_of_date)
    forward, forward_source = estimate_forward(
        expiry_options,
        spot=spot,
        expiry=expiry,
        as_of=as_of_date,
        risk_free_rate=risk_free_rate,
    )

    rnd = build_rnd_from_calls(
        calls,
        spot=spot,
        forward=forward,
        expiry=expiry,
        as_of=as_of_date,
        risk_free_rate=risk_free_rate,
        forward_source=forward_source,
        expiry_quality=expiry_quality,
    )
    prob = compute_prob_of_target(rnd, target) if target is not None else None
    skew = compute_skew_signal(
        expiry_options,
        spot=spot,
        forward=forward,
        expiry=expiry,
        as_of=as_of_date,
        risk_free_rate=risk_free_rate,
        skew_history=skew_history,
    )
    term_structure = compute_term_structure(
        options,
        spot=spot,
        as_of=as_of_date,
        risk_free_rate=risk_free_rate,
    )
    status = _status_from_quality(rnd.get("quality"), bool(rnd.get("densities")))
    signal = _distribution_signal(skew)
    if status == "honest_empty":
        signal = "unknown"

    return {
        "status": status,
        "instruments": [instrument],
        "implied_range": implied_range,
        "rnd": rnd,
        "prob_of_target": prob,
        "skew": skew,
        "term_structure": term_structure,
        "quality": {
            "status": status,
            "reason": None if status != "degraded" else "insufficient_liquidity_for_full_rnd",
            "diagnostics": rnd.get("diagnostics", {}),
        },
        "signal": signal,
    }


def normalize_option_rows(rows: Iterable[Mapping[str, Any]]) -> list[NormalizedOption]:
    out: list[NormalizedOption] = []
    for row in rows or []:
        strike = _first_float(row, "strike", "strike_price", "k")
        expiry = _coerce_date(_first(row, "expiry", "expiration", "expiration_date", "expiry_date"))
        option_type = _normalize_type(_first(row, "type", "option_type", "right", "contract_type", "put_call"))
        bid = _first_float(row, "bid", "bid_price")
        ask = _first_float(row, "ask", "ask_price")
        mid = _first_float(row, "mid", "mark", "last", "price")
        if mid is None and bid is not None and ask is not None:
            mid = (bid + ask) / 2.0
        if strike is None or expiry is None or option_type is None or bid is None or ask is None or mid is None:
            continue
        iv = _first_float(row, "implied_vol", "implied_volatility", "iv")
        out.append(
            NormalizedOption(
                strike=strike,
                expiry=expiry,
                option_type=option_type,
                bid=bid,
                ask=ask,
                mid=mid,
                volume=_first_float(row, "volume", "vol"),
                open_interest=_first_float(row, "open_interest", "oi", "openInterest"),
                implied_vol=iv,
                raw=row,
            )
        )
    return sorted(out, key=lambda o: (o.expiry, o.option_type, o.strike))


def pick_target_expiry(
    rows: Iterable[NormalizedOption | Mapping[str, Any]],
    *,
    as_of: date | str | datetime | None = None,
) -> tuple[date, int, str] | None:
    options = _ensure_options(rows)
    as_of_date = _coerce_date(as_of) or date.today()
    expiries = sorted({o.expiry for o in options if (o.expiry - as_of_date).days >= FALLBACK_DTE_MIN})
    if not expiries:
        return None
    primary = [e for e in expiries if PRIMARY_DTE_MIN <= (e - as_of_date).days <= PRIMARY_DTE_MAX]
    if primary:
        expiry = min(primary, key=lambda e: abs((e - as_of_date).days - TARGET_DTE))
        return expiry, (expiry - as_of_date).days, "target_90d"
    expiry = min(expiries, key=lambda e: (e - as_of_date).days)
    return expiry, (expiry - as_of_date).days, "fallback_nearest_ge_21d"


def filter_option_chain_rows(
    rows: Iterable[NormalizedOption | Mapping[str, Any]],
    *,
    option_type: str = "call",
    max_spread_pct: float = MAX_SPREAD_PCT,
) -> list[NormalizedOption]:
    options = _ensure_options(rows)
    typ = _normalize_type(option_type)
    filtered: list[NormalizedOption] = []
    for option in options:
        if typ and option.option_type != typ:
            continue
        if option.bid <= 0 or option.ask <= option.bid or option.mid <= 0:
            continue
        spread_pct = (option.ask - option.bid) / option.mid
        has_liquidity = (option.open_interest or 0) > 0 or (option.volume or 0) > 0
        if spread_pct <= max_spread_pct and has_liquidity:
            filtered.append(option)
    dedup: dict[float, NormalizedOption] = {}
    for option in sorted(filtered, key=lambda o: (o.strike, o.mid)):
        dedup[option.strike] = option
    return [dedup[k] for k in sorted(dedup)]


def compute_implied_range(
    rows: Iterable[NormalizedOption | Mapping[str, Any]],
    *,
    spot: float,
    expiry: date | str | datetime,
    as_of: date | str | datetime | None = None,
) -> dict[str, Any]:
    options = [o for o in _ensure_options(rows) if o.expiry == _coerce_date(expiry)]
    calls = filter_option_chain_rows(options, option_type="call")
    puts = filter_option_chain_rows(options, option_type="put")
    atm_call = _nearest(calls, spot)
    atm_put = _nearest(puts, spot)
    dte = (_coerce_date(expiry) - (_coerce_date(as_of) or date.today())).days
    if not atm_call or not atm_put:
        return {
            "lower": None,
            "upper": None,
            "confidence": 0.68,
            "expiry": _coerce_date(expiry).isoformat(),
            "days_to_expiry": dte,
            "straddle_price": None,
            "straddle_pct": None,
            "quality": "degraded",
            "reason": "missing_liquid_atm_call_or_put",
        }
    straddle = atm_call.mid + atm_put.mid
    return {
        "lower": max(0.0, spot - straddle),
        "upper": spot + straddle,
        "confidence": 0.68,
        "expiry": atm_call.expiry.isoformat(),
        "days_to_expiry": dte,
        "straddle_price": straddle,
        "straddle_pct": straddle / spot,
        "quality": "ok",
    }


def estimate_forward(
    rows: Iterable[NormalizedOption | Mapping[str, Any]],
    *,
    spot: float,
    expiry: date | str | datetime,
    as_of: date | str | datetime | None = None,
    risk_free_rate: float = 0.0,
) -> tuple[float, str]:
    options = [o for o in _ensure_options(rows) if o.expiry == _coerce_date(expiry)]
    calls = filter_option_chain_rows(options, option_type="call")
    puts = filter_option_chain_rows(options, option_type="put")
    put_by_strike = {p.strike: p for p in puts}
    pairs = [(c, put_by_strike[c.strike]) for c in calls if c.strike in put_by_strike]
    if not pairs:
        return spot, "spot_fallback"
    call, put = min(pairs, key=lambda pair: abs(pair[0].strike - spot))
    t = max(_year_fraction(as_of, expiry), 1e-9)
    return call.strike + exp(risk_free_rate * t) * (call.mid - put.mid), "put_call_parity"


def build_rnd_from_calls(
    call_rows: Iterable[NormalizedOption | Mapping[str, Any]],
    *,
    spot: float,
    forward: float | None = None,
    expiry: date | str | datetime,
    as_of: date | str | datetime | None = None,
    risk_free_rate: float = 0.0,
    forward_source: str = "unknown",
    expiry_quality: str = "target_90d",
) -> dict[str, Any]:
    calls = filter_option_chain_rows(call_rows, option_type="call")
    valid_count = len(calls)
    expiry_date = _coerce_date(expiry)
    dte = (expiry_date - (_coerce_date(as_of) or date.today())).days
    base = {
        "method": "breeden_litzenberger",
        "smoothing": "pchip_monotone_cubic",
        "expiry": expiry_date.isoformat(),
        "forward": forward if forward is not None else spot,
        "discount_factor": exp(-risk_free_rate * max(_year_fraction(as_of, expiry_date), 0.0)),
        "strikes": [],
        "call_prices": [],
        "densities": [],
        "cdf": [],
        "tail_policy": "none_mvp_grid_only",
        "normalization": "area_1",
        "quality": "low",
        "diagnostics": {
            "valid_strike_count": valid_count,
            "grid_step": None,
            "negative_mass_clipped": 0.0,
            "tail_mass": 0.0,
            "forward_source": forward_source,
            "days_to_expiry": dte,
            "expiry_selection": expiry_quality,
        },
    }
    if valid_count < MIN_RND_STRIKES:
        base["smoothing"] = "skipped"
        base["diagnostics"]["reason"] = "fewer_than_8_liquid_call_strikes"
        return base

    strikes = np.array([c.strike for c in calls], dtype=float)
    prices = np.array([c.mid for c in calls], dtype=float)
    order = np.argsort(strikes)
    strikes = strikes[order]
    prices = _enforce_nonincreasing(prices[order])
    grid_step = _infer_grid_step(strikes)
    grid = np.arange(strikes[0], strikes[-1] + grid_step * 0.5, grid_step)
    smoothed = _smooth_calls(strikes, prices, grid)
    smoothed = _enforce_nonincreasing(smoothed)
    second = (smoothed[:-2] - 2.0 * smoothed[1:-1] + smoothed[2:]) / (grid_step * grid_step)
    raw_density = exp(risk_free_rate * max(_year_fraction(as_of, expiry_date), 0.0)) * second
    negative_mass = float(_trapz(np.abs(np.minimum(raw_density, 0.0)), grid[1:-1]))
    density = np.maximum(raw_density, 0.0)
    area = float(_trapz(density, grid[1:-1]))
    if area <= 0 or not isfinite(area):
        base["smoothing"] = "linear_fallback_failed"
        base["diagnostics"]["reason"] = "non_positive_density_area"
        base["diagnostics"]["negative_mass_clipped"] = negative_mass
        return base
    density = density / area
    cdf = _cdf_from_density(grid[1:-1], density)
    quality = "high" if valid_count >= HIGH_QUALITY_STRIKES else "medium"
    if forward_source != "put_call_parity" or expiry_quality != "target_90d":
        quality = _min_quality(quality, "medium")
    if negative_mass > 0.20:
        quality = "low"
    return {
        **base,
        "strikes": [float(x) for x in grid[1:-1]],
        "call_prices": [float(x) for x in smoothed[1:-1]],
        "densities": [float(x) for x in density],
        "cdf": [float(x) for x in cdf],
        "quality": quality,
        "diagnostics": {
            **base["diagnostics"],
            "valid_strike_count": valid_count,
            "grid_step": float(grid_step),
            "negative_mass_clipped": negative_mass,
            "tail_mass": 0.0,
            "forward_source": forward_source,
        },
    }


def compute_prob_of_target(rnd: Mapping[str, Any], target: float) -> dict[str, Any]:
    strikes = np.array(rnd.get("strikes") or [], dtype=float)
    densities = np.array(rnd.get("densities") or [], dtype=float)
    if len(strikes) < 2 or len(densities) != len(strikes):
        return {
            "target": target,
            "prob_above": None,
            "prob_below": None,
            "interpretation": "RND unavailable; target tail probability skipped",
        }
    if target <= strikes[0]:
        prob_above = 1.0
    elif target >= strikes[-1]:
        prob_above = 0.0
    else:
        mask = strikes >= target
        x = np.concatenate(([target], strikes[mask]))
        y = np.concatenate(([float(np.interp(target, strikes, densities))], densities[mask]))
        prob_above = float(_trapz(y, x))
    prob_above = max(0.0, min(1.0, prob_above))
    return {
        "target": target,
        "prob_above": prob_above,
        "prob_below": 1.0 - prob_above,
        "interpretation": f"Target is in the ~{prob_above:.0%} risk-neutral upper tail",
    }


def compute_skew_signal(
    rows: Iterable[NormalizedOption | Mapping[str, Any]],
    *,
    spot: float,
    forward: float,
    expiry: date | str | datetime,
    as_of: date | str | datetime | None = None,
    risk_free_rate: float = 0.0,
    skew_history: Iterable[float] | None = None,
) -> dict[str, Any]:
    expiry_date = _coerce_date(expiry)
    options = [o for o in _ensure_options(rows) if o.expiry == expiry_date]
    t = max(_year_fraction(as_of, expiry_date), 1e-9)
    put_iv = _iv_at_delta(options, spot=spot, t=t, r=risk_free_rate, target_delta=-0.25, option_type="put")
    call_iv = _iv_at_delta(options, spot=spot, t=t, r=risk_free_rate, target_delta=0.25, option_type="call")
    raw = None if put_iv is None or call_iv is None else put_iv - call_iv
    percentile = _percentile(raw, list(skew_history or [])) if raw is not None and skew_history else None
    if raw is None:
        interpretation = "Insufficient liquid options for 25-delta skew"
    elif raw > 0:
        interpretation = "Put-side protection is richer than call-side upside"
    elif raw < 0:
        interpretation = "Call-side upside is richer than put-side protection"
    else:
        interpretation = "25-delta call and put IV are balanced"
    return {
        "raw": raw,
        "put_iv": put_iv,
        "call_iv": call_iv,
        "percentile_1y": percentile,
        "comparison_window": "1y",
        "interpretation": interpretation,
        "forward": forward,
    }


def compute_term_structure(
    rows: Iterable[NormalizedOption | Mapping[str, Any]],
    *,
    spot: float,
    as_of: date | str | datetime | None = None,
    risk_free_rate: float = 0.0,
) -> dict[str, Any] | None:
    options = _ensure_options(rows)
    as_of_date = _coerce_date(as_of) or date.today()
    expiries = sorted({o.expiry for o in options if (o.expiry - as_of_date).days >= 7})
    if len(expiries) < 2:
        return None
    near, far = expiries[0], expiries[1]
    near_iv = _atm_iv(options, spot=spot, expiry=near, as_of=as_of_date, risk_free_rate=risk_free_rate)
    far_iv = _atm_iv(options, spot=spot, expiry=far, as_of=as_of_date, risk_free_rate=risk_free_rate)
    if near_iv is None or far_iv is None:
        slope = "unknown"
        interpretation = "Insufficient ATM IV data for term structure"
    elif near_iv > far_iv:
        slope = "downward"
        interpretation = "Near-month IV is richer than the next expiry"
    elif near_iv < far_iv:
        slope = "upward"
        interpretation = "Next-expiry IV is richer than near-month IV"
    else:
        slope = "flat"
        interpretation = "Near and next-expiry IV are balanced"
    return {
        "near_expiry": near.isoformat(),
        "far_expiry": far.isoformat(),
        "near_iv": near_iv,
        "far_iv": far_iv,
        "slope": slope,
        "interpretation": interpretation,
    }


def _empty_payload(instrument: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "status": "honest_empty",
        "instruments": [instrument],
        "implied_range": None,
        "rnd": {"quality": "low", "diagnostics": {"reason": reason}},
        "prob_of_target": None,
        "skew": None,
        "term_structure": None,
        "quality": {"status": "honest_empty", "reason": reason, "diagnostics": {"reason": reason}},
        "signal": "unknown",
    }


def _ensure_options(rows: Iterable[NormalizedOption | Mapping[str, Any]]) -> list[NormalizedOption]:
    rows = list(rows or [])
    if not rows:
        return []
    if isinstance(rows[0], NormalizedOption):
        return list(rows)  # type: ignore[return-value]
    return normalize_option_rows(rows)  # type: ignore[arg-type]


def _first(row: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in row and row[name] not in ("", None):
            return row[name]
    return None


def _first_float(row: Mapping[str, Any], *names: str) -> float | None:
    value = _first(row, *names)
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if isfinite(value) else None


def _coerce_date(value: date | str | datetime | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value)
    if "T" in text:
        text = text.split("T", 1)[0]
    return datetime.strptime(text[:10], "%Y-%m-%d").date()


def _normalize_type(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"c", "call", "calls"}:
        return "call"
    if text in {"p", "put", "puts"}:
        return "put"
    return None


def _year_fraction(as_of: date | str | datetime | None, expiry: date | str | datetime) -> float:
    start = _coerce_date(as_of) or date.today()
    end = _coerce_date(expiry)
    return max((end - start).days / 365.0, 0.0)


def _nearest(options: list[NormalizedOption], strike: float) -> NormalizedOption | None:
    return min(options, key=lambda o: abs(o.strike - strike), default=None)


def _enforce_nonincreasing(values: np.ndarray) -> np.ndarray:
    values = np.array(values, dtype=float).copy()
    for i in range(1, len(values)):
        if values[i] > values[i - 1]:
            values[i] = values[i - 1]
    return values


def _infer_grid_step(strikes: np.ndarray) -> float:
    diffs = np.diff(np.unique(strikes))
    diffs = diffs[diffs > 0]
    if len(diffs) == 0:
        return 1.0
    return float(np.median(diffs))


def _smooth_calls(strikes: np.ndarray, prices: np.ndarray, grid: np.ndarray) -> np.ndarray:
    try:
        return np.asarray(PchipInterpolator(strikes, prices, extrapolate=False)(grid), dtype=float)
    except Exception:
        return np.interp(grid, strikes, prices)


def _cdf_from_density(strikes: np.ndarray, density: np.ndarray) -> np.ndarray:
    cdf = np.zeros_like(density)
    for i in range(1, len(density)):
        cdf[i] = cdf[i - 1] + 0.5 * (density[i - 1] + density[i]) * (strikes[i] - strikes[i - 1])
    if cdf[-1] > 0:
        cdf = cdf / cdf[-1]
    return cdf


def _min_quality(a: str, b: str) -> str:
    order = {"low": 0, "medium": 1, "high": 2}
    return a if order[a] <= order[b] else b


def _status_from_quality(quality: str | None, has_rnd: bool) -> str:
    if not has_rnd:
        return "degraded"
    return "ok" if quality in {"high", "medium"} else "degraded"


def _distribution_signal(skew: Mapping[str, Any] | None) -> str:
    if not skew or skew.get("raw") is None:
        return "unknown"
    raw = float(skew["raw"])
    percentile = skew.get("percentile_1y")
    if percentile is not None and percentile > 0.80 and raw > 0:
        return "bearish"
    if percentile is not None and percentile > 0.80 and raw < 0:
        return "euphoric"
    if raw > 0.04:
        return "bearish"
    if raw < -0.04:
        return "euphoric"
    return "neutral"


def _percentile(value: float, history: list[float]) -> float | None:
    clean = sorted(x for x in history if x is not None and isfinite(float(x)))
    if not clean:
        return None
    return sum(1 for x in clean if x <= value) / len(clean)


def _atm_iv(
    options: list[NormalizedOption],
    *,
    spot: float,
    expiry: date,
    as_of: date,
    risk_free_rate: float,
) -> float | None:
    subset = [o for o in options if o.expiry == expiry and o.option_type == "call"]
    atm = _nearest(filter_option_chain_rows(subset, option_type="call"), spot)
    if not atm:
        return None
    if atm.implied_vol and atm.implied_vol > 0:
        return atm.implied_vol
    return implied_volatility(atm.mid, spot, atm.strike, _year_fraction(as_of, expiry), risk_free_rate, "call")


def _iv_at_delta(
    options: list[NormalizedOption],
    *,
    spot: float,
    t: float,
    r: float,
    target_delta: float,
    option_type: str,
) -> float | None:
    filtered = filter_option_chain_rows(options, option_type=option_type)
    points: list[tuple[float, float]] = []
    for option in filtered:
        iv = option.implied_vol
        if not iv or iv <= 0:
            iv = implied_volatility(option.mid, spot, option.strike, t, r, option_type)
        if not iv:
            continue
        delta = black_scholes_delta(spot, option.strike, t, r, iv, option_type)
        points.append((delta, iv))
    if not points:
        return None
    points = sorted(points)
    deltas = np.array([p[0] for p in points], dtype=float)
    ivs = np.array([p[1] for p in points], dtype=float)
    if target_delta < deltas[0] or target_delta > deltas[-1]:
        return float(ivs[int(np.argmin(np.abs(deltas - target_delta)))])
    return float(np.interp(target_delta, deltas, ivs))


def implied_volatility(price: float, spot: float, strike: float, t: float, r: float, option_type: str) -> float | None:
    intrinsic = max(0.0, spot - strike) if option_type == "call" else max(0.0, strike - spot)
    if price < intrinsic or price <= 0 or spot <= 0 or strike <= 0 or t <= 0:
        return None

    def objective(vol: float) -> float:
        return black_scholes_price(spot, strike, t, r, vol, option_type) - price

    try:
        return float(brentq(objective, 1e-4, 5.0, maxiter=100))
    except ValueError:
        return None


def black_scholes_price(spot: float, strike: float, t: float, r: float, vol: float, option_type: str) -> float:
    if t <= 0 or vol <= 0:
        return max(0.0, spot - strike) if option_type == "call" else max(0.0, strike - spot)
    d1 = (log(spot / strike) + (r + 0.5 * vol * vol) * t) / (vol * sqrt(t))
    d2 = d1 - vol * sqrt(t)
    if option_type == "call":
        return spot * _norm_cdf(d1) - strike * exp(-r * t) * _norm_cdf(d2)
    return strike * exp(-r * t) * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


def black_scholes_delta(spot: float, strike: float, t: float, r: float, vol: float, option_type: str) -> float:
    d1 = (log(spot / strike) + (r + 0.5 * vol * vol) * t) / (vol * sqrt(t))
    if option_type == "call":
        return _norm_cdf(d1)
    return _norm_cdf(d1) - 1.0


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + erf(x / sqrt(2.0)))

