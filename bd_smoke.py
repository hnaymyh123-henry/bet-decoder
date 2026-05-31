"""One-shot REAL-API smoke: decode NVDA via live MiroMind Deep Research.
Uses the real key from .env (NOT blocked). Caches to pricelens.db so re-runs are cheap.
Prints the resulting card + per-assumption evidence + cost. ~$3-10, a few minutes.
"""
import sys, os, json, time
sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

import db
import decoder

print("=== REAL-API smoke: decode NVDA (live MiroMind) ===")
print("MIROMIND_API_KEY present:", bool(os.environ.get("MIROMIND_API_KEY")) or "(from .env)")
t0 = time.time()

conn = db.init_db("pricelens.db")
events = []
def emit(e):
    try:
        events.append(e)
    except Exception:
        pass

card = decoder.decode_bet("market", "NVDA", "zh", emit=emit, conn=conn)
dt = time.time() - t0

print(f"\n--- elapsed {dt:.1f}s · {len(events)} activity events ---")
print("subject     :", card.subject)
print("source_type :", card.source_type)
print("card_kind   :", card.card_kind)
print("bet (scalar):", card.bet)
print("theme_exposures:", [(t.theme, t.exposure_pct) for t in (card.theme_exposures or [])])

detail = getattr(card, "decode_detail", {}) or {}
print("\nmode        :", detail.get("mode"))
print("primary_lens:", detail.get("primary_lens"))
print("cross_lenses:", detail.get("cross_lenses"))

ev = detail.get("evidence") or {}
print("\n=== EVIDENCE (real Deep Research) ===")
print("assumptions :", ev.get("assumption_count"))
print("found       :", ev.get("found_count"), "| empty:", ev.get("empty_count"))
print("cache_hits  :", ev.get("cache_hits"), "| new hunter calls:", ev.get("new_hunter_calls"))
print("cost (USD)  :", ev.get("cost"))
for b in (ev.get("briefs") or [])[:3]:
    print("\n  · assumption:", str(b.get("assumption_text"))[:80])
    print("    status   :", b.get("status"))
    txt = json.dumps(b, ensure_ascii=False)[:600]
    print("    brief    :", txt)

print("\n=== DONE ===")
