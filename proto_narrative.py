"""PROTOTYPE — one real Deep Research call testing whether a *market-sentiment*
targeted prompt produces depth a formula can't. Subject-level (not per-number).

Uses the real MIROMIND_API_KEY from .env (NOT blocked). ~$3-10, a few minutes.
Prints raw + parsed output + cost. Throwaway probe; not wired into the pipeline.
"""
import sys, os, re, json, time
sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

import client

TICKER = "NVDA"
COMPANY = "NVIDIA Corporation"
PRICE = "211.14"
# the formula engine's implied numbers (from today's real decode) — the QUESTIONS,
# not answers to verify.
IMPLIED = (
    "  - 隐含 P/E ≈ 32.4x（市场给的静态市盈率）\n"
    "  - 隐含 5 年营收 CAGR ≈ 49%（DCF 反解：要撑住现价必须的复合增速）\n"
    "  - 隐含 P/FCF ≈ 53x（现价对应的自由现金流倍数）"
)

raw = open("prompts/market_narrative.md", encoding="utf-8").read()
m = re.search(r"## ===== PROMPT START =====(.*?)## ===== PROMPT END =====", raw, re.S)
body = m.group(1).strip()
prompt = (body
          .replace("{{LANG}}", "zh")
          .replace("{{TICKER}}", TICKER)
          .replace("{{COMPANY_NAME}}", COMPANY)
          .replace("{{CURRENT_PRICE}}", PRICE)
          .replace("{{IMPLIED_ASSUMPTIONS}}", IMPLIED)
          .replace("{{ISO_TIMESTAMP}}", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))

print("=== Market-Narrative deep-research PROTOTYPE: NVDA ===")
print("key present:", bool(os.environ.get("MIROMIND_API_KEY")) or "(from .env)")
print("model:", client.MODEL_FLAGSHIP)
t0 = time.time()
res = client.call_deepresearch(prompt, model=client.MODEL_FLAGSHIP, verbose=False)
dt = time.time() - t0

print(f"\n--- elapsed {dt:.0f}s · tool_calls={res.get('tool_call_count')} · "
      f"search_results={len(res.get('search_results') or [])} · cost=${res.get('cost_usd', 0):.2f} ---")

content = res.get("content", "") or ""
try:
    obj = client.parse_loose_json(content)
    print("\n=== PARSED OK — top-level keys:", list(obj.keys()), "===")
    print("\ncoverage     :", obj.get("coverage"))
    reg = obj.get("sentiment_regime") or {}
    print("regime       :", reg.get("label"), "—", reg.get("rationale"))
    print("bull_case    :", len(obj.get("bull_case") or []), "claims")
    print("bear_case    :", len(obj.get("bear_case") or []), "claims")
    print("contested    :", [a.get("axis") for a in (obj.get("contested_axis") or [])])
    print("catalysts    :", [c.get("event") for c in (obj.get("catalysts") or [])])
    print("\nHEADLINE     :", obj.get("headline"))
    print("\n=== FULL JSON ===")
    print(json.dumps(obj, ensure_ascii=False, indent=2))
except Exception as e:
    print("\n(JSON parse failed:", e, "— raw content below)\n")
    print(content)

print("\n=== DONE ===")
