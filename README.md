# Bet Decoder

**An X-ray for investment bets.** Paste any bet — a market price, an analyst target, a tweet, or your whole portfolio — and Bet Decoder reverse-decodes *what that bet implicitly believes*, lets you stack multiple bets side by side, and has an AI synthesize the cross-bet insights (e.g. "your portfolio rides the same assumption as Goldman's price target").

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

The SQLite database (`pricelens.db`) is created automatically on first run.

- **Without an API key** the workbench runs on built-in fixtures (zero cost) — enough to explore the UI.
- **With live decoding**, create a `.env` with `MIROMIND_API_KEY=...` (OpenAI-compatible). Decoding spends ~$0.10–3 per stock.

To pre-populate caches before a demo (so nothing runs live on stage):

```bash
python prerun_demo.py            # dry-run: prints the plan + cost estimate, no network
python prerun_demo.py --execute  # real pre-run (spends budget), then the demo is $0
```

## The workbench

A three-zone app:

- **Main canvas** — multiple Bet Cards side by side (single-stock = compact card, portfolio = dashboard).
- **Activity feed** (right) — the agent's reasoning streamed live over SSE, and replayable per card.
- **Synthesis panel** (bottom) — select ≥2 cards and the AI surfaces their relationships (consensus / divergence / contradiction / shared-root / drift) with a headline insight.

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

- [`PRD.md`](PRD.md) — frozen product spec (5 modules, data model, public interfaces)
- [`BET_DECODER_VISION.md`](BET_DECODER_VISION.md) — product vision + the 5-act demo
- [`API_CONTRACT.md`](API_CONTRACT.md) — REST + SSE endpoints
- [`PROJECT_CONTEXT.md`](PROJECT_CONTEXT.md) — architecture decisions
- [`pricelens_design_system.md`](pricelens_design_system.md) — the visual contract
- [`docs/glossary.md`](docs/glossary.md) — terminology

## Run with Docker

```bash
docker build -t bet-decoder .
docker run -p 8000:8000 --env-file .env bet-decoder   # open http://127.0.0.1:8000/
```

## Contributing

Issues and PRs welcome — the project is designed for contribution. Different markets need different data sources (US/yfinance today; CN/Wind, EU/Refinitiv could follow), and the prompts + schemas (evidence brief, decoder voice, synthesis rules) are meant to iterate against community feedback.

Each module ships a deterministic, zero-API verification script (`verify_m1.py` … `verify_m8_integration.py`); run them after changes. CI (`.github/workflows/verify.yml`) runs every suite offline on each push and PR, so a change that breaks one is caught automatically.

## License

[MIT](LICENSE).
