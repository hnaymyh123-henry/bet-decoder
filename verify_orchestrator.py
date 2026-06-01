"""orchestrator.decode_bet_agentic verification — deterministic, zero API/network.

Drives the agent loop with a SCRIPTED tool-calling stub (no network): the agent
investigates via tools then submits a plan, and the resulting card is proven
shape-identical (parity) to the deterministic decode of the same fixture. Also
covers every fallback path (no plan / exception / ToolCallingUnsupported / llm=None
/ portfolio) → deterministic decode.

Run:  MIROMIND_API_KEY="" OFFLINE_MODE=1 "/c/Users/Henry Ma/miniconda3/python.exe" verify_orchestrator.py
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
print("orchestrator.decode_bet_agentic verification")
print("=" * 72)

COST = decoder.Fundamentals(
    ticker="COST", current_price=900.0, revenue_ttm=255e9, net_income_ttm=7.4e9,
    ebitda_ttm=11e9, fcf_ttm=6e9, book_equity=23e9, eps_ttm=16.6,
    shares_outstanding=0.443e9, net_debt=-5e9, beta=0.8, growth_rate=0.09,
    industry="Discount Stores")
FF = lambda t: COST          # noqa: E731  (no network)
HUNTER = lambda *a, **k: None  # noqa: E731  (evidence honest-empty, zero cost)


def _tc(name, args):
    return {"id": f"call_{name}", "name": name, "arguments_raw": json.dumps(args)}


def _resp(tcs, content=""):
    return {
        "content": content, "model": "stub", "usage": None, "cost_usd": 0.0,
        "tool_calls": tcs,
        "assistant_message": {
            "role": "assistant", "content": content,
            "tool_calls": [{"id": t["id"], "type": "function",
                            "function": {"name": t["name"],
                                         "arguments": t["arguments_raw"]}}
                           for t in tcs]},
        "finish_reason": "tool_calls" if tcs else "stop",
    }


def make_stub(script):
    state = {"i": 0}

    def stub(messages, **kw):
        i = min(state["i"], len(script) - 1)
        state["i"] += 1
        return script[i]
    return stub


# --- AC1: happy path — agent investigates then submits a traditional plan ----
HAPPY = [
    _resp([_tc("get_fundamentals", {})], "看一下基本面。"),
    _resp([_tc("run_lens", {"lens": "pe"})], "盈利稳定,P/E 是合适的镜头。"),
    _resp([_tc("submit_decode_plan",
               {"mode": "traditional", "primary_key": "pe", "cross_keys": ["dcf"],
                "reason": "稳健盈利的零售商 → 用 P/E"})], "提交方案。"),
]
events = []
ag = orchestrator.decode_bet_agentic(
    "market", "COST", "zh", emit=lambda e: events.append(e),
    llm=make_stub(HAPPY), fundamentals_fn=FF, hunter=HUNTER)
det = decoder.decode_bet("market", "COST", "zh", fundamentals_fn=FF, hunter=HUNTER)

check("AC1 agentic decode returns a SINGLE market card", ag.card_kind == db.SINGLE,
      f"kind={ag.card_kind}")
check("AC1 mode tagged agentic_*", ag.decode_detail.get("mode") == "agentic_traditional",
      f"mode={ag.decode_detail.get('mode')}")
check("AC1 agent_trace recorded (thoughts + tool calls)",
      ag.decode_detail.get("agentic") is True
      and len(ag.decode_detail.get("agent_trace") or []) >= 3,
      f"trace_len={len(ag.decode_detail.get('agent_trace') or [])}")
check("AC1 real agency streamed as ActivityEvents (decision events emitted)",
      any(e.get("kind") == "decision" for e in events)
      and any("tool" == e.get("phase") for e in events),
      f"{len(events)} events")

# --- AC2: PARITY — agentic card matches the deterministic decode -------------
check("AC2 parity: same primary lens (both pe)",
      ag.decode_detail["primary_lens"]["lens"]
      == det.decode_detail["primary_lens"]["lens"] == "pe")
check("AC2 parity: identical bet (implied value)",
      ag.bet == det.bet, f"agentic={ag.bet} deterministic={det.bet}")

# --- AC3: agent submits an ANCHOR plan → anchor card -------------------------
ANCHOR = [
    _resp([_tc("classify_subject", {})], "判断是不是叙事定价。"),
    _resp([_tc("submit_decode_plan",
               {"mode": "anchor_primary", "reason": "按叙事/锚定拆解"})], "提交。"),
]
agA = orchestrator.decode_bet_agentic("market", "COST", "zh", llm=make_stub(ANCHOR),
                                      fundamentals_fn=FF, hunter=HUNTER)
check("AC3 anchor plan → anchor card (agentic_anchor_primary + anchor_mode detail)",
      agA.decode_detail.get("mode") == "agentic_anchor_primary"
      and "anchor_mode" in agA.decode_detail,
      f"mode={agA.decode_detail.get('mode')}")

# --- AC4: fallbacks all degrade to the deterministic decode ------------------
det_mode = det.decode_detail.get("mode")  # "traditional"

# (a) llm=None → deterministic, NOT tagged agentic
d_none = orchestrator.decode_bet_agentic("market", "COST", "zh", llm=None,
                                         fundamentals_fn=FF, hunter=HUNTER)
check("AC4a llm=None → deterministic decode (mode 'traditional', not agentic_*)",
      d_none.decode_detail.get("mode") == det_mode
      and not str(d_none.decode_detail.get("mode")).startswith("agentic"))

# (b) stub raises → deterministic
def _boom(messages, **kw):
    raise RuntimeError("model exploded")
d_err = orchestrator.decode_bet_agentic("market", "COST", "zh", llm=_boom,
                                        fundamentals_fn=FF, hunter=HUNTER)
check("AC4b agent exception → deterministic fallback (no crash)",
      d_err.decode_detail.get("mode") == det_mode)

# (c) ToolCallingUnsupported → deterministic
def _tcu(messages, **kw):
    raise client.ToolCallingUnsupported("provider has no tools")
d_tcu = orchestrator.decode_bet_agentic("market", "COST", "zh", llm=_tcu,
                                        fundamentals_fn=FF, hunter=HUNTER)
check("AC4c ToolCallingUnsupported → deterministic fallback",
      d_tcu.decode_detail.get("mode") == det_mode)

# (d) model answers with NO tool call → no plan submitted → deterministic card
d_noplan = orchestrator.decode_bet_agentic(
    "market", "COST", "zh", llm=make_stub([_resp([], "我觉得是 P/E")]),
    fundamentals_fn=FF, hunter=HUNTER)
check("AC4d no plan submitted → deterministic card (mode not agentic_*)",
      not str(d_noplan.decode_detail.get("mode")).startswith("agentic"),
      f"mode={d_noplan.decode_detail.get('mode')}")

# (e) portfolio source_type → deterministic portfolio decode (agent decode is
#     single-market only)
d_pf = orchestrator.decode_bet_agentic("portfolio", "COST, AMD", "zh",
                                       llm=make_stub(HAPPY), fundamentals_fn=FF,
                                       hunter=HUNTER)
check("AC4e portfolio → deterministic portfolio card (not agentic)",
      d_pf.card_kind == db.PORTFOLIO)

print("=" * 72)
print(f"RESULT: {_passed} passed, {_failed} failed")
print("=" * 72)
raise SystemExit(1 if _failed else 0)
