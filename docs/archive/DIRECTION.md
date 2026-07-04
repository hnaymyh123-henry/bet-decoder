# 方向对齐 · 多框架预期分解引擎 + agentic 呈现

> **一句话**：把产品的核心从"反向 DCF 解释一个隐含数字"升级为"**用一栈正交框架把市场预期分解成多条 channel、做跨框架对账、并把这场分解做成一个 agent 驱动、可操纵的调查**"；决策纪律层挂在它之上。
>
> **版本**：v0.3（方向草案；并入 F1–F5 review 修正 + O1–O5 锁定 + 呈现层年轻化 + 决策层 Trade Plan，**大框架已对齐，细节待打磨**）· 2026-07-04
> **状态**：本文档只钉**大框架**。标 `[LOCKED]` 的是 2026-07-04 讨论中已对齐、不再回头论证的；标 `[OPEN]` 的是留给后续一起打磨的产品细节。**这不是定稿 PRD。**
> **与现有文档的关系**：本方向**取代** `TRADING_AGENT_PRD.md` v0.1 的重心叙事、以及 `BET_DECODER_VISION.md` §1 的原语定义——但只在冲突处取代，两份文档的具体设计在细节打磨阶段逐条并入或废弃，暂不改动它们。

---

## 0. 为什么有这次对齐

现有产品是一条纯诊断链，且诊断只用了**一个框架**（反向 DCF）去解释"市场隐含相信什么"。两个问题：

1. **一个框架只照亮一条 channel。** 价格里叠着好几层预期，反向 DCF 只反解现金流那层。TSLA "无 DCF 解"不是 DCF 失败，是市场在说"这个价格由非-DCF 的 channel 主导"——DCF 能探测残差，解码不了。
2. **产出是一张固定模板卡 = 研报感的根因。** 只要底下推理是确定性决策树，输出就*能*被灌进固定 slot，于是无论怎么美化都像研报，不像 agent（这正是 2026-06-01 "theater" 批评的结构根因）。

方向修正一句话：**推理框架层做深（多框架），呈现层做活（agentic），且后者是前者的结果不是并列任务。**

---

## 1. 重心叙事 `[LOCKED]`

- **头牌 = 预期透明化 + 决策纪律**，不是 "trading agent / 模拟盘"。理由：模拟盘是被商品化的东西（每个券商都有 demo account），而"市场预期随时间的漂移做透明化"没人系统做过，是独有护城河，也更贴近"让隐性知识平权"。
- **核心对象是"变化"不是"快照"。** 用户原话是"市场预期的**变化**"。散户体验市场是价格 ticker；专业玩家体验市场是一条**预期修正流**。把"价格是预期的下游 + 预期在怎么漂"做可见，是最真的一次平权。
- **paper trading 降级成 commitment device + demo 道具**，不是游戏化账户。实质教育价值来自"承诺可证伪 thesis + 追踪现实是否背离"，不是虚拟美元的 P&L 曲线（短期纸面盈亏是噪声，会训练用户盯错东西）。

---

## 2. 原语迁移 `[LOCKED]`

- **旧原语 `Bet Card`（静态名词、你收到的产物）→ 降级成"快照"。**
- **新原语 = 一次"解码 / 调查"（过程、agent 驱动、可操纵的对象）。** 卡 = 这场调查在某一刻的一张冻结快照。
- **现有架构存活且升级**：不可变快照 + `derived_from` 血缘在旧模型里只是"存一张卡"；在新模型里变成**真正的调查史**——沿途冻快照、用 what-if 分叉衍生卡 = 把调查往另一支探。血缘图第一次有该有的语义。
- **北极星比喻**：现在的卡是一封**格式信**（固定填空）；目标是一个**你能看着他干活、还能随时打断提问的分析师**——按票不同用不同查法 / 开口先讲最要命那条 / 你追问他把相关那条 channel 拉到面前 / 你能看他怎么推。四条都在"透明"一侧，没有一条是"你该买"。

### 原语规格（O1 `[LOCKED 2026-07-04]`）

**一场解码（Decode）的 state：**

```
Decode {
  subject / source     # 被解码的 bet + 原始 input
  panel[]              # 全量便宜 kernel 的结果，每条带 horizon 标签（F1/F4）
  investigation        # agent 注意力轨迹：深挖了哪条 / 贵 gate 开没开 / 卡面排序
  reconciliation       # horizon 桶内的跨证人对账（一致 / 分歧 + headline）
  series_link          # 时间轴：同 subject 的解码序列（漂移长在这，F2）
  lineage              # 分叉轴：derived_from + 分叉类型
  decision?            # 可选：决策层跑过才有的 Trade Thesis
}
```
两根轴正交：**series_link 答"预期在怎么漂"（时间）**，**lineage 答"假设不同会怎样"（反事实）**。

**可变性 · 一条判定线**：*这次互动改没改任何输入 / 假设？*

| 互动 | 例 | 改假设? | 系统行为 |
|---|---|---|---|
| 揭示 | "为什么说拥挤?" | 否 | 原地解释，不新建 |
| 深挖 | "叙事挖深一点" | 否 | 开 gate，investigation 增厚（只增不改），不新建 |
| 改写 | "增速只有 20% 呢?" | 是 | 分叉出新的不可变节点，`derived_from=父`，父一字不动 |
| 序列 | 次日重解码 | —（新观测）| 新节点，与父是 series 关系（非分叉）|

git 语：**解码=commit · 序列=main 上累积的 commit · what-if=从某 commit 拉 branch · 追问=`git show` · 不可变=永不 force-push**。坚持不可变的三个理由：诚实可审计（"上月市场隐含什么"永远可查）+ 漂移曲线要求每点冻结 + 它直接铸造 O4 的两种手感（追问轻 / what-if 重）。

**落库（决策 3）· 扩展既有为主 + 一张小观测表为辅：**

| 概念 | 落到 | 改动 |
|---|---|---|
| 解码 state | `decode_detail` JSON | v3→v4 幂等迁移，扩 JSON 形状 |
| 分叉轴 | `derived_from` / `derivation_kind` / `derivation_json` | 零新机制（6/1 已建）|
| 序列轴 | series 分组 + 日去重索引 | 语义升级；去重当初就设计成排除衍生卡 = 分叉天然不污染序列，零改动 |
| **漂移观测点** | **新表 `panel_observations(subject, as_of_date, channel, metrics_json, horizon)`，append-only** | 唯一新表。历史回填 + 每次解码的 panel 都落一份；**观测（数据点）≠ 解码（调查）**，不塞 `bet_cards` 免造几百张幽灵卡 |

---

## 3. 引擎先于呈现 + 两条护栏 `[LOCKED]`

### 3.1 因果，不是并列
> **呈现层能不能真 agentic，是引擎 agentic 的*结果*。** 只要引擎是决策树，把卡做得再"对话式"也只是给固定流程套 chat 皮 = **theater 2.0**（同一个坑换衣服）。正确次序：先让引擎变成真会做选择的 agent（自己支配**注意力**：哪条 channel 深挖 / 贵调用花在哪 / 卡面谁领衔——channel 本身全量常跑，见 3.2），一旦调查路径因票而异，固定模板就装不下它，呈现层被迫变成动态组合、可追问的——**agentic 引擎逼出 agentic 呈现**。

### 3.2 护栏 A：panel 常跑，agency = 注意力（F1 修正）
反向 DCF、期权分布提取、因子回归这些**框架本身是确定、可靠、便宜的 kernel**，不该 agentic——而且**全量常跑（standing instrument panel）**：每次解码都跑同一套 panel，同一标的的时间序列才无洞（漂移是头牌，序列完整性不可妥协）、跨卡才可比。agency **不选择"跑什么"，只支配注意力**：哪条 channel 深挖、贵调用（narrative DR）开不开、卡面谁领衔、怎么回应追问。类比：医生给每个人抽同一套血常规，*诊断路径*才因人而异。要的是**聪明分析师盯着一面常开的仪表盘**，不是会即兴发挥的计算器。（这是 `agent_tools.py` "包装既有 fn 不重实现"已经摸对的直觉，给它一个名字守住它。）

### 3.3 护栏 B：平权 = 引擎可任意复杂，卡的脸永远白话
失败模式：堆十个 quant 模型 → Bloomberg 指标墙 → 比原来更不透明 = 反向平权。铁律：**引擎复杂度↑，输出面的可读性必须持平或↑**。复杂性进 "▾ 详细决策链" 抽屉，卡的正面永远是每条 channel 的一句白话。成功的唯一标准：普通人读完，理解得比以前**多**。

---

## 4. 预期分解栈 `[LOCKED 方向 / OPEN 具体条目]`

多条 channel 是**审讯同一个价格的多个独立证人**，对账 = 交叉质证——**不是价格的加法分解项**（别试图让各账加总到 100%；"DCF 解释不了的部分 → narrative premium" 这种加法残差只是证人之间的一对关系，不是全局结构）。每条 channel 配成熟框架、输出一句白话、**带 horizon 标签**。价格里叠着的预期层（示意，非加法恒等式）：

`现金流价值 · 分布/风险定价 · 预期修正动能 · 持仓拥挤度 · 宏观/因子 beta · 叙事溢价`

| Channel | 框架 | 输出白话（示意） | 成本 | 状态 |
|---|---|---|---|---|
| 现金流账 | 反向 DCF + 倍数 lens（已有 7 lens） | "需要 ~38% 长期增速——历史极少数公司做到" | 免费·无 LLM | 有 |
| **分布账（keystone）** | **期权隐含分布**（IV 曲面 / skew / term structure） | "3 个月定价 68% 落在 $180–250；下行保险比它自己近一年更贵" | 便宜·无 LLM | **建议先做** |
| 变化账 | consensus estimate 修正 + 分歧度 + 价格反应函数 | "90 天 estimates 上修 12%，价格涨 25%——价格跑在预期前面" | 便宜 | 建议第二 |
| 拥挤账 | 空头 / 持仓 / 资金流 | "空头回补空间小 + 基金持仓已高——这个多头 bet 很脆" | 便宜 | 缓 v1.1 |
| **高度栈**（合并老"因子账+叙事账"）| 按高度分层 macro/industry/company，每层 = 可测半 + 叙事半（见下）| "这价格 60% 是 AI-beta，40% 才是它自己的 bet" | 可测半便宜 / 叙事半贵·门控 | 轻量可测半进 MVP·叙事半 gated |

**高度栈展开**——叙事不是一条 channel，是按高度分层的深挖；三层的理解框架 / horizon / 共享范围完全不同（修"叙事太笼统"）：

| 高度 | 理解框架 | horizon | 谁共享 | 可测半（cheap·panel）| 叙事半（gated DR）|
|---|---|---|---|---|---|
| 宏观 | 利率 / 流动性 / risk appetite | regime，月-年 | ~所有资产 | 对宏观因子的 beta | "市场信软着陆、降 3 次" |
| 行业 | 周期位置 / 渗透率 S 曲线 / 竞争结构 | cycle，1-5y | 同主题的票 | 对主题篮子的 exposure（**MVP 用现成主题 ETF 如 SMH/SOXX 代理**）| "AI-capex 复利到 2027" |
| 公司 | 催化剂 / 执行 / 护城河 | 因名而异 | 该票独有 | 剥掉宏观+行业后的 idiosyncratic 残差 | "ASIC 替代慢于预期" |

**每层还有"分布半"**：读该层标的（个股 / 主题 ETF / 指数+VIX）的期权隐含分布——每层 = 可测半（exposure）+ 分布半（期权）+ 叙事半（gated DR），VIX 进宏观层。详见「期权分布 channel · 完整规格」。

**MVP panel = DCF + 期权 + consensus + 轻量高度归因**（市场 beta + 主题 ETF beta 两个回归，切 macro / 主题 / 公司 粗三分）；叙事深挖 gated 且**按高度打标**（永不吐笼统 "narrative"）；富多因子模型 + positioning 缓 v1.1；panel 先按单卡定义，组合视图 fast-follow 复用现有 parent→constituents。卡面因此拿到 "这价格 X% 是主题 beta、只有 Y% 是它自己" —— Aha B 从组合下放到单卡。

**四个不可动摇的原则：**

1. **keystone 是"点→分布"的跃迁。** 反向 DCF 给一个*点*，期权市场给市场定价的*整个概率分布*——"市场预期"从一个数变成一个*形状*，这才是它真实的样子；而且"68% 落在 X–Y"比"隐含 CAGR"更好懂，平权不降反升。**但口径必须诚实**：期权分布是 risk-neutral（Q 测度）——它是*市场为各种结局开出的价格*，不是市场信念的真实概率，卡面白话按"市场开价"的口径说；skew 类白话必须报**相对自身历史/同行的分位**（股票期权 skew 结构性偏 put 侧，"在为下跌付钱"是常态不是信号；call 侧被抢筹倒挂反而是 euphoria/拥挤信号）。
2. **洞察在跨框架对账，不在任何单框架。** 当 DCF-隐含增速、期权-隐含分布、consensus 修正三者打架时，分歧本身就是信号（"基本面平、期权平静、但价格涨 25% 靠资金流 = positioning-driven 假涨，很脆"）。这是你已有的 cross-card synthesis 原则，从 cross-**card** 挪到 cross-**framework**。
3. **对账必须 horizon-aware。** DCF 隐含 5 年、期权覆盖数周到数月、consensus 修正以季度计——不分期限的对账会把 term structure 误报成矛盾。对账在同 horizon 桶内进行；跨桶差异以"期限结构"的名义叙述（本身是合法洞察，但不许标 ⚠ 矛盾）。
4. **成本纪律：便宜信号门控贵信号。** panel（近乎免费）常跑盯着，只有穿过阈值才触发一次贵的 narrative Deep Research 去查"到底发生了什么"。

---

## 5. 决策层被重新武装 `[LOCKED 方向]`

一旦预期是"分布"而非"点"，`TRADING_AGENT_PRD.md` 的决策字段全部升级（细节待那份文档重写时逐条落）：

| 字段 | 旧 | 新 |
|---|---|---|
| edge | 你的 view vs 隐含的一个点 | 你的 view 落在市场隐含分布的哪个分位 + 哪些 channel 支持/反对你 |
| Kelly 输入 | p_win 与赔率全靠 base_rate 手估 | **期权分布给的是"市场开出的赔率"（Q 测度），不是你的 p_win**——Kelly = f(你的 p, 市场赔率)：你的独立 view（base_rate 那套，原设计是对的）仍给 p，期权给赔率与市场 view；风险中性概率直接当 p_win 会系统性偏差（put 因保险需求偏贵）|
| KILL / self_falsification | 一句可证伪的话 | **分型**：价格可观测型 → 期权报隐含触发概率（可量化）；基本面型（"营收连 2 季破 11%"）→ 期权定价不了，走 consensus 修正跟踪 |
| conviction | 单点判断 | 由**跨 channel 一致性（同 horizon 桶内）**决定：都指同向 = high，互相矛盾 = low（矛盾本身是最诚实的信号）|

---

## 6. 落地节奏 `[LOCKED]`

**做 A 的野心 + B 的节奏**：目标是完整重构（否则又把通用能力当 single-purpose 用，重蹈 v0.6 覆辙），但从**期权那一条 channel 切入验证**——它最能证明"点→分布"跃迁对不对，且几乎免费。

推荐加入顺序：**⓪ 漂移脊椎（不是一条 channel，是脊椎交付物）**——对历史逐时点重跑便宜 kernel，产出"隐含假设时间序列叠在价格上"的 hero visual；依赖 qveris 的 **point-in-time consensus**（估计的"当时值"而非回看值），实现期第一个要验的数据集 → ① 期权隐含分布（keystone，升级透明化 + edge/赔率数学）→ ② consensus 修正（字面上的"变化"，喂漂移监控）→ ③ 轻量高度归因（市场 beta + 主题 ETF beta，切 macro / 主题 / 公司 粗三分）；叙事半按高度打标、走 gated 深挖。富因子模型 + positioning 缓 v1.1。

### 主动监控 · 引擎的第二入口 `[方向 LOCKED 2026-07-04 / 细节 OPEN]`

反应式解码之外，引擎有第二个入口：**调度发起的主动监控**——替用户盯住持仓 + 保存过的解码，出事主动 report。共享同一注意力环，几乎全复用已有零件（panel 常跑 + `panel_observations` + 漂移脊椎），是很薄的一层。它一次兑现两个 pillar：盯的是"预期的变化"（透明化），最好的触发是用户的"决策红线"（决策纪律）。

- **scope**：持仓 + 保存过的解码（复用组合原语）。**cadence**：按日（对齐"新鲜度按天"；日内不做）。
- **触发（MVP）= 重大事件**（财报 / 评级 / 大跳空，用 qveris 事件源）。选事件优先：离散、无歧义、不用调噪声阈值、正好是"第一时间该扫"的时刻，最快跑起来、绕开最难的阈值问题。**白赚**：基本面型 KILL line（"增速连 2 季破 11%"）本就只在财报时可观测 = 天然事件驱动，events-first 仍能第一时间抓到"论点被打破"。**缓（细节，后面优化）**：连续"指标漂出自身历史"独立触发、事件之间的持续 drift 报警、KILL 自动检测。
- **report 形态**：不是重写全报告，是**变化摘要**——自上次解码以来动了什么、为什么撞到你承诺的 thesis。**事件是触发时机，报告顺带把"这段预期怎么漂的"带出来**（漂移的差异化价值搭事件的车，不因 events-first 丢掉）。便宜默认（只读 panel），够严重才升级开一次 narrative DR。
- **delivery 分层**：论点被打破 / 严重 → 第一时间 push；例行变化 → 工作台"变化 feed"累积、用户来看（不 nag）。**诚实边界**：报告"变了什么 + 撞到哪条 KILL"，不说"你该卖"。
- **排期**：反应式引擎是前置依赖；监控是**头牌能力不是 P2**——纠正 `TRADING_AGENT_PRD` 把 T11 放 P2 的错。

---

## 呈现层 · 年轻化重设计（O4 `[LOCKED 2026-07-04]`）

**视觉语言整体转向**：从"Goldman 研报"（austere、密、禁深色/圆角/渐变）转到 **年轻化交易 App**（Robinhood / 长桥 / crypto 交易所 / RockFlow）。参考实现 = `mockup_v2_young.html`（vibe 已确认）。

- **视觉铁律（新）**：深色底、卡片流、**一屏一个焦点**（砍信息量）、超大 tabular 数字、涨绿跌红用**鲜亮**版、图表带柔和渐变面积填充、Inter 圆体、AI/agent 用紫色强调。
- **结构隐喻**：不是"从上读到下的报告"，是"**持仓终端 / 移动 App**"——扫卡片 → 点开钻取；章节/exhibit 语法退到单卡展开后的内部。
- **追问 = 底部"问 agent"聊天栏**（RockFlow 招牌 AI 味 + O1 的"追问"交互合一）；改假设 = 分叉衍生卡（O1 决策 2 不变）。
- **⚠ `pricelens_design_system.md` 作废**：整份是研报美学，与新方向相反 → 待 vibe 落地后按新语言重写一版（follow-up）。

**一期范围（scope）`[LOCKED]`**：
- **持仓中心**。通用解码（解析推文 / 分析师目标价 / 自由文本任意 bet）**延后**，不进一期。
- 一期入口 `+` = **导入组合 / 加持仓**（ticker + 权重），不是通用 bet 解码。（"买入前先解码候选 ticker"是否进一期 = 小尾巴，待定。）

**跨卡综合 = 组合内综合**（收敛）：
- 不是独立功能，是**持仓页的第二层**：`持仓页 = 顶部一张组合综合卡（读 across）+ 下面一列 position 卡`。
- 综合卡形态 = **高度栈跨持仓聚合**（"你整个组合 58% 在赌同一个 AI 主题——你以为分散了" = Aha B，算法复用）+ 跨持仓矛盾 / 共享 KILL 标记（"NVDA 要 hyperscaler 加 capex，GOOG 要它控成本——打架"）。
- 安全丢弃：Aha A（你 vs 分析师）随通用解码延后，一期不缺；Aha D（跨时间对比）已被漂移脊椎 / 监控覆盖。

---

## 决策层 · Trade Plan + 可插拔执行（O5 `[LOCKED 2026-07-04]`）

**修正**：`TRADING_AGENT_PRD` 以 paper-trading 模拟盘为中心；本方向取代其框架——**决策层输出一个可执行的 Trade Plan 对象，"执行"是可插拔后端，分阶段从安全到真实接**。区分两件被混淆的事：**闭环**（plan→执行→跟踪，要）vs **游戏**（假组合 P&L 排行榜，不要——被商品化 + 教人盯短期噪声）。

**Trade Plan 结构**（分析 + 交易策略拧成一个方案；`TRADING_AGENT_PRD` 零件全活，框架替换）：
- **stance / 方向**：贵/便宜 + 风险回报对称性（来自分析）。
- **strategy archetype**（"结合交易策略"的落点）：分析状态 → 打法。MVP 几个干净原型：便宜+催化 → 分批建仓带 stop；高估+下行不对称 → 减/不加/对冲；无 DCF 锚+高叙事 → 小仓动量紧 stop；拥挤+脆 → 减仓收紧 stop。
- **entry**（价位/条件，可分批）· **size**（Kelly §5 修正版 + 上限）· **stop + KILL**（KILL 分型：价格 stop 期权可观测 / 基本面认错线走 consensus）· **exit/止盈**（论点兑现或 edge 消失）。
- **透明 + self_falsification 强制**：方案自带"为什么 + 我错了会怎样"——区别于黑箱荐股 App 的根本；低信心/小 edge → 方案可为"不动"，不硬造交易。

**执行后端 · 分阶段**（= "接 API 做交易闭环"；Plan 是稳定接口，后端往上插；P&L 全程安静跟踪，非首页游戏）：

| 阶段 | 后端 | 性质 |
|---|---|---|
| **一期** `[LOCKED]` | Plan → **展示，用户手动去券商执行** | 软件不下单；闭环 = 方案 + 你执行 + 监控盯 KILL |
| 二期 | Plan → 券商 **paper/sandbox API**（如 Alpaca paper）| 真实下单管线、假钱；同一套代码以后切真 |
| 三期 | 真实券商 + **每单人工 approve** | 老 T14；需合规，AI 不代下单 |

**边界（进 O10）**：向终端用户输出可执行交易方案 = 投顾 / robo-advisory 地界（MAS / CSRC 需牌照）；透明 + self_falsification 使其非黑箱荐股，但合规面真实，须认真对待，不能挥手带过。

---

## 模型分配（O12 `[LOCKED 2026-07-04]`）

一个模型不干两种活——拆角色：

| 角色 | 干什么 | 模型 |
|---|---|---|
| **数据层** | 拉 qveris（标准 panel 确定性 + 追问按需）| 不是模型——确定性 Python；仅"追问按需"那条走 harness tool-call |
| **编排脑**（agentic harness）| O3 注意力环 / gate 叙事 DR / what-if / 答追问——需**可靠 function-calling** | 便宜快、可靠 function-calling 的模型（DeepSeek-V4-Pro 现成）；**不是 MiroMind DR** |
| **叙事 DR 工具** | 叙事半 gated 深挖（自己上网研究）| **MiroMind flagship deep-research**（已集成 `narrative.py`），当一个 gated 工具被编排脑调用 |
| **便宜叙述** | 白话卡面文案 + 跨卡综合文本 | 便宜 chat 模型（复用编排脑或 mini）|

**要点**：MiroMind DR 不是有问题，是**别当编排脑**——它为"自己上网研究"造，不为"可靠驱动自定义工具"造（代码里 `ToolCallingUnsupported` seam + 用 DeepSeek 测 tool-calling 已撞到此）。摆回"叙事研究专员"完美。成本也对：编排脑跑得勤 → 便宜快；DR 贵慢 → gated 当工具。`client.py` 已 provider 可配，测试期把编排脑在 MiroMind mini / DeepSeek 间 A/B 即可定。（MiroMind function-calling 能力系从代码信号推断，非独立实测。）

---

## 期权分布 channel · 完整规格（细化 O2 · `[框架 LOCKED / 数值细节可微调 2026-07-04]`）

**统一原则**：期权分布不绑个股——它是**instrument 级能力**，高度栈告诉你读哪个标的的期权：公司=个股（NVDA）· 行业=主题 ETF（SMH/SOXX）· 宏观=指数 ETF（SPY/QQQ）+ VIX。所以每高度层 = **可测半（exposure 回归）+ 分布半（该层期权）+ 叙事半（gated DR）**。全部确定性 Python，无 LLM。

**每个 instrument 提取 5 个量**：

| 量 | 怎么算 | 输出 / 卡面话 |
|---|---|---|
| 隐含波动区间 | 最近月 ATM straddle 价 / 现价 → ±X% | "3 个月 68% 落在 $185–248" |
| 整条 RND | vol smile（各行权 IV）→ Breeden-Litzenberger：密度 ∝ ∂²C/∂K²（**先平滑拟合 smile 再取二阶差分**，防二阶导噪声）| 支撑下面两条 |
| prob-of-target | RND 对目标价以外的尾部积分 | "你的 $300 目标在 ~8% 尾巴" |
| skew | 25Δ put IV − 25Δ call IV → **换成对自身 1y 的分位**（F3；绝对 skew 永远偏 put）| "下行保险比自己近一年更贵 / 更平" |
| term structure | 近月 vs 远月 IV | "近月更贵 = 在定价财报 / 催化风险" |

**跨高度 vol 分解（新信号）**：个股 IV vs VIX / 主题 IV → 把"隐含风险"拆成 自身 / 主题 / 大盘 vol（与拆**价格**是镜像）→ "NVDA 自己平静，但主题 SMH / 大盘 VIX 在定肥左尾 = 相对自满、脆"（单看个股永远看不出，得跨高度比）。

**喂决策层（O5）**：RND = 市场开的赔率分布 → edge = 你的 view 落在哪个分位；tail 概率 = Kelly 的市场赔率侧；价格型 KILL 的隐含触发概率直接从 RND 读。

**horizon**：期权覆盖数周–数月（F4）→ 分布带 horizon 标签，**不直接对 DCF 的 5y**。

**退化 / 边界（诚实）**：流动性差（价差宽 / 行权稀疏）→ 只出 ATM 隐含区间，跳过完整 RND，标数据质量；个股无期权 → 该层分布 honest-empty，靠主题 / 指数 ETF 期权 + beta 兜底；确切字段（IV vs 价、VIX term）= qveris，实现期对齐（O6）。

---

## 7. 待打磨（细节，一起做）`[OPEN]`

大框架之外，以下都还没对齐，是接下来一起打磨的议程：

| # | 开放问题 |
|---|---|
| ~~O1~~ | ✅ **Closed 2026-07-04** — 见 §2「原语规格」。state = panel/investigation/reconciliation/series_link/lineage/decision?；可变性 = 揭示/深挖不分叉·改假设才分叉·全程不可变；落库 = 扩 `decode_detail`(v3→v4) + 复用血缘三件套 + 复用 series 去重 + 新增 `panel_observations` append-only 表。|
| ~~O2~~ | ✅ **Closed 2026-07-04** — MVP panel = DCF + 期权 + consensus + 轻量高度归因（市场 beta + 主题 ETF beta；行业篮子用现成 ETF 如 SMH/SOXX 代理）；叙事深挖 gated + 按高度（macro/industry/company）打标；富因子模型 + positioning 缓 v1.1；panel 按单卡定义，组合视图 fast-follow。|
| ~~O3~~ | ✅ **Closed 2026-07-04** — panel 全量常跑，agent 只支配注意力（深挖 / 开 gate / 领衔 / 对账 / 答追问）。花钱动作由**便宜确定性信号提名候选**（如 `narrative_premium≥50%` 提名叙事深挖），agent 只在候选里排序 / 选择 / 叙述、无权凭空开贵调用。`decode_bet_agentic` 的 plan 从"选装配器"升级成"给定全 panel → 分配注意力"，装配复用既有。|
| O11 | **主动监控细节**（方向已锁 events-first，见 §6「主动监控」）：待优化 = 连续自指漂移触发、事件间持续 drift 报警、feed/push 具体形态、KILL 自动检测。|
| ~~O12~~ | ✅ **Closed 2026-07-04** — 模型分配：数据层确定性 Python / 编排脑 function-calling（DeepSeek，非 MiroMind DR）/ 叙事 DR = MiroMind 当 gated 工具 / 便宜叙述用 mini。见「模型分配」。|
| ~~O4~~ | ✅ **Closed 2026-07-04** — 见「呈现层 · 年轻化重设计」。年轻化交易 App 视觉（深色/卡片流/一屏一焦点，参考 `mockup_v2_young.html`）；结构 = 持仓终端非报告；追问 = 底部问-agent 聊天栏。|
| ~~O5~~ | ✅ **Closed 2026-07-04** — 决策层出可执行 Trade Plan（stance/strategy archetype/entry/size/stop+KILL/exit + self_falsification）；执行分阶段（一期展示手动 → 二期 sandbox API → 三期真券商+人工 approve）；模拟盘"游戏"砍掉；合规进 O10。见「决策层 · Trade Plan」。 |
| ~~O6~~ | ✅ **Closed 2026-07-04** — 数据源用 **qveris.ai**（付费金融数据一站式，覆盖期权链 + estimate 修正）；Deep Research 沿用 **MiroMind API**。构建"分布账/变化账"channel 时再对齐 qveris 的实际 API 形态（原始链 vs 已算 IV 指标）——实现期 follow-up。接入用 qveris **CLI**（qveris.ai/docs/cli）+ 用户 token，测试验证期再登。|
| O7 | **新栈的成本模型**：逐 channel 成本 + 门控策略下的单卡总成本，对齐 $100 纪律。 |
| ~~O8~~ | ✅ **Closed 2026-07-04** — 卡面格式 = young 持仓卡（一句解码 + sparkline + 状态点），详情页一屏一块；并入 O4。 |
| O9 | **命名 / 定位**：核心已从"解码一个 bet"漂到"分解市场预期"，产品名是否仍是 Bet Decoder。 |
| O10 | **合规边界复核**：分布化决策层下重新确认"不个性化投顾、不碰真钱、决策自带认错条件"。 |

---

## 8. 现有 codebase 存活映射 `[参考]`

| 现有 | 新角色 | 改动 |
|---|---|---|
| `reverse_dcf.py` + 7 lens | 现金流账的 kernel（栈里的一条） | 无需改；从"主引擎"降级成"一条 channel" |
| `narrative.py` | 高度栈的**叙事半**：按高度（macro/industry/company）打标的 gated 深挖 | 定位调整 + 输出加高度标签 |
| `intelligence.py`（`_kelly` / base_rate） | 决策层的仓位/外部视角零件 | 输入换成期权隐含概率 |
| 不可变快照 + `derived_from` 血缘 | "解码"的快照 / 分叉机制 | 存活并升级为调查史 |
| activity SSE 流 | agent 真实调查轨迹（不再是树自述） | 从 theater 变成真内容 |
| `agent_tools.py` | agent 的**注意力工具集**（深挖某 channel / 开叙事 gate / 跑 what-if / 答追问）；panel kernel 走确定性 sweep，不经 agent 选择 | 扩展 |
| `app.html` | 呈现层 | 整体重做为年轻化交易 App（深色卡片流，参考 `mockup_v2_young.html`）；`pricelens_design_system.md` 作废待重写 |

---

> **下一步**：从 §7 开放项里挑一条开始打磨。技术上最该先探掉的是 **O6（期权链 + estimate 修正数据可行性）**——它是"点→分布"整个方向的地基；产品上最该先钉的是 **O1（新原语规格）** 或 **O4（呈现层交互）**。
