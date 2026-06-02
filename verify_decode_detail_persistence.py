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

# --- AC7: anchor-mode base value carries its OWN build-up (reconciles to BASE) -
# An AI-composite, narrative-priced fixture routes to anchor mode. The
# 基础业务价值 branch must carry a FORWARD DCF build-up (consensus CAGR) whose
# per_share reconciles to base_business_value (NOT the price) — proving the
# db-side base build-up mirrors decoder._base_business_value with no drift —
# plus an explicit 现价−基础=溢价 bridge step and quantified theme rows (R1).
_NV = decoder.Fundamentals(
    ticker="NVDA", current_price=224.0, revenue_ttm=60e9, net_income_ttm=32e9,
    ebitda_ttm=38e9, fcf_ttm=30e9, book_equity=43e9, eps_ttm=2.9,
    shares_outstanding=24.4e9, net_debt=-10e9, beta=1.5, growth_rate=0.5,
    industry="Semiconductors", tags=["GPU accelerator"])
_nc = decoder.decode_bet("market", "NVDA", "zh",
                         fundamentals_fn=lambda t: _NV, hunter=lambda *a, **k: None)
_nam = (_nc.decode_detail or {}).get("anchor_mode") or {}
_base_val = _nam.get("base_business_value")
_nder = (db.build_card_display(_nc) or {}).get("derivations") or {}
_nbr = _nder.get("branches", [])
_basebr = next((b for b in _nbr if b.get("lens") == "base"), {})
_baselv = next((lv for lv in _basebr.get("levels", []) if lv.get("kind") == "implied"), {})
_bbd = _baselv.get("breakdown")
_txts = " ".join(str(lv.get("text") or "") for b in _nbr for lv in b.get("levels", []))
check("AC7 anchor mode triggered on the AI-composite fixture",
      bool(_nam.get("components")) and isinstance(_base_val, (int, float)) and _base_val > 0,
      f"mode={(_nc.decode_detail or {}).get('mode')} base={_base_val and round(_base_val,2)}")
check("AC7 基础业务价值 branch carries a 5y build-up reconciling to BASE (not price)",
      bool(_bbd) and len(_bbd.get("years") or []) == 5
      and isinstance(_base_val, (int, float)) and _base_val > 0
      and abs(_bbd["per_share"] - _base_val) / _base_val < 0.02,
      f"per_share={_bbd and round(_bbd['per_share'], 2)} vs base={_base_val and round(_base_val, 2)}")
check("AC7 base build-up labels reconcile to 基础业务价值 (not 现价)",
      bool(_bbd) and _bbd.get("reconcile_label") == "基础业务价值"
      and _bbd.get("cagr_label") == "基线增速(通用)")
check("AC7 explicit 现价−基础=溢价 bridge step present in the tree",
      "− 基础" in _txts and "叙事/期权溢价" in _txts, _txts[:90])
check("AC7 baseline-assumptions step precedes base value, HONESTLY labeled (not '共识')",
      "基线假设" in _txts and "前向 DCF 折现" in _txts and "非公司专属共识" in _txts
      and "共识增速" not in _txts)
check("AC7 base node carries an interpretive 估值地板 line (richer tree)",
      "估值地板" in _txts)
check("AC7 quantified theme-exposure row present (R1)",
      "主题暴露:" in _txts)

# --- AC8: _display carries a reconstructed decode ACTIVITY log (full agent activity)
# rendered in the AGENT panel — built from persisted decode_detail, kind-tagged, and
# HONEST (a deterministic decode must not be labelled an autonomous agent).
_nact = ((db.build_card_display(_nc) or {}).get("activity")) or []
_akinds = {a.get("kind") for a in _nact}
_atext = " ".join(a.get("text", "") for a in _nact)
check("AC8 _display.activity reconstructs the decode steps (non-empty, kind-tagged)",
      len(_nact) >= 4 and {"decision", "computation"} <= _akinds,
      f"n={len(_nact)} kinds={sorted(_akinds)}")
check("AC8 activity covers mode + base value + 对账 (anchor card)",
      "解码模式" in _atext and "基础业务价值" in _atext and "对账" in _atext)
check("AC8 activity is honest — deterministic card NOT labelled '自主选择'",
      "自主选择" not in _atext)

# --- AC9: portfolio theme aggregation + equal-weight default (R1) --------------
# A portfolio of two AI-composite legs (no weights in the input) must apply an
# equal-weight default and aggregate the legs' anchor-mode theme exposures into
# weighted, card-level theme rows (concentration flag + contributing tickers).
# Previously deferred ("#3") — the portfolio page is empty without it.
_pf = decoder.decode_bet(
    "portfolio", "AAA,BBB", "zh",
    fundamentals_fn=lambda t: decoder.Fundamentals(
        ticker=t, current_price=224.0, revenue_ttm=60e9, net_income_ttm=32e9,
        ebitda_ttm=38e9, fcf_ttm=30e9, book_equity=43e9, eps_ttm=2.9,
        shares_outstanding=24.4e9, net_debt=-10e9, beta=1.5, growth_rate=0.5,
        industry="Semiconductors", tags=["GPU accelerator"]),
    hunter=lambda *a, **k: None)
_pw = [(h.ticker, h.weight_pct) for h in (_pf.holdings or [])]
_pte = _pf.theme_exposures or []
check("AC9 portfolio equal-weight default applied (string input carries no weights)",
      len(_pw) == 2 and all(abs((w or 0) - 50.0) < 0.1 for _, w in _pw), f"{_pw}")
check("AC9 portfolio theme aggregation produced weighted rows from anchor legs",
      bool(_pte) and any(getattr(t, "theme", None) for t in _pte),
      f"themes={[(getattr(t, 'theme', None), getattr(t, 'exposure_pct', None)) for t in _pte]}")
check("AC9 aggregated theme flags concentration + lists both contributing tickers",
      any(getattr(t, "is_concentration_risk", False)
          and len(getattr(t, "contributing_tickers", []) or []) == 2 for t in _pte))

print("=" * 72)
print(f"RESULT: {_passed} passed, {_failed} failed")
print("=" * 72)
raise SystemExit(1 if _failed else 0)
