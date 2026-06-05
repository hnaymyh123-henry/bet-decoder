"""Bet Decoder reverse DCF prototype.

Run: python reverse_dcf.py NVDA
Deps: pip install -r requirements.txt
"""
import json
import os
import sys
import time
from dataclasses import dataclass, replace

import numpy as np
import yfinance as yf
from scipy.optimize import brentq


@dataclass
class CompanyData:
    ticker: str
    current_price: float
    revenue_ttm: float
    fcf_ttm: float
    shares_outstanding: float
    net_debt: float
    beta: float


@dataclass
class Assumptions:
    revenue_cagr_5y: float
    terminal_growth: float
    terminal_fcf_margin: float
    wacc: float


def _safe_row(df, *candidates):
    for name in candidates:
        if name in df.index:
            val = df.loc[name].iloc[0]
            if val is not None and not (isinstance(val, float) and np.isnan(val)):
                return float(val)
    return 0.0


def pull_company_data(ticker: str) -> CompanyData:
    t = yf.Ticker(ticker)
    info = t.info
    financials = t.financials
    cashflow = t.cashflow
    bs = t.balance_sheet

    revenue = _safe_row(financials, "Total Revenue", "TotalRevenue")
    ocf = _safe_row(cashflow, "Operating Cash Flow", "Total Cash From Operating Activities")
    capex = _safe_row(cashflow, "Capital Expenditure", "Capital Expenditures")
    fcf = ocf + capex  # capex is signed negative in yfinance

    total_debt = _safe_row(bs, "Total Debt")
    cash = _safe_row(bs, "Cash And Cash Equivalents", "Cash")
    net_debt = total_debt - cash

    return CompanyData(
        ticker=ticker,
        current_price=float(info.get("currentPrice") or info.get("regularMarketPrice")),
        revenue_ttm=revenue,
        fcf_ttm=fcf,
        shares_outstanding=float(info["sharesOutstanding"]),
        net_debt=net_debt,
        beta=float(info.get("beta") or 1.0),
    )


# --- Live / sourced macro inputs -------------------------------------------
# Equity risk premium: a sourced constant, not a guess.  Damodaran's implied ERP
# for the US sits ~4.3–4.8%; pin to a documented value, refresh manually (a live
# Damodaran pull is an Excel scrape — deferred to a later tier).
EQUITY_RISK_PREMIUM = 0.046          # Damodaran implied ERP (US, ~2026)
# Terminal growth: long-run nominal GDP anchor (~2% real GDP + ~2% inflation,
# held conservative).  A labeled convention — never the driver of the bet.
TERMINAL_GROWTH = 0.025
# Cap the upper anchor's growth: extrapolating an extreme trailing CAGR (e.g. a
# post-AI-boom ~100%) flat for 5y is absurd (revenue ×32). The upper anchor models
# a CAPPED continuation; the real trailing CAGR is still surfaced for contrast.
HIST_CAGR_CAP = 0.40

# --- Tier 2: hardcoded sector long-run growth + TAM (no network, $0) ----------
# Long-run (~10y) revenue CAGR by sector — a sourced rough NORM, not a forecast of
# any single company; drives the "industry" base scenario. Matched as a substring
# against the lowercased "sector / industry" string (longest key first).
SECTOR_LONG_RUN_CAGR = {
    "semiconductor": 0.15, "ai": 0.18, "cloud": 0.18, "software": 0.12,
    "internet": 0.12, "technology": 0.11, "communication": 0.06,
    "biotech": 0.10, "pharmaceutical": 0.06, "health": 0.07,
    "auto": 0.08, "consumer cyclical": 0.06, "consumer defensive": 0.04,
    "discount stores": 0.07, "retail": 0.05, "industrial": 0.05,
    "financial": 0.05, "bank": 0.04, "energy": 0.03, "utilities": 0.03,
    "real estate": 0.04, "materials": 0.04,
}
# Approximate total addressable market (USD) by sector — coarse, for the implied
# market-share landing. Missing key → landing shows revenue terminal only.
SECTOR_TAM_USD = {
    "semiconductor": 1_500e9, "ai": 1_300e9, "cloud": 1_000e9,
    "software": 1_200e9, "internet": 900e9, "auto": 4_000e9,
    "health": 9_000e9, "pharmaceutical": 1_600e9, "discount stores": 6_000e9,
    "retail": 6_000e9, "energy": 7_000e9, "financial": 5_000e9,
}


def sector_of(industry: str | None) -> str | None:
    """Map a yfinance 'sector / industry' string to a sector key. Prefer the more
    SPECIFIC industry part (after '/') over the broad sector bucket, then longest
    key first (so 'discount stores' beats both 'retail' and the 'consumer
    defensive' bucket). None if no hit."""
    if not industry:
        return None
    low = str(industry).lower()
    parts = [p.strip() for p in low.split("/") if p.strip()]
    keys = sorted(SECTOR_LONG_RUN_CAGR, key=len, reverse=True)
    for seg in (parts[::-1] + [low]):   # industry part first, then sector, then whole
        for key in keys:
            if key in seg:
                return key
    return None


def industry_cagr(industry: str | None) -> float:
    """Sector long-run CAGR for the 'industry' scenario; fallback to long-run
    nominal-GDP terminal when the sector is unknown."""
    k = sector_of(industry)
    return SECTOR_LONG_RUN_CAGR.get(k, TERMINAL_GROWTH) if k else TERMINAL_GROWTH


def industry_tam(industry: str | None) -> float | None:
    """Sector TAM (USD) for the implied market-share landing; None if unknown."""
    k = sector_of(industry)
    return SECTOR_TAM_USD.get(k) if k else None


def compute_fade_path(start_cagr: float, end_cagr: float, years: int = 5) -> list[float]:
    """Linear fade start→end over `years` — the 'momentum' scenario: current growth
    decaying to long-run GDP, instead of extrapolating an extreme rate flat."""
    if years <= 1:
        return [end_cagr]
    step = (end_cagr - start_cagr) / (years - 1)
    return [start_cagr + step * i for i in range(years)]
_RF_FALLBACK = 0.045                 # offline / fetch-failure fallback
_RF_CACHE = os.path.join("cache", "macro", "risk_free.json")
_RF_TTL_SEC = 86_400                 # 1 day


def fetch_risk_free(default: float = _RF_FALLBACK) -> tuple[float, str]:
    """Live 10-year Treasury yield (^TNX) as the risk-free rate.

    Returns (rate_as_decimal, source).  Order: fresh file cache → (skip the
    network when OFFLINE_MODE) → live ^TNX (then cached for a day) → fallback.
    Never raises — a macro lookup must not sink a decode.
    """
    # 1) fresh file cache
    try:
        with open(_RF_CACHE, encoding="utf-8") as fh:
            c = json.load(fh)
        if (time.time() - float(c.get("ts", 0)) < _RF_TTL_SEC
                and isinstance(c.get("rate"), (int, float)) and 0.0 < c["rate"] < 0.20):
            return float(c["rate"]), "cache"
    except Exception:
        pass
    # 2) offline → never hit the network
    if os.environ.get("OFFLINE_MODE"):
        return default, "fallback_offline"
    # 3) live ^TNX
    try:
        hist = yf.Ticker("^TNX").history(period="5d")
        last = float(hist["Close"].dropna().iloc[-1])
        # ^TNX quotes the yield in percent (e.g. 4.45 → 4.45%); guard the scale.
        rate = last / 100.0
        if rate > 0.20:           # some feeds report ×10 (e.g. 44.5) — normalize
            rate = last / 1000.0
        if 0.0 < rate < 0.20:
            try:
                os.makedirs(os.path.dirname(_RF_CACHE), exist_ok=True)
                with open(_RF_CACHE, "w", encoding="utf-8") as fh:
                    json.dump({"rate": rate, "ts": time.time(), "raw_tnx": last}, fh)
            except Exception:
                pass
            return rate, "live_tnx"
    except Exception:
        pass
    # 4) fallback
    return default, "fallback"


def compute_wacc(
    beta: float,
    risk_free: float = _RF_FALLBACK,
    equity_premium: float = EQUITY_RISK_PREMIUM,
    beta_cap: float = 1.5,
) -> float:
    effective_beta = min(max(beta, 0.5), beta_cap)
    return risk_free + effective_beta * equity_premium


def dcf_equity_value_per_share(a: Assumptions, data: CompanyData,
                               growth_path: list[float] | None = None,
                               years: int = 5) -> float:
    """`growth_path` (optional per-year growth rates) lets a scenario FADE growth
    year-by-year (e.g. momentum: current rate → GDP); when None, the constant
    `a.revenue_cagr_5y` is used — Tier-1 behavior, unchanged.

    `years` (default 5, backward-compatible) is the explicit-forecast horizon —
    the number of years the company grows at `a.revenue_cagr_5y` (or `growth_path`)
    before the Gordon terminal. Parameterizing it lets us reverse-solve the
    market-implied COMPETITIVE ADVANTAGE PERIOD (how many years of growth the price
    buys) instead of fixing the horizon at 5."""
    revenue = data.revenue_ttm
    pv = 0.0
    for year in range(1, years + 1):
        g = (growth_path[year - 1]
             if (growth_path and year - 1 < len(growth_path)) else a.revenue_cagr_5y)
        revenue *= 1 + g
        fcf = revenue * a.terminal_fcf_margin
        pv += fcf / (1 + a.wacc) ** year

    if a.wacc <= a.terminal_growth:
        return -1e9  # infeasible: Gordon model breaks

    terminal_fcf = revenue * a.terminal_fcf_margin * (1 + a.terminal_growth)
    terminal_value = terminal_fcf / (a.wacc - a.terminal_growth)
    pv += terminal_value / (1 + a.wacc) ** years

    equity = pv - data.net_debt
    return equity / data.shares_outstanding


SEARCH_RANGES = {
    # Upper bound raised to 1.00: extreme-premium names (real NVDA-style) imply a
    # 5y CAGR above 80%; capping at 0.80 made reverse_solve return None for them,
    # dropping the implied-vs-history contrast. Above ~100% it is still honestly
    # "no feasible solution" (the price is outside any defensible growth range).
    "revenue_cagr_5y": (-0.10, 1.00),
    "terminal_growth": (0.000, 0.060),
    "terminal_fcf_margin": (-0.20, 0.60),
    "wacc": (0.040, 0.250),
}


def reverse_solve(target_price: float, base: Assumptions, solve_for: str, data: CompanyData):
    lo, hi = SEARCH_RANGES[solve_for]

    def diff(x):
        a = replace(base, **{solve_for: x})
        return dcf_equity_value_per_share(a, data) - target_price

    try:
        f_lo, f_hi = diff(lo), diff(hi)
        if f_lo * f_hi > 0:
            return None  # no root in range
        return brentq(diff, lo, hi)
    except (ValueError, ZeroDivisionError):
        return None


def monte_carlo_implied(
    data: CompanyData,
    solve_for: str,
    base: Assumptions,
    perturbations: dict,
    n: int = 500,
    seed: int = 42,
):
    rng = np.random.default_rng(seed)
    results = []
    for _ in range(n):
        perturbed_kwargs = {}
        for k, (lo, hi) in perturbations.items():
            if k == solve_for:
                continue
            perturbed_kwargs[k] = float(rng.uniform(lo, hi))
        perturbed = replace(base, **perturbed_kwargs)
        x = reverse_solve(data.current_price, perturbed, solve_for, data)
        if x is not None:
            results.append(x)

    if not results:
        return None

    arr = np.array(results)
    return {
        "p25": float(np.percentile(arr, 25)),
        "p50": float(np.percentile(arr, 50)),
        "p75": float(np.percentile(arr, 75)),
        "samples": len(results),
        "success_rate": len(results) / n,
    }


# ===========================================================================
# Intelligence-layer helpers (consumed by intelligence.py / decoder._lens_dcf).
# These let the reverse-solve answer questions richer than "what ONE growth rate
# justifies the price?": how many YEARS of moat the price buys (implied CAP), what
# PROBABILITY the market assigns each scenario, and which driver is LOAD-BEARING.
# Pure functions over the SAME DCF math above — no new valuation model.
# ===========================================================================

def implied_scenario_probabilities(values, price):
    """Invert one price into a max-entropy probability simplex over scenario values.

    A price is a probability-weighted expected value: price = Σ pᵢ·vᵢ. With one
    price (one mean constraint) and ≥2 free probabilities the system is
    UNDERDETERMINED, so we close it with the maximum-entropy principle — the least
    committal distribution consistent with the price: pᵢ ∝ exp(λ·vᵢ), λ from a 1-D
    root-find (the mean is monotonic in λ).

    Returns (probs: list[float] | None, note). None when the price is OUTSIDE the
    [min,max] of the scenario values — itself the signal that the price is beyond
    even the most bullish modelled scenario ("above_top") or below the bear
    ("below_bottom")."""
    v = np.array([float(x) for x in values], dtype=float)
    if v.size < 2:
        return None, "need_2_scenarios"
    vmin, vmax = float(v.min()), float(v.max())
    if not (vmin < price < vmax):
        return None, ("above_top" if price >= vmax else "below_bottom")
    span = vmax - vmin

    def mean_at(lmbda):
        z = lmbda * (v - vmin) / span
        z = z - z.max()                 # numerical stabilization
        w = np.exp(z)
        w = w / w.sum()
        return float((w * v).sum())

    try:
        lam = brentq(lambda l: mean_at(l) - price, -200.0, 200.0, xtol=1e-8)
    except (ValueError, ZeroDivisionError):
        return None, "no_solution"
    z = lam * (v - vmin) / span
    z = z - z.max()
    w = np.exp(z)
    w = w / w.sum()
    return [float(x) for x in w], "ok"


def implied_cap_years(data: CompanyData, sustained_growth: float,
                      margin: float, wacc: float, *, max_years: int = 30) -> float | None:
    """Market-implied Competitive Advantage Period (MICAP): how many years of
    `sustained_growth` (at consensus margin/WACC, then a GDP-growth terminal) the
    current price requires. Solve for the horizon N where the DCF value reconciles
    to the price — the DURATION analogue of the implied-growth reverse-solve
    ("how long?" instead of "how fast?").

    Returns a float year count (interpolated between the bracketing integers), or
    None when the price is below the 1-year value or above the max_years value
    (outside the modellable range)."""
    if (sustained_growth is None or sustained_growth <= TERMINAL_GROWTH
            or wacc <= TERMINAL_GROWTH):
        return None
    a = Assumptions(revenue_cagr_5y=sustained_growth, terminal_growth=TERMINAL_GROWTH,
                    terminal_fcf_margin=margin, wacc=wacc)
    price = data.current_price
    prev_v = None
    for n in range(1, max_years + 1):
        v = dcf_equity_value_per_share(a, data, years=n)
        if v is None or v < -1e8:
            return None
        if v >= price:
            if n == 1 or prev_v is None:
                return float(n)
            frac = (price - prev_v) / (v - prev_v) if v != prev_v else 0.0
            return round((n - 1) + frac, 1)
        prev_v = v
    return None  # price needs > max_years of this growth → off the chart


_DRIVER_LABELS = {
    "revenue_cagr_5y": "营收增速", "terminal_fcf_margin": "FCF 利润率",
    "wacc": "贴现率 (WACC)", "terminal_growth": "终值增速",
}


def rank_driver_elasticity(base: Assumptions, data: CompanyData,
                           rel_bump: float = 0.02) -> list[dict]:
    """At the price-justifying assumption point, rank value drivers by ELASTICITY —
    the % change in per-share value per % change in each driver. The largest
    |elasticity| is the LOAD-BEARING ('turbo-trigger') driver the price is really
    betting on. Replaces the arbitrary fixed-range Monte-Carlo perturbation with a
    company-specific statement of WHICH assumption matters most.

    Returns [{driver, label, elasticity}, ...] sorted by |elasticity| desc."""
    v0 = dcf_equity_value_per_share(base, data)
    if v0 is None or abs(v0) < 1e-9 or v0 < -1e8:
        return []
    out = []
    for drv in ("revenue_cagr_5y", "terminal_fcf_margin", "wacc", "terminal_growth"):
        cur = getattr(base, drv)
        if cur is None:
            continue
        bumped = cur * (1.0 + rel_bump) if cur != 0 else rel_bump
        a1 = replace(base, **{drv: bumped})
        v1 = dcf_equity_value_per_share(a1, data)
        if v1 is None or v1 < -1e8:
            continue
        denom = (bumped - cur) / cur if cur != 0 else rel_bump
        if denom == 0:
            continue
        elasticity = ((v1 - v0) / v0) / denom
        out.append({"driver": drv, "label": _DRIVER_LABELS.get(drv, drv),
                    "elasticity": round(float(elasticity), 2)})
    out.sort(key=lambda d: abs(d["elasticity"]), reverse=True)
    return out


def main(ticker: str):
    print(f"=== Bet Decoder reverse DCF · {ticker} ===\n")
    print("Pulling data from yfinance...")
    data = pull_company_data(ticker)

    print(f"Current price:    ${data.current_price:,.2f}")
    print(f"TTM revenue:      ${data.revenue_ttm/1e9:,.2f}B")
    print(f"TTM FCF:          ${data.fcf_ttm/1e9:,.2f}B")
    print(f"Shares out:       {data.shares_outstanding/1e9:.2f}B")
    print(f"Net debt:         ${data.net_debt/1e9:,.2f}B")
    print(f"Beta:             {data.beta:.2f}")

    consensus_wacc = compute_wacc(data.beta)
    consensus = Assumptions(
        revenue_cagr_5y=0.15,
        terminal_growth=0.025,
        terminal_fcf_margin=max(data.fcf_ttm / data.revenue_ttm, 0.05) if data.revenue_ttm else 0.15,
        wacc=consensus_wacc,
    )

    print(
        f"\nConsensus baseline: growth={consensus.revenue_cagr_5y:.1%}, "
        f"margin={consensus.terminal_fcf_margin:.1%}, WACC={consensus.wacc:.1%}, "
        f"terminal={consensus.terminal_growth:.1%}"
    )
    baseline_price = dcf_equity_value_per_share(consensus, data)
    print(f"Baseline DCF price: ${baseline_price:,.2f}  (actual: ${data.current_price:,.2f})")

    perturbations = {
        "revenue_cagr_5y": (0.05, 0.30),
        "terminal_growth": (0.015, 0.035),
        "terminal_fcf_margin": (consensus.terminal_fcf_margin * 0.6, consensus.terminal_fcf_margin * 1.4),
        "wacc": (consensus.wacc - 0.015, consensus.wacc + 0.015),
    }

    print("\n--- Point-estimate reverse solve ---")
    for var in ["revenue_cagr_5y", "terminal_fcf_margin", "wacc"]:
        x = reverse_solve(data.current_price, consensus, var, data)
        if x is None:
            print(f"  {var:25s}: NO SOLUTION (price outside feasible range)")
        else:
            print(f"  {var:25s}: {x:.2%}")

    print("\n--- Monte Carlo interval estimate (G2, n=500) ---")
    for var in ["revenue_cagr_5y", "terminal_fcf_margin", "wacc"]:
        result = monte_carlo_implied(data, var, consensus, perturbations)
        if result is None:
            print(f"  {var:25s}: NO SOLUTION")
            continue
        print(
            f"  {var:25s}: [{result['p25']:.1%}  ..  {result['p50']:.1%}  ..  {result['p75']:.1%}]"
            f"  (n={result['samples']}, success={result['success_rate']:.0%})"
        )


if __name__ == "__main__":
    ticker = sys.argv[1] if len(sys.argv) > 1 else "NVDA"
    main(ticker)
