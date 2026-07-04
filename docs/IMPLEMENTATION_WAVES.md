# PlayInsight Implementation Waves

> Working note for the v2 build in `C:\Users\Henry Ma\Desktop\PlayInsight`.
> Authoritative product/spec inputs remain `PRD.md` and `docs/SPEC_A` through `docs/SPEC_F`.

## Wave 0 - Foundation

Goal: make the v2 data spine executable without changing the user-facing app.

- Data schema: `panel_observations`, `position_ledger`, `panel_backfill_runs`.
- Deterministic kernels: options distribution/RND and Trade Plan/reconciliation core.
- Branding/context: mark PlayInsight as the active v2 workspace while preserving Bet Decoder history.
- Verification: each slice ships a focused `tests/verify_*.py` suite and does not require network/API keys.

## Wave 1 - Integration

Goal: wire the new kernels into existing app boundaries.

- API: add chart, positions, panel backfill, and card plan endpoints.
- Decode detail v4: project existing v3 cashflow data into `panel[]` and `reconciliation`.
- Backfill: write price-driven implied-growth and distribution observations into `panel_observations`.
- Keep existing `/api/decode` lifecycle: JSON `{job_id, card}` plus `/api/stream/activity/{job_id}`.

## Wave 2 - Product Surface

Goal: make the v2 product legible in the workbench.

- Enhanced chart consumes `GET /api/chart/{subject}` only; no fake historical layers.
- Portfolio page shows Aha B composite: shared-root risk, contradictions, shared KILL lines.
- Trade Plan section shows position freshness, KILL, self-falsification, and manual confirmation.
- Empty states: demo cache, no ledger, offline/no key, channel honest-empty.

## Wave 3 - Monitoring

Goal: ship honest events-first monitoring.

- Monitor only confirmed open positions and explicit watchlist/candidate items.
- Budget gate: default daily scan budget below free qveris allowance.
- Feed severity includes `why_severity`; skipped scans are not reported as calm.
- KILL checks run through the same event pipeline, not a separate hidden loop.

