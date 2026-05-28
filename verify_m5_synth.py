"""M3 cross-card synthesis verification — deterministic, ZERO API/network cost.

Covers every Issue #5 acceptance criterion with a stub chat hook + hand-built
BetCards (theme_exposures + DCF bands via runs).  No MiroMind API, no yfinance.
Prints one PASS/FAIL per check.

(Named `verify_m5_synth` only to avoid clashing with the M5 *activity-stream*
verifier; this file verifies Module 3 cross-card synthesis.)

Run:  "/c/Users/Henry Ma/miniconda3/python.exe" verify_m5_synth.py
"""
from __future__ import annotations

import db
import synthesizer as syn
from synthesizer import (
    REL_CONSENSUS,
    REL_CONTRADICTION,
    REL_DIVERGENCE,
    REL_DRIFT,
    REL_SAME_SOURCE,
    cache_key_for,
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


# --- stub chat hook -------------------------------------------------------
# A deterministic stand-in for client.call_chat.  Tracks call count and returns
# canned JSON so the 同源 fuzzy-match + narrative paths run with zero API cost.

class StubChat:
    def __init__(self, *, themes_same: bool = True, narrative: str | None = "综合叙事 [rel]",
                 raise_on: str | None = None):
        self.calls = 0
        self.themes_same = themes_same
        self.narrative = narrative
        self.raise_on = raise_on  # "all" -> every call raises (chat-broken test)

    def __call__(self, prompt: str):
        self.calls += 1
        if self.raise_on == "all":
            raise RuntimeError("stub chat forced failure")
        if "narrative" in prompt:
            if self.narrative is None:
                return {"garbage": "no narrative key"}  # bad result -> retry/fallback
            return {"narrative": self.narrative}
        # theme fuzzy-match prompt
        return {"same": self.themes_same}


def no_chat_marker():
    """A chat that must never be called (asserts deterministic path)."""
    def _c(prompt):  # pragma: no cover - should not run
        raise AssertionError("chat hook was called on a path that must be LLM-free")
    return _c


# --- card builders (in-memory DB) -----------------------------------------

def fresh_conn():
    return db.init_db(":memory:")


def _make_run_with_band(conn, ticker: str, p25, p50, p75, sr=0.6) -> int:
    """Insert a minimal run + one rdcf_intervals(revenue_cagr_5y) band; return run_id."""
    cur = conn.execute(
        "INSERT INTO runs (ticker, generated_at, mode, current_price) "
        "VALUES (?, ?, ?, ?)",
        (ticker, "2026-05-29T00:00:00Z", "standard", 100.0),
    )
    run_id = cur.lastrowid
    conn.execute(
        "INSERT INTO rdcf_intervals (run_id, variable, p25, p50, p75, success_rate) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (run_id, "revenue_cagr_5y", p25, p50, p75, sr),
    )
    conn.commit()
    return run_id


def save_dcf_card(conn, *, subject, source_type, bet, run_id,
                  trade_date=None, created_at=None) -> str:
    card = db.BetCard(
        subject=subject, source_type=source_type, card_kind=db.SINGLE,
        bet=bet, run_id=run_id, trade_date=trade_date, created_at=created_at,
    )
    return db.save_card(conn, card)


def save_theme_card(conn, *, subject, source_type, themes, card_kind=db.SINGLE) -> str:
    card = db.BetCard(
        subject=subject, source_type=source_type, card_kind=card_kind,
        bet=None,
        theme_exposures=[
            db.ThemeExposure(theme=t, exposure_pct=p,
                             contributing_tickers=[subject],
                             is_concentration_risk=(p or 0) >= 50)
            for (t, p) in themes
        ],
    )
    return db.save_card(conn, card)


# ==========================================================================
# AC 1 — signature + only takes card_ids + does NOT self-store cards
# ==========================================================================
print("\n=== AC1: signature / pure consumer ===")
conn = fresh_conn()
r1 = _make_run_with_band(conn, "AAA", 0.10, 0.15, 0.20)
r2 = _make_run_with_band(conn, "AAA", 0.10, 0.16, 0.20)
id_a = save_dcf_card(conn, subject="AAA", source_type="market", bet=0.15, run_id=r1)
id_b = save_dcf_card(conn, subject="AAA", source_type="analyst_pt", bet=0.16, run_id=r2)
cards_before = len(db.list_cards(conn))
res = synthesize_cards([id_a, id_b], lang="zh", conn=conn, chat=None)
cards_after = len(db.list_cards(conn))
check("AC1 returns SynthesisResult dict", isinstance(res, dict))
check("AC1 does NOT create/store cards (count unchanged)",
      cards_before == cards_after, f"{cards_before} -> {cards_after}")
check("AC1 accepts card_ids only (ran without card objects)", "relations" in res)

# ==========================================================================
# AC 2 — SynthesisResult structure
# ==========================================================================
print("\n=== AC2: SynthesisResult structure ===")
keys_ok = set(res.keys()) == {"card_ids", "generated_at", "headline_insight",
                              "relations", "narrative"}
check("AC2 top-level keys exact", keys_ok, str(sorted(res.keys())))
rel_struct_ok = True
for r in res["relations"]:
    if set(r.keys()) != {"id", "type", "card_a", "card_b", "strength",
                         "shared_assumption", "detail", "comparable"}:
        rel_struct_ok = False
check("AC2 relation object shape", rel_struct_ok)
check("AC2 headline_insight is {text,relation_id}|None",
      res["headline_insight"] is None or
      set(res["headline_insight"].keys()) == {"text", "relation_id"})
check("AC2 narrative is str|None (chat=None -> None)", res["narrative"] is None)

# ==========================================================================
# AC 3 — five relations auto-routed by card pairing
# ==========================================================================
print("\n=== AC3: auto-routing of relation types ===")

# (a) same subject, diff source -> consensus / divergence / contradiction
conn = fresh_conn()
rA = _make_run_with_band(conn, "MMM", 0.20, 0.25, 0.30)  # band width 0.10
rB = _make_run_with_band(conn, "MMM", 0.20, 0.26, 0.30)
m_mkt = save_dcf_card(conn, subject="MMM", source_type="market", bet=0.25, run_id=rA)
m_pt = save_dcf_card(conn, subject="MMM", source_type="analyst_pt", bet=0.26, run_id=rB)
res_same_subj = synthesize_cards([m_mkt, m_pt], conn=conn, chat=None)
types_ss = {r["type"] for r in res_same_subj["relations"]}
check("AC3a same-subject/diff-source routes to consensus|divergence|contradiction",
      types_ss <= {REL_CONSENSUS, REL_DIVERGENCE, REL_CONTRADICTION} and len(types_ss) == 1,
      str(types_ss))

# (b) diff subject, shared theme -> same-source
conn = fresh_conn()
t1 = save_theme_card(conn, subject="NVDA", source_type="market",
                     themes=[("AI 基础设施", 60.0)])
t2 = save_theme_card(conn, subject="MSFT", source_type="market",
                     themes=[("AI 基础设施", 55.0)])
res_ss = synthesize_cards([t1, t2], conn=conn, chat=None)
check("AC3b diff-subject/shared-theme routes to same-source",
      any(r["type"] == REL_SAME_SOURCE for r in res_ss["relations"]),
      str([r["type"] for r in res_ss["relations"]]))

# (c) same series, diff time -> drift
conn = fresh_conn()
rd1 = _make_run_with_band(conn, "TSLA", 0.20, 0.25, 0.30)
rd2 = _make_run_with_band(conn, "TSLA", 0.20, 0.25, 0.30)
# Same series_key (subject+source), different trade_date. Use analyst_pt to avoid
# the market daily-dedup collapsing the two snapshots into one card.
d_old = save_dcf_card(conn, subject="TSLA", source_type="analyst_pt", bet=0.25,
                      run_id=rd1, trade_date="2026-05-20",
                      created_at="2026-05-20T00:00:00Z")
d_new = save_dcf_card(conn, subject="TSLA", source_type="analyst_pt", bet=0.40,
                      run_id=rd2, trade_date="2026-05-29",
                      created_at="2026-05-29T00:00:00Z")
res_drift = synthesize_cards([d_old, d_new], conn=conn, chat=None)
check("AC3c same-series/diff-time routes to drift",
      any(r["type"] == REL_DRIFT for r in res_drift["relations"]),
      str([r["type"] for r in res_drift["relations"]]))

# ==========================================================================
# AC 4 — strength only strong/medium/weak; DCF band as ruler; non-DCF threshold
# ==========================================================================
print("\n=== AC4: strength buckets + band ruler ===")
all_strengths = set()
for rr in (res_same_subj, res_ss, res_drift):
    for r in rr["relations"]:
        all_strengths.add(r["strength"])
check("AC4 strengths only in {strong,medium,weak}",
      all_strengths <= {"strong", "medium", "weak"}, str(all_strengths))

# Band-ruler unit checks (direct, deterministic). band width = 0.10.
da = {"lens": "dcf", "family": "dcf", "value": 0.25,
      "band": {"p25": 0.20, "p75": 0.30}, "comparable": True}
# gap 0.02 < 0.5*0.10=0.05 -> weak
s_weak, c1, _ = syn._driver_gap_strength(da, {**da, "value": 0.27})
# gap 0.08 in (0.05, 0.10] -> medium
s_med, c2, _ = syn._driver_gap_strength(da, {**da, "value": 0.33})
# gap 0.15 > 0.10 -> strong
s_str, c3, _ = syn._driver_gap_strength(da, {**da, "value": 0.40})
check("AC4 DCF gap<half-band -> weak", s_weak == "weak", s_weak)
check("AC4 DCF gap~1band -> medium", s_med == "medium", s_med)
check("AC4 DCF gap>1band -> strong", s_str == "strong", s_str)

# Non-DCF threshold path (P/E): relative gap.
pe_a = {"lens": "pe", "family": "multiple", "value": 30.0, "band": None, "comparable": True}
s_pe_weak, _, _ = syn._driver_gap_strength(pe_a, {**pe_a, "value": 31.0})   # ~3% < 10%
s_pe_strong, _, _ = syn._driver_gap_strength(pe_a, {**pe_a, "value": 60.0})  # ~67% > 30%
check("AC4 non-DCF small rel-gap -> weak", s_pe_weak == "weak", s_pe_weak)
check("AC4 non-DCF large rel-gap -> strong", s_pe_strong == "strong", s_pe_strong)

# ==========================================================================
# AC 5 — same-source uses geometric mean; theme align via injectable chat stub
# ==========================================================================
print("\n=== AC5: geometric mean + fuzzy theme match ===")
# Build two cards whose theme labels DIFFER textually so exact-match fails and
# the chat fuzzy-match decides. exposures 80 & 20 -> geo = sqrt(1600)=40 -> medium.
conn = fresh_conn()
g1 = save_theme_card(conn, subject="AVGO", source_type="market",
                     themes=[("光模块 / 光通信", 80.0)])
g2 = save_theme_card(conn, subject="CRDO", source_type="market",
                     themes=[("光互连产业链", 20.0)])
stub_same = StubChat(themes_same=True, narrative=None)  # narrative not needed here
res_geo = synthesize_cards([g1, g2], conn=conn, chat=stub_same)
ss_rel = next((r for r in res_geo["relations"] if r["type"] == REL_SAME_SOURCE), None)
check("AC5 fuzzy theme-match via chat stub finds same-source",
      ss_rel is not None and stub_same.calls >= 1,
      f"chat_calls={stub_same.calls}")
check("AC5 geo-mean sqrt(80*20)=40 -> medium strength",
      ss_rel is not None and ss_rel["strength"] == "medium",
      ss_rel["strength"] if ss_rel else "none")
# chat says NOT same -> no same-source relation (honest).
stub_diff = StubChat(themes_same=False, narrative=None)
res_nodiff = synthesize_cards([g1, g2], conn=conn, chat=stub_diff, use_cache=False)
check("AC5 chat 'not same' -> no forced same-source",
      not any(r["type"] == REL_SAME_SOURCE for r in res_nodiff["relations"]))

# ==========================================================================
# AC 6 — two same-subject diff-source cards with BIG gap -> divergence + strength
# ==========================================================================
print("\n=== AC6: same-subject big-gap -> divergence/contradiction ===")
conn = fresh_conn()
rbig1 = _make_run_with_band(conn, "BIGG", 0.20, 0.25, 0.30)  # band 0.10
rbig2 = _make_run_with_band(conn, "BIGG", 0.20, 0.25, 0.30)
b_mkt = save_dcf_card(conn, subject="BIGG", source_type="market", bet=0.22, run_id=rbig1)
b_pt = save_dcf_card(conn, subject="BIGG", source_type="analyst_pt", bet=0.45, run_id=rbig2)
res_big = synthesize_cards([b_mkt, b_pt], conn=conn, chat=None)
big_rel = res_big["relations"][0] if res_big["relations"] else None
# gap 0.23 >> band 0.10 -> strong -> contradiction
check("AC6 big gap -> divergence-family relation",
      big_rel is not None and big_rel["type"] in (REL_DIVERGENCE, REL_CONTRADICTION),
      big_rel["type"] if big_rel else "none")
check("AC6 big gap -> strong strength",
      big_rel is not None and big_rel["strength"] == "strong",
      big_rel["strength"] if big_rel else "none")

# ==========================================================================
# AC 7 — diff-subject both heavy on same theme -> same-source -> headline
# ==========================================================================
print("\n=== AC7: same-source selected as headline ===")
conn = fresh_conn()
h1 = save_theme_card(conn, subject="NVDA", source_type="market",
                     themes=[("AI 基础设施", 70.0)])
h2 = save_theme_card(conn, subject="AMD", source_type="market",
                     themes=[("AI 基础设施", 65.0)])
res_head = synthesize_cards([h1, h2], conn=conn, chat=None)
hl = res_head["headline_insight"]
hl_rel = next((r for r in res_head["relations"]
               if hl and r["id"] == hl["relation_id"]), None)
check("AC7 headline present for strong same-source",
      hl is not None, str(hl))
check("AC7 headline anchors the same-source relation",
      hl_rel is not None and hl_rel["type"] == REL_SAME_SOURCE,
      hl_rel["type"] if hl_rel else "none")

# ==========================================================================
# AC 8 — cache: same card-set hash hits 2nd time; add/remove -> new hash -> rerun
# ==========================================================================
print("\n=== AC8: card-set hash caching ===")
conn = fresh_conn()
ca = save_theme_card(conn, subject="NVDA", source_type="market", themes=[("AI", 60.0)])
cb = save_theme_card(conn, subject="MSFT", source_type="market", themes=[("AI", 55.0)])
cc = save_theme_card(conn, subject="GOOG", source_type="market", themes=[("AI", 50.0)])
k_ab = cache_key_for([ca, cb])
k_ab_rev = cache_key_for([cb, ca])
check("AC8 hash order-insensitive", k_ab == k_ab_rev, f"{k_ab} vs {k_ab_rev}")
res_first = synthesize_cards([ca, cb], conn=conn, chat=None)
gen_first = res_first["generated_at"]
# 2nd call with same set: must return the cached object (same generated_at).
res_second = synthesize_cards([ca, cb], conn=conn, chat=None)
check("AC8 2nd call same set -> cache hit (identical generated_at)",
      res_second["generated_at"] == gen_first)
# add a card -> new hash -> rerun (cache miss).
res_added = synthesize_cards([ca, cb, cc], conn=conn, chat=None)
check("AC8 add card -> new hash -> rerun (different cache key)",
      cache_key_for([ca, cb, cc]) != k_ab)
check("AC8 added-set result includes all 3", len(res_added["card_ids"]) == 3)

# ==========================================================================
# AC 9 — no significant relation -> headline=null, never fabricate same-source
# ==========================================================================
print("\n=== AC9: no relation -> headline null, no fabricated 同源 ===")
conn = fresh_conn()
# Two diff-subject cards with NO shared theme (and chat would say not-same).
n1 = save_theme_card(conn, subject="KO", source_type="market", themes=[("饮料", 40.0)])
n2 = save_theme_card(conn, subject="XOM", source_type="market", themes=[("石油", 40.0)])
res_none = synthesize_cards([n1, n2], conn=conn, chat=None)  # chat=None -> exact-only
check("AC9 unrelated cards -> no same-source relation",
      not any(r["type"] == REL_SAME_SOURCE for r in res_none["relations"]))
check("AC9 no significant relation -> headline=null",
      res_none["headline_insight"] is None, str(res_none["headline_insight"]))

# ==========================================================================
# AC 10 — non-comparable driver (DCF vs anchor) -> comparable=false + qualitative
# ==========================================================================
print("\n=== AC10: non-comparable driver -> comparable=false ===")
# Build two same-subject diff-source cards: one DCF (run band), one anchor-mode.
# The anchor card carries decode_detail with mode=anchor_primary so _read_driver
# marks it non-comparable.
conn = fresh_conn()
rc = _make_run_with_band(conn, "ANCH", 0.20, 0.25, 0.30)
dcf_card = db.BetCard(subject="ANCH", source_type="market", card_kind=db.SINGLE,
                      bet=0.25, run_id=rc)
dcf_id = db.save_card(conn, dcf_card)
anchor_card = db.BetCard(subject="ANCH", source_type="analyst_pt",
                         card_kind=db.SINGLE, bet=120.0)
anchor_id = db.save_card(conn, anchor_card)
# Direct read-driver unit check on an in-process anchor card (decode_detail set).
anchor_obj = db.BetCard(subject="ANCH", source_type="analyst_pt",
                        card_kind=db.SINGLE, bet=120.0)
anchor_obj.decode_detail = {"mode": "anchor_primary", "anchor_mode": {}, "r2_band": None}
drv = syn._read_driver(anchor_obj, conn)
check("AC10 anchor-mode card -> driver comparable=false",
      drv["comparable"] is False, str(drv["comparable"]))
da_dcf = {"lens": "dcf", "family": "dcf", "value": 0.25,
          "band": {"p25": 0.20, "p75": 0.30}, "comparable": True}
strength, comparable, gap = syn._driver_gap_strength(da_dcf, drv)
check("AC10 DCF-vs-anchor compare -> comparable=false in relation",
      comparable is False, f"comparable={comparable}")

# ==========================================================================
# AC 11 — chat returns bad result -> retry 1 -> fall back to graph (narrative=null)
# ==========================================================================
print("\n=== AC11: chat broken -> graph-only, no crash ===")
conn = fresh_conn()
b1 = save_theme_card(conn, subject="NVDA", source_type="market", themes=[("AI", 60.0)])
b2 = save_theme_card(conn, subject="AMD", source_type="market", themes=[("AI", 55.0)])
# Stub whose narrative call always returns garbage (no "narrative" key) -> both
# attempts fail -> narrative None. (themes_same=True so the relation still forms.)
bad_narr = StubChat(themes_same=True, narrative=None)
res_bad = synthesize_cards([b1, b2], conn=conn, chat=bad_narr, use_cache=False)
check("AC11 bad-narrative chat -> narrative falls back to None",
      res_bad["narrative"] is None)
check("AC11 relations graph still intact despite bad narrative",
      len(res_bad["relations"]) >= 1)
# Stub that raises on EVERY call -> must not crash; relations from exact-match only.
raiser = StubChat(raise_on="all")
res_raise = synthesize_cards([b1, b2], conn=conn, chat=raiser, use_cache=False)
check("AC11 chat raising on every call -> no crash, dict returned",
      isinstance(res_raise, dict) and res_raise["narrative"] is None)

# Good narrative path (stub returns proper JSON) -> narrative populated.
good = StubChat(themes_same=True, narrative="NVDA 与 AMD 同押 AI 基础设施。[rel_x]")
res_good = synthesize_cards([b1, b2], conn=conn, chat=good, use_cache=False)
check("AC11 good chat -> narrative populated",
      isinstance(res_good["narrative"], str) and res_good["narrative"].strip() != "")

# ==========================================================================
# AC 12 — <2 cards -> empty result (no crash)
# ==========================================================================
print("\n=== AC12: <2 cards guard ===")
conn = fresh_conn()
solo = save_theme_card(conn, subject="NVDA", source_type="market", themes=[("AI", 60.0)])
res_solo = synthesize_cards([solo], conn=conn, chat=None)
check("AC12 single card -> empty relations + null headline",
      res_solo["relations"] == [] and res_solo["headline_insight"] is None)
res_zero = synthesize_cards([], conn=conn, chat=None)
check("AC12 empty card_ids -> empty result, no crash",
      res_zero["relations"] == [] and isinstance(res_zero, dict))
# Missing ids (not stored) are skipped; one valid + one missing < 2 -> empty.
res_missing = synthesize_cards([solo, "deadbeef"], conn=conn, chat=None)
check("AC12 missing ids skipped -> <2 valid -> empty",
      res_missing["relations"] == [])

# ==========================================================================
# AC 13 — deterministic path never touches chat (cost discipline)
# ==========================================================================
print("\n=== AC13: chat=None path is LLM-free ===")
conn = fresh_conn()
z1 = save_theme_card(conn, subject="NVDA", source_type="market", themes=[("AI 基础设施", 60.0)])
z2 = save_theme_card(conn, subject="AMD", source_type="market", themes=[("AI 基础设施", 55.0)])
# exact-match theme -> no chat needed; chat=None means even narrative is skipped.
res_free = synthesize_cards([z1, z2], conn=conn, chat=None)
check("AC13 chat=None still finds exact-theme same-source (no LLM)",
      any(r["type"] == REL_SAME_SOURCE for r in res_free["relations"]))
check("AC13 chat=None -> narrative None (no LLM call)", res_free["narrative"] is None)

# ==========================================================================
# Summary
# ==========================================================================
print(f"\n=== RESULT: {_passed} passed, {_failed} failed ===")
raise SystemExit(1 if _failed else 0)
