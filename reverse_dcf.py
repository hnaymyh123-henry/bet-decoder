"""PriceLens reverse DCF prototype.

Run: python reverse_dcf.py NVDA
Deps: pip install -r requirements.txt
"""
import sys
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


def compute_wacc(beta: float, risk_free: float = 0.045, equity_premium: float = 0.055) -> float:
    return risk_free + beta * equity_premium


def dcf_equity_value_per_share(a: Assumptions, data: CompanyData) -> float:
    years = 5
    revenue = data.revenue_ttm
    pv = 0.0
    for year in range(1, years + 1):
        revenue *= 1 + a.revenue_cagr_5y
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
    "revenue_cagr_5y": (-0.10, 0.80),
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


def main(ticker: str):
    print(f"=== PriceLens reverse DCF · {ticker} ===\n")
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
