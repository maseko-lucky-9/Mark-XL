"""Thread-safe adapter for the optional ``mark_xl_rust`` maturin wheel.

This module is the single integration point between Mark-XL's Python/PyQt6
process and the vendored Rust workspace (agent / security / storage / LoopGuard
primitives).  The wheel is **optional**: the entire app must run when it is
absent or when the ``use_rust_wheel`` flag is off.

Legacy-mode degradation
------------------------
If ``import mark_xl_rust`` raises ``ImportError`` (no wheel built for this
platform), this module still imports cleanly with ``WHEEL_AVAILABLE = False``.
In that state every factory function returns ``None`` and logs a one-line
"legacy mode" message, so callers can branch on the falsy return (or, more
robustly, gate on ``WHEEL_AVAILABLE`` first).  No call path raises just because
the wheel is missing.

Threading contract (AC6)
------------------------
Synchronous Rust calls must NEVER run on the Qt main thread.  They are offloaded
to a module-level :class:`~concurrent.futures.ThreadPoolExecutor`.  The pool is
created lazily and its shutdown is registered with :mod:`atexit`
(``wait=False``) so the test suite (and the app) never hangs on interpreter
exit waiting on idle workers.  Results are marshalled back to the Qt main thread
via :class:`RustResultBridge`'s ``result_ready`` signal (a queued
cross-thread connection), never by touching Qt objects from a worker thread.

The Rust GIL-release mechanism (``py.allow_threads`` around
``RUNTIME.block_on(...)``) is exercised through :func:`gil_probe`, which submits
the test-only ``_gil_probe`` pyfunction to the pool (see WO-1 spec AC5).

``check_ssrf`` return-value note
--------------------------------
The Rust ``check_ssrf`` returns ``Option<String>``: ``None`` == "URL is safe",
``Some(reason)`` == "blocked, here is why".  This adapter ALSO returns ``None``
when the wheel is absent (after logging "legacy mode").  The two ``None`` cases
are therefore ambiguous on the return value alone.  This is intentional and
safe for WO-1: there is no caller acting on the result yet, and later WOs gate
on ``WHEEL_AVAILABLE`` before calling, so "wheel absent" never masquerades as
"URL safe" in practice.  Callers that need to distinguish should check
``WHEEL_AVAILABLE`` first.
"""

from __future__ import annotations

import atexit
import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, Optional

from PyQt6.QtCore import QObject, pyqtSignal

# ---------------------------------------------------------------------------
# Optional wheel import — never raises.
# ---------------------------------------------------------------------------
try:
    import mark_xl_rust as _rust

    WHEEL_AVAILABLE = True
except ImportError:
    _rust = None
    WHEEL_AVAILABLE = False

logger = logging.getLogger(__name__)

_LEGACY_MSG = "mark_xl_rust wheel unavailable — running in legacy mode (%s)"

# ---------------------------------------------------------------------------
# Lazy, daemon-safe ThreadPoolExecutor (AC6).
#
# Created on first use under a lock.  ThreadPoolExecutor has no public knob to
# mark its workers as daemon threads, so instead of fighting that we register an
# atexit hook that calls shutdown(wait=False).  Idle workers do not block
# interpreter exit, and the explicit non-blocking shutdown guarantees the test
# suite never hangs on teardown.
# ---------------------------------------------------------------------------
_executor: Optional[ThreadPoolExecutor] = None
_executor_lock = threading.Lock()


def _get_executor() -> ThreadPoolExecutor:
    """Return the module-level executor, creating it lazily on first use."""
    global _executor
    if _executor is None:
        with _executor_lock:
            if _executor is None:
                _executor = ThreadPoolExecutor(
                    max_workers=4, thread_name_prefix="mark_xl_rust"
                )
                # Never wait on idle workers at exit — keeps the suite from hanging.
                atexit.register(_executor.shutdown, wait=False)
    return _executor


def run_in_executor(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Future:
    """Submit ``fn(*args, **kwargs)`` to the off-main-thread pool.

    Returns a :class:`concurrent.futures.Future`.  Use this for any synchronous
    Rust call so it never blocks the Qt main thread (AC6).
    """
    executor = _get_executor()
    return executor.submit(fn, *args, **kwargs)


# ---------------------------------------------------------------------------
# Factory / passthrough functions.
# Each returns None + logs "legacy mode" when the wheel is absent.
# ---------------------------------------------------------------------------
def get_loop_guard(*args: Any, **kwargs: Any) -> Optional[Any]:
    """Return a ``mark_xl_rust.LoopGuard`` instance, or ``None`` in legacy mode.

    ``LoopGuard`` constructs hermetically (no live engine/runtime required).
    """
    if not WHEEL_AVAILABLE:
        logger.warning(_LEGACY_MSG, "get_loop_guard")
        return None
    return _rust.LoopGuard(*args, **kwargs)


def is_sensitive_file(path: str) -> Optional[bool]:
    """Wrap ``mark_xl_rust.is_sensitive_file``.

    Returns a ``bool`` when the wheel is present, or ``None`` (and logs "legacy
    mode") when it is absent.
    """
    if not WHEEL_AVAILABLE:
        logger.warning(_LEGACY_MSG, "is_sensitive_file")
        return None
    return _rust.is_sensitive_file(path)


def check_ssrf(url: str) -> Optional[str]:
    """Wrap ``mark_xl_rust.check_ssrf`` (SSRF host check).

    Wheel present: passthrough of the Rust ``Option<String>`` — ``None`` means
    the URL is safe, a ``str`` is the block reason.

    Wheel absent: returns ``None`` after logging "legacy mode".  See the module
    docstring for the (intentional, safe) ``None`` ambiguity between
    "wheel absent" and "URL safe".
    """
    if not WHEEL_AVAILABLE:
        logger.warning(_LEGACY_MSG, "check_ssrf")
        return None
    return _rust.check_ssrf(url)


def redact(text: str) -> str:
    """Wrap ``mark_xl_rust.SecretScanner().redact(text)``.

    Returns the input string with secrets replaced by ``[REDACTED:<pattern_name>]``
    tokens.  Wheel absent → returns ``text`` unchanged and logs ``_LEGACY_MSG``.
    """
    if not WHEEL_AVAILABLE:
        logger.warning(_LEGACY_MSG, "redact")
        return text
    try:
        return _rust.SecretScanner().redact(text)
    except Exception:
        logger.warning(_LEGACY_MSG, "redact")
        return text


def injection_scan(text: str) -> dict:
    """Wrap ``mark_xl_rust.InjectionScanner().scan(text)``.

    The wheel returns a JSON string; this wrapper parses it and returns a
    ``dict`` with keys ``is_clean`` (bool), ``threat_level`` (str), and
    ``findings`` (list).  Block condition for callers:
    ``is_clean is False and threat_level == "high"``.

    Wheel absent OR scan/parse error → returns the SAFE clean verdict
    ``{"is_clean": True, "threat_level": "low", "findings": []}`` and logs a
    warning (fail-open on scanner FAULT, never on a positive detection).
    """
    _SAFE = {"is_clean": True, "threat_level": "low", "findings": []}
    if not WHEEL_AVAILABLE:
        logger.warning(_LEGACY_MSG, "injection_scan")
        return _SAFE
    try:
        import json as _json
        raw = _rust.InjectionScanner().scan(text)
        return _json.loads(raw)
    except Exception:
        logger.warning("injection_scan failed — returning safe clean verdict")
        return _SAFE


def audit_verify_crosscheck(path: str) -> Optional[bool]:
    """Optional independent cross-check via ``mark_xl_rust.AuditLogger``.

    Calls ``AuditLogger(path).verify_chain()`` which returns
    ``(bool, Optional[int])``.  Returns the bool component, or ``None``
    when the wheel is absent.  NOT authoritative — ``core.audit.verify_chain``
    is.
    """
    if not WHEEL_AVAILABLE:
        logger.warning(_LEGACY_MSG, "audit_verify_crosscheck")
        return None
    try:
        result = _rust.AuditLogger(path).verify_chain()
        # Returns (bool, Optional[int]) per analysis §1
        return result[0] if isinstance(result, tuple) else bool(result)
    except Exception:
        logger.warning(_LEGACY_MSG, "audit_verify_crosscheck")
        return None


def gil_probe(millis: int) -> Optional[Future]:
    """Submit the test-only ``_gil_probe`` to the pool (AC5).

    ``_gil_probe`` performs the identical ``py.allow_threads(|| RUNTIME.block_on(
    tokio::sleep(millis)))`` used by the real agent ``run()`` sites, with no
    network.  Returns a :class:`Future`, or ``None`` (and logs "legacy mode")
    when the wheel is absent.
    """
    if not WHEEL_AVAILABLE:
        logger.warning(_LEGACY_MSG, "gil_probe")
        return None
    return run_in_executor(_rust._gil_probe, millis)


# ---------------------------------------------------------------------------
# Qt main-thread result marshalling (AC6).
# ---------------------------------------------------------------------------
class RustResultBridge(QObject):
    """Marshal worker-pool results back to the Qt main thread.

    ``dispatch(fn, *args)`` runs ``fn`` in the off-main-thread executor and, on
    completion, emits :pyattr:`result_ready` with the result.  Because the
    bridge lives on the thread that created it (normally the Qt main thread),
    the cross-thread ``emit`` is delivered via a queued connection — the
    connected slot runs on the main thread, not on the worker.
    """

    result_ready = pyqtSignal(object)

    def dispatch(self, fn: Callable[..., Any], *args: Any) -> Future:
        """Run ``fn(*args)`` in the executor; emit ``result_ready`` on done.

        Returns the :class:`Future` so callers may also await/inspect it.
        """
        future = run_in_executor(fn, *args)

        def _on_done(fut: Future) -> None:
            # Runs on the worker thread; emit is queued back to the main thread.
            self.result_ready.emit(fut.result())

        future.add_done_callback(_on_done)
        return future


# ---------------------------------------------------------------------------
# WO-4 P1 — SQLiteMemory wrappers (C-1)
# All gated on WHEEL_AVAILABLE; use run_in_executor for off-thread dispatch.
# The flag decision is passed in by callers; this module never reads it (AC10).
# ---------------------------------------------------------------------------

def sqlite_memory(db_path: str) -> "Optional[Any]":
    """Construct mark_xl_rust.SQLiteMemory(db_path). Returns None in legacy mode.

    Caller must invoke via: run_in_executor(sqlite_memory, db_path).result(timeout=5)
    """
    if not WHEEL_AVAILABLE:
        logger.warning(_LEGACY_MSG, "sqlite_memory")
        return None
    return _rust.SQLiteMemory(db_path)


def sqlite_store(mem: Any, content: str, source: str, metadata: "Optional[str]" = None) -> "Optional[str]":
    """Call mem.store(content, source, metadata) -> UUID str. Returns None in legacy mode.

    Confirmed signature (phase_2_handoff.txt): store(content, source, metadata=None) -> uuid
    Caller must invoke via: run_in_executor(sqlite_store, mem, content, source, metadata).result(timeout=5)
    """
    if not WHEEL_AVAILABLE or mem is None:
        logger.warning(_LEGACY_MSG, "sqlite_store")
        return None
    return mem.store(content, source, metadata)


def sqlite_retrieve(mem: Any, query: str, top_k: int = 5) -> "Optional[str]":
    """Call mem.retrieve(query, top_k) -> JSON str. Returns None in legacy mode.

    JSON hit keys: {content, score, source, metadata} (C6)
    Caller must invoke via: run_in_executor(sqlite_retrieve, mem, query, top_k).result(timeout=5)
    """
    if not WHEEL_AVAILABLE or mem is None:
        logger.warning(_LEGACY_MSG, "sqlite_retrieve")
        return None
    return mem.retrieve(query, top_k)


def has_semantic_bindings() -> bool:
    """Return True iff the wheel is available AND FAISSMemory has retrieve_by_embedding.

    False pre-rebuild (P1 state). Used for runtime graceful-degrade decisions.
    """
    return WHEEL_AVAILABLE and hasattr(_rust.FAISSMemory if _rust is not None else object, "retrieve_by_embedding")
