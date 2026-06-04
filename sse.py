"""SSE event generators for the evidence stream.

Two modes:
- stream_evidence_mock: deterministic fake stream, no LLM cost (frontend dev + offline demos)
- stream_evidence_live: real path, deferred to Phase C
"""
import asyncio
import json
import time
from typing import AsyncIterator


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


_MOCK_AGENT_STEPS = [
    (0.0, "thinking", "拆解假设:聚焦数据中心 GPU 收入复合增速,需对照管理层指引与第三方需求侧信号。"),
    (1.2, "search", "搜索:NVIDIA datacenter revenue guidance FY2027 hyperscaler capex"),
    (3.5, "read", "读取:Nvidia FY26 Q1 earnings transcript — Jensen Huang prepared remarks"),
    (6.8, "search", "搜索:Microsoft Google Meta AWS 2026 AI capex commentary"),
    (10.4, "read", "读取:Morgan Stanley GPU demand tracker — May 2026 update"),
    (14.0, "synthesize", "合并三方证据:管理层指引 + 超大规模厂商资本开支 + 渠道数据,评估假设支撑度。"),
]

_MOCK_CONTENT_CHUNKS = [
    "## 假设审视:数据中心 GPU 复合增速 35%(2026-2030)\n\n",
    "**管理层口径**:Nvidia 在 FY26 Q1 财报会上重申数据中心需求仍处于早期,",
    "Blackwell 平台 ramp 进入第二阶段,Q2 数据中心收入指引环比 +18%。",
    "管理层未给出多年指引,但 Jensen 在问答环节将 AI 基础设施 TAM 上调至 1 万亿美元。\n\n",
    "**超大规模厂商资本开支**:微软、Google、Meta、AWS 四家 2026 资本开支指引合计 4,200 亿美元,",
    "同比 +38%,其中 60%+ 流向 AI 基础设施。Meta 在 5 月明确将 2026 capex 上修至 720-770 亿美元。\n\n",
    "**第三方渠道**:Morgan Stanley GPU tracker 显示 Blackwell B200 在 5 月交期延长至 52 周,",
    "供给端仍是瓶颈,价格端未见松动。SemiAnalysis 估算 2026 GPU 出货量 +85% YoY。\n\n",
    "**反方证据**:Goldman Sachs 5 月警告 2027 后超大规模厂商 ROI 测算开始恶化,",
    "若大模型推理需求增长不及预期,2028 年订单可能下修。\n\n",
    "**结论**:35% 复合增速在 2026-2027 有强支撑,2028-2030 不确定性显著增加。",
]


# Schema strictly matches PRD §15 Appendix A so the same renderer works for
# real LLM output in Phase C.
_MOCK_BRIEF = {
    "assumption_id": None,           # filled per request
    "assumption": "NVDA 数据中心 GPU 业务能保持 35% 复合增速",
    "evidence_count": {"support": 3, "refute": 2, "neutral": 1},
    "overall_balance": "lean_support",
    "generated_at": "2026-05-27T12:34:56Z",
    "evidence_items": [
        {
            "direction": "support",
            "claim": "Jensen 重申 AI infra TAM $1T;Q2 数据中心收入指引 +18% QoQ",
            "body_md": "Nvidia FY26 Q1 earnings call — Jensen Huang restated AI infrastructure TAM at $1T; Q2 data center revenue guided +18% QoQ.",
            "sources": [{
                "url": "https://investor.nvidia.com/financial-info/financial-reports/",
                "title": "Nvidia FY26 Q1 Earnings",
                "date": "2026-05-15",
                "publisher": "Nvidia IR",
            }],
            "scores": {"recency": 5, "source_quality": 5, "relevance": 5},
        },
        {
            "direction": "support",
            "claim": "Hyperscaler 2026 capex 合计 $420B (+38% YoY),AI infra 占 >60%",
            "body_md": "MSFT/GOOG/META/AWS combined 2026 capex guide $420B (+38% YoY), AI infra >60% of mix.",
            "sources": [{
                "url": "https://www.example-research.com/hyperscaler-capex-2026",
                "title": "Hyperscaler 2026 capex aggregate",
                "date": "2026-05-10",
                "publisher": "Industry research",
            }],
            "scores": {"recency": 5, "source_quality": 4, "relevance": 5},
        },
        {
            "direction": "support",
            "claim": "Blackwell B200 lead time 延长到 52 周,定价坚挺",
            "body_md": "Morgan Stanley GPU tracker — Blackwell B200 lead time extended to 52 weeks in May; pricing firm.",
            "sources": [{
                "url": "https://www.morganstanley.com/research/gpu-tracker",
                "title": "MS GPU Tracker May 2026",
                "date": "2026-05-22",
                "publisher": "Morgan Stanley",
            }],
            "scores": {"recency": 5, "source_quality": 4, "relevance": 4},
        },
        {
            "direction": "refute",
            "claim": "Hyperscaler AI ROI 在 2027 后可能转负(若推理需求不及预期)",
            "body_md": "Goldman Sachs AI ROI note — hyperscaler AI ROI math turns negative post-2027 if inference demand undershoots.",
            "sources": [{
                "url": "https://www.goldmansachs.com/insights/ai-roi-2026",
                "title": "GS AI ROI 2026",
                "date": "2026-05-08",
                "publisher": "Goldman Sachs",
            }],
            "scores": {"recency": 5, "source_quality": 4, "relevance": 4},
        },
        {
            "direction": "refute",
            "claim": "Google TPU v6 + AWS Trainium2 已占内部推理负载约 15%",
            "body_md": "Reuters — ASIC competition: Google TPU v6 and AWS Trainium2 take ~15% of internal inference workloads.",
            "sources": [{
                "url": "https://www.reuters.com/technology/asic-2026",
                "title": "ASIC competition vs Nvidia",
                "date": "2026-05-18",
                "publisher": "Reuters",
            }],
            "scores": {"recency": 5, "source_quality": 4, "relevance": 4},
        },
        {
            "direction": "neutral",
            "claim": "2026 GPU 出货量预估 +85% YoY;2027+ 取决于 capex 弹性",
            "body_md": "SemiAnalysis 2026 GPU shipment model — 2026 GPU shipments estimated +85% YoY; 2027+ depends on capex elasticity.",
            "sources": [{
                "url": "https://semianalysis.com/p/2026-gpu-shipments",
                "title": "SemiAnalysis 2026 GPU shipments",
                "date": "2026-05-20",
                "publisher": "SemiAnalysis",
            }],
            "scores": {"recency": 5, "source_quality": 4, "relevance": 5},
        },
    ],
}


async def stream_evidence_mock(
    ticker: str, assumption_id: str, text: str
) -> AsyncIterator[str]:
    """Emit a fake evidence stream over ~5-10s. No LLM cost."""
    start = time.time()

    yield _sse(
        "start",
        {
            "ticker": ticker,
            "assumption_id": assumption_id,
            "assumption_text": text,
            "ts": 0.0,
        },
    )
    await asyncio.sleep(0.2)

    prev_ts = 0.0
    for ts, kind, message in _MOCK_AGENT_STEPS:
        await asyncio.sleep(min(max(ts - prev_ts, 0.3), 1.5) * 0.25)
        prev_ts = ts
        yield _sse(
            "agent_step",
            {
                "ts": round(time.time() - start, 2),
                "type": kind,
                "text": message,  # field name matches SSE schema (was 'message')
            },
        )

    for chunk in _MOCK_CONTENT_CHUNKS:
        await asyncio.sleep(0.3)
        yield _sse(
            "content_delta",
            {"ts": round(time.time() - start, 2), "text": chunk},  # was 'delta'
        )

    await asyncio.sleep(0.3)
    yield _sse(
        "usage",
        {
            "prompt_tokens": 1850,
            "completion_tokens": 920,
            "num_search_queries": 4,
            "cost_usd": 0.0115,
            "elapsed_s": round(time.time() - start, 2),
        },
    )

    brief = dict(_MOCK_BRIEF)
    brief["assumption_id"] = assumption_id
    await asyncio.sleep(0.2)
    yield _sse(
        "complete",
        {
            "ts": round(time.time() - start, 2),
            "brief": brief,
        },
    )
