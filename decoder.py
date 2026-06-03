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
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import db
import evidence
import narrative
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
    # --- classification hints (Issue #3, anchor mode gate) ---
    # `industry` (yfinance sector/industry string) and `tags` (free-form labels)
    # feed the deterministic AI-composite detector. Both optional / injectable so
    # tests pin the classification without any network.
    industry: str | None = None
    tags: list[str] = field(default_factory=list)
    # Company's OWN trailing revenue CAGR (computed from financials in
    # fetch_fundamentals; the DCF lens's upper "historical continuation" anchor).
    hist_revenue_cagr: float | None = None

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


def _trailing_revenue_cagr(financials, years: int = 5) -> float | None:
    """Trailing revenue CAGR from an already-pulled yfinance financials frame
    (most-recent first).  None when <2 usable annual revenue points.  Pure compute,
    no network — so the DCF lens's upper anchor needs no extra fetch and tests can
    inject the value via the stub Fundamentals."""
    try:
        for name in ("Total Revenue", "TotalRevenue"):
            if name in financials.index:
                vals = []
                for v in financials.loc[name].tolist()[:years]:
                    try:
                        fv = float(v)
                    except (TypeError, ValueError):
                        continue
                    if not math.isnan(fv) and fv > 0:
                        vals.append(fv)
                if len(vals) >= 2 and vals[-1] > 0:
                    n = len(vals) - 1
                    c = (vals[0] / vals[-1]) ** (1.0 / n) - 1.0
                    return float(c) if not math.isnan(c) else None
                return None
    except Exception:
        return None
    return None


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

    industry = " / ".join(
        str(info.get(k)) for k in ("sector", "industry") if info.get(k)
    ) or None
    # Company's own trailing revenue CAGR from the financials pulled above (no
    # extra network) — the DCF lens's upper "historical continuation" anchor.
    hist_revenue_cagr = _trailing_revenue_cagr(financials)

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
        industry=industry,
        # Harvest AI-theme SPECIFIC keywords from the business summary so the
        # deterministic classifier works on REAL yfinance data (the bare
        # `industry` label carries none).  Specific-only → no mis-gating.
        tags=_summary_specific_tags(info.get("longBusinessSummary")),
        hist_revenue_cagr=hist_revenue_cagr,
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
    # Net-cash company (cash > debt + equity value): enterprise value goes
    # negative, and a negative "implied multiple" is meaningless — it would leak
    # a nonsense number into cross-validation / evidence.  No solution instead.
    if ev <= 0:
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
    # Live risk-free (10Y ^TNX, cached + offline fallback) → WACC uses a REAL
    # rate, not a hardcoded 4.5%.  Margin is the real TTM FCF margin.
    live_rf, rf_src = reverse_dcf.fetch_risk_free()
    consensus_wacc = reverse_dcf.compute_wacc(data.beta, risk_free=live_rf)
    base_margin = (
        max(data.fcf_ttm / data.revenue_ttm, 0.05) if data.revenue_ttm else 0.15
    )
    # The company's OWN trailing revenue CAGR — kept REAL for the implied-vs-history
    # contrast, but CAPPED for the upper anchor (extrapolating an extreme trailing
    # CAGR flat for 5y is absurd; a proper fade is a later tier). Computed in
    # fetch_fundamentals from already-pulled financials (tests inject via the stub).
    hist_cagr = getattr(f, "hist_revenue_cagr", None)
    hist_capped = (min(hist_cagr, reverse_dcf.HIST_CAGR_CAP)
                   if hist_cagr is not None else None)

    # Two business-value ANCHORS (both at live WACC + real TTM margin) so the base
    # is a defensible RANGE, not one fixed number — and built ONLY from the
    # company's own data (no third-party forecast, per the product thesis):
    #   • lower = conservative zero-growth (earnings never grow)
    #   • upper = historical continuation (5y at the company's own past CAGR)
    A = reverse_dcf.Assumptions
    _dcf = reverse_dcf.dcf_equity_value_per_share
    def _feasible(x):
        return float(x) if isinstance(x, (int, float)) and x > -1e8 else None
    base_low = _feasible(_dcf(A(0.0, 0.0, base_margin, consensus_wacc), data))
    base_high = None
    if hist_capped is not None:
        base_high = _feasible(
            _dcf(A(max(hist_capped, 0.0), reverse_dcf.TERMINAL_GROWTH, base_margin, consensus_wacc), data)
        )
    if base_high is None:
        base_high = base_low                       # no usable history → range collapses to floor
    if (base_low is not None and base_high is not None and base_high < base_low):
        base_low, base_high = base_high, base_low  # keep low ≤ high

    # Narrative-premium reference = the UPPER anchor: how far the price sits above
    # "even continuing the company's own history would justify".
    baseline_price = base_high if base_high is not None else base_low

    # Consensus envelope for the reverse-solve (implied CAGR holds the OTHER vars
    # fixed; the solver overwrites its cagr field, so that value is moot).
    consensus = A(
        revenue_cagr_5y=(hist_cagr if hist_cagr is not None else 0.15),
        terminal_growth=reverse_dcf.TERMINAL_GROWTH,
        terminal_fcf_margin=base_margin,
        wacc=consensus_wacc,
    )

    # Point estimate: the 5y revenue CAGR the price implies (the market's bet).
    # CAN be None (TSLA-style: price outside the feasible CAGR range).
    point = reverse_dcf.reverse_solve(anchor, consensus, "revenue_cagr_5y", data)

    # Neither a baseline NOR a point → the DCF lens has nothing to say.
    if point is None and baseline_price is None:
        return None

    band = None
    if point is not None:
        perturbations = {
            "revenue_cagr_5y": (0.05, 0.30),
            "terminal_growth": (0.015, 0.035),
            "terminal_fcf_margin": (base_margin * 0.6, base_margin * 1.4),
            "wacc": (consensus_wacc - 0.015, consensus_wacc + 0.015),
        }
        band = reverse_dcf.monte_carlo_implied(
            data, "revenue_cagr_5y", consensus, perturbations
        )

    # Shared envelope: live rate + BOTH anchors + the company's own history, so
    # db.py can render the range + an implied-vs-history contrast from a card
    # already in the DB.  baseline_dcf_price stays = upper anchor (downstream
    # narrative-premium reference) to minimize churn.
    _extra = dict(
        band=band,
        baseline_dcf_price=baseline_price,
        baseline_dcf_low=base_low,
        baseline_dcf_high=base_high,
        hist_cagr=hist_cagr,
        hist_cagr_capped=hist_capped,
        risk_free_used=live_rf,
        risk_free_source=rf_src,
        consensus_wacc=consensus_wacc,
        consensus_terminal_growth=consensus.terminal_growth,
        consensus_terminal_fcf_margin=base_margin,
    )
    if point is None:
        return {
            "metric": "implied_revenue_cagr_5y",
            "implied_value": None,           # no feasible implied CAGR
            "implied_label": "隐含 5 年营收 CAGR(无可行解,仅余基础估值区间)",
            "unit": "",
            "point_solved": False,
            **_extra,
        }
    return _result(
        "implied_revenue_cagr_5y", point, unit="",
        implied_label="隐含 5 年营收 CAGR",
        implied_cagr=point,              # the market-implied growth (for db contrast)
        point_solved=True,
        **_extra,
    )


# ===========================================================================
# AI-composite detector (anchor-mode front gate — Issue #3 decision 9)
# ===========================================================================
#
# PRD 模块 2 决策 9: when the subject is an "AI 复合体" (GPU / 存储 / 光模块 /
# AI 应用), narrative/theme pricing dominates and anchor mode is the *primary*
# decode — NOT a fallback.  The gate is a deterministic keyword/tag rule so the
# same subject always classifies the same way (no LLM, reproducible, free).
#
# We match against: explicit `tags`, the `industry` string, and a small ticker
# whitelist for the canonical AI-complex names used in the demo.  Keep this
# conservative — a *normal* stock must never be mis-gated into anchor mode.

# Theme buckets → the keywords that imply membership in the AI complex.
#
# Each theme has two tiers of keywords:
#   - SPECIFIC: AI-proprietary terms that fire on their own (e.g. "hbm", "gpu").
#     These are unambiguous — a warehouse REIT will never carry "hbm".
#   - GENERIC: terms that ALSO appear in plenty of non-AI businesses
#     ("memory", "storage", "optical") and must NOT fire alone.  A self-storage
#     operator, a cold-storage warehouse REIT, or a senior-care "memory care"
#     facility all match a bare "storage"/"memory" — but none is an AI complex.
#     Generic terms only count when a semiconductor / tech sector signal
#     co-occurs (see _SEMI_TECH_SIGNALS), so the gate stays conservative.
AI_COMPOSITE_THEMES: dict[str, dict[str, tuple[str, ...]]] = {
    "AI 基础设施": {
        "specific": (
            "gpu", "accelerator", "ai chip", "ai 芯片",
            "ai infrastructure", "ai 基础设施",
        ),
        "generic": ("datacenter", "data center", "数据中心"),
    },
    "存储": {
        # HBM/DRAM/NAND/GDDR are AI-memory specific; bare "memory"/"storage" are
        # generic (storage REITs, memory-care facilities) and gated on a tech
        # signal.
        "specific": ("hbm", "dram", "nand", "gddr", "高带宽内存"),
        "generic": ("memory", "storage", "存储"),
    },
    "光模块": {
        "specific": ("transceiver", "光模块", "光通信", "silicon photonics"),
        "generic": ("optical",),
    },
    "AI 应用": {
        "specific": ("ai application", "ai 应用", "generative ai", "llm", "copilot"),
        "generic": (),
    },
}

# Sector / industry signals that, when present alongside a GENERIC keyword,
# confirm the subject is in the AI/semiconductor complex rather than (say) a
# storage REIT or a senior-care operator that merely shares a word.
_SEMI_TECH_SIGNALS: tuple[str, ...] = (
    "semiconductor", "半导体", "technology", "科技", "chip", "芯片",
    "integrated circuit", "集成电路", "fabless", "foundry", "晶圆",
    "ai", "artificial intelligence", "人工智能", "compute", "computing",
    "hardware", "电子",
)


def _has_semi_tech_signal(low: str) -> bool:
    """True when a lowercased text carries a semiconductor/tech sector signal."""
    return any(sig in low for sig in _SEMI_TECH_SIGNALS)


def _ai_theme_for(text: str, *, corpus: str | None = None) -> str | None:
    """Return the AI-complex theme a lowercased `text` matches, or None.

    SPECIFIC keywords match on their own.  GENERIC keywords only match when a
    semiconductor/tech sector signal co-occurs — checked against `corpus`
    (tags + industry joined) so a generic term in a tag can still be confirmed
    by the industry string and vice-versa.  Defaults `corpus` to `text` when not
    supplied.
    """
    low = text.lower()
    ctx = (corpus or text).lower()
    has_signal = _has_semi_tech_signal(ctx)
    for theme, tiers in AI_COMPOSITE_THEMES.items():
        if any(kw in low for kw in tiers.get("specific", ())):
            return theme
    # Generic tier: only after no specific match, and only with a tech signal.
    if has_signal:
        for theme, tiers in AI_COMPOSITE_THEMES.items():
            if any(kw in low for kw in tiers.get("generic", ())):
                return theme
    return None


def _summary_specific_tags(text: str | None) -> list[str]:
    """Harvest AI-theme SPECIFIC keywords present in a business-summary string.

    `fetch_fundamentals` feeds this from yfinance's `longBusinessSummary` so the
    deterministic classifier works on REAL data.  yfinance's `industry` is a bare
    label ("Semiconductors") that carries none of the specific keywords, but the
    business summary does ("...graphics processing units (GPUs)...data center...").
    Only the SPECIFIC (unambiguous) tier is harvested — never the generic tier —
    so a company is never mis-gated into anchor mode by a shared word ("memory",
    "storage", "optical") that happened to appear in prose.  Reproducible, no LLM.
    """
    if not text:
        return []
    low = str(text).lower()
    hits: list[str] = []
    for tiers in AI_COMPOSITE_THEMES.values():
        for kw in tiers.get("specific", ()):
            if kw in low and kw not in hits:
                hits.append(kw)
    return hits


def is_ai_composite(f: Fundamentals) -> tuple[bool, str | None]:
    """Deterministic AI-complex classification (PRD 决策 9).

    Returns (is_composite, theme).  Decision order:
      1. any explicit tag matches an AI theme keyword
      2. the industry string matches an AI theme keyword
    Otherwise (False, None).

    The gate keys off *classification signals* (industry / tags) that
    `fetch_fundamentals` populates from yfinance — NOT a bare ticker whitelist.
    Generic, easily-shared words ("memory", "storage", "optical") only trip the
    gate when a semiconductor/tech sector signal co-occurs, so a self-storage
    REIT, a cold-storage warehouse operator, or a senior "memory care" facility
    is never mis-gated into anchor mode.  Reproducible, no LLM, no collision with
    the traditional ticker-agnostic lens tree.
    """
    # Build a combined corpus so a generic keyword in `tags` can be confirmed by
    # a tech signal living in `industry` (and vice-versa).
    corpus = " ".join(
        [str(t) for t in (f.tags or [])] + ([f.industry] if f.industry else [])
    )
    for tag in (f.tags or []):
        theme = _ai_theme_for(str(tag), corpus=corpus)
        if theme:
            return True, theme
    if f.industry:
        theme = _ai_theme_for(f.industry, corpus=corpus)
        if theme:
            return True, theme
    return False, None


# ===========================================================================
# Anchor-lens registry (2nd tier — TAM / 期权 / 类比 / 叙事)
# ===========================================================================
#
# Anchor lenses decode the *psychological anchor* a trader prices off when
# traditional valuation breaks (or when narrative dominates).  Each one turns
# the gap between the anchor price and a base business value into a priced
# narrative/option component.
#
# Cost discipline (Issue #3): every anchor lens takes an optional `llm` hook
# that defaults to None.  With llm=None the lens emits a deterministic stub
# component (no Deep Research call, zero API cost) so tests are pinned + free.
# A real MiroMind Deep Research client can be injected later to ground the
# claim/probability/evidence in live research.

# An anchor lens solve takes (gap_value, anchor, base_value, f, llm) and returns
# a *component* dict in the generalized Bet schema, or None to abstain.
AnchorSolve = Callable[[float, float, float, Fundamentals, Any], Optional[dict]]


@dataclass
class AnchorLens:
    key: str
    label: str
    solve: AnchorSolve


ANCHOR_LENS_REGISTRY: dict[str, AnchorLens] = {}


def anchor_lens(key: str, label: str) -> Callable[[AnchorSolve], AnchorSolve]:
    """Register a 2nd-tier anchor lens by `key`."""
    def _register(fn: AnchorSolve) -> AnchorSolve:
        ANCHOR_LENS_REGISTRY[key] = AnchorLens(key=key, label=label, solve=fn)
        return fn
    return _register


def register_anchor_lens(lens_obj: AnchorLens) -> None:
    """Imperative anchor-lens registration."""
    ANCHOR_LENS_REGISTRY[lens_obj.key] = lens_obj


def _anchor_component(*, lens_key: str, lens_label: str, claim: str,
                      implied_amount: float, implied_assumption: str,
                      probability: float | None = None,
                      theme: str | None = None,
                      evidence: list | None = None) -> dict:
    """Uniform anchor-component envelope — a generalized Bet (M1 schema), NOT a
    new top-level structure.  `evidence` defaults to an honest empty placeholder
    (Issue #4 fills it; we never fabricate)."""
    return {
        "lens": lens_key,
        "lens_label": lens_label,
        "claim": claim,
        "implied_amount": float(implied_amount),     # $/share this component prices
        "implied_assumption": implied_assumption,    # what you must believe
        "probability": probability,                  # implied prob (None if n/a)
        "theme": theme,                              # AI-complex theme tag (R1)
        "evidence": list(evidence or []),            # placeholder until #4
    }


@anchor_lens("narrative", "叙事锚")
def _anchor_narrative(gap: float, anchor: float, base_value: float,
                      f: Fundamentals, llm) -> dict | None:
    """Narrative anchor: the slice of price the growth/AI story carries beyond
    the base business value.  Deterministic stub when llm is None."""
    if gap <= 0:
        return None
    if llm is not None:  # pragma: no cover - real Deep Research path (not in tests)
        return _anchor_via_llm("narrative", "叙事锚", gap, anchor, base_value, f, llm)
    is_ai, theme = is_ai_composite(f)
    premium = (gap / anchor) if anchor else 0.0
    factor = _style_factor(f, premium)
    return _anchor_component(
        lens_key="narrative", lens_label="叙事锚",
        claim=("市场为 AI 增长叙事支付的溢价" if is_ai
               else f"市场为「{factor}」支付的溢价"),
        implied_amount=gap,
        implied_assumption="叙事兑现:增长曲线显著超过传统估值锚定的水平",
        probability=None,
        # AI 赛道主题优先;否则用具体因子名(不再是空泛的死字符串「增长叙事」)
        theme=theme or factor,
    )


@anchor_lens("option", "期权锚")
def _anchor_option(gap: float, anchor: float, base_value: float,
                   f: Fundamentals, llm) -> dict | None:
    """Option anchor: price the call-option-like upside on a low-probability,
    high-payoff outcome.  Stub splits a slice of the gap as option value."""
    if gap <= 0:
        return None
    if llm is not None:  # pragma: no cover - real Deep Research path
        return _anchor_via_llm("option", "期权锚", gap, anchor, base_value, f, llm)
    return _anchor_component(
        lens_key="option", lens_label="期权锚",
        claim="对小概率、高赔付结局的看涨期权式定价",
        implied_amount=gap,
        implied_assumption="存在尾部上行情景(新市场/平台级突破)被市场以期权方式计价",
        probability=0.25,   # stub implied probability — replaced by #4 evidence
    )


@anchor_lens("tam", "TAM 锚")
def _anchor_tam(gap: float, anchor: float, base_value: float,
                f: Fundamentals, llm) -> dict | None:
    """TAM anchor: price implies capturing a slice of a large addressable market.
    Stub expresses the gap as an implied incremental-TAM-capture component."""
    if gap <= 0:
        return None
    if llm is not None:  # pragma: no cover - real Deep Research path
        return _anchor_via_llm("tam", "TAM 锚", gap, anchor, base_value, f, llm)
    return _anchor_component(
        lens_key="tam", lens_label="TAM 锚",
        claim="价格隐含对一个远大于当前营收的可寻址市场的份额捕获",
        implied_amount=gap,
        implied_assumption="目标 TAM 在预测期内大幅扩张且公司维持/扩大份额",
    )


@anchor_lens("analogy", "类比锚")
def _anchor_analogy(gap: float, anchor: float, base_value: float,
                    f: Fundamentals, llm) -> dict | None:
    """Analogy anchor: price anchored to a comparable historical winner's
    trajectory.  Stub frames the gap as a comparable-path premium."""
    if gap <= 0:
        return None
    if llm is not None:  # pragma: no cover - real Deep Research path
        return _anchor_via_llm("analogy", "类比锚", gap, anchor, base_value, f, llm)
    return _anchor_component(
        lens_key="analogy", lens_label="类比锚",
        claim="价格类比于某个历史赢家的成长轨迹",
        implied_amount=gap,
        implied_assumption="本标的将复制对标公司的份额/利润轨迹",
    )


def _anchor_via_llm(key, label, gap, anchor, base_value, f, llm):  # pragma: no cover
    """Deep Research grounded anchor component (live path, not exercised by the
    zero-cost test suite).  Kept thin + isolated so the deterministic path above
    never accidentally hits the network/API.  The injected `llm` is expected to
    expose a `call_deepresearch(prompt)->dict` interface (see client.py)."""
    prompt = (
        f"Decode the ${gap:,.2f}/share gap between {f.ticker}'s anchor price "
        f"${anchor:,.2f} and its base business value ${base_value:,.2f} under "
        f"the '{label}' frame. Return claim, implied_amount, implied_assumption, "
        f"probability, evidence."
    )
    try:
        res = llm.call_deepresearch(prompt)
    except Exception:
        # Live failure must not crash decode — degrade to the deterministic stub.
        return _anchor_component(
            lens_key=key, lens_label=label,
            claim=f"{label}成分(Deep Research 失败,留空)",
            implied_amount=gap,
            implied_assumption="(证据查询失败,诚实留空)",
        )
    return _anchor_component(
        lens_key=key, lens_label=label,
        claim=res.get("claim", f"{label}成分"),
        implied_amount=res.get("implied_amount", gap),
        implied_assumption=res.get("implied_assumption", ""),
        probability=res.get("probability"),
        evidence=res.get("evidence") or [],
    )


# Deterministic priority order in which anchor lenses are tried for the gap.
_ANCHOR_PRIORITY = ["narrative", "option", "tam", "analogy"]


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
    # General guard: a *multiple*-family lens reporting a negative implied value
    # is definitionally invalid (a negative P/E, P/S, EV/EBITDA, P/FCF, P/B has
    # no meaning — it signals a negative denominator/EV slipped the applicable
    # gate).  Treat it as no-solution so the nonsense never reaches cross-
    # validation or evidence.  The DCF family is exempt: an implied CAGR can be
    # legitimately negative (price below the no-growth value).
    if (lens_obj.family == "multiple"
            and isinstance(result.get("implied_value"), (int, float))
            and result["implied_value"] < 0):
        return None
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
        # A lens that returns an envelope with no implied_value (e.g. the DCF
        # lens when its point reverse-solve had no root but a baseline survives)
        # is NOT a valid *primary* — it carries no comparable scalar for
        # card.bet.  Skip it for primary selection; it can still appear among the
        # cross results below, where its DCF baseline feeds anchor mode.
        if res is not None and res.get("implied_value") is not None:
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
# Anchor mode (Issue #3) — base value + narrative/option components + 对账
# ===========================================================================

# How close Σ(components) + base must land to the anchor to call it reconciled.
_RECON_TOL_PCT = 0.01  # 1% of anchor

# Narrative-premium mode gate (data-source-agnostic).  When the DCF base business
# value explains less than (1 - this) of the anchor price, narrative/theme pricing
# dominates → anchor mode is PRIMARY even without an AI keyword.  Calibrated so a
# plain compounder (COST ≈ 13-19% premium) stays traditional while a narrative-
# priced name (NVDA ≈ 69-77%, TSLA ≈ 94%) gates into anchor.  Only fires off a
# REAL DCF baseline (base_src == "dcf_baseline"), never a degenerate base=0.
_NARRATIVE_PREMIUM_GATE = 0.50

# Style / factor labels — the cross-holding "common bet" a portfolio aggregates
# on.  Derived from the narrative premium (share of price the DCF base business
# value cannot explain), NOT a keyword guess: a high premium means the price is
# carried by a growth/story bet rather than by current earnings.  Because the
# label is a function of a real number, holdings with the same premium band line
# up — so "你以为分散持有 N 只,实际都押在『高估值成长』" emerges from the data
# rather than from a fixed string.  Honest + reproducible (no LLM).
_FACTOR_HIGH_GROWTH = "高估值成长"        # premium ≥ 50% (also forces anchor mode)
_FACTOR_GROWTH_PREMIUM = "成长溢价"       # 30% ≤ premium < 50%
_FACTOR_EARNINGS_ANCHORED = "盈利锚定"    # premium < 30% (most of price is base)


def _style_factor(f: Fundamentals, narrative_premium: float | None) -> str:
    """Map a card's narrative premium to a concrete style/factor label.

    This is the handle a portfolio aggregates on (保 aha): a NVDA+AAPL+TSLA basket
    all carries a high premium → all map to 「高估值成长」 → the portfolio surfaces
    a genuine common bet instead of the vague 「增长叙事」.  Crucially the label
    depends only on the premium, so even when the AI-keyword gate misses (real
    yfinance text), the cross-holding alignment still holds."""
    p = narrative_premium or 0.0
    if p >= _NARRATIVE_PREMIUM_GATE:
        return _FACTOR_HIGH_GROWTH
    if p >= 0.30:
        return _FACTOR_GROWTH_PREMIUM
    return _FACTOR_EARNINGS_ANCHORED


def _base_business_value(anchor: float, f: Fundamentals,
                         cross_results: list[dict]) -> tuple[float, str, float]:
    """Conservative *base business value* the traditional lenses can defend.

    Preference order:
      1. DCF consensus baseline price (the business-value floor) — this is
         ALWAYS available now: `_lens_dcf` returns the baseline even when its
         point reverse-solve has no root (the original bug threw it away, so an
         undervalued stock fell to base=0 and looked like 100% narrative).
      2. 0 (no defensible base → the whole price is narrative/option)

    Returns (clamped_base, source_label, raw_base).  `clamped_base` is in
    [0, anchor] so the residual gap is non-negative; `raw_base` is the
    *unclamped* DCF baseline so the caller can detect base > anchor (the stock
    is UNDERVALUED relative to its DCF) and emit an honest "no narrative gap"
    card instead of a misleading 100%-narrative one.
    """
    raw_base = 0.0
    src = "none"
    # Look for a DCF view among already-solved cross lenses first.  Accept a DCF
    # envelope even when its implied_value is None — what we need here is its
    # baseline_dcf_price, which survives a no-root point solve.
    dcf_view = next((r for r in cross_results if r.get("lens") == "dcf"
                     and r.get("baseline_dcf_price") is not None), None)
    if dcf_view is None:
        # Run DCF explicitly to obtain its consensus baseline (business value).
        dcf_view = _run_lens("dcf", anchor, f)
    if dcf_view is not None and dcf_view.get("baseline_dcf_price") is not None:
        raw_base = float(dcf_view["baseline_dcf_price"])
        src = "dcf_baseline"
    # Clamp to [0, anchor]: a base above the anchor (DCF says undervalued) means
    # anchor mode has no narrative gap to decompose; clamp to anchor so the gap
    # floors at 0.  Keep raw_base for the caller's undervalued detection.
    clamped = max(0.0, min(raw_base, anchor))
    return clamped, src, raw_base


def _run_anchor_mode(anchor: float, f: Fundamentals, emit, subject: str,
                     cross_results: list[dict], *, llm=None) -> dict:
    """Decode an anchor-priced bet into base business value + priced
    narrative/option components, reconciled to the anchor price.

    Returns an anchor-mode detail dict:
        {
          "anchor_price", "base_business_value", "base_source",
          "components": [generalized-Bet component, ...],
          "reconciliation": {"sum": .., "anchor": .., "residual": ..,
                             "reconciled": bool, "tolerance": ..},
          "theme_exposures": [db.ThemeExposure, ...],   # R1
        }
    Each component reuses the generalized Bet schema (claim / implied_amount /
    implied_assumption / probability / evidence) — no new top-level structure.
    """
    base, base_src, raw_base = _base_business_value(anchor, f, cross_results)
    gap = max(0.0, anchor - base)
    # Undervalued: DCF business value exceeds the anchor price → there is NO
    # narrative/option gap to decompose.  This is the honest opposite of a
    # 100%-narrative card and must be surfaced as such (the original bug,
    # discarding the DCF baseline, made these look like 100% narrative).
    undervalued = base_src == "dcf_baseline" and raw_base > anchor

    _safe_emit(emit, phase="anchor_base", kind="computation",
               text=(f"基础业务价值={base:.2f}({base_src}),"
                     + (f"DCF 基础估值 {raw_base:.2f} > 锚价 {anchor:.2f} → 低估,无叙事 gap"
                        if undervalued
                        else f"叙事/期权待对账缺口={gap:.2f}")),
               subject=subject,
               payload={"base": base, "gap": gap, "raw_base": raw_base,
                        "undervalued": undervalued})

    components: list[dict] = []
    if gap > 0:
        # Run anchor lenses in priority order; first applicable component takes
        # the residual gap (so Σ reconciles exactly).  Each lens may abstain.
        for key in _ANCHOR_PRIORITY:
            lens_obj = ANCHOR_LENS_REGISTRY.get(key)
            if lens_obj is None:
                continue
            try:
                comp = lens_obj.solve(gap, anchor, base, f, llm)
            except Exception:
                comp = None
            if comp is not None:
                components.append(comp)
                # Defensive .get: the emit text is built even when emit is None
                # (it's an argument), so a malformed component missing
                # implied_amount/lens_label must not crash here either.
                _safe_emit(emit, phase="anchor_component", kind="decision",
                           text=f"{comp.get('lens_label', comp.get('lens', '锚成分'))} "
                                f"→ 隐含 ${float(comp.get('implied_amount', 0) or 0):.2f}",
                           subject=subject, payload=comp)
                break  # one component carries the residual (keeps Σ exact)

    # 加总对账: base + Σ(component implied_amount) ≈ anchor.  Defensive .get so a
    # malformed component (missing implied_amount) can never crash reconciliation.
    comp_sum = sum(c.get("implied_amount", 0) or 0 for c in components)
    total = base + comp_sum
    residual = anchor - total
    reconciled = abs(residual) <= max(_RECON_TOL_PCT * anchor, 1e-6)

    _safe_emit(emit, phase="anchor_reconcile", kind="computation",
               text=f"对账:基础 {base:.2f} + 成分 {comp_sum:.2f} = {total:.2f} "
                    f"≈ 锚价 {anchor:.2f}(残差 {residual:.2f},{'通过' if reconciled else '超容差'})",
               subject=subject,
               payload={"sum": total, "anchor": anchor, "residual": residual,
                        "reconciled": reconciled})

    # R1 — theme exposures off the priced narrative/anchor components.
    theme_exposures = _theme_exposures_from_anchor(f, anchor, base, components)

    return {
        "base_business_value": base,
        "base_source": base_src,
        "raw_base_business_value": raw_base,   # unclamped DCF baseline
        "undervalued": undervalued,            # base > anchor → no narrative gap
        "components": components,
        "reconciliation": {
            "sum": total,
            "anchor": anchor,
            "residual": residual,
            "reconciled": reconciled,
            "tolerance": max(_RECON_TOL_PCT * anchor, 1e-6),
        },
        "theme_exposures": theme_exposures,
    }


def _theme_exposures_from_anchor(f: Fundamentals, anchor: float, base: float,
                                 components: list[dict]) -> list[db.ThemeExposure]:
    """R1: turn priced anchor components into card-level ThemeExposure rows.

    Two complementary dimensions ride out together:
      • a 赛道/主题 row per priced component (AI composites carry their AI theme,
        e.g. NVDA → 「AI 基础设施」);
      • a single 因子/风格 row (`_style_factor` of the narrative premium) that is
        the cross-holding handle a portfolio aggregates on — holdings line up by
        premium band, so a NVDA+AAPL+TSLA basket's common bet (「高估值成长」) is
        real, not the vague fixed string 「增长叙事」.

    Exposure % = implied_amount / anchor (share of price the theme carries); the
    factor row uses the premium itself.  De-duped so the AI theme and the factor
    never double-count when they coincide.
    """
    rows: list[db.ThemeExposure] = []
    is_ai, ai_theme = is_ai_composite(f)
    premium = (max(0.0, anchor - base) / anchor) if anchor else 0.0
    factor = _style_factor(f, premium)
    seen: set[str] = set()

    def _add(theme: str | None, pct: float | None) -> None:
        if not theme or theme in seen:
            return
        seen.add(theme)
        rows.append(db.ThemeExposure(
            theme=theme,
            exposure_pct=round(pct, 2) if pct is not None else None,
            contributing_tickers=[f.ticker],
            is_concentration_risk=bool(pct is not None and pct >= 50.0),
        ))

    for c in components:
        amt = c.get("implied_amount") or 0.0
        if amt <= 0:
            continue
        # 赛道主题: AI 票用 AI 主题;否则落到具体因子名(不再是 lens_label 裸名)
        theme = c.get("theme") or (ai_theme if is_ai else factor)
        pct = (amt / anchor * 100.0) if anchor else None
        _add(theme, pct)

    # AI 赛道保底行: 即便 component 用了更窄的标签,NVDA 类卡也总暴露 AI 主题。
    if is_ai and ai_theme:
        narrative_amt = sum(c.get("implied_amount") or 0.0 for c in components)
        pct = (narrative_amt / anchor * 100.0) if anchor else None
        _add(ai_theme, pct)

    # 因子/风格行(必出): 所有有叙事溢价的卡都对齐到它 → 组合 aha 的来源。即便 AI
    # 关键词门未命中(真实 yfinance 文本只有裸行业名),这一行仍让跨持仓对齐成立。
    if premium > 0:
        _add(factor, premium * 100.0)

    return rows


# ===========================================================================
# Step 3 — evidence (Issue #4): non-skippable hook into the decode flow
# ===========================================================================

# Sentinel passed as `hunter` to skip Step 3's Deep Research hunt entirely (zero
# cost) while still attaching a shape-consistent honest-empty evidence node.  Used
# for PORTFOLIO legs: a portfolio's signal is its holding composition + theme
# exposure + cross-card synthesis, NOT a per-holding evidence hunt (which would be
# 3 flagship Deep Research calls × every holding).  A user who wants a holding's
# evidence decodes that holding as a single market card.  Single cards never use
# this — Step 3 stays non-skippable for any bet the user decodes directly.
_SKIP_EVIDENCE = object()


def _attach_evidence(card, f: Fundamentals, anchor: float | None,
                     emit, lang: str, conn, hunter) -> None:
    """Run Step 3 for a freshly-assembled single card and attach the evidence
    section to card.decode_detail['evidence'].  Non-skippable for directly-decoded
    single cards; evidence.gather_evidence_for_card honestly leaves briefs empty
    when nothing is found and never raises, so this is safe on every decode path.
    The one exception is the `_SKIP_EVIDENCE` sentinel (portfolio legs) — see its
    definition above — which attaches an honest-empty section without hunting."""
    detail = getattr(card, "decode_detail", None)
    if detail is None:
        return
    if hunter is _SKIP_EVIDENCE:
        # Portfolio leg: skip the costly per-holding hunt, keep the node shape.
        detail["evidence"] = _empty_evidence_section()
        return
    company = getattr(f, "industry", None) or card.subject
    try:
        section = evidence.gather_evidence_for_card(
            card, conn=conn, hunter=hunter, lang=lang,
            company_name=company, current_price=anchor, emit=emit,
        )
    except Exception:
        # Step 3 must never crash decode; degrade to an honest-empty section.
        section = _empty_evidence_section()
    detail["evidence"] = section


# ===========================================================================
# Step 4 — market narrative (the live multi/空 debate behind the implied numbers).
# Offline-safe, injectable, honest-empty.  This is the qualitative layer the
# formula can't produce: it researches WHY the market holds these implied numbers
# (bull/bear/regime/catalysts) and binds the debate back to each number.
# ===========================================================================

def _implied_assumptions_block(detail: dict) -> str:
    """Format the formula's implied numbers as a bullet block — handed to the
    narrative researcher as the QUESTIONS to investigate (not answers to verify)."""
    lenses: list[dict] = []
    pm = detail.get("primary_lens")
    if isinstance(pm, dict):
        lenses.append(pm)
    lenses += [c for c in (detail.get("cross_lenses") or []) if isinstance(c, dict)]
    bullets: list[str] = []
    seen: set = set()
    for ln in lenses:
        label = ln.get("implied_label") or ln.get("lens_label") or ln.get("metric")
        val = ln.get("implied_value")
        unit = ln.get("unit") or ""
        if label is None or val is None:
            continue
        try:
            vs = f"{float(val):.2f}{unit}"
            dedup = round(float(val), 4)
        except (TypeError, ValueError):
            vs = f"{val}{unit}"
            dedup = val
        if (label, dedup) in seen:
            continue
        seen.add((label, dedup))
        bullets.append(f"  - {label} ≈ {vs}")
    np_ = detail.get("narrative_premium")
    if np_ is not None:
        try:
            bullets.append(f"  - 叙事溢价 ≈ {round(float(np_) * 100)}%"
                           "（基础业务价值之外、靠叙事支撑的价格占比）")
        except (TypeError, ValueError):
            pass
    return "\n".join(bullets) if bullets else "  - (无可用隐含假设)"


def _attach_market_narrative(card, *, emit=None, lang: str = "zh",
                             conn=None, narrator=None) -> None:
    """Step 4: research the live market debate for a single market card and attach
    it to card.decode_detail['market_narrative'] = {coverage, full, summary}.

    Offline-safe: the researcher self-guards on OFFLINE_MODE / no key → honest
    'unavailable' with zero cost/network, so the verify suite and offline decodes
    add nothing.  MVP scope = single market cards (the per-number bindings need
    single-card lenses); other kinds get an honest 'unavailable' stub.  Never
    raises, never fabricates."""
    detail = getattr(card, "decode_detail", None)
    if detail is None:
        return
    if card.source_type != SOURCE_MARKET or card.card_kind != db.SINGLE:
        detail["market_narrative"] = {"coverage": "unavailable", "full": None,
                                      "summary": {"coverage": "unavailable"}}
        return
    implied_block = _implied_assumptions_block(detail)
    anchor_price = detail.get("anchor_price")
    _safe_emit(emit, phase="market_narrative", kind="decision",
               text=f"研究 {card.subject} 的市场多空叙事(deep research)…",
               subject=card.subject)
    try:
        env, _hit = narrative.research_market_narrative(
            card.subject, current_price=anchor_price,
            implied_assumptions=implied_block, lang=lang,
            conn=conn, researcher=narrator,
        )
        result = narrative.build_card_narrative(env)
    except Exception as exc:  # must never crash a decode
        result = {"coverage": "unavailable", "full": None,
                  "summary": {"coverage": "unavailable"}, "error": str(exc)}

    # Cross-check (decision B): pair the narrative's per-number lean with the
    # already-attached evidence layer's independent verdict; a divergence is signal,
    # and surfacing it is what makes the (otherwise invisible) evidence layer pay
    # off. Merge the verdicts into the summary bindings so card_to_json carries them.
    try:
        full = result.get("full")
        if full:
            rows = narrative.cross_check(detail.get("evidence") or {}, full)
            by_label = {r["label"]: r for r in rows}
            for b in (result.get("summary", {}).get("bindings") or []):
                r = by_label.get(b.get("assumption"))
                if r:
                    b["narrative_verdict"] = r["narrative"]
                    b["evidence_verdict"] = r["evidence"]
                    b["diverges"] = r["diverges"]
            result["cross_check"] = rows
    except Exception:
        pass  # cross-check is a nice-to-have; never break a decode over it

    detail["market_narrative"] = result
    sq = (result.get("full") or {}).get("source_quality") if result.get("full") else None
    _safe_emit(emit, phase="market_narrative", kind="computation",
               text=f"市场叙事 coverage={result.get('coverage')}"
                    + (f",信源 {sq}" if sq else ""),
               subject=card.subject, payload={"coverage": result.get("coverage")})


def _empty_evidence_section() -> dict:
    """The canonical empty evidence section — the single source of truth for the
    shape every card kind exposes at decode_detail['evidence']."""
    return {
        "briefs": [], "assumption_count": 0, "found_count": 0,
        "empty_count": 0, "cache_hits": 0, "new_hunter_calls": 0,
        "cost": {"estimated_first_decode_usd": 0.0, "actual_new_call_usd": 0.0},
    }


def _aggregate_leg_evidence(leg_evidence: dict[str, dict]) -> dict:
    """Roll each portfolio leg's evidence section into ONE aggregate section that
    matches the single-card shape (briefs / assumption_count / found_count /
    empty_count / cache_hits / new_hunter_calls / cost), plus a per-leg `legs`
    breakdown for drill-down.

    This guarantees `decode_detail['evidence']` has a consistent shape across
    card kinds, so a consumer that reads e.g. `evidence['found_count']` never
    hits a KeyError or mis-renders a portfolio card as "no evidence".
    """
    agg = _empty_evidence_section()
    if not leg_evidence:
        agg["legs"] = {}
        return agg

    est = 0.0
    actual = 0.0
    for sec in leg_evidence.values():
        if not isinstance(sec, dict):
            continue
        agg["briefs"].extend(sec.get("briefs", []) or [])
        agg["assumption_count"] += int(sec.get("assumption_count", 0) or 0)
        agg["found_count"] += int(sec.get("found_count", 0) or 0)
        agg["empty_count"] += int(sec.get("empty_count", 0) or 0)
        agg["cache_hits"] += int(sec.get("cache_hits", 0) or 0)
        agg["new_hunter_calls"] += int(sec.get("new_hunter_calls", 0) or 0)
        cost = sec.get("cost", {}) or {}
        est += float(cost.get("estimated_first_decode_usd", 0.0) or 0.0)
        actual += float(cost.get("actual_new_call_usd", 0.0) or 0.0)
    agg["cost"] = {
        "estimated_first_decode_usd": round(est, 2),
        "actual_new_call_usd": round(actual, 2),
    }
    # Per-leg breakdown (compact: counts + cost, not the full briefs again).
    agg["legs"] = {
        tk: {
            "assumption_count": (sec or {}).get("assumption_count", 0),
            "found_count": (sec or {}).get("found_count", 0),
            "empty_count": (sec or {}).get("empty_count", 0),
            "cost": (sec or {}).get("cost", {}),
        }
        for tk, sec in leg_evidence.items()
    }
    return agg


# ===========================================================================
# Public API — decode_bet
# ===========================================================================

def decode_bet(source_type: str,
               source_input: "str | dict",
               lang: str = "zh",
               emit=None,
               *,
               llm=None,
               fundamentals_fn: Callable[[str], Fundamentals] = fetch_fundamentals,
               conn=None,
               hunter=None,
               narrator=None,
               _plan_override=None
               ) -> db.BetCard:
    """Decode any bet source into a full BetCard (passive — does NOT store it).

    Three stages (PRD 模块 2 决策 3): Step 1 adapter → Step 2 reverse-solve →
    Step 3 evidence (Issue #4, evidence.py).  Step 3 is **non-skippable** — it
    always runs for any decoded single card; there is no flag to disable it.
    An insufficient card or an empty implied-assumption list simply yields an
    empty evidence section (honest留空, never fabricated, never raises).

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
    conn : optional SQLite connection for the evidence cache (db.llm_cache,
        category="evidence").  None ⇒ Step 3 uses a process-local memory cache,
        so evidence is still hunted+cached, never skipped.
    hunter : optional injectable evidence hunter callable.  None ⇒ Step 3 uses
        the real Deep Research client (live path).  Tests inject a stub that
        returns a written-down brief, so the verify suite costs $0.

    Returns a db.BetCard. Never raises on bad / empty input — degrades to a
    "数据不足" card instead.
    """
    if source_type == SOURCE_PORTFOLIO:
        card = _decode_portfolio(source_input, lang, emit, fundamentals_fn,
                                 llm=llm, conn=conn, hunter=hunter)
        _attach_market_narrative(card, emit=emit, lang=lang, conn=conn, narrator=narrator)
        return card
    if source_type == SOURCE_MARKET:
        card = _decode_market(source_input, lang, emit, fundamentals_fn,
                              llm=llm, conn=conn, hunter=hunter,
                              plan_override=_plan_override)
        _attach_market_narrative(card, emit=emit, lang=lang, conn=conn, narrator=narrator)
        return card

    # Out-of-scope source types (analyst_pt / opinion = V2, or unknown): return a
    # graceful insufficient card rather than raising — keeps callers crash-free.
    # Preserve the ACTUAL requested source_type on the card instead of disguising
    # an unknown type as "market" (the old behavior silently mislabeled the card,
    # corrupting its series_key and any downstream grouping).  The cards table
    # stores source_type as free TEXT (no CHECK), so an honest value round-trips
    # cleanly; the insufficient status already tells consumers not to use it.
    subject = _coerce_subject(source_input)
    return _insufficient_card(
        subject=subject or "?",
        source_type=source_type or SOURCE_MARKET,  # honest; only blank → market
        source_ref=subject,
        reason=f"source_type '{source_type}' MVP 不支持(仅 market/portfolio)",
        emit=emit,
    )


# --- Market single-card path -----------------------------------------------

def _decode_market(source_input, lang, emit,
                   fundamentals_fn, *, llm=None, conn=None, hunter=None,
                   plan_override=None) -> db.BetCard:
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

    src_ref = str(source_input) if not isinstance(source_input, dict) else ticker

    # Agentic plan override (Phase C): the orchestrator already decided the plan,
    # so bypass the deterministic gates + lens-selection and apply it directly.
    # Reuses the SAME assemblers, so the card shape is identical to a deterministic
    # decode (parity).  The orchestrator tags decode_detail with the agent trace.
    if plan_override is not None:
        return _apply_plan_override(plan_override, ticker, src_ref, anchor, f,
                                    emit, lang, llm=llm, conn=conn, hunter=hunter)

    # Stage 2a — front gate (Issue #3 决策 9): narrative/theme-priced subjects
    # (AI 复合体: GPU/存储/光模块/AI 应用) → anchor mode is PRIMARY, traditional
    # lenses demote to cross-reference.  Deterministic classification, no LLM.
    is_ai, ai_theme = is_ai_composite(f)
    if is_ai:
        _safe_emit(emit, phase="frame_gate", kind="decision",
                   text=f"识别为 AI 复合体(主题={ai_theme})→ anchor mode 作 primary,"
                        f"传统 lens 降为交叉参考",
                   subject=ticker, payload={"ai_theme": ai_theme})
        # Still run traditional lenses for cross-reference (their divergence is
        # part of the story), but they don't drive the bet.
        plan = select_lenses(f)
        cross_primary, cross_extra = _run_plan(plan, anchor, f, None, ticker)
        cross_refs = [c for c in ([cross_primary] + cross_extra) if c]
        return _assemble_anchor_card(
            ticker, src_ref, anchor, f, emit, lang,
            cross_refs, mode="anchor_primary",
            reason=f"AI 复合体叙事/主题定价 → anchor mode primary(主题={ai_theme})",
            llm=llm, conn=conn, hunter=hunter,
        )

    # Stage 2b — shared core: pick lenses (deterministic) + reverse-solve.
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
        # Every applicable traditional lens returned no solution (TSLA-style:
        # DCF/multiples can't explain the price) → anchor mode as FALLBACK.
        _safe_emit(emit, phase="anchor_fallback", kind="decision",
                   text="所有适用传统 lens 反解无解 → 切 anchor mode(对账拆解)",
                   subject=ticker)
        return _assemble_anchor_card(
            ticker, src_ref, anchor, f, emit, lang,
            cross_refs=[], mode="anchor_fallback",
            reason="传统估值无法解释该价格,切 anchor mode 对账拆解",
            llm=llm, conn=conn, hunter=hunter,
        )

    # Stage 2c — narrative-premium gate (data-source-agnostic).  Even when a
    # traditional lens solves, if the DCF base business value explains less than
    # (1 - _NARRATIVE_PREMIUM_GATE) of the price, narrative/theme pricing
    # dominates → anchor mode PRIMARY.  This catches narrative-priced names whose
    # yfinance industry carries no AI keyword (real NVDA = "Semiconductors") — the
    # gap the keyword gate (Stage 2a) structurally cannot see.  Gated on a REAL
    # DCF baseline so a missing-data base=0 can never spuriously trip it.
    anchor_cross = [c for c in ([primary_result] + cross_results) if c]
    _base, _base_src, _ = _base_business_value(anchor, f, anchor_cross)
    narrative_premium = (max(0.0, anchor - _base) / anchor) if anchor > 0 else 0.0
    if _base_src == "dcf_baseline" and narrative_premium >= _NARRATIVE_PREMIUM_GATE:
        _, _theme_lbl = is_ai_composite(f)  # best-effort 赛道主题 (may be None)
        _factor_lbl = _style_factor(f, narrative_premium)  # 因子名 (总是有值)
        _label = _theme_lbl or _factor_lbl
        _safe_emit(emit, phase="frame_gate", kind="decision",
                   text=(f"叙事溢价 {narrative_premium*100:.0f}%(基础业务价值仅解释 "
                         f"{_base/anchor*100:.0f}% 现价)≥ 阈值 "
                         f"{_NARRATIVE_PREMIUM_GATE*100:.0f}% → anchor mode primary,"
                         f"因子={_label}"),
                   subject=ticker,
                   payload={"narrative_premium": round(narrative_premium, 4),
                            "theme": _theme_lbl, "factor": _factor_lbl,
                            "base_business_value": _base})
        return _assemble_anchor_card(
            ticker, src_ref, anchor, f, emit, lang, anchor_cross,
            mode="anchor_primary",
            reason=(f"叙事溢价 {narrative_premium*100:.0f}% ≥ 阈值 "
                    f"{_NARRATIVE_PREMIUM_GATE*100:.0f}% → anchor primary(因子={_label})"),
            llm=llm, conn=conn, hunter=hunter,
        )

    return _assemble_traditional_card(
        ticker, src_ref, anchor, f, primary_result, cross_results,
        narrative_premium,
        {"primary": plan.primary, "cross": plan.cross, "reason": plan.reason},
        emit, lang, conn=conn, hunter=hunter,
    )


def _fund_snapshot(f: Fundamentals) -> dict:
    """A JSON-able snapshot of the fundamentals used in the decode, persisted into
    decode_detail so build_card_display can compute the multi-level derivation tree
    (revenue path, price÷EPS, etc.) from REAL inputs on reload — not just conclusions."""
    return {
        "revenue_ttm": f.revenue_ttm, "eps_ttm": f.eps_ttm, "fcf_ttm": f.fcf_ttm,
        "ebitda_ttm": f.ebitda_ttm, "book_equity": f.book_equity,
        "net_income_ttm": getattr(f, "net_income_ttm", None),
        "shares_outstanding": f.shares_outstanding, "net_debt": f.net_debt,
        "growth_rate": f.growth_rate, "industry": f.industry,
    }


def _assemble_traditional_card(ticker, src_ref, anchor, f, primary_result,
                               cross_results, narrative_premium, lens_plan,
                               emit, lang, *, conn=None, hunter=None,
                               mode: str = "traditional") -> db.BetCard:
    """Assemble a traditional (multiple-lens) single BetCard.  Extracted from
    _decode_market so BOTH the deterministic decode AND the agentic orchestrator's
    plan-override build byte-identical cards (parity), running Step 3 the same way.
    `bet` = the primary implied metric value (a single comparable number)."""
    _safe_emit(emit, phase="solve", kind="computation",
               text=f"primary {primary_result['lens']} → "
                    f"{primary_result['implied_label']}={primary_result['implied_value']:.2f}",
               subject=ticker, payload=primary_result)
    card = db.BetCard(
        subject=ticker,
        source_type=SOURCE_MARKET,
        card_kind=db.SINGLE,
        source_ref=src_ref,
        bet=float(primary_result["implied_value"]),
    )
    card.decode_detail = {                       # type: ignore[attr-defined]
        "mode": mode,
        "anchor_price": anchor,
        "anchor_type": "market",
        # Narrative premium = share of price NOT explained by the DCF base
        # business value ("how much story is in this price").
        "narrative_premium": round(narrative_premium, 4),
        "primary_lens": primary_result,
        "cross_lenses": cross_results,
        "lens_plan": lens_plan,
        "fundamentals": _fund_snapshot(f),
        "lang": lang,
    }
    _safe_emit(emit, phase="assemble", kind="decision",
               text=f"组装 BetCard 完成({1 + len(cross_results)} 个 lens 视角)",
               subject=ticker, payload=None)
    # Step 3 — evidence (non-skippable, Issue #4). Hunts every implied
    # assumption (primary + cross lenses); honest-empty if none found.
    _attach_evidence(card, f, anchor, emit, lang, conn, hunter)
    return card


# --- Agentic plan-override applier (Phase C) -------------------------------

def _apply_plan_override(plan, ticker, src_ref, anchor, f, emit, lang, *,
                         llm=None, conn=None, hunter=None) -> db.BetCard:
    """Apply an orchestrator-chosen decode plan, bypassing the deterministic
    gates/lens-selection but REUSING the same assemblers — so the resulting card +
    decode_detail are shape-identical to a deterministic decode (parity).

    plan = {"mode": "traditional"|"anchor_primary"|"anchor_fallback"|"anchor",
            "primary_key": <lens key>, "cross_keys": [<lens key>...],
            "reason": <str why the agent chose this>}
    Degrades safely: an unknown primary lens falls back to select_lenses; a primary
    lens with no solution falls back to anchor mode (same as the deterministic tree).
    """
    mode = (plan.get("mode")
            or ("anchor_primary" if plan.get("anchor") else "traditional"))
    reason = plan.get("reason") or "agent-selected plan"
    cross_keys = [k for k in (plan.get("cross_keys") or []) if k in LENS_REGISTRY]

    if str(mode).startswith("anchor"):
        ref_keys = cross_keys or list(LENS_REGISTRY)
        cross = [r for r in (_run_lens(k, anchor, f) for k in ref_keys) if r]
        return _assemble_anchor_card(
            ticker, src_ref, anchor, f, emit, lang, cross,
            mode=(mode if mode != "anchor" else "anchor_primary"), reason=reason,
            llm=llm, conn=conn, hunter=hunter)

    # Traditional: run the agent's primary lens; no solution → anchor fallback
    # (mirrors the deterministic tree's behavior at decoder.py Stage 2b/2c).
    primary_key = plan.get("primary_key")
    if primary_key not in LENS_REGISTRY:
        primary_key = select_lenses(f).primary
    primary_result = (_run_lens(primary_key, anchor, f)
                      if primary_key in LENS_REGISTRY else None)
    if primary_result is None:
        cross = [r for r in (_run_lens(k, anchor, f) for k in list(LENS_REGISTRY)) if r]
        return _assemble_anchor_card(
            ticker, src_ref, anchor, f, emit, lang, cross, mode="anchor_fallback",
            reason=f"{reason}; primary lens {primary_key} had no solution",
            llm=llm, conn=conn, hunter=hunter)
    cross_results = [r for r in (_run_lens(k, anchor, f) for k in cross_keys) if r]
    anchor_cross = [c for c in ([primary_result] + cross_results) if c]
    _base, _src, _ = _base_business_value(anchor, f, anchor_cross)
    np_ = (max(0.0, anchor - _base) / anchor) if anchor and anchor > 0 else 0.0
    return _assemble_traditional_card(
        ticker, src_ref, anchor, f, primary_result, cross_results, np_,
        {"primary": primary_key, "cross": cross_keys, "reason": reason},
        emit, lang, conn=conn, hunter=hunter, mode=mode)


# --- Anchor-mode single-card assembler -------------------------------------

def _assemble_anchor_card(ticker, src_ref, anchor, f, emit, lang,
                          cross_refs: list[dict], *, mode: str, reason: str,
                          llm=None, conn=None, hunter=None) -> db.BetCard:
    """Build a single BetCard via anchor mode (primary or fallback).

    Anchor mode output = base business value + narrative/option components,
    reconciled to the anchor (PRD 决策 10).  The card's `bet` carries the share
    of price the narrative/anchor components explain (a single comparable scalar
    for M3); R1 theme_exposures are attached to the card; R2 DCF band rides on
    decode_detail (and is persistable through runs.rdcf_intervals which already
    has p25/p75 columns)."""
    anchor_detail = _run_anchor_mode(anchor, f, emit, ticker, cross_refs, llm=llm)

    # `bet` = narrative/anchor share of price (the comparable scalar). Falls back
    # to the anchor itself if no components (degenerate: whole price is base).
    comp_sum = sum(c.get("implied_amount", 0) or 0
                   for c in anchor_detail["components"])
    bet_value = float(comp_sum) if anchor_detail["components"] else None

    card = db.BetCard(
        subject=ticker,
        source_type=SOURCE_MARKET,
        card_kind=db.SINGLE,
        source_ref=src_ref,
        bet=bet_value,
        # R1: anchor-mode theme exposures attached so save_card persists them.
        theme_exposures=anchor_detail["theme_exposures"],
    )

    # R2: surface the DCF Monte-Carlo band from any DCF cross-reference so M3 can
    # read it.  Lives on decode_detail; the band's p25/p75 persist via the
    # existing runs.rdcf_intervals columns when the caller wires a run.
    dcf_view = next((c for c in cross_refs if c.get("lens") == "dcf"), None)
    r2_band = dcf_view.get("band") if dcf_view else None

    # Narrative premium = share of price the base business value fails to explain
    # (= gap / anchor).  Undervalued cards clamp base to anchor → premium 0.
    _base_bv = anchor_detail.get("base_business_value") or 0.0
    narrative_premium = (max(0.0, anchor - _base_bv) / anchor) if anchor and anchor > 0 else 0.0

    card.decode_detail = {                       # type: ignore[attr-defined]
        "mode": mode,                            # anchor_primary | anchor_fallback
        "anchor_price": anchor,
        "anchor_type": "market",
        "reason": reason,
        "narrative_premium": round(narrative_premium, 4),
        "anchor_mode": anchor_detail,            # base + components + 对账
        "cross_lenses": cross_refs,              # traditional lenses (reference)
        "r2_band": r2_band,                      # R2 (p25/p50/p75) | None
        "fundamentals": _fund_snapshot(f),
        "lang": lang,
    }
    _safe_emit(emit, phase="assemble", kind="decision",
               text=f"组装 anchor-mode BetCard 完成"
                    f"({len(anchor_detail['components'])} 个叙事/期权成分,"
                    f"{len(anchor_detail['theme_exposures'])} 个主题暴露)",
               subject=ticker, payload=None)
    # Step 3 — evidence (non-skippable, Issue #4). Each priced narrative/option
    # component is an implied assumption to research; honest-empty if none found.
    _attach_evidence(card, f, anchor, emit, lang, conn, hunter)
    return card


# --- Portfolio aggregate-card path -----------------------------------------

# A theme is a portfolio-level concentration risk when, after weighting, it
# carries this share of the WHOLE portfolio's value (not a single stock's).
_PORTFOLIO_CONC_PCT = 40.0


def _aggregate_portfolio_themes(holdings: list, per_ticker: dict) -> list:
    """R1 (portfolio): weight each anchor-mode leg's theme exposures into card-level
    theme rows. A leg's `exposure_pct` is the share of THAT stock's price the theme
    carries; the portfolio-level exposure is Σ (normalized weight_i × exposure_i).
    Honest: only legs that decoded into anchor mode contribute; traditional legs
    add nothing. None when no leg carries a theme."""
    if not holdings:
        return []
    n = len(holdings)
    wmap = {h.ticker: (h.weight_pct if h.weight_pct is not None else 100.0 / n) for h in holdings}
    wtotal = sum(wmap.values()) or 100.0
    agg: dict = {}                       # theme -> {"pct": float, "tickers": set}
    for h in holdings:
        pt = per_ticker.get(h.ticker) or {}
        am = pt.get("anchor_mode") if isinstance(pt, dict) else None
        if not am:
            continue
        wfrac = wmap[h.ticker] / wtotal
        for te in (am.get("theme_exposures") or []):
            theme = te.get("theme") if isinstance(te, dict) else getattr(te, "theme", None)
            ep = te.get("exposure_pct") if isinstance(te, dict) else getattr(te, "exposure_pct", None)
            if not theme or not isinstance(ep, (int, float)):
                continue
            d = agg.setdefault(theme, {"pct": 0.0, "tickers": set()})
            d["pct"] += wfrac * ep       # this leg's weighted contribution to the theme
            d["tickers"].add(h.ticker)
    rows: list = []
    for theme, d in sorted(agg.items(), key=lambda kv: -kv[1]["pct"]):
        pct = round(d["pct"], 2)
        rows.append(db.ThemeExposure(
            theme=theme, exposure_pct=pct,
            contributing_tickers=sorted(d["tickers"]),
            is_concentration_risk=(pct >= _PORTFOLIO_CONC_PCT),
        ))
    return rows


def _decode_portfolio(source_input, lang, emit,
                      fundamentals_fn, *, llm=None, conn=None, hunter=None) -> db.BetCard:
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
    # Per-leg evidence sections so the aggregate card carries a unified, shape-
    # consistent `evidence` node.  Legs are decoded for their implied EXPOSURE
    # only — they pass _SKIP_EVIDENCE so NO per-holding Deep Research hunt runs
    # (cost discipline: a portfolio's signal is composition + cross-card synthesis,
    # not a hunt × every holding).  The aggregate is therefore an honest empty
    # roll-up; decode a holding as a single card to get its evidence.
    leg_evidence: dict[str, dict] = {}
    failed_legs: dict[str, str] = {}     # ticker -> 失败原因(诚实暴露,不再静默吞)
    import time as _time
    for spec in holdings_spec:
        tk = spec["ticker"]
        weight = spec.get("weight_pct")
        holdings.append(db.Holding(ticker=tk, weight_pct=weight))
        # Decode each leg with ONE retry.  A portfolio decode hits the data source
        # (yfinance) once per holding, so a transient rate-limit / timeout on a
        # single leg is common — a short backoff + one retry recovers most.  A
        # PERSISTENT failure is RECORDED, not silently swallowed: otherwise all
        # legs failing yields an empty theme set that looks like a valid "no common
        # bet" answer when the truth is "couldn't decode" (this was the bug behind
        # the empty-theme portfolio cards seen when yfinance was rate-limited).
        last_err = None
        decoded_ok = False
        for attempt in range(2):
            try:
                leg = _decode_market(tk, lang, None, fundamentals_fn,
                                     llm=llm, conn=conn, hunter=_SKIP_EVIDENCE)
                detail = getattr(leg, "decode_detail", None) or {}
                if detail.get("primary_lens"):
                    per_ticker[tk] = detail["primary_lens"]
                    decoded_ok = True
                elif detail.get("anchor_mode"):
                    # Anchor-mode leg: surface its anchor detail for R1 aggregation.
                    per_ticker[tk] = {"lens": "anchor", "anchor_mode": detail["anchor_mode"]}
                    decoded_ok = True
                # Capture each leg's (honest-empty) evidence section so the aggregate
                # node stays keyed by ticker with a shape-consistent roll-up.
                if isinstance(detail.get("evidence"), dict):
                    leg_evidence[tk] = detail["evidence"]
                if decoded_ok:
                    break
                # Leg RETURNED but produced nothing aggregatable: _decode_market turns
                # an upstream (yfinance) failure / missing price into an *insufficient
                # card* rather than raising, so a soft failure surfaces here as "no
                # primary_lens / anchor_mode" — NOT an exception.  Capture its reason
                # and retry once (this is the empty-theme-portfolio bug's real path).
                last_err = detail.get("reason") or "数据不足,无可聚合结果"
            except Exception as exc:                # hard-failure path (also recorded)
                last_err = f"{type(exc).__name__}: {exc}"
            if attempt == 0:
                _time.sleep(0.3)                    # brief backoff before the retry
        if not decoded_ok:
            failed_legs[tk] = str(last_err)
            _safe_emit(emit, phase="leg_error", kind="decision",
                       text=f"持仓 {tk} 解码失败(已重试):{failed_legs[tk]} — "
                            f"诚实留空,不计入主题聚合(避免伪装成'无共同赌注')",
                       subject=subject, payload={"ticker": tk})

    # Equal-weight default when no weights were supplied (a plain comma string
    # carries none) so composition / weighting is always well-defined.
    if holdings and all(h.weight_pct is None for h in holdings):
        eq = round(100.0 / len(holdings), 2)
        for h in holdings:
            h.weight_pct = eq
    # R1 (previously deferred): weight the legs' anchor-mode theme exposures into
    # card-level theme rows + a portfolio-level concentration flag.
    theme_rows = _aggregate_portfolio_themes(holdings, per_ticker)

    card = db.BetCard(
        subject=subject,
        source_type=SOURCE_PORTFOLIO,
        card_kind=db.PORTFOLIO,
        source_ref=", ".join(h.ticker for h in holdings),
        bet=None,                       # portfolio carries no single scalar bet
        holdings=holdings,
        theme_exposures=theme_rows,     # R1 weighted theme aggregation
    )
    card.decode_detail = {              # type: ignore[attr-defined]
        "anchor_type": "portfolio",
        "per_ticker_primary": per_ticker,
        "holding_count": len(holdings),
        "decoded_legs": len(per_ticker),
        "failed_legs": failed_legs,     # 诚实:哪些腿没解出来(数据源临时不可用)
        # Step 3 — unified evidence node so consumers can read
        # decode_detail["evidence"] with the SAME shape on every card kind
        # (single OR portfolio).  Aggregates each leg's found/cost; a per-leg
        # breakdown rides under "legs" for drill-down.
        "evidence": _aggregate_leg_evidence(leg_evidence),
        "lang": lang,
    }
    _safe_emit(emit, phase="assemble", kind="decision",
               text=f"组装组合卡完成({len(holdings)} 持仓,{len(per_ticker)} 个成功解码"
                    + (f",{len(failed_legs)} 个失败(数据源临时不可用)" if failed_legs else "") + ")",
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
        # Step 3 still "ran" — there are simply no implied assumptions to research
        # on a 数据不足 card, so the evidence section is honestly empty (boundary:
        # source missing → 留空, not error / not skipped).
        "evidence": _empty_evidence_section(),
    }
    return card


if __name__ == "__main__":  # pragma: no cover - manual smoke (hits yfinance)
    import sys
    tk = sys.argv[1] if len(sys.argv) > 1 else "NVDA"
    c = decode_bet(SOURCE_MARKET, tk)
    print(f"subject={c.subject} bet={c.bet}")
    print(c.decode_detail)  # type: ignore[attr-defined]
