# SPEC · E 组 呈现层（v2 · 增强股价图）

> 状态：定稿 · 2026-07-04
> 依赖：SPEC A-D 组 · PRD v2.0 §5
> 参考实现：`mockup_enhanced_chart.html`（可交互 mockup）

---

## 核心设计原则

**线条是给 AI 看的底稿，AI 翻译成白话给用户。** 用户不需要看懂线，用户看 AI 翻译的一句话。线是 AI 的底稿不是用户的阅读对象。

**图是主体，所有信息都在图上，点哪里出哪里的分析。** 没有层级按钮，没有预设的"先看 L0 再看 L1"。图上所有元素（K 线 / 分布带 / 隐含增速线 / consensus 轨迹 / 事件锚点 / KILL 线）默认全部显示，用户点哪个元素，AI 白话条就更新为该元素的解读。

## 整体结构

股价图 = 主体。所有 channel 的信息以叠加层形式画在图上。点图上任意元素 → AI 白话条更新 + 问 AI 栏 placeholder 动态更新。

---

## 默认视图

### 图上元素（全部默认显示）

| 元素 | 视觉 | 对应 channel | 点了出什么 |
|---|---|---|---|
| K 线 | 标准 OHLC 蜡烛 | 价格 channel | "这天价格 $X，市场要的增速 Y%，比昨天变难/变容易" |
| **分布带** | 青绿阴影区 | distribution | "市场觉得 3 个月内大概率落在 $185-248。现在在中上位置。阴影收窄=更确定" |
| **隐含增速线** | 紫色实线（画在图下方独立子图，右 Y 轴标 %，不与价格轴混）| cashflow | "这条紫线是市场对 NVDA 的要求——现在要每年涨 38%。45 天前财报时跳高" |
| **consensus 轨迹** | 灰虚线 | revision | "这条灰虚线是分析师觉得该值多少。现在 $190，市场价跑在前面 13%" |
| **事件锚点** | 黄色竖线 | 事件源 | "这天发了财报。市场要求从 34% 跳到 41%——预期跳变点" |
| **KILL 线** | 红色水平虚线 | 决策层 | "这根红线是你的认错线——$180。现在离它 1%，期权觉得 12% 概率碰到" |

### AI 白话条（紫色，图下方）

- **默认态**（没点任何元素时）：总览一句话——"市场现在要 NVDA 每年涨 38% 才配得上这个价——历史极少公司做到。离你的认错线还剩 1%。"
- **点了元素后**：更新为该元素的解读（见上表）
- 每条解读末尾标来源（"— 点了阴影区 · 分布账"）

### 问 AI 栏

- placeholder 动态更新：点了某元素后，placeholder 变成该元素相关的追问示例
  - 点了阴影区 → "问 PlainSight · 为什么阴影最近收窄了?"
  - 点了紫线 → "问 PlainSight · 为什么 45 天前突然跳高?"
  - 点了红线 → "问 PlainSight · 如果跌破 $180 我该怎么办?"

---

### 术语翻译表

| 内部术语 | 用户可见文案 |
|---|---|
| implied_cagr / 隐含增速 | "市场要它做到多难" / "每年涨 X%" |
| implied_range / 分布带 | "市场觉得会落在这" |
| consensus / 分析师共识 | "分析师觉得值多少" |
| KILL line | "到这该认错" |
| skew | "下行保险比平时贵/便宜" |
| term structure | "近期在定价什么风险" |
| reconciliation verdict | "市场信号一致/有分歧/互相矛盾" |

---

## 点击交互 spec

### 交互流程

1. 用户看到图上所有元素（K 线 + 阴影 + 紫线 + 灰线 + 黄线 + 红线）
2. 点任意元素 → AI 白话条更新为该元素解读 + 问 AI 栏 placeholder 更新
3. 用户可以继续点别的元素，或用问 AI 栏深入追问

### 点击元素 → AI 解读映射

| 点了什么 | AI 白话条内容 | 问 AI 栏 placeholder |
|---|---|---|
| 分布带（阴影） | "市场觉得 3 个月内大概率落在 $185-248。现在在中上位置。阴影收窄=更确定" | "为什么阴影最近收窄了?" |
| 隐含增速线（紫线） | "这条紫线是市场的要求——现在要每年涨 38%。45 天前财报时跳高" | "为什么 45 天前突然跳高?" |
| consensus 轨迹（灰虚线） | "这条灰虚线是分析师觉得该值多少。现在 $190，市场价跑在前面 13%" | "为什么价格跑在分析师前面?" |
| 事件锚点（黄线） | "这天发了财报。市场要求从 34% 跳到 41%——预期跳变点" | "财报具体说了什么?" |
| KILL 线（红线） | "这根红线是你的认错线——$180。现在离它 1%，期权觉得 12% 概率碰到" | "如果跌破 $180 我该怎么办?" |
| 蜡烛（有事件） | "这天发了财报，价格从 $198 跳到 $215。市场要求从 34% 跳到 41%" | "财报具体说了什么?" |
| 蜡烛（无事件） | "这天价格 $X。市场要的增速对应 Y%，比昨天变难/变容易" | "这天有什么特别的?" |
| 未点任何元素（默认） | "市场现在要 NVDA 每年涨 38% 才配得上这个价——历史极少公司做到。离认错线还剩 1%" | "问 PlainSight 任何问题" |

### 卡片字段

每张持仓卡 = 增强股价图的**缩略版**：

| 位置 | 字段 | 来源 |
|---|---|---|
| 主信息 | ticker + 仓位% | bet_cards + portfolio_holdings |
| 价格 | 现价 + 涨跌（小字）| bet_cards.bet |
| **AI 一句话** | 领衔 channel headline（紫色条）| decode_detail headline |
| **健康度色条** | 红/黄/绿（verdict + KILL 距离）| reconciliation + kill |
| sparkline | 30 天迷你 K 线 + KILL 线 | panel_observations |

### 排序

默认按健康度排序（最危险的在最上面），不按价格涨跌。用户可切"按涨跌"排序。

### 健康度色条规则

| 颜色 | 条件 |
|---|---|
| 红 | 任一桶 contradiction / kill.status=triggered |
| 黄 | 任一 check divergent / kill.status=approached |
| 绿 | 全部 checks aligned / kill.status=monitoring |

---

## 问 AI 交互

### 入口

底部紫色固定栏（不是浮动，是主交互入口）。

### 问题分类 + 工具（同 SPEC_E v1）

| 类型 | 例 | 工具 |
|---|---|---|
| 揭示 | "为什么说矛盾?" | explain_reconciliation |
| 深挖 | "叙事挖深一点" | deep_dive_channel |
| 对比 | "和 TSLA 比怎样?" | compare_with |
| what-if | "增速只有 20% 呢?" | propose_revision |
| 决策 | "我该买吗?" | build_trade_plan |
| **图上追问** | "为什么 45 天前突然变难了?" | explain_point（hover 点的上下文）|

### 图上追问（新增）

用户 hover 某根蜡烛/某个点后，可以直接问 AI 关于那个点的问题。问 AI 栏的 placeholder 动态更新为 hover 点的上下文（如"问 PlainSight · '为什么 45 天前突然变难了?'"）。

---

## 变化 feed

### 入口

列表页顶部"变化"tab（类似资讯 tab），不占列表页空间。

### feed item 结构

```jsonc
{
  "item_id": "feed_001",
  "timestamp": "2026-07-04T16:00:00Z",
  "subject": "NVDA",
  "trigger": "earnings",
  "severity": "high",
  "headline": "财报后隐含增速 34%→41%，逼近你的认错线",
  "changes": [
    {"channel":"cashflow","field":"implied_cagr","before":0.34,"after":0.41,"direction":"worse"},
    {"channel":"distribution","field":"skew.percentile_1y","before":0.65,"after":0.82,"direction":"worse"}
  ],
  "kill_status": {"approached":true,"kill_line":"营收增速连2季破11%","margin":0.01},
  "action_hint": "点开看图",
  "card_id": "abc123"
}
```

点 feed item → 跳到该 ticker 的增强股价图，自动定位到事件发生的那根蜡烛。

### severity 与 worse/better 算法

`direction="worse"` 不能只看数值上升/下降，必须绑定该字段的风险语义：

| 字段 | worse 条件 | better 条件 |
|---|---|---|
| `cashflow.implied_cagr` | 上升，说明市场要求变难 | 下降 |
| `distribution.skew.percentile_1y` | 上升且 >0.80，下行保险异常贵 | 回落到 <0.65 |
| `distribution.implied_range.width` | 急剧变宽，市场不确定性上升 | 收窄 |
| `revision.est_rev_90d` | 下修 | 上修 |
| `altitude.decomposition.company_pct` | 下降，说明更多是系统性 beta | 上升 |
| `kill.margin` | 逼近/触发 KILL | 远离 KILL |

severity 规则：

| severity | 条件 |
|---|---|
| high | kill triggered；或 contradiction；或 2 个以上 key fields worse 且其中一个是 KILL / skew / implied_cagr |
| medium | kill approached；或任一 divergent check；或单个 key field 显著 worse |
| low | 轻微 drift / informational event |

每个 feed item 必须带 `why_severity`：

```jsonc
{
  "severity": "high",
  "why_severity": [
    "KILL margin 从 6% 缩到 1%",
    "skew 1y 分位升到 82%"
  ]
}
```

若数据因预算未扫描或 honest-empty，feed item 用 `severity="unknown"`，不得报 low。

---

## Portfolio Decode 下游

组合 decode 产出两层：

1. `card_kind="portfolio"` 的父卡：组合综合、持仓列表、跨持仓 synthesis。
2. 每个持仓腿的 single-card decode：作为 portfolio 的 constituents，供详情页、Trade Plan、KILL、chart 单独使用。

下游规则：

| 下游 | 读取什么 |
|---|---|
| 组合综合卡 | 父卡 decode_detail + constituent card ids + `/api/synthesize` |
| 单票详情 | constituent single card |
| Trade Plan | 默认对单票生成；组合级只生成 risk summary，不给组合级 target_weight |
| 监控 | 只监控 position_ledger open 的单票；父组合卡不直接推 KILL |
| chart | 单票 `GET /api/chart/{subject}`；组合页显示每腿 sparkline |

父卡必须保存 `decode_detail.constituent_card_ids[]`，否则组合综合无法稳定复用单票结果。

---

## 组合综合卡（Aha B）

组合页顶部必须有一张**组合综合卡**，不是隐藏在文字分析里。它承载 PRD 的 Aha B："你以为分散，其实在赌同一个 root assumption"。

| 区域 | 内容 | 数据来源 |
|---|---|---|
| Headline | "58% 的组合风险来自同一条 AI-capex 假设" | `synthesizer.py` + altitude decomposition |
| 共根假设 | shared-root / contradiction / drift 关系表 | `/api/synthesize` |
| 系统性 KILL | 多只持仓共享的 KILL 线 | TradePlan decision.kill |
| 集中度条 | macro / theme / company 三段 | altitude.metrics.decomposition |

空态：

- 持仓 <2：显示"至少 2 个持仓后才能看共根假设"。
- synthesis honest-empty：显示"未发现显著跨持仓关系"，不渲染空表。
- 单只票缺 altitude：该票标 `data_missing`，不把缺失当作分散。

---

## Trade Plan 呈现（pillar 2）

Trade Plan 不是首页卖点，但卡详情必须有一个可展开的决策纪律区：

| 区域 | 内容 |
|---|---|
| stance strip | avoid / hold / trim / accumulate 等，附 `actionable` 状态 |
| position context | fresh ledger / stale snapshot / preview，明确系统是否知道用户实际执行 |
| entry/size | 分批、目标权重、Kelly 打折、单仓上限、现金下限 |
| KILL line | 价格型/基本面型，状态 monitoring/approached/triggered |
| self-falsification | "我错了会怎样"强制显示 |
| manual confirmation | "我已执行 / 我已调整 / 我已清仓"按钮，写 `position_ledger` |

诚实边界：

- 若 `position_freshness="stale_snapshot"`，显示黄条："这是导入快照，不代表你现在仍持有；确认后才会主动监控 KILL。"
- 若 `stance=short` 但一期不可执行，显示为"避免/减仓/可选对冲"，不显示自动做空。
- 若无 KILL line，Trade Plan 不渲染为可执行方案，只显示"缺认错条件，拒绝输出计划"。

---

## 漂移半图

增强股价图必须有一个可切换的 drift view，显示"预期怎么漂"，否则 v2.0 的核心对象会退回静态快照。

| 图层 | 含义 | 缺数据时 |
|---|---|---|
| implied_growth | 市场要求的增长/估值难度时间序列 | 显示从首次记录日起，不回填假历史 |
| distribution_band | 期权隐含 68% 区间漂移 | 无 RND 时仅显示 ATM 区间，标 degraded |
| consensus | 分析师共识轨迹 | point-in-time 冷启动时隐藏历史线 |
| events | 解释跳变的事件锚点 | 无事件源时隐藏，不编造 |

数据只来自 `GET /api/chart/{subject}`；卡 JSON 的单点 `decode_detail` 不能冒充历史序列。

---

## 空态 / 首次用户

新用户第一屏必须可用，不能是空白 canvas：

| 状态 | 呈现 |
|---|---|
| 无卡 | 显示组合导入入口 + 3 个 demo cards（来自 shipped `pricelens.db`）|
| 无 position ledger | 所有监控文案显示"保存/确认持仓后开始监控" |
| 无 qveris/API key | demo cache 可看；live decode 按 `offline_mode` 显示不可用 |
| channel honest-empty | 卡内显示"这一层暂无数据"，不降级成矛盾/不画假线 |

---

## 视觉规范

| 元素 | 规格 |
|---|---|
| 背景 | `#0B0E11`（深色底）|
| 卡片 | `#161A1F` |
| 文字 | `#EAECEF`（主）/ `#8A94A3`（次）/ `#5B6673`（弱）|
| 涨 | `#16C784` |
| 跌 | `#F6465D` |
| 警告 | `#F0B90B` |
| AI / 隐含增速线 | `#7B61FF`（紫）|
| 分布带 | `#5DCAA5`（青绿，低透明度填充）|
| consensus 线 | `#8A94A3`（灰虚线）|
| 事件锚点 | `#F0B90B`（黄虚线）|
| KILL 线 | `#F6465D`（红虚线）|
| 字体 | Inter |
| 数字 | tabular-nums |

---

## 已确认参数

| # | 参数 | 定值 |
|---|---|---|
| S1 | 详情页块顺序 | **领衔 channel 优先**（L2 的紫线/灰线由 agent 决定谁领衔标注）|
| S2 | feed 最大累积数 | **100** |
| S3 | sparkline 时间窗口 | **30d** |
