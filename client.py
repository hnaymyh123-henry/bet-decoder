"""LLM API client — provider-configurable.

Two modes through one client (per PRD §8):
- deepresearch mode: Evidence Hunter + Market Narrative (needs a web-search agent)
- chat mode (tool_choice=none): Decoder narrator, Critic, Synthesizer

Provider is selected by the LLM_PROVIDER env var:

  miromind  (default) — agentic Deep Research; always streams SSE and emits
            non-standard `reasoning_steps` / `search_results`, so we parse SSE by
            hand (the OpenAI SDK can't model it).  Web-search capable.

  tokendance          — OpenAI-compatible gateway (DeepSeek V4 Pro etc.); standard
            chat completions via the `openai` SDK.  A plain chat model CANNOT
            browse the web, so "deep research" here would be ungrounded guesswork
            with fabricated sources.  WEB_SEARCH_CAPABLE is therefore False unless
            ALLOW_UNGROUNDED_RESEARCH=1, and the research layers (evidence /
            narrative) honest-empty instead of inventing sources.

Env knobs:
  LLM_PROVIDER=miromind|tokendance
  LLM_BASE_URL=...                 (override the provider default endpoint)
  LLM_MODEL / LLM_MODEL_CHAT / LLM_MODEL_RESEARCH=...   (override model ids)
  ALLOW_UNGROUNDED_RESEARCH=1      (let a non-search provider answer research calls)
  <PROVIDER>_API_KEY               (MIROMIND_API_KEY or TOKENDANCE_API_KEY)
"""
import json
import os
import re
from typing import Iterator

import httpx
from dotenv import load_dotenv

load_dotenv()

PROVIDER = os.environ.get("LLM_PROVIDER", "miromind").strip().lower()
_ALLOW_UNGROUNDED = os.environ.get("ALLOW_UNGROUNDED_RESEARCH", "").lower() in (
    "1", "true", "yes")

if PROVIDER == "tokendance":
    BASE_URL = os.environ.get("LLM_BASE_URL", "https://tokendance.space/gateway/v1")
    API_KEY_ENV = "TOKENDANCE_API_KEY"
    _MODEL = os.environ.get("LLM_MODEL", "deepseek-v4-pro")
    MODEL_MINI = os.environ.get("LLM_MODEL_CHAT", _MODEL)
    MODEL_FLAGSHIP = os.environ.get("LLM_MODEL_RESEARCH", _MODEL)
    PROTOCOL = "openai"
    # A plain chat model can't do grounded web research; only allow it to answer
    # research-mode calls when the operator explicitly opts in (and accepts that
    # the output is the model's unverified guesswork, not web-sourced evidence).
    WEB_SEARCH_CAPABLE = _ALLOW_UNGROUNDED
    # Gateway per-token rates aren't published on the models endpoint; leave cost
    # untracked here (DeepSeek is cheap).  Override via code if you need it.
    PRICING_PER_MILLION: dict[str, tuple[float, float]] = {}
else:  # miromind (default)
    BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.miromind.ai/v1")
    API_KEY_ENV = "MIROMIND_API_KEY"
    MODEL_MINI = "mirothinker-1-7-deepresearch-mini"
    MODEL_FLAGSHIP = "mirothinker-1-7-deepresearch"
    PROTOCOL = "miromind-sse"
    WEB_SEARCH_CAPABLE = True
    PRICING_PER_MILLION = {
        "mirothinker-1-7-deepresearch-mini": (1.25, 10.0),
        "mirothinker-1-7-deepresearch": (4.0, 25.0),
    }


def api_key_present() -> bool:
    """True if the active provider's API key is set in the environment."""
    return bool(os.environ.get(API_KEY_ENV))


def web_search_capable() -> bool:
    """True if the active provider can actually perform grounded web research.

    The research layers (evidence hunter, market narrative) MUST honest-empty when
    this is False — a non-search chat model would otherwise emit ungrounded claims
    with fabricated source URLs, which the source-tier classifier would then mis-
    rank as real authority.  That is exactly the failure this product exists to
    avoid, so the guardrail lives here, in code."""
    return WEB_SEARCH_CAPABLE


class ToolCallingUnsupported(RuntimeError):
    """Raised by call_chat_tools when the active provider can't do OpenAI-style
    function calling (the MiroMind SSE path).  The orchestrator catches it and
    falls back to the deterministic decode."""


# Test seam: assign a callable to drive call_chat_tools with scripted tool_calls
# (offline orchestrator tests inject a stub here so they never hit the network).
_CHAT_TOOLS_IMPL = None


def tool_calling_capable() -> bool:
    """True if the active provider supports OpenAI-style function calling.

    Only the standard OpenAI protocol (e.g. tokendance / DeepSeek V4 Pro, which
    supports up to 128 tools + parallel calls).  The MiroMind SSE path has its own
    built-in web search but does NOT expose arbitrary `tools`, so it returns False
    and the orchestrator falls back to the deterministic decode there."""
    return _CHAT_TOOLS_IMPL is not None or PROTOCOL == "openai"


def _api_key() -> str:
    key = os.environ.get(API_KEY_ENV)
    if not key:
        raise RuntimeError(
            f"{API_KEY_ENV} not set (LLM_PROVIDER={PROVIDER}). "
            "Copy .env.example to .env and fill in your key."
        )
    return key


# ---------------------------------------------------------------------------
# Standard OpenAI-compatible path (TokenDance / any OpenAI-style gateway).
# ---------------------------------------------------------------------------

def _openai_call(prompt: str, model: str, timeout: float, verbose: bool = False) -> dict:
    """One non-streaming chat completion via the OpenAI SDK.  No tools/search —
    returns the same envelope shape as the MiroMind path (search_results empty)."""
    from openai import OpenAI  # imported lazily so the miromind path needs no SDK

    client = OpenAI(base_url=BASE_URL, api_key=_api_key(), timeout=timeout)
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
    )
    content = (resp.choices[0].message.content or "") if resp.choices else ""
    if verbose:
        print(content, end="", flush=True)
    usage = None
    if getattr(resp, "usage", None) is not None:
        try:
            usage = resp.usage.model_dump()
        except Exception:
            usage = {
                "prompt_tokens": getattr(resp.usage, "prompt_tokens", 0),
                "completion_tokens": getattr(resp.usage, "completion_tokens", 0),
            }
    return {
        "content": content,
        "model": model,
        "usage": usage,
        "cost_usd": estimate_cost(usage, model),
        "reasoning_chars": 0,
        "tool_call_count": 0,
        "search_results": [],
        "last_chunk": {},
    }


# ---------------------------------------------------------------------------
# MiroMind SSE path (custom: reasoning_steps / search_results / num_search_queries).
# ---------------------------------------------------------------------------

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
    """Deepresearch mode. On MiroMind the model can call web_search etc.; on an
    OpenAI-compatible gateway it degrades to a plain chat completion (no tools —
    callers gate on web_search_capable() to avoid presenting ungrounded output)."""
    if PROTOCOL == "openai":
        return _openai_call(prompt, model, timeout, verbose=verbose)
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
    """Chat mode, no tool use."""
    if PROTOCOL == "openai":
        return _openai_call(prompt, model, timeout, verbose=verbose)
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "tool_choice": "none",
    }
    return _aggregate_stream(payload, timeout, verbose=verbose)


def call_chat_tools(
    messages: list[dict],
    *,
    model: str = MODEL_MINI,
    tools: list[dict] | None = None,
    tool_choice: str = "auto",
    temperature: float | None = None,
    timeout: float = 180.0,
    verbose: bool = False,
) -> dict:
    """One tool-calling chat turn over a FULL `messages` array (so the caller can
    thread assistant tool-call turns + role:"tool" results across rounds).

    Returns the standard envelope PLUS:
      - tool_calls: [{"id","name","arguments_raw"}]  (empty when the model answered)
      - assistant_message: the assistant turn dict to append to `messages` BEFORE
        the tool results (OpenAI matches each result to it by tool_call_id)
      - finish_reason

    Raises ToolCallingUnsupported on a non-tool provider (miromind-sse)."""
    if _CHAT_TOOLS_IMPL is not None:
        return _CHAT_TOOLS_IMPL(messages, model=model, tools=tools,
                                tool_choice=tool_choice, temperature=temperature,
                                timeout=timeout, verbose=verbose)
    if PROTOCOL != "openai":
        raise ToolCallingUnsupported(
            f"provider '{PROVIDER}' (protocol {PROTOCOL}) has no OpenAI tool calling"
        )
    return _openai_call_tools(messages, model=model, tools=tools,
                              tool_choice=tool_choice, temperature=temperature,
                              timeout=timeout, verbose=verbose)


def _openai_call_tools(messages, *, model, tools, tool_choice, temperature,
                       timeout, verbose=False) -> dict:
    """One OpenAI-compatible chat completion with optional `tools` (extended
    envelope — see call_chat_tools)."""
    from openai import OpenAI

    client = OpenAI(base_url=BASE_URL, api_key=_api_key(), timeout=timeout)
    kwargs: dict = {"model": model, "messages": messages}
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = tool_choice
    if temperature is not None:
        kwargs["temperature"] = temperature
    resp = client.chat.completions.create(**kwargs)
    msg = resp.choices[0].message if resp.choices else None
    content = (getattr(msg, "content", None) or "") if msg else ""
    finish_reason = resp.choices[0].finish_reason if resp.choices else None
    raw_calls = (getattr(msg, "tool_calls", None) or []) if msg else []
    tool_calls = [
        {"id": tc.id, "name": tc.function.name,
         "arguments_raw": tc.function.arguments}
        for tc in raw_calls
    ]
    # Re-send-safe assistant turn (built manually so no stray SDK fields like
    # `refusal`/`audio` leak into the next request and 400 it).
    assistant_message: dict = {"role": "assistant", "content": content}
    if raw_calls:
        assistant_message["tool_calls"] = [
            {"id": tc.id, "type": "function",
             "function": {"name": tc.function.name,
                          "arguments": tc.function.arguments}}
            for tc in raw_calls
        ]
    if verbose and content:
        print(content, end="", flush=True)
    usage = None
    if getattr(resp, "usage", None) is not None:
        try:
            usage = resp.usage.model_dump()
        except Exception:
            usage = {"prompt_tokens": getattr(resp.usage, "prompt_tokens", 0),
                     "completion_tokens": getattr(resp.usage, "completion_tokens", 0)}
    return {
        "content": content,
        "model": model,
        "usage": usage,
        "cost_usd": estimate_cost(usage, model),
        "tool_calls": tool_calls,
        "assistant_message": assistant_message,
        "finish_reason": finish_reason,
        "reasoning_chars": 0,
        "tool_call_count": len(tool_calls),
        "search_results": [],
        "last_chunk": {},
    }


def estimate_cost(usage: dict | None, model: str) -> float:
    if not usage:
        return 0.0
    rate_in, rate_out = PRICING_PER_MILLION.get(model, (0.0, 0.0))
    prompt_tokens = usage.get("prompt_tokens", 0)
    completion_tokens = usage.get("completion_tokens", 0)
    return (prompt_tokens * rate_in + completion_tokens * rate_out) / 1_000_000
