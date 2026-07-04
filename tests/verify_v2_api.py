"""Offline verification for PlayInsight v2 API integration endpoints.

Run from repo root:
    python tests/verify_v2_api.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ["OFFLINE_MODE"] = "1"
os.environ["MIROMIND_API_KEY"] = ""
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import api  # noqa: E402
import db  # noqa: E402
from db import BetCard  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


_passed = 0
_failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"[PASS] {name}" + (f"  | {detail}" if detail else ""))
    else:
        _failed += 1
        print(f"[FAIL] {name}" + (f"  | {detail}" if detail else ""))


def rnd() -> dict:
    return {
        "prices": [70, 85, 100, 115, 130],
        "probs": [0.10, 0.20, 0.30, 0.25, 0.15],
    }


def panel() -> list[dict]:
    return [
        {
            "channel": "cashflow",
            "horizon": "5y",
            "status": "ok",
            "signal": "undervalued",
            "metrics": {
                "anchor_price": 100.0,
                "baseline": 125.0,
                "implied_cagr": 0.22,
                "xray": {
                    "base_rate": {"live": {"percentile": 80}},
                    "scenario_probs": {
                        "by_name": {"bear": 0.1, "base": 0.2, "bull": 0.5, "moonshot": 0.2},
                        "note": "ok",
                    },
                    "wwhtbt": [{"kind": "kill", "value": "revenue growth breaks below base-rate median"}],
                },
            },
        },
        {
            "channel": "distribution",
            "horizon": "1-3m",
            "status": "ok",
            "signal": "neutral",
            "metrics": {
                "spot": 100.0,
                "rnd": rnd(),
                "distribution_band": {"lower": 85.0, "upper": 118.0, "confidence": 0.68},
            },
        },
        {
            "channel": "revision",
            "horizon": "quarterly",
            "status": "ok",
            "metrics": {
                "est_rev_90d": 0.08,
                "price_rev_90d": 0.12,
                "consensus": {"value": 119.0, "point_in_time": False, "status": "honest_empty"},
                "data_source": {"point_in_time": False},
            },
        },
        {
            "channel": "altitude",
            "horizon": "mixed",
            "status": "ok",
            "metrics": {"decomposition": {"company_pct": 0.60}},
        },
    ]


tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
tmp.close()
api.DB_PATH = tmp.name
db.ensure_schema(api.DB_PATH)
tc = TestClient(api.app)

print("=" * 72)
print("PlayInsight v2 API endpoint verification")
print("=" * 72)


with db.connection(api.DB_PATH) as conn:
    rich = BetCard(subject="PLAN", source_type="market", created_at="2026-07-05T01:00:00+00:00")
    rich.decode_detail = {"panel": panel()}
    rich_id = db.save_card(conn, rich)

    empty = BetCard(subject="EMPTY", source_type="market", created_at="2026-07-05T01:01:00+00:00")
    empty.decode_detail = {"mode": "v3_no_panel"}
    empty_id = db.save_card(conn, empty)

    db.insert_panel_observation(
        conn,
        subject="PLAN",
        as_of_date="2026-07-01",
        channel="cashflow",
        horizon="5y",
        metrics={
            "implied_cagr": 0.22,
            "price": {"open": 98.0, "high": 103.0, "low": 97.5, "close": 101.0, "volume": 123456},
            "events": [{"date": "2026-07-01", "type": "earnings", "headline": "quarterly update"}],
        },
        quality="high",
        status="ok",
    )
    db.insert_panel_observation(
        conn,
        subject="PLAN",
        as_of_date="2026-07-01",
        channel="distribution",
        horizon="1-3m",
        metrics={"distribution_band": {"lower": 85.0, "upper": 118.0, "confidence": 0.68}},
        quality="medium",
        status="ok",
    )
    db.insert_panel_observation(
        conn,
        subject="PLAN",
        as_of_date="2026-07-01",
        channel="revision",
        horizon="quarterly",
        metrics={"consensus": {"value": 119.0, "point_in_time": False, "status": "honest_empty"}},
        quality="low",
        status="honest_empty",
    )


print("\n=== cards/{id}/plan ===")
r_missing_plan = tc.get("/api/cards/no-such-card/plan")
check("plan endpoint returns 404 for unknown card",
      r_missing_plan.status_code == 404 and r_missing_plan.json().get("error_code") == "card_not_found",
      str(r_missing_plan.json()))

r_empty_plan = tc.get(f"/api/cards/{empty_id}/plan")
empty_plan = r_empty_plan.json()
check("plan endpoint degrades honestly when decode_detail.panel is absent",
      r_empty_plan.status_code == 200 and empty_plan.get("status") == "degraded"
      and empty_plan.get("plan") is None,
      str(empty_plan))

r_pos = tc.post("/api/positions/events", json={
    "subject": "PLAN",
    "source": "plan_confirmed",
    "plan_card_id": rich_id,
    "side": "long",
    "weight_pct": 12.5,
    "avg_price": 100.0,
    "executed_at": "2026-07-05T10:00:00+00:00",
})
check("position event happy path",
      r_pos.status_code == 200 and r_pos.json().get("position_event_id"),
      str(r_pos.json()))

r_plan = tc.get(f"/api/cards/{rich_id}/plan")
plan_body = r_plan.json()
check("plan endpoint builds local Trade Plan from stored panel",
      r_plan.status_code == 200 and plan_body.get("status") == "ok"
      and isinstance(plan_body.get("plan"), dict)
      and plan_body["position_context"]["has_position"] is True,
      str(plan_body.get("plan", {}).get("stance")))
check("plan endpoint does not need network-generated data",
      plan_body.get("plan", {}).get("kill", {}).get("line") is not None)


print("\n=== chart ===")
r_chart_empty = tc.get("/api/chart/NOOBS")
chart_empty = r_chart_empty.json()
check("chart endpoint returns honest-empty with no stored series",
      r_chart_empty.status_code == 200 and chart_empty.get("status") == "honest_empty"
      and all(not v for v in chart_empty.get("layers", {}).values()),
      str(chart_empty.get("empty_layers")))

r_chart = tc.get(f"/api/chart/PLAN?card_id={rich_id}&as_of=2026-07-05&window=180d")
chart = r_chart.json()
layers = chart.get("layers") or {}
check("chart endpoint assembles stored panel observation points",
      r_chart.status_code == 200 and chart.get("quality") in {"ok", "degraded"}
      and layers.get("price") and layers.get("distribution_band")
      and layers.get("implied_growth") and layers.get("consensus")
      and layers.get("events"),
      str(layers))
check("chart endpoint adds decision kill line only from card/local decision",
      bool(layers.get("kill_lines")) and layers["kill_lines"][0].get("source") == "decision",
      str(layers.get("kill_lines")))

r_chart_mismatch = tc.get(f"/api/chart/OTHER?card_id={rich_id}")
check("chart endpoint rejects mismatched card subject",
      r_chart_mismatch.status_code == 400 and r_chart_mismatch.json().get("error_code") == "bad_request")


print("\n=== positions ===")
r_bad_pos = tc.post("/api/positions/events", json={"subject": "PLAN"})
check("position event bad request for missing fields",
      r_bad_pos.status_code == 400 and r_bad_pos.json().get("error_code") == "bad_request")

r_open = tc.get("/api/positions?status=open")
open_positions = r_open.json().get("positions") or []
check("positions query returns current open ledger facts",
      r_open.status_code == 200 and len(open_positions) == 1 and open_positions[0]["subject"] == "PLAN",
      str(open_positions))

r_close = tc.post("/api/positions/events", json={
    "subject": "PLAN",
    "source": "close_confirmed",
    "side": "flat",
    "weight_pct": 0,
    "executed_at": "2026-07-06T10:00:00+00:00",
})
check("position close event infers closed status",
      r_close.status_code == 200 and r_close.json().get("status") == "closed",
      str(r_close.json()))

r_open_after_close = tc.get("/api/positions?status=open")
r_closed = tc.get("/api/positions?status=closed")
check("positions current list does not report stale open after close",
      r_open_after_close.status_code == 200 and r_open_after_close.json().get("positions") == [],
      str(r_open_after_close.json()))
check("positions current list reports latest closed state",
      r_closed.status_code == 200 and r_closed.json().get("positions", [{}])[0].get("status") == "closed",
      str(r_closed.json()))

r_bad_status = tc.get("/api/positions?status=stale")
check("positions query rejects invalid status",
      r_bad_status.status_code == 400 and r_bad_status.json().get("error_code") == "bad_request")


print("\n=== panel backfill ===")
r_bad_backfill = tc.post("/api/panel/backfill", json={"subjects": [], "channels": ["cashflow"]})
check("backfill create validates required shape",
      r_bad_backfill.status_code == 400 and r_bad_backfill.json().get("error_code") == "bad_request")

r_backfill = tc.post("/api/panel/backfill", json={
    "run_id": "bf_test_plan",
    "subjects": ["PLAN", "EMPTY"],
    "channels": ["cashflow", "distribution"],
    "start_date": "2026-01-01",
    "end_date": "2026-07-05",
    "credit_budget": 80,
})
backfill = r_backfill.json().get("run") or {}
check("backfill create records queued MVP run without starting work",
      r_backfill.status_code == 200 and backfill.get("run_id") == "bf_test_plan"
      and backfill.get("status") == "queued"
      and backfill.get("observations_written") == 0,
      str(backfill))

r_get_backfill = tc.get("/api/panel/backfill/bf_test_plan")
check("backfill get returns stored run",
      r_get_backfill.status_code == 200
      and r_get_backfill.json().get("run", {}).get("subjects") == ["PLAN", "EMPTY"],
      str(r_get_backfill.json()))

r_missing_backfill = tc.get("/api/panel/backfill/no_such_run")
check("backfill get returns 404 for unknown run",
      r_missing_backfill.status_code == 404
      and r_missing_backfill.json().get("error_code") == "backfill_not_found",
      str(r_missing_backfill.json()))


print("\n" + "=" * 72)
print(f"RESULT: {_passed} passed, {_failed} failed")
print("=" * 72)
raise SystemExit(1 if _failed else 0)
