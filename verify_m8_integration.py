"""M8 end-to-end integration verification — deterministic, ZERO API/network cost.

Issue #8 收口集成.  Drives the REAL FastAPI app through a TestClient with the
REAL engines (decode_bet / synthesize_cards / activity sink + replay), proving
every module's interface meshes.  Cost is held at $0 by two stubs:

  - ``decoder.fetch_fundamentals`` → a hardcoded Fundamentals snapshot (no
    yfinance call).  This is the ONLY network seam the REST decode path hits.
  - the evidence hunter is left at its default, which returns ``None`` when no
    ``MIROMIND_API_KEY`` is set (every test env) → honest-empty briefs, no Deep
    Research call.  We assert MIROMIND_API_KEY is unset so this can never spend.
  - the synthesis chat hook: the /api/synthesize endpoint uses the real client by
    default, so we monkeypatch ``synthesizer._default_chat`` → a no-LLM stub
    (returns None) so the relation graph is built deterministically, free.

No MiroMind API, no yfinance.  Each acceptance criterion prints one PASS/FAIL.

Run:  "/c/Users/Henry Ma/miniconda3/python.exe" verify_m8_integration.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

# Cost-safety: make absolutely sure the live hunter/synth paths can never reach
# the network even if some code ignored our stubs.  Set the key EMPTY (not pop):
# client.py's load_dotenv() does NOT override an already-present env var, so an
# empty string stays empty and a real key in a parent .env can never be
# re-injected.  Popping it would let load_dotenv re-read the real key from .env
# → a live (hanging, billable) Deep Research call.  Empty key → honest-empty.
os.environ["MIROMIND_API_KEY"] = ""
os.environ["OFFLINE_MODE"] = "false"

import db
import decoder
import synthesizer
from decoder import Fundamentals

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


# --- hardcoded fundamentals (no network) ----------------------------------
# COST-like clean profitable case → traditional P/E primary (NOT AI-composite,
# so no anchor-mode; keeps the e2e card a simple comparable single card).

_FIX = {
    "COST": Fundamentals(
        ticker="COST", current_price=900.0,
        revenue_ttm=255e9, net_income_ttm=7.4e9, ebitda_ttm=11e9,
        fcf_ttm=6e9, book_equity=23e9, eps_ttm=16.6,
        shares_outstanding=0.443e9, net_debt=-5e9, beta=0.8, growth_rate=0.09,
    ),
    "WMT": Fundamentals(
        ticker="WMT", current_price=95.0,
        revenue_ttm=665e9, net_income_ttm=19e9, ebitda_ttm=40e9,
        fcf_ttm=12e9, book_equity=90e9, eps_ttm=2.4,
        shares_outstanding=8.0e9, net_debt=20e9, beta=0.6, growth_rate=0.05,
    ),
}


def stub_fundamentals(ticker: str) -> Fundamentals:
    f = _FIX.get(ticker.upper())
    if f is None:
        raise RuntimeError(f"no fixture for {ticker}")
    return f


def stub_chat_none():
    """Replacement for synthesizer._default_chat: deterministic graph-only (no LLM)."""
    return None


# --- wire the app with a temp DB + stubs -----------------------------------
import api  # noqa: E402  (import after env + stubs are staged)

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
api.DB_PATH = _tmp.name                      # isolate from the real pricelens.db
decoder.fetch_fundamentals = stub_fundamentals   # no yfinance in the decode path
synthesizer._default_chat = stub_chat_none       # no LLM in /api/synthesize

from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(api.app)

print("=" * 70)
print("M8 收口集成 — end-to-end acceptance verification (stub engines, $0)")
print("=" * 70)


# ==========================================================================
# AC-SMOKE — uvicorn-importable; GET / returns 200 + workbench HTML.
# ==========================================================================
print("\n=== AC-SMOKE: app imports + serves the workbench ===")
check("SMOKE app imported + TestClient constructed (uvicorn-importable)",
      api.app is not None and hasattr(api.app, "routes"))
r_health = client.get("/api/health")
check("SMOKE GET /api/health 200 + {status:ok}",
      r_health.status_code == 200 and r_health.json().get("status") == "ok",
      f"{r_health.status_code}")
r_root = client.get("/")
root_html = r_root.text
workbench_markers = [
    'class="workbench"', 'id="wb-canvas"', 'id="wb-feed"',
    'id="card-grid"', 'id="tab-bar"',
]
present = [m for m in workbench_markers if m in root_html]
check("SMOKE GET / 200 + content-type HTML",
      r_root.status_code == 200 and "text/html" in r_root.headers.get("content-type", ""),
      f"{r_root.status_code} {r_root.headers.get('content-type')}")
check("SMOKE GET / body carries workbench DOM markers (canvas + feed + tabs)",
      len(present) >= 5, f"{len(present)}/{len(workbench_markers)} markers: {present}")


# ==========================================================================
# AC1 — POST /api/decode → M2 decode_bet → db.save_card.  Returns {job_id, card}.
# ==========================================================================
print("\n=== AC1: POST /api/decode (decode → save) ===")
r_dec = client.post("/api/decode", json={"source_type": "market",
                                          "source_input": "COST", "lang": "zh"})
dec_body = r_dec.json()
check("AC1 decode 200", r_dec.status_code == 200, f"{r_dec.status_code} {dec_body}")
check("AC1 response shape {job_id, card}",
      isinstance(dec_body, dict) and "job_id" in dec_body and "card" in dec_body,
      str(sorted(dec_body.keys())))
card_obj = dec_body.get("card") or {}
job_id = dec_body.get("job_id")
cost_card_id = card_obj.get("card_id")
check("AC1 card carries an id + subject COST (persisted)",
      cost_card_id is not None and card_obj.get("subject") == "COST",
      f"id={cost_card_id} subject={card_obj.get('subject')}")
check("AC1 card has a comparable bet scalar (primary lens implied value)",
      card_obj.get("bet") is not None, f"bet={card_obj.get('bet')}")


# ==========================================================================
# AC2 — GET /api/cards (list) + GET /api/cards/{id} round-trip the saved card.
# ==========================================================================
print("\n=== AC2: GET /api/cards + /api/cards/{id} ===")
r_list = client.get("/api/cards")
list_body = r_list.json()
check("AC2 GET /api/cards 200 + {cards:[...]}",
      r_list.status_code == 200 and isinstance(list_body.get("cards"), list),
      f"{r_list.status_code}")
ids_in_list = [c.get("card_id") for c in list_body.get("cards", [])]
check("AC2 list contains the just-decoded card",
      cost_card_id in ids_in_list, f"have {len(ids_in_list)} cards")
r_get = client.get(f"/api/cards/{cost_card_id}")
get_body = r_get.json()
check("AC2 GET /api/cards/{id} 200 + same subject",
      r_get.status_code == 200 and get_body.get("subject") == "COST",
      f"{r_get.status_code}")
r_404 = client.get("/api/cards/deadbeefdeadbeef")
check("AC2 unknown id → 404 error_code=card_not_found",
      r_404.status_code == 404 and r_404.json().get("error_code") == "card_not_found",
      f"{r_404.status_code} {r_404.json().get('error_code')}")


# ==========================================================================
# AC3 — decode a 2nd distinct card so a synthesize set exists.
# ==========================================================================
print("\n=== AC3: decode 2nd card for a synthesize set ===")
r_dec2 = client.post("/api/decode", json={"source_type": "market",
                                          "source_input": "WMT", "lang": "zh"})
card2 = r_dec2.json().get("card") or {}
wmt_card_id = card2.get("card_id")
check("AC3 2nd decode (WMT) 200 + distinct card",
      r_dec2.status_code == 200 and wmt_card_id is not None
      and wmt_card_id != cost_card_id,
      f"id={wmt_card_id}")


# ==========================================================================
# AC4 — POST /api/synthesize over the 2-card set → M3 SynthesisResult shape.
# ==========================================================================
print("\n=== AC4: POST /api/synthesize (cross-card) ===")
r_syn = client.post("/api/synthesize",
                    json={"card_ids": [cost_card_id, wmt_card_id], "lang": "zh"})
syn_body = r_syn.json()
check("AC4 synthesize 200", r_syn.status_code == 200, f"{r_syn.status_code}")
syn_keys_ok = set(syn_body.keys()) == {"card_ids", "generated_at",
                                       "headline_insight", "relations", "narrative"}
check("AC4 SynthesisResult top-level keys exact (API_CONTRACT §3)",
      syn_keys_ok, str(sorted(syn_body.keys())))
check("AC4 relations is a list; card_ids includes both inputs",
      isinstance(syn_body.get("relations"), list)
      and set(syn_body.get("card_ids", [])) == {cost_card_id, wmt_card_id},
      f"card_ids={syn_body.get('card_ids')}")
check("AC4 narrative is None (chat stubbed off → graph-only, $0)",
      syn_body.get("narrative") is None)
# bad body → 400
r_syn_bad = client.post("/api/synthesize", json={"card_ids": [], "lang": "zh"})
check("AC4 empty card_ids → 400 bad_request",
      r_syn_bad.status_code == 400 and r_syn_bad.json().get("error_code") == "bad_request",
      f"{r_syn_bad.status_code}")


# ==========================================================================
# AC5 — activity SSE: GET /api/stream/activity/{job_id} replays the decode job.
# The /api/decode path persists the agent reasoning to activity_logs under
# job_id; the stream endpoint replays it as a flushed SSE event stream.
# ==========================================================================
print("\n=== AC5: GET /api/stream/activity/{job_id} (replay SSE) ===")


def consume_sse(text: str) -> list[dict]:
    """Parse an SSE response body into the JSON objects it carries (skip :comments)."""
    out: list[dict] = []
    for block in text.split("\n\n"):
        block = block.strip("\n")
        if not block or block.startswith(":"):
            continue
        for line in block.split("\n"):
            if line.startswith("data:"):
                try:
                    out.append(json.loads(line[len("data:"):].strip()))
                except json.JSONDecodeError:
                    pass
    return out


# speed=0 → replay with no inter-event sleeps (fast, deterministic).
r_sse = client.get(f"/api/stream/activity/{job_id}?speed=0")
sse_events = consume_sse(r_sse.text)
check("AC5 stream 200 + text/event-stream",
      r_sse.status_code == 200
      and "text/event-stream" in r_sse.headers.get("content-type", ""),
      f"{r_sse.status_code} {r_sse.headers.get('content-type')}")
check("AC5 replay yields the decode job's reasoning events (>=2)",
      len(sse_events) >= 2, f"n_events={len(sse_events)}")
check("AC5 events carry kind tags from the M5 vocab",
      all(e.get("kind") in {"decision", "computation", "evidence", "relation"}
          for e in sse_events) and len(sse_events) > 0,
      f"kinds={sorted({e.get('kind') for e in sse_events})}")
check("AC5 stream terminates with a terminal=done frame (no hang, bug#34)",
      bool(sse_events) and sse_events[-1].get("terminal") == "done",
      f"last_terminal={sse_events[-1].get('terminal') if sse_events else None}")
# Unknown job → synthetic error-terminal so the client never hangs.
r_sse_missing = consume_sse(client.get("/api/stream/activity/nope-no-such-job").text)
check("AC5 unknown job → single error-terminal frame (client won't hang)",
      len(r_sse_missing) == 1 and r_sse_missing[0].get("terminal") == "error",
      f"frames={len(r_sse_missing)}")


# ==========================================================================
# AC6 — full-chain coherence: the synthesize result references exactly the
# cards we decoded+saved, proving every module's id contract lines up end to end.
# ==========================================================================
print("\n=== AC6: full-chain id coherence ===")
chain_ok = (
    cost_card_id in ids_in_list
    and wmt_card_id is not None
    and set(syn_body.get("card_ids", [])) == {cost_card_id, wmt_card_id}
    and sse_events and sse_events[-1].get("terminal") == "done"
)
check("AC6 decode→save→get/list→synthesize→activity-SSE chain coheres on ids",
      chain_ok)


# ==========================================================================
# AC7 — DELETE /api/cards/{id} removes a card (cleanup + contract).
# ==========================================================================
print("\n=== AC7: DELETE /api/cards/{id} ===")
r_del = client.delete(f"/api/cards/{wmt_card_id}")
check("AC7 delete 200 + {deleted:true}",
      r_del.status_code == 200 and r_del.json().get("deleted") is True,
      f"{r_del.status_code} {r_del.json()}")
r_get_deleted = client.get(f"/api/cards/{wmt_card_id}")
check("AC7 deleted card now 404",
      r_get_deleted.status_code == 404)


# ==========================================================================
# AC8 — cost discipline: prove no real API key is present (the live decode +
# synth endpoints fall back to honest-empty / graph-only at $0).
# ==========================================================================
print("\n=== AC8: cost discipline (no live API touched) ===")
check("AC8 MIROMIND_API_KEY unset → live hunter is honest-empty, never spends",
      not os.environ.get("MIROMIND_API_KEY"))
# The decoded COST card's evidence section must be honest-empty (no fabrication,
# no Deep Research call) — assert the briefs are empty.
ev = (card_obj.get("decode_detail") or {}).get("evidence") if isinstance(
    card_obj.get("decode_detail"), dict) else None
# decode_detail is not persisted, so re-decode in-process to inspect the section.
in_proc = decoder.decode_bet("market", "COST", "zh",
                             fundamentals_fn=stub_fundamentals)
ev_section = (getattr(in_proc, "decode_detail", {}) or {}).get("evidence", {})
check("AC8 evidence section present + honestly empty (found=0, $0 actual)",
      ev_section.get("found_count") == 0
      and ev_section.get("cost", {}).get("actual_new_call_usd") == 0.0,
      f"found={ev_section.get('found_count')} "
      f"cost={ev_section.get('cost', {}).get('actual_new_call_usd')}")


# ==========================================================================
# AC9 — prerun_demo.py dry-run: 5-act plan + cost estimate, and the dry-run path
# is import-isolated from the network (no client/httpx/yfinance).  We assert the
# cost ACCOUNTING is complete + internally consistent (incl. the flagship market-
# narrative layer, which an earlier model omitted) and that the budget guardrail
# is computed correctly — NOT a fixed "fits budget" verdict, since the dataset vs
# $100 budget tradeoff is a product decision that may change the totals.
# The --execute branch exists (code review can see it calls real decode/synth)
# but is NOT run here.
# ==========================================================================
print("\n=== AC9: prerun_demo.py dry-run (plan + cost, zero network) ===")
import importlib  # noqa: E402

# Snapshot modules so we can assert the dry-run path imports no network client.
_before = set(sys.modules)
import prerun_demo  # noqa: E402

plan = prerun_demo.estimate_plan()
check("AC9 plan covers 5 acts (NVDA, TSLA contrast, 8-ticker portfolio, synthesize)",
      len(plan["acts"]) == 5 and plan["n_portfolio_tickers"] == 8
      and plan["n_singles"] == 2,
      f"acts={len(plan['acts'])} portfolio={plan['n_portfolio_tickers']}")
check("AC9 cost accounting complete + consistent (total == evidence + narrative + synthesis)",
      abs(plan["total_cost_usd"]
          - (plan["evidence_cost_usd"] + plan["narrative_cost_usd"]
             + plan["synthesis_cost_usd"])) < 0.01
      and plan["total_calls"] == plan["evidence_calls"] + plan["narrative_calls"] + 1,
      f"total=${plan['total_cost_usd']} = ev ${plan['evidence_cost_usd']} + narr "
      f"${plan['narrative_cost_usd']} + synth ${plan['synthesis_cost_usd']}")
# Regression guard: the flagship market-narrative layer (decoder._attach_market_
# narrative) runs for the 2 SINGLE MARKET cards and is $0 for portfolios — it must
# be counted (it was omitted before, understating the bill ~$16).
check("AC9 flagship market-narrative counted (2 single cards; portfolio = $0)",
      plan["narrative_calls"] == 2 and plan["narrative_cost_usd"] > 0,
      f"narrative_calls={plan['narrative_calls']} cost=${plan['narrative_cost_usd']}")
check("AC9 within_budget flag derived correctly from total vs $100/100-call budget",
      plan["within_budget"] == (plan["total_cost_usd"] <= plan["budget_usd"]
                                and plan["total_calls"] <= plan["budget_calls"]),
      f"within_budget={plan['within_budget']} total=${plan['total_cost_usd']}/"
      f"${plan['budget_usd']}")
check("AC9 upper bound is a true ceiling (>= base) + flag derived correctly",
      plan["upper_bound_cost_usd"] >= plan["total_cost_usd"]
      and plan["upper_bound_within_budget"] == (
          plan["upper_bound_cost_usd"] <= plan["budget_usd"]
          and plan["upper_bound_calls"] <= plan["budget_calls"]),
      f"ub=${plan['upper_bound_cost_usd']} calls={plan['upper_bound_calls']}")
check("AC9 each act lists the caches it fills (evidence/narrative/synthesis/activity/price)",
      all(("fills" in a) for a in plan["acts"])
      and any("llm_cache(evidence)" in a["fills"] for a in plan["acts"])
      and any("llm_cache(narrative)" in a["fills"] for a in plan["acts"])
      and any("llm_cache(synthesis)" in a["fills"] for a in plan["acts"])
      and any("activity_logs" in a["fills"] for a in plan["acts"]),
      "fills manifest present")
# Dry-run import isolation: importing prerun_demo must NOT pull in the network
# client / httpx / yfinance (it imports only `evidence`).  We check the *delta*
# of newly-imported modules from this import, because earlier e2e ACs already
# loaded client/httpx/yfinance through the real REST decode path — so an absolute
# "not in sys.modules" check would mis-attribute those to prerun.
_after = set(sys.modules)
newly = _after - _before
_NET = ("client", "httpx", "yfinance")
newly_net = [m for m in newly if m in _NET]
check("AC9 importing prerun_demo pulls in NO network module (client/httpx/yfinance)",
      newly_net == [], f"newly_imported_network={newly_net}")
check("AC9 --execute branch exists as code (gated; not run here)",
      callable(getattr(prerun_demo, "run_execute", None)))
# main() default (no --execute) returns the budget guardrail code (0 in budget /
# 2 over budget — the docstringed pre-flight signal) and adds no network import.
# Capture stdout so the verifier output isn't flooded by the plan print, and
# re-measure the import delta around the call.
import contextlib  # noqa: E402
import io  # noqa: E402
_before_main = set(sys.modules)
_buf = io.StringIO()
with contextlib.redirect_stdout(_buf):
    rc = prerun_demo.main([])
_main_net = [m for m in (set(sys.modules) - _before_main) if m in _NET]
_expected_rc = 0 if plan["within_budget"] else 2
check("AC9 main([]) dry-run returns the budget guardrail code (0 in / 2 over) + no network import",
      rc == _expected_rc and _main_net == [],
      f"rc={rc} expected={_expected_rc} within_budget={plan['within_budget']} main_net={_main_net}")


# --- cleanup temp db -------------------------------------------------------
try:
    os.unlink(_tmp.name)
except OSError:
    pass


# --- summary --------------------------------------------------------------
print("=" * 70)
print(f"RESULT: {_passed} passed, {_failed} failed")
print("=" * 70)
raise SystemExit(1 if _failed else 0)
