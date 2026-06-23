"""Thread-safe single-writer store executor for WO-6 SQLite stores.

All WO-6 persistence wrappers (trace store, telemetry store, and related
SQLite-backed components) route their writes through this module so that the
process holds **exactly one** writer thread across every store.  A single
serialised writer is what makes concurrent SQLite access safe under WAL: WAL
permits many concurrent readers but still serialises writers, so funnelling all
mutations through one worker eliminates ``database is locked`` contention by
construction rather than by retry/backoff.

Threading contract
-------------------
* Synchronous SQLite calls must NEVER run on the Qt main thread.  They are
  offloaded to a module-level :class:`~concurrent.futures.ThreadPoolExecutor`
  with ``max_workers=1`` — the single-writer guarantee.
* The pool is created lazily on first use under a double-checked lock, and its
  shutdown is registered with :mod:`atexit` (``wait=False``) so the test suite
  (and the app) never hangs on interpreter exit waiting on an idle worker.
* Importing this module has **zero** side effects: no pool, no database
  connection, and no Qt object is created at import time.
* Results are marshalled back to the Qt main thread via
  :class:`StoreResultBridge`'s ``result_ready`` signal (a queued cross-thread
  connection), never by touching Qt objects from the worker thread.

WAL helper
----------
:func:`ensure_wal` puts a database into Write-Ahead-Logging mode once, up front,
on a short-lived connection.  It is idempotent: running it against a database
that is already in WAL mode is a no-op that simply confirms the mode.
"""

from __future__ import annotations

import atexit
import logging
import sqlite3
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Optional

from PyQt6.QtCore import QObject, pyqtSignal

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy singleton pool — max_workers=1 (single writer).
#
# ThreadPoolExecutor has no public knob to mark its workers as daemon threads,
# so instead of fighting that we register an atexit hook that calls
# shutdown(wait=False).  An idle worker does not block interpreter exit, and the
# explicit non-blocking shutdown guarantees the test suite never hangs on
# teardown.
# ---------------------------------------------------------------------------
_store_executor: Optional[ThreadPoolExecutor] = None
_store_executor_lock = threading.Lock()


def _get_store_executor() -> ThreadPoolExecutor:
    """Return the module-level single-writer executor, creating it lazily.

    Uses double-checked locking so the pool is constructed at most once, with
    ``max_workers=1`` (the single-writer guarantee) and the atexit shutdown hook
    registered exactly once.
    """
    global _store_executor
    if _store_executor is None:
        with _store_executor_lock:
            if _store_executor is None:
                _store_executor = ThreadPoolExecutor(
                    max_workers=1, thread_name_prefix="mark_xl_store"
                )
                # Never wait on the idle worker at exit — keeps the suite from hanging.
                atexit.register(_store_executor.shutdown, wait=False)
    return _store_executor


def submit(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Future:
    """Submit ``fn(*args, **kwargs)`` to the single-writer store pool.

    Returns a :class:`concurrent.futures.Future`.  Use this for any synchronous
    SQLite write so it never blocks the Qt main thread and is serialised behind
    the one writer worker.
    """
    return _get_store_executor().submit(fn, *args, **kwargs)


def ensure_wal(path: str) -> None:
    """Put the SQLite database at ``path`` into Write-Ahead-Logging mode.

    Opens a short-lived connection, executes ``PRAGMA journal_mode=WAL``, reads
    the mode back, and verifies it is ``"wal"`` (case-insensitive).  Idempotent:
    a database already in WAL mode is left unchanged.  Raises
    :class:`RuntimeError` if the database refuses to enter WAL mode (e.g. an
    in-memory or unsupported filesystem target), so callers fail loudly rather
    than silently running without the single-writer concurrency guarantee.
    """
    safe_path = Path(path).as_posix()
    conn = sqlite3.connect(safe_path)
    try:
        row = conn.execute("PRAGMA journal_mode=WAL").fetchone()
        mode = (row[0] if row else "") or ""
        if mode.lower() != "wal":
            logger.error(
                "ensure_wal: %s did not enter WAL mode (got %r)", safe_path, mode
            )
            raise RuntimeError(
                f"ensure_wal: expected WAL journal mode for {safe_path!r}, got {mode!r}"
            )
        logger.debug("ensure_wal: %s journal_mode=%s", safe_path, mode)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Qt main-thread result marshalling.
# ---------------------------------------------------------------------------
class StoreResultBridge(QObject):
    """Marshal single-writer store results back to the Qt main thread.

    ``dispatch(fn, *args)`` runs ``fn`` in the off-main-thread store pool and, on
    completion, emits :pyattr:`result_ready` with the result.  Because the bridge
    lives on the thread that created it (normally the Qt main thread), the
    cross-thread ``emit`` is delivered via a queued connection — the connected
    slot runs on the main thread, not on the worker.
    """

    result_ready = pyqtSignal(object)

    def dispatch(self, fn: Callable[..., Any], *args: Any) -> Future:
        """Run ``fn(*args)`` in the store pool; emit ``result_ready`` on done.

        Returns the :class:`Future` so callers may also await/inspect it.
        """
        future = submit(fn, *args)

        def _on_done(fut: Future) -> None:
            # Runs on the worker thread; emit is queued back to the main thread.
            # Guard fut.result(): a raised exception must be logged, never allowed
            # to crash the worker callback or silently vanish.
            try:
                result = fut.result()
            except Exception:
                logger.warning(
                    "StoreResultBridge.dispatch: store task raised; "
                    "suppressing result_ready emit",
                    exc_info=True,
                )
                return
            self.result_ready.emit(result)

        future.add_done_callback(_on_done)
        return future
