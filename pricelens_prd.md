# PriceLens · Product Requirements Document

> 反向解码股票价格背后的市场隐含推理
>
> 版本：v0.5 · 2026-05-27 · solo project

---

## 0. TL;DR

**PriceLens 是一个反向运行的投研 Agent。** 现有所有投研 AI 都在做"分析公司 → 输出报告"——PriceLens 反过来：以**当前股价为输入**，反推市场必须假设了什么才能算出这个价格，并对每条假设的证据强度评分。

| 项 | 内容 |
|---|---|
| 赛道 | UCWS Singapore Hackathon 2026 · Agent 赛道 · MiroMind 命题 |
| 核心叙事 | "推理透明" — 透明的是市场的集体推理，不是 AI 自己的推理 |
| 时间 | 2026-04-25 启动 · 2026-06-03 筛选 · 2026-06-13 Demo Day |
| 团队 | 1 人 |
| 状态 | PRD v0.5 · **W1 + W2 已 closed**;W3 进行中(5d 短期归因 + 前端打磨)|

---

## 1. 问题与机会

### 1.1 现有工具的核心局限

| 类型 | 代表 | 局限 |
|---|---|---|
| 通用 deep research | Perplexity, ChatGPT Deep Research | 输出叙述性报告，推理过程是事后叙述，不可审计 |
| 专业投研工具 | Hebbia, Brightwave, AlphaSense | 强于文档检索 + 引用，但仍是"问问题 → 给答案"范式 |
| 量化平台 | Bloomberg Terminal, FactSet | 提供数据，不解释价格 |
| **共同缺失** | — | **没有任何工具系统性地反向解码市场推理** |

### 1.2 主题契合度

MiroMind 命题强调 "AI 的判断过程要可见、可追踪、可验证"。多数参赛队伍会做"AI 自己推理的可视化"——这条已经被 Perplexity 等做过。

**PriceLens 的差异化**：透明的对象是**市场**的集体推理，不是 AI 的推理。这是投资领域最深层的黑盒——当一个分析师说"市场已经 price-in 了"时，他指的就是这个黑盒。没有任何成熟产品做过。

---

## 2. 产品定义

### 2.1 一句话

> 输入：股票代码 + 时间尺度
> 输出：价格背后市场必须假设了什么 + 每条假设的证据评分 + 关键不确定性

### 2.2 关键洞察

价格不是单一信号，它是两个推理系统在多个时间尺度上的叠加：

| 时间尺度 | 主导驱动 | 解码对象 |
|---|---|---|
| 1d / intraday | 资金面、事件 | 流量、情绪 |
| 5d | 资金面 + 短期基本面修正 | + 分析师调整、板块轮动 |
| 30d | 基本面 + 板块 | 财报修正、宏观因子 |
| 1y | 基本面共识 | 隐含 DCF 假设 |

### 2.3 与现有工具对比

| 维度 | 现有工具 | PriceLens |
|---|---|---|
| AI 角色 | 生成分析 | **解码市场已做的隐含分析** |
| 透明对象 | AI 自己的推理 | **市场的集体推理** |
| 输入 | "分析 X 公司" | "NVDA 当前 $130, 5 日 −5%" |
| 输出 | 报告 + 引用 | 可编辑的推理链 + 归因表 |
| 用户介入 | 追问 | 修改任一假设 → 实时重算 |

---

## 3. 用户画像

| Persona | 典型问题 | 价值点 |
|---|---|---|
| 主动型基金经理 | "TSLA 这周跌 5% 到底是什么原因？" | 量化归因 |
| 个人投资者（进阶） | "我看好 NVDA，但市场已经定价了吗？" | 看到分歧在哪 |
| 卖方分析师 | "我的目标价和市场分歧来自哪几个假设？" | 写报告的论据 |
| 学术研究者 | "Black-Litterman 风格的反向价格分解" | 开源工具 |

### 核心使用场景

**A. 短期波动归因**
TSLA 5 日跌 5% → 输入 "TSLA 5d" → 看 waterfall 拆解 → 点开"short interest +18%"看具体证据。

**B. 长期假设审计**
考虑买入 NVDA → 输入 "NVDA 1y" → 看到当前 $130 隐含的 6 条核心假设 → 对其中"FSD/AI capex"表示怀疑 → 修改假设 → 公允价从 $130 跌到 $95 → 决定不买。

**C. 分歧地图（P1）**
用户输入自己的假设 → 与市场共识对比 → 系统标出分歧来自第 3 条假设 → 用户基于此构建非共识 trade。

---

## 4. 功能范围

### 4.1 P0（MVP 必备，无则产品不成立）

| ID | 功能 | 描述 |
|---|---|---|
| F1 | 多时间尺度选择器 | 用户选 1d / 5d / 30d / 1y，系统按权重路由 |
| F2 | 长期反向解码 | 输入价格 → 反推 5-7 条核心假设 |
| F3 | 短期归因（scope 降级） | 仅 5d 单尺度，2 因子归因（基本面修正 + 持仓变动）；30d / 1d 不做。作为长期解码的辅助视图 |
| F4 | 证据搜集（**hero feature**） | 每条假设调用 MiroMind deepresearch 跑 web 证据 + 评分；前端独立展示位，是"推理透明"的核心呈现 |
| F5 | 推理链可视化 | 反向 DCF 流程图 + 假设表 + 归因瀑布 |
| F6 | 用户介入重跑 | 任一假设可滑块修改，下游实时重算。**滑块范围 = Monte Carlo p10-p90**（B3 决议）；两端标注"市场隐含下/上限"；超出区间显示提示"已脱离市场共识" |

### 4.2 P1（demo 加分）

| ID | 功能 | 描述 |
|---|---|---|
| ~~F7~~ | ~~Falsifiability matrix~~ | ✂ **删除（B1 决议，2026-05-27）** — 与 F6 滑块功能重叠；demo 时间让给 F3 5d 归因 |
| F8 | 因子权重透明 | 权重表可视化、可调整 |
| F9 | 个人 vs 市场分歧地图 | 用户假设 vs 共识 → 显示分歧 |
| F12 | F6 滑块 escape hatch | 解锁"超出市场隐含区间"模式，允许用户测极端 case（B3 后续） |

### 4.3 P2（有余力做）

| ID | 功能 | 描述 |
|---|---|---|
| F10 | 历史回放 | 看任意时点的解码结果 |
| F11 | 多股票对比 | 同时解码 N 只股票 |

### 4.4 明确不做

- ❌ 投资建议 / 价格预测（合规风险）
- ❌ 实盘交易接入
- ❌ A 股 / 港股（MVP 只做美股）
- ❌ 期货 / 加密货币 / FX
- ❌ 移动端

### 4.5 合规与免责（B2 决议）

PriceLens 输出的内容（公允价、隐含假设、evidence 评分）形式上类似投资建议，但本质是研究工具。必须在 UI 显式声明边界：

**Footer 常驻一行小字（双语）：**

> ZH：「PriceLens 是研究性工具，展示市场价格背后的隐含假设。所有数值不构成投资建议，使用者需自行判断。」
>
> EN：「PriceLens is a research tool that decodes implied assumptions behind market prices. Outputs are not investment advice. Use at your own discretion.」

**关键数字旁边 tooltip(2 处)：**

1. "估算公允价" (i) tooltip：
   - ZH：「基于 DCF 框架的数学解。DCF 框架对成长股 / 早期公司适用性有限」
   - EN：「Mathematical solution under the DCF framework. DCF has limited applicability for growth / early-stage companies.」
2. "Evidence 评分" (i) tooltip：
   - ZH：「评分基于来源质量 / 时效性 / 相关性三维度，不代表对该证据真实性的背书」
   - EN：「Scores reflect source quality / recency / relevance. They do not endorse the underlying claim's truthfulness.」

**实现：** Footer 5 分钟；2 处 tooltip 30 分钟。W3 末顺手做。

---

## 5. 系统架构

### 5.1 分层

```
┌─────────────────────────────────────────────────────────┐
│  Layer 5: Frontend                                       │
│  - 研报版式 UI (见 pricelens_design_system.md)            │
│  - 时间尺度选择器                                         │
│  - Assumption Table + Evidence 抽屉 + 反向 DCF 流程图     │
│  - 滑块实验室（实时重算）                                  │
└─────────────────────────────────────────────────────────┘
                            ↑
┌─────────────────────────────────────────────────────────┐
│  Layer 4: Orchestration (MiroFlow)                       │
│  - Planner / Timeframe Router / Synthesizer 图           │
└─────────────────────────────────────────────────────────┘
                            ↑
┌─────────────────────────────────────────────────────────┐
│  Layer 3: MiroMind API（单一 client，两种调用模式）       │
│  ───────────────────────────────────────────             │
│  💎 Deep Research 模式 (tool_choice=auto, flagship 235B) │
│     - Evidence Hunter（per-assumption，hero feature）     │
│  ⭐ Chat 模式 (tool_choice=none, mini 30B)                │
│     - Long-term Decoder 解读（数字 → 人话假设）            │
│     - Short-term Attribution 解读（数字 → 归因叙述）       │
│     - Critic（一致性 / 冲突）                             │
│     - Synthesizer（组装前端结构）                          │
└─────────────────────────────────────────────────────────┘
                            ↑
┌─────────────────────────────────────────────────────────┐
│  Layer 2: Computation（Python，无 LLM）                  │
│  - 反向 DCF（scipy.optimize.brentq）                      │
│  - 因子归因（Barra-lite）                                 │
└─────────────────────────────────────────────────────────┘
                            ↑
┌─────────────────────────────────────────────────────────┐
│  Layer 1: Data Tools                                     │
│  价格期权 / 财务 / 共识 / 流量 / 持仓 / 新闻 / 宏观         │
└─────────────────────────────────────────────────────────┘
```

### 5.2 Agent 职责

| Agent | 输入 | 输出 | 实现 |
|---|---|---|---|
| Planner | 用户问题 | 任务图 | Python（MiroFlow graph） |
| Timeframe Router | 时间尺度 | 加权配置 | Python |
| Long-term Decoder | 股票 + 价格 + 财报 | 假设表（5-7 条） | Python（反向 DCF）+ ⭐ Chat |
| Short-term Attribution | 股票 + 时间窗 + 价格变动 | 归因 waterfall | Python（因子模型）+ ⭐ Chat |
| **Evidence Hunter** | 单条假设/因子 | 正反证据 + 评分 | **💎 Deep Research**（hero） |
| Critic | 任一 agent 输出 | 一致性问题、置信度 | ⭐ Chat |
| Synthesizer | 全部输出 | 用户面板可读结果 | ⭐ Chat |

### 5.3 数据源

| 类别 | 数据源 | 难度 |
|---|---|---|
| 价格、期权链 | yfinance（免费） | 低 |
| 财报 | SEC EDGAR API（免费） | 中 |
| 分析师共识 | Yahoo Finance / Tipranks | 中 |
| Short interest | FINRA、NASDAQ（免费） | 中 |
| 13F 持仓 | SEC EDGAR 13F-HR（免费） | 中高 |
| 期权流量 | yfinance options chain | 中 |
| 新闻 + 情绪 | NewsAPI / Marketaux + LLM | 低 |
| 宏观因子 | FRED API（免费） | 低 |

---

## 6. 反向 DCF 方法论

### 6.1 数学本质

标准 DCF：多个假设 → 一个价格
反向 DCF：价格 + N-1 个假设 → 解出剩下的一个

DCF 有 4-6 个核心假设（增长率、利润率、WACC、终值增速、税率、再投资率）。1 个方程解多个未知数是欠定问题。
**解法**：分多次反推，每次锁住其他变量，解一个变量。

### 6.2 实现步骤

**Step 1：构建 baseline DCF（用 consensus 填默认值）**

| 参数 | 数据源 | 默认策略 |
|---|---|---|
| 历史营收/利润率 | `yfinance.Ticker.financials`（5Y） | 直接取 |
| 共识增长（next 5Y） | `yfinance.Ticker.analysis` 或 Tipranks | 中位数 |
| WACC | beta from `Ticker.info` + 10Y treasury from FRED | CAPM 公式 |
| 终值增长率 | — | 默认 2.5% (GDP 长期平均) |
| 当前 FCF | `Ticker.cashflow`：OCF - Capex | 直接算 |
| 净债务 | `Ticker.balance_sheet` | 直接算 |
| 股本 | `Ticker.info["sharesOutstanding"]` | 直接取 |

**Step 2：反向求解器**

```python
from scipy.optimize import brentq

def reverse_solve(target_price, fixed_assumptions, solve_for, search_range):
    """给定目标价 + 锁定的其他假设，解出 solve_for 应为多少"""
    def price_diff(x):
        assumptions = {**fixed_assumptions, solve_for: x}
        return dcf_price(assumptions) - target_price
    return brentq(price_diff, search_range[0], search_range[1])
```

**Step 3：暴露多个隐含假设**

对每个核心假设做一次反向求解，每次把其他变量锁在 consensus：

```python
implied = {
    "implied_5y_revenue_growth": reverse_solve(
        target_price=current_price,
        fixed_assumptions={**consensus, "growth": None},
        solve_for="growth",
        search_range=(-0.20, 0.80),
    ),
    "implied_terminal_margin": reverse_solve(...),
    # ...
}
```

**Step 3.5：Monte Carlo 区间估计（G2 决议）**

点估计在成长股上不可靠（NVDA $130 → 隐含增长可能解出 60%+，数学正确但经济离谱）。改用区间：给锁定参数加扰动，反复反向解，输出 [p25, p50, p75]。

```python
def monte_carlo_implied(data, solve_for, base, perturbations, n=500):
    """给锁定参数加扰动,反复反向解,返回 [p25, p50, p75]"""
    rng = np.random.default_rng(42)
    results = []
    for _ in range(n):
        perturbed = {k: rng.uniform(*r) for k, r in perturbations.items() if k != solve_for}
        x = reverse_solve(data.current_price, replace(base, **perturbed), solve_for, data)
        if x is not None:
            results.append(x)
    return np.percentile(results, [25, 50, 75])
```

输出形如："市场必须假设 NVDA 增长在 **28%-42%** 区间"，而非单点 28.3%。
- demo 上区间叙事更有张力（它本身就提出一个开放问题）
- 边缘 case 自然失败（成长股的 p75 异常高 → 自动暴露方法学边界）
- 完整实现见 `reverse_dcf.py` 的 `monte_carlo_implied()`

**Step 4：识别 load-bearing 假设（敏感度排序）**

```python
def sensitivity(assumption_name, delta=0.01):
    """∂P/∂x 数值版"""
    p_base = dcf_price(consensus)
    perturbed = {**consensus, assumption_name: consensus[assumption_name] + delta}
    return (dcf_price(perturbed) - p_base) / p_base / delta
```

排序后，前 3 个最敏感的 = load-bearing 假设。

**Step 5：每个假设 → LLM + web 搜证据 + 评分**
用 MiroThinker 跑，已经在第 5 节定义。

### 6.3 已知陷阱

| 陷阱 | 处理 |
|---|---|
| 负 FCF 公司（早期成长股） | 用 "implied path to FCF positive year" 替代直接增长率 |
| 非美企业的 WACC | MVP 只做美股 |
| 周期股（如银行） | 后续可加 residual income model；MVP 不强行套 DCF |
| 解出的隐含值超出合理范围（如增长率 >50%）| G2 决议：改用 Monte Carlo 区间估计；区间会自然显示边界；p75 异常高时 UI 标注"接近 DCF 框架边界" |
| **完全 NO SOLUTION（如 TSLA）** | **B4 决议：进入"DCF 边界态"，UI 切换 + Evidence Hunter 切到 boundary mode 寻找替代框架证据（详见 §6.4）** |
| DCF 方法学局限性（成长股） | Demo 时主动说"DCF 是工具不是真理，价值在透明展示" |

### 6.4 DCF 边界态处理（B4 决议）

**今天 reverse_dcf.py 实测 TSLA 完全 NO SOLUTION**：当前价 $429 vs baseline DCF $24，单变量反向解全部失败或退化（WACC 解出 4.2% 即比国债还低）。

这种情况在美股很常见（早期 Snowflake / Palantir / TSLA / DJT 等高估值成长股）。直接报错"computation failed"会让 demo 翻车，必须转化为产品语言。

#### 6.4.1 触发条件（reverse_dcf.py 已可识别）

任一满足即进入边界态：
- 任一变量 NO SOLUTION
- Monte Carlo 成功率 < 30%
- p50 超出合理范围（growth > 50% / WACC < 5% / margin > 50%）

#### 6.4.2 UI 切换

假设面板自动切换为"方法学边界面板"：

| 区块 | 内容 |
|---|---|
| 顶部红色横幅 | ⚠ 当前价格无法用标准 DCF 框架解释 |
| 数学事实 | "为了解出 ${{price}}，需要同时假设 X、Y、Z，三者均不现实" |
| 方法学反思 | "{{ticker}} 当前定价可能来自：[Evidence Hunter 返回的候选 framework]" |
| 替代建议 | "建议改用 Sum-of-Parts / Real Options / Market Comparables 等框架" |

#### 6.4.3 Evidence Hunter 切到 boundary mode

边界态下 Evidence Hunter 接收不同 prompt（见 `prompts/evidence_hunter.md` 的 `mode=boundary`）：

- **不再找** "支持/反驳市场假设 X% 增长" 的证据（在 NO SOLUTION 场景下没意义）
- **改为找** "市场目前在用什么 framework 给 {{ticker}} 定价" 的证据
- 输出仍走 G1 schema，但 `assumption_text` 字段改为 framework hypothesis（如"market 用 robotaxi optionality framework"），evidence 围绕该 framework 的可信度

#### 6.4.4 Demo 价值

TSLA 段落是整个 demo 的 "honesty moment"：
> "我们的 DCF 系统在 TSLA 上'失败'了 — 但这恰恰是 PriceLens 最诚实的瞬间。我们不假装能解释一切，我们告诉你哪些价格 DCF 解释得了，哪些解释不了。然后系统自动转入'寻找市场使用的真正 framework'模式..."

绝大多数 AI 投研产品做不到"诚实承认方法边界"。这是 PriceLens 的关键差异化点之一。

---

## 7. 短期归因方法论

### 7.1 因子模型（Barra-lite）

5 个归因桶：

| 桶 | 数据源 | 计算 |
|---|---|---|
| Fundamental update | 分析师 TP 修正、月度销量 | EPS revision × historical beta |
| Sector / macro | 10Y yield, ETF flows | 历史 β × 因子变动 |
| Flow / positioning | FINRA short interest, CBOE put/call, 13F | 残差分配 |
| Technical | 200-day MA, RSI | CTA 信号 + 技术指标 |
| Unexplained | — | 残差，显式标注为噪声 |

### 7.2 残差诚实原则

如果某条变动无法被显式因子解释，**绝不编造叙事**。直接标 "Unexplained residual"。

---

## 8. 技术选型

| 层 | 选型 | 理由 |
|---|---|---|
| LLM | **MiroMind API（唯一）** | 同一 client 跑两种模式：deepresearch（Evidence Hunter）+ chat (`tool_choice=none`，跑数字→人话、Critic、Synthesizer） |
| 模型策略 | mini 30B 用于 dev + chat 类任务；flagship 235B 只用于最终 evidence | 控制预算（mini 比 flagship 便宜约 3 倍） |
| Prompt 双语设计（A1 决议） | 所有 prompt 模板带 `{{LANG=zh\|en}}` 占位符；测试期 zh，最终提交 en | 比赛要求 en，但测试 zh 更易 review；一键切换避免改两遍 |
| Agent 编排 | **MiroFlow** | 赞助方主推；做 graph 编排，不强依赖任何模型 |
| 后端 | Python 3.11 + FastAPI | 与 MiroFlow 生态一致 |
| 前端 | Vanilla HTML/JS（直接基于 `pricelens_mockup.html` 扩展） | 不引入新框架，节省时间 |
| 部署 | 本地 Demo | hackathon 不需要 prod 部署 |

---

## 9. 单人 24 天里程碑

| 周次 | 日期 | 目标 | 验收 |
|---|---|---|---|
| W1 | 2026-05-20 → 05-26 | API 接通 + 数据源 + 双模式验证 | **✅ Closed 2026-05-27** — Test A deepresearch 1 call $3.21 / Test B chat 1 call $0.18，reverse_dcf.py 在 COST/NVDA/TSLA 三种典型场景全部通过（clean / tension / boundary） |
| W2 | 05-27 → 06-02 | 长期解码端到端贯通 + 后端 + 前端骨架 | **✅ Closed 2026-05-27**（提前完成）— pipeline.py 6 步全通（reverse_dcf → boundary detect → decoder narrator → evidence hunter → critic → synthesizer）；api.py FastAPI 4 endpoints；前端 mockup 接真数据 + 3 ticker 切换 + boundary mode + 滑块真 DCF（JS 端）；全栈集成 TestClient + Claude in Chrome 双向验证通过 |
| W3 | 06-03 → 06-09 | 5d 短期归因 + UI 打磨 + SSE 流式（W4 一并做） | (a) **5d 2 因子归因 waterfall**（基本面修正 + 持仓变动 + 不可解释残差）；(b) G3-C evidence drawer 冻结时间戳标注；(c) bugfix（滑块初始 baseline、cover-grid 窄屏 stack）；(d) SSE 流式 evidence（G4-D）推到 W4 跟预跑一起做 |
| W4 | 06-10 → 06-12 | 预跑 + OFFLINE_MODE + SSE + 打磨 + Demo | (a) 3 只股票（NVDA / TSLA / COST）evidence 预跑并缓存锁版（~$60 一次性）；(b) **G5-B：实现 OFFLINE_MODE 开关**，断网也能跑完 5 分钟；(c) **G4-D SSE 流式 + G6-C Agent 行动日志面板**，demo 时故意 SSE live 跑 1 条 evidence 作为高光；(d) G6-B：UI 角标 "Powered by MiroMind Deep Research"；(e) 录视频 + ppt |

**关键警戒线**：
- 05-26（W1 末）：若 Test B（chat 模式）质量不可用 → 启动退路 1（Python 模板硬编码"数字→人话"，LLM 只做最后润色）
- 06-02（W2 末）：若长期解码端到端没跑通 → 砍所有 P1，全力推 long-term 主线
- 06-09（W3 末）：若 5d 归因因子数 <2 → 全砍短期，只留长期解码 + evidence brief 双主秀

### 单人项目调整

| 原计划（隐含 2-3 人） | 单人现实版 |
|---|---|
| W1：基础设施 + 多数据源 | **只跑通 MiroFlow + 1 数据源** |
| W2：完整反向 DCF | **简化反 DCF（3 参数）** |
| W3：5 个短期因子 + 前端 | **只做 2 个高置信因子** + 前端核心 |
| 不变 | 不变 |

---

## 10. 风险与缓解

| 风险 | 严重度 | 缓解 |
|---|---|---|
| 短期归因准确性低 | 高 | 诚实标注"不可解释"；2 个高置信因子 > 10 个糙因子 |
| 反向 DCF 数学难做 | 中 | 用简化版（3 参数：增速 / 利润率 / WACC），不追求 Wall Street 级精度 |
| **Reverse DCF 在成长股给出经济离谱解** | **高（项目方法学最大风险）** | G2 决议：用 Monte Carlo 区间估计代替点估计；p25-p75 区间叙事可承受异常值；p75 异常高时 UI 标注接近方法学边界。原型见 `reverse_dcf.py` |
| MiroMind API 额度不够（$100 + 100 calls） | 高 | (a) dev 全程用 mini；(b) 强制缓存层（同一 assumption 不重跑）；(c) demo 中 5/6 条走缓存，只对 1 条故意 SSE 流式 live 跑（G4-D + G6-C）；(d) 实测一次完整 NVDA demo ~$7.5 + 6 calls，预算够约 10 次完整跑 |
| Chat 模式（tool_choice=none）输出质量未知 | 中 | W1 必跑 Test B；翻车则退路：Python 模板硬编码"数字→人话"逻辑，LLM 只做最后润色，绝不破例引入第二个 API |
| **Demo 现场任何 API/数据/网络故障** | 高 | G5 决议：实现 `OFFLINE_MODE` 开关（B）+ 三级自动降级（D：live → cache → 全离线 demo）；演示前一晚必须完整测一次全离线模式 |
| 数据 API 限速 / 失败 | 中 | 全部数据请求加缓存层 |
| 时间不够，前端做不完 | 中 | mockup 已存在；可直接用作 demo，必要时砍交互 |
| 选题撞车（别队也做投研推理透明） | 中 | "反向解码市场"角度是差异化护城河 |

---

## 11. Demo Day 演示脚本（5 分钟草案）

```
[0:00-0:30] 问题陈述
"所有投研 AI 都告诉你公司值多少钱。
 但市场每天都在告诉你它认为公司值多少钱。
 没有人解码这个市场的回答。"

[0:30-1:30] COST：方法学清晰工作（"系统能给清晰答案"）
- 输入 COST → 出 5 条假设（区间估计："市场假设增长在 16%-28% 区间"）
- 强调："COST 不是炒作股，DCF 框架适用，结果干净"
- 点开任一假设 → evidence brief 瞬间出（缓存）

[1:30-2:30] NVDA：方法学暴露矛盾 + MiroMind live demo（demo 高光）
- 输入 NVDA → 出 6 条假设（区间："市场必须假设 56%-71% 增长 + 6% WACC"）
- 强调："这是市场必须**同时**假设的事 — 任一不成立 $213 站不住"
- 点击第 1 条假设 → 瞬间出 evidence（缓存）
- **点击第 2 条 "AI capex 持续性" → 故意 live 跑（G4-D SSE + G6-C 行动日志）**
  → 右侧 "Agent 行动日志" 面板流式输出：
    `[+3s] 调用 MiroMind Deep Research...`
    `[+8s] agent 正在搜索 "NVIDIA Q1 2026 capex guidance"...`
    `[+15s] agent 阅读 nvidianews.nvidia.com...`
    `[+22s] agent 综合 6 条证据，生成结构化输出`
  → ~30 秒后 evidence brief 完整出现（SSE 渐进式）
  → 强调："这是 MiroMind Deep Research 真实在跑，不是预录"
- 修改滑块（范围 = Monte Carlo p10-p90） → 公允价实时重算；evidence 冻结 + 标注（G3-C）

[2:30-3:30] TSLA：方法学诚实承认边界（B4 决议高光）
- 输入 TSLA → 系统不报错，进入"DCF 边界态"
- 红色横幅：⚠ 当前价格无法用标准 DCF 解释
- 显示数学事实："需要同时假设 growth > 80% + WACC < 4%，两者均不现实"
- 切到方法学反思 + Evidence Hunter boundary mode：自动搜"市场在用什么 framework 给 TSLA 定价"
- 强调："我们不假装能解释一切。这是 PriceLens 最诚实的瞬间"

[3:30-4:30] 短期归因 demo（TSLA 5 日）
- 输入 TSLA 5d → 出 waterfall（2 因子：基本面修正 + 持仓变动）
- 强调："这不是事后叙述，是量化归因"
- 点击各条 → 看到 evidence

[4:30-5:00] 收尾
"PriceLens 不给你答案，
 PriceLens 让你看到市场已经给出的答案，
 并告诉你它在什么前提下成立。
 哪些价格 DCF 解释得了，哪些解释不了 — 我们都诚实告诉你。"

(全程 UI 右下角角标：Powered by MiroMind Deep Research — G6-B；底部 footer：disclaimer — B2)
```

---

## 12. 成功标准

### 黑客松层面
- ✅ 进入 Demo Day Top 15-20 finalist（线上筛选通过）
- 🎯 进入 Agent 赛道前 3
- 🏆 拿到 Agent 赛道冠军（$10K）

### 产品层面（5 分钟 demo 后评委反应）
- 评委说 "我没见过这种角度" → ✅ 差异化成功
- 评委尝试点击/交互节点 → ✅ 交互成功
- 评委问 "这能不能扩展到 X" → ✅ 留下深刻印象

### 开源层面（次要加分）
- GitHub repo 完整，README 清晰
- 代码可被他人 fork 跑通
- 50+ stars

---

## 13. 待决议事项

| # | 问题 | 状态 |
|---|---|---|
| Q1 | ~~MiroMind API 实际接口形态~~ | ✅ Closed（2026-05-26）— OpenAI 兼容 chat completions；单一模型支持 deepresearch / chat 两种模式 |
| Q2 | ~~是否做 Singapore 本地股票~~ | ✅ Closed（2026-05-26）— 不做。新增数据源工作量大、与差异化无关 |
| Q3 | 前端框架 | ✅ Closed（2026-05-26）— 直接基于 `pricelens_mockup.html` 扩展（见 §8） |
| Q4 | 是否要做 P1 功能 F9（分歧地图） | W3 中期决定 |
| Q5 | MiroMind 的 rate limit（RPM / TPM）未知 | W1 验证时观察 |
| Q6 | G1-G6 全部 closed | ✅ Closed（2026-05-27）— 见各章节内的"G# 决议"标注 + Appendix A |
| Q7 | Wind AIFin Market 数据源接入 | ⏸ Deferred — post-MVP 升级项。已确认 API key + skill 安装路径可行；MVP 阶段使用 yfinance |
| Q8 | B1-B4 产品决策 | ✅ Closed（2026-05-27）— B1 F7 删 / B2 disclaimer 双语 footer+tooltip / B3 滑块范围 = Monte Carlo p10-p90 / B4 DCF 边界态（§6.4） |
| Q9 | UI / 输出语言 | ✅ Closed（2026-05-27）— A1 决议：测试期 zh，最终提交 en；prompt 模板带 `{{LANG}}` 占位符一键切换 |

---

## 14. 文档关联

| 文档 | 作用 |
|---|---|
| `CLAUDE.md` | 项目上下文（给未来 Claude 会话） |
| `pricelens_design_system.md` | 前端设计的不可违反的视觉契约 |
| `pricelens_mockup.html` | 设计系统的参考实现 |
| `reverse_dcf.py` | 反向 DCF 原型（含 G2 Monte Carlo 区间估计） |
| `requirements.txt` | Python 依赖 |
| `prompts/evidence_hunter.md` | Evidence Hunter prompt 模板（standard + boundary 两种 mode） |
| `prompts/decoder_narrator.md` | Long-term Decoder narrator prompt 模板（数字 → 人话假设） |
| `hackathon_track.png` | MiroMind 赛道原始命题图 |

---

## 15. Appendix A · Evidence brief schema（G1 决议）

### A.1 Schema 定义

Evidence Hunter 的输出契约。所有 agent（Critic / Synthesizer / 前端）按此 schema 解析。

```json
{
  "assumption_id": "nvda_growth_5y",
  "assumption_text": "市场假设 NVDA 未来 5 年营收 CAGR 在 28%-42% 区间",
  "evidence_items": [
    {
      "direction": "support",
      "claim": "AI capex 2026 指引超预期",
      "body_md": "Markdown 正文，允许加粗 / 列表 / 引用 / 数字表格...",
      "sources": [
        {
          "url": "https://nvidianews.nvidia.com/...",
          "title": "NVIDIA Q1 2026 Earnings Call",
          "date": "2026-04-15",
          "publisher": "NVIDIA IR"
        }
      ],
      "scores": {
        "recency": 5,
        "source_quality": 5,
        "relevance": 5
      }
    }
  ],
  "overall_balance": "lean_support",
  "evidence_count": {"support": 3, "refute": 2, "neutral": 1},
  "generated_at": "2026-05-27T12:34:56Z",
  "generation_metadata": {
    "model": "mirothinker-1-7-deepresearch",
    "tool_calls": 18,
    "tokens": {"input": 12450, "output": 3200},
    "cost_usd": 1.18
  }
}
```

### A.2 字段语义

| 字段 | 必填 | 说明 |
|---|---|---|
| `assumption_id` | 是 | 跨 agent 引用，格式 `{ticker}_{var}` |
| `assumption_text` | 是 | 人话版，直接显示给用户（区间估计形式） |
| `direction` | 是 | `support` / `refute` / `neutral`。前端按此分两栏 |
| `claim` | 是 | 一句话总结 |
| `body_md` | 是 | 详细 markdown，evidence 抽屉展开内容 |
| `sources` | 是 | ≥ 1 个引用；带 `date` 用于 G3 冻结时显示"基于 X 日数据" |
| `scores.recency` | 是 | 1-5，见 A.3 |
| `scores.source_quality` | 是 | 1-5 |
| `scores.relevance` | 是 | 1-5 |
| `overall_balance` | 是 | 5 档：`bear` / `lean_bear` / `balanced` / `lean_support` / `support` |
| `evidence_count` | 是 | 简单计数，前端徽章用 |
| `generation_metadata` | 是 | 成本追踪、可审计、demo 时可展示 |

### A.3 评分规则（1-5 三维度）

**recency（时效性）**
- 5 = 30 天内 | 4 = 90 天内 | 3 = 6 个月内 | 2 = 1 年内 | 1 = 1 年以上

**source_quality（来源质量）**
- 5 = 一手：公司财报、IR 演示、SEC filing、央行/政府数据
- 4 = 高质量二手：卖方研报、Bloomberg / Reuters / WSJ / FT
- 3 = 一般二手：行业媒体、知名分析师博客
- 2 = 三手：转载新闻、汇总报道
- 1 = 匿名 / 论坛 / 不可验证

**relevance（相关性）**
- 5 = 直接证实或证伪假设 | 4 = 强相关上下文 | 3 = 一般相关
- 2 = 弱相关 | 1 = 边缘相关（Critic 应标记可疑）

### A.4 Critic 校验规则

Critic agent 读 evidence brief 时必须校验：
1. `direction` 与 `claim` / `body_md` 内容方向一致（避免标错支持/反对）
2. `scores.recency` 与 `sources[].date` 实际匹配
3. 至少 1 条 `support` 和 1 条 `refute`（纯一面倒不可信，标记 "evidence imbalance"）
4. `overall_balance` 与 `evidence_count` 大致一致（5 条 support / 0 条 refute 不能是 `balanced`）

### A.5 Evidence Hunter prompt 收尾约束

Evidence Hunter 的 prompt 末尾必须包含：

```
请严格按以下 JSON schema 输出。所有顶层字段必填。
body_md 字段允许 markdown；其他字段必须是 plain string / number / enum。
不要在 JSON 外输出任何文字。
{schema 全文}
```

### A.6 与 G3（evidence 冻结）的关系

用户拖动滑块改假设时（G3-C）：
- evidence brief 内容不变
- 前端在 evidence 抽屉顶部加一条信息条："基于市场原始假设 `assumption_text`（`generated_at` 抓取）"
- 滑块下方按钮 "用我的假设重新评估"（P2，灰禁）

---

## 修订日志

| 版本 | 日期 | 修改 |
|---|---|---|
| v0.1 | 2026-05-20 | 初稿。从多轮对话沉淀而来。 |
| v0.2 | 2026-05-26 | MiroMind API 到位后重构。锁定"单一 API + 两种模式"架构；新增 Layer 2 Computation 层；F4 升级 hero / F3 scope 降级；W1 验收增加 Test B；预算模型 + 风险表更新；Q1/Q2/Q3 closed。 |
| v0.3 | 2026-05-27 | G1-G6 决议全部落地：G1 evidence schema（见 Appendix A）；G2 Monte Carlo 区间估计（§6.2 Step 3.5 + `reverse_dcf.py`）；G3 evidence 冻结 + 标注；G4 SSE 流式 evidence；G5 OFFLINE_MODE + 三级降级；G6 UI 角标 + Agent 行动日志。Demo 脚本重写（live agent log 高光时刻）。风险表新增 2 行（reverse DCF 经济离谱解 / demo 现场故障）。Wind AIFin 数据源决定：post-MVP 升级，MVP 用 yfinance（Q7 deferred）。 |
| v0.4 | 2026-05-27 | reverse_dcf.py 在 COST / NVDA / TSLA 实测后，B1-B4 + A1 决议全部落地：B1 F7 falsifiability matrix 删除（与 F6 重叠 + demo 时间让给 5d 归因）；B2 双语 disclaimer（footer 常驻 + 2 处 tooltip，§4.5）；B3 F6 滑块范围 = Monte Carlo p10-p90；B4 DCF 边界态完整 spec（§6.4，含 TSLA 案例 + Evidence Hunter boundary mode）；A1 prompt 双语设计 `{{LANG}}` 占位符（§8）。Demo 脚本完全重写为 COST→NVDA→TSLA 三段叙事。Prompt 模板 evidence_hunter.md + decoder_narrator.md 待 review。 |
| v0.5 | 2026-05-27 | **W1 + W2 closed**(同日提前完成)。Stack:`pipeline.py` 6 步端到端 + `api.py` FastAPI + `critic.py` Python 机械校验 + `prompts/synthesizer.md` chat 模式 + 前端 mockup 接真数据(ticker 切换 + boundary mode + 滑块真 DCF in JS)。Git init 后共 12+ commits on master,3 个 worker agents 并行 + 4 个 bugfix commits(slider initial、cover-grid 窄屏 stack、table responsive、sec-h 对齐)。剩余 W3 重点:5d 短期归因 waterfall + G3-C 冻结标注;SSE 流式 + OFFLINE_MODE 推到 W4 跟预跑一起做。Synthesizer 代码到位但未 live 测过(留 W4 demo dry-run 时一并)。 |
