"""Module 3 — Cross-card synthesis (跨卡综合).

A *pure consumer* relation engine + synthesis narrator over a set of already-
stored BetCards (PRD 模块 3).  It reads cards by id, discovers **horizontal**
relations between them (vs. M2's vertical single-card cross-check), and asks a
chat-mode LLM to weave a grounded narrative.  It NEVER mutates / creates /
persists cards — the only thing it writes is its own `SynthesisResult` blob into
`llm_cache` (category ``"synthesis"``, key = sorted card-set hash).

Five relation types, auto-routed per card pair (PRD 决策 5):
  - same subject, different source  →  consensus | divergence | contradiction
  - different subject               →  same-source (同源, the core Aha)
  - same series, different time      →  drift (漂移)

Strength is **strong | medium | weak** only (PRD 决策 6, no 0-100 false
precision).  For same-subject / same-lens pairs the Monte-Carlo DCF *band width*
is the yard-stick for "is this gap significant" (gap < ½ band = weak, ≈ 1 band =
medium, > 1 band = strong); non-DCF lenses use a small threshold registry.
Same-source strength is the **geometric mean** √(exposureA × exposureB) of the
two cards' shared-theme exposures.

Cost discipline: this module is **chat-mode only** (never Deep Research).  All
LLM touch-points (theme fuzzy-match for 同源, relation wording, narrative) go
through an injectable ``chat`` hook that defaults to the real ``client.call_chat``
but is replaced by a stub in tests, so the deterministic logic (routing, strength,
geometric mean, caching) is exercised at **zero API cost**.

Public entry point::

    synthesize_cards(card_ids, lang="zh", emit=None, *, conn=None, chat=None)
        -> SynthesisResult (a plain dict, see API_CONTRACT.md §3)
"""
from __future__ import annotations

import hashlib
import json
import math
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import db

# ---------------------------------------------------------------------------
# Relation-type constants + headline weighting.
# ---------------------------------------------------------------------------

REL_CONSENSUS = "consensus"
REL_DIVERGENCE = "divergence"
REL_CONTRADICTION = "contradiction"
REL_SAME_SOURCE = "same-source"   # 同源
REL_DRIFT = "drift"               # 漂移

# headline_insight = Top1 by (strength_rank × type_weight).  同源 (same-source)
# is the most valuable Aha so it carries the highest weight (PRD glossary).
_TYPE_WEIGHT: dict[str, float] = {
    REL_SAME_SOURCE: 1.6,
    REL_CONTRADICTION: 1.3,
    REL_DIVERGENCE: 1.1,
    REL_CONSENSUS: 1.0,
    REL_DRIFT: 0.9,
}
_STRENGTH_RANK: dict[str, int] = {"strong": 3, "medium": 2, "weak": 1}

# Soft ceiling: above this many cards we hint the caller to narrow the set, but
# never hard-fail (PRD 决策 7: 数量下限 2、无硬上限、>8 软提示).
_SOFT_CARD_CEILING = 8

# Default-arg sentinel: lets us tell "caller passed nothing" (→ real client)
# apart from an explicit ``chat=None`` (→ no LLM, deterministic graph-only).
_CHAT_SENTINEL = object()


# ---------------------------------------------------------------------------
# Non-DCF lens significance thresholds.
#
# For same-lens, same-subject pairs we need a "how big a gap is meaningful" ruler.
# DCF carries a Monte-Carlo band so we use band width directly.  Other lenses
# have no band, so we fall back to a *relative* gap threshold on the implied
# multiple: |a - b| / mean.  These are deliberately coarse (three buckets).
# ---------------------------------------------------------------------------

# relative-gap cut points → (weak if < lo, medium if < hi, else strong)
_NON_DCF_THRESHOLDS: dict[str, tuple[float, float]] = {
    "pe": (0.10, 0.30),
    "ps": (0.12, 0.35),
    "ev_ebitda": (0.12, 0.35),
    "p_fcf": (0.15, 0.40),
    "p_b": (0.15, 0.40),
    "peg": (0.15, 0.40),
}
_DEFAULT_THRESHOLD = (0.15, 0.40)


# ===========================================================================
# emit helper (M5 callback contract — safe no-op when emit is None)
# ===========================================================================

def _safe_emit(emit, *, phase: str, kind: str, text: str,
               subject: str, card_ids: list[str], payload: dict | None = None) -> None:
    """Best-effort ActivityEvent emit. ``emit=None`` ⇒ no streaming side effects.
    A broken emit callback must never break synthesis, so we swallow exceptions.
    """
    if emit is None:
        return
    try:
        emit({
            "phase": phase,
            "kind": kind,
            "text": text,
            "source": {"kind": "synthesis", "card_ids": card_ids, "subject": subject},
            "payload": payload,
        })
    except Exception:
        pass  # emit is decoration, never load-bearing


# ===========================================================================
# Card-set cache key (deterministic, order-insensitive)
# ===========================================================================

def cache_key_for(card_ids: list[str]) -> str:
    """Stable hash of a card set, independent of input order (PRD 决策 7:
    增删卡 → 新 hash → 重跑).  Duplicate ids collapse so the same logical set
    always maps to the same key."""
    canon = "|".join(sorted(set(card_ids)))
    return "synthesis_" + hashlib.md5(canon.encode("utf-8")).hexdigest()[:16]


# ===========================================================================
# Driver extraction — read a card's primary implied metric + (optional) band
# ===========================================================================

def _band_from_run(conn, run_id: int | None) -> dict | None:
    """Read the DCF Monte-Carlo band (R2) off a persisted run's rdcf_intervals
    (the `revenue_cagr_5y` row carries p25/p75).  Returns None when unavailable
    or when no conn is supplied."""
    if conn is None or run_id is None:
        return None
    try:
        row = conn.execute(
            "SELECT p25, p50, p75, success_rate FROM rdcf_intervals "
            "WHERE run_id = ? AND variable = ?",
            (run_id, "revenue_cagr_5y"),
        ).fetchone()
    except Exception:
        return None
    if row is None:
        return None
    p25, p50, p75, sr = row["p25"], row["p50"], row["p75"], row["success_rate"]
    if p25 is None and p50 is None and p75 is None:
        return None
    return {"p25": p25, "p50": p50, "p75": p75, "success_rate": sr}


def _read_driver(card: "db.BetCard", conn=None) -> dict:
    """Normalize a card's primary "driver" into a comparable view.

    Returns::

        {
          "lens": str | None,         # lens key (dcf/pe/...) or "anchor"/None
          "family": str | None,       # "dcf" | "multiple" | "anchor" | None
          "value": float | None,      # the comparable implied scalar
          "label": str | None,        # human label for the metric
          "band": {p25,p50,p75,...} | None,  # DCF Monte-Carlo band (R2)
          "comparable": bool,         # False for anchor-mode / insufficient cards
          "status": str | None,       # "insufficient" when the card is a stub
        }

    Source order: the in-process ``decode_detail`` attribute (set by M2 / tests)
    first, then the persisted run (band only).  ``card.bet`` is the always-present
    fallback scalar.
    """
    detail = getattr(card, "decode_detail", None) or {}
    status = detail.get("status")

    # Insufficient / stub card: nothing comparable.
    if status == "insufficient":
        return {"lens": None, "family": None, "value": card.bet, "label": None,
                "band": None, "comparable": False, "status": "insufficient"}

    # Anchor-mode card (anchor_primary / anchor_fallback): the bet is the
    # narrative/anchor share of price — NOT a traditional lens driver, so it is
    # not directly comparable to a DCF/multiple driver from another card.
    mode = detail.get("mode")
    if mode in ("anchor_primary", "anchor_fallback") or detail.get("anchor_mode"):
        band = detail.get("r2_band") or _band_from_run(conn, card.run_id)
        return {"lens": "anchor", "family": "anchor", "value": card.bet,
                "label": "叙事/锚定成分占价", "band": band,
                "comparable": False, "status": None}

    # Traditional / primary-lens card.
    primary = detail.get("primary_lens") or {}
    lens_key = primary.get("lens")
    family = primary.get("lens_family")
    value = primary.get("implied_value")
    if value is None:
        value = card.bet
    label = primary.get("implied_label")
    band = primary.get("band")
    if band is None and (lens_key == "dcf" or family == "dcf"):
        band = _band_from_run(conn, card.run_id)

    # Production fallback: a card rebuilt from storage (get_card) has NO
    # decode_detail — save_card doesn't persist it (contract: the comparable
    # scalar rides on `card.bet`, the DCF band lives in runs.rdcf_intervals).
    # When a DCF band exists on the card's run, treat it as a DCF driver so the
    # band-ruler still applies.  Otherwise keep the bet scalar with an unknown
    # lens (two unknown-lens cards still compare on one axis via the threshold).
    if lens_key is None and not detail:
        run_band = _band_from_run(conn, card.run_id)
        if run_band is not None:
            lens_key, family, band = "dcf", "dcf", run_band
            label = label or "隐含 5 年营收 CAGR"

    return {"lens": lens_key, "family": family, "value": value, "label": label,
            "band": band, "comparable": True, "status": None}


# ===========================================================================
# Strength helpers
# ===========================================================================

def _band_width(band: dict | None) -> float | None:
    """p75 - p25 width of a Monte-Carlo band, or None when unusable."""
    if not band:
        return None
    p25, p75 = band.get("p25"), band.get("p75")
    if p25 is None or p75 is None:
        return None
    w = abs(p75 - p25)
    return w if w > 0 else None


def _strength_by_band(gap: float, band_width: float) -> str:
    """Map a driver gap onto strong/medium/weak using the band width as ruler
    (PRD 决策 6): gap < ½ band → weak, ½..1 band → medium, > 1 band → strong."""
    if gap < 0.5 * band_width:
        return "weak"
    if gap <= 1.0 * band_width:
        return "medium"
    return "strong"


def _strength_by_threshold(rel_gap: float, lens_key: str | None) -> str:
    """Non-DCF strength: relative gap vs. a per-lens coarse threshold."""
    lo, hi = _NON_DCF_THRESHOLDS.get(lens_key or "", _DEFAULT_THRESHOLD)
    if rel_gap < lo:
        return "weak"
    if rel_gap < hi:
        return "medium"
    return "strong"


def _driver_gap_strength(da: dict, db_: dict) -> tuple[str, bool, float | None]:
    """Strength of a same-subject driver comparison.

    Returns (strength, comparable, gap).  comparable=False when the two drivers
    can't be put on one axis (different lens, or one side is anchor-mode) — the
    caller then downgrades to a qualitative relation with ``comparable=false``.
    """
    va, vb = da.get("value"), db_.get("value")
    la, lb = da.get("lens"), db_.get("lens")

    # Non-comparable: anchor-mode involved, or different lenses, or missing value.
    if not da.get("comparable") or not db_.get("comparable"):
        return "weak", False, None
    if va is None or vb is None:
        return "weak", False, None
    if la != lb:
        return "weak", False, None

    gap = abs(va - vb)

    # DCF: use the wider of the two cards' band widths as the ruler.
    if la == "dcf" or da.get("family") == "dcf":
        widths = [w for w in (_band_width(da.get("band")), _band_width(db_.get("band"))) if w]
        if widths:
            return _strength_by_band(gap, max(widths)), True, gap
        # No band available → fall through to relative threshold.

    # Multiple / other lens: relative gap vs. threshold.
    #
    # Sign divergence first.  For a multiple lens the sign of the *implied*
    # value carries meaning (e.g. P/E +20 = priced as profitable vs. P/E −20 =
    # priced as loss-making): a positive-vs-negative pair is a fundamental
    # disagreement about the business state, not a mere numeric gap.  We surface
    # that as a strong contradiction directly, before the rel-gap math (which
    # would otherwise just see a large distance OR — worse, when |va|==|vb| —
    # cancel out around a near-zero mean).
    if (va > 0) != (vb > 0) and va != 0 and vb != 0:
        return "strong", True, gap

    # Both same sign (or one is exactly zero).  Use the relative gap vs. a
    # per-lens threshold.  When the mean magnitude is 0 the two values are not
    # on a usable common scale (e.g. both ≈0), so the comparison is *not*
    # comparable rather than silently "weak".
    mean = (abs(va) + abs(vb)) / 2.0
    if mean == 0:
        return "weak", False, gap
    rel_gap = gap / mean
    return _strength_by_threshold(rel_gap, la), True, gap


# ===========================================================================
# Theme alignment (同源) — geometric mean + fuzzy match
# ===========================================================================

def _exact_theme_match(theme_a: str, theme_b: str) -> bool:
    """Deterministic theme equality (case/space-insensitive substring either way).
    Used when no chat hook is supplied (tests / offline)."""
    a = (theme_a or "").strip().lower()
    b = (theme_b or "").strip().lower()
    if not a or not b:
        return False
    return a == b or a in b or b in a


def _themes_align(theme_a: str, theme_b: str, chat: Optional[Callable]) -> bool:
    """Decide whether two theme labels denote the same underlying theme.

    Cheap path first (exact/substring) — if it already matches we never spend an
    LLM call.  Otherwise, when a ``chat`` hook is supplied, ask it for a yes/no
    fuzzy judgement (embedded in the synthesis call budget, no extra Deep
    Research).  ``chat=None`` ⇒ exact-only (deterministic, free).

    Stateless single-pair primitive.  For a whole synthesize run use
    ``_ThemeAligner`` (memoizes + caps the chat calls); this bare function is the
    fallback path the aligner delegates to and is still used directly by tests.
    """
    if _exact_theme_match(theme_a, theme_b):
        return True
    if chat is None:
        return False
    prompt = (
        "判断下面两个投资主题词是否指向同一底层主题(同义/包含即算同一)。"
        "只回答 JSON {\"same\": true|false}。\n"
        f"主题A: {theme_a}\n主题B: {theme_b}"
    )
    try:
        raw = chat(prompt)
        data = _parse_chat_json(raw)
        return bool(data.get("same"))
    except Exception:
        return False  # chat failure → honest "no match", never fabricate 同源


# Hard ceiling on fuzzy theme-alignment chat calls per synthesize run.  The
# pairwise routing is O(cards²) and each pair compares O(themes²) theme labels,
# so a naive run could fire hundreds of serial LLM calls.  We memoize every
# alignment verdict (so a repeated theme pair never re-asks) and stop spending
# new chat calls once this many fuzzy questions have been asked — past the cap
# every still-unresolved pair degrades to exact-string matching (deterministic,
# free), never fabricating 同源.
_THEME_ALIGN_CALL_CAP = 8


class _ThemeAligner:
    """Run-scoped theme-alignment with memoization + a hard chat-call cap.

    One instance lives for the duration of a single ``synthesize_cards`` call.
    It wraps the (optional) ``chat`` hook and guarantees:

      - the exact/substring cheap path is always tried first (free);
      - each *unordered* theme pair is fuzzy-matched via chat **at most once**
        (memoized verdict reused for every later occurrence of that pair);
      - the total number of fuzzy chat calls is bounded by ``call_cap`` — once
        hit, every still-unresolved pair falls back to exact matching only.

    ``chat=None`` ⇒ exact-only, the aligner never touches an LLM.
    """

    def __init__(self, chat: Optional[Callable], call_cap: int | None = None):
        self._chat = chat
        # Resolve the cap at *construction* time (not as a default-arg bound at
        # def-time) so the module-level ``_THEME_ALIGN_CALL_CAP`` stays tunable.
        self._call_cap = _THEME_ALIGN_CALL_CAP if call_cap is None else call_cap
        self._chat_calls = 0                     # fuzzy chat calls actually made
        self._memo: dict[tuple[str, str], bool] = {}

    @staticmethod
    def _pair_key(theme_a: str, theme_b: str) -> tuple[str, str]:
        """Order-insensitive, normalized key so (A,B) and (B,A) share a memo slot."""
        a = (theme_a or "").strip().lower()
        b = (theme_b or "").strip().lower()
        return (a, b) if a <= b else (b, a)

    @property
    def chat_calls(self) -> int:
        return self._chat_calls

    def align(self, theme_a: str, theme_b: str) -> bool:
        # Cheap deterministic path: never consumes a chat call or a memo slot.
        if _exact_theme_match(theme_a, theme_b):
            return True
        if self._chat is None:
            return False

        key = self._pair_key(theme_a, theme_b)
        if key in self._memo:
            return self._memo[key]          # already asked this pair → reuse

        # Over the budget: degrade to exact-only (already failed above → False).
        # Memoize the False so we don't keep re-checking the same pair either.
        if self._chat_calls >= self._call_cap:
            self._memo[key] = False
            return False

        self._chat_calls += 1
        verdict = _themes_align(theme_a, theme_b, self._chat)
        self._memo[key] = verdict
        return verdict


def _shared_theme_strength(card_a: "db.BetCard", card_b: "db.BetCard",
                           aligner: "_ThemeAligner"
                           ) -> tuple[str | None, float | None, str]:
    """Find the strongest shared theme between two cards and score its strength.

    Returns (shared_theme | None, geo_mean_pct | None, strength).  Strength comes
    from the **geometric mean** √(exposureA × exposureB) of the two cards'
    exposure % to the aligned theme (PRD 决策 6).  None theme ⇒ no 同源 relation.

    A theme pair only counts when **both** sides carry a real (>0) exposure: a
    geometric mean of 0 means there is no genuine shared exposure, so we skip it
    rather than fabricate a flimsy "0% exposure" 同源 (PRD 决策 8: 绝不凑牵强同源).
    """
    best: tuple[str, float] | None = None  # (theme_label, geo_mean)
    for ta in (card_a.theme_exposures or []):
        for tb in (card_b.theme_exposures or []):
            if not aligner.align(ta.theme, tb.theme):
                continue
            ea = ta.exposure_pct
            eb = tb.exposure_pct
            if ea is None or eb is None or ea <= 0 or eb <= 0:
                # No usable common exposure → not a real 同源 candidate. Skipping
                # (rather than recording geo=0) keeps a contrived "暴露 0%" link
                # from ever winning `best` or occupying the headline.
                continue
            geo = math.sqrt(ea * eb)
            label = ta.theme  # canonical = card_a's label
            if best is None or geo > best[1]:
                best = (label, geo)
    if best is None:
        return None, None, "weak"
    theme, geo = best
    # Geometric-mean exposure (in %) → strength buckets.  These are exposure-share
    # thresholds, deliberately coarse: ≥50% strong, ≥25% medium, else weak.
    if geo >= 50.0:
        strength = "strong"
    elif geo >= 25.0:
        strength = "medium"
    else:
        strength = "weak"
    return theme, geo, strength


# ===========================================================================
# Relation builders (per pair, auto-routed)
# ===========================================================================

def _rel_id() -> str:
    return "rel_" + uuid.uuid4().hex[:8]


def _same_subject_relation(ca, cb, da, db_, *, lang: str) -> dict:
    """Route a same-subject / different-source pair into
    consensus | divergence | contradiction with a band-aware strength."""
    strength, comparable, gap = _driver_gap_strength(da, db_)
    va, vb = da.get("value"), db_.get("value")

    # Decide the relation TYPE.
    if comparable and va is not None and vb is not None:
        if strength == "weak":
            rel_type = REL_CONSENSUS   # values agree within noise
        else:
            # Diverge.  "Contradiction" = the two bets point opposite directions
            # relative to a shared neutral (sign of the gap large AND lenses say
            # opposite stance).  We treat a *large* (strong) gap as contradiction,
            # a moderate (medium) gap as divergence.
            rel_type = REL_CONTRADICTION if strength == "strong" else REL_DIVERGENCE
    else:
        # Not comparable (anchor-mode / mismatched lens): qualitative divergence.
        rel_type = REL_DIVERGENCE

    label = da.get("label") or db_.get("label") or "隐含驱动"
    shared = f"{ca.subject} 的 {label}"
    if comparable and va is not None and vb is not None:
        detail = (f"{ca.source_type} 卡隐含 {va:.3g} vs {cb.source_type} 卡隐含 "
                  f"{vb:.3g}(差 {gap:.3g})。")
        if rel_type == REL_CONSENSUS:
            detail += "两源对该驱动的押注基本一致。"
        elif rel_type == REL_CONTRADICTION:
            detail += "两源对该驱动的押注差距显著,接近相互矛盾。"
        else:
            detail += "两源对该驱动的押注出现可观分歧。"
    else:
        detail = (f"{ca.source_type} 卡与 {cb.source_type} 卡的驱动不可直接比较"
                  f"(lens 不同或含 anchor 模式),仅作定性分歧标注。")

    return {
        "id": _rel_id(),
        "type": rel_type,
        "card_a": ca.card_id,
        "card_b": cb.card_id,
        "strength": strength,
        "shared_assumption": shared,
        "detail": detail,
        "comparable": comparable,
    }


def _drift_relation(ca, cb, da, db_, *, lang: str) -> dict:
    """Route a same-series / different-time pair into a drift relation.
    ``ca`` is the OLDER card, ``cb`` the NEWER (caller orders them)."""
    strength, comparable, gap = _driver_gap_strength(da, db_)
    va, vb = da.get("value"), db_.get("value")
    label = da.get("label") or db_.get("label") or "隐含驱动"
    shared = f"{ca.subject} 的 {label}(随时间)"
    if comparable and va is not None and vb is not None:
        direction = "变激进" if vb > va else ("变保守" if vb < va else "基本持平")
        detail = (f"{ca.trade_date} 隐含 {va:.3g} → {cb.trade_date} 隐含 {vb:.3g}"
                  f"({direction},差 {gap:.3g})。")
    else:
        detail = (f"{ca.trade_date} 与 {cb.trade_date} 两张同序列卡的驱动不可直接"
                  f"比较,仅标注时间上的变化存在。")
    return {
        "id": _rel_id(),
        "type": REL_DRIFT,
        "card_a": ca.card_id,
        "card_b": cb.card_id,
        "strength": strength,
        "shared_assumption": shared,
        "detail": detail,
        "comparable": comparable,
    }


def _same_source_relation(ca, cb, *, lang: str, aligner: "_ThemeAligner") -> dict | None:
    """Route a different-subject pair into a 同源 relation IFF they share a
    theme.  Returns None when there is NO shared theme (PRD 决策 8: 绝不凑牵强
    同源)."""
    theme, geo, strength = _shared_theme_strength(ca, cb, aligner)
    if theme is None:
        return None
    geo_txt = f"{geo:.0f}%" if geo is not None else "—"
    detail = (f"{ca.subject} 与 {cb.subject} 表面无关,却都重押「{theme}」"
              f"(暴露几何均值 {geo_txt})。一损俱损的隐藏同源。")
    return {
        "id": _rel_id(),
        "type": REL_SAME_SOURCE,
        "card_a": ca.card_id,
        "card_b": cb.card_id,
        "strength": strength,
        "shared_assumption": f"共同主题:{theme}",
        "detail": detail,
        "comparable": True,   # 同源 compares exposures, always on one axis
    }


# ===========================================================================
# Pairwise routing
# ===========================================================================

def _route_pair(ca, cb, *, lang: str, aligner: "_ThemeAligner", conn) -> dict | None:
    """Auto-route ONE card pair to its relation (PRD 决策 5), or None if no
    relation applies."""
    same_subject = ca.subject == cb.subject
    same_series = ca.series_key == cb.series_key

    # Same series (subject+source identical) but different snapshot time → drift.
    if same_series and ca.trade_date != cb.trade_date:
        # Order older→newer by `trade_date` (ascending). We only reach here when
        # the two trade_dates differ, so they fully determine the order; the
        # created_at tiebreak below is a defensive no-op for the equal-date case
        # that this branch never sees. NOTE: relies on `trade_date` being a
        # zero-padded ISO "YYYY-MM-DD" string so a lexicographic compare equals a
        # chronological one (guaranteed by db.BetCard.__post_init__).
        older, newer = (ca, cb)
        ka = (ca.trade_date or "", ca.created_at or "")
        kb = (cb.trade_date or "", cb.created_at or "")
        if kb < ka:
            older, newer = cb, ca
        da = _read_driver(older, conn)
        db_ = _read_driver(newer, conn)
        return _drift_relation(older, newer, da, db_, lang=lang)

    # Same subject, different source → consensus / divergence / contradiction.
    if same_subject and ca.source_type != cb.source_type:
        da = _read_driver(ca, conn)
        db_ = _read_driver(cb, conn)
        return _same_subject_relation(ca, cb, da, db_, lang=lang)

    # Different subject → same-source (同源) via shared theme exposures.
    if not same_subject:
        return _same_source_relation(ca, cb, lang=lang, aligner=aligner)

    # Same subject + same source + same trade_date (duplicate-ish): nothing.
    return None


# ===========================================================================
# headline_insight selection
# ===========================================================================

def _pick_headline(relations: list[dict]) -> dict | None:
    """Top1 relation by (strength_rank × type_weight); None when there is no
    significant relation (PRD: 无显著关系 → headline=null).  We require the
    winner to be at least 'medium' OR a same-source link — a board of only weak
    consensus/drift links is not a headline-worthy Aha."""
    if not relations:
        return None

    def score(r: dict) -> float:
        return _STRENGTH_RANK.get(r["strength"], 1) * _TYPE_WEIGHT.get(r["type"], 1.0)

    best = max(relations, key=score)
    # Significance gate: weak + non-同源 → not headline-worthy.
    if best["strength"] == "weak" and best["type"] != REL_SAME_SOURCE:
        return None
    text = best["detail"]
    return {"text": text, "relation_id": best["id"]}


# ===========================================================================
# Narrative (chat mode, 1 retry, graceful fallback)
# ===========================================================================

_NARRATIVE_TEMPLATE = "prompts/cross_card_synthesizer.md"


def _load_narrative_prompt(lang: str, relations_block: str) -> str:
    """Render the narrative prompt from the editable template, with a self-
    contained inline fallback so a missing file never breaks synthesis."""
    try:
        from prompt_loader import load_prompt
        return load_prompt(_NARRATIVE_TEMPLATE, LANG=lang,
                           RELATIONS_BLOCK=relations_block)
    except Exception:
        lang_word = "中文" if lang == "zh" else "English"
        return (
            "你是 Bet Decoder 的跨卡综合 agent。下面是已发现的跨卡关系(每条带 id)。"
            f"请用 {lang_word} 写一段综合叙事,**每句话末尾用 [relation_id] 标注它"
            "依据的关系**,只能引用给定关系、不得编造。结论必须能对账回这些关系。"
            "只输出 JSON {\"narrative\": \"...\"}。\n\n关系列表:\n" + relations_block
        )


def _build_narrative(relations: list[dict], cards_by_id: dict, lang: str,
                     chat: Optional[Callable]) -> str | None:
    """Ask the chat hook for a synthesis narrative whose every sentence anchors a
    relation_id.  ``chat=None`` ⇒ no narrative (pure graph).  On a bad/garbled
    chat result, retry ONCE then fall back to None (PRD 决策 8)."""
    if chat is None or not relations:
        return None

    rel_lines = []
    for r in relations:
        ca = cards_by_id.get(r["card_a"])
        cb = cards_by_id.get(r["card_b"])
        sa = ca.subject if ca else r["card_a"]
        sb = cb.subject if cb else r["card_b"]
        rel_lines.append(
            f"- [{r['id']}] {r['type']} ({r['strength']}): {sa} ↔ {sb} | "
            f"{r['shared_assumption']} | {r['detail']}"
        )
    relations_block = "\n".join(rel_lines)
    prompt = _load_narrative_prompt(lang, relations_block)

    for _attempt in range(2):  # initial + 1 retry
        try:
            raw = chat(prompt)
            data = _parse_chat_json(raw)
            narrative = data.get("narrative")
            if isinstance(narrative, str) and narrative.strip():
                return narrative.strip()
        except Exception:
            pass
    return None  # both attempts failed → pure relations graph, no crash


# ---------------------------------------------------------------------------
# chat-result parsing (tolerant of {"content": ...} client envelope + fences)
# ---------------------------------------------------------------------------

def _parse_chat_json(raw: Any) -> dict:
    """Normalize a chat hook's return into a dict.

    Accepts: a dict already (test stubs), the real client's
    ``{"content": "...json..."}`` envelope, or a bare JSON string.  Raises on
    anything unparseable (caller treats that as a failed attempt)."""
    if isinstance(raw, dict) and "content" not in raw:
        return raw
    text = raw.get("content") if isinstance(raw, dict) else raw
    if not isinstance(text, str):
        raise ValueError("chat returned non-text content")
    try:
        from client import parse_loose_json
        return parse_loose_json(text)
    except Exception:
        return json.loads(text)


# ===========================================================================
# Public API
# ===========================================================================

def synthesize_cards(card_ids: list[str],
                     lang: str = "zh",
                     emit=None,
                     *,
                     conn=None,
                     chat: Any = _CHAT_SENTINEL,
                     use_cache: bool = True) -> dict:
    """Synthesize cross-card relations + narrative over an existing card set.

    Pure consumer: loads cards by id, NEVER mutates/creates cards.  The only
    write is the cached ``SynthesisResult`` blob in ``llm_cache``
    (category ``"synthesis"``, key = sorted card-set hash).

    Parameters
    ----------
    card_ids : list[str]
        ≥2 card ids; the cards must already be stored.  Order-insensitive.
    lang : "zh" | "en"
        Narrative language.
    emit : optional ActivityEvent callback (M5).  None ⇒ no streaming.
    conn : optional sqlite3 connection.  Required to *load* cards and to read
        DCF bands off runs; defaults to a fresh ``db.init_db("pricelens.db")``.
    chat : optional chat hook ``(prompt:str) -> dict | str``.  Omit it to use the
        real ``client.call_chat`` (chat mode); pass ``chat=None`` for a fully
        deterministic graph-only run (no LLM, no 同源 fuzzy-match, no narrative);
        or inject a stub in tests so the run costs nothing.  Any chat failure
        degrades to a pure relations graph (narrative=None), never crashes.
    use_cache : look up / store the result in ``llm_cache``.

    Returns
    -------
    dict (SynthesisResult)::

        { card_ids, generated_at, headline_insight|None, relations[], narrative|None }
    """
    owns_conn = False
    if conn is None:
        conn = db.init_db("pricelens.db")
        owns_conn = True

    # Chat-hook resolution policy:
    #   - chat is _CHAT_SENTINEL (default, caller passed nothing) → real client.
    #   - chat is None (explicit)                                  → no LLM
    #     (deterministic graph-only path; tests use this or a stub).
    #   - chat is a callable                                       → use it.
    # Resolution is deferred until we actually have ≥2 cards so the no-op /
    # insufficient paths never import client.py.
    chat_hook = chat

    key = cache_key_for(card_ids)

    # --- cache lookup -----------------------------------------------------
    if use_cache:
        cached = db.cache_get(conn, "synthesis", key)
        if cached is not None:
            _safe_emit(emit, phase="cache_hit", kind="relation",
                       text="跨卡综合缓存命中(同一卡集合)",
                       subject="synthesis", card_ids=list(card_ids),
                       payload={"cache_key": key})
            if owns_conn:
                conn.close()
            return cached

    _safe_emit(emit, phase="load", kind="decision",
               text=f"载入 {len(card_ids)} 张卡准备跨卡综合",
               subject="synthesis", card_ids=list(card_ids))

    # --- load cards (skip missing ids; dedup, preserve order) -------------
    seen: set[str] = set()
    cards: list[db.BetCard] = []
    for cid in card_ids:
        if cid in seen:
            continue
        seen.add(cid)
        c = db.get_card(conn, cid)
        if c is not None:
            cards.append(c)

    cards_by_id = {c.card_id: c for c in cards}
    resolved_ids = [c.card_id for c in cards]

    # --- guard: need ≥2 cards ---------------------------------------------
    if len(cards) < 2:
        result = _empty_result(resolved_ids or list(dict.fromkeys(card_ids)))
        _safe_emit(emit, phase="insufficient", kind="relation",
                   text="可用卡不足 2 张,无法做跨卡综合(返回空关系图谱)",
                   subject="synthesis", card_ids=resolved_ids)
        if use_cache:
            db.cache_put(conn, "synthesis", key, result)
        if owns_conn:
            conn.close()
        return result

    if len(cards) > _SOFT_CARD_CEILING:
        _safe_emit(emit, phase="soft_cap", kind="decision",
                   text=f"卡数 {len(cards)} > {_SOFT_CARD_CEILING},建议收敛卡集合(软提示)",
                   subject="synthesis", card_ids=resolved_ids)

    # Resolve the real chat hook only now (so the no-op / insufficient paths
    # never import client when not needed).  Normalize to a callable or None:
    #   sentinel → real client · None → no LLM · callable → as-is.
    if chat_hook is _CHAT_SENTINEL:
        chat_hook = _default_chat()

    # --- pairwise routing -------------------------------------------------
    # One run-scoped theme aligner: memoizes every fuzzy theme verdict and caps
    # the total fuzzy chat calls (so the O(cards²·themes²) pairing can't fan out
    # into hundreds of serial LLM calls).
    aligner = _ThemeAligner(chat_hook)
    relations: list[dict] = []
    n = len(cards)
    for i in range(n):
        for j in range(i + 1, n):
            rel = _route_pair(cards[i], cards[j], lang=lang, aligner=aligner, conn=conn)
            if rel is not None:
                relations.append(rel)
                _safe_emit(emit, phase="relation", kind="relation",
                           text=f"发现关系:{rel['type']} ({rel['strength']}) "
                                f"{cards[i].subject} ↔ {cards[j].subject}",
                           subject="synthesis", card_ids=resolved_ids, payload=rel)

    # --- headline + narrative --------------------------------------------
    headline = _pick_headline(relations)
    _safe_emit(emit, phase="headline", kind="relation",
               text=(headline["text"] if headline else "无显著跨卡关系,headline 留空"),
               subject="synthesis", card_ids=resolved_ids,
               payload=headline)

    narrative = _build_narrative(relations, cards_by_id, lang, chat_hook)

    result = {
        "card_ids": resolved_ids,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "headline_insight": headline,
        "relations": relations,
        "narrative": narrative,
    }

    # --- persist to llm_cache only (NOT bet_cards) ------------------------
    if use_cache:
        db.cache_put(conn, "synthesis", key, result)

    _safe_emit(emit, phase="done", kind="relation",
               text=f"跨卡综合完成:{len(relations)} 条关系"
                    f"{'(含 headline)' if headline else '(无 headline)'}",
               subject="synthesis", card_ids=resolved_ids,
               payload={"relation_count": len(relations)})

    if owns_conn:
        conn.close()
    return result


def _empty_result(card_ids: list[str]) -> dict:
    """A well-formed but empty SynthesisResult (no relations, no headline)."""
    return {
        "card_ids": list(card_ids),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "headline_insight": None,
        "relations": [],
        "narrative": None,
    }


# ---------------------------------------------------------------------------
# Default chat hook (real client, chat mode).  Isolated so the import only
# happens when an actual LLM call is needed.
# ---------------------------------------------------------------------------

def _default_chat() -> Callable:
    """Return a chat callable backed by the real MiroMind client (chat mode).

    Imported lazily so the deterministic / test path never touches client.py.
    Failures inside the call are handled by the callers (treated as a bad attempt
    → degrade to graph-only)."""
    from client import MODEL_MINI, call_chat

    def _chat(prompt: str) -> dict:
        return call_chat(prompt, model=MODEL_MINI)

    return _chat


if __name__ == "__main__":  # pragma: no cover - manual smoke
    import sys
    ids = sys.argv[1:]
    if len(ids) < 2:
        print("usage: python synthesizer.py <card_id> <card_id> [...]")
        raise SystemExit(1)
    res = synthesize_cards(ids, chat=None)  # chat=None → graph only, no API
    print(json.dumps(res, ensure_ascii=False, indent=2))
