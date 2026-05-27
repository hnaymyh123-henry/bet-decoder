"""PriceLens W2 end-to-end pipeline.

Flow:
  1. yfinance + reverse DCF Monte Carlo (reverse_dcf.py)
  2. Boundary detection (B4)
  3. Decoder narrator (chat mode, ~$0.17)
  4. Evidence hunter per assumption (deepresearch, ~$3.21/each)
  5. Critic per evidence brief (Python, no LLM, free)
  6. Synthesizer (chat mode, ~$0.05-0.20)
  7. Aggregate + save

Cost-safe flags:
  --no-evidence        skip evidence calls entirely (decoder only)
  --max-evidence N     limit to N evidence calls (default: all)
  --no-critic          skip Python critic (still free; debug only)
  --no-synthesis       skip synthesizer LLM call
  --offline            cache-only, no API calls (G5 demo mode)
  --no-cache           force fresh API calls
  --flagship           use 235B model for evidence (3x cost)

Examples:
  python pipeline.py NVDA --no-evidence --no-synthesis    # cheapest, decoder only
  python pipeline.py NVDA --no-evidence                   # decoder + synthesis stub
  python pipeline.py NVDA --max-evidence 1                # full path on 1 assumption
  python pipeline.py NVDA --offline                       # demo mode, cache only
"""
import argparse
import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from client import (
    MODEL_FLAGSHIP,
    MODEL_MINI,
    call_chat,
    call_deepresearch,
    parse_loose_json,
)
from critic import validate_evidence_brief
from prompt_loader import load_prompt
from reverse_dcf import (
    Assumptions,
    CompanyData,
    compute_historical_context,
    compute_wacc,
    dcf_equity_value_per_share,
    format_historical_context_md,
    monte_carlo_implied,
    pull_analyst_consensus,
    pull_company_data,
)

CACHE_DIR = Path("cache")
OUTPUTS_DIR = Path("outputs")


# ----- Cache layer (G5 foundation) -----

def _cache_path(category: str, key: str) -> Path:
    p = CACHE_DIR / category
    p.mkdir(parents=True, exist_ok=True)
    return p / f"{key}.json"


def cache_get(category: str, key: str):
    p = _cache_path(category, key)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return None


def cache_put(category: str, key: str, data: dict):
    p = _cache_path(category, key)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _hash(*args) -> str:
    raw = "|".join(str(a) for a in args)
    return hashlib.md5(raw.encode()).hexdigest()[:12]


# ----- Reverse DCF wrapper -----

def build_reverse_dcf_output(data: CompanyData) -> dict:
    consensus_wacc = compute_wacc(data.beta)
    consensus = Assumptions(
        revenue_cagr_5y=0.15,
        terminal_growth=0.025,
        terminal_fcf_margin=max(data.fcf_ttm / data.revenue_ttm, 0.05) if data.revenue_ttm else 0.15,
        wacc=consensus_wacc,
    )
    baseline_price = dcf_equity_value_per_share(consensus, data)

    perturbations = {
        "revenue_cagr_5y": (0.05, 0.30),
        "terminal_growth": (0.015, 0.035),
        "terminal_fcf_margin": (consensus.terminal_fcf_margin * 0.6, consensus.terminal_fcf_margin * 1.4),
        "wacc": (consensus.wacc - 0.015, consensus.wacc + 0.015),
    }
    intervals = {}
    for var in ["revenue_cagr_5y", "terminal_fcf_margin", "wacc"]:
        intervals[var] = monte_carlo_implied(data, var, consensus, perturbations)

    return {
        "ticker": data.ticker,
        "current_price": data.current_price,
        "baseline_dcf_price": baseline_price,
        "consensus_assumptions": asdict(consensus),
        "implied_intervals": intervals,
        # Raw inputs so the frontend can recompute DCF when user moves the slider
        # (avoids round-trip latency). Keep in sync with reverse_dcf.dcf_equity_value_per_share.
        "company_inputs": {
            "revenue_ttm": data.revenue_ttm,
            "fcf_ttm": data.fcf_ttm,
            "shares_outstanding": data.shares_outstanding,
            "net_debt": data.net_debt,
            "beta": data.beta,
        },
    }


def detect_boundary(rdcf: dict) -> bool:
    """B4: hard boundary if no Monte Carlo variable converged meaningfully."""
    valid = 0
    for interval in rdcf["implied_intervals"].values():
        if interval is not None and interval.get("success_rate", 0) > 0.3:
            valid += 1
    return valid == 0


def get_company_name(ticker: str) -> str:
    import yfinance as yf
    try:
        info = yf.Ticker(ticker).info
        return info.get("longName") or info.get("shortName") or ticker
    except Exception:
        return ticker


# ----- Decoder narrator -----

def run_decoder(data: CompanyData, rdcf: dict, mode: str, company_name: str,
                use_cache: bool, offline: bool):
    # Historical context bumps cache version so old caches without it get invalidated.
    cache_key = f"{rdcf['ticker']}_v2_{_hash(rdcf['ticker'], rdcf['current_price'], rdcf['baseline_dcf_price'], mode)}"
    if use_cache:
        cached = cache_get("decoder", cache_key)
        if cached:
            print(f"  [cache hit] decoder narrator")
            return cached, True

    if offline:
        raise RuntimeError(f"Offline mode but no decoder cache for {rdcf['ticker']}")

    boundary_reason = ""
    if mode == "boundary":
        nonconverged = [k for k, v in rdcf["implied_intervals"].items()
                        if v is None or v.get("success_rate", 0) <= 0.3]
        boundary_reason = f"硬边界:Monte Carlo 反向解全部失败或退化(未收敛变量: {', '.join(nonconverged)})"

    historical = compute_historical_context(data)
    consensus = pull_analyst_consensus(rdcf["ticker"])
    historical_md = format_historical_context_md(historical, consensus)
    if not historical_md.strip():
        historical_md = "(no historical context available from yfinance)"

    prompt = load_prompt(
        "prompts/decoder_narrator.md",
        LANG="zh",
        MODE=mode,
        TICKER=rdcf["ticker"],
        COMPANY_NAME=company_name,
        CURRENT_PRICE=f"{rdcf['current_price']}",
        BASELINE_PRICE=f"{rdcf['baseline_dcf_price']:.2f}",
        REVERSE_DCF_OUTPUT_JSON=json.dumps(rdcf, ensure_ascii=False, indent=2),
        HISTORICAL_CONTEXT=historical_md,
        BOUNDARY_REASON=boundary_reason,
        ISO_TIMESTAMP=datetime.now(timezone.utc).isoformat(),
    )
    print(f"  [api call] decoder narrator (~$0.17)")
    raw = call_chat(prompt, model=MODEL_MINI, verbose=False)
    parsed = parse_loose_json(raw["content"])
    parsed["_meta"] = {
        "usage": raw["usage"],
        "cost_usd": raw["cost_usd"],
        "reasoning_chars": raw["reasoning_chars"],
    }
    cache_put("decoder", cache_key, parsed)
    return parsed, False


# ----- Evidence hunter -----

def run_evidence(ticker: str, assumption: dict, mode: str, company_name: str,
                 current_price: float, model: str, use_cache: bool, offline: bool):
    aid = assumption.get("id") or f"{ticker}_unknown"
    cache_key = f"{ticker}_{aid}_{mode}_{_hash(assumption.get('human_text', '')[:80])}"

    if use_cache:
        cached = cache_get("evidence", cache_key)
        if cached:
            print(f"  [cache hit] evidence: {aid}")
            return cached, True

    if offline:
        raise RuntimeError(f"Offline mode but no evidence cache for {aid}")

    interval = assumption.get("interval", {}) or {}
    p25 = interval.get("p25", 0)
    p50 = interval.get("p50", 0)
    p75 = interval.get("p75", 0)
    est_cost = 3.21 if model == MODEL_MINI else 10.5

    prompt = load_prompt(
        "prompts/evidence_hunter.md",
        LANG="zh",
        MODE=mode,
        TICKER=ticker,
        COMPANY_NAME=company_name,
        CURRENT_PRICE=f"{current_price}",
        ASSUMPTION_TYPE=assumption.get("metric", ""),
        ASSUMPTION_TEXT=assumption.get("human_text", assumption.get("rationale", "")),
        INTERVAL_P25_P50_P75=f"[{p25}, {p50}, {p75}]",
        BOUNDARY_REASON="" if mode == "standard" else "DCF 无法解释当前价格",
        ISO_TIMESTAMP=datetime.now(timezone.utc).isoformat(),
        MODEL_NAME=model,
    )
    print(f"  [api call] evidence: {aid} ({model.split('-')[-1]}, ~${est_cost:.2f})")
    raw = call_deepresearch(prompt, model=model, verbose=False)
    try:
        parsed = parse_loose_json(raw["content"])
    except json.JSONDecodeError as e:
        parsed = {"error": str(e), "raw_content": raw["content"]}

    parsed["_meta"] = {
        "usage": raw["usage"],
        "cost_usd": raw["cost_usd"],
        "tool_call_count": raw["tool_call_count"],
        "search_results_count": len(raw["search_results"]),
    }
    cache_put("evidence", cache_key, parsed)
    return parsed, False


# ----- Synthesizer -----

def run_synthesizer(rdcf: dict, mode: str, company_name: str,
                    decoder: dict, evidence_briefs: list, critic_reports: list,
                    use_cache: bool, offline: bool):
    cache_key = f"{rdcf['ticker']}_{_hash(rdcf['ticker'], rdcf['current_price'], mode, len(evidence_briefs), len(critic_reports))}"
    if use_cache:
        cached = cache_get("synthesizer", cache_key)
        if cached:
            print(f"  [cache hit] synthesizer")
            return cached, True

    if offline:
        raise RuntimeError(f"Offline mode but no synthesizer cache for {rdcf['ticker']}")

    # Strip _meta from inputs to keep prompt size manageable
    decoder_lean = {k: v for k, v in decoder.items() if k != "_meta"}
    briefs_lean = [{k: v for k, v in b.items() if k != "_meta"} for b in evidence_briefs]

    prompt = load_prompt(
        "prompts/synthesizer.md",
        LANG="zh",
        MODE=mode,
        TICKER=rdcf["ticker"],
        COMPANY_NAME=company_name,
        CURRENT_PRICE=f"{rdcf['current_price']}",
        BASELINE_PRICE=f"{rdcf['baseline_dcf_price']:.2f}",
        DECODER_OUTPUT_JSON=json.dumps(decoder_lean, ensure_ascii=False, indent=2),
        EVIDENCE_BRIEFS_JSON=json.dumps(briefs_lean, ensure_ascii=False, indent=2),
        CRITIC_REPORTS_JSON=json.dumps(critic_reports, ensure_ascii=False, indent=2),
        ISO_TIMESTAMP=datetime.now(timezone.utc).isoformat(),
    )
    print(f"  [api call] synthesizer (chat, ~$0.05-0.20)")
    raw = call_chat(prompt, model=MODEL_MINI, verbose=False)
    try:
        parsed = parse_loose_json(raw["content"])
    except json.JSONDecodeError as e:
        parsed = {"error": str(e), "raw_content": raw["content"]}
    parsed["_meta"] = {
        "usage": raw["usage"],
        "cost_usd": raw["cost_usd"],
        "reasoning_chars": raw["reasoning_chars"],
    }
    cache_put("synthesizer", cache_key, parsed)
    return parsed, False


# ----- Main -----

def main():
    parser = argparse.ArgumentParser(description="PriceLens W2 pipeline")
    parser.add_argument("ticker")
    parser.add_argument("--no-evidence", action="store_true")
    parser.add_argument("--max-evidence", type=int, default=None)
    parser.add_argument("--no-critic", action="store_true")
    parser.add_argument("--no-synthesis", action="store_true")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--flagship", action="store_true")
    args = parser.parse_args()

    use_cache = not args.no_cache
    evidence_model = MODEL_FLAGSHIP if args.flagship else MODEL_MINI

    print(f"=== PriceLens Pipeline: {args.ticker} ===")
    print(f"cache={'on' if use_cache else 'off'} offline={args.offline} "
          f"evidence_model={evidence_model.split('-')[-1] if evidence_model else 'N/A'}\n")

    # 1. Data + reverse DCF
    print("[1/4] Pull data + reverse DCF Monte Carlo...")
    data = pull_company_data(args.ticker)
    rdcf = build_reverse_dcf_output(data)
    print(f"  current=${rdcf['current_price']:.2f} baseline_dcf=${rdcf['baseline_dcf_price']:.2f}")
    intervals_summary = {
        k: (f"[{v['p25']:.1%}..{v['p50']:.1%}..{v['p75']:.1%}] (s={v['success_rate']:.0%})" if v else "NO SOLUTION")
        for k, v in rdcf["implied_intervals"].items()
    }
    for k, s in intervals_summary.items():
        print(f"    {k:25s}: {s}")
    company_name = get_company_name(args.ticker)

    # 2. Boundary detection
    print(f"\n[2/4] Boundary detection (B4)...")
    is_boundary = detect_boundary(rdcf)
    mode = "boundary" if is_boundary else "standard"
    if is_boundary:
        print(f"  mode = BOUNDARY (TSLA-like, DCF cannot explain)")
    else:
        print(f"  mode = standard")

    # 3. Decoder narrator
    print(f"\n[3/4] Decoder narrator (chat mode, MODE={mode})...")
    decoder, decoder_cached = run_decoder(data, rdcf, mode, company_name, use_cache, args.offline)

    if mode == "standard":
        assumptions = decoder.get("implied_assumptions", [])
        print(f"  Got {len(assumptions)} implied assumptions")
        evidence_targets = assumptions
    else:
        be = decoder.get("boundary_explanation") or {}
        hyps = be.get("framework_hypotheses", [])
        print(f"  Got {len(hyps)} framework hypotheses (boundary mode)")
        evidence_targets = [
            {
                "id": f"{args.ticker}_framework_{i}",
                "metric": h.get("framework_name", "unknown"),
                "human_text": h.get("rationale", ""),
                "interval": {},
            }
            for i, h in enumerate(hyps)
        ]

    # 4. Evidence
    evidence_briefs = []
    if args.no_evidence:
        print(f"\n[4/6] SKIPPED evidence (--no-evidence flag)")
    elif not evidence_targets:
        print(f"\n[4/6] No assumptions to research")
    else:
        targets = evidence_targets[: args.max_evidence] if args.max_evidence else evidence_targets
        print(f"\n[4/6] Evidence hunt for {len(targets)} assumptions...")
        for assumption in targets:
            brief, _cached = run_evidence(
                args.ticker, assumption, mode, company_name,
                rdcf["current_price"], evidence_model, use_cache, args.offline,
            )
            evidence_briefs.append(brief)

    # 5. Critic (Python, no LLM)
    critic_reports = []
    if args.no_critic:
        print(f"\n[5/6] SKIPPED critic (--no-critic flag)")
    elif not evidence_briefs:
        print(f"\n[5/6] No evidence briefs to critic")
    else:
        print(f"\n[5/6] Critic mechanical validation ({len(evidence_briefs)} briefs)...")
        for brief in evidence_briefs:
            report = validate_evidence_brief(brief)
            critic_reports.append(report)
            print(f"  {brief.get('assumption_id', '?'):30s}: {report['verdict']:8s} "
                  f"(errors={report['counts']['errors']}, warnings={report['counts']['warnings']})")

    # 6. Synthesizer (chat mode)
    synthesis = None
    if args.no_synthesis:
        print(f"\n[6/6] SKIPPED synthesizer (--no-synthesis flag)")
    else:
        print(f"\n[6/6] Synthesizer (chat mode, MODE={mode})...")
        synthesis, _cached = run_synthesizer(
            rdcf, mode, company_name, decoder, evidence_briefs, critic_reports,
            use_cache, args.offline,
        )
        if "headline" in synthesis:
            print(f"  headline: {synthesis['headline']}")

    # Aggregate
    OUTPUTS_DIR.mkdir(exist_ok=True)
    output = {
        "ticker": args.ticker,
        "company_name": company_name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "reverse_dcf": rdcf,
        "decoder_output": decoder,
        "evidence_briefs": evidence_briefs,
        "critic_reports": critic_reports,
        "synthesis": synthesis,
    }
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = OUTPUTS_DIR / f"{args.ticker}_{timestamp}.json"
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    # Summary
    decoder_cost = decoder.get("_meta", {}).get("cost_usd", 0)
    evidence_cost = sum(b.get("_meta", {}).get("cost_usd", 0) for b in evidence_briefs)
    synth_cost = (synthesis or {}).get("_meta", {}).get("cost_usd", 0) if synthesis else 0
    total_cost = decoder_cost + evidence_cost + synth_cost

    print(f"\n=== Summary ===")
    print(f"Ticker:         {args.ticker} ({company_name})")
    print(f"Current price:  ${rdcf['current_price']:.2f}")
    print(f"Baseline DCF:   ${rdcf['baseline_dcf_price']:.2f}")
    print(f"Mode:           {mode}")
    print(f"Assumptions/hypotheses: {len(evidence_targets)}")
    print(f"Evidence briefs ran:    {len(evidence_briefs)}")
    print(f"Decoder cost:   ${decoder_cost:.4f}  ({'cache' if decoder_cached else 'fresh'})")
    print(f"Evidence cost:  ${evidence_cost:.4f}  ({sum(1 for b in evidence_briefs if b.get('_meta', {}).get('cost_usd', 0) == 0)} cached / {sum(1 for b in evidence_briefs if b.get('_meta', {}).get('cost_usd', 0) > 0)} fresh)")
    print(f"Total cost:     ${total_cost:.4f}")
    print(f"Saved to:       {out_path}")


if __name__ == "__main__":
    main()
