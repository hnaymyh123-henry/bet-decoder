"""Agent activity stream (Module 5) — event protocol + SSE pipeline + replay.

Cross-cutting infrastructure that makes the agent's *process* visible. It is the
"How" primitive (BetCard answers "What"). The decoder (`decode_bet`) and the
synthesizer (`synthesize_cards`) already accept an ``emit(event)`` callback and
fire decision-grade reasoning steps through it. This module supplies the *sink*
that those callbacks feed into:

  raw emit dict  ──►  normalize → ActivityEvent  ──►  (a) live push to listeners
                                                      (b) buffer for persistence
                                                      (c) terminal done/error

Design contract (see API_CONTRACT.md §4 + PRD 模块 5):

  ActivityEvent = {
    job_id, seq, t_offset_ms,
    source: { kind: "decode"|"synthesis", card_id?, card_ids?, subject },
    phase, kind: "decision"|"computation"|"evidence"|"relation",
    text, payload, terminal: None|"done"|"error"
  }

The decoder/synthesizer emit dicts carry only the *semantic* part
(phase/kind/text/source/payload). The sink is what assigns ``seq`` (monotonic
per job) and ``t_offset_ms`` (ms since the job started), and what appends the
single terminal event. So the engines never need to know about timing, ordering,
or persistence — exactly the separation the AC asks for.

Persistence: one row per job in ``activity_logs`` (``events_json`` blob = JSON
array of the full ActivityEvent list). We write that table with plain SQL so we
do NOT touch ``db.py``'s schema (table already exists from M1) — only its data.

Concurrency: a process-wide serial queue (``JobQueue``) ensures the feed only
ever plays ONE coherent sequence at a time. A second job submitted while one is
running waits its turn (FIFO).

Cost: this is a pure pipeline. It NEVER calls a real LLM. Tests drive
``decode_bet``/``synthesize_cards`` with stubs and assert on event shape.
"""
from __future__ import annotations

import asyncio
import json
import queue
import threading
import time
import uuid
from typing import Any, Callable, Iterable

# Canonical kind vocabulary (decision-level semantic steps + a tag).
KIND_DECISION = "decision"
KIND_COMPUTATION = "computation"
KIND_EVIDENCE = "evidence"
KIND_RELATION = "relation"
_VALID_KINDS = {KIND_DECISION, KIND_COMPUTATION, KIND_EVIDENCE, KIND_RELATION}

# source.kind vocabulary (which engine produced the event).
SRC_DECODE = "decode"
SRC_SYNTHESIS = "synthesis"

# Terminal states. Every job ends with exactly one terminal event.
TERMINAL_DONE = "done"
TERMINAL_ERROR = "error"


# ---------------------------------------------------------------------------
# Event normalization — turn a raw engine emit dict into a canonical
# ActivityEvent. Tolerant of garbage: a bad event must never crash the sink.
# ---------------------------------------------------------------------------

def _coerce_source(raw_source: Any) -> dict:
    """Normalize the ``source`` sub-object. Always returns a dict with at least
    ``kind`` + ``subject``; preserves ``card_id`` / ``card_ids`` when present."""
    if not isinstance(raw_source, dict):
        return {"kind": SRC_DECODE, "subject": "?"}
    kind = raw_source.get("kind")
    if kind not in (SRC_DECODE, SRC_SYNTHESIS):
        # Infer: card_ids (plural) ⇒ synthesis; otherwise decode.
        kind = SRC_SYNTHESIS if raw_source.get("card_ids") is not None else SRC_DECODE
    out: dict = {"kind": kind, "subject": str(raw_source.get("subject", "?"))}
    if raw_source.get("card_id") is not None:
        out["card_id"] = raw_source["card_id"]
    if raw_source.get("card_ids") is not None:
        cids = raw_source["card_ids"]
        out["card_ids"] = list(cids) if isinstance(cids, (list, tuple)) else [cids]
    return out


def normalize_event(raw: Any, *, job_id: str, seq: int, t_offset_ms: int,
                    terminal: str | None = None) -> dict:
    """Coerce one raw emit dict into a canonical ActivityEvent.

    Missing / wrong-typed fields get safe defaults so a broken engine event can
    never break the stream. ``seq`` and ``t_offset_ms`` are supplied by the sink
    (the engines do not know them). ``terminal`` is set only for the closing
    event the orchestrator appends.
    """
    if not isinstance(raw, dict):
        raw = {"text": str(raw)} if raw is not None else {}

    kind = raw.get("kind")
    if kind not in _VALID_KINDS:
        kind = KIND_DECISION  # default to the broadest semantic bucket

    payload = raw.get("payload")
    if payload is not None and not isinstance(payload, dict):
        payload = {"value": payload}

    return {
        "job_id": job_id,
        "seq": int(seq),
        "t_offset_ms": int(t_offset_ms),
        "source": _coerce_source(raw.get("source")),
        "phase": str(raw.get("phase", "")),
        "kind": kind,
        "text": str(raw.get("text", "")),
        "payload": payload,
        "terminal": terminal,
    }


# ---------------------------------------------------------------------------
# Persistence DAO — write/read the activity_logs table with plain SQL.
# We deliberately do NOT add these to db.py: the table is owned by M1's schema
# and the instruction is to write it, not redefine it. Keeping the SQL here
# means db.py's schema stays byte-for-byte unchanged.
# ---------------------------------------------------------------------------

def save_activity_log(conn, job_id: str, source_ref: str | None,
                      events: list[dict]) -> None:
    """Upsert the full event list for ``job_id`` into ``activity_logs``.

    ``events_json`` is a JSON array (the replay blob). Idempotent: re-saving the
    same job overwrites with the latest buffer (so a job can persist
    incrementally and the final write wins)."""
    blob = json.dumps(events, ensure_ascii=False)
    with conn:
        conn.execute(
            """
            INSERT INTO activity_logs (job_id, source_ref, events_json, created_at)
            VALUES (?, ?, ?, datetime('now'))
            ON CONFLICT(job_id) DO UPDATE SET
                source_ref  = excluded.source_ref,
                events_json = excluded.events_json
            """,
            (job_id, source_ref, blob),
        )


def get_activity_log(conn, job_id: str) -> list[dict] | None:
    """Read back the event list for ``job_id``. ``None`` if no such job;
    ``[]`` if the row exists but stored an empty / unparseable blob."""
    row = conn.execute(
        "SELECT events_json FROM activity_logs WHERE job_id = ?", (job_id,)
    ).fetchone()
    if row is None:
        return None
    raw = row["events_json"] if not isinstance(row, tuple) else row[0]
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    return data if isinstance(data, list) else []


# ---------------------------------------------------------------------------
# ActivitySink — the emit callback factory.
# Receives raw engine events, stamps seq + t_offset_ms, normalizes, buffers,
# and (optionally) pushes to a live listener. The orchestrator appends the
# terminal event and persists the buffer.
# ---------------------------------------------------------------------------

class ActivitySink:
    """A stateful emit sink for one job.

    Use as ``emit=sink`` when calling ``decode_bet`` / ``synthesize_cards``.
    Each engine event becomes a canonical ActivityEvent with a monotonic ``seq``
    and a wall-clock ``t_offset_ms`` measured from sink creation.
    """

    def __init__(self, job_id: str, *, source_ref: str | None = None,
                 on_event: Callable[[dict], None] | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.job_id = job_id
        self.source_ref = source_ref
        self.events: list[dict] = []
        self._seq = 0
        self._on_event = on_event
        self._clock = clock
        self._t0 = clock()
        self._closed = False
        # Guards _seq / events / _closed. The engine may emit from a worker
        # thread while another thread reads sink.events; without this lock seq
        # assignment + append could race (duplicate/missing seq, torn reads).
        self._lock = threading.Lock()

    # The emit callback the engines call: sink(raw_event_dict).
    def __call__(self, raw: Any) -> None:
        # Build + record the event under the lock so seq is unique & monotonic
        # and the append is atomic. Notify OUTSIDE the lock so a slow listener
        # can't serialize the producing engine (and can't deadlock if it calls
        # back into the sink).
        with self._lock:
            if self._closed:
                return  # never accept events after the terminal one
            ev = normalize_event(
                raw, job_id=self.job_id, seq=self._seq,
                t_offset_ms=int((self._clock() - self._t0) * 1000),
            )
            self._seq += 1
            self.events.append(ev)
        self._notify(ev)

    def terminal(self, status: str, text: str, payload: dict | None = None) -> dict:
        """Append the single closing event (``done`` or ``error``) and freeze
        the sink. Returns the terminal event."""
        with self._lock:
            ev = normalize_event(
                {"phase": status, "kind": KIND_DECISION, "text": text,
                 "source": {"kind": SRC_DECODE, "subject": self.source_ref or "?"},
                 "payload": payload},
                job_id=self.job_id, seq=self._seq,
                t_offset_ms=int((self._clock() - self._t0) * 1000),
                terminal=status,
            )
            self._seq += 1
            self.events.append(ev)
            self._closed = True
        self._notify(ev)
        return ev

    def _notify(self, ev: dict) -> None:
        if self._on_event is None:
            return
        try:
            self._on_event(ev)
        except Exception:
            pass  # a broken listener must not break the producing engine


# ---------------------------------------------------------------------------
# Job runner — wraps an engine call in a sink, guarantees a terminal event,
# and persists the buffer. This is the "live, persisted, replayable" path.
# ---------------------------------------------------------------------------

def run_job(work: Callable[..., Any],
            *,
            job_id: str | None = None,
            source_ref: str | None = None,
            conn=None,
            conn_factory: Callable[[], Any] | None = None,
            cancel_event: "threading.Event | None" = None,
            on_event: Callable[[dict], None] | None = None,
            done_text: str = "完成",
            clock: Callable[[], float] = time.monotonic) -> dict:
    """Run ``work(emit)`` under an ActivitySink, append a terminal event, and
    persist all events to ``activity_logs``.

    ``work`` is any callable that takes the emit sink and does the real job, e.g.
    ``lambda emit: decode_bet("market", "NVDA", "zh", emit=emit, ...)``. If
    ``cancel_event`` is given and ``work`` accepts a ``cancel`` keyword, it is
    forwarded so the engine can stop early when the client disconnects.

    Persistence (two mutually-exclusive options):
      - ``conn``         : an already-open connection. Used directly. Correct
                           only when run_job executes on the SAME thread that
                           created ``conn`` (sqlite binds a connection to its
                           creating thread). This is the ``/api/decode`` + test
                           path (run_job called inline on the request thread).
      - ``conn_factory`` : a thread-safe callable returning a FRESH connection.
                           run_job opens it on the CURRENT thread and closes it
                           when done. This is the path the background-thread
                           runner uses (``live_activity_stream``) so it never
                           reuses a connection created on the event-loop thread —
                           the cross-thread bug that silently dropped every live
                           activity_logs write (sqlite check_same_thread).

    On success → terminal ``done``. On any exception → terminal ``error`` with a
    human-readable reason. The exception is swallowed so the pipeline degrades
    gracefully; the result is surfaced via ``info["result"]`` / ``info["error"]``.

    Returns ``{job_id, events, result, error, terminal}``.
    """
    job_id = job_id or uuid.uuid4().hex
    sink = ActivitySink(job_id, source_ref=source_ref, on_event=on_event, clock=clock)

    result: Any = None
    error: str | None = None
    try:
        result = _call_work(work, sink, cancel_event)
        term = sink.terminal(TERMINAL_DONE, done_text)
    except Exception as exc:  # honest human reason, never re-raised
        error = f"{type(exc).__name__}: {exc}"
        term = sink.terminal(TERMINAL_ERROR, f"任务失败:{error}",
                             payload={"error": error})

    # Persist. A factory wins (own thread-local connection, closed here); else
    # fall back to the passed-in same-thread connection.
    if conn_factory is not None:
        own = None
        try:
            own = conn_factory()
            save_activity_log(own, job_id, source_ref, sink.events)
        except Exception:
            pass  # persistence failure must not lose the live stream
        finally:
            if own is not None:
                try:
                    own.close()
                except Exception:
                    pass
    elif conn is not None:
        try:
            save_activity_log(conn, job_id, source_ref, sink.events)
        except Exception:
            pass  # persistence failure must not lose the live stream

    return {
        "job_id": job_id,
        "events": sink.events,
        "result": result,
        "error": error,
        "terminal": term,
    }


def _call_work(work: Callable[..., Any], sink: "ActivitySink",
               cancel_event: "threading.Event | None") -> Any:
    """Invoke ``work(emit)``, forwarding ``cancel=cancel_event`` only if the
    callable advertises a ``cancel`` parameter. Keeps backward compatibility
    with the many ``lambda emit: ...`` callers that take a single arg."""
    if cancel_event is not None:
        try:
            import inspect
            params = inspect.signature(work).parameters
            if "cancel" in params or any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
            ):
                return work(sink, cancel=cancel_event)
        except (TypeError, ValueError):
            pass  # un-introspectable callable → fall through to single-arg call
    return work(sink)


# ---------------------------------------------------------------------------
# Serial job queue — the feed plays one coherent sequence at a time.
# A second job submitted while one runs is queued (FIFO) and runs after.
# ---------------------------------------------------------------------------

class JobQueue:
    """Process-wide serial executor for activity jobs.

    Jobs run one-at-a-time on a single worker thread; concurrent ``submit``
    calls enqueue and execute in FIFO order. This guarantees the unified
    workbench feed never interleaves two jobs.
    """

    def __init__(self, *, auto_start: bool = True):
        self._q: "queue.Queue[tuple]" = queue.Queue()
        self._results: dict[str, dict] = {}
        self._results_lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._running = False
        self._idle = threading.Event()
        self._idle.set()
        if auto_start:
            self.start()

    def start(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        self._running = True
        self._worker = threading.Thread(target=self._loop, daemon=True)
        self._worker.start()

    def submit(self, work: Callable[..., Any], *,
               on_complete: Callable[[dict], None] | None = None,
               **run_kwargs) -> str:
        """Enqueue a job. Returns its ``job_id`` immediately (job may still be
        queued). ``run_kwargs`` are forwarded to ``run_job``. ``on_complete`` (if
        given) is invoked with the job's ``info`` dict on the worker thread right
        after ``run_job`` returns — used by the live SSE stream to unblock its
        consumer even on the failure/empty path."""
        job_id = run_kwargs.get("job_id") or uuid.uuid4().hex
        run_kwargs["job_id"] = job_id
        self._idle.clear()
        self._q.put((work, run_kwargs, on_complete))
        return job_id

    def _loop(self) -> None:
        while self._running:
            try:
                work, run_kwargs, on_complete = self._q.get(timeout=0.1)
            except queue.Empty:
                if self._q.empty():
                    self._idle.set()
                continue
            try:
                info = run_job(work, **run_kwargs)
                with self._results_lock:
                    self._results[info["job_id"]] = info
                if on_complete is not None:
                    try:
                        on_complete(info)
                    except Exception:
                        pass  # a broken completion hook must not kill the worker
            finally:
                self._q.task_done()
                if self._q.empty():
                    self._idle.set()

    def result(self, job_id: str) -> dict | None:
        with self._results_lock:
            return self._results.get(job_id)

    def wait_idle(self, timeout: float | None = None) -> bool:
        """Block until the queue has drained (all jobs done). Test helper."""
        return self._idle.wait(timeout=timeout)

    def stop(self) -> None:
        self._running = False
        if self._worker is not None:
            self._worker.join(timeout=1.0)


# A lazily-created default queue the API uses so the live feed is serialized.
_DEFAULT_QUEUE: JobQueue | None = None


def default_queue() -> JobQueue:
    global _DEFAULT_QUEUE
    if _DEFAULT_QUEUE is None:
        _DEFAULT_QUEUE = JobQueue()
    return _DEFAULT_QUEUE


# ---------------------------------------------------------------------------
# Replay — re-emit a stored event list honoring the original timing.
# ---------------------------------------------------------------------------

def replay_events(events: Iterable[dict] | None,
                  on_event: Callable[[dict], None],
                  *,
                  speed: float = 1.0,
                  max_gap_ms: int | None = None,
                  sleep: Callable[[float], None] = time.sleep) -> list[dict]:
    """Re-play ``events`` in stored order, sleeping between them so the inter-
    event gaps match the original ``t_offset_ms`` deltas (scaled by ``speed``).

    - ``speed`` > 1 plays faster; ``speed=0`` (or negative) plays with no waits.
    - ``max_gap_ms`` caps any single wait (so a long real gap doesn't stall a
      demo). ``None`` = no cap.
    - Order is preserved by ``seq`` when present, else stored order.
    - Tolerant: ``None`` / ``[]`` ⇒ no-op, returns ``[]``. Bad/missing offsets
      are treated as 0.

    Returns the list it replayed (in replay order), so callers can assert the
    sequence is identical to the original.
    """
    if not events:
        return []
    ordered = sorted(
        events,
        key=lambda e: e.get("seq", 0) if isinstance(e, dict) else 0,
    )
    replayed: list[dict] = []
    prev_off = None
    for ev in ordered:
        off = ev.get("t_offset_ms", 0) if isinstance(ev, dict) else 0
        try:
            off = int(off)
        except (TypeError, ValueError):
            off = 0
        if prev_off is not None and speed > 0:
            gap_ms = max(off - prev_off, 0)
            if max_gap_ms is not None:
                gap_ms = min(gap_ms, max_gap_ms)
            wait_s = (gap_ms / 1000.0) / speed
            if wait_s > 0:
                sleep(wait_s)
        prev_off = off
        try:
            on_event(ev)
        except Exception:
            pass  # a broken consumer must not abort the replay
        replayed.append(ev)
    return replayed


# ---------------------------------------------------------------------------
# SSE framing.
# Bug #34 fix lives here + in api.py: each event is a self-contained SSE frame
# terminated by a BLANK line ("\n\n"). The previous mock used a multi-field
# frame (``event:`` + ``data:``) which is fine, but the live activity stream
# emits ONE JSON object per frame on the default ``message`` event so a plain
# ``new EventSource().onmessage`` receives every event — no per-type listener
# needed. Crucially the frame must end with a blank line and the server must
# FLUSH after every frame (StreamingResponse + an async generator that yields
# each frame separately does this; see api.py). The earlier hang after
# ``agent_step`` was an under-flushed / mis-framed stream.
# ---------------------------------------------------------------------------

def format_sse(event: dict, *, event_name: str | None = None) -> str:
    """Render one ActivityEvent as an SSE frame: ``data: {json}\\n\\n``.

    A terminal event additionally carries ``event: done|error`` so a client can
    listen for it explicitly, but the JSON ``terminal`` field is authoritative."""
    line = f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
    name = event_name
    if name is None and isinstance(event, dict) and event.get("terminal"):
        name = event["terminal"]
    if name:
        return f"event: {name}\n{line}"
    return line


def sse_comment(text: str = "") -> str:
    """An SSE comment line (``: ...\\n\\n``) — used as a keep-alive / initial
    flush primer so proxies open the stream immediately."""
    return f": {text}\n\n"


# ---------------------------------------------------------------------------
# Async SSE generators for the API layer.
# ---------------------------------------------------------------------------

async def live_activity_stream(work: Callable[..., Any],
                               *,
                               job_id: str,
                               source_ref: str | None = None,
                               conn=None,
                               conn_factory: Callable[[], Any] | None = None,
                               done_text: str = "完成",
                               job_queue: "JobQueue | None" = None):
    """Async generator: run ``work`` through the serial JobQueue under a sink and
    yield each ActivityEvent as an SSE frame as it is produced, ending after the
    terminal frame. Explicitly flushes per frame (one ``yield`` per frame).

    Serialization (bug-2 fix): the job is submitted to a ``JobQueue`` (the
    process-wide ``default_queue()`` unless one is passed). The queue runs jobs
    one-at-a-time on a single worker thread, so two concurrent live requests can
    NOT burn the LLM in parallel — the second waits for the first to finish.
    The SSE generator subscribes to its job's events via an ``on_event`` bridge
    that hops each event back to the event loop.

    Persistence (bug-1 fix): the queue worker thread persists via ``conn_factory``
    (its OWN fresh connection), never a connection created on the event-loop
    thread — that cross-thread reuse is what silently dropped every live
    ``activity_logs`` write under sqlite ``check_same_thread``.

    Cancellation (bug-5 fix): a ``cancel_event`` is created and forwarded to the
    engine (when ``work`` accepts ``cancel=``). If the client disconnects, the
    async generator is closed → our ``finally`` sets the event so the engine
    stops issuing new LLM calls instead of running to completion unwatched.

    Bug #34 (kept): every event is its own flushed frame and the terminal event
    reliably closes the stream.
    """
    loop = asyncio.get_event_loop()
    aq: asyncio.Queue = asyncio.Queue()
    _SENTINEL = object()
    cancel_event = threading.Event()
    q = job_queue or default_queue()

    def on_event(ev: dict) -> None:
        # Called from the queue worker thread; hop back to the loop thread.
        loop.call_soon_threadsafe(aq.put_nowait, ev)

    def on_done(_info: dict) -> None:
        # Fired by the queue after run_job returns; unblock the generator.
        loop.call_soon_threadsafe(aq.put_nowait, _SENTINEL)

    # Prime the stream so the client/proxy opens it right away.
    yield sse_comment("activity-stream-open")

    q.submit(
        work, job_id=job_id, source_ref=source_ref,
        conn_factory=conn_factory, conn=None if conn_factory else conn,
        cancel_event=cancel_event, on_event=on_event, done_text=done_text,
        on_complete=on_done,
    )

    try:
        while True:
            ev = await aq.get()
            if ev is _SENTINEL:
                break
            yield format_sse(ev)
            if ev.get("terminal"):
                # Terminal delivered; stop. (on_done sentinel, if it arrives
                # after, is harmless — the queue/aq are local to this request.)
                break
    finally:
        # Client disconnect or normal close → signal the engine to stop making
        # new LLM calls. Harmless if the job already finished.
        cancel_event.set()


async def replay_activity_stream(events: list[dict] | None,
                                 *,
                                 speed: float = 1.0,
                                 max_gap_ms: int | None = 2000):
    """Async generator: replay a stored event list as SSE frames, honoring the
    original inter-event timing (scaled by ``speed``, each gap capped at
    ``max_gap_ms``). Empty / missing ⇒ emits only the open primer + a synthetic
    error terminal so the client never hangs."""
    yield sse_comment("activity-replay-open")
    if not events:
        # Honest empty: tell the client there is nothing, with a terminal so the
        # front end stops waiting.
        empty_term = {
            "job_id": "", "seq": 0, "t_offset_ms": 0,
            "source": {"kind": SRC_DECODE, "subject": "?"},
            "phase": "empty", "kind": KIND_DECISION,
            "text": "无可回放事件", "payload": None, "terminal": TERMINAL_ERROR,
        }
        yield format_sse(empty_term)
        return

    ordered = sorted(events, key=lambda e: e.get("seq", 0) if isinstance(e, dict) else 0)
    prev_off = None
    for ev in ordered:
        off = ev.get("t_offset_ms", 0) if isinstance(ev, dict) else 0
        try:
            off = int(off)
        except (TypeError, ValueError):
            off = 0
        if prev_off is not None and speed > 0:
            gap_ms = max(off - prev_off, 0)
            if max_gap_ms is not None:
                gap_ms = min(gap_ms, max_gap_ms)
            wait_s = (gap_ms / 1000.0) / speed
            if wait_s > 0:
                await asyncio.sleep(wait_s)
        prev_off = off
        yield format_sse(ev)
