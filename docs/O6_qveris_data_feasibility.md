# O6 技术地基可行性报告 · qveris 期权链 + consensus 数据

> 探查日期：2026-07-04
> 目的：验证 PlainSight "点→分布"方向的数据地基是否可行
> 结论：**期权链 + consensus + 事件源三层数据均可行，但 point-in-time consensus 是最大风险点**

---

## 1. qveris.ai 是什么

qveris.ai **不是传统数据源**，而是 **AI agent 的能力路由网络**（capability routing network）——把 10,000+ 个金融数据能力（来自多个 provider）统一到一个 discover / inspect / call 协议下。

| 维度 | 规格 |
|---|---|
| **覆盖** | 6 个金融域，10,000+ capability（Investment Research 36 caps · Systematic Trading 38 caps · Macro & Fixed Income 25 caps 等）|
| **接入方式** | REST API（`/api/v1/search` discover → `/api/v1/tools/by-ids` inspect → `/api/v1/tools/execute` call）/ CLI / Python SDK / MCP Server |
| **认证** | API key（`qv_...`），Dashboard 创建 |
| **计费** | credits 制：discover/inspect 免费，call 花 1-100 credits/次；signup 送 1000 + 每日 100 免费 |
| **协议** | Discover（自然语言搜能力）→ Inspect（查 schema/cost/latency）→ Call（沙箱执行，结构化 JSON 返回）|

**关键洞察**：qveris 的价值不是数据本身，是"统一接口 + 多 provider 路由 + 质量信号（latency/success_rate/cost）+ 审计 trail"。PlainSight 也可以跳过 qveris 直接接 provider API。

---

## 2. 三层数据可行性

### 2.1 期权链（keystone channel 地基）✅ 可行

**PRD §3 需要的**：各行权 IV / 价格 → 算 RND / skew / term structure

**qveris Provider Hub 覆盖**：

| Provider | 期权数据 | 说明 |
|---|---|---|
| **EODHD** | ✅ 明确提到 "US stock options" | 历史期权链 + 实时价，150,000+ tickers |
| **Finnhub** | ✅ 有期权链 API | 免费 tier 覆盖基本期权链 |
| **Alpha Vantage** | ✅ 有 `HISTORICAL_OPTIONS` + realtime options | 覆盖美股期权链 |
| **Yahoo Finance** | ✅ yfinance `ticker.option_chain()` | 免费，覆盖美股主要 ticker |
| **Financial Modeling Prep** | ✅ 有期权链 | 覆盖美股 |

**结论**：期权链数据多层 provider 覆盖，**可行**。最小成本路径 = Yahoo Finance（免费）或 Finnhub（免费 tier）。通过 qveris 路由 = 1-5 credits/call。

**字段形态需 inspect 确认**：原始期权链（各行权 bid/ask/IV）vs 已算指标（ATM IV / skew / term structure）。PRD §3.2 需要原始期权链（各行权 IV）来算 RND（Breeden-Litzenberger 二阶差分），所以**必须确认 provider 返回的是 per-strike IV 而非聚合指标**。

### 2.2 consensus estimate 修正 ⚠️ 可行但 point-in-time 是风险

**PRD §2.2 变化账 + §9.1 ⓪ 漂移脊椎需要的**：consensus estimate 修正 + 分歧度 + **point-in-time**（估计的"当时值"而非回看值）

**qveris Provider Hub 覆盖**：

| Provider | consensus 数据 | point-in-time? |
|---|---|---|
| **Finnhub** | ✅ analyst estimates / consensus | ❌ 通常是当前值，非历史 point-in-time |
| **Alpha Vantage** | ✅ analyst estimates | ❌ 同上 |
| **Financial Modeling Prep** | ✅ analyst estimates | ⚠️ 部分历史，但非严格 point-in-time |
| **Yahoo Finance** | ✅ analyst estimates（basic）| ❌ 当前值 |

**关键风险**：**point-in-time consensus**（"当时市场的估计值"而非回看修正后的值）是漂移脊椎（§9.1 ⓪）的核心依赖——要画"隐含假设时间序列叠在价格上"，必须用每个时点的"当时估计值"。大部分免费/便宜 provider 提供**当前** consensus，不是**历史 point-in-time**。

**point-in-time consensus 通常需要付费数据源**：FactSet / Refinitiv / Visible Alpha / Compustat。qveris Provider Hub 里没看到这些顶级 provider（看到的是 Gildata 恒生聚源，可能有 A 股 point-in-time）。

**降级方案**（若 point-in-time 不可得）：
- **方案 A**：从现在开始每日记录 consensus 快照（自建 point-in-time 数据库），历史部分留空——漂移脊椎从"有数据的那天"开始画，不回填历史。
- **方案 B**：用价格本身 + 期权隐含分布的时间序列替代 consensus 修正——价格是天然的 point-in-time，期权分布也是。漂移脊椎不一定要 consensus，可以先用价格 + 期权分布画，consensus 修正作为后续补充。
- **方案 C**：探 qveris 是否有 point-in-time 能力（需 API key 实际 discover "point-in-time consensus estimates"）。

### 2.3 事件源（§6 监控触发）✅ 可行

**PRD §6 需要的**：财报 / 评级 / 大跳空

| Provider | 事件数据 |
|---|---|
| **Finnhub** | ✅ earnings calendar / company news / rating changes |
| **Financial Modeling Prep** | ✅ earnings calendar / stock rating |
| **Alpha Vantage** | ✅ earnings |
| **Yahoo Finance** | ✅ earnings calendar（basic）|

**结论**：事件源多层 provider 覆盖，**可行**。earnings calendar 是最标准的金融数据，免费 provider 就够。

---

## 3. qveris vs 直接接 provider

| 维度 | 通过 qveris 路由 | 直接接 provider API |
|---|---|---|
| **接入成本** | 一个 API key，统一协议 | 每个 provider 单独注册 + 适配 |
| **多 provider fallback** | ✅ 内置（一个 capability 多 provider）| 需自建 |
| **质量信号** | ✅ latency / success_rate / cost 可见 | 需自己测 |
| **审计 trail** | ✅ execution_id / search_id 全留 | 需自建 |
| **成本** | credits（1-100/call）+ 免费 100/日 | provider 自己的计费（很多免费 tier）|
| **依赖** | 多一层 qveris 依赖 | 直接 |
| **期权链字段** | 需 inspect 确认 per-strike IV | 直接看 provider 文档 |

**建议**：MVP **先用 qveris 路由**（统一接口 + 免费 100 credits/日够开发期用），provider 首选 Yahoo Finance（免费）+ Finnhub（免费 tier）+ EODHD（期权专长）。生产期若成本或字段不满足，再切直接接。

---

## 4. 接入方案

### 4.1 第一步：注册 qveris + 拿 API key

```bash
# 1. 在 qveris.ai 注册，拿 API key
export QVERIS_API_KEY="qv_your_key"
export QVERIS_BASE_URL="https://qveris.ai/api/v1"

# 2. Discover 期权链能力（免费）
curl -sS "$QVERIS_BASE_URL/search" \
  -H "Authorization: Bearer $QVERIS_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query":"option chain implied volatility per strike","limit":5,"session_id":"plainsight-probe"}'

# 3. Discover consensus 能力（免费）
curl -sS "$QVERIS_BASE_URL/search" \
  -H "Authorization: Bearer $QVERIS_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query":"consensus analyst estimates revisions point-in-time","limit":5,"session_id":"plainsight-probe"}'

# 4. Discover 事件能力（免费）
curl -sS "$QVERIS_BASE_URL/search" \
  -H "Authorization: Bearer $QVERIS_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query":"earnings calendar corporate events ratings","limit":5,"session_id":"plainsight-probe"}'
```

### 4.2 第二步：Inspect 关键 capability 的字段形态

重点确认：
- 期权链 capability 返回的是 **per-strike IV**（算 RND 需要）还是聚合指标（ATM IV / skew）
- consensus capability 是否有 **historical / point-in-time** 参数
- 事件 capability 的字段（event type / date / severity）

### 4.3 第三步：Call 验证一个端到端样本

用 NVDA 做样本：
- 拉期权链 → 算 ATM straddle 隐含区间 + RND + skew + term structure
- 拉 consensus → 看是否有历史修正
- 拉 earnings calendar → 确认事件源

---

## 5. 风险矩阵 + 降级方案

| 风险 | 影响 | 概率 | 降级方案 |
|---|---|---|---|
| **期权链返回聚合指标而非 per-strike IV** | 无法算 RND（Breeden-Litzenberger 需要 per-strike）| 中 | 直接接 Yahoo Finance / Finnhub 原始期权链（确认有 per-strike）|
| **point-in-time consensus 不可得** | 漂移脊椎无法回填历史 | **高** | 方案 B：用价格 + 期权分布时间序列替代；从现在开始自建 consensus 快照 |
| **qveris credits 不够生产用** | 监控层每日扫持仓耗 credits | 低 | 直接接 provider（Yahoo/Finnhub 免费 tier 够 MVP）|
| **期权链流动性差（小票/非美股）** | RND 噪声大 | 中 | PRD §3.6 已有退化方案：honest-empty + 主题/指数 ETF 期权 + beta 兜底 |

---

## 6. 结论 + 下一步

### 结论

**"点→分布"方向的数据地基可行**：
- ✅ 期权链（keystone）— 多 provider 覆盖，字段需 inspect 确认
- ⚠️ consensus 修正 — 可行，但 **point-in-time 是最大风险**，建议先用价格 + 期权分布时间序列替代（方案 B），consensus 修正作为后续补充
- ✅ 事件源 — 标准金融数据，免费 provider 够

### 下一步（需用户）

1. **注册 qveris.ai 拿 API key**（免费 1000 signup credits + 100/日）
2. **跑 §4.1 的 3 个 discover 命令**（免费），把结果发给我
3. 我根据 discover 结果做 inspect + call 验证，确认字段形态
4. 若 point-in-time consensus 确认不可得 → 采用方案 B（价格 + 期权分布时间序列），调整 PRD §9.1 ⓪ 漂移脊椎的实现路径

> **最小验证成本**：$0（qveris 免费 credits + Yahoo Finance 免费 + Finnhub 免费 tier）
> **最小验证时间**：拿到 API key 后 30 分钟内可完成三个 discover + 一个端到端 call
