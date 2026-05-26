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


def compute_wacc(
    beta: float,
    risk_free: float = 0.045,
    equity_premium: float = 0.055,
    beta_cap: float = 1.5,
) -> float:
    effective_beta = min(max(beta, 0.5), beta_cap)
    return risk_free + effective_beta * equity_premium


def pull_analyst_consensus(ticker: str) -> dict:
    """Best-effort analyst consensus from yfinance. All keys optional, None if missing."""
    result = {
        "revenue_growth_5y_consensus": None,
        "eps_growth_5y_consensus": None,
        "target_mean_price": None,
        "recommendation_mean": None,
    }
    try:
        info = yf.Ticker(ticker).info
    except Exception:
        return result

    def _get_float(key):
        try:
            val = info.get(key)
            if val is None:
                return None
            f = float(val)
            if np.isnan(f):
                return None
            return f
        except (TypeError, ValueError):
            return None

    result["revenue_growth_5y_consensus"] = _get_float("revenueGrowth")
    result["eps_growth_5y_consensus"] = _get_float("earningsGrowth")
    result["target_mean_price"] = _get_float("targetMeanPrice")
    result["recommendation_mean"] = _get_float("recommendationMean")
    return result


def compute_historical_context(data: "CompanyData", years: int = 5) -> dict:
    """Backward-looking context for narrator comparison baselines."""
    result = {
        "revenue_cagr_5y_actual": None,
        "fcf_margin_5y_avg": None,
        "fcf_margin_ttm": 0.0,
        "years_of_data": 0,
    }

    # TTM FCF margin from already-pulled data
    if data.revenue_ttm and data.revenue_ttm > 0:
        result["fcf_margin_ttm"] = data.fcf_ttm / data.revenue_ttm

    try:
        t = yf.Ticker(data.ticker)
        financials = t.financials
        cashflow = t.cashflow
    except Exception:
        return result

    # Extract revenue series
    revenue_series = None
    for name in ("Total Revenue", "TotalRevenue"):
        if name in financials.index:
            revenue_series = financials.loc[name]
            break

    # Extract OCF + CapEx series
    ocf_series = None
    capex_series = None
    for name in ("Operating Cash Flow", "Total Cash From Operating Activities"):
        if name in cashflow.index:
            ocf_series = cashflow.loc[name]
            break
    for name in ("Capital Expenditure", "Capital Expenditures"):
        if name in cashflow.index:
            capex_series = cashflow.loc[name]
            break

    # Revenue CAGR
    if revenue_series is not None:
        vals = []
        for v in revenue_series.tolist()[:years]:
            try:
                f = float(v)
                if not np.isnan(f) and f > 0:
                    vals.append(f)
            except (TypeError, ValueError):
                continue
        result["years_of_data"] = len(vals)
        if len(vals) >= 2:
            # yfinance returns most-recent first; oldest is last
            latest = vals[0]
            oldest = vals[-1]
            n_periods = len(vals) - 1
            if oldest > 0 and latest > 0 and n_periods > 0:
                try:
                    cagr = (latest / oldest) ** (1.0 / n_periods) - 1.0
                    if not np.isnan(cagr):
                        result["revenue_cagr_5y_actual"] = float(cagr)
                except (ValueError, ZeroDivisionError):
                    pass

    # FCF margin average
    if revenue_series is not None and ocf_series is not None and capex_series is not None:
        margins = []
        n = min(len(revenue_series), len(ocf_series), len(capex_series), years)
        for i in range(n):
            try:
                rev = float(revenue_series.iloc[i])
                ocf = float(ocf_series.iloc[i])
                capex = float(capex_series.iloc[i])
                if np.isnan(rev) or np.isnan(ocf) or np.isnan(capex):
                    continue
                if rev <= 0:
                    continue
                fcf = ocf + capex  # capex signed negative
                margins.append(fcf / rev)
            except (TypeError, ValueError):
                continue
        if margins:
            result["fcf_margin_5y_avg"] = float(sum(margins) / len(margins))

    return result


def format_historical_context_md(context: dict, consensus: dict | None = None) -> str:
    """Format as markdown bullets for the {{HISTORICAL_CONTEXT}} prompt placeholder."""
    lines = []

    cagr = context.get("revenue_cagr_5y_actual")
    years = context.get("years_of_data") or 0
    if cagr is not None and years >= 2:
        # year range: assume most recent fiscal year ≈ current year - 1
        from datetime import datetime as _dt
        end_year = _dt.now().year - 1
        start_year = end_year - (years - 1)
        lines.append(f"- 过去 {years} 年({start_year}-{end_year})营收 CAGR ≈ {cagr * 100:.0f}%")

    fcf_avg = context.get("fcf_margin_5y_avg")
    fcf_ttm = context.get("fcf_margin_ttm")
    if fcf_avg is not None and fcf_ttm is not None:
        lines.append(
            f"- 过去 {years or 5} 年 FCF margin 均值 ≈ {fcf_avg * 100:.0f}%,当前 TTM = {fcf_ttm * 100:.1f}%"
        )
    elif fcf_ttm is not None:
        lines.append(f"- 当前 TTM FCF margin = {fcf_ttm * 100:.1f}%")

    if consensus:
        target = consensus.get("target_mean_price")
        if target is not None:
            lines.append(f"- 卖方目标价中位数 = ${target:.0f}")
        rec = consensus.get("recommendation_mean")
        if rec is not None:
            lines.append(f"- 卖方推荐均值 = {rec:.1f}(1=Strong Buy / 5=Strong Sell)")
        rev_g = consensus.get("revenue_growth_5y_consensus")
        if rev_g is not None:
            lines.append(f"- 卖方一致营收增速预期 ≈ {rev_g * 100:.1f}%")
        eps_g = consensus.get("eps_growth_5y_consensus")
        if eps_g is not None:
            lines.append(f"- 卖方一致 EPS 增速预期 ≈ {eps_g * 100:.1f}%")

    return "\n".join(lines) if lines else "- (历史数据与卖方共识暂无)"


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
