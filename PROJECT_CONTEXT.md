# PROJECT_CONTEXT · Bet Decoder

> /dev 工程上下文索引。产品决策见 `PRD.md`(冻结版,权威);术语见 `docs/glossary.md`;愿景见 `BET_DECODER_VISION.md`。
> 本文件记录**架构决策 + 模块依赖 + 当前状态**,架构变化时立即更新(不等 Phase 5)。

---

## 一句话

Bet Decoder = 投资 bet 的 X 光机。输入任意 bet(市场价/分析师目标价/推文/持仓)→ 反向解码隐含假设 → 多卡并列 → AI 跨卡综合。开源 self-hosted,单文件 SQLite,`git clone && uvicorn` 即跑。

## 架构决策(Phase 2 检查点,锁定 2026-05-28)

| 项 | 决策 | 理由 |
|---|---|---|
| 认证 | **无认证** | self-hosted 单用户开源工具 |
| 后端 | **FastAPI**(扩展 `api.py`) | 沿用现有 |
| 前端 | **vanilla JS**,扩展 `app.html`,严守 `pricelens_design_system.md` | 沿用现有,无框架 |
| 存储 | **SQLite + `db.py` DAO**;schema 见 PRD.md 数据模型总览 | 单文件,零配置 |
| DB 迁移 | **手写幂等 DDL**(`init_db` 内 CREATE TABLE IF NOT EXISTS + 老数据回填脚本),**不上 Alembic** | solo + 单文件 SQLite,重型框架过度 |
| LLM | **MiroMind API**(OpenAI 兼容,`client.py`);chat mode(narration/critic/synth)+ deep research mode(evidence/anchor lens) | 单 API 双模式切换 |
| API 设计 | REST;命名 `/api/cards`、`/api/decode`、`/api/synthesize`、`/stream/...`(SSE);统一错误 `{error_code, message}` | 见 `API_CONTRACT.md` |
| API Contract | **出 `API_CONTRACT.md`** 锁前后端接口 | M4 通过 API 调 M2/M3,避免扯皮 |
| 代码风格 | Python 3.11 + PEP 8;前端跟 mockup 既有约定 | 沿用 |

## 构建路径决策(2026-05-28)

**直接建真后端** —— 按冻结 PRD 一次性实现 M1→M2→M3→M5→M4,接 reverse_dcf/SQLite/MiroMind API,无 throwaway hardcoded 原型。(vision 文档的 4.5 天 hardcoded 路径已弃用。)

## 模块依赖

```
1 数据模型 ← 基础, 全依赖
2 Decoder  ← 1
3 跨卡综合  ← 1+2
5 活动流    ← 跨 2+3+4 (SSE 横切)
4 工作台前端 ← 1, API 调 2/3, 消费 5
```
实现顺序:1 → 2 → 3 → 5 → 4

## 当前状态

- **GitHub repo**:https://github.com/hnaymyh123-henry/bet-decoder (private)。✅ **origin 已同步**(2026-06-01 经 PR #9 rebase-merge;origin/master == 本地 master == `f26fc47`,0 ahead/0 behind)
- **Phase 1-5 + Phase 4 code review + 市场叙事层全部完成**(latest 2026-06-01):真后端 M1-M5 + 前端 + 发布脚手架(README/LICENSE/Dockerfile) + 市场叙事层(narrative.py)。**12 套件 ~290 断言全绿**(M1-M8 + phase4-W1/W2/W3 + narrative 19)。Phase 4 抓修 5 CRITICAL + ~12 SHOULD-FIX 真实路径 bug。
- **完整模块**:db.py(M1)· decoder.py + evidence.py(M2)· synthesizer.py(M3)· activity.py + api.py(M5/SSE+REST)· app.html(M4)· prerun_demo.py(demo 预跑)· verify_m1..m8 + verify_phase4_w1/w2/w3
- **技术债**:见 `docs/feature-log.md` 技术债登记(TD1-TD7:band-ruler 真路径 / sse.py 旧 mock / gbk 编码 / W1 旧脚本 / pricelens_prd 历史 / AI 真实质量未验 / .env 真 key ops)
- **下一步(需用户)**:demo 前 `python prerun_demo.py --execute`(兼首次真实 LLM smoke)。**2026-06-01 成本重算并压回预算内**:prerun 现 **~$38.61**(证据 $19.26 = NVDA+TSLA 单股 ×3 条 · flagship 市场叙事 $16.14 = 2 张单股 · 综合 $3.21),**✅ 在 $100 预算内**(dry-run exit 0)。所选杠杆:**组合逐股证据不再 hunt**——decoder 对持仓腿传 `_SKIP_EVIDENCE`(组合的信号=构成+跨卡综合,不是逐股 deep research;要看某持仓的证据就把它当单股卡解码)。单股卡的 Step 3 仍强制不跳过。中途的"~$106"(诚实但超支)和旧"~$32"(stale)均已作废。(origin 同步已于 2026-06-01 经 PR #9 完成)

## 关键约束

- **成本纪律**:证据强制不跳过但按 ticker+假设缓存;demo 前预跑缓存,现场不 live 跑 deepresearch(预算 $100 + 100 calls)
- **诚实定位**:显式化隐含假设 + lens 透明,不宣称还原唯一真相;查不到证据诚实留空绝不编造
- **MVP 范围**:只做 Market + Portfolio 两种 source;Analyst/Opinion 推 V2
