"""Agentic layer REAL smoke — DeepSeek V4 Pro via the TokenDance gateway.

This is the **required "the real path actually works" evidence** for the agentic
layer.  The offline verify_*.py suites prove the CODE paths with a scripted stub;
this script proves the LIVE tool-calling protocol against a real model:

  0  PROTOCOL  — a direct call_chat_tools turn that asks the model to call two
                 tools, then feeds back one role:"tool" result PER tool_call.id.
                 This is the #1 real-path failure mode: DeepSeek 400s the next
                 request if any tool_call.id is missing its matching role:"tool"
                 result.  We surface whether the model emitted PARALLEL calls and
                 prove the round-trip doesn't error.
  1  DECODE    — orchestrator.decode_bet_agentic on a real ticker: the agent must
                 investigate with ≥1 tool round and submit a plan (card mode tagged
                 agentic_*), proving the full agent loop works end to end.
  2  WHAT-IF   — answer_followup with a "what if WACC=9%" question → a revision
                 with a before→after diff that actually moved.
  3  REVISE    — build_revised_card + save → a provenanced derived card; the
                 parent is untouched.

SAFETY — this spends real money, so it is double-gated:
  * It does NOTHING (prints a plan, exit 0) unless you pass --execute.
  * Even with --execute it refuses (exit 2) unless TOKENDANCE_API_KEY is set and
    the active provider can actually tool-call.

Usage (do this yourself, with your key in .env):
    python smoke_agentic.py                      # dry plan, ZERO network/cost
    python smoke_agentic.py --execute            # REAL DeepSeek smoke (small spend)
    python smoke_agentic.py --execute --ticker AAPL
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# This is the DeepSeek smoke: default the provider to tokendance unless the
# operator already pinned one in the OS env. Must be set BEFORE importing client
# (client reads LLM_PROVIDER at import time). OFFLINE_MODE off — we want the live
# path. The key itself comes from .env (client.load_dotenv()).
os.environ.setdefault("LLM_PROVIDER", "tokendance")
os.environ["OFFLINE_MODE"] = "false"

import client  # noqa: E402

_passed = 0
_failed = 0


def check(name: str, cond: bool, detail: str = "") -> bool:
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
    extra = f"  | {detail}" if detail else ""
    print(f"[{'PASS' if cond else 'FAIL'}] {name}{extra}")
    return bool(cond)


# --- a couple of harmless real tools for the protocol probe ----------------
_PROBE_TOOLS = [
    {"type": "function", "function": {
        "name": "get_price", "description": "Return the current price of a ticker.",
        "parameters": {"type": "object",
                       "properties": {"ticker": {"type": "string"}},
                       "required": ["ticker"]}}},
    {"type": "function", "function": {
        "name": "get_sector", "description": "Return the sector of a ticker.",
        "parameters": {"type": "object",
                       "properties": {"ticker": {"type": "string"}},
                       "required": ["ticker"]}}},
]


def protocol_probe() -> None:
    """Step 0 — prove the live tool-calling round-trip: tool_calls → one
    role:"tool" per id → a second turn that does NOT 400. Reports parallelism."""
    print("\n=== STEP 0: live tool-calling protocol (parallel → role:tool) ===")
    msgs = [
        {"role": "system", "content": "You are a tool-using assistant. When asked "
         "about a stock, call BOTH get_price and get_sector (in parallel) before "
         "answering. Use the tools; do not guess."},
        {"role": "user", "content": "What is NVDA's price and sector? Call the tools."},
    ]
    resp = client.call_chat_tools(msgs, model=client.MODEL_MINI, tools=_PROBE_TOOLS,
                                  tool_choice="auto", temperature=0)
    tcs = resp.get("tool_calls") or []
    check("STEP0 model returned ≥1 tool call", len(tcs) >= 1,
          f"n_calls={len(tcs)} parallel={'yes' if len(tcs) > 1 else 'no'}")
    if not tcs:
        return
    # Echo the assistant turn, then EXACTLY one role:"tool" per tool_call.id.
    msgs.append(resp.get("assistant_message")
                or {"role": "assistant", "content": resp.get("content") or ""})
    fake = {"get_price": {"ticker": "NVDA", "price": 900.0},
            "get_sector": {"ticker": "NVDA", "sector": "Semiconductors"}}
    for tc in tcs:
        msgs.append({"role": "tool", "tool_call_id": tc.get("id"),
                     "content": json.dumps(fake.get(tc.get("name"), {"ok": True}))})
    try:
        resp2 = client.call_chat_tools(msgs, model=client.MODEL_MINI,
                                       tools=_PROBE_TOOLS, tool_choice="auto",
                                       temperature=0)
        ok = bool((resp2.get("content") or "").strip()) or bool(resp2.get("tool_calls"))
        check("STEP0 feeding role:tool per id → 2nd turn succeeds (no 400 — THE real "
              "path failure mode)", ok, f"reply={str(resp2.get('content'))[:48]!r}")
    except Exception as exc:
        check("STEP0 feeding role:tool per id → 2nd turn succeeds (no 400)", False,
              f"raised {type(exc).__name__}: {exc}")


def run_execute(ticker: str) -> int:
    import db
    import decoder
    import orchestrator

    print("=" * 72)
    print(f"Agentic REAL smoke — provider={client.PROVIDER} model={client.MODEL_MINI}")
    print("=" * 72)

    # 0 — live protocol round-trip.
    protocol_probe()

    # fundamentals: real yfinance (this is the real path).
    print(f"\n[fetch] real fundamentals for {ticker} (yfinance) ...")
    try:
        f = decoder.fetch_fundamentals(ticker)
    except Exception as exc:
        print(f"  ⚠ fundamentals fetch failed: {exc}")
        f = None
    if f is None or not getattr(f, "current_price", None):
        check("FETCH real fundamentals available (needed for the agentic decode)",
              False, "no fundamentals — pick a liquid --ticker and retry")
        return _summary()

    _tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    _tmp.close()
    conn = db.init_db(_tmp.name)

    # 1 — agentic decode.
    print(f"\n=== STEP 1: agentic decode {ticker} ===")
    trace_holder: dict = {}
    card = orchestrator.decode_bet_agentic("market", ticker, "zh", conn=conn)
    dd = getattr(card, "decode_detail", None) or {}
    trace = dd.get("agent_trace") or []
    n_tool = sum(1 for t in trace if t.get("tool"))
    pid = db.save_card(conn, card)
    card.card_id = pid
    check("STEP1 a valid card was produced + persisted", card is not None and pid,
          f"id={pid} bet={card.bet}")
    decode_ok = check(
        "STEP1 the AGENT drove it (mode agentic_* + ≥1 tool call in the trace)",
        str(dd.get("mode", "")).startswith("agentic_") and n_tool >= 1,
        f"mode={dd.get('mode')!r} tool_calls={n_tool}")
    if not decode_ok:
        print("      NOTE: mode not agentic_* means decode_bet_agentic fell back to "
              "the deterministic path (provider couldn't tool-call, or the model "
              "never submitted a plan). Check TOKENDANCE_API_KEY + provider.")

    # 2 — what-if follow-up.
    print(f"\n=== STEP 2: what-if follow-up (WACC=9%) ===")
    qa = orchestrator.answer_followup(
        card, "如果把 WACC 提到 9% 会怎样?隐含增长要变多少?", "zh", conn=conn)
    rev = qa.get("revision")
    print(f"      answer: {str(qa.get('answer'))[:80]}")
    print(f"      cost_usd (gateway rates unpublished → may read $0): {qa.get('cost_usd')}")
    whatif_ok = check(
        "STEP2 a what-if revision with a before→after diff that MOVED",
        isinstance(rev, dict) and rev.get("kind") == "whatif" and bool(rev.get("diff"))
        and rev["diff"][0].get("before") != rev["diff"][0].get("after"),
        f"diff={rev.get('diff') if isinstance(rev, dict) else None}")

    # 3 — revise → provenanced derived card.
    print(f"\n=== STEP 3: revise → provenanced derived card ===")
    if whatif_ok:
        derived = orchestrator.build_revised_card(card, rev)
        did = db.save_card(conn, derived)
        derived.card_id = did
        reloaded = db.get_card(conn, did)
        parent_now = db.get_card(conn, pid)
        check("STEP3 derived card persists with provenance (derived_from + diff)",
              did != pid and reloaded.derived_from == pid
              and (reloaded.decode_detail or {}).get("revision", {}).get("diff"),
              f"did={did} derived_from={reloaded.derived_from}")
        check("STEP3 parent is untouched (immutable snapshot held)",
              parent_now.derived_from is None
              and "revision" not in (parent_now.decode_detail or {}))
    else:
        check("STEP3 revise (skipped — no revision proposed in STEP2)", False)

    conn.close()
    try:
        os.unlink(_tmp.name)
    except OSError:
        pass
    return _summary()


def _summary() -> int:
    print("=" * 72)
    print(f"RESULT: {_passed} passed, {_failed} failed")
    print("=" * 72)
    return 1 if _failed else 0


def print_dry_run(ticker: str) -> None:
    print("=" * 72)
    print("Agentic REAL smoke — DRY RUN (no network, no cost)")
    print("=" * 72)
    print(f"Active provider (LLM_PROVIDER): {client.PROVIDER}")
    print(f"Tool-calling capable: {client.tool_calling_capable()}  "
          f"| API key present: {client.api_key_present()}  "
          f"| web-search capable: {client.web_search_capable()}")
    print("\nWith --execute this WOULD (small real spend on DeepSeek V4 Pro):")
    print("  0  probe the live tool-calling protocol (parallel calls → role:tool, no 400)")
    print(f"  1  agentic-decode {ticker} (agent picks the X-ray plan via tools)")
    print("  2  ask a what-if (WACC=9%) → a before→after revision diff")
    print("  3  save the confirmed revision as a provenanced derived card")
    print("\nTo run it (put TOKENDANCE_API_KEY in .env first):")
    print(f"    python smoke_agentic.py --execute --ticker {ticker}")
    print("=" * 72)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Agentic layer REAL smoke (DeepSeek V4 "
                                "Pro via TokenDance). Dry-run by default.")
    p.add_argument("--execute", action="store_true",
                   help="ACTUALLY call the real model (spends a little). Without it, "
                        "only prints the plan.")
    p.add_argument("--ticker", default="NVDA",
                   help="ticker to decode in the smoke (default NVDA).")
    args = p.parse_args(argv)

    if not args.execute:
        print_dry_run(args.ticker)
        return 0

    # Hard refuse to spend without a key / a tool-calling provider.
    if not client.api_key_present():
        print(f"❌ {client.API_KEY_ENV} not set (provider={client.PROVIDER}). "
              "Put it in .env, then re-run with --execute.")
        return 2
    if not client.tool_calling_capable():
        print(f"❌ provider {client.PROVIDER} can't do OpenAI tool calling. "
              "Set LLM_PROVIDER=tokendance (DeepSeek V4 Pro) and retry.")
        return 2
    return run_execute(args.ticker)


if __name__ == "__main__":
    sys.exit(main())
