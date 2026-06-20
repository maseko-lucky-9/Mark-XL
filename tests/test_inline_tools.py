"""
T11 — Inline tool tests: save_memory, agent_task, shutdown_jarvis.

These three tool names are handled INSIDE JarvisLocal._execute_tool and are
never routed to the actions/ sub-modules.  Each test calls _execute_tool as
an unbound method with a minimal MagicMock 'self', then asserts the specific
inline contract.
"""
import os
import sys
import time
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure project root is importable (conftest already does this; belt-and-suspenders)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main as main_module
from main import JarvisLocal


# ---------------------------------------------------------------------------
# Shared helper — mirrors _make_fake_jarvis from test_dispatch.py
# ---------------------------------------------------------------------------

def _make_fake_jarvis():
    """
    Minimal mock 'self' accepted by JarvisLocal._execute_tool.

    Attributes touched by _execute_tool before the inline branches:
      - self.ui.set_state(...)   → absorbed by MagicMock
      - self.ui.muted            → MagicMock is truthy → the
                                   `if not self.ui.muted` guard stays False
                                   (harmless; doesn't affect inline paths)
      - self.ui.current_file     → None (prevents file_processor path mutation)
      - self.speak               → MagicMock
      - self.speak_error(...)    → absorbed by MagicMock
    """
    fake = MagicMock()
    fake.ui = MagicMock()
    fake.ui.current_file = None
    fake.speak = MagicMock()
    return fake


# ---------------------------------------------------------------------------
# T11-1: save_memory
# ---------------------------------------------------------------------------

def test_save_memory_returns_silent(monkeypatch):
    """save_memory inline handler returns __SILENT__ without routing to actions/."""
    fake_self = _make_fake_jarvis()

    # Prevent real file I/O from update_memory (module-level import in main.py:72)
    mock_update_memory = MagicMock()
    monkeypatch.setattr("main.update_memory", mock_update_memory)

    args = {"category": "notes", "key": "test_key", "value": "test_value"}
    result = JarvisLocal._execute_tool(fake_self, "save_memory", args)

    # Contract 1: return value must be exactly __SILENT__
    assert result == "__SILENT__", (
        f"Expected '__SILENT__', got {result!r}"
    )

    # Contract 2: the real memory writer was called (inline, not actions-routed)
    mock_update_memory.assert_called_once_with(
        {"notes": {"test_key": {"value": "test_value"}}}
    )

    # Contract 3: no error path was taken (speak_error should not have been called)
    fake_self.speak_error.assert_not_called()


def test_save_memory_skips_update_when_key_empty(monkeypatch):
    """save_memory still returns __SILENT__ even when key is empty (no-op update)."""
    fake_self = _make_fake_jarvis()

    mock_update_memory = MagicMock()
    monkeypatch.setattr("main.update_memory", mock_update_memory)

    # Missing key → the `if key and value:` guard fires; update_memory not called
    args = {"category": "notes", "key": "", "value": "test_value"}
    result = JarvisLocal._execute_tool(fake_self, "save_memory", args)

    assert result == "__SILENT__"
    mock_update_memory.assert_not_called()


# ---------------------------------------------------------------------------
# T11-2: agent_task
# ---------------------------------------------------------------------------

def test_agent_task_enqueues_to_queue(monkeypatch):
    """agent_task handler enqueues to task queue, not routed to actions/."""
    fake_self = _make_fake_jarvis()

    # Build a mock queue whose .submit() returns a fake task ID
    mock_queue = MagicMock()
    mock_queue.submit = MagicMock(return_value="task-abc123")

    # The branch does `from agent.task_queue import get_queue, TaskPriority` at
    # call time — Python resolves `get_queue` via sys.modules['agent.task_queue'].
    # Patching the attribute on that module object intercepts the local import.
    monkeypatch.setattr("agent.task_queue.get_queue", lambda: mock_queue)

    args = {"goal": "do something useful", "priority": "normal"}
    result = JarvisLocal._execute_tool(fake_self, "agent_task", args)

    # Contract 1: return string must contain the task ID
    assert "task-abc123" in result, (
        f"Expected task ID in result, got {result!r}"
    )

    # Contract 2: submit was called exactly once with the correct goal
    mock_queue.submit.assert_called_once()
    call_kwargs = mock_queue.submit.call_args.kwargs
    assert call_kwargs.get("goal") == "do something useful", (
        f"Expected goal='do something useful', got {call_kwargs!r}"
    )

    # Contract 3: no error path
    fake_self.speak_error.assert_not_called()


def test_agent_task_maps_priority_high(monkeypatch):
    """agent_task correctly maps the 'high' priority string to TaskPriority.HIGH."""
    from agent.task_queue import TaskPriority

    fake_self = _make_fake_jarvis()

    captured = {}

    def _fake_submit(goal, priority, speak):
        captured["priority"] = priority
        return "task-xyz"

    mock_queue = MagicMock()
    mock_queue.submit = _fake_submit
    monkeypatch.setattr("agent.task_queue.get_queue", lambda: mock_queue)

    args = {"goal": "urgent task", "priority": "high"}
    result = JarvisLocal._execute_tool(fake_self, "agent_task", args)

    assert captured.get("priority") == TaskPriority.HIGH
    assert "task-xyz" in result


# ---------------------------------------------------------------------------
# T11-3: shutdown_jarvis
# ---------------------------------------------------------------------------

def test_shutdown_jarvis_calls_os_exit(monkeypatch, thread_guard):
    """shutdown_jarvis spawns a daemon thread that calls os._exit(0); os._exit is mocked.

    The _shutdown closure calls speak("Goodbye.") then sleeps 2.5s before os._exit.
    We poll up to 5 seconds in 50ms increments to avoid a fixed sleep that is both
    slower than needed and brittle if the OS scheduler delays the thread.
    """
    fake_self = _make_fake_jarvis()

    exit_mock = MagicMock()

    # The _shutdown closure does `import time, os` — this fetches the cached
    # `os` module from sys.modules.  Patching os._exit on that module object
    # is the correct and only intercept point.
    monkeypatch.setattr(os, "_exit", exit_mock)

    args = {}
    result = JarvisLocal._execute_tool(fake_self, "shutdown_jarvis", args)

    # Contract 1: immediate return before the thread sleeps
    assert result == "Shutting down.", (
        f"Expected 'Shutting down.', got {result!r}"
    )

    # Poll until os._exit(0) is called (ceiling: 5s — thread sleeps 2.5s first).
    # Using a polling loop rather than a fixed sleep cuts average CI wall time
    # from ~3s to ~2.55s and avoids spurious failures if the thread runs late.
    deadline = time.time() + 5.0
    while time.time() < deadline and not exit_mock.called:
        time.sleep(0.05)

    exit_mock.assert_called_once_with(0)


def test_shutdown_jarvis_speaks_goodbye(monkeypatch, thread_guard):
    """shutdown_jarvis thread calls self.speak('Goodbye.') before exiting.

    speak("Goodbye.") is invoked at the START of _shutdown (before the 2.5s sleep),
    so the polling ceiling of 2 seconds is sufficient to observe it.
    """
    fake_self = _make_fake_jarvis()

    # Suppress os._exit so the test process is not killed
    monkeypatch.setattr(os, "_exit", MagicMock())

    args = {}
    JarvisLocal._execute_tool(fake_self, "shutdown_jarvis", args)

    # Poll until speak("Goodbye.") is called (ceiling: 2s — speak fires before the
    # 2.5s sleep, so this is generous while being faster than the old 3s fixed sleep).
    deadline = time.time() + 2.0
    while time.time() < deadline and not fake_self.speak.called:
        time.sleep(0.05)

    fake_self.speak.assert_called_with("Goodbye.")
