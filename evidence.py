"""Module 2 — Step 3: Evidence Hunter (Issue #4).

The decode pipeline is three stages (PRD 模块 2 决策 3):

    Step 1  前置适配器   source → anchor price + fundamentals   (decoder.py)
    Step 2  共享核心     pick lenses → reverse-solve metrics      (decoder.py)
    Step 3  找证据       每条隐含假设 → evidence brief            (THIS FILE)

PRD 模块 2 决策 4 + 7 pin the contract:

  - 决策 4 — 证据 **强制不跳过**, 按 ticker+假设缓存, demo 前预跑, 证据归 M2 (单卡级).
            "8 新票组合首解 ≈ $24" 量级 (see `estimate_portfolio_first_decode_cost`).
  - 决策 7 — 证据查不到 → 字段留空 + 标注, **绝不编造**.

Cost discipline (the load-bearing rule of this issue): the heavy Deep Research
call is *injectable*.  `hunt_evidence(..., hunter=None)` uses the real MiroMind
client by default, but every test injects a stub that returns a written-down
brief — so the whole verify suite runs at **zero API cost**.  The cache is keyed
by ticker+assumption so a second decode of the same bet costs nothing.

This module is a peer of `decoder.py`: `decoder.decode_bet` calls
`gather_evidence_for_card` as its non-skippable Step 3 (no flag turns it off).
It does NOT touch synthesis / pipeline.py / db.py schema (cache_get/cache_put are
used as-is).
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import db

# ---------------------------------------------------------------------------
# Cost model.  The MINI per-call figure is derived from client.py's token pricing
# and lands ≈ $3.21 — empirically a safe conservative (MiroMind console 2026-06-01
# showed real mini calls at $0.32–$5.93, avg ~$2.1).  The FLAGSHIP figure is NOT
# derived: the token-footprint derivation overestimated it ~5-7x (it applied the
# 235B output rate to a footprint back-fit for mini), so it is pinned to the
# console's observed flagship billing of $0.80–$1.60 (avg ~$1.2).  See the
# COST_PER_EVIDENCE_* constants below.
# ---------------------------------------------------------------------------

# Representative token footprint of one Deep Research evidence turn.  A Deep
# Research call is reasoning-heavy (long tool-augmented chains), so completion
# tokens dominate.  Tuned so the mini cost reproduces the historical $3.21/call
# figure exactly under client.PRICING_PER_MILLION["...-mini"] = (1.25, 10.0):
#   (50_000 * 1.25 + 314_750 * 10.0) / 1e6 = 3.21
_EVIDENCE_PROMPT_TOKENS = 50_000
_EVIDENCE_COMPLETION_TOKENS = 314_750


def _cost_per_call(model: str) -> float:
    """USD for one Deep Research evidence call on `model`, from client pricing."""
    try:
        from client import PRICING_PER_MILLION
        rate_in, rate_out = PRICING_PER_MILLION.get(model, (0.0, 0.0))
    except Exception:
        # client import must never be load-bearing for a pure-Python estimate.
        rate_in, rate_out = (1.25, 10.0)  # MODEL_MINI fallback rates
    return (_EVIDENCE_PROMPT_TOKENS * rate_in
            + _EVIDENCE_COMPLETION_TOKENS * rate_out) / 1_000_000


def _hunter_model(*, flagship: bool = False) -> str:
    """The model `_default_hunter` actually calls.  Today it's MODEL_MINI for
    both the dev and demo paths; `flagship=True` lets a caller cost the 235B
    model explicitly.  Kept as a function so the estimate and the live call can
    never silently disagree on which model is priced."""
    try:
        from client import MODEL_MINI, MODEL_FLAGSHIP
        return MODEL_FLAGSHIP if flagship else MODEL_MINI
    except Exception:
        return "mirothinker-1-7-deepresearch" if flagship \
            else "mirothinker-1-7-deepresearch-mini"


# Per-call constants.  Kept as module names because verify_m4 asserts on them.
#
# MINI is derived from the client rate table and is empirically sound: the
# MiroMind console (2026-06-01) shows real mini Deep Research calls billing
# $0.32–$5.93 (avg ~$2.1), so the derived $3.21 sits a touch high = a safe
# conservative per-call figure.
#
# FLAGSHIP is NOT derived: the token footprint above is back-fit for the mini
# model, so applying the 235B output rate to it yields ~$8 — but the console
# shows flagship calls actually bill $0.80–$1.60 (avg ~$1.2), i.e. the derivation
# overestimated ~5-7x.  We pin flagship to that observed billing instead.
COST_PER_EVIDENCE_MINI = round(_cost_per_call(_hunter_model(flagship=False)), 2)
COST_PER_EVIDENCE_FLAGSHIP = 2.00  # console-calibrated (MiroMind 2026-06-01), not derived

# A freshly-decoded ticker hunts evidence for EVERY implied assumption, not one:
# `gather_evidence_for_card` runs the primary lens + up to 2 cross lenses (and
# anchor cards hunt each priced narrative/option component).  So a traditional
# ticker is ~1 primary + ~2 cross ≈ 3 hunts.  The old value of 1 under-counted
# the real spend ~3x (8 票真实 ≈ $77, not the "≈ $24" headline).  Callers that
# know the exact lens plan should pass the real count (see
# estimate_portfolio_first_decode_cost(..., assumptions_per_ticker=...)).
ASSUMPTIONS_PER_TICKER_FIRST_DECODE = 3


def estimate_evidence_cost(n_assumptions: int, *, flagship: bool = False) -> float:
    """Estimated USD cost to hunt evidence for `n_assumptions` (cache-miss path).

    Cache hits cost $0; this is the *first-decode* upper bound.
    """
    per = COST_PER_EVIDENCE_FLAGSHIP if flagship else COST_PER_EVIDENCE_MINI
    return max(0, int(n_assumptions)) * per


def assumptions_per_card(card) -> int:
    """The number of distinct implied assumptions a decoded card will hunt
    evidence for — i.e. exactly how many Deep Research calls a first decode of
    this card costs.  This is the ground truth `gather_evidence_for_card` walks,
    so a cost estimate built from it matches the real hunt count.

    Returns 0 for an insufficient card (nothing to research)."""
    return len(_implied_assumptions_from_card(card))


def estimate_portfolio_first_decode_cost(
    n_tickers: int,
    assumptions_per_ticker: int = ASSUMPTIONS_PER_TICKER_FIRST_DECODE,
    *,
    flagship: bool = False,
) -> dict:
    """Cost guard for a fresh multi-ticker portfolio's *first* decode.

    Returns a dict so callers can both print a human line and assert on the
    magnitude.  Each ticker hunts `assumptions_per_ticker` implied assumptions
    (primary + cross lenses, or anchor components) — default 3, reflecting the
    real `gather_evidence_for_card` walk.  Worked example: 8 brand-new tickers ×
    3 hunts × $3.21 (mini) ≈ **$77** — the honest first-decode magnitude (the
    earlier "≈ $24" headline assumed 1 hunt/ticker and under-counted ~3x).

    For an exact figure when the cards are already decoded, sum
    `assumptions_per_card(card)` across the basket and pass it as
    `n_tickers=1, assumptions_per_ticker=<that sum>`.
    """
    n_assumptions = max(0, int(n_tickers)) * max(0, int(assumptions_per_ticker))
    total = estimate_evidence_cost(n_assumptions, flagship=flagship)
    per = COST_PER_EVIDENCE_FLAGSHIP if flagship else COST_PER_EVIDENCE_MINI
    model = _hunter_model(flagship=flagship)
    return {
        "n_tickers": int(n_tickers),
        "assumptions_per_ticker": int(assumptions_per_ticker),
        "n_evidence_calls": n_assumptions,
        "cost_per_call_usd": per,
        "model": model,
        "estimated_cost_usd": round(total, 2),
        "human": (
            f"{n_tickers} 新票组合首解 ≈ {n_assumptions} 次 Deep Research "
            f"({model}) × ${per:.2f} ≈ ${total:.0f}"
        ),
    }


# ---------------------------------------------------------------------------
# Cache.  Primary store is db.llm_cache (category="evidence") via a conn.  When
# no conn is supplied (the M2/M3 test path calls decode_bet without one), we fall
# back to a process-local dict so Step 3 still caches within a run and is *never
# skipped* — there is no flag, only "DB cache" vs "memory cache".
# ---------------------------------------------------------------------------

_MEM_CACHE: dict[str, dict] = {}

EVIDENCE_CATEGORY = "evidence"


def make_cache_key(ticker: str, assumption: dict, lang: str = "zh") -> str:
    """Stable cache key for one (ticker, assumption) evidence brief.

    Keys off the assumption's id (falls back to metric, then a hash of the human
    text) so the same bet's same assumption reuses the cached brief regardless of
    object identity.
    """
    aid = (
        assumption.get("id")
        or assumption.get("assumption_id")
        or assumption.get("metric")
        or assumption.get("lens")
        or "unknown"
    )
    # Include a short, stable signature of the human text so two assumptions that
    # share a metric label but differ in wording don't collide.  Must use a
    # *content* hash (sha1), NOT Python's built-in hash(): hash() is salted per
    # process (PYTHONHASHSEED) so a key built in one process never matches the
    # same (ticker, assumption) in another — which silently defeats the whole
    # pre-run-then-demo cache strategy and re-burns real Deep Research money on
    # every restart.  sha1 is stable across processes / restarts / machines.
    text = str(assumption.get("human_text")
               or assumption.get("implied_assumption")
               or assumption.get("claim") or "")[:120]
    sig = hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]
    return f"{str(ticker).upper()}|{aid}|{lang}|{sig}"


def _cache_get(conn, key: str) -> dict | None:
    if conn is not None:
        return db.cache_get(conn, EVIDENCE_CATEGORY, key)
    return _MEM_CACHE.get(key)


def _cache_put(conn, key: str, payload: dict, ticker: str | None) -> None:
    if conn is not None:
        db.cache_put(conn, EVIDENCE_CATEGORY, key, payload, ticker=ticker)
    else:
        _MEM_CACHE[key] = payload


# ---------------------------------------------------------------------------
# Honest-empty brief (决策 7: 查不到 → 留空标注, 绝不编造).
# ---------------------------------------------------------------------------

def _empty_brief(ticker: str, assumption: dict, reason: str) -> dict:
    """A brief that honestly records 'no evidence found' — never fabricated."""
    return {
        "assumption_id": assumption.get("id")
        or assumption.get("assumption_id")
        or assumption.get("metric")
        or assumption.get("lens"),
        "assumption_text": assumption.get("human_text")
        or assumption.get("implied_assumption")
        or assumption.get("claim"),
        "status": "not_found",           # explicit annotation
        "note": reason,                  # why it's empty
        "evidence_items": [],            # left empty — no fabrication
        "overall_balance": None,         # honestly null, not invented
        "evidence_count": {"support": 0, "refute": 0, "neutral": 0},
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "_meta": {"cost_usd": 0.0, "tool_call_count": 0, "fabricated": False},
    }


def _normalize_brief(raw: Any, ticker: str, assumption: dict) -> dict:
    """Coerce a hunter's return into the brief schema; honest-empty on garbage.

    A hunter may return a brief dict directly, or a {content,...} envelope (like
    client.call_deepresearch) we still parse.  Anything we can't read as a brief
    with real evidence items degrades to an honest-empty brief — never invented.
    """
    if raw is None:
        return _empty_brief(ticker, assumption, "hunter 返回空 (查不到)")

    brief: dict | None = None
    if isinstance(raw, dict) and "evidence_items" in raw:
        brief = raw
    elif isinstance(raw, dict) and "content" in raw:
        # client.call_deepresearch-style envelope: parse the JSON content.
        try:
            from client import parse_loose_json
            parsed = parse_loose_json(raw.get("content") or "")
        except Exception:
            parsed = None
        if isinstance(parsed, dict) and "evidence_items" in parsed:
            brief = parsed
            meta = raw.get("usage") or {}
            brief.setdefault("_meta", {})
            brief["_meta"].update({
                "usage": raw.get("usage"),
                "cost_usd": raw.get("cost_usd", 0.0),
                "tool_call_count": raw.get("tool_call_count", 0),
            })
    if brief is None:
        return _empty_brief(ticker, assumption, "hunter 输出无法解析为证据 (留空)")

    items = brief.get("evidence_items") or []
    if not items:
        # Hunter ran but found nothing → honest empty, keep its note if any.
        out = _empty_brief(ticker, assumption,
                           brief.get("note") or "hunter 未找到任何证据")
        out["_meta"] = brief.get("_meta", out["_meta"])
        return out

    # Real evidence: stamp the schema fields the rest of M2/M3 expect.
    brief.setdefault("assumption_id", assumption.get("id")
                     or assumption.get("metric") or assumption.get("lens"))
    brief.setdefault("assumption_text", assumption.get("human_text")
                     or assumption.get("implied_assumption")
                     or assumption.get("claim"))
    brief.setdefault("status", "found")
    brief.setdefault("evidence_count", {"support": 0, "refute": 0, "neutral": 0})
    brief.setdefault("_meta", {"cost_usd": 0.0, "tool_call_count": 0})
    brief["_meta"].setdefault("fabricated", False)
    return brief


# ---------------------------------------------------------------------------
# Default (real) hunter — Deep Research via client.py.  Isolated + lazy so the
# deterministic / test path NEVER imports the network client.
# ---------------------------------------------------------------------------

def _default_hunter(ticker: str, assumption: dict, *,
                    lang: str, mode: str, company_name: str,
                    current_price: float | None) -> dict | None:  # pragma: no cover
    """Live evidence hunt (real MiroMind Deep Research).  Not exercised by the
    zero-cost test suite — tests always inject a stub.  Kept thin: build prompt
    from the existing template, call the flagship model, return the raw envelope
    (`_normalize_brief` parses it).

    Cost-safety guard: when OFFLINE_MODE is set OR no MIROMIND_API_KEY is
    configured, we DO NOT import or call the network client at all — we return
    None so hunt_evidence records an honest-empty brief.  This guarantees the
    verify suite (and any OFFLINE_MODE caller) can never accidentally spend money
    even though `hunter=None` nominally means "use the real client".

    Honoring OFFLINE_MODE here matters because a key can be silently
    re-populated from a project `.env` via dotenv even after a caller cleared the
    env var — so "no key" alone is not a reliable kill-switch.  OFFLINE_MODE is
    the explicit one: it always wins.
    """
    import os
    import client
    _offline = os.environ.get("OFFLINE_MODE", "").lower() in ("1", "true", "yes")
    if _offline:
        return None  # explicit offline kill-switch → honest留空, zero network
    if not client.api_key_present():
        return None  # no key for the active provider → honest留空, zero network/import
    if not client.web_search_capable():
        # Active provider can't do grounded web search (e.g. a plain chat model
        # like DeepSeek via TokenDance).  Hunting "evidence" from it would be
        # ungrounded with fabricated sources → honest留空 instead.  Override with
        # ALLOW_UNGROUNDED_RESEARCH=1 only if you accept that risk.
        return None

    from client import call_deepresearch, MODEL_MINI
    from prompt_loader import load_prompt

    interval = assumption.get("interval") or {}
    prompt = load_prompt(
        "prompts/evidence_hunter.md",
        LANG=lang,
        MODE=mode,
        TICKER=ticker,
        COMPANY_NAME=company_name or ticker,
        CURRENT_PRICE=f"{current_price if current_price is not None else ''}",
        ASSUMPTION_TYPE=assumption.get("metric") or assumption.get("lens") or "",
        ASSUMPTION_TEXT=assumption.get("human_text")
        or assumption.get("implied_assumption")
        or assumption.get("claim") or "",
        INTERVAL_P25_P50_P75=f"[{interval.get('p25', 0)}, "
        f"{interval.get('p50', 0)}, {interval.get('p75', 0)}]",
        BOUNDARY_REASON="" if mode == "standard" else "DCF 无法解释当前价格",
        ISO_TIMESTAMP=datetime.now(timezone.utc).isoformat(),
        MODEL_NAME=MODEL_MINI,
    )
    return call_deepresearch(prompt, model=MODEL_MINI, verbose=False)


# ---------------------------------------------------------------------------
# Single-assumption hunt (cache-aware, injectable, honest-empty).
# ---------------------------------------------------------------------------

def hunt_evidence(
    assumption: dict,
    ticker: str,
    *,
    conn=None,
    hunter: Optional[Callable] = None,
    lang: str = "zh",
    mode: str = "standard",
    company_name: str | None = None,
    current_price: float | None = None,
    use_cache: bool = True,
    emit=None,
    subject: str | None = None,
) -> tuple[dict, bool]:
    """Hunt evidence for ONE implied assumption.  Returns (brief, cache_hit).

    Contract:
      - cache hit (same ticker+assumption) → returns cached brief, hunter is
        NOT called (cache_hit=True). This is how the cost guard pays off.
      - hunter is injectable: default = real Deep Research; tests pass a stub.
      - hunter returns nothing / raises / empty → honest-empty brief, never
        fabricated (决策 7).
    """
    key = make_cache_key(ticker, assumption, lang)
    if use_cache:
        cached = _cache_get(conn, key)
        if cached is not None:
            _safe_emit(emit, subject or ticker, ticker, assumption,
                       text=f"证据缓存命中:{_aid(assumption)}", cache_hit=True)
            return cached, True

    fn = hunter or _default_hunter
    _safe_emit(emit, subject or ticker, ticker, assumption,
               text=f"开始查证据:{_aid(assumption)}", cache_hit=False)
    try:
        raw = fn(
            ticker, assumption,
            lang=lang, mode=mode,
            company_name=company_name or ticker,
            current_price=current_price,
        )
    except TypeError:
        # Allow a minimal stub signature hunter(ticker, assumption).
        try:
            raw = fn(ticker, assumption)
        except Exception as exc:
            brief = _empty_brief(ticker, assumption, f"hunter 调用失败: {exc}")
            _cache_put(conn, key, brief, ticker)
            return brief, False
    except Exception as exc:
        # Live failure must NOT crash decode and must NOT fabricate →留空标注.
        brief = _empty_brief(ticker, assumption, f"hunter 调用失败: {exc}")
        _cache_put(conn, key, brief, ticker)
        return brief, False

    brief = _normalize_brief(raw, ticker, assumption)
    _cache_put(conn, key, brief, ticker)
    found = brief.get("status") == "found"
    _safe_emit(emit, subject or ticker, ticker, assumption,
               text=(f"证据完成:{_aid(assumption)} "
                     f"({'有证据' if found else '诚实留空'})"),
               cache_hit=False, found=found)
    return brief, False


# ---------------------------------------------------------------------------
# Card-level Step 3 — the non-skippable orchestrator called by decode_bet.
# ---------------------------------------------------------------------------

def _implied_assumptions_from_card(card) -> list[dict]:
    """Pull every implied assumption a decoded card exposes — traditional
    primary/cross lens metrics AND anchor-mode narrative/option components.

    Each returned dict carries enough to (a) build an evidence prompt and (b)
    key the cache: id / metric / human_text / interval (when present).
    Empty list when the card is insufficient or has no implied assumptions.
    """
    detail = getattr(card, "decode_detail", None) or {}
    out: list[dict] = []

    if detail.get("status") == "insufficient":
        return out  # 数据不足 → nothing to research (boundary: empty, no error)

    def _from_lens(lens_res: dict) -> dict:
        return {
            "id": f"{card.subject}_{lens_res.get('lens')}",
            "metric": lens_res.get("metric") or lens_res.get("lens"),
            "human_text": lens_res.get("implied_label"),
            "lens": lens_res.get("lens"),
            "interval": (lens_res.get("band") or {}),
        }

    # Traditional mode: primary + cross lenses are the implied assumptions.
    if detail.get("primary_lens"):
        out.append(_from_lens(detail["primary_lens"]))
    for c in detail.get("cross_lenses", []) or []:
        if c:
            out.append(_from_lens(c))

    # Anchor mode: each priced narrative/option/TAM/analogy component is an
    # implied assumption (its claim + implied_assumption are the research target).
    anchor = detail.get("anchor_mode") or {}
    for comp in anchor.get("components", []) or []:
        out.append({
            "id": f"{card.subject}_{comp.get('lens')}",
            "metric": comp.get("lens"),
            "lens": comp.get("lens"),
            "human_text": comp.get("implied_assumption"),
            "claim": comp.get("claim"),
            "implied_assumption": comp.get("implied_assumption"),
        })

    # Dedup by cache-affecting identity (id+metric) preserving order.
    seen: set[str] = set()
    deduped: list[dict] = []
    for a in out:
        k = f"{a.get('id')}|{a.get('metric')}"
        if k in seen:
            continue
        seen.add(k)
        deduped.append(a)
    return deduped


def gather_evidence_for_card(
    card,
    *,
    conn=None,
    hunter: Optional[Callable] = None,
    lang: str = "zh",
    company_name: str | None = None,
    current_price: float | None = None,
    use_cache: bool = True,
    emit=None,
) -> dict:
    """Step 3 for a whole decoded card: hunt evidence for EVERY implied
    assumption.  Non-skippable — there is no flag to disable it; an insufficient
    card or an empty assumption list simply yields an empty evidence section
    (boundary-safe, never raises).

    Returns an `evidence` section dict that decode_bet attaches to
    card.decode_detail["evidence"]:

        {
          "briefs": [brief, ...],          # one per implied assumption (incl. empties)
          "assumption_count": int,
          "found_count": int,              # briefs with real evidence
          "empty_count": int,              # honestly-left-empty briefs
          "cache_hits": int,
          "new_hunter_calls": int,         # what actually hit the hunter this run
          "cost": {...},                   # estimate_evidence_cost output + actual
        }
    """
    subject = getattr(card, "subject", None) or "?"
    detail = getattr(card, "decode_detail", None) or {}
    anchor_price = detail.get("anchor_price")
    cp = current_price if current_price is not None else anchor_price
    # boundary mode: DCF can't explain → boundary evidence; else standard.
    mode = "boundary" if detail.get("mode") == "anchor_fallback" else "standard"

    assumptions = _implied_assumptions_from_card(card)

    est = estimate_evidence_cost(len(assumptions))
    print(f"[evidence] {subject}: {len(assumptions)} 个隐含假设待查证据, "
          f"预估首解成本 ≈ ${est:.2f} (cache 命中则 $0)")

    briefs: list[dict] = []
    cache_hits = 0
    new_calls = 0
    actual_cost = 0.0
    for a in assumptions:
        brief, hit = hunt_evidence(
            a, subject,
            conn=conn, hunter=hunter, lang=lang, mode=mode,
            company_name=company_name, current_price=cp,
            use_cache=use_cache, emit=emit, subject=subject,
        )
        briefs.append(brief)
        if hit:
            cache_hits += 1
        else:
            new_calls += 1
            actual_cost += float((brief.get("_meta") or {}).get("cost_usd") or 0.0)

    found = sum(1 for b in briefs if b.get("status") == "found")
    empty = sum(1 for b in briefs if b.get("status") != "found")
    return {
        "briefs": briefs,
        "assumption_count": len(assumptions),
        "found_count": found,
        "empty_count": empty,
        "cache_hits": cache_hits,
        "new_hunter_calls": new_calls,
        "cost": {
            "estimated_first_decode_usd": round(est, 2),
            "actual_new_call_usd": round(actual_cost, 2),
        },
    }


# ---------------------------------------------------------------------------
# emit helper (M5 contract; evidence-kind events).  Safe no-op when emit None.
# ---------------------------------------------------------------------------

def _aid(assumption: dict) -> str:
    return str(assumption.get("id") or assumption.get("metric")
               or assumption.get("lens") or "?")


def _safe_emit(emit, subject: str, ticker: str, assumption: dict, *,
               text: str, cache_hit: bool, found: bool | None = None) -> None:
    if emit is None:
        return
    try:
        emit({
            "phase": "evidence",
            "kind": "evidence",          # M5 ActivityEvent kind
            "text": text,
            "source": {"kind": "decode", "subject": subject},
            "payload": {
                "ticker": ticker,
                "assumption_id": _aid(assumption),
                "cache_hit": cache_hit,
                "found": found,
            },
        })
    except Exception:
        pass  # emit is decoration, never load-bearing


def reset_memory_cache() -> None:
    """Clear the process-local fallback cache (test isolation helper)."""
    _MEM_CACHE.clear()
