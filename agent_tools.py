"""agent_tools.py — the tool registry the agentic orchestrator + Q&A agent call.

Each tool is a thin, declarative wrapper over an EXISTING function (decoder /
reverse_dcf / evidence / narrative) — no valuation logic is reimplemented here.
`dispatch()` validates the web-gate, runs the tool, JSON-sanitizes the result,
emits an ActivityEvent, and NEVER raises (the agent loop must survive a bad call).

Web-grounded tools (gather_evidence / research_narrative) honest-empty when the
provider can't actually search (client.web_search_capable() is False) — they never
fabricate sources, which is the product's core integrity guarantee.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Callable

import client

# decoder / reverse_dcf / evidence / narrative are imported lazily inside tool
# bodies to keep this module cheap to import and avoid circulars.


# ---------------------------------------------------------------------------
# Context + registry
# ---------------------------------------------------------------------------

@dataclass
class ToolContext:
    """Per-session state tools read so they need no globals.  Built once by the
    orchestrator / Q&A agent (fundamentals preloaded so cheap tools never refetch)."""
    ticker: str | None = None
    fundamentals: Any = None                  # decoder.Fundamentals (preloaded)
    anchor_price: float | None = None
    conn: Any = None
    hunter: Any = None                        # injectable evidence hunter (tests)
    narrator: Any = None                      # injectable narrative researcher
    lang: str = "zh"
    emit: Callable | None = None              # ActivityEvent sink (M5)
    fundamentals_fn: Callable | None = None   # for a 2nd ticker (compare); tests inject


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict                          # JSON Schema for the args object
    fn: Callable                              # fn(args: dict, ctx: ToolContext) -> dict
    web_gated: bool = False


TOOL_REGISTRY: dict[str, Tool] = {}


def tool(name: str, description: str, parameters: dict, *, web_gated: bool = False):
    def deco(fn: Callable) -> Callable:
        TOOL_REGISTRY[name] = Tool(name, description, parameters, fn, web_gated)
        return fn
    return deco


def openai_tools_spec(names: list[str] | None = None) -> list[dict]:
    """Render selected tools as the OpenAI `tools` array.  names=None → all."""
    sel = (TOOL_REGISTRY if names is None
           else {n: TOOL_REGISTRY[n] for n in names if n in TOOL_REGISTRY})
    return [
        {"type": "function",
         "function": {"name": t.name, "description": t.description,
                      "parameters": t.parameters}}
        for t in sel.values()
    ]


def _json_safe(o: Any) -> Any:
    """Recursively coerce a tool result to something json.dumps can handle."""
    if o is None or isinstance(o, (bool, int, float, str)):
        return o
    if is_dataclass(o) and not isinstance(o, type):
        return _json_safe(asdict(o))
    if isinstance(o, dict):
        return {str(k): _json_safe(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [_json_safe(v) for v in o]
    return str(o)


def _emit(ctx: ToolContext, name: str, args: dict, result: dict) -> None:
    """Emit one ActivityEvent for a tool call (never raises)."""
    if not callable(getattr(ctx, "emit", None)):
        return
    kind = "evidence" if TOOL_REGISTRY.get(name, None) and TOOL_REGISTRY[name].web_gated else "computation"
    try:
        ctx.emit({
            "phase": "tool", "kind": kind, "subject": ctx.ticker,
            "text": f"工具 {name}({', '.join(f'{k}={v}' for k, v in (args or {}).items())[:80]})",
            "payload": {"tool": name, "args": args,
                        "ok": "error" not in (result or {})},
        })
    except Exception:
        pass


def dispatch(name: str, arguments: dict | None, ctx: ToolContext) -> dict:
    """Run one tool by name.  Validates the web-gate, never raises, emits an event."""
    t = TOOL_REGISTRY.get(name)
    if t is None:
        result = {"error": f"unknown tool: {name}",
                  "available": sorted(TOOL_REGISTRY.keys())}
        _emit(ctx, name, arguments or {}, result)
        return result
    if t.web_gated and not client.web_search_capable():
        # Provider can't browse → would be ungrounded with fabricated sources.
        result = {"web_grounded": False, "coverage": "unavailable",
                  "reason": "provider_not_web_capable",
                  "note": ("active LLM provider has no web search; evidence/narrative "
                           "are honest-empty rather than fabricated. Set "
                           "ALLOW_UNGROUNDED_RESEARCH=1 to override (unverified).")}
        _emit(ctx, name, arguments or {}, result)
        return result
    try:
        result = t.fn(arguments or {}, ctx)
    except Exception as exc:  # the agent recovers from a bad call; loop never dies
        result = {"error": str(exc), "tool": name}
    result = _json_safe(result if isinstance(result, dict) else {"result": result})
    _emit(ctx, name, arguments or {}, result)
    return result


# ---------------------------------------------------------------------------
# Cheap, pure-Python tools (no web) — the agentic decode's bread and butter
# ---------------------------------------------------------------------------

def _flat_fundamentals(f) -> dict:
    """Fundamentals dataclass → flat JSON dict (fields + derived predicates)."""
    d = asdict(f)
    d["market_cap"] = f.market_cap
    for p in ("has_revenue", "has_positive_earnings", "has_positive_fcf",
              "has_positive_ebitda", "has_book_equity", "has_growth"):
        d[p] = bool(getattr(f, p))
    return d


@tool("get_fundamentals",
      "Get the company's trailing fundamentals (revenue, earnings, FCF, margins, "
      "beta, market cap, etc.) and which valuation inputs are available.",
      {"type": "object", "properties": {
          "ticker": {"type": "string",
                     "description": "Optional; defaults to the card's subject."}}})
def _t_get_fundamentals(args: dict, ctx: ToolContext) -> dict:
    import decoder
    tk = (args.get("ticker") or ctx.ticker or "").upper()
    if ctx.fundamentals is not None and tk == (ctx.ticker or "").upper():
        return _flat_fundamentals(ctx.fundamentals)
    fn = ctx.fundamentals_fn or decoder.fetch_fundamentals
    return _flat_fundamentals(fn(tk))


@tool("classify_subject",
      "Classify whether the subject is an AI-composite / narrative-priced name "
      "(GPU/storage/optical/AI-app), which argues for anchor mode over multiples.",
      {"type": "object", "properties": {}})
def _t_classify(args: dict, ctx: ToolContext) -> dict:
    import decoder
    is_ai, theme = decoder.is_ai_composite(ctx.fundamentals)
    return {"is_ai_composite": bool(is_ai), "theme": theme}


@tool("plan_lenses",
      "Get the deterministic lens plan (primary + cross valuation lenses) the rule "
      "engine would pick. Use as a SUGGESTION you can override, not a mandate.",
      {"type": "object", "properties": {}})
def _t_plan_lenses(args: dict, ctx: ToolContext) -> dict:
    import decoder
    plan = decoder.select_lenses(ctx.fundamentals)
    return {"primary": plan.primary, "cross": list(plan.cross),
            "reason": plan.reason, "insufficient": bool(plan.insufficient)}


@tool("run_lens",
      "Reverse-solve ONE valuation lens at the current price: returns the implied "
      "value (e.g. implied P/E, implied 5y revenue CAGR for 'dcf') or no_solution.",
      {"type": "object", "properties": {
          "lens": {"type": "string",
                   "description": "lens key: pe|ps|ev_ebitda|p_fcf|p_b|peg|dcf"}},
       "required": ["lens"]})
def _t_run_lens(args: dict, ctx: ToolContext) -> dict:
    import decoder
    key = str(args.get("lens", "")).lower()
    if key not in decoder.LENS_REGISTRY:
        return {"error": f"unknown lens '{key}'",
                "available": sorted(decoder.LENS_REGISTRY.keys())}
    res = decoder._run_lens(key, ctx.anchor_price, ctx.fundamentals)
    return res if res else {"lens": key, "no_solution": True}


@tool("run_all_applicable_lenses",
      "Run every valuation lens that applies and return all implied values in one "
      "call (cheaper than calling run_lens repeatedly).",
      {"type": "object", "properties": {}})
def _t_run_all_lenses(args: dict, ctx: ToolContext) -> dict:
    import decoder
    results = []
    for key in decoder.LENS_REGISTRY:
        try:
            r = decoder._run_lens(key, ctx.anchor_price, ctx.fundamentals)
        except Exception:
            r = None
        if r:
            results.append(r)
    return {"results": results, "count": len(results)}


@tool("run_anchor_decompose",
      "Decompose the price into base business value + narrative/option/TAM/analogy "
      "components reconciled to the anchor (anchor mode). Use for narrative-priced "
      "names where multiples can't explain the price.",
      {"type": "object", "properties": {}})
def _t_anchor(args: dict, ctx: ToolContext) -> dict:
    import decoder
    f = ctx.fundamentals
    anchor = ctx.anchor_price
    cross = []
    for key in decoder.LENS_REGISTRY:
        try:
            r = decoder._run_lens(key, anchor, f)
        except Exception:
            r = None
        if r:
            cross.append(r)
    # llm=None → deterministic anchor decomposition (no LLM cost).
    return decoder._run_anchor_mode(anchor, f, ctx.emit, ctx.ticker, cross, llm=None)


@tool("whatif_reverse_dcf",
      "Re-solve the reverse DCF with overridden assumptions to answer 'what if' "
      "questions (e.g. wacc=0.09). Returns the implied driver under the override vs "
      "the consensus baseline, so you can show how the bet shifts.",
      {"type": "object", "properties": {
          "solve_for": {"type": "string",
                        "description": "var to solve: revenue_cagr_5y (default) | "
                                       "terminal_fcf_margin | terminal_growth | wacc"},
          "overrides": {"type": "object",
                        "description": "assumption overrides, e.g. {\"wacc\": 0.09, "
                                       "\"terminal_fcf_margin\": 0.25}"}}})
def _t_whatif_dcf(args: dict, ctx: ToolContext) -> dict:
    import reverse_dcf
    from dataclasses import replace
    f, anchor = ctx.fundamentals, ctx.anchor_price
    if not (f and f.has_revenue and f.shares_outstanding and anchor):
        return {"error": "DCF needs revenue + shares + a price; not available here."}
    # Mirror decoder._lens_dcf's consensus construction (reuses reverse_dcf math).
    data = reverse_dcf.CompanyData(
        ticker=f.ticker, current_price=anchor, revenue_ttm=f.revenue_ttm,
        fcf_ttm=f.fcf_ttm if f.fcf_ttm is not None else 0.0,
        shares_outstanding=f.shares_outstanding, net_debt=f.net_debt or 0.0,
        beta=f.beta if f.beta is not None else 1.0)
    base_margin = (max(data.fcf_ttm / data.revenue_ttm, 0.05)
                   if data.revenue_ttm else 0.15)
    consensus = reverse_dcf.Assumptions(
        revenue_cagr_5y=0.15, terminal_growth=0.025,
        terminal_fcf_margin=base_margin, wacc=reverse_dcf.compute_wacc(data.beta))
    solve_for = str(args.get("solve_for") or "revenue_cagr_5y")
    if solve_for not in ("revenue_cagr_5y", "terminal_growth",
                         "terminal_fcf_margin", "wacc"):
        return {"error": f"cannot solve_for '{solve_for}'"}
    overrides = {k: v for k, v in (args.get("overrides") or {}).items()
                 if k in ("revenue_cagr_5y", "terminal_growth",
                          "terminal_fcf_margin", "wacc") and k != solve_for}
    try:
        scenario = replace(consensus, **overrides)
    except TypeError as exc:
        return {"error": f"bad overrides: {exc}"}
    baseline_iv = reverse_dcf.reverse_solve(anchor, consensus, solve_for, data)
    scenario_iv = reverse_dcf.reverse_solve(anchor, scenario, solve_for, data)
    return {
        "solve_for": solve_for,
        "overrides_applied": overrides,
        "consensus_assumptions": asdict(consensus),
        "baseline_implied_value": baseline_iv,
        "scenario_implied_value": scenario_iv,
        "baseline_price": anchor,
        "note": ("scenario_implied_value is the driver the DCF needs to justify the "
                 "SAME price under your overrides; compare to baseline_implied_value."),
    }


@tool("compare_subjects",
      "Compare the card's subject to another ticker on their primary implied "
      "valuation metric (and DCF baseline where available).",
      {"type": "object", "properties": {
          "ticker_b": {"type": "string", "description": "the other ticker"}},
       "required": ["ticker_b"]})
def _t_compare(args: dict, ctx: ToolContext) -> dict:
    import decoder
    tb = str(args.get("ticker_b", "")).upper()
    if not tb:
        return {"error": "ticker_b required"}
    fn = ctx.fundamentals_fn or decoder.fetch_fundamentals

    def _primary(f):
        if f is None or f.current_price is None:
            return None
        plan = decoder.select_lenses(f)
        if plan.insufficient:
            return {"insufficient": plan.reason}
        return decoder._run_lens(plan.primary, f.current_price, f)

    try:
        f_b = fn(tb)
    except Exception as exc:
        return {"error": f"could not fetch {tb}: {exc}"}
    return {"ticker_a": ctx.ticker, "ticker_b": tb,
            "a_primary": _primary(ctx.fundamentals), "b_primary": _primary(f_b)}


# ---------------------------------------------------------------------------
# Web-grounded tools — honest-empty unless the provider can actually search
# ---------------------------------------------------------------------------

@tool("research_narrative",
      "Deep-research the live bull/bear debate behind the implied numbers (regime, "
      "bull case, bear case, catalysts, with sources tiered by credibility). "
      "Requires a web-search-capable provider; otherwise returns unavailable.",
      {"type": "object", "properties": {
          "implied_assumptions": {"type": "string",
                                  "description": "the implied numbers to investigate"}}},
      web_gated=True)
def _t_research_narrative(args: dict, ctx: ToolContext) -> dict:
    import narrative
    env, _hit = narrative.research_market_narrative(
        ctx.ticker, current_price=ctx.anchor_price,
        implied_assumptions=args.get("implied_assumptions", ""),
        lang=ctx.lang, conn=ctx.conn, researcher=ctx.narrator)
    res = narrative.build_card_narrative(env)
    return {"coverage": res.get("coverage"), "summary": res.get("summary"),
            "full": res.get("full")}


@tool("gather_evidence",
      "Hunt real-world evidence for ONE implied assumption (returns sources tiered "
      "by credibility; social/crypto can't be a sole backer). Requires a web-search "
      "provider; otherwise returns unavailable.",
      {"type": "object", "properties": {
          "assumption_text": {"type": "string",
                              "description": "the assumption to find evidence for"},
          "metric": {"type": "string", "description": "optional metric/lens label"}}},
      web_gated=True)
def _t_gather_evidence(args: dict, ctx: ToolContext) -> dict:
    import evidence
    assumption = {"human_text": args.get("assumption_text", ""),
                  "metric": args.get("metric", ""),
                  "lens": args.get("metric", "")}
    hunter = ctx.hunter or evidence._default_hunter
    raw = hunter(ctx.ticker, assumption, lang=ctx.lang, mode="standard",
                 company_name=(getattr(ctx.fundamentals, "industry", None)
                               or ctx.ticker),
                 current_price=ctx.anchor_price)
    if raw is None:
        return {"web_grounded": False, "coverage": "unavailable",
                "reason": "no_result"}
    return {"web_grounded": True, "brief": raw}
