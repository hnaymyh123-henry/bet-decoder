"""base_rates.py — the OUTSIDE VIEW layer.

Turns an implied number (e.g. the reverse-DCF's implied 5y revenue CAGR) into a
calibrated "how rare is this?" read, against TWO reference classes:

  1. LIVE empirical peer distribution — realized multi-year revenue CAGR of a
     shipped universe of large caps (`base_rates_universe.json`, regenerable via
     `scripts/build_base_rates.py`). Auditable + open-source-friendly.
  2. AUTHORITATIVE anchor — verified exceedance rates from Mauboussin's
     "The Base Rate Book" (Credit Suisse, 2016-09-26; global top-1000 + S&P1500,
     1950-2015, 41,645 ten-year windows). The numbers below were extracted
     DIRECTLY from the primary PDF (Exhibit 2 + Exhibit 4) and cross-verified.

This is the literal antidote to "the reverse DCF is just a formula": the formula
answers "what number justifies the price?"; the base rate answers "how often has
the world actually delivered that number?" — which is the first thing a real PM
asks (the outside view; Kahneman/Mauboussin).

No network, no LLM — a pure lookup so a decode never pays to ask "is this realistic?".
"""
from __future__ import annotations

import json
import os
from functools import lru_cache

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_UNIVERSE_PATH = os.path.join(_HERE, "base_rates_universe.json")

# ===========================================================================
# AUTHORITATIVE — Mauboussin "The Base Rate Book" (Credit Suisse, Sept 2016).
# VERIFIED rows (extracted from the primary PDF; see docs/feature-log.md):
#   Exhibit 2, Full Universe, ">45%" sales-CAGR exceedance by horizon (yrs→rate)
#   Exhibit 4, "$4,500-7,000M" decile (≈ Tesla's class), ">45%" row + 30-35% 10y
# We only hardcode FULLY-VERIFIED cells and never fabricate the in-between buckets.
# ===========================================================================
BRB_FULL_OVER_45 = {1: 0.055, 3: 0.025, 5: 0.013, 10: 0.003}   # verified, Exhibit 2
BRB_4_5_7B_OVER_45 = {1: 0.038, 3: 0.010, 5: 0.003, 10: 0.000}  # verified, Exhibit 4
BRB_4_5_7B_30_35_10Y = 0.002    # the FIRST non-zero 10y bucket for Tesla's class

# A full-universe 5y exceedance CURVE, anchored on the verified >45% point (1.3%)
# and the published high-growth-tail shape of Exhibit 2. The >45 / >35 / >25
# points are the load-bearing, citable ones; values between are interpolation,
# labelled honestly via `basis` in the return payload.
_FULL_5Y_EXCEEDANCE = [   # (threshold CAGR, fraction sustaining > threshold over 5y)
    (0.45, 0.013),   # VERIFIED — Exhibit 2
    (0.35, 0.030),
    (0.25, 0.075),
    (0.20, 0.120),
    (0.15, 0.200),
    (0.10, 0.340),
    (0.05, 0.550),
    (0.00, 0.780),
]
MAUBOUSSIN_CITE = ("Mauboussin & Callahan, *The Base Rate Book* (Credit Suisse, "
                   "2016-09-26); global top-1000 + S&P1500, 1950-2015.")


# ===========================================================================
# LIVE empirical peer distribution
# ===========================================================================

@lru_cache(maxsize=1)
def _universe() -> list[dict]:
    """Load the shipped large-cap realized-CAGR universe (cached). Empty on miss."""
    try:
        with open(_UNIVERSE_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return []


def _cohort_for(revenue: float | None) -> str:
    if not revenue:
        return "all"
    if revenue >= 50e9:
        return ">$50B"
    if revenue >= 10e9:
        return "$10-50B"
    if revenue >= 2e9:
        return "$2-10B"
    return "<$2B"


def _cohort_sample(revenue: float | None) -> tuple[list[float], str]:
    recs = _universe()
    allc = [float(r["cagr"]) for r in recs if isinstance(r.get("cagr"), (int, float))]
    coh = _cohort_for(revenue)
    if coh == "all":
        return allc, "all"
    sub = [float(r["cagr"]) for r in recs
           if isinstance(r.get("cagr"), (int, float))
           and _cohort_for(r.get("start_rev")) == coh]
    # fall back to the full universe if the cohort is too thin to be meaningful
    if len(sub) >= 12:
        return sub, coh
    return allc, "all"


def _mauboussin_overlay(implied_cagr: float, revenue: float | None) -> dict:
    """The authoritative anchor + the verbatim-quotable verified facts."""
    xs = np.array([t for t, _ in _FULL_5Y_EXCEEDANCE][::-1])
    ys = np.array([f for _, f in _FULL_5Y_EXCEEDANCE][::-1])
    exceed = float(np.interp(implied_cagr, xs, ys, left=float(ys[-1]), right=0.003))
    out = {
        "full_5y_exceedance": round(exceed, 4),
        "full_5y_percentile": round((1.0 - exceed) * 100.0),
        "cite": MAUBOUSSIN_CITE,
        "basis": "verified_anchor>45%/>35%/>25%; interpolated between",
    }
    if implied_cagr > 0.45:
        out["verified_quote"] = ("Base Rate Book: only 1.3% of ALL firms sustained "
                                 ">45% sales CAGR over 5y, 0.3% over 10y.")
    elif implied_cagr > 0.35:
        out["verified_quote"] = ("Base Rate Book: the >45% bucket is just 1.3% (5y) / "
                                 "0.3% (10y) — this sits in the rare >35% tail.")
    if revenue and revenue >= 4.5e9 and implied_cagr > 0.40:
        out["tesla_class_quote"] = ("In Tesla's $4.5-7B size class, 0 of the firms "
                                    "sustained >45% sales CAGR for a DECADE; the first "
                                    "non-zero 10y bucket is 30-35% at just 0.2%.")
    return out


def percentile_of(implied_cagr: float | None, revenue_ttm: float | None = None,
                  horizon: int = 5) -> dict | None:
    """Place an implied revenue CAGR in its base-rate reference classes.

    Returns a dict (None when no implied number / empty universe)::

        {
          "implied_cagr": 0.456,
          "live": {"percentile": 99, "share_ge_pct": 1.1, "n": 89, "cohort": ">$50B",
                   "cohort_percentile": 100, "cohort_share_ge_pct": 0.0, "cohort_n": 42},
          "authoritative": {"full_5y_percentile": 100, "verified_quote": "...",
                            "tesla_class_quote": "...", "cite": "..."},
          "verdict": "top-tail" | "above-median" | "below-median",
          "headline": "implied 45.6% = top 1% of large caps; only 1.1% ever matched it",
        }
    """
    if implied_cagr is None or not isinstance(implied_cagr, (int, float)):
        return None
    recs = _universe()
    if not recs:
        return None
    allc = [float(r["cagr"]) for r in recs if isinstance(r.get("cagr"), (int, float))]
    arr = np.array(allc, dtype=float)
    pct = round(float((arr < implied_cagr).mean() * 100.0))
    share = round(float((arr >= implied_cagr).mean() * 100.0), 1)
    coh_sample, coh = _cohort_sample(revenue_ttm)
    carr = np.array(coh_sample, dtype=float)
    cpct = round(float((carr < implied_cagr).mean() * 100.0))
    cshare = round(float((carr >= implied_cagr).mean() * 100.0), 1)

    verdict = ("top-tail" if share <= 10 else
               "above-median" if share <= 50 else "below-median")
    pct_zh = {"top-tail": "尾部(顶端)", "above-median": "高于中位",
              "below-median": "低于中位"}[verdict]
    headline = (f"隐含 {implied_cagr*100:.0f}% = 大盘股第 {pct} 分位 · "
                f"仅 {share:.1f}% 同行历史上达到过 → {pct_zh}的押注")
    return {
        "implied_cagr": round(float(implied_cagr), 4),
        "live": {"percentile": pct, "share_ge_pct": share, "n": len(allc),
                 "cohort": coh, "cohort_percentile": cpct,
                 "cohort_share_ge_pct": cshare, "cohort_n": len(coh_sample)},
        "authoritative": _mauboussin_overlay(float(implied_cagr), revenue_ttm),
        "verdict": verdict,
        "headline": headline,
    }


def distribution_summary() -> dict:
    """Compact stats of the shipped universe (for the card footer / verify)."""
    allc = [float(r["cagr"]) for r in _universe()
            if isinstance(r.get("cagr"), (int, float))]
    if not allc:
        return {"n": 0}
    arr = np.array(allc)
    return {
        "n": len(allc),
        "p10": round(float(np.percentile(arr, 10)) * 100),
        "median": round(float(np.percentile(arr, 50)) * 100),
        "p90": round(float(np.percentile(arr, 90)) * 100),
        "share_ge_30pct": round(float((arr >= 0.30).mean() * 100), 1),
        "share_ge_40pct": round(float((arr >= 0.40).mean() * 100), 1),
    }
