"""Agentic layer end-to-end verification — deterministic, ZERO API/network cost.

Phase F.  Drives the REAL FastAPI app through a TestClient with the REAL engines
(orchestrator.decode_bet_agentic / answer_followup / build_revised_card + the
activity sink + replay), proving the agentic HTTP surface meshes end to end:

  - POST /api/decode (agentic=PRIMARY) runs the tool-calling loop, applies the
    agent's plan, and PERSISTS decode_detail (the TD1 keystone) — round-trips
    byte-identical through GET /api/cards/{id} (card_to_json_full).
  - POST /api/cards/{id}/ask runs a job → returns {answer, revision}; the agent's
    reasoning replays via GET /api/stream/activity/{job_id}.
  - POST /api/cards/{id}/revise persists a PROVENANCED derived card (derived_from
    + derivation diff); the parent is untouched and both coexist the same day
    (the daily-unique index excludes derived cards).
  - Offline guard: /ask refuses (503) under OFFLINE_MODE; /revise still works
    (no LLM/network).  Airtight fallback: agentic decode with no tool-calling
    provider degrades to the deterministic decode and STILL persists detail.

Cost is held at $0 by three seams:
  - ``decoder.fetch_fundamentals`` → a hardcoded Fundamentals snapshot (no yfinance).
  - ``client._CHAT_TOOLS_IMPL`` → a SCRIPTED tool-calling stub (no real LLM). It
    routes by the system prompt: the decode-planner loop gets the decode script,
    the Q&A loop gets the what-if script.
  - MIROMIND_API_KEY="" so even an un-stubbed path is honest-empty, never billable.

Run:  "/c/Users/Henry Ma/miniconda3/python.exe" verify_agentic_e2e.py
"""
from __future__ import annotations

import json
import os
import tempfile

# Cost-safety: empty (not pop) the key so client.py's load_dotenv() can't
# re-inject a real key from a parent .env. OFFLINE_MODE=false so the live decode/
# ask endpoints don't 503 (we drive them with the scripted stub instead).
os.environ["MIROMIND_API_KEY"] = ""
os.environ["OFFLINE_MODE"] = "false"

import client
import db
import decoder
from decoder import Fundamentals

_passed = 0
_failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
    extra = f"  | {detail}" if detail else ""
    print(f"[{'PASS' if cond else 'FAIL'}] {name}{extra}")


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


# --- hardcoded fundamentals (no network) ----------------------------------
# COST: a clean, profitable, DCF-solvable case → traditional P/E primary +
# a what-if that actually moves the implied number. WMT: the fallback-path
# fixture (a 2nd distinct subject so it doesn't dedup-collide with COST).
_FIX = {
    "COST": Fundamentals(
        ticker="COST", current_price=900.0, revenue_ttm=255e9, net_income_ttm=7.4e9,
        ebitda_ttm=11e9, fcf_ttm=6e9, book_equity=23e9, eps_ttm=16.6,
        shares_outstanding=0.443e9, net_debt=-5e9, beta=0.8, growth_rate=0.09,
        industry="Discount Stores"),
    "WMT": Fundamentals(
        ticker="WMT", current_price=95.0, revenue_ttm=665e9, net_income_ttm=19e9,
        ebitda_ttm=40e9, fcf_ttm=12e9, book_equity=90e9, eps_ttm=2.4,
        shares_outstanding=8.0e9, net_debt=20e9, beta=0.6, growth_rate=0.05,
        industry="Discount Stores"),
}


def stub_fundamentals(ticker: str) -> Fundamentals:
    f = _FIX.get(ticker.upper())
    if f is None:
        raise RuntimeError(f"no fixture for {ticker}")
    return f


# --- scripted tool-calling stub (no real LLM) ------------------------------
def _tc(name, args):
    return {"id": f"call_{name}", "name": name, "arguments_raw": json.dumps(args)}


def _resp(tcs, content=""):
    return {"content": content, "cost_usd": 0.0, "tool_calls": tcs,
            "assistant_message": {"role": "assistant", "content": content,
                                  "tool_calls": [{"id": t["id"], "type": "function",
                                                  "function": {"name": t["name"],
                                                               "arguments": t["arguments_raw"]}}
                                                 for t in tcs]},
            "finish_reason": "tool_calls" if tcs else "stop"}


# Decode loop: investigate once, then submit a traditional P/E plan.
_DECODE_SCRIPT = [
    _resp([_tc("plan_lenses", {})], "先看哪些 lens 适用。"),
    _resp([_tc("submit_decode_plan", {"mode": "traditional", "primary_key": "pe",
                                      "reason": "COST 稳健盈利,用 P/E 反解最贴切。"})]),
]
# Q&A loop: a what-if → propose_revision (re-solve under WACC=9%).
_QA_SCRIPT = [
    _resp([_tc("propose_revision", {"solve_for": "revenue_cagr_5y",
                                    "overrides": {"wacc": 0.09},
                                    "summary": "WACC 提到 9%,需要更高的隐含增长来支撑同样的价格。"})]),
]
_state = {"decode": 0, "qa": 0}


def scripted_tools(messages, **kw):
    sys = (messages[0].get("content") if messages else "") or ""
    if "decode planner" in sys:  # orchestrator._SYSTEM_PROMPT marker
        i = min(_state["decode"], len(_DECODE_SCRIPT) - 1)
        _state["decode"] += 1
        return _DECODE_SCRIPT[i]
    i = min(_state["qa"], len(_QA_SCRIPT) - 1)  # _QA_SYSTEM_PROMPT
    _state["qa"] += 1
    return _QA_SCRIPT[i]


# --- wire the app with a temp DB + stubs -----------------------------------
import api  # noqa: E402  (import after env is staged)

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
api.DB_PATH = _tmp.name
decoder.fetch_fundamentals = stub_fundamentals
client._CHAT_TOOLS_IMPL = scripted_tools

from fastapi.testclient import TestClient  # noqa: E402

tc = TestClient(api.app)

print("=" * 72)
print("Agentic layer — end-to-end acceptance verification (scripted stub, $0)")
print("=" * 72)


# ==========================================================================
# AC-SMOKE — app imports + the agentic routes exist.
# ==========================================================================
print("\n=== AC-SMOKE: app + agentic routes ===")
routes = {getattr(r, "path", "") for r in api.app.routes}
check("SMOKE app imports + /ask + /revise routes registered",
      "/api/cards/{card_id}/ask" in routes and "/api/cards/{card_id}/revise" in routes,
      f"ask={'/api/cards/{card_id}/ask' in routes} revise={'/api/cards/{card_id}/revise' in routes}")
check("SMOKE GET /api/health 200",
      tc.get("/api/health").json().get("status") == "ok")


# ==========================================================================
# AC1 — POST /api/decode (agentic=PRIMARY) runs the loop + persists detail.
# ==========================================================================
print("\n=== AC1: agentic decode → plan applied + decode_detail persisted ===")
r_dec = tc.post("/api/decode", json={"source_type": "market",
                                     "source_input": "COST", "lang": "zh"})
dec_body = r_dec.json()
check("AC1 decode 200 + {job_id, card}",
      r_dec.status_code == 200 and "job_id" in dec_body and "card" in dec_body,
      f"{r_dec.status_code} {sorted(dec_body.keys())}")
card_obj = dec_body.get("card") or {}
cost_id = card_obj.get("card_id")
job_id = dec_body.get("job_id")
dd_post = card_obj.get("decode_detail") or {}
check("AC1 card persisted (id + subject COST)",
      cost_id is not None and card_obj.get("subject") == "COST",
      f"id={cost_id} subject={card_obj.get('subject')}")
check("AC1 decode_detail.mode tagged agentic_* (the agent's plan was applied)",
      str(dd_post.get("mode", "")).startswith("agentic_"),
      f"mode={dd_post.get('mode')!r}")
check("AC1 decode_detail records the real agent trace (tool calls + plan)",
      dd_post.get("agentic") is True and isinstance(dd_post.get("agent_trace"), list)
      and any(t.get("tool") == "submit_decode_plan" for t in dd_post.get("agent_trace", [])),
      f"trace_len={len(dd_post.get('agent_trace', []))}")


# ==========================================================================
# AC2 — GET /api/cards/{id} round-trips decode_detail byte-identical (TD1).
# ==========================================================================
print("\n=== AC2: decode_detail survives save+reload (TD1 regression, HTTP layer) ===")
r_get = tc.get(f"/api/cards/{cost_id}")
get_body = r_get.json()
dd_reloaded = get_body.get("decode_detail") or {}
check("AC2 GET /api/cards/{id} 200 + carries decode_detail (not lost on reload)",
      r_get.status_code == 200 and isinstance(dd_reloaded, dict) and bool(dd_reloaded),
      f"{r_get.status_code} keys={sorted(dd_reloaded.keys())[:6]}")
check("AC2 reloaded decode_detail deep-equals the decoded one (lossless round-trip)",
      json.dumps(dd_reloaded, sort_keys=True, default=str)
      == json.dumps(dd_post, sort_keys=True, default=str),
      "json-normalized compare")
check("AC2 reloaded card exposes a _display projection (renders rich, not the thin branch)",
      isinstance(get_body.get("_display"), dict) and get_body["_display"].get("bets"),
      f"_display={'present' if get_body.get('_display') else 'absent'}")


# ==========================================================================
# AC3 — POST /ask runs a job → {answer, revision}; reasoning replays via SSE.
# ==========================================================================
print("\n=== AC3: /ask what-if → revision proposed (not yet saved) + activity replay ===")
r_ask = tc.post(f"/api/cards/{cost_id}/ask",
                json={"question": "如果 WACC 提到 9% 会怎样?", "lang": "zh"})
ask_body = r_ask.json()
ask_job = ask_body.get("revision") is not None and ask_body.get("job_id")
rev = ask_body.get("revision") or {}
check("AC3 ask 200 + non-empty answer + job_id",
      r_ask.status_code == 200 and bool(ask_body.get("answer")) and bool(ask_body.get("job_id")),
      f"{r_ask.status_code} answer={str(ask_body.get('answer'))[:32]!r}")
check("AC3 a what-if revision is proposed (kind=whatif, override carried)",
      rev.get("kind") == "whatif" and rev.get("params", {}).get("overrides") == {"wacc": 0.09},
      f"params={rev.get('params')}")
check("AC3 revision diff actually moved the implied number (before != after)",
      bool(rev.get("diff")) and rev["diff"][0]["before"] != rev["diff"][0]["after"],
      f"diff={rev.get('diff')}")
check("AC3 proposing did NOT persist a new card yet (still just the parent)",
      len(tc.get("/api/cards").json().get("cards", [])) == 1)
# replay the agent's reasoning for this ask job
ask_events = consume_sse(tc.get(f"/api/stream/activity/{ask_body.get('job_id')}?speed=0").text)
check("AC3 /ask reasoning replays via activity SSE + terminates (no hang)",
      len(ask_events) >= 1 and ask_events[-1].get("terminal") == "done",
      f"n_events={len(ask_events)} last={ask_events[-1].get('terminal') if ask_events else None}")


# ==========================================================================
# AC4 — POST /revise persists a PROVENANCED derived card; parent untouched;
#       both coexist the same day (dedup index excludes derived cards).
# ==========================================================================
print("\n=== AC4: /revise → provenanced derived card; parent immutable; coexist ===")
r_rev = tc.post(f"/api/cards/{cost_id}/revise", json={"revision": rev})
rev_body = r_rev.json()
derived = rev_body.get("card") or {}
did = derived.get("card_id")
check("AC4 revise 200 + a NEW card distinct from the parent",
      r_rev.status_code == 200 and did is not None and did != cost_id,
      f"{r_rev.status_code} did={did} pid={cost_id}")
check("AC4 derived card records provenance (derived_from=parent + whatif derivation)",
      derived.get("derived_from") == cost_id
      and (derived.get("decode_detail") or {}).get("revision", {}).get("diff")
      and str((derived.get("decode_detail") or {}).get("mode", "")).startswith("whatif_"),
      f"derived_from={derived.get('derived_from')} mode={(derived.get('decode_detail') or {}).get('mode')}")
# re-fetch the parent — it must be byte-for-byte the original (no mutation).
parent_now = tc.get(f"/api/cards/{cost_id}").json()
pdd = parent_now.get("decode_detail") or {}
check("AC4 parent is UNTOUCHED (no derived_from, original agentic_* mode, no revision)",
      parent_now.get("derived_from") is None
      and str(pdd.get("mode", "")).startswith("agentic_")
      and "revision" not in pdd,
      f"derived_from={parent_now.get('derived_from')} mode={pdd.get('mode')}")
all_ids = [c.get("card_id") for c in tc.get("/api/cards").json().get("cards", [])]
check("AC4 parent + derived BOTH persist (same-day coexist; dedup exempts derived)",
      cost_id in all_ids and did in all_ids and len(all_ids) == 2,
      f"ids={all_ids}")


# ==========================================================================
# AC5 — offline guard: /ask refuses (503); /revise still works (no LLM/network).
# ==========================================================================
print("\n=== AC5: OFFLINE_MODE guard (/ask refuses, /revise allowed) ===")
os.environ["OFFLINE_MODE"] = "1"
try:
    r_ask_off = tc.post(f"/api/cards/{cost_id}/ask", json={"question": "x"})
    check("AC5 /ask refuses offline with 503 error_code=offline_mode",
          r_ask_off.status_code == 503
          and r_ask_off.json().get("error_code") == "offline_mode",
          f"{r_ask_off.status_code} {r_ask_off.json().get('error_code')}")
    r_rev_off = tc.post(f"/api/cards/{cost_id}/revise", json={"revision": rev})
    check("AC5 /revise STILL works offline (pure build+save, no network) → another derived",
          r_rev_off.status_code == 200 and (r_rev_off.json().get("card") or {}).get("card_id"),
          f"{r_rev_off.status_code}")
finally:
    os.environ["OFFLINE_MODE"] = "false"


# ==========================================================================
# AC6 — airtight fallback: agentic decode with NO tool-calling provider
#       degrades to the deterministic decode and STILL persists decode_detail.
# ==========================================================================
print("\n=== AC6: agentic fallback → deterministic decode (still persists detail) ===")
_saved_impl = client._CHAT_TOOLS_IMPL
client._CHAT_TOOLS_IMPL = None  # no stub + miromind protocol → not tool-calling capable
try:
    r_fb = tc.post("/api/decode", json={"source_type": "market",
                                        "source_input": "WMT", "lang": "zh"})
    fb_card = r_fb.json().get("card") or {}
    fb_dd = fb_card.get("decode_detail") or {}
    check("AC6 decode still 200 + a valid persisted card (fallback, no crash)",
          r_fb.status_code == 200 and fb_card.get("card_id") and fb_card.get("subject") == "WMT",
          f"{r_fb.status_code} id={fb_card.get('card_id')}")
    check("AC6 fallback card has decode_detail but is NOT tagged agentic_* (deterministic path)",
          bool(fb_dd) and not str(fb_dd.get("mode", "")).startswith("agentic_")
          and fb_dd.get("agentic") is not True,
          f"mode={fb_dd.get('mode')!r} agentic={fb_dd.get('agentic')}")
finally:
    client._CHAT_TOOLS_IMPL = _saved_impl


# --- cleanup ---------------------------------------------------------------
try:
    os.unlink(_tmp.name)
except OSError:
    pass

print("=" * 72)
print(f"RESULT: {_passed} passed, {_failed} failed")
print("=" * 72)
raise SystemExit(1 if _failed else 0)
