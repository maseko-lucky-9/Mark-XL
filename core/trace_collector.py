"""Trace capture wrapper for WO-6: TraceStore + TraceCollector.

Gates on ``enable_trace_store`` AND :data:`WHEEL_AVAILABLE` — both are decided by
the *caller* (the flag bool is passed in; this module never reads a flag itself).

Design contract
---------------
* **No flag reads.** This module never calls the flag accessor; the token count
  for it in this file is zero by contract.  Whether tracing is on is the
  caller's decision, handed in as ``enable_trace_store``.
* **Zero import side effects.** Importing this module constructs no store, opens
  no database, and starts no thread.  The optional ``mark_xl_rust`` import is the
  only top-level work and it can never raise (ImportError is swallowed).
* **Store, not path, into the collector.** :class:`mark_xl_rust.TraceCollector`
  takes a live :class:`mark_xl_rust.TraceStore`, mirroring the wheel API
  ``TraceCollector(store)`` — never a filesystem path.
* **Single writer.** Every mutating call routes through
  :func:`core.store_executor.submit`, so all WO-6 store writes share the one
  ``max_workers=1`` writer pool and never contend on SQLite under WAL.

Trace / TraceStep JSON schema (verified against the wheel)
----------------------------------------------------------
A *step* dict MUST carry all six fields — the Rust deserialiser rejects a
partial step::

    {
        "step_type": "tool_call",      # snake_case: tool_call|generate|retrieve|route|respond
        "timestamp": 1.0,              # float, epoch seconds
        "duration_seconds": 0.0,       # float
        "input": {...},                # MAP/object — a bare string is rejected
        "output": {...},               # MAP/object — a bare string is rejected
        "metadata": {...}              # MAP/object
    }

A persisted *trace* dict (as returned by :meth:`TraceCapture.get`) uses the key
``trace_id`` (not ``id``) and carries::

    trace_id, query, agent, model, engine, steps, result, outcome,
    started_at, ended_at, total_tokens, total_latency_seconds, metadata
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Optional wheel import — never raises.  On a platform without the compiled
# ``mark_xl_rust`` wheel this module still imports cleanly with
# ``WHEEL_AVAILABLE = False`` and every TraceCapture method degrades to a no-op.
try:
    import mark_xl_rust as _rust

    WHEEL_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only on wheel-less platforms
    _rust = None
    WHEEL_AVAILABLE = False

# Imported for its single-writer ``submit`` and the WAL helper.  This import
# pulls in PyQt6 transitively but constructs no store, pool, or connection.
from core import store_executor as _store_executor_mod


def _trace_store_and_collector(db_path: str, enable_trace_store: bool):
    """Return ``(TraceStore, TraceCollector)`` or ``None``.  Never raises.

    Gated on ``enable_trace_store and WHEEL_AVAILABLE`` — both supplied by the
    caller (the flag) and the environment (the wheel).  On any failure
    (wheel-less, WAL refused, store construction error) this returns ``None`` so
    :class:`TraceCapture` falls back to a fully no-op instance.
    """
    if not enable_trace_store or not WHEEL_AVAILABLE:
        return None
    try:
        safe = Path(db_path).as_posix()
        _store_executor_mod.ensure_wal(safe)
        store = _rust.TraceStore(safe)
        collector = _rust.TraceCollector(store)
        return (store, collector)
    except Exception as e:  # pragma: no cover - defensive; degrade to no-op
        logger.warning("TraceStore/Collector init failed: %s", e)
        return None


class TraceCapture:
    """High-level trace capture API wrapping ``TraceStore`` + ``TraceCollector``.

    When ``enable_trace_store`` is ``False`` or the wheel is absent, the instance
    is *disabled*: :pyattr:`enabled` is ``False``, no database file is created,
    and every method is a silent no-op (reads return empty/``None``).

    All store mutations are routed through :func:`core.store_executor.submit`, so
    they run on the shared single-writer pool and never block the caller's
    thread.  Reads block briefly on the returned :class:`~concurrent.futures.Future`
    (bounded by an explicit timeout) so the pool stays the single point of
    serialisation for the underlying SQLite handle.
    """

    def __init__(self, db_path: str, enable_trace_store: bool):
        pair = _trace_store_and_collector(db_path, enable_trace_store)
        if pair is not None:
            self._store, self._collector = pair
        else:
            self._store = None
            self._collector = None

    @property
    def enabled(self) -> bool:
        """``True`` when a live store/collector pair is held (flag on + wheel)."""
        return self._store is not None

    # -- lifecycle (collector-driven) --------------------------------------
    def start(self, trace_id: str, query: str, agent: str, model: str) -> None:
        """Begin a trace.  No-op when disabled."""
        if self._collector is None:
            return
        _store_executor_mod.submit(
            self._collector.start_trace, trace_id, query, agent, model
        )

    def add_step(self, trace_id: str, step: dict) -> None:
        """Append a step to an in-flight trace.  No-op when disabled.

        ``step`` is serialised to JSON before crossing the wheel boundary.  Per
        the verified schema the step MUST carry ``step_type``, ``timestamp``,
        ``duration_seconds``, ``input``, ``output`` and ``metadata``; ``input``
        and ``output`` MUST be maps/objects (a bare string is rejected by the
        Rust deserialiser).
        """
        if self._collector is None:
            return
        step_json = json.dumps(step)
        _store_executor_mod.submit(self._collector.add_step, trace_id, step_json)

    def end(self, trace_id: str, result: str, outcome: Optional[str] = None) -> None:
        """Finalise a trace and flush it to the store.  No-op when disabled."""
        if self._collector is None:
            return
        _store_executor_mod.submit(
            self._collector.end_trace, trace_id, result, outcome
        )

    # -- direct store access -----------------------------------------------
    def save(self, trace_dict: dict) -> None:
        """Persist a pre-built Trace dict directly.  No-op when disabled."""
        if self._store is None:
            return
        trace_json = json.dumps(trace_dict)
        _store_executor_mod.submit(self._store.save, trace_json)

    def get(self, trace_id: str) -> Optional[dict]:
        """Return the Trace dict for ``trace_id`` or ``None``.

        Returns ``None`` when disabled, when the trace is absent, or on any
        store/JSON error (logged, never raised).
        """
        if self._store is None:
            return None
        fut = _store_executor_mod.submit(self._store.get, trace_id)
        try:
            raw = fut.result(timeout=5.0)
            if raw is None:
                return None
            return json.loads(raw)
        except Exception as e:
            logger.warning("TraceCapture.get failed: %s", e)
            return None

    def list(self, limit: int = 100, offset: int = 0) -> list[dict]:
        """Return a page of Trace dicts.  Empty list when disabled or on error."""
        if self._store is None:
            return []
        fut = _store_executor_mod.submit(self._store.list_traces, limit, offset)
        try:
            items = fut.result(timeout=10.0)
            return [json.loads(item) for item in (items or [])]
        except Exception as e:
            logger.warning("TraceCapture.list failed: %s", e)
            return []

    def count(self) -> int:
        """Return the number of persisted traces.  ``0`` when disabled or on error."""
        if self._store is None:
            return 0
        fut = _store_executor_mod.submit(self._store.count)
        try:
            return fut.result(timeout=5.0)
        except Exception as e:
            logger.warning("TraceCapture.count failed: %s", e)
            return 0

    # -- raw handles (for advanced callers / lifecycle assertions) ---------
    @property
    def store(self) -> Optional[Any]:
        """The live ``TraceStore`` handle, or ``None`` when disabled."""
        return self._store

    @property
    def collector(self) -> Optional[Any]:
        """The live ``TraceCollector`` handle, or ``None`` when disabled."""
        return self._collector
