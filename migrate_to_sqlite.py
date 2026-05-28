"""One-shot migration: outputs/*.json + cache/{decoder,evidence,synthesizer}/*.json -> SQLite."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from db import cache_put, init_db, save_pipeline_run


PROJECT_ROOT = Path(__file__).parent
OUTPUTS_DIR = PROJECT_ROOT / "outputs"
CACHE_BASE = PROJECT_ROOT / "cache"
CACHE_CATEGORIES = ("decoder", "evidence", "synthesizer")


def _infer_ticker(cache_key: str) -> str | None:
    """All three cache layers prefix their keys with `{TICKER}_...`."""
    if "_" not in cache_key:
        return None
    head = cache_key.split("_", 1)[0]
    # Heuristic: tickers are 1-6 uppercase letters/digits (NVDA, BRK-B, etc.).
    if 1 <= len(head) <= 6 and head.replace("-", "").isalnum():
        return head.upper()
    return None


def migrate_outputs(conn, dry_run: bool) -> tuple[int, int, int]:
    """Returns (imported, skipped_existing, errors)."""
    imported = skipped = errors = 0
    if not OUTPUTS_DIR.exists():
        return 0, 0, 0
    files = sorted(OUTPUTS_DIR.glob("*.json"))
    for p in files:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"  [error] {p.name}: {exc}", file=sys.stderr)
            errors += 1
            continue

        ticker = data.get("ticker")
        generated_at = data.get("generated_at")
        if not ticker or not generated_at:
            print(f"  [skip] {p.name}: missing ticker/generated_at", file=sys.stderr)
            errors += 1
            continue

        # Idempotency: skip if (ticker, generated_at) already in runs
        existing = conn.execute(
            "SELECT id FROM runs WHERE ticker = ? AND generated_at = ?",
            (ticker, generated_at),
        ).fetchone()
        if existing is not None:
            skipped += 1
            continue

        if dry_run:
            imported += 1
            continue

        try:
            save_pipeline_run(conn, data)
            imported += 1
        except Exception as exc:  # pragma: no cover - defensive
            print(f"  [error] {p.name}: {exc}", file=sys.stderr)
            errors += 1

    return imported, skipped, errors


def migrate_cache_category(conn, category: str, dry_run: bool) -> tuple[int, int, int]:
    """Returns (imported, skipped_existing, errors). cache_put uses upsert
    so 'skipped' here means 'would overwrite'; we count an existing row as a
    skip for the dry-run summary, otherwise let the upsert refresh."""
    imported = skipped = errors = 0
    cat_dir = CACHE_BASE / category
    if not cat_dir.exists():
        return 0, 0, 0
    for p in sorted(cat_dir.glob("*.json")):
        cache_key = p.stem
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"  [error] cache/{category}/{p.name}: {exc}", file=sys.stderr)
            errors += 1
            continue

        ticker = _infer_ticker(cache_key)

        existing = conn.execute(
            "SELECT cache_key FROM llm_cache WHERE cache_key = ?",
            (cache_key,),
        ).fetchone()

        if dry_run:
            if existing is None:
                imported += 1
            else:
                skipped += 1
            continue

        if existing is not None:
            # Already present — count as skipped (upsert would just refresh
            # to the same content, no real work).
            skipped += 1
            continue

        try:
            cache_put(conn, category, cache_key, payload, ticker=ticker)
            imported += 1
        except Exception as exc:  # pragma: no cover - defensive
            print(f"  [error] cache/{category}/{p.name}: {exc}", file=sys.stderr)
            errors += 1
    return imported, skipped, errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate PriceLens JSON files to SQLite.")
    parser.add_argument("--db", default="pricelens.db", help="SQLite DB path (default: pricelens.db)")
    parser.add_argument("--dry-run", action="store_true", help="Count only; do not write to DB.")
    args = parser.parse_args()

    print(f"Migration target: {args.db} (dry_run={args.dry_run})")
    conn = init_db(args.db)

    print("\nMigrating outputs/*.json ...")
    out_imp, out_skip, out_err = migrate_outputs(conn, args.dry_run)

    cache_stats: dict[str, tuple[int, int, int]] = {}
    for cat in CACHE_CATEGORIES:
        print(f"Migrating cache/{cat}/*.json ...")
        cache_stats[cat] = migrate_cache_category(conn, cat, args.dry_run)

    total_cache_entries = conn.execute(
        "SELECT COUNT(*) AS n FROM llm_cache"
    ).fetchone()["n"]

    print("\nMigration complete.")
    print(f"  runs:        {out_imp} imported, {out_skip} skipped (already present), {out_err} errors")
    for cat, (imp, skp, err) in cache_stats.items():
        label = f"cache/{cat}:"
        print(f"  {label:<22} {imp} imported, {skp} skipped (already present), {err} errors")
    print(f"  Total LLM cache entries: {total_cache_entries}")

    if args.dry_run:
        print("\n(dry-run — no rows were written)")

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
