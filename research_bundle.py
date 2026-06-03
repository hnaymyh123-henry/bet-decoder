"""research_bundle.py — ONE Deep Research call per card (evidence + narrative).

The old decode made N per-assumption evidence calls PLUS a separate market-narrative
call (N+1 sequential flagship Deep Research calls — slow + expensive). This module
packages all of it into ONE call: a single research session over the subject returns
one structured JSON, which `split_bundle` distributes back into:

  • an EVIDENCE section  (shape-identical to evidence.gather_evidence_for_card)
  • a NARRATIVE envelope (shape-identical to narrative.research_market_narrative)

so the rest of the pipeline (db.build_card_display, the frontend, cross_check) is
unchanged. Cached by subject+date → a re-decode is $0.

Honest-empty + offline/no-key guarded (mirrors evidence/narrative): never fabricates,
never crashes a decode, never bills in a test/offline run.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Callable, Optional

import db

CATEGORY = "research_bundle"
_PROMPT_PATH = "prompts/research_bundle.md"
_MEM_CACHE: dict[str, dict] = {}


def reset_memory_cache() -> None:
    _MEM_CACHE.clear()


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def make_cache_key(subject: str, lang: str = "zh", as_of: str | None = None) -> str:
    day = as_of or _today_utc()
    raw = f"{(subject or '').upper()}|{lang}|{day}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _cache_get(conn, key):
    return db.cache_get(conn, CATEGORY, key) if conn is not None else _MEM_CACHE.get(key)


def _cache_put(conn, key, payload, subject):
    if conn is not None:
        db.cache_put(conn, CATEGORY, key, payload, ticker=subject)
    else:
        _MEM_CACHE[key] = payload


def build_bundle_prompt(subject, company_name, current_price, implied_assumptions,
                        lang="zh", as_of=None) -> str:
    from prompt_loader import load_prompt
    return load_prompt(
        _PROMPT_PATH, LANG=lang, TICKER=subject,
        COMPANY_NAME=company_name or subject,
        CURRENT_PRICE=f"{current_price if current_price is not None else ''}",
        IMPLIED_ASSUMPTIONS=implied_assumptions,
        ISO_TIMESTAMP=(as_of or datetime.now(timezone.utc).isoformat()))


def _default_bundle_researcher(prompt: str) -> dict | None:
    """One flagship Deep Research call. Honest-empty under OFFLINE / no-key / no-web
    (mirrors evidence._default_hunter + narrative._default_researcher) so a default
    decode never silently bills or hits the network in a test/offline run."""
    import client
    if os.environ.get("OFFLINE_MODE", "").lower() in ("1", "true", "yes"):
        return None
    if not client.api_key_present():
        return None
    if not client.web_search_capable():
        return None
    return client.call_deepresearch(prompt, model=client.MODEL_FLAGSHIP, verbose=False)


def _unavailable(subject, lang, as_of, reason) -> dict:
    return {"subject": subject, "as_of": as_of or _today_utc(), "lang": lang,
            "content": "", "coverage": "unavailable", "reason": reason,
            "_meta": {"cost_usd": 0.0, "usage": None, "tool_call_count": 0,
                      "search_results": 0, "model": None}}


def research_bundle(subject, *, company_name=None, current_price=None,
                    implied_assumptions="", lang="zh", conn=None,
                    researcher: Optional[Callable[[str], "dict | None"]] = None,
                    use_cache: bool = True, as_of: str | None = None) -> tuple[dict, bool]:
    """Run (or cache-hit) ONE research-bundle pass. Returns (envelope, cache_hit).
    Envelope mirrors narrative.research_market_narrative: {subject, as_of, lang,
    content (raw model JSON text | ""), coverage ("raw"|"unavailable"), _meta}."""
    key = make_cache_key(subject, lang, as_of)
    if use_cache:
        cached = _cache_get(conn, key)
        if cached is not None:
            return cached, True
    prompt = build_bundle_prompt(subject, company_name, current_price,
                                 implied_assumptions, lang, as_of)
    fn = researcher or _default_bundle_researcher
    try:
        raw = fn(prompt)
    except Exception as exc:
        result = _unavailable(subject, lang, as_of, f"bundle researcher 调用失败: {exc}")
        _cache_put(conn, key, result, subject)
        return result, False
    content = (raw.get("content") if isinstance(raw, dict) else None) or ""
    if not content:
        result = _unavailable(subject, lang, as_of, "offline/keyless/空响应 → 材料不足")
        _cache_put(conn, key, result, subject)
        return result, False
    result = {
        "subject": subject, "as_of": as_of or _today_utc(), "lang": lang,
        "content": content, "coverage": "raw",
        "_meta": {"cost_usd": raw.get("cost_usd", 0.0), "usage": raw.get("usage"),
                  "tool_call_count": raw.get("tool_call_count", 0),
                  "search_results": len(raw.get("search_results") or []),
                  "model": raw.get("model")},
    }
    _cache_put(conn, key, result, subject)
    return result, False


def _empty_evidence_section(n: int) -> dict:
    return {"briefs": [], "assumption_count": n, "found_count": 0, "empty_count": 0,
            "cache_hits": 0, "new_hunter_calls": 0, "via_bundle": True,
            "cost": {"estimated_first_decode_usd": 0.0, "actual_new_call_usd": 0.0}}


# A Deep Research model often appends a reference list ("[11] ... https://...")
# AFTER the JSON object, which makes a whole-string json.loads fail. And it may
# echo its OWN assumption_id ("implied_pe_32.91") instead of the id we asked for
# ("NVDA_pe"). The two helpers below make the split robust to both.

def _parse_content(content: str):
    """Extract the first complete top-level JSON object from `content`, tolerant of
    leading prose / markdown fences / a trailing reference list. None on failure."""
    if not content:
        return None
    try:
        from client import parse_loose_json
        p = parse_loose_json(content)
        if isinstance(p, dict):
            return p
    except Exception:
        pass
    i = content.find("{")
    if i < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for j in range(i, len(content)):
        ch = content[j]
        if esc:
            esc = False
            continue
        if ch == "\\":
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                blob = content[i:j + 1]
                try:
                    return json.loads(blob)
                except Exception:
                    try:
                        from client import parse_loose_json
                        return parse_loose_json(blob)
                    except Exception:
                        return None
    return None


# Lens token → synonyms that may appear in the model's assumption_id / text, so we
# can match the model's free-form ids back to OUR assumptions by valuation lens.
_LENS_SYNONYMS = {
    "pe": ("nvda_pe", "_pe", "p/e", "pe", "市盈"),
    "dcf": ("dcf", "cagr", "营收", "增速", "revenue", "growth"),
    "p_fcf": ("p_fcf", "p/fcf", "fcf", "自由现金"),
    "ps": ("p_s", "p/s", "市销"),
    "ev_ebitda": ("ev_ebitda", "ebitda"),
    "p_b": ("p_b", "p/b", "市净", "book"),
    "peg": ("peg",),
    "narrative": ("narrative", "叙事", "溢价", "option", "期权", "tam"),
}


def _match_entry(a: dict, ev_list: list, by_id: dict) -> dict | None:
    """Find the bundle evidence entry for our assumption `a`: exact id first, then a
    fuzzy match by valuation-lens token (the model often renames the id)."""
    aid = str(a.get("id") or "")
    if aid in by_id:
        return by_id[aid]
    tok = str(a.get("metric") or a.get("lens") or "").lower()
    syns = _LENS_SYNONYMS.get(tok, (tok,) if tok else ())
    for e in ev_list:
        if not isinstance(e, dict):
            continue
        hay = (str(e.get("assumption_id")) + " " + str(e.get("assumption_text"))).lower()
        if any(s and s in hay for s in syns):
            return e
    return None


def split_bundle(envelope: dict, assumptions: list[dict], *, ticker: str,
                 lang: str = "zh") -> tuple[dict, dict]:
    """Distribute ONE bundle envelope back into (evidence_section, narrative_envelope),
    each shape-identical to the legacy per-call outputs. Honest-empty when the bundle
    is unavailable/unparseable. Never raises."""
    import evidence as _ev

    content = (envelope or {}).get("content") or ""
    parsed = _parse_content(content)

    if not isinstance(parsed, dict):
        ev_section = _empty_evidence_section(len(assumptions))
        # still record the honest-empty briefs so the card shows what was asked
        ev_section["briefs"] = [_ev._empty_brief(ticker, a, "bundle 不可用/解析失败")
                                for a in assumptions]
        ev_section["empty_count"] = len(assumptions)
        narr_env = {"subject": ticker, "as_of": (envelope or {}).get("as_of"),
                    "lang": lang, "content": "",
                    "coverage": (envelope or {}).get("coverage", "unavailable"),
                    "_meta": (envelope or {}).get("_meta")}
        return ev_section, narr_env

    # ---- evidence ----
    ev_list = [e for e in (parsed.get("evidence") or []) if isinstance(e, dict)]
    by_id: dict[str, dict] = {}
    for e in ev_list:
        if e.get("assumption_id"):
            by_id[str(e["assumption_id"])] = e
    bundle_cost = float((envelope.get("_meta") or {}).get("cost_usd") or 0.0)
    briefs = []
    for a in assumptions:
        entry = _match_entry(a, ev_list, by_id)
        if isinstance(entry, dict) and (entry.get("evidence_items")):
            # compute evidence_count from item stances so downstream tallies are real
            cnt = {"support": 0, "refute": 0, "neutral": 0}
            for it in entry.get("evidence_items") or []:
                st = str((it or {}).get("stance") or "neutral").lower()
                cnt[st if st in cnt else "neutral"] += 1
            entry.setdefault("evidence_count", cnt)
            briefs.append(_ev._normalize_brief(entry, ticker, a))
        else:
            briefs.append(_ev._empty_brief(ticker, a, "bundle 未覆盖该假设(诚实留空)"))
    found = sum(1 for b in briefs if b.get("status") == "found")
    ev_section = {
        "briefs": briefs, "assumption_count": len(assumptions),
        "found_count": found, "empty_count": len(briefs) - found,
        "cache_hits": 0, "new_hunter_calls": 1, "via_bundle": True,
        "cost": {"estimated_first_decode_usd": 0.0,
                 "actual_new_call_usd": round(bundle_cost, 2)},
    }

    # ---- narrative (everything except `evidence`) → narrative envelope ----
    narr_fields = {k: v for k, v in parsed.items() if k != "evidence"}
    narr_fields.setdefault("subject", ticker)
    narr_env = {
        "subject": ticker, "as_of": envelope.get("as_of"), "lang": lang,
        "content": json.dumps(narr_fields, ensure_ascii=False),
        "coverage": "raw", "_meta": envelope.get("_meta"),
    }
    return ev_section, narr_env
