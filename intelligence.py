"""intelligence.py — the X-RAY layer that turns a reverse-DCF NUMBER into an
analyst-grade JUDGMENT.

The reverse DCF answers "what ONE growth rate justifies the price?" — a formula.
A real PM, given that number, runs an investigation (Expectations Investing,
Rappaport & Mauboussin): read the implied expectation, judge it against base
rates + scenarios, decide on the MISMATCH. This module produces exactly that
judgment layer, as one `xray` block attached to the DCF lens:

  • base_rate          — how rare is the implied number? (outside view; base_rates.py)
  • scenarios + probs  — invert the price into a max-entropy scenario probability simplex
  • driver_elasticity  — which assumption is LOAD-BEARING (this is a margin bet, not a growth bet)
  • implied_cap        — how many YEARS of moat the price buys (MICAP)
  • wwhtbt             — "what would have to be true" + a falsifiable KILL line
  • kelly              — the risk/reward ASYMMETRY read at the price (upside vs downside; no position size)

Pure compute (yfinance data already pulled; reverse_dcf math reused) — no LLM,
no network, so it never adds decode cost.
"""
from __future__ import annotations

import base_rates
import reverse_dcf as rdcf

# Fixed, named, interpretable scenario grid (revenue CAGR). Bear/base/bull are
# defensible anchors; moonshot brackets the AI-extreme names so the price usually
# falls INSIDE the envelope (a real simplex) rather than off the end.
_SCENARIO_GRID = [
    ("bear", "熊市", 0.05, "需求降温 / 份额流失"),
    ("base", "基准", 0.15, "行业长期增速"),
    ("bull", "牛市", 0.30, "持续领先"),
    ("moonshot", "登月", 0.50, "颠覆性主导"),
]


def _scenario_values(data: rdcf.CompanyData, margin: float, wacc: float) -> list[dict]:
    out = []
    for key, zh, g, why in _SCENARIO_GRID:
        a = rdcf.Assumptions(revenue_cagr_5y=g, terminal_growth=rdcf.TERMINAL_GROWTH,
                             terminal_fcf_margin=margin, wacc=wacc)
        v = rdcf.dcf_equity_value_per_share(a, data)
        out.append({"name": key, "name_zh": zh, "growth": g, "why": why,
                    "value": float(v) if isinstance(v, (int, float)) else None})
    return out


def _scenario_probs(scenarios: list[dict], price: float) -> dict:
    vals = [s["value"] for s in scenarios if isinstance(s.get("value"), (int, float))]
    if len(vals) < 2:
        return {"weights": None, "note": "insufficient_scenarios"}
    probs, note = rdcf.implied_scenario_probabilities(vals, price)
    by_name = {}
    if probs is not None:
        usable = [s for s in scenarios if isinstance(s.get("value"), (int, float))]
        by_name = {usable[i]["name"]: round(probs[i], 4) for i in range(len(usable))}
    return {"weights": probs, "by_name": by_name, "note": note}


def _kelly(scenarios: list[dict], probs: dict, price: float) -> dict | None:
    """Risk/reward ASYMMETRY read at the current price — deliberately NOT a position
    size. From the scenario values + their implied probabilities it reads how much
    probability sits above the price and how lopsided upside vs downside is. It
    describes the SHAPE of the risk/reward; it does not recommend or size a trade."""
    w = probs.get("weights")
    usable = [s for s in scenarios if isinstance(s.get("value"), (int, float))]
    by_name = probs.get("by_name") or {}

    def _sv(s):  # compact scenario projection the frontend renders the derivation from
        return {"name_zh": s["name_zh"], "value": round(s["value"], 2),
                "prob": by_name.get(s["name"])}

    # vmax / vmin scenarios always exist when there are ≥1 valued scenarios — they
    # drive the 上行 / 下行 lines and the above_top note even when no simplex solves.
    top_s = max(usable, key=lambda s: s["value"]) if usable else None
    bottom_s = min(usable, key=lambda s: s["value"]) if usable else None
    _ends = ({"top": {"name_zh": top_s["name_zh"], "value": round(top_s["value"], 2)},
              "bottom": {"name_zh": bottom_s["name_zh"], "value": round(bottom_s["value"], 2)}}
             if top_s and bottom_s else {})

    if not w or len(w) != len(usable):
        # Out-of-envelope: the price sits above every defensible scenario value.
        if probs.get("note") == "above_top" and _ends:
            return {"verdict": "above_top", "p_win": 0.0,
                    "statement_zh": "现价高于所有可辩护情景估值 —— 没有情景能把现价拉回内在价值之上,风险回报极不对称。",
                    "derivation": {"price": round(price, 2), **_ends,
                                   "note_zh": (f"现价 ${price:,.0f} 高于所有情景估值 —— 最高情景「{top_s['name_zh']}」"
                                               f"也只值 ${top_s['value']:,.0f}。没有任何建模情景能把现价拉回内在价值之上,"
                                               "风险回报极不对称。")}}
        return None
    p_win = sum(w[i] for i, s in enumerate(usable) if s["value"] > price)
    vmax = max(s["value"] for s in usable)
    vmin = min(s["value"] for s in usable)
    upside = (vmax - price) / price if price else 0.0
    downside = (price - vmin) / price if price else 0.0
    if downside <= 0 or upside <= 0:
        return {"verdict": "degenerate", "p_win": round(p_win, 3),
                "statement_zh": "情景区间退化,无法给出风险回报不对称读数。",
                "derivation": {"price": round(price, 2), "p_win": round(p_win, 3), **_ends,
                               "note_zh": "情景区间退化(上行或下行 ≤ 0),无法给出风险回报不对称读数。"}}
    ratio = upside / downside
    favorable = ratio >= 1.0
    if favorable:
        shape = "小概率大涨、大概率小跌 —— 风险回报偏有利"
    elif p_win >= 0.5:
        shape = "大概率小涨、小概率大跌 —— 风险回报不对称(下行大于上行)"
    else:
        shape = "上行有限、下行更大 —— 风险回报不对称"
    above_list = [s for s in usable if s["value"] > price]
    below_list = sorted((s for s in usable if s["value"] <= price), key=lambda s: s["value"])
    return {
        "verdict": "favorable" if favorable else "unfavorable",
        "p_win": round(p_win, 3),
        "upside": round(upside, 3),
        "downside": round(downside, 3),
        "ratio": round(ratio, 2),
        "statement_zh": (f"现价之上的情景占 {p_win*100:.0f}% 概率,"
                         f"上行 +{upside*100:.0f}% / 下行 −{downside*100:.0f}%"
                         f" —— {shape}。"),
        # derivation: every displayed number's operands, so the frontend can print
        # the arithmetic (= ... ) with real values instead of bare results.
        "derivation": {
            "price": round(price, 2),
            "above": [_sv(s) for s in above_list],   # value > price → these sum into p_win
            "below": [_sv(s) for s in below_list],
            **_ends,                                  # top (vmax) drives 上行, bottom (vmin) drives 下行
        },
    }


def _wwhtbt(implied_cagr, implied_rev_5y, implied_market_share, margin,
            base_rate) -> list[dict]:
    """'What would have to be true' decomposed into checkable conditions + a
    falsifiable KILL line. Deterministic from the implied numbers — no LLM."""
    items: list[dict] = []
    if isinstance(implied_rev_5y, (int, float)):
        items.append({"kind": "target",
                      "label": "5 年后营收须达到",
                      "value": f"${implied_rev_5y/1e9:,.0f}B"})
    if isinstance(implied_market_share, (int, float)) and implied_market_share > 0:
        if implied_market_share <= 1.0:
            items.append({"kind": "target",
                          "label": "隐含行业市占须达到",
                          "value": f"{implied_market_share*100:.0f}%"})
        else:
            # >100% market share is impossible — the implied revenue exceeds the
            # (coarse, hardcoded) sector TAM. Honest flag instead of a fake number.
            items.append({"kind": "flag",
                          "label": "隐含营收已超行业 TAM",
                          "value": (f"达 TAM 的 {implied_market_share*100:.0f}% → "
                                    f"现有赛道框架解释不了(TAM 需重设)")})
    if isinstance(margin, (int, float)):
        items.append({"kind": "floor",
                      "label": "FCF 利润率须维持 ≥",
                      "value": f"{margin*100:.0f}%"})
    # KILL line: tie to the empirical base-rate median — if growth reverts to the
    # typical large-cap rate, the implied premium is falsified.
    summ = base_rates.distribution_summary()
    median = summ.get("median")
    if isinstance(implied_cagr, (int, float)) and isinstance(median, (int, float)):
        items.append({"kind": "kill",
                      "label": "证伪线 (KILL)",
                      "value": (f"实际营收增速连续 2 季跌破大盘中位 {median}% "
                                f"(隐含要 {implied_cagr*100:.0f}%) → 论点破")})
    return items


def _verdict(ticker, br, probs, load_bearing, scenarios) -> str:
    """One-sentence decode verdict — synthesizes rarity (base rate) + market
    conviction (scenario probs) + nature (load-bearing driver) into the punchline
    a PM would lead with. This is the logical spine the rest of the card supports."""
    # rarity (outside view)
    if br:
        v, pct, share = br["verdict"], br["live"]["percentile"], br["live"]["share_ge_pct"]
        if v == "top-tail":
            rarity = (f"要求 {ticker} 跑出历史前 {max(1, 100 - pct)}%(仅 {share}% 同行"
                      f"曾做到)的极端增速")
        elif v == "above-median":
            rarity = f"要求 {ticker} 跑出高于行业中位(第 {pct} 分位)的增速"
        else:
            rarity = f"只要求 {ticker} 跑出低于中位的温和增速"
    else:
        rarity = f"对 {ticker} 的定价已高到 DCF 无法解释(纯叙事)"
    # market conviction (implied probabilities)
    note = probs.get("note")
    bn = probs.get("by_name") or {}
    if note == "above_top":
        conviction = "现价更冲出了我们最激进的 moonshot 情景 —— 市场在押一个尚未建模的更狂热故事"
    elif bn:
        top = max(bn, key=bn.get)
        zh = next((s["name_zh"] for s in scenarios if s["name"] == top), top)
        bull_share = (bn.get("bull", 0) + bn.get("moonshot", 0)) * 100
        if top in ("moonshot", "bull"):
            conviction = f"市场已把 {bn[top]*100:.0f}% 概率压在「{zh}」情景上(信心拥挤)"
        else:
            conviction = f"但市场自身只给 {bull_share:.0f}% 信心在牛市以上(并不狂热)"
    else:
        conviction = ""
    nature = f";本质是一个「{load_bearing['label']}」bet" if load_bearing else ""
    sep = "," if conviction else ""
    return f"市场{rarity}{sep}{conviction}{nature}。"


def build_xray(*, data: rdcf.CompanyData, fundamentals, implied_cagr,
               base_margin: float, wacc: float, sustained_growth=None,
               implied_rev_5y=None, implied_market_share=None) -> dict:
    """Assemble the full X-RAY judgment block for a DCF-decoded bet. Safe on
    missing pieces (each sub-read degrades to None). Never raises."""
    price = data.current_price

    # 1) base rate (outside view)
    try:
        br = base_rates.percentile_of(implied_cagr, getattr(data, "revenue_ttm", None))
    except Exception:
        br = None

    # 2) scenarios + max-entropy implied probabilities
    scenarios = _scenario_values(data, base_margin, wacc)
    probs = _scenario_probs(scenarios, price)
    top_name = max(probs.get("by_name") or {}, key=(probs.get("by_name") or {}).get, default=None)
    scen_statement = None
    if probs.get("note") == "above_top":
        moon = next((s for s in scenarios if s["name"] == "moonshot"), None)
        g = f"{moon['growth']*100:.0f}%" if moon else "最高"
        scen_statement = (f"现价高于 moonshot({g} 增速) 的 DCF 估值 → 市场在为一个比我们最"
                          f"激进情景还乐观的故事定价")
    elif probs.get("note") == "below_bottom":
        scen_statement = "现价低于 bear 情景估值 → 市场定价比最悲观假设还差(深度低估?)"
    elif top_name:
        bn = probs["by_name"]
        zh = next((s["name_zh"] for s in scenarios if s["name"] == top_name), top_name)
        scen_statement = f"市场把 ~{bn[top_name]*100:.0f}% 的概率压在「{zh}」情景上"

    # 3) driver elasticity (load-bearing assumption)
    base_assump = rdcf.Assumptions(
        revenue_cagr_5y=(implied_cagr if isinstance(implied_cagr, (int, float)) else 0.15),
        terminal_growth=rdcf.TERMINAL_GROWTH, terminal_fcf_margin=base_margin, wacc=wacc)
    elas = rdcf.rank_driver_elasticity(base_assump, data)
    # The "this is a ___ bet" headline ranks only OPERATING drivers (growth vs
    # margin) — a DCF terminal value is always most sensitive to WACC, so including
    # the discount rate would make every name "a WACC bet" (true but uninformative).
    # The analyst's variant view is about the operating triggers (Mauboussin's value
    # factors), so that's what the headline compares. The full table (incl. WACC)
    # still rides on `driver_elasticity` for the detail view.
    _OP = ("revenue_cagr_5y", "terminal_fcf_margin")
    op = [e for e in elas if e["driver"] in _OP]
    load_bearing = None
    if len(op) >= 2 and abs(op[1]["elasticity"]) > 1e-6:
        ratio = abs(op[0]["elasticity"]) / abs(op[1]["elasticity"])
        load_bearing = {
            **op[0], "ratio_vs_next": round(ratio, 1),
            "statement_zh": (f"价格对「{op[0]['label']}」的敏感度是「{op[1]['label']}」的 "
                             f"{ratio:.1f}× → 这本质是一个 {op[0]['label']} bet")}
    elif op:
        load_bearing = {**op[0], "statement_zh": f"主导经营驱动:「{op[0]['label']}」"}

    # 4) implied CAP (years of moat)
    sg = sustained_growth
    if sg is None and isinstance(implied_cagr, (int, float)):
        sg = min(implied_cagr, 0.40)   # cap the sustained rate for the duration read
    cap_years = None
    try:
        cap_years = rdcf.implied_cap_years(data, sg, base_margin, wacc) if sg else None
    except Exception:
        cap_years = None
    implied_cap = None
    if sg:
        if cap_years is not None:
            implied_cap = {"years": cap_years, "sustained_growth": round(sg, 4),
                           "statement_zh": (f"市场在为 ~{cap_years:.0f} 年的 {sg*100:.0f}% "
                                            f"持续增长付费")}
        else:
            implied_cap = {"years": None, "sustained_growth": round(sg, 4),
                           "statement_zh": (f"按 {sg*100:.0f}% 持续增长,现价隐含的久期超过 30 年 "
                                            f"→ 久期假设极端")}

    # 5) WWHTBT + kill line
    wwhtbt = _wwhtbt(implied_cagr, implied_rev_5y, implied_market_share, base_margin, br)

    # 6) risk/reward asymmetry read (deliberately NOT a position size)
    kelly = _kelly(scenarios, probs, price)

    headline = (br or {}).get("headline") if br else (scen_statement or "")
    verdict = _verdict(getattr(data, "ticker", "该股"), br,
                       {**probs, "statement_zh": scen_statement}, load_bearing, scenarios)
    return {
        "verdict_zh": verdict,
        "base_rate": br,
        "scenarios": scenarios,
        "scenario_probs": {**probs, "statement_zh": scen_statement},
        "driver_elasticity": elas,
        "load_bearing": load_bearing,
        "implied_cap": implied_cap,
        "wwhtbt": wwhtbt,
        "kelly": kelly,
        "headline_zh": headline,
    }
