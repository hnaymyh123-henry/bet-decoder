"""V2 schema/data-layer verification (schema v4).

Deterministic, zero API/network. Covers the SPEC_A groundwork:
new schema v4 metadata, panel_observations, position_ledger,
panel_backfill_runs, DAO helpers, and v3 decode_detail compatibility.

Run:  python tests/verify_v2_schema.py
"""
from __future__ import annotations

import db
from db import BetCard

_passed = 0
_failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global _passed, _failed
    status = "PASS" if cond else "FAIL"
    if cond:
        _passed += 1
    else:
        _failed += 1
    print(f"[{status}] {name}" + (f" | {detail}" if detail else ""))


def _columns(conn, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


print("=" * 72)
print("schema v4 / v2 data-layer verification")
print("=" * 72)

conn = db.init_db(":memory:")

# --- AC1: schema v4 metadata + tables are present and idempotent -------------
version = conn.execute(
    "SELECT value FROM schema_meta WHERE key = 'version'"
).fetchone()["value"]
check("AC1 schema_meta version bumped to v4", version == "4", f"version={version}")

expected_tables = {
    "panel_observations",
    "position_ledger",
    "panel_backfill_runs",
}
actual_tables = {
    r["name"]
    for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall()
}
check("AC1 v4 tables exist", expected_tables <= actual_tables)

db._apply_schema(conn)
db._apply_schema(conn)
check("AC1 _apply_schema remains idempotent",
      conn.execute("SELECT COUNT(*) AS n FROM schema_meta").fetchone()["n"] >= 2)

check("AC1 panel_observations columns match SPEC_A",
      {
          "id", "subject", "as_of_date", "channel", "horizon",
          "metrics_json", "quality", "status", "cost_credits", "created_at",
      } <= _columns(conn, "panel_observations"))
check("AC1 position_ledger columns match SPEC_A",
      {
          "id", "subject", "source", "plan_card_id", "side", "quantity",
          "weight_pct", "avg_price", "executed_at", "status", "note",
          "created_at",
      } <= _columns(conn, "position_ledger"))
check("AC1 panel_backfill_runs columns match SPEC_A",
      {
          "run_id", "subjects_json", "channels_json", "start_date", "end_date",
          "status", "credit_budget", "credits_spent", "observations_written",
          "skipped_json", "error_json", "created_at", "completed_at",
      } <= _columns(conn, "panel_backfill_runs"))

# --- AC2: panel observations are append-only and latest-read is deterministic -
older_id = db.insert_panel_observation(
    conn,
    subject="NVDA",
    as_of_date="2026-07-03",
    channel="distribution",
    horizon="1-3m",
    metrics={"prob_of_target": {"target": 300, "prob": 0.05}},
    quality="medium",
    status="ok",
    cost_credits=2,
    created_at="2026-07-03T10:00:00+00:00",
)
newer_id = db.insert_panel_observation(
    conn,
    subject="NVDA",
    as_of_date="2026-07-03",
    channel="distribution",
    horizon="1-3m",
    metrics={"prob_of_target": {"target": 300, "prob": 0.08}},
    quality="high",
    status="ok",
    cost_credits=3,
    created_at="2026-07-03T11:00:00+00:00",
)
cashflow_id = db.insert_panel_observation(
    conn,
    subject="NVDA",
    as_of_date="2026-07-02",
    channel="cashflow",
    horizon="5y",
    metrics={"implied_cagr": 0.38},
    quality="high",
    status="ok",
    created_at="2026-07-02T11:00:00+00:00",
)
history = db.list_panel_observations(conn, subject="NVDA", channel="distribution")
latest_dist = db.get_latest_panel_observations(
    conn, "NVDA", channel="distribution"
)
latest_all = db.get_latest_panel_observations(conn, "NVDA")
check("AC2 panel observations append duplicate subject/date/channel rows",
      len(history) == 2 and {older_id, newer_id} == {r["id"] for r in history})
check("AC2 latest channel observation picks newest created_at/id",
      len(latest_dist) == 1
      and latest_dist[0]["id"] == newer_id
      and latest_dist[0]["metrics"]["prob_of_target"]["prob"] == 0.08)
check("AC2 latest observations return latest per channel",
      {r["channel"]: r["id"] for r in latest_all}
      == {"cashflow": cashflow_id, "distribution": newer_id})

# --- AC3: position ledger append/query helpers ------------------------------
plan = BetCard(subject="NVDA", source_type="market")
plan_id = db.save_card(conn, plan)
open_id = db.record_position_ledger_event(
    conn,
    subject="NVDA",
    source="plan_confirmed",
    plan_card_id=plan_id,
    side="long",
    quantity=10,
    weight_pct=12.5,
    avg_price=214.86,
    executed_at="2026-07-03T14:30:00+00:00",
    status="open",
    note="confirmed in UI",
    created_at="2026-07-03T14:31:00+00:00",
)
closed_id = db.record_position_ledger_event(
    conn,
    subject="NVDA",
    source="close_confirmed",
    side="flat",
    executed_at="2026-07-04T14:30:00+00:00",
    status="closed",
    created_at="2026-07-04T14:31:00+00:00",
)
events = db.list_position_ledger_events(conn, subject="NVDA")
open_events = db.list_position_ledger_events(conn, subject="NVDA", status="open")
latest_event = db.get_latest_position_ledger_event(conn, "NVDA")
check("AC3 position ledger records manual facts",
      {open_id, closed_id} == {e["id"] for e in events})
check("AC3 position ledger filters by status",
      len(open_events) == 1 and open_events[0]["id"] == open_id)
check("AC3 latest position event uses executed_at ordering",
      latest_event["id"] == closed_id and latest_event["side"] == "flat")
check("AC3 position event can link to a TradePlan card id",
      open_events[0]["plan_card_id"] == plan_id)

# --- AC4: backfill run create/update/query helpers --------------------------
rid = db.record_panel_backfill_run(
    conn,
    run_id="bf-20260704-nvda",
    subjects=["NVDA", "MSFT"],
    channels=["cashflow", "distribution"],
    start_date="2026-01-01",
    end_date="2026-07-04",
    status="planned",
    credit_budget=80,
    created_at="2026-07-04T00:00:00+00:00",
)
created = db.get_panel_backfill_run(conn, rid)
updated_ok = db.update_panel_backfill_run(
    conn,
    rid,
    status="partial",
    credits_spent=17,
    observations_written=42,
    skipped=[{"subject": "MSFT", "date": "2026-01-02", "channel": "distribution",
              "reason": "no_chain"}],
    error={"kind": "provider_partial"},
    completed_at="2026-07-04T00:10:00+00:00",
)
updated = db.get_panel_backfill_run(conn, rid)
partial = db.list_panel_backfill_runs(conn, status="partial")
check("AC4 backfill run stores JSON lists as structured values",
      created["subjects"] == ["NVDA", "MSFT"]
      and created["channels"] == ["cashflow", "distribution"])
check("AC4 backfill run update patches mutable fields",
      updated_ok and updated["status"] == "partial"
      and updated["credits_spent"] == 17
      and updated["observations_written"] == 42
      and updated["skipped"][0]["reason"] == "no_chain"
      and updated["error"]["kind"] == "provider_partial")
check("AC4 backfill run list filters by status",
      len(partial) == 1 and partial[0]["run_id"] == rid)

# --- AC5: existing card behavior survives, v3 detail reads as v4 superset ----
v3 = BetCard(subject="COST", source_type="market",
             created_at="2026-07-04T01:00:00+00:00",
             trade_date="2026-07-04")
v3.decode_detail = {
    "mode": "anchor_primary",
    "anchor_price": 900.0,
    "primary_lens": {"lens": "dcf", "implied_cagr": 0.18},
    "cross_lenses": [{"lens": "pe", "implied": 54.2}],
    "anchor_mode": {"base_business_value": 700.0},
    "evidence": {"briefs": []},
    "narrative_premium": 0.22,
}
v3_id = db.save_card(conn, v3)
loaded = db.get_card(conn, v3_id)
dd = loaded.decode_detail
check("AC5 v3 decode_detail fields are preserved",
      dd["primary_lens"]["lens"] == "dcf"
      and dd["cross_lenses"][0]["lens"] == "pe"
      and dd["narrative_premium"] == 0.22)
check("AC5 v3 decode_detail is normalized to v4 superset on read",
      isinstance(dd.get("panel"), list) and dd["panel"][0]["channel"] == "cashflow"
      and dd["investigation"]["trace"] == []
      and dd["reconciliation"]["conviction_input"]["net_verdict"] == "unknown"
      and dd["series_link"]["prev_card_id"] is None
      and dd["decision"] is None)

same_day = BetCard(subject="COST", source_type="market",
                   created_at=v3.created_at, trade_date=v3.trade_date)
same_day_id = db.save_card(conn, same_day)
check("AC5 existing same-day market-card dedup still works",
      same_day_id == v3_id)

print(f"RESULT: {_passed} passed, {_failed} failed")
raise SystemExit(1 if _failed else 0)
