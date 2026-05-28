"""SQLite storage layer for PriceLens — replaces outputs/*.json + cache/*/*.json."""
from __future__ import annotations

import json
import sqlite3
from typing import Any


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
"""


# ---------------------------------------------------------------------------
# Connection + bootstrap
# ---------------------------------------------------------------------------

def init_db(db_path: str = "pricelens.db") -> sqlite3.Connection:
    """Open (creating if missing) the SQLite DB and ensure schema is present."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_SQL)
    # Seed schema_meta once.
    existing = conn.execute(
        "SELECT value FROM schema_meta WHERE key = ?", ("version",)
    ).fetchone()
    if existing is None:
        conn.execute(
            "INSERT INTO schema_meta(key, value) VALUES (?, ?)", ("version", "1")
        )
        conn.execute(
            "INSERT INTO schema_meta(key, value) VALUES (?, datetime('now'))",
            ("created_at",),
        )
        conn.commit()
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


def _safe_get(d: dict | None, key: str, default=None):
    return (d or {}).get(key, default)


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

    with conn:  # implicit BEGIN/COMMIT, rolls back on exception
        cur = conn.execute(
            """
            INSERT INTO runs (
                ticker, company_name, generated_at, mode,
                current_price, baseline_dcf, total_cost_usd, decoder_cached
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                output.get("ticker"),
                output.get("company_name"),
                output.get("generated_at"),
                mode,
                float(rdcf.get("current_price") or 0.0),
                float(rdcf.get("baseline_dcf_price") or 0.0) if rdcf.get("baseline_dcf_price") is not None else None,
                total_cost,
                0,
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
