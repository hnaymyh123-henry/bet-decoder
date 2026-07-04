# SPEC · A 组 数据结构（v3 → v4）

> 状态：草案 · 2026-07-04 · 待用户 review
> 依赖：PRD v2.0 §1（原语）· §2（预期分解栈）· §3（期权分布）· §4（决策层）· §8（数据模型）
> 现有代码：`db.py` schema v3 · `decoder.py` decode_detail 组装

---

## A 组总览

| # | 项 | 现状 | spec 目标 |
|---|---|---|---|
| A1 | `decode_detail` JSON v4 形状 | v3 = mode/anchor_price/primary_lens/cross_lenses/anchor_mode/evidence/narrative_premium/r2_band | v4 = 加 panel[]/investigation/reconciliation/decision?，向后兼容 v3 |
| A2 | `panel_observations` 表 DDL | 不存在 | append-only 漂移观测表，支撑 §9.1 ⓪ 漂移脊椎 + §6 监控 |
| A3 | TradePlan 存储结构 | 不存在 | 存 decode_detail JSON 的 decision 字段（不独立建表）|
| A4 | BetCard v4 schema | v3 = bet_cards 表 + derived_from 血缘 | v4 = bet_cards 表加列（decode_detail v4 幂等迁移），卡外形不变 |
| A5 | `position_ledger` 表 DDL | 不存在 | 手动执行后的最小持仓事实账，支撑 Trade Plan 闭环 + KILL/监控诚实边界 |
| A6 | `panel_backfill_runs` 表 DDL | 不存在 | 漂移脊椎历史回填的运行记录、预算、覆盖率、失败原因 |

---

## 关键设计决策（需用户拍板）

| # | 决策 | 推荐 | 理由 |
|---|---|---|---|
| **D1** | v4 兼容策略：超集（v3 旧字段保留+新字段叠加）vs 重组（旧字段迁进新结构）| **超集** | 幂等迁移零风险；v3 卡读回来自动兼容；旧字段 `primary_lens`/`cross_lenses`/`anchor_mode` 被 panel[] 包裹但保留原值不丢 |
| **D2** | panel[] 信封：统一信封 vs 各 channel 各自 schema | **统一信封 + channel-specific metrics** | 信封统一（channel/horizon/headline/quality/status），metrics{} 内部按 channel 不同——现金流账 metrics 和分布账 metrics 字段不同，但信封一致 |
| **D3** | TradePlan 存哪：decode_detail JSON 的 decision 字段 vs 独立 trade_plans 表 | **decode_detail JSON** | TradePlan 是 Decode 的可选产物（§1.2 decision?），与 decode 同生命周期；独立表引入 JOIN + 一对一关系管理，过度；JSON 里查询用 SQLite json_extract 够 |
| **D4** | panel_observations 的 metrics_json：统一信封 vs 各 channel 各自 | **同 D2，统一信封 + channel-specific metrics** | 与 panel[] 的 metrics schema 完全一致——panel_observations 就是 panel 输出的时间序列快照 |
| **D5** | investigation trace 结构：结构化事件数组 vs 自由文本 | **结构化事件数组** | 每个 trace item = {step, action, channel?, gate?, cost_credits?, rationale}；可回放、可审计、agent_trace 语义一致 |

---

## A1 · `decode_detail` JSON v4 形状

### 设计原则

1. **超集兼容**：v3 字段全部保留（mode/anchor_price/primary_lens/cross_lenses/anchor_mode/evidence/narrative_premium/r2_band/fundamentals/lang），v4 新增字段叠加。
2. **panel[] 是新核心**：包裹 v3 的 primary_lens + cross_lenses + anchor_mode，但旧字段保留原值不丢（双写期：v4 panel[] 生成后，v3 旧字段从 panel[] 投影出来回填，保证旧消费者不崩）。
3. **decision? 可选**：只有跑过决策层才有；纯解码不生成 decision。

### v4 JSON Schema

```jsonc
{
  // ===== v3 保留字段（向后兼容）=====
  "mode": "traditional" | "anchor_primary" | "anchor_fallback" | "agentic_*",
  "anchor_price": 214.86,
  "anchor_type": "market",
  "narrative_premium": 0.42,
  "primary_lens": { /* v3 lens 结果，保留 */ },
  "cross_lenses": [ /* v3 交叉 lens，保留 */ ],
  "anchor_mode": { /* v3 anchor 结果，保留 */ },
  "lens_plan": { /* v3，保留 */ },
  "r2_band": { "p25": 180, "p50": 210, "p75": 250 },  // v3 蒙特卡洛 band
  "fundamentals": { /* v3 fund snapshot */ },
  "evidence": { /* v3 证据 section */ },
  "market_narrative": { /* v3 可选 */ },
  "lang": "zh",

  // ===== v4 新增字段 =====

  // panel[]：全量便宜 kernel 的结果，每条带 horizon 标签
  "panel": [
    {
      "channel": "cashflow",           // 见 §2.2 channel 枚举
      "horizon": "5y",                 // 见 C1 horizon 桶 spec（待对）
      "headline": "需要 ~38% 长期增速——历史极少数公司做到",
      "quality": "high" | "medium" | "low" | "empty",  // 数据质量
      "status": "ok" | "degraded" | "honest_empty" | "error",
      "metrics": {                     // channel-specific，【示意·B 组定稿】以下字段为示例，精确定义见 B 组各 channel spec
        "primary_lens": "dcf",
        "implied_cagr": 0.38,
        "implied_wacc": 0.062,
        "implied_terminal_margin": 0.35,
        "band": { "p25": 0.32, "p75": 0.45 },
        "narrative_premium": 0.42,
        "cross_validation": [ { "lens": "pe", "implied": 45.2, "divergence": 0.12 } ]
      },
      "cost_credits": 0                // 该 channel 花了多少（0 = 确定性 kernel）
    },
    {
      "channel": "distribution",       // keystone
      "horizon": "1-3m",
      "headline": "3 个月 68% 落在 $185–248；下行保险比近一年更贵",
      "quality": "high",
      "status": "ok",
      "metrics": {
        "implied_range": { "lower": 185, "upper": 248, "confidence": 0.68 },
        "rnd": { /* RND 密度数组或参数化表示 */ },
        "prob_of_target": { "target": 300, "prob": 0.08 },
        "skew": { "value": 0.05, "percentile_1y": 0.82 },
        "term_structure": { "near_iv": 0.45, "far_iv": 0.38, "slope": "downward" },
        "instruments": [ { "type": "stock", "symbol": "NVDA" } ]
      },
      "cost_credits": 3
    },
    {
      "channel": "revision",           // 变化账
      "horizon": "quarterly",
      "headline": "90 天 estimates 上修 12%，价格涨 25%——价格跑在预期前面",
      "quality": "medium",
      "status": "ok",
      "metrics": {
        "est_rev_90d": 0.12,
        "price_rev_90d": 0.25,
        "dispersion": 0.08,
        "price_ahead_of_estimates": true
      },
      "cost_credits": 2
    },
    {
      "channel": "altitude",           // 高度栈
      "horizon": "mixed",              // 三层不同 horizon
      "headline": "这价格 60% 是 AI-beta，40% 才是它自己的 bet",
      "quality": "medium",
      "status": "ok",
      "metrics": {
        "macro": { "beta": 1.15, "narrative": null, "dr_gated": false },
        "industry": { "theme_etf": "SMH", "beta": 1.3, "narrative": null, "dr_gated": false },
        "company": { "residual": 0.4, "narrative": null, "dr_gated": true }
      },
      "cost_credits": 5
    }
  ],

  // investigation：agent 注意力轨迹（结构化事件数组）
  "investigation": {
    "trace": [
      {
        "seq": 1,
        "action": "panel_sweep",       // 全量跑 panel
        "channel": null,               // null = 全 channel
        "gate": null,                  // 贵 gate 是否开
        "cost_credits": 10,
        "rationale": "全量 panel 常跑，收集所有 channel 基线"
      },
      {
        "seq": 2,
        "action": "deep_dive",         // 深挖某 channel
        "channel": "distribution",
        "gate": null,
        "cost_credits": 0,
        "rationale": "期权 skew 在 1y 82 分位，深挖分布账"
      },
      {
        "seq": 3,
        "action": "open_gate",         // 开叙事 DR gate
        "channel": "altitude",
        "gate": "narrative_dr",
        "cost_credits": 15,
        "rationale": "narrative_premium=0.42 过阈值，开 DR 查 ASIC 替代叙事"
      }
    ],
    "card_order": ["distribution", "cashflow", "altitude", "revision"],  // 卡面排序
    "total_cost_credits": 25
  },

  // reconciliation：跨 channel 检查（signal 层）+ 期限结构（descriptive）— C 组 review 修正，取代 buckets
  "reconciliation": {
    "checks": [                        // 只列触发的检查（未触发 = aligned）
      {
        "check": "price_vs_fundamentals",
        "channels": ["cashflow", "revision"],
        "verdict": "divergent",        // aligned | divergent | contradiction
        "headline": "价格涨 25% 但 estimates 只上修 12% = 跑在预期前，positioning-driven"
      }
    ],
    "term_structure": {                // descriptive 层，永不判矛盾（F4）
      "shape": "upward",
      "note": "短期期权平静 vs 长期 DCF 激进 = 期限结构，非矛盾",
      "is_contradiction": false
    },
    "conviction_input": {              // 数的是检查（见 C 组）
      "aligned_count": 3,
      "divergent_count": 1,
      "contradiction_count": 0,
      "total_checks": 4,
      "net_verdict": "divergent"       // 供 D 组 stance 消费（替代旧 long_bucket verdict）
    }
  },

  // series_link：时间轴引用（同 subject 的解码序列）
  "series_link": {
    "series_key": "NVDA|market",       // = bet_cards.series_key
    "prev_card_id": "abc123",          // 同 series 上一张卡（null = 第一张）
    "prev_trade_date": "2026-07-03",
    "drift_since_prev": null           // 填充时机：监控/重解码时算（见 A2 panel_observations）
  },

  // portfolio only：父组合卡稳定引用各持仓腿的单票卡
  "constituent_card_ids": ["nvda_card_id", "msft_card_id"],

  // lineage：分叉轴引用（已在 v3 列上，这里冗余存一份方便 JSON 消费）
  "lineage": {
    "derived_from": null,              // = bet_cards.derived_from
    "derivation_kind": null,           // = bet_cards.derivation_kind
    "derivation": null                 // = bet_cards.derivation_json
  },

  // decision?：可选，跑过决策层才有
  "decision": null                     // 或 TradePlan 结构（见 A3）
}
```

### v3 → v4 迁移策略

```python
# db.py _migrate_decode_detail_v4（幂等）
def _migrate_decode_detail_v4(dd: dict) -> dict:
    """v3 decode_detail → v4：旧字段保留，新字段补默认值。幂等。"""
    if "panel" not in dd:
        # 从 v3 旧字段投影出 panel[0]（现金流账）
        dd["panel"] = [_project_v3_to_panel_cashflow(dd)]
    if "investigation" not in dd:
        dd["investigation"] = {"trace": [], "card_order": [], "total_cost_credits": 0}
    if "reconciliation" not in dd:
        dd["reconciliation"] = {
            "checks": [],
            "term_structure": {"shape": "unknown", "note": "v3 card: no v4 term-structure reconciliation", "is_contradiction": False},
            "conviction_input": {
                "aligned_count": 0,
                "divergent_count": 0,
                "contradiction_count": 0,
                "total_checks": 0,
                "net_verdict": "unknown"
            }
        }
    if "series_link" not in dd:
        dd["series_link"] = {"series_key": None, "prev_card_id": None, "prev_trade_date": None, "drift_since_prev": None}
    if "lineage" not in dd:
        dd["lineage"] = {"derived_from": None, "derivation_kind": None, "derivation": None}
    if "decision" not in dd:
        dd["decision"] = None
    return dd
```

---

## A2 · `panel_observations` 表 DDL

### 设计原则

1. **append-only**：观测点是不可变的历史记录，只增不改不删。
2. **观测 ≠ 解码**：panel_observations 存"数据点"（某 subject 某 as_of_date 某 channel 的 metrics），不存"调查"（那是 decode_detail）。一个 subject 每天每 channel 一行。
3. **metrics_json 与 panel[].metrics schema 一致**（D4）：panel_observations 就是 panel 输出的时间序列快照。
4. **支撑漂移脊椎 + 监控**：漂移脊椎 = 对历史逐时点重跑 panel → 每个时点落一行 → 时间序列查询；监控 = 每日扫 panel → 落一行 → 与历史比。

### DDL

```sql
CREATE TABLE IF NOT EXISTS panel_observations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    subject         TEXT    NOT NULL,          -- ticker 或 ETF symbol
    as_of_date      TEXT    NOT NULL,          -- YYYY-MM-DD（按天，§6 cadence）
    channel         TEXT    NOT NULL,          -- cashflow | distribution | revision | altitude
    horizon         TEXT,                      -- 5y | 1-3m | quarterly | mixed
    metrics_json    TEXT,                      -- 与 decode_detail.panel[].metrics 同 schema
    quality         TEXT,                      -- high | medium | low | empty
    status          TEXT,                      -- ok | degraded | honest_empty | error
    cost_credits    INTEGER DEFAULT 0,
    created_at      TEXT    NOT NULL,
    -- append-only：同 subject + as_of_date + channel 允许多行（重跑会追加），
    -- 但查询时取最新一行（created_at DESC）。如需严格去重可加 UNIQUE。
    -- 注意：subject 是软引用（bet_cards.subject 不是 PK，不加 FOREIGN KEY 约束），
    -- 完整性由应用层保证（subject 必须是有效 ticker/ETF symbol）
);
CREATE INDEX IF NOT EXISTS idx_panel_obs_subject_date
    ON panel_observations(subject, as_of_date DESC);
CREATE INDEX IF NOT EXISTS idx_panel_obs_subject_channel_date
    ON panel_observations(subject, channel, as_of_date DESC);
```

### 查询模式

```sql
-- 漂移脊椎：某 subject 某 channel 的时间序列
SELECT as_of_date, metrics_json->>'$.implied_cagr' as cagr
FROM panel_observations
WHERE subject = 'NVDA' AND channel = 'cashflow'
ORDER BY as_of_date;

-- 监控：最新观测 vs 历史分位
SELECT as_of_date, metrics_json->>'$.skew.percentile_1y' as skew_pct
FROM panel_observations
WHERE subject = 'NVDA' AND channel = 'distribution'
ORDER BY as_of_date DESC LIMIT 30;
```

---

## A3 · TradePlan 存储结构

### 设计原则

1. **存 decode_detail JSON 的 decision 字段**（D3）：TradePlan 是 Decode 的可选产物，与 decode 同生命周期。
2. **不可变**：TradePlan 一旦生成 = 冻结快照（继承 BetCard 不可变性）。修改 = 新建衍生卡。
3. **查询用 json_extract**：`SELECT decode_detail_json->>'$.decision.stance' FROM bet_cards WHERE ...`

### TradePlan JSON Schema（存 decode_detail.decision）

```jsonc
{
  "stance": "avoid",                   // accumulate | strong_buy | hold | trim | avoid | short
  "strategy_archetype": "wait",          // value_accumulate | maintain | risk_off | wait | hedge_short
  "conviction": "low",                 // high | medium | low
  "edge": {
    "view_quantile": 0.92,             // 你的 view 落在市场隐含分布的 92 分位
    "supporting_channels": ["cashflow"],    // 哪些 channel 支持你
    "opposing_channels": ["distribution", "revision"]  // 哪些反对
  },
  "entry": {
    "price": 214.86,                   // decode 现价（不加滑点，§4.6 LOCKED）
    "condition": "immediate",          // immediate | limit@X | staged
    "stages": null                     // 分批时 [{weight, price, condition}]
  },
  "size": {
    "kelly_fraction": 0.25,            // ¼ Kelly（§4.4 LOCKED）
    "raw_kelly": 0.08,                 // 原始 Kelly f*
    "conviction_multiplier": 0.3,      // low ×0.3（§4.4 LOCKED）
    "target_weight": 0.0,             // 最终目标权重（edge<2% → 0，§4.5 LOCKED）
    "cap": 0.20,                       // 单仓上限 20%（§4.4 LOCKED）
    "cash_floor": 0.10                 // 现金下限 10%（§4.4 LOCKED）
  },
  "stop": {
    "type": "price",                   // price | fundamental
    "level": 180.0,                    // stop 价
    "implied_prob": 0.12               // 价格型：期权 RND 隐含触发概率
  },
  "kill": {
    "type": "fundamental",             // price | fundamental
    "line": "营收增速连续 2 季跌破 11%",
    "tracking": "consensus_revision",  // 基本面型走 consensus 修正跟踪
    "implied_prob": null               // 基本面型：期权定价不了，null
  },
  "exit": {
    "condition": "thesis_realized_or_edge_gone",
    "rationale": "论点兑现或 edge 消失"
  },
  "rationale": "市场隐含 38% 增速远超可辩护 25%，无安全边际",
  "self_falsification": "若营收增速连 2 季破 11%→论点破（但已 avoid，falsify = 继续不买）"
}
```

---

## A5 · `position_ledger` 表 DDL

### 为什么必须有

Trade Plan 只是建议；监控和 KILL 必须知道用户**实际执行了什么**，否则系统会对已经清仓的票继续推 KILL，或把导入时的静态快照误当成现有持仓。这违反 PRD 的"诚实"边界。

一期不接券商、不自动下单，但必须有一个最小手动 ledger：

1. 用户点"我已执行/我已调整"后写入一条事实记录。
2. 监控默认只盯 `position_ledger.status = open` 的持仓，以及用户显式保存为 watchlist 的解码。
3. 仅导入快照但未确认执行的持仓，监控 UI 必须标 `"stale_snapshot"`，不能冒充实时持仓。

### DDL

```sql
CREATE TABLE IF NOT EXISTS position_ledger (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    subject         TEXT    NOT NULL,
    source          TEXT    NOT NULL,      -- manual_import | plan_confirmed | manual_adjustment | close_confirmed
    plan_card_id    TEXT,                  -- 来源 Trade Plan，可空
    side            TEXT    NOT NULL,      -- long | short | flat
    quantity        REAL,                  -- 可空；用户只填权重时不用 quantity
    weight_pct      REAL,                  -- 组合权重，0..100
    avg_price       REAL,
    executed_at     TEXT    NOT NULL,      -- ISO timestamp，用户确认的执行时间
    status          TEXT    NOT NULL,      -- open | closed | stale_snapshot
    note            TEXT,
    created_at      TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_position_ledger_subject_status
    ON position_ledger(subject, status, executed_at DESC);
CREATE INDEX IF NOT EXISTS idx_position_ledger_plan
    ON position_ledger(plan_card_id);
```

### has_position 来源

`has_position` 不再由前端布尔值拍脑袋传入。决策层读取：

1. 若 `position_ledger` 中 subject 最新记录 `status=open` 且 `weight_pct > 0`，`has_position=true`。
2. 若无 ledger，但请求显式传 `preview_has_position`，仅用于预览，响应必须带 `position_source="user_preview"`。
3. 若只有导入快照，`has_position=true` 但 `position_freshness="stale_snapshot"`，监控不能自动推送 KILL，只能在卡内展示"需确认持仓仍有效"。

---

## A4 · BetCard v4 schema

### 设计原则

1. **bet_cards 表加列**：decode_detail v4 幂等迁移（`_migrate_decode_detail_v4` 在 `card_from_row` 时跑）。
2. **卡外形不变**：BetCard dataclass 字段不变，decode_detail 仍是 runtime 属性。
3. **panel_observations 不挂在卡上**：观测点是独立表，卡通过 subject 关联（不是 card_id）。

### bet_cards 表 v4 变更

```sql
-- v3 → v4：仅 decode_detail_json 内容升级（列不变，JSON 形状变）
-- SCHEMA_VERSION = "4"
-- 迁移：card_from_row 时跑 _migrate_decode_detail_v4（惰性，不批量回填）
```

**零列变更**——v4 的所有新数据都在 `decode_detail_json` 里。`panel_observations` 是新表但与 `bet_cards` 平行（通过 subject 软关联，不通过 card_id）。

### BetCard dataclass（不变）

```python
@dataclass
class BetCard:
    subject: str
    source_type: str
    card_kind: str = SINGLE
    source_ref: str | None = None
    bet: float | None = None
    run_id: int | None = None
    card_id: str | None = None
    series_key: str | None = None
    trade_date: str | None = None
    created_at: str | None = None
    holdings: list[Holding] = field(default_factory=list)
    theme_exposures: list[ThemeExposure] = field(default_factory=list)
    derived_from: str | None = None
    derivation_kind: str | None = None
    derivation: dict | None = None
    # decode_detail 仍是 runtime 属性（v4 形状）
```

---

## A6 · `panel_backfill_runs` 表 DDL

### 为什么需要

S0 漂移脊椎不是一次 decode，而是对一批 subject、一个历史窗口、若干 cheap channel 的批处理。必须记录覆盖率、credits、失败原因和哪些日期没有数据；否则前端无法区分"历史平静"和"根本没回填"。

### DDL

```sql
CREATE TABLE IF NOT EXISTS panel_backfill_runs (
    run_id              TEXT PRIMARY KEY,
    subjects_json       TEXT    NOT NULL,
    channels_json       TEXT    NOT NULL,  -- cashflow/distribution/revision/altitude subset
    start_date          TEXT    NOT NULL,
    end_date            TEXT    NOT NULL,
    status              TEXT    NOT NULL,  -- planned | running | completed | partial | failed | cancelled
    credit_budget       INTEGER NOT NULL,
    credits_spent       INTEGER DEFAULT 0,
    observations_written INTEGER DEFAULT 0,
    skipped_json        TEXT,              -- [{subject,date,channel,reason}]
    error_json          TEXT,
    created_at          TEXT    NOT NULL,
    completed_at        TEXT
);
```

### S0 默认回填策略 `[LOCKED MVP]`

| channel | 历史窗口 | cadence | 数据源 | 失败时 |
|---|---:|---|---|---|
| price | 180d | daily | yfinance/cache | chart price layer empty |
| cashflow implied_growth | 180d | daily | 历史价格 + 当前 fundamentals 固定反解 | 标 `price_driven_only=true` |
| distribution_band | 90d | daily/available expiries | historical options if provider supports; else current only | layer empty + reason |
| consensus | 从启用日开始 | daily snapshot | provider 当前 consensus | 不回填假历史 |
| events | 180d | event dates | provider calendar/news | layer empty |

预算：默认 `credit_budget=80`；distribution 历史期权优先，consensus 历史 point-in-time 不在 MVP 回填承诺内。

---

## 5 个设计决策已确认（全推荐）

| # | 决策 | 定值 |
|---|---|---|
| D1 | v4 兼容策略 | **超集**（v3 旧字段保留+新字段叠加，幂等迁移零风险）|
| D2 | panel[] 信封 | **统一信封 + channel-specific metrics**（信封一致，metrics 内部按 channel 不同）|
| D3 | TradePlan 存哪 | **decode_detail JSON 的 decision 字段**（与 decode 同生命周期，不独立建表）|
| D4 | panel_observations metrics | **同 panel[].metrics schema**（观测点 = panel 输出的时间序列快照）|
| D5 | investigation trace | **结构化事件数组**（可回放可审计，每个 trace item = {seq, action, channel, gate, cost, rationale}）|
| D6 | 执行后持仓事实 | **最小 `position_ledger`**（手动确认执行/调整/清仓，不接券商也不伪装实时）|

> Review 修正（2026-07-04）：
> 1. A2 panel_observations 去掉 FOREIGN KEY（bet_cards.subject 非 PK，软引用由应用层保证）
> 2. A1 panel[].metrics 标注"示意·B 组定稿"
> 3. A1 reconciliation 迁移默认值从旧 `buckets` 改为 `checks[] + term_structure + conviction_input.net_verdict`
