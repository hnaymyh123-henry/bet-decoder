"""Module 2 — Decoder Engine skeleton + pluggable valuation-lens registry.

Frame-adaptive agentic decode (PRD.md 模块 2):

    任意 source  ──▶  [前置适配器]  source → anchor price + fundamentals
                       [共享核心]    pick lenses → reverse-solve implied metrics
                       [后置组装]    → BetCard (M1 type, passive return)

`reverse_dcf.py` is demoted to *one tool inside the DCF lens* — every other lens
is a ~5-line arithmetic reverse-solve.  The lens registry is pluggable (a dict +
a `@lens` decorator) and seeded with 7 traditional lenses:
DCF / P/E / P/S / EV-EBITDA / P-FCF / P-B / PEG.

Lens *selection* is a deterministic constraint decision tree (PRD decision 3) so
the same fundamentals always yield the same primary + cross-validation lenses —
no LLM is required for the MVP skeleton.  An optional `llm` hook is threaded
through for future agentic narration, but defaults to ``None`` and is never
invoked by the deterministic path, so tests run at zero API cost.

This module is PASSIVE: it returns a BetCard and never stores it (the caller
persists via ``db.save_card``).  It does **not** implement anchor mode (Issue
#3), the evidence hunter (Issue #4), cross-card synthesis, rendering, or SSE.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Optional

import db
import reverse_dcf

# ---------------------------------------------------------------------------
# Source-type constants (mirror db.py; MVP scope = Market + Portfolio).
# ---------------------------------------------------------------------------

SOURCE_MARKET = db.SOURCE_MARKET          # "market"
SOURCE_PORTFOLIO = db.SOURCE_PORTFOLIO    # "portfolio"
SOURCE_ANALYST_PT = db.SOURCE_ANALYST_PT  # "analyst_pt"  (V2)
SOURCE_OPINION = db.SOURCE_OPINION        # "opinion"     (V2)

_MVP_SOURCES = {SOURCE_MARKET, SOURCE_PORTFOLIO}


# ===========================================================================
# Neutral fundamentals snapshot
# ===========================================================================
#
# A lens-agnostic view of one company's trailing fundamentals.  Every value is
# nullable: the decision tree reads the `has_*` flags / None-ness to pick lenses,
# and individual lenses bail out (return None) when an input they need is
# missing.  `fetch_fundamentals` populates this from yfinance, but it is
# injectable so tests run on hardcoded data with zero network / API cost.

@dataclass
class Fundamentals:
    ticker: str
    current_price: float | None = None     # market price (Market anchor default)
    revenue_ttm: float | None = None        # total revenue, TTM
    net_income_ttm: float | None = None      # net income, TTM (drives P/E)
    ebitda_ttm: float | None = None          # EBITDA, TTM (drives EV/EBITDA)
    fcf_ttm: float | None = None             # free cash flow, TTM (drives P-FCF)
    book_equity: float | None = None         # total stockholder equity (drives P/B)
    eps_ttm: float | None = None             # diluted EPS, TTM
    shares_outstanding: float | None = None
    net_debt: float | None = None            # total debt - cash (EV bridge)
    beta: float | None = None
    growth_rate: float | None = None         # fwd/consensus growth (drives PEG)

    # --- derived predicates the decision tree reads ---
    @property
    def has_positive_earnings(self) -> bool:
        return self.net_income_ttm is not None and self.net_income_ttm > 0

    @property
    def has_revenue(self) -> bool:
        return self.revenue_ttm is not None and self.revenue_ttm > 0

    @property
    def has_positive_fcf(self) -> bool:
        return self.fcf_ttm is not None and self.fcf_ttm > 0

    @property
    def has_positive_ebitda(self) -> bool:
        return self.ebitda_ttm is not None and self.ebitda_ttm > 0

    @property
    def has_book_equity(self) -> bool:
        return self.book_equity is not None and self.book_equity > 0

    @property
    def has_growth(self) -> bool:
        return self.growth_rate is not None and self.growth_rate > 0

    @property
    def market_cap(self) -> float | None:
        if self.current_price is None or self.shares_outstanding is None:
            return None
        return self.current_price * self.shares_outstanding

    def enterprise_value(self, anchor_price: float) -> float | None:
        """EV at a given (anchor) price per share = mkt cap + net debt."""
        if self.shares_outstanding is None:
            return None
        nd = self.net_debt or 0.0
        return anchor_price * self.shares_outstanding + nd


def fetch_fundamentals(ticker: str) -> Fundamentals:
    """Populate a Fundamentals snapshot from yfinance (best-effort, all fields
    optional).  Reuses reverse_dcf's tolerant row reader for the statements.

    Network/quota lives entirely here; tests inject a stub `fundamentals_fn`
    into `decode_bet` instead of calling this, so no live fetch / API cost.
    """
    import yfinance as yf  # local import: keeps `import decoder` network-free

    t = yf.Ticker(ticker)
    info = t.info or {}
    financials = t.financials
    cashflow = t.cashflow
    bs = t.balance_sheet

    revenue = reverse_dcf._safe_row(financials, "Total Revenue", "TotalRevenue") or None
    net_income = reverse_dcf._safe_row(
        financials, "Net Income", "Net Income Common Stockholders"
    ) or None
    ebitda = reverse_dcf._safe_row(financials, "EBITDA", "Normalized EBITDA") or None
    ocf = reverse_dcf._safe_row(
        cashflow, "Operating Cash Flow", "Total Cash From Operating Activities"
    )
    capex = reverse_dcf._safe_row(cashflow, "Capital Expenditure", "Capital Expenditures")
    fcf = (ocf + capex) if (ocf or capex) else None  # capex signed negative
    book = reverse_dcf._safe_row(
        bs, "Stockholders Equity", "Total Stockholder Equity", "Common Stock Equity"
    ) or None
    total_debt = reverse_dcf._safe_row(bs, "Total Debt")
    cash = reverse_dcf._safe_row(bs, "Cash And Cash Equivalents", "Cash")
    net_debt = total_debt - cash

    def _f(key):
        try:
            v = info.get(key)
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    return Fundamentals(
        ticker=ticker,
        current_price=_f("currentPrice") or _f("regularMarketPrice"),
        revenue_ttm=revenue,
        net_income_ttm=net_income,
        ebitda_ttm=ebitda,
        fcf_ttm=fcf,
        book_equity=book,
        eps_ttm=_f("trailingEps"),
        shares_outstanding=_f("sharesOutstanding"),
        net_debt=net_debt,
        beta=_f("beta") or 1.0,
        growth_rate=_f("earningsGrowth") or _f("revenueGrowth"),
    )


# ===========================================================================
# Lens registry (pluggable)
# ===========================================================================
#
# A Lens turns an anchor *price per share* into the business metric that price
# implies under that valuation frame.  `applicable(f)` gates the lens on the
# fundamentals it needs; `solve(anchor, f)` returns a result dict or None when
# it has no solution (→ the decision tree falls back to the next lens).

LensSolve = Callable[[float, Fundamentals], Optional[dict]]
LensApplicable = Callable[[Fundamentals], bool]


@dataclass
class Lens:
    key: str
    label: str
    applicable: LensApplicable
    solve: LensSolve
    family: str = "multiple"   # "dcf" | "multiple"


LENS_REGISTRY: dict[str, Lens] = {}


def lens(key: str, label: str, *, family: str = "multiple",
         applicable: LensApplicable) -> Callable[[LensSolve], LensSolve]:
    """Register a lens by `key`. Decorate a `solve(anchor, f) -> dict | None`."""
    def _register(fn: LensSolve) -> LensSolve:
        LENS_REGISTRY[key] = Lens(
            key=key, label=label, applicable=applicable, solve=fn, family=family,
        )
        return fn
    return _register


def register_lens(lens_obj: Lens) -> None:
    """Imperative registration (e.g. for a lens with non-trivial setup)."""
    LENS_REGISTRY[lens_obj.key] = lens_obj


def _result(metric: str, value: float | None, *, implied_label: str,
            unit: str = "x", **extra) -> dict | None:
    """Uniform lens result envelope. None value → caller treats as no-solution."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    out = {
        "metric": metric,           # the business/valuation metric reverse-solved
        "implied_value": value,     # the number the anchor price implies
        "implied_label": implied_label,
        "unit": unit,
    }
    out.update(extra)
    return out


# --- Multiple lenses (each ~5 lines of arithmetic) -------------------------
#
# Convention: the *anchor price* is the input bet.  Each multiple lens reports
# the valuation multiple that anchor price implies given trailing fundamentals,
# i.e. "to justify this price you must believe the market pays <implied_value>
# times <denominator>".

@lens("pe", "P/E", applicable=lambda f: f.has_positive_earnings
      and f.eps_ttm is not None and f.eps_ttm > 0)
def _lens_pe(anchor: float, f: Fundamentals) -> dict | None:
    # implied trailing P/E = price / EPS
    return _result("implied_pe", anchor / f.eps_ttm,
                   implied_label="隐含市盈率 P/E")


@lens("ps", "P/S", applicable=lambda f: f.has_revenue
      and f.shares_outstanding is not None and f.shares_outstanding > 0)
def _lens_ps(anchor: float, f: Fundamentals) -> dict | None:
    # implied P/S = market cap / revenue
    mcap = anchor * f.shares_outstanding
    return _result("implied_ps", mcap / f.revenue_ttm,
                   implied_label="隐含市销率 P/S")


@lens("ev_ebitda", "EV/EBITDA", applicable=lambda f: f.has_positive_ebitda
      and f.shares_outstanding is not None and f.shares_outstanding > 0)
def _lens_ev_ebitda(anchor: float, f: Fundamentals) -> dict | None:
    ev = f.enterprise_value(anchor)
    if ev is None:
        return None
    return _result("implied_ev_ebitda", ev / f.ebitda_ttm,
                   implied_label="隐含 EV/EBITDA")


@lens("p_fcf", "P/FCF", applicable=lambda f: f.has_positive_fcf
      and f.shares_outstanding is not None and f.shares_outstanding > 0)
def _lens_p_fcf(anchor: float, f: Fundamentals) -> dict | None:
    mcap = anchor * f.shares_outstanding
    return _result("implied_p_fcf", mcap / f.fcf_ttm,
                   implied_label="隐含 P/FCF")


@lens("p_b", "P/B", applicable=lambda f: f.has_book_equity
      and f.shares_outstanding is not None and f.shares_outstanding > 0)
def _lens_p_b(anchor: float, f: Fundamentals) -> dict | None:
    mcap = anchor * f.shares_outstanding
    return _result("implied_p_b", mcap / f.book_equity,
                   implied_label="隐含市净率 P/B")


@lens("peg", "PEG", applicable=lambda f: f.has_positive_earnings
      and f.has_growth and f.eps_ttm is not None and f.eps_ttm > 0)
def _lens_peg(anchor: float, f: Fundamentals) -> dict | None:
    # PEG = (P/E) / (growth% as a number, e.g. 25 for 25%)
    pe = anchor / f.eps_ttm
    growth_pts = f.growth_rate * 100.0
    if growth_pts <= 0:
        return None
    return _result("implied_peg", pe / growth_pts, unit="",
                   implied_label="隐含 PEG", implied_pe=pe,
                   growth_pct=f.growth_rate)


# --- DCF lens (wraps reverse_dcf.py; algorithm untouched) ------------------

@lens("dcf", "DCF", family="dcf", applicable=lambda f: f.has_revenue
      and f.shares_outstanding is not None and f.shares_outstanding > 0)
def _lens_dcf(anchor: float, f: Fundamentals) -> dict | None:
    """Reverse-solve the implied 5y revenue CAGR (and a Monte-Carlo band, R2)
    that a DCF needs to justify `anchor`.  This is a thin wrapper over
    reverse_dcf.py — none of its math is reimplemented here.
    """
    if not f.has_revenue or not f.shares_outstanding:
        return None
    data = reverse_dcf.CompanyData(
        ticker=f.ticker,
        current_price=anchor,
        revenue_ttm=f.revenue_ttm,
        fcf_ttm=f.fcf_ttm if f.fcf_ttm is not None else 0.0,
        shares_outstanding=f.shares_outstanding,
        net_debt=f.net_debt or 0.0,
        beta=f.beta if f.beta is not None else 1.0,
    )
    consensus_wacc = reverse_dcf.compute_wacc(data.beta)
    base_margin = (
        max(data.fcf_ttm / data.revenue_ttm, 0.05) if data.revenue_ttm else 0.15
    )
    consensus = reverse_dcf.Assumptions(
        revenue_cagr_5y=0.15,
        terminal_growth=0.025,
        terminal_fcf_margin=base_margin,
        wacc=consensus_wacc,
    )
    # Point estimate: implied revenue CAGR holding other vars at consensus.
    point = reverse_dcf.reverse_solve(anchor, consensus, "revenue_cagr_5y", data)
    if point is None:
        return None  # DCF cannot explain this price → fallback to next lens
    # Monte-Carlo band (R2): p25/p50/p75 of the implied CAGR.
    perturbations = {
        "revenue_cagr_5y": (0.05, 0.30),
        "terminal_growth": (0.015, 0.035),
        "terminal_fcf_margin": (base_margin * 0.6, base_margin * 1.4),
        "wacc": (consensus_wacc - 0.015, consensus_wacc + 0.015),
    }
    band = reverse_dcf.monte_carlo_implied(
        data, "revenue_cagr_5y", consensus, perturbations
    )
    baseline_price = reverse_dcf.dcf_equity_value_per_share(consensus, data)
    return _result(
        "implied_revenue_cagr_5y", point, unit="",
        implied_label="隐含 5 年营收 CAGR",
        band=band,                       # {p25,p50,p75,success_rate,...} | None
        baseline_dcf_price=baseline_price,
        consensus_wacc=consensus_wacc,
    )


# ===========================================================================
# Frame-adaptive lens selection (deterministic decision tree)
# ===========================================================================

@dataclass
class LensPlan:
    primary: str | None                       # lens key, or None = data-insufficient
    cross: list[str] = field(default_factory=list)
    reason: str = ""                          # human trace of the decision
    insufficient: bool = False                # True → return a "数据不足" card


def select_lenses(f: Fundamentals) -> LensPlan:
    """Deterministic, reproducible lens selection (PRD 模块 2 决策 3).

    Rules (first-match for primary; cross = up to 2 other applicable lenses):
      - no revenue at all                         → 数据不足 (no lens)
      - positive earnings                         → primary P/E
      - revenue but no earnings                   → primary P/S
      - else (defensive fallback)                 → primary P/S
    Cross-validation candidates, in priority order, that are *applicable* and
    not already the primary:  DCF, P/FCF, EV/EBITDA, PEG, P/B, P/S, P/E.
    """
    # Hard gate: with no revenue we can't anchor any traditional lens.
    if not f.has_revenue:
        return LensPlan(primary=None, cross=[], insufficient=True,
                        reason="无营收数据,传统 lens 全部无法锚定 → 数据不足")

    # Primary selection.
    if f.has_positive_earnings and f.eps_ttm and f.eps_ttm > 0:
        primary, why = "pe", "有正盈利 → primary P/E"
    elif f.has_revenue:
        primary, why = "ps", "有营收但无正盈利 → primary P/S"
    else:  # unreachable given the gate, kept as defensive fallback
        primary, why = "ps", "兜底 → primary P/S"

    # Cross-validation: deterministic priority, skip primary + non-applicable,
    # cap at 2 (PRD: 1 primary + 1-2 cross).
    cross_priority = ["dcf", "p_fcf", "ev_ebitda", "peg", "p_b", "ps", "pe"]
    cross: list[str] = []
    for key in cross_priority:
        if key == primary:
            continue
        lens_obj = LENS_REGISTRY.get(key)
        if lens_obj is not None and lens_obj.applicable(f):
            cross.append(key)
        if len(cross) >= 2:
            break

    return LensPlan(primary=primary, cross=cross, reason=why)


# ===========================================================================
# emit helper (M5 callback contract — safe no-op when emit is None)
# ===========================================================================

def _safe_emit(emit, *, phase: str, kind: str, text: str,
               subject: str, payload: dict | None = None) -> None:
    """Best-effort ActivityEvent emit.  `emit=None` → no streaming side effects
    at all.  A broken emit callback must never break decoding, so we swallow.
    """
    if emit is None:
        return
    try:
        emit({
            "phase": phase,
            "kind": kind,
            "text": text,
            "source": {"kind": "decode", "subject": subject},
            "payload": payload,
        })
    except Exception:
        pass  # emit is decoration, never load-bearing


# ===========================================================================
# Lens execution (shared core)
# ===========================================================================

def _run_lens(key: str, anchor: float, f: Fundamentals) -> dict | None:
    """Run one lens by key with a no-solution-safe envelope (None on failure)."""
    lens_obj = LENS_REGISTRY.get(key)
    if lens_obj is None or not lens_obj.applicable(f):
        return None
    try:
        result = lens_obj.solve(anchor, f)
    except (ZeroDivisionError, ValueError, TypeError):
        return None
    if result is None:
        return None
    result = dict(result)
    result["lens"] = key
    result["lens_label"] = lens_obj.label
    result["lens_family"] = lens_obj.family
    return result


def _run_plan(plan: LensPlan, anchor: float, f: Fundamentals,
              emit, subject: str) -> tuple[dict | None, list[dict]]:
    """Execute a LensPlan with primary fallback.

    Returns (primary_result, cross_results).  If the chosen primary yields no
    solution, walk the remaining applicable lenses (cross order then the rest)
    until one solves — that becomes the effective primary.
    """
    # Build a fallback chain: planned primary, then planned cross, then any
    # other applicable lens (priority order). Dedup, keep order.
    chain: list[str] = []
    for key in ([plan.primary] if plan.primary else []) + plan.cross + list(LENS_REGISTRY):
        if key and key not in chain:
            chain.append(key)

    primary_result: dict | None = None
    used_primary_key: str | None = None
    for key in chain:
        res = _run_lens(key, anchor, f)
        if res is not None:
            primary_result = res
            used_primary_key = key
            if key != plan.primary:
                _safe_emit(emit, phase="lens_fallback", kind="decision",
                           text=f"primary lens {plan.primary} 无解,fallback → {key}",
                           subject=subject)
            break

    # Cross results = every *other* applicable cross/priority lens that solves,
    # excluding the one promoted to primary.  Divergence is itself the Aha, so
    # we keep all that solve (side by side).
    cross_results: list[dict] = []
    cross_keys = [k for k in (plan.cross + list(LENS_REGISTRY))
                  if k != used_primary_key]
    seen: set[str] = set()
    for key in cross_keys:
        if key in seen:
            continue
        seen.add(key)
        res = _run_lens(key, anchor, f)
        if res is not None:
            cross_results.append(res)
        if len(cross_results) >= 2:
            break

    return primary_result, cross_results


# ===========================================================================
# Public API — decode_bet
# ===========================================================================

def decode_bet(source_type: str,
               source_input: "str | dict",
               lang: str = "zh",
               emit=None,
               *,
               llm=None,
               fundamentals_fn: Callable[[str], Fundamentals] = fetch_fundamentals
               ) -> db.BetCard:
    """Decode any bet source into a full BetCard (passive — does NOT store it).

    Parameters
    ----------
    source_type : "market" | "portfolio"  (MVP; analyst_pt/opinion → V2)
    source_input : ticker string (market) or a basket spec (portfolio).
        Portfolio accepts: a comma/space-separated ticker string, a list of
        tickers, a list of {"ticker","weight_pct"} dicts, or {"holdings":[...]}.
    lang : "zh" | "en" — reserved for narration; the skeleton is language-neutral.
    emit : optional ActivityEvent callback (M5).  None ⇒ no streaming side
        effects (batch / test path).
    llm : optional MiroMind client hook.  The deterministic skeleton never calls
        it; reserved for future agentic narration.  Defaults to None so tests
        cost nothing.
    fundamentals_fn : injectable fundamentals fetcher (default = yfinance).
        Tests pass a stub returning hardcoded Fundamentals.

    Returns a db.BetCard. Never raises on bad / empty input — degrades to a
    "数据不足" card instead.
    """
    if source_type == SOURCE_PORTFOLIO:
        return _decode_portfolio(source_input, lang, emit, fundamentals_fn)
    if source_type == SOURCE_MARKET:
        return _decode_market(source_input, lang, emit, fundamentals_fn)

    # Out-of-scope source types (analyst_pt / opinion = V2, or unknown): return a
    # graceful insufficient card rather than raising — keeps callers crash-free.
    subject = _coerce_subject(source_input)
    return _insufficient_card(
        subject=subject or "?",
        source_type=source_type if source_type in (SOURCE_ANALYST_PT, SOURCE_OPINION)
        else SOURCE_MARKET,
        source_ref=subject,
        reason=f"source_type '{source_type}' MVP 不支持(仅 market/portfolio)",
        emit=emit,
    )


# --- Market single-card path -----------------------------------------------

def _decode_market(source_input, lang, emit,
                   fundamentals_fn) -> db.BetCard:
    ticker = _coerce_subject(source_input)
    if not ticker:
        return _insufficient_card(
            subject="?", source_type=SOURCE_MARKET, source_ref=None,
            reason="空输入,无法确定标的", emit=emit,
        )

    _safe_emit(emit, phase="adapter", kind="decision",
               text=f"Market source → 标的 {ticker},锚价=现价",
               subject=ticker)

    # Stage 1 — front adapter: source → anchor price + fundamentals.
    try:
        f = fundamentals_fn(ticker)
    except Exception as exc:  # upstream (yfinance) failure → insufficient, no crash
        return _insufficient_card(
            subject=ticker, source_type=SOURCE_MARKET, source_ref=ticker,
            reason=f"基本面拉取失败: {exc}", emit=emit,
        )

    if f is None:
        return _insufficient_card(
            subject=ticker, source_type=SOURCE_MARKET, source_ref=ticker,
            reason="无基本面数据", emit=emit,
        )

    anchor = f.current_price
    if anchor is None or anchor <= 0:
        return _insufficient_card(
            subject=ticker, source_type=SOURCE_MARKET, source_ref=ticker,
            reason="无有效现价(锚价)", emit=emit,
        )

    # Stage 2 — shared core: pick lenses (deterministic) + reverse-solve.
    plan = select_lenses(f)
    _safe_emit(emit, phase="lens_select", kind="decision",
               text=f"选 lens:{plan.reason}", subject=ticker,
               payload={"primary": plan.primary, "cross": plan.cross})

    if plan.insufficient:
        return _insufficient_card(
            subject=ticker, source_type=SOURCE_MARKET, source_ref=ticker,
            reason=plan.reason, emit=emit, anchor=anchor,
        )

    primary_result, cross_results = _run_plan(plan, anchor, f, emit, ticker)

    if primary_result is None:
        # Every applicable lens returned no solution → this is the DCF-boundary
        # / anchor-mode trigger (Issue #3).  Here we degrade honestly.
        return _insufficient_card(
            subject=ticker, source_type=SOURCE_MARKET, source_ref=ticker,
            reason="所有适用 lens 反解无解(传统估值无法解释该价格,候选 anchor mode)",
            emit=emit, anchor=anchor,
        )

    _safe_emit(emit, phase="solve", kind="computation",
               text=f"primary {primary_result['lens']} → "
                    f"{primary_result['implied_label']}={primary_result['implied_value']:.2f}",
               subject=ticker, payload=primary_result)

    # Stage 3 — assembler: → BetCard.  `bet` = the primary implied metric value
    # so the card carries a single comparable number; full lens detail rides in
    # source_ref-adjacent structures that M3/M4 read off the run (R2 band lives
    # in the lens result, surfaced once #3 wires runs).
    card = db.BetCard(
        subject=ticker,
        source_type=SOURCE_MARKET,
        card_kind=db.SINGLE,
        source_ref=str(source_input) if not isinstance(source_input, dict) else ticker,
        bet=float(primary_result["implied_value"]),
    )
    # Attach decode detail as a plain attribute (not persisted by save_card, but
    # available to the caller / M4 in-process).  Keeps decode_bet self-contained.
    card.decode_detail = {                       # type: ignore[attr-defined]
        "anchor_price": anchor,
        "anchor_type": "market",
        "primary_lens": primary_result,
        "cross_lenses": cross_results,
        "lens_plan": {"primary": plan.primary, "cross": plan.cross,
                      "reason": plan.reason},
        "lang": lang,
    }
    _safe_emit(emit, phase="assemble", kind="decision",
               text=f"组装 BetCard 完成({1 + len(cross_results)} 个 lens 视角)",
               subject=ticker, payload=None)
    return card


# --- Portfolio aggregate-card path -----------------------------------------

def _decode_portfolio(source_input, lang, emit,
                      fundamentals_fn) -> db.BetCard:
    holdings_spec = _parse_portfolio(source_input)
    subject = "Portfolio"

    if not holdings_spec:
        return _insufficient_card(
            subject=subject, source_type=SOURCE_PORTFOLIO, source_ref=None,
            reason="空持仓清单", emit=emit, card_kind=db.PORTFOLIO,
        )

    _safe_emit(emit, phase="adapter", kind="decision",
               text=f"Portfolio source → {len(holdings_spec)} 个持仓,逐股解码后聚合",
               subject=subject)

    holdings: list[db.Holding] = []
    # Per-ticker primary metric (used by R1 theme aggregation downstream / #3).
    per_ticker: dict[str, dict] = {}
    for spec in holdings_spec:
        tk = spec["ticker"]
        weight = spec.get("weight_pct")
        holdings.append(db.Holding(ticker=tk, weight_pct=weight))
        # Best-effort decode of each leg (never let one bad ticker sink the card).
        try:
            leg = _decode_market(tk, lang, None, fundamentals_fn)
            detail = getattr(leg, "decode_detail", None)
            if detail and detail.get("primary_lens"):
                per_ticker[tk] = detail["primary_lens"]
        except Exception:
            pass

    card = db.BetCard(
        subject=subject,
        source_type=SOURCE_PORTFOLIO,
        card_kind=db.PORTFOLIO,
        source_ref=", ".join(h.ticker for h in holdings),
        bet=None,                       # portfolio carries no single scalar bet
        holdings=holdings,
        theme_exposures=[],             # R1/theme aggregation arrives with #3
    )
    card.decode_detail = {              # type: ignore[attr-defined]
        "anchor_type": "portfolio",
        "per_ticker_primary": per_ticker,
        "holding_count": len(holdings),
        "decoded_legs": len(per_ticker),
        "lang": lang,
    }
    _safe_emit(emit, phase="assemble", kind="decision",
               text=f"组装组合卡完成({len(holdings)} 持仓,{len(per_ticker)} 个成功解码)",
               subject=subject, payload=None)
    return card


# ===========================================================================
# Helpers
# ===========================================================================

def _coerce_subject(source_input) -> str:
    """Pull a ticker/subject string out of various market source shapes."""
    if source_input is None:
        return ""
    if isinstance(source_input, str):
        return source_input.strip().upper()
    if isinstance(source_input, dict):
        tk = source_input.get("ticker") or source_input.get("subject") or ""
        return str(tk).strip().upper()
    return str(source_input).strip().upper()


def _parse_portfolio(source_input) -> list[dict]:
    """Normalize many portfolio input shapes into [{"ticker","weight_pct"?}].

    Accepts: list[str], list[dict], {"holdings":[...]}, or a comma/space/newline
    separated string. Bad / empty entries are dropped (never raises).
    """
    if source_input is None:
        return []

    raw: list = []
    if isinstance(source_input, dict):
        raw = source_input.get("holdings") or source_input.get("tickers") or []
    elif isinstance(source_input, (list, tuple)):
        raw = list(source_input)
    elif isinstance(source_input, str):
        # split on comma / whitespace / newline
        parts = [p for chunk in source_input.split(",") for p in chunk.split()]
        raw = [p for p in parts if p]
    else:
        return []

    out: list[dict] = []
    for item in raw:
        if isinstance(item, str):
            tk = item.strip().upper()
            if tk:
                out.append({"ticker": tk})
        elif isinstance(item, dict):
            tk = (item.get("ticker") or item.get("symbol") or "").strip().upper()
            if not tk:
                continue
            w = item.get("weight_pct", item.get("weight"))
            try:
                w = float(w) if w is not None else None
            except (TypeError, ValueError):
                w = None
            out.append({"ticker": tk, "weight_pct": w})
    return out


def _insufficient_card(*, subject: str, source_type: str,
                       source_ref: str | None, reason: str, emit,
                       anchor: float | None = None,
                       card_kind: str = db.SINGLE) -> db.BetCard:
    """Build a graceful "数据不足" BetCard (no crash, no fabrication)."""
    _safe_emit(emit, phase="insufficient", kind="decision",
               text=f"数据不足:{reason}", subject=subject)
    card = db.BetCard(
        subject=subject,
        source_type=source_type,
        card_kind=card_kind,
        source_ref=source_ref,
        bet=None,
    )
    card.decode_detail = {              # type: ignore[attr-defined]
        "status": "insufficient",
        "reason": reason,
        "anchor_price": anchor,
    }
    return card


if __name__ == "__main__":  # pragma: no cover - manual smoke (hits yfinance)
    import sys
    tk = sys.argv[1] if len(sys.argv) > 1 else "NVDA"
    c = decode_bet(SOURCE_MARKET, tk)
    print(f"subject={c.subject} bet={c.bet}")
    print(c.decode_detail)  # type: ignore[attr-defined]
