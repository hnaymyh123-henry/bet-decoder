"""5-act demo pre-run script (Issue #8).

The demo MUST never hit a live Deep Research call on stage (budget = $100 +
100 calls; one mini evidence call ≈ $3.21, one flagship market-narrative call ≈
$8.07).  So we pre-run everything once, ahead of time, and let the caches serve
the demo for free:

    llm_cache(evidence)   — one brief per implied assumption per ticker
    llm_cache(narrative)  — the live bull/bear debate per SINGLE MARKET card
    llm_cache(synthesis)  — the cross-card relation/narrative blob
    activity_logs         — the agent reasoning stream, for SSE replay. Acts 1-2
                            decode AGENTICALLY → a REAL agent decode trace on a
                            tool-calling provider (TokenDance/DeepSeek); on the
                            default miromind provider it airtight-falls-back.
    cache/price_history    — monthly closes for the chart (file cache)

This script is **dry-run by default**.  With no flag it touches NOTHING on the
network and writes NOTHING to the DB: it prints exactly which cards it WOULD
decode, the per-act and total cost estimate, and the caches each act fills, then
asserts the total fits the budget.  Only ``--execute`` actually runs the real
engines (real yfinance + real MiroMind) and populates the caches.

    python prerun_demo.py            # dry-run: plan + cost, ZERO network/DB
    python prerun_demo.py --execute  # REAL pre-run (spends budget) — run this
                                     # yourself, ahead of the demo, with a key set.

5 acts (BET_DECODER_VISION.md §4):
    1  NVDA   market card        "原来市场押了 3 件事"
    2  TSLA   market card        a contrast card (anchor/no-DCF-solution case)
    3  (recap — no new decode)
    4  8-ticker portfolio card   "我的组合 73% 都在 BET AI infra"
    5  cross-card synthesis      "我的组合跟 Goldman 同源" (the wow moment)
"""
from __future__ import annotations

import argparse
import sys

# Make stdout/stderr UTF-8 so the demo plan (中文 + ✅) prints on Windows gbk
# consoles instead of raising UnicodeEncodeError.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

import evidence

# ---------------------------------------------------------------------------
# The 5-act demo dataset.  Tickers are decoded as MARKET cards; the portfolio is
# one PORTFOLIO card over 8 holdings; the synthesis stitches the single cards +
# portfolio together.
# ---------------------------------------------------------------------------

# Act 1 + Act 2 single market cards (subject → label).
DEMO_SINGLES = [
    ("NVDA", "act1", "NVDA 市场卡 — 市场对 AI infra 的隐含 bet"),
    ("TSLA", "act2", "对照卡 — 传统估值难以解释的锚定/叙事定价"),
]

# Act 4 portfolio: 8 holdings (equal-ish weights; the demo narrative is that the
# basket is far more concentrated in the AI-infra theme than it looks).
DEMO_PORTFOLIO = [
    {"ticker": "NVDA", "weight_pct": 18.0},
    {"ticker": "MSFT", "weight_pct": 16.0},
    {"ticker": "GOOG", "weight_pct": 14.0},
    {"ticker": "AVGO", "weight_pct": 12.0},
    {"ticker": "AMD",  "weight_pct": 12.0},
    {"ticker": "TSM",  "weight_pct": 12.0},
    {"ticker": "AMZN", "weight_pct": 10.0},
    {"ticker": "META", "weight_pct": 6.0},
]

# Each first-decode of a ticker hunts up to ASSUMPTIONS_PER_TICKER_FIRST_DECODE
# Deep Research briefs (primary + cross lenses).  This MUST match what decode_bet
# actually does — an earlier version hard-coded "1" here and understated the bill
# ~3x.  We charge each distinct ticker once (cache-aware; the 2nd decode is $0).
ASSUMPTIONS_PER_CARD = evidence.ASSUMPTIONS_PER_TICKER_FIRST_DECODE  # currently 3
UPPER_BOUND_ASSUMPTIONS_PER_CARD = evidence.ASSUMPTIONS_PER_TICKER_FIRST_DECODE  # == base today

# Synthesis is chat-mode only (NOT Deep Research).  A mini chat call is a tiny
# fraction of a Deep Research call; we budget it conservatively at one mini
# evidence-call-equivalent so the total stays an honest upper bound.
SYNTHESIS_COST_UPPER_USD = evidence.COST_PER_EVIDENCE_MINI  # conservative ceiling

# Step 4 market narrative (decoder._attach_market_narrative) is a FLAGSHIP Deep
# Research call.  It runs for every SINGLE MARKET card and is hard-guarded OFF for
# portfolios/holdings ($0 there) — so it does NOT scale with the 8-holding basket;
# only the single market acts (1 & 2) carry it, ~1 flagship call each.
NARRATIVE_COST_USD = evidence.COST_PER_EVIDENCE_FLAGSHIP  # flagship deep research / single card

BUDGET_USD = 100.0
BUDGET_CALLS = 100


# ---------------------------------------------------------------------------
# Cost estimation (pure arithmetic — never calls anything).
# ---------------------------------------------------------------------------

def estimate_plan() -> dict:
    """Build the full cost + cache-fill plan for all 5 acts.  No side effects."""
    n_singles = len(DEMO_SINGLES)
    n_portfolio_tickers = len(DEMO_PORTFOLIO)
    # Distinct tickers that get a first-decode evidence hunt.  The portfolio
    # decodes each holding once; tickers shared with the single cards (NVDA) are
    # cached by the FIRST decode, so they cost $0 the second time.
    single_tickers = {t for t, _, _ in DEMO_SINGLES}
    portfolio_tickers = {h["ticker"] for h in DEMO_PORTFOLIO}
    distinct_tickers = single_tickers | portfolio_tickers
    n_distinct = len(distinct_tickers)
    shared = sorted(single_tickers & portfolio_tickers)

    # Per-act evidence-call counts (cache-aware: a ticker decoded earlier is free
    # later).  We walk the acts in order and only charge a ticker the first time.
    seen: set[str] = set()

    def charge(tickers, per_card):
        new = [t for t in tickers if t not in seen]
        seen.update(tickers)
        return len(new) * per_card, new

    act1_calls, act1_new = charge(["NVDA"], ASSUMPTIONS_PER_CARD)
    act2_calls, act2_new = charge(["TSLA"], ASSUMPTIONS_PER_CARD)
    # Act 4 portfolio: legs are decoded for their exposure but NOT evidence-hunted
    # (decoder passes _SKIP_EVIDENCE for legs — cost discipline).  So the portfolio
    # adds ZERO evidence calls; we still note which tickers it newly decodes.
    pf_tickers = [h["ticker"] for h in DEMO_PORTFOLIO]
    seen.update(pf_tickers)
    act4_calls = 0

    per = evidence.COST_PER_EVIDENCE_MINI
    acts = [
        {
            "act": 1, "kind": "decode/market", "subject": "NVDA",
            "new_evidence_calls": act1_calls,
            "narrative_calls": 1, "narrative_cost_usd": round(NARRATIVE_COST_USD, 2),
            "cost_usd": round(act1_calls * per + NARRATIVE_COST_USD, 2),
            "fills": ["llm_cache(evidence)", "llm_cache(narrative)",
                      "activity_logs", "cache/price_history"],
            "note": "市场卡(agentic 解码:tool-calling provider 上记录真实 agent 推理 trace 供 $0 回放;miromind 气密回退确定性);首解 hunt 证据 + flagship 市场叙事(deep research)",
        },
        {
            "act": 2, "kind": "decode/market", "subject": "TSLA",
            "new_evidence_calls": act2_calls,
            "narrative_calls": 1, "narrative_cost_usd": round(NARRATIVE_COST_USD, 2),
            "cost_usd": round(act2_calls * per + NARRATIVE_COST_USD, 2),
            "fills": ["llm_cache(evidence)", "llm_cache(narrative)",
                      "activity_logs", "cache/price_history"],
            "note": "对照卡(agentic 解码);锚定/叙事定价拆解 + flagship 市场叙事",
        },
        {
            "act": 3, "kind": "recap", "subject": "(无新解码)",
            "new_evidence_calls": 0, "narrative_calls": 0, "narrative_cost_usd": 0.0,
            "cost_usd": 0.0,
            "fills": [],
            "note": "叙事递进幕,复用已解码卡,无成本",
        },
        {
            "act": 4, "kind": "decode/portfolio", "subject": f"{n_portfolio_tickers} 持仓组合",
            "new_evidence_calls": act4_calls,
            # narrative is hard-guarded OFF for portfolios (decoder) → $0, does NOT
            # scale with holdings.
            "narrative_calls": 0, "narrative_cost_usd": 0.0,
            "cost_usd": round(act4_calls * per, 2),
            "fills": ["activity_logs"],
            "note": (f"逐股解码聚合({n_portfolio_tickers} 持仓);"
                     "逐股证据已砍($0,成本纪律)+ 组合卡无 flagship 叙事($0)"),
        },
        {
            "act": 5, "kind": "synthesize", "subject": "跨卡综合(单股×组合)",
            "new_evidence_calls": 0, "narrative_calls": 0, "narrative_cost_usd": 0.0,
            "cost_usd": round(SYNTHESIS_COST_UPPER_USD, 2),
            "fills": ["llm_cache(synthesis)", "activity_logs"],
            "note": "chat 模式(非 Deep Research);成本远低于一次证据调用,按上限计",
        },
    ]

    evidence_calls = act1_calls + act2_calls + act4_calls
    evidence_cost = round(evidence_calls * per, 2)
    # Step 4 market narrative: one flagship Deep Research call per SINGLE MARKET
    # card (acts 1 & 2 only — portfolios are guarded off, so it does NOT scale with
    # the basket).  This was previously omitted entirely, understating the bill.
    narrative_calls = n_singles
    narrative_cost = round(narrative_calls * NARRATIVE_COST_USD, 2)
    total_cost = round(evidence_cost + SYNTHESIS_COST_UPPER_USD + narrative_cost, 2)
    total_calls = evidence_calls + 1 + narrative_calls  # +1 synthesis chat call

    # Upper-bound (paranoid) estimate: only SINGLE market cards hunt evidence now
    # (portfolio legs are skipped), each up to the full primary+cross set.
    # Narrative is likewise singles-only.
    ub_distinct_calls = n_singles * UPPER_BOUND_ASSUMPTIONS_PER_CARD
    ub_cost = round(ub_distinct_calls * per + SYNTHESIS_COST_UPPER_USD + narrative_cost, 2)
    ub_calls = ub_distinct_calls + 1 + narrative_calls

    return {
        "acts": acts,
        "n_singles": n_singles,
        "n_portfolio_tickers": n_portfolio_tickers,
        "n_distinct_tickers": n_distinct,
        "distinct_tickers": sorted(distinct_tickers),
        "shared_tickers": shared,
        "evidence_calls": evidence_calls,
        "evidence_cost_usd": evidence_cost,
        "synthesis_cost_usd": round(SYNTHESIS_COST_UPPER_USD, 2),
        "narrative_calls": narrative_calls,
        "narrative_cost_usd": narrative_cost,
        "narrative_cost_per_call_usd": round(NARRATIVE_COST_USD, 2),
        "total_cost_usd": total_cost,
        "total_calls": total_calls,
        "cost_per_call_usd": per,
        "upper_bound_cost_usd": ub_cost,
        "upper_bound_calls": ub_calls,
        "budget_usd": BUDGET_USD,
        "budget_calls": BUDGET_CALLS,
        "within_budget": total_cost <= BUDGET_USD and total_calls <= BUDGET_CALLS,
        "upper_bound_within_budget": ub_cost <= BUDGET_USD
        and ub_calls <= BUDGET_CALLS,
    }


# ---------------------------------------------------------------------------
# Dry-run printer (default path).  Pure I/O — no network, no DB.
# ---------------------------------------------------------------------------

def print_dry_run(plan: dict) -> None:
    print("=" * 72)
    print("Bet Decoder · 5-act demo PRE-RUN — DRY RUN (no network, no DB writes)")
    print("=" * 72)
    print("Run with --execute to actually pre-run (spends budget). Default = plan only.\n")

    print("PLAN — what each act WOULD decode + the caches it fills:")
    print("-" * 72)
    for a in plan["acts"]:
        fills = ", ".join(a["fills"]) if a["fills"] else "(none)"
        print(f"  幕{a['act']} [{a['kind']:>17}] {a['subject']}")
        print(f"        新增证据调用: {a['new_evidence_calls']:>2}   "
              f"预估成本: ${a['cost_usd']:>6.2f}")
        print(f"        填充缓存: {fills}")
        print(f"        说明: {a['note']}")
    print("-" * 72)

    print("\nDATASET:")
    print(f"  单股市场卡 (Act 1,2): {[t for t, _, _ in DEMO_SINGLES]}")
    print(f"  组合卡 (Act 4): {plan['n_portfolio_tickers']} 持仓 "
          f"{[h['ticker'] for h in DEMO_PORTFOLIO]}")
    print(f"  跨卡综合 (Act 5): 单股卡 + 组合卡 一次综合")
    print(f"  去重后需首解的标的: {plan['n_distinct_tickers']} 个 "
          f"{plan['distinct_tickers']}")
    if plan["shared_tickers"]:
        print(f"  共享标的(第二次解码命中缓存 $0): {plan['shared_tickers']}")

    print("\nCOST ESTIMATE (mini Deep Research @ "
          f"${plan['cost_per_call_usd']:.2f}/call):")
    print(f"  证据调用: {plan['evidence_calls']} 次 × "
          f"${plan['cost_per_call_usd']:.2f} = ${plan['evidence_cost_usd']:.2f}")
    print(f"  市场叙事(flagship deep research): {plan['narrative_calls']} 次 × "
          f"${plan['narrative_cost_per_call_usd']:.2f} = ${plan['narrative_cost_usd']:.2f}  "
          f"(仅单股卡;组合卡 $0)")
    print(f"  综合调用(chat 上限): 1 次 ≈ ${plan['synthesis_cost_usd']:.2f}")
    print(f"  --------------------------------------------------")
    print(f"  预估总成本: ${plan['total_cost_usd']:.2f}   "
          f"(总调用 ~{plan['total_calls']} 次)")
    print(f"  预算: ${plan['budget_usd']:.0f} / {plan['budget_calls']} 调用")
    print(f"  在预算内: {'✅ 是' if plan['within_budget'] else '❌ 否'}  "
          f"(占预算 {plan['total_cost_usd'] / plan['budget_usd'] * 100:.1f}%)")

    print(f"\n  保守上界(每卡 hunt 全部 primary+cross 假设): "
          f"${plan['upper_bound_cost_usd']:.2f} / ~{plan['upper_bound_calls']} 调用 "
          f"→ {'✅ 仍在预算内' if plan['upper_bound_within_budget'] else '❌ 超预算'}")

    print("\nNEXT — to actually populate the demo caches (do this BEFORE the demo,")
    print("with MIROMIND_API_KEY set in .env):")
    print("    python prerun_demo.py --execute")
    print("Then the live demo serves every act from cache at $0 (no on-stage calls).")
    print("=" * 72)


# ---------------------------------------------------------------------------
# Execute path (gated behind --execute).  This is the ONLY branch that calls the
# real engines / network / DB.  It is never run by the verify suite.
# ---------------------------------------------------------------------------

def run_execute(plan: dict) -> None:  # pragma: no cover - live path, spends budget
    """REAL pre-run: decode every card + run the cross-card synthesis with the
    real MiroMind client, persisting briefs/synthesis/activity into the caches.

    Imported lazily so the dry-run path never imports the network client.
    """
    import uuid

    import activity
    import db
    import decoder
    import orchestrator
    import synthesizer

    print("⚠ EXECUTE MODE — this WILL call the real MiroMind API and spend budget.")
    print(f"  Estimated total: ${plan['total_cost_usd']:.2f} "
          f"(upper bound ${plan['upper_bound_cost_usd']:.2f})\n")

    conn = db.init_db("pricelens.db")
    decoded_ids: list[str] = []

    # Acts 1-2: single market cards, decoded AGENTICALLY so the demo can replay the
    # REAL agent reasoning ($0 SSE replay). On a tool-calling provider (TokenDance/
    # DeepSeek) decode_bet_agentic records a genuine agent decode trace into
    # activity_logs; on the default miromind provider it airtight-falls-back to the
    # deterministic decode (same card, no trace) — either way it never crashes. Each
    # is wrapped in activity.run_job (run inline on THIS thread, so sharing `conn` is
    # safe) → the reasoning persists under a job_id the front-end can replay.
    for ticker, act, label in DEMO_SINGLES:
        job_id = uuid.uuid4().hex
        print(f"[{act}] agentic-decoding market card {ticker} (job {job_id[:8]}) ...")

        def _work(emit, _t=ticker):
            return orchestrator.decode_bet_agentic("market", _t, "zh", emit=emit,
                                                   conn=conn)

        info = activity.run_job(_work, job_id=job_id, source_ref=ticker, conn=conn,
                                done_text="解码完成")
        card = info.get("result")
        if card is None:
            print(f"      ⚠ no card produced ({info.get('error')}) — skipping")
            continue
        cid = db.save_card(conn, card)
        card.card_id = cid
        decoded_ids.append(cid)
        mode = (getattr(card, "decode_detail", {}) or {}).get("mode")
        print(f"      saved card_id={cid} bet={card.bet} mode={mode} "
              f"(activity job {job_id[:8]} cached for $0 replay)")

    # Act 4: portfolio card over the 8 holdings.
    print(f"[act4] decoding portfolio card ({len(DEMO_PORTFOLIO)} holdings) ...")
    pf_card = decoder.decode_bet("portfolio", {"holdings": DEMO_PORTFOLIO}, "zh",
                                 conn=conn)
    pf_id = db.save_card(conn, pf_card)
    pf_card.card_id = pf_id
    decoded_ids.append(pf_id)
    print(f"      saved portfolio card_id={pf_id}")

    # Act 5: cross-card synthesis over the full set (single cards + portfolio).
    print(f"[act5] synthesizing across {len(decoded_ids)} cards ...")
    result = synthesizer.synthesize_cards(decoded_ids, "zh", conn=conn)
    n_rel = len(result.get("relations", []))
    print(f"      synthesis cached: {n_rel} relations, "
          f"headline={'yes' if result.get('headline_insight') else 'none'}")

    conn.close()
    print("\n✅ Pre-run complete. The demo caches are now populated for $0 replay.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="5-act demo pre-run (dry-run by default; --execute spends budget)."
    )
    parser.add_argument(
        "--execute", action="store_true",
        help="ACTUALLY run the real engines (real MiroMind API + DB writes). "
             "Without this flag the script only prints the plan + cost (zero cost).",
    )
    args = parser.parse_args(argv)

    plan = estimate_plan()

    if not args.execute:
        print_dry_run(plan)
        # Honest guardrail: signal failure if the plan ever blows the budget so a
        # CI / pre-flight check can catch a dataset that grew too large.
        return 0 if plan["within_budget"] else 2

    run_execute(plan)
    return 0


if __name__ == "__main__":
    sys.exit(main())
