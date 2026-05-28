# Glossary · Bet Decoder

> 术语精度沉淀。主对话和后续所有 Phase 严格使用规范词(包括 AI 自己的提问)。
> 在 Phase 1 模块对齐过程中逐条 inline 写入。

---

**Bet Card**:产品的核心基础单元。每个被反向解码的投资 bet 都表示为一张 Bet Card。下分两个 sub-type:单股卡、组合卡。
_Avoid_:Fact Card、卡片(泛指)

**单股卡 (Single-asset Bet Card)**:针对单只标的的 Bet Card,bet 内容是 DCF 参数(CAGR / WACC / 终值利润率)。涵盖 Market / Analyst / Opinion 三种 source。
_Avoid_:个股卡、股票卡

**组合卡 (Portfolio Bet Card)**:针对一个投资组合的 Bet Card。**形态是仪表盘(展开式全景),不是紧凑卡片**(子决策 B 锁定 2026-05-28)。bet 内容是跨股主题暴露 / 集中度等组合级专属指标。
_Avoid_:个股仪表盘

**单股卡(形态补充)**:紧凑卡片形态,核心价值是"多张并列对比"(Market vs Analyst vs Opinion 同屏)。

**Bet**:一个押注的最小单元。在单股卡里是一个 DCF 参数押注;在组合卡里是一个主题暴露押注。
_Avoid_:assumption(中文统一用"押注"或"bet")、bet 元组的具体结构待 Q1 锁定

**Source type**:一个 bet 的来源类型,枚举值:market(当前市场价)/ analyst(分析师目标价)/ opinion(推文/文本观点)/ portfolio(持仓组合)。
_Avoid_:input type、来源

**主题暴露 (theme exposure)**:组合卡专属。把组合内各只票的隐含 bet 按持仓权重聚合到主题级(如 "AI 基础设施 capex" 76%)。存于 `portfolio_exposures` 表。
_Avoid_:因子暴露(factor exposure,那是 Barra 风格的另一回事)

**集中风险 (concentration risk)**:组合卡专属。多只持仓押注同一主题导致的隐藏集中。由 `portfolio_exposures.is_concentration_risk` 标记。是 Aha 1 的核心产物。
_Avoid_:持仓集中度(那只是单股权重高,不是 bet 同源)

**草稿态 (draft state)**:组合编辑过程中的可变工作状态。只有"解码/保存"才把草稿固化成一张不可变 Bet Card 快照。
_Avoid_:临时卡、未保存卡

**锚价 (anchor_price)**:反向 DCF 求解所用的输入价。Market 卡 = 当前价;Analyst 卡 = 目标价;Opinion 卡 = 文本抽取价。存于 `runs.anchor_price`。
_Avoid_:current_price(那只是 anchor 的一种)

**Lens(估值视角)**:把一个价格反解成隐含业务指标所用的估值方法。分两梯队:传统 lens(DCF/PE/PS/EV-EBITDA/P-FCF/P-B/PEG)+ 锚 lens(TAM/期权/类比/叙事)。注册表可插拔。
_Avoid_:估值方法(口语可用,代码/文档统一用 lens)、method(跟"4 种解码方法"那个旧说法区分)

**Frame-adaptive decode**:Module 2 的核心 —— agent 按公司商业模式挑合适的 lens,而非对所有公司套同一个 DCF。reverse_dcf.py 只是 DCF lens 下的一个工具。
_Avoid_:reverse DCF(那只是其中一个 lens,不等于整个解码)

**Anchor mode(锚模式)**:传统 lens 全失效(给出荒谬值)时的解码模式 —— decode 交易者的心理锚(叙事/期权/TAM/类比),把价格拆成"基础业务 + 叙事/期权成分"并对账到现价。**对 AI 复合体(GPU/存储/光模块/AI 应用)是 primary 而非 fallback**。
_Avoid_:边界态(旧词,boundary mode;现已升级为 anchor mode,语义从"失败兜底"变"主动锚定")

**交叉验证 (cross-check)**:一张单股卡除 primary lens 外,自动跑 1-2 个适用 lens,展示它们隐含值的分歧。分歧大 = 市场的 bet 在此最依赖假设(是 Aha,不是 bug)。属 Module 2 **单卡内纵向**(方法维度)。
_Avoid_:多方法(旧说法);**勿与跨卡综合混淆**(那是 Module 3 多卡间横向)

---

> 以下为 Module 3(跨卡综合)术语,锁定 2026-05-28。

**跨卡综合 (cross-card synthesis)**:Module 3 的核心 —— 取 ≥2 张已存在的 BetCard,做**多卡之间横向**的关系发现 + AI 综合叙事。纯消费方(只读卡、不改卡、不生成卡)。区别于交叉验证(单卡内纵向)。
_Avoid_:综合(泛指)、cross-check(那是单卡内的交叉验证)

**同源 (shared root)**:跨卡关系的一种,也是产品**最值钱的 Aha**。两张 subject 不同的卡,表面无关,底层却押注同一假设/主题(如你的组合 + 高盛 NVDA PT 都依赖"AI capex 不崩")。强度按两卡对共享主题暴露的几何均值算。
_Avoid_:相关性(correlation,那是统计概念,不是 bet 同根)

**漂移 (drift)**:跨卡关系的一种。**同一 series**(subject+source 相同)在不同时间的两张卡,隐含 driver 的变化(如 NVDA 市场卡上周隐含 45% → 今天 50%)。揭示市场押注随时间变激进/保守。
_Avoid_:波动(volatility,那是价格波动不是 bet 漂移)

**关系强度三档 (relation strength)**:跨卡关系的强弱,只分**强 / 中 / 弱**三档(不用 0-100,避免假精确 + AI 困惑数值语义)。同标的比对用**蒙特卡洛 band 宽**当"多大差距才算有意义"的参照单位。
_Avoid_:相关系数、置信度、0-100 分

**headline_insight**:一次跨卡综合里**最尖锐的一条发现**,单抽成一句话当 demo 字幕。按"强度 × 关系类型权重"取 Top1,同源权重最高。无显著关系时为 null(诚实留空,绝不编造)。
_Avoid_:摘要、总结(它是单条最尖锐发现,不是全量总结)

**SynthesisResult**:Module 3 单入口 `synthesize_cards(card_ids, lang)` 的返回结构。三段式:`relations[]`(结构化关系图谱)+ `narrative`(AI 叙事,每句挂 relation_id 可下钻)+ `headline_insight`。存于 `llm_cache` 表(category="synthesis"),不进 bet_cards(综合非卡)。
_Avoid_:综合报告、synthesis card(它不是一张卡)

---

> 以下为 Module 5(Agent 活动流)术语,锁定 2026-05-28。

**Agent 活动流 (agent activity stream)**:Module 5 拥有的横切基础设施 —— 把"agent 此刻在干啥"的语义推理步实时流给前端。定位是**"过程透明"的产品价值载体,不是装饰性进度条**。BetCard 答 What(结论),活动流答 How(推理过程)。
_Avoid_:进度条、loading、日志(它是产品价值,不是技术副产品)

**ActivityEvent**:活动流的最小单元,一条 = agent 的一个**决策级语义推理步**(人类分析师会口述的判断,非机械进度)。带 `kind`(decision/computation/evidence/relation)+ `t_offset_ms`(时序回放)+ `source`(哪卡/哪次综合)+ `terminal`(done/error)。存于 `activity_logs` 表。
_Avoid_:log entry、step(太机械);它必须是"语义推理步"

**时序回放 (timed replay)**:把存好的 ActivityEvent 日志按原始 `t_offset_ms` 节奏重新流一遍,观感同 live。缓存卡/历史卡靠它在 demo 现场重现"agent 正在思考"。是"预跑缓存、demo 不烧钱"策略的关键配套。
_Avoid_:回看、静态日志(它带时序模拟,不是静态调出)

**统一工作台 feed (unified workspace feed)**:活动流的呈现范围 —— 所有 agent 活动(不管解哪张卡/跑哪次综合)汇进一条全局 feed,事件标 source。主舞台是工作台,单卡可镜像。最有"Agent 感"的形态。
_Avoid_:单卡流、per-card stream(那是被否决的方案 a)

---

> 以下为 Module 4(工作台前端)术语,锁定 2026-05-28。

**工作台 (workspace)**:产品的主交互面,Module 4 的核心形态。三区布局:主画布(多 Bet Card 并列)+ 右侧统一活动流 feed + 底部综合面板。是 pivot 后"Agent/App 感"的落点 —— 区别于旧的"单股研报页"。
_Avoid_:研报页、报告页、dashboard(组合卡才叫仪表盘,工作台是更上层的容器)
