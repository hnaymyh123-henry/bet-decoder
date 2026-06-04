"""SQLite storage layer for PriceLens / Bet Decoder.

Replaces outputs/*.json + cache/*/*.json. As of the Bet Decoder pivot (M1) this
layer also owns the Bet Card data model: the `bet_cards` envelope plus the
`portfolio_holdings` / `theme_exposures` / `activity_logs` tables, and a passive
DAO (`save_card` / `get_card` / `list_cards` / `delete_card` +
`card_to_json` / `card_from_row`). "Passive" means: M2 pushes cards in, consumers
pull cards out; this layer never calls upstream (no decode / synthesis / render /
SSE-emit logic lives here).
"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from typing import Any, Iterator


# ---------------------------------------------------------------------------
# Schema (DDL). Keep column/table names stable — Worker B + api.py code
# against this contract.
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
-- Schema v1
CREATE TABLE IF NOT EXISTS runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker          TEXT    NOT NULL,
    company_name    TEXT,
    generated_at    TEXT    NOT NULL,
    mode            TEXT    NOT NULL,
    current_price   REAL    NOT NULL,
    baseline_dcf    REAL,
    total_cost_usd  REAL    DEFAULT 0,
    decoder_cached  INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_runs_ticker_time ON runs(ticker, generated_at DESC);

CREATE TABLE IF NOT EXISTS rdcf_results (
    run_id                    INTEGER PRIMARY KEY,
    consensus_revenue_cagr    REAL,
    consensus_terminal_growth REAL,
    consensus_terminal_margin REAL,
    consensus_wacc            REAL,
    company_inputs_json       TEXT,
    FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS rdcf_intervals (
    run_id        INTEGER NOT NULL,
    variable      TEXT    NOT NULL,
    p10           REAL,
    p25           REAL,
    p50           REAL,
    p75           REAL,
    p90           REAL,
    success_rate  REAL,
    PRIMARY KEY (run_id, variable),
    FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS decoder_outputs (
    run_id            INTEGER PRIMARY KEY,
    raw_json          TEXT,
    cost_usd          REAL,
    prompt_tokens     INTEGER,
    completion_tokens INTEGER,
    FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS assumptions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id         INTEGER NOT NULL,
    seq            INTEGER NOT NULL,
    assumption_id  TEXT,
    metric         TEXT,
    human_text     TEXT,
    interval_p10   REAL,
    interval_p50   REAL,
    interval_p90   REAL,
    extra_json     TEXT,
    FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_assumptions_run_seq ON assumptions(run_id, seq);

CREATE TABLE IF NOT EXISTS evidence_briefs (
    assumption_id   INTEGER PRIMARY KEY,
    overall_balance TEXT,
    support_count   INTEGER DEFAULT 0,
    refute_count    INTEGER DEFAULT 0,
    neutral_count   INTEGER DEFAULT 0,
    generated_at    TEXT,
    cost_usd        REAL,
    tool_call_count INTEGER,
    FOREIGN KEY (assumption_id) REFERENCES assumptions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS evidence_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    brief_id        INTEGER NOT NULL,
    seq             INTEGER NOT NULL,
    direction       TEXT,
    claim           TEXT,
    body_md         TEXT,
    recency_score   INTEGER,
    quality_score   INTEGER,
    relevance_score INTEGER,
    FOREIGN KEY (brief_id) REFERENCES evidence_briefs(assumption_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_evidence_items_brief ON evidence_items(brief_id, seq);

CREATE TABLE IF NOT EXISTS sources (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    evidence_item_id INTEGER NOT NULL,
    url              TEXT,
    title            TEXT,
    date             TEXT,
    publisher        TEXT,
    FOREIGN KEY (evidence_item_id) REFERENCES evidence_items(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_sources_item ON sources(evidence_item_id);

CREATE TABLE IF NOT EXISTS critic_reports (
    brief_id      INTEGER PRIMARY KEY,
    verdict       TEXT,
    error_count   INTEGER DEFAULT 0,
    warning_count INTEGER DEFAULT 0,
    info_count    INTEGER DEFAULT 0,
    issues_json   TEXT,
    FOREIGN KEY (brief_id) REFERENCES evidence_briefs(assumption_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS syntheses (
    run_id            INTEGER PRIMARY KEY,
    headline          TEXT,
    overall_balance   TEXT,
    confidence        REAL,
    raw_json          TEXT,
    cost_usd          REAL,
    prompt_tokens     INTEGER,
    completion_tokens INTEGER,
    FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS short_term_attributions (
    run_id        INTEGER PRIMARY KEY,
    window_days   INTEGER,
    return_pct    REAL,
    factors_json  TEXT,
    raw_json      TEXT,
    FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS llm_cache (
    cache_key         TEXT PRIMARY KEY,
    category          TEXT NOT NULL,
    ticker            TEXT,
    payload_json      TEXT NOT NULL,
    cost_usd          REAL,
    prompt_tokens     INTEGER,
    completion_tokens INTEGER,
    created_at        TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_llm_cache_cat_ticker ON llm_cache(category, ticker);

CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- ===================================================================
-- Bet Decoder pivot (M1) — Bet Card data model
-- ===================================================================

-- Card envelope. One row per decoded bet (immutable snapshot).
--   card_kind   = 'single' | 'portfolio'
--   source_type = 'market' | 'analyst_pt' | 'opinion' (single) | 'portfolio'
--   series_key  = "<subject>|<source_type>" — groups snapshots of the same bet
--   run_id      = FK -> runs (single cards reuse the runs sub-tables);
--                 NULL for portfolio cards.
--   trade_date  = trading-day bucket used for Market-card dedup (1 card / day).
CREATE TABLE IF NOT EXISTS bet_cards (
    card_id     TEXT PRIMARY KEY,
    subject     TEXT NOT NULL,
    source_type TEXT NOT NULL,
    card_kind   TEXT NOT NULL,
    source_ref  TEXT,
    series_key  TEXT NOT NULL,
    bet         REAL,
    trade_date  TEXT,
    created_at  TEXT NOT NULL,
    run_id      INTEGER,
    FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_bet_cards_series ON bet_cards(series_key, created_at DESC);
-- Dedup guard: at most one *Market* card per (series_key, trade_date). The
-- predicate is scoped to source_type='market' so it matches save_card's dedup
-- semantics exactly (PRD 行为⑦: only Market cards are one-per-trading-day;
-- analyst_pt / opinion cards are NOT deduped and may share a series+day).
-- v2 of this index (the v1 form omitted the source_type predicate, which made
-- a 2nd same-day non-Market card collide and 500). _migrate_dedup_index drops
-- the v1 index on startup so existing DBs pick up the corrected predicate.
CREATE UNIQUE INDEX IF NOT EXISTS uq_bet_cards_market_day
    ON bet_cards(series_key, trade_date)
    WHERE trade_date IS NOT NULL AND source_type = 'market';

-- Portfolio-card holdings.
CREATE TABLE IF NOT EXISTS portfolio_holdings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id     TEXT    NOT NULL,
    ticker      TEXT    NOT NULL,
    weight_pct  REAL,
    run_id      INTEGER,
    FOREIGN KEY (card_id) REFERENCES bet_cards(card_id) ON DELETE CASCADE,
    FOREIGN KEY (run_id)  REFERENCES runs(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_portfolio_holdings_card ON portfolio_holdings(card_id);

-- Theme exposures. Shared by BOTH portfolio cards and single cards
-- (single cards in anchor mode emit theme rows too — PRD M1 decision 12 / R1).
--   contributing_tickers = JSON array of ticker strings
--   is_concentration_risk = 0 | 1
CREATE TABLE IF NOT EXISTS theme_exposures (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id               TEXT    NOT NULL,
    theme                 TEXT    NOT NULL,
    exposure_pct          REAL,
    contributing_tickers  TEXT,
    is_concentration_risk INTEGER DEFAULT 0,
    FOREIGN KEY (card_id) REFERENCES bet_cards(card_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_theme_exposures_card ON theme_exposures(card_id);

-- Agent activity-stream log (M5). Table only — emit logic lives in M5, not here.
--   events_json = JSON array of ActivityEvent objects for one job (replay blob)
CREATE TABLE IF NOT EXISTS activity_logs (
    job_id      TEXT PRIMARY KEY,
    source_ref  TEXT,
    events_json TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_activity_logs_created ON activity_logs(created_at DESC);
"""


# ---------------------------------------------------------------------------
# Connection + bootstrap
# ---------------------------------------------------------------------------

SCHEMA_VERSION = "3"  # v1 = original 13 tables; v2 = Bet Card model + runs anchor cols; v3 = decode_detail persistence + card lineage (derived_from)


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _migrate_runs_anchor(conn: sqlite3.Connection) -> None:
    """Idempotent ALTER: add anchor_price / anchor_type to runs and backfill.

    PRD M1 decision 13: legacy runs backfill anchor_price=current_price,
    anchor_type='market'. Safe to run on every startup — only ADDs columns that
    are missing, and the backfill UPDATE is a no-op once rows are populated.
    """
    cols = _table_columns(conn, "runs")
    if "anchor_price" not in cols:
        conn.execute("ALTER TABLE runs ADD COLUMN anchor_price REAL")
    if "anchor_type" not in cols:
        conn.execute("ALTER TABLE runs ADD COLUMN anchor_type TEXT")
    # Backfill any rows still missing anchor data (legacy rows, or rows inserted
    # before these columns existed). New rows are written with anchors directly
    # by save_pipeline_run, so this only touches stragglers.
    conn.execute(
        "UPDATE runs SET anchor_price = current_price WHERE anchor_price IS NULL"
    )
    conn.execute(
        "UPDATE runs SET anchor_type = 'market' WHERE anchor_type IS NULL"
    )


def _migrate_dedup_index(conn: sqlite3.Connection) -> None:
    """Drop the v1 ``uq_bet_cards_series_day`` index if present.

    The v1 unique index covered (series_key, trade_date) for ALL source_types,
    so a second non-Market card on the same subject+day collided and 500'd. The
    v2 index (``uq_bet_cards_market_day``, created in SCHEMA_SQL) adds a
    ``source_type='market'`` predicate. ``CREATE INDEX IF NOT EXISTS`` will not
    replace the old index, so we drop it explicitly. Idempotent.
    """
    conn.execute("DROP INDEX IF EXISTS uq_bet_cards_series_day")


def _migrate_card_detail(conn: sqlite3.Connection) -> None:
    """Idempotent: persist the rich decode_detail + card lineage (schema v3).

    Adds nullable columns to ``bet_cards``:
      - decode_detail_json : the full decode_detail blob (was lost on reload, TD1).
        Persisting it lets a reloaded card be interrogated/revised and revives the
        mode / narrative_premium / market_narrative round-trip in card_to_json.
      - derived_from       : parent card_id for a what-if / revision card (NULL =
        an original). Originals stay immutable; a revision is a NEW derived card.
      - derivation_kind    : 'whatif' | 'revision' | NULL.
      - derivation_json    : {params, prompt, diff:[{field,before,after}]} blob.

    Also repredicates the daily-unique Market index to exclude derived cards, so
    unlimited same-day what-if snapshots can coexist with the one canonical card.
    Modeled on _migrate_runs_anchor / _migrate_dedup_index — only touches what's
    missing, safe to re-run.
    """
    cols = _table_columns(conn, "bet_cards")
    for col in ("decode_detail_json", "derived_from", "derivation_kind",
                "derivation_json"):
        if col not in cols:
            conn.execute(f"ALTER TABLE bet_cards ADD COLUMN {col} TEXT")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_bet_cards_derived ON bet_cards(derived_from)"
    )
    # Repredicate uq_bet_cards_market_day: same name, new predicate (… AND
    # derived_from IS NULL), so a drop+recreate is required (CREATE IF NOT EXISTS
    # won't replace an existing same-named index). derived_from now exists (added
    # above), so the partial-index predicate can reference it.
    conn.execute("DROP INDEX IF EXISTS uq_bet_cards_market_day")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_bet_cards_market_day "
        "ON bet_cards(series_key, trade_date) "
        "WHERE trade_date IS NOT NULL AND source_type = 'market' "
        "AND derived_from IS NULL"
    )


def _apply_schema(conn: sqlite3.Connection) -> None:
    """Run the DDL + idempotent migrations + schema_meta seed on ``conn``.

    Split out of ``init_db`` so ``ensure_schema`` (process startup) does the
    DDL once and ``get_connection`` (per request) can stay a cheap connect with
    no DDL. Safe to call repeatedly: every statement is ``IF NOT EXISTS`` /
    guarded ALTER, so re-running is a no-op.
    """
    conn.executescript(SCHEMA_SQL)
    _migrate_runs_anchor(conn)
    _migrate_dedup_index(conn)
    _migrate_card_detail(conn)
    # Seed / bump schema_meta.
    existing = conn.execute(
        "SELECT value FROM schema_meta WHERE key = ?", ("version",)
    ).fetchone()
    if existing is None:
        conn.execute(
            "INSERT INTO schema_meta(key, value) VALUES (?, ?)",
            ("version", SCHEMA_VERSION),
        )
        conn.execute(
            "INSERT INTO schema_meta(key, value) VALUES (?, datetime('now'))",
            ("created_at",),
        )
    elif existing[0] != SCHEMA_VERSION:
        conn.execute(
            "UPDATE schema_meta SET value = ? WHERE key = ?",
            (SCHEMA_VERSION, "version"),
        )
    conn.commit()


# Tracks db_paths whose schema this process has already ensured, so a fresh
# get_connection() never re-runs the DDL/migration. ":memory:" is never recorded
# (each :memory: connection is a *separate* private DB, so its schema must be
# created on the connection that uses it).
_SCHEMA_READY: set[str] = set()
_SCHEMA_LOCK = threading.Lock()


def get_connection(db_path: str = "pricelens.db") -> sqlite3.Connection:
    """Open a lightweight per-use connection: connect + row_factory + FK pragma.

    Does NOT run DDL or migrations — call ``ensure_schema(db_path)`` once at
    process startup for that. SQLite connections are bound to the creating
    thread, so this is the right primitive for the "one connection per request /
    per worker thread, always closed" pattern (see ``connection()`` and
    ``api.py``). Pass the same ``db_path`` from a background thread to share the
    on-disk database safely across threads.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def ensure_schema(db_path: str = "pricelens.db") -> None:
    """Create/upgrade the schema for ``db_path`` exactly once per process.

    Idempotent and cheap on repeat (a membership check after the first call).
    Call this once at process startup; per-request code then uses the lighter
    ``get_connection`` / ``connection`` which skip the DDL. Never caches
    ``":memory:"`` — those DBs are connection-private and must be schema'd by
    whoever opens them (that path falls through to a full ``_apply_schema``).
    """
    if db_path != ":memory:":
        with _SCHEMA_LOCK:
            if db_path in _SCHEMA_READY:
                return
    conn = get_connection(db_path)
    try:
        _apply_schema(conn)
    finally:
        conn.close()
    if db_path != ":memory:":
        with _SCHEMA_LOCK:
            _SCHEMA_READY.add(db_path)


@contextmanager
def connection(db_path: str = "pricelens.db") -> Iterator[sqlite3.Connection]:
    """Context manager: yield a fresh ``get_connection`` and guarantee close.

    Use per request / per unit of work::

        with db.connection(DB_PATH) as conn:
            ... use conn ...
        # conn is closed here, even on exception

    Calls ``ensure_schema(db_path)`` first so callers don't depend on a startup
    hook having fired (e.g. a TestClient constructed without a ``with`` block, or
    a fresh temp DB swapped in by a test). ``ensure_schema`` short-circuits on a
    set-membership check after the first call, so the per-request cost is a lock
    + lookup — it does NOT re-run the DDL/migration each request (the old
    ``init_db()``-per-request behavior this replaced). Leak-free: always closes.
    """
    ensure_schema(db_path)
    conn = get_connection(db_path)
    try:
        yield conn
    finally:
        conn.close()


def init_db(db_path: str = "pricelens.db") -> sqlite3.Connection:
    """Open (creating if missing) the SQLite DB and ensure schema is present.

    Backward-compatible: returns a live connection with the schema applied, just
    as before. Internally now reuses ``_apply_schema`` (and records the path as
    schema-ready so a later ``ensure_schema`` is a no-op). New code that wants a
    per-request connection without re-running DDL should prefer
    ``ensure_schema`` (once) + ``get_connection`` / ``connection`` instead.
    """
    conn = get_connection(db_path)
    _apply_schema(conn)
    if db_path != ":memory:":
        with _SCHEMA_LOCK:
            _SCHEMA_READY.add(db_path)
    return conn


# ---------------------------------------------------------------------------
# Helpers (private)
# ---------------------------------------------------------------------------

def _meta_cost(payload: dict | None) -> float:
    if not payload:
        return 0.0
    meta = payload.get("_meta") or {}
    return float(meta.get("cost_usd") or 0.0)


def _meta_tokens(payload: dict | None) -> tuple[int | None, int | None]:
    if not payload:
        return None, None
    usage = (payload.get("_meta") or {}).get("usage") or {}
    return usage.get("prompt_tokens"), usage.get("completion_tokens")


def _meta_tool_calls(payload: dict | None) -> int | None:
    if not payload:
        return None
    return (payload.get("_meta") or {}).get("tool_call_count")


# ---------------------------------------------------------------------------
# DAO: pipeline runs (write)
# ---------------------------------------------------------------------------

def save_pipeline_run(conn: sqlite3.Connection, output: dict) -> int:
    """Persist a full pipeline output dict in one transaction; return run_id."""
    rdcf = output.get("reverse_dcf") or {}
    decoder = output.get("decoder_output") or {}
    briefs = output.get("evidence_briefs") or []
    critic_reports = output.get("critic_reports") or []
    synthesis = output.get("synthesis")
    short_term = output.get("short_term")
    mode = output.get("mode") or decoder.get("mode") or "standard"

    decoder_cost = _meta_cost(decoder)
    decoder_pt, decoder_ct = _meta_tokens(decoder)
    evidence_cost = sum(_meta_cost(b) for b in briefs)
    synth_cost = _meta_cost(synthesis) if synthesis else 0.0
    total_cost = decoder_cost + evidence_cost + synth_cost

    current_price = float(rdcf.get("current_price") or 0.0)
    # Anchor columns (M1). Pipeline output is Market-anchored by construction, so
    # default anchor_price = current_price, anchor_type = 'market'. Honor explicit
    # overrides if a future caller sets them on the output dict.
    anchor_price = output.get("anchor_price")
    anchor_price = float(anchor_price) if anchor_price is not None else current_price
    anchor_type = output.get("anchor_type") or "market"

    with conn:  # implicit BEGIN/COMMIT, rolls back on exception
        cur = conn.execute(
            """
            INSERT INTO runs (
                ticker, company_name, generated_at, mode,
                current_price, baseline_dcf, total_cost_usd, decoder_cached,
                anchor_price, anchor_type
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                output.get("ticker"),
                output.get("company_name"),
                output.get("generated_at"),
                mode,
                current_price,
                float(rdcf.get("baseline_dcf_price") or 0.0) if rdcf.get("baseline_dcf_price") is not None else None,
                total_cost,
                0,
                anchor_price,
                anchor_type,
            ),
        )
        run_id = cur.lastrowid

        # rdcf_results
        consensus = rdcf.get("consensus_assumptions") or {}
        conn.execute(
            """
            INSERT INTO rdcf_results (
                run_id, consensus_revenue_cagr, consensus_terminal_growth,
                consensus_terminal_margin, consensus_wacc, company_inputs_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                consensus.get("revenue_cagr_5y"),
                consensus.get("terminal_growth"),
                consensus.get("terminal_fcf_margin"),
                consensus.get("wacc"),
                json.dumps(rdcf.get("company_inputs") or {}, ensure_ascii=False),
            ),
        )

        # rdcf_intervals: one row per variable, including null intervals
        interval_rows = []
        for variable, interval in (rdcf.get("implied_intervals") or {}).items():
            if interval is None:
                interval_rows.append((run_id, variable, None, None, None, None, None, None))
            else:
                interval_rows.append((
                    run_id, variable,
                    interval.get("p10"),
                    interval.get("p25"),
                    interval.get("p50"),
                    interval.get("p75"),
                    interval.get("p90"),
                    interval.get("success_rate"),
                ))
        if interval_rows:
            conn.executemany(
                """
                INSERT INTO rdcf_intervals (
                    run_id, variable, p10, p25, p50, p75, p90, success_rate
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                interval_rows,
            )

        # decoder_outputs: full raw JSON for round-trip fidelity
        conn.execute(
            """
            INSERT INTO decoder_outputs (
                run_id, raw_json, cost_usd, prompt_tokens, completion_tokens
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                run_id,
                json.dumps(decoder, ensure_ascii=False),
                decoder_cost,
                decoder_pt,
                decoder_ct,
            ),
        )

        # assumptions: standard mode uses implied_assumptions;
        # boundary mode synthesizes pseudo-assumptions from framework_hypotheses
        # so evidence_briefs (which the pipeline always indexes by position)
        # can be FK-anchored.
        assumption_list: list[dict] = []
        if mode == "standard":
            assumption_list = decoder.get("implied_assumptions") or []
        else:
            be = decoder.get("boundary_explanation") or {}
            for i, hyp in enumerate(be.get("framework_hypotheses") or []):
                assumption_list.append({
                    "id": f"{output.get('ticker')}_framework_{i}",
                    "metric": hyp.get("framework_name", ""),
                    "human_text": hyp.get("rationale", ""),
                    "interval": {},
                    # Stash extra boundary-only fields so round-trip preserves them
                    # under extra_json. (Not used by Worker B's reconstruction —
                    # boundary mode reconstructs from decoder.raw_json instead.)
                    "_boundary_extra": {
                        "framework_name": hyp.get("framework_name"),
                        "evidence_query_hint": hyp.get("evidence_query_hint"),
                    },
                })

        assumption_row_ids: list[int] = []
        for seq, a in enumerate(assumption_list):
            interval = a.get("interval") or {}
            # Strip recognised top-level keys; rest goes to extra_json
            extra = {k: v for k, v in a.items()
                     if k not in ("id", "metric", "human_text", "interval")}
            cur = conn.execute(
                """
                INSERT INTO assumptions (
                    run_id, seq, assumption_id, metric, human_text,
                    interval_p10, interval_p50, interval_p90, extra_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    seq,
                    a.get("id"),
                    a.get("metric"),
                    a.get("human_text"),
                    interval.get("p10"),
                    interval.get("p50"),
                    interval.get("p90"),
                    json.dumps(extra, ensure_ascii=False) if extra else None,
                ),
            )
            assumption_row_ids.append(cur.lastrowid)

        # evidence_briefs + nested evidence_items + sources + critic_reports.
        # Pipeline keeps briefs/critic_reports parallel to assumptions by index;
        # we honor that mapping where lengths align.
        for i, brief in enumerate(briefs):
            if i >= len(assumption_row_ids):
                # Shouldn't happen with current pipeline, but be defensive
                break
            anchor_id = assumption_row_ids[i]
            counts = brief.get("evidence_count") or {}
            conn.execute(
                """
                INSERT INTO evidence_briefs (
                    assumption_id, overall_balance,
                    support_count, refute_count, neutral_count,
                    generated_at, cost_usd, tool_call_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    anchor_id,
                    brief.get("overall_balance"),
                    int(counts.get("support") or 0),
                    int(counts.get("refute") or 0),
                    int(counts.get("neutral") or 0),
                    brief.get("generated_at"),
                    _meta_cost(brief),
                    _meta_tool_calls(brief),
                ),
            )

            for j, item in enumerate(brief.get("evidence_items") or []):
                scores = item.get("scores") or {}
                cur = conn.execute(
                    """
                    INSERT INTO evidence_items (
                        brief_id, seq, direction, claim, body_md,
                        recency_score, quality_score, relevance_score
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        anchor_id, j,
                        item.get("direction"),
                        item.get("claim"),
                        item.get("body_md"),
                        scores.get("recency"),
                        scores.get("source_quality"),
                        scores.get("relevance"),
                    ),
                )
                item_id = cur.lastrowid
                source_rows = [
                    (item_id, s.get("url"), s.get("title"),
                     s.get("date"), s.get("publisher"))
                    for s in (item.get("sources") or [])
                ]
                if source_rows:
                    conn.executemany(
                        """
                        INSERT INTO sources (
                            evidence_item_id, url, title, date, publisher
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        source_rows,
                    )

            # critic_reports parallels briefs by index
            if i < len(critic_reports):
                rep = critic_reports[i] or {}
                counts_c = rep.get("counts") or {}
                conn.execute(
                    """
                    INSERT INTO critic_reports (
                        brief_id, verdict, error_count, warning_count, info_count, issues_json
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        anchor_id,
                        rep.get("verdict"),
                        int(counts_c.get("errors") or 0),
                        int(counts_c.get("warnings") or 0),
                        int(counts_c.get("info") or 0),
                        json.dumps(rep.get("issues") or [], ensure_ascii=False),
                    ),
                )

        # syntheses
        if synthesis:
            synth_pt, synth_ct = _meta_tokens(synthesis)
            conn.execute(
                """
                INSERT INTO syntheses (
                    run_id, headline, overall_balance, confidence,
                    raw_json, cost_usd, prompt_tokens, completion_tokens
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    synthesis.get("headline"),
                    synthesis.get("overall_balance"),
                    synthesis.get("confidence"),
                    json.dumps(synthesis, ensure_ascii=False),
                    synth_cost,
                    synth_pt,
                    synth_ct,
                ),
            )

        # short_term_attributions
        if short_term:
            conn.execute(
                """
                INSERT INTO short_term_attributions (
                    run_id, window_days, return_pct, factors_json, raw_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    short_term.get("window_days"),
                    short_term.get("return_pct"),
                    json.dumps(short_term.get("factors") or [], ensure_ascii=False),
                    json.dumps(short_term, ensure_ascii=False),
                ),
            )

    return run_id


# ---------------------------------------------------------------------------
# DAO: pipeline runs (read)
# ---------------------------------------------------------------------------

def _reconstruct_run(conn: sqlite3.Connection, run_row: sqlite3.Row) -> dict:
    """Rebuild the JSON dict shape that pipeline.py originally wrote."""
    run_id = run_row["id"]

    rdcf_row = conn.execute(
        "SELECT * FROM rdcf_results WHERE run_id = ?", (run_id,)
    ).fetchone()
    interval_rows = conn.execute(
        "SELECT * FROM rdcf_intervals WHERE run_id = ?", (run_id,)
    ).fetchall()
    decoder_row = conn.execute(
        "SELECT * FROM decoder_outputs WHERE run_id = ?", (run_id,)
    ).fetchone()
    synth_row = conn.execute(
        "SELECT * FROM syntheses WHERE run_id = ?", (run_id,)
    ).fetchone()
    st_row = conn.execute(
        "SELECT * FROM short_term_attributions WHERE run_id = ?", (run_id,)
    ).fetchone()

    # Reverse DCF reconstruction
    implied_intervals: dict[str, Any] = {}
    for r in interval_rows:
        # Treat the row as null-interval only if EVERY percentile is null;
        # otherwise rebuild it.
        if r["p25"] is None and r["p50"] is None and r["p75"] is None \
                and r["success_rate"] is None:
            implied_intervals[r["variable"]] = None
        else:
            interval = {
                "p25": r["p25"],
                "p50": r["p50"],
                "p75": r["p75"],
                "success_rate": r["success_rate"],
            }
            if r["p10"] is not None:
                interval["p10"] = r["p10"]
            if r["p90"] is not None:
                interval["p90"] = r["p90"]
            implied_intervals[r["variable"]] = interval

    rdcf: dict[str, Any] = {
        "ticker": run_row["ticker"],
        "current_price": run_row["current_price"],
        "baseline_dcf_price": run_row["baseline_dcf"],
        "consensus_assumptions": {
            "revenue_cagr_5y": rdcf_row["consensus_revenue_cagr"] if rdcf_row else None,
            "terminal_growth": rdcf_row["consensus_terminal_growth"] if rdcf_row else None,
            "terminal_fcf_margin": rdcf_row["consensus_terminal_margin"] if rdcf_row else None,
            "wacc": rdcf_row["consensus_wacc"] if rdcf_row else None,
        },
        "implied_intervals": implied_intervals,
        "company_inputs": json.loads(rdcf_row["company_inputs_json"]) if rdcf_row and rdcf_row["company_inputs_json"] else {},
    }

    # Decoder
    decoder_dict: dict[str, Any] = {}
    if decoder_row and decoder_row["raw_json"]:
        decoder_dict = json.loads(decoder_row["raw_json"])

    # Briefs + critic — rebuild from per-assumption rows, ordered by seq
    assumption_rows = conn.execute(
        "SELECT * FROM assumptions WHERE run_id = ? ORDER BY seq",
        (run_id,),
    ).fetchall()
    evidence_briefs: list[dict] = []
    critic_reports: list[dict] = []
    for a in assumption_rows:
        brief_row = conn.execute(
            "SELECT * FROM evidence_briefs WHERE assumption_id = ?", (a["id"],)
        ).fetchone()
        if brief_row is None:
            continue
        item_rows = conn.execute(
            "SELECT * FROM evidence_items WHERE brief_id = ? ORDER BY seq",
            (a["id"],),
        ).fetchall()
        items_out: list[dict] = []
        for it in item_rows:
            src_rows = conn.execute(
                "SELECT * FROM sources WHERE evidence_item_id = ? ORDER BY id",
                (it["id"],),
            ).fetchall()
            items_out.append({
                "direction": it["direction"],
                "claim": it["claim"],
                "body_md": it["body_md"],
                "sources": [
                    {
                        "url": s["url"],
                        "title": s["title"],
                        "date": s["date"],
                        "publisher": s["publisher"],
                    }
                    for s in src_rows
                ],
                "scores": {
                    "recency": it["recency_score"],
                    "source_quality": it["quality_score"],
                    "relevance": it["relevance_score"],
                },
            })
        brief_out: dict[str, Any] = {
            "assumption_id": a["assumption_id"],
            "assumption_text": a["human_text"],
            "evidence_items": items_out,
            "overall_balance": brief_row["overall_balance"],
            "evidence_count": {
                "support": brief_row["support_count"],
                "refute": brief_row["refute_count"],
                "neutral": brief_row["neutral_count"],
            },
            "generated_at": brief_row["generated_at"],
            "_meta": {
                "cost_usd": brief_row["cost_usd"],
                "tool_call_count": brief_row["tool_call_count"],
            },
        }
        evidence_briefs.append(brief_out)

        crit_row = conn.execute(
            "SELECT * FROM critic_reports WHERE brief_id = ?", (a["id"],)
        ).fetchone()
        if crit_row:
            critic_reports.append({
                "issues": json.loads(crit_row["issues_json"]) if crit_row["issues_json"] else [],
                "verdict": crit_row["verdict"],
                "counts": {
                    "errors": crit_row["error_count"],
                    "warnings": crit_row["warning_count"],
                    "info": crit_row["info_count"],
                },
            })

    # Synthesis
    synthesis: dict | None = None
    if synth_row and synth_row["raw_json"]:
        synthesis = json.loads(synth_row["raw_json"])

    # Short-term
    short_term: dict | None = None
    if st_row and st_row["raw_json"]:
        short_term = json.loads(st_row["raw_json"])

    return {
        "ticker": run_row["ticker"],
        "company_name": run_row["company_name"],
        "generated_at": run_row["generated_at"],
        "mode": run_row["mode"],
        "reverse_dcf": rdcf,
        "decoder_output": decoder_dict,
        "evidence_briefs": evidence_briefs,
        "critic_reports": critic_reports,
        "synthesis": synthesis,
        "short_term": short_term,
    }


def get_latest_run(conn: sqlite3.Connection, ticker: str) -> dict | None:
    """Latest run for `ticker` in original JSON shape, or None if missing."""
    row = conn.execute(
        """
        SELECT * FROM runs
        WHERE ticker = ?
        ORDER BY generated_at DESC, id DESC
        LIMIT 1
        """,
        (ticker,),
    ).fetchone()
    if row is None:
        return None
    return _reconstruct_run(conn, row)


def get_latest_run_with_short_term(conn: sqlite3.Connection, ticker: str) -> dict | None:
    """Latest run for `ticker` that has short-term attribution; returns ONLY the
    short_term sub-dict (mirrors /api/decode/{ticker}/short-term behavior)."""
    row = conn.execute(
        """
        SELECT r.* FROM runs r
        JOIN short_term_attributions st ON st.run_id = r.id
        WHERE r.ticker = ?
        ORDER BY r.generated_at DESC, r.id DESC
        LIMIT 1
        """,
        (ticker,),
    ).fetchone()
    if row is None:
        return None
    st_row = conn.execute(
        "SELECT raw_json FROM short_term_attributions WHERE run_id = ?", (row["id"],)
    ).fetchone()
    if st_row is None or st_row["raw_json"] is None:
        return None
    return json.loads(st_row["raw_json"])


def list_tickers(conn: sqlite3.Connection) -> list[str]:
    """Distinct tickers across all runs, sorted ascending."""
    rows = conn.execute(
        "SELECT DISTINCT ticker FROM runs ORDER BY ticker"
    ).fetchall()
    return [r["ticker"] for r in rows]


# ---------------------------------------------------------------------------
# DAO: LLM cache
# ---------------------------------------------------------------------------

def cache_get(conn: sqlite3.Connection, category: str, key: str) -> dict | None:
    """Look up a cached LLM payload by (category, key). Returns deserialized dict or None."""
    row = conn.execute(
        "SELECT payload_json FROM llm_cache WHERE cache_key = ? AND category = ?",
        (key, category),
    ).fetchone()
    if row is None:
        return None
    return json.loads(row["payload_json"])


def cache_put(
    conn: sqlite3.Connection,
    category: str,
    key: str,
    payload: dict,
    ticker: str | None = None,
) -> None:
    """Upsert a payload into the llm_cache table. Pulls cost/tokens from `_meta` if present."""
    cost = _meta_cost(payload)
    prompt_tokens, completion_tokens = _meta_tokens(payload)
    with conn:
        conn.execute(
            """
            INSERT INTO llm_cache (
                cache_key, category, ticker, payload_json,
                cost_usd, prompt_tokens, completion_tokens
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET
                category          = excluded.category,
                ticker            = excluded.ticker,
                payload_json      = excluded.payload_json,
                cost_usd          = excluded.cost_usd,
                prompt_tokens     = excluded.prompt_tokens,
                completion_tokens = excluded.completion_tokens,
                created_at        = datetime('now')
            """,
            (
                key,
                category,
                ticker,
                json.dumps(payload, ensure_ascii=False),
                cost or None,
                prompt_tokens,
                completion_tokens,
            ),
        )


# ===========================================================================
# Bet Card data model (M1) — type + passive DAO
# ===========================================================================
#
# A BetCard is an immutable snapshot of one decoded bet. Two sub-types share one
# type, distinguished by `card_kind`:
#   - 'single'    : one subject (a ticker). May carry a `bet` value (NULL for
#                   Opinion cards with a missing target). Reuses the `runs`
#                   sub-tables via `run_id`. May also carry `theme_exposures`
#                   rows when decoded in anchor mode (R1).
#   - 'portfolio' : a basket. Carries `holdings` + `theme_exposures`; `run_id`
#                   is NULL.
#
# This DAO is PASSIVE: it serializes/persists/reads cards. It never decodes,
# synthesizes, renders, or emits activity events.

SINGLE = "single"
PORTFOLIO = "portfolio"

# source_type values. Market/Portfolio are the MVP scope; analyst_pt/opinion
# are accepted by the schema (V2) so cards round-trip cleanly when they land.
SOURCE_MARKET = "market"
SOURCE_ANALYST_PT = "analyst_pt"
SOURCE_OPINION = "opinion"
SOURCE_PORTFOLIO = "portfolio"

# source_types whose snapshots are deduped to one card per trading day.
_DAILY_DEDUP_SOURCES = {SOURCE_MARKET}


@dataclass
class Holding:
    """One position inside a portfolio card."""
    ticker: str
    weight_pct: float | None = None
    run_id: int | None = None


@dataclass
class ThemeExposure:
    """A thematic exposure attached to a card. Used by both card kinds."""
    theme: str
    exposure_pct: float | None = None
    contributing_tickers: list[str] = field(default_factory=list)
    is_concentration_risk: bool = False


@dataclass
class BetCard:
    """Immutable snapshot of one decoded bet.

    `card_id` and `series_key` are derived in `__post_init__` if not supplied:
      - card_id    : a fresh UUID4 hex (unique per snapshot)
      - series_key : "<subject>|<source_type>" (groups snapshots of one bet)
    `created_at` defaults to now (UTC, ISO-8601). `trade_date` is the trading-day
    bucket used for Market-card dedup; defaults to the date part of created_at.
    """
    subject: str
    source_type: str
    card_kind: str = SINGLE
    source_ref: str | None = None
    bet: float | None = None          # nullable — Opinion cards may lack a target
    run_id: int | None = None         # single cards reuse runs; NULL for portfolio
    card_id: str | None = None
    series_key: str | None = None
    trade_date: str | None = None
    created_at: str | None = None
    holdings: list[Holding] = field(default_factory=list)
    theme_exposures: list[ThemeExposure] = field(default_factory=list)
    # Lineage (schema v3): a what-if / revision is a NEW card derived from a parent
    # (the original stays immutable). NULL derived_from = an original card.
    derived_from: str | None = None          # parent card_id, or None for originals
    derivation_kind: str | None = None        # 'whatif' | 'revision' | None
    derivation: dict | None = None            # {params, prompt, diff:[{field,before,after}]}
    # decode_detail is a RUNTIME attribute set by the decoder (not a dataclass
    # field); save_card persists it to decode_detail_json and card_from_row
    # restores it, so reloaded cards can be interrogated/revised.

    def __post_init__(self) -> None:
        if self.card_id is None:
            self.card_id = uuid.uuid4().hex
        if self.series_key is None:
            self.series_key = make_series_key(self.subject, self.source_type)
        if self.created_at is None:
            self.created_at = datetime.now(timezone.utc).isoformat()
        if self.trade_date is None:
            self.trade_date = self.created_at[:10]  # YYYY-MM-DD


def make_series_key(subject: str, source_type: str) -> str:
    """Canonical grouping key for a bet's snapshot series."""
    return f"{subject}|{source_type}"


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def _json_default(o: Any) -> Any:
    """json.dumps fallback so a decode_detail blob is always serializable.

    decode_detail nests dataclasses (e.g. ThemeExposure inside anchor_mode); turn
    any dataclass into a dict so it round-trips, and degrade anything else to its
    str() rather than raising (persisting *something* beats a 500)."""
    if is_dataclass(o) and not isinstance(o, type):
        return asdict(o)
    return str(o)


def _dump_detail(detail: Any) -> str | None:
    """Serialize a decode_detail dict to JSON text (or None). Never raises."""
    if not detail:
        return None
    try:
        return json.dumps(detail, ensure_ascii=False, default=_json_default)
    except (TypeError, ValueError):
        return None


def card_to_json(card: BetCard) -> dict:
    """Lossless dict form of a BetCard (JSON-safe).

    Also surfaces two derived display hints from the (non-persisted)
    decode_detail when it is present on a freshly-decoded card: ``mode``
    (traditional | anchor_primary | anchor_fallback) and ``narrative_premium``
    (0..1 — share of price the DCF base business value fails to explain).
    Reloaded / round-tripped cards carry no decode_detail, so both are None
    there; that keeps card_to_json/from_json round-trip equality intact."""
    _dd = getattr(card, "decode_detail", None) or {}
    return {
        "card_id": card.card_id,
        "subject": card.subject,
        "source_type": card.source_type,
        "card_kind": card.card_kind,
        "source_ref": card.source_ref,
        "bet": card.bet,
        "run_id": card.run_id,
        "series_key": card.series_key,
        "trade_date": card.trade_date,
        "created_at": card.created_at,
        "holdings": [
            {"ticker": h.ticker, "weight_pct": h.weight_pct, "run_id": h.run_id}
            for h in card.holdings
        ],
        "theme_exposures": [
            {
                "theme": t.theme,
                "exposure_pct": t.exposure_pct,
                "contributing_tickers": list(t.contributing_tickers or []),
                "is_concentration_risk": bool(t.is_concentration_risk),
            }
            for t in card.theme_exposures
        ],
        # Derived display hints from decode_detail (None on reloaded cards).
        "mode": _dd.get("mode"),
        "narrative_premium": _dd.get("narrative_premium"),
        # Compact market-narrative summary (regime/headline/bindings/source_quality);
        # the full validated narrative stays on decode_detail.
        "market_narrative": (_dd.get("market_narrative") or {}).get("summary"),
        # Portfolio honesty: tickers whose leg couldn't be decoded (data source
        # temporarily unavailable). Lets the UI warn instead of presenting an
        # empty-but-valid theme set as a real "no common bet". [] on clean cards.
        "failed_legs": list((_dd.get("failed_legs") or {}).keys()),
        # Lineage (v3): non-null on a what-if / revision card derived from a parent.
        "derived_from": card.derived_from,
        "derivation_kind": card.derivation_kind,
        "derivation": card.derivation,
        # Compact display projection (None for portfolio/detail-less cards) so a
        # real/reloaded single card renders rich, not the thin "no data" branch.
        "_display": build_card_display(card),
    }


def card_to_json_full(card: BetCard) -> dict:
    """card_to_json PLUS the full decode_detail (evidence, anchor_mode, agent_trace,
    market_narrative.full). Used by get_card / the Q&A agent, which need the rich
    internal state. list_cards stays on the compact card_to_json to avoid bloating
    list responses with every card's full detail."""
    out = card_to_json(card)
    out["decode_detail"] = getattr(card, "decode_detail", None)
    return out


def _fmt_implied(v: Any, unit: str) -> str:
    """Format an implied value for the card: 54.2x (multiple) / 18% (fraction) /
    1,234.00 (level) / 无解 (no value)."""
    if not isinstance(v, (int, float)):
        return "无解"
    if unit:
        return f"{v:.1f}{unit}"
    if 0 < abs(v) < 1:
        return f"{v * 100:.0f}%"
    return f"{v:,.2f}"


def _money_big(v: Any) -> str:
    """Compact large-dollar format: $1.20T / $255B / $640M / $1,234."""
    if not isinstance(v, (int, float)):
        return "—"
    a = abs(v)
    if a >= 1e12:
        return f"${v / 1e12:.2f}T"
    if a >= 1e9:
        return f"${v / 1e9:,.0f}B"
    if a >= 1e6:
        return f"${v / 1e6:,.0f}M"
    return f"${v:,.0f}"


def _pct1(v: Any) -> str:
    return f"{v * 100:.1f}%" if isinstance(v, (int, float)) else "—"


def _multiple_compute_text(lens: str, anchor: float, fund: dict) -> str:
    """The mechanical 'price ÷ metric' step for a multiple lens, from real inputs.
    Returns '' when the denominator isn't available (honest — no fabricated step)."""
    shares = fund.get("shares_outstanding")
    mcap = anchor * shares if (anchor and shares) else None
    eps, rev, fcf = fund.get("eps_ttm"), fund.get("revenue_ttm"), fund.get("fcf_ttm")
    ebitda, be, nd = fund.get("ebitda_ttm"), fund.get("book_equity"), (fund.get("net_debt") or 0.0)
    g = fund.get("growth_rate")
    if lens == "pe" and isinstance(eps, (int, float)) and eps:
        return f"现价 ${anchor:,.0f} ÷ 每股收益 EPS ${eps:.2f}"
    if lens == "ps" and mcap and isinstance(rev, (int, float)) and rev:
        return f"市值 {_money_big(mcap)} ÷ 营收 {_money_big(rev)}"
    if lens == "p_fcf" and mcap and isinstance(fcf, (int, float)) and fcf:
        return f"市值 {_money_big(mcap)} ÷ 自由现金流 {_money_big(fcf)}"
    if lens == "ev_ebitda" and mcap and isinstance(ebitda, (int, float)) and ebitda:
        return f"企业价值 {_money_big(mcap + nd)} ÷ EBITDA {_money_big(ebitda)}"
    if lens == "p_b" and mcap and isinstance(be, (int, float)) and be:
        return f"市值 {_money_big(mcap)} ÷ 净资产 {_money_big(be)}"
    if lens == "peg" and isinstance(g, (int, float)) and g:
        return f"隐含 P/E ÷ 近一年增速 {g * 100:.0f}%"
    return ""


def _dcf_breakdown(revenue, cagr, margin, wacc, g, net_debt, shares) -> dict | None:
    """The full DCF working AT THE IMPLIED CAGR — mirrors
    reverse_dcf.dcf_equity_value_per_share line-for-line so `per_share` reconciles to
    the market price the reverse-solve matched. Returns the 5-year projection +
    Gordon terminal value + equity bridge for the derivation worksheet. None when
    infeasible (wacc<=g) / inputs missing — never a fabricated sheet."""
    vals = (revenue, cagr, margin, wacc, g, shares)
    if not all(isinstance(x, (int, float)) for x in vals) or not shares or wacc <= g:
        return None
    nd = net_debt if isinstance(net_debt, (int, float)) else 0.0
    years, rev, pv_sum = [], float(revenue), 0.0
    for y in range(1, 6):
        rev *= (1 + cagr)
        fcf = rev * margin
        disc = (1 + wacc) ** y
        pv = fcf / disc
        pv_sum += pv
        years.append({"y": y, "revenue": rev, "fcf": fcf, "disc": disc, "pv": pv})
    terminal_fcf = rev * margin * (1 + g)          # rev = year-5 revenue
    terminal_value = terminal_fcf / (wacc - g)
    terminal_pv = terminal_value / (1 + wacc) ** 5
    ev = pv_sum + terminal_pv
    equity = ev - nd
    return {
        "inputs": {"revenue": revenue, "cagr": cagr, "margin": margin, "wacc": wacc,
                   "g": g, "net_debt": nd, "shares": shares},
        "years": years, "pv_fcf_sum": pv_sum, "terminal_fcf": terminal_fcf,
        "terminal_value": terminal_value, "terminal_pv": terminal_pv,
        "ev": ev, "equity": equity, "per_share": equity / shares,
    }


# The GENERIC baseline revenue CAGR for the base-business-value forward DCF.
# ⚠ One-size-fits-all (mirrors decoder._lens_dcf), NOT the company's own analyst
# consensus — the data layer has no reliable 5y revenue consensus, so we use a
# generic baseline and surface the company's actual growth for contrast rather than
# overclaiming "consensus". Pinned so the base-value build-up reconciles to `base`
# (verify_decode_detail_persistence.py's reconciliation guard flags any drift).
_CONSENSUS_BASE_CAGR = 0.15


def _build_derivations(dd: dict) -> dict | None:
    """Multi-level derivation tree from REAL computed numbers (no fabrication):
    root 现价 → a branch per lens (or base/premium for anchor) → mechanical chain →
    implied value (band) → sub-implication. Missing inputs render nothing, never
    invented. Drives the front-end's renderDerivationTree."""
    anchor = dd.get("anchor_price")
    if not isinstance(anchor, (int, float)) or anchor <= 0:
        return None
    fund = dd.get("fundamentals") or {}
    shares = fund.get("shares_outstanding")
    mcap = anchor * shares if shares else None
    binds = ((dd.get("market_narrative") or {}).get("summary") or {}).get("bindings") or []

    def _ev(i):
        b = binds[i] if i < len(binds) else None
        if isinstance(b, dict) and b.get("evidence_verdict"):
            return {"evidence_verdict": b.get("evidence_verdict"), "diverges": bool(b.get("diverges"))}
        return None

    root = {"label": "现价 · 输入", "value": f"${anchor:,.2f}",
            "sub": (f"市值 {_money_big(mcap)}" if mcap else "")}
    branches: list[dict] = []
    am = dd.get("anchor_mode") or {}
    bi = 0  # binding index for evidence matching (best-effort, by order)

    if am.get("components"):
        base = am.get("base_business_value")   # = upper anchor (premium reference)
        # Mirror the base with _dcf_breakdown using the params persisted on the DCF
        # cross-lens so the worksheet reconciles WITHOUT a re-decode (works on cards
        # already in the DB). Tier 1: the base is a RANGE built only from the
        # company's OWN data — lower = conservative zero-growth, upper = historical
        # continuation — and the inputs are now LIVE / sourced, not hardcoded.
        dcf_ref = next((r for r in (dd.get("cross_lenses") or [])
                        if isinstance(r, dict) and r.get("lens") == "dcf"
                        and isinstance(r.get("baseline_dcf_price"), (int, float))), None)
        if isinstance(base, (int, float)):
            base_pct = max(0, round(base / anchor * 100)) if anchor else 0
            base_levels = []
            if dcf_ref is not None:
                w = dcf_ref.get("consensus_wacc")
                tg = dcf_ref.get("consensus_terminal_growth")
                m = dcf_ref.get("consensus_terminal_fcf_margin")
                rf = dcf_ref.get("risk_free_used")
                hist = dcf_ref.get("hist_cagr")
                implied = dcf_ref.get("implied_cagr")
                sector = dcf_ref.get("sector")
                ind_cagr = dcf_ref.get("industry_cagr")
                sec_tam = dcf_ref.get("sector_tam")
                mom_start = dcf_ref.get("momentum_start")
                sc_cons = dcf_ref.get("scenario_conservative")
                sc_ind = dcf_ref.get("scenario_industry")
                sc_mom = dcf_ref.get("scenario_momentum")
                rev5, share = dcf_ref.get("implied_rev_5y"), dcf_ref.get("implied_market_share")
                rev, nd, sh = (fund.get("revenue_ttm"), fund.get("net_debt"),
                               fund.get("shares_outstanding"))
                # Live / sourced params (no more 凭空写死).
                aparts = []
                if isinstance(rf, (int, float)): aparts.append(f"无风险利率 {_pct1(rf)}(10Y 美债实时)")
                if isinstance(w, (int, float)): aparts.append(f"贴现率 {_pct1(w)}(CAPM 权益成本)")
                if isinstance(tg, (int, float)): aparts.append(f"终值增速 {_pct1(tg)}(长期 GDP 锚)")
                if isinstance(m, (int, float)): aparts.append(f"FCF 利润率 {_pct1(m)}(实际 TTM)")
                if aparts:
                    base_levels.append({"kind": "assume", "text": "实时参数:" + " · ".join(aparts)})
                # Scenario ① conservative zero-growth (the floor).
                if isinstance(sc_cons, (int, float)):
                    bd = _dcf_breakdown(rev, 0.0, m, w, 0.0, nd, sh)
                    if bd:
                        bd["summary_label"] = "DCF 建模底稿 · 零增长(最保守)"
                        bd["cagr_label"] = "增速(零增长)"; bd["reconcile_label"] = "保守情景价值"
                    p = max(0, round(sc_cons / anchor * 100)) if anchor else 0
                    base_levels.append({"kind": "implied", "label": "情景① 保守 · 零增长",
                                        "impl": _money_big(sc_cons), "impl_num": sc_cons,
                                        "text": f"盈利零增长 → 占现价 {p}%", "breakdown": bd})
                # Scenario ② industry norm (sector long-run CAGR) — the PREMIUM REFERENCE.
                if isinstance(sc_ind, (int, float)):
                    bd = _dcf_breakdown(rev, ind_cagr if isinstance(ind_cagr, (int, float)) else 0.0,
                                        m, w, tg, nd, sh)
                    if bd:
                        bd["summary_label"] = "DCF 建模底稿 · 行业长期增速(部门基准)"
                        bd["cagr_label"] = "增速(行业基准)"; bd["reconcile_label"] = "行业情景价值"
                    p = max(0, round(sc_ind / anchor * 100)) if anchor else 0
                    sct = f"{sector} " if sector else ""
                    base_levels.append({"kind": "implied", "label": "情景② 行业 · 长期基准(叙事溢价参照)",
                                        "impl": _money_big(sc_ind), "impl_num": sc_ind,
                                        "text": f"按 {sct}行业长期增速 {_pct1(ind_cagr)} → 占现价 {p}%",
                                        "breakdown": bd})
                # Scenario ③ momentum (current growth fading to GDP). No constant-CAGR
                # worksheet — the growth fades year by year; show the value + the path.
                if isinstance(sc_mom, (int, float)):
                    p = max(0, round(sc_mom / anchor * 100)) if anchor else 0
                    mtxt = (f"当前增速 {mom_start * 100:.0f}% 逐年衰减到长期 GDP {_pct1(tg)}"
                            if isinstance(mom_start, (int, float)) else "当前增速逐年衰减建模")
                    base_levels.append({"kind": "implied", "label": "情景③ 动能 · 当前增速衰减",
                                        "impl": _money_big(sc_mom), "impl_num": sc_mom,
                                        "text": f"{mtxt} → 占现价 {p}%"})
                # Implied-number LANDING — the reverse-solved CAGR turned into reality.
                if isinstance(implied, (int, float)) and isinstance(rev5, (int, float)):
                    land = f"反解隐含增速 {implied * 100:.0f}% → 5 年后营收 {_money_big(rev)} → {_money_big(rev5)}"
                    if isinstance(share, (int, float)) and sector:
                        land += f" → 需占 {sector} 行业 TAM({_money_big(sec_tam)}) 的 {share * 100:.0f}%"
                    base_levels.append({"kind": "imply", "text": "落地检验:" + land})
                # Three-way growth contrast — all the company's own / sector data.
                if isinstance(implied, (int, float)):
                    bits = [f"市场隐含 {implied * 100:.0f}%"]
                    if isinstance(hist, (int, float)): bits.append(f"公司历史 {hist * 100:.0f}%")
                    if isinstance(ind_cagr, (int, float)): bits.append(f"行业长期 {ind_cagr * 100:.0f}%")
                    base_levels.append({"kind": "imply", "text": "增速对照:" + " vs ".join(bits)})
            # base_business_value = the INDUSTRY scenario (the premium reference).
            base_levels.append({"kind": "implied", "label": "基础业务价值(行业基准锚)",
                                "impl": _money_big(base), "impl_num": base,
                                "text": f"以行业长期增速为参照 → 行业锚占现价 {base_pct}%;"
                                        f"现价高出的部分 = 市场押你跑赢行业的溢价"})
            base_levels.append({"kind": "imply",
                                "text": "= 三情景(保守↔动能)只用公司自己 + 行业基准的数,不引第三方预测;"
                                        "叙事溢价 = 现价超出行业基准锚的部分"})
            branches.append({"lens": "base", "label": "基础业务价值(三情景:保守/行业/动能)", "primary": True,
                             "levels": base_levels})
        gap = (max(0.0, anchor - base) if isinstance(base, (int, float)) and anchor else None)
        # theme_exposures may be dicts (reloaded card) OR ThemeExposure dataclasses
        # (freshly decoded, pre-round-trip) — normalize both to dicts.
        themes = [(te if isinstance(te, dict) else {
                       "theme": getattr(te, "theme", None),
                       "exposure_pct": getattr(te, "exposure_pct", None),
                       "is_concentration_risk": getattr(te, "is_concentration_risk", None)})
                  for te in (am.get("theme_exposures") or [])]
        for ci, comp in enumerate(am["components"]):
            amt = comp.get("implied_amount")
            levels = []
            # Bridge: make the 现价 − 基础 = 溢价 arithmetic an explicit derivation step.
            if ci == 0 and gap is not None:
                levels.append({"kind": "compute",
                               "text": f"现价 {_money_big(anchor)} − 基础 {_money_big(base)} "
                                       f"= 叙事/期权溢价 {_money_big(gap)}(占现价 {round(gap / anchor * 100)}%)"})
            levels.append({"kind": "implied", "label": comp.get("lens_label") or comp.get("lens") or "成分",
                           "impl": (_money_big(amt) if isinstance(amt, (int, float)) else "—"),
                           "impl_num": amt if isinstance(amt, (int, float)) else None, "ev": _ev(bi),
                           "text": (f"= 现价里 {round(amt / anchor * 100)}% 的溢价"
                                    if isinstance(amt, (int, float)) else "")})
            bi += 1
            if comp.get("claim"):
                levels.append({"kind": "imply", "text": "claim:" + str(comp["claim"])})
            if comp.get("implied_assumption"):
                levels.append({"kind": "imply", "text": "要兑现:" + str(comp["implied_assumption"])})
            # Theme exposures (R1): quantified rows replace the bare theme label.
            if ci == 0 and any(t.get("theme") for t in themes):
                for te in themes:
                    if not te.get("theme"):
                        continue
                    pctv = te.get("exposure_pct")
                    conc = " · ⚠ 集中风险" if te.get("is_concentration_risk") else ""
                    levels.append({"kind": "imply",
                                   "text": "主题暴露:" + str(te.get("theme"))
                                           + (f" 占现价 {round(pctv)}%" if isinstance(pctv, (int, float)) else "")
                                           + conc})
            elif comp.get("theme"):
                levels.append({"kind": "imply", "text": "主题:" + str(comp["theme"])})
            branches.append({"lens": comp.get("lens") or "comp",
                             "label": comp.get("lens_label") or comp.get("lens") or "成分", "levels": levels})
        return {"root": root, "branches": branches}

    # traditional: a branch per lens (primary + cross)
    primary = dd.get("primary_lens")
    lenses = ([primary] if isinstance(primary, dict) else [])
    lenses += [c for c in (dd.get("cross_lenses") or []) if isinstance(c, dict)]
    for r in lenses:
        lens = r.get("lens")
        iv = r.get("implied_value")
        unit = r.get("unit") or ""
        band = r.get("band") or {}
        p25, p50, p75 = band.get("p25"), band.get("p50"), band.get("p75")
        band_num = ({"p25": p25, "p50": p50, "p75": p75}
                    if isinstance(p25, (int, float)) and isinstance(p75, (int, float)) else None)
        is_primary = (r is primary)
        if lens == "dcf":
            levels = []
            w, tg, m = r.get("consensus_wacc"), r.get("consensus_terminal_growth"), r.get("consensus_terminal_fcf_margin")
            rf, hist = r.get("risk_free_used"), r.get("hist_cagr_capped")
            b_low, b_high = r.get("baseline_dcf_low"), r.get("baseline_dcf_high")
            parts = []
            if isinstance(rf, (int, float)): parts.append(f"无风险利率 {_pct1(rf)}(10Y 美债实时)")
            if isinstance(w, (int, float)): parts.append(f"贴现率 {_pct1(w)}(CAPM 权益成本)")
            if isinstance(tg, (int, float)): parts.append(f"终值增速 {_pct1(tg)}")
            if isinstance(m, (int, float)): parts.append(f"FCF 利润率 {_pct1(m)}")
            if parts:
                levels.append({"kind": "assume", "text": "固定其余假设:" + " · ".join(parts) + " → 只解营收增速"})
            if isinstance(b_low, (int, float)) and isinstance(b_high, (int, float)):
                lo_p = round(min(b_low, anchor) / anchor * 100) if anchor else None
                hi_p = round(min(b_high, anchor) / anchor * 100) if anchor else None
                _ind = r.get("scenario_industry")
                _indtxt = (f" · 行业锚 ${_ind:,.0f}" if isinstance(_ind, (int, float)) else "")
                levels.append({"kind": "baseline",
                               "text": f"三情景区间:${b_low:,.2f}(保守)↔ ${b_high:,.2f}(动能){_indtxt}"
                                       f" → 解释现价 {lo_p}%–{hi_p}%"})
            else:
                bl = r.get("baseline_dcf_price")
                if isinstance(bl, (int, float)):
                    expl = round(min(bl, anchor) / anchor * 100) if anchor else None
                    levels.append({"kind": "baseline",
                                   "text": f"基线 DCF = ${bl:,.2f} → 解释现价 {expl}%"})
            if iv is not None:
                levels.append({"kind": "implied", "label": r.get("implied_label") or "隐含 5 年营收 CAGR",
                               "impl": _fmt_implied(iv, unit), "impl_num": iv, "unit": unit,
                               "band": band_num, "ev": _ev(bi),
                               "breakdown": _dcf_breakdown(fund.get("revenue_ttm"), iv, m, w, tg,
                                                           fund.get("net_debt"), fund.get("shares_outstanding"))})
                rev, g0 = fund.get("revenue_ttm"), fund.get("growth_rate")
                if isinstance(rev, (int, float)) and rev > 0:
                    rev5 = rev * ((1 + iv) ** 5)
                    sub = f"= 营收 5 年 {_money_big(rev)} → {_money_big(rev5)}({rev5 / rev:.1f}×)"
                    if isinstance(g0, (int, float)):
                        sub += f" · vs 近一年实际 {g0 * 100:.0f}%"
                    if isinstance(p75, (int, float)) and iv > p75:
                        sub += " · ↑偏激进"
                    levels.append({"kind": "imply", "text": sub})
            else:
                levels.append({"kind": "implied", "label": r.get("implied_label") or "隐含增速",
                               "impl": "无可行解", "nosol": True,
                               "text": "DCF 无法在可行区间反解出增速 → 价格超出 DCF 可解释范围"})
            bi += 1
            branches.append({"lens": "dcf", "label": "用 DCF 反解隐含增速", "primary": is_primary, "levels": levels})
        else:
            levels = []
            ct = _multiple_compute_text(lens, anchor, fund)
            if ct:
                levels.append({"kind": "compute", "text": ct})
            levels.append({"kind": "implied", "label": r.get("implied_label") or r.get("lens_label") or lens,
                           "impl": _fmt_implied(iv, unit),
                           "impl_num": iv if isinstance(iv, (int, float)) else None,
                           "unit": unit, "band": band_num, "ev": _ev(bi), "nosol": iv is None})
            bi += 1
            branches.append({"lens": lens, "label": r.get("implied_label") or r.get("lens_label") or lens,
                             "primary": is_primary, "levels": levels})
    return {"root": root, "branches": branches} if branches else None


def _bet_statement(dd: dict) -> dict | None:
    """The ONE consistent headline every card leads with — 'what is the market
    betting at this price?'. Anchored on the reverse-DCF implied growth (the
    universal bet); narrative-priced names (DCF can't explain the price) state the
    narrative-premium bet instead. All from real numbers — no fabrication."""
    anchor = dd.get("anchor_price")
    if not isinstance(anchor, (int, float)) or anchor <= 0:
        return None
    fund = dd.get("fundamentals") or {}
    am = dd.get("anchor_mode") or {}
    np_ = dd.get("narrative_premium")
    dcf = None
    for r in ([dd.get("primary_lens")] + list(dd.get("cross_lenses") or [])):
        if isinstance(r, dict) and r.get("lens") == "dcf":
            dcf = r
            break
    cagr = dcf.get("implied_value") if dcf else None

    # Narrative-priced: anchor decomposition with a real premium, OR the DCF can't
    # pin an implied growth (price outside the feasible range) → the bet IS the story.
    if am.get("components") and (cagr is None or (isinstance(np_, (int, float)) and np_ >= 0.5)):
        pct = round((np_ or 0) * 100)
        theme = ""
        tex = am.get("theme_exposures") or []
        if tex and isinstance(tex[0], dict):
            theme = tex[0].get("theme") or ""
        if not theme:
            for comp in am.get("components", []):
                if comp.get("theme"):
                    theme = comp["theme"]
                    break
        core = (theme + "兑现") if theme else "叙事兑现"
        return {"kind": "narrative", "core": core,
                "detail": f"现价 {pct}% 是 DCF 业务价值解释不了的溢价 —— 需要的增长远超常规估值能锚定的水平"}

    if isinstance(cagr, (int, float)):
        core = f"未来 5 年营收年增 ~{cagr * 100:.0f}%"
        bits = []
        g0 = fund.get("growth_rate")
        if isinstance(g0, (int, float)):
            bits.append(f"vs 近一年实际 {g0 * 100:.0f}%")
        prim = dd.get("primary_lens")
        if isinstance(prim, dict) and prim.get("unit") == "x" and isinstance(prim.get("implied_value"), (int, float)):
            bits.append(f"把 {prim.get('implied_label') or prim.get('lens_label') or 'P/E'} 推到 {prim['implied_value']:.1f}x")
        band = (dcf or {}).get("band") or {}
        if isinstance(band.get("p25"), (int, float)) and isinstance(band.get("p75"), (int, float)):
            bits.append(f"蒙特卡洛区间 {band['p25'] * 100:.0f}–{band['p75'] * 100:.0f}%")
        return {"kind": "growth", "core": core, "detail": " · ".join(bits)}

    # No DCF growth + not narrative → fall back to the primary multiple (still a bet).
    prim = dd.get("primary_lens")
    if isinstance(prim, dict) and isinstance(prim.get("implied_value"), (int, float)):
        return {"kind": "multiple",
                "core": f"{prim.get('implied_label') or prim.get('lens_label') or '估值'} "
                        f"{_fmt_implied(prim['implied_value'], prim.get('unit') or '')}",
                "detail": "DCF 无法在可行区间反解出增速 → 价格超出 DCF 可解释范围"}
    return None


_REGIME_ZH = {"optimistic": "偏乐观", "mixed": "分歧", "cautious": "谨慎",
              "pessimistic": "偏悲观", "neutral": "中性", "euphoric": "亢奋"}


def _build_activity(dd: dict) -> list[dict]:
    """Reconstruct the decode's activity log from the persisted decode_detail — the
    real decision / computation / evidence / relation steps the decode performed, in
    order. Always available (no live job_id needed), so the AGENT panel shows the
    full activity for any reloaded card. Honest: every line is a persisted result,
    never fabricated; a deterministic decode is NOT labelled as an autonomous agent."""
    out: list[dict] = []

    def add(kind: str, text: str) -> None:
        if text:
            out.append({"kind": kind, "text": text})

    anchor = dd.get("anchor_price")
    fund = dd.get("fundamentals") or {}
    am = dd.get("anchor_mode") or {}
    agentic = bool(dd.get("agentic"))
    auton = " · agent 自主选择" if agentic else ""

    if am.get("components"):
        add("decision", "解码模式:锚定复合体(传统估值锚 + 叙事/期权溢价)" + auton)
    else:
        add("decision", "解码模式:传统多 lens 反解" + auton)
    if dd.get("reason"):
        add("decision", "判定依据:" + str(dd["reason"]))

    if isinstance(fund.get("revenue_ttm"), (int, float)):
        bits = [f"营收 TTM {_money_big(fund['revenue_ttm'])}"]
        if isinstance(fund.get("fcf_ttm"), (int, float)):
            bits.append(f"FCF {_money_big(fund['fcf_ttm'])}")
        sh = fund.get("shares_outstanding")
        if isinstance(sh, (int, float)) and sh > 0:
            bits.append(f"股数 {sh / 1e9:.1f}B 股" if sh >= 1e9 else f"股数 {sh / 1e6:.0f}M 股")
        add("computation", "读取基本面:" + " · ".join(bits))

    if am.get("components"):
        base = am.get("base_business_value")
        _dcf = next((r for r in (dd.get("cross_lenses") or [])
                     if isinstance(r, dict) and r.get("lens") == "dcf"), {})
        _rf = _dcf.get("risk_free_used")
        _lo = _dcf.get("baseline_dcf_low")
        _ind, _mom = _dcf.get("scenario_industry"), _dcf.get("scenario_momentum")
        _hist, _impl = _dcf.get("hist_cagr"), _dcf.get("implied_cagr")
        _sector, _rev5, _share = _dcf.get("sector"), _dcf.get("implied_rev_5y"), _dcf.get("implied_market_share")
        if isinstance(_rf, (int, float)):
            add("computation", f"取实时无风险利率(10Y 美债)≈ {_rf * 100:.1f}% → CAPM 折现率")
        if isinstance(_lo, (int, float)):
            seg = f"三情景 DCF(只用公司自己 + 行业基准):保守 {_money_big(_lo)}"
            if isinstance(_ind, (int, float)): seg += f" · 行业 {_money_big(_ind)}"
            if isinstance(_mom, (int, float)): seg += f" · 动能 {_money_big(_mom)}"
            add("computation", seg)
        elif isinstance(base, (int, float)) and anchor:
            add("computation", f"前向 DCF → 基础业务价值 {_money_big(base)}"
                f"(占现价 {max(0, round(base / anchor * 100))}%)")
        if isinstance(_impl, (int, float)) and isinstance(_rev5, (int, float)):
            ltxt = f"落地:反解隐含 {_impl * 100:.0f}% → 5 年营收 {_money_big(_rev5)}"
            if isinstance(_share, (int, float)) and _sector:
                ltxt += f" → 占 {_sector} 行业 TAM {_share * 100:.0f}%"
            add("computation", ltxt)
        if isinstance(_impl, (int, float)):
            bits = [f"市场隐含 {_impl * 100:.0f}%"]
            if isinstance(_hist, (int, float)): bits.append(f"公司历史 {_hist * 100:.0f}%")
            _ic = _dcf.get("industry_cagr")
            if isinstance(_ic, (int, float)): bits.append(f"行业长期 {_ic * 100:.0f}%")
            add("computation", "增速对照:" + " vs ".join(bits))
        for comp in am["components"]:
            amt = comp.get("implied_amount")
            if isinstance(amt, (int, float)):
                add("decision", f"叙事/期权成分:{comp.get('lens_label') or comp.get('lens') or '成分'} → "
                    f"{_money_big(amt)}" + (f"(占现价 {round(amt / anchor * 100)}%)" if anchor else ""))
        recon = am.get("reconciliation") or {}
        if isinstance(recon.get("anchor"), (int, float)) and isinstance(recon.get("sum"), (int, float)):
            add("computation", f"对账:基础 + 成分 = {_money_big(recon['sum'])} ≈ 现价 "
                f"{_money_big(recon['anchor'])}" + ("(通过)" if recon.get("reconciled") else "(残差超容差)"))
        for te in (am.get("theme_exposures") or []):
            th = te.get("theme") if isinstance(te, dict) else getattr(te, "theme", None)
            pctv = te.get("exposure_pct") if isinstance(te, dict) else getattr(te, "exposure_pct", None)
            if th:
                add("relation", f"主题暴露:{th}" + (f" 占现价 {round(pctv)}%" if isinstance(pctv, (int, float)) else ""))
    else:
        prim = dd.get("primary_lens")
        if isinstance(prim, dict):
            add("decision", f"主 lens:{prim.get('lens_label') or prim.get('lens')} → "
                f"{prim.get('implied_label') or '隐含值'} = "
                f"{_fmt_implied(prim.get('implied_value'), prim.get('unit') or '')}")
        for r in (dd.get("cross_lenses") or []):
            if isinstance(r, dict) and (not isinstance(prim, dict) or r.get("lens") != prim.get("lens")):
                add("computation", f"交叉验证 {r.get('lens_label') or r.get('lens')}:"
                    f"{_fmt_implied(r.get('implied_value'), r.get('unit') or '')}")

    evd = dd.get("evidence") or {}
    fc, ac = evd.get("found_count"), evd.get("assumption_count")
    if isinstance(fc, int):
        t = (f"证据检索:{ac} 个假设" if isinstance(ac, int) else "证据检索")
        t += f" · {fc} 条独立来源" + ("(诚实留空)" if fc == 0 else "")
        add("evidence", t)

    mn = (dd.get("market_narrative") or {}).get("summary") or {}
    cov = mn.get("coverage")
    if cov and cov not in ("unavailable", "unparseable"):
        reg = _REGIME_ZH.get(mn.get("regime"), mn.get("regime") or "—")
        add("relation", f"市场叙事:{reg} · 多 {mn.get('bull_count', '?')} / 空 {mn.get('bear_count', '?')}")
        ndiv = sum(1 for b in (mn.get("bindings") or []) if isinstance(b, dict) and b.get("diverges"))
        if ndiv:
            add("relation", f"⚠ 叙事-证据分歧 {ndiv} 处(详见深度分析)")
    elif cov:
        add("relation", "市场叙事:材料不足 / 未联网 — 诚实留空")

    add("decision", "组装 BetCard 完成")
    return out


def build_card_display(card: BetCard) -> dict | None:
    """Project decode_detail → the compact `_display` the front-end's renderSingleCard
    reads (baseline_dcf / anchor / bets / risks / chain), so a real OR reloaded single
    card renders rich instead of the thin 'no data' branch (previously only the
    hardcoded fixtures had _display). None for portfolio / detail-less cards — the
    portfolio renderer uses holdings/theme_exposures, not _display."""
    dd = getattr(card, "decode_detail", None)
    if not dd or card.card_kind != SINGLE:
        return None
    anchor = dd.get("anchor_price")
    np_ = dd.get("narrative_premium")
    am = dd.get("anchor_mode") or {}
    bets: list[dict] = []
    if am.get("components"):
        base = am.get("base_business_value")
        if anchor and isinstance(base, (int, float)):
            bets.append({
                "metric": "基础业务价值(DCF)解释的占比",
                "impl": f"{max(0, round(base / anchor * 100))}%",
                "base": (f"叙事溢价 {round(np_ * 100)}%"
                         if isinstance(np_, (int, float)) else ""),
                "iv": ""})
        for comp in am["components"]:
            amt = comp.get("implied_amount")
            bets.append({
                "metric": comp.get("claim") or comp.get("lens_label")
                or comp.get("lens") or "成分",
                "impl": f"${amt:,.0f}" if isinstance(amt, (int, float)) else "—",
                "base": "", "iv": comp.get("implied_assumption") or "",
                "lens": comp.get("lens"),
                "impl_num": amt if isinstance(amt, (int, float)) else None,
                "band": None, "nosol": amt is None})
    else:
        lenses = ([dd["primary_lens"]] if isinstance(dd.get("primary_lens"), dict) else [])
        lenses += [c for c in (dd.get("cross_lenses") or []) if isinstance(c, dict)]
        for r in lenses:
            iv = r.get("implied_value")
            band = r.get("band") or {}
            p25, p75 = band.get("p25"), band.get("p75")
            band_str = ""
            if isinstance(p25, (int, float)) and isinstance(p75, (int, float)):
                band_str = (f"蒙特卡洛 {p25 * 100:.0f}%–{p75 * 100:.0f}%"
                            if 0 < abs(p25) < 1 else f"区间 {p25:.1f}–{p75:.1f}")
            p50 = band.get("p50")
            bets.append({
                "metric": r.get("implied_label") or r.get("lens_label")
                or r.get("lens") or "lens",
                "impl": _fmt_implied(iv, r.get("unit") or ""),
                "base": "", "iv": band_str, "nosol": iv is None,
                "lens": r.get("lens"), "unit": r.get("unit") or "",
                "impl_num": iv if isinstance(iv, (int, float)) else None,
                "band": ({"p25": p25, "p50": p50, "p75": p75}
                         if isinstance(p25, (int, float))
                         and isinstance(p75, (int, float)) else None)})
    # baseline_dcf: anchor base business value → a cross/primary lens's DCF baseline.
    # Tier 1 also surfaces the two-anchor RANGE + the company's own history vs the
    # market-implied growth (all from the DCF lens envelope) for the KPI + tree.
    base_dcf = am.get("base_business_value")
    base_low = base_high = hist_cagr = implied_cagr = None
    sc_industry = sc_momentum = sector = sec_tam = implied_rev_5y = implied_share = None
    xray = None
    for r in ([dd.get("primary_lens")] + list(dd.get("cross_lenses") or [])):
        if isinstance(r, dict) and r.get("lens") == "dcf":
            if base_dcf is None and isinstance(r.get("baseline_dcf_price"), (int, float)):
                base_dcf = r["baseline_dcf_price"]
            base_low = r.get("baseline_dcf_low")
            base_high = r.get("baseline_dcf_high")
            hist_cagr = r.get("hist_cagr")
            implied_cagr = r.get("implied_cagr")
            sc_industry = r.get("scenario_industry")
            sc_momentum = r.get("scenario_momentum")
            sector = r.get("sector")
            sec_tam = r.get("sector_tam")
            implied_rev_5y = r.get("implied_rev_5y")
            implied_share = r.get("implied_market_share")
            xray = r.get("xray")          # X-RAY intelligence layer (intelligence.py)
            break
    # risks: honest, derived from the market-narrative contested points (if any).
    mn = (dd.get("market_narrative") or {}).get("summary") or {}
    risks = " · ".join(mn.get("contested") or [])
    # chain: a modest decision chain from the plan/mode + the headline implied number.
    chain: list[dict] = []
    reason = ((dd.get("lens_plan") or {}).get("reason") or am.get("reason")
              or dd.get("reason"))
    if reason:
        chain.append({"t": "fact", "m": "·", "c": reason})
    if bets:
        chain.append({"t": "implied", "m": "⇒",
                      "c": f"{bets[0]['metric']} = {bets[0]['impl']}"})
    if dd.get("agentic"):
        chain.append({"t": "support", "m": "✦",
                      "c": "agent 自主选择此解码方案(非固定决策树)"})
    # decomp: the price decomposition for anchor/narrative cards (base business
    # value + narrative/option/TAM parts = anchor price) — drives the waterfall.
    decomp = None
    if am.get("components"):
        recon = am.get("reconciliation") or {}
        base_bv = am.get("base_business_value")
        parts = [
            {"label": c.get("lens_label") or c.get("lens") or "成分",
             "amt": c.get("implied_amount"), "theme": c.get("theme") or ""}
            for c in am["components"]
            if isinstance(c.get("implied_amount"), (int, float))
        ]
        recon_anchor = recon.get("anchor")
        decomp = {
            "base": base_bv if isinstance(base_bv, (int, float)) else None,
            "anchor": recon_anchor if isinstance(recon_anchor, (int, float)) else anchor,
            "residual": recon.get("residual"),
            "parts": parts}
    return {"baseline_dcf": base_dcf, "baseline_dcf_low": base_low,
            "baseline_dcf_high": base_high, "hist_cagr": hist_cagr,
            "implied_cagr": implied_cagr,
            "scenario_industry": sc_industry, "scenario_momentum": sc_momentum,
            "sector": sector, "sector_tam": sec_tam,
            "implied_rev_5y": implied_rev_5y, "implied_market_share": implied_share,
            "xray": xray,
            "anchor": anchor, "bets": bets,
            "risks": risks, "chain": chain, "decomp": decomp,
            "derivations": _build_derivations(dd),
            "bet_statement": _bet_statement(dd),
            "activity": _build_activity(dd)}


def card_from_json(data: dict) -> BetCard:
    """Inverse of card_to_json. Tolerates missing optional keys."""
    return BetCard(
        card_id=data.get("card_id"),
        subject=data["subject"],
        source_type=data["source_type"],
        card_kind=data.get("card_kind", SINGLE),
        source_ref=data.get("source_ref"),
        bet=data.get("bet"),
        run_id=data.get("run_id"),
        series_key=data.get("series_key"),
        trade_date=data.get("trade_date"),
        created_at=data.get("created_at"),
        holdings=[
            Holding(
                ticker=h["ticker"],
                weight_pct=h.get("weight_pct"),
                run_id=h.get("run_id"),
            )
            for h in (data.get("holdings") or [])
        ],
        theme_exposures=[
            ThemeExposure(
                theme=t["theme"],
                exposure_pct=t.get("exposure_pct"),
                contributing_tickers=list(t.get("contributing_tickers") or []),
                is_concentration_risk=bool(t.get("is_concentration_risk")),
            )
            for t in (data.get("theme_exposures") or [])
        ],
        derived_from=data.get("derived_from"),
        derivation_kind=data.get("derivation_kind"),
        derivation=data.get("derivation"),
    )


def card_from_row(conn: sqlite3.Connection, row: sqlite3.Row) -> BetCard:
    """Reconstruct a full BetCard from a `bet_cards` row, pulling its child
    holdings + theme_exposures from their tables."""
    card_id = row["card_id"]
    holding_rows = conn.execute(
        "SELECT * FROM portfolio_holdings WHERE card_id = ? ORDER BY id",
        (card_id,),
    ).fetchall()
    theme_rows = conn.execute(
        "SELECT * FROM theme_exposures WHERE card_id = ? ORDER BY id",
        (card_id,),
    ).fetchall()
    cols = set(row.keys())  # rows from a pre-v3 DB may lack the lineage columns
    card = BetCard(
        card_id=card_id,
        subject=row["subject"],
        source_type=row["source_type"],
        card_kind=row["card_kind"],
        source_ref=row["source_ref"],
        bet=row["bet"],
        run_id=row["run_id"],
        series_key=row["series_key"],
        trade_date=row["trade_date"],
        created_at=row["created_at"],
        holdings=[
            Holding(
                ticker=h["ticker"],
                weight_pct=h["weight_pct"],
                run_id=h["run_id"],
            )
            for h in holding_rows
        ],
        theme_exposures=[
            ThemeExposure(
                theme=t["theme"],
                exposure_pct=t["exposure_pct"],
                contributing_tickers=_loads_ticker_list(t["contributing_tickers"]),
                is_concentration_risk=bool(t["is_concentration_risk"]),
            )
            for t in theme_rows
        ],
        derived_from=row["derived_from"] if "derived_from" in cols else None,
        derivation_kind=row["derivation_kind"] if "derivation_kind" in cols else None,
        derivation=(_loads_detail(row["derivation_json"])
                    if "derivation_json" in cols else None),
    )
    # Restore the rich decode_detail (v3) so a reloaded card is fully
    # interrogable/revisable (TD1 fix). A pre-v3 row, or one saved without detail,
    # simply leaves the card without it (degrades to the old summary-only behavior).
    if "decode_detail_json" in cols:
        dd = _loads_detail(row["decode_detail_json"])
        if dd is not None:
            card.decode_detail = dd
    return card


def _loads_ticker_list(raw: Any) -> list[str]:
    """Parse a contributing_tickers JSON blob defensively. Dirty / non-list /
    unparseable values degrade to [] instead of raising (bug: a single corrupt
    row would otherwise 500 the whole list_cards / get_card response)."""
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    return [str(x) for x in data]


def _loads_detail(raw: Any) -> dict | None:
    """Parse a decode_detail / derivation JSON blob defensively (None on empty or
    unparseable), so one corrupt row never 500s get_card / list_cards."""
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    return data if isinstance(data, dict) else None


# ---------------------------------------------------------------------------
# DAO: Bet Cards
# ---------------------------------------------------------------------------

def _find_dedup_card_id(conn: sqlite3.Connection, card: BetCard) -> str | None:
    """Return the existing Market card_id for this (series_key, trade_date), or
    None. Only Market source_types are deduped (PRD 行为⑦)."""
    if card.source_type not in _DAILY_DEDUP_SOURCES or card.trade_date is None:
        return None
    if card.derived_from is not None:
        return None  # a what-if / revision is its own card, never deduped
    existing = conn.execute(
        "SELECT card_id FROM bet_cards WHERE series_key = ? AND trade_date = ? "
        "AND source_type = ? AND derived_from IS NULL",
        (card.series_key, card.trade_date, card.source_type),
    ).fetchone()
    return existing["card_id"] if existing is not None else None


def save_card(conn: sqlite3.Connection, card: BetCard) -> str:
    """Persist a BetCard (envelope + child rows) in one transaction.

    Returns the card_id actually stored. Dedup rule (M1 decision 8 / PRD 行为⑦):
    Market cards are one-per-trading-day per series. If a Market card already
    exists for the same (series_key, trade_date), no new card is created and the
    EXISTING card_id is returned. Other source_types (analyst_pt / opinion /
    portfolio) are NEVER deduped — two same-subject, same-day cards both persist.

    Concurrency-safe: the pre-check is best-effort, and the actual INSERT is
    guarded against the unique-index race (two Market cards racing on the same
    day) by catching IntegrityError and falling back to the already-stored id.
    Without this fallback a concurrent same-day Market double-write 500'd.
    """
    # Best-effort dedup pre-check for Market cards: hit -> return existing id.
    pre = _find_dedup_card_id(conn, card)
    if pre is not None:
        return pre

    try:
        with conn:  # implicit BEGIN/COMMIT, rolls back on exception
            conn.execute(
                """
                INSERT INTO bet_cards (
                    card_id, subject, source_type, card_kind, source_ref,
                    series_key, bet, trade_date, created_at, run_id,
                    decode_detail_json, derived_from, derivation_kind, derivation_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    card.card_id,
                    card.subject,
                    card.source_type,
                    card.card_kind,
                    card.source_ref,
                    card.series_key,
                    card.bet,
                    card.trade_date,
                    card.created_at,
                    card.run_id,
                    _dump_detail(getattr(card, "decode_detail", None)),
                    card.derived_from,
                    card.derivation_kind,
                    _dump_detail(card.derivation),
                ),
            )

            if card.holdings:
                conn.executemany(
                    """
                    INSERT INTO portfolio_holdings (card_id, ticker, weight_pct, run_id)
                    VALUES (?, ?, ?, ?)
                    """,
                    [
                        (card.card_id, h.ticker, h.weight_pct, h.run_id)
                        for h in card.holdings
                    ],
                )

            if card.theme_exposures:
                conn.executemany(
                    """
                    INSERT INTO theme_exposures (
                        card_id, theme, exposure_pct, contributing_tickers,
                        is_concentration_risk
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            card.card_id,
                            t.theme,
                            t.exposure_pct,
                            json.dumps(list(t.contributing_tickers or []),
                                       ensure_ascii=False),
                            1 if t.is_concentration_risk else 0,
                        )
                        for t in card.theme_exposures
                    ],
                )
    except sqlite3.IntegrityError:
        # Lost a race on the Market daily unique index (another writer inserted
        # the same series+day between our pre-check and INSERT). The `with conn`
        # block rolled back our partial write; return the winner's id so the
        # caller still gets a valid card instead of a 500.
        winner = _find_dedup_card_id(conn, card)
        if winner is not None:
            return winner
        raise  # a genuine, non-dedup integrity violation — surface it

    return card.card_id


def get_card(conn: sqlite3.Connection, card_id: str) -> BetCard | None:
    """Read one card by id (with children). Returns None if not found."""
    row = conn.execute(
        "SELECT * FROM bet_cards WHERE card_id = ?", (card_id,)
    ).fetchone()
    if row is None:
        return None
    return card_from_row(conn, row)


def list_cards(
    conn: sqlite3.Connection,
    series_key: str | None = None,
    subject: str | None = None,
    source_type: str | None = None,
) -> list[BetCard]:
    """List cards, newest first.

    Filter by series_key directly, or by (subject, source_type) which are
    combined into a series_key. With no filter, returns all cards.
    """
    if series_key is None and subject is not None and source_type is not None:
        series_key = make_series_key(subject, source_type)

    if series_key is not None:
        rows = conn.execute(
            "SELECT * FROM bet_cards WHERE series_key = ? "
            "ORDER BY created_at DESC, card_id DESC",
            (series_key,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM bet_cards ORDER BY created_at DESC, card_id DESC"
        ).fetchall()
    return [card_from_row(conn, r) for r in rows]


def delete_card(conn: sqlite3.Connection, card_id: str) -> bool:
    """Delete a card and its child rows (FK ON DELETE CASCADE). Returns True if a
    card was removed, False if no such card existed."""
    with conn:
        cur = conn.execute("DELETE FROM bet_cards WHERE card_id = ?", (card_id,))
    return cur.rowcount > 0
