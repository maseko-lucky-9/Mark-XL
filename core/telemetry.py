"""Telemetry capture wrapper for WO-6: TelemetryStore + TelemetryAggregator.

Gates on ``enable_telemetry`` AND :data:`WHEEL_AVAILABLE` — both decided by the
*caller* (the flag bool is passed in; this module never reads a flag itself).

Design contract
---------------
* **No flag reads.** This module never calls the flag accessor; the flag-read
  token count for this file is zero by contract.  Whether telemetry is on is the
  caller's decision, handed in as ``enable_telemetry``.
* **Zero import side effects.** Importing this module constructs no store, opens
  no database, and starts no thread or pool.  The optional ``mark_xl_rust``
  import is the only top-level work and it can never raise (ImportError is
  swallowed).
* **Single writer.** Every mutating call routes through
  :func:`core.store_executor.submit`, so all WO-6 store writes share the one
  ``max_workers=1`` writer pool and never contend on SQLite under WAL.

AC5b read-back path (verified against the wheel)
------------------------------------------------
The Rust ``TelemetryStore`` (``telemetry/src/store.rs``) exposes only
``record``, ``count`` and ``clear`` — there is **no** ``get`` or
``list_records``.  Round-trip verification therefore reads back through:

* :meth:`TelemetryCapture.count` — the authoritative row count
  (``SELECT COUNT(*) FROM telemetry``), which goes non-zero after ``record``.
* :meth:`TelemetryCapture.stats` — ``TelemetryAggregator.stats()`` returns a
  JSON-encoded ``AggregateStats`` object.  The current Rust aggregator is a
  read-only stub that returns the default (all-zero) struct, so ``stats()``
  confirms the aggregator is *wired and queryable* (a non-empty dict with the
  six known keys) rather than reflecting per-record values.  ``count()`` is the
  value-bearing read-back; ``stats()`` is the structural read-back.

``TelemetryAggregator`` exposes ``stats`` only — it has **no** ``count`` method,
so the aggregator read-back is ``stats()`` while the count read-back is the
store's own ``count()``.

Field types (Rust ABI)
-----------------------
``record`` mirrors the wheel signature
``record(model_id, prompt_tokens=0, completion_tokens=0, total_tokens=0,
latency_seconds=0.0, ttft=0.0, cost_usd=0.0, timestamp=None)``.  Token fields are
``int`` (Rust ``i64``); ``latency_seconds``, ``ttft``, ``cost_usd`` and
``timestamp`` are ``float`` (Rust ``f64`` — never ``f32``).  A ``None`` timestamp
lets the wheel stamp the current epoch second.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Optional wheel import — never raises.  On a platform without the compiled
# ``mark_xl_rust`` wheel this module still imports cleanly with
# ``WHEEL_AVAILABLE = False`` and every TelemetryCapture method degrades to a
# no-op.
try:
    import mark_xl_rust as _rust

    WHEEL_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only on wheel-less platforms
    _rust = None
    WHEEL_AVAILABLE = False

# Imported for its single-writer ``submit`` and the WAL helper.  This import
# pulls in PyQt6 transitively but constructs no store, pool, or connection.
from core import store_executor as _store_executor_mod


def _make_telemetry_store(db_path: str, enable_telemetry: bool):
    """Return ``(TelemetryStore, TelemetryAggregator)`` or ``None``.  Never raises.

    Gated on ``enable_telemetry and WHEEL_AVAILABLE`` — both supplied by the
    caller (the flag) and the environment (the wheel).  On any failure
    (wheel-less, WAL refused, store construction error) this returns ``None`` so
    :class:`TelemetryCapture` falls back to a fully no-op instance.
    """
    if not enable_telemetry or not WHEEL_AVAILABLE:
        return None
    try:
        safe = Path(db_path).as_posix()
        _store_executor_mod.ensure_wal(safe)
        store = _rust.TelemetryStore(safe)
        aggregator = _rust.TelemetryAggregator(store)
        return (store, aggregator)
    except Exception as e:  # pragma: no cover - defensive; degrade to no-op
        logger.warning("TelemetryStore/Aggregator init failed: %s", e)
        return None


class TelemetryCapture:
    """High-level telemetry capture API wrapping ``TelemetryStore`` + ``TelemetryAggregator``.

    When ``enable_telemetry`` is ``False`` or the wheel is absent, the instance is
    *disabled*: :pyattr:`enabled` is ``False``, no database file is created, and
    every method is a silent no-op (writes drop, reads return ``0`` / ``{}``).

    All store mutations are routed through :func:`core.store_executor.submit`, so
    they run on the shared single-writer pool and never block the caller's
    thread.  Reads block briefly on the returned :class:`~concurrent.futures.Future`
    (bounded by an explicit timeout) so the pool stays the single point of
    serialisation for the underlying SQLite handle.
    """

    def __init__(self, db_path: str, enable_telemetry: bool):
        pair = _make_telemetry_store(db_path, enable_telemetry)
        if pair is not None:
            self._store, self._aggregator = pair
        else:
            self._store = None
            self._aggregator = None

    @property
    def enabled(self) -> bool:
        """``True`` when a live store/aggregator pair is held (flag on + wheel)."""
        return self._store is not None

    # -- writes (single-writer pool) ---------------------------------------
    def record(
        self,
        model_id: str,
        *,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        latency_seconds: float = 0.0,
        ttft: float = 0.0,
        cost_usd: float = 0.0,
        timestamp: Optional[float] = None,
    ) -> None:
        """Record a telemetry event.  No-op when disabled.

        Mirrors the verified wheel signature
        ``record(model_id, prompt_tokens, completion_tokens, total_tokens,
        latency_seconds, ttft, cost_usd, timestamp)`` — positional past
        ``model_id``.  Token fields are ``int`` (i64); latency/ttft/cost/timestamp
        are ``float`` (f64).  A ``None`` timestamp lets the wheel stamp the
        current epoch second.
        """
        if self._store is None:
            return
        _store_executor_mod.submit(
            self._store.record,
            model_id,
            prompt_tokens,
            completion_tokens,
            total_tokens,
            latency_seconds,
            ttft,
            cost_usd,
            timestamp,
        )

    # -- reads (AC5b round-trip) -------------------------------------------
    def count(self) -> int:
        """Return the number of persisted telemetry records.

        ``0`` when disabled or on error.  This is the value-bearing AC5b
        read-back: it reflects ``record`` calls (``SELECT COUNT(*)``).
        """
        if self._store is None:
            return 0
        fut = _store_executor_mod.submit(self._store.count)
        try:
            return fut.result(timeout=5.0)
        except Exception as e:
            logger.warning("TelemetryCapture.count failed: %s", e)
            return 0

    def stats(self) -> dict:
        """Return aggregated stats via ``TelemetryAggregator``.  AC5b read-back path.

        The wheel returns a JSON-encoded ``AggregateStats`` object; it is decoded
        to a ``dict`` (keys: ``total_requests``, ``total_tokens``, ``avg_latency``,
        ``avg_throughput``, ``total_cost``, ``total_energy``).  The current Rust
        aggregator is a read-only stub returning the default struct, so the values
        are zeros — the non-empty dict confirms the aggregator is wired and
        queryable.  Returns ``{}`` when disabled or on error.
        """
        if self._aggregator is None:
            return {}
        fut = _store_executor_mod.submit(self._aggregator.stats)
        try:
            result = fut.result(timeout=5.0)
            if isinstance(result, str):
                return json.loads(result)
            if isinstance(result, dict):
                return result
            return {}
        except Exception as e:
            logger.warning("TelemetryCapture.stats failed: %s", e)
            return {}

    # -- raw handles (for advanced callers / lifecycle assertions) ---------
    @property
    def store(self) -> Optional[Any]:
        """The live ``TelemetryStore`` handle, or ``None`` when disabled."""
        return self._store

    @property
    def aggregator(self) -> Optional[Any]:
        """The live ``TelemetryAggregator`` handle, or ``None`` when disabled."""
        return self._aggregator
