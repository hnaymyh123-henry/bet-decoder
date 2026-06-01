<!-- FROZEN 2026-05-28 · Phase 1 产品对齐定稿 · 49 决策 · Phase 2 技术拆解唯一输入 -->
<!-- 工作底稿见 PRD-draft.md(含逐题对齐过程);术语见 docs/glossary.md;愿景见 BET_DECODER_VISION.md -->

# Bet Decoder — PRD v1.0 (Phase 1 冻结版)

> 本文档由 `/dev` Phase 1 模块化对齐冻结而成(2026-05-28),5 模块 49 条决策全部 LOCKED。
> 是 Phase 2 技术拆解的唯一权威输入。改动需走变更评审,不在主对话随手改。

---

## 一句话定位

Bet Decoder 是投资 bet 的 X 光机:输入任意 bet(市场价 / 分析师目标价 / 推文 / 持仓),反向解码出它隐含相信什么,多张卡并列对比,AI 做跨卡综合。

**两个正交核心 primitive**:
- **BetCard** 答 **What** —— 一个 bet 解码后的结论快照
- **ActivityEvent** 答 **How** —— agent 一步步推出该结论的语义推理流

## 模块清单与依赖

```
1. Bet Card 数据模型  ← 基础, 其他全依赖
2. Decoder Engine     ← 依赖 1
3. 跨卡综合           ← 依赖 1 + 2
5. Agent 活动流       ← 跨 2 + 3 + 4 (SSE 横切基础设施)
4. 工作台前端         ← 依赖 1, 通过 API 调 2/3, 消费 5
```
推荐实现顺序:1 → 2 → 3 → 5 → 4(数据底座先行,前端最后接合)

---

## 数据模型总览(冻结态,已并入全部回填)

> 所有 schema 由 Module 1 拥有。下表是 5 模块对齐后的**最终目标 schema**。

| 表 | 状态 | 关键列 | 服务于 |
|---|---|---|---|
| `bet_cards` | 新增(信封) | `card_id, subject, source_type, card_kind('single'\|'portfolio'), source_ref, series_key=(subject,source_type), created_at, run_id(FK runs, NULL=组合)` | M1 卡身份 |
| `runs` | 现有 + 补列 | 补 `anchor_price` + `anchor_type('market'\|'analyst_pt'\|'opinion')`;implied driver 子表带 **蒙特卡洛 band p25/p75**(R2) | 单股卡复用 |
| `portfolio_holdings` | 新增 | `card_id, ticker, weight_pct, run_id` | 组合卡持仓 |
| `theme_exposures` | 新增 | `card_id, theme, exposure_pct, contributing_tickers, is_concentration_risk` —— **组合卡 + 单股卡(R1,anchor mode 产出)共用** | M3 同源比对 |
| `llm_cache` | 现有 | 增 category=`"synthesis"`(SynthesisResult blob, key=卡集合 hash) | M3 综合缓存 |
| `activity_logs` | 新增 | `job_id, source_ref, events_json, created_at` —— ActivityEvent 日志 blob | M5 时序回放 |

> 命名说明:旧设计里这张暴露表叫 `portfolio_exposures`(仅组合卡)。因 R1 要求单股卡也产出主题暴露,冻结版统一为 **`theme_exposures`**(card 级,两种卡共用)。Phase 2 建表用此名。

## 公共接口契约(冻结态)

```python
# Module 1 — 被动存储层
BetCard                                   # 统一类型,含 single/portfolio 两 sub-type
save_card / get_card / list_cards / delete_card
card_to_json / card_from_row

# Module 2 — 解码(被动返回,不自存)
decode_bet(source_type, source_input, lang, emit=None) -> BetCard
#   emit: 可选 ActivityEvent 回调(M5 注入);None=不流式(batch/test)

# Module 3 — 跨卡综合(纯消费,只吃 card_id,不自存)
synthesize_cards(card_ids, lang, emit=None) -> SynthesisResult

# Module 5 — 活动流(横切)
#   emit(ActivityEvent) 回调契约 + SSE 端点 + activity_logs 落库 + 回放
# Module 4 — 工作台前端,通过 API 调 M2/M3,SSE 消费 M5
```

两个产物结构:
```
SynthesisResult { card_ids, generated_at,
  headline_insight: {text, relation_id} | null,
  relations: [{id, type, card_a, card_b, strength(strong|medium|weak),
               shared_assumption, detail, comparable:bool}],
  narrative: str | null }

ActivityEvent { job_id, seq, t_offset_ms,
  source:{kind:"decode"|"synthesis", card_id|card_ids, subject},
  phase, kind:"decision"|"computation"|"evidence"|"relation",
  text, payload:dict|null, terminal: null|"done"|"error" }
```

---

## 模块 1:Bet Card 数据模型 ✅ LOCKED

**边界**:Bet Card 的 schema / 序列化 / 持久化。被动存储层,不碰生成/综合/渲染。

**Big Picture**:① 卡分两类(单股/组合各有专属指标,不强行合并)· ② 统称 "Bet Card" 下分两 sub-type · ③ 单股 Market/Analyst/Opinion 三 source 共用一个 schema,bet 值可空(兼容 Opinion 残缺)· ④ 形态分化:单股=紧凑卡片(可并列),组合=仪表盘(展开全景+主题暴露+集中风险)· ⑤ 被动存储层 · ⑥ 接口见上。

**行为**:⑦ 不可变快照,每次解码=新卡 · ⑧ 唯一 `card_id` + `series_key=(subject,source_type)` 分组 + 去重(Market 卡按交易日收盘粒度,一天最多一张)· ⑨ 新鲜度按天派生(卡日期≠今日最新交易日→历史快照),不存卡上不比价 · ⑩ 被动:M2 推卡进/消费方拉卡出,永不调上游。

**细节**:⑪ 方案 C 混合复用 —— 新增 `bet_cards` 信封;单股卡 `run_id`→复用 `runs`+子表;组合卡新建表;现有 13 表仅 runs 补 2 列 · ⑫ `portfolio_holdings` + `theme_exposures`(见数据模型总览)· ⑬ runs 补 `anchor_price`+`anchor_type`(老数据回填 anchor_price=current_price, anchor_type='market')· ⑭ 组合编辑草稿态(可变工作草稿,仅"解码/保存"固化成不可变快照)。

**已验证**:`bet_card_sample.html`(真实 NVDA:CAGR 50%/WACC 6.2%/终值利润率无解)+ `portfolio_card_sample.html`(8 持仓,76% AI infra 集中风险)。
**Phase 2 补充**:① runs 子表带蒙特卡洛 band 列(R2)· ② `theme_exposures` 支持单股卡行(R1)· ③ 新增 `activity_logs` 表(M5)。

---

## 模块 2:Decoder Engine ✅ LOCKED

**边界**:任意 source → 完整 BetCard(含证据)。orchestrate 三步,被动返回不自存。
**核心灵魂**:**frame-adaptive agentic decode** —— agent 按公司挑合适估值 lens → 反解隐含业务指标 → 找证据。`reverse_dcf.py` 降级成 DCF lens 下的一个工具。

**Big Picture**:① 三段式:前置适配器(source→锚价)+ 共享核心(反解)+ 后置组装(→BetCard)· ② 诚实定位(显式化隐含假设 + lens 透明,**不宣称还原唯一真相**);MVP 只做 Market+Portfolio,Analyst/Opinion 推 V2 · ③ 三步:选 lens(agentic)→ 反解(代码)→ 找证据(agentic),lens 选择走约束决策树保可复现 · ④ Step 3 证据**强制不跳过**(按 ticker+假设缓存+demo 前预跑;8 新票组合首解≈$24);证据归 M2(单卡级)· ⑤ 单入口 `decode_bet(...)→BetCard`,不自存,流式版交 M5。

**行为**:⑥ lens 注册表(可插拔)种子 7 个(DCF/PE/PS/EV-EBITDA/P-FCF/P-B/PEG);一卡=1 primary + 1-2 个交叉验证 lens(分歧本身是 Aha)· ⑦ 失败:选 lens 失败→兜底 PS(无收入→"数据不足");反解无解→fallback 下个 lens→全失效进 anchor mode;证据查不到→诚实留空**绝不编造** · ⑧ 锚 lens 第二梯队(TAM/期权/类比/叙事)· ⑨ **anchor mode 对 AI 复合体(GPU/存储/光模块/AI 应用)作 primary**(决策树前置判断叙事/主题定价→是则 anchor 主导、传统 lens 降交叉参考)· ⑩ anchor 输出=基础业务价值 + 叙事/期权成分**加总对账到现价**,每成分={claim,隐含金额,隐含假设/概率,证据},复用泛化 Bet schema。

**Phase 2 产物补充(供 M3)**:R1 单股卡产出 `theme_exposures` 行(anchor mode 识别叙事成分时)· R2 DCF driver 带蒙特卡洛 band(p25/p75)。
**V2 延后**:宏观流动性作分母变量(需真宏观数据+流动性 beta,**不做拍脑袋估算**)· 模糊 Opinion 文本抽取(随 Opinion source 一起做)。

---

## 模块 3:跨卡综合 ✅ LOCKED

**边界**:跨卡关系引擎 + 综合叙事生成器。纯消费方(只读卡/不改卡/不生成卡)。**多卡间横向关系**,区别于 M2 交叉验证(单卡内纵向)。

**Big Picture**:① **同源(看似无关两卡实押同一假设)是核心 Aha** · ② 输出=图谱+叙事双层(narrative 每句挂 relation_id 可下钻;headline_insight 当 demo 字幕);**结论必须能对账回证据** · ③ 手动触发 + 卡集合 hash 缓存;**全程 chat mode**(不调 Deep Research)· ④ 单入口 `synthesize_cards(card_ids,lang)`,不自存,**只吃 card_id**。

**行为**:⑤ 五关系按卡配对**自动路由**(同 subject 异 source→共识/分歧/矛盾;异 subject→同源;同 series 异时间→漂移)· ⑥ 强度:同标的=同 lens→driver 直接比,**蒙特卡洛 band 宽当强/中/弱尺子**(非 DCF lens 注册表给简单阈值);输出**仅强/中/弱**;同源用几何均值 √(暴露A×暴露B);同源主题对齐走 chat 模糊匹配(嵌在综合调用内,不额外加 call)· ⑦ 缓存=卡集合 hash,增删全量重跑,过期不失效;数量下限 2、无硬上限、路由+TopK 收敛、>8 软提示 · ⑧ 失败:无关系→headline=null **绝不凑牵强同源**;driver 不可比→降级定性+标注 `comparable=false`;chat 坏→重试 1 次→退回纯图谱。

**细节**:⑨ SynthesisResult(见接口契约)存 `llm_cache`(category="synthesis"),不进 bet_cards · ⑩ 代码复用(client.py chat / cache_get-put / 新建 cross_card_synthesizer.md);**旧 `run_synthesizer` 被泛化取代,Phase 2 清理**(工程细节)。

---

## 模块 5:Agent 活动流 ✅ LOCKED

**边界**:活动流事件协议 + SSE 管道 + M2/M3 埋点。横切基础设施,不碰 decode/综合/渲染/建表。把 W2/W3 临时 SSE 正式化,顺收 bug #34。

**Big Picture**:① 定位=**"过程透明"价值载体,非装饰进度条**(卡给结果,活动流给过程)· ② **live + 持久化可回放,带时序模拟**(呼应"预跑缓存 demo 不烧钱"——只有可回放,缓存卡现场才有流可看)· ③ 事件粒度=**决策级语义推理步**(非机械进度),单档 + `kind` 标签 · ④ 范围=**统一工作台 feed**(非每卡小流),事件标 source,SSE 传输,emit 回调注入 M2/M3。

**行为**:⑤ 回放按需触发(cache miss→live;cache hit→秒出结果+回放入口;demo 主讲控节奏);**必有终态事件**(done/error 带人话);断线从持久化补流 · ⑥ 并发=**串行+排队**(一次演一个连贯推理;连贯性>并行速度)。

**细节**:⑦ ActivityEvent(见接口契约)存 `activity_logs` 表;回放=按 seq 重发+t_offset_ms 还原节奏。代码复用旧 `/stream/evidence` SSE + 前端 Agent log(工程细节)。

---

## 模块 4:工作台前端 ✅ LOCKED

**边界**:纯展示+交互层,API 调 M2/M3 + 消费 M5 流,无业务逻辑。复用扩展 `app.html`,从"单股研报页"→"多卡工作台"。

**决策**:① 展示交互层无业务逻辑 · ② 起点=扩展 app.html · ③ **严守 `pricelens_design_system.md`**(纸感/oxblood/Geist/无蓝紫渐变/>3 行用表格/数字右对齐 mono)· ④ **三区布局**:主画布(多卡并列)+ 右侧活动流 feed + 底部综合面板(展开式)· ⑤ 主流:输入框贴 bet→选 source type→decode→右侧 live 流→卡落画布 · ⑥ 综合流:选≥2 卡→综合→底部 headline 字幕+可下钻图谱+叙事 · ⑦ 卡形态继承 M1(单股紧凑/组合仪表盘)· ⑧ 复用 `bet_card_sample.html`/`portfolio_card_sample.html` 渲染组件 · 综合无关系→优雅空态(承接 M3)。

---

## 跨模块 V2 / 已知延后

| # | 项 | 归属 | 为何延后 |
|---|---|---|---|
| V1 | 宏观流动性作分母端变量 | M2 | 需真宏观数据+流动性 beta,不做拍脑袋估算 |
| V2 | 模糊 Opinion 文本抽取 + Analyst/Opinion source | M1→M2 | reverse DCF 对它们不诚实,MVP 只做 Market+Portfolio |
| V3 | 受控主题词表(替代 chat 模糊匹配) | M3 | 同源主题对齐 MVP 用 chat 模糊匹配,稳定性优化留 V2 |
| V4 | SSE 断线 Last-Event-ID 精细重连 | M5 | MVP 从持久化事件补流即可 |
| V5 | SynthesisResult 升级一等公民表(保存/分享综合) | M3→M1 | MVP 用 llm_cache 缓存够用 |

## Phase 2 拆解须知

- **实现顺序**:1 → 2 → 3 → 5 → 4。M1 数据底座 + M2 解码核心是关键路径。
- **回填合并进首次实现**:R1/R2(M2 产物)、`theme_exposures` 统一命名、`activity_logs` 表都直接在各模块首次落地时一并做,不当独立任务。
- **待清理工程债**:旧 `pipeline.py::run_synthesizer`(被 M3 取代)· bug #34(SSE agent_step 后卡住,M5 收掉)。
- **成本纪律**:证据强制不跳过但按 ticker+假设缓存;demo 前预跑 + 缓存回放,现场不 live 跑 deepresearch。

---

> 配套文档:`BET_DECODER_VISION.md`(愿景/5 幕 demo/Aha 矩阵)· `docs/glossary.md`(~24 术语)· `pricelens_design_system.md`(视觉契约)· `PRD-draft.md`(逐题对齐工作底稿)。
