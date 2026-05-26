"""Test B: Decoder Narrator on NVDA reverse_dcf.py output.

Validates: chat mode (tool_choice=none) works, generates structured human-readable
assumptions per A3 prompt template. Critical for go/no-go on chat mode quality.
Pre-req: MIROMIND_API_KEY in .env.

Run: python test_b_chat.py
"""
import json
import sys
from datetime import datetime, timezone

from client import MODEL_MINI, call_chat, parse_loose_json
from prompt_loader import load_prompt


NVDA_REVERSE_DCF = {
    "ticker": "NVDA",
    "current_price": 212.65,
    "baseline_dcf_price": 45.36,
    "consensus_assumptions": {
        "revenue_cagr_5y": 0.15,
        "terminal_growth": 0.025,
        "terminal_fcf_margin": 0.448,
        "wacc": 0.168,
    },
    "implied_intervals": {
        "revenue_cagr_5y": {"p25": 0.562, "p50": 0.629, "p75": 0.705, "samples": 472, "success_rate": 0.94},
        "terminal_fcf_margin": None,
        "wacc": {"p25": 0.052, "p50": 0.062, "p75": 0.074, "samples": 480, "success_rate": 0.96},
    },
}

HISTORICAL_CONTEXT = """- NVDA 过去 5 年(FY21-FY25)营收 CAGR ≈ 56%(AI / Data Center 拉动)
- 半导体行业 5 年远期共识 CAGR 中位数 ≈ 15%
- NVDA 当前 TTM FCF margin = 44.8%(历史峰值,AI capex 周期)
- 美 10Y Treasury yield ≈ 4.5%
- 标普 500 隐含股权风险溢价 ≈ 5.5%"""


def main():
    prompt = load_prompt(
        "prompts/decoder_narrator.md",
        LANG="zh",
        MODE="standard",
        TICKER="NVDA",
        COMPANY_NAME="NVIDIA Corporation",
        CURRENT_PRICE="212.65",
        BASELINE_PRICE="45.36",
        REVERSE_DCF_OUTPUT_JSON=json.dumps(NVDA_REVERSE_DCF, ensure_ascii=False, indent=2),
        HISTORICAL_CONTEXT=HISTORICAL_CONTEXT,
        BOUNDARY_REASON="",
        ISO_TIMESTAMP=datetime.now(timezone.utc).isoformat(),
    )

    print(f"=== Test B · Decoder Narrator (chat mode, tool_choice=none) ===")
    print(f"Model: {MODEL_MINI}")
    print(f"Prompt length: {len(prompt)} chars\n")
    print("Calling MiroMind API... (streaming)\n")
    print("--- Response (live) ---")

    result = call_chat(prompt, model=MODEL_MINI, verbose=True)

    print("\n--- Response (end) ---")

    print(f"\n--- Usage ---")
    print(f"Model returned:     {result['model']}")
    print(f"Reasoning chars:    {result['reasoning_chars']:,} (thinking trace, billed as output)")
    print(f"Tool calls:         {result['tool_call_count']}")
    if result["usage"]:
        u = result["usage"]
        print(f"Tokens:             {u.get('prompt_tokens', 0)} in + {u.get('completion_tokens', 0)} out = {u.get('total_tokens', 0)}")
        print(f"Estimated cost:    ${result['cost_usd']:.4f}")
    else:
        print(f"Usage:              (none — last chunk: {result['last_chunk']})")

    print(f"\n--- JSON validation ---")
    try:
        parsed = parse_loose_json(result["content"])
        assumptions = parsed.get("implied_assumptions", [])
        load_bearing = sum(1 for a in assumptions if a.get("load_bearing"))
        print(f"[OK] JSON parse OK (via loose parser)")
        print(f"[OK] implied_assumptions: {len(assumptions)} (load_bearing={load_bearing})")
        if not (5 <= len(assumptions) <= 7):
            print(f"[WARN] schema wants 5-7 assumptions, got {len(assumptions)}")
        if load_bearing != 3:
            print(f"[WARN] schema wants exactly 3 load_bearing, got {load_bearing}")
    except json.JSONDecodeError as e:
        print(f"[FAIL] JSON parse FAILED: {e}")
        print(f"  First 200 chars: {result['content'][:200]!r}")

    with open("test_b_output.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\nFull output saved to test_b_output.json")


if __name__ == "__main__":
    main()
