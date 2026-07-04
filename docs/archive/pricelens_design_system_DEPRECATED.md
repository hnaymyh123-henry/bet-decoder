# PriceLens · 前端设计指导思路

> 本文档定义 PriceLens 前端的设计哲学、视觉语言与组件规范。任何新增页面 / 组件都必须遵循这里的原则——一致性优先于个人审美。
>
> 参考实现：`app.html`
> 版本：v0.1 · 2026-05-20

---

## 1. 设计哲学

### 1.1 核心隐喻

**这是一份可印刷的、严肃的研究报告**——不是 SaaS 产品落地页，不是 AI demo，不是 dashboard。

每一个设计决策都要回答一个问题：**"这个元素出现在 Goldman Sachs 或 Morgan Stanley 的 PDF 研报里，违和吗？"**——如果违和，就不放。

### 1.2 三条铁律

| # | 原则 | 反例 |
|---|---|---|
| **1** | **可信度第一，惊艳第二** | 不用 gradient、glow、shadow、emoji、sparkles |
| **2** | **数字是主角** | 标题不能比数字大；mono 字体专给数字 |
| **3** | **可读 > 可看** | 不为了视觉牺牲信息密度；表格 > 卡片 |

### 1.3 用户心智模型

用户应该在 5 秒内意识到：

> "这不是一个 AI 给我编故事——这是一份**用 AI 生成的研究报告**，结构、措辞、版式都和我熟悉的卖方研报一致，只是分析师换成了机器。"

视觉风格直接传达"专业可信"，文字风格再补充"透明可审计"。

---

## 2. 颜色系统

### 2.1 调色板

```
/* —— 纸面 (paper-on-paper) —— */
--paper:    #FFFFFF;   /* 主背景：纯白 */
--paper-2:  #FAF9F6;   /* 次背景：暖白 / 米白 (panel, exec box, table head) */
--paper-3:  #F2F1EC;   /* 第三层：分隔/空白 bar */

/* —— 分隔线 —— */
--rule:     #1A1A1A;   /* 主分隔：黑色 (section 级) */
--line:     #DBD9D2;   /* 次分隔：浅米色 (item 级) */
--line-2:   #B8B4A8;   /* 强分隔：中米色 (轴线/刻度) */

/* —— 文字 —— */
--ink:      #0A0A0A;   /* 正文/数字 */
--ink-2:    #2C2C2C;   /* 段落文字 */
--ink-3:    #5C5C5C;   /* 次要文字 */
--ink-4:    #8C8A82;   /* 辅助/单位 */
--ink-5:    #B8B4A8;   /* 占位/分隔 */

/* —— 强调色 (唯一品牌色) —— */
--accent:   #8B2A1F;   /* Oxblood — 经典研报红 */
--accent-soft: #F4E4E2;

/* —— 信号色 (语义) —— */
--positive: #1F5C3D;   /* 深绿，表示利好 / 高置信 */
--negative: #8B2A1F;   /* 同 accent，表示下跌 / 风险 */
--neutral:  #8C6F00;   /* 芥末黄，表示中性 / 中等置信 */
--highlight: #FFF4D6;  /* 浅芥末黄，行高亮 */
```

### 2.2 使用规则

- **不允许使用蓝色和紫色**——任何形式都不行。SaaS / AI 产品标志色，会立刻破坏"研报感"
- **不允许使用渐变**——所有色块都是纯色
- **accent 只用在三个地方**：编号 (01, 02, 03...) / 强调数字 (gap 列) / 触发态 (selected row, hovered slider thumb)
- **黑色 (`--rule`) 用于 section 主分隔线**，体现报告的"页面"层级
- **米白 (`--paper-2`) 用于所有"框"** —— Executive Summary、Recommendation Snapshot、表头、Lab 侧栏、Evidence Panel

### 2.3 涨跌色

| 语义 | 颜色 | 用途 |
|---|---|---|
| 上涨 / 利好 / 强证据 | `--positive` 深绿 | gap 列上升、conviction BULLISH、evidence Strong |
| 下跌 / 风险 / 弱证据 | `--negative` oxblood | gap 列下降、conviction BEARISH、evidence Weak |
| 中性 / 中等 | `--neutral` 芥末黄 | FAIRLY PRICED 评级、evidence Medium |

**注意**：涨跌色绝不用绿色 + 红色（中国惯例反过来）的鲜艳版本——使用的是 muted 的深绿和 oxblood，匹配研报色调。

---

## 3. 字体系统

### 3.1 字族

```css
--sans: "Geist", -apple-system, system-ui, sans-serif;
--mono: "Geist Mono", ui-monospace, monospace;
```

只用两种字体——**Geist** 和 **Geist Mono**（同家族）。它们的几何感传达"科技、精确"，但保留了人文细节。

**严禁** 使用：Inter、Roboto、Arial、Times、Space Grotesk、任何 serif（衬线）、任何 italic（斜体）。

### 3.2 字重哲学

**用字重对比代替斜体强调**——研报里没有斜体。

```
对比模式：
- 强调：500 weight (medium)
- 正常：400 weight (regular)
- 弱化：300 weight (light)   ← 当 em 用，比如 "5Y revenue CAGR · per annum" 里的 "per annum"
- 数字超大显示：200 weight (extralight) + 强负字距
```

### 3.3 字号 / 字距 阶梯

| 用途 | 字号 | 字重 | 字距 (letter-spacing) |
|---|---|---|---|
| 正文 (body) | 13px | 400 | 0 |
| 段落 lede | 13.5px | 400, line-height 1.7 | 0 |
| 表格内容 | 12.5px | 400/500 | 0 |
| 数字（中） | 12.5px mono | 500 | -0.01em |
| 数字（大，table cell） | 18px mono | 500 | -0.01em |
| 数字（巨，dashboard） | 36px mono | 500 | -0.03em |
| Section H2 | 24px | 500 | -0.02em |
| Page H1 | 52px | 400 | -0.035em |
| Eyebrow / Label | 9.5-10.5px mono | 500 | **0.14-0.22em** UPPERCASE |
| Disclaimer | 9.5px mono | 400 | 0.02em |

**关键模式**：所有标签 / eyebrow / 章节编号必须 **`text-transform: uppercase` + `letter-spacing: 0.14em+`**——这是研报"小字大间距"的视觉签名。

### 3.4 mono vs sans 的分工

| 用 mono (Geist Mono) | 用 sans (Geist) |
|---|---|
| 所有数字（价格、百分比、年份、ID） | 标题、段落、句子 |
| Ticker (NVDA, TSLA) | 公司全称 |
| Label / Eyebrow / Tag | 描述文字 |
| 引用源 (SEC EDGAR, FINRA) | 引文内容 |
| 时间戳、ID、版本号 | — |

**铁律**：表格里所有数字列必须 mono + `font-feature-settings: "tnum" 1`（等宽数字），保证小数点对齐。

---

## 4. 排版逻辑

### 4.1 整体结构 — 报告页层级

```
┌─────────────────────────────────────────┐
│  MASTHEAD (黑色顶栏，全大写小字)         │  ← 报告刊头
├─────────────────────────────────────────┤
│  COVER (封面：标题 + 评级框 + 署名)      │  ← 第 1 页
├─────────────────────────────────────────┤
│  EXECUTIVE SUMMARY (米白 box，编号要点) │  ← 摘要
├─────────────────────────────────────────┤
│  1.0  SECTION  (主标题 + lede + exhibit)│  ← 正文章节
│  2.0  SECTION                            │
│  3.0  SECTION                            │
│  ...                                     │
├─────────────────────────────────────────┤
│  FOOTER (metadata 4 列 + DISCLAIMER)    │  ← 法律免责
└─────────────────────────────────────────┘
```

### 4.2 Section 标准结构

每个章节遵循同一个 5 步骨架：

```
1. Section Header
   ┌─────────┬───────────────────────┬──────────────┐
   │  1.0    │  H2 章节标题            │  meta (右对齐)│
   └─────────┴───────────────────────┴──────────────┘
   (下划黑色 rule)

2. Section Lede (一段说明文字，13.5px, max-width 780px)

3. Exhibit Caption (类似 "Exhibit 1.1 · title · source")
   ┌─────────┬──────────────────────────┬───────────┐
   │ EX 1.1  │  exhibit title           │  source   │
   └─────────┴──────────────────────────┴───────────┘
   (下划浅 line)

4. Exhibit Content (表格 / 图 / 流程图)

5. Exhibit Foot Note (浅灰小字 dashed top border)
```

**必须有 Exhibit caption** —— 这是研报视觉签名。任何展示数据的元素，上方都要有 "Exhibit X.Y · 标题 · 来源" 这一行。

### 4.3 数据表标准

```
列结构 (从左到右)：
[#] [Item Name] [Implied / Value] [Consensus / Baseline] [Gap] [Sensitivity Bar] [Badge]

对齐：
- 文字列：左对齐
- 数字列：右对齐
- Badge / Status：居中

表头：
- 米白底 (paper-2)
- mono uppercase 9.5px 0.14em letter-spacing
- 下方 2px 黑线 (var(--rule))

行：
- 默认 paper-1 白底
- hover → paper-2 米白
- "高敏感度" 行用 rgba(accent, 0.04) 极淡红底标记
```

### 4.4 章节编号约定

- **0.0** — Executive Summary
- **1.0, 2.0, 3.0...** — 主章节
- **1.1, 1.2** — 子章节（如果有）
- **F.01, F.02** — Falsifiability matrix 行（用 F 前缀区分）
- **Exhibit X.Y** — 图表编号（章节号 + 序号）

### 4.5 留白原则

- **段落 max-width: 780px** — 即使容器更宽，正文也不超过这个宽度（研报印刷栏宽）
- **Section 之间**: `border-bottom: 1px solid var(--rule)` + 64px 上下间距
- **不要 fancy 的 hero spacing** — 标题距离上一个 section 的 border 只有 56px，不留呼吸感（研报版式紧凑）

---

## 5. 组件库

### 5.1 Masthead (顶栏)

```
- 高度: 14px padding × 2
- 背景: #1A1A1A (--rule)
- 文字: #FFF, mono 10.5px, uppercase, 0.16em letter-spacing
- 三栏布局: brand · 面包屑 · 时间戳
- 状态点用 5BC97E 绿色 + pulse 动画
```

### 5.2 Recommendation Snapshot (评级框)

```
- 边框: 1px var(--line)
- 顶部 3px accent 色条 (像研报的颜色 tab)
- 背景: var(--paper-2)
- 内部表格: 标签左 / 数字右 / dashed 行分隔
- 底部 badge: 黑底白字 mono UPPERCASE 0.18em
- Badge 颜色映射:
  - UNDERVALUED → bg positive (绿)
  - FAIRLY PRICED → bg neutral (芥末黄)
  - OVERVALUED → bg negative (oxblood)
```

### 5.3 Executive Summary Frame

```
- 米白底 box (paper-2)
- 标题行: "0.0 / EXECUTIVE SUMMARY" + 副标题
- 内容: 2 列 grid，6 条 takeaway
- 每条结构: [accent 编号 01] [正文，strong 关键词加粗黑色]
```

### 5.4 Exhibit Caption / Exhibit Foot

```html
<!-- 上方 -->
<div class="exhibit">
  <span class="lbl">Exhibit X.Y</span>   <!-- accent 色, uppercase, 0.18em -->
  <span class="title">...</span>          <!-- 中等加粗 -->
  <span class="src">...</span>            <!-- mono 灰色, 右对齐 -->
</div>

<!-- 下方 -->
<div class="exhibit-foot">
  Note: ...                                <!-- mono 9.5px 灰色, dashed top border -->
</div>
```

### 5.5 Evidence Card

```
- 左边 2px accent 色条 (border-left)
- padding 0 18px
- 头部: [SOURCE NAME mono uppercase accent] [date mono ink-4]
- 正文: 12.5px line-height 1.6 ink-2
- 引文 (quote): 顶部 1px line 分隔，前后用 accent 色的 " " 包裹
```

### 5.6 Sensitivity Slider Row

```
4 列 grid: [编号 36px] [名称+描述 1.4fr] [滑块 1.4fr] [数字 0.6fr]

滑块:
- 轨道: 1px 浅米色横线 (line-2)
- 两端有 6px 短刻度
- thumb: 14px × 14px 方形 (不是圆!), accent 色填充, 2px white border, 1px accent ring
- 拖动时显示 grabbing cursor

数字显示:
- 右对齐
- mono 18px 500 weight
- 单位 (%, yrs) 11px ink-3
- 下方有 vs 行: 10px mono "±$28 / pp"
```

### 5.7 Falsifiability Matrix

```
5 列表格:
[# F.01] [Assumption] [Required Move from→to] [Implication 段落] [Falsifiable Signal 右对齐]

Required Move 视觉:
"32.4%" (灰删除线) → "36.8%" (accent 色) (+4.4pp 灰色)

底部 summary box:
- 黑底白字 (--ink)
- 强调词用 #FFD89B 暖芥末色 (而不是 accent，避免和黑底冲突)
- 右侧大数字 mono 32px 500
```

---

## 6. 交互原则

### 6.1 动画

**只有三种被允许的动画**：

1. **数字 flash** — 拖动滑块时，数字短暂变 accent 色 (220ms)
2. **状态点 pulse** — masthead 上的市场状态绿点
3. **Evidence panel fade-in** — 点击表格行展开证据 (300ms ease, translateY 4px)

**禁止**：
- ❌ hero scroll-triggered reveal
- ❌ 数字 count-up 动画
- ❌ parallax
- ❌ 鼠标跟随效果
- ❌ 任何 "炫" 的过渡

研报不会动——它躺在那里。互动只在用户主动操作时回应。

### 6.2 Hover 状态

- 表格行 hover: 背景从 paper → paper-2，移动 0px (不要 transform)
- 链接 hover: 颜色加深，不要 underline 动画
- 按钮 hover: 反色 (透明背景 → 黑底白字)，不要 shadow 提升

### 6.3 Click 反馈

- 表格行点击展开证据 panel: 当前行加 highlight 黄色背景 + 默认展开第一条
- 多个交互对象 (driver rows) 共享一个 panel，切换时直接换内容，不要先收起再展开

---

## 7. 信息密度原则

研报的信息密度是 SaaS 产品的 3-5 倍。**不要怕密**，要怕**乱**。

### 7.1 密但不乱的方法

| 技巧 | 应用 |
|---|---|
| **统一的 baseline 对齐** | 表格里所有数字底部对齐，标签上对齐 |
| **mono 等宽数字** | `font-feature-settings: "tnum" 1` 强制小数点对齐 |
| **3 级灰度** | ink (主) / ink-3 (次) / ink-4 (辅) — 用灰度区分层级，不要用字号 |
| **Dashed border** | 行内分隔用 dashed，section 分隔用 solid，主分隔用黑色 |
| **右对齐数字** | 任何数字列必须 right-align |
| **uppercase + letter-spacing** | 标签用小字 + 大间距，自带"骨架感" |

### 7.2 该用表格而非卡片

任何超过 3 行的数据展示，都要用表格而不是 grid 卡片。卡片是 SaaS 的，表格才是研报的。

---

## 8. Demo Day 路演适配

5 分钟现场 demo 时，这套设计的优势：

| 评委看到 | 内心 OS |
|---|---|
| 黑色顶栏 + EQUITY RESEARCH | "他们不是做玩具，做真东西" |
| Cover 上的 FAIRLY PRICED badge | "这是真有评级体系的" |
| Exec Summary 编号要点 | "结构化输出，可读" |
| Reverse-DCF 三栏流程图 | "啊，这是反过来算的，懂了" |
| Assumption Table 拖滑块 → Snapshot Box 评级跟着变 | "等等，这个真的在重算" |
| Falsifiability Matrix | "这是真的研究方法，不是 chatbot" |

视觉策略 = **建立信任** → **让交互成为惊喜**。

---

## 9. 实施清单 (Build Checklist)

任何新页面在合入前，过一遍以下清单：

- [ ] 没有 gradient、shadow、glow、emoji
- [ ] 没有蓝色 / 紫色
- [ ] 字体只用 Geist + Geist Mono
- [ ] 没有 italic (`font-style: italic` 全局搜索应该为空)
- [ ] 所有数字列右对齐 + mono + tnum
- [ ] 每个 section 有 1.0 / 2.0 编号
- [ ] 每个数据展示有 Exhibit X.Y 标题
- [ ] 表头用 paper-2 米白底 + 黑线下划
- [ ] Section 之间用 var(--rule) 黑色分隔
- [ ] 段落 max-width 780px
- [ ] 标签用 uppercase + letter-spacing ≥ 0.14em
- [ ] Disclaimer 在底部 (法律必要)

---

## 10. 反例：不要做成的样子

为了对比强化，明确**禁止**的视觉风格：

| ❌ 不允许 | ✅ 我们的做法 |
|---|---|
| Stripe / Linear / Vercel landing page 大字 hero | Masthead + 评级框的研报封面 |
| Glassmorphism / blur backdrop | 纯色 paper-on-paper 分层 |
| 渐变 stat card 网格 | 数据表 + 米白表头 |
| Emoji 装饰 (📈 ✨ 🚀) | 全部去除 |
| "Powered by AI" + sparkles | "Analyst: PriceLens Agent · MiroThinker-1.7" 署名 |
| Dark mode 默认 | 永远白底 |
| Floating action button | 表格行内点击 |
| Toast 通知 | Inline evidence panel |

---

## 修订日志

| 版本 | 日期 | 修改 |
|---|---|---|
| v0.1 | 2026-05-20 | 初稿。从 `app.html` 提炼。 |
