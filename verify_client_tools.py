"""client.call_chat_tools verification — deterministic, zero API/network.

Exercises the tool-calling seam via a scripted stub (_CHAT_TOOLS_IMPL): the
extended envelope (tool_calls / assistant_message / finish_reason), arg parsing
via parse_loose_json (incl. DeepSeek's `\\$` escapes), the capability flag, and
the ToolCallingUnsupported fallback on the miromind SSE path.

Run:  MIROMIND_API_KEY="" "/c/Users/Henry Ma/miniconda3/python.exe" verify_client_tools.py
"""
from __future__ import annotations

import client

_passed = 0
_failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  | {detail}" if detail else ""))


print("=" * 72)
print("client.call_chat_tools (tool calling) verification")
print("=" * 72)

_seen = {}


def _stub(messages, **kw):
    _seen["messages"] = messages
    _seen["tools"] = kw.get("tools")
    _seen["temperature"] = kw.get("temperature")
    return {
        "content": "", "model": "stub",
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        "cost_usd": 0.0,
        "tool_calls": [{"id": "call_1", "name": "run_lens",
                        "arguments_raw": '{"lens": "pe"}'}],
        "assistant_message": {"role": "assistant", "content": None,
                              "tool_calls": [{"id": "call_1", "type": "function",
                                              "function": {"name": "run_lens",
                                                           "arguments": '{"lens":"pe"}'}}]},
        "finish_reason": "tool_calls",
        "tool_call_count": 1, "search_results": [],
    }


# --- AC1: stub seam threads messages + returns the extended envelope --------
client._CHAT_TOOLS_IMPL = _stub
env = client.call_chat_tools(
    [{"role": "user", "content": "decode NVDA"}],
    tools=[{"type": "function", "function": {"name": "run_lens"}}],
    temperature=0,
)
check("AC1 _CHAT_TOOLS_IMPL stub is used (no network) + threads messages",
      _seen.get("messages") == [{"role": "user", "content": "decode NVDA"}]
      and _seen.get("temperature") == 0)
check("AC1 extended envelope present (tool_calls / assistant_message / finish_reason)",
      {"tool_calls", "assistant_message", "finish_reason"} <= set(env.keys())
      and env["tool_calls"][0]["name"] == "run_lens",
      f"finish={env.get('finish_reason')}")

# --- AC2: tool-call arguments parse via parse_loose_json (incl. \$ escapes) --
args = client.parse_loose_json(env["tool_calls"][0]["arguments_raw"])
check("AC2 tool_call arguments parse to a dict", args == {"lens": "pe"}, f"{args}")
loose = client.parse_loose_json('{"target": "\\$200 PT", "x": 1}')
check("AC2 parse_loose_json tolerates DeepSeek's non-spec \\$ escape",
      loose == {"target": "$200 PT", "x": 1}, f"{loose}")

# --- AC3: tool_calling_capable() reflects the provider --------------------
check("AC3 tool_calling_capable True while a stub is installed",
      client.tool_calling_capable() is True)
client._CHAT_TOOLS_IMPL = None

# --- AC4: miromind SSE path raises ToolCallingUnsupported -------------------
_orig_proto = client.PROTOCOL
try:
    client.PROTOCOL = "miromind-sse"
    check("AC4 tool_calling_capable() False on the miromind SSE path (no stub)",
          client.tool_calling_capable() is False)
    raised = False
    try:
        client.call_chat_tools([{"role": "user", "content": "x"}],
                               tools=[{"type": "function", "function": {"name": "f"}}])
    except client.ToolCallingUnsupported:
        raised = True
    check("AC4 call_chat_tools raises ToolCallingUnsupported on miromind", raised)
finally:
    client.PROTOCOL = _orig_proto

print("=" * 72)
print(f"RESULT: {_passed} passed, {_failed} failed")
print("=" * 72)
raise SystemExit(1 if _failed else 0)
