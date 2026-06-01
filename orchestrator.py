"""orchestrator.py — the agentic decode (Phase C).

`decode_bet_agentic` lets an LLM decide HOW to X-ray a bet: it investigates the
company via the agent_tools registry (fundamentals, lenses, anchor decomposition,
what-if DCF) and then calls `submit_decode_plan` with its chosen plan. We hand that
plan to `decoder.decode_bet(_plan_override=...)`, which reuses the SAME assemblers —
so the card + decode_detail are shape-identical to a deterministic decode, just
agent-chosen. Every decision/tool-call streams as a real ActivityEvent (genuine
agency, not the old fixed-tree narration).

Airtight fallback: anything that goes wrong (no tool calling, no plan, an
exception, offline) degrades to the deterministic `decoder.decode_bet`.
"""
from __future__ import annotations

import json
import os

import agent_tools
import client

# Tools exposed during a decode (investigation only; evidence/narrative are
# attached by decode_bet itself, and compare is a Q&A concern).
_DECODE_TOOLS = ["get_fundamentals", "classify_subject", "plan_lenses", "run_lens",
                 "run_all_applicable_lenses", "run_anchor_decompose",
                 "whatif_reverse_dcf"]

# The control tool that ends the loop with the agent's chosen plan.
_SUBMIT_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_decode_plan",
        "description": ("Call this ONCE when you've decided how to X-ray the bet. "
                        "Ends the analysis and builds the Bet Card from your plan."),
        "parameters": {
            "type": "object",
            "properties": {
                "mode": {"type": "string",
                         "enum": ["traditional", "anchor_primary", "anchor_fallback"],
                         "description": "traditional = reverse-solve a valuation "
                         "multiple (set primary_key); anchor_primary/anchor_fallback "
                         "= decompose the price into base business value + narrative/"
                         "option/TAM components (for narrative-priced names where "
                         "multiples can't explain the price)."},
                "primary_key": {"type": "string",
                                "description": "for traditional mode, the primary "
                                "lens: pe|ps|ev_ebitda|p_fcf|p_b|peg|dcf"},
                "cross_keys": {"type": "array", "items": {"type": "string"},
                               "description": "cross-reference lenses to also show"},
                "reason": {"type": "string",
                           "description": "one sentence: why this plan fits THIS "
                           "company (what you found)."},
            },
            "required": ["mode", "reason"],
        },
    },
}

_SYSTEM_PROMPT = (
    "You are the decode planner for Bet Decoder. Your job: decide HOW to reverse-"
    "decode what the market price of a stock implicitly assumes — you are NOT "
    "writing a report. Investigate the company with the tools (fundamentals, "
    "classify, lenses, anchor decomposition, what-if DCF), reason briefly about "
    "which valuation frame actually fits THIS company, then call submit_decode_plan "
    "exactly once. Guidance: a profitable, steadily-growing company → a multiple "
    "(P/E, etc.); a narrative/AI-composite name whose price multiples can't explain "
    "→ anchor mode. Keep tool use tight (a few calls). Be honest: if a lens has no "
    "solution, that itself is signal. Respond in the user's language for the reason."
)

_SENTINEL = object()


def _resolve_llm(llm):
    """sentinel → the real tool-calling client (only if capable + keyed + online);
    None → deterministic (no LLM); a callable → use it (test stub)."""
    if llm is _SENTINEL:
        if os.environ.get("OFFLINE_MODE", "").lower() in ("1", "true", "yes"):
            return None
        if not client.tool_calling_capable() or not client.api_key_present():
            return None
        return client.call_chat_tools
    return llm  # None (deterministic) or an injected callable both pass through


def _emit(emit, ticker, kind, text, payload=None):
    if not callable(emit):
        return
    try:
        emit({"phase": "agent", "kind": kind, "subject": ticker,
              "text": text, "payload": payload})
    except Exception:
        pass


def _task_prompt(ticker, f, lang) -> str:
    cp = f.current_price
    return (f"Decode the market bet on {ticker} at its current price "
            f"≈ {cp}. Industry: {f.industry or 'n/a'}. Decide the X-ray plan and "
            f"call submit_decode_plan. Write 'reason' in "
            f"{'Chinese' if lang == 'zh' else 'English'}.")


def decode_bet_agentic(source_type, source_input, lang: str = "zh", emit=None, *,
                       llm=_SENTINEL, fundamentals_fn=None, conn=None, hunter=None,
                       narrator=None, max_rounds: int = 8,
                       max_tool_calls: int = 16):
    """Agent-driven decode. Falls back to decoder.decode_bet on any failure."""
    import decoder
    ff = fundamentals_fn or decoder.fetch_fundamentals

    def _deterministic():
        return decoder.decode_bet(source_type, source_input, lang, emit,
                                  fundamentals_fn=ff, conn=conn, hunter=hunter,
                                  narrator=narrator)

    # Only single MARKET bets get the agentic decode; portfolio/other → deterministic.
    if source_type != decoder.SOURCE_MARKET:
        return _deterministic()
    llm_fn = _resolve_llm(llm)
    if llm_fn is None:
        return _deterministic()

    ticker = decoder._coerce_subject(source_input)
    try:
        f = ff(ticker)
    except Exception:
        f = None
    if f is None or not f.current_price or f.current_price <= 0:
        return _deterministic()  # nothing to anchor on → deterministic insufficient

    ctx = agent_tools.ToolContext(
        ticker=ticker, fundamentals=f, anchor_price=f.current_price, conn=conn,
        hunter=hunter, narrator=narrator, lang=lang, emit=emit, fundamentals_fn=ff)
    tools_spec = agent_tools.openai_tools_spec(_DECODE_TOOLS) + [_SUBMIT_TOOL]
    messages = [{"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": _task_prompt(ticker, f, lang)}]
    trace: list[dict] = []
    plan = None
    _emit(emit, ticker, "decision", f"Agent 接手 {ticker} 的解码:先调研再定 X 光方案")

    try:
        calls_made = 0
        for rnd in range(max_rounds):
            resp = llm_fn(messages, model=client.MODEL_MINI, tools=tools_spec,
                          tool_choice="auto", temperature=0)
            content = (resp.get("content") or "").strip()
            if content:
                _emit(emit, ticker, "decision", content)
                trace.append({"round": rnd, "thought": content})
            tcs = resp.get("tool_calls") or []
            if not tcs:
                break  # the model answered without submitting a plan
            messages.append(resp.get("assistant_message")
                            or {"role": "assistant", "content": content})
            done = False
            for tc in tcs:
                calls_made += 1
                raw = tc.get("arguments_raw")
                if isinstance(raw, str):
                    try:
                        args = client.parse_loose_json(raw)
                    except Exception:
                        args = {}
                else:
                    args = raw or {}
                if not isinstance(args, dict):
                    args = {}
                name = tc.get("name")
                if name == "submit_decode_plan":
                    plan = {"mode": args.get("mode"),
                            "primary_key": args.get("primary_key"),
                            "cross_keys": args.get("cross_keys") or [],
                            "reason": args.get("reason")}
                    result = {"submitted": True, "plan": plan}
                    done = True
                else:
                    result = agent_tools.dispatch(name, args, ctx)
                trace.append({"tool": name, "args": args})
                messages.append({"role": "tool", "tool_call_id": tc.get("id"),
                                 "content": json.dumps(result, ensure_ascii=False,
                                                       default=str)})
            if done or calls_made >= max_tool_calls:
                break
    except client.ToolCallingUnsupported:
        return _deterministic()
    except Exception as exc:  # never let the agent crash a decode
        _emit(emit, ticker, "decision", f"agent 异常({exc})→ 回退确定性解码")
        return _deterministic()

    # Build the card: agent plan if submitted, else deterministic. Either way the
    # card is shape-identical (decode_bet reuses the same assemblers).
    card = decoder.decode_bet(source_type, source_input, lang, emit,
                              fundamentals_fn=ff, conn=conn, hunter=hunter,
                              narrator=narrator,
                              _plan_override=plan if plan else None)
    dd = getattr(card, "decode_detail", None)
    if isinstance(dd, dict):
        dd["agentic"] = True
        dd["agent_trace"] = trace
        if plan:
            dd["mode"] = "agentic_" + str(dd.get("mode", "decode"))
    _emit(emit, ticker, "decision",
          f"方案落地:{(plan or {}).get('reason') or '未提交方案,回退确定性'}")
    return card
