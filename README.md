# PlayInsight

> PlayInsight is the active v2 successor workspace, copied from the historical `bet-decoder` codebase. Older names in this repository (`Bet Decoder`, `PlainSight`, `PriceLens`) are retained only where they explain lineage or archived decisions.

**An X-ray for investment bets.** Paste a bet — a stock's market price, or your whole portfolio — and Bet Decoder reverse-decodes *what that bet implicitly believes*, lets you stack multiple bets side by side, and has an AI synthesize the cross-bet insights (e.g. "your portfolio rides the same assumption across every holding"). *(Decoding analyst price targets and free-text opinions is on the roadmap.)*

Self-hosted, single-file SQLite, open-source. `git clone && uvicorn api:app` and you have your own instance.

---

## Why this exists

Existing investment-research AI tools all do `company → report`. Bet Decoder does the inverse: `price → implied assumptions → evidence per assumption`. The object of transparency is **the market's collective reasoning**, not the AI's. Stock prices are uniquely suited to this because the valuation math is well known and the underlying data is public.

It is honest about its limits: it makes implicit assumptions **explicit and testable under a transparent lens** — it does not claim to recover a single "true" bet.

## Quickstart

```bash
git clone https://github.com/hnaymyh123-henry/bet-decoder.git
cd bet-decoder
pip install -r requirements.txt          # Python 3.11
uvicorn api:app --reload --port 8000     # open http://127.0.0.1:8000/
```

The repo ships a **pre-run `pricelens.db`** — COST, NVDA, TSLA and a 5-holding portfolio are already decoded and cached. A fresh clone therefore shows the **real X-ray output offline, with no API key and $0**: run the command above and the four demo bets load onto the canvas automatically.

- **Demo / explore (no key needed)** — the shipped cache is enough to see the full decode on the four demo bets: the scenario ladder, the step-by-step reasoning chain, and the cross-card portfolio synthesis.
- **Decode your own bets (live)** — create a `.env` with `MIROMIND_API_KEY=...` (OpenAI-compatible). Each new decode spends ~$0.10–3 per stock; re-decoding an already-cached bet is free.

To pre-populate caches before a demo (so nothing runs live on stage):

```bash
python prerun_demo.py            # dry-run: prints the plan + cost estimate, no network
python prerun_demo.py --execute  # real pre-run (spends budget), then the demo is $0
```

## The workbench

A three-zone app:

- **Main canvas** — multiple Bet Cards side by side (single-stock = compact card, portfolio = dashboard).
- **Activity feed** (right) — the agent's reasoning streamed live over SSE, and replayable per card.
- **Synthesis** — decode a portfolio and the AI auto-surfaces its holdings' relationships (consensus / divergence / contradiction / shared-root / drift) with a headline insight.

## How it works

| Layer | Module | Role |
|---|---|---|
| Data model | `db.py` | Bet Card schema + SQLite persistence (single + portfolio cards) |
| Decoder | `decoder.py` | Frame-adaptive decode: an agent picks the right valuation lens per company (DCF / P-E / P-S / EV-EBITDA / P-FCF / P-B / PEG), reverse-solves the implied drivers, and cross-validates. For narrative/AI-complex names it switches to **anchor mode** (TAM / optionality / analogy / narrative) and reconciles components back to the price. |
| Evidence | `evidence.py` | Hunts evidence for each implied assumption; honest-empty when none found, never fabricated. |
| Synthesis | `synthesizer.py` | Cross-card relation engine + narrative (the "shared-root" insight is the payoff). |
| Activity | `activity.py` | Event protocol + SSE pipeline + timed replay (the process-transparency layer). |
| Frontend | `app.html` | The workbench, served by `api.py`. |
| Valuation core | `reverse_dcf.py` | Reverse DCF with Monte-Carlo interval estimation (one lens among many). |

Two orthogonal primitives: a **Bet Card** answers *what* a bet believes; an **Activity Event** answers *how* the agent figured it out.

## Documentation

- [`PRD.md`](PRD.md) — authoritative product spec for PlayInsight v2.0
- [`docs/SPEC_A_data_structures.md`](docs/SPEC_A_data_structures.md) through [`docs/SPEC_F_api.md`](docs/SPEC_F_api.md) — authoritative v2 engineering specs
- [`API_CONTRACT.md`](API_CONTRACT.md) — REST + SSE endpoints
- [`PROJECT_CONTEXT.md`](PROJECT_CONTEXT.md) — local engineering context and lineage notes
- [`mockup_v2_young.html`](mockup_v2_young.html) — young trading-app visual reference (v2.0)
- [`docs/glossary.md`](docs/glossary.md) — terminology
- [`docs/feature-log.md`](docs/feature-log.md) — delivered features + tech debt
- [`docs/archive/`](docs/archive/) — superseded docs (DIRECTION / TRADING_AGENT_PRD / VISION / design_system), kept for traceability

## Run with Docker

```bash
docker build -t bet-decoder .
docker run -p 8000:8000 --env-file .env bet-decoder   # open http://127.0.0.1:8000/
```

## Contributing

Issues and PRs welcome — the project is designed for contribution. Different markets need different data sources (US/yfinance today; CN/Wind, EU/Refinitiv could follow), and the prompts + schemas (evidence brief, decoder voice, synthesis rules) are meant to iterate against community feedback.

Each module ships a deterministic, zero-API verification script under `tests/` (`tests/verify_m1.py` … `tests/verify_m8_integration.py`); run them from the repo root after changes (e.g. `PYTHONPATH=. python tests/verify_m1.py`). CI (`.github/workflows/verify.yml`) runs every suite offline on each push and PR, so a change that breaks one is caught automatically.

## License

[MIT](LICENSE).
