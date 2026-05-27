"""PriceLens FastAPI backend.

Serves cached pipeline outputs to the frontend. Does NOT trigger any LLM calls.

Start the server:
    uvicorn api:app --reload --port 8000
"""
import json
import os
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from sse import stream_evidence_mock


def _offline_mode_enabled() -> bool:
    return os.environ.get("OFFLINE_MODE", "").lower() in ("1", "true", "yes")

OUTPUTS_DIR = Path(__file__).parent / "outputs"
MOCKUP_PATH = Path(__file__).parent / "pricelens_mockup.html"
PRICE_HISTORY_CACHE_DIR = Path(__file__).parent / "cache" / "price_history"
PRICE_HISTORY_TTL_SECONDS = 24 * 60 * 60  # 1 day

app = FastAPI(title="PriceLens API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/tickers")
def list_tickers():
    if not OUTPUTS_DIR.exists():
        return {"tickers": []}
    tickers = set()
    for p in OUTPUTS_DIR.glob("*.json"):
        name = p.stem
        if "_" in name:
            tickers.add(name.split("_", 1)[0])
    return {"tickers": sorted(tickers)}


@app.get("/api/decode/{ticker}")
def get_decode(ticker: str):
    ticker_upper = ticker.upper()
    if not OUTPUTS_DIR.exists():
        raise HTTPException(
            status_code=404,
            detail=f"No cached decode for {ticker_upper}. Run python pipeline.py {ticker_upper} --no-evidence first.",
        )
    matches = sorted(OUTPUTS_DIR.glob(f"{ticker_upper}_*.json"))
    if not matches:
        raise HTTPException(
            status_code=404,
            detail=f"No cached decode for {ticker_upper}. Run python pipeline.py {ticker_upper} --no-evidence first.",
        )
    latest = matches[-1]
    data = json.loads(latest.read_text(encoding="utf-8"))
    return JSONResponse(content=data)


def _find_latest_short_term(ticker_upper: str):
    """Scan newest-first to find a cached output that has a non-null short_term.

    Fixes QA-A W1: a fresh pipeline run without --short-term would otherwise
    shadow earlier runs that had it. The window is variable, so we don't filter
    by window here — caller can read short_term['window_days'].
    """
    if not OUTPUTS_DIR.exists():
        return None
    matches = sorted(OUTPUTS_DIR.glob(f"{ticker_upper}_*.json"), reverse=True)
    for p in matches:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("short_term") is not None:
            return data["short_term"]
    return None


@app.get("/api/decode/{ticker}/short-term")
def get_short_term(ticker: str):
    """Latest non-null short-term attribution for {ticker}. Window-agnostic."""
    ticker_upper = ticker.upper()
    st = _find_latest_short_term(ticker_upper)
    if st is None:
        raise HTTPException(
            status_code=404,
            detail=f"No short-term attribution computed for {ticker_upper}. Run python pipeline.py {ticker_upper} --short-term first.",
        )
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


@app.get("/api/price-history/{ticker}")
def get_price_history(ticker: str, period: str = "5y"):
    """Monthly close prices over N years for chart rendering.

    Backed by a file cache (1 day TTL) so frontend reloads don't hammer yfinance.
    Returns: {"ticker": ..., "period": ..., "interval": "1mo",
              "points": [{"date": "YYYY-MM-DD", "close": float}, ...]}
    """
    ticker_upper = ticker.upper()
    allowed_periods = {"1y", "2y", "5y", "10y", "max"}
    if period not in allowed_periods:
        raise HTTPException(
            status_code=400,
            detail=f"period must be one of {sorted(allowed_periods)}",
        )

    PRICE_HISTORY_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = PRICE_HISTORY_CACHE_DIR / f"{ticker_upper}_{period}.json"

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
        points.append({"date": ts.strftime("%Y-%m-%d"), "close": round(close_f, 4)})

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
    if not MOCKUP_PATH.exists():
        raise HTTPException(status_code=404, detail="pricelens_mockup.html not found")
    return HTMLResponse(content=MOCKUP_PATH.read_text(encoding="utf-8"))
