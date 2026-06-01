# BET DECODER · 产品愿景

> **一句话**: PriceLens 重新定位为"投资 bet 的 X 光机" — 输入任何 bet (市场价 / 分析师目标价 / 朋友推文 / 你的持仓), 输出"这个 bet 隐含相信什么 + 跟其他 bet 的对比 + 跨 bet 的洞察"。
>
> **版本**: v0.7 · 2026-05-28 · 自经过 6 轮 UI 迭代 (demo_b 到 demo_g) 后确立的产品形态
>
> **状态**: 产品形态已锁定, 等待开发方案细化

---

## 0. 为什么要做这次 pivot

### 0.1 现有定位的死胡同

PRD v0.6 的定位是 "**反向解码股票价格背后的市场隐含推理**", 但经过 6 轮 UI 迭代发现这个定位有 2 个根本问题:

1. **"研报感"无法摆脱** — 不论我们怎么做可视化(滑块 / 缩进树 / 真节点树 / 时间机器 / 张力盘), 最终产物始终是一份"基于 NVDA 这一只股的反向 DCF 分析报告"。用户的核心动作是 "读", 而不是 "用"。
2. **核心能力被 underused** — 我们的核心 engine 是"反向解码任意 bet → 隐含假设", 但产品只用它解码 1 个 bet (市场对一只股的当前定价)。这是把一个通用能力当成 single-purpose 工具用。

### 0.2 Pivot 后的核心洞察

> **反向解码 engine 不只能用在"市场对一只股的当前 bet"上, 它能用在 ANY bet 上。**

任何投资观点本质上都是一个 bet:
- 当前股价 = 市场的 bet
- 分析师目标价 = 分析师的 bet
- 朋友推文 = 朋友的 bet
- 你的持仓 = 你的 bet
- 历史某天的股价 = 当时市场的 bet

我们的 engine 可以**把它们全部拆解成同一种形态的 "Bet Card"** — 再让用户**横向对比 / 聚合 / 综合**。这才是 unique 的产品价值。

### 0.3 与"推理透明"主题的更强契合

- 旧定位: AI 帮你看懂市场的隐含推理 (对象单一)
- 新定位: AI 帮你看懂**任意一方**的隐含推理 + **跨方对比** (对象任意)

→ "推理透明"的对象不再局限于市场, 而是**所有投资观点**。这是更深刻的差异化。

---

## 1. 核心概念: Bet Card

**Bet Card 是产品的核心基础单元。** 每张卡是一个被反向解码的押注:

```
┌─ Bet Card 标准结构 ───────────────────┐
│ Subject:  谁/什么的 bet              │
│ Source:   原始 input (价格 / URL /...)│
│ ─────────────────────────────────── │
│ Bet 1:    [指标 1] = [数值]           │
│ Bet 2:    [指标 2] = [数值]           │
│ Bet 3:    [指标 3] = [数值]           │
│           (相对基线/共识的差距)        │
│ ─────────────────────────────────── │
│ 关键风险: [可证伪的破点 1-3 条]       │
│ ─────────────────────────────────── │
│ [▾ 详细决策链]                       │
│   (点开看每个 bet 的反向求解过程 +    │
│    业务前提 + evidence)              │
└──────────────────────────────────────┘
```

### 1.1 Bet Card 的四种 source 类型

| 类型 | Source 示例 | 解码内容 |
|---|---|---|
| **Market** | "NVDA $214.86" | 市场对这只股**当前**的隐含 bet |
| **Analyst** | "Goldman 5/24 PT $300" 报告链接 | Goldman 的隐含 bet (vs 市场) |
| **Opinion** | 一段推文 / YouTube 转录 / 自然语言 thesis | 该意见的隐含 bet |
| **Portfolio** | 一份持仓清单(8 只票) | 你的组合**跨股聚合**的隐含 bet |

### 1.2 Bet Card 之间的关系

**单卡只是开始, 多卡共存才是产品的真正价值。** 多卡同时在场时, AI 自动做跨卡综合:

- **对比** (Market vs Analyst): "Goldman 比市场激进 15pt 增速"
- **同源** (Portfolio vs Analyst): "你的组合 73% 依赖 GS 同样的假设"
- **矛盾** (Portfolio 内部): "你的 NVDA 仓位需要 hyperscaler 加 capex, 你的 GOOG 仓位需要 GOOG 控成本 — 两者矛盾"
- **时间漂移** (同一 source 不同时间): "GS 一个月前隐含 60%, 现在 70% — 他在追市场不是带市场"

→ 这些**跨卡 insight 是任何单卡 feature 都给不了的**。是 Aha 的真正来源。

---

## 2. 产品形态: Bet Card 工作台

### 2.1 主界面

```
┌──────────────────────────────────────────────────────────────┐
│ 🔍 解码任意 bet... 粘贴 ticker / 持仓 / 分析师报告 / 推文 URL │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  [Bet Card]    [Bet Card]    [Bet Card]                      │
│   NVDA 市场     Goldman PT    你的持仓                       │
│                                                              │
│  ╔═ 🔮 AI 跨卡分析 ═══════════════════════════════════════╗  │
│  ║ 综合洞察(自动生成, 多卡共存时出现)                       ║  │
│  ╚════════════════════════════════════════════════════════╝  │
└──────────────────────────────────────────────────────────────┘
```

### 2.2 关键设计原则

1. **统一 input 框** — 一个框接受任意 bet source (ticker / 持仓 / URL / 文本 / 推文)
2. **统一 output 形态** — Bet Card, 都有 Subject / Source / Bets / 风险 / 决策链
3. **卡并列共存** — 不限制数量, 多卡同时在场是核心
4. **AI 跨卡综合** — 多卡时 AI 自动出 insight 段落(不是单卡总结, 而是 cross-card)
5. **Live agent activity** — 粘贴新 source 时 SSE 流出 agent 工作过程 ("在抓 GS 报告 PDF...在反向求解 GS 隐含 DCF...")
6. **卡可保存 / 重组 / 分享** — 每张卡是 portable 单位

### 2.3 与现有 PRD 的关系

| 旧 (PRD v0.6) | 新 (BET Decoder v0.7) |
|---|---|
| F1: 多时间尺度选择器 | 仍保留(作为 Market 类卡的子能力) |
| F2: 长期反向解码 | **升级为通用反向解码 engine** (any source) |
| F3: 5d 短期归因 | 仍保留(作为 Market 类卡的子能力) |
| F4: 证据搜集 | **升级为多源验证** (any bet 都可查证据) |
| F5: 推理链可视化 | **升级为 Bet Card 决策链** |
| F6: 用户介入重跑 | 退化 — slider 不再是主交互 (经反复迭代证明不 work) |
| (新增) | **F10: Bet Card 工作台** — 主产品形态 |
| (新增) | **F11: AI 跨卡综合** — 真正的 Aha 来源 |
| (新增) | **F12: 多 source 解析** — URL/text/portfolio 输入 |

---

## 3. Aha 矩阵

四个真正的 Aha moment, 必须 1+2 组合才能产生:

| ID | Aha | 触发条件 | 单 feature 能否给? |
|---|---|---|---|
| **A** | "你跟分析师的隐含 bet 重合度" | Portfolio 卡 + Analyst 卡共存 | ✗ 必须组合 |
| **B** | "你的组合跨股 dependency" | Portfolio 卡内部 (跨股聚合) | ✗ 必须聚合 |
| **C** | "互相矛盾的 bets" | Portfolio 卡内部 + AI 综合 | ✗ 必须有 AI 综合 |
| **D** | "时间 bet 漂移" | 同 source 不同时间的两张卡 | ✗ 必须多卡 |

**单纯做 Aha 1 (组合) 或 Aha 2 (解码 thesis) 都给不出 A-D**, 必须 1+2 + 跨卡 AI 才完整。

---

## 4. Demo 叙事 (5 幕递进, 每幕 1-1.5 分钟)

| 幕 | 动作 | 出现的卡片 | 累计感受 |
|---|---|---|---|
| **1** | 粘贴 "NVDA" | NVDA 市场卡 | "原来市场押了 3 件事" |
| **2** | 粘贴 GS 报告链接 | + Goldman 卡 (并列) | "Goldman 比市场激进 15pt 增速" |
| **3** | 粘贴 Citron 看空推文 | + Citron 卡 | "看多看空双方在 BET 同一件事 = ASIC 替代速度" |
| **4** | 粘贴 8 只票持仓 | + 组合聚合卡 | "我的组合 73% 都在 BET AI infra, 我以为分散了" |
| **5** | AI 跨卡综合 | (顶部红框) | "我的组合跟 Goldman 同源, 一起涨一起死" |

**第 5 幕是真正的 wow moment** — 跨卡综合是单一 feature 给不了的, 必须 1+2 组合 + AI 联结。

---

## 5. 实现路径

| Phase | 内容 | 时间 (hardcoded 原型) | 时间 (真后端) | 累计 Aha |
|---|---|---|---|---|
| **P1** | 单卡:NVDA 市场卡(复用 demo_g 的内核, 重新包成"卡片"形态) | 0.5 天 | +1 天(接 reverse_dcf.py / SQLite) | ★★ |
| **P2** | 双卡并列 + 自动 diff(NVDA 市场卡 vs hardcoded GS 卡) | 1 天 | +2 天(URL 抓取 + thesis 抽取 LLM) | ★★★ |
| **P3** | 组合聚合卡(8 只票 hardcoded, 跨股 bet 占比聚合) | 1 天 | +2 天(批量反向解码 + 聚合算法) | ★★★★ |
| **P4** | AI 跨卡综合(hardcoded NVDA + GS + 组合的 cross-insight) | 0.5 天 | +1 天(DR augment prompt 跨卡输入) | ★★★★★ |
| **P5** | 真 input 框 + SSE 流式 agent activity | 1.5 天 | +2 天(任意 source 解析 + 实时流) | (form 升级) |
| **总计** | | **4.5 天 hardcoded** | **+8 天真后端** | |

### 5.1 推荐节奏

- **W1 (本周末前)**: P1 + P2 hardcoded → 你看双卡对比效果, 确认形态对了
- **W2**: P3 + P4 hardcoded → 看完整 5 幕 demo, 确认 Aha 链条完整
- **W3-W4**: P5 + 真后端集成 (按需选 1-2 个 source 类型先做真的, 其他保留 hardcoded for demo)

### 5.2 跟现有 codebase 的接口

| 现有模块 | Bet Decoder 里的角色 | 改动 |
|---|---|---|
| `reverse_dcf.py` | Market 类卡的底层算法 | 无需改, 直接复用 |
| `pipeline.py` | Market 卡的 backend pipeline | 重新包装为 "decode_bet(source) → BetCard" 接口 |
| `db.py` (SQLite) | 持久化卡片(让卡片可保存 / 历史回看) | 加新表 `bet_cards` |
| `api.py` | 改为接受多种 source 类型 | 新 endpoint: `/api/decode?source=...` |
| `sse.py` | Agent activity 流(已实现, 现在升级为核心交互) | 改为通用 decode 流(不只是 evidence) |
| `app.html` | **大面积重构**为 Bet Card 工作台 | 重做 |
| `cache/decoder/` etc. | 持续可用, 缓存 key 改成 by source 而不是 by ticker | 调整 cache key |

---

## 6. 待决议项

| # | 问题 | 备选 |
|---|---|---|
| ~~Q1~~ | ~~产品名~~ | ✅ Closed 2026-05-28 — **Bet Decoder** (用户确认) |
| Q2 | 4 种 source 类型 (Market / Analyst / Opinion / Portfolio) 都做, 还是先做 Market + Analyst 验证形态? | 推荐先做 Market + Portfolio (因为 Portfolio 的 Aha 最强) |
| Q3 | 跨卡 AI 综合用 chat 模式还是 deepresearch 模式? | 推荐 chat (~$0.20/次), DR 太贵 |
| Q4 | 真 Analyst 卡的 URL 抓取 + thesis 抽取要哪个 LLM 做? | 推荐 mini chat (便宜) |
| Q5 | Portfolio 类卡的输入形式? broker 集成 / CSV 上传 / 文本粘贴? | MVP 文本粘贴; broker 集成 post-v1.0 |
| Q6 | "跨卡 dependency 矩阵"(Aha B)的算法是什么? | 启发式: 看哪些 bet 共享相同业务前提(eg "hyperscaler capex") |
| Q7 | 一张卡的保存 / 分享形式? PNG / 链接 / 嵌入? | 推荐:可分享链接 + 嵌入式 widget(类 Tweet embed) |

---

## 7. 这次 pivot 改变了什么 (engineer/agent context)

### 7.1 核心定位变更

- **旧**: "PriceLens 是反向 DCF 投资研究工具"
- **新**: "Bet Decoder 是投资 bet 的 X 光机"

### 7.2 用户心智变更

- **旧**: 用户来这里"看 NVDA 的分析报告"
- **新**: 用户来这里"X 光透视任何投资 bet"

### 7.3 核心交互变更

- **旧**: 浏览预制的研报视图, slider / toggle 微调
- **新**: 粘贴 bet source → 看 agent 实时解码 → 多卡共存 → AI 跨卡综合

### 7.4 差异化护城河变更

- **旧**: 反向 DCF 方法学 + 推理透明
- **新**: 反向 DCF 方法学 + **跨卡综合** + **通用 bet 解码** + 推理透明

### 7.5 后端架构影响

- 不需要推翻现有 reverse_dcf.py / pipeline.py / db.py
- 主要工作在 **input 解析层 (URL/text/portfolio → 结构化 source)** + **跨卡综合 prompt** + **前端重做**

### 7.6 PRD 的 §11 demo script 需要重写

旧的 3 幕 (COST/NVDA/TSLA) demo 已不适用。新的 5 幕 (5 张卡递进 + AI 综合) 见本文档 §4。

---

## 8. UI 迭代历史 (lineage notes, 删除前留档)

经过 6 轮 HTML demo 探索 (b → g), 每一轮被否定的原因都贡献了最终方案的某一面:

| Demo | 内容 | 被否原因 | 留下的洞察 |
|---|---|---|---|
| b | Slider + 实时 DCF | 像 calculator, 滑块没人会用 | 需要 visual causation |
| c | 嵌套缩进卡 | 像研报大纲, 不够交互 | 需要真视觉树 |
| d | SVG 节点+连线树 | 4 方法挤一棵树, 3 scenario 像 3 张图 | 需要单方法独立树 |
| e | 渐进式拆解树 (click to reveal) | 拆解太 basic, 像默认结构 | 需要"决策链"感 |
| f | 每方法独立 viz (公式 + bar) | 公式像教材, 静态 | 需要 market dynamics |
| g | 时间机器 + 张力盘 | 仍是研报形态, 不像 App | 需要产品形态 pivot |

**关键拐点**: demo_g 之后用户提出"产品形态像研报, 不像 App/Agent" → 直接推动了本次 Bet Decoder pivot。

demo_g 的时间机器 + 张力盘**视觉资产可以复用**在 Market 类卡的"详细决策链"里(P1 之后)。其他 demo 的代码全部废弃。
