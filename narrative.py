"""Market Narrative Researcher (Bet Decoder).

ONE subject-level Deep Research pass into the LIVE market debate (bull / bear /
sentiment regime / catalysts), to be bound back to the formula's implied numbers.
Contrast with `evidence.py`, which hunts per-implied-number; this researches the
*market's reasoning about the subject*, which the formula structurally can't.

SCOPE OF THIS MODULE (deliberately partial):
  - prompt build (template fill)                       ✅ shape-independent
  - cached + injectable + OFFLINE-guarded research call ✅ shape-independent
  - honest-empty envelope (no fabricated narrative)     ✅ shape-independent
  - TYPED parse of the model JSON (regime/bull/bear/    ⛔ NOT yet — waits on the
    assumption_bindings ...) and the formula<->narrative    first real output so we
    binding step                                            shape it to reality.

The cache stores the RAW model output, so a later parser (shaped to what the model
actually returns) can re-read cached results without paying for a re-call.
"""
from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from typing import Callable, Optional
from urllib.parse import urlparse

import db

NARRATIVE_CATEGORY = "market_narrative"
_PROMPT_PATH = "prompts/market_narrative.md"

# process-local fallback cache when no sqlite conn is supplied (mirrors evidence.py)
_MEM_CACHE: dict[str, dict] = {}


def reset_memory_cache() -> None:
    """Clear the process-local fallback cache (tests)."""
    _MEM_CACHE.clear()


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def make_cache_key(subject: str, lang: str = "zh", as_of: str | None = None) -> str:
    """Stable cache key for a subject's market narrative on a given day.

    Sentiment moves on a daily-ish cadence, so the key is subject+date+lang — one
    subject's narrative is reused across ALL its cards that day (the market card,
    a portfolio leg, an analyst-PT card), which is where the cost saving lives.
    Uses sha1 (not the builtin hash()) so the key is stable across processes — the
    cross-process cache bug Phase 4 fixed in evidence.py.
    """
    day = as_of or _today_utc()
    raw = f"{(subject or '').upper()}|{lang}|{day}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _cache_get(conn, key: str) -> dict | None:
    if conn is not None:
        return db.cache_get(conn, NARRATIVE_CATEGORY, key)
    return _MEM_CACHE.get(key)


def _cache_put(conn, key: str, payload: dict, subject: str | None) -> None:
    if conn is not None:
        db.cache_put(conn, NARRATIVE_CATEGORY, key, payload, ticker=subject)
    else:
        _MEM_CACHE[key] = payload


def build_narrative_prompt(
    subject: str,
    company_name: str | None,
    current_price: float | None,
    implied_assumptions: str,
    lang: str = "zh",
    as_of: str | None = None,
) -> str:
    """Fill the market_narrative template. `implied_assumptions` is a pre-formatted
    bullet block — the formula's implied numbers, handed over as the QUESTIONS to
    research (not answers to verify)."""
    from prompt_loader import load_prompt
    return load_prompt(
        _PROMPT_PATH,
        LANG=lang,
        TICKER=subject,
        COMPANY_NAME=company_name or subject,
        CURRENT_PRICE=f"{current_price if current_price is not None else ''}",
        IMPLIED_ASSUMPTIONS=implied_assumptions,
        ISO_TIMESTAMP=(as_of or datetime.now(timezone.utc).isoformat()),
    )


def _default_researcher(prompt: str) -> dict | None:
    """Real Deep Research call (flagship). Honest-empty under the OFFLINE / no-key
    kill-switch so a default decode never silently bills or hits the network in a
    test / offline run (mirrors evidence._default_hunter)."""
    if os.environ.get("OFFLINE_MODE", "").lower() in ("1", "true", "yes"):
        return None
    if not os.environ.get("MIROMIND_API_KEY"):
        return None
    from client import call_deepresearch, MODEL_FLAGSHIP
    return call_deepresearch(prompt, model=MODEL_FLAGSHIP, verbose=False)


def research_market_narrative(
    subject: str,
    *,
    company_name: str | None = None,
    current_price: float | None = None,
    implied_assumptions: str = "",
    lang: str = "zh",
    conn=None,
    researcher: Optional[Callable[[str], "dict | None"]] = None,
    use_cache: bool = True,
    as_of: str | None = None,
) -> tuple[dict, bool]:
    """Run (or cache-hit) ONE market-narrative research pass for `subject`.

    Returns ``(result, cache_hit)``. ``result`` is a stable envelope::

        {
          "subject", "as_of", "lang",
          "content": <raw model text | "">,   # the JSON the model emitted
          "coverage": "raw" | "unavailable",   # typed coverage comes with the parser
          "_meta": {"cost_usd", "usage", "tool_call_count", "search_results", "model"},
        }

    It deliberately does NOT impose a typed schema on ``content`` yet — the raw
    model output is stored so a later parser (shaped to the real output) can
    re-read cached results without a re-call. honest-empty (coverage
    "unavailable") whenever the researcher is offline / keyless / raises / returns
    nothing — never fabricated.
    """
    key = make_cache_key(subject, lang, as_of)
    if use_cache:
        cached = _cache_get(conn, key)
        if cached is not None:
            return cached, True

    prompt = build_narrative_prompt(
        subject, company_name, current_price, implied_assumptions, lang, as_of
    )
    fn = researcher or _default_researcher
    try:
        raw = fn(prompt)
    except Exception as exc:  # live failure must never crash a decode or fabricate
        result = _unavailable(subject, lang, as_of, f"researcher 调用失败: {exc}")
        _cache_put(conn, key, result, subject)
        return result, False

    content = (raw.get("content") if isinstance(raw, dict) else None) or ""
    if not content:
        result = _unavailable(subject, lang, as_of,
                              "offline / keyless / 空响应 → 市场叙事材料不足")
        _cache_put(conn, key, result, subject)
        return result, False

    result = {
        "subject": subject,
        "as_of": as_of or _today_utc(),
        "lang": lang,
        "content": content,
        # the model self-reports coverage (rich/partial/thin) inside `content`;
        # until the typed parser lands we mark "raw" = present-but-unparsed.
        "coverage": "raw",
        "_meta": {
            "cost_usd": raw.get("cost_usd", 0.0),
            "usage": raw.get("usage"),
            "tool_call_count": raw.get("tool_call_count", 0),
            "search_results": len(raw.get("search_results") or []),
            "model": raw.get("model"),
        },
    }
    _cache_put(conn, key, result, subject)
    return result, False


def _unavailable(subject, lang, as_of, reason: str) -> dict:
    """Honest-empty envelope — no fabricated narrative (project's 查不到留空 rule)."""
    return {
        "subject": subject,
        "as_of": as_of or _today_utc(),
        "lang": lang,
        "content": "",
        "coverage": "unavailable",
        "reason": reason,
        "_meta": {"cost_usd": 0.0, "usage": None, "tool_call_count": 0,
                  "search_results": 0, "model": None},
    }


# ===========================================================================
# Typed parse + source tiering — the CODE-ENFORCED honesty guardrail.
# The prompt asks the model to avoid social-media-only sourcing, but prompts can
# be ignored (the first real run cited X / Instagram / KuCoin), so we re-classify
# every source here and tag low-tier claims rather than trust the model's
# self-restraint. We TAG (not delete) so the card can grey/flag D-tier sources
# while still showing what the model found.
# ===========================================================================

# A = primary (filings / IR / company's own site), B = top-tier financial press /
# sell-side, C = general media / research aggregators / named-author opinion
# (default), D = social & non-news (never sufficient as a claim's only source).
# Matched against the URL HOST (exact or subdomain), never as a bare substring —
# substring matching mis-fires ("x.com" ⊂ "netflix.com"; "/investor" ⊂ an article
# slug like ".../investors-might-be-penalizing-nvidia").
_TIER_A_HOSTS = ("sec.gov", "nvidia.com")
_TIER_B_HOSTS = (
    "reuters.com", "bloomberg.com", "wsj.com", "ft.com", "cnbc.com", "yahoo.com",
    "kiplinger.com", "barrons.com", "marketwatch.com", "morningstar.com",
    "businessinsider.com", "economist.com", "nikkei.com", "indiatimes.com",
)
_TIER_D_HOSTS = (
    "x.com", "twitter.com", "instagram.com", "reddit.com", "tiktok.com",
    "facebook.com", "youtube.com", "youtu.be", "kucoin.com", "beincrypto.com",
    "t.me", "threads.net",
)
_TIER_RANK = {"A": 3, "B": 2, "C": 1, "D": 0}


def _host_matches(host: str, domains: tuple) -> bool:
    """True when host == domain or is a subdomain of it (never a bare substring)."""
    return any(host == d or host.endswith("." + d) for d in domains)


def classify_source(source: dict | None) -> str:
    """Map one source to a quality tier A/B/C/D from its URL host.

    No URL → "D" (unverifiable). Social / crypto-promo hosts → "D". Primary
    filings / IR / company site → "A". Top-tier financial press → "B".
    Everything else → "C".
    """
    url = ((source or {}).get("url") or "").strip().lower()
    if not url:
        return "D"
    host = urlparse(url).netloc.lower()
    if not host:
        return "D"
    if _host_matches(host, _TIER_D_HOSTS):
        return "D"
    if host.startswith("investor.") or _host_matches(host, _TIER_A_HOSTS):
        return "A"
    if _host_matches(host, _TIER_B_HOSTS):
        return "B"
    return "C"


def _best_tier(sources: list | None) -> str:
    tiers = [classify_source(s) for s in (sources or []) if isinstance(s, dict)]
    if not tiers:
        return "D"  # a claim with no sources is unverified
    return max(tiers, key=lambda t: _TIER_RANK[t])


def parse_narrative(content: str | None) -> dict | None:
    """Parse the model's JSON narrative (tolerant of markdown fences / loose
    escapes via client.parse_loose_json). Returns None on empty/garbage."""
    if not content:
        return None
    try:
        from client import parse_loose_json
        return parse_loose_json(content)
    except Exception:
        return None


_CLAIM_LISTS = ("bull_case", "bear_case", "catalysts")


def validate_narrative(parsed: dict | None) -> dict | None:
    """Tag every claim with a source tier + `unverified` flag (best source is D),
    annotate each source with its tier, and attach a card-level `source_quality`
    summary. Does NOT delete — surfaces quality so the UI can grey/flag rather
    than silently drop."""
    if not parsed:
        return parsed
    counts = {"A": 0, "B": 0, "C": 0, "D": 0}
    unverified = 0

    def _tag(item: dict) -> None:
        nonlocal unverified
        srcs = item.get("sources") or []
        for s in srcs:
            if isinstance(s, dict):
                s["tier"] = classify_source(s)
        bt = _best_tier(srcs)
        item["source_tier"] = bt
        item["unverified"] = (bt == "D")
        counts[bt] += 1
        if bt == "D":
            unverified += 1

    reg = parsed.get("sentiment_regime")
    if isinstance(reg, dict):
        _tag(reg)
    for key in _CLAIM_LISTS:
        for item in (parsed.get(key) or []):
            if isinstance(item, dict):
                _tag(item)

    parsed["source_quality"] = {
        "by_tier": counts,
        "unverified_claims": unverified,
        "total_claims": sum(counts.values()),
    }
    return parsed


def summarize_narrative(full: dict | None) -> dict:
    """Compact form for the card / API (full validated narrative stays on
    decode_detail). Safe on None / unavailable."""
    if not full or full.get("coverage") == "unavailable":
        return {"coverage": (full or {}).get("coverage", "unavailable")}
    reg = full.get("sentiment_regime") or {}
    return {
        "coverage": full.get("coverage"),
        "regime": reg.get("label"),
        "regime_rationale": reg.get("rationale"),
        "headline": full.get("headline"),
        "bull_count": len(full.get("bull_case") or []),
        "bear_count": len(full.get("bear_case") or []),
        "contested": [a.get("axis") for a in (full.get("contested_axis") or [])
                      if isinstance(a, dict)],
        "bindings": [
            {"assumption": b.get("assumption_text"),
             "leans": b.get("where_price_leans"),
             "note": b.get("note")}
            for b in (full.get("assumption_bindings") or []) if isinstance(b, dict)
        ],
        "source_quality": full.get("source_quality"),
    }


def build_card_narrative(envelope: dict | None) -> dict:
    """Research envelope → ``{coverage, full, summary}``.

    ``full`` is the validated + source-tagged narrative (None when unavailable /
    unparseable); ``summary`` is the compact card form. Never raises."""
    if (not envelope or envelope.get("coverage") == "unavailable"
            or not envelope.get("content")):
        return {"coverage": "unavailable", "full": None,
                "summary": {"coverage": "unavailable"}}
    parsed = parse_narrative(envelope.get("content"))
    if parsed is None:
        return {"coverage": "unparseable", "full": None,
                "summary": {"coverage": "unparseable"}}
    full = validate_narrative(parsed)
    cov = full.get("coverage") or "raw"
    full["_meta"] = envelope.get("_meta")
    return {"coverage": cov, "full": full, "summary": summarize_narrative(full)}
