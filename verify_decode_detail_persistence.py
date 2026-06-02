"""decode_detail persistence + card lineage verification (schema v3).

Deterministic, zero API/network. Proves the TD1 fix: a reloaded card carries its
rich decode_detail (so it can be interrogated/revised, and mode/narrative_premium/
market_narrative round-trip), nested ThemeExposure dataclasses serialize, derived
(what-if) cards coexist with the canonical daily Market card, and the migration is
idempotent + degrades gracefully on cards saved without detail.

Run:  MIROMIND_API_KEY="" "/c/Users/Henry Ma/miniconda3/python.exe" verify_decode_detail_persistence.py
"""
from __future__ import annotations

import db
from db import BetCard, ThemeExposure

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
print("decode_detail persistence + lineage (schema v3) verification")
print("=" * 72)

conn = db.init_db(":memory:")


def _detail():
    """A representative anchor-mode decode_detail with a nested ThemeExposure
    dataclass (the serialization hazard) + a market_narrative summary+full."""
    return {
        "mode": "anchor_primary",
        "narrative_premium": 0.77,
        "anchor_price": 950.0,
        "anchor_mode": {
            "base_business_value": 220.0,
            "theme_exposures": [
                ThemeExposure(theme="AI infra", exposure_pct=73.0,
                              contributing_tickers=["NVDA"],
                              is_concentration_risk=True),
            ],
        },
        "evidence": {"briefs": [], "found_count": 0},
        "market_narrative": {
            "summary": {"regime": "mixed", "headline": "AI capex debate"},
            "full": {"bull_case": [{"claim": "TAM huge"}]},
        },
    }


# --- AC1: decode_detail round-trips across save → reload --------------------
c = BetCard(subject="NVDA", source_type="market")
c.decode_detail = _detail()
cid = db.save_card(conn, c)
r = db.get_card(conn, cid)
check("AC1 reloaded card carries decode_detail (TD1 fixed)",
      getattr(r, "decode_detail", None) is not None
      and r.decode_detail.get("mode") == "anchor_primary"
      and r.decode_detail.get("narrative_premium") == 0.77,
      f"mode={getattr(r,'decode_detail',{}) and r.decode_detail.get('mode')}")
check("AC1 nested ThemeExposure dataclass serialized → dict (no crash)",
      r.decode_detail["anchor_mode"]["theme_exposures"][0] == {
          "theme": "AI infra", "exposure_pct": 73.0,
          "contributing_tickers": ["NVDA"], "is_concentration_risk": True},
      f"{r.decode_detail['anchor_mode']['theme_exposures'][0]}")
check("AC1 market_narrative.full round-trips",
      r.decode_detail["market_narrative"]["full"]["bull_case"][0]["claim"] == "TAM huge")

# --- AC2: card_to_json surfaces mode/np/market_narrative on a RELOADED card -
j = db.card_to_json(r)
check("AC2 card_to_json on reloaded card has non-null mode/narrative_premium",
      j["mode"] == "anchor_primary" and j["narrative_premium"] == 0.77,
      f"mode={j['mode']} np={j['narrative_premium']}")
check("AC2 card_to_json carries compact market_narrative summary",
      (j.get("market_narrative") or {}).get("regime") == "mixed")
full = db.card_to_json_full(r)
check("AC2 card_to_json_full carries the FULL decode_detail",
      full.get("decode_detail", {}).get("market_narrative", {}).get("full") is not None
      and "decode_detail" not in j,  # compact form must NOT carry it
      "full has detail; compact omits it")

# --- AC3: a card saved WITHOUT detail degrades gracefully -------------------
plain = BetCard(subject="COST", source_type="market")
pid = db.save_card(conn, plain)
rp = db.get_card(conn, pid)
jp = db.card_to_json(rp)
check("AC3 detail-less card: no decode_detail, mode/np None, no crash",
      getattr(rp, "decode_detail", None) is None
      and jp["mode"] is None and jp["narrative_premium"] is None)

# --- AC4: derived (what-if) card coexists with the canonical daily card -----
d = BetCard(subject="NVDA", source_type="market", derived_from=cid,
            derivation_kind="whatif",
            derivation={"params": {"wacc": 0.09},
                        "diff": [{"field": "cagr", "before": 70, "after": 45}]})
d.decode_detail = _detail()
did = db.save_card(conn, d)
check("AC4 derived card persists as its OWN card (not deduped to parent)",
      did != cid and db.get_card(conn, did) is not None
      and db.get_card(conn, cid) is not None,
      f"did={did[:8]} cid={cid[:8]}")
check("AC4 derived lineage round-trips (derived_from / kind / diff)",
      (rd := db.get_card(conn, did)).derived_from == cid
      and rd.derivation_kind == "whatif"
      and rd.derivation["diff"][0]["after"] == 45)
jd = db.card_to_json(rd)
check("AC4 card_to_json exposes lineage for the frontend",
      jd["derived_from"] == cid and jd["derivation_kind"] == "whatif")
# A SECOND original same-day Market card still dedups to the first (canonical).
dup = BetCard(subject="NVDA", source_type="market",
              created_at=c.created_at, trade_date=c.trade_date)
dup_id = db.save_card(conn, dup)
check("AC4 a 2nd ORIGINAL same-day Market card still dedups to the canonical one",
      dup_id == cid, f"dup_id={dup_id[:8]} cid={cid[:8]}")
check("AC4 _find_dedup_card_id returns None for a derived card",
      db._find_dedup_card_id(conn, d) is None)

# --- AC5: migration idempotent + unparseable detail degrades to None --------
db._apply_schema(conn)
db._apply_schema(conn)
check("AC5 _apply_schema is idempotent (re-run twice, original card still reads)",
      db.get_card(conn, cid).decode_detail.get("mode") == "anchor_primary")
check("AC5 _loads_detail tolerates garbage → None (no crash)",
      db._loads_detail("<<not json>>") is None and db._loads_detail("") is None
      and db._loads_detail('[1,2]') is None)  # non-dict → None
check("AC5 _dump_detail(None/empty) → None; valid detail → JSON text",
      db._dump_detail(None) is None and db._dump_detail({}) is None
      and isinstance(db._dump_detail({"a": 1}), str))

# --- AC6: DCF build-up worksheet reconciles to price (drift guard) ----------
# A real decode of a solvable fixture (offline, fundamentals stubbed) → the DCF
# branch carries a build-up whose per_share ≈ market price, proving db._dcf_breakdown
# mirrors reverse_dcf (catches formula drift).
import decoder  # noqa: E402

_COST = decoder.Fundamentals(
    ticker="COST", current_price=900.0, revenue_ttm=255e9, net_income_ttm=7.4e9,
    ebitda_ttm=11e9, fcf_ttm=6e9, book_equity=23e9, eps_ttm=16.6,
    shares_outstanding=0.443e9, net_debt=-5e9, beta=0.8, growth_rate=0.09,
    industry="Discount Stores")
_cc = decoder.decode_bet("market", "COST", "zh",
                         fundamentals_fn=lambda t: _COST, hunter=lambda *a, **k: None)
_der = (db.build_card_display(_cc) or {}).get("derivations") or {}
_dcf = next((b for b in _der.get("branches", []) if b.get("lens") == "dcf"), {})
_bd = next((lv.get("breakdown") for lv in _dcf.get("levels", [])
            if lv.get("kind") == "implied"), None)
_anchor = _cc.decode_detail["anchor_price"]
check("AC6 DCF build-up present on the solvable DCF branch (5y projection + bridge)",
      bool(_bd) and len(_bd.get("years") or []) == 5 and "per_share" in _bd)
check("AC6 build-up RECONCILES to price (db _dcf_breakdown matches reverse_dcf, no drift)",
      bool(_bd) and abs(_bd["per_share"] - _anchor) / _anchor < 0.02,
      f"per_share={_bd and round(_bd['per_share'], 2)} vs anchor={_anchor}")

print("=" * 72)
print(f"RESULT: {_passed} passed, {_failed} failed")
print("=" * 72)
raise SystemExit(1 if _failed else 0)
