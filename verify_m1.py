"""M1 acceptance verification — Bet Card data layer.

Self-contained: uses a throwaway temp DB file (never touches pricelens.db) and
prints one line per acceptance criterion as `[ACx] ... : PASS/FAIL`.

Run:  python verify_m1.py
Exit code 0 iff every AC passes.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile

import db
from db import (
    BetCard, Holding, ThemeExposure,
    SINGLE, PORTFOLIO, SOURCE_MARKET, SOURCE_OPINION, SOURCE_PORTFOLIO,
    make_series_key, save_card, get_card, list_cards, delete_card,
    card_to_json, card_from_json, card_from_row,
)

_results: list[tuple[str, bool, str]] = []


def check(tag: str, ok: bool, note: str = "") -> None:
    _results.append((tag, bool(ok), note))


def _seed_legacy_run_without_anchor(db_path: str) -> int:
    """Simulate a v1 (pre-anchor) DB: create the runs table by the OLD DDL, drop
    the anchor columns scenario, insert a legacy run, THEN let init_db migrate."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    # Minimal old-style runs table (no anchor columns).
    conn.execute(
        """
        CREATE TABLE runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            company_name TEXT,
            generated_at TEXT NOT NULL,
            mode TEXT NOT NULL,
            current_price REAL NOT NULL,
            baseline_dcf REAL,
            total_cost_usd REAL DEFAULT 0,
            decoder_cached INTEGER DEFAULT 0
        )
        """
    )
    cur = conn.execute(
        "INSERT INTO runs (ticker, company_name, generated_at, mode, current_price)"
        " VALUES (?, ?, ?, ?, ?)",
        ("LEGACY", "Legacy Co", "2026-01-01T00:00:00Z", "standard", 123.45),
    )
    rid = cur.lastrowid
    conn.commit()
    conn.close()
    return rid


def main() -> int:
    tmpdir = tempfile.mkdtemp(prefix="m1_verify_")
    db_path = os.path.join(tmpdir, "m1_test.db")

    # ---- Pre-stage: seed a legacy run (no anchor cols) so AC3 backfill is real.
    legacy_run_id = _seed_legacy_run_without_anchor(db_path)

    # AC1: init_db idempotent — run twice, second must not error; new tables +
    # columns present afterward.
    ac1_ok = True
    ac1_note = ""
    try:
        conn = db.init_db(db_path)          # 1st (also runs the anchor migration)
        conn.close()
        conn = db.init_db(db_path)          # 2nd — must be a no-op, no error
    except Exception as exc:  # noqa: BLE001
        ac1_ok = False
        ac1_note = f"init_db raised: {exc}"
        check("AC1", ac1_ok, ac1_note)
        _report()
        return 1

    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    needed_tables = {"bet_cards", "portfolio_holdings", "theme_exposures", "activity_logs"}
    runs_cols = {r[1] for r in conn.execute("PRAGMA table_info(runs)").fetchall()}
    ac1_ok = needed_tables.issubset(tables) and {"anchor_price", "anchor_type"}.issubset(runs_cols)
    ac1_note = f"tables={sorted(needed_tables & tables)} runs_anchor_cols={sorted({'anchor_price','anchor_type'} & runs_cols)}"
    check("AC1", ac1_ok, ac1_note)

    # AC2: new tables have the expected key columns.
    bc_cols = {r[1] for r in conn.execute("PRAGMA table_info(bet_cards)").fetchall()}
    te_cols = {r[1] for r in conn.execute("PRAGMA table_info(theme_exposures)").fetchall()}
    al_cols = {r[1] for r in conn.execute("PRAGMA table_info(activity_logs)").fetchall()}
    ph_cols = {r[1] for r in conn.execute("PRAGMA table_info(portfolio_holdings)").fetchall()}
    ac2_ok = (
        {"card_id", "subject", "source_type", "card_kind", "source_ref",
         "series_key", "created_at", "run_id"}.issubset(bc_cols)
        and {"card_id", "theme", "exposure_pct", "contributing_tickers",
             "is_concentration_risk"}.issubset(te_cols)
        and {"job_id", "source_ref", "events_json", "created_at"}.issubset(al_cols)
        and {"card_id", "ticker", "weight_pct", "run_id"}.issubset(ph_cols)
    )
    check("AC2", ac2_ok, "bet_cards/theme_exposures/activity_logs/portfolio_holdings cols present")

    # AC3: runs anchor backfill — no NULLs; legacy row got current_price + market.
    null_anchor = conn.execute(
        "SELECT COUNT(*) FROM runs WHERE anchor_price IS NULL OR anchor_type IS NULL"
    ).fetchone()[0]
    legacy = conn.execute(
        "SELECT anchor_price, anchor_type, current_price FROM runs WHERE id = ?",
        (legacy_run_id,),
    ).fetchone()
    ac3_ok = (
        null_anchor == 0
        and legacy is not None
        and legacy["anchor_price"] == legacy["current_price"]
        and legacy["anchor_type"] == "market"
    )
    check("AC3", ac3_ok, f"null_anchor_rows={null_anchor} legacy_anchor={legacy['anchor_price']}/{legacy['anchor_type']}")

    # AC4: BetCard type has single + portfolio sub-types; single bet nullable.
    single_nobet = BetCard(subject="NVDA", source_type=SOURCE_MARKET)  # bet defaults None
    single_bet = BetCard(subject="NVDA", source_type=SOURCE_MARKET, bet=170.0)
    portfolio = BetCard(subject="My Portfolio", source_type=SOURCE_PORTFOLIO,
                        card_kind=PORTFOLIO)
    ac4_ok = (
        single_nobet.card_kind == SINGLE and single_nobet.bet is None
        and single_bet.bet == 170.0
        and portfolio.card_kind == PORTFOLIO
        and single_nobet.series_key == make_series_key("NVDA", SOURCE_MARKET)
    )
    check("AC4", ac4_ok, "single(bet None ok)+portfolio sub-types")

    # AC5: save_card single -> get_card round-trip, fields identical.
    # Anchor the single card to the legacy run to exercise run_id FK.
    sc = BetCard(subject="COST", source_type=SOURCE_MARKET, bet=900.0,
                 run_id=legacy_run_id, source_ref="market price $900",
                 trade_date="2026-05-29")
    sc_id = save_card(conn, sc)
    got = get_card(conn, sc_id)
    ac5_ok = (
        got is not None
        and got.card_id == sc.card_id
        and got.subject == sc.subject
        and got.source_type == sc.source_type
        and got.card_kind == sc.card_kind
        and got.source_ref == sc.source_ref
        and got.bet == sc.bet
        and got.run_id == sc.run_id
        and got.series_key == sc.series_key
        and got.trade_date == sc.trade_date
        and got.created_at == sc.created_at
    )
    check("AC5", ac5_ok, "single card save->get round-trip identical")

    # AC6: same (subject, source_type) Market card same trading day -> dedup hit,
    # no second card created.
    dup = BetCard(subject="COST", source_type=SOURCE_MARKET, bet=905.0,
                  trade_date="2026-05-29")
    dup_id = save_card(conn, dup)
    cost_market_count = conn.execute(
        "SELECT COUNT(*) FROM bet_cards WHERE series_key = ?",
        (make_series_key("COST", SOURCE_MARKET),),
    ).fetchone()[0]
    ac6_ok = dup_id == sc_id and cost_market_count == 1
    check("AC6", ac6_ok, f"dedup returned existing id={dup_id == sc_id}, count={cost_market_count}")

    # AC6b (boundary of dedup): different trading day -> NOT deduped.
    next_day = BetCard(subject="COST", source_type=SOURCE_MARKET, bet=910.0,
                       trade_date="2026-05-30")
    next_id = save_card(conn, next_day)
    cost_market_count2 = conn.execute(
        "SELECT COUNT(*) FROM bet_cards WHERE series_key = ?",
        (make_series_key("COST", SOURCE_MARKET),),
    ).fetchone()[0]
    ac6b_ok = next_id != sc_id and cost_market_count2 == 2
    check("AC6b", ac6b_ok, f"new-day not deduped, count={cost_market_count2}")

    # AC7: list_cards groups by series_key=(subject, source_type).
    cost_series = list_cards(conn, subject="COST", source_type=SOURCE_MARKET)
    cost_series2 = list_cards(conn, series_key=make_series_key("COST", SOURCE_MARKET))
    ac7_ok = (
        len(cost_series) == 2
        and all(c.series_key == make_series_key("COST", SOURCE_MARKET) for c in cost_series)
        and len(cost_series2) == 2
        # newest-first ordering
        and cost_series[0].trade_date >= cost_series[1].trade_date
    )
    check("AC7", ac7_ok, f"series grouping returned {len(cost_series)} (subject+source == series_key)")

    # AC8: portfolio card save -> portfolio_holdings + theme_exposures rows land,
    # run_id associations correct.
    pf = BetCard(
        subject="AI Infra Book", source_type=SOURCE_PORTFOLIO, card_kind=PORTFOLIO,
        holdings=[
            Holding(ticker="NVDA", weight_pct=40.0, run_id=legacy_run_id),
            Holding(ticker="AVGO", weight_pct=36.0),
            Holding(ticker="MSFT", weight_pct=24.0),
        ],
        theme_exposures=[
            ThemeExposure(theme="AI infrastructure", exposure_pct=76.0,
                          contributing_tickers=["NVDA", "AVGO"],
                          is_concentration_risk=True),
            ThemeExposure(theme="Cloud", exposure_pct=24.0,
                          contributing_tickers=["MSFT"]),
        ],
    )
    pf_id = save_card(conn, pf)
    h_rows = conn.execute(
        "SELECT ticker, weight_pct, run_id FROM portfolio_holdings WHERE card_id = ? ORDER BY id",
        (pf_id,),
    ).fetchall()
    t_rows = conn.execute(
        "SELECT theme, exposure_pct, is_concentration_risk FROM theme_exposures WHERE card_id = ?",
        (pf_id,),
    ).fetchall()
    ac8_ok = (
        len(h_rows) == 3
        and h_rows[0]["ticker"] == "NVDA" and h_rows[0]["run_id"] == legacy_run_id
        and h_rows[1]["run_id"] is None
        and len(t_rows) == 2
        and any(r["is_concentration_risk"] == 1 for r in t_rows)
    )
    check("AC8", ac8_ok, f"holdings={len(h_rows)} themes={len(t_rows)} run_id wired")

    # AC8b: single card in anchor mode also carries theme_exposures (R1, shared table).
    single_anchor = BetCard(
        subject="VRT", source_type=SOURCE_MARKET, bet=120.0, trade_date="2026-05-29",
        theme_exposures=[ThemeExposure(theme="AI power/cooling", exposure_pct=55.0,
                                       contributing_tickers=["VRT"])],
    )
    sa_id = save_card(conn, single_anchor)
    sa_back = get_card(conn, sa_id)
    ac8b_ok = (
        sa_back is not None and sa_back.card_kind == SINGLE
        and len(sa_back.theme_exposures) == 1
        and sa_back.theme_exposures[0].theme == "AI power/cooling"
    )
    check("AC8b", ac8b_ok, "single card shares theme_exposures table (R1)")

    # AC9: card_to_json / card_from_row (and card_from_json) lossless both ways.
    pf_json = card_to_json(get_card(conn, pf_id))
    pf_rebuilt = card_from_json(pf_json)
    pf_json2 = card_to_json(pf_rebuilt)
    # row-based path
    row = conn.execute("SELECT * FROM bet_cards WHERE card_id = ?", (pf_id,)).fetchone()
    pf_from_row_json = card_to_json(card_from_row(conn, row))
    ac9_ok = (pf_json == pf_json2 == pf_from_row_json)
    check("AC9", ac9_ok, "to_json/from_json/from_row all agree")

    # AC10a: Opinion card with NULL bet — can store + read, no error.
    op = BetCard(subject="Cathie Wood TSLA call", source_type=SOURCE_OPINION,
                 bet=None, source_ref="opinion: TSLA to $2600 by 2029")
    op_id = save_card(conn, op)
    op_back = get_card(conn, op_id)
    ac10a_ok = op_back is not None and op_back.bet is None
    check("AC10a", ac10a_ok, "opinion card with NULL bet stores+reads")

    # AC10b: get_card on non-existent id -> None (no exception).
    try:
        missing = get_card(conn, "does-not-exist-xyz")
        ac10b_ok = missing is None
        note10b = "returns None"
    except Exception as exc:  # noqa: BLE001
        ac10b_ok = False
        note10b = f"raised {exc}"
    check("AC10b", ac10b_ok, note10b)

    # AC10c: delete_card removes card + children (cascade); returns bool sensibly.
    deleted = delete_card(conn, pf_id)
    still_holdings = conn.execute(
        "SELECT COUNT(*) FROM portfolio_holdings WHERE card_id = ?", (pf_id,)
    ).fetchone()[0]
    still_themes = conn.execute(
        "SELECT COUNT(*) FROM theme_exposures WHERE card_id = ?", (pf_id,)
    ).fetchone()[0]
    deleted_missing = delete_card(conn, "does-not-exist-xyz")
    ac10c_ok = (
        deleted is True and still_holdings == 0 and still_themes == 0
        and deleted_missing is False
    )
    check("AC10c", ac10c_ok, f"delete cascades (h={still_holdings},t={still_themes}); missing->False")

    conn.close()
    return _report()


def _report() -> int:
    print("\n=== M1 Acceptance Verification ===")
    all_pass = True
    for tag, ok, note in _results:
        status = "PASS" if ok else "FAIL"
        if not ok:
            all_pass = False
        print(f"[{tag}] {status}  {note}")
    print("=" * 36)
    print("RESULT:", "ALL PASS" if all_pass else "SOME FAILED")
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
