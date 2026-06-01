"""PriceLens FastAPI backend.

Serves cached pipeline outputs to the frontend. Does NOT trigger any LLM calls.

Start the server:
    uvicorn api:app --reload --port 8000
"""
import json
import os
import re
import time
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

app = FastAPI(title="PriceLens API")

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
            "message": f"No cached decode for {ticker_upper}. Run python pipeline.py {ticker_upper} --no-evidence first.",
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
            "message": f"No short-term attribution computed for {ticker_upper}. Run python pipeline.py {ticker_upper} --short-term first.",
        })
    return JSONResponse(content=st)


# Legacy alias — the path "/5d" was the original endpoint. Keep for any client
# that was wired to it; new code should use /short-term.
@app.get("/api/decode/{ticker}/5d")
def get_short_term_legacy_5d(ticker: str):
    return get_short_term(ticker)


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
        except Exception as exc:
            return JSONResponse(
                status_code=502,
                content={"error_code": "upstream_error", "message": f"save_card failed: {exc}"},
            )

        return {"job_id": job_id, "card": db.card_to_json_full(card)}


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
        except Exception as exc:
            return JSONResponse(
                status_code=502,
                content={"error_code": "upstream_error", "message": f"revise failed: {exc}"},
            )
        return {"card": db.card_to_json_full(derived)}


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

    if cache_file.exists():
        age = time.time() - cache_file.stat().st_mtime
        if age < PRICE_HISTORY_TTL_SECONDS:
            try:
                return JSONResponse(content=json.loads(cache_file.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                pass  # fall through to refetch

    try:
        import yfinance as yf
        hist = yf.Ticker(ticker_upper).history(period=period, interval="1mo")
    except Exception as exc:
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


@app.get("/")
def root():
    if not APP_HTML_PATH.exists():
        raise HTTPException(status_code=404, detail="app.html not found")
    return HTMLResponse(content=APP_HTML_PATH.read_text(encoding="utf-8"))
