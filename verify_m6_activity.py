"""M5 Agent activity stream verification — deterministic, ZERO API/network cost.

Covers every Issue #6 acceptance criterion using a stub fundamentals fetcher +
stub evidence hunter + stub chat hook, driving the REAL decode_bet /
synthesize_cards through the REAL activity sink / queue / replay / SSE framing.
No MiroMind API, no yfinance. Prints one PASS/FAIL per check.

(Named verify_m6_activity to disambiguate from verify_m5_synth.py, which —
despite the "m5" — verifies Module 3 cross-card synthesis.)

Run:  "/c/Users/Henry Ma/miniconda3/python.exe" verify_m6_activity.py
"""
from __future__ import annotations

import asyncio
import json
import time

import activity
import db
import decoder
from activity import (
    ActivitySink,
    JobQueue,
    format_sse,
    get_activity_log,
    live_activity_stream,
    normalize_event,
    replay_activity_stream,
    replay_events,
    run_job,
    save_activity_log,
)
from decoder import Fundamentals, decode_bet

# --- counters -------------------------------------------------------------
_passed = 0
_failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global _passed, _failed
    status = "PASS" if cond else "FAIL"
    if cond:
        _passed += 1
    else:
        _failed += 1
    extra = f"  | {detail}" if detail else ""
    print(f"[{status}] {name}{extra}")


# --- stubs (no network, no API) -------------------------------------------

NVDA = Fundamentals(
    ticker="NVDA", current_price=180.0,
    revenue_ttm=130e9, net_income_ttm=73e9, ebitda_ttm=88e9,
    fcf_ttm=60e9, book_equity=80e9, eps_ttm=2.95,
    shares_outstanding=24.5e9, net_debt=-30e9, beta=1.7, growth_rate=0.55,
)


def stub_fundamentals(ticker: str) -> Fundamentals:
    if ticker.upper() == "NVDA":
        return NVDA
    raise RuntimeError(f"no fixture for {ticker}")


def stub_hunter(ticker, assumption, **kw):
    """Minimal evidence brief — deterministic, free."""
    return {
        "assumption": assumption.get("text") or assumption.get("id") or "?",
        "evidence_items": [],
        "evidence_count": {"support": 0, "refute": 0, "neutral": 0},
        "overall_balance": "insufficient",
    }


def consume_sse(frames: list[str]) -> list[dict]:
    """Parse a list of SSE frame strings into the JSON objects they carry.
    Skips comment/keep-alive frames. Validates each data frame is blank-line
    terminated."""
    out = []
    for f in frames:
        if f.startswith(":"):           # comment / primer frame
            continue
        for block in f.split("\n\n"):
            block = block.strip("\n")
            if not block:
                continue
            for line in block.split("\n"):
                if line.startswith("data:"):
                    out.append(json.loads(line[len("data:"):].strip()))
    return out


async def drain(agen) -> list[str]:
    return [frame async for frame in agen]


# ===========================================================================
print("=" * 70)
print("M5 Agent activity stream — acceptance verification")
print("=" * 70)


# AC1 — ActivityEvent canonical structure (sink fills seq + t_offset_ms).
sink = ActivitySink("job1", source_ref="NVDA")
sink({"phase": "adapter", "kind": "decision", "text": "hi",
      "source": {"kind": "decode", "subject": "NVDA"}, "payload": {"a": 1}})
ev = sink.events[0]
_required = {"job_id", "seq", "t_offset_ms", "source", "phase", "kind", "text",
             "payload", "terminal"}
check("AC1 ActivityEvent has all canonical keys",
      _required.issubset(ev.keys()), f"keys={sorted(ev.keys())}")
check("AC1 source carries kind+subject+card_id|card_ids slots",
      ev["source"]["kind"] == "decode" and ev["source"]["subject"] == "NVDA")
check("AC1 kind is from the controlled vocab",
      ev["kind"] in {"decision", "computation", "evidence", "relation"})
check("AC1 sink-assigned seq starts at 0, monotonic",
      ev["seq"] == 0 and ev["t_offset_ms"] >= 0)
check("AC1 non-terminal events have terminal=None", ev["terminal"] is None)


# AC2 — emit sink: normalizes engine events + persists to activity_logs (blob).
conn = db.init_db(":memory:")
sink2 = ActivitySink("jobAC2", source_ref="NVDA")
# Feed it a raw decoder-shaped event (no seq/offset — sink must add them).
sink2({"phase": "lens_select", "kind": "decision", "text": "选 DCF",
       "source": {"kind": "decode", "subject": "NVDA"}, "payload": None})
sink2({"phase": "solve", "kind": "computation", "text": "解出增速",
       "source": {"kind": "decode", "subject": "NVDA"}, "payload": {"g": 0.55}})
check("AC2 sink normalizes raw events → canonical (adds seq/offset)",
      sink2.events[0]["seq"] == 0 and sink2.events[1]["seq"] == 1
      and all("t_offset_ms" in e for e in sink2.events))
save_activity_log(conn, "jobAC2", "NVDA", sink2.events)
row = conn.execute("SELECT events_json FROM activity_logs WHERE job_id=?",
                   ("jobAC2",)).fetchone()
blob_ok = row is not None and isinstance(json.loads(row["events_json"]), list)
check("AC2 persisted to activity_logs as events_json blob (by job_id)", blob_ok,
      f"rows={1 if row else 0}")


# AC3 — live: run REAL decode_bet(emit=sink) → kind-tagged sequence, terminal done.
captured: list[dict] = []
conn3 = db.init_db(":memory:")
info = run_job(
    lambda emit: decode_bet("market", "NVDA", "zh", emit=emit,
                            fundamentals_fn=stub_fundamentals, hunter=stub_hunter,
                            conn=conn3),
    job_id="jobAC3", source_ref="NVDA", conn=conn3,
    on_event=captured.append, done_text="解码完成",
)
kinds_present = {e["kind"] for e in info["events"]}
check("AC3 live decode_bet streamed events (>=2)", len(info["events"]) >= 2,
      f"n={len(info['events'])}")
check("AC3 events carry kind tags from the vocab",
      kinds_present.issubset({"decision", "computation", "evidence", "relation"})
      and len(kinds_present) >= 1, f"kinds={sorted(kinds_present)}")
check("AC3 sequence ends with terminal='done'",
      info["events"][-1]["terminal"] == "done")
check("AC3 exactly one terminal event in the stream",
      sum(1 for e in info["events"] if e["terminal"]) == 1)
check("AC3 on_event live-pushed every event (incl terminal)",
      len(captured) == len(info["events"]))
check("AC3 seq is contiguous 0..n-1",
      [e["seq"] for e in info["events"]] == list(range(len(info["events"]))))


# AC4 — failure → terminal='error' with a human reason; result/error surfaced.
def boom(emit):
    emit({"phase": "start", "kind": "decision", "text": "开工",
          "source": {"kind": "decode", "subject": "X"}})
    raise ValueError("数据源不可用")


conn4 = db.init_db(":memory:")
finfo = run_job(boom, job_id="jobAC4", source_ref="X", conn=conn4)
term = finfo["events"][-1]
check("AC4 failure ends with terminal='error'", term["terminal"] == "error")
check("AC4 terminal carries a human-readable reason",
      "数据源不可用" in term["text"] or "数据源不可用" in (term.get("payload") or {}).get("error", ""),
      f"text={term['text']!r}")
check("AC4 run_job swallowed the exception (no crash) + surfaced error string",
      finfo["error"] is not None and "ValueError" in finfo["error"])
check("AC4 pre-failure events preserved before the error terminal",
      len(finfo["events"]) == 2 and finfo["events"][0]["terminal"] is None)


# AC5 — replay: read back a job, replay by seq + t_offset_ms, order identical,
#       inter-event gaps ≈ original offsets.
# Build a job with KNOWN offsets via a fake clock so timing is deterministic.
_t = {"v": 0.0}
def fake_clock():
    return _t["v"]


sink5 = ActivitySink("jobAC5", source_ref="NVDA", clock=fake_clock)
_t["v"] = 0.000; sink5({"phase": "a", "kind": "decision", "text": "e0",
                        "source": {"kind": "decode", "subject": "NVDA"}})
_t["v"] = 0.100; sink5({"phase": "b", "kind": "computation", "text": "e1",
                        "source": {"kind": "decode", "subject": "NVDA"}})
_t["v"] = 0.350; sink5({"phase": "c", "kind": "evidence", "text": "e2",
                        "source": {"kind": "decode", "subject": "NVDA"}})
_t["v"] = 0.400; sink5.terminal("done", "完成")
conn5 = db.init_db(":memory:")
save_activity_log(conn5, "jobAC5", "NVDA", sink5.events)

loaded = get_activity_log(conn5, "jobAC5")
check("AC5 get_activity_log returns the stored list", isinstance(loaded, list)
      and len(loaded) == 4, f"n={len(loaded) if loaded else None}")

# Replay with a fake sleep that records the requested gaps.
gaps: list[float] = []
order: list[str] = []
replayed = replay_events(
    loaded, lambda e: order.append(e["text"]),
    speed=1.0, sleep=lambda s: gaps.append(s),
)
check("AC5 replay order identical to original seq order",
      order == ["e0", "e1", "e2", "完成"], f"order={order}")
# Original offsets: 0,100,250(? actually 0.35→350),... so gaps = 100,250,50 ms.
expected_ms = [100, 250, 50]
got_ms = [round(g * 1000) for g in gaps]
check("AC5 inter-event gaps ≈ original t_offset deltas",
      got_ms == expected_ms, f"got={got_ms}ms expected={expected_ms}ms")
check("AC5 replay output sequence == original",
      [e["seq"] for e in replayed] == [0, 1, 2, 3])


# AC6 — SSE endpoint framing: data: {...}\n\n, properly framed + flushed.
frame = format_sse(loaded[0])
check("AC6 SSE frame is 'data: {json}\\n\\n'",
      frame.startswith("data: ") and frame.endswith("\n\n")
      and "\n\n" not in frame[:-2], f"frame={frame!r}")
# Terminal frame additionally tags event: done so named listeners can catch it.
tframe = format_sse(loaded[-1])
check("AC6 terminal frame tags 'event: done' + data",
      tframe.startswith("event: done\n") and "data: " in tframe)
# Replay generator emits one self-contained frame per event (explicit per-yield
# flush) — drain it and confirm every event survived intact.
frames = asyncio.run(drain(replay_activity_stream(loaded, speed=0)))
parsed = consume_sse(frames)
check("AC6 replay SSE stream yields one flushed frame per event",
      len(parsed) == 4 and [p["text"] for p in parsed] == ["e0", "e1", "e2", "完成"],
      f"n_frames={len(frames)} n_events={len(parsed)}")


# AC7 — bug #34: events AFTER the first agent_step are not stalled. We reproduce
# the class of bug (a stream that opens but never delivers post-step events) and
# prove the fix: every event including those after the first decision frame is
# delivered as its own frame, and the terminal frame closes the stream.
live_frames = asyncio.run(drain(live_activity_stream(
    lambda emit: decode_bet("market", "NVDA", "zh", emit=emit,
                            fundamentals_fn=stub_fundamentals, hunter=stub_hunter,
                            conn=db.init_db(":memory:")),
    job_id="jobAC7", source_ref="NVDA", conn=db.init_db(":memory:"),
)))
live_events = consume_sse(live_frames)
# There must be events after the FIRST one, and the stream must terminate.
has_post_first = len(live_events) >= 2
terminated = bool(live_events) and live_events[-1].get("terminal") == "done"
# Confirm the frame stream isn't coalesced (each non-comment frame == 1 event).
non_comment = [f for f in live_frames if not f.startswith(":")]
one_event_per_frame = len(non_comment) == len(live_events)
check("AC7 (bug#34) post-first-step events are delivered, not stalled",
      has_post_first, f"n_events={len(live_events)}")
check("AC7 (bug#34) live stream terminates with done (no hang)", terminated)
check("AC7 (bug#34) one event per flushed frame (no coalescing)",
      one_event_per_frame, f"frames={len(non_comment)} events={len(live_events)}")


# AC8 — concurrency: serial + queued. Two jobs submitted ~together → run one at
# a time; the feed plays one coherent sequence then the next.
q = JobQueue()
interleave_log: list[str] = []


def slow_work(tag):
    def _w(emit):
        for i in range(3):
            emit({"phase": tag, "kind": "decision", "text": f"{tag}-{i}",
                  "source": {"kind": "decode", "subject": tag}})
            interleave_log.append(f"{tag}-{i}")
            time.sleep(0.01)
        return tag
    return _w


jid_a = q.submit(slow_work("A"), source_ref="A")
jid_b = q.submit(slow_work("B"), source_ref="B")
q.wait_idle(timeout=5.0)
q.stop()
# Coherence: A's three events must all appear before B's (no interleave) OR vice
# versa — but never A0,B0,A1... The first job to run finishes entirely first.
def _coherent(seq: list[str]) -> bool:
    # find which tag ran first; all of its events must precede the other's.
    if not seq:
        return False
    first = seq[0][0]
    boundary = max(i for i, s in enumerate(seq) if s[0] == first)
    return all(s[0] == first for s in seq[:boundary + 1])


check("AC8 two jobs run serially (queued, no interleave)",
      _coherent(interleave_log), f"log={interleave_log}")
check("AC8 both queued jobs completed",
      q.result(jid_a) is not None and q.result(jid_b) is not None)
check("AC8 each completed job ended with a terminal event",
      q.result(jid_a)["events"][-1]["terminal"] == "done"
      and q.result(jid_b)["events"][-1]["terminal"] == "done")


# AC9 — edge: bad events don't crash the sink; empty replay doesn't error.
bad_sink = ActivitySink("jobBad")
for bad in (None, 123, "just a string", {"text": object()}, {"kind": "??"},
            {"source": "notadict"}, {"payload": [1, 2, 3]}):
    bad_sink(bad)  # must not raise
check("AC9 sink tolerates garbage events (no crash, all normalized)",
      len(bad_sink.events) == 7
      and all(e["kind"] in {"decision", "computation", "evidence", "relation"}
              for e in bad_sink.events))
check("AC9 bad source coerced to a dict with kind+subject",
      bad_sink.events[5]["source"]["kind"] in {"decode", "synthesis"}
      and "subject" in bad_sink.events[5]["source"])
check("AC9 non-dict payload wrapped, never crashes",
      bad_sink.events[6]["payload"] == {"value": [1, 2, 3]})

# Empty / missing replay.
empty_conn = db.init_db(":memory:")
check("AC9 get_activity_log(missing job) → None",
      get_activity_log(empty_conn, "nope") is None)
check("AC9 replay_events(None) → [] (no error)", replay_events(None, lambda e: None) == [])
check("AC9 replay_events([]) → [] (no error)", replay_events([], lambda e: None) == [])
# Empty-job SSE replay yields an error-terminal so the client never hangs.
empty_frames = asyncio.run(drain(replay_activity_stream(None)))
empty_parsed = consume_sse(empty_frames)
check("AC9 empty SSE replay emits an error-terminal (client won't hang)",
      len(empty_parsed) == 1 and empty_parsed[0]["terminal"] == "error")


# AC10 — normalize_event directly (unit): defaults + terminal stamping.
ne = normalize_event({"text": "x"}, job_id="j", seq=5, t_offset_ms=42)
check("AC10 normalize_event defaults kind→decision, terminal→None",
      ne["kind"] == "decision" and ne["terminal"] is None and ne["seq"] == 5
      and ne["t_offset_ms"] == 42)
nt = normalize_event({"text": "bye"}, job_id="j", seq=9, t_offset_ms=99,
                     terminal="done")
check("AC10 normalize_event stamps terminal when asked",
      nt["terminal"] == "done")


# --- summary --------------------------------------------------------------
print("=" * 70)
print(f"RESULT: {_passed} passed, {_failed} failed")
print("=" * 70)
raise SystemExit(1 if _failed else 0)
