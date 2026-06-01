"""Phase 4 — W2 decoder/evidence bug-fix verification (deterministic, $0 API).

Each check reproduces the *original* Phase-4-review bug and proves the fix. The
real MiroMind client is NEVER imported or called: every decode injects a stub
hunter (or runs with the key blocked), so this suite costs nothing.

Covers:
  #1  evidence.make_cache_key is process-STABLE (sha1, not hash()) — verified
      ACROSS TWO SUBPROCESSES so PYTHONHASHSEED randomization can't pass it.
  #2  anchor mode keeps the DCF baseline even when the point reverse-solve has
      no root → an UNDERVALUED stock is NOT mis-judged as 100% narrative.
  #3  is_ai_composite no longer mis-gates a storage REIT / 养老 facility on bare
      "memory"/"storage".
  #4  cost estimate reflects the REAL per-ticker hunt count (1 primary + cross),
      and the model rate matches the model _default_hunter calls.
  #5  the portfolio aggregate card carries a shape-consistent `evidence` node.
  #6  EV/EBITDA with negative enterprise value → None (no negative multiple).
  #7  nits: dead `replace` import gone; unknown source_type not masqueraded as
      market; comp_sum defensive.

Run:  MIROMIND_API_KEY= "/c/Users/Henry Ma/miniconda3/python.exe" verify_phase4_w2.py
(Set MIROMIND_API_KEY to empty so the hunter=None default stays honest-empty.)
"""
from __future__ import annotations

import os
import subprocess
import sys

import db
import decoder
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


def D(card):
    return getattr(card, "decode_detail", {}) or {}


def EV(card):
    return D(card).get("evidence") or {}


class StubHunter:
    """Returns a written-down brief for every hunt; counts calls. Never the API."""
    def __init__(self):
        self.calls = 0

    def __call__(self, ticker, assumption, **kw):
        self.calls += 1
        return {
            "assumption_id": assumption.get("id"),
            "assumption_text": assumption.get("human_text"),
            "evidence_items": [
                {"direction": "support", "claim": "stub", "body_md": "b",
                 "sources": [{"url": "https://e.com", "title": "t",
                              "date": "2026-05-01", "publisher": "S"}],
                 "scores": {"recency": 5, "source_quality": 4, "relevance": 5}},
            ],
            "overall_balance": "support",
            "evidence_count": {"support": 1, "refute": 0, "neutral": 0},
            "generated_at": "2026-05-29T00:00:00Z",
            "_meta": {"cost_usd": 3.21, "tool_call_count": 4},
        }


print("=" * 72)
print("Phase 4 — W2 decoder/evidence bug-fix verification")
print("=" * 72)


# ===========================================================================
# #1 — evidence cache key is process-STABLE (sha1, not hash()).
# ===========================================================================
# The bug: make_cache_key used abs(hash(text)), which Python salts per process
# (PYTHONHASHSEED). A key built during the demo pre-run never matched the same
# (ticker, assumption) at demo time → cache永不命中 → re-burns real money on every
# restart. The only faithful test runs the keygen in TWO SEPARATE PROCESSES and
# asserts the keys are identical. (An in-process call can't catch this — the
# salt is fixed for the life of a process.)
_KEYGEN_SNIPPET = (
    "import evidence;"
    "a={'id':'NVDA_dcf','metric':'implied_revenue_cagr_5y',"
    "'human_text':'隐含 5 年营收 CAGR 必须显著高于历史'};"
    "print(evidence.make_cache_key('NVDA', a, 'zh'))"
)


def _keygen_in_subprocess(hashseed: str) -> str:
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = hashseed       # force a DIFFERENT hash salt each run
    env["MIROMIND_API_KEY"] = ""           # never touch the network on import
    out = subprocess.check_output(
        [sys.executable, "-c", _KEYGEN_SNIPPET],
        env=env, cwd=os.path.dirname(os.path.abspath(__file__)),
        stderr=subprocess.STDOUT,
    )
    return out.decode("utf-8", "replace").strip().splitlines()[-1]


key_a = _keygen_in_subprocess("0")        # hash randomization disabled
key_b = _keygen_in_subprocess("12345")    # a wholly different hash salt
key_c = _keygen_in_subprocess("99999")    # and another
check("#1 cache key STABLE across processes (sha1, not hash()) — 3 hashseeds agree",
      key_a == key_b == key_c and key_a and "|" in key_a,
      f"hashseed0={key_a} | hashseed12345={key_b} | hashseed99999={key_c}")
# Sanity: the in-process key matches the subprocess key too (same algorithm).
_inproc = evidence.make_cache_key(
    "NVDA",
    {"id": "NVDA_dcf", "metric": "implied_revenue_cagr_5y",
     "human_text": "隐含 5 年营收 CAGR 必须显著高于历史"}, "zh")
check("#1b in-process key == subprocess key (single sha1 source of truth)",
      _inproc == key_a, f"in-proc={_inproc}")


# ===========================================================================
# #2 — anchor mode keeps the DCF baseline; undervalued stock ≠ 100% narrative.
# ===========================================================================
# The bug: _lens_dcf returned None entirely when the *point* reverse-solve had
# no root, discarding the already-computed baseline_dcf_price. _base_business_
# value then fell to base=0 → the whole price looked like narrative (100%
# exposure), even for an UNDERVALUED stock. The fix decouples baseline from
# point-solve: baseline always survives.
#
# Construct an UNDERVALUED AI-composite fixture whose consensus DCF baseline
# (≈$591) sits far ABOVE the anchor ($50) — and whose $50-anchor reverse-solve
# has NO root (point_solved=False), exactly the scenario the bug mishandled.
# shares_outstanding=1.71e9 is tuned so dcf_equity_value_per_share(consensus)
# ≈ $591 (the review's "consensus DCF=$591, anchor=$50" input). It routes through
# anchor mode (AI-composite gate), exercising _base_business_value directly.
UNDERVALUED = Fundamentals(
    ticker="UNDR", current_price=50.0,
    revenue_ttm=130e9, net_income_ttm=73e9, ebitda_ttm=88e9,
    fcf_ttm=60e9, book_equity=80e9, eps_ttm=2.95,
    shares_outstanding=1.71e9, net_debt=-30e9, beta=1.7, growth_rate=0.55,
    industry="Technology / Semiconductors — AI chip / GPU accelerator",
)
# First, confirm the consensus DCF baseline (≈$591) survives even though the
# $50-anchor point reverse-solve has NO root — the exact bug scenario.
import reverse_dcf  # noqa: E402
_dcf_view = decoder._run_lens("dcf", 50.0, UNDERVALUED)
_baseline = (_dcf_view or {}).get("baseline_dcf_price")
check("#2a DCF baseline (≈$591) survives a no-point-solve (baseline >> $50 anchor)",
      _dcf_view is not None and _baseline is not None and _baseline > 50.0
      and 560 <= _baseline <= 620
      and _dcf_view.get("point_solved") is False,
      f"baseline_dcf_price=${_baseline:.2f} point_solved={(_dcf_view or {}).get('point_solved')} "
      f"vs anchor=$50.00" if _baseline is not None else "baseline LOST (bug)")
und_card = decode_bet("market", "UNDR", "zh",
                      fundamentals_fn=lambda t: UNDERVALUED,
                      hunter=StubHunter())
und_am = D(und_card).get("anchor_mode") or {}
und_comps = und_am.get("components") or []
und_recon = und_am.get("reconciliation") or {}
# Honest "no narrative gap": base clamps to anchor, gap=0 → NO narrative
# components, undervalued flag set, bet is None (not the 100% narrative the bug
# produced).
check("#2b undervalued stock → NO narrative components (not 100% 叙事)",
      len(und_comps) == 0 and und_am.get("undervalued") is True
      and und_card.bet is None,
      f"components={len(und_comps)} undervalued={und_am.get('undervalued')} "
      f"bet={und_card.bet} raw_base=${und_am.get('raw_base_business_value', 0):.2f}")
# And no theme-exposure row claims a ≥50% (let alone 100%) AI exposure.
und_exps = und_card.theme_exposures or []
und_max_exp = max((t.exposure_pct or 0.0 for t in und_exps), default=0.0)
check("#2c undervalued stock → no 100% (or even ≥50%) narrative exposure row",
      und_max_exp < 50.0,
      f"max exposure_pct={und_max_exp} (themes={[(t.theme, t.exposure_pct) for t in und_exps]})")

# Counter-case: a genuinely OVERVALUED AI composite (anchor far above baseline)
# DOES still produce narrative components — the fix must not over-correct.
OVERVALUED = Fundamentals(
    ticker="OVER", current_price=180.0,
    revenue_ttm=130e9, net_income_ttm=73e9, ebitda_ttm=88e9,
    fcf_ttm=60e9, book_equity=80e9, eps_ttm=2.95,
    shares_outstanding=24.5e9, net_debt=-30e9, beta=1.7, growth_rate=0.55,
    industry="Technology / Semiconductors — AI chip / GPU accelerator",
)
ov_card = decode_bet("market", "OVER", "zh",
                     fundamentals_fn=lambda t: OVERVALUED, hunter=StubHunter())
ov_am = D(ov_card).get("anchor_mode") or {}
check("#2d overvalued AI composite STILL decodes narrative components (no over-correct)",
      len(ov_am.get("components") or []) >= 1
      and ov_am.get("undervalued") is False
      and ov_card.bet is not None,
      f"components={len(ov_am.get('components') or [])} bet={ov_card.bet:.2f} "
      f"base=${ov_am.get('base_business_value', 0):.2f}")


# ===========================================================================
# #3 — is_ai_composite no longer mis-gates storage REIT / 养老 on bare words.
# ===========================================================================
STORAGE_REIT = Fundamentals(
    ticker="PSA", current_price=300.0, revenue_ttm=4e9, net_income_ttm=2e9,
    eps_ttm=11.0, shares_outstanding=0.18e9, net_debt=10e9, beta=0.8,
    industry="Real Estate / REIT — Industrial Self-Storage",
)
SENIOR_CARE = Fundamentals(
    ticker="CARE", current_price=40.0, revenue_ttm=2e9, net_income_ttm=0.1e9,
    eps_ttm=1.0, shares_outstanding=0.3e9, net_debt=1e9, beta=0.9,
    industry="Healthcare / Senior Living — Memory Care Facilities",
)
COLD_STORAGE = Fundamentals(
    ticker="COLD", current_price=25.0, revenue_ttm=3e9, net_income_ttm=0.2e9,
    eps_ttm=0.7, shares_outstanding=0.28e9, net_debt=4e9, beta=0.7,
    industry="Industrials / REIT — Temperature-Controlled Cold Storage Warehouses",
)
reit_ai, reit_theme = decoder.is_ai_composite(STORAGE_REIT)
care_ai, care_theme = decoder.is_ai_composite(SENIOR_CARE)
cold_ai, _ = decoder.is_ai_composite(COLD_STORAGE)
check("#3a self-storage REIT NOT gated AI composite (bare 'storage' no longer fires)",
      reit_ai is False, f"is_ai={reit_ai} theme={reit_theme}")
check("#3b 'memory care' senior facility NOT gated AI composite (bare 'memory')",
      care_ai is False, f"is_ai={care_ai} theme={care_theme}")
check("#3c cold-storage warehouse NOT gated AI composite",
      cold_ai is False, f"is_ai={cold_ai}")
# Regression-positive: real AI-memory signal (HBM / DRAM, or memory + semi
# signal) MUST still fire so the fix doesn't blind the gate.
HBM_MAKER = Fundamentals(
    ticker="MU", current_price=120.0, revenue_ttm=25e9, net_income_ttm=3e9,
    eps_ttm=2.7, shares_outstanding=1.1e9, net_debt=5e9, beta=1.2,
    industry="Technology / Semiconductors — HBM / DRAM memory for AI",
)
MEM_SEMI = Fundamentals(
    ticker="MEMX", current_price=80.0, revenue_ttm=10e9, net_income_ttm=1e9,
    eps_ttm=2.0, shares_outstanding=0.5e9, net_debt=1e9, beta=1.3,
    industry="Semiconductor memory chips",  # generic 'memory' + semi signal
)
hbm_ai, hbm_theme = decoder.is_ai_composite(HBM_MAKER)
mem_ai, mem_theme = decoder.is_ai_composite(MEM_SEMI)
check("#3d HBM/DRAM maker STILL gated AI composite (specific term fires)",
      hbm_ai is True and hbm_theme == "存储",
      f"is_ai={hbm_ai} theme={hbm_theme}")
check("#3e generic 'memory' + semiconductor signal STILL fires (gated, not blind)",
      mem_ai is True and mem_theme == "存储",
      f"is_ai={mem_ai} theme={mem_theme}")


# ===========================================================================
# #4 — cost estimate reflects the REAL hunt count + correct model rate.
# ===========================================================================
# The bug: ASSUMPTIONS_PER_TICKER_FIRST_DECODE=1 under-counted ~3x because
# gather_evidence_for_card hunts primary + cross (≈3). The portfolio estimator
# now gives ~$77 for 8 tickers, and the per-card estimate matches the actual
# hunt count exactly.
port_est = evidence.estimate_portfolio_first_decode_cost(8)
check("#4a 8-ticker first-decode estimate ≈ $77 (3 hunts/ticker), not the old $24",
      70 <= port_est["estimated_cost_usd"] <= 85
      and port_est["n_evidence_calls"] == 24
      and port_est["assumptions_per_ticker"] == 3,
      f"{port_est['human']} → ${port_est['estimated_cost_usd']}")
# The estimate's model must be the one _default_hunter actually calls.
_hunter_model = evidence._hunter_model(flagship=False)
check("#4b estimate model == _default_hunter's model (rate consistency)",
      port_est["model"] == _hunter_model
      and "mini" in _hunter_model,
      f"estimate model={port_est['model']} hunter model={_hunter_model}")
# Per-card estimate equals the REAL hunt count (no ~3x drift): decode NVDA with
# a counting stub, then assert estimate == hunter.calls.
_nvda = Fundamentals(
    ticker="NVDA", current_price=180.0, revenue_ttm=130e9, net_income_ttm=73e9,
    ebitda_ttm=88e9, fcf_ttm=60e9, book_equity=80e9, eps_ttm=2.95,
    shares_outstanding=24.5e9, net_debt=-30e9, beta=1.7, growth_rate=0.55,
)
evidence.reset_memory_cache()
_h = StubHunter()
_c = decode_bet("market", "NVDA", "zh", fundamentals_fn=lambda t: _nvda, hunter=_h)
_sec = EV(_c)
_per_card = evidence.assumptions_per_card(_c)
_est = _sec.get("cost", {}).get("estimated_first_decode_usd")
check("#4c per-card estimate == actual hunt count (estimate ↔ real, no under-count)",
      _per_card == _h.calls == _sec.get("assumption_count")
      and abs(_est - _h.calls * evidence.COST_PER_EVIDENCE_MINI) < 1e-9,
      f"assumptions_per_card={_per_card} hunter_calls={_h.calls} "
      f"assumption_count={_sec.get('assumption_count')} est=${_est}")
# Mini per-call constant derives from client pricing (and reproduces $3.21).
check("#4d COST_PER_EVIDENCE_MINI derived from client rate (= $3.21)",
      abs(evidence.COST_PER_EVIDENCE_MINI - 3.21) < 1e-9,
      f"COST_PER_EVIDENCE_MINI=${evidence.COST_PER_EVIDENCE_MINI}")
# Cost-safety hardening: _default_hunter must honor OFFLINE_MODE as its docstring
# promises — returning None (no network) EVEN WITH a key set.  This is the
# reliable kill-switch (a key can be silently re-populated from a project .env by
# dotenv even after a caller clears it, so "no key" alone is not dependable).
_saved_off = os.environ.get("OFFLINE_MODE")
_saved_key = os.environ.get("MIROMIND_API_KEY")
try:
    os.environ["OFFLINE_MODE"] = "true"
    os.environ["MIROMIND_API_KEY"] = "dummy_should_be_ignored"
    _off_res = evidence._default_hunter(
        "NVDA", {"id": "x", "human_text": "t"}, lang="zh", mode="standard",
        company_name="NVDA", current_price=180.0)
finally:
    if _saved_off is None:
        os.environ.pop("OFFLINE_MODE", None)
    else:
        os.environ["OFFLINE_MODE"] = _saved_off
    if _saved_key is None:
        os.environ.pop("MIROMIND_API_KEY", None)
    else:
        os.environ["MIROMIND_API_KEY"] = _saved_key
check("#4e OFFLINE_MODE=true → _default_hunter returns None even WITH a key (kill-switch)",
      _off_res is None,
      f"returned={_off_res!r} (must be None, no network)")


# ===========================================================================
# #5 — portfolio aggregate card carries a shape-consistent evidence node.
# ===========================================================================
# The bug: _decode_portfolio set no `evidence` key, so a consumer reading
# decode_detail["evidence"][...] KeyError'd / mis-rendered the portfolio as "no
# evidence". The fix attaches a unified aggregate section with the SAME keys as
# a single-card section, plus a per-leg breakdown.
_FIX = {f.ticker: f for f in (_nvda,
        Fundamentals(ticker="COST", current_price=900.0, revenue_ttm=255e9,
                     net_income_ttm=7.4e9, ebitda_ttm=11e9, fcf_ttm=6e9,
                     book_equity=23e9, eps_ttm=16.6, shares_outstanding=0.443e9,
                     net_debt=-5e9, beta=0.8, growth_rate=0.09))}


def _pf_fund(t):
    f = _FIX.get(t.upper())
    if f is None:
        raise RuntimeError(f"no fixture {t}")
    return f


evidence.reset_memory_cache()
hp = StubHunter()
pf = decode_bet("portfolio", "NVDA, COST", "zh",
                fundamentals_fn=_pf_fund, hunter=hp)
pf_ev = EV(pf)
SINGLE_KEYS = {"briefs", "assumption_count", "found_count", "empty_count",
               "cache_hits", "new_hunter_calls", "cost"}
check("#5a portfolio card HAS an evidence node with the single-card shape",
      pf.card_kind == db.PORTFOLIO and isinstance(pf_ev, dict)
      and SINGLE_KEYS.issubset(set(pf_ev.keys())),
      f"keys={sorted(pf_ev.keys())}")
# Cost discipline (2026-06-01): portfolio legs are NOT individually evidence-
# hunted — _decode_portfolio passes the _SKIP_EVIDENCE sentinel, so the stub
# hunter is never called and the aggregate is an HONEST-EMPTY roll-up: the node
# still exists with the single-card shape and stays keyed per leg, but found/new
# counts are 0.  (A holding's evidence is obtained by decoding it as a single card.)
check("#5b portfolio legs NOT evidence-hunted (cost discipline; honest-empty roll-up)",
      hp.calls == 0
      and pf_ev.get("found_count") == 0
      and pf_ev.get("new_hunter_calls") == 0
      and isinstance(pf_ev.get("legs"), dict)
      and set(pf_ev["legs"].keys()) == {"NVDA", "COST"},
      f"hunter_calls={hp.calls} found={pf_ev.get('found_count')} "
      f"new_calls={pf_ev.get('new_hunter_calls')} "
      f"legs={list((pf_ev.get('legs') or {}).keys())}")
# Cost shape consistency: a consumer can read cost.actual_new_call_usd safely.
check("#5c portfolio evidence cost node has the same keys as a single card",
      set((pf_ev.get("cost") or {}).keys())
      >= {"estimated_first_decode_usd", "actual_new_call_usd"},
      f"cost={pf_ev.get('cost')}")


# ===========================================================================
# #6 — EV/EBITDA with negative enterprise value → None (no negative multiple).
# ===========================================================================
# Net-cash company: cash so large that EV = mcap + net_debt < 0 at the anchor.
# net_debt = -100e9, anchor*shares = 50*1e9 = 50e9 → EV = -50e9 < 0.
NETCASH = Fundamentals(
    ticker="NETCASH", current_price=50.0, revenue_ttm=10e9,
    net_income_ttm=2e9, ebitda_ttm=3e9, eps_ttm=2.0,
    shares_outstanding=1e9, net_debt=-100e9, beta=1.0,
)
_ev_raw = NETCASH.enterprise_value(50.0)
ev_res = decoder._run_lens("ev_ebitda", 50.0, NETCASH)
check("#6a EV/EBITDA with negative EV → None (no negative implied multiple)",
      _ev_raw is not None and _ev_raw < 0 and ev_res is None,
      f"raw EV=${_ev_raw/1e9:.1f}B → lens result={ev_res}")
# General guard: a multiple-family lens never surfaces a negative implied value
# (the _run_lens family guard). Positive-EV case still solves normally.
POSEV = Fundamentals(
    ticker="POSEV", current_price=50.0, revenue_ttm=10e9,
    net_income_ttm=2e9, ebitda_ttm=3e9, eps_ttm=2.0,
    shares_outstanding=1e9, net_debt=10e9, beta=1.0,
)
pos_res = decoder._run_lens("ev_ebitda", 50.0, POSEV)
check("#6b positive-EV EV/EBITDA still solves (guard only blocks negatives)",
      pos_res is not None and pos_res["implied_value"] > 0,
      f"implied_ev_ebitda={pos_res['implied_value']:.2f}" if pos_res else "None")


# ===========================================================================
# #7 — nits: dead import gone, source_type honest, comp_sum defensive.
# ===========================================================================
import inspect  # noqa: E402
_dec_src = inspect.getsource(decoder)
check("#7a dead `replace` import removed from decoder.py",
      "from dataclasses import dataclass, field, replace" not in _dec_src
      and "from dataclasses import dataclass, field\n" in _dec_src,
      "dataclasses import no longer pulls in unused `replace`")
# unknown source_type → insufficient card that HONESTLY records the type (not
# silently relabeled "market").
unk = decode_bet("totally_unknown_source", "NVDA", "zh",
                 fundamentals_fn=lambda t: _nvda, hunter=StubHunter())
check("#7b unknown source_type preserved on card (not masqueraded as market)",
      isinstance(unk, db.BetCard)
      and unk.source_type == "totally_unknown_source"
      and D(unk).get("status") == "insufficient",
      f"source_type={unk.source_type} status={D(unk).get('status')}")
# A real V2 type (analyst_pt) likewise keeps its own type.
unk2 = decode_bet("analyst_pt", "NVDA $300", "zh",
                  fundamentals_fn=lambda t: _nvda, hunter=StubHunter())
check("#7c V2 source_type (analyst_pt) preserved, not relabeled",
      unk2.source_type == "analyst_pt",
      f"source_type={unk2.source_type}")
# comp_sum defensive: a malformed anchor component (missing implied_amount) must
# not crash reconciliation. Drive _run_anchor_mode with a stub anchor lens that
# omits implied_amount.
_orig_narr = decoder.ANCHOR_LENS_REGISTRY["narrative"].solve
try:
    decoder.ANCHOR_LENS_REGISTRY["narrative"].solve = (
        lambda gap, anchor, base, f, llm: {
            "lens": "narrative", "lens_label": "叙事锚", "claim": "malformed",
            # NOTE: intentionally NO "implied_amount" key
            "implied_assumption": "x", "probability": None, "evidence": [],
        }
    )
    crash = None
    try:
        _ = decode_bet("market", "OVER", "zh",
                       fundamentals_fn=lambda t: OVERVALUED, hunter=StubHunter())
    except Exception as exc:
        crash = exc
finally:
    decoder.ANCHOR_LENS_REGISTRY["narrative"].solve = _orig_narr
check("#7d malformed component (no implied_amount) never crashes 对账 (comp_sum .get)",
      crash is None, f"raised={crash}")


# ===========================================================================
print("=" * 72)
print(f"RESULT: {_passed} passed, {_failed} failed")
print("=" * 72)
raise SystemExit(1 if _failed else 0)
