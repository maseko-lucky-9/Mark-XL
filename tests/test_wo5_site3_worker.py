"""WO-5 T07: Site 3 — worker-thread path via _call_tool (AC-3).

Site #3 is not a new wiring site — it RIDES Site #2's _call_tool (executor.py)
on the worker thread (task_queue.py:159 -> _run_task -> executor.execute ->
_call_tool).  No production code was added for Site #3; these tests exercise
the existing path from a daemon thread to confirm:

  AC-3 acceptance criteria:
  - Worker-thread A-B-A-B trip returns "Loop detected: ..."
  - loop_detected signal is received on Qt main thread after cross-thread emit
  - No RuntimeError: "QObject: Cannot create children for a parent that is in
    a different thread"
  - task_queue.py has ZERO get_flag literals (static assertion)
  - Shared singleton: core.loop_guard._GUARD is the same object used by both
    the main-thread path and the worker-thread path
"""
import sys
import threading
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers (mirrors site2 patterns to stay consistent)
# ---------------------------------------------------------------------------

def _flag_selector(loopguard: bool, registry: bool = True, sec: bool = False):
    """Return a get_flag side-effect that selects specific flag values."""
    mapping = {
        "use_tool_registry": registry,
        "enable_loopguard": loopguard,
        "enable_security_gates": sec,
    }
    return lambda name, *a, **k: mapping.get(name, False)


def _low_knobs_cfg():
    """Return a get_security_config side-effect with tight knobs for fast trips."""
    return lambda key, default=None: {
        "loopguard_max_identical": 2,
        "loopguard_max_ping_pong": 2,
        "loopguard_poll_budget": 100,
    }.get(key, default)


# ---------------------------------------------------------------------------
# TestAC3WorkerPath
# ---------------------------------------------------------------------------

class TestAC3WorkerPath:

    # -----------------------------------------------------------------------
    # 1. Worker-thread A-B-A-B trips on call #3 AND fires loop_detected signal
    # -----------------------------------------------------------------------

    def test_worker_thread_abab_trip(self, monkeypatch, qapp):
        """A-B-A-B from a daemon thread trips on call #3 and fires loop_detected.

        Strategy:
        1. Patch flags + knobs to enable loopguard with tight limits.
        2. Patch tool_registry.dispatch with a fake that echoes the tool name.
        3. Pre-connect the bridge's loop_detected signal to a collector BEFORE
           the worker thread starts (main-thread affinity for bridge construction).
        4. Run A-B-A-B calls inside a threading.Thread (simulating the worker).
        5. Join the thread, then processEvents() to drain the Qt queue.
        6. Assert result #3 starts with "Loop detected:" and signal was received.
        """
        import memory.config_manager as cm
        monkeypatch.setattr(cm, "get_flag", _flag_selector(loopguard=True))
        monkeypatch.setattr(cm, "get_security_config", _low_knobs_cfg())

        import core.tool_registry as tr
        monkeypatch.setattr(tr, "dispatch", lambda name, params, **kw: f"ok:{name}")

        # Construct bridge on main thread BEFORE the worker thread starts, so Qt
        # auto-queues cross-thread emits back to the main thread correctly.
        import core.loop_guard as lg
        bridge = lg.get_bridge()
        assert bridge is not None, "get_bridge() must return a LoopGuardBridge"

        received_signals: list[str] = []
        errors: list[str] = []  # captures any RuntimeError from cross-thread QObject creation

        def _on_loop_detected(reason: str) -> None:
            received_signals.append(reason)

        bridge.loop_detected.connect(_on_loop_detected)

        from agent.executor import _call_tool

        results: list[str] = []

        def _worker():
            try:
                results.append(_call_tool("tool_a", {}, player=None, speak=None))
                results.append(_call_tool("tool_b", {}, player=None, speak=None))
                results.append(_call_tool("tool_a", {}, player=None, speak=None))
                results.append(_call_tool("tool_b", {}, player=None, speak=None))
            except RuntimeError as exc:
                errors.append(str(exc))

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        t.join(timeout=5.0)

        # Drain the Qt event queue so cross-thread signal emissions are delivered.
        # qapp fixture guarantees a QApplication exists; qWait(50) processes events
        # with a small sleep so the queued cross-thread emit can land on the main loop.
        from PyQt6.QtTest import QTest
        QTest.qWait(50)  # ms — gives the Qt event loop time to deliver queued signals

        # AC-3: no RuntimeError from cross-thread QObject creation
        assert not errors, (
            f"RuntimeError must NOT occur in the worker thread: {errors}"
        )

        # AC-3: call #3 trips
        assert len(results) >= 3, f"Expected at least 3 results, got {results!r}"
        assert results[0] == "ok:tool_a", f"Call #1 should pass, got {results[0]!r}"
        assert results[1] == "ok:tool_b", f"Call #2 should pass, got {results[1]!r}"
        assert results[2].startswith("Loop detected:"), (
            f"Call #3 should be tripped by loopguard, got {results[2]!r}"
        )

        # AC-3: loop_detected signal received on main thread
        assert len(received_signals) >= 1, (
            "loop_detected signal must be received on the main thread after "
            f"the worker thread emits it; received_signals={received_signals!r}"
        )
        assert all(isinstance(s, str) and s for s in received_signals), (
            f"All received signal payloads must be non-empty strings: {received_signals!r}"
        )

        # Cleanup: disconnect to avoid leaking across tests
        bridge.loop_detected.disconnect(_on_loop_detected)

    # -----------------------------------------------------------------------
    # 1b. REAL site-#3 chain: TaskQueue -> _worker_loop -> _run_task ->
    #     executor.execute -> _call_tool trips on the worker thread (AC-3)
    # -----------------------------------------------------------------------

    def test_taskqueue_real_chain_trips_and_resets(self, monkeypatch, qapp):
        """AC-3 end-to-end: a looping task enqueued through the REAL TaskQueue
        trips the shared guard on the worker thread, and a fresh task does NOT
        carry over loop state (per-query reset at the execute() boundary).

        Unlike test_worker_thread_abab_trip (which drives _call_tool directly in
        a bare thread as a focused cross-thread-signal probe), this test exercises
        the genuine production chain the spec's AC-3 requires:

            TaskQueue.submit -> _worker_loop (daemon thread, task_queue.py:142)
              -> _run_task (task_queue.py:174)
              -> AgentExecutor.execute (task_queue.py:178)
                  -> loop_guard.reset()  (per-query reset boundary)
                  -> _call_tool(...) per plan step  (executor.py:402)
                      -> shared core.loop_guard.check against the ONE _GUARD

        The planner LLM call is the only non-deterministic touch; we stub
        create_plan to emit an A-B-A-B step plan and _summarize to a fixed
        string so the chain is hermetic. Everything downstream of create_plan —
        the worker thread, execute()'s reset preamble, the per-step _call_tool
        dispatch, and the shared guard — is the real production code.
        """
        import memory.config_manager as cm
        monkeypatch.setattr(cm, "get_flag", _flag_selector(loopguard=True))
        monkeypatch.setattr(cm, "get_security_config", _low_knobs_cfg())

        import core.tool_registry as tr
        monkeypatch.setattr(tr, "dispatch", lambda name, params, **kw: f"ok:{name}")
        # execute() builds a registry-based planner prompt when use_tool_registry
        # is ON; stub the two pure string-builders so the chain does not depend on
        # the live _REGISTRY contents (build_planner_tool_block indexes
        # PLANNER_TOOLS_ORDER). These are NOT the code under test for AC-3.
        monkeypatch.setattr(tr, "build_planner_tool_block", lambda: "")
        import agent.planner as planner_mod
        monkeypatch.setattr(planner_mod, "build_planner_prompt", lambda block: "PLANNER")

        # Pre-construct the bridge on the MAIN thread so cross-thread emits from
        # the worker thread auto-queue back to the main loop (D7 / NFR-3).
        import core.loop_guard as lg
        bridge = lg.get_bridge()
        assert bridge is not None, "get_bridge() must return a LoopGuardBridge"

        received_signals: list[str] = []
        bridge.loop_detected.connect(lambda reason: received_signals.append(reason))

        # ---- Stub the two LLM touch-points on AgentExecutor.execute's path ----
        # create_plan is imported into agent.executor's namespace at module load
        # (`from agent.planner import create_plan`), so patch it THERE.
        import agent.executor as executor_mod

        looping_plan = {
            "steps": [
                {"step": 1, "tool": "tool_a", "description": "A", "parameters": {}},
                {"step": 2, "tool": "tool_b", "description": "B", "parameters": {}},
                {"step": 3, "tool": "tool_a", "description": "A again — trips", "parameters": {}},
                {"step": 4, "tool": "tool_b", "description": "B again", "parameters": {}},
            ]
        }
        nonlooping_plan = {
            "steps": [
                {"step": 1, "tool": "tool_a", "description": "single A", "parameters": {}},
            ]
        }

        plans = [looping_plan, nonlooping_plan]
        plan_calls: list[str] = []

        def _fake_create_plan(goal, context="", system=None):
            plan_calls.append(goal)
            # First task gets the looping plan; second gets the clean plan.
            return plans[min(len(plan_calls) - 1, len(plans) - 1)]

        monkeypatch.setattr(executor_mod, "create_plan", _fake_create_plan)

        # _summarize hits call_llm_text — stub it to a deterministic string so a
        # fully-successful (or partially-tripped) run never touches Ollama.
        monkeypatch.setattr(
            executor_mod.AgentExecutor,
            "_summarize",
            lambda self, goal, completed_steps, speak: "summary-done",
        )

        # ---- Drive the REAL TaskQueue worker chain ----
        from agent.task_queue import TaskQueue
        from PyQt6.QtTest import QTest

        results: dict[str, str] = {}
        done = threading.Event()

        def _on_complete(task_id: str, result):
            # Capture the step result the executor recorded for the looping task.
            results[task_id] = result
            if len(results) >= 1:
                done.set()

        queue = TaskQueue(max_concurrent=1)
        queue.start()
        try:
            tid1 = queue.submit("loop please", on_complete=_on_complete)

            # Pump the Qt loop while the daemon worker thread runs the chain.
            deadline_ms = 0
            while not done.is_set() and deadline_ms < 5000:
                QTest.qWait(50)
                deadline_ms += 50

            assert done.is_set(), (
                f"Worker chain did not complete in time; results={results!r}"
            )
            assert plan_calls, "create_plan must have been driven by execute()"

            # AC-3: the looping task tripped the shared guard ON THE WORKER THREAD.
            # The trip surfaces as a "Loop detected:" step result recorded by
            # execute(); _summarize then returns its fixed string, but the
            # loop_detected SIGNAL is the load-bearing AC-3 evidence (it can only
            # fire if loop_guard.check returned non-None inside _call_tool).
            QTest.qWait(50)  # final drain for the cross-thread emit
            assert received_signals, (
                "loop_detected must fire from the worker-thread chain "
                f"(TaskQueue -> execute -> _call_tool); received={received_signals!r}"
            )
            assert all(isinstance(s, str) and s for s in received_signals), (
                f"signal payloads must be non-empty strings: {received_signals!r}"
            )

            # ---- Per-query reset: a fresh task does NOT carry over loop state ----
            signals_before_task2 = len(received_signals)
            results.clear()
            done.clear()
            tid2 = queue.submit("clean please", on_complete=_on_complete)

            deadline_ms = 0
            while not done.is_set() and deadline_ms < 5000:
                QTest.qWait(50)
                deadline_ms += 50

            assert done.is_set(), (
                f"Second (clean) task did not complete; results={results!r}"
            )
            QTest.qWait(50)
            # execute() calls loop_guard.reset() at its start, so the single-step
            # clean task must NOT inherit the prior task's tripped counters — no
            # NEW loop_detected signal is emitted for the clean task.
            assert len(received_signals) == signals_before_task2, (
                "Per-query reset failed: the fresh task inherited loop state from "
                f"the prior task. signals before={signals_before_task2}, "
                f"after={len(received_signals)}"
            )
        finally:
            queue.stop()
            bridge.loop_detected.disconnect()

    # -----------------------------------------------------------------------
    # 2. Static assertion: task_queue.py has zero get_flag / loop_guard imports
    # -----------------------------------------------------------------------

    def test_taskqueue_no_get_flag(self):
        """task_queue.py must contain zero 'get_flag' literals (static grep).

        Site #3 rides Site #2's executor path; task_queue.py itself must have
        no knowledge of get_flag or loop_guard — those live entirely in executor.
        """
        task_queue_path = Path(__file__).resolve().parent.parent / "agent" / "task_queue.py"
        assert task_queue_path.exists(), f"task_queue.py not found at {task_queue_path}"

        content = task_queue_path.read_text(encoding="utf-8")

        # AC-3 static: zero get_flag literals
        assert "get_flag" not in content, (
            "task_queue.py must NOT contain 'get_flag' — loopguard is wired "
            "inside executor._call_tool, not in the task queue."
        )

        # AC-3 static: zero loop_guard imports
        import_lines = [
            line for line in content.splitlines()
            if "import" in line and "loop_guard" in line
        ]
        assert not import_lines, (
            f"task_queue.py must NOT import loop_guard; found: {import_lines}"
        )

    # -----------------------------------------------------------------------
    # 3. Shared singleton: same _GUARD object used across both call sites
    # -----------------------------------------------------------------------

    def test_shared_singleton(self, monkeypatch):
        """core.loop_guard._GUARD is the same object for main-thread and worker-thread calls.

        Both the main-thread site (site 1, via __main__) and the worker-thread
        site (site 3, via executor._call_tool from a thread) share the same
        module-level _GUARD singleton protected by _GUARD_LOCK.

        Test strategy:
        1. Enable loopguard via patched flags.
        2. Call get_guard() explicitly on the main thread to seed the singleton.
        3. Capture the id() of core.loop_guard._GUARD.
        4. Call _call_tool from a worker thread (which also calls get_guard
           via executor.py line ~232).
        5. Capture id() of core.loop_guard._GUARD again inside the thread.
        6. Assert both ids match — same object, no double-init.
        """
        import memory.config_manager as cm
        monkeypatch.setattr(cm, "get_flag", _flag_selector(loopguard=True))
        monkeypatch.setattr(cm, "get_security_config", _low_knobs_cfg())

        import core.tool_registry as tr
        monkeypatch.setattr(tr, "dispatch", lambda name, params, **kw: f"ok:{name}")

        import core.loop_guard as lg

        # Seed the singleton on the main thread
        guard_main = lg.get_guard(
            True,
            max_identical=2,
            max_ping_pong=2,
            poll_budget=100,
        )
        assert guard_main is not None, "get_guard() must return a non-None object"
        main_thread_id = id(lg._GUARD)

        worker_ids: list[int] = []
        errors: list[str] = []

        from agent.executor import _call_tool

        def _worker():
            try:
                # This call triggers get_guard inside executor.py — must reuse singleton
                _call_tool("tool_x", {}, player=None, speak=None)
                worker_ids.append(id(lg._GUARD))
            except RuntimeError as exc:
                errors.append(str(exc))

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        t.join(timeout=5.0)

        assert not errors, f"No RuntimeError expected in worker thread: {errors}"
        assert worker_ids, "Worker thread must capture _GUARD id"

        assert worker_ids[0] == main_thread_id, (
            f"_GUARD must be the same singleton object on all threads. "
            f"main id={main_thread_id}, worker id={worker_ids[0]}"
        )
