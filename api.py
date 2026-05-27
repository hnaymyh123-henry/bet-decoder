"""PriceLens FastAPI backend.

Serves cached pipeline outputs to the frontend. Does NOT trigger any LLM calls.

Start the server:
    uvicorn api:app --reload --port 8000
"""
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

OUTPUTS_DIR = Path(__file__).parent / "outputs"
MOCKUP_PATH = Path(__file__).parent / "pricelens_mockup.html"

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


@app.get("/api/decode/{ticker}/5d")
def get_short_term(ticker: str):
    ticker_upper = ticker.upper()
    if not OUTPUTS_DIR.exists():
        raise HTTPException(
            status_code=404,
            detail=f"No short-term attribution computed for {ticker_upper}. Run python pipeline.py {ticker_upper} --short-term first.",
        )
    matches = sorted(OUTPUTS_DIR.glob(f"{ticker_upper}_*.json"))
    if not matches:
        raise HTTPException(
            status_code=404,
            detail=f"No short-term attribution computed for {ticker_upper}. Run python pipeline.py {ticker_upper} --short-term first.",
        )
    latest = matches[-1]
    data = json.loads(latest.read_text(encoding="utf-8"))
    short_term = data.get("short_term")
    if short_term is None:
        raise HTTPException(
            status_code=404,
            detail=f"No short-term attribution computed for {ticker_upper}. Run python pipeline.py {ticker_upper} --short-term first.",
        )
    return JSONResponse(content=short_term)


@app.get("/")
def root():
    if not MOCKUP_PATH.exists():
        raise HTTPException(status_code=404, detail="pricelens_mockup.html not found")
    return HTMLResponse(content=MOCKUP_PATH.read_text(encoding="utf-8"))
