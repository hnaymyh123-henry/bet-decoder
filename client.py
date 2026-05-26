"""MiroMind API client.

NOTE: MiroMind always streams SSE (no way to disable) and emits non-standard
`reasoning_steps` fields, so we can't use the OpenAI SDK directly. We use httpx
and parse SSE manually.

Two modes through one client (per PRD §8):
- deepresearch mode (tool_choice=auto, default): for Evidence Hunter
- chat mode (tool_choice=none): for Decoder narrator, Critic, Synthesizer
"""
import json
import os
import re
from typing import Iterator

import httpx
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://api.miromind.ai/v1"

PRICING_PER_MILLION = {
    "mirothinker-1-7-deepresearch-mini": (1.25, 10.0),
    "mirothinker-1-7-deepresearch": (4.0, 25.0),
}

MODEL_MINI = "mirothinker-1-7-deepresearch-mini"
MODEL_FLAGSHIP = "mirothinker-1-7-deepresearch"


def _api_key() -> str:
    key = os.environ.get("MIROMIND_API_KEY")
    if not key:
        raise RuntimeError(
            "MIROMIND_API_KEY not set. Copy .env.example to .env and fill in your key."
        )
    return key


def _stream_sse(payload: dict, timeout: float) -> Iterator[dict]:
    """Yield parsed JSON objects from SSE 'data:' lines."""
    headers = {
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": "application/json",
    }
    with httpx.stream(
        "POST",
        f"{BASE_URL}/chat/completions",
        headers=headers,
        json=payload,
        timeout=timeout,
    ) as response:
        response.raise_for_status()
        for raw_line in response.iter_lines():
            if not raw_line or not raw_line.startswith("data:"):
                continue
            data = raw_line[len("data:"):].strip()
            if data == "[DONE]":
                break
            try:
                yield json.loads(data)
            except json.JSONDecodeError:
                continue


def _aggregate_stream(payload: dict, timeout: float, verbose: bool = False) -> dict:
    content_parts: list[str] = []
    reasoning_chars = 0
    last_chunk: dict = {}
    usage: dict | None = None
    search_results: list = []
    model: str = payload["model"]

    for chunk in _stream_sse(payload, timeout):
        last_chunk = chunk
        if "model" in chunk:
            model = chunk["model"]
        if "usage" in chunk and chunk["usage"]:
            usage = chunk["usage"]
        if "search_results" in chunk and chunk["search_results"]:
            search_results = chunk["search_results"]

        for choice in chunk.get("choices", []):
            delta = choice.get("delta", {}) or {}

            if "content" in delta and delta["content"]:
                content_parts.append(delta["content"])
                if verbose:
                    print(delta["content"], end="", flush=True)

            for step in delta.get("reasoning_steps", []) or []:
                if step.get("type") == "thinking":
                    reasoning_chars += len(step.get("thought", ""))

    content = "".join(content_parts)
    # Tool count comes from usage.num_search_queries (authoritative);
    # reasoning_steps doesn't expose tool events.
    tool_call_count = (usage or {}).get("num_search_queries", 0)

    return {
        "content": content,
        "model": model,
        "usage": usage,
        "cost_usd": estimate_cost(usage, model),
        "reasoning_chars": reasoning_chars,
        "tool_call_count": tool_call_count,
        "search_results": search_results,
        "last_chunk": last_chunk,
    }


# JSON parser tolerant of model quirks (e.g. `\$` markdown escapes inside JSON strings)
_INVALID_ESCAPE_RE = re.compile(r'\\([^"\\/bfnrtu])')


def parse_loose_json(text: str) -> dict:
    """json.loads but tolerant of non-spec backslash escapes the model sometimes emits.

    Strips markdown-fence wrappers, removes BOM, then tries strict json.loads.
    On failure, drops invalid `\\X` escapes (e.g. `\\$` → `$`) and retries.
    """
    cleaned = text.strip().lstrip("﻿")
    if cleaned.startswith("```"):
        # Strip leading ```json or ``` and trailing ```
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        fixed = _INVALID_ESCAPE_RE.sub(r"\1", cleaned)
        return json.loads(fixed)


def call_deepresearch(
    prompt: str,
    model: str = MODEL_MINI,
    timeout: float = 600.0,
    verbose: bool = False,
) -> dict:
    """Test A path: deepresearch mode. Model can call web_search etc."""
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
    }
    return _aggregate_stream(payload, timeout, verbose=verbose)


def call_chat(
    prompt: str,
    model: str = MODEL_MINI,
    timeout: float = 180.0,
    verbose: bool = False,
) -> dict:
    """Test B path: chat mode, no tool use."""
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "tool_choice": "none",
    }
    return _aggregate_stream(payload, timeout, verbose=verbose)


def estimate_cost(usage: dict | None, model: str) -> float:
    if not usage:
        return 0.0
    rate_in, rate_out = PRICING_PER_MILLION.get(model, (0.0, 0.0))
    prompt_tokens = usage.get("prompt_tokens", 0)
    completion_tokens = usage.get("completion_tokens", 0)
    return (prompt_tokens * rate_in + completion_tokens * rate_out) / 1_000_000
