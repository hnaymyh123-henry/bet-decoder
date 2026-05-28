# API_CONTRACT · Bet Decoder

> Front/back-end interface contract. Locks the types + functions + REST/SSE
> endpoints so M2/M3/M4/M5 can build against M1 without re-litigating shapes.
> Authoritative product spec: `PRD.md` (frozen). This file is the engineering
> contract derived from it.
>
> Status legend: **[M1 ✅ implemented]** = live in `db.py` today ·
> **[Mx 🔒 placeholder]** = signature reserved here, owned by that module, not
> yet implemented.

---

## 0. Conventions

- No auth (self-hosted single user).
- All persistence goes through `db.py` DAOs; connections are per-request
  (`db.init_db(DB_PATH)`), sync, opened on the thread that uses them.
- Unified error envelope for REST endpoints:
  ```json
  { "error_code": "string_snake_case", "message": "human-readable detail" }
  ```
  HTTP status carries the class (404 not-found, 400 bad-request, 409 conflict,
  503 offline, 502 upstream); the body carries `error_code` + `message`.
- Dates/timestamps: ISO-8601 UTC strings. `trade_date` is `YYYY-MM-DD`.

---

## 1. Module 1 — Bet Card data model  **[M1 ✅ implemented in `db.py`]**

The data底座. Passive storage layer: serialize / persist / read. Never decodes,
synthesizes, renders, or emits events.

### 1.1 Types (`db.py`)

```python
@dataclass
class Holding:
    ticker: str
    weight_pct: float | None = None
    run_id: int | None = None

@dataclass
class ThemeExposure:
    theme: str
    exposure_pct: float | None = None
    contributing_tickers: list[str] = []
    is_concentration_risk: bool = False

@dataclass
class BetCard:
    subject: str                         # e.g. "NVDA"
    source_type: str                     # market | analyst_pt | opinion | portfolio
    card_kind: str = "single"            # single | portfolio
    source_ref: str | None = None        # raw bet text / URL / draft ref
    bet: float | None = None             # NULLABLE (Opinion may lack a target)
    run_id: int | None = None            # single cards reuse runs; NULL for portfolio
    card_id: str | None = None           # auto: uuid4 hex if omitted
    series_key: str | None = None        # auto: "<subject>|<source_type>"
    trade_date: str | None = None        # auto: created_at[:10]; dedup bucket
    created_at: str | None = None        # auto: now UTC ISO-8601
    holdings: list[Holding] = []         # portfolio cards
    theme_exposures: list[ThemeExposure] = []  # portfolio AND single (anchor mode, R1)
```

Constants: `SINGLE`, `PORTFOLIO`; `SOURCE_MARKET`, `SOURCE_ANALYST_PT`,
`SOURCE_OPINION`, `SOURCE_PORTFOLIO`.

### 1.2 Functions (`db.py`)

```python
make_series_key(subject, source_type) -> str          # "<subject>|<source_type>"

save_card(conn, card: BetCard) -> str                 # returns stored card_id
#   Market cards dedup to one-per-trading-day per series: if a card already
#   exists for (series_key, trade_date), no insert happens and the EXISTING
#   card_id is returned. Other source_types are never deduped.

get_card(conn, card_id) -> BetCard | None             # None if not found
list_cards(conn, series_key=None, subject=None, source_type=None) -> list[BetCard]
#   newest-first. Filter by series_key, or by (subject, source_type) pair, or
#   nothing (= all cards).
delete_card(conn, card_id) -> bool                    # True if removed (FK cascade)

card_to_json(card) -> dict                            # lossless, JSON-safe
card_from_json(data) -> BetCard                       # inverse of card_to_json
card_from_row(conn, row) -> BetCard                   # rebuild from bet_cards row + children
```

### 1.3 Tables owned by M1

| Table | Key columns |
|---|---|
| `bet_cards` | `card_id` PK, `subject`, `source_type`, `card_kind`, `source_ref`, `series_key`, `bet`(nullable), `trade_date`, `created_at`, `run_id` FK→runs (NULL ok) |
| `portfolio_holdings` | `card_id` FK, `ticker`, `weight_pct`, `run_id` FK |
| `theme_exposures` | `card_id` FK, `theme`, `exposure_pct`, `contributing_tickers`(JSON), `is_concentration_risk` — **shared by single + portfolio cards** |
| `activity_logs` | `job_id` PK, `source_ref`, `events_json`(JSON blob), `created_at` — **table only; emit logic is M5** |
| `runs` (existing) | + `anchor_price` REAL, + `anchor_type` TEXT (`market`\|`analyst_pt`\|`opinion`). Legacy rows backfilled to `anchor_price=current_price, anchor_type='market'`. |

Existing 13 tables are otherwise unchanged. Migration is hand-written idempotent
DDL inside `init_db` (`CREATE TABLE IF NOT EXISTS` + `ALTER TABLE ADD COLUMN`
guarded by `PRAGMA table_info`). No Alembic.

---

## 2. Module 2 — Decoder Engine  **[M2 🔒 placeholder]**

Any source → a full BetCard (with evidence). Passive return — does NOT self-store
(the caller persists via `save_card`).

```python
decode_bet(source_type: str,
           source_input: str | dict,   # ticker / portfolio basket / bet text
           lang: str,                   # "zh" | "en"
           emit=None) -> BetCard
#   emit: optional ActivityEvent callback (M5-injected); None = no streaming
#         (batch / test path).
#   MVP scope: source_type in {market, portfolio}. analyst_pt/opinion = V2.
#   Single cards may set run_id (reuse runs sub-tables) + theme_exposures
#   (anchor mode, R1). Portfolio cards set holdings + theme_exposures.
```

---

## 3. Module 3 — Cross-card synthesis  **[M3 🔒 placeholder]**

Pure consumer: reads cards by id, never mutates/creates cards. Result cached in
`llm_cache` (category `"synthesis"`, key = card-set hash); not a first-class
table at MVP.

```python
synthesize_cards(card_ids: list[str],
                 lang: str,
                 emit=None) -> SynthesisResult
```

```python
SynthesisResult {
  card_ids: list[str],
  generated_at: str,
  headline_insight: { text: str, relation_id: str } | None,   # demo subtitle
  relations: [
    { id: str, type: str,                # consensus|divergence|contradiction|same-source|drift
      card_a: str, card_b: str,          # card_ids
      strength: "strong"|"medium"|"weak",
      shared_assumption: str,
      detail: str,
      comparable: bool }
  ],
  narrative: str | None                   # each sentence anchors a relation_id
}
```

---

## 4. Module 5 — Activity stream (SSE)  **[M5 🔒 placeholder]**

Cross-cutting. `emit(ActivityEvent)` callback injected into M2/M3. Events
persisted to `activity_logs` for replay (by `seq` + `t_offset_ms`).

```python
ActivityEvent {
  job_id: str, seq: int, t_offset_ms: int,
  source: { kind: "decode"|"synthesis", card_id?: str, card_ids?: list[str], subject: str },
  phase: str,
  kind: "decision"|"computation"|"evidence"|"relation",
  text: str,
  payload: dict | None,
  terminal: None | "done" | "error"      # every job ends with a terminal event
}
```

SSE endpoint (replaces the W2/W3 mock `/api/stream/evidence/...`):

```
GET /api/stream/activity/{job_id}        # text/event-stream; live or replay
```

---

## 5. Module 4 — Workbench REST  **[M4 🔒 placeholder]**

Pure presentation/interaction layer; no business logic. Calls M2/M3 over REST,
consumes M5 over SSE.

| Method | Path | Body / Query | Returns |
|---|---|---|---|
| GET | `/api/cards` | `?series_key=` or `?subject=&source_type=` | `{ cards: [card_to_json...] }` |
| GET | `/api/cards/{card_id}` | — | `card_to_json` or 404 `{error_code:"card_not_found"}` |
| POST | `/api/decode` | `{ source_type, source_input, lang }` | `{ job_id, card: card_to_json }` (drives M2 → save_card) |
| DELETE | `/api/cards/{card_id}` | — | `{ deleted: bool }` |
| POST | `/api/synthesize` | `{ card_ids: [...], lang }` | `SynthesisResult` (drives M3) |
| GET | `/api/stream/activity/{job_id}` | — | SSE `ActivityEvent` stream (M5) |

Existing endpoints kept (legacy single-stock view): `/api/health`,
`/api/tickers`, `/api/decode/{ticker}`, `/api/decode/{ticker}/short-term`,
`/api/price-history/{ticker}`, `/api/offline-mode`, `/`.

---

## 6. Error codes (seed list)

| error_code | HTTP | Meaning |
|---|---|---|
| `card_not_found` | 404 | no card for the given id |
| `no_cached_decode` | 404 | no run/decode for ticker (legacy endpoints) |
| `bad_request` | 400 | malformed body / unknown source_type |
| `duplicate_card` | 409 | (reserved) explicit dedup conflict if ever surfaced |
| `offline_mode` | 503 | OFFLINE_MODE active, LLM path refused |
| `upstream_error` | 502 | yfinance / MiroMind upstream failure |
