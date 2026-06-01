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
| LLM | **provider 可配**(`client.py`):默认 **MiroMind**(agentic deep-research,自定义 SSE,公开仓默认);测试切 **TokenDance/DeepSeek V4 Pro**(OpenAI 兼容 chat + **function-calling**,无 web 搜索)。chat mode(narration/critic/synth)+ deep research mode(evidence/anchor lens)+ **tool-calling mode**(agentic decode / Q&A) | 单 client 多 provider;`PROTOCOL`/`WEB_SEARCH_CAPABLE`/`PRICING` 按 provider |
| API 设计 | REST;`/api/cards`、`/api/decode`(agentic PRIMARY)、`/api/cards/{id}/ask`、`/api/cards/{id}/revise`、`/api/synthesize`、`/stream/...`(SSE);统一错误 `{error_code, message}` | 见 `API_CONTRACT.md` |
| API Contract | **出 `API_CONTRACT.md`** 锁前后端接口 | M4 通过 API 调 M2/M3,避免扯皮 |
| 代码风格 | Python 3.11 + PEP 8;前端跟 mockup 既有约定 | 沿用 |

## 构建路径决策(2026-05-28)

**直接建真后端** —— 按冻结 PRD 一次性实现 M1→M2→M3→M5→M4,接 reverse_dcf/SQLite/MiroMind API,无 throwaway hardcoded 原型。(vision 文档的 4.5 天 hardcoded 路径已弃用。)

## Agentic 层(2026-06-01,在真后端之上叠加)

> 起因:确定性决策树 + LLM 仅作子程序 → 产品"像固定网页非 agent"。加入真正的 agentic 层(用户选定:对话式可改写卡 + agent 决策解码,二者一起做)。

| 项 | 决策 | 理由 |
|---|---|---|
| Agentic 解码 | `orchestrator.decode_bet_agentic`:LLM 工具调用循环决定 X 光方案 → `submit_decode_plan` → `decoder.decode_bet(_plan_override=)` 复用既有装配器 | **parity-by-construction**:卡/`decode_detail` 形状与确定性解码逐字节一致,持久化/序列化/综合/前端全不变 |
| 默认 PRIMARY | `POST /api/decode` 默认 `agentic:true`;provider 不支持工具调用 / 异常 / 离线 → 气密回退 `decode_bet` | 真实 agency 但永不崩 |
| 可改写卡 | 追问经 `answer_followup`;what-if 经 `propose_revision`(返回 before→after diff,**不落库**);确认后 `build_revised_card` 建**新衍生卡**(`derived_from`+derivation) | 不可变快照:改写=新卡非 mutation,auditability 是产品论点 |
| 持久化 keystone | schema **v3**:`bet_cards` +`decode_detail_json`/`derived_from`/`derivation_kind`/`derivation_json`;日去重索引重谓词排除衍生卡 | 回读卡可被追问/改写(修 TD1 根因) |
| 工具注册表 | `agent_tools.py`:8 工具**包装既有 fn 无重实现**;`dispatch` 校验 + web-gate 诚实留空 + emit ActivityEvent + 永不抛 | 复用 + web 诚实(非搜索 provider 不编造来源) |
| 真实 agency | agent 决策/工具调用经既有 `activity.py` SSE 流出(非固定树自述) | 过程透明=产品价值载体 |

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

- **GitHub repo**:https://github.com/hnaymyh123-henry/bet-decoder (private)。✅ **origin 已同步**:2026-06-01 经 PR #9 rebase-merge 到 `f26fc47`;此后 Agentic 层 Phase A-F 已直接推 master(narrative 收尾 + 6 phase commit)。日常用 `git -c credential.helper='!gh auth git-credential' push origin master` 一次性凭证助手推(不改持久 git config)
- **Phase 1-5 + Phase 4 code review + 市场叙事层全部完成**(latest 2026-06-01):真后端 M1-M5 + 前端 + 发布脚手架(README/LICENSE/Dockerfile) + 市场叙事层(narrative.py)。**12 套件 ~290 断言全绿**(M1-M8 + phase4-W1/W2/W3 + narrative 19)。Phase 4 抓修 5 CRITICAL + ~12 SHOULD-FIX 真实路径 bug。
- **Agentic 层(2026-06-01)叠加完成**:Phase A-F 全落地,新增 `orchestrator.py`(agentic 解码 + Q&A + 溯源改写)· `agent_tools.py`(工具注册表)· client.py 加 `call_chat_tools`/stub seam · db.py schema v3(decode_detail 持久化 + 卡 lineage)· api.py 加 `/ask` `/revise` + `/decode` agentic PRIMARY · app.html 加每卡讨论块 + WHAT-IF 修正提案 + 衍生卡挂轨。**6 新离线套件 +83 断言全绿**(decode_detail 15 · client_tools 7 · agent_tools 16 · orchestrator 12 · qa_revise 11 · agentic_e2e 22)→ **累计 ~373 断言全绿**。CI `verify_*.py` glob 自动纳入。
- **完整模块**:db.py(M1 + v3 持久化)· decoder.py + evidence.py(M2 + `_plan_override` hook)· synthesizer.py(M3)· activity.py + api.py(M5/SSE+REST)· app.html(M4 + 讨论/改写)· **orchestrator.py + agent_tools.py(Agentic)**· prerun_demo.py(demo 预跑)· verify_m1..m8 + verify_phase4_w1/w2/w3 + verify_narrative + **verify_{decode_detail_persistence,client_tools,agent_tools,orchestrator,qa_revise,agentic_e2e}**
- **技术债**:见 `docs/feature-log.md` 技术债登记(~~TD1 已解根因(Phase A,回读卡 band 已落库;剩 synthesizer 消费 band 改造)~~ / TD2 sse.py 旧 mock / TD3 gbk 编码 / TD4 W1 旧脚本 / TD5 pricelens_prd 历史 / TD6 AI 真实质量未验 / TD7 .env 真 key ops)
- **下一步(需用户)**:demo 前 `python prerun_demo.py --execute`(兼首次真实 LLM smoke)。**2026-06-01 成本重算并压回预算内**:prerun 现 **~$26.47**(证据 $19.26 = NVDA+TSLA 单股 ×3 条 · flagship 市场叙事 $4.00 = 2 张单股 · 综合 $3.21),**✅ 在 $100 预算内**(dry-run exit 0)。**已按 MiroMind 控制台真实账单校准(2026-06-01 调用日志)**:旗舰 deepresearch 真实 $0.80–$1.60/次(原 $8.07 是 token footprint 回拟、高估 ~5-7 倍)→ `COST_PER_EVIDENCE_FLAGSHIP` 钉成 $2.00;mini $3.21 确认为安全保守值(真实 $0.32–$5.93,均值 ~$2.1)。(MiroMind 走预付费资源包 → 那些调用显示 $0.00,外加 USD 余额。)所选杠杆:**组合逐股证据不再 hunt**——decoder 对持仓腿传 `_SKIP_EVIDENCE`(组合的信号=构成+跨卡综合,不是逐股 deep research;要看某持仓的证据就把它当单股卡解码)。单股卡的 Step 3 仍强制不跳过。中途的"~$106"(诚实但超支)和旧"~$32"(stale)均已作废。(origin 同步已于 2026-06-01 经 PR #9 完成)

- **下一步 · Agentic 真实冒烟(需用户)**:`smoke_agentic.py`(待建/已建)走真实 **DeepSeek V4 Pro**(TokenDance gateway)——一次 agentic 解码(≥1 轮工具调用 + 有效卡 + 非空 trace + 记录成本)+ 一次 what-if 追问 + 一次 revise。**必须验并行 tool_call → 匹配 role:tool 结果**(DeepSeek 工具协议:每个 `tool_call.id` 必须有对应 `role:tool` 否则下一请求 400)。需用户把 `TOKENDANCE_API_KEY` 放进 `.env` + 少量花费;CI 不含(离线套件已覆盖代码路径正确性)。

## 关键约束

- **成本纪律**:证据强制不跳过但按 ticker+假设缓存;demo 前预跑缓存,现场不 live 跑 deepresearch(预算 $100 + 100 calls)
- **诚实定位**:显式化隐含假设 + lens 透明,不宣称还原唯一真相;查不到证据诚实留空绝不编造
- **MVP 范围**:只做 Market + Portfolio 两种 source;Analyst/Opinion 推 V2
