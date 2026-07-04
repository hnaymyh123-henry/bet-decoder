# Feature Log · PlayInsight

> 已交付功能 + 技术债登记。PlayInsight 是当前 v2 successor workspace;历史 Bet Decoder/PlainSight/PriceLens 名称只作 traceability。权威 v2 规格见 `PRD.md` + `docs/SPEC_A-F`;架构见 `PROJECT_CONTEXT.md`。

---

## 已完成

### 迭代 2026-05-28 — Bet Decoder 真后端 v1.0(Phase 1→5 + Phase 4 独立 review)

| 模块 | 交付 | 验证 |
|---|---|---|
| M1 数据层 (`db.py`) | bet_cards/portfolio_holdings/theme_exposures/activity_logs 表 + runs anchor 列 + BetCard DAO(save/get/list/delete + json/row 序列化)+ 连接生命周期(ensure_schema/get_connection/contextmanager)+ 日去重乐观插入 | verify_m1 ALL · phase4-W1 31 |
| M2 解码 (`decoder.py` + `evidence.py`) | decode_bet 三段式 + 7 传统 lens 注册表 + frame-adaptive 确定性决策树 + 交叉验证 + anchor mode(锚 lens + AI 复合体 primary + 对账现价)+ R1 主题暴露 + R2 蒙特卡洛 band + Step3 证据(强制不跳过、sha1 跨进程缓存、诚实留空、成本守卫) | verify_m2 30 · m3_anchor 19 · m4_evidence 15 · phase4-W2 25 |
| M3 跨卡综合 (`synthesizer.py`) | synthesize_cards + 五关系自动路由 + 强/中/弱(band 当尺 + 符号分歧→contradiction)+ 同源几何均值 + 主题对齐 chat 模糊匹配(memoize + 调用上限)+ SynthesisResult 存 llm_cache | verify_m5_synth 40 · phase4-W3 20 |
| M5 活动流 (`activity.py` + `api.py` SSE) | ActivityEvent 协议 + emit sink(线程安全)+ 持久化 activity_logs(后台线程自有连接)+ 时序回放 + JobQueue 串行 + 断开取消 + bug#34 修复 | verify_m6 39 |
| M4 工作台前端 (`app.html` + `api.py` REST) | 三区布局(多卡画布 + 右侧活动流 feed + 底部综合面板)+ decode/综合交互 + SSE 消费 + 复用样例卡 + 严守设计系统 + 5 REST 端点(输入校验 + 统一错误格式) | verify_m7 14 |
| 收口 (`verify_m8_integration.py` + `prerun_demo.py`) | 端到端集成 e2e + 5 幕 demo 预跑脚本(默认 dry-run,--execute 真跑) | verify_m8 35 |
| 发布脚手架 | README.md · MIT LICENSE · Dockerfile · .dockerignore | — |

**质量**:Phase 4 独立对抗 review(3 agent)抓出并修复 5 CRITICAL + ~12 SHOULD-FIX 真实路径 bug。全套 ~267 断言全绿。

---

### 迭代 2026-06-01 — Agentic 层(对话式可改写卡 + agent 决策解码)

> 起因:用户判断"产品还是像固定网页,不是 agentic"。根因(结构性非表面):decode 是确定性决策树,LLM 只是narration 子程序,"活动流"是那棵树在自述 = theater 非 agency;流程一锤定音(input→卡),无法追问一张卡。本迭代加入真正的 agentic 层。

| 阶段 | 交付 | 验证 |
|---|---|---|
| A 持久化 decode_detail (`db.py`) | schema v3 幂等迁移(decode_detail_json/derived_from/derivation_kind/derivation_json 列 + idx_bet_cards_derived + **日去重索引重谓词排除衍生卡** `AND derived_from IS NULL`);save_card 落库 + card_from_row 重建 decode_detail/lineage;`card_to_json_full` + `build_card_display`(_display 投影,真卡脱离 thin 分支);嵌套 ThemeExposure asdict 序列化 | verify_decode_detail_persistence 15 |
| B 工具调用 client + 工具注册表 (`client.py` + `agent_tools.py`) | `call_chat_tools`(OpenAI tools 扩展信封 + `_CHAT_TOOLS_IMPL` stub seam + `ToolCallingUnsupported`);`Tool`/`TOOL_REGISTRY`/`dispatch`(校验 + web-gate 诚实留空 + emit ActivityEvent + 永不抛);8 工具**包装既有 fn 无重实现** | verify_client_tools 7 · verify_agent_tools 16 |
| C 编排器(agentic 解码,PRIMARY)(`orchestrator.py` + `decoder.py` hook) | `decode_bet_agentic` 工具调用循环(max_rounds=8 · 温度 0 · 调用上限)→ `submit_decode_plan` → `decode_bet(_plan_override=)` **复用既有装配器(parity by construction)** → 标记 `mode=agentic_*` + `agent_trace`;气密 try/except 回退确定性 `decode_bet` | verify_orchestrator 12 |
| D 对话式 Q&A + 溯源改写 (`orchestrator.py` + `api.py`) | `answer_followup`(why / what-if / compare / bear-case 工具子集);`propose_revision` 元工具跑 what-if 返回 before→after diff(**不落库**,确认后才存);`build_revised_card` 建新衍生卡(derived_from + derivation diff,**父卡不变**);`POST /ask`(job→activity 回放)+ `POST /revise`(离线可用) | verify_qa_revise 11 |
| E 前端 (`app.html`) | 真卡走 `_display` 富渲染;每卡"讨论/DISCUSS"块(按字重区分人/机 + mono 大写标签非气泡 + ▾工具调用 disclosure);WHAT-IF 修正提案 before→after 表 + "保存为衍生卡";衍生卡"← 衍生自"徽章 + 挂在父卡下;严守 `pricelens_design_system.md` | verify_m7 14 |
| F 收口 (`verify_agentic_e2e.py`) | TestClient e2e:agentic 解码 plan 落地 + 持久化 detail + **无损回读(TD1)**· /ask job→SSE 回放 · /revise 溯源衍生卡 + 父卡不变 + 同日共存 · 离线护栏(/ask 503,/revise 通)· 气密回退 | verify_agentic_e2e 22 |

**载荷设计**:(1) **parity-by-construction**——agent 只选 plan,装配复用既有 assembler → 卡/decode_detail 形状与确定性解码逐字节一致(持久化/序列化/综合/前端全不变);(2) **不可变快照保留**——改写=新衍生卡而非静默 mutation,auditability 是产品论点;(3) **真实 agency 非 theater**——agent 的决策/工具调用经既有 activity SSE 真实流出;(4) **web 诚实**——非搜索 provider 上 evidence/narrative 工具诚实留空,编造来源是发布阻断项。**provider 可配**:默认 miromind(公开仓);测试可切 TokenDance/DeepSeek V4 Pro(OpenAI 兼容 function-calling,无 web 搜索)。**全套 6 离线套件 +83 断言全绿**(decode_detail 15 · client_tools 7 · agent_tools 16 · orchestrator 12 · qa_revise 11 · agentic_e2e 22);累计 ~373 断言全绿。CI 用 `verify_*.py` glob 自动纳入新套件。**真实冒烟 `smoke_agentic.py`(DeepSeek V4 Pro)待用户跑**(需 TokenDance key + 少量花费;必须验并行 tool_call→匹配 role:tool 结果)。

---

## 技术债登记(待下轮处理)

| # | 项 | 来源 | 影响 | 目标 |
|---|---|---|---|---|
| ~~TD1~~ | ✅ **已解(根因)**:Agentic Phase A 让 `get_card` 重建 `decode_detail`(蒙特卡洛 band 随卡落库 → 回读卡现在拿得到 band),`verify_decode_detail_persistence` 证无损往返。**剩余**:`synthesizer` 仍走相对差距 fallback,需改读持久化 `decode_detail` 里的 band 才能用上"band 当尺"——降级为 TD1' 小跟进(精度优化,非阻断) | Phase 4 review → Agentic Phase A 修 | 低(回读卡已可被追问/改写;综合精度可再提) | 下一轮:`synthesizer` 从 reloaded `decode_detail` 读 band 替代相对差距 fallback |
| TD2 | **`sse.py` 旧 mock SSE 端点**(`stream_evidence_mock`)疑似被 M5 `activity.py` 真 SSE 取代,api.py 仍 import | Phase 5 清查 | 低 | 下一轮:确认 M4 不再用 mock 流后 git rm |
| TD3 | **Windows gbk 控制台编码**:`verify_*.py` / `prerun_demo.py` 打印中文/emoji 在 gbk 控制台崩(需 `PYTHONIOENCODING=utf-8`);prerun_demo 已内置 stdout reconfigure,verify 脚本未 | Phase 4/5 | 低(加环境变量即绕过) | 下一轮:给 verify 脚本统一加 stdout reconfigure,或 README 注明 |
| ~~TD4~~ | ✅ **已解**:旧 W1 测试脚本 `test_a_evidence.py` / `test_b_chat.py` 已不在仓库 | Phase 5 清查 | 低 | ✅ 已删除 |
| ~~TD5~~ | ✅ **已删**:`pricelens_prd.md`(改名前旧 PRD,已被 `PRD.md` 取代)在仓库清理时移除 | Phase 5 清查 | 低(历史归档) | ✅ 已删除 |
| TD6 | **AI 真实输出质量未验证**:Phase 4 验的是代码路径正确性,真实 LLM 解码/证据/综合的产出质量从未跑过;`prerun_demo.py --execute` 是首次真实 smoke | Phase 4 | 中(demo 前必验) | demo 前:用户跑 --execute(2026-06-01 成本重算并压回预算内 ~$38.61,组合逐股证据已砍 → decoder 对持仓腿传 `_SKIP_EVIDENCE`)兼作真实 smoke |
| TD7 | **OPS:项目 `.env` 含真 MIROMIND_API_KEY** → 任何走默认 hunter 的 decode 打真实 API;脚本须 `MIROMIND_API_KEY=""` 或靠 stub | Phase 4 | 中(成本/挂起风险) | 持续:运行脚本注意屏蔽 key;verify_m8 已自我屏蔽 |

---

### 迭代 2026-07-04 — 方向升级 v2.0 · 多框架预期分解引擎 + agentic 呈现（PRD 收口，代码未动）

> 起因:v1.0 是单框架(反向 DCF)纯诊断链 + 固定模板卡 → 一个框架只照亮一条 channel + 研报感结构根因。本次把核心升级为"用一栈正交框架把市场预期分解成多条 channel、跨框架对账、agent 驱动可操纵的调查",决策纪律层挂其上。

| 项 | 交付 | 状态 |
|---|---|---|
| **PRD v2.0 定稿** | `PRD.md` 覆盖升级(12 章骨架):§0 定位 · §1 原语(Decode/调查 + series/lineage 双轴)· §2 预期分解栈(5 channel + 高度栈 + 四原则)· §3 期权分布完整规格 · §4 决策层 Trade Plan + 可插拔执行 · §5 年轻化呈现 · §6 主动监控 · §7 模型分配 · §8 数据模型(panel_observations 新表 + decode_detail v3→v4)· §9 落地节奏 · §10 诚实边界 · §11 codebase 存活映射 · §12 开放项 | ✅ 大框架 LOCKED / 细节 OPEN |
| **文档收口** | 4 份旧文档归档 `docs/archive/`(DIRECTION v0.3 / TRADING_AGENT_PRD v0.1 / BET_DECODER_VISION v0.7 / pricelens_design_system DEPRECATED)+ 原位置留 stub 指向 PRD v2.0;README Documentation 段同步更新 | ✅ |
| **新视觉稿** | `mockup_v2_young.html`(年轻化交易 App vibe,深色/卡片流/一屏一焦点) | ✅ vibe 确认 |

**大框架 LOCKED 的开放项(§12)**:O6 qveris 数据 API 形态(待探·地基)/ O7 成本模型 / O9 命名定位 / O10 合规边界 / O11 监控细节 / 决策层参数定值 / 一期是否含候选 ticker 解码。

**落地节奏(§9 LOCKED)**:⓪ 漂移脊椎 → ① 期权隐含分布(keystone)→ ② consensus 修正 → ③ 轻量高度归因。技术地基 = O6。

**代码状态**:所有 `.py` 停在 2026-06-05(Agentic 层),v2.0 代码重构尚未开始。`reverse_dcf`/`narrative`/`intelligence`/血缘/activity/`agent_tools`/`orchestrator`/`decoder` 全部存活升级(见 §11 映射),主要重做 `app.html`。

**新增技术债**:

| # | 项 | 影响 | 目标 |
|---|---|---|---|
| ~~TD8~~ | ✅ **已解**:PROJECT_CONTEXT.md / CLAUDE.md / README.md 已改为 PlayInsight v2 successor context,并指向 `PRD.md` + `docs/SPEC_A-F` 为当前权威;旧 Bet Decoder/PriceLens/PlainSight 文档仅作 traceability | 低 | ✅ 已更新 |
| TD9 | **README.md 主体描述仍为 v1.0**(单框架/X光机/严守 design_system) | 低(文档列表已修,主体待代码落地后重写) | S3 前端落地后重写 README |
