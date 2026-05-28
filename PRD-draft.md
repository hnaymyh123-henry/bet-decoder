<!-- phase1-progress: ALL 5 modules LOCKED at 2026-05-28. Phase 1 complete. Next=Step D freeze PRD → Phase 2. -->
<!-- modules: 1.Bet Card 数据模型 2.Decoder Engine 3.跨卡综合 5.Agent 活动流 4.工作台前端 -->
<!-- backfill to Module 2 (locked): R1 单股卡产出主题暴露%, R2 DCF driver 带蒙特卡洛 band p25/p75 -->
<!-- backfill to Module 1 (locked): 新增 activity_logs 表存 ActivityEvent 日志 blob (Module 5 需) -->

# Bet Decoder — PRD (Phase 1 工作底稿)

> ⚠️ 已冻结 —— 权威版是 **`PRD.md`**(2026-05-28 定稿)。本文件保留为逐题对齐的工作底稿/审计记录,Phase 2 请读 `PRD.md`。
> 基础愿景见 `BET_DECODER_VISION.md`;术语见 `docs/glossary.md`。

---

## 一句话定位

Bet Decoder 是投资 bet 的 X 光机:输入任意 bet(市场价 / 分析师目标价 / 推文 / 持仓),反向解码出它隐含相信什么,多张卡并列对比,AI 做跨卡综合。

## 模块清单与依赖

```
1. Bet Card 数据模型  ← 基础, 其他全依赖
2. Decoder Engine     ← 依赖 1
3. 跨卡综合           ← 依赖 1 + 2
5. Agent 活动流       ← 跨 2 + 4 (SSE 基础设施)
4. 工作台前端         ← 依赖 1, 通过 API 调 2/3
```
对齐顺序:1 → 2 → 3 → 5 → 4

---

## 模块 1:Bet Card 数据模型 ✅ LOCKED (2026-05-28)

**目标与边界**:定义 Bet Card 的 schema / 序列化 / 持久化。拥有 `bet_cards` 表 + CRUD,不碰生成(Module 2)/缓存/综合(Module 3)/渲染(Module 4)。

**关键决策(14 条)**:

### Big Picture (6)
1. **卡分两类**:单股卡 / 组合卡拆开(各有专属指标,强行合并会丢失类型特定指标)
2. **命名**:统称 "Bet Card",下分两 sub-type
3. **单股内部**:Market / Analyst / Opinion 三种 source **共用一个单股卡 schema**;bet 值可空(兼容 Opinion 残缺)
4. **形态分化**:单股 = 紧凑卡片(可并列对比);组合 = 仪表盘(展开全景,带主题暴露 + 集中风险)
5. **模块边界**:Module 1 = schema + 序列化 + 持久化;被动存储层
6. **对外接口**:`BetCard` 类型 + `save/get/list/delete_card` + `card_to_json/card_from_row`

### 行为 (4)
7. **生命周期**:不可变快照,每次解码 = 一张新卡
8. **身份**:唯一 `card_id` + 系列分组 `series_key=(subject, source_type)` + 去重规则;Market 卡按交易日收盘粒度(一天最多一张)
9. **新鲜度**:按天派生判定(卡日期 ≠ 今日最新交易日 → 历史快照);不存卡上,不比价格
10. **数据流**:Module 1 被动存储层,Module 2 推卡进 / 消费方拉卡出,永不调上游

### 细节 (4)
11. **表设计 = 方案 C 混合复用**:新增 `bet_cards` 信封表;单股卡 `run_id` → 复用现有 `runs`+子表;组合卡新建 2 表。现有 13 表不改(除 #13 的 runs 补 2 列)
12. **portfolio 两张表**:
    - `portfolio_holdings (card_id, ticker, weight_pct, run_id)`
    - `portfolio_exposures (card_id, theme, exposure_pct, contributing_tickers, is_concentration_risk)`
13. **runs 加 2 列**:`anchor_price`(反算锚价)+ `anchor_type`('market'|'analyst_pt'|'opinion');老数据回填 anchor_price=current_price, anchor_type='market'
14. **组合编辑草稿态**:编辑组合时有可变工作草稿,只有"解码/保存"才生成不可变快照(避免增删刷屏历史)

**已验证**:两张样例卡用真实 SQLite 数据渲染通过 — `bet_card_sample.html`(单股,真实 NVDA:CAGR 50% / WACC 6.2% / 终值利润率无解)+ `portfolio_card_sample.html`(8 持仓组合仪表盘,76% AI infra 集中风险)。

**关联现有 codebase**:复用 `db.py`(扩展 `bet_cards` + 2 portfolio 表 + runs 补 2 列);复用 `reverse_dcf.py` 算法不变。

**场景压测结果**:
- Opinion 残缺 → 单股卡 bet 值可空("未提及");"怎么从模糊文本抽取"标记为 Module 2 难点
- 同日重复解码 → 去重命中,不生成第二张 ✅
- 组合加票 → 新建快照,旧卡留历史;编辑过程走草稿态 ✅

**已知未决(留给 Module 2)**:模糊 Opinion 文本(如"NVDA 还能涨")如何抽取/是否拒绝解码。

**接口回填(Module 5 压测揪出,本模块已 LOCKED 但需补)**:新增 `activity_logs(job_id, source_ref, events_json, created_at)` 表 —— 存 ActivityEvent 日志 blob,供活动流时序回放。

---

## 模块 2:Decoder Engine ✅ LOCKED (2026-05-28)

**目标与边界**:把任意 source 解码成完整 BetCard(含证据)。orchestrate 三步流程,被动返回卡不自存。不碰持久化/跨卡综合/渲染。

**核心灵魂**:解码不是"对价格跑写死的 DCF",而是 **agent 按公司挑合适的估值 lens → 反解隐含业务指标 → 找证据**。reverse_dcf.py 降级成 DCF lens 下的一个工具。

**关键决策(10 条)**:

### Big Picture (5)
1. **架构**:前置适配器(source→锚价)+ 共享核心(反解)+ 后置组装(→BetCard)三段式
2. **定位 + 范围**:诚实定位(显式化隐含假设 + lens 透明,**不宣称还原唯一真相**);MVP 只做 Market + Portfolio;Analyst/Opinion 推 V2(对它们用 reverse DCF 不诚实)
3. **核心 = frame-adaptive agentic decode**:Step 1 选 lens(agentic / Deep Research)· Step 2 反解隐含指标(确定 / 代码)· Step 3 找证据(agentic);lens 选择走约束决策树保可复现
4. **边界**:Module 2 orchestrate 3 步;**Step 3 证据强制不跳过**(按 ticker+假设缓存 + demo 前预跑扛成本;首次解码 8 新票组合 ≈ $24);证据归 Module 2 内(单卡级,区别于 Module 3 跨卡级);source-type 检测在外(MVP trivial)
5. **接口**:单入口 `decode_bet(source_type, source_input, lang) → 完整 BetCard`;不自存(调用方交 Module 1);流式版留 Module 5

### 行为 (4)
6. **lens 注册表**(可插拔)+ 种子 7 个:
   - 第一梯队(传统):DCF / P/E / P/S / EV-EBITDA / P-FCF / P-B / PEG
   - 一张卡 = agent 挑 1 primary lens + 自动跑 1-2 个适用 lens **交叉验证**(分歧本身是 Aha)
7. **失败处理**:Step1 选 lens 失败→兜底 P/S(连收入都没有→"数据不足");Step2 反解无解→fallback 下个 lens→全失效进 anchor mode;Step3 证据查不到→诚实留空,**绝不编造**
8. **锚 lens 第二梯队**(重度 agentic):TAM 锚 / 期权锚 / 类比锚 / 叙事锚;传统 lens 全失效→切 anchor mode decode 交易者心理锚
9. **anchor mode 升级为 AI 复合体的 primary**:决策树前置判断"是否叙事/主题定价"(GPU/存储/光模块/AI 应用)→ 是则 anchor 主导、传统 lens 降交叉参考;**宏观流动性维度推 V2**
10. **anchor-mode 输出格式**:基础业务价值(传统 lens 算)+ agent 识别的叙事/期权成分,**加总对账到现价**;每成分 = {claim, 隐含金额, 隐含假设/概率, 证据};复用 Module 1 泛化 Bet schema(不新建结构)

**场景压测结果(B.3 全过)**:
- AI 新贵(无盈利无 FCF)→ PS lens;若 PS 隐含增速也荒谬则升级 anchor mode ✅
- 多 lens 打架 → 全展示交叉验证,用户评判 + Module 3 解读分歧含义 ✅
- TSLA 全失效 → anchor mode,锚定趋势/叙事/期权因素 ✅

**关联现有 codebase**:复用 `reverse_dcf.py`(DCF lens)+ `client.py`(Deep Research / chat)+ evidence hunter prompt;新写倍数类 lens 反解(每个 ~5 行算术)+ 锚 lens(Deep Research 驱动)+ lens 决策树 prompt。

**known-未决(记下不丢)**:
- 宏观流动性作为分母端变量(V2)— 需接真宏观数据 + 流动性 beta,精确拆"叙事成分 vs 流动性成分",**不做拍脑袋估算**
- 模糊 Opinion 文本抽取(从 Module 1 继承,V2 随 Opinion source 一起做)

**接口回填(Module 3 压测揪出,本模块已 LOCKED 但需补产物,不算重开)**:
- (R1) BetCard 单股卡产物要带 **"主题暴露 %"**(anchor mode 识别叙事成分时顺手输出)→ 供 M3 跨标的同源比对
- (R2) DCF driver 产物要带 **蒙特卡洛 band(p25/p75)** → 供 M3 强/中/弱阈值参照

## 模块 3:跨卡综合 ✅ LOCKED (2026-05-28)

**目标与边界**:跨卡关系引擎 + 综合叙事生成器。**纯消费方**:只读已存在的 BetCard(≥2 张),不改卡/不生成卡/不持久化卡。产出跨卡关系图谱 + AI 综合叙事。区别于 Module 2 的"交叉验证"(单卡内多 lens 纵向分歧),M3 做的是**多卡之间横向关系**。

**关键决策(10 条)**:

### Big Picture (4)
1. **定位**:跨卡关系引擎 + 综合叙事生成器,纯消费方(只读卡、不改卡、不生成卡)。**"同源"(看似无关两卡实押同一底层假设)是核心 Aha 产物**。
2. **输出 = 图谱+叙事双层**:底层结构化关系图谱(可追溯、前端可渲染)+ 上层 AI 叙事(每句论断挂 relation_id 可下钻)。`headline_insight` 单抽一句最尖锐发现当 demo 字幕。理由同 M2:**结论必须能对账回证据**,纯叙事=AI 编故事违背"推理透明"灵魂。
3. **触发 + 能力**:手动触发(选 ≥2 卡点综合)+ 按卡集合 hash 增量缓存;**综合全程走 chat mode**(轻量任务,不调 Deep Research,同源检测也靠 context 塞多卡摘要 + chat 推理)。
4. **接口**:单入口 `synthesize_cards(card_ids, lang) → SynthesisResult`;不自存(持久化交调用方);**硬边界 = 只吃 card_id**(卡必须已解码已存)。三模块职责切干净:M2 source→card(纵向)· M1 card 存取 · M3 cards→综合(横向)。

### 行为 (4)
5. **五种关系按卡配对自动路由**(用户不手选关系类型,M3 按 subject/series 结构自判):
   - 同 subject 不同 source → 共识 / 分歧 / 矛盾
   - 不同 subject 底层同假设 → 同源
   - 同 series 不同时间 → 漂移
6. **强度判定 + 主题对齐**:
   - 同标的比对 → lens 按公司选 → 同标的=同 lens → driver 单位天然可比,直接相减,**不需 percentile 通分**
   - **蒙特卡洛 band 宽当"强/中/弱"的参照单位**(差距 < 半 band=弱 / ≈1 band=中 / >1 band=强);band 是 M2 副产品,M3 白嫖。非 DCF lens 在注册表给简单阈值
   - 输出**三档(强/中/弱),不用 0-100**(避免 AI 困惑数值语义 + 假精确)
   - 同源用**几何均值** √(暴露_A×暴露_B)(两卡都重押才算强同源)
   - 同源主题对齐走 **chat 模糊匹配**(判"AI capex"≈"AI基础设施");嵌在综合那次 chat 调用里,不额外加 call。受控词表留 V2 可选
7. **缓存 + 数量**:缓存 key=卡 id 集合排序 hash;增删卡→新集合→**全量重跑**(不增量);**卡过期不使缓存失效**(综合也是不可变快照,符合 M1 哲学)。数量下限 2,无硬上限,靠"路由只跑有意义簇 + 输出 TopK"自然收敛,>8 张软提示。
8. **失败处理(延续 M2"诚实留空绝不编造")**:无显著关系→headline 留 null,**绝不凑牵强同源**;同标的但 driver 不可比(如一卡 DCF 一卡 anchor mode)→降级定性 + 标注"框架不同无法量化"(`comparable=false`,本身是有价值信息);chat 返回坏结果→重试 1 次→仍败退回纯结构化图谱(narrative=null,不崩)。

### 细节 (2)
9. **SynthesisResult schema** + **存储复用 `llm_cache` 表**(category="synthesis", key=卡集合 hash,不新建表;综合非卡不进 bet_cards):
   ```
   SynthesisResult {
     card_ids: [str]; generated_at: str
     headline_insight: {text, relation_id} | null
     relations: [{id, type(consensus|divergence|contradiction|shared_root|drift),
                  card_a, card_b, strength(strong|medium|weak),
                  shared_assumption, detail, comparable:bool}]
     narrative: str | null
   }
   ```
10. **代码复用映射**(工程细节,Tech Lead 自行拍板,不占产品决策):复用 `client.py`(chat)+ `db.py::cache_get/put`;新建/改写 `prompts/cross_card_synthesizer.md`;依赖 M1 `card_from_row` 读卡 + M2 band。**旧 `pipeline.py::run_synthesizer`(W2 单股跨假设综合)被 M3 泛化取代,留 Phase 2 清理**。

**场景压测结果(B.3 全过)**:
- 同标的三源(市场/高盛/看空 Opinion)→ 市场 vs 高盛强分歧;市场 vs anchor-mode Opinion 降级定性(`comparable=false`)✅
- 跨标的同源(组合 76% AI infra + 美光 92% AI capex)→ 模糊匹配判同 → √(0.76×0.92)=0.84 强同源 → 选作 headline ✅
- 同序列漂移(NVDA 今天 50% vs 上周 45%)→ 中漂移(依赖 M1 series_key 分组)✅
- 无关系(COST + 公用事业股)→ headline=null 诚实留空 ✅
- chat 失败 → 重试 1 次 → 退回纯图谱 ✅

**回填 Module 2(压测揪出,Module 2 已 LOCKED 但需补接口产物,不算重开)**:
- (R1) **单股卡也要产出"主题暴露 %"**:anchor mode 识别叙事成分时顺手输出,否则跨标的同源无数据可比
- (R2) **DCF driver 要带蒙特卡洛 band(p25/p75)**:供 M3 当强/中/弱阈值

**回填 Module 4**:"无显著关系"要有优雅空态,不空白报错。

---

## 模块 5:Agent 活动流 ✅ LOCKED (2026-05-28)

**目标与边界**:活动流事件协议 + SSE 传输管道 + 在 M2/M3 流程埋点。**横切基础设施**:不碰 decode 逻辑(M2)/综合逻辑(M3)/渲染(M4)/存储建表(M1)。把"agent 此刻在干啥"实时流给前端。把 W2/W3 那版临时 `/stream/evidence` SSE 正式化,顺收 bug #34。

**关键决策(7 条)**:

### Big Picture (4)
1. **定位**:= 事件协议 + SSE 管道 + 埋点。**产品意图 = "过程透明"的价值载体,不是装饰性进度条**——卡(M1)给结果,活动流给过程(agent 怎么选 lens/反解/找证据/发现同源),用户看着 AI 推理本身就是差异化。
2. **live + 持久化可回放,带时序模拟**:live 解码实时流;缓存/历史卡从存好的事件日志**回放(按原始节奏重演)**。决定性理由 = 呼应已定的"预跑缓存、demo 不 live 跑 deepresearch"策略——只有可回放,缓存卡在 demo 现场才有活动流可看。
3. **事件粒度 = 决策级语义推理步**(人类分析师会口述的判断,非"step 1 完成"式机械进度);**单档** + `kind` 标签(decision/computation/evidence/relation)供 M4 分样式,不做里程碑+可展开两档(避免前端复杂 + SSE 卡死风险)。
4. **范围 = 统一工作台 feed**(不是每卡一条小流):所有 agent 活动汇进一条全局 feed,事件标 `source`(哪卡/哪次综合),单卡可镜像但**主舞台是工作台**。呼应 pivot 初心——一条实时活动 feed 最有"Agent 感"。传输用 **SSE**(单向够用,不上 WebSocket);emit 回调注入 M2/M3,M2/M3 不感知传输。

### 行为 (2)
5. **回放按需触发 + 必有终态**:cache miss→live 流;cache hit→秒出结果 + "回放活动流"入口;demo→主讲点回放控节奏。无论成败,活动流**以明确终态事件收尾**(done/error 各带人话,呼应 M2 诚实留空)。断线从持久化事件补流(实现细节)。
6. **并发 = 串行 + 排队**:一次只演一个连贯推理过程,进行中再触发则排队(前端显示"排队中 N 个")。理由:本产品核心价值是"看 agent 连贯推理",连贯性 > 并行速度;demo 卡全预缓存,回放本就一个个来。每 job 各存自己事件日志。

### 细节 (1)
7. **ActivityEvent schema** + 存 `activity_logs` 表(**回填 Module 1**):
   ```
   ActivityEvent {
     job_id; seq; t_offset_ms          # 时序回放靠 t_offset_ms
     source: {kind:"decode"|"synthesis", card_id|card_ids, subject}
     phase                              # M2: lens|solve|evidence|anchor|cross_check
                                        # M3: route|relate|headline
     kind: "decision"|"computation"|"evidence"|"relation"   # 样式标签
     text                               # 人话推理行(用户读的)
     payload: dict | null               # 结构化细节 {lens, implied_cagr...}
     terminal: null|"done"|"error"
   }
   ```
   一个 job 的日志 = 按 seq 排好的一串事件;回放=按 seq 重发 + t_offset_ms 还原节奏。**BetCard 答 What(结论),ActivityEvent 答 How(推理过程),正交两个 primitive**。代码复用(旧 `/stream/evidence` SSE + 前端 Agent log)= 工程细节,Tech Lead 自行定。

**场景压测结果(B.3 全过)**:
- live 解码→实时流+kind 标签+done 终态 ✅
- demo 回放(cache hit)→按 t_offset_ms 重演 ✅
- 解码失败→error 终态带人话,卡仍生成证据留空 ✅
- 解码进行中触发新解码→排队,先演完当前 ✅
- M3 综合事件标 source.kind="synthesis" 串入同一 feed ✅

**回填 Module 1(M1 已 LOCKED 但需补)**:新增 `activity_logs(job_id, source_ref, events_json, created_at)` 表存事件日志 blob,供回放。

---

## 模块 4:工作台前端 ✅ LOCKED (2026-05-28)

**目标与边界**:纯展示 + 交互层。通过 API 调 M2(decode)/M3(synthesize),消费 M5 活动流。**不含业务逻辑**。复用扩展现有 `pricelens_mockup.html`,从"单股研报页"改造成"多卡工作台"。

**关键决策(8 条,快速对齐)**:

### Big Picture (3)
1. **边界**:展示+交互层,API 调 M2/M3 + 消费 M5 活动流,无业务逻辑。
2. **起点**:复用扩展 `pricelens_mockup.html`(已实现设计系统),改造成多卡工作台。
3. **设计语言**:严格继承 `pricelens_design_system.md`(纸感 / oxblood / Geist / 无蓝紫渐变 / >3 行数据用表格 / 数字右对齐 mono),不重新设计。

### 布局 + 行为 (4)
4. **三区工作台**(LOCKED 取推荐):主画布(多 Bet Card 并列)+ **右侧栏活动流 feed**(统一,M5,实时/回放)+ 底部综合面板(展开式)。理由:活动流常驻右侧,综合按需展开于底部。
5. **主交互流**:顶部输入框贴 bet → 选 source type(market/analyst/opinion/portfolio)→ decode → 右侧 live 活动流 → 卡落进主画布。
6. **综合流**:主画布选 ≥2 卡 → 点"综合" → 底部出 headline 字幕 + 可下钻关系图谱 + 叙事(M3 SynthesisResult)。
7. **卡形态**:单股=紧凑卡可并列对比,组合=展开仪表盘(直接继承 M1)。

### 细节 (1)
8. `bet_card_sample.html` / `portfolio_card_sample.html` 已验证的卡渲染作为组件并入工作台。组件代码 = 工程细节,Phase 2 自理。

**场景压测结果(B.3 全过)**:贴 bet→活动流→卡落画布 ✅ · 多卡并列对比 ✅ · 选 2 卡综合→底部 headline+图谱+叙事 ✅ · 组合仪表盘渲染 ✅ · 综合无关系→优雅空态(接住 M3 回填)✅ · 缓存卡点回放→右侧时序回放 ✅

**承接的回填项**:M3 的"无显著关系优雅空态" → 由 D6 综合面板空态实现。

---

> **Phase 1 产品对齐完成 ✅ —— 5 模块全部 LOCKED(2026-05-28)。** 总决策数:M1(14)+ M2(10)+ M3(10)+ M5(7)+ M4(8)= 49 条。下一步 = Phase 1 Step D(冻结 PRD)→ Phase 2 技术拆解。
