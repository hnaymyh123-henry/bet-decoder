"""orchestrator.answer_followup + build_revised_card verification.

Deterministic, zero API/network: a scripted tool-calling stub drives the Q&A agent
through the four question shapes (why / what-if / compare / bear-case), and proves
a confirmed what-if becomes a PROVENANCED derived card (parent untouched, both
coexist same-day). Web-gated tools stay honest-empty (no fabrication).

Run:  MIROMIND_API_KEY="" OFFLINE_MODE=1 "/c/Users/Henry Ma/miniconda3/python.exe" verify_qa_revise.py
"""
from __future__ import annotations

import json

import client
import db
import decoder
import orchestrator

_passed = 0
_failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  | {detail}" if detail else ""))


print("=" * 72)
print("answer_followup + build_revised_card verification")
print("=" * 72)

COST = decoder.Fundamentals(
    ticker="COST", current_price=900.0, revenue_ttm=255e9, net_income_ttm=7.4e9,
    ebitda_ttm=11e9, fcf_ttm=6e9, book_equity=23e9, eps_ttm=16.6,
    shares_outstanding=0.443e9, net_debt=-5e9, beta=0.8, growth_rate=0.09,
    industry="Discount Stores")
AMD = decoder.Fundamentals(
    ticker="AMD", current_price=170.0, revenue_ttm=23e9, net_income_ttm=1.6e9,
    ebitda_ttm=3e9, fcf_ttm=3e9, book_equity=57e9, eps_ttm=1.0,
    shares_outstanding=1.62e9, net_debt=-1e9, beta=1.7, growth_rate=0.3,
    industry="Semiconductors")
FF = lambda t: {"COST": COST, "AMD": AMD}[t.upper()]  # noqa: E731
HUNTER = lambda *a, **k: None  # noqa: E731


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


def make_stub(script):
    st = {"i": 0}

    def stub(messages, **kw):
        i = min(st["i"], len(script) - 1)
        st["i"] += 1
        return script[i]
    return stub


# A real decoded parent card (carries decode_detail with the implied numbers).
conn = db.init_db(":memory:")
parent = decoder.decode_bet("market", "COST", "zh", fundamentals_fn=FF, hunter=HUNTER)
pid = db.save_card(conn, parent)
parent.card_id = pid

# --- AC1: "why" question → final_answer grounded in the implied number -------
why = orchestrator.answer_followup(
    parent, "为什么隐含 P/E 这么高?", llm=make_stub([
        _resp([_tc("run_lens", {"lens": "pe"})], "查 P/E。"),
        _resp([_tc("final_answer", {"answer": "市场这个价隐含 P/E ≈ 54,押注盈利长期维持。"})]),
    ]), fundamentals_fn=FF, hunter=HUNTER)
check("AC1 'why' → answer returned, no revision",
      bool(why["answer"]) and "P/E" in why["answer"] and why["revision"] is None,
      f"answer={why['answer'][:40]!r}")
check("AC1 tool_trace records the run_lens call",
      any(t.get("tool") == "run_lens" for t in why["tool_trace"]))

# --- AC2: "what if WACC=9%" → propose_revision returns a before→after diff ----
wi = orchestrator.answer_followup(
    parent, "如果 WACC 提到 9% 会怎样?", llm=make_stub([
        _resp([_tc("propose_revision",
                   {"solve_for": "revenue_cagr_5y", "overrides": {"wacc": 0.09},
                    "summary": "WACC 提到 9%,需要更高的隐含增长来支撑同样的价格。"})]),
    ]), fundamentals_fn=FF, hunter=HUNTER)
rev = wi["revision"]
check("AC2 what-if → a revision is proposed (NOT yet persisted)",
      isinstance(rev, dict) and rev.get("kind") == "whatif"
      and rev["params"]["overrides"] == {"wacc": 0.09})
check("AC2 revision carries a before→after diff that actually moved",
      rev.get("diff") and rev["diff"][0]["before"] != rev["diff"][0]["after"],
      f"diff={rev.get('diff')}")
check("AC2 proposing a revision did NOT create a new card yet",
      len(db.list_cards(conn)) == 1)

# --- AC3: confirm → a PROVENANCED derived card; parent untouched -------------
derived = orchestrator.build_revised_card(parent, rev)
did = db.save_card(conn, derived)
derived.card_id = did
check("AC3 derived card persists as its own card alongside the parent (same day)",
      did != pid and len(db.list_cards(conn)) == 2)
rd = db.get_card(conn, did)
check("AC3 derived card records provenance (derived_from + derivation diff)",
      rd.derived_from == pid and rd.derivation_kind == "whatif"
      and rd.decode_detail.get("revision", {}).get("diff"))
rp = db.get_card(conn, pid)
check("AC3 parent is untouched (no derivation, original mode preserved)",
      rp.derived_from is None
      and rp.decode_detail.get("mode") == parent.decode_detail.get("mode")
      and "revision" not in (rp.decode_detail or {}))

# --- AC4: "compare to AMD" → compare_subjects then answer --------------------
cmp = orchestrator.answer_followup(
    parent, "跟 AMD 比一比", llm=make_stub([
        _resp([_tc("compare_subjects", {"ticker_b": "AMD"})], "对比两者。"),
        _resp([_tc("final_answer", {"answer": "COST 是稳健盈利,AMD 是高增长叙事。"})]),
    ]), fundamentals_fn=FF, hunter=HUNTER)
check("AC4 'compare' → compare_subjects called + answer returned",
      any(t.get("tool") == "compare_subjects" for t in cmp["tool_trace"])
      and bool(cmp["answer"]) and cmp["revision"] is None)

# --- AC5: "bear case" on a web-incapable provider → no fabrication, no crash --
_orig = client.WEB_SEARCH_CAPABLE
try:
    client.WEB_SEARCH_CAPABLE = False
    bear = orchestrator.answer_followup(
        parent, "最强的熊市论据是什么?", llm=make_stub([
            _resp([_tc("research_narrative", {})], "找多空论战。"),
            _resp([_tc("final_answer",
                       {"answer": "目前 provider 无法联网核实,这里不编造来源。"})]),
        ]), fundamentals_fn=FF, hunter=HUNTER)
    check("AC5 web-gated bear-case handled gracefully (answer, no crash)",
          bool(bear["answer"]) and any(t.get("tool") == "research_narrative"
                                       for t in bear["tool_trace"]))
finally:
    client.WEB_SEARCH_CAPABLE = _orig

# --- AC6: llm unavailable → graceful, no crash ------------------------------
none_r = orchestrator.answer_followup(parent, "x", llm=None, fundamentals_fn=FF)
check("AC6 no LLM → graceful 'unavailable' answer, revision None",
      bool(none_r["answer"]) and none_r["revision"] is None)

print("=" * 72)
print(f"RESULT: {_passed} passed, {_failed} failed")
print("=" * 72)
raise SystemExit(1 if _failed else 0)
