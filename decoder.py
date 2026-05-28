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
import evidence
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

    industry = " / ".join(
        str(info.get(k)) for k in ("sector", "industry") if info.get(k)
    ) or None

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
        tags=[],
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
AI_COMPOSITE_THEMES: dict[str, tuple[str, ...]] = {
    "AI 基础设施": (
        "gpu", "accelerator", "ai chip", "ai 芯片", "datacenter",
        "data center", "数据中心", "ai infrastructure", "ai 基础设施",
    ),
    "存储": ("hbm", "memory", "dram", "nand", "存储", "storage"),
    "光模块": ("optical", "transceiver", "光模块", "光通信", "silicon photonics"),
    "AI 应用": ("ai application", "ai 应用", "generative ai", "llm", "copilot"),
}

def _ai_theme_for(text: str) -> str | None:
    """Return the AI-complex theme a lowercased text matches, or None."""
    low = text.lower()
    for theme, kws in AI_COMPOSITE_THEMES.items():
        if any(kw in low for kw in kws):
            return theme
    return None


def is_ai_composite(f: Fundamentals) -> tuple[bool, str | None]:
    """Deterministic AI-complex classification (PRD 决策 9).

    Returns (is_composite, theme).  Decision order:
      1. any explicit tag matches an AI theme keyword
      2. the industry string matches an AI theme keyword
    Otherwise (False, None).

    The gate keys off *classification signals* (industry / tags) that
    `fetch_fundamentals` populates from yfinance — NOT a bare ticker whitelist.
    This keeps it conservative (a normal ticker with no AI signal never trips
    the gate) and reproducible, and it does not collide with the traditional
    decision tree's ticker-agnostic lens selection.
    """
    for tag in (f.tags or []):
        theme = _ai_theme_for(str(tag))
        if theme:
            return True, theme
    if f.industry:
        theme = _ai_theme_for(f.industry)
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
    return _anchor_component(
        lens_key="narrative", lens_label="叙事锚",
        claim="市场为 AI 增长叙事支付的溢价" if is_ai else "市场为增长叙事支付的溢价",
        implied_amount=gap,
        implied_assumption="叙事兑现:增长曲线显著超过传统估值锚定的水平",
        probability=None,
        theme=theme or "增长叙事",
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
# Anchor mode (Issue #3) — base value + narrative/option components + 对账
# ===========================================================================

# How close Σ(components) + base must land to the anchor to call it reconciled.
_RECON_TOL_PCT = 0.01  # 1% of anchor


def _base_business_value(anchor: float, f: Fundamentals,
                         cross_results: list[dict]) -> tuple[float, str]:
    """Conservative *base business value* the traditional lenses can defend.

    Preference order:
      1. DCF consensus baseline price (the business-value floor, R2-bearing)
      2. a conservative multiple-implied value (rare fallback)
      3. 0 (no defensible base → the whole price is narrative/option)

    Returns (base_value, source_label).  The base is clamped to [0, anchor] so
    the residual gap that anchor lenses decompose is always non-negative.
    """
    base = 0.0
    src = "none"
    # Look for a DCF view among already-solved cross lenses first.
    dcf_view = next((r for r in cross_results if r.get("lens") == "dcf"), None)
    if dcf_view is None:
        # Run DCF explicitly to obtain its consensus baseline (business value).
        dcf_view = _run_lens("dcf", anchor, f)
    if dcf_view is not None and dcf_view.get("baseline_dcf_price") is not None:
        base = float(dcf_view["baseline_dcf_price"])
        src = "dcf_baseline"
    # Clamp: a base above the anchor (DCF says undervalued) means anchor mode has
    # no narrative gap to decompose; clamp to anchor so the gap floors at 0.
    base = max(0.0, min(base, anchor))
    return base, src


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
    base, base_src = _base_business_value(anchor, f, cross_results)
    gap = max(0.0, anchor - base)

    _safe_emit(emit, phase="anchor_base", kind="computation",
               text=f"基础业务价值={base:.2f}({base_src}),叙事/期权待对账缺口={gap:.2f}",
               subject=subject, payload={"base": base, "gap": gap})

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
                _safe_emit(emit, phase="anchor_component", kind="decision",
                           text=f"{comp['lens_label']} → 隐含 ${comp['implied_amount']:.2f}",
                           subject=subject, payload=comp)
                break  # one component carries the residual (keeps Σ exact)

    # 加总对账: base + Σ(component implied_amount) ≈ anchor.
    comp_sum = sum(c["implied_amount"] for c in components)
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

    Exposure % = component implied_amount / anchor (the share of price the theme
    carries).  An AI-composite subject always gets at least one row tagged with
    its AI theme so M3 同源比对 has a comparable handle.
    """
    rows: list[db.ThemeExposure] = []
    is_ai, ai_theme = is_ai_composite(f)
    for c in components:
        amt = c.get("implied_amount") or 0.0
        if amt <= 0:
            continue
        theme = c.get("theme") or (ai_theme if is_ai else c.get("lens_label"))
        if not theme:
            continue
        pct = (amt / anchor * 100.0) if anchor else None
        rows.append(db.ThemeExposure(
            theme=theme,
            exposure_pct=round(pct, 2) if pct is not None else None,
            contributing_tickers=[f.ticker],
            is_concentration_risk=bool(pct is not None and pct >= 50.0),
        ))
    # Guarantee an AI-infra theme row for AI composites even if components used a
    # narrower theme label (so NVDA-style cards always expose "AI 基础设施").
    if is_ai and ai_theme and not any(r.theme == ai_theme for r in rows):
        narrative_amt = sum(c.get("implied_amount") or 0.0 for c in components)
        pct = (narrative_amt / anchor * 100.0) if anchor else None
        rows.append(db.ThemeExposure(
            theme=ai_theme,
            exposure_pct=round(pct, 2) if pct is not None else None,
            contributing_tickers=[f.ticker],
            is_concentration_risk=bool(pct is not None and pct >= 50.0),
        ))
    return rows


# ===========================================================================
# Step 3 — evidence (Issue #4): non-skippable hook into the decode flow
# ===========================================================================

def _attach_evidence(card, f: Fundamentals, anchor: float | None,
                     emit, lang: str, conn, hunter) -> None:
    """Run Step 3 for a freshly-assembled single card and attach the evidence
    section to card.decode_detail['evidence'].  Always invoked (no skip flag);
    evidence.gather_evidence_for_card honestly leaves briefs empty when nothing
    is found and never raises, so this is safe on every decode path."""
    detail = getattr(card, "decode_detail", None)
    if detail is None:
        return
    company = getattr(f, "industry", None) or card.subject
    try:
        section = evidence.gather_evidence_for_card(
            card, conn=conn, hunter=hunter, lang=lang,
            company_name=company, current_price=anchor, emit=emit,
        )
    except Exception:
        # Step 3 must never crash decode; degrade to an honest-empty section.
        section = {
            "briefs": [], "assumption_count": 0, "found_count": 0,
            "empty_count": 0, "cache_hits": 0, "new_hunter_calls": 0,
            "cost": {"estimated_first_decode_usd": 0.0, "actual_new_call_usd": 0.0},
        }
    detail["evidence"] = section


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
               hunter=None
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
        return _decode_portfolio(source_input, lang, emit, fundamentals_fn,
                                 llm=llm, conn=conn, hunter=hunter)
    if source_type == SOURCE_MARKET:
        return _decode_market(source_input, lang, emit, fundamentals_fn,
                              llm=llm, conn=conn, hunter=hunter)

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
                   fundamentals_fn, *, llm=None, conn=None, hunter=None) -> db.BetCard:
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
        source_ref=src_ref,
        bet=float(primary_result["implied_value"]),
    )
    # Attach decode detail as a plain attribute (not persisted by save_card, but
    # available to the caller / M4 in-process).  Keeps decode_bet self-contained.
    card.decode_detail = {                       # type: ignore[attr-defined]
        "mode": "traditional",
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
    # Step 3 — evidence (non-skippable, Issue #4). Hunts every implied
    # assumption (primary + cross lenses); honest-empty if none found.
    _attach_evidence(card, f, anchor, emit, lang, conn, hunter)
    return card


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
    comp_sum = sum(c["implied_amount"] for c in anchor_detail["components"])
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

    card.decode_detail = {                       # type: ignore[attr-defined]
        "mode": mode,                            # anchor_primary | anchor_fallback
        "anchor_price": anchor,
        "anchor_type": "market",
        "reason": reason,
        "anchor_mode": anchor_detail,            # base + components + 对账
        "cross_lenses": cross_refs,              # traditional lenses (reference)
        "r2_band": r2_band,                      # R2 (p25/p50/p75) | None
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
    for spec in holdings_spec:
        tk = spec["ticker"]
        weight = spec.get("weight_pct")
        holdings.append(db.Holding(ticker=tk, weight_pct=weight))
        # Best-effort decode of each leg (never let one bad ticker sink the card).
        try:
            leg = _decode_market(tk, lang, None, fundamentals_fn,
                                 llm=llm, conn=conn, hunter=hunter)
            detail = getattr(leg, "decode_detail", None)
            if detail and detail.get("primary_lens"):
                per_ticker[tk] = detail["primary_lens"]
            elif detail and detail.get("anchor_mode"):
                # Anchor-mode leg: surface its anchor detail for R1 aggregation.
                per_ticker[tk] = {"lens": "anchor", "anchor_mode": detail["anchor_mode"]}
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
        # Step 3 still "ran" — there are simply no implied assumptions to research
        # on a 数据不足 card, so the evidence section is honestly empty (boundary:
        # source missing → 留空, not error / not skipped).
        "evidence": {
            "briefs": [], "assumption_count": 0, "found_count": 0,
            "empty_count": 0, "cache_hits": 0, "new_hunter_calls": 0,
            "cost": {"estimated_first_decode_usd": 0.0, "actual_new_call_usd": 0.0},
        },
    }
    return card


if __name__ == "__main__":  # pragma: no cover - manual smoke (hits yfinance)
    import sys
    tk = sys.argv[1] if len(sys.argv) > 1 else "NVDA"
    c = decode_bet(SOURCE_MARKET, tk)
    print(f"subject={c.subject} bet={c.bet}")
    print(c.decode_detail)  # type: ignore[attr-defined]
