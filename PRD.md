<!-- v2.0 · 2026-07-04 · 多框架预期分解引擎 + agentic 呈现定稿 · 大框架 LOCKED / 细节标 [OPEN] 待对 -->
<!-- 取代：PRD v1.0（5模块单框架）、docs/archive/DIRECTION.md v0.3、docs/archive/TRADING_AGENT_PRD_v0.1、docs/archive/BET_DECODER_VISION_v0.7 §1 -->
<!-- 术语见 docs/glossary.md；工程上下文见 PROJECT_CONTEXT.md；API 见 API_CONTRACT.md -->

# Bet Decoder — PRD v2.0（多框架预期分解引擎定稿）

> 本文档由 2026-07-04 方向对齐冻结而成。大框架已 LOCKED，标 `[OPEN]` 的是待第二轮细节对齐的条目（见 §12）。
> 是后续技术拆解与代码实现的唯一权威输入。改动需走变更评审，不在主对话随手改。
> **与历史文档的关系**：本 PRD 取代 v1.0 的 5 模块单框架结构、`DIRECTION.md` v0.3 的方向草案、`TRADING_AGENT_PRD` v0.1 的决策层框架、`BET_DECODER_VISION` v0.7 §1 的原语定义——前三份原件移至 `docs/archive/` 留档。

---

## §0 一句话定位 + 为什么有这次升级

### 0.1 一句话

> **把产品的核心从"反向 DCF 解释一个隐含数字"升级为"用一栈正交框架把市场预期分解成多条 channel、做跨框架对账、并把这场分解做成一个 agent 驱动、可操纵的调查"；决策纪律层挂在它之上。**

头牌 = **预期透明化 + 决策纪律**，不是"trading agent / 模拟盘"。模拟盘是被商品化的东西（每个券商都有 demo account），而"市场预期随时间的漂移做透明化"没人系统做过，是独有护城河，也更贴近"让隐性知识平权"。

**核心对象是"变化"不是"快照"。** 散户体验市场是价格 ticker；专业玩家体验市场是一条**预期修正流**。把"价格是预期的下游 + 预期在怎么漂"做可见，是最真的一次平权。paper trading 降级成 commitment device + demo 道具，不是游戏化账户。

### 0.2 为什么有这次升级

现有产品（v1.0）是一条纯诊断链，且诊断只用了**一个框架**（反向 DCF）去解释"市场隐含相信什么"。两个问题：

1. **一个框架只照亮一条 channel。** 价格里叠着好几层预期，反向 DCF 只反解现金流那层。TSLA "无 DCF 解"不是 DCF 失败，是市场在说"这个价格由非-DCF 的 channel 主导"——DCF 能探测残差，解码不了。
2. **产出是一张固定模板卡 = 研报感的根因。** 只要底下推理是确定性决策树，输出就*能*被灌进固定 slot，于是无论怎么美化都像研报，不像 agent（这正是 2026-06-01 "theater" 批评的结构根因）。

**方向修正一句话**：推理框架层做深（多框架），呈现层做活（agentic），且后者是前者的结果不是并列任务。

---

## §1 原语规格 `[LOCKED 2026-07-04]`

### 1.1 原语迁移

- **旧原语 `Bet Card`（静态名词、你收到的产物）→ 降级成"快照"。**
- **新原语 = 一次"解码 / 调查"（过程、agent 驱动、可操纵的对象）。** 卡 = 这场调查在某一刻的一张冻结快照。
- **现有架构存活且升级**：不可变快照 + `derived_from` 血缘在旧模型里只是"存一张卡"；在新模型里变成**真正的调查史**——沿途冻快照、用 what-if 分叉衍生卡 = 把调查往另一支探。血缘图第一次有该有的语义。
- **北极星比喻**：现在的卡是一封**格式信**（固定填空）；目标是一个**你能看着他干活、还能随时打断提问的分析师**——按票不同用不同查法 / 开口先讲最要命那条 / 你追问他把相关那条 channel 拉到面前 / 你能看他怎么推。四条都在"透明"一侧，没有一条是"你该买"。

### 1.2 Decode 的 state

```
Decode {
  subject / source     # 被解码的 bet + 原始 input
  panel[]              # 全量便宜 kernel 的结果，每条带 horizon 标签
  investigation        # agent 注意力轨迹：深挖了哪条 / 贵 gate 开没开 / 卡面排序
  reconciliation       # horizon 桶内的跨证人对账（一致 / 分歧 + headline）
  series_link          # 时间轴：同 subject 的解码序列（漂移长在这）
  lineage              # 分叉轴：derived_from + 分叉类型
  decision?            # 可选：决策层跑过才有的 Trade Plan
}
```

两根轴正交：**series_link 答"预期在怎么漂"（时间）**，**lineage 答"假设不同会怎样"（反事实）**。

### 1.3 可变性 · 一条判定线

*这次互动改没改任何输入 / 假设？*

| 互动 | 例 | 改假设? | 系统行为 |
|---|---|---|---|
| 揭示 | "为什么说拥挤?" | 否 | 原地解释，不新建 |
| 深挖 | "叙事挖深一点" | 否 | 开 gate，investigation 增厚（只增不改），不新建 |
| 改写 | "增速只有 20% 呢?" | 是 | 分叉出新的不可变节点，`derived_from=父`，父一字不动 |
| 序列 | 次日重解码 | —（新观测）| 新节点，与父是 series 关系（非分叉）|

**git 语**：解码=commit · 序列=main 上累积的 commit · what-if=从某 commit 拉 branch · 追问=`git show` · 不可变=永不 force-push。坚持不可变的三个理由：诚实可审计（"上月市场隐含什么"永远可查）+ 漂移曲线要求每点冻结 + 它直接铸造两种手感（追问轻 / what-if 重）。

### 1.4 落库

扩展既有为主 + 一张小观测表为辅：

| 概念 | 落到 | 改动 |
|---|---|---|
| 解码 state | `decode_detail` JSON | v3→v4 幂等迁移，扩 JSON 形状 |
| 分叉轴 | `derived_from` / `derivation_kind` / `derivation_json` | 零新机制（6/1 已建）|
| 序列轴 | series 分组 + 日去重索引 | 语义升级；去重当初就设计成排除衍生卡 = 分叉天然不污染序列，零改动 |
| **漂移观测点** | **新表 `panel_observations(subject, as_of_date, channel, metrics_json, horizon)`，append-only** | 唯一新表。历史回填 + 每次解码的 panel 都落一份；**观测（数据点）≠ 解码（调查）**，不塞 `bet_cards` 免造几百张幽灵卡 |

---

## §2 预期分解栈 `[LOCKED 方向 / OPEN 具体条目]`

### 2.1 核心原则：多 channel = 审讯同一价格的多个独立证人

多条 channel 是**审讯同一个价格的多个独立证人**，对账 = 交叉质证——**不是价格的加法分解项**（别试图让各账加总到 100%）。每条 channel 配成熟框架、输出一句白话、**带 horizon 标签**。

> **加法对账的边界**：channel 之间不做全局加法分解（"DCF 解释不了的部分 → narrative premium" 这种只是证人之间的一对关系，不是全局结构）。但**叙事半（高度栈·B5）内部**允许对账——叙事成分是同一高度栈内部的结构化分解，`base_business_value + 叙事成分 sum` 对账回现价是合法的局部对账（验证叙事分解的自洽性），不等于把多条 channel 加总到 100%。

价格里叠着的预期层（示意，非加法恒等式）：

`现金流价值 · 分布/风险定价 · 预期修正动能 · 持仓拥挤度 · 宏观/因子 beta · 叙事溢价`

### 2.2 Channel 清单

| Channel | 框架 | 输出白话（示意） | 成本 | 状态 |
|---|---|---|---|---|
| 现金流账 | 反向 DCF + 倍数 lens（已有 7 lens） | "需要 ~38% 长期增速——历史极少数公司做到" | 免费·无 LLM | 有 |
| **分布账（keystone）** | **期权隐含分布**（IV 曲面 / skew / term structure） | "3 个月定价 68% 落在 $180–250；下行保险比它自己近一年更贵" | 便宜·无 LLM | **建议先做** |
| 变化账 | consensus estimate 修正 + 分歧度 + 价格反应函数 | "90 天 estimates 上修 12%，价格涨 25%——价格跑在预期前面" | 便宜 | 建议第二 |
| 拥挤账 | 空头 / 持仓 / 资金流 | "空头回补空间小 + 基金持仓已高——这个多头 bet 很脆" | 便宜 | 缓 v1.1 |
| **高度栈**（合并老"因子账+叙事账"）| 按高度分层 macro/industry/company，每层 = 可测半 + 叙事半（见下）| "这价格 60% 是 AI-beta，40% 才是它自己的 bet" | 可测半便宜 / 叙事半贵·门控 | 轻量可测半进 MVP·叙事半 gated |

### 2.3 高度栈展开

叙事不是一条 channel，是按高度分层的深挖；三层的理解框架 / horizon / 共享范围完全不同（修"叙事太笼统"）：

| 高度 | 理解框架 | horizon | 谁共享 | 可测半（cheap·panel）| 叙事半（gated DR）|
|---|---|---|---|---|---|
| 宏观 | 利率 / 流动性 / risk appetite | regime，月-年 | ~所有资产 | 对宏观因子的 beta | "市场信软着陆、降 3 次" |
| 行业 | 周期位置 / 渗透率 S 曲线 / 竞争结构 | cycle，1-5y | 同主题的票 | 对主题篮子的 exposure（**MVP 用现成主题 ETF 如 SMH/SOXX 代理**）| "AI-capex 复利到 2027" |
| 公司 | 催化剂 / 执行 / 护城河 | 因名而异 | 该票独有 | 剥掉宏观+行业后的 idiosyncratic 残差 | "ASIC 替代慢于预期" |

**每层还有"分布半"**：读该层标的（个股 / 主题 ETF / 指数+VIX）的期权隐含分布——每层 = 可测半（exposure）+ 分布半（期权）+ 叙事半（gated DR），VIX 进宏观层。详见 §3。

### 2.4 MVP panel

**MVP panel = DCF + 期权 + consensus + 轻量高度归因**（市场 beta + 主题 ETF beta 两个回归，切 macro / 主题 / 公司 粗三分）；叙事深挖 gated 且**按高度打标**（永不吐笼统 "narrative"）；富多因子模型 + positioning 缓 v1.1；panel 先按单卡定义，组合视图 fast-follow 复用现有 parent→constituents。卡面因此拿到 "这价格 X% 是主题 beta、只有 Y% 是它自己" —— Aha B 从组合下放到单卡。

### 2.5 四个不可动摇的原则

1. **keystone 是"点→分布"的跃迁。** 反向 DCF 给一个*点*，期权市场给市场定价的*整个概率分布*——"市场预期"从一个数变成一个*形状*，这才是它真实的样子；而且"68% 落在 X–Y"比"隐含 CAGR"更好懂，平权不降反升。**但口径必须诚实**：期权分布是 risk-neutral（Q 测度）——它是*市场为各种结局开出的价格*，不是市场信念的真实概率，卡面白话按"市场开价"的口径说；skew 类白话必须报**相对自身历史/同行的分位**（股票期权 skew 结构性偏 put 侧，"在为下跌付钱"是常态不是信号；call 侧被抢筹倒挂反而是 euphoria/拥挤信号）。
2. **洞察在跨框架对账，不在任何单框架。** 当 DCF-隐含增速、期权-隐含分布、consensus 修正三者打架时，分歧本身就是信号（"基本面平、期权平静、但价格涨 25% 靠资金流 = positioning-driven 假涨，很脆"）。这是已有的 cross-card synthesis 原则，从 cross-**card** 挪到 cross-**framework**。
3. **对账必须 horizon-aware，但不再被 horizon 桶隔离。** DCF 隐含 5 年、期权覆盖数周到数月、consensus 修正以季度计——不分期限的对账会把 term structure 误报成矛盾。v2.0 采用**具名跨 channel checks**（价格 vs 基本面、估值 vs 期权、预期动能 vs 期权、集中度）产出 aligned/divergent/contradiction；跨期限 level 差异只进 `term_structure` descriptive 层，永不判矛盾。
4. **成本纪律：便宜信号门控贵信号。** panel（近乎免费）常跑盯着，只有穿过阈值才触发一次贵的 narrative Deep Research 去查"到底发生了什么"。

---

## §3 期权分布 channel · 完整规格 `[框架 LOCKED / 数值细节可微调 2026-07-04]`

### 3.1 统一原则

期权分布不绑个股——它是**instrument 级能力**，高度栈告诉你读哪个标的的期权：公司=个股（NVDA）· 行业=主题 ETF（SMH/SOXX）· 宏观=指数 ETF（SPY/QQQ）+ VIX。所以每高度层 = **可测半（exposure 回归）+ 分布半（该层期权）+ 叙事半（gated DR）**。全部确定性 Python，无 LLM。

### 3.2 每个 instrument 提取 5 个量

| 量 | 怎么算 | 输出 / 卡面话 |
|---|---|---|
| 隐含波动区间 | 最近月 ATM straddle 价 / 现价 → ±X% | "3 个月 68% 落在 $185–248" |
| 整条 RND | vol smile（各行权 IV）→ Breeden-Litzenberger：密度 ∝ ∂²C/∂K²（**先平滑拟合 smile 再取二阶差分**，防二阶导噪声）| 支撑下面两条 |
| prob-of-target | RND 对目标价以外的尾部积分 | "你的 $300 目标在 ~8% 尾巴" |
| skew | 25Δ put IV − 25Δ call IV → **换成对自身 1y 的分位**（绝对 skew 永远偏 put）| "下行保险比自己近一年更贵 / 更平" |
| term structure | 近月 vs 远月 IV | "近月更贵 = 在定价财报 / 催化风险" |

### 3.3 跨高度 vol 分解（新信号）

个股 IV vs VIX / 主题 IV → 把"隐含风险"拆成 自身 / 主题 / 大盘 vol（与拆**价格**是镜像）→ "NVDA 自己平静，但主题 SMH / 大盘 VIX 在定肥左尾 = 相对自满、脆"（单看个股永远看不出，得跨高度比）。

### 3.4 喂决策层

RND = 市场开的赔率分布 → edge = 你的 view 落在哪个分位；tail 概率 = Kelly 的市场赔率侧；价格型 KILL 的隐含触发概率直接从 RND 读。详见 §4。

### 3.5 horizon

期权覆盖数周–数月 → 分布带 horizon 标签，**不直接对 DCF 的 5y**。

### 3.6 退化 / 边界（诚实）

流动性差（价差宽 / 行权稀疏）→ 只出 ATM 隐含区间，跳过完整 RND，标数据质量；个股无期权 → 该层分布 honest-empty，靠主题 / 指数 ETF 期权 + beta 兜底；确切字段（IV vs 价、VIX term）= qveris，实现期对齐（§12 O6）。

---

## §4 决策层 · Trade Plan + 可插拔执行 `[LOCKED 方向]`

### 4.1 修正

`TRADING_AGENT_PRD` v0.1 以 paper-trading 模拟盘为中心；本 PRD 取代其框架——**决策层输出一个可执行的 Trade Plan 对象，"执行"是可插拔后端，分阶段从安全到真实接**。区分两件被混淆的事：**闭环**（plan→执行→跟踪，要）vs **游戏**（假组合 P&L 排行榜，不要——被商品化 + 教人盯短期噪声）。

### 4.2 Trade Plan 结构

分析 + 交易策略拧成一个方案；`TRADING_AGENT_PRD` 零件全活，框架替换：

- **stance / 方向**：贵/便宜 + 风险回报对称性（来自分析）。
- **strategy archetype**（"结合交易策略"的落点）：分析状态 → 打法。MVP 几个干净原型：便宜+催化 → 分批建仓带 stop；高估+下行不对称 → 减/不加/对冲；无 DCF 锚+高叙事 → 小仓动量紧 stop；拥挤+脆 → 减仓收紧 stop。
- **entry**（价位/条件，可分批）· **size**（Kelly §4.4 修正版 + 上限）· **stop + KILL**（KILL 分型：价格 stop 期权可观测 / 基本面认错线走 consensus）· **exit/止盈**（论点兑现或 edge 消失）。
- **透明 + self_falsification 强制**：方案自带"为什么 + 我错了会怎样"——区别于黑箱荐股 App 的根本；低信心/小 edge → 方案可为"不动"，不硬造交易。

### 4.3 决策字段升级（预期分布化后）

一旦预期是"分布"而非"点"，决策字段全部升级：

| 字段 | 旧（v0.1）| 新（v2.0）|
|---|---|---|
| edge | 你的 view vs 隐含的一个点 | 你的 view 落在市场隐含分布的哪个分位 + 哪些 channel 支持/反对你 |
| Kelly 输入 | p_win 与赔率全靠 base_rate 手估 | **期权分布给的是"市场开出的赔率"（Q 测度），不是你的 p_win**——Kelly = f(你的 p, 市场赔率)：你的独立 view（base_rate 那套，原设计是对的）仍给 p，期权给赔率与市场 view；风险中性概率直接当 p_win 会系统性偏差（put 因保险需求偏贵）|
| KILL / self_falsification | 一句可证伪的话 | **分型**：价格可观测型 → 期权报隐含触发概率（可量化）；基本面型（"营收连 2 季破 11%"）→ 期权定价不了，走 consensus 修正跟踪 |
| conviction | 单点判断 | 由**跨 channel 一致性（同 horizon 桶内）**决定：都指同向 = high，互相矛盾 = low（矛盾本身是最诚实的信号）|

### 4.4 决策规则零件（来自 TRADING_AGENT_PRD §2，升级版）

> 以下是**产品级规则**，定义 agent 的判断行为，不是代码。参数已 LOCKED 2026-07-04。

**stance 判定**：诊断信号 → 方向（accumulate/strong_buy/hold/trim/avoid/**short**）`[含 short · LOCKED]`。升级：stance 现在由**跨 channel 对账**驱动而非单一 DCF 信号——DCF 贵但期权平静 + consensus 上修 = 可能 hold 而非 avoid。

**conviction 判定**：high = 跨 channel 同 horizon 一致 + 证据支持；medium = 任一存疑；low = channel 互相矛盾 / 证据 honest-empty / 纯叙事。

**仓位规则**：① 期权 RND 给市场赔率 → 算原始 Kelly 分数 `f*`；② 取 **¼ Kelly**（fractional，防过度下注）`[LOCKED]`；③ 按 conviction 打折：high ×1.0 / medium ×0.6 / low ×0.3 `[LOCKED]`；④ **单仓上限 20%**、**账户现金下限 10%** `[LOCKED]`；⑤ conviction=low 且 edge 小 → 目标权重可为 0（不硬凑交易，见 §4.5）。

**止损 / 认错（KILL line）**：分型——价格可观测型 KILL → 期权报隐含触发概率（"你的 stop $180 在 RND 的 ~12% 尾巴"）；基本面型 KILL（"营收连 2 季破 11%"）→ 期权定价不了，走 consensus 修正跟踪。每张 Plan 强制带 KILL。

### 4.5 什么时候不交易（诚实约束，护城河）

- conviction=low **且** edge 未超过 **2%** `[LOCKED]` → 默认 **hold / 空仓**，明确输出"不值得下注"而非硬造一个买卖。
- 证据 honest-empty（provider 无法联网核实）→ 不得据此提升 conviction，理由里注明"未经网络验证"。
- **每张 Plan 强制填 `self_falsification`**——没有认错条件的决策不允许生成。这是本产品区别于黑箱喊单的根本。

### 4.6 执行后端 · 分阶段

Plan 是稳定接口，后端往上插；P&L 全程安静跟踪，非首页游戏：

| 阶段 | 后端 | 性质 |
|---|---|---|
| **一期** `[LOCKED]` | Plan → **展示，用户手动去券商执行**（entry 用 decode 现价、不加滑点 `[LOCKED]`；KILL 撞线只告警+建议不自动平仓 `[LOCKED]`）| 软件不下单；闭环 = 方案 + 你执行 + 监控盯 KILL |
| 二期 | Plan → 券商 **paper/sandbox API**（如 Alpaca paper）| 真实下单管线、假钱；同一套代码以后切真 |
| 三期 | 真实券商 + **每单人工 approve** | 需合规，AI 不代下单 |

---

## §5 呈现层 · 年轻化重设计 `[LOCKED 2026-07-04]`

### 5.1 视觉语言整体转向

从"Goldman 研报"（austere、密、禁深色/圆角/渐变）转到 **年轻化交易 App**（Robinhood / 长桥 / crypto 交易所 / RockFlow）。**持仓列表**用 young 卡片（`mockup_v2_young.html`，vibe 已确认）；**单卡详情**用增强股价图（`mockup_enhanced_chart.html`，见 `docs/SPEC_E`）——图为主体、点元素出 AI 白话，取代 young mockup 里的分块详情。两者互补非冲突（review 修正）。

> ⚠ `pricelens_design_system.md`（旧视觉契约）已作废，移 `docs/archive/`；待 vibe 落地后按新语言另立新设计系统文档。

**视觉铁律（新）**：深色底、卡片流、**一屏一个焦点**（砍信息量）、超大 tabular 数字、涨绿跌红用**鲜亮**版、图表带柔和渐变面积填充、Inter 圆体、AI/agent 用紫色强调。

**结构隐喻**：不是"从上读到下的报告"，是"**持仓终端 / 移动 App**"——扫卡片 → 点开钻取；章节/exhibit 语法退到单卡展开后的内部。

**追问 = 底部"问 agent"聊天栏**（RockFlow 招牌 AI 味 + §1.3 的"追问"交互合一）；改假设 = 分叉衍生卡（§1.3 决策不变）。

### 5.2 一期范围（scope）`[LOCKED 2026-07-04]`

- **持仓中心**。通用解码（解析推文 / 分析师目标价 / 自由文本任意 bet）**延后**，不进一期。
- 一期入口 `+` = **导入组合 / 加持仓**（ticker + 权重），不是通用 bet 解码。
- **"买入前先解码候选 ticker" 限制进入一期** `[修正 2026-07-04]` — 一期主路径仍是持仓扫描（导入已有持仓 → 解码 → 对账 → 决策），但允许 `watchlist/candidate` 模式：用户可解码未持有 ticker，系统只输出 Decode + "若你考虑建仓"的 preview Trade Plan，不进入主动 KILL 监控，除非用户手动确认建仓并写入 `position_ledger`。这避免砍掉真实买前研究需求，同时保持"监控只盯确认持仓"的诚实边界。

### 5.3 跨卡综合 = 组合内综合（收敛）

不是独立功能，是**持仓页的第二层**：`持仓页 = 顶部一张组合综合卡（读 across）+ 下面一列 position 卡`。

综合卡形态 = **高度栈跨持仓聚合**（"你整个组合 58% 在赌同一个 AI 主题——你以为分散了" = Aha B，算法复用）+ 跨持仓矛盾 / 共享 KILL 标记（"NVDA 要 hyperscaler 加 capex，GOOG 要它控成本——打架"）。

安全丢弃：Aha A（你 vs 分析师）随通用解码延后，一期不缺；Aha D（跨时间对比）已被漂移脊椎 / 监控覆盖。

---

## §6 主动监控 · 引擎的第二入口 `[LOCKED 2026-07-04]`

反应式解码之外，引擎有第二个入口：**调度发起的主动监控**——替用户盯住持仓 + 保存过的解码，出事主动 report。共享同一注意力环，几乎全复用已有零件（panel 常跑 + `panel_observations` + 漂移脊椎），是很薄的一层。它一次兑现两个 pillar：盯的是"预期的变化"（透明化），最好的触发是用户的"决策红线"（决策纪律）。

| 维度 | 规格 |
|---|---|
| **scope** | 持仓 + 保存过的解码（复用组合原语）|
| **cadence** | 按日（对齐"新鲜度按天"；日内不做）|
| **触发（MVP）** | **重大事件**（财报 / 评级 / 大跳空，用 qveris 事件源）。选事件优先：离散、无歧义、不用调噪声阈值、正好是"第一时间该扫"的时刻。**白赚**：基本面型 KILL line（"增速连 2 季破 11%"）本就只在财报时可观测 = 天然事件驱动，events-first 仍能第一时间抓到"论点被打破" |
| **连续漂移触发** `[LOCKED]` | **MVP 不做，缓 v1.1**——保持 events-first 纯粹，最快跑起来。v1.1 再加"连续 N 日 panel 分位漂出"独立触发（需调阈值，是 events-first 跑通后的优化） |
| **事件间 drift 报警** `[LOCKED]` | **只在事件时带出 drift**——drift 搭事件的车，不 nag。事件触发的报告顺带把"自上次解码以来预期怎么漂的"带出来，不单独在事件间推送 drift 摘要 |
| **report 形态** | 不是重写全报告，是**变化摘要**——自上次解码以来动了什么、为什么撞到你承诺的 thesis。**事件是触发时机，报告顺带把"这段预期怎么漂的"带出来**（漂移的差异化价值搭事件的车，不因 events-first 丢掉）。便宜默认（只读 panel），够严重才升级开一次 narrative DR |
| **delivery 分层** `[LOCKED]` | 论点被打破 / 严重 → 第一时间 push；例行变化 → 工作台"变化 feed"累积、用户来看（不 nag）。**MVP feed/push 形态 = 工作台内 feed 累积 + 浏览器推送**（不碰邮件/移动端，工程量最小；离线覆盖与移动端缓 v1.1+）。**诚实边界**：报告"变了什么 + 撞到哪条 KILL"，不说"你该卖" |
| **KILL 自动检测** `[LOCKED]` | **事件触发时顺带查 KILL**——复用事件管道，不单独建检测环。每日独立扫所有持仓的 KILL line（更实时但更重）缓 v1.1；价格型 KILL 走期权隐含触发概率实时算、基本面型走事件管道的分型检测（§4.4 KILL 分型）缓 v1.1+ |
| **排期** | 反应式引擎是前置依赖；监控是**头牌能力不是 P2**——纠正 `TRADING_AGENT_PRD` 把 T11 放 P2 的错 |

---

## §7 模型分配 `[LOCKED 2026-07-04]`

一个模型不干两种活——拆角色：

| 角色 | 干什么 | 模型 |
|---|---|---|
| **数据层** | 拉 qveris（标准 panel 确定性 + 追问按需）| 不是模型——确定性 Python；仅"追问按需"那条走 harness tool-call |
| **编排脑**（agentic harness）| §1.3 注意力环 / gate 叙事 DR / what-if / 答追问——需**可靠 function-calling** | 便宜快、可靠 function-calling 的模型（DeepSeek-V4-Pro 现成）；**不是 MiroMind DR** |
| **叙事 DR 工具** | 叙事半 gated 深挖（自己上网研究）| **MiroMind flagship deep-research**（已集成 `narrative.py`），当一个 gated 工具被编排脑调用 |
| **便宜叙述** | 白话卡面文案 + 跨卡综合文本 | 便宜 chat 模型（复用编排脑或 mini）|

**要点**：MiroMind DR 不是有问题，是**别当编排脑**——它为"自己上网研究"造，不为"可靠驱动自定义工具"造。摆回"叙事研究专员"完美。成本也对：编排脑跑得勤 → 便宜快；DR 贵慢 → gated 当工具。`client.py` 已 provider 可配，测试期把编排脑在 MiroMind mini / DeepSeek 间 A/B 即可定。

---

## §8 数据模型与公共接口

> v1.0 的 schema 与接口存活并升级。本节是技术拆解的输入，具体 DDL 见 `db.py`。

### 8.1 数据模型（v1.0 存活 + v2.0 新增）

| 表 | 状态 | 关键列 | 服务于 |
|---|---|---|---|
| `bet_cards` | v1.0 存活 | `card_id, subject, source_type, card_kind, source_ref, series_key, created_at, run_id, derived_from, derivation_kind, derivation_json`（v3 血缘）| 卡身份 + 调查史 |
| `runs` | v1.0 + 补列 | `anchor_price, anchor_type` + implied driver 子表带蒙特卡洛 band p25/p75 | 单股卡复用 |
| `portfolio_holdings` | v1.0 存活 | `card_id, ticker, weight_pct, run_id` | 组合卡持仓 |
| `theme_exposures` | v1.0 存活（单股+组合共用）| `card_id, theme, exposure_pct, contributing_tickers, is_concentration_risk` | 高度栈归因 + 同源比对 |
| `llm_cache` | v1.0 存活 | 增 category=`"synthesis"` | 综合缓存 |
| `activity_logs` | v1.0 存活 | `job_id, source_ref, events_json, created_at` | 活动流回放 |
| **`decode_detail`** | **v2.0 升级** | JSON 列扩 v3→v4：存 panel[]/investigation/reconciliation/series_link/lineage/decision? | §1.2 Decode state |
| **`panel_observations`** | **v2.0 新增** | `subject, as_of_date, channel, metrics_json, horizon`，**append-only** | 漂移观测点（§1.4）|

### 8.2 公共接口契约（v1.0 存活 + v2.0 扩展）

```python
# 数据层（被动存储）
save_card / get_card / list_cards / delete_card
card_to_json / card_from_row / build_card_display  # v1.0 + v3 _display 投影

# 解码（v2.0：agentic PRIMARY，panel 常跑）
decode_bet_agentic(source_type, source_input, lang, emit=None) -> BetCard
#   panel 全量常跑（§2.4）→ agent 分配注意力（深挖/gate/领衔/对账）→ 复用既有装配器
#   气密回退确定性 decode_bet（provider 不支持工具调用 / 异常 / 离线）

# 调查交互（v1.0 agentic 层存活）
answer_followup(card_id, question, ...) -> 回答  # 揭示/深挖，不新建
propose_revision(card_id, what_if, ...) -> diff  # 改假设，返回 before→after，不落库
build_revised_card(card_id, revision) -> 新衍生卡  # 确认后存，derived_from=父

# 跨卡综合（v2.0 收敛为组合内综合）
synthesize_cards(card_ids, lang, emit=None) -> SynthesisResult  # 高度栈跨持仓聚合

# 决策层（v2.0 新增）
build_trade_plan(card_id) -> TradePlan  # §4.2 结构
# 活动流（v1.0 存活）
# emit(ActivityEvent) 回调契约 + SSE 端点 + activity_logs 落库 + 回放
```

### 8.3 产物结构（v1.0 存活 + v2.0 扩展）

```
TradePlan {
  stance, strategy_archetype,
  edge: {view_quantile, supporting_channels[], opposing_channels[]},
  conviction,  # 跨 channel 一致性决定
  entry, size: {kelly_fraction, target_weight, cap},
  stop, kill: {type: price|fundamental, line, implied_prob?},  # 价格型带期权隐含概率
  exit, rationale, self_falsification
}

Decode {  # §1.2，存 decode_detail JSON
  subject, source, panel[], investigation,
  reconciliation, series_link, lineage, decision?
}
```

---

## §9 落地节奏 + 实现阶段 `[LOCKED]`

### 9.1 做大改的野心 + 切入的节奏

目标是完整重构（否则又把通用能力当 single-purpose 用，重蹈 v0.6 覆辙），但从**期权那一条 channel 切入验证**——它最能证明"点→分布"跃迁对不对，且几乎免费。

推荐加入顺序：

**⓪ 漂移脊椎**（不是一条 channel，是脊椎交付物）——对历史逐时点重跑便宜 kernel，产出"隐含假设时间序列叠在价格上"的 hero visual。**主路径（review 修正）= 价格 + 期权隐含分布的历史**（两者天然 point-in-time，历史期权链 EODHD 可得）+ **价格驱动的隐含增速线**（历史价 + 当前 fundamentals 固定 → 反解"价格变动如何改变了这个赌注"）。**estimate 驱动的漂移需 point-in-time consensus，降为增强非前提**（O6 标高风险，多数免费源无历史 point-in-time）——缺它时隐含增速线只反映价格驱动那半，estimate 修正 overlay 待数据到位再补。旧"以 point-in-time consensus 为主路径"作废 →
**① 期权隐含分布**（keystone，升级透明化 + edge/赔率数学）→
**② consensus 修正**（字面上的"变化"，喂漂移监控）→
**③ 轻量高度归因**（市场 beta + 主题 ETF beta，切 macro / 主题 / 公司 粗三分）；叙事半按高度打标、走 gated 深挖。富因子模型 + positioning 缓 v1.1。

### 9.2 实现阶段（milestone）

| 阶段 | 内容 | 交付的可感知能力 |
|---|---|---|
| **S0** | 漂移脊椎 + 期权 channel + 数据接入验证 | hero visual + "点→分布"跃迁被证明可行 |
| **S1** | 多框架引擎（panel 常跑 + agent 注意力 + 跨框架对账）| 任一票 → 多 channel 解码 + 跨框架对账 |
| **S2** | 决策层 Trade Plan | 任一卡 → 带仓位/止损的可执行方案 |
| **S3** | 年轻化前端 + 持仓中心 | 工作台里完整走完"导入持仓→解码→对账→决策" |
| **S4** | 主动监控（events-first）| 出事主动 report + KILL 撞线告警 |
| 后续 | 富因子 + positioning + 二三期执行后端 | 从诊断工具变成持续盯盘的 agent |

---

## §10 诚实边界与范围红线

### 10.1 诚实边界（产品原则）

1. **不硬凑交易**：低信心/小 edge → 明说"不值得下注"。
2. **决策自带认错条件**：`self_falsification` 是强制字段。
3. **不碰真钱（一期）**：一期止步 Plan 展示 + 用户手动执行；实盘需每单人工 approve，且 AI 助手不代为下单。
4. **不做个性化投顾话术**：输出的是"市场隐含 vs 可辩护区间"的结构化对撞 + 概率/base-rate，不是"你应该买"的劝导。
5. **外部视角优先**：base_rate 永远在场，压制过度自信。
6. **期权口径诚实**：分布是 risk-neutral（Q 测度）= 市场开价，不是真实信念概率；skew 报相对自身历史/同行的分位。
7. **跨框架对账 horizon-aware**：不分期限的对账会把 term structure 误报成矛盾；跨桶差异以"期限结构"叙述，不许标 ⚠ 矛盾。
8. **成本纪律**：便宜信号门控贵信号，panel 常跑盯着，过阈值才触发贵的 narrative DR。

### 10.2 范围红线（明确不做）

- 实盘自动下单（一期；二三期需合规 + 人工 approve）
- 个性化投顾 / 劝导话术
- 高频 / 日内
- 期权与衍生品定价（期权只读隐含分布，不做衍生品交易）
- 杠杆与保证金
- 多账户多策略隔离
- 通用 bet 解码（推文 / 分析师目标价 / 自由文本）——延后，不进一期

### 10.3 合规边界 `[LOCKED 2026-07-04]`

向终端用户输出可执行交易方案 = 投顾 / robo-advisory 地界（MAS / CSRC 需牌照）。PlainSight 的合规姿态已在 §4.6 执行后端分阶段 + §10.1 诚实边界 + §10.2 范围红线 + §4.5 self_falsification 强制 + §4.6 一期"展示不下单"中共同锁定：

- **一期不碰真钱**（§4.6）：Plan → 展示，用户手动去券商执行，软件不下单。
- **不做个性化投顾话术**（§10.1/§10.2）：输出的是"市场隐含 vs 可辩护区间"的结构化对撞 + 概率/base-rate，不是"你应该买"的劝导。
- **决策自带认错条件**（§4.5）：每张 Plan 强制 `self_falsification`，没有认错条件的决策不允许生成——这是非黑箱荐股的根本。
- **二三期接真券商仍需每单人工 approve**（§4.6），AI 不代为下单。

分布化决策层下，以上四条共同构成"不个性化投顾、不碰真钱、决策自带认错条件"的边界。**但需清醒（review 修正）**：一期 TradePlan 会吐 stance + target_weight + entry + stop，已相当接近"具体买入方案"，监管眼里仍可能算 advisory——self_falsification + 不碰真钱是**缓释不是豁免**。上线前建议就"结构化决策支持 vs 个性化投顾"的边界做一次法律确认；二三期真实执行后端另按当地牌照单独评审。

---

## §11 现有 codebase 存活映射 `[参考]`

| 现有 | 新角色 | 改动 |
|---|---|---|
| `reverse_dcf.py` + 7 lens | 现金流账的 kernel（栈里的一条）| 无需改；从"主引擎"降级成"一条 channel" |
| `narrative.py` | 高度栈的**叙事半**：按高度（macro/industry/company）打标的 gated 深挖 | 定位调整 + 输出加高度标签 |
| `intelligence.py`（`_kelly` / base_rate）| 决策层的仓位/外部视角零件 | 输入换成期权隐含概率 |
| 不可变快照 + `derived_from` 血缘 | "解码"的快照 / 分叉机制 | 存活并升级为调查史 |
| activity SSE 流 | agent 真实调查轨迹（不再是树自述）| 从 theater 变成真内容 |
| `agent_tools.py` | agent 的**注意力工具集**（深挖某 channel / 开叙事 gate / 跑 what-if / 答追问）；panel kernel 走确定性 sweep，不经 agent 选择 | 扩展 |
| `app.html` | 呈现层 | 整体重做为年轻化交易 App（深色卡片流，参考 `mockup_v2_young.html`）|
| `orchestrator.py` | 编排脑的雏形（agentic 解码 + Q&A + 改写）| plan 从"选装配器"升级成"给定全 panel → 分配注意力" |
| `decoder.py` | 装配器（被 orchestrator 复用）| 复用既有，加 panel 注入 hook |

---

## §12 开放项（全部 Closed 2026-07-04）

大框架已 LOCKED，开放项全部闭环。细节 spec 见 `docs/SPEC_A` 至 `docs/SPEC_F`。

| # | 开放问题 | 状态 |
|---|---|---|
| ~~O6~~ | ✅ **方向 Closed** — 探查完成，见 `docs/O6_qveris_data_feasibility.md`。期权链✅ / consensus⚠️point-in-time 风险 / 事件✅。下一步用户注册 qveris 拿 key 跑 discover。 |
| ~~O7~~ | ✅ **Closed** — 成本模型延后实现期随 channel 落地逐条核算，$100 纪律不变。 |
| ~~O9~~ | ✅ **Closed** — 对外品牌名 **PlainSight**；内部 repo/代码仍用 `bet-decoder`（代号）——route 2：品牌与代号分离（2026-07-04）。 |
| ~~O10~~ | ✅ **Closed** — 合规边界已在 §4.5/§4.6/§10.1/§10.2 共同锁定。 |
| ~~O11~~ | ✅ **Closed** — 4 个细节全按最简 MVP（连续漂移缓 v1.1 / drift 搭事件 / feed 工作台+浏览器 / KILL 复用事件管道）。 |
| ~~决策参数~~ | ✅ **Closed** — 8 项全默认（¼ Kelly / 20% / 10% / 1.0-0.6-0.3 / 2% / 含 short / 不自动平 / 不加滑点）。 |
| ~~一期 scope~~ | ✅ **Closed** — 主路径持仓扫描；允许 watchlist/candidate 解码，但不主动监控，Trade Plan 标 preview。 |

### Spec 附件（A-F 组，28 个参数全部定值）

| 组 | 内容 | 文件 |
|---|---|---|
| A | 数据结构（decode_detail v4 / panel_observations / TradePlan / BetCard）| `docs/SPEC_A_data_structures.md` |
| B | 各 channel 输出（现金流/分布/变化/高度栈·可测+叙事）| `docs/SPEC_B_channels.md` |
| C | 跨框架对账（具名 checks + term_structure + conviction）| `docs/SPEC_C_reconciliation.md` |
| D | 决策层（stance/archetype/Kelly/KILL）| `docs/SPEC_D_decision.md` |
| E | 呈现层（增强股价图 + AI 白话翻译）| `docs/SPEC_E_presentation.md` |
| F | API（decode/plan/monitor）| `docs/SPEC_F_api.md` |

---

> **下一步**：代码动工。落地顺序（§9 LOCKED）：⓪ 漂移脊椎 → ① 期权隐含分布（keystone）→ ② consensus 修正 → ③ 轻量高度归因。技术地基 = O6（用户注册 qveris 拿 API key → 跑 discover 确认字段）。
