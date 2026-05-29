"""Phase-4 W3 cross-card synthesis BUG-FIX verification — ZERO API/network cost.

Each block below *reproduces the original bug* (so it would FAIL against the
pre-fix synthesizer.py) and then asserts the fixed behavior.  A stub chat hook
with a call counter exercises the 同源 fuzzy-match path so we can prove the
memoization + hard call-cap without spending a single real LLM call.

Bugs covered (Phase-4 review):
  #1 [CRITICAL] non-DCF strength math degeneration
        (a) mean==0 → must be NOT comparable (not silently weak)
        (b) opposite-sign implied multiples → contradiction (not numeric gap)
  #2 [CRITICAL] same-source theme alignment: real chat O(K²T²), no cap/cache
        (a) a repeated theme pair is fuzzy-matched via chat at most ONCE
        (b) past the hard cap, no further chat calls — degrade to exact match
  #3 [SHOULD-FIX] geo-mean == 0 still padded a flimsy 同源
        all-zero exposures → no same-source relation, headline not occupied
  #4 [NIT] drift created_at tiebreak / dead subject_label (compile + behavior)

Run:  "/c/Users/Henry Ma/miniconda3/python.exe" verify_phase4_w3.py
"""
from __future__ import annotations

import db
import synthesizer as syn
from synthesizer import (
    REL_CONTRADICTION,
    REL_SAME_SOURCE,
    _ThemeAligner,
    _driver_gap_strength,
    _shared_theme_strength,
    synthesize_cards,
)

# --- counters -------------------------------------------------------------
_passed = 0
_failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global _passed, _failed
    status = "PASS" if cond else "FAIL"
    if cond:
        _passed += 1
    else:
        _failed += 1
    extra = f"  | {detail}" if detail else ""
    print(f"[{status}] {name}{extra}")


# --- counting stub chat ----------------------------------------------------

class CountingChat:
    """Deterministic stand-in for client.call_chat with a fuzzy-call counter.

    Theme-align prompts contain '主题' and we answer {"same": always_same}.
    Narrative prompts contain 'narrative' and we answer with a canned string.
    """

    def __init__(self, *, always_same: bool = True, narrative: str | None = None):
        self.calls = 0
        self.theme_calls = 0
        self.always_same = always_same
        self.narrative = narrative

    def __call__(self, prompt: str):
        self.calls += 1
        if "narrative" in prompt:
            return {"narrative": self.narrative} if self.narrative else {"x": 1}
        self.theme_calls += 1
        return {"same": self.always_same}


def fresh_conn():
    return db.init_db(":memory:")


def save_theme_card(conn, *, subject, source_type, themes, card_kind=db.SINGLE) -> str:
    card = db.BetCard(
        subject=subject, source_type=source_type, card_kind=card_kind, bet=None,
        theme_exposures=[
            db.ThemeExposure(theme=t, exposure_pct=p, contributing_tickers=[subject],
                             is_concentration_risk=(p or 0) >= 50)
            for (t, p) in themes
        ],
    )
    return db.save_card(conn, card)


# ==========================================================================
# Bug #1a — non-DCF mean==0 → NOT comparable (was: silently weak)
# ==========================================================================
print("\n=== Bug#1a: non-DCF mean==0 -> comparable=False ===")
# Two multiple-lens drivers both ≈0 → mean magnitude 0 → no usable common scale.
za = {"lens": "pe", "family": "multiple", "value": 0.0, "band": None, "comparable": True}
zb = {"lens": "pe", "family": "multiple", "value": 0.0, "band": None, "comparable": True}
strength, comparable, gap = _driver_gap_strength(za, zb)
check("Bug#1a mean==0 -> comparable=False (not silent weak)",
      comparable is False, f"strength={strength} comparable={comparable}")

# Sanity: a normal same-sign pair is still comparable (regression guard).
pa = {"lens": "pe", "family": "multiple", "value": 30.0, "band": None, "comparable": True}
_, comp_ok, _ = _driver_gap_strength(pa, {**pa, "value": 31.0})
check("Bug#1a normal positive pair still comparable", comp_ok is True)

# ==========================================================================
# Bug #1b — opposite-sign implied multiples → contradiction (was: numeric gap)
# ==========================================================================
print("\n=== Bug#1b: opposite-sign multiples -> contradiction ===")
# P/E +20 (priced profitable) vs P/E -20 (priced loss-making): sign carries the
# fundamental disagreement. |20|==|20| also makes the OLD mean-based rel_gap a
# trap (gap 40 / mean 20 = 200% but the *intent* is a stance flip).
pos = {"lens": "pe", "family": "multiple", "value": 20.0, "band": None, "comparable": True}
neg = {"lens": "pe", "family": "multiple", "value": -20.0, "band": None, "comparable": True}
s_sign, c_sign, g_sign = _driver_gap_strength(pos, neg)
check("Bug#1b opposite sign -> strong + comparable",
      s_sign == "strong" and c_sign is True, f"strength={s_sign} comparable={c_sign}")

# And end-to-end: a same-subject/diff-source pair of opposite-sign cards routes
# to a CONTRADICTION relation (strong gap → contradiction in _same_subject_relation).
class _Obj:  # minimal in-process card stub with decode_detail
    def __init__(self, subject, source_type, lens, value):
        self.subject = subject
        self.source_type = source_type
        self.card_id = f"{subject}-{source_type}"
        self.bet = value
        self.run_id = None
        self.decode_detail = {
            "primary_lens": {"lens": lens, "lens_family": "multiple",
                             "implied_value": value, "implied_label": "隐含 P/E",
                             "band": None}
        }

ca = _Obj("LOSSCO", "market", "pe", 20.0)
cb = _Obj("LOSSCO", "analyst_pt", "pe", -20.0)
da = syn._read_driver(ca, None)
db_ = syn._read_driver(cb, None)
rel = syn._same_subject_relation(ca, cb, da, db_, lang="zh")
check("Bug#1b opposite-sign same-subject pair -> CONTRADICTION relation",
      rel["type"] == REL_CONTRADICTION and rel["comparable"] is True, rel["type"])

# ==========================================================================
# Bug #2a — repeated theme pair fuzzy-matched via chat AT MOST ONCE (memoize)
# ==========================================================================
print("\n=== Bug#2a: theme-align memoized (repeat pair asks chat once) ===")
aligner = _ThemeAligner(CountingChat(always_same=True))
chat_obj = aligner._chat
# Same non-exact pair queried 5×: first asks chat, rest hit the memo.
v1 = aligner.align("光模块 / 光通信", "光互连产业链")
v2 = aligner.align("光模块 / 光通信", "光互连产业链")
v3 = aligner.align("光互连产业链", "光模块 / 光通信")   # reversed order -> same memo slot
v4 = aligner.align("光模块 / 光通信", "光互连产业链")
v5 = aligner.align("光互连产业链", "光模块 / 光通信")
check("Bug#2a repeated pair -> chat asked exactly once",
      chat_obj.theme_calls == 1, f"theme_calls={chat_obj.theme_calls}")
check("Bug#2a memoized verdict consistent (all True)",
      all([v1, v2, v3, v4, v5]))
check("Bug#2a reversed-order pair shares memo slot (order-insensitive)",
      aligner.chat_calls == 1, f"chat_calls={aligner.chat_calls}")

# ==========================================================================
# Bug #2b — hard call cap: past the cap, NO more chat calls (degrade to exact)
# ==========================================================================
print("\n=== Bug#2b: theme-align hard call cap ===")
cap_chat = CountingChat(always_same=False)   # chat says 'not same' -> never short-circuits via memo to True
aligner2 = _ThemeAligner(cap_chat, call_cap=3)
# Fire 20 DISTINCT non-exact pairs; only the first 3 may reach chat.
for i in range(20):
    aligner2.align(f"主题甲_{i}", f"主题乙_{i}")
check("Bug#2b chat calls bounded by cap (<= 3)",
      cap_chat.theme_calls <= 3, f"theme_calls={cap_chat.theme_calls} cap=3")
check("Bug#2b aligner reports calls == cap (3 distinct pairs consumed budget)",
      aligner2.chat_calls == 3, f"chat_calls={aligner2.chat_calls}")
# A NEW distinct pair after the cap must NOT add a chat call.
before = cap_chat.theme_calls
aligner2.align("封顶后新主题A", "封顶后新主题B")
check("Bug#2b post-cap distinct pair -> no new chat call",
      cap_chat.theme_calls == before, f"{before} -> {cap_chat.theme_calls}")

# End-to-end limit through synthesize_cards: many distinct-theme cards, low cap.
conn = fresh_conn()
e2e_chat = CountingChat(always_same=True)
ids = []
for i in range(6):  # 6 cards -> 15 pairs, each a distinct theme -> many fuzzy asks
    ids.append(save_theme_card(conn, subject=f"SUB{i}", source_type="market",
                               themes=[(f"独立主题_{i}", 50.0)]))
# Patch the module cap low so we can prove the ceiling without 100s of pairs.
_orig_cap = syn._THEME_ALIGN_CALL_CAP
syn._THEME_ALIGN_CALL_CAP = 4
try:
    res_e2e = synthesize_cards(ids, conn=conn, chat=e2e_chat, use_cache=False)
finally:
    syn._THEME_ALIGN_CALL_CAP = _orig_cap
check("Bug#2b synthesize fuzzy theme calls capped (<= 4)",
      e2e_chat.theme_calls <= 4, f"theme_calls={e2e_chat.theme_calls}")
check("Bug#2b synthesize still returns a well-formed result",
      isinstance(res_e2e, dict) and "relations" in res_e2e)

# ==========================================================================
# Bug #3 — geo-mean == 0 must NOT fabricate a 同源 (PRD 决策 8)
# ==========================================================================
print("\n=== Bug#3: geo==0 -> no fabricated same-source ===")
# Aligner that ALWAYS aligns themes, so only the exposure gate can stop a link.
always = _ThemeAligner(CountingChat(always_same=True))

ca0 = db.BetCard(subject="ZA", source_type="market", card_kind=db.SINGLE, bet=None,
                 theme_exposures=[db.ThemeExposure(theme="主题X", exposure_pct=0.0)])
cb0 = db.BetCard(subject="ZB", source_type="market", card_kind=db.SINGLE, bet=None,
                 theme_exposures=[db.ThemeExposure(theme="主题Y", exposure_pct=0.0)])
theme, geo, strength = _shared_theme_strength(ca0, cb0, always)
check("Bug#3 both exposures 0 -> no shared theme (theme is None)",
      theme is None, f"theme={theme} geo={geo}")

# One side None exposure -> still skipped.
caN = db.BetCard(subject="NA", source_type="market", card_kind=db.SINGLE, bet=None,
                 theme_exposures=[db.ThemeExposure(theme="主题X", exposure_pct=None)])
cbN = db.BetCard(subject="NB", source_type="market", card_kind=db.SINGLE, bet=None,
                 theme_exposures=[db.ThemeExposure(theme="主题Y", exposure_pct=40.0)])
theme2, _, _ = _shared_theme_strength(caN, cbN, always)
check("Bug#3 one None exposure -> skipped (no same-source)", theme2 is None)

# End-to-end: all-zero-exposure cards -> no same-source relation, headline empty.
conn = fresh_conn()
z1 = save_theme_card(conn, subject="ZZA", source_type="market", themes=[("主题X", 0.0)])
z2 = save_theme_card(conn, subject="ZZB", source_type="market", themes=[("主题Y", 0.0)])
# always_same chat would align the themes — only the geo gate prevents 同源.
res_zero = synthesize_cards([z1, z2], conn=conn, chat=CountingChat(always_same=True),
                            use_cache=False)
check("Bug#3 e2e geo==0 -> no same-source relation",
      not any(r["type"] == REL_SAME_SOURCE for r in res_zero["relations"]),
      str([r["type"] for r in res_zero["relations"]]))
check("Bug#3 e2e geo==0 -> headline not occupied by flimsy 同源",
      res_zero["headline_insight"] is None, str(res_zero["headline_insight"]))

# Positive control: real exposures DO still produce a same-source link.
conn = fresh_conn()
p1 = save_theme_card(conn, subject="PA", source_type="market", themes=[("AI 基础设施", 60.0)])
p2 = save_theme_card(conn, subject="PB", source_type="market", themes=[("AI 基础设施", 55.0)])
res_pos = synthesize_cards([p1, p2], conn=conn, chat=None, use_cache=False)
check("Bug#3 positive control: real exposures still yield same-source",
      any(r["type"] == REL_SAME_SOURCE for r in res_pos["relations"]))

# ==========================================================================
# Bug #4 — drift created_at tiebreak ordering + no dead subject_label
# ==========================================================================
print("\n=== Bug#4: drift ordering / dead-var removal ===")
import inspect

src = inspect.getsource(syn)
check("Bug#4 dead `subject_label` removed from source",
      "subject_label" not in src)

# Drift orders older->newer by trade_date; same-date defensive tiebreak by
# created_at is documented and harmless.  Verify older/newer ordering in detail.
conn = fresh_conn()
old_id = save_theme_card(conn, subject="DR", source_type="analyst_pt", themes=[("主题", 30.0)])
# rebuild with explicit dates via BetCard directly (theme card builder lacks dates)
od = db.BetCard(subject="DR", source_type="analyst_pt", card_kind=db.SINGLE, bet=0.25,
                trade_date="2026-05-01", created_at="2026-05-01T00:00:00Z")
nd = db.BetCard(subject="DR", source_type="analyst_pt", card_kind=db.SINGLE, bet=0.40,
                trade_date="2026-05-20", created_at="2026-05-20T00:00:00Z")
oid = db.save_card(conn, od)
nid = db.save_card(conn, nd)
# Pass NEW first so the router must reorder using trade_date.
res_drift = synthesize_cards([nid, oid], conn=conn, chat=None, use_cache=False)
drift = next((r for r in res_drift["relations"] if r["type"] == "drift"), None)
check("Bug#4 drift relation present", drift is not None)
check("Bug#4 drift orders older card_a -> newer card_b regardless of input order",
      drift is not None and drift["card_a"] == oid and drift["card_b"] == nid,
      f"card_a={drift['card_a'] if drift else None}")

# ==========================================================================
# Summary
# ==========================================================================
print(f"\n=== RESULT: {_passed} passed, {_failed} failed ===")
raise SystemExit(1 if _failed else 0)
