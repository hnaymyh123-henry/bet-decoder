# CLAUDE.md · PriceLens Project

> Project-specific instructions for any Claude session working inside this folder.
> The user's global workspace CLAUDE.md lives at `C:\Users\Henry Ma\CLAUDE.md` and still applies — this file adds project-specific context on top.

---

## Project at a glance

**Project name:** PriceLens — Reverse-engineered investment thesis decoder
**Event:** UCWS Singapore Hackathon 2026 — Agent track, MiroMind partnership
**One-line pitch:** Given a stock price, PriceLens reverse-engineers what assumptions the market must be making to arrive at it, and surfaces the evidence behind each one.
**Timeline:** 2026-04-25 launch · online screening 2026-06-03 to 06-05 · Demo Day Singapore 2026-06-13
**Team size:** 1 (solo)
**Status:** PRD v0.5. **W1 + W2 closed** (Test A/B passed, $3.39 spent; pipeline 6-step end-to-end works with Python critic + chat synthesizer wired; FastAPI 4 endpoints; frontend mockup driven by real data with ticker switcher / boundary mode / slider real DCF / bilingual disclaimer). Total ~$3.78 spent of $100 budget. 12+ commits on master after git init. **W3 in progress**: 5d short-term attribution (Worker A/B parallel) + G3-C frozen-evidence tag + this docs sync. SSE streaming (G4-D) + OFFLINE_MODE (G5-B) deferred to W4 alongside the demo pre-run.

---

## Why this project exists

**MiroMind track theme:** "推理透明" (reasoning transparency). The judging criterion is whether the AI's reasoning process is auditable, traceable, and verifiable — not just whether the answer is correct.

**Differentiation from other entries:** Most teams will build "AI research agent + citation links + reasoning visualization." PriceLens is the inverse — instead of `analyze company → output report`, it does `take price as input → decompose into implicit assumptions → score evidence`. The thing being decoded is the market's collective reasoning, not the AI's own reasoning. This is angle is novel within the track.

**Why investment research specifically:** MiroMind's own track copy mentioned 投研 (investment research) as a sample domain. Stock prices are uniquely well-suited to reverse decomposition because the DCF math is well-known and the underlying data (financials, consensus, market data) is fully public.

---

## Documents in this folder

| File | Purpose |
|---|---|
| `CLAUDE.md` | This file — project context for Claude sessions |
| `pricelens_prd.md` | Product requirements doc v0.4 — scope, features, milestones, G1-G6 + B1-B4 decisions, §6.4 DCF boundary state, Appendix A evidence schema |
| `reverse_dcf.py` | Reverse DCF prototype with Monte Carlo interval estimation (G2). Run: `python reverse_dcf.py NVDA` |
| `requirements.txt` | Python deps: yfinance, numpy, scipy |
| `prompts/evidence_hunter.md` | Evidence Hunter prompt template (deepresearch mode; supports standard + boundary modes per B4) |
| `prompts/decoder_narrator.md` | Long-term Decoder narrator prompt template (chat mode; numbers → human-readable assumptions) |
| `api.py` | FastAPI server. Run: `uvicorn api:app --reload --port 8000`. Serves cached pipeline outputs from `outputs/` + the mockup at `/`. No LLM. |
| `outputs/{TICKER}_{timestamp}.json` | Pipeline output per ticker per run. Frontend reads via `/api/decode/{ticker}`. |
| `cache/decoder/`, `cache/evidence/`, `cache/synthesizer/` | Cached LLM outputs (G5 foundation). Cache key invalidates on input change; decoder bumped to v2 schema 2026-05-27. |
| `critic.py` | Python mechanical validation of evidence briefs per PRD §15 Appendix A.4. No LLM. Returns `{issues, verdict, counts}`. |
| `short_term.py` | 5d short-term attribution (W3). Decomposes price move into fundamental / flow / unexplained factors. yfinance only, no LLM. |
| `.claude/launch.json` | Preview server config. `preview_start pricelens-api` launches uvicorn on port 8765. |
| `pricelens_design_system.md` | Frontend design philosophy + visual language (THE source of truth for any UI work) |
| `pricelens_mockup.html` | Production-grade interactive mockup, opens in browser. Implements the full design system. |
| `hackathon_track.png` | Original MiroMind track brief image — sets the "推理透明" theme |

**Read order for a fresh Claude session:** start with this file → PRD → design system → open the HTML mockup in browser to see what's been built.

---

## Key product decisions (locked, do not re-litigate)

These were settled after extensive back-and-forth. Don't re-open these unless the user explicitly asks:

1. **Reverse-DCF as core mechanic** — not "AI generates research report." Inputs are 4Q trailing actuals, output is implied assumptions, solved one variable at a time with Brent's method while holding others at consensus.
2. **Investment research as the domain** — not medical, legal, or policy. Chosen because data is public, math is well-defined, and demo audience is at Singapore (finance-adjacent).
3. **Multi-timeframe attribution (1d/5d/30d/1y)** — long-term decoding is the hero feature; short-term attribution is the secondary feature on TSLA.
4. **Solo project** — scope must fit one person × 24 days.
5. **Frontend follows research-report aesthetic** — paper-on-paper, oxblood accent, Geist fonts. No SaaS / AI-product visual cliches. See `pricelens_design_system.md` for the full visual contract.

---

## Architecture (high-level)

```
Frontend (HTML/JS — extends pricelens_mockup.html)
    ↓
Orchestration: MiroFlow (planner → timeframe router → synthesizer)
    ↓
MiroMind API (single client, two calling modes):
    - Chat mode (tool_choice=none, mini 30B): Decoder narration, Critic, Synthesizer
    - Deep Research mode (auto, flagship 235B): Evidence Hunter (hero — one call per assumption)
    ↓
Computation (Python, no LLM):
    - Reverse DCF (scipy.optimize.brentq)
    - Factor attribution (Barra-lite)
    ↓
Data Tools:
    - yfinance, SEC EDGAR, FINRA, CBOE, FRED, NewsAPI
```

**Model API:** MiroMind API (OpenAI-compatible chat completions). Same client serves both modes via `tool_choice`. Models: `mirothinker-1-7-deepresearch-mini` (dev + chat) and `mirothinker-1-7-deepresearch` (evidence only).
**Framework:** MiroFlow (graph orchestration, model-agnostic).
**Budget:** $100 + 100 calls. Demo strategy: pre-run all evidence and cache; never live-run deepresearch during demo.

---

## Conventions for any work in this folder

### Code
- **Python 3.11** for backend (matches MiroFlow)
- Default to **PEP 8** + meaningful names
- No premature abstraction — solo project, 24 days, optimize for delivery not architecture purity

### Frontend
- **Strictly follow `pricelens_design_system.md`** — any deviation needs the user's explicit OK
- Hard rules from the design system:
  - No blue, purple, or gradients
  - No italic — use weight contrast
  - Only Geist + Geist Mono fonts
  - Tables > grid cards for any >3-row data
  - All numbers right-aligned, mono, `tnum` enabled
- The `pricelens_mockup.html` file is the canonical reference implementation. New components should be consistent with it.

### Writing (PRD, docs, etc.)
- The user prefers concise, structured docs over prose
- Use tables, numbered sections, and explicit P0/P1/P2 priority labels
- Avoid filler hedging ("perhaps", "could possibly") — be direct
- Express dates absolutely (2026-06-13), not relatively ("in three weeks")
- The user is in learning phase for AI/LLM/agentic engineering — explain non-obvious concepts step-by-step when first introduced

### When the user asks for design or product input
- The user has strong product taste and will push back if you're not specific enough
- Don't propose generic options — propose specific ones with tradeoffs explicitly named
- If you're uncertain, say so directly; don't fake confidence
- Several past iterations were rejected for being "too generic" or "not differentiated" — favor sharp opinions over safe ones

---

## Open items (as of 2026-05-26)

| # | Item | Action |
|---|---|---|
| Q1 | ~~Confirm MiroMind API endpoint, model name~~ | ✅ Closed 2026-05-26 — OpenAI-compatible, two models, single base URL |
| Q2 | Validate yfinance / SEC EDGAR data quality for all 6 implied assumptions | W1 |
| Q3 | ~~Decide if to add SGX local stocks~~ | ✅ Closed 2026-05-26 — NOT doing it |
| Q4 | Build phase has not started — PRD W1 plan needs activation | **In progress** |
| Q5 | MiroMind rate limit (RPM / TPM) unknown | Observe during W1 Test A/B |
| Q6 | Verify chat mode (tool_choice=none) output quality is usable | W1 Test B — fallback is Python templating, never a second API |

---

## Bugfix / behavior notes

(empty — project hasn't started building yet)

---

## Things that should not be in this folder

- `.env` files with API keys → use parent dir or `.env.local` (gitignore)
- Raw downloaded data dumps → use `data/` subfolder, gitignore
- Personal notes unrelated to the project

---

## Communication style

- The user types in Chinese; Claude can respond in Chinese
- Code, file names, and technical terms stay in English
- Visual design discussions: the user is opinionated, expects pushback, doesn't want flattery

---

## Last session summary (rolling)

**2026-05-20** — Locked PRD v0.1, generated three iterations of the HTML mockup (final = research-report aesthetic), extracted the design system into a permanent doc, organized everything into this folder.

**2026-05-26** — MiroMind API access secured ($100 + 100 calls). Researched platform docs and confirmed: the API ships an OpenAI-compatible endpoint with two models (mini 30B + flagship 235B), both deepresearch agents. Key architectural decision: stay on a **single API** by toggling between deep research mode (`tool_choice=auto`, used only for Evidence Hunter) and chat mode (`tool_choice=none`, used for all narration / Critic / Synthesizer). PRD upgraded to v0.2 with: (1) new Layer 2 "Computation" between data and LLM; (2) F4 evidence search promoted to hero feature; (3) F3 short-term attribution scope-reduced to 5d + 2 factors only; (4) W1 verification expanded with Test A (deep research) + Test B (chat mode); (5) budget mitigations (mini for dev, cached pre-runs for demo).

**2026-05-27 (W2 closing dev session)** — Built critic.py (Python mechanical validation per Appendix A.4, free) + prompts/synthesizer.md + pipeline run_synthesizer (chat mode, cached, ~$0.05-0.20/run, code only — never live-tested). Then W2-2 slider real DCF: pipeline exposes company_inputs in rdcf; JS port of dcf_equity_value_per_share; computeAdjustedPrice uses effective=consensus+sliderOverrides. Verified across COST/NVDA/TSLA — proves the demo narrative ("growth alone at p50 → $211 ≈ market; growth+WACC both at p50 → $1028 overshoot"). Browser-tested via Claude Preview + Claude in Chrome — caught 2 real bugs and fixed both: (a) slider initial = $693 not baseline $65 (sliderTouched flag separate from sliderValues); (b) ~706px viewport horizontal overflow 91px (cover-grid + main-grid both stack at <1024px). Three preview-tool "click handler doesn't fire" alarms turned out to be Preview-tool quirks; real DOM .click() works fine. Total W2 cost spent: $3.78 of $100 budget. **W3 in progress** via 2 parallel worker agents: A=short_term.py backend (factors: fundamental_update + flow_positioning + unexplained, yfinance-only no LLM) + pipeline/api integration, B=waterfall UI in mockup html. Tech Lead doing T3 (G3-C frozen-tag, light mode after B finishes) + T4 (this docs sync).

**2026-05-27 (W2 building dev session)** — Pivoted to /dev mode (Tech Lead orchestrating worker agents). Git init. 3 worker agents spawned in parallel (non-overlapping files, no worktree available so manual git mgmt by Tech Lead): A=FastAPI backend (api.py, 70 lines, 4 endpoints, no Pydantic, CORS), B=frontend integration (pricelens_mockup.html: ticker switcher / boundary mode UI / slider / bilingual disclaimer / agent log skeleton / fixture fallback), C=reverse_dcf improvements (beta cap 1.5, analyst consensus pull, historical context computation + markdown formatter). All 3 completed in ~7-15 min each. Tech Lead wired C's historical context into pipeline.py decoder prompt, bumped decoder cache to v2. All 4 commits on master. Full integration test via TestClient: all endpoints pass, mockup renders with FIXTURE_DATA fallback, bilingual disclaimer visible. **W2 backbone done.** Cost gotcha noted: yfinance's revenueGrowth/earningsGrowth are YoY not 5Y forward — internal field names keep `_5y_consensus` suffix but display strings honestly say "近一年". Next: critic + synthesizer agents (W2 finisher); then W3 short-term attribution + slider real formula.

**2026-05-27 (earlier today)** — Major decision-locking day. Scope-risk audit identified 6 high-impact gaps (G1-G6), all decided with user: G1 evidence brief JSON schema (PRD Appendix A); G2 Monte Carlo interval estimation; G3 evidence frozen + annotated on slider; G4 SSE streaming evidence; G5 OFFLINE_MODE + 3-tier degradation; G6 UI credit + Agent action log. Wind AIFin Market evaluated and deferred to post-MVP — MVP stays on yfinance. PRD upgraded to v0.3.

`reverse_dcf.py` ran on COST / NVDA / TSLA — methodology validated decisively. COST gave clean intervals (growth 16-28%), NVDA gave intervals that honestly surface the bubble assumption tension (60%+ growth or 6% WACC required), TSLA returned NO SOLUTION across the board (DCF can't explain $429 price). This 3-stock variation became the new demo narrative arc.

Follow-up product audit identified 4 remaining product-level decisions (B1-B4), all locked: B1 F7 falsifiability matrix deleted (overlaps with F6 slider; demo time reallocated to 5d attribution); B2 bilingual disclaimer (footer + 2 tooltips); B3 slider range = Monte Carlo p10-p90; B4 DCF boundary state (PRD §6.4) — full spec including Evidence Hunter's boundary mode for hunting alternative framework evidence. Plus A1: bilingual prompt design (zh in dev, en in submission) via `{{LANG}}` placeholder. PRD upgraded to v0.4 with full demo script rewrite (3-act: COST → NVDA → TSLA).

Prompt templates `prompts/evidence_hunter.md` and `prompts/decoder_narrator.md` drafted with full bilingual + dual-mode (standard / boundary) support. Awaiting user review then ready for W1 API tests (Test A: deepresearch on real assumption; Test B: chat mode for numbers→human-readable).

Next session: user reviews prompts → wires MiroMind API key → W1 Test A + Test B run (estimated 30 min once key is in).
