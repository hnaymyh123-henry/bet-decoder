# SPEC · B 组 各 channel 输出（算法层）

> 状态：草案 · 2026-07-04 · 待用户 review
> 依赖：SPEC A 组（panel[].metrics 信封）· PRD v2.0 §2（预期分解栈）· §3（期权分布）
> 现有代码：`decoder.py` 7 lens + `reverse_dcf.py` + `intelligence.py`

---

## B 组总览

| # | channel | 现状 | spec 目标 |
|---|---|---|---|
| B1 | 现金流账 | 7 lens 已实现（`decoder.py` LENS_REGISTRY），输出结构已有 | spec 化现有 lens 输出 → panel[].metrics.cashflow |
| B2 | 分布账（keystone）| 不存在 | 5 个量的计算 spec + 算法参数 |
| B3 | 变化账 | 不存在 | 输出字段 + 数据来源（qveris provider）|
| B4 | 高度栈·可测半 | 不存在 | macro/industry/company 三层 exposure 回归 spec |
| B5 | 高度栈·叙事半 | `narrative.py` 已有 gated DR | 按高度打标 + 输入/输出 schema |

---

## B1 · 现金流账 · metrics spec

### 现有代码结构（`decoder.py`）

7 lens 共享统一信封（`_result()`）：
```python
{
  "metric": str,           # 反解的指标名
  "implied_value": float,  # 隐含数值
  "implied_label": str,    # 中文标签
  "unit": str,             # 单位（默认 "x"）
  # + lens-specific extra 字段
}
```

### panel[].metrics.cashflow spec

```jsonc
{
  "primary_lens": "dcf",                    // primary lens key
  "primary": {                              // primary lens 完整结果（= v3 primary_lens）
    "metric": "implied_revenue_cagr_5y",
    "implied_value": 0.38,
    "implied_label": "隐含 5 年营收 CAGR",
    "unit": "",
    "point_solved": true,                   // false = 无可行解（TSLA 式）
    // DCF-specific extra（仅 dcf lens）：
    "implied_cagr": 0.38,
    "band": { "p25": 0.32, "p50": 0.38, "p75": 0.45 },  // 蒙特卡洛 R2
    "baseline_dcf_price": 165.0,            // industry scenario
    "baseline_dcf_low": 120.0,              // conservative scenario
    "baseline_dcf_high": 180.0,             // momentum scenario
    "hist_cagr": 0.25,
    "hist_cagr_capped": 0.25,
    "scenario_conservative": 120.0,
    "scenario_industry": 165.0,
    "scenario_momentum": 180.0,
    "sector": "technology",
    "industry_cagr": 0.12,
    "sector_tam": 500000000000,             // null when unknown
    "momentum_start": 0.25,
    "implied_rev_5y": 50000000000,
    "implied_market_share": 0.1,            // null when no TAM
    "revenue_ttm": 10000000000,
    "risk_free_used": 0.043,
    "risk_free_source": "yfinance_10y",
    "consensus_wacc": 0.085,
    "consensus_terminal_growth": 0.025,
    "consensus_terminal_fcf_margin": 0.35,
    "xray": {                               // intelligence.py X-RAY 层
      "base_rate": {                        // = intelligence.py build_xray().base_rate
        "verdict": "top-tail",
        "live": {
          "percentile": 95,                 // 真实代码是 0-100 分位，不是 0-1
          "share_ge_pct": 3.2
        },
        "headline": "要求跑出历史前 5% 的增速"
      },
      "scenario_probs": { /* max-entropy */ },
      "driver_elasticity": { /* */ },
      "implied_cap": null,
      "wwhtbt": [ /* what-would-have-to-be-true */ ],
      "kill_line": "营收增速连 2 季破 11%"
    }
  },
  "cross": [                                // 交叉验证 lens（= v3 cross_lenses）
    {
      "lens": "pe",
      "metric": "implied_pe",
      "implied_value": 45.2,
      "implied_label": "隐含市盈率 P/E",
      "unit": "x"
    },
    {
      "lens": "ps",
      "metric": "implied_ps",
      "implied_value": 28.5,
      "implied_label": "隐含市销率 P/S",
      "unit": "x"
    }
  ],
  "narrative_premium": 0.232,               // (anchor - baseline_dcf_price) / anchor = (214.86-165)/214.86
  "narrative_premium_vs_low": 0.442,        // (anchor - baseline_dcf_low) / anchor = (214.86-120)/214.86（参考：vs conservative）
  "cross_validation_divergence": 0.12,      // cross lens 间最大分歧（待 C 组定义算法）
  "undervalued": false,                     // baseline > anchor?
  "above_top": false,                       // anchor > 所有 scenario?
  "point_solved": true,                     // primary 有可行解?
  "signal": "overvalued"                    // 必填标准字段，供 C2 对账用：overvalued|fair|undervalued|no_solution
}
```

### 多倍数 lens（pe/ps/ev_ebitda/p_fcf/p_b/peg）的输出

只有 `metric` / `implied_value` / `implied_label` / `unit`，无 DCF 的 extra 字段。PEG 多 `implied_pe` + `growth_pct`。

### anchor mode（叙事/AI 复合体）的 cashflow channel

当 DCF 无解（TSLA 式）→ primary_lens 的 `point_solved=false` + `implied_value=null`，cashflow channel 的 status 降级为 `"degraded"`，headline 改为"无 DCF 解——价格由非-DCF channel 主导"。此时 anchor_mode 的 components（narrative/option/tam/analogy）归到 altitude channel 的叙事半，不重复进 cashflow。

---

## B2 · 分布账（keystone）· metrics spec + 算法参数

### panel[].metrics.distribution spec

```jsonc
{
  "instruments": [                          // 读的期权标的（高度栈决定）
    { "type": "stock", "symbol": "NVDA", "role": "company" }
    // 行业层：{ "type": "etf", "symbol": "SMH", "role": "industry" }
    // 宏观层：{ "type": "etf", "symbol": "SPY", "role": "macro" }
    //         { "type": "index", "symbol": "VIX", "role": "macro_vol" }
  ],
  "implied_range": {                        // 量 1：ATM straddle 隐含区间
    "lower": 185.0,
    "upper": 248.0,
    "confidence": 0.68,                     // 1 σ = 68%
    "expiry": "2026-10-17",                 // 用的到期日
    "days_to_expiry": 105,
    "straddle_price": 16.5,                 // ATM straddle 价
    "straddle_pct": 0.077                   // straddle / 现价
  },
  "rnd": {                                  // 量 2：风险中性密度
    "method": "breeden_litzenberger",
    "smoothing": "cubic_spline",            // smile 平滑方法
    "expiry": "2026-10-17",
    "forward": 216.3,                       // 用 put-call parity 估的 forward，缺失时用 spot 并标 degraded
    "discount_factor": 0.989,
    "strikes": [180, 185, 190, /* ... */ 280],
    "call_prices": [38.2, 34.1, 30.4, /* ... */ 1.2],  // mid price after quality filter
    "densities": [0.02, 0.05, /* ... */ 0.01],  // discounted second derivative, normalized
    "tail_policy": "lognormal_extrapolate_to_0_999",
    "normalization": "area_1",              // 积分到 1
    "quality": "high"                       // high = 行权点≥15 / medium = 8-14 / low = <8
  },
  "prob_of_target": {                       // 量 3：目标价尾部概率
    "target": 300.0,
    "prob_above": 0.08,                     // RND 对 target 以上的积分
    "prob_below": 0.92,
    "interpretation": "你的 $300 目标在 ~8% 尾巴"
  },
  "skew": {                                 // 量 4：skew + 自身历史分位
    "raw": 0.05,                            // 25Δ put IV − 25Δ call IV
    "put_iv": 0.48,
    "call_iv": 0.43,
    "percentile_1y": 0.82,                  // 对自身 1y skew 的分位
    "comparison_window": "1y",              // 对比的回看窗口
    "interpretation": "下行保险比近一年更贵"  // percentile > 0.8
  },
  "term_structure": {                       // 量 5：期限结构
    "near_expiry": "2026-08-15",
    "far_expiry": "2027-01-15",
    "near_iv": 0.45,
    "far_iv": 0.38,
    "slope": "downward",                    // downward = near > far / upward = near < far
    "interpretation": "近月更贵 = 定价财报/催化风险"
  },
  "cross_altitude_vol": {                   // 跨高度 vol 分解（§3.3）
    "stock_iv": 0.45,
    "theme_iv": 0.42,                       // SMH IV
    "market_iv": 0.16,                      // VIX
    "decomposition": {
      "idiosyncratic": 0.03,                // stock - theme
      "theme_residual": 0.26,               // theme - market
      "market": 0.16
    },
    "interpretation": "NVDA 自己平静，但主题/大盘在定肥左尾 = 相对自满、脆"
  },
  "signal": "bearish"                        // 方向信号（供 C2 对账用）：bullish|neutral|bearish|euphoric
}
```

### RND 数值算法 `[LOCKED MVP]`

Breeden-Litzenberger 是 keystone 的心脏，MVP 不留实现自由度：

1. 选择目标到期日：取距离 90 天最近、且 DTE 在 45-150 天之间的 expiry；若没有，降级为最近 DTE>=21 的 expiry 并标 `quality=medium`。
2. 过滤 strike：只保留 bid>0、ask>bid、spread/mid <= 15%、open_interest 或 volume 非零的 call；少于 8 个有效 strike 时跳过 RND，只保留 ATM straddle 区间。
3. 估 forward：优先用 put-call parity 在近 ATM strike 估 `F = K + exp(rT)(C-P)`；put 数据缺失时用 spot，`rnd.quality` 至多 `medium`。
4. 平滑 call price 曲线：对 `(strike, call_mid)` 做 monotone cubic spline；平滑后必须满足 call price 随 strike 单调下降，否则删除异常点重拟合一次。
5. 二阶差分：在等距 strike 网格上计算 `density(K_i) = exp(rT) * (C_{i-1} - 2*C_i + C_{i+1}) / dK^2`；负 density 截断为 0，并记录 `negative_mass_clipped`。
6. 尾部处理：左右尾用与 ATM IV 匹配的一阶 lognormal tail 外推到累计概率 0.1% / 99.9%；若外推后尾部质量 > 20%，`quality=low`。
7. 归一化：梯形积分归一到 1；输出 `strikes[]`、`densities[]`、`cdf[]`（可选）和质量诊断。

质量诊断字段：

```jsonc
{
  "quality": "high",
  "diagnostics": {
    "valid_strike_count": 21,
    "grid_step": 5,
    "negative_mass_clipped": 0.014,
    "tail_mass": 0.083,
    "forward_source": "put_call_parity"
  }
}
```

### 算法参数（已定值）

| # | 参数 | 意义 | 候选 | 推荐 |
|---|---|---|---|---|
| **P1** | **到期日选择** | 算隐含区间/skew 用哪个到期日 | A=最近月 / B=最近季月 / C=~90 天 | **C（~90 天）** — 太短噪声大，太长不反映近期预期；90 天是"3 个月"白话的对应 |
| **P2** | **smile/价格曲线平滑方法** | 算 RND 前平滑 call price 曲线 | A=monotone cubic spline / B=SVI 参数化 / C=局部多项式 | **A（monotone cubic spline）** — 简单可靠，够 MVP；SVI 更专业但复杂 |
| **P3** | **strike 范围** | 取哪些行权价算 RND | A=全部 / B=±2σ 内 / C=有 bid 的行权价 | **C（有 bid 的）** — 避免无流动性 strike 的噪声 IV |
| **P4** | **25Δ 插值** | 算 skew 时 25Δ 的 IV 怎么来 | A=线性插值 IV / B=BSM 反解 | **B（BSM 反解）** — 从期权价格反解 IV 更准 |
| **P5** | **skew 分位窗口** | skew 对比的历史回看窗口 | A=6m / B=1y / C=2y | **B（1y）** — 平衡信号新鲜度与稳定性 |
| **P6** | **term structure 到期日** | 算 term structure 用哪两个到期日 | A=最近月 vs 次月 / B=最近季月 vs 次季月 | **A（最近月 vs 次月）** — 最敏感的催化信号 |

### 退化逻辑（PRD §3.6 落地）

| 条件 | 退化行为 | status |
|---|---|---|
| 行权点 < 8 | 跳过 RND，只出 ATM 隐含区间 | `"degraded"` |
| 个股无期权 | honest-empty，靠主题/指数 ETF 期权 + beta 兜底；`signal="unknown"`，不得参与 contradiction | `"honest_empty"` |
| 价差 > 15% mid | 该 strike 标低质量，不入 RND 计算 | 仍出隐含区间，RND 标 `"degraded"` |

---

## B3 · 变化账 · metrics spec

### panel[].metrics.revision spec

```jsonc
{
  "est_rev_90d": 0.12,                      // 90 天 consensus estimate 修正 %
  "est_rev_30d": 0.05,                      // 30 天
  "price_rev_90d": 0.25,                    // 90 天价格变化
  "price_rev_30d": 0.08,                    // 30 天
  "price_ahead_of_estimates": true,         // price_rev > est_rev（价格跑在预期前面）
  "divergence": {                           // 分析师分歧度
    "method": "std_of_estimates",
    "value": 0.08,                          // estimate 的标准差 / 均值
    "percentile_1y": 0.65                   // 对自身 1y 分歧度的分位
  },
  "reaction_function": {                    // 价格对 estimate 修正的反应
    "slope": 2.08,                          // price_rev_90d / max(|est_rev_90d|, ε)；est≈0 时 slope=null（review 修正，防除零爆炸）
    "slope_valid": true,                    // false = estimates 基本没动，slope 无意义，改看 price_ahead_of_estimates 布尔
    "interpretation": "价格对 estimate 修正的敏感度偏高"
  },
  "data_source": {
    "provider": "finnhub",                  // qveris 路由的 provider
    "field": "analyst_estimate_revisions",
    "point_in_time": false                  // ⚠️ 见 O6 风险：免费 provider 非 point-in-time
  },
  "signal": "improving"                     // 必填标准字段，供 C2 对账用：improving|stable|deteriorating|unknown
}
```

### 数据来源（待 O6 inspect 确认）

| 字段 | provider 候选 | point-in-time? |
|---|---|---|
| est_rev_90d/30d | Finnhub / Alpha Vantage | ❌ 当前值 |
| divergence | Finnhub / Yahoo Finance | ❌ |
| price_rev | 任意（价格是天然 point-in-time）| ✅ |

**point-in-time 冷启动降级 `[LOCKED]`**：若 consensus 历史不可得 → `est_rev_90d=null`、`signal="unknown"`、`status="honest_empty"`，并在 `data_source.point_in_time=false` 中明示。C 组所有依赖 revision 的 check 必须 `skip`，不得把"价格上涨但 est_rev=null"误报为 positioning-driven。系统从当天开始每日记录 consensus 快照；累计满 30 天后可启用 30d revision check，满 90 天后启用旗舰 90d check。

### 标准 signal 契约 `[LOCKED]`

所有 channel 必须在 panel 信封顶层或 metrics 内产出 `signal`；C 组只读这些标准 signal，不读各 channel 内部临时字段。

| channel | allowed signals | unknown/empty 行为 |
|---|---|---|
| cashflow | `overvalued` / `fair` / `undervalued` / `no_solution` | `no_solution` 可参与对账，但 conviction 至多 medium |
| distribution | `bullish` / `neutral` / `bearish` / `euphoric` / `unknown` | `unknown` 跳过与 distribution 相关 check |
| revision | `improving` / `stable` / `deteriorating` / `unknown` | `unknown` 跳过与 revision 相关 check |
| altitude | macro/industry/company 三层 signal | 缺层时该层 check 跳过 |

阈值统一：

- distribution `bearish`: `skew.percentile_1y > 0.80` 或 put-side tail probability 显著高于 1y 中位。
- distribution `euphoric`: 25Δ call IV > 25Δ put IV 且该倒挂在自身 1y call-skew 分位 > 0.80。
- revision `improving/deteriorating`: `est_rev_90d > +5%` / `< -5%`；冷启动 unknown。
- price-ahead flagship check: `price_rev_90d - est_rev_90d > 10pp`，但仅当 `est_rev_90d` 非 null 且 `data_source.point_in_time=true` 或本地快照覆盖满 90 天。

---

## B4 · 高度栈·可测半 · metrics spec

### panel[].metrics.altitude spec（可测半）

```jsonc
{
  "macro": {
    "method": "regression",
    "factor": "SPY",                        // 宏观因子（市场 beta）
    "beta": 1.15,                           // 60 日回归 beta
    "r_squared": 0.62,
    "window": "60d",                        // 回归窗口
    "narrative": null,                      // 叙事半（gated DR，见 B5）
    "dr_gated": false                       // 是否开了 DR
  },
  "industry": {
    "method": "regression",
    "factor": "SMH",                        // 主题 ETF（§2.3 MVP 用现成 ETF 代理）
    "beta": 1.30,
    "r_squared": 0.71,
    "window": "60d",
    "theme_etf": "SMH",                     // 用的主题 ETF
    "narrative": null,
    "dr_gated": false
  },
  "company": {
    "method": "residual",                   // 剥掉 macro + industry 后的残差
    "residual_variance": 0.4,               // 残差占总方差的比例
    "interpretation": "40% 是它自己的 bet",
    "narrative": null,
    "dr_gated": true                        // 公司层叙事半常开 DR
  },
  "decomposition": {                        // 价格归因（镜像 vol 分解）
    "macro_pct": 0.35,                      // 宏观 beta 贡献
    "industry_pct": 0.25,                   // 主题 beta 贡献
    "company_pct": 0.40,                    // 公司特有
    "method": "variance_decomposition"
  },
  "signals": {                              // 方向信号（供 C2 对账用，按高度拆分）
    "macro": "risk_on",                     // risk_on|risk_off|neutral
    "industry": "theme_bid_up",             // theme_bid_up|theme_cooling|neutral
    "company": "residual_high"              // residual_high|residual_low|neutral
  }
}
```

### 算法参数（需用户拍板）

| # | 参数 | 意义 | 候选 | 推荐 |
|---|---|---|---|---|
| **P7** | **回归窗口** | beta 回归用多少天 | A=30d / B=60d / C=120d | **B（60d）** — 30d 太短噪声大，120d 太长不反映近期 |
| **P8** | **主题 ETF 映射** | 怎么把 ticker 映射到主题 ETF | A=硬编码表 / B=qveris REF.CLASSIFICATION.THEME / C=手动配置 | **A（硬编码表）** — MVP 票少，硬编码够快；后期接 qveris |

### P8 当前选择 + 后续迭代路径 `[LOCKED MVP · 迭代路径已标注]`

**MVP（当前）**：硬编码 `THEME_ETF_MAP`（见下表）。理由——一期 demo 只做持仓扫描，票少（NVDA/COST/TSLA 等样本），硬编码最快、零外部依赖、零 credits 成本。映射表：

```python
THEME_ETF_MAP = {
    "semiconductor": "SMH",      # NVDA, AMD, AVGO, TSM, etc.
    "ai_infra": "SMH",           # 暂归半导体
    "cloud": "SKYY",             # 或 FDN
    "ev_battery": "LIT",         # 宁德时代相关
    "fintech": "FINX",
    "biotech": "XBI",
    "energy": "XLE",
    "consumer_tech": "XLY",      # COST, AMZN
    # default: SPY（无主题 → 纯市场 beta）
}
```

ticker → theme 的映射通过 `fundamentals.industry` 关键词匹配（复用 `reverse_dcf.sector_of()`）。

**迭代路径**：

| 阶段 | 方案 | 触发条件 | 改动 |
|---|---|---|---|
| **v1.1** | 硬编码表扩充 + 手动配置覆盖 | 持仓票超出硬编码覆盖范围 | 加 `ticker_override` 字段（用户/agent 可指定某 ticker 用哪个 ETF）|
| **v1.2** | 接 qveris `REF.CLASSIFICATION.THEME` | 持仓票多、主题多、硬编码维护成本高 | qveris discover "thematic classification" → call 拿 ticker→theme 映射；硬编码表降级为 fallback |
| **v2.0** | 自建主题篮子（不依赖现成 ETF） | 需要更精确的主题暴露（现成 ETF 覆盖不全 / 成分股漂移）| 用 `theme_exposures` 表的自定义篮子 + 持仓成分股回归 |
| **P9** | **归因方法** | macro/industry/company 怎么切 | A=方差分解 / B=连续回归正交化 | **A（方差分解）** — 简单直观，够 MVP |

### 硬编码主题 ETF 映射（MVP）

```python
THEME_ETF_MAP = {
    "semiconductor": "SMH",      # NVDA, AMD, AVGO, TSM, etc.
    "ai_infra": "SMH",           # 暂归半导体
    "cloud": "SKYY",             # 或 FDN
    "ev_battery": "LIT",         # 宁德时代相关
    "fintech": "FINX",
    "biotech": "XBI",
    "energy": "XLE",
    "consumer_tech": "XLY",      # COST, AMZN
    # default: SPY（无主题 → 纯市场 beta）
}
```

---

## B5 · 高度栈·叙事半 · gated DR 接口 spec

### 现有代码（`narrative.py`）

已有 `narrative.py` 做 gated Deep Research（MiroMind flagship）。v2.0 的改动 = **按高度打标** + **结构化输出**。

### gated 触发条件

| 高度 | 触发条件 | 理由 |
|---|---|---|
| macro | `narrative_premium >= 0.5` 且 macro beta 高 | 价格一半以上靠叙事 + 宏观主导 |
| industry | `narrative_premium >= 0.5` 且 industry beta 高 | 同上，主题主导 |
| company | `narrative_premium >= 0.3`（比 macro/industry 低）| 公司层叙事更常见，阈值低 |

### DR 输入 schema

```jsonc
{
  "altitude": "company",                    // macro | industry | company
  "subject": "NVDA",
  "trigger": "narrative_premium=0.42 >= 0.3",
  "context": {
    "implied_cagr": 0.38,                   // 来自 cashflow channel
    "baseline_dcf_price": 165.0,
    "anchor_price": 214.86,
    "narrative_premium": 0.42,
    "macro_beta": 1.15,
    "industry_beta": 1.30,
    "company_residual": 0.40
  },
  "question": "NVDA 价格的 42% 叙事溢价，市场在讲什么故事？ASIC 替代速度？AI capex 复利？"
}
```

### DR 输出 schema（挂到 altitude channel 的 narrative 字段）

```jsonc
{
  "altitude": "company",
  "narrative": {
    "headline": "市场在赌 AI capex 复利到 2027 + ASIC 替代慢于预期",
    "components": [
      {
        "claim": "AI capex 复利到 2027",
        "implied_amount": 30.0,             // 隐含金额（$）
        "implied_assumption": "hyperscaler capex 年增 30%+ 到 2027",
        "evidence": [ /* evidence items */ ],
        "falsifiable": "hyperscaler capex 增速跌破 15%"
      },
      {
        "claim": "ASIC 替代慢于预期",
        "implied_amount": 12.0,
        "implied_assumption": "ASIC 市占率 2027 仍 < 20%",
        "evidence": [ /* */ ],
        "falsifiable": "ASIC 市占率 2027 突破 30%"
      }
    ],
    "reconciliation": {                     // 叙事成分加总对账到价格
      "base_business_value": 165.0,         // DCF baseline
      "narrative_components_sum": 42.0,     // 30 + 12
      "total": 207.0,                       // 165 + 42
      "anchor": 214.86,
      "residual": 7.86,                     // 对账残差
      "residual_pct": 0.037                 // < 5% = 对账良好
    }
  },
  "dr_cost_credits": 15,                    // DR 调用花了多少
  "dr_model": "miromind_flagship"           // 用的模型
}
```

### 与 altitude channel 的关系

altitude channel 的 metrics 里，每层的 `narrative` 字段 = 该层 DR 输出（null = 未开 gate）。`dr_gated` = 是否触发了 DR。

---

## 9 个算法参数已确认（全推荐 + P8 迭代路径已标注）

### B2 分布账（6 个）

| # | 参数 | 定值 |
|---|---|---|
| P1 | 到期日选择 | **~90 天** |
| P2 | smile 平滑方法 | **cubic spline** |
| P3 | strike 范围 | **有 bid 的** |
| P4 | 25Δ 插值 | **BSM 反解** |
| P5 | skew 分位窗口 | **1y** |
| P6 | term structure 到期日 | **最近月 vs 次月** |

### B4 高度栈可测半（3 个）

| # | 参数 | 定值 | 迭代路径 |
|---|---|---|---|
| P7 | 回归窗口 | **60d** | — |
| P8 | 主题 ETF 映射 | **硬编码表（MVP）** | v1.1 扩充+手动覆盖 → v1.2 接 qveris REF.CLASSIFICATION.THEME → v2.0 自建主题篮子 |
| P9 | 归因方法 | **方差分解** | — |
