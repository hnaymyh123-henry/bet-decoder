"""Market Narrative module verification — deterministic, zero API/network cost.

Exercises the parse + source-tier classifier + validate + summarize on the REAL
first-run NVDA output (narrative_sample.json), plus the offline honest-empty path.
The classifier is the code-enforced honesty guardrail (the model cited X /
Instagram / KuCoin despite the prompt; we re-tier here and flag them).

Run:  "/c/Users/Henry Ma/miniconda3/python.exe" verify_narrative.py
"""
from __future__ import annotations

import json
import os

import narrative

_passed = 0
_failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global _passed, _failed
    status = "PASS" if cond else "FAIL"
    if cond:
        _passed += 1
    else:
        _failed += 1
    print(f"[{status}] {name}" + (f"  | {detail}" if detail else ""))


print("=" * 72)
print("Market Narrative — parse + source-tier classifier verification")
print("=" * 72)

# --- AC1: classify_source buckets real domains correctly --------------------
def C(url):
    return narrative.classify_source({"url": url})


cases = {
    "https://www.reuters.com/x": "B",
    "https://finance.yahoo.com/x": "B",
    "https://www.cnbc.com/x": "B",
    "https://www.kiplinger.com/x": "B",
    "https://www.businessinsider.com/x": "B",
    "https://economictimes.indiatimes.com/x": "B",
    "https://www.fool.com/x": "C",
    "https://simplywall.st/x": "C",
    "https://seekingalpha.com/x": "C",
    "https://michaeljburry.substack.com/p/x": "C",
    "https://au.investing.com/x": "C",
    "https://x.com/y/status/1": "D",
    "https://twitter.com/y": "D",
    "https://www.instagram.com/p/x": "D",
    "https://www.kucoin.com/news/x": "D",
    "https://beincrypto.com/x": "D",
    "https://www.sec.gov/x": "A",
    "https://investor.nvidia.com/x": "A",
    # regression: substring matching must NOT fire on these
    "https://www.netflix.com/title/1": "C",          # contains "x.com" as substring
    "https://finance.yahoo.com/markets/article/investors-might-be-penalizing-nvidia": "B",  # "/investor" in slug
}
bad = {u: (C(u), exp) for u, exp in cases.items() if C(u) != exp}
check("AC1 classify_source tiers real domains (B/C/D/A)", not bad,
      f"mismatches={bad}" if bad else f"{len(cases)} domains OK")
check("AC1 no-url source → D (unverifiable)",
      narrative.classify_source({"url": ""}) == "D"
      and narrative.classify_source({}) == "D")

# --- AC2: parse the real first-run output -----------------------------------
sample = json.load(open("narrative_sample.json", encoding="utf-8"))
content = json.dumps(sample, ensure_ascii=False)
parsed = narrative.parse_narrative(content)
check("AC2 parse_narrative round-trips the real model JSON",
      isinstance(parsed, dict)
      and {"sentiment_regime", "bull_case", "bear_case", "assumption_bindings",
           "headline"}.issubset(parsed.keys()),
      f"keys={sorted((parsed or {}).keys())[:6]}...")
check("AC2 parse_narrative('') → None (honest, no crash)",
      narrative.parse_narrative("") is None and narrative.parse_narrative("not json") is None)

# --- AC3: validate tags every claim + attaches source_quality ---------------
v = narrative.validate_narrative(parsed)
claims = ([v["sentiment_regime"]] + v["bull_case"] + v["bear_case"] + v["catalysts"])
all_tagged = all(c.get("source_tier") in ("A", "B", "C", "D")
                 and isinstance(c.get("unverified"), bool) for c in claims)
check("AC3 every claim tagged with source_tier + unverified", all_tagged,
      f"{len(claims)} claims tagged")
sq = v.get("source_quality") or {}
check("AC3 source_quality summary present + counts add up",
      sq.get("total_claims") == sum(sq.get("by_tier", {}).values())
      and sq.get("total_claims") == len(claims),
      f"by_tier={sq.get('by_tier')} total={sq.get('total_claims')}")

# --- AC4: the social/crypto sources the model snuck in are flagged D --------
d_sources = 0
for c in claims:
    for s in (c.get("sources") or []):
        if s.get("tier") == "D":
            d_sources += 1
check("AC4 social/crypto sources flagged D (3 IG + x.com + KuCoin + BeInCrypto = 6)",
      d_sources == 6, f"D-tier sources found={d_sources}")
# but every claim still has a real (>=C) backer → no claim is D-only
check("AC4 no claim is social-ONLY (unverified_claims == 0; discipline mostly held)",
      sq.get("unverified_claims") == 0,
      f"unverified_claims={sq.get('unverified_claims')}")

# --- AC5: build_card_narrative end-to-end + compact summary -----------------
env = {"coverage": "raw", "content": content,
       "_meta": {"cost_usd": 6.73, "tool_call_count": 3}}
card_narr = narrative.build_card_narrative(env)
summ = card_narr["summary"]
check("AC5 build_card_narrative → rich coverage + full + summary",
      card_narr["coverage"] == "rich" and card_narr["full"] is not None,
      f"coverage={card_narr['coverage']}")
check("AC5 summary carries regime + headline + bull/bear counts",
      summ.get("regime") == "mixed" and bool(summ.get("headline"))
      and summ.get("bull_count") == 4 and summ.get("bear_count") == 4,
      f"regime={summ.get('regime')} bull/bear={summ.get('bull_count')}/{summ.get('bear_count')}")
leans = [b.get("leans") for b in summ.get("bindings", [])]
check("AC5 assumption bindings preserved with per-number lean verdicts",
      leans == ["lean_bull", "contested", "lean_bear"],
      f"leans={leans}")

# --- AC6: honest-empty paths never fabricate --------------------------------
empty = narrative.build_card_narrative({"coverage": "unavailable", "content": ""})
check("AC6 unavailable envelope → unavailable summary (no fabrication)",
      empty["coverage"] == "unavailable" and empty["full"] is None
      and empty["summary"]["coverage"] == "unavailable")
garbage = narrative.build_card_narrative({"coverage": "raw", "content": "<<not json>>"})
check("AC6 unparseable content → unparseable (no crash, no fabrication)",
      garbage["coverage"] == "unparseable" and garbage["full"] is None)
# offline research path → unavailable, zero cost
os.environ["OFFLINE_MODE"] = "1"
narrative.reset_memory_cache()
env_off, hit = narrative.research_market_narrative("NVDA", implied_assumptions="- x")
check("AC6 OFFLINE research → unavailable envelope, zero cost",
      env_off["coverage"] == "unavailable" and env_off["_meta"]["cost_usd"] == 0.0
      and hit is False)

# --- AC7: cross-check pairs narrative lean with the independent evidence verdict
# (decision B). Evidence verdicts mirror the real cached NVDA briefs.
ev_section = {"briefs": [
    {"assumption_id": "NVDA_implied_pe", "overall_balance": "lean_support"},
    {"assumption_id": "NVDA_implied_revenue_cagr_5y", "overall_balance": "lean_bear"},
    {"assumption_id": "NVDA_implied_p_fcf", "overall_balance": "balanced"},
]}
xc = narrative.cross_check(ev_section, sample)
by_g = {narrative._metric_group(r["label"]): r for r in xc}
pe, cagr, pfcf = by_g.get("pe"), by_g.get("cagr"), by_g.get("p_fcf")
check("AC7 cross-check P/E: narrative+evidence agree (both bull)",
      bool(pe) and pe["narrative"] == "bull" and pe["evidence"] == "bull"
      and pe["agree"] and not pe["diverges"], f"{pe}")
check("AC7 cross-check CAGR diverges (narrative mixed vs evidence bear)",
      bool(cagr) and cagr["narrative"] == "mixed" and cagr["evidence"] == "bear"
      and cagr["diverges"], f"{cagr}")
check("AC7 cross-check P/FCF diverges (narrative bear vs evidence mixed)",
      bool(pfcf) and pfcf["narrative"] == "bear" and pfcf["evidence"] == "mixed"
      and pfcf["diverges"], f"{pfcf}")
check("AC7 normalizers map both scales to bull/mixed/bear",
      narrative._norm_evidence_balance("support") == "bull"
      and narrative._norm_evidence_balance("bear") == "bear"
      and narrative._norm_evidence_balance("balanced") == "mixed"
      and narrative._norm_narrative_lean("lean_bull") == "bull"
      and narrative._norm_narrative_lean("contested") == "mixed")
no_ev = narrative.cross_check({"briefs": []}, sample)
check("AC7 no evidence brief → evidence None, no false agree/diverge",
      all(r["evidence"] is None and not r["agree"] and not r["diverges"] for r in no_ev),
      f"{[(r['label'][:10], r['evidence']) for r in no_ev]}")

print("=" * 72)
print(f"RESULT: {_passed} passed, {_failed} failed")
print("=" * 72)
raise SystemExit(1 if _failed else 0)
