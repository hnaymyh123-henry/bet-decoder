# Feature Log · Bet Decoder

> 已交付功能 + 技术债登记。按迭代追加。权威规格见 `PRD.md`;架构见 `PROJECT_CONTEXT.md`。

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

## 技术债登记(待下轮处理)

| # | 项 | 来源 | 影响 | 目标 |
|---|---|---|---|---|
| TD1 | **band-ruler 真实读卡路径失效**:`get_card` 不重建 decode_detail + traditional 卡 run_id 未落 → 蒙特卡洛 band 拿不到,综合走相对差距 fallback | Phase 4 review (synthesizer.py:188/203/212) | 中(综合精度略降,不崩) | 下一轮:持久化最小 driver 视图(lens/value/band)到 bet_cards 或 runs 子表 |
| TD2 | **`sse.py` 旧 mock SSE 端点**(`stream_evidence_mock`)疑似被 M5 `activity.py` 真 SSE 取代,api.py 仍 import | Phase 5 清查 | 低 | 下一轮:确认 M4 不再用 mock 流后 git rm |
| TD3 | **Windows gbk 控制台编码**:`verify_*.py` / `prerun_demo.py` 打印中文/emoji 在 gbk 控制台崩(需 `PYTHONIOENCODING=utf-8`);prerun_demo 已内置 stdout reconfigure,verify 脚本未 | Phase 4/5 | 低(加环境变量即绕过) | 下一轮:给 verify 脚本统一加 stdout reconfigure,或 README 注明 |
| TD4 | **旧 W1 测试脚本** `test_a_evidence.py` / `test_b_chat.py`:pivot 前 W1 验证脚本,无 import,已被 verify_m* 取代 | Phase 5 清查 | 低 | 下一轮:确认无用后删 |
| TD5 | **`pricelens_prd.md`** 被 `PRD.md` 取代,仅存 pivot 前历史细节 | Phase 5 清查 | 低(历史归档) | 待定:保留作历史,或归入 docs/archive/ |
| TD6 | **AI 真实输出质量未验证**:Phase 4 验的是代码路径正确性,真实 LLM 解码/证据/综合的产出质量从未跑过;`prerun_demo.py --execute` 是首次真实 smoke | Phase 4 | 中(demo 前必验) | demo 前:用户跑 --execute(2026-06-01 重算诚实成本 ~$106,超 $100 预算 → 先选削减杠杆,见 CLAUDE.md Status)兼作真实 smoke |
| TD7 | **OPS:项目 `.env` 含真 MIROMIND_API_KEY** → 任何走默认 hunter 的 decode 打真实 API;脚本须 `MIROMIND_API_KEY=""` 或靠 stub | Phase 4 | 中(成本/挂起风险) | 持续:运行脚本注意屏蔽 key;verify_m8 已自我屏蔽 |
