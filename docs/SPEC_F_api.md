# SPEC · F 组 API

> 状态：草案 · 2026-07-04 · 待用户 review
> 依赖：SPEC A-E 组
> PRD 依据：§8.2 公共接口契约 · 现有 `api.py` + `API_CONTRACT.md`

---

## F 组总览

| # | 项 | 待 spec |
|---|---|---|
| F1 | `decode_bet_agentic` 请求/响应 | 解码 API 完整字段 |
| F2 | `build_trade_plan` 请求/响应 | 决策层 API |
| F3 | 监控调度 API | 扫描/取 feed/订阅 push |
| F4 | 图表数据 API | 增强股价图 6 层时间序列 |
| F5 | position ledger API | 手动执行/调整/清仓事实账 |
| F6 | drift backfill API | S0 漂移脊椎历史回填 |

---

## F1 · `decode_bet_agentic` 请求/响应

### 请求

```http
POST /api/decode
Content-Type: application/json

{
  "source_type": "market" | "portfolio",      // MVP 只支持这两种（§5.2）
  "source_input": {
    // market:
    "ticker": "NVDA",
    // portfolio:
    "holdings": [{"ticker": "NVDA", "weight_pct": 0.31}, ...]
  },
  "lang": "zh",                                // 默认 zh
  "agentic": true                              // 默认 true；false=确定性回退
}
```

### 响应（现有 api.py 兼容：JSON 启动 job + activity SSE 回放）

`POST /api/decode` 不直接返回 SSE。它同步返回 `{job_id, card}`，前端随后订阅 `GET /api/stream/activity/{job_id}` 回放/追踪活动流。这与当前 `api.py` 的实现一致，避免把 decode endpoint 写成两种生命周期。

```jsonc
{
  "job_id": "job_001",
  "card": {
    "card_id": "abc123",
    "subject": "NVDA",
    "source_type": "market",
    "card_kind": "single",
    "bet": 215.10,
    "decode_detail": { /* v4 decode_detail，可能是确定性 fallback */ }
  }
}
```

**SSE 流**（ActivityEvent，复用 v1.0 协议）：

```
event: activity
data: {"job_id":"job_001","seq":1,"t_offset_ms":0,"source":{"kind":"decode","subject":"NVDA"},"phase":"panel_sweep","kind":"computation","text":"全量 panel 常跑...","payload":null}

event: activity
data: {"job_id":"job_001","seq":2,"t_offset_ms":1200,"phase":"deep_dive","kind":"decision","text":"期权 skew 在 1y 82 分位，深挖分布账","payload":{"channel":"distribution"}}

event: activity
data: {"job_id":"job_001","seq":3,"t_offset_ms":3500,"phase":"reconcile","kind":"relation","text":"跨框架对账：长期桶 divergence","payload":{"verdict":"divergence"}}

event: done
data: {"job_id":"job_001","card_id":"abc123","terminal":"done"}
```

**完整卡 JSON**（`GET /api/cards/{card_id}`）：

```jsonc
{
  "card_id": "abc123",
  "subject": "NVDA",
  "source_type": "market",
  "card_kind": "single",
  "bet": 215.10,
  "trade_date": "2026-07-04",
  "created_at": "2026-07-04T16:00:00Z",
  "series_key": "NVDA|market",
  "derived_from": null,
  "derivation_kind": null,
  "decode_detail": {
    // 完整 v4 decode_detail（SPEC A1）
    "mode": "agentic_traditional",
    "panel": [ /* 4 channel（SPEC B）*/ ],
    "investigation": { /* trace（SPEC A1）*/ },
    "reconciliation": { /* checks[] + term_structure + conviction_input（SPEC C）*/ },
    "series_link": { /* */ },
    "lineage": { /* */ },
    "decision": null                    // 纯解码不生成，需调 build_trade_plan
  }
}
```

### 错误响应

```jsonc
{
  "error_code": "DECODE_FAILED",
  "message": "panel sweep 失败：qveris API 超时",
  "job_id": "job_001",
  "partial_card_id": null              // 气密回退可能产出部分卡
}
```

### 气密回退

`agentic=true` 但 provider 不支持工具调用 / 异常 / 离线 → 回退确定性 `decode_bet`（v1.0 路径），`mode` 标 `"fallback_deterministic"` 或沿用 v1.0 deterministic mode 并在 `decode_detail.agentic.fallback_reason` 写明原因。

---

## F2 · `build_trade_plan` 请求/响应

### 请求

```http
POST /api/cards/{card_id}/plan
Content-Type: application/json

{
  "preview_has_position": null,                // 可选，仅预览；真实 has_position 优先来自 position_ledger
  "target_price": null,                        // 可选：用户目标价（影响 edge 分位计算）
  "lang": "zh"
}
```

### 响应

```jsonc
{
  "card_id": "abc123",
  "plan": {
    // 完整 TradePlan（SPEC A3 + SPEC D）
    "stance": "avoid",
    "strategy_archetype": "wait",
    "conviction": "low",
    "edge": {
      "edge_prob": 0.015,
      "edge_quantile": null,
      "view_quantile": 0.92,
      "supporting_channels": ["cashflow"],
      "opposing_channels": ["distribution", "revision"]
    },
    "entry": {"method": "none"},
    "size": {
      "raw_kelly": 0.08,
      "fractional_kelly": 0.02,
      "conviction_multiplier": 0.3,
      "target_weight": 0.0,            // edge<2% → 0
      "cap": 0.20,
      "cash_floor": 0.10
    },
    "stop": null,                       // wait archetype 无 stop
    "kill": {
      "type": "fundamental",
      "line": "营收增速连 2 季破 11%",
      "tracking": "consensus_revision",
      "implied_prob": null,
      "status": "monitoring"
    },
    "exit": {"condition": "edge_appears"},
    "rationale": "市场隐含 38% 增速远超可辩护 25%，无安全边际",
    "self_falsification": "若营收增速连 2 季破 11%→论点破"
  },
  "cost_credits": 0                     // 决策层纯计算，不花 credits（除非开 DR）
}
```

响应必须包含持仓新鲜度：

```jsonc
{
  "position_context": {
    "has_position": true,
    "position_source": "position_ledger",       // position_ledger | user_preview | stale_snapshot | none
    "position_freshness": "fresh"               // fresh | stale_snapshot | unknown
  }
}
```

### 诚实约束闸门

若 `conviction=low` 且 `edge<2%` → `plan.stance="avoid"` + `plan.size.target_weight=0` + `plan.rationale` 含"不值得下注"。

若 `self_falsification` 无法生成 → 404 + `{"error_code":"NO_KILL_LINE","message":"无法生成认错条件，拒绝输出 Trade Plan"}`。

---

## F3 · 监控调度 API

### 设计原则

PRD §6：监控 = 调度发起的主动扫描。MVP events-first（财报/评级/大跳空触发）。API 需要支持：触发扫描、取 feed、订阅 push。

### ① 触发扫描

```http
POST /api/monitor/scan
Content-Type: application/json

{
  "trigger": "earnings" | "rating" | "gap" | "manual",
  "subjects": ["NVDA", "COST"],                // 扫哪些标的（空=ledger open positions + watchlist）
  "as_of_date": "2026-07-04"
}
```

响应（异步 job）：

```jsonc
{
  "job_id": "mon_001",
  "status": "running",
  "subjects_scanning": 2
}
```

扫描结果通过 SSE 流式返回 + 落 panel_observations + 生成 feed items。默认 scope 不包括 `stale_snapshot`，除非请求显式 `include_stale_snapshots=true`，且响应/Feed 必须标明不是确认持仓。

### 监控 credits 闸门

MVP 默认不做"每日全量全 channel 深扫"。调度器按成本分层：

| 层 | 每日默认 | 单标的 credits | 说明 |
|---|---|---:|---|
| price/event heartbeat | 全 open positions + watchlist | 0 | 价格、事件、gap，免费/本地缓存 |
| cheap panel | 仅事件触发或用户手动 scan | 3-10 | distribution/revision/altitude 可测半 |
| narrative DR | 仅阈值触发 + 用户确认 | 15+ | 贵 gate，不自动批量跑 |

预算规则：

- 默认每日预算 `monitor_daily_credit_budget=80`，低于 qveris 免费 100/日，预留手动操作余量。
- 扫描前先估算，超过预算则按 severity 排序截断，并在响应写 `skipped_due_to_budget`。
- feed 必须说明"因预算未扫描"和"未发现变化"不同，不能把未扫描报成平静。

### ② 取变化 feed

```http
GET /api/monitor/feed?subject=NVDA&limit=20&severity=high,medium
```

响应：

```jsonc
{
  "items": [
    {
      // 完整 feed item（SPEC E4）
      "item_id": "feed_001",
      "timestamp": "2026-07-04T16:00:00Z",
      "subject": "NVDA",
      "trigger": "earnings",
      "severity": "high",
      "headline": "财报后隐含增速 34%→41%，逼近你的认错线",
      "changes": [ /* */ ],
      "kill_status": { /* */ },
      "action_hint": "查看 Trade Plan",
      "card_id": "abc123"
    }
  ],
  "has_more": false,
  "oldest_timestamp": "2026-06-15T10:00:00Z"
}
```

### ③ 订阅 push（SSE）

```http
GET /api/monitor/stream
Accept: text/event-stream
```

```
event: feed_item
data: {"item_id":"feed_002","severity":"high","subject":"NVDA","headline":"KILL 线触发：营收增速跌破 11%"}

event: heartbeat
data: {"timestamp":"2026-07-04T17:00:00Z"}
```

- severity=high 的 feed item 通过此流 push
- severity=medium/low 只进 feed 累积，不 push
- heartbeat 每 60s 一次，保活

### ④ KILL 状态查询

```http
GET /api/monitor/kill-status?subject=NVDA
```

```jsonc
{
  "subject": "NVDA",
  "kills": [
    {
      "type": "fundamental",
      "line": "营收增速连 2 季破 11%",
      "status": "approached",
      "current_value": 0.12,
      "threshold": 0.11,
      "margin": 0.01,
      "last_checked": "2026-07-04T16:00:00Z"
    }
  ]
}
```

## F4 · 图表数据 API（增强股价图）

增强股价图需要 6 层时间序列，不能只靠 card 的单时点快照。新增：

```http
GET /api/chart/{subject}?as_of=2026-07-04&window=180d&card_id=abc123
```

响应：

```jsonc
{
  "subject": "NVDA",
  "as_of": "2026-07-04",
  "window": "180d",
  "quality": "degraded",
  "layers": {
    "price": [{"date":"2026-07-01","open":210,"high":218,"low":208,"close":215,"volume":123456}],
    "distribution_band": [{"date":"2026-07-01","lower":185,"upper":248,"confidence":0.68,"source":"panel_observations"}],
    "implied_growth": [{"date":"2026-07-01","value":0.38,"source":"panel_observations"}],
    "consensus": [{"date":"2026-07-01","value":190,"point_in_time":false,"status":"honest_empty"}],
    "events": [{"date":"2026-06-20","type":"earnings","headline":"财报"}],
    "kill_lines": [{"type":"price","level":180,"status":"monitoring","source":"decision"}]
  },
  "empty_layers": [
    {"layer":"consensus","reason":"point-in-time consensus cold start"}
  ]
}
```

诚实空态：

- 任一 layer 缺数据时返回空数组 + `empty_layers[]`，前端显示"暂无该层历史"，不得画假线。
- `consensus.point_in_time=false` 时只可做当前截面展示，不可参与 90d drift/positioning check。
- `card_id` 可选；传入时用于读取该卡的 KILL/decision，不传则只返回 subject 级观测。

## F5 · position ledger API

```http
POST /api/positions/events
Content-Type: application/json

{
  "subject": "NVDA",
  "source": "plan_confirmed",
  "plan_card_id": "abc123",
  "side": "long",
  "weight_pct": 12.5,
  "avg_price": 215.10,
  "executed_at": "2026-07-04T16:30:00Z",
  "note": "手动在券商执行"
}
```

响应：

```jsonc
{
  "position_event_id": 101,
  "subject": "NVDA",
  "status": "open",
  "position_source": "position_ledger"
}
```

清仓：

```http
POST /api/positions/events
{
  "subject": "NVDA",
  "source": "close_confirmed",
  "side": "flat",
  "weight_pct": 0,
  "executed_at": "2026-07-10T10:00:00Z"
}
```

查询：

```http
GET /api/positions?status=open
```

---

## F6 · drift backfill API

```http
POST /api/panel/backfill
Content-Type: application/json

{
  "subjects": ["NVDA", "COST"],
  "channels": ["price", "cashflow", "distribution"],
  "start_date": "2026-01-01",
  "end_date": "2026-07-04",
  "credit_budget": 80
}
```

响应：

```jsonc
{
  "run_id": "bf_001",
  "status": "running",
  "estimated_credits": 64,
  "subjects": ["NVDA", "COST"],
  "channels": ["price", "cashflow", "distribution"]
}
```

查询：

```http
GET /api/panel/backfill/{run_id}
```

```jsonc
{
  "run_id": "bf_001",
  "status": "partial",
  "credits_spent": 52,
  "observations_written": 360,
  "skipped": [
    {"subject":"COST","date":"2026-02-03","channel":"distribution","reason":"no_historical_options"}
  ]
}
```

回填写入 `panel_observations`，运行元数据写 `panel_backfill_runs`。若超预算，必须先停在预算内并返回 `status="partial"`，不得静默跳过后报 completed。

---

## API 端点汇总

| 方法 | 路径 | 用途 | 来源 |
|---|---|---|---|
| POST | `/api/decode` | 解码（agentic PRIMARY）| F1（v1.0 升级）|
| GET | `/api/cards` | 列卡 | v1.0 |
| GET | `/api/cards/{id}` | 取卡（含 decode_detail v4）| v1.0 |
| DELETE | `/api/cards/{id}` | 删卡 | v1.0 |
| POST | `/api/cards/{id}/ask` | 问 agent（E3）| v1.0 agentic 层 |
| POST | `/api/cards/{id}/revise` | what-if（返回 diff，不落库）| v1.0 agentic 层 |
| POST | `/api/cards/{id}/plan` | 生成 Trade Plan | **F2 新增** |
| POST | `/api/synthesize` | 跨卡综合（组合内）| v1.0（收敛为组合内）|
| POST | `/api/monitor/scan` | 触发监控扫描 | **F3 新增** |
| GET | `/api/monitor/feed` | 取变化 feed | **F3 新增** |
| GET | `/api/monitor/stream` | 订阅 push（SSE）| **F3 新增** |
| GET | `/api/monitor/kill-status` | KILL 状态 | **F3 新增** |
| GET | `/api/chart/{subject}` | 增强股价图 6 层时间序列 | **F4 新增** |
| POST | `/api/positions/events` | 记录手动执行/调整/清仓 | **F5 新增** |
| GET | `/api/positions` | 查询 open/closed 持仓事实账 | **F5 新增** |
| POST | `/api/panel/backfill` | 触发漂移脊椎历史回填 | **F6 新增** |
| GET | `/api/panel/backfill/{run_id}` | 查询回填运行状态 | **F6 新增** |
| GET | `/api/stream/activity/{job_id}` | 解码/问答/监控 activity SSE | v1.0 |

---

## 3 个算法参数已确认（全推荐）

| # | 参数 | 定值 | 意义 |
|---|---|---|---|
| T1 | 监控扫描并发 | **并发 3** | 串行太慢，全并发怕 qveris 限流 |
| T2 | feed 保留时长 | **30d** | 够看一个月变化，不过载 |
| T3 | heartbeat 间隔 | **60s** | 平衡保活与流量 |

---

## 全部 6 组 spec 已完成（A-F）

| 组 | 内容 | 文件 | 状态 |
|---|---|---|---|
| A | 数据结构 | `docs/SPEC_A_data_structures.md` | ✅ 定稿 |
| B | 各 channel 输出 | `docs/SPEC_B_channels.md` | ✅ 定稿 |
| C | 跨框架对账 | `docs/SPEC_C_reconciliation.md` | ✅ 定稿 |
| D | 决策层 | `docs/SPEC_D_decision.md` | ✅ 定稿 |
| E | 呈现层（增强股价图）| `docs/SPEC_E_presentation.md` | ✅ 定稿 |
| F | API | `docs/SPEC_F_api.md` | ✅ 定稿 |

**参数总计**：5（A 组设计决策）+ 9（B 组算法）+ 4（C 组）+ 4（D 组）+ 3（E 组）+ 3（F 组）= **28 个参数全部定值**。
