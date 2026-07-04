"""Bet Decoder FastAPI backend.

Serves cached pipeline outputs to the frontend. Does NOT trigger any LLM calls.

Start the server:
    uvicorn api:app --reload --port 8000
"""
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

import activity
import db
from sse import stream_evidence_mock

# Tickers in path segments must match this (defense-in-depth — yfinance / DB
# lookups should never see arbitrary path content). Upper letters, digits,
# dot and hyphen (e.g. BRK.B, RDS-A), 1–10 chars.
TICKER_RE = re.compile(r"^[A-Z0-9.\-]{1,10}$")


def _bad_request(message: str) -> JSONResponse:
    return JSONResponse(status_code=400,
                        content={"error_code": "bad_request", "message": message})


def _require_dict(body) -> dict | None:
    """Return body if it's a dict, else None (caller returns _bad_request).
    A list / str / number / oversized-non-object body must 400, not 500."""
    return body if isinstance(body, dict) else None


def _valid_ticker(ticker: str) -> bool:
    return bool(TICKER_RE.match(ticker or ""))


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _subject_key(subject: str) -> str:
    return str(subject or "").strip().upper()


def _latest_position_context(conn, subject: str) -> dict:
    latest = db.get_latest_position_ledger_event(conn, _subject_key(subject))
    if not latest:
        return {
            "has_position": False,
            "position_source": "none",
            "position_freshness": "unknown",
            "latest_event": None,
        }
    has_position = latest.get("status") == "open" and latest.get("side") != "flat"
    # SPEC F2: freshness = fresh (position_ledger 有确认记录) | stale_snapshot
    # (无 ledger，只有 decode 时的 snapshot) | unknown (无任何记录)
    freshness = "fresh" if latest.get("source") in ("plan_confirmed", "close_confirmed", "manual_adjust") else "stale_snapshot"
    return {
        "has_position": bool(has_position),
        "position_source": "position_ledger",
        "position_freshness": freshness,
        "latest_event": latest,
    }


def _panel_from_card(card) -> list[dict]:
    detail = getattr(card, "decode_detail", None) or {}
    panel = detail.get("panel")
    return panel if isinstance(panel, list) else []


def _extract_card_decision(card, position_context: dict | None = None) -> dict | None:
    detail = getattr(card, "decode_detail", None) or {}
    decision = detail.get("decision")
    if isinstance(decision, dict):
        return decision
    panel = _panel_from_card(card)
    if not panel:
        return None
    import trade_plan

    return trade_plan.build_trade_plan(
        panel,
        position_context or {},
        detail.get("reconciliation") if isinstance(detail.get("reconciliation"), dict) else None,
    )


def _empty_chart_layers() -> dict:
    return {
        "price": [],
        "distribution_band": [],
        "implied_growth": [],
        "consensus": [],
        "events": [],
        "kill_lines": [],
    }


def _chart_empty_layers(layers: dict) -> list[dict]:
    reasons = {
        "price": "no stored price history",
        "distribution_band": "no stored distribution history",
        "implied_growth": "no stored implied-growth history",
        "consensus": "point-in-time consensus cold start",
        "events": "no stored event history",
        "kill_lines": "no decision or KILL line for this chart",
    }
    return [
        {"layer": name, "reason": reasons.get(name, "no stored data")}
        for name, points in layers.items()
        if not points
    ]


def _append_chart_point(layers: dict, observation: dict) -> None:
    date = observation.get("as_of_date")
    channel = observation.get("channel")
    metrics = observation.get("metrics") or {}

    price = metrics.get("price") or metrics.get("ohlc") or metrics.get("price_ohlc")
    if isinstance(price, dict):
        point = {"date": date, "source": "panel_observations"}
        for key in ("open", "high", "low", "close", "volume"):
            if key in price:
                point[key] = price[key]
        if "close" in point:
            layers["price"].append(point)

    band = metrics.get("distribution_band") or metrics.get("band")
    if channel == "distribution" and isinstance(band, dict):
        point = {"date": date, "source": "panel_observations"}
        for key in ("lower", "upper", "confidence"):
            if key in band:
                point[key] = band[key]
        if "lower" in point and "upper" in point:
            layers["distribution_band"].append(point)

    growth = metrics.get("implied_growth", metrics.get("implied_cagr"))
    if channel == "cashflow" and isinstance(growth, (int, float)):
        layers["implied_growth"].append({
            "date": date,
            "value": float(growth),
            "source": "panel_observations",
        })

    consensus = metrics.get("consensus")
    if channel == "revision" and isinstance(consensus, dict):
        point = {"date": date, "source": "panel_observations"}
        for key in ("value", "point_in_time", "status"):
            if key in consensus:
                point[key] = consensus[key]
        layers["consensus"].append(point)

    events = metrics.get("events")
    if isinstance(events, list):
        for event in events:
            if isinstance(event, dict):
                layers["events"].append({"date": event.get("date", date), **event})


def _position_event_response(event_id: int, subject: str, status: str) -> dict:
    return {
        "position_event_id": event_id,
        "subject": _subject_key(subject),
        "status": status,
        "position_source": "position_ledger",
    }

# Common SSE response headers. X-Accel-Buffering:no defeats nginx proxy
# buffering; Cache-Control:no-cache + Connection:keep-alive keep the stream
# open and unbuffered. These (plus per-frame yields in the generator) are the
# server side of the bug #34 fix — the stream must never be buffered.
SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _offline_mode_enabled() -> bool:
    return os.environ.get("OFFLINE_MODE", "").lower() in ("1", "true", "yes")

OUTPUTS_DIR = Path(__file__).parent / "outputs"
APP_HTML_PATH = Path(__file__).parent / "app.html"
PRICE_HISTORY_CACHE_DIR = Path(__file__).parent / "cache" / "price_history"
PRICE_HISTORY_TTL_SECONDS = 24 * 60 * 60  # 1 day

app = FastAPI(title="Bet Decoder API")

# SQLite-backed storage (v0.6). FastAPI runs sync endpoints in a threadpool,
# and sqlite3 connections are bound to the thread that created them, so we open
# a FRESH connection per request and always close it (db.connection context
# manager). The schema/migration DDL runs ONCE at process startup
# (ensure_schema), not on every request — the previous _db() = init_db() opened
# a never-closed connection AND re-ran the full DDL + migration per request
# (connection leak + wasted work).
DB_PATH = "pricelens.db"


def _conn_factory():
    """Thread-safe fresh-connection factory. Safe to call from a background
    worker thread — each thread gets its own connection. Calls ensure_schema
    first (cheap set-membership check after the first call) so a worker-thread
    connection never races ahead of schema creation when the startup hook hasn't
    run (e.g. a TestClient built without a `with` block)."""
    db.ensure_schema(DB_PATH)
    return db.get_connection(DB_PATH)


@app.on_event("startup")
def _on_startup() -> None:
    # Build/upgrade the schema once for this process. Per-request handlers then
    # use the lightweight get_connection / connection (no DDL).
    db.ensure_schema(DB_PATH)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(HTTPException)
async def _http_exception_handler(request: Request, exc: HTTPException):
    """Normalize any HTTPException into the unified ``{error_code, message}``
    envelope (API_CONTRACT §0). Some legacy endpoints still raise HTTPException
    with a plain ``detail`` string; this reshapes them so every error body
    carries an ``error_code`` + ``message`` regardless of how it was raised."""
    detail = exc.detail
    if isinstance(detail, dict) and "error_code" in detail:
        body = detail
    else:
        code_by_status = {
            400: "bad_request", 404: "not_found", 409: "conflict",
            502: "upstream_error", 503: "offline_mode",
        }
        body = {
            "error_code": code_by_status.get(exc.status_code, "error"),
            "message": detail if isinstance(detail, str) else str(detail),
        }
    return JSONResponse(status_code=exc.status_code, content=body)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/tickers")
def list_tickers():
    with db.connection(DB_PATH) as conn:
        return {"tickers": db.list_tickers(conn)}


@app.get("/api/decode/{ticker}")
def get_decode(ticker: str):
    ticker_upper = ticker.upper()
    if not _valid_ticker(ticker_upper):
        raise HTTPException(status_code=400, detail={
            "error_code": "bad_request", "message": f"Invalid ticker {ticker!r}."})
    with db.connection(DB_PATH) as conn:
        data = db.get_latest_run(conn, ticker_upper)
    if data is None:
        raise HTTPException(status_code=404, detail={
            "error_code": "no_cached_decode",
            "message": f"No cached decode for {ticker_upper}. Decode it first via POST /api/decode, or run prerun_demo.py to populate the demo cache.",
        })
    return JSONResponse(content=data)


@app.get("/api/decode/{ticker}/short-term")
def get_short_term(ticker: str):
    """Latest non-null short-term attribution for {ticker}. Window-agnostic."""
    ticker_upper = ticker.upper()
    if not _valid_ticker(ticker_upper):
        raise HTTPException(status_code=400, detail={
            "error_code": "bad_request", "message": f"Invalid ticker {ticker!r}."})
    with db.connection(DB_PATH) as conn:
        st = db.get_latest_run_with_short_term(conn, ticker_upper)
    if st is None:
        raise HTTPException(status_code=404, detail={
            "error_code": "no_cached_decode",
            "message": f"No short-term attribution available for {ticker_upper}.",
        })
    return JSONResponse(content=st)


@app.get("/api/offline-mode")
def get_offline_mode():
    return {"offline": _offline_mode_enabled()}


@app.get("/api/stream/evidence/{ticker}/{assumption_id}")
async def stream_evidence(
    ticker: str, assumption_id: str, text: str = "", mock: bool = True
):
    """SSE evidence stream. Current behavior is mock-only; live wiring deferred to Phase C.
    The `mock` query param is accepted but ignored — Phase C will honor it."""
    if _offline_mode_enabled():
        return JSONResponse(
            status_code=503,
            content={
                "error": "offline mode active",
                "retry_with": "set OFFLINE_MODE=false in env",
            },
        )
    stream = stream_evidence_mock(ticker.upper(), assumption_id, text)
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ===========================================================================
# Module 5 — Agent activity stream (SSE).
#
# Three surfaces:
#   GET  /api/stream/activity/{job_id}     replay a persisted job (timed)
#   POST /api/stream/decode                live: decode_bet → activity SSE
#   POST /api/stream/synthesize            live: synthesize_cards → activity SSE
#
# The live endpoints run the engine through a single serial JobQueue so the feed
# only ever plays ONE coherent sequence at a time (a concurrent request waits).
# Every job ends with a terminal event (done|error). All events are persisted to
# activity_logs for replay. NO real LLM is required — decode/synthesize fall back
# to their deterministic / cached paths.
# ===========================================================================


@app.get("/api/stream/activity/{job_id}")
async def stream_activity_replay(job_id: str, speed: float = 1.0):
    """Replay a persisted activity job as a timed SSE stream.

    Honors the original inter-event timing (scaled by ``speed``). Unknown /
    empty job ⇒ a single synthetic error-terminal frame so the client never
    hangs (bug #34 class: a stream that opens but never closes)."""
    with db.connection(DB_PATH) as conn:
        events = activity.get_activity_log(conn, job_id)
    stream = activity.replay_activity_stream(events, speed=speed)
    return StreamingResponse(stream, media_type="text/event-stream", headers=SSE_HEADERS)


@app.post("/api/stream/decode")
async def stream_decode(body=Body(default=None)):
    """Live-decode a single/portfolio bet, streaming the agent's reasoning as an
    activity SSE. Body: {source_type, source_input, lang?}. The decoded card is
    persisted by the front-end's /api/decode path; this endpoint streams the
    *process* and persists the event log.

    Serialized via the process-wide JobQueue (default_queue) inside
    live_activity_stream — a second concurrent live request waits its turn
    instead of running a parallel LLM job. The queue worker thread persists with
    its OWN connection (conn_factory), never one created on this event-loop
    thread."""
    if _offline_mode_enabled():
        return JSONResponse(
            status_code=503,
            content={"error_code": "offline_mode", "message": "OFFLINE_MODE active; live decode refused."},
        )
    body = _require_dict(body)
    if body is None:
        return _bad_request("request body must be a JSON object.")
    source_type = body.get("source_type")
    source_input = body.get("source_input")
    lang = body.get("lang", "zh")
    if not source_type or source_input is None:
        return _bad_request("source_type and source_input are required.")

    import decoder

    job_id = body.get("job_id") or _new_job_id()
    subject = source_input if isinstance(source_input, str) else "portfolio"

    def work(emit, cancel=None):
        # Opens its own connection on the queue-worker thread (where work runs)
        # and closes it — no cross-thread reuse, no leak. The engine ignores
        # `cancel` today; the signature lets run_job forward the disconnect
        # signal so a future cancel-aware decode can short-circuit.
        conn = _conn_factory()
        try:
            return decoder.decode_bet(source_type, source_input, lang, emit=emit,
                                      conn=conn)
        finally:
            conn.close()

    stream = activity.live_activity_stream(
        work, job_id=job_id, source_ref=str(subject),
        conn_factory=_conn_factory, done_text="解码完成",
    )
    return StreamingResponse(stream, media_type="text/event-stream", headers=SSE_HEADERS)


@app.post("/api/stream/synthesize")
async def stream_synthesize(body=Body(default=None)):
    """Live cross-card synthesis, streaming the relation-engine's steps as an
    activity SSE. Body: {card_ids: [...], lang?}. Serialized via the process-wide
    JobQueue (default_queue) inside live_activity_stream; the worker thread
    persists the event log to activity_logs with its OWN connection."""
    if _offline_mode_enabled():
        return JSONResponse(
            status_code=503,
            content={"error_code": "offline_mode", "message": "OFFLINE_MODE active; live synthesis refused."},
        )
    body = _require_dict(body)
    if body is None:
        return _bad_request("request body must be a JSON object.")
    card_ids = body.get("card_ids")
    lang = body.get("lang", "zh")
    if not isinstance(card_ids, list) or len(card_ids) < 1:
        return _bad_request("card_ids (non-empty list) is required.")
    if not all(isinstance(c, str) for c in card_ids):
        return _bad_request("card_ids must all be strings.")

    import synthesizer

    job_id = body.get("job_id") or _new_job_id()

    def work(emit, cancel=None):
        conn = _conn_factory()
        try:
            return synthesizer.synthesize_cards(card_ids, lang, emit=emit,
                                                conn=conn)
        finally:
            conn.close()

    stream = activity.live_activity_stream(
        work, job_id=job_id, source_ref="+".join(str(c)[:6] for c in card_ids),
        conn_factory=_conn_factory, done_text="综合完成",
    )
    return StreamingResponse(stream, media_type="text/event-stream", headers=SSE_HEADERS)


def _new_job_id() -> str:
    import uuid
    return uuid.uuid4().hex


# ===========================================================================
# Module 4 — Workbench REST (cards + decode + synthesize).
#
# Pure CRUD over db.py DAOs + thin wrappers around decode_bet / synthesize_cards.
# These are the endpoints the multi-card workbench front-end calls. Contract:
# API_CONTRACT.md §5. The live activity SSE is the separate /api/stream/* family
# above; the front-end pairs POST /api/decode (gets job_id + card) with an
# EventSource on /api/stream/activity/{job_id} for the agent feed.
# ===========================================================================


@app.get("/api/cards")
def list_cards(series_key: str = None, subject: str = None, source_type: str = None):
    """List stored Bet Cards, newest-first.

    Filter by series_key, or by (subject, source_type) pair, or nothing (= all).
    Returns lossless card_to_json for each."""
    with db.connection(DB_PATH) as conn:
        cards = db.list_cards(
            conn, series_key=series_key, subject=subject, source_type=source_type
        )
        return {"cards": [db.card_to_json(c) for c in cards]}


@app.get("/api/cards/{card_id}")
def get_card(card_id: str):
    """Fetch one card by id. 404 with error_code=card_not_found if absent."""
    with db.connection(DB_PATH) as conn:
        card = db.get_card(conn, card_id)
        if card is None:
            return JSONResponse(
                status_code=404,
                content={"error_code": "card_not_found", "message": f"No card for id {card_id}."},
            )
        return JSONResponse(content=db.card_to_json_full(card))


@app.get("/api/cards/{card_id}/plan")
def get_card_plan(card_id: str):
    """Build a local Trade Plan for a stored card from its decode_detail.panel.

    This endpoint does not fetch missing data. If the card has no v2 panel, it
    returns an explicit degraded response rather than inventing a plan.
    """
    with db.connection(DB_PATH) as conn:
        card = db.get_card(conn, card_id)
        if card is None:
            return JSONResponse(
                status_code=404,
                content={"error_code": "card_not_found", "message": f"No card for id {card_id}."},
            )

        detail = getattr(card, "decode_detail", None) or {}
        panel = _panel_from_card(card)
        position_context = _latest_position_context(conn, card.subject)
        if not panel:
            return {
                "card_id": card_id,
                "subject": card.subject,
                "status": "degraded",
                "reason": "decode_detail.panel is missing or empty",
                "position_context": position_context,
                "plan": None,
            }

        import trade_plan

        plan = trade_plan.build_trade_plan(
            panel,
            position_context,
            detail.get("reconciliation") if isinstance(detail.get("reconciliation"), dict) else None,
        )
        if plan is None:
            return {
                "card_id": card_id,
                "subject": card.subject,
                "status": "degraded",
                "reason": "trade plan could not be built from available panel data",
                "position_context": position_context,
                "plan": None,
            }
        return {
            "card_id": card_id,
            "subject": card.subject,
            "status": "ok",
            "position_context": position_context,
            "plan": plan,
        }


@app.post("/api/decode")
def decode_card(body=Body(default=None)):
    """Decode a bet into a BetCard and persist it.

    Body: {source_type, source_input, lang?}. Drives M2 decode_bet → db.save_card.
    Returns {job_id, card: card_to_json}. The front-end opens an EventSource on
    /api/stream/activity/{job_id} to watch the agent reason (replay, since this
    path persists the event log). OFFLINE_MODE refuses with 503."""
    if _offline_mode_enabled():
        return JSONResponse(
            status_code=503,
            content={"error_code": "offline_mode", "message": "OFFLINE_MODE active; live decode refused."},
        )
    body = _require_dict(body)
    if body is None:
        return _bad_request("request body must be a JSON object.")
    source_type = body.get("source_type")
    source_input = body.get("source_input")
    lang = body.get("lang", "zh")
    if not source_type or source_input is None:
        return _bad_request("source_type and source_input are required.")

    import decoder
    import orchestrator
    # MVP supports only market + portfolio; reject V2 source types (analyst_pt/opinion)
    # with an explicit 400 instead of silently returning an "insufficient" card.
    if source_type not in decoder._MVP_SOURCES:
        return _bad_request(
            f"source_type '{source_type}' 暂不支持(MVP 仅 market / portfolio)。"
        )

    # Agentic decode is PRIMARY (the agent picks the plan); it self-falls-back to
    # the deterministic decode when the provider can't tool-call. Set agentic:false
    # to force the deterministic tree.
    agentic = bool(body.get("agentic", True))
    job_id = body.get("job_id") or _new_job_id()
    subject = source_input if isinstance(source_input, str) else "portfolio"

    # Run the decode through the activity sink so the reasoning is persisted to
    # activity_logs (job_id), then save the resulting card. run_job is called
    # inline on THIS request thread, so the per-request connection it shares is
    # used only on its creating thread (safe). run_job guarantees a terminal
    # event and never raises, so a decode failure still returns a card
    # (decode_bet degrades to a "数据不足" card rather than raising).
    with db.connection(DB_PATH) as conn:
        def work(emit):
            if agentic:
                return orchestrator.decode_bet_agentic(
                    source_type, source_input, lang, emit=emit, conn=conn)
            return decoder.decode_bet(source_type, source_input, lang, emit=emit, conn=conn)

        try:
            info = activity.run_job(
                work, job_id=job_id, source_ref=str(subject), conn=conn,
                done_text="解码完成",
            )
        except Exception as exc:
            return JSONResponse(
                status_code=502,
                content={"error_code": "upstream_error", "message": f"decode failed: {exc}"},
            )

        card = info.get("result")
        if card is None:
            return JSONResponse(
                status_code=502,
                content={"error_code": "upstream_error", "message": info.get("error") or "decode produced no card."},
            )

        try:
            stored_id = db.save_card(conn, card)
            card.card_id = stored_id
            stored = db.get_card(conn, stored_id) or card
        except Exception as exc:
            return JSONResponse(
                status_code=502,
                content={"error_code": "upstream_error", "message": f"save_card failed: {exc}"},
            )

        return {"job_id": job_id, "card": db.card_to_json_full(stored)}


@app.post("/api/cards/{card_id}/ask")
def ask_card(card_id: str, body=Body(default=None)):
    """Ask a follow-up about a decoded card. The agent answers by calling tools and
    may PROPOSE a what-if revision (returned, not yet saved). Body: {question, lang?,
    job_id?}. Returns {job_id, answer, revision|None}; the front-end opens an
    EventSource on /api/stream/activity/{job_id} to replay the agent's reasoning.
    OFFLINE_MODE refuses (the answer needs the live LLM)."""
    if _offline_mode_enabled():
        return JSONResponse(
            status_code=503,
            content={"error_code": "offline_mode",
                     "message": "OFFLINE_MODE active; follow-up Q&A refused."},
        )
    body = _require_dict(body)
    if body is None:
        return _bad_request("request body must be a JSON object.")
    question = body.get("question")
    if not question or not str(question).strip():
        return _bad_request("question is required.")
    lang = body.get("lang", "zh")
    import orchestrator

    job_id = body.get("job_id") or _new_job_id()
    with db.connection(DB_PATH) as conn:
        card = db.get_card(conn, card_id)
        if card is None:
            return JSONResponse(
                status_code=404,
                content={"error_code": "card_not_found", "message": f"No card for id {card_id}."},
            )

        def work(emit):
            return orchestrator.answer_followup(card, str(question), lang,
                                                emit=emit, conn=conn)

        try:
            info = activity.run_job(
                work, job_id=job_id, source_ref=str(card.subject), conn=conn,
                done_text="答复完成",
            )
        except Exception as exc:
            return JSONResponse(
                status_code=502,
                content={"error_code": "upstream_error", "message": f"ask failed: {exc}"},
            )
        r = info.get("result") or {}
        return {"job_id": job_id, "answer": r.get("answer"),
                "revision": r.get("revision")}


@app.post("/api/cards/{card_id}/revise")
def revise_card(card_id: str, body=Body(default=None)):
    """Confirm a proposed revision → persist a NEW derived card (the original is
    untouched). Body: {revision} (the object returned by /ask). Returns {card}.
    No LLM/network, so this is allowed offline."""
    body = _require_dict(body)
    if body is None:
        return _bad_request("request body must be a JSON object.")
    revision = body.get("revision")
    if not isinstance(revision, dict):
        return _bad_request("a 'revision' object (from /ask) is required.")
    import orchestrator

    with db.connection(DB_PATH) as conn:
        parent = db.get_card(conn, card_id)
        if parent is None:
            return JSONResponse(
                status_code=404,
                content={"error_code": "card_not_found", "message": f"No card for id {card_id}."},
            )
        try:
            derived = orchestrator.build_revised_card(parent, revision)
            stored_id = db.save_card(conn, derived)
            derived.card_id = stored_id
            stored = db.get_card(conn, stored_id) or derived
        except Exception as exc:
            return JSONResponse(
                status_code=502,
                content={"error_code": "upstream_error", "message": f"revise failed: {exc}"},
            )
        return {"card": db.card_to_json_full(stored)}


@app.delete("/api/cards/{card_id}")
def delete_card(card_id: str):
    """Delete a card (FK cascade to children). Returns {deleted: bool}."""
    with db.connection(DB_PATH) as conn:
        return {"deleted": db.delete_card(conn, card_id)}


@app.post("/api/synthesize")
def synthesize(body=Body(default=None)):
    """Cross-card synthesis over an existing card set.

    Body: {card_ids: [...], lang?}. Drives M3 synthesize_cards (chat mode, cached
    in llm_cache). Returns the SynthesisResult dict (headline_insight may be
    None → front-end shows an honest empty state). OFFLINE_MODE refuses."""
    if _offline_mode_enabled():
        return JSONResponse(
            status_code=503,
            content={"error_code": "offline_mode", "message": "OFFLINE_MODE active; live synthesis refused."},
        )
    body = _require_dict(body)
    if body is None:
        return _bad_request("request body must be a JSON object.")
    card_ids = body.get("card_ids")
    lang = body.get("lang", "zh")
    if not isinstance(card_ids, list) or len(card_ids) < 1:
        return _bad_request("card_ids (non-empty list) is required.")
    if not all(isinstance(c, str) for c in card_ids):
        return _bad_request("card_ids must all be strings.")

    import synthesizer

    with db.connection(DB_PATH) as conn:
        try:
            result = synthesizer.synthesize_cards(card_ids, lang, conn=conn)
        except Exception as exc:
            return JSONResponse(
                status_code=502,
                content={"error_code": "upstream_error", "message": f"synthesis failed: {exc}"},
            )
    return JSONResponse(content=result)


@app.get("/api/chart/{subject}")
def get_chart(subject: str, as_of: str = None, window: str = "180d", card_id: str = None):
    """Return the v2 chart contract from stored observations only.

    Missing historical layers are honest-empty; this endpoint never fabricates a
    price series or point-in-time consensus history.
    """
    subject_norm = _subject_key(subject)
    if not subject_norm:
        return _bad_request("subject is required.")

    with db.connection(DB_PATH) as conn:
        layers = _empty_chart_layers()
        observations = db.list_panel_observations(conn, subject=subject_norm, limit=500)
        for obs in reversed(observations):
            _append_chart_point(layers, obs)

        if card_id:
            card = db.get_card(conn, card_id)
            if card is None:
                return JSONResponse(
                    status_code=404,
                    content={"error_code": "card_not_found", "message": f"No card for id {card_id}."},
                )
            if _subject_key(card.subject) != subject_norm:
                return _bad_request("card_id subject does not match chart subject.")
            decision = _extract_card_decision(card, _latest_position_context(conn, card.subject))
            kill = (decision or {}).get("kill") if isinstance(decision, dict) else None
            if isinstance(kill, dict):
                stop = (decision or {}).get("stop") or {}
                point = {
                    "type": kill.get("type") or "decision",
                    "line": kill.get("line"),
                    "status": kill.get("status") or "monitoring",
                    "source": "decision",
                }
                if isinstance(stop, dict) and stop.get("price") is not None:
                    point["level"] = stop.get("price")
                layers["kill_lines"].append(point)

    empty_layers = _chart_empty_layers(layers)
    quality = "honest_empty" if len(empty_layers) == len(layers) else ("degraded" if empty_layers else "ok")
    return {
        "subject": subject_norm,
        "as_of": as_of,
        "window": window,
        "status": "honest_empty" if quality == "honest_empty" else "ok",
        "quality": quality,
        "layers": layers,
        "empty_layers": empty_layers,
    }


@app.post("/api/positions/events")
def post_position_event(body=Body(default=None)):
    body = _require_dict(body)
    if body is None:
        return _bad_request("request body must be a JSON object.")
    subject = _subject_key(body.get("subject"))
    source = body.get("source")
    side = body.get("side")
    if not subject or not source or not side:
        return _bad_request("subject, source, and side are required.")
    status = body.get("status") or ("closed" if side == "flat" else "open")
    if status not in {"open", "closed"}:
        return _bad_request("status must be 'open' or 'closed'.")
    executed_at = body.get("executed_at") or _utc_now_iso()
    with db.connection(DB_PATH) as conn:
        event_id = db.record_position_ledger_event(
            conn,
            subject=subject,
            source=str(source),
            plan_card_id=body.get("plan_card_id"),
            side=str(side),
            quantity=body.get("quantity"),
            weight_pct=body.get("weight_pct"),
            avg_price=body.get("avg_price"),
            executed_at=str(executed_at),
            status=status,
            note=body.get("note"),
        )
    return _position_event_response(event_id, subject, status)


@app.get("/api/positions")
def get_positions(status: str = None, subject: str = None, limit: int = 500):
    if status is not None and status not in {"open", "closed"}:
        return _bad_request("status must be 'open' or 'closed'.")
    subject_norm = _subject_key(subject) if subject else None
    with db.connection(DB_PATH) as conn:
        events = db.list_position_ledger_events(conn, subject=subject_norm, limit=limit)

    latest_by_subject = {}
    for event in events:
        key = event.get("subject")
        if key and key not in latest_by_subject:
            latest_by_subject[key] = event
    positions = list(latest_by_subject.values())
    if status is not None:
        positions = [p for p in positions if p.get("status") == status]
    return {
        "positions": positions,
        "count": len(positions),
        "position_source": "position_ledger",
    }


@app.post("/api/panel/backfill")
def post_panel_backfill(body=Body(default=None)):
    body = _require_dict(body)
    if body is None:
        return _bad_request("request body must be a JSON object.")
    subjects = body.get("subjects")
    channels = body.get("channels")
    start_date = body.get("start_date")
    end_date = body.get("end_date")
    if not isinstance(subjects, list) or not subjects or not all(isinstance(s, str) and s.strip() for s in subjects):
        return _bad_request("subjects must be a non-empty list of strings.")
    if not isinstance(channels, list) or not channels or not all(isinstance(c, str) and c.strip() for c in channels):
        return _bad_request("channels must be a non-empty list of strings.")
    if not start_date or not end_date:
        return _bad_request("start_date and end_date are required.")

    status = body.get("status") or "queued"
    if status not in {"queued", "running", "completed", "failed", "partial", "planned"}:
        return _bad_request("status must be queued, running, completed, failed, partial, or planned.")
    run_id = body.get("run_id") or f"bf_{_new_job_id()}"
    credit_budget = int(body.get("credit_budget", 80))
    completed_at = _utc_now_iso() if status in {"completed", "failed"} else body.get("completed_at")

    with db.connection(DB_PATH) as conn:
        try:
            rid = db.record_panel_backfill_run(
                conn,
                run_id=str(run_id),
                subjects=[_subject_key(s) for s in subjects],
                channels=[str(c).strip() for c in channels],
                start_date=str(start_date),
                end_date=str(end_date),
                status=status,
                credit_budget=credit_budget,
                credits_spent=int(body.get("credits_spent", 0)),
                observations_written=int(body.get("observations_written", 0)),
                skipped=body.get("skipped"),
                error=body.get("error"),
                completed_at=completed_at,
            )
            run = db.get_panel_backfill_run(conn, rid)
        except Exception as exc:
            return JSONResponse(
                status_code=409,
                content={"error_code": "conflict", "message": f"could not create backfill run: {exc}"},
            )
    return {"run": run}


@app.get("/api/panel/backfill/{run_id}")
def get_panel_backfill(run_id: str):
    with db.connection(DB_PATH) as conn:
        run = db.get_panel_backfill_run(conn, run_id)
    if run is None:
        return JSONResponse(
            status_code=404,
            content={"error_code": "backfill_not_found", "message": f"No panel backfill run for id {run_id}."},
        )
    return {"run": run}


@app.get("/api/price-history/{ticker}")
def get_price_history(ticker: str, period: str = "5y"):
    """Monthly close prices over N years for chart rendering.

    Backed by a file cache (1 day TTL) so frontend reloads don't hammer yfinance.
    Returns: {"ticker": ..., "period": ..., "interval": "1mo",
              "points": [{"date": "YYYY-MM-DD", "close": float, "volume": float}, ...]}
    Old cached payloads without "volume" remain readable; the frontend tolerates the
    missing key and skips the volume sub-chart.
    """
    ticker_upper = ticker.upper()
    if not _valid_ticker(ticker_upper):
        raise HTTPException(status_code=400, detail={
            "error_code": "bad_request", "message": f"Invalid ticker {ticker!r}."})
    allowed_periods = {"1y", "2y", "5y", "10y", "max"}
    if period not in allowed_periods:
        raise HTTPException(status_code=400, detail={
            "error_code": "bad_request",
            "message": f"period must be one of {sorted(allowed_periods)}",
        })

    PRICE_HISTORY_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    # v2 suffix: schema now includes "volume". Old v1 files (close-only) are
    # ignored so the frontend reliably gets the volume sub-chart on first load.
    cache_file = PRICE_HISTORY_CACHE_DIR / f"{ticker_upper}_{period}_v2.json"

    def _serve_cache(stale: bool = False):
        try:
            payload = json.loads(cache_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if stale:
            payload = {**payload, "stale": True}
        return JSONResponse(content=payload)

    if cache_file.exists():
        age = time.time() - cache_file.stat().st_mtime
        if age < PRICE_HISTORY_TTL_SECONDS:
            resp = _serve_cache()
            if resp is not None:
                return resp
        # OFFLINE_MODE never refetches: serve the shipped demo cache even when
        # stale (real historical closes, just old — marked "stale": true).
        elif _offline_mode_enabled():
            resp = _serve_cache(stale=True)
            if resp is not None:
                return resp
    if _offline_mode_enabled():
        raise HTTPException(status_code=503, detail={
            "error_code": "offline_mode",
            "message": f"OFFLINE_MODE active and no cached price history for {ticker_upper}.",
        })

    try:
        import yfinance as yf
        hist = yf.Ticker(ticker_upper).history(period=period, interval="1mo")
    except Exception as exc:
        # Degrade to the stale cache (if any) before failing — real old data
        # beats an error page when yfinance is unreachable.
        resp = _serve_cache(stale=True) if cache_file.exists() else None
        if resp is not None:
            return resp
        raise HTTPException(
            status_code=502,
            detail=f"Failed to fetch price history for {ticker_upper} from yfinance: {exc}",
        )

    if hist is None or hist.empty:
        raise HTTPException(
            status_code=502,
            detail=f"yfinance returned no data for {ticker_upper}. Check ticker symbol.",
        )

    points = []
    for ts, row in hist.iterrows():
        close = row.get("Close")
        if close is None:
            continue
        try:
            close_f = float(close)
        except (TypeError, ValueError):
            continue
        if close_f != close_f:  # NaN check
            continue
        vol = row.get("Volume")
        try:
            vol_f = float(vol) if vol is not None else 0.0
        except (TypeError, ValueError):
            vol_f = 0.0
        if vol_f != vol_f:  # NaN
            vol_f = 0.0
        points.append({
            "date": ts.strftime("%Y-%m-%d"),
            "close": round(close_f, 4),
            "volume": round(vol_f, 0),
        })

    if not points:
        raise HTTPException(
            status_code=502,
            detail=f"yfinance returned no usable close prices for {ticker_upper}.",
        )

    payload = {
        "ticker": ticker_upper,
        "period": period,
        "interval": "1mo",
        "points": points,
    }
    try:
        cache_file.write_text(json.dumps(payload), encoding="utf-8")
    except OSError:
        pass  # serving the data matters more than cache write
    return JSONResponse(content=payload)


# ---------------------------------------------------------------------------
# Monitor API (SPEC F3): scan / feed / stream / kill-status
# ---------------------------------------------------------------------------

import uuid as _uuid


def _monitor_subjects(conn) -> list[str]:
    """Default scan scope: open positions from position_ledger."""
    rows = db.list_position_ledger_events(conn, status="open")
    return [r["subject"] for r in rows if r.get("subject")]


def _build_feed_item(subject: str, trigger: str, severity: str, headline: str,
                     changes: list | None = None, kill_status: dict | None = None,
                     card_id: str | None = None) -> dict:
    return {
        "item_id": _uuid.uuid4().hex,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "subject": subject,
        "trigger": trigger,
        "severity": severity,
        "headline": headline,
        "changes": changes or [],
        "kill_status": kill_status,
        "action_hint": "查看增强股价图",
        "card_id": card_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/api/monitor/scan")
def monitor_scan(body=Body(default=None)):
    """Trigger a monitoring scan (SPEC F3). MVP: price/event heartbeat only.

    Scans open positions + optional explicit subjects. Generates feed items
    for material changes. Budget-gated per SPEC F3 credits gate.
    """
    body = _require_dict(body)
    if body is None:
        return _bad_request("Request body must be a JSON object.")

    trigger = body.get("trigger", "manual")
    as_of = body.get("as_of_date") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    explicit_subjects = body.get("subjects") or []

    with db.connection(DB_PATH) as conn:
        # Default scope: open positions + explicit subjects
        subjects = list(set((_monitor_subjects(conn) + explicit_subjects)))
        if not subjects:
            return {
                "job_id": None,
                "status": "noop",
                "subjects_scanning": 0,
                "message": "No open positions or subjects to scan.",
            }

        job_id = f"mon_{_uuid.uuid4().hex[:12]}"
        budget = int(body.get("credit_budget", 80))

        db.insert_monitor_scan(conn, {
            "job_id": job_id,
            "trigger": trigger,
            "subjects": subjects,
            "as_of_date": as_of,
            "status": "running",
            "credits_budget": budget,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })

        # MVP scan: heartbeat only (price/event, 0 credits)
        feed_items = []
        for subj in subjects:
            # Check latest card for this subject
            cards = db.list_cards(conn, subject=subj)
            if not cards:
                continue
            card = cards[0]
            dd = getattr(card, "decode_detail", None) or {}
            decision = dd.get("decision") or {}
            kill = decision.get("kill") if isinstance(decision, dict) else None

            # Generate feed item only if KILL status is approached/triggered
            if kill and isinstance(kill, dict):
                kill_status = kill.get("status", "monitoring")
                if kill_status in ("approached", "triggered"):
                    severity = "high" if kill_status == "triggered" else "medium"
                    feed_item = _build_feed_item(
                        subject=subj,
                        trigger=trigger,
                        severity=severity,
                        headline=f"{subj} KILL 状态: {kill_status} — {kill.get('line', '')}",
                        kill_status={
                            "approached": kill_status == "approached",
                            "kill_line": kill.get("line"),
                            "status": kill_status,
                        },
                        card_id=getattr(card, "card_id", None),
                    )
                    db.insert_monitor_feed_item(conn, feed_item)
                    feed_items.append(feed_item)

        db.update_monitor_scan(conn, job_id,
            status="completed",
            subjects_scanned=len(subjects),
            credits_spent=0,
            feed_items_generated=len(feed_items),
            completed_at=datetime.now(timezone.utc).isoformat(),
        )

    return {
        "job_id": job_id,
        "status": "completed",
        "subjects_scanning": len(subjects),
        "feed_items": len(feed_items),
    }


@app.get("/api/monitor/feed")
def monitor_feed(subject: str = None, severity: str = None, limit: int = 50):
    """Get monitor feed items (SPEC F3). Newest first."""
    if limit > 200:
        limit = 200
    with db.connection(DB_PATH) as conn:
        items = db.list_monitor_feed(conn, subject=subject, severity=severity, limit=limit)
    return {
        "items": items,
        "has_more": len(items) == limit,
        "oldest_timestamp": items[-1]["timestamp"] if items else None,
    }


@app.get("/api/monitor/kill-status")
def monitor_kill_status(subject: str = None):
    """Query KILL line status for subjects (SPEC F3)."""
    with db.connection(DB_PATH) as conn:
        subjects_to_check = [subject] if subject else _monitor_subjects(conn)
        results = []
        for subj in subjects_to_check:
            cards = db.list_cards(conn, subject=subj)
            if not cards:
                continue
            card = cards[0]
            dd = getattr(card, "decode_detail", None) or {}
            decision = dd.get("decision") or {}
            kill = decision.get("kill") if isinstance(decision, dict) else None
            if kill and isinstance(kill, dict):
                results.append({
                    "subject": subj,
                    "type": kill.get("type"),
                    "line": kill.get("line"),
                    "status": kill.get("status", "monitoring"),
                    "implied_prob": kill.get("implied_prob"),
                    "last_checked": dd.get("anchor_price") and datetime.now(timezone.utc).isoformat(),
                })
    return {"subjects": results}


@app.get("/api/monitor/stream")
async def monitor_stream():
    """SSE stream for high-severity feed items + heartbeat (SPEC F3)."""
    import asyncio

    async def event_generator():
        last_check = datetime.now(timezone.utc)
        while True:
            now = datetime.now(timezone.utc)
            # Check for new high-severity feed items every 5 seconds
            if (now - last_check).total_seconds() >= 5:
                last_check = now
                try:
                    with db.connection(DB_PATH) as conn:
                        items = db.list_monitor_feed(conn, severity="high", limit=5)
                    for item in items:
                        yield f"event: feed_item\ndata: {json.dumps(item, default=str)}\n\n"
                except Exception:
                    pass
            # Heartbeat every 60s
            yield f"event: heartbeat\ndata: {json.dumps({'timestamp': now.isoformat()})}\n\n"
            await asyncio.sleep(60)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/")
def root():
    if not APP_HTML_PATH.exists():
        raise HTTPException(status_code=404, detail="app.html not found")
    return HTMLResponse(content=APP_HTML_PATH.read_text(encoding="utf-8"))
