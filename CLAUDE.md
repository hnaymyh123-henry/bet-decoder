# CLAUDE.md · PriceLens Project

> Project-specific instructions for any Claude session working inside this folder.
> The user's global workspace CLAUDE.md lives at `C:\Users\Henry Ma\CLAUDE.md` and still applies — this file adds project-specific context on top.

---

## Project at a glance

**Project name:** Bet Decoder (formerly: PriceLens) — Investment-bet X-ray, open-source
**Positioning:** Self-hosted, single-file-SQLite open-source tool. Anyone can `git clone && uvicorn api:app` and run their own instance. Pivoted 2026-05-28 from "single-stock reverse DCF report" to "universal investment-bet decoder".
**Origin (historical):** Started as an entry to UCWS Singapore Hackathon 2026 (Agent track, MiroMind partnership). 2026-05-27: re-framed as open-source product. 2026-05-28: pivoted to Bet Decoder concept after 6 rounds of UI iteration (demo_b → demo_g) revealed the "single stock report" form was inherently report-like rather than app-like.
**One-line pitch:** Paste any investment bet (current price / analyst target / tweet / your portfolio) → Bet Decoder X-rays what that bet implicitly believes, lets you stack multiple bets side-by-side, and has AI synthesize cross-bet insights.
**Roadmap:** 2026-06-13 v1.0 public release · post-release driven by GitHub Issues; LICENSE / README / Dockerfile not yet in (queued).
**Maintainership:** Single maintainer at v1.0; designed for contributions.
**Status:** **Phase 1→5 + Phase 4 code-review + market-narrative layer DONE (latest 2026-06-01).** Real backend M1-M5 + frontend + release scaffolding (README/LICENSE/Dockerfile) + market-narrative layer on `master` (`f26fc47`); **origin SYNCED via PR #9 (2026-06-01)** — origin/master == local master, no longer stale. **12 verify suites all green (~290 assertions)** (M1 ALL · M2 31 · M3 21 · M4 15 · M5 40 · M6 39 · M7 14 · M8 35 · phase4-W1 31 · phase4-W2 25 · phase4-W3 20 · narrative 19). **Market-narrative layer (2026-06-01):** `narrative.py` deep-researches the live bull/bear debate behind the implied numbers (formula = question generator); source-tier classifier A/B/C/D by host (code-enforced honesty, social/crypto can't be a claim's sole backer); valuation-tension gate → anchor mode via `narrative_premium ≥ 50%` (decoupled from theme keywords); cross-check pairs narrative lean vs independent evidence verdict per number + flags divergences (decision B); portfolio parent→constituents view + weight bar; de-clutter (accent rationed to design-system §2.2, 58→19 hits). **Phase 4 caught + fixed 5 CRITICAL + ~12 SHOULD-FIX real-path bugs that stub tests missed** (via 3 independent adversarial review agents): evidence cache key salted-hash→sha1 (cross-process), DCF baseline discarded→decoupled (undervalued no longer 100%-narrative), cross-thread sqlite→activity_logs now persists, live SSE→JobQueue serialized, synth theme-align O(K²T²)→capped+memoized, non-DCF strength mean=0/negative, dedup IntegrityError→optimistic-insert, ai-gate over-match, cost 3x understated, +more. **⚠ OPS:** project `.env` has a real MIROMIND_API_KEY → any decode via default hunter hits live API; run verify scripts/scripts with `MIROMIND_API_KEY=""` or rely on stubs (verify_m8 now self-protects). On Windows gbk console add `PYTHONIOENCODING=utf-8` to run verify_*/prerun without UnicodeEncodeError. **Known limitation (deferred):** band-ruler (蒙特卡洛 band 当强度尺) is dead in the real get_card path (decode_detail not persisted + traditional cards lack run_id) → synthesis uses relative-gap fallback; wiring driver-view persistence is a follow-up. **Next (needs USER):** before demo `python prerun_demo.py --execute` (~$32) to populate caches. (origin sync DONE via PR #9 2026-06-01.)

**⚠ ALWAYS READ BET_DECODER_VISION.md before doing product/UI work** — it has the current product form, Bet Card primitive spec, 5-act demo narrative, Aha matrix, P1-P5 implementation phases, and the codebase interface map.

---

## Why this project exists

**Core thesis:** Existing investment-research AI tools all do `analyze company → output report`. PriceLens does the inverse: `take price as input → decompose into implicit assumptions → score evidence per assumption`. The object of transparency is **the market's collective reasoning**, not the AI's own reasoning. We don't see any mature open-source or commercial product systematically doing this.

**Why investment research specifically:** Stock prices are uniquely well-suited to reverse decomposition because the DCF math is well-known and the underlying data (financials, consensus, market data) is fully public. Bonds / FX / commodities can follow once methodology is proven.

**Why open-source:**
1. A reasoning-transparency tool *must* be auditable; closed-source contradicts the thesis
2. Different markets need different data sources (US/yfinance, CN/Wind, EU/Refinitiv) — closed source can't cover all
3. Prompts + schemas (evidence brief, critic rules, decoder voice) should iterate against community feedback
4. Lowers the barrier to investment research — Bloomberg Terminal is $24k/yr; self-hosted PriceLens is only the LLM call cost (~$0.10-3 per stock)

---

## Documents in this folder

| File | Purpose |
|---|---|
| `CLAUDE.md` | This file — project context for Claude sessions |
| **`BET_DECODER_VISION.md`** | **🔥 2026-05-28 pivot** — Bet Decoder product vision, Bet Card primitive, 5-act demo, P1-P5 implementation. READ THIS FIRST for any product/UI work. |
| `pricelens_prd.md` | Product requirements doc v0.7 — head section is pivot announcement, body §1-§15 is pre-pivot v0.6 content (LLM arch / DCF algo / SQLite / risks still valid; demo script & feature list superseded). |
| `reverse_dcf.py` | Reverse DCF prototype with Monte Carlo interval estimation (G2). Run: `python reverse_dcf.py NVDA` |
| `requirements.txt` | Python deps: yfinance, numpy, scipy |
| `prompts/evidence_hunter.md` | Evidence Hunter prompt template (deepresearch mode; supports standard + boundary modes per B4) |
| `prompts/decoder_narrator.md` | Long-term Decoder narrator prompt template (chat mode; numbers → human-readable assumptions) |
| `api.py` | FastAPI server. Run: `uvicorn api:app --reload --port 8000`. Serves pipeline outputs (from SQLite as of v0.6) + the mockup at `/`. No LLM. |
| `db.py` | SQLite DAO layer (v0.6+). Single source of truth for schema DDL and `save_pipeline_run` / `get_latest_run` / `cache_get` / `cache_put`. DB file: `pricelens.db` at project root. |
| `migrate_to_sqlite.py` | One-shot migration: reads legacy `outputs/*.json` + `cache/{decoder,evidence,synthesizer}/*.json`, writes them into `pricelens.db`. Idempotent. |
| `outputs/{TICKER}_{timestamp}.json` | **Legacy (v0.5 and earlier).** Pipeline used to write per-run JSON here. As of v0.6, retained as on-disk safety net for ~1 week then to be deleted; new runs write to SQLite. |
| `cache/decoder/`, `cache/evidence/`, `cache/synthesizer/` | **Legacy (v0.5 and earlier).** LLM cache moved into the `llm_cache` table in SQLite at v0.6. `cache/price_history/` STAYS as files (time series + TTL fits the filesystem better). |
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

**2026-06-01 (市场叙事层落地 + origin 首次同步 via PR #9)** — 本轮把产品从"拆公式"推进到"拆 market sentiment"(公式退居为 question generator)。**新增 `narrative.py` + `prompts/market_narrative.md`**:flagship deepresearch 研究隐含数字背后的多空论战(~$6.73/call)。**代码强制诚实护栏**:source-tier 分级器(A/B/C/D 按 host,exact/subdomain 而非 substring——修了 netflix.com 误判含 "x.com"、Yahoo `/investors` slug 误判 A 两个假阳性);社媒/加密源不能作为某条 claim 的唯一背书。**估值张力门**:anchor mode 触发从"主题关键词匹配"解耦成 `narrative_premium = 现价中 DCF base 解释不了的占比 ≥ 50%`,卡上露出溢价 %。**交叉验证(决策 B)**:每个隐含数字把"叙事 lean"对"独立证据 verdict"配对,分歧打 ⚠ —— 让证据层挣回自己的成本而非砍掉。**前端**:组合卡父→子视图(decoded 持仓归到组合下 + 权重构成条);去杂(oxblood accent 按设计系统 §2.2 配给,58→19 处,全为信号)。**测试**:`verify_narrative` 19 断言(分级器/解析/校验/honest-empty/cross-check 全覆盖,用真实 $6.73 NVDA 输出 `narrative_sample.json` 当 fixture);全套 12 套件 ~290 断言全绿。**origin 同步**(悬了两个 phase 的 push 问题解决):用 `git -c credential.helper='!gh auth git-credential'` 一次性凭证助手(不改持久 git config)push 分支 → 开 PR #9(39 commit)→ rebase-merge 合并;GitHub rebase 重写 SHA 致本地无法快进,确认两边树逐字节相同后把本地 `git reset --hard origin/master`,0 ahead/0 behind,临时分支已删。master=`f26fc47`。

**2026-05-28 (Phase 4 独立 Code Review ✅ — 抓出 5 CRITICAL + ~12 SHOULD-FIX 真路径 bug 并全修)** — 用户问"这些走 QA 了么",我诚实承认 Phase 3 只做了 Tech Lead 重跑 worker 自测(全 stub LLM),没走独立 QA。用户选"完整 Phase 4 Code Review"。**派 3 个独立对抗 review agent(staff-engineer 视角,只读真实代码,专挑 stub 测不到的真实路径)**——评级 **REQUEST CHANGES**,发现:🔴 evidence 缓存 key 用进程随机 `hash()`→跨进程永不命中(预跑缓存策略失效)· 🔴 DCF 反解失败丢弃 baseline→被低估股判 100% 叙事 · 🔴 非 DCF 强度 mean=0/负值退化 · 🔴 live SSE 绕开 JobQueue · 🔴 同源主题对齐 O(K²T²) 无上限 LLM 调用;🟠 跨线程 sqlite→activity_logs 静默不落库(回放失效)· dedup IntegrityError 500· ai-gate "memory/storage" 误判普通公司· 成本估算 3x 偏低· 组合卡跳过证据· EV/EBITDA 负值· 断开不取消引擎· ActivitySink 非线程安全· 畸形 body→500· 错误格式不统一。**派 3 个 fix worker 并行修(文件不重叠:W1=db/api/activity · W2=decoder/evidence · W3=synthesizer)**,每个补可复现测试,全部合并。**重新评级 APPROVE:全套 11 套件 ~267 断言全绿**(含 phase4-W1 31/W2 25/W3 20 + 跨线程 activity_logs 落库已证)。Tech Lead 轻量修了 verify_m8 的 env 屏蔽脆弱性(pop→设空串,防 dotenv 重注真 key 致挂起)。**教训进 memory**:绿色 stub 测试≠QA,LLM/IO/并发代码合并前必须独立对抗 review + 真实 smoke。**关键 ops 发现**:项目 .env 有真 key,decode 默认 hunter 会打真实 API;Windows gbk 控制台跑脚本需 `PYTHONIOENCODING=utf-8`。**延后**:band-ruler 真实读卡路径死代码(综合走相对差距 fallback)。master=f667cb1。

**2026-05-28 (Phase 3 多 Agent 开发 ✅ 全部完成 — 8 Issue 全建全合并,真后端 M1-M5 落地)** — 续 Phase 2,全自动跑完 Phase 3(用户授权"每个 worker 完成后不询问,自动续推")。**8 个 Worker Agent(isolation worktree)按依赖链顺序/并行完成,我作为 Tech Lead 本地 review+测+合并**:#1 M1 数据层(db.py:bet_cards/theme_exposures/activity_logs + runs anchor 列 + BetCard DAO)→ #2 M2 解码骨架(decoder.py:decode_bet 三段式 + 7 lens 注册表 + 确定性决策树)→ #3 M2 anchor mode(锚 lens + AI 复合体 primary + R1 theme_exposures + R2 蒙特卡洛 band)∥ #4 M2 证据(evidence.py:Step3 不可跳过 + 缓存 + 诚实留空)→ #5 M3 综合(synthesizer.py:五关系路由 + 强/中/弱 + 同源几何均值;删旧 run_synthesizer)→ #6 M5 活动流(activity.py:ActivityEvent + emit sink + SSE + 时序回放 + **修 bug#34**)→ #7 M4 前端(pricelens_mockup.html 改三区工作台 + api.py +5 REST;Chrome 真机验证)→ #8 收口(verify_m8 e2e 35 + prerun_demo.py)。**191 断言全绿**。**关键工程解法**:push 凭证坏 + 改不了 settings → 每 worker 首步 `git merge master`(共享 .git 库拿已合并成果),无需 push/改配置链式累积;本地合并模式(worktree 提交 → 我本地 review+合并 → gh 关 Issue)。**成本纪律全程**:所有 worker 自测用 stub LLM,零真实 API 花费;真实 demo 预跑(~$32)留用户 demo 前 `--execute`。**环境约束**:Bash shell 重启后 PATH 坏(coreutils/python 不在 PATH;python 在 `/c/Users/Henry Ma/miniconda3/python.exe`,coreutils 加 `/c/Program Files/Git/usr/bin`);worktree 物理目录因 OS 句柄删不掉(git 已 prune,无害)。**下一步**:Phase 5 已轻量收尾;待用户 `git push` 同步 GitHub(origin 仍 stale 82e8107)+ demo 前预跑。恢复:读 PRD.md + PROJECT_CONTEXT.md + API_CONTRACT.md,master=e69c5f1。

**2026-05-28 (Phase 2 技术拆解 ✅ — GitHub repo 上线 + 8 Issue 建成)** — 续 Phase 1 收口。**Step D 冻结**:`PRD.md` 定稿(5 模块 + 数据模型总览 + 公共接口契约 + Phase 2 拆解须知);`PRD-draft.md` 标为工作底稿。**Phase 2**:架构决策落 `PROJECT_CONTEXT.md`(无认证 / FastAPI / vanilla JS / SQLite + 手写幂等 DDL不上Alembic / MiroMind API / 出 API_CONTRACT.md);构建路径 = **直接建真后端**(非 hardcoded 原型)。**GitHub**:私有 repo **github.com/hnaymyh123-henry/bet-decoder** 已建并 push(2 个提交:SQLite 迁移 + Phase 1 docs);8 个 Issue 全建好(#1 M1数据层阻塞全部 → #2 M2骨架+7lens → #3 anchor+R1/R2 ∥ #4 证据 → #5 M3综合 → #6 M5活动流 → #7 M4前端 → #8 收口),含工程级验收标准 + 依赖。**⚠️ 两个环境约束(重要,影响 Phase 3)**:(1) auto 模式分类器 HARD BLOCK 拦"推代码到外部新 repo",已由用户在 `~/.claude/settings.json` 加 `autoMode.environment` 声明自己 GitHub 账号解除(我自己改不了该配置=正确的自我修改防护);(2) **我的 Bash 环境 git push 拿不到凭证(GCM 非交互 401),但 `gh` 命令全可用**(gh api/issue create/`gh pr merge` 服务端合并都通)——所以 Phase 3 的 PR 合并走 gh 没问题,但"推 commit/branch"要么用户跑一行、要么待解。一个 trivial doc commit(4f54143)尚未 push,不阻塞。**下一步**:Phase 3 多 Agent 并行——但先定 push 处理方式(用户推 / 本地合并 / 修凭证)。恢复:读 PRD.md + PROJECT_CONTEXT.md + GitHub Issues #1-8。

**2026-05-28 (Phase 1 产品对齐 ✅ 全部完成 — 5 模块全 LOCKED,共 49 决策)** — 续上一 session,把剩余 Module 3/5/4 全 close-out。**全部落盘**:`PRD-draft.md` 顶部 meta = "ALL 5 modules LOCKED, Next=Step D freeze PRD → Phase 2";`docs/glossary.md` 累计 ~24 术语。**Module 3 跨卡综合(10)**:纯消费方关系引擎+综合叙事;图谱+叙事双层(headline_insight=demo 字幕);手动触发+卡集合hash缓存;**全程 chat mode**;五关系按卡配对自动路由(同标的→共识/分歧/矛盾,跨标的→同源,同序列→漂移);强度只分**强/中/弱**(蒙特卡洛 band 宽当"多大差距才有意义"尺子,同源用几何均值);同源主题对齐走 chat 模糊匹配;失败诚实留空;SynthesisResult 存 llm_cache。**Module 5 Agent活动流(7)**:事件协议+SSE管道+埋点的横切基础设施;定位=过程透明价值载体(非进度条);**live+持久化可回放带时序模拟**(配套预跑缓存demo);事件=决策级语义推理步单档+kind标签;**统一工作台feed**;emit回调注入M2/M3;串行+排队;ActivityEvent 存 activity_logs。**Module 4 工作台前端(8,快速过)**:展示交互层调M2/M3+消费M5流;复用扩展 pricelens_mockup.html;严守设计系统;**三区布局**(主画布多卡并列+右侧活动流feed+底部综合面板);卡形态继承M1。**关键洞察**:BetCard 答 What / ActivityEvent 答 How,正交两 primitive。**回填项(邻居模块已LOCKED但需补接口产物,不算重开)**:M2 ← R1 单股卡产主题暴露% + R2 DCF driver 带蒙特卡洛 band;M1 ← 新增 activity_logs 表。**协作边界进 memory**:Phase 1 只问产品决策,纯实现/代码复用细节自理。**下一步**:Step D 把 PRD-draft.md 冻结成正式 PRD.md → Phase 2 技术拆解。恢复:读 PRD-draft.md(5 模块全 LOCKED)+ glossary,直接进 Step D 或 Phase 2。

**2026-05-28 (Phase 1 产品对齐进行中 — /dev Module 1+2 已 LOCKED)** — 在 BET DECODER pivot 后,走 `/dev` Phase 1 模块化对齐。5 模块顺序:1.Bet Card 数据模型 → 2.Decoder Engine → 3.跨卡综合 → 5.Agent 活动流 → 4.工作台前端。**状态锚点全部落盘**:`PRD-draft.md`(顶部 progress meta = module 2/5 locked, current=3, layer=Big Picture)+ `docs/glossary.md`(14 术语)+ 2 张真数据样例卡(`bet_card_sample.html` 单股 / `portfolio_card_sample.html` 组合仪表盘)。**Module 1 LOCKED(14 决策)**:卡分两类(单股卡片/组合仪表盘)、Bet Card 命名、单股 3 source 共用 schema(bet 可空)、不可变快照、series 分组+去重+日收盘粒度、新鲜度按天、被动存储层、**方案 C 混合复用 runs**(新增 bet_cards 信封 + portfolio_holdings/portfolio_exposures 2 表 + runs 加 anchor_price/anchor_type)、组合编辑草稿态。**Module 2 LOCKED(10 决策)**:**frame-adaptive agentic decode**(agent 按公司挑 lens,reverse_dcf.py 降级成 DCF lens 一个工具)、诚实定位(不宣称还原真相)、MVP 只做 Market+Portfolio、lens 注册表 7 个(DCF/PE/PS/EV-EBITDA/P-FCF/P-B/PEG)+ primary+交叉验证、证据强制不跳过、**锚 lens 第二梯队 + anchor mode 对 AI 复合体(GPU/存储/光模块/AI 应用)作 primary**、anchor 输出=基础+叙事/期权成分对账现价。**known-未决**:宏观流动性分母变量(V2 接真数据)· Opinion 模糊文本抽取(V2)。**下一步**:Module 3(跨卡综合)Big Picture。恢复方式:读 PRD-draft.md 顶部 progress meta + glossary,继续 `/dev` Phase 1 Step B。

**2026-05-28 (BET DECODER PIVOT — major product reframe)** — After 6 rounds of UI iteration (demo_b sliders → demo_c outline → demo_d mind map → demo_e progressive tree → demo_f bespoke per-method → demo_g time-machine+tension), user called out "the product form is a visualized research report, not an App/Agent." This catalyzed a pivot from "single-stock reverse DCF report" to "**Bet Decoder — investment-bet X-ray**". Core insight: the reverse-decoding engine is universal — it can decode ANY bet (market price / analyst PT / tweet / portfolio), not just one stock's market bet. New product primitive is "**Bet Card**" — every decoded bet becomes a portable card with Subject / Source / Bets / Risks / decision chain. Multi-card coexistence + AI cross-card synthesis (e.g., "your portfolio depends on the same assumption as Goldman's PT") is the true Aha that no single feature gives. Deliverables this turn: (1) `BET_DECODER_VISION.md` 350+ lines complete vision doc (Bet Card spec, 5-act demo, P1-P5 implementation, codebase interface map, UI lineage notes from b→g); (2) deleted demo_b through demo_g; (3) `pricelens_prd.md` bumped v0.6→v0.7 with pivot announcement at top, body sections marked partially superseded. Name confirmed: **Bet Decoder** (BAT was autocorrect of Bet). **Next**: user decides P1-P5 pacing for hardcoded prototype (4.5 days total).

**2026-05-27 (positioning + storage pivot)** — User called the strategic shift: "改掉 Hackathon-centric 定位,做成真正的开源项目,起码先从 JSON 文件迁到 SQLite." Three decisions locked: (1) start the pivot NOW (before v1.0 demo), not after; (2) SQLite schema = full normalization (multi-table + foreign keys, 11 tables + llm_cache + schema_meta); (3) scope = PRD/CLAUDE.md rewrite + storage migration (README/LICENSE/Dockerfile queued for next pass). PRD upgraded to v0.6: §0 reframed from "UCWS Hackathon" → "open-source self-hosted tool"; §1.3 new section on why open-source; §8 tech stack adds storage row (SQLite + price_history files); §11 renamed "v1.0 release demo script"; §12 success criteria rewritten (drops finalist/top-3/$10k, adds GitHub stars/forks/external PRs/external runs); §14 docs table adds db.py + migrate_to_sqlite.py. Dispatched: Worker A (db.py + migration script) and Worker B (rewire pipeline.py + api.py), B blocked by A. `cache/price_history/` stays as files (time series + TTL fits filesystem). Legacy `outputs/*.json` + `cache/{decoder,evidence,synthesizer}/*.json` get migrated then deprecated for ~1 week as safety net.

**2026-05-20** — Locked PRD v0.1, generated three iterations of the HTML mockup (final = research-report aesthetic), extracted the design system into a permanent doc, organized everything into this folder.

**2026-05-26** — MiroMind API access secured ($100 + 100 calls). Researched platform docs and confirmed: the API ships an OpenAI-compatible endpoint with two models (mini 30B + flagship 235B), both deepresearch agents. Key architectural decision: stay on a **single API** by toggling between deep research mode (`tool_choice=auto`, used only for Evidence Hunter) and chat mode (`tool_choice=none`, used for all narration / Critic / Synthesizer). PRD upgraded to v0.2 with: (1) new Layer 2 "Computation" between data and LLM; (2) F4 evidence search promoted to hero feature; (3) F3 short-term attribution scope-reduced to 5d + 2 factors only; (4) W1 verification expanded with Test A (deep research) + Test B (chat mode); (5) budget mitigations (mini for dev, cached pre-runs for demo).

**2026-05-27 (W2 closing dev session)** — Built critic.py (Python mechanical validation per Appendix A.4, free) + prompts/synthesizer.md + pipeline run_synthesizer (chat mode, cached, ~$0.05-0.20/run, code only — never live-tested). Then W2-2 slider real DCF: pipeline exposes company_inputs in rdcf; JS port of dcf_equity_value_per_share; computeAdjustedPrice uses effective=consensus+sliderOverrides. Verified across COST/NVDA/TSLA — proves the demo narrative ("growth alone at p50 → $211 ≈ market; growth+WACC both at p50 → $1028 overshoot"). Browser-tested via Claude Preview + Claude in Chrome — caught 2 real bugs and fixed both: (a) slider initial = $693 not baseline $65 (sliderTouched flag separate from sliderValues); (b) ~706px viewport horizontal overflow 91px (cover-grid + main-grid both stack at <1024px). Three preview-tool "click handler doesn't fire" alarms turned out to be Preview-tool quirks; real DOM .click() works fine. Total W2 cost spent: $3.78 of $100 budget. **W3 in progress** via 2 parallel worker agents: A=short_term.py backend (factors: fundamental_update + flow_positioning + unexplained, yfinance-only no LLM) + pipeline/api integration, B=waterfall UI in mockup html. Tech Lead doing T3 (G3-C frozen-tag, light mode after B finishes) + T4 (this docs sync).

**2026-05-27 (W2 building dev session)** — Pivoted to /dev mode (Tech Lead orchestrating worker agents). Git init. 3 worker agents spawned in parallel (non-overlapping files, no worktree available so manual git mgmt by Tech Lead): A=FastAPI backend (api.py, 70 lines, 4 endpoints, no Pydantic, CORS), B=frontend integration (pricelens_mockup.html: ticker switcher / boundary mode UI / slider / bilingual disclaimer / agent log skeleton / fixture fallback), C=reverse_dcf improvements (beta cap 1.5, analyst consensus pull, historical context computation + markdown formatter). All 3 completed in ~7-15 min each. Tech Lead wired C's historical context into pipeline.py decoder prompt, bumped decoder cache to v2. All 4 commits on master. Full integration test via TestClient: all endpoints pass, mockup renders with FIXTURE_DATA fallback, bilingual disclaimer visible. **W2 backbone done.** Cost gotcha noted: yfinance's revenueGrowth/earningsGrowth are YoY not 5Y forward — internal field names keep `_5y_consensus` suffix but display strings honestly say "近一年". Next: critic + synthesizer agents (W2 finisher); then W3 short-term attribution + slider real formula.

**2026-05-27 (earlier today)** — Major decision-locking day. Scope-risk audit identified 6 high-impact gaps (G1-G6), all decided with user: G1 evidence brief JSON schema (PRD Appendix A); G2 Monte Carlo interval estimation; G3 evidence frozen + annotated on slider; G4 SSE streaming evidence; G5 OFFLINE_MODE + 3-tier degradation; G6 UI credit + Agent action log. Wind AIFin Market evaluated and deferred to post-MVP — MVP stays on yfinance. PRD upgraded to v0.3.

`reverse_dcf.py` ran on COST / NVDA / TSLA — methodology validated decisively. COST gave clean intervals (growth 16-28%), NVDA gave intervals that honestly surface the bubble assumption tension (60%+ growth or 6% WACC required), TSLA returned NO SOLUTION across the board (DCF can't explain $429 price). This 3-stock variation became the new demo narrative arc.

Follow-up product audit identified 4 remaining product-level decisions (B1-B4), all locked: B1 F7 falsifiability matrix deleted (overlaps with F6 slider; demo time reallocated to 5d attribution); B2 bilingual disclaimer (footer + 2 tooltips); B3 slider range = Monte Carlo p10-p90; B4 DCF boundary state (PRD §6.4) — full spec including Evidence Hunter's boundary mode for hunting alternative framework evidence. Plus A1: bilingual prompt design (zh in dev, en in submission) via `{{LANG}}` placeholder. PRD upgraded to v0.4 with full demo script rewrite (3-act: COST → NVDA → TSLA).

Prompt templates `prompts/evidence_hunter.md` and `prompts/decoder_narrator.md` drafted with full bilingual + dual-mode (standard / boundary) support. Awaiting user review then ready for W1 API tests (Test A: deepresearch on real assumption; Test B: chat mode for numbers→human-readable).

Next session: user reviews prompts → wires MiroMind API key → W1 Test A + Test B run (estimated 30 min once key is in).
