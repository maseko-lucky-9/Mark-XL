import json
import logging
import queue as _stdqueue
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Any

from core import store_executor as _store_executor_mod

# ---------------------------------------------------------------------------
# Optional mark_xl_rust wheel — never raises at import (legacy mode when absent).
# Mirrors core/mark_xl_rust_adapter.py:56-62.
# ---------------------------------------------------------------------------
try:
    import mark_xl_rust as _rust

    _WHEEL_AVAILABLE = True
except ImportError:
    _rust = None
    _WHEEL_AVAILABLE = False


class TaskStatus(Enum):
    PENDING    = "pending"
    RUNNING    = "running"
    COMPLETED  = "completed"
    FAILED     = "failed"
    CANCELLED  = "cancelled"


class TaskPriority(Enum):
    LOW    = 3
    NORMAL = 2
    HIGH   = 1


# ---------------------------------------------------------------------------
# Status vocabulary bridge (FR-2).
#
# Rust SchedulerStore validates status against: active / paused / cancelled /
# completed.  The Python TaskStatus enum above is UNCHANGED; we map across the
# boundary instead of renaming.
#
#   Python TaskStatus     ->  Rust SchedulerStore status
#   -----------------------------------------------------
#   PENDING               ->  "active"
#   RUNNING               ->  "active"
#   COMPLETED             ->  "completed"
#   FAILED                ->  "cancelled"   (store has no "failed")
#   CANCELLED             ->  "cancelled"
#
# Reverse (Rust -> Python), used by reload_from_store():
#   "active"    -> TaskStatus.PENDING
#   "paused"    -> TaskStatus.PENDING
#   "completed" -> TaskStatus.COMPLETED
#   "cancelled" -> TaskStatus.CANCELLED
# ---------------------------------------------------------------------------
_TASK_STATUS_TO_RUST = {
    TaskStatus.PENDING:   "active",
    TaskStatus.RUNNING:   "active",
    TaskStatus.COMPLETED: "completed",
    TaskStatus.FAILED:    "cancelled",
    TaskStatus.CANCELLED: "cancelled",
}

# Far-future sentinel so the store's "once" scheduler never marks our queued
# jobs as due.  TaskQueue jobs are run-now work items, not cron schedules; the
# store's schedule_type/value columns are unused for execution and only satisfy
# the create_task() signature.
_STORE_SCHEDULE_TYPE = "once"
_STORE_SCHEDULE_VALUE = "2999-01-01T00:00:00Z"


class _SchedulerStoreProxy:
    """Single-owner-thread proxy around the unsendable Rust ``SchedulerStore``.

    The PyO3 ``SchedulerStore`` pyclass is ``unsendable``: it panics if any
    method is called from a thread other than the one that *constructed* it.
    TaskQueue, however, touches the store from several threads (the submitting
    thread, the worker loop, and per-task daemon threads).  This proxy resolves
    that by owning ONE dedicated daemon thread that both constructs the store
    and is the only thread that ever calls into it.  Every public method
    marshals the call onto that thread via a command queue and blocks for the
    result — so callers see an ordinary synchronous API while the unsendable
    contract is never violated.

    This also subsumes the single-writer guarantee the rest of WO-6 gets from
    ``core.store_executor`` (which we cannot use here precisely because it would
    move the store across threads): one owner thread == one serialised writer.
    """

    def __init__(self, db_path: str):
        self._db_path = Path(db_path).as_posix()
        self._cmds: "_stdqueue.Queue" = _stdqueue.Queue()
        self._ready = threading.Event()
        self._init_error: Exception | None = None
        self._closed = False
        self._thread = threading.Thread(
            target=self._run, name="SchedulerStoreOwner", daemon=True
        )
        self._thread.start()
        # Block until the owner thread has attempted construction so a failed
        # init surfaces synchronously to _make_scheduler_store's try/except.
        self._ready.wait()
        if self._init_error is not None:
            raise self._init_error

    def _run(self) -> None:
        try:
            store = _rust.SchedulerStore(self._db_path)
        except Exception as e:  # pragma: no cover - exercised via init failure
            self._init_error = e
            self._ready.set()
            return
        self._ready.set()
        while True:
            item = self._cmds.get()
            if item is None:  # shutdown sentinel
                break
            method, args, result_box, done = item
            try:
                result_box.append(("ok", getattr(store, method)(*args)))
            except Exception as e:
                result_box.append(("err", e))
            finally:
                done.set()

    def _call(self, method: str, *args):
        if self._closed:
            raise RuntimeError("SchedulerStore proxy is closed")
        result_box: list = []
        done = threading.Event()
        self._cmds.put((method, args, result_box, done))
        done.wait()
        kind, payload = result_box[0]
        if kind == "err":
            raise payload
        return payload

    # --- mirrored SchedulerStore surface used by TaskQueue --------------
    def create_task(self, name: str, schedule_type: str, schedule_value: str):
        return self._call("create_task", name, schedule_type, schedule_value)

    def update_status(self, task_id: str, status: str):
        return self._call("update_status", task_id, status)

    def list_tasks(self):
        return self._call("list_tasks")

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._cmds.put(None)


def _make_scheduler_store(db_path: str, use_scheduler_store: bool):
    """Return a SchedulerStore proxy or None (never raises; None == legacy mode).

    The store is created only when the flag (passed in by the caller — never
    read from config inside this module) is on AND the optional wheel is
    available.  Because the Rust ``SchedulerStore`` is unsendable, it is wrapped
    in :class:`_SchedulerStoreProxy`, which confines every call to one dedicated
    owner thread.  On any failure it logs once and returns None so the queue
    falls back to the in-memory path.
    """
    if not use_scheduler_store or not _WHEEL_AVAILABLE:
        return None
    try:
        safe = Path(db_path).as_posix()
        _store_executor_mod.ensure_wal(safe)
        return _SchedulerStoreProxy(safe)
    except Exception:
        logging.getLogger(__name__).warning(
            "SchedulerStore init failed, falling back to in-memory"
        )
        return None


@dataclass(order=True)
class Task:
    priority:    int                       
    created_at:  float = field(compare=False)
    task_id:     str   = field(compare=False)
    goal:        str   = field(compare=False)
    status:      TaskStatus = field(compare=False, default=TaskStatus.PENDING)
    result:      Any        = field(compare=False, default=None)
    error:       str        = field(compare=False, default="")
    speak:       Any        = field(compare=False, default=None)   
    on_complete: Any        = field(compare=False, default=None)  
    cancel_flag: threading.Event = field(compare=False, default_factory=threading.Event)


class TaskQueue:
    def __init__(self, max_concurrent: int = 1, *, _scheduler_store=None):
        self._queue:        list[Task]       = []
        self._lock:         threading.Lock   = threading.Lock()
        self._condition:    threading.Condition = threading.Condition(self._lock)
        self._tasks:        dict[str, Task]  = {}
        self._running:      bool             = False
        self._worker_thread: threading.Thread | None = None
        self._max_concurrent = max_concurrent
        self._active_count   = 0
        self._executor       = None
        # Durable persistence (FR-1/FR-3).  None == legacy in-memory only.
        self._store          = _scheduler_store
        # Bridges our Python Task.task_id -> the store's auto-generated UUID so
        # later update_status() calls target the right row.  Guarded by _lock.
        self._store_id_map:  dict[str, str]  = {}

    def _get_executor(self):
        if self._executor is None:
            from agent.executor import AgentExecutor
            self._executor = AgentExecutor()
        return self._executor

    # ------------------------------------------------------------------
    # SchedulerStore persistence helpers (FR-1/FR-2/FR-3).
    #
    # All store writes are funnelled through core.store_executor.submit so they
    # run on the single shared writer thread (never the Qt main thread) and
    # never contend under WAL.  Every helper is a no-op when self._store is None,
    # which keeps the legacy in-memory path byte-identical.
    # ------------------------------------------------------------------
    def _store_create(self, task: "Task") -> None:
        """Persist a freshly-submitted task, capturing its store UUID.

        SchedulerStore.create_task auto-generates the row id, so we cannot pass
        our own task_id.  We encode (task_id, goal, priority) into the store's
        ``name`` column as a JSON blob for a lossless reload, then map our
        task_id -> the returned store id for subsequent status updates.
        """
        if self._store is None:
            return
        name_blob = json.dumps(
            {"task_id": task.task_id, "goal": task.goal, "priority": task.priority}
        )
        # The proxy is synchronous but runs the actual Rust call on its own
        # dedicated owner thread, so this never touches the unsendable store from
        # the wrong thread and never blocks on a heavy operation.
        try:
            raw = self._store.create_task(
                name_blob, _STORE_SCHEDULE_TYPE, _STORE_SCHEDULE_VALUE
            )
            store_id = json.loads(raw).get("id")
        except Exception:
            logging.getLogger(__name__).warning(
                "SchedulerStore create_task failed for task %s", task.task_id
            )
            return
        if store_id:
            with self._lock:
                self._store_id_map[task.task_id] = store_id

    def _store_update_status(self, task: "Task") -> None:
        """Persist a status change for an already-stored task."""
        if self._store is None:
            return
        with self._lock:
            store_id = self._store_id_map.get(task.task_id)
        if not store_id:
            return
        rust_status = _TASK_STATUS_TO_RUST.get(task.status, "active")
        try:
            self._store.update_status(store_id, rust_status)
        except Exception:
            logging.getLogger(__name__).warning(
                "SchedulerStore update_status failed for task %s", task.task_id
            )

    def start(self) -> None:
        if self._running:
            return
        self._running      = True
        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            daemon=True,
            name="AgentTaskQueue"
        )
        self._worker_thread.start()
        print("[TaskQueue] ✅ Started")

    def stop(self) -> None:
        self._running = False
        with self._condition:
            self._condition.notify_all()
        print("[TaskQueue] 🔴 Stopped")

    def submit(
        self,
        goal:        str,
        priority:    TaskPriority = TaskPriority.NORMAL,
        speak:       Callable | None = None,
        on_complete: Callable | None = None,
    ) -> str:

        task_id = str(uuid.uuid4())[:8]
        task    = Task(
            priority    = priority.value,
            created_at  = time.time(),
            task_id     = task_id,
            goal        = goal,
            speak       = speak,
            on_complete = on_complete,
        )

        # Persist (and capture the store-id) BEFORE the task becomes visible to
        # the worker loop.  Otherwise the worker could set RUNNING/COMPLETED and
        # try to update_status before _store_create has mapped the store-id,
        # leaving a completed task stuck at "active" in the store.  _store_create
        # is a no-op in legacy mode, so the in-memory path is unaffected.
        self._store_create(task)

        with self._condition:
            self._queue.append(task)
            self._queue.sort(key=lambda t: (t.priority, t.created_at))
            self._tasks[task_id] = task
            self._condition.notify()

        print(f"[TaskQueue] 📥 Task queued: [{task_id}] {goal[:60]}")
        return task_id

    def cancel(self, task_id: str) -> bool:

        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return False
            if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
                return False

            task.cancel_flag.set()
            task.status = TaskStatus.CANCELLED
            print(f"[TaskQueue] 🚫 Task cancelled: [{task_id}]")

        self._store_update_status(task)
        return True

    def get_status(self, task_id: str) -> dict | None:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return None
            return {
                "task_id": task.task_id,
                "goal":    task.goal,
                "status":  task.status.value,
                "result":  task.result,
                "error":   task.error,
            }

    def get_all_statuses(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "task_id": t.task_id,
                    "goal":    t.goal[:50],
                    "status":  t.status.value,
                }
                for t in self._tasks.values()
            ]

    def pending_count(self) -> int:
        with self._lock:
            return sum(1 for t in self._queue if t.status == TaskStatus.PENDING)

    def reload_from_store(self) -> int:
        """Reload still-pending tasks from the SchedulerStore (FR-3).

        Returns the number of tasks re-queued.  No-op (returns 0) when there is
        no store.  Only rows whose store status is "active"/"paused" are
        reloaded — completed/cancelled work is left in the store as history.
        Each row's ``name`` column carries the JSON blob written by
        ``_store_create`` (task_id, goal, priority), so the original Python Task
        identity is recovered losslessly; older/foreign rows fall back to the
        raw name and the store row id.
        """
        if self._store is None:
            return 0
        loaded = 0
        try:
            # SchedulerStore.list_tasks() returns ONE JSON-array string, not a
            # list of strings — decode it once.
            raw = self._store.list_tasks()
            rows = json.loads(raw) if isinstance(raw, str) else raw
            for row in rows:
                data = row if isinstance(row, dict) else json.loads(row)

                rust_status = data.get("status", "active")
                if rust_status not in ("active", "paused"):
                    continue

                store_id = data.get("id", "")
                name = data.get("name", "")
                # Try to recover our encoded blob from the name column.
                task_id = ""
                goal = ""
                priority_val = TaskPriority.NORMAL.value
                if name:
                    try:
                        blob = json.loads(name)
                        if isinstance(blob, dict) and "task_id" in blob:
                            task_id = blob.get("task_id", "")
                            goal = blob.get("goal", "")
                            priority_val = blob.get("priority", priority_val)
                    except (ValueError, TypeError):
                        # Foreign / legacy row: treat the raw name as the goal.
                        goal = name
                if not task_id:
                    task_id = store_id or str(uuid.uuid4())[:8]

                task = Task(
                    priority   = priority_val,
                    created_at = data.get("created_at", 0.0) or time.time(),
                    task_id    = task_id,
                    goal       = goal,
                    status     = TaskStatus.PENDING,
                )
                with self._condition:
                    if task_id not in self._tasks:
                        self._queue.append(task)
                        self._queue.sort(key=lambda t: (t.priority, t.created_at))
                        self._tasks[task_id] = task
                        if store_id:
                            self._store_id_map[task_id] = store_id
                        loaded += 1
                        self._condition.notify()
        except Exception as e:
            logging.getLogger(__name__).warning("reload_from_store failed: %s", e)
        return loaded

    def _worker_loop(self) -> None:
        while self._running:
            task = None

            with self._condition:
                while self._running and not self._next_task():
                    self._condition.wait(timeout=1.0)
                task = self._next_task()
                if task:
                    task.status = TaskStatus.RUNNING
                    self._active_count += 1
                    try:
                        self._queue.remove(task)
                    except ValueError:
                        pass

            if task:
                # RUNNING maps to the store's "active" status (FR-2); persisting
                # it keeps the stored row in step with the live task state.
                self._store_update_status(task)
                threading.Thread(
                    target=self._run_task,
                    args=(task,),
                    daemon=True,
                    name=f"AgentTask-{task.task_id}"
                ).start()

    def _next_task(self) -> Task | None:
        if self._active_count >= self._max_concurrent:
            return None
        for task in self._queue:
            if task.status == TaskStatus.PENDING and not task.cancel_flag.is_set():
                return task
        return None

    def _run_task(self, task: Task) -> None:
        print(f"[TaskQueue] ▶️ Running: [{task.task_id}] {task.goal[:60]}")
        try:
            executor = self._get_executor()
            result   = executor.execute(
                goal        = task.goal,
                speak       = task.speak,
                cancel_flag = task.cancel_flag,
            )

            with self._lock:
                if task.cancel_flag.is_set():
                    task.status = TaskStatus.CANCELLED
                else:
                    task.status = TaskStatus.COMPLETED
                    task.result = result
                self._active_count -= 1

            self._store_update_status(task)

            if task.on_complete and not task.cancel_flag.is_set():
                try:
                    task.on_complete(task.task_id, result)
                except Exception as e:
                    print(f"[TaskQueue] ⚠️ on_complete callback error: {e}")

            print(f"[TaskQueue] ✅ Completed: [{task.task_id}]")

        except Exception as e:
            with self._lock:
                task.status = TaskStatus.FAILED
                task.error  = str(e)
                self._active_count -= 1
            self._store_update_status(task)
            print(f"[TaskQueue] ❌ Failed: [{task.task_id}] {e}")

        with self._condition:
            self._condition.notify()

_queue        = TaskQueue()
_queue_started = False
_queue_lock    = threading.Lock()


def get_queue() -> TaskQueue:
    global _queue_started
    with _queue_lock:
        if not _queue_started:
            _queue.start()
            _queue_started = True
    return _queue