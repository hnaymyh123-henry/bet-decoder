"""Deterministic Trade Plan and reconciliation core for PlayInsight v2.

This module is pure compute: no network, no database, and no dependency on the
frontend. It accepts v4-style panel envelopes and returns reconciliation and
decision payloads that can be stored under decode_detail.decision.
"""
from __future__ import annotations

from math import inf
from typing import Any


CHECK_NAMES = (
    "price_vs_fundamentals",
    "valuation_vs_options",
    "momentum_vs_options",
    "concentration",
)

CONVICTION_MULT = {"high": 1.0, "medium": 0.6, "low": 0.3}

ARCHETYPE_SPEC = {
    "value_accumulate": {
        "entry": {"method": "staged", "batches": [
            {"weight": 0.5, "condition": "immediate"},
            {"weight": 0.3, "condition": "pullback_5pct"},
            {"weight": 0.2, "condition": "confirmation_30d"},
        ]},
        "stop": {"method": "trailing", "trail_pct": 0.15},
        "exit": {"condition": "thesis_realized_or_edge_gone"},
    },
    "maintain": {
        "entry": {"method": "none"},
        "stop": {"method": "kill_line_only"},
        "exit": {"condition": "kill_triggered_or_thesis_broken"},
    },
    "risk_off": {
        "entry": {"method": "reduce", "reduce_pct": 0.33},
        "stop": {"method": "tighten", "trail_pct": 0.08},
        "exit": {"condition": "reduced_to_target_or_edge_returns"},
    },
    "wait": {
        "entry": {"method": "none"},
        "stop": None,
        "exit": {"condition": "edge_appears"},
    },
    "hedge_short": {
        "entry": {"method": "single", "condition": "immediate"},
        "stop": {"method": "fixed", "above_entry_pct": 0.10},
        "exit": {"condition": "euphoric_fades_or_thesis_realized"},
    },
}


def panel_by_channel(panel_list: list[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    """Index a panel list by channel, ignoring malformed entries."""
    out: dict[str, dict[str, Any]] = {}
    for panel in panel_list or []:
        if isinstance(panel, dict) and panel.get("channel"):
            out[str(panel["channel"])] = panel
    return out


def metric(panel_map: dict[str, dict[str, Any]], channel: str, path: str, default: Any = None) -> Any:
    """Read a dotted path from panel[channel].metrics."""
    cur: Any = (panel_map.get(channel) or {}).get("metrics") or {}
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def _panel_metrics(panel_map: dict[str, dict[str, Any]], channel: str) -> dict[str, Any]:
    return (panel_map.get(channel) or {}).get("metrics") or {}


def signal(panel_map: dict[str, dict[str, Any]], channel: str, default: str = "unknown") -> str:
    """Return explicit signal or derive a conservative one from channel metrics."""
    panel = panel_map.get(channel) or {}
    explicit = panel.get("signal") or (panel.get("metrics") or {}).get("signal")
    if explicit:
        return str(explicit)

    metrics = panel.get("metrics") or {}
    if channel == "cashflow":
        anchor = metrics.get("anchor_price") or metrics.get("current_price") or metrics.get("spot")
        low = metrics.get("baseline_low") or metric(panel_map, channel, "band.p25")
        high = metrics.get("baseline_high") or metric(panel_map, channel, "band.p75")
        baseline = metrics.get("baseline") or metric(panel_map, channel, "band.p50")
        if metrics.get("point_solved") is False:
            return "no_solution"
        if isinstance(anchor, (int, float)) and isinstance(baseline, (int, float)) and baseline > anchor:
            return "undervalued"
        if isinstance(anchor, (int, float)) and isinstance(high, (int, float)) and anchor > high:
            return "overvalued"
        if isinstance(anchor, (int, float)) and isinstance(low, (int, float)) and isinstance(high, (int, float)):
            if low <= anchor <= high:
                return "fair"
    if channel == "distribution":
        skew_pct = metric(panel_map, channel, "skew.percentile_1y")
        call_skew = metrics.get("call_skew")
        if call_skew == "inverted" or metrics.get("euphoric") is True:
            return "euphoric"
        if isinstance(skew_pct, (int, float)) and skew_pct > 0.8:
            return "bearish"
        up = metrics.get("prob_up") or metric(panel_map, channel, "prob_of_up.prob")
        down = metrics.get("prob_down")
        if isinstance(up, (int, float)) and isinstance(down, (int, float)):
            return "bullish" if up > down else "bearish" if down > up else "neutral"
        if panel.get("status") == "honest_empty":
            return "unknown"
        return "neutral" if metrics else default
    if channel == "revision":
        est = metrics.get("est_rev_90d")
        pit = (metrics.get("data_source") or {}).get("point_in_time")
        if est is None or pit is not True:
            return "unknown"
        if est > 0.05:
            return "improving"
        if est < -0.05:
            return "deteriorating"
        return "stable"
    return default


def extract_signals(panel_list: list[dict[str, Any]] | dict[str, dict[str, Any]]) -> dict[str, str]:
    panel_map = panel_list if isinstance(panel_list, dict) else panel_by_channel(panel_list)
    return {ch: signal(panel_map, ch) for ch in ("cashflow", "distribution", "revision", "altitude")}


def _check_item(name: str, channels: list[str], verdict: str, headline: str) -> dict[str, Any]:
    return {"check": name, "channels": channels, "verdict": verdict, "headline": headline}


def run_cross_channel_checks(
    signals: dict[str, str],
    metrics: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Run the four named cross-channel checks, including honest skipped states."""
    checks: list[dict[str, Any]] = []

    rev = metrics.get("revision") or {}
    revision_ready = (
        signals.get("revision") != "unknown"
        and rev.get("est_rev_90d") is not None
        and (rev.get("data_source") or {}).get("point_in_time") is True
    )
    if not revision_ready:
        checks.append(_check_item(
            "price_vs_fundamentals", ["cashflow", "revision"], "skipped",
            "consensus point-in-time unavailable; skipped price vs fundamentals",
        ))
    elif (rev.get("price_rev_90d") or 0.0) - rev["est_rev_90d"] > 0.10:
        checks.append(_check_item(
            "price_vs_fundamentals", ["cashflow", "revision"], "divergent",
            "price has run ahead of estimates; likely positioning-driven",
        ))

    cf = signals.get("cashflow", "unknown")
    dist = signals.get("distribution", "unknown")
    if dist == "unknown":
        checks.append(_check_item(
            "valuation_vs_options", ["cashflow", "distribution"], "skipped",
            "options distribution unavailable; skipped valuation vs options",
        ))
    elif cf == "overvalued" and dist == "euphoric":
        checks.append(_check_item(
            "valuation_vs_options", ["cashflow", "distribution"], "contradiction",
            "overvalued cashflow signal plus euphoric options pricing",
        ))
    elif cf == "overvalued" and dist == "neutral":
        checks.append(_check_item(
            "valuation_vs_options", ["cashflow", "distribution"], "divergent",
            "overvalued cashflow signal but options are calm",
        ))

    if signals.get("revision") == "unknown" or dist == "unknown":
        checks.append(_check_item(
            "momentum_vs_options", ["revision", "distribution"], "skipped",
            "revision or options signal unavailable; skipped momentum vs options",
        ))
    elif signals.get("revision") == "improving" and dist == "bearish":
        checks.append(_check_item(
            "momentum_vs_options", ["revision", "distribution"], "divergent",
            "estimates are improving but options price downside protection",
        ))

    alt = metrics.get("altitude") or {}
    company_pct = metric({"altitude": {"metrics": alt}}, "altitude", "decomposition.company_pct")
    if company_pct is None:
        checks.append(_check_item(
            "concentration", ["altitude"], "skipped",
            "altitude decomposition unavailable; skipped concentration check",
        ))
    elif company_pct < 0.30:
        checks.append(_check_item(
            "concentration", ["altitude"], "divergent",
            "most of the move is macro/theme beta rather than company-specific alpha",
        ))

    return checks


def _term_structure(signals: dict[str, str]) -> dict[str, Any]:
    dist = signals.get("distribution")
    cf = signals.get("cashflow")
    if dist in ("neutral", "unknown") and cf == "undervalued":
        return {"shape": "upward", "note": "short-term options are calm while long-term DCF is favorable", "is_contradiction": False}
    if dist in ("bearish", "euphoric") and cf in ("fair", "undervalued"):
        return {"shape": "downward", "note": "short-term options price event risk while long-term DCF is not impaired", "is_contradiction": False}
    return {"shape": "flat", "note": "near-term and long-term signals do not show a material term-structure gap", "is_contradiction": False}


def build_reconciliation(panel_list: list[dict[str, Any]]) -> dict[str, Any]:
    panel_map = panel_by_channel(panel_list)
    signals = extract_signals(panel_map)
    metrics = {ch: _panel_metrics(panel_map, ch) for ch in ("cashflow", "distribution", "revision", "altitude")}
    checks = run_cross_channel_checks(signals, metrics)
    ci = _conviction_input(checks)
    return {"checks": checks, "term_structure": _term_structure(signals), "conviction_input": ci, "signals": signals}


def _conviction_input(checks: list[dict[str, Any]]) -> dict[str, Any]:
    by_name = {c.get("check"): c for c in checks}
    divergent = contradiction = skipped = 0
    for name in CHECK_NAMES:
        verdict = (by_name.get(name) or {}).get("verdict", "aligned")
        if verdict == "divergent":
            divergent += 1
        elif verdict == "contradiction":
            contradiction += 1
        elif verdict == "skipped":
            skipped += 1
    aligned = len(CHECK_NAMES) - divergent - contradiction - skipped
    net = "contradiction" if contradiction else "divergent" if divergent else "aligned"
    return {
        "aligned_count": aligned,
        "divergent_count": divergent,
        "contradiction_count": contradiction,
        "skipped_count": skipped,
        "total_checks": len(CHECK_NAMES),
        "net_verdict": net,
    }


def compute_conviction(
    reconciliation: dict[str, Any],
    panel_list: list[dict[str, Any]] | None = None,
) -> str:
    ci = reconciliation.get("conviction_input") or {}
    divergent = ci.get("divergent_count", 0)
    contradiction = ci.get("contradiction_count", 0)
    skipped = ci.get("skipped_count", 0)
    fragile = divergent + contradiction

    if contradiction >= 1:
        conviction = "low"
    elif fragile <= 1:
        conviction = "medium" if skipped >= 2 else "high"
    elif fragile == 2:
        conviction = "medium"
    else:
        conviction = "low"

    if panel_list:
        panel_map = panel_by_channel(panel_list)
        if any((p.get("status") == "honest_empty") for p in panel_map.values()):
            conviction = _cap_conviction(conviction, "medium")
        if metric(panel_map, "cashflow", "point_solved", True) is False:
            conviction = _cap_conviction(conviction, "medium")
    return conviction


def _cap_conviction(value: str, cap: str) -> str:
    order = {"low": 0, "medium": 1, "high": 2}
    return value if order[value] <= order[cap] else cap


def determine_stance(
    reconciliation: dict[str, Any],
    conviction: str,
    distribution_signal: str,
    revision_signal: str,
    cashflow_signal: str,
    has_position: bool,
) -> str:
    nv = (reconciliation.get("conviction_input") or {}).get("net_verdict", "aligned")
    net_verdict = {"aligned": "consistent", "divergent": "divergence", "contradiction": "contradiction"}.get(nv, "unknown")

    if conviction == "low":
        return "trim" if has_position and cashflow_signal == "overvalued" else "avoid"

    if cashflow_signal == "overvalued" and distribution_signal in ("euphoric", "bearish"):
        return "trim" if has_position else "avoid"

    downgrade = revision_signal == "deteriorating"
    if conviction == "high" and net_verdict == "consistent":
        if cashflow_signal == "undervalued":
            return "accumulate" if downgrade else "strong_buy"
        if cashflow_signal == "fair":
            return "hold" if downgrade else "accumulate"
        if cashflow_signal == "overvalued":
            return "hold" if has_position else "avoid"
        if cashflow_signal == "no_solution":
            return "hold"

    if conviction == "high" and net_verdict == "divergence":
        if cashflow_signal == "undervalued":
            return "accumulate"
        if cashflow_signal == "fair":
            return "hold"
        if cashflow_signal == "overvalued":
            return "trim" if has_position else "avoid"

    if conviction == "medium":
        if net_verdict == "consistent":
            if cashflow_signal == "undervalued":
                return "hold" if downgrade else "accumulate"
            if cashflow_signal == "fair":
                return "hold"
            if cashflow_signal == "overvalued":
                return "trim" if has_position else "avoid"
        if net_verdict == "divergence":
            if cashflow_signal == "undervalued":
                return "hold"
            if cashflow_signal == "overvalued":
                return "trim" if has_position else "avoid"

    if cashflow_signal == "overvalued":
        return "hold" if has_position else "avoid"
    return "hold"


def map_archetype(stance: str, conviction: str, reconciliation: dict[str, Any]) -> str:
    if stance in ("strong_buy", "accumulate"):
        return "value_accumulate"
    if stance == "hold":
        return "maintain"
    if stance == "trim":
        return "risk_off"
    if stance == "short":
        return "hedge_short"
    return "wait"


def extract_base_rate_pct01(intelligence_xray: dict[str, Any] | None) -> float | None:
    br = (intelligence_xray or {}).get("base_rate") or {}
    live = br.get("live") or {}
    pct = live.get("percentile")
    if pct is None:
        return None
    if not isinstance(pct, (int, float)):
        return None
    return pct / 100.0 if pct > 1 else pct


def your_p_win(intelligence_xray: dict[str, Any] | None) -> float:
    xray = intelligence_xray or {}
    scenario_probs = xray.get("scenario_probs") or {}
    if scenario_probs.get("note") == "above_top":
        return 0.0
    probs = scenario_probs.get("by_name") or {}
    raw_p = float(probs.get("bull", 0.0) or 0.0) + float(probs.get("moonshot", 0.0) or 0.0)
    pct01 = extract_base_rate_pct01(xray)
    if pct01 is None:
        penalty = 0.7
    elif pct01 > 0.90:
        penalty = 0.5
    elif pct01 > 0.75:
        penalty = 0.7
    else:
        penalty = 1.0
    return min(max(raw_p * penalty, 0.0), 1.0)


def _normalize_rnd(rnd: Any) -> list[tuple[float, float]]:
    if not rnd:
        return []
    rows: list[tuple[Any, Any]] = []
    if isinstance(rnd, dict):
        if isinstance(rnd.get("points"), list):
            for item in rnd["points"]:
                if isinstance(item, dict):
                    rows.append((item.get("price") or item.get("x") or item.get("strike"), item.get("prob") or item.get("p") or item.get("density")))
        else:
            prices = rnd.get("prices") or rnd.get("x") or rnd.get("strikes")
            probs = rnd.get("probs") or rnd.get("probabilities") or rnd.get("density") or rnd.get("y")
            if isinstance(prices, list) and isinstance(probs, list):
                rows.extend(zip(prices, probs))
    elif isinstance(rnd, list):
        for item in rnd:
            if isinstance(item, dict):
                rows.append((item.get("price") or item.get("x") or item.get("strike"), item.get("prob") or item.get("p") or item.get("density")))
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                rows.append((item[0], item[1]))

    points: list[tuple[float, float]] = []
    for price, prob in rows:
        if isinstance(price, (int, float)) and isinstance(prob, (int, float)) and prob >= 0:
            points.append((float(price), float(prob)))
    total = sum(prob for _, prob in points)
    if total <= 0:
        return []
    return sorted((price, prob / total) for price, prob in points)


def rnd_integrate(rnd: Any, lower: float = -inf, upper: float = inf) -> float:
    points = _normalize_rnd(rnd)
    return sum(prob for price, prob in points if lower <= price <= upper)


def rnd_conditional_expectation(rnd: Any, lower: float = -inf, upper: float = inf) -> float | None:
    points = [(price, prob) for price, prob in _normalize_rnd(rnd) if lower <= price <= upper]
    mass = sum(prob for _, prob in points)
    if mass <= 0:
        return None
    return sum(price * prob for price, prob in points) / mass


def compute_kelly(your_p: float, rnd: Any, conviction: str, current_price: float) -> dict[str, Any]:
    points = _normalize_rnd(rnd)
    if not points or not current_price:
        return _degraded_size(conviction, "degraded_no_rnd")

    p_market_up = rnd_integrate(points, lower=current_price, upper=inf)
    up_exp = rnd_conditional_expectation(points, lower=current_price, upper=inf)
    down_exp = rnd_conditional_expectation(points, lower=-inf, upper=current_price)
    if up_exp is None or down_exp is None:
        return _degraded_size(conviction, "degraded_incomplete_rnd")

    expected_up = max(up_exp / current_price - 1.0, 0.0)
    expected_down = max(1.0 - down_exp / current_price, 0.0)
    b = expected_up / expected_down if expected_down > 0 else inf
    p = min(max(your_p, 0.0), 1.0)
    q = 1.0 - p
    raw = (b * p - q) / b if b and b > 0 and b != inf else p
    fractional = raw * 0.25
    adjusted = max(fractional * CONVICTION_MULT.get(conviction, 0.3), 0.0)
    return {
        "raw_kelly": round(raw, 4),
        "kelly_fraction": 0.25,
        "fractional_kelly": round(fractional, 4),
        "conviction_multiplier": CONVICTION_MULT.get(conviction, 0.3),
        "target_weight": round(min(adjusted, 0.20), 4),
        "cap": 0.20,
        "cash_floor": 0.10,
        "market_odds": {
            "p_market_up": round(p_market_up, 4),
            "b": round(b, 4) if b != inf else inf,
            "expected_up": round(expected_up, 4),
            "expected_down": round(expected_down, 4),
        },
    }


def _degraded_size(conviction: str, status: str) -> dict[str, Any]:
    return {
        "raw_kelly": 0.0,
        "kelly_fraction": 0.25,
        "fractional_kelly": 0.0,
        "conviction_multiplier": CONVICTION_MULT.get(conviction, 0.3),
        "target_weight": 0.0,
        "cap": 0.20,
        "cash_floor": 0.10,
        "market_odds": None,
        "status": status,
    }


def compute_edge(
    your_p: float,
    market_p_up: float | None,
    rnd: Any,
    current_price: float,
    target_price: float | None = None,
    signals: dict[str, str] | None = None,
) -> dict[str, Any]:
    if market_p_up is None:
        return {
            "edge_prob": None,
            "edge_quantile": None,
            "view_quantile": None,
            "supporting_channels": [],
            "opposing_channels": ["distribution_missing"],
            "status": "degraded_no_rnd",
        }
    edge_prob = your_p - market_p_up
    view_quantile = rnd_integrate(rnd, lower=target_price, upper=inf) if target_price else None
    market_quantile = rnd_integrate(rnd, lower=current_price, upper=inf)
    edge_quantile = (view_quantile - market_quantile) if view_quantile is not None else None
    supporting, opposing = _channel_support(signals or {})
    return {
        "edge_prob": round(edge_prob, 4),
        "edge_quantile": round(edge_quantile, 4) if edge_quantile is not None else None,
        "view_quantile": round(view_quantile, 4) if view_quantile is not None else None,
        "supporting_channels": supporting,
        "opposing_channels": opposing,
    }


def _channel_support(signals: dict[str, str]) -> tuple[list[str], list[str]]:
    positive = {"cashflow": {"undervalued"}, "distribution": {"bullish"}, "revision": {"improving"}}
    negative = {"cashflow": {"overvalued"}, "distribution": {"bearish", "euphoric"}, "revision": {"deteriorating"}}
    supporting = [ch for ch, vals in positive.items() if signals.get(ch) in vals]
    opposing = [ch for ch, vals in negative.items() if signals.get(ch) in vals]
    return supporting, opposing


def compute_stop(archetype_spec: dict[str, Any], current_price: float | None) -> dict[str, Any] | None:
    if not current_price:
        return None
    stop = archetype_spec.get("stop")
    if not stop:
        return None
    if stop.get("method") in ("trailing", "tighten"):
        return {"type": "price", "level": round(current_price * (1.0 - stop["trail_pct"]), 4), "method": stop["method"]}
    if stop.get("method") == "fixed":
        return {"type": "price", "level": round(current_price * (1.0 + stop["above_entry_pct"]), 4), "method": "fixed"}
    return {"type": "fundamental", "level": None, "method": stop.get("method")}


def _kill_line_from_wwhtbt(wwhtbt: Any) -> str | None:
    if isinstance(wwhtbt, list):
        for item in wwhtbt:
            if isinstance(item, dict) and item.get("kind") == "kill":
                return item.get("value") or item.get("label")
    if isinstance(wwhtbt, dict):
        return wwhtbt.get("kill_line") or wwhtbt.get("line")
    return None


def build_kill(
    archetype_spec: dict[str, Any],
    wwhtbt: Any,
    rnd: Any,
    stop: dict[str, Any] | None,
    revision_metrics: dict[str, Any],
) -> dict[str, Any] | None:
    kill_line = _kill_line_from_wwhtbt(wwhtbt)
    if kill_line:
        return {
            "type": "fundamental",
            "line": kill_line,
            "metric": "revenue_growth",
            "threshold": None,
            "quarters_required": 2,
            "current_value": revision_metrics.get("est_rev_90d"),
            "implied_prob": None,
            "tracking": "consensus_revision",
            "status": "monitoring",
        }
    if stop and stop.get("type") == "price" and stop.get("level") is not None:
        prob = rnd_integrate(rnd, upper=stop["level"]) if rnd else None
        return {
            "type": "price",
            "line": f"price crosses {stop['level']}",
            "level": stop["level"],
            "implied_prob": round(prob, 4) if prob is not None else None,
            "status": "monitoring" if prob is not None else "degraded_no_rnd",
        }
    return None


def build_entry(archetype_spec: dict[str, Any], current_price: float | None) -> dict[str, Any]:
    spec = archetype_spec.get("entry") or {}
    method = spec.get("method")
    if method == "staged":
        return {"price": current_price, "condition": "staged", "stages": spec.get("batches")}
    if method == "reduce":
        return {"price": current_price, "condition": "reduce", "reduce_pct": spec.get("reduce_pct"), "stages": None}
    if method == "single":
        return {"price": current_price, "condition": spec.get("condition", "immediate"), "stages": None}
    return {"price": current_price, "condition": "none", "stages": None}


def synthesize_rationale(
    stance: str,
    conviction: str,
    edge: dict[str, Any],
    reconciliation: dict[str, Any],
) -> str:
    ci = reconciliation.get("conviction_input") or {}
    edge_prob = edge.get("edge_prob")
    edge_text = "unknown edge" if edge_prob is None else f"edge_prob={edge_prob:.4f}"
    return (
        f"stance={stance}; conviction={conviction}; net_verdict={ci.get('net_verdict')}; "
        f"{edge_text}; skipped_checks={ci.get('skipped_count', 0)}"
    )


def _current_price(panel_map: dict[str, dict[str, Any]]) -> float | None:
    for channel, path in (
        ("cashflow", "anchor_price"),
        ("cashflow", "current_price"),
        ("distribution", "spot"),
        ("distribution", "current_price"),
    ):
        value = metric(panel_map, channel, path)
        if isinstance(value, (int, float)):
            return float(value)
    return None


def build_trade_plan(
    panel_list: list[dict[str, Any]],
    position_context: dict[str, Any] | None = None,
    reconciliation: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    panel_map = panel_by_channel(panel_list)
    position_context = position_context or {}
    has_position = bool(position_context.get("has_position"))

    reconciliation = reconciliation or build_reconciliation(panel_list)
    conviction = compute_conviction(reconciliation, panel_list)
    signals = reconciliation.get("signals") or extract_signals(panel_map)

    stance = determine_stance(
        reconciliation,
        conviction,
        distribution_signal=signals.get("distribution", "unknown"),
        revision_signal=signals.get("revision", "unknown"),
        cashflow_signal=signals.get("cashflow", "unknown"),
        has_position=has_position,
    )
    archetype = map_archetype(stance, conviction, reconciliation)
    current_price = _current_price(panel_map)
    distribution_rnd = metric(panel_map, "distribution", "rnd")
    xray = metric(panel_map, "cashflow", "primary.xray") or metric(panel_map, "cashflow", "xray") or {}
    target_price = metric(panel_map, "cashflow", "target_price") or metric(panel_map, "cashflow", "baseline")

    your_p = your_p_win(xray)
    market_p_up = rnd_integrate(distribution_rnd, lower=current_price, upper=inf) if distribution_rnd and current_price else None
    edge = compute_edge(your_p, market_p_up, distribution_rnd, current_price or 0.0, target_price, signals)
    size = compute_kelly(your_p, distribution_rnd, conviction, current_price or 0.0) if distribution_rnd else _degraded_size(conviction, "degraded_no_rnd")

    if conviction == "low" and (edge.get("edge_prob") is None or edge["edge_prob"] < 0.02):
        size["target_weight"] = 0.0
        stance = "avoid"
        archetype = "wait"

    cashflow_sig = signals.get("cashflow", "unknown")
    distribution_sig = signals.get("distribution", "unknown")

    # SPEC D: 一期 short 不可执行（持仓中心，short 的是不持有的票无入口）。
    # short 场景在 determine_stance 里已降级为 trim/avoid，
    # 这里检测是否是 short 降级场景，保留语义标记。
    downgraded_from_short = (
        cashflow_sig == "overvalued"
        and distribution_sig in ("euphoric", "bearish")
        and stance in ("trim", "avoid")
    )

    archetype_spec = ARCHETYPE_SPEC[archetype]
    stop = compute_stop(archetype_spec, current_price)
    kill = build_kill(
        archetype_spec,
        xray.get("wwhtbt"),
        distribution_rnd,
        stop,
        _panel_metrics(panel_map, "revision"),
    )
    if not kill:
        return None

    entry = build_entry(archetype_spec, current_price)
    rationale = synthesize_rationale(stance, conviction, edge, reconciliation)
    return {
        "stance": stance,
        "strategy_archetype": archetype,
        "conviction": conviction,
        "edge": edge,
        "entry": entry,
        "size": size,
        "stop": stop,
        "kill": kill,
        "exit": archetype_spec["exit"],
        "rationale": rationale,
        "self_falsification": kill.get("line"),
        "actionable": not downgraded_from_short,  # short 降级场景标记不可执行
        "downgraded_from": "short" if downgraded_from_short else None,
    }
