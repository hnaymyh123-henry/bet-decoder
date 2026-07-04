# SPEC · D 组 决策层

> 状态：草案 · 2026-07-04 · 待用户 review
> 依赖：SPEC A 组（TradePlan 存储）· SPEC B 组（channel metrics）· SPEC C 组（reconciliation + conviction）
> PRD 依据：§4 决策层 · 现有代码 `intelligence.py`（`_kelly` / `_wwhtbt` / `build_xray`）

---

## D 组总览

| # | 项 | 待 spec |
|---|---|---|
| D1 | stance 判定规则 | 跨 channel 信号 → stance 的映射表 |
| D2 | strategy archetype 映射 | 分析状态 → 打法原型 |
| D3 | Kelly 公式 spec | 期权 RND → 市场赔率 → ¼ Kelly × conviction → 上限截断 |
| D4 | KILL 分型具体形态 | 价格型（期权隐含触发概率）/ 基本面型（consensus 跟踪）|

---

## D1 · stance 判定规则

### 设计原则

stance 由**跨 channel 对账结果**驱动（PRD §4.4），不是单一 DCF 信号。输入 = reconciliation 的三态 + conviction + 各 channel 的方向信号。

### stance 枚举

`accumulate` / `strong_buy` / `hold` / `trim` / `avoid` / `short`（含 short，§4.4 LOCKED）

> ⚠ **一期 short 不可执行（review 修正）**：一期持仓中心、通用解码延后，你 short 的是**不持有**的票、无入口。一期把 short 场景降级为 **trim / hedge / avoid**（对已持有的减 / 对冲 / 不加），保留 short 语义但标 `actionable: false`，随通用解码一起在后续开放。

### 判定映射表

按 **conviction × net_verdict** 为主轴，revision + distribution 特殊信号作修正：

| conviction | net_verdict | cashflow signal | 修正信号 | → stance |
|---|---|---|---|---|
| high | consistent | undervalued | revision deteriorating | **accumulate**（降一级）|
| high | consistent | undervalued | — | **strong_buy** |
| high | consistent | fair | — | **accumulate** |
| high | consistent | overvalued | — | **hold**（已持有）/ **avoid**（未持有）|
| high | consistent | no_solution | distribution euphoric | **avoid**（纯叙事+顶部拥挤）|
| high | consistent | no_solution | distribution neutral/bearish | **hold**（无 DCF 锚，不激进）|
| high | divergence | undervalued | — | **accumulate**（有分歧不激进）|
| high | divergence | fair | — | **hold** |
| high | divergence | overvalued | — | **trim**（已持有）/ **avoid**（未持有）|
| medium | consistent | undervalued | revision deteriorating | **hold**（降一级）|
| medium | consistent | undervalued | — | **accumulate** |
| medium | consistent | fair | — | **hold** |
| medium | consistent | overvalued | — | **trim**（已持有）/ **avoid**（未持有）|
| medium | divergence | undervalued | revision deteriorating | **hold**（基本面好但预期在恶化）|
| medium | divergence | overvalued | distribution bearish | **short**（高估+下行保护贵）|
| medium | divergence | overvalued | distribution euphoric | **short**（顶部拥挤）|
| low | * | * | * | **avoid**（默认不参与）|
| low | contradiction | overvalued | distribution euphoric | **short**（矛盾+顶部=最脆）|

### 判定优先级（从高到低）

1. **conviction=low 默认 avoid**（一期不自动 short；overvalued + euphoric 只转 trim/avoid）
2. **distribution euphoric + overvalued → 升级到 short**（顶部拥挤是最强做空信号）
3. **distribution bearish + overvalued → 升级到 short**（下行保护贵 = 市场在为跌定价）
4. **revision deteriorating → 降一级**（预期在恶化，不激进）
5. **no_solution → 不出 strong_buy**（无 DCF 锚不能最高信心）

### stance 判定伪代码

```python
def determine_stance(reconciliation, conviction, distribution_signal,
                     revision_signal, cashflow_signal, has_position):
    # review 修正：对账从"桶"改为"检查"，用 net_verdict 替代 long_bucket verdict
    nv = reconciliation["conviction_input"]["net_verdict"]  # aligned | divergent | contradiction
    net_verdict = {"aligned": "consistent", "divergent": "divergence",
                    "contradiction": "contradiction"}[nv]  # 映射到下方判定表旧词汇

    # 规则 1：conviction=low 默认 avoid；一期不自动 short，short 只作为不可执行语义
    if conviction == "low":
        if (cashflow_signal == "overvalued" and
            distribution_signal == "euphoric" and
            net_verdict == "contradiction"):
            return "trim" if has_position else "avoid"
        return "avoid"

    # 规则 2-3：euphoric/bearish + overvalued → 一期已持有则 trim/hedge，未持有 avoid
    if cashflow_signal == "overvalued":
        if distribution_signal in ("euphoric", "bearish"):
            return "trim" if has_position else "avoid"

    # 规则 4：revision deteriorating 降一级
    downgrade = revision_signal == "deteriorating"

    # 主轴：conviction × long_verdict × cashflow_signal
    if conviction == "high" and net_verdict == "consistent":
        if cashflow_signal == "undervalued":
            return "accumulate" if downgrade else "strong_buy"
        if cashflow_signal == "fair":
            return "hold" if downgrade else "accumulate"
        if cashflow_signal == "overvalued":
            return "hold"  # 已持有 hold，未持有 avoid（下方处理）
        if cashflow_signal == "no_solution":
            return "hold"  # 无 DCF 锚不激进

    if conviction == "medium":
        if net_verdict == "consistent":
            if cashflow_signal == "undervalued":
                return "hold" if downgrade else "accumulate"
            if cashflow_signal == "fair":
                return "hold"
            if cashflow_signal == "overvalued":
                return "trim" if has_position else "avoid"
        if net_verdict == "divergence":
            if cashflow_signal == "undervalued":
                return "hold"  # 基本面好但分歧
            if cashflow_signal == "overvalued":
                return "trim" if has_position else "avoid"  # divergence + overvalued 已被规则 2-3 覆盖，兜底

    # 默认
    if cashflow_signal == "overvalued":
        return "hold" if has_position else "avoid"
    return "hold"
```

---

## D2 · strategy archetype 映射

### 设计原则

archetype = 分析状态 → 打法原型（PRD §4.2）。不是独立决策，是 stance 落地到具体执行策略。

### archetype 枚举 + 映射

| stance | conviction | net_verdict / 估值特征 | → archetype | 打法 |
|---|---|---|---|---|
| strong_buy | high | undervalued + consistent | **value_accumulate** | 分批建仓带 stop，每批 5% |
| accumulate | high/medium | undervalued/fair | **value_accumulate** | 同上，节奏更慢 |
| hold | any | fair / no_solution | **maintain** | 不动，盯 KILL |
| trim | medium | overvalued + divergence | **risk_off** | 减仓 1/3，收紧 stop |
| avoid | low / high+overvalued | — | **wait** | 不建仓，等 edge 出现 |
| short | any | overvalued + euphoric/bearish | **hedge_short** | 小仓做空，紧 stop 在 euphoric 消退处 |

### archetype → entry/stop/exit 规格化

```python
ARCHETYPE_SPEC = {
    "value_accumulate": {
        "entry": {"method": "staged", "batches": [{"weight": 0.5, "condition": "immediate"},
                                                    {"weight": 0.3, "condition": "pullback_5pct"},
                                                    {"weight": 0.2, "condition": "confirmation_30d"}]},
        "stop": {"method": "trailing", "trail_pct": 0.15},
        "exit": {"condition": "thesis_realized_or_edge_gone"}
    },
    "maintain": {
        "entry": {"method": "none"},
        "stop": {"method": "kill_line_only"},  # 只盯 KILL，不下 trailing stop
        "exit": {"condition": "kill_triggered_or_thesis_broken"}
    },
    "risk_off": {
        "entry": {"method": "reduce", "reduce_pct": 0.33},
        "stop": {"method": "tighten", "trail_pct": 0.08},
        "exit": {"condition": "reduced_to_target_or_edge_returns"}
    },
    "wait": {
        "entry": {"method": "none"},
        "stop": None,
        "exit": {"condition": "edge_appears"}
    },
    "hedge_short": {
        "entry": {"method": "single", "condition": "immediate"},
        "stop": {"method": "fixed", "above_entry_pct": 0.10},  # 空头 stop 在 entry 以上
        "exit": {"condition": "euphoric_fades_or_thesis_realized"}
    }
}
```

---

## D3 · Kelly 公式 spec

### 设计原则

PRD §4.3：**期权分布给的是"市场开出的赔率"（Q 测度），不是你的 p_win**。Kelly = f(你的 p, 市场赔率)。

### 三层输入

| 输入 | 来源 | 说明 |
|---|---|---|
| **你的 p（p_win）** | `intelligence.py` base_rate + 场景概率 | 你的独立 view——base_rate 那套（原设计是对的），给"你认为多大可能涨" |
| **市场赔率** | distribution channel 的 RND | 期权 RND = 市场为各种结局开出的价格 → 转成赔率 |
| **conviction** | C3 reconciliation | 打折系数（high×1.0 / medium×0.6 / low×0.3）|

### Kelly 计算步骤

```python
def compute_kelly(your_p_win, rnd, conviction, current_price):
    """从你的 p_win + 期权 RND 算 ¼ Kelly × conviction 仓位。"""

    # Step 1：从 RND 提取市场赔率
    # RND 给的是 P(market) 的密度；把"涨"和"跌"的尾部概率积出来
    p_market_up = rnd_integrate(rnd, lower=current_price, upper=inf)   # 市场认为涨的概率
    p_market_down = 1.0 - p_market_up

    # 市场赔率 = 如果涨，你赚多少 / 如果跌，你亏多少
    # 用 RND 的条件期望算涨跌幅度
    expected_up = rnd_conditional_expectation(rnd, lower=current_price) / current_price - 1
    # review 修正：down 用对称口径（损失分数），与 up 一致；旧式 P/E-1 会系统性偏
    expected_down = 1 - rnd_conditional_expectation(rnd, upper=current_price) / current_price

    # 市场赔率 b = expected_up / expected_down（赢的倍数 / 输的倍数）
    b = expected_up / expected_down if expected_down > 0 else float('inf')

    # Step 2：原始 Kelly 公式
    # f* = (b * p - q) / b，其中 p=你的 p_win，q=1-p
    p = your_p_win
    q = 1.0 - p
    raw_kelly = (b * p - q) / b if b > 0 else 0.0

    # Step 3：¼ Kelly（§4.4 LOCKED）
    fractional_kelly = raw_kelly * 0.25

    # Step 4：conviction 打折（§4.4 LOCKED）
    conviction_mult = {"high": 1.0, "medium": 0.6, "low": 0.3}[conviction]
    adjusted = fractional_kelly * conviction_mult

    # Step 5：上限截断（§4.4 LOCKED）
    target_weight = min(adjusted, 0.20)  # 单仓上限 20%

    return {
        "raw_kelly": round(raw_kelly, 4),
        "fractional_kelly": round(fractional_kelly, 4),
        "conviction_multiplier": conviction_mult,
        "target_weight": round(target_weight, 4),
        "cap": 0.20,
        "market_odds": {"p_market_up": round(p_market_up, 4),
                        "b": round(b, 4),
                        "expected_up": round(expected_up, 4),
                        "expected_down": round(expected_down, 4)}
    }
```

### 你的 p_win 怎么来（复用 intelligence.py）

`intelligence.py` 的 `_scenario_probs` 已经用 max-entropy 算了场景概率。升级版 = 把"你的 p_win" = 场景概率里 bull+moonshot 的权重（你认为涨的概率），但**用 base_rate 校准**——如果 base_rate 显示隐含增速在 95 分位（极罕见），你的 p_win 要往下压（base_rate 惩罚）。

> ⚠ **epistemic 局限（review 修正）**：这个 p_win 取自同一套 DCF scenario_probs，**不是真正独立**于估值模型。于是 edge（你的 p vs 市场 p）有一部分是"模型跟自己比"。base_rate 惩罚是唯一的外部锚。真正独立的 view（如用户手动 override、或第二套独立估值）留待后续；文档承认此限，不宣称 edge 是完全独立的两方对撞。

```python
def extract_base_rate_pct01(intelligence_xray):
    """从真实 intelligence.py 输出取 base-rate 分位，并统一到 0..1。"""
    br = intelligence_xray.get("base_rate") or {}
    live = br.get("live") or {}
    pct = live.get("percentile")
    if pct is None:
        return None
    return pct / 100.0 if pct > 1 else pct

def your_p_win(intelligence_xray):
    """从 intelligence xray 的场景概率 + base_rate 惩罚算你的 p_win。"""
    probs = intelligence_xray["scenario_probs"]["by_name"]
    raw_p = (probs.get("bull", 0) + probs.get("moonshot", 0))

    # out-of-envelope 诚实处理：真实 intelligence._kelly 在 above_top 时 p_win=0。
    # 决策层沿用这个风险读数，不把 moonshot 概率硬补回来。
    if intelligence_xray.get("scenario_probs", {}).get("note") == "above_top":
        return 0.0

    pct01 = extract_base_rate_pct01(intelligence_xray)

    # base_rate 惩罚：隐含增速越罕见，你的 p_win 越保守
    if pct01 is None:
        penalty = 0.7                   # 缺 outside-view 时保守
    elif pct01 > 0.90:                  # 极罕见
        penalty = 0.5
    elif pct01 > 0.75:                  # 罕见
        penalty = 0.7
    else:                               # 常见
        penalty = 1.0

    return min(raw_p * penalty, 1.0)
```

### edge 计算

```python
def compute_edge(your_p_win, market_p_up, rnd, current_price, target_price=None):
    """edge = 你的 view 与市场隐含 view 的差距。"""
    # edge = your_p_win - market_p_up（概率差）
    edge_prob = your_p_win - market_p_up

    # 如果有目标价，也算分位 edge
    if target_price:
        view_quantile = rnd_integrate(rnd, lower=target_price, upper=inf)
        market_quantile = rnd_integrate(rnd, lower=current_price, upper=inf)
        edge_quantile = view_quantile - market_quantile
    else:
        edge_quantile = None

    return {
        "edge_prob": round(edge_prob, 4),        # 概率差（用于 §4.5 edge<2% 判定）
        "edge_quantile": round(edge_quantile, 4) if edge_quantile else None,
        "view_quantile": round(view_quantile, 4) if target_price else None,
        "supporting_channels": [...],             # 哪些 channel 信号支持你的 view
        "opposing_channels": [...]                # 哪些反对
    }
```

---

## D4 · KILL 分型具体形态

### 设计原则

PRD §4.3：KILL 分型——价格可观测型 → 期权报隐含触发概率；基本面型 → consensus 修正跟踪。

### 价格型 KILL

**触发**：当 stance 含 stop 价位时（archetype spec 里的 stop.level）。

**期权隐含触发概率**：从 RND 积分 stop 价以下的尾部概率。

```python
def price_kill_implied_prob(rnd, stop_price):
    """从期权 RND 算 stop 被触发的隐含概率。"""
    prob = rnd_integrate(rnd, upper=stop_price)  # RND 对 stop 以下的积分
    return {
        "type": "price",
        "level": stop_price,
        "implied_prob": round(prob, 4),
        "interpretation": f"你的 stop ${stop_price} 在 RND 的 {prob*100:.0f}% 尾巴"
    }
```

**退化**：如果 RND 不可得（个股无期权/流动性差）→ `implied_prob = null` + status `"degraded"`，stop 价仍设但报不了隐含概率。

### 基本面型 KILL

**触发**：当 cashflow channel 有 `xray.wwhtbt` 里的 kill_line 时（来自 `intelligence.py._wwhtbt`）。

**consensus 跟踪**：KILL line 是"营收增速连 2 季破 11%"这类 → 用 revision channel 的 consensus 修正跟踪。

```python
def fundamental_kill_tracking(kill_line, revision_metrics):
    """基本面型 KILL 的跟踪状态。"""
    # kill_line 解析出指标 + 阈值（从 intelligence.py 的 wwhtbt kill 项解析）
    # 例："营收增速连续 2 季跌破 11%" → metric=revenue_growth, threshold=0.11, quarters=2

    # 从 revision channel 的 consensus 数据看当前值
    current_value = revision_metrics.get("est_rev_90d")  # 简化：用 estimate 修正趋势代理

    return {
        "type": "fundamental",
        "line": kill_line,                     # 原始 KILL line 文本
        "metric": "revenue_growth",            # 解析出的指标
        "threshold": 0.11,                     # 阈值
        "quarters_required": 2,                # 需要连续几季
        "current_value": current_value,
        "implied_prob": None,                  # 基本面型：期权定价不了
        "tracking": "consensus_revision",      # 跟踪方式
        "status": "monitoring"                 # monitoring | approached | triggered
    }
```

**触发逻辑**（§6 监控用）：

| 状态 | 条件 | 行为 |
|---|---|---|
| `monitoring` | 当前值未接近阈值 | 继续盯 |
| `approached` | 当前值在阈值 20% 范围内 | feed 标黄 + 提醒 |
| `triggered` | 连续 N 季突破阈值 | push 告警 + thesis 标 invalidated |

---

## Trade Plan 组装伪代码

```python
def panel_by_channel(panel_list):
    """panel 是 list，不是 dict。按 channel 建索引。"""
    return {p["channel"]: p for p in panel_list}

def metric(panel_map, channel, path, default=None):
    """从 panel[channel].metrics 取字段；path 用点号，如 'xray.wwhtbt'。"""
    cur = (panel_map.get(channel) or {}).get("metrics") or {}
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur

def signal(panel_map, channel, default="unknown"):
    p = panel_map.get(channel) or {}
    return p.get("signal") or (p.get("metrics") or {}).get("signal") or default

def build_trade_plan(reconciliation, conviction, panel, position_context):
    panel_map = panel_by_channel(panel)
    has_position = position_context["has_position"]
    current_price = metric(panel_map, "cashflow", "anchor_price") or metric(panel_map, "distribution", "spot")
    distribution_rnd = metric(panel_map, "distribution", "rnd")
    xray = metric(panel_map, "cashflow", "primary.xray") or metric(panel_map, "cashflow", "xray") or {}

    # 1. stance（D1）
    stance = determine_stance(reconciliation, conviction,
                              distribution_signal=signal(panel_map, "distribution"),
                              revision_signal=signal(panel_map, "revision"),
                              cashflow_signal=signal(panel_map, "cashflow"),
                              has_position=has_position)

    # 2. archetype（D2）
    archetype = map_archetype(stance, conviction, reconciliation)

    # 3. edge（D3）
    your_p = your_p_win(xray)
    market_p_up = rnd_integrate(distribution_rnd, lower=current_price, upper=inf) if distribution_rnd else None
    edge = compute_edge(your_p, market_p_up, distribution_rnd, current_price) if market_p_up is not None else {
        "edge_prob": None,
        "edge_quantile": None,
        "supporting_channels": [],
        "opposing_channels": ["distribution_missing"]
    }

    # 4. size（D3）
    size = compute_kelly(your_p, distribution_rnd, conviction, current_price) if distribution_rnd else {
        "raw_kelly": 0.0,
        "fractional_kelly": 0.0,
        "conviction_multiplier": {"high": 1.0, "medium": 0.6, "low": 0.3}[conviction],
        "target_weight": 0.0,
        "cap": 0.20,
        "market_odds": None,
        "status": "degraded_no_rnd"
    }

    # 5. 诚实约束闸门（§4.5）
    if conviction == "low" and (edge["edge_prob"] is None or edge["edge_prob"] < 0.02):
        size["target_weight"] = 0.0
        stance = "avoid"
        archetype = "wait"

    # 6. stop + KILL（D4）
    archetype_spec = ARCHETYPE_SPEC[archetype]
    stop_price = compute_stop(archetype_spec, current_price)
    kill = build_kill(archetype_spec, xray.get("wwhtbt"),
                      distribution_rnd, stop_price,
                      (panel_map.get("revision") or {}).get("metrics") or {})

    # 7. entry + exit（D2 archetype spec）
    entry = build_entry(archetype_spec, current_price)
    exit_cond = archetype_spec["exit"]

    # 8. rationale + self_falsification（强制）
    rationale = synthesize_rationale(stance, conviction, edge, reconciliation)
    self_falsification = kill["line"] if kill else None
    if not self_falsification:
        return None  # §4.5：无认错条件 → 拒绝生成

    return {
        "stance": stance,
        "strategy_archetype": archetype,
        "conviction": conviction,
        "edge": edge,
        "entry": entry,
        "size": size,
        "stop": stop_price,
        "kill": kill,
        "exit": exit_cond,
        "rationale": rationale,
        "self_falsification": self_falsification
    }
```

---

## 4 个算法参数已确认（全推荐）

| # | 参数 | 定值 | 意义 |
|---|---|---|---|
| R1 | base_rate 惩罚梯度 | **3 档**（>90 分位压 0.5 / >75 压 0.7 / 其他不压）| 你的 p_win 在 base_rate 多罕见时往下压 |
| R2 | value_accumulate 分批节奏 | **3 批 50/30/20**（immediate / pullback_5pct / confirmation_30d）| 建仓分几批 + 条件 |
| R3 | trailing stop 幅度 | **15%** | value_accumulate 的 trailing stop 宽度 |
| R4 | hedge_short stop 位 | **entry 以上 10%** | 做空 stop 在 entry 以上多少 |
