"""Issue #4 verification — evidence layer (Step 3), deterministic & ZERO API cost.

Naming note: file is `verify_m4_evidence.py` (the "m4" is just the internal
sequence after M1/M2/anchor) — it verifies Issue #4 (evidence hunter), NOT the
M4 frontend module.

Cost discipline (the load-bearing rule of this issue): the Deep Research hunter
is injectable.  Every check here passes a STUB hunter (returns a written-down
brief or None) — the real MiroMind client is NEVER imported or called.  Cache
hits, honest-empty, cost estimation are all exercised at $0.

Run:  "/c/Users/Henry Ma/miniconda3/python.exe" verify_m4_evidence.py
"""
from __future__ import annotations

import db
import evidence
from decoder import Fundamentals, decode_bet

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


# --- fixtures -------------------------------------------------------------

NVDA = Fundamentals(
    ticker="NVDA", current_price=180.0,
    revenue_ttm=130e9, net_income_ttm=73e9, ebitda_ttm=88e9,
    fcf_ttm=60e9, book_equity=80e9, eps_ttm=2.95,
    shares_outstanding=24.5e9, net_debt=-30e9, beta=1.7, growth_rate=0.55,
)
COST = Fundamentals(
    ticker="COST", current_price=900.0,
    revenue_ttm=255e9, net_income_ttm=7.4e9, ebitda_ttm=11e9,
    fcf_ttm=6e9, book_equity=23e9, eps_ttm=16.6,
    shares_outstanding=0.443e9, net_debt=-5e9, beta=0.8, growth_rate=0.09,
)
NOREV = Fundamentals(
    ticker="NOREV", current_price=8.0,
    revenue_ttm=None, net_income_ttm=-1e9, eps_ttm=-2.0,
    shares_outstanding=0.1e9, net_debt=0.0, beta=1.5,
)
_FIXTURES = {f.ticker: f for f in (NVDA, COST, NOREV)}


def stub_fundamentals(ticker: str) -> Fundamentals:
    f = _FIXTURES.get(ticker.upper())
    if f is None:
        raise RuntimeError(f"no fixture for {ticker}")
    return f


class StubHunter:
    """A written-down evidence brief, returned for every hunt. Records calls so
    we can prove caching (2nd lookup → zero new calls). Never the real API."""
    def __init__(self):
        self.calls = 0

    def __call__(self, ticker, assumption, **kw):
        self.calls += 1
        return {
            "assumption_id": assumption.get("id"),
            "assumption_text": assumption.get("human_text"),
            "evidence_items": [
                {
                    "direction": "support",
                    "claim": "stubbed支持证据",
                    "body_md": "stub body",
                    "sources": [{"url": "https://example.com", "title": "t",
                                 "date": "2026-05-01", "publisher": "Stub"}],
                    "scores": {"recency": 5, "source_quality": 4, "relevance": 5},
                },
                {
                    "direction": "refute",
                    "claim": "stubbed反对证据",
                    "body_md": "stub body 2",
                    "sources": [{"url": "https://example.com/2", "title": "t2",
                                 "date": "2026-04-01", "publisher": "Stub"}],
                    "scores": {"recency": 4, "source_quality": 4, "relevance": 4},
                },
            ],
            "overall_balance": "balanced",
            "evidence_count": {"support": 1, "refute": 1, "neutral": 0},
            "generated_at": "2026-05-29T00:00:00Z",
            "_meta": {"cost_usd": 3.21, "tool_call_count": 5},
        }


class EmptyHunter:
    """A hunter that finds NOTHING (returns None) — must yield honest-empty, never
    fabricated."""
    def __init__(self):
        self.calls = 0

    def __call__(self, ticker, assumption, **kw):
        self.calls += 1
        return None


class BoomHunter:
    """A hunter that raises — must NOT crash decode, must degrade to留空."""
    def __call__(self, ticker, assumption, **kw):
        raise RuntimeError("hunter network 500")


class StubEmit:
    def __init__(self):
        self.events: list[dict] = []

    def __call__(self, ev):
        self.events.append(ev)


def D(card):
    return getattr(card, "decode_detail", {}) or {}


def EV(card):
    return D(card).get("evidence") or {}


# ===========================================================================
print("=" * 72)
print("Issue #4 — evidence layer (Step 3) acceptance verification")
print("=" * 72)
evidence.reset_memory_cache()

# AC1 — every implied assumption triggers one evidence lookup; each yields a
# brief (or honest-empty). Stub hunter returns a real brief for each.
hunter = StubHunter()
card = decode_bet("market", "NVDA", "zh", fundamentals_fn=stub_fundamentals,
                  hunter=hunter)
sec = EV(card)
n_assump = sec.get("assumption_count", 0)
check("AC1 each implied assumption → one evidence brief",
      n_assump >= 1 and len(sec.get("briefs", [])) == n_assump
      and hunter.calls == n_assump
      and all(b.get("status") == "found" for b in sec["briefs"]),
      f"assumptions={n_assump} briefs={len(sec.get('briefs', []))} "
      f"hunter_calls={hunter.calls}")

# AC2 — evidence NOT found → fields left empty + annotated, NEVER fabricated.
evidence.reset_memory_cache()
eh = EmptyHunter()
card_e = decode_bet("market", "NVDA", "zh", fundamentals_fn=stub_fundamentals,
                    hunter=eh)
sec_e = EV(card_e)
briefs_e = sec_e.get("briefs", [])
all_empty_annotated = bool(briefs_e) and all(
    b.get("status") == "not_found"
    and b.get("evidence_items") == []
    and b.get("overall_balance") is None
    and b.get("_meta", {}).get("fabricated") is False
    and b.get("note")
    for b in briefs_e
)
check("AC2 evidence not found → empty + annotated, NEVER fabricated",
      all_empty_annotated and sec_e.get("found_count") == 0
      and sec_e.get("empty_count") == len(briefs_e),
      f"empty={sec_e.get('empty_count')} found={sec_e.get('found_count')} "
      f"note={briefs_e[0].get('note') if briefs_e else None}")

# AC3 — cache: 2nd hunt of same ticker+assumption → cache hit, hunter NOT called
# again (counter proves zero new calls on the second decode).
evidence.reset_memory_cache()
h = StubHunter()
conn = db.init_db(":memory:")  # real DB-backed evidence cache (category="evidence")
c1 = decode_bet("market", "NVDA", "zh", fundamentals_fn=stub_fundamentals,
                hunter=h, conn=conn)
calls_after_first = h.calls
c2 = decode_bet("market", "NVDA", "zh", fundamentals_fn=stub_fundamentals,
                hunter=h, conn=conn)
calls_after_second = h.calls
sec2 = EV(c2)
check("AC3 cache hit on 2nd decode → ZERO new hunter calls",
      calls_after_first >= 1 and calls_after_second == calls_after_first
      and sec2.get("cache_hits") == sec2.get("assumption_count")
      and sec2.get("new_hunter_calls") == 0,
      f"1st_calls={calls_after_first} 2nd_calls={calls_after_second} "
      f"cache_hits={sec2.get('cache_hits')}/{sec2.get('assumption_count')}")
# Cache also works via the in-memory fallback (no conn supplied).
evidence.reset_memory_cache()
hm = StubHunter()
_ = decode_bet("market", "COST", "zh", fundamentals_fn=stub_fundamentals, hunter=hm)
mem_first = hm.calls
cm2 = decode_bet("market", "COST", "zh", fundamentals_fn=stub_fundamentals, hunter=hm)
check("AC3b memory-cache fallback (no conn) also caches → zero new calls 2nd time",
      mem_first >= 1 and hm.calls == mem_first
      and EV(cm2).get("new_hunter_calls") == 0,
      f"1st={mem_first} 2nd_total={hm.calls} new_calls={EV(cm2).get('new_hunter_calls')}")

# AC4 — cost guard: per-decode estimate present on the card, and the portfolio
# first-decode estimator gives the HONEST magnitude.  [Phase4-W2 fix #4]: the
# old "≈ $24" headline assumed 1 hunt/ticker, but gather_evidence_for_card hunts
# every implied assumption (primary + up to 2 cross ≈ 3) — so 8 brand-new
# tickers ≈ 8 × 3 × $3.21 ≈ $77.  The estimator now reflects that real spend
# (under-counting it ~3x was the bug this fix closes).
est = sec.get("cost", {}).get("estimated_first_decode_usd")
port_est = evidence.estimate_portfolio_first_decode_cost(8)
check("AC4 per-decode cost estimate attached to card",
      est is not None and est > 0,
      f"estimated_first_decode_usd=${est}")
check("AC4 '8 新票组合首解 ≈ $77 量级' estimator (honest 3 hunts/ticker)",
      70 <= port_est["estimated_cost_usd"] <= 85
      and port_est["n_evidence_calls"] == 24
      and port_est["assumptions_per_ticker"] == 3,
      f"{port_est['human']} → ${port_est['estimated_cost_usd']}")
# The per-card estimate must match the card's REAL hunt count (no ~3x drift):
# estimate built from the decoded card == hunter calls actually made.
_card_assumptions = evidence.assumptions_per_card(card)
check("AC4c per-card estimate matches the real hunt count (no ~3x under-count)",
      _card_assumptions == sec.get("assumption_count")
      and abs(est - _card_assumptions * evidence.COST_PER_EVIDENCE_MINI) < 1e-9,
      f"assumptions_per_card={_card_assumptions} "
      f"== assumption_count={sec.get('assumption_count')}; est=${est}")
# single-assumption cost function sanity
check("AC4b estimate_evidence_cost linear in assumptions",
      abs(evidence.estimate_evidence_cost(1) - evidence.COST_PER_EVIDENCE_MINI) < 1e-9
      and abs(evidence.estimate_evidence_cost(3)
              - 3 * evidence.COST_PER_EVIDENCE_MINI) < 1e-9,
      f"1→${evidence.estimate_evidence_cost(1):.2f} 3→${evidence.estimate_evidence_cost(3):.2f}")

# AC5 — Step 3 is NON-SKIPPABLE: there is no flag/kwarg on decode_bet that turns
# it off, and an evidence section is always attached to a decoded single card.
import inspect
sig = inspect.signature(decode_bet)
skip_flags = [p for p in sig.parameters
              if "skip" in p.lower() or "no_evidence" in p.lower()
              or "evidence" in p.lower() and p != "hunter"]
check("AC5 decode_bet has NO skip/disable-evidence flag",
      not skip_flags and "evidence" in D(card),
      f"params={list(sig.parameters)} → no skip flag; evidence attached={'evidence' in D(card)}")

# AC5b — emit fires evidence-kind ActivityEvents (M5 contract).
evidence.reset_memory_cache()
spy = StubEmit()
_ = decode_bet("market", "NVDA", "zh", emit=spy, fundamentals_fn=stub_fundamentals,
               hunter=StubHunter())
ev_events = [e for e in spy.events if e.get("kind") == "evidence"]
check("AC5b emit fires evidence-kind events during Step 3",
      len(ev_events) >= 1
      and all(e.get("phase") == "evidence" for e in ev_events),
      f"evidence events={len(ev_events)} phases={[e['phase'] for e in ev_events]}")

# AC6 — boundary: empty assumption list / source missing → empty evidence, NOT
# an error.  (a) insufficient card (no revenue) still has an evidence section,
# empty.  (b) empty portfolio → no crash.
norev = decode_bet("market", "NOREV", "zh", fundamentals_fn=stub_fundamentals,
                   hunter=StubHunter())
check("AC6 insufficient card → empty evidence section (no error, not skipped)",
      D(norev).get("status") == "insufficient"
      and EV(norev).get("assumption_count") == 0
      and EV(norev).get("briefs") == [],
      f"status={D(norev).get('status')} assumptions={EV(norev).get('assumption_count')}")
# gather_evidence_for_card on a hand-built insufficient card never raises.
try:
    empty_sec = evidence.gather_evidence_for_card(norev, hunter=StubHunter())
    boundary_ok = empty_sec.get("assumption_count") == 0
except Exception as exc:
    boundary_ok = False
    empty_sec = {"err": str(exc)}
check("AC6b gather_evidence_for_card on insufficient card never raises",
      boundary_ok, f"section={empty_sec}")

# AC7 — cost discipline: with hunter=None AND no API key, Step 3 does NOT touch
# the network — it honestly leaves evidence empty (zero $). Proves the default
# path is safe even though it nominally "uses the real client".
import os
had_key = bool(os.environ.get("MIROMIND_API_KEY"))
evidence.reset_memory_cache()
card_default = decode_bet("market", "NVDA", "zh",
                          fundamentals_fn=stub_fundamentals)  # hunter=None
sec_d = EV(card_default)
check("AC7 cost discipline — default (hunter=None, no key) → honest-empty, no API",
      (not had_key) and sec_d.get("assumption_count") >= 1
      and sec_d.get("found_count") == 0
      and all(b.get("status") == "not_found" for b in sec_d.get("briefs", [])),
      f"no_key={not had_key} found={sec_d.get('found_count')} "
      f"empty={sec_d.get('empty_count')}")

# AC8 — hunter that RAISES must not crash decode; degrades to honest-empty.
evidence.reset_memory_cache()
card_boom = decode_bet("market", "NVDA", "zh", fundamentals_fn=stub_fundamentals,
                       hunter=BoomHunter())
check("AC8 raising hunter never crashes decode → honest-empty briefs",
      isinstance(card_boom, db.BetCard)
      and all(b.get("status") == "not_found"
              for b in EV(card_boom).get("briefs", [])),
      f"briefs={len(EV(card_boom).get('briefs', []))} all empty")

# AC9 — anchor-mode card (narrative components) also gets evidence per component.
NVDA_AI = Fundamentals(
    ticker="NVDA", current_price=180.0,
    revenue_ttm=130e9, net_income_ttm=73e9, ebitda_ttm=88e9,
    fcf_ttm=60e9, book_equity=80e9, eps_ttm=2.95,
    shares_outstanding=24.5e9, net_debt=-30e9, beta=1.7, growth_rate=0.55,
    industry="Technology / Semiconductors — AI chip / GPU accelerator",
)
evidence.reset_memory_cache()
ha = StubHunter()
ai_card = decode_bet("market", "NVDA", "zh",
                     fundamentals_fn=lambda t: NVDA_AI, hunter=ha)
ai_sec = EV(ai_card)
check("AC9 anchor-mode card hunts evidence for narrative/option components too",
      D(ai_card).get("mode") == "anchor_primary"
      and ai_sec.get("assumption_count") >= 1
      and ha.calls == ai_sec.get("assumption_count"),
      f"mode={D(ai_card).get('mode')} assumptions={ai_sec.get('assumption_count')} "
      f"hunter_calls={ha.calls}")

# ===========================================================================
print("=" * 72)
print(f"RESULT: {_passed} passed, {_failed} failed")
print("=" * 72)
raise SystemExit(1 if _failed else 0)
