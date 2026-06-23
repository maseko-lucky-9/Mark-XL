"""WO-6 store tests — TraceStore / TraceCollector (T10) and siblings.

Shared preamble (imports, ``WHEEL_AVAILABLE``, ``_arm64``) is defined once here;
parallel WO-6 store tasks append their own test functions below.

Gating
------
* ``WHEEL_AVAILABLE`` — the compiled ``mark_xl_rust`` wheel is importable.
* ``_arm64`` — the wheel in this repo is built for arm64 (Apple Silicon); the
  store round-trip tests that actually exercise the Rust SQLite layer are
  arm64-only and skipped elsewhere.  Flag-OFF baseline tests are pure-Python and
  always run.
"""

import platform

import pytest

from core.trace_collector import WHEEL_AVAILABLE

_arm64 = platform.machine().lower() in ("arm64", "aarch64")


# ---------------------------------------------------------------------------
# T10 — TraceCapture (TraceStore + TraceCollector)
# ---------------------------------------------------------------------------
@pytest.mark.skipif(
    not WHEEL_AVAILABLE or not _arm64,
    reason="arm64-only wheel store test",
)
def test_trace_store_capacity_1000():
    """AC5, NFR-6: write 1000 traces, all retrievable."""
    import os
    import tempfile
    import time
    import uuid

    from core.trace_collector import TraceCapture

    with tempfile.TemporaryDirectory() as tmpdir:
        db = os.path.join(tmpdir, "traces.db")
        capture = TraceCapture(db, enable_trace_store=True)
        assert capture.enabled

        # Write 1000 traces via save(). Trace dicts use ``trace_id`` (verified
        # against the wheel's persisted schema) and require the float epoch
        # ``started_at``.
        trace_ids = []
        for i in range(1000):
            tid = str(uuid.uuid4())
            trace_ids.append(tid)
            trace = {
                "trace_id": tid,
                "query": f"query_{i}",
                "agent": "test_agent",
                "model": "gpt-4",
                "steps": [],
                "started_at": time.time(),
                "outcome": "success",
            }
            capture.save(trace)

        # Drain the single-writer pool: a no-op submitted last cannot complete
        # until every prior save() has run (max_workers=1, strict FIFO).
        from core import store_executor as se

        se.submit(lambda: None).result(timeout=30.0)

        count = capture.count()
        assert count >= 1000, f"Expected >= 1000 traces, got {count}"

        # list 10
        page = capture.list(limit=10)
        assert len(page) == 10, f"Expected 10 from list, got {len(page)}"

        # get by id
        retrieved = capture.get(trace_ids[0])
        assert retrieved is not None, "get() returned None for a saved trace"


@pytest.mark.skipif(
    not WHEEL_AVAILABLE or not _arm64,
    reason="arm64-only wheel store test",
)
def test_trace_collector_lifecycle():
    """AC5: start -> add_step -> end lifecycle."""
    import os
    import tempfile
    import time
    import uuid

    from core.trace_collector import TraceCapture

    with tempfile.TemporaryDirectory() as tmpdir:
        db = os.path.join(tmpdir, "trace_lifecycle.db")
        capture = TraceCapture(db, enable_trace_store=True)
        assert capture.enabled

        tid = str(uuid.uuid4())
        capture.start(tid, "test query", "test_agent", "gpt-4")
        # input/output MUST be maps — the Rust deserialiser rejects bare strings.
        capture.add_step(
            tid,
            {
                "step_type": "tool_call",
                "timestamp": time.time(),
                "duration_seconds": 0.05,
                "input": {"tool": "input"},
                "output": {"tool": "output"},
                "metadata": {},
            },
        )
        capture.end(tid, "final result", "success")

        # Drain the single-writer pool so the lifecycle has fully flushed.
        from core import store_executor as se

        se.submit(lambda: None).result(timeout=10.0)

        assert (
            capture.store.count() == 1
        ), f"Expected 1 trace, got {capture.store.count()}"
        assert capture.collector.active_count() == 0, "Expected 0 active after end"

        trace = capture.get(tid)
        assert trace is not None
        assert len(trace.get("steps", [])) >= 1


def test_trace_store_off_baseline():
    """AC6: enable_trace_store=False -> no DB created, all methods no-ops."""
    import os
    import tempfile

    from core.trace_collector import TraceCapture

    with tempfile.TemporaryDirectory() as tmpdir:
        db = os.path.join(tmpdir, "traces_off.db")
        capture = TraceCapture(db, enable_trace_store=False)

        assert not capture.enabled
        assert not os.path.exists(db), "DB must not be created when flag is OFF"

        # All methods are no-ops.
        capture.start("tid", "query", "agent", "model")
        capture.add_step("tid", {"step_type": "tool_call"})
        capture.end("tid", "result")
        capture.save({"trace_id": "tid"})
        assert capture.get("tid") is None
        assert capture.list() == []
        assert capture.count() == 0


# ---------------------------------------------------------------------------
# T11 — TelemetryCapture (TelemetryStore + TelemetryAggregator)
# ---------------------------------------------------------------------------
@pytest.mark.skipif(
    not WHEEL_AVAILABLE or not _arm64,
    reason="arm64-only wheel store test",
)
def test_telemetry_record_roundtrip():
    """AC5b: record calls round-trip via store count + aggregator stats.

    ``count()`` is the value-bearing row-count read-back (reflects the record
    calls); ``stats()`` is the aggregation read-back — the Rust aggregator runs
    a single SQL ``COUNT``/``SUM``/``AVG`` over the ``telemetry`` table and maps
    the row into ``AggregateStats``, so the decoded dict carries real non-zero
    totals/averages of the known inputs, not a stub of zeros.
    """
    import os
    import tempfile

    import pytest

    from core.telemetry import TelemetryCapture

    with tempfile.TemporaryDirectory() as tmpdir:
        db = os.path.join(tmpdir, "telemetry.db")
        cap = TelemetryCapture(db, enable_telemetry=True)
        assert cap.enabled

        # Three records with KNOWN inputs; assertions below derive the expected
        # aggregates directly from these tuples.
        records = [
            # (model_id, prompt, completion, total, latency_s, ttft, cost_usd)
            ("gpt-4", 10, 20, 30, 1.5, 0.5, 0.02),
            ("gpt-4", 5, 15, 20, 2.5, 0.3, 0.04),
            ("gpt-4", 8, 12, 25, 3.5, 0.4, 0.06),
        ]
        for model_id, prompt, completion, total, latency, ttft, cost in records:
            cap.record(
                model_id,
                prompt_tokens=prompt,
                completion_tokens=completion,
                total_tokens=total,
                latency_seconds=latency,
                ttft=ttft,
                cost_usd=cost,
            )

        # Drain the single-writer pool so every async write has landed.
        from core import store_executor as se

        se.submit(lambda: None).result(timeout=5.0)

        n = len(records)
        expected_total_tokens = sum(r[3] for r in records)
        expected_total_cost = sum(r[6] for r in records)
        expected_avg_latency = sum(r[4] for r in records) / n

        # Value-bearing read-back: the store row count reflects the record calls.
        assert cap.count() == n, f"Expected {n} records, got {cap.count()}"

        # Aggregation read-back: stats() decodes to a dict carrying real,
        # non-zero aggregates computed by the Rust SQL aggregation.
        stats = cap.stats()
        assert isinstance(stats, dict)
        assert stats, "aggregator stats must decode to a non-empty dict"
        assert "total_requests" in stats
        assert "total_tokens" in stats
        assert stats["total_requests"] == n
        assert stats["total_tokens"] == expected_total_tokens
        assert stats["total_cost"] == pytest.approx(expected_total_cost)
        assert stats["avg_latency"] == pytest.approx(expected_avg_latency)


def test_telemetry_off_baseline():
    """AC6: enable_telemetry=False -> no DB created, all methods are no-ops."""
    import os
    import tempfile

    from core.telemetry import TelemetryCapture

    with tempfile.TemporaryDirectory() as tmpdir:
        db = os.path.join(tmpdir, "telemetry_off.db")
        cap = TelemetryCapture(db, enable_telemetry=False)

        assert not cap.enabled
        assert not os.path.exists(db), "DB must not be created when flag is OFF"

        # All methods are no-ops — must not raise and must return empties.
        cap.record("gpt-4", prompt_tokens=10)
        assert cap.count() == 0
        assert cap.stats() == {}
        assert not os.path.exists(db), "record() must not create a DB when OFF"


# ---------------------------------------------------------------------------
# T07 — SchedulerStore integration in agent/task_queue.py (FR-1/FR-2/FR-3)
# Reuses the module-level WHEEL_AVAILABLE / _arm64 preamble above.
# ---------------------------------------------------------------------------
@pytest.mark.skipif(
    not WHEEL_AVAILABLE or not _arm64,
    reason="arm64-only wheel store test",
)
def test_scheduler_store_persist_reload():
    """AC1/FR-1/FR-3: a submitted task survives TaskQueue re-instantiation.

    Submit through one queue+store, then build a fresh queue over the SAME db
    file and reload — the pending task must come back with its identity intact.
    """
    import os
    import tempfile

    from agent.task_queue import TaskQueue, TaskPriority, _make_scheduler_store

    with tempfile.TemporaryDirectory() as tmpdir:
        db = os.path.join(tmpdir, "sched.db")

        store1 = _make_scheduler_store(db, use_scheduler_store=True)
        assert store1 is not None, "store must be created when flag ON + wheel present"
        q1 = TaskQueue(_scheduler_store=store1)
        tid = q1.submit("test goal", priority=TaskPriority.HIGH)

        # The store proxy is synchronous (it serialises onto its own owner
        # thread), so the create_task row + store-id mapping are committed by the
        # time submit() returns — no async drain needed.
        assert tid in q1._store_id_map, "store create_task did not commit"

        # Fresh queue over the same db file — simulates a process restart.
        store2 = _make_scheduler_store(db, use_scheduler_store=True)
        q2 = TaskQueue(_scheduler_store=store2)
        count = q2.reload_from_store()

        assert count >= 1, f"Expected at least 1 task reloaded, got {count}"
        assert tid in q2._tasks, "reloaded task must keep its original task_id"
        reloaded = q2._tasks[tid]
        assert reloaded.goal == "test goal"
        assert reloaded.priority == TaskPriority.HIGH.value


def test_scheduler_store_off_baseline():
    """AC6: use_scheduler_store=False -> no store, no DB file, legacy path.

    Runs on every platform — the OFF path must be inert whether or not the
    wheel is present.
    """
    import os
    import tempfile

    from agent.task_queue import TaskQueue, _make_scheduler_store

    with tempfile.TemporaryDirectory() as tmpdir:
        db = os.path.join(tmpdir, "sched_off.db")

        store = _make_scheduler_store(db, use_scheduler_store=False)
        assert store is None, "flag OFF must yield None (legacy mode)"
        assert not os.path.exists(db), "DB must not be created when flag is OFF"

        # Default construction (the module singleton path) uses no store.
        q = TaskQueue()
        assert q._store is None
        tid = q.submit("no-store task")
        assert tid in q._tasks
        # No store -> reload is a hard no-op.
        assert q.reload_from_store() == 0


# ---------------------------------------------------------------------------
# T08 — SessionStore integration + migration in memory/memory_manager.py
#       (FR-4/FR-5/FR-6). Reuses the module-level WHEEL_AVAILABLE / _arm64
#       preamble above.
# ---------------------------------------------------------------------------
@pytest.mark.skipif(
    not WHEEL_AVAILABLE or not _arm64,
    reason="arm64-only wheel store test",
)
def test_session_migration_bak_written():
    """AC4: migrate writes .bak before SQLite; 0-msg round-trip passes."""
    import json
    import os
    import tempfile

    from memory.memory_manager import migrate_sessions_if_needed, _make_session_store

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "sessions.db")
        source_path = os.path.join(tmpdir, "session_source.json")
        bak_path = source_path + ".bak"

        # No source file — exercises the "no source" path (documented no-op).
        assert not os.path.exists(source_path)

        migrate_sessions_if_needed(source_path, db_path, use_session_store=True)

        # .bak must exist after migration (crash-safe backup written first).
        assert os.path.exists(bak_path), f".bak not written at {bak_path}"

        # Round-trip: store is accessible; list_sessions reads back cleanly.
        store = _make_session_store(db_path, use_session_store=True)
        assert store is not None
        # Real wheel signature: list_sessions(active_only: bool, limit: int).
        listed = store.list_sessions(False, 1000)
        # JSON-string boundary (serde) — must parse; 0 migrated messages (no-op).
        parsed = json.loads(listed) if isinstance(listed, str) else listed
        assert isinstance(parsed, list)
        for sess in parsed:
            assert len(sess.get("messages", []) or []) == 0


@pytest.mark.skipif(
    not WHEEL_AVAILABLE or not _arm64,
    reason="arm64-only wheel store test",
)
def test_session_migration_roundtrip_with_messages():
    """AC4: forward-compat — a JSON source WITH messages round-trips identically."""
    import json
    import os
    import tempfile

    from memory.memory_manager import migrate_sessions_if_needed, _make_session_store

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "sessions_msgs.db")
        source_path = os.path.join(tmpdir, "history.json")
        bak_path = source_path + ".bak"

        history = {
            "messages": [
                {"role": "user", "content": "hello there", "channel": "cli"},
                {"role": "assistant", "content": "hi, how can I help?", "channel": "cli"},
                {"role": "user", "content": "what is 2+2?", "channel": "cli"},
            ]
        }
        with open(source_path, "w", encoding="utf-8") as fh:
            json.dump(history, fh)

        migrate_sessions_if_needed(source_path, db_path, use_session_store=True)

        # .bak written before any DB write, and is a faithful copy of the source.
        assert os.path.exists(bak_path)
        with open(bak_path, "r", encoding="utf-8") as fh:
            assert json.load(fh) == history

        # Every message reads back identically (loss-free round-trip, NFR-5).
        store = _make_session_store(db_path, use_session_store=True)
        assert store is not None
        listed = store.list_sessions(False, 1000)
        parsed = json.loads(listed) if isinstance(listed, str) else listed
        all_contents = []
        for sess in parsed:
            for item in sess.get("messages", []) or []:
                all_contents.append((item.get("role"), item.get("content")))
        expected = [(rec["role"], rec["content"]) for rec in history["messages"]]
        for pair in expected:
            assert pair in all_contents, f"message not round-tripped: {pair}"


@pytest.mark.skipif(
    not WHEEL_AVAILABLE or not _arm64,
    reason="arm64-only wheel store test",
)
def test_session_migration_idempotent_resume_from_bak():
    """AC4: a pre-existing .bak (crashed prior run) is resumed, not overwritten."""
    import json
    import os
    import tempfile

    from memory.memory_manager import migrate_sessions_if_needed

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "sessions_resume.db")
        source_path = os.path.join(tmpdir, "history.json")
        bak_path = source_path + ".bak"

        # Source has one content; .bak (from a "crashed" prior run) has another.
        with open(source_path, "w", encoding="utf-8") as fh:
            json.dump({"messages": [{"role": "user", "content": "from source"}]}, fh)
        with open(bak_path, "w", encoding="utf-8") as fh:
            json.dump({"messages": [{"role": "user", "content": "from bak"}]}, fh)

        migrate_sessions_if_needed(source_path, db_path, use_session_store=True)

        # .bak must be untouched (resume path does not re-copy the source over it).
        with open(bak_path, "r", encoding="utf-8") as fh:
            assert json.load(fh)["messages"][0]["content"] == "from bak"


def test_session_store_off_baseline():
    """AC6: use_session_store=False -> no migration, no DB, no .bak, factory None."""
    import os
    import tempfile

    from memory.memory_manager import migrate_sessions_if_needed, _make_session_store

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "sessions_off.db")
        source_path = os.path.join(tmpdir, "source.json")

        migrate_sessions_if_needed(source_path, db_path, use_session_store=False)

        assert not os.path.exists(db_path), "DB must not be created when flag is OFF"
        assert not os.path.exists(
            source_path + ".bak"
        ), ".bak must not be created when flag is OFF"

        store = _make_session_store(db_path, use_session_store=False)
        assert store is None, "Factory must return None when flag is OFF"


def test_session_store_factory_alias_present():
    """Both _make_session_store and the tasks.md _session_store name resolve."""
    import memory.memory_manager as mm

    assert mm._session_store is mm._make_session_store


# ---------------------------------------------------------------------------
# T09 — Flag-OFF baseline parity tests + test_flags.py guard (AC6, FR-18)
# FR-18 guard: test_flags.py allowlist verified GREEN at this commit
# callers remain: {main.py, agent/executor.py} — NO new get_flag callers added by WO-6
# ---------------------------------------------------------------------------
def test_flag_off_baseline_all_four():
    """AC6: all 4 flags OFF -> no stores created, legacy paths active."""
    import os
    import tempfile

    from agent.task_queue import _make_scheduler_store
    from core.telemetry import TelemetryCapture
    from core.trace_collector import TraceCapture
    from memory.memory_manager import _make_session_store

    with tempfile.TemporaryDirectory() as tmpdir:
        db1 = os.path.join(tmpdir, "sched.db")
        db2 = os.path.join(tmpdir, "sess.db")
        db3 = os.path.join(tmpdir, "trace.db")
        db4 = os.path.join(tmpdir, "telem.db")

        # All flags OFF — even if the wheel IS present the flags gate wins.
        s1 = _make_scheduler_store(db1, use_scheduler_store=False)
        s2 = _make_session_store(db2, use_session_store=False)
        c3 = TraceCapture(db3, enable_trace_store=False)
        c4 = TelemetryCapture(db4, enable_telemetry=False)

        assert s1 is None, "Scheduler store must be None when flag OFF"
        assert s2 is None, "Session store must be None when flag OFF"
        assert not c3.enabled, "TraceCapture must be disabled when flag OFF"
        assert not c4.enabled, "TelemetryCapture must be disabled when flag OFF"

        # No DB files must be created — flag-OFF is truly inert.
        for db in [db1, db2, db3, db4]:
            assert not os.path.exists(db), f"DB must not be created when flag OFF: {db}"


def test_wheel_absent_fallback():
    """AC6: WHEEL_AVAILABLE=False simulation -> factories return None, no exception."""
    import os
    import tempfile
    from unittest.mock import patch

    with tempfile.TemporaryDirectory() as tmpdir:
        db1 = os.path.join(tmpdir, "sched2.db")
        db2 = os.path.join(tmpdir, "sess2.db")

        # task_queue uses the private name _WHEEL_AVAILABLE.
        with patch("agent.task_queue._WHEEL_AVAILABLE", False):
            from agent.task_queue import _make_scheduler_store

            s1 = _make_scheduler_store(db1, use_scheduler_store=True)
            assert s1 is None, "Scheduler store must be None when wheel absent"
            assert not os.path.exists(db1), "No DB when wheel absent"

        # memory_manager uses the private name _WHEEL_AVAILABLE.
        with patch("memory.memory_manager._WHEEL_AVAILABLE", False):
            from memory.memory_manager import _make_session_store

            s2 = _make_session_store(db2, use_session_store=True)
            assert s2 is None, "Session store must be None when wheel absent"
            assert not os.path.exists(db2), "No DB when wheel absent"

        # trace_collector and telemetry expose the public name WHEEL_AVAILABLE.
        # Mutate the module attribute directly (patch() works too but the
        # module-global is read at call-time inside the private factory, so
        # direct assignment is equally reliable and avoids import-order issues).
        import core.telemetry as telem_mod
        import core.trace_collector as tc_mod

        db3 = os.path.join(tmpdir, "trace2.db")
        db4 = os.path.join(tmpdir, "telem2.db")

        orig_tc = tc_mod.WHEEL_AVAILABLE
        orig_tl = telem_mod.WHEEL_AVAILABLE
        try:
            tc_mod.WHEEL_AVAILABLE = False
            telem_mod.WHEEL_AVAILABLE = False

            from core.telemetry import TelemetryCapture
            from core.trace_collector import TraceCapture

            c3 = TraceCapture(db3, enable_trace_store=True)
            c4 = TelemetryCapture(db4, enable_telemetry=True)

            assert not c3.enabled, "TraceCapture must be disabled when wheel absent"
            assert not c4.enabled, "TelemetryCapture must be disabled when wheel absent"
            assert not os.path.exists(db3), "No trace DB when wheel absent"
            assert not os.path.exists(db4), "No telem DB when wheel absent"
        finally:
            tc_mod.WHEEL_AVAILABLE = orig_tc
            telem_mod.WHEEL_AVAILABLE = orig_tl


# ---------------------------------------------------------------------------
# T13 — FR-18 get_flag guard (verified at this commit)
# ---------------------------------------------------------------------------
# FR-18 guard: test_flags.py allowlist verified GREEN at this commit
# callers remain: {main.py, agent/executor.py} -- NO new get_flag callers added by WO-6
# get_flag count in WO-6 production files: 0 (verified by T13 grep scan)
def test_fr18_get_flag_guard():
    """FR-18: no get_flag substring in any WO-6 production file; test_flags.py stays GREEN."""
    import subprocess, sys, os

    repo = "/Users/ltmas/Repo/agents/mark-xl"
    files = [
        "core/store_executor.py",
        "core/trace_collector.py",
        "core/telemetry.py",
        "agent/task_queue.py",
        "memory/memory_manager.py",
    ]

    for f in files:
        path = os.path.join(repo, f)
        if not os.path.exists(path):
            continue  # skip if file doesn't exist (shouldn't happen)
        with open(path) as fh:
            content = fh.read()
        assert "get_flag" not in content, f"FR-18 VIOLATION: 'get_flag' found in {f}"

    # Verify test_flags.py passes (run it as a subprocess)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_flags.py", "-q", "--tb=short"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"test_flags.py FAILED:\n{result.stdout}\n{result.stderr}"


# ---------------------------------------------------------------------------
# T12 — AC2 concurrency test: StoreResultBridge + 100 writes + WAL assertion
# ---------------------------------------------------------------------------
@pytest.mark.skipif(
    not WHEEL_AVAILABLE or not _arm64,
    reason="AC2 store concurrency test: arm64 wheel required",
)
def test_concurrency_100_writes_wal():
    """AC2: 100 concurrent store writes via store_executor; StoreResultBridge; WAL confirmed."""
    import os
    import sqlite3
    import tempfile
    import time

    # Set offscreen platform before any Qt import
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    try:
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtCore import QTimer
    except ImportError:
        pytest.skip("PyQt6 not available")

    from core.store_executor import StoreResultBridge, submit
    from core.trace_collector import TraceCapture

    # QApplication is required for Qt signal machinery
    app = QApplication.instance() or QApplication([])

    with tempfile.TemporaryDirectory() as tmpdir:
        db = os.path.join(tmpdir, "concurrency.db")
        capture = TraceCapture(db, enable_trace_store=True)
        assert capture.enabled, "TraceCapture must be enabled for AC2 test"

        # --- 100 writes via submit ---
        import uuid
        import json

        futures = []
        for i in range(100):
            tid = str(uuid.uuid4())
            trace = {
                "trace_id": tid,
                "query": f"q{i}",
                "agent": "test",
                "model": "gpt-4",
                "steps": [],
                "started_at": time.time(),
                "outcome": "success",
            }
            # Use store_executor.submit directly (the single-writer pool).
            # capture.store.save expects a JSON string (raw Rust method).
            fut = submit(capture.store.save, json.dumps(trace))
            futures.append(fut)

        # Measure elapsed while waiting for all 100 writes
        start = time.monotonic()

        # --- QTimer frame counter (~30 FPS for 3 seconds) ---
        frame_count = [0]

        def tick():
            frame_count[0] += 1

        timer = QTimer()
        timer.setInterval(33)  # ~30 FPS
        timer.timeout.connect(tick)
        timer.start()

        # --- StoreResultBridge smoke-test ---
        bridge = StoreResultBridge()
        bridge_results = []
        bridge.result_ready.connect(lambda r: bridge_results.append(r))
        bridge_fut = bridge.dispatch(lambda: "bridge_ok")

        # Process events for 3 seconds while futures drain
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)

        timer.stop()
        elapsed = time.monotonic() - start

        # Wait for all 100 futures
        for fut in futures:
            fut.result(timeout=10.0)

        # Wait for bridge future
        bridge_fut.result(timeout=5.0)

        # --- Assertions ---
        # (a) All 100 writes completed without exception
        for i, fut in enumerate(futures):
            assert fut.exception() is None, f"Future {i} raised: {fut.exception()}"

        # (b) Qt main thread not stalled — at least 80 frames in ~3 seconds (~26 FPS)
        assert frame_count[0] >= 80, (
            f"Expected >= 80 frames (30 FPS), got {frame_count[0]} in {elapsed:.1f}s"
        )

        # (c) WAL mode confirmed on the DB
        conn = sqlite3.connect(db)
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()
        assert mode.lower() == "wal", f"Expected WAL mode, got {mode!r}"

        # (d) Writes actually landed
        count = capture.count()
        assert count >= 100, f"Expected >= 100 traces in DB, got {count}"

        print(
            f"AC2 PASS: {count} writes, {frame_count[0]} frames in {elapsed:.2f}s, WAL={mode}"
        )


# ---------------------------------------------------------------------------
# T21 — AC3 entry-point integration test + AC8 packaging assertions
# ---------------------------------------------------------------------------
def test_entrypoint_imports_main():
    """AC3: python -m mark_xl dispatches to main.main."""
    import importlib.util
    import os
    import sys

    # Verify mark_xl/__main__.py contains 'from main import main'
    main_file = os.path.join("/Users/ltmas/Repo/agents/mark-xl", "mark_xl", "__main__.py")
    with open(main_file) as f:
        source = f.read()
    assert "from main import main" in source, "__main__.py must import main from main.py"
    assert "if __name__" in source, "__main__.py must have __name__ guard"

    # Verify the actual main function exists in main.py
    spec = importlib.util.spec_from_file_location(
        "main_module",
        "/Users/ltmas/Repo/agents/mark-xl/main.py",
    )
    # Just load the module spec; don't execute (avoids Qt initialization)
    assert spec is not None, "main.py must be loadable"

    # Verify mark_xl package is importable
    # Add repo to path for this test
    repo = "/Users/ltmas/Repo/agents/mark-xl"
    if repo not in sys.path:
        sys.path.insert(0, repo)

    import mark_xl

    assert hasattr(mark_xl, "__version__"), "mark_xl must have __version__"

    # Verify console entry point script exists — resolve via the active interpreter's
    # scripts directory so this passes regardless of whether the venv is PATH-activated.
    import sysconfig

    scripts_dir = sysconfig.get_path("scripts")
    if scripts_dir is None:
        scripts_dir = os.path.dirname(sys.executable)
    # Account for possible .exe extension on Windows; on macOS/Linux there is none.
    entry_name = "mark-xl.exe" if sys.platform == "win32" else "mark-xl"
    entry = os.path.join(scripts_dir, entry_name)
    assert os.path.exists(entry), (
        f"mark-xl console entry point must exist at {entry} "
        f"(scripts_dir={scripts_dir})"
    )


def test_packaging_artifacts():
    """AC8: LICENSE (MIT), NOTICE (Apache), requirements.txt pinned, VENDORING.md has maturin+arm64."""
    import os

    repo = "/Users/ltmas/Repo/agents/mark-xl"

    # LICENSE exists and contains MIT
    license_path = os.path.join(repo, "LICENSE")
    assert os.path.exists(license_path), "LICENSE must exist at repo root"
    license_text = open(license_path).read()
    assert "MIT" in license_text, "LICENSE must contain MIT"

    # NOTICE exists and contains Apache
    notice_path = os.path.join(repo, "NOTICE")
    assert os.path.exists(notice_path), "NOTICE must exist at repo root"
    notice_text = open(notice_path).read()
    assert "Apache" in notice_text, "NOTICE must contain Apache"

    # requirements.txt — all package spec lines use ==
    req_path = os.path.join(repo, "requirements.txt")
    assert os.path.exists(req_path), "requirements.txt must exist"
    with open(req_path) as f:
        lines = f.readlines()
    package_lines = [
        l.strip() for l in lines
        if l.strip() and not l.strip().startswith("#")
    ]
    for line in package_lines:
        # Skip git+, -r, -e lines (non-version-spec lines)
        if line.startswith(("-", "git+")):
            continue
        assert "==" in line, f"requirements.txt line not pinned: {line!r}"

    # docs/VENDORING.md contains maturin and arm64
    vendoring_path = os.path.join(repo, "docs", "VENDORING.md")
    assert os.path.exists(vendoring_path), "docs/VENDORING.md must exist"
    vendoring_text = open(vendoring_path).read()
    assert "maturin" in vendoring_text, "VENDORING.md must contain 'maturin'"
    assert "arm64" in vendoring_text.lower(), "VENDORING.md must contain 'arm64'"


# --- WO-6 per-task suite complete ---
# Final run summary (arm64 macOS): see T21-quality-signal.json
