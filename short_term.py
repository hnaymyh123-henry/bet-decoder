"""PriceLens short-term attribution (W3).

Decompose a stock's N-day price move into factor contributions:
  1. fundamental_update     (consensus / target-price gap × decay)
  2. flow_positioning        (sector ETF excess return → stock-specific flow)
  3. unexplained             (residual)

Honesty rule (PRD §7.2): if data is missing, contribution_pct = 0 and
evidence_text explicitly states "数据不可用" — never fabricate.
"""
from datetime import datetime, timezone

import yfinance as yf


# Map ticker → sector ETF for flow attribution.
# Defaults to SPY (broad market) when unmapped.
SECTOR_ETF_MAP = {
    "NVDA": "SOXX",   # semiconductors
    "TSLA": "XLY",    # consumer discretionary
    "COST": "XRT",    # retail
    "AAPL": "XLK",    # tech
    "MSFT": "XLK",
    "GOOGL": "XLC",
    "META": "XLC",
    "AMZN": "XLY",
}


def _safe_window_return(ticker: str, window_days: int):
    """Return (price_start, price_end, return_pct, start_date, end_date) or None on failure."""
    try:
        hist = yf.Ticker(ticker).history(period=f"{window_days + 5}d")
        if hist is None or len(hist) < 2:
            return None
        # Use the last `window_days + 1` rows (so N-day return spans N+1 closes).
        # If we have fewer rows, use whatever we have.
        rows = hist.tail(window_days + 1)
        if len(rows) < 2:
            return None
        price_start = float(rows["Close"].iloc[0])
        price_end = float(rows["Close"].iloc[-1])
        if price_start <= 0:
            return None
        ret = price_end / price_start - 1.0
        start_date = rows.index[0].strftime("%Y-%m-%d")
        end_date = rows.index[-1].strftime("%Y-%m-%d")
        return price_start, price_end, ret, start_date, end_date
    except Exception:
        return None


def _fundamental_factor(ticker: str) -> dict:
    """Factor 1: fundamental update.

    yfinance rarely exposes intra-window EPS estimate deltas, so we use a
    simplified gap proxy: (targetMeanPrice / currentPrice - 1) × decay coefficient.
    The decay (0.1) reflects that analyst target gaps are slow-moving signals
    and a 5d window only realizes a fraction.
    """
    factor = {
        "name": "fundamental_update",
        "label": "基本面修正 / Fundamental update",
        "contribution_pct": 0.0,
        "evidence_text": "无可用 EPS 修正或目标价数据 — yfinance 未返回 targetMeanPrice / currentPrice。",
        "raw_data": {},
    }
    try:
        info = yf.Ticker(ticker).info or {}
        target = info.get("targetMeanPrice")
        current = info.get("currentPrice") or info.get("regularMarketPrice")
        beta = info.get("beta") or 1.0
        if target and current and current > 0:
            gap = float(target) / float(current) - 1.0
            decay = 0.10  # 5d window only realizes ~10% of a slow-moving consensus gap
            contribution = gap * decay
            factor["contribution_pct"] = round(contribution, 4)
            factor["evidence_text"] = (
                f"Sell-side 共识目标价 ${float(target):.2f} vs 现价 ${float(current):.2f} "
                f"→ 隐含 gap {gap:+.2%},按 5d 衰减系数 0.10 → 贡献 {contribution:+.2%}"
            )
            factor["raw_data"] = {
                "target_mean_price": float(target),
                "current_price": float(current),
                "implied_gap": round(gap, 4),
                "decay_coef": decay,
                "beta": float(beta),
            }
    except Exception as e:
        factor["evidence_text"] = f"基本面数据拉取异常: {type(e).__name__} — 数据不可用,贡献设为 0。"
    return factor


def _flow_factor(ticker: str, stock_return: float, window_days: int) -> dict:
    """Factor 2: flow & positioning.

    Sector ETF same-window return represents broad sector flow. The stock's
    excess return over its sector is the stock-specific flow component.
    """
    sector_etf = SECTOR_ETF_MAP.get(ticker.upper(), "SPY")
    factor = {
        "name": "flow_positioning",
        "label": "持仓变动 / Flow & positioning",
        "contribution_pct": 0.0,
        "evidence_text": f"Sector ETF ({sector_etf}) 数据不可用,无法计算超额收益 — 贡献设为 0。",
        "raw_data": {"sector_etf": sector_etf},
    }
    sector_data = _safe_window_return(sector_etf, window_days)
    if sector_data is None:
        return factor
    _, _, sector_ret, _, _ = sector_data
    excess = stock_return - sector_ret
    factor["contribution_pct"] = round(excess, 4)
    factor["evidence_text"] = (
        f"Sector ETF ({sector_etf}) {window_days}d 内 {sector_ret:+.2%},"
        f"股票 {window_days}d 内 {stock_return:+.2%},"
        f"残差(stock-specific flow) {excess:+.2%}"
    )
    factor["raw_data"] = {
        "sector_etf": sector_etf,
        "sector_etf_return": round(sector_ret, 4),
        "stock_return": round(stock_return, 4),
        "stock_excess_return": round(excess, 4),
    }
    return factor


def compute_short_term_attribution(ticker: str, window_days: int = 5) -> dict:
    """Decompose stock's N-day price move into factor contributions."""
    ticker_upper = ticker.upper()
    window = _safe_window_return(ticker_upper, window_days)
    if window is None:
        # Hard failure: no price history. Return a structured "data unavailable" payload.
        return {
            "ticker": ticker_upper,
            "window_days": window_days,
            "start_date": None,
            "end_date": None,
            "price_start": None,
            "price_end": None,
            "return_pct": 0.0,
            "factors": [
                {
                    "name": "fundamental_update",
                    "label": "基本面修正 / Fundamental update",
                    "contribution_pct": 0.0,
                    "evidence_text": "无价格历史 — 数据不可用。",
                    "raw_data": {},
                },
                {
                    "name": "flow_positioning",
                    "label": "持仓变动 / Flow & positioning",
                    "contribution_pct": 0.0,
                    "evidence_text": "无价格历史 — 数据不可用。",
                    "raw_data": {},
                },
                {
                    "name": "unexplained",
                    "label": "不可解释残差 / Unexplained residual",
                    "contribution_pct": 0.0,
                    "evidence_text": "无价格历史 — 数据不可用。",
                    "raw_data": {},
                },
            ],
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    price_start, price_end, return_pct, start_date, end_date = window

    factor_fund = _fundamental_factor(ticker_upper)
    factor_flow = _flow_factor(ticker_upper, return_pct, window_days)

    explained = factor_fund["contribution_pct"] + factor_flow["contribution_pct"]
    residual = round(return_pct - explained, 4)
    factor_unexplained = {
        "name": "unexplained",
        "label": "不可解释残差 / Unexplained residual",
        "contribution_pct": residual,
        "evidence_text": (
            "未被上述因子归因的部分 — 可能来自新闻、流动性或其他未观察因子。"
        ),
        "raw_data": {
            "total_return": round(return_pct, 4),
            "explained_sum": round(explained, 4),
        },
    }

    return {
        "ticker": ticker_upper,
        "window_days": window_days,
        "start_date": start_date,
        "end_date": end_date,
        "price_start": round(price_start, 4),
        "price_end": round(price_end, 4),
        "return_pct": round(return_pct, 4),
        "factors": [factor_fund, factor_flow, factor_unexplained],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
