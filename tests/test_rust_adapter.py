"""Tests for core.mark_xl_rust_adapter (WO-1).

Covers both the wheel-present path (the wheel IS editable-installed in
.venv-mac, so these run live) and the simulated wheel-absent path (ImportError
faked via sys.modules + importlib.reload, with clean teardown). Includes the
load-bearing GIL no-freeze proof (AC5) and the import-safety / gate tests.
"""

import importlib
import logging
import sys
import threading
import time

import pytest

import core.mark_xl_rust_adapter as adapter
from core.mark_xl_rust_adapter import (
    WHEEL_AVAILABLE,
    RustResultBridge,
    check_ssrf,
    get_loop_guard,
    gil_probe,
    is_sensitive_file,
    run_in_executor,
)

_skip_no_wheel = pytest.mark.skipif(
    not WHEEL_AVAILABLE, reason="mark_xl_rust wheel not built for this platform"
)


# ---------------------------------------------------------------------------
# Import safety (unconditional) — module imports cleanly regardless of wheel.
# ---------------------------------------------------------------------------
def test_module_imports_and_exposes_contract():
    assert isinstance(adapter.WHEEL_AVAILABLE, bool)
    assert hasattr(adapter, "get_loop_guard")
    assert hasattr(adapter, "is_sensitive_file")
    assert hasattr(adapter, "check_ssrf")
    assert hasattr(adapter, "run_in_executor")
    assert hasattr(adapter, "gil_probe")
    assert issubclass(adapter.RustResultBridge, object)


# ---------------------------------------------------------------------------
# Wheel-present path (live).
# ---------------------------------------------------------------------------
@_skip_no_wheel
def test_wheel_available_is_true():
    assert WHEEL_AVAILABLE is True


@_skip_no_wheel
def test_get_loop_guard_returns_instance():
    guard = get_loop_guard()
    assert guard is not None


@_skip_no_wheel
def test_is_sensitive_file_returns_bool():
    result = is_sensitive_file("config/api_keys.json")
    assert result is not None
    assert isinstance(result, bool)


@_skip_no_wheel
def test_check_ssrf_passthrough():
    # Safe URL -> None (Rust Option::None). Wheel is present, so None here means
    # "safe", not "legacy mode".
    assert check_ssrf("https://example.com") is None
    # Cloud metadata endpoint -> blocked, str reason.
    blocked = check_ssrf("http://169.254.169.254/")
    assert isinstance(blocked, str)
    assert blocked  # non-empty reason


@_skip_no_wheel
def test_run_in_executor_resolves():
    fut = run_in_executor(lambda a, b: a + b, 2, 3)
    assert fut.result(timeout=5) == 5


@_skip_no_wheel
def test_rust_result_bridge_emits_on_main_thread(qapp):
    """dispatch() runs fn in the pool and emits result_ready on the MAIN thread."""
    bridge = RustResultBridge()
    captured = {}

    def _slot(payload):
        captured["payload"] = payload
        captured["on_main"] = threading.current_thread() is threading.main_thread()

    bridge.result_ready.connect(_slot)

    fut = bridge.dispatch(lambda x: x * 10, 7)

    # Spin the event loop until the queued cross-thread emit is delivered.
    deadline = time.time() + 5.0
    while "payload" not in captured and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.005)

    fut.result(timeout=5)  # ensure the worker finished cleanly
    qapp.processEvents()  # drain any final queued event

    assert captured.get("payload") == 70
    assert captured.get("on_main") is True


# ---------------------------------------------------------------------------
# GIL no-freeze (AC5) — load-bearing.
# ---------------------------------------------------------------------------
@_skip_no_wheel
def test_gil_no_freeze_main_thread_advances():
    """While _gil_probe blocks ~200ms in Rust, the main thread keeps advancing.

    Proves py.allow_threads released the GIL: if it did NOT, the main-thread
    counter would not advance during the Rust block.
    """
    fut = gil_probe(200)
    assert fut is not None

    counter = 0
    start = time.time()
    # Spin the main thread until the Rust block finishes (future done).
    while not fut.done() and (time.time() - start) < 2.0:
        counter += 1
        time.sleep(0.001)

    fut.result(timeout=5)  # await completion so nothing leaks into shutdown

    # During a 200ms GIL-released block, the main thread advanced many times.
    assert counter > 10, f"main thread only advanced {counter}x — GIL not released"


# ---------------------------------------------------------------------------
# Wheel-absent path (simulated ImportError, no uninstall).
# ---------------------------------------------------------------------------
@pytest.fixture
def reloaded_without_wheel(caplog):
    """Reload the adapter with mark_xl_rust blocked, restore cleanly after."""
    saved = sys.modules.get("mark_xl_rust", None)
    # Setting to None makes `import mark_xl_rust` raise ImportError.
    sys.modules["mark_xl_rust"] = None
    try:
        with caplog.at_level(logging.WARNING, logger="core.mark_xl_rust_adapter"):
            importlib.reload(adapter)
            yield adapter, caplog
    finally:
        # Restore the real module (or remove the sentinel) and reload so every
        # subsequent test sees the live wheel again.
        if saved is not None:
            sys.modules["mark_xl_rust"] = saved
        else:
            sys.modules.pop("mark_xl_rust", None)
        importlib.reload(adapter)


def test_wheel_absent_degrades_gracefully(reloaded_without_wheel):
    mod, caplog = reloaded_without_wheel

    assert mod.WHEEL_AVAILABLE is False

    # Factories return None in legacy mode.
    assert mod.get_loop_guard() is None
    assert mod.is_sensitive_file("config/api_keys.json") is None
    assert mod.check_ssrf("https://example.com") is None
    assert mod.gil_probe(50) is None

    # A "legacy mode" log was emitted.
    assert any("legacy mode" in rec.getMessage() for rec in caplog.records)


def test_wheel_restored_after_reload_fixture():
    """Sanity: after the wheel-absent fixture teardown, the live wheel is back."""
    importlib.reload(adapter)
    if WHEEL_AVAILABLE:  # only meaningful when a wheel exists in this env
        assert adapter.WHEEL_AVAILABLE is True
