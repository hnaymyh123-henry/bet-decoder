# SPEC · C 组 跨框架对账

> 状态：草案 · 2026-07-04 · 待用户 review
> 依赖：SPEC A 组（reconciliation 结构）· SPEC B 组（各 channel metrics）
> PRD 依据：§2.5 四个不可动摇的原则

---

## C 组总览

| # | 项 | 待 spec |
|---|---|---|
| C1 | horizon 桶定义 | DCF 5y / 期权数周-数月 / consensus 季度——具体边界、几个桶 |
| C2 | 对账算法 | 同桶内一致/分歧/矛盾怎么判定；跨桶怎么报"期限结构" |
| C3 | conviction 计算 | 跨 channel 一致性怎么量化成 high/medium/low |

---

## C1 · horizon 桶定义

### 设计原则

对账只在**同 horizon 桶内**进行（PRD §2.5 原则 3）。跨桶差异 = 期限结构（合法洞察，不许标 ⚠ 矛盾）。

### 桶定义

每个 channel 的 panel 输出带 `horizon` 标签（A 组 spec）。对账时按 horizon 分桶：

| 桶 | horizon 标签 | 覆盖的 channel | 含义 |
|---|---|---|---|
| **短期** | `"1-3m"` | distribution（期权 ~90 天）| 市场对近期结局的定价 |
| **中期** | `"quarterly"` | revision（consensus 90 天修正）| 分析师预期的近期修正方向 |
| **长期** | `"5y"` | cashflow（DCF 5 年）+ altitude·macro（regime 月-年）+ altitude·industry（cycle 1-5y）| 长期基本面 + 主题 + 宏观 beta |

### 桶映射规则

| channel | horizon 标签 | 进哪个桶 |
|---|---|---|
| cashflow | `"5y"` | 长期 |
| distribution | `"1-3m"` | 短期 |
| revision | `"quarterly"` | 中期 |
| altitude（整体）| `"mixed"` | **拆分**：macro 进长期、industry 进长期、company 视情况 |

**altitude 特殊处理**：altitude channel 的 horizon 是 `"mixed"`（三层不同），对账时拆开——macro/industry 的可测半进长期桶（与 cashflow 对账"长期假设是否一致"），company 的可测半进长期桶（残差 = 公司特有长期 bet），company 的叙事半（gated DR）也进长期桶（叙事是长期假设）。

### 为什么是 3 个桶不是更多

- **短期（1-3m）**：期权覆盖的窗口，反映市场对近期催化剂的定价
- **中期（quarterly）**：consensus 修正的节奏，反映分析师预期的近期方向
- **长期（5y）**：DCF + 主题/宏观 beta，反映长期基本面假设

期权和 consensus 不合并——虽然都是"近期"，但期权是市场定价（Q 测度）、consensus 是分析师信念（P 测度），口径不同，分桶对账才有意义（"期权平静但 consensus 上修 = 市场和分析师分歧"是合法洞察）。

---

## C2 · 对账算法

### ⚠ 对账重设计（review 修正 2026-07-04，取代"按 horizon 分桶对账"）

**放弃"按 horizon 分桶、桶内对账"**——它有两个致命问题：distribution / revision 各自单 channel 成桶 → 永远 consistent → 只有长桶能分歧 →（1）conviction 的 divergence 阈值**够不到**（永远 high，死代码）；（2）旗舰信号"price ahead of fundamentals = 脆"跨 horizon，被误判成期限结构。

改成**两层**：

1. **跨 channel 检查（signal 层）**——一组**具名的 channel-对关系检查**，每个产出 aligned / divergent / contradiction。这是"洞察在分歧里"的真正落点，含旗舰信号。
2. **期限结构（descriptive 层）**——短/长 horizon 的 level 差异（near IV vs far IV、短期分布 vs 长期 DCF），**只叙述、永不判矛盾**（保护 F4）。

三态（现在作用于**检查**，不是桶）：

| verdict | 含义 |
|---|---|
| `aligned` | 这对 channel 相互印证 |
| `divergent` | 有分歧但各有道理（软信号）|
| `contradiction` | 互斥信号（硬信号，最脆）|

horizon 标签仍保留（每个 channel 带 horizon，term_structure 层用），但**不再拿来隔离对账**。

### 各 channel 的"方向信号"定义

每个 channel 产出一个标准化的方向信号，供对账用：

| channel | 方向信号字段 | 取值 | 怎么算 |
|---|---|---|---|
| cashflow | `signal` | `overvalued` / `fair` / `undervalued` / `no_solution` | `undervalued = baseline > anchor`；`overvalued = anchor > baseline_high`；`fair` = 在区间内；`no_solution` = DCF 无解 |
| distribution | `signal` | `bullish` / `neutral` / `bearish` / `euphoric` | `euphoric` = call 侧 skew 倒挂（抢筹）；`bullish` = 上行概率 > 下行；`bearish` = 下行保护异常贵（skew percentile > 0.8）；`neutral` = 平静 |
| revision | `signal` | `improving` / `stable` / `deteriorating` / `unknown` | `improving` = est_rev_90d > +5%；`deteriorating` = < -5%；`stable` = 中间；point-in-time 冷启动 = `unknown` |
| altitude | `signal`（拆三层）| macro: `risk_on` / `risk_off` / `neutral`<br>industry: `theme_bid_up` / `theme_cooling` / `neutral`<br>company: `residual_high` / `residual_low` / `neutral` | macro: beta > 1.2 + VIX 低 = risk_on；industry: theme beta 高 + 主题 ETF 涨 = bid_up；company: residual_variance > 0.5 = high |

### 跨 channel 检查表（signal 层核心）

| 检查 | channel 对 | aligned（健康）| divergent / contradiction（⚠ 脆）|
|---|---|---|---|
| **价格 vs 基本面** | cashflow × revision | 价格涨幅 ≈ estimate 上修 | `price_rev ≫ est_rev` → 价格跑在预期前 = **positioning-driven 假涨**（旗舰，PRD §2.5 原则 2）|
| **估值 vs 期权风险定价** | cashflow × distribution | overvalued + skew 贵（下行被认知）= 风险已定价 | overvalued + 期权平静 = **市场自满、脆**（divergent）；overvalued + euphoric = **顶部拥挤**（contradiction）|
| **预期动能 vs 期权** | revision × distribution | improving + 期权乐观 | improving 但期权定肥左尾 = 市场怀疑这个改善（divergent）|
| **集中度** | altitude 内部 | 公司自身占比高 | 大部分是主题/宏观 beta = 伪分散、系统性脆（divergent）|

```python
def run_cross_channel_checks(signals, metrics):
    checks = []  # 只收触发的；未触发 = aligned，不入 list
    rev = metrics["revision"]

    # 价格 vs 基本面（旗舰信号）
    revision_ready = (
        signals.get("revision") != "unknown" and
        rev.get("est_rev_90d") is not None and
        (rev.get("data_source") or {}).get("point_in_time") is True
    )
    if revision_ready and (rev.get("price_rev_90d") or 0) - rev["est_rev_90d"] > 0.10:
        checks.append({"check": "price_vs_fundamentals", "channels": ["cashflow", "revision"],
                       "verdict": "divergent", "headline": "价格跑在预期前 ≈ positioning-driven"})
    elif not revision_ready:
        checks.append({"check": "price_vs_fundamentals", "channels": ["cashflow", "revision"],
                       "verdict": "skipped", "headline": "consensus point-in-time 冷启动，跳过价格vs基本面"})

    cf, dist = signals["cashflow"], signals["distribution"]
    if dist == "unknown":
        checks.append({"check": "valuation_vs_options", "channels": ["cashflow", "distribution"],
                       "verdict": "skipped", "headline": "期权分布不可得，跳过估值vs期权"})
    elif cf == "overvalued" and dist == "euphoric":
        checks.append({"check": "valuation_vs_options", "channels": ["cashflow", "distribution"],
                       "verdict": "contradiction", "headline": "高估 + 顶部拥挤"})
    elif cf == "overvalued" and dist == "neutral":
        checks.append({"check": "valuation_vs_options", "channels": ["cashflow", "distribution"],
                       "verdict": "divergent", "headline": "高估但期权自满"})

    if signals["revision"] == "improving" and dist == "bearish":
        checks.append({"check": "momentum_vs_options", "channels": ["revision", "distribution"],
                       "verdict": "divergent", "headline": "改善但期权定肥左尾"})

    if metrics["altitude"]["decomposition"]["company_pct"] < 0.30:
        checks.append({"check": "concentration", "channels": ["altitude"],
                       "verdict": "divergent", "headline": "大部分是 beta，伪分散"})
    return checks
```

（跨 horizon 的期限结构见下节，是 descriptive 层、永不判矛盾。）

### 期限结构（descriptive 层）

短/长 horizon 的 level 差异**只叙述、永不判矛盾**（F4）。填 reconciliation 的 `term_structure.shape`：

| 模式（短 horizon vs 长 horizon）| shape | headline 示例 |
|---|---|---|
| 短期期权平静 + 长期 DCF 激进 | `upward` | "期权平静 vs DCF 激进 = 期限结构，市场在赌长期" |
| 短期期权紧张 + 长期 DCF 平静 | `downward` | "期权定价财报风险但 DCF 平静 = 近期催化，长期无忧" |
| 短/长指向一致 | `flat` | "近端与长端同向 = 无期限结构张力" |

### reconciliation 结构（填 A 组的 reconciliation 字段）

```jsonc
{
  "checks": [                             // 只列触发的检查（未触发 = aligned）
    { "check": "price_vs_fundamentals", "channels": ["cashflow", "revision"],
      "verdict": "divergent",
      "headline": "价格涨 25% 但 estimates 只上修 12% = 跑在预期前，positioning-driven" },
    { "check": "valuation_vs_options", "channels": ["cashflow", "distribution"],
      "verdict": "divergent",
      "headline": "高估但期权平静 = 市场自满" }
  ],
  "term_structure": {                     // descriptive 层，永不判矛盾（F4）
    "shape": "upward",                    // upward=短期平静+长期激进 / downward / flat
    "note": "短期期权平静 vs 长期 DCF 激进 = 期限结构，非矛盾",
    "is_contradiction": false
  },
  "conviction_input": {                   // 喂 C3；数的是检查，不是桶
    "aligned_count": 2,                   // 4 个检查里 2 个 aligned（未触发）
    "divergent_count": 2,
    "contradiction_count": 0,
    "skipped_count": 0,                   // 数据冷启动 / honest-empty，不计入 aligned
    "total_checks": 4,
    "net_verdict": "divergent"            // 供 D 组 stance 消费（替代旧 long_bucket verdict）
  }
}
```

### count 语义明确 `[LOCKED 2026-07-04 · review 修正]`

**count 数的是"检查"，不是桶、也不是 channel。** 共 4 个具名检查（价格vs基本面 / 估值vs期权 / 预期vs期权 / 集中度），每个 aligned / divergent / contradiction / skipped。`skipped` 是诚实缺数据，不算 aligned，也不制造 divergence。`fragile = divergent + contradiction`。阈值现在**可达**（fragile ∈ 0..4）：

- fragile ≤ 1 → high（0-1 个软分歧可容忍）
- fragile == 2 → medium
- fragile ≥ 3 → low
- 任何 contradiction → 见 C3（硬信号优先）

**net_verdict**（供 stance 消费）：有 contradiction → `contradiction`；否则有 divergent → `divergent`；全 aligned → `aligned`。

---

## C3 · conviction 计算

### 设计原则

conviction 由**跨 channel 一致性（同 horizon 桶内）**决定（PRD §4.3）。不是单点判断，是对账结果的函数。

### conviction 判定规则

```python
def compute_conviction(reconciliation):
    ci = reconciliation["conviction_input"]
    divergent = ci["divergent_count"]
    contradiction = ci["contradiction_count"]
    fragile = divergent + contradiction

    skipped = ci.get("skipped_count", 0)

    # 矛盾优先（硬信号）。review 修正：当前检查集中 contradiction 数量很少，
    # 不能写成 >=2 才 low，否则几乎不可达。
    if contradiction >= 1:
        return "low"

    # 无矛盾：按脆检查数（total ~4，阈值可达）
    if fragile <= 1:
        return "medium" if skipped >= 2 else "high"  # 缺数据太多不可 high
    if fragile == 2:
        return "medium"
    return "low"           # 3+ 检查脆
```

### 降级规则

| 条件 | conviction 降级到 | 理由 |
|---|---|---|
| 任何 channel `status = "honest_empty"` | 至多 `medium` | 数据缺失不可高信心 |
| 任何 channel `status = "degraded"` | 不降级（但 headline 注明） | 退化是数据质量，非信号矛盾 |
| cashflow `point_solved = false`（DCF 无解）| 至多 `medium` | 纯叙事，无 DCF 锚 |
| evidence `honest_empty`（证据查不到）| 不降级（但 rationale 注明"未经网络验证"）| PRD §4.5 诚实约束 |

### conviction 与仓位的关系（链到 D 组）

conviction 不直接决定仓位，通过 §4.4 ③ 打折：
- high × 1.0
- medium × 0.6
- low × 0.3

conviction + edge 阈值（§4.5）共同决定最终仓位：conviction=low 且 edge<2% → 目标权重 0。

---

## 4 个算法参数已确认（全推荐）

| # | 参数 | 定值 | 衡量什么 |
|---|---|---|---|
| Q1 | 对账单位 | **4 个具名跨 channel 检查**（取代 3 horizon 桶）| 对账数几个关系 |
| Q2 | contradiction 严格度 | **≥1 → low** | 硬矛盾一条即低信心，避免死代码 |
| Q3 | conviction 阈值 | **fragile ≤1→high / ==2→medium / ≥3→low**（contradiction 优先降级）| 脆检查数切信心档 |
| Q4 | euphoric 信号 | **算** | call 倒挂算 euphoria/拥挤 |
