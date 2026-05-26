"""Test A: Evidence Hunter on NVDA 5Y revenue growth assumption.

Validates: deepresearch mode works, returns valid JSON, measures cost.
Pre-req: MIROMIND_API_KEY in .env (see .env.example).

Run: python test_a_evidence.py [--flagship]
"""
import json
import sys
from datetime import datetime, timezone

from client import MODEL_FLAGSHIP, MODEL_MINI, call_deepresearch, parse_loose_json
from prompt_loader import load_prompt


def main():
    model = MODEL_FLAGSHIP if "--flagship" in sys.argv else MODEL_MINI

    prompt = load_prompt(
        "prompts/evidence_hunter.md",
        LANG="zh",
        MODE="standard",
        TICKER="NVDA",
        COMPANY_NAME="NVIDIA Corporation",
        CURRENT_PRICE="212.65",
        ASSUMPTION_TYPE="revenue_cagr_5y",
        ASSUMPTION_TEXT="市场必须假设 NVDA 未来 5 年营收 CAGR 在 56%-71% 区间(中位数 63%)",
        INTERVAL_P25_P50_P75="[0.562, 0.629, 0.705]",
        BOUNDARY_REASON="",
        ISO_TIMESTAMP=datetime.now(timezone.utc).isoformat(),
        MODEL_NAME=model,
    )

    print(f"=== Test A · Evidence Hunter (deepresearch mode) ===")
    print(f"Model: {model}")
    print(f"Prompt length: {len(prompt)} chars\n")
    print("Calling MiroMind API... (deepresearch may take 60-180s)\n")
    print("--- Response (live streaming) ---")

    result = call_deepresearch(prompt, model=model, verbose=True)

    print("\n--- Response (end) ---")
    print(f"Total content length: {len(result['content'])} chars")

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
        items = parsed.get("evidence_items", [])
        support = sum(1 for i in items if i.get("direction") == "support")
        refute = sum(1 for i in items if i.get("direction") == "refute")
        print(f"[OK] JSON parse OK (via loose parser)")
        print(f"[OK] evidence_items: {len(items)} (support={support}, refute={refute})")
        print(f"[OK] overall_balance: {parsed.get('overall_balance')}")
        if support < 3 or refute < 2:
            print(f"[WARN] schema wants support>=3 and refute>=2")
    except json.JSONDecodeError as e:
        print(f"[FAIL] JSON parse FAILED: {e}")
        print(f"  First 200 chars of content: {result['content'][:200]!r}")

    with open("test_a_output.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\nFull output saved to test_a_output.json")


if __name__ == "__main__":
    main()
