"""
test_regressions.py — WO-0 regression tests for bugs B1-B4.

B1 (T12): flight_finder missing speak= in main._execute_tool (flight_finder at main.py:896)
B2 (T13): file_processor missing from executor._call_tool dispatch
B3 (T14): unknown tool silently called _run_generated_code instead of raising
B4 (T15): PLANNER_PROMPT missing file_processor tool entry

Regression tests are PROVE-IT tests: they verify that known bugs have been fixed.
Each test MUST pass with the current (fixed) codebase and WOULD FAIL on the pre-fix version.
"""
import inspect
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure project root is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main as main_module
from main import JarvisLocal


# ─────────────────────────────────────────────────────────────────────────────
# Shared helper — minimal mock 'self' for JarvisLocal._execute_tool
# ─────────────────────────────────────────────────────────────────────────────

def _make_fake_jarvis():
    """
    Minimal mock 'self' accepted by JarvisLocal._execute_tool.

    Attributes:
      - self.ui.set_state(...) → absorbed by MagicMock
      - self.ui.muted → MagicMock is truthy
      - self.ui.current_file → None (prevents file_processor path mutation)
      - self.speak → MagicMock (the critical spy for regression tests)
      - self.speak_error(...) → absorbed by MagicMock
    """
    fake = MagicMock()
    fake.ui = MagicMock()
    fake.ui.current_file = None
    fake.speak = MagicMock()
    return fake


# ─────────────────────────────────────────────────────────────────────────────
# B1 — flight_finder speak forwarding (TAS-2, T12)
# ─────────────────────────────────────────────────────────────────────────────

def test_b1_flight_finder_speak_forwarded():
    """B1 regression: flight_finder call in main._execute_tool must forward speak=self.speak.

    PRE-FIX FAILURE: main.py:896 was `flight_finder(parameters=args, player=self.ui)`
    — no speak= argument. This test verifies the fix is in place.

    POST-FIX: main.py:896 is `flight_finder(parameters=args, player=self.ui, speak=self.speak)`
    — speak keyword is required and must point to self.speak exactly.
    """
    fake_self = _make_fake_jarvis()
    speak_mock = fake_self.speak

    # Mock the flight_finder module-level import in main.py:76
    with patch("main.flight_finder", new=MagicMock(return_value="Flight details returned")) as flight_mock:
        result = JarvisLocal._execute_tool(fake_self, "flight_finder", {})

        # Contract 1: flight_finder was called
        flight_mock.assert_called_once()

        # Contract 2: speak= kwarg must be present and point to self.speak
        call_kwargs = flight_mock.call_args.kwargs
        assert "speak" in call_kwargs, (
            "B1: speak kwarg is MISSING from flight_finder call. "
            "Expected flight_finder(..., speak=self.speak) but got flight_finder(..., "
            f"{', '.join(call_kwargs.keys())})"
        )

        assert call_kwargs["speak"] is speak_mock, (
            f"B1: speak kwarg has wrong value. Expected {speak_mock!r} but got {call_kwargs['speak']!r}"
        )

        # Contract 3: other required kwargs (parameters, player) are also present
        assert "parameters" in call_kwargs, "B1: parameters kwarg is missing"
        assert "player" in call_kwargs, "B1: player kwarg is missing"

        # Contract 4: result is well-formed
        assert isinstance(result, str), f"Expected str result, got {type(result)}"
        assert result != "", "Expected non-empty result"

        # Contract 5: no error path was taken
        fake_self.speak_error.assert_not_called()


def test_b1_flight_finder_call_with_args():
    """B1 regression: flight_finder forwards user args (parameters) correctly.

    Verify that the parameters dict is passed through exactly as provided.
    """
    fake_self = _make_fake_jarvis()
    speak_mock = fake_self.speak

    test_args = {
        "departure": "SFO",
        "destination": "JFK",
        "date": "2025-12-25"
    }

    with patch("main.flight_finder", new=MagicMock(return_value="Round-trip found")) as flight_mock:
        result = JarvisLocal._execute_tool(fake_self, "flight_finder", test_args)

        # Verify parameters were forwarded correctly
        call_kwargs = flight_mock.call_args.kwargs
        assert call_kwargs["parameters"] == test_args, (
            f"B1: parameters were not forwarded correctly. "
            f"Expected {test_args}, got {call_kwargs['parameters']}"
        )

        # Verify speak is still forwarded
        assert call_kwargs["speak"] is speak_mock, (
            "B1: speak kwarg was lost when forwarding parameters"
        )


# ─────────────────────────────────────────────────────────────────────────────
# B2 — executor dispatches file_processor (TAS-3)
# ─────────────────────────────────────────────────────────────────────────────

def test_b2_executor_dispatches_file_processor():
    """B2 regression: executor._call_tool must dispatch 'file_processor' to
    actions.file_processor.file_processor.

    Pre-fix failure: executor.py had no 'file_processor' branch — it fell through
    to the silent _run_generated_code fallback. The fix (T06) adds the branch.
    """
    from agent.executor import _call_tool

    player_mock = MagicMock(name="player")
    speak_mock = MagicMock(name="speak")

    with patch("actions.file_processor.file_processor", new=MagicMock(return_value="ok")) as fp_mock:
        result = _call_tool("file_processor", {"file_path": "/tmp/test.txt", "action": "summarize"},
                           player=player_mock, speak=speak_mock)

        fp_mock.assert_called_once()
        call_kwargs = fp_mock.call_args.kwargs
        assert call_kwargs.get("player") is player_mock, f"B2: player not forwarded. Got {call_kwargs}"
        assert call_kwargs.get("speak") is speak_mock, f"B2: speak not forwarded. Got {call_kwargs}"
        assert result == "ok"


# ─────────────────────────────────────────────────────────────────────────────
# B3 — unknown tool raises ValueError (TAS-4)
# ─────────────────────────────────────────────────────────────────────────────

def test_b3_unknown_tool_raises():
    """B3 regression: executor._call_tool must raise ValueError for unknown tool names.

    Pre-fix failure: executor.py had an else-fallback at :243-245 that silently called
    _run_generated_code for ANY unknown tool. The fix (T06) replaces it with a raise.
    """
    from agent.executor import _call_tool

    with pytest.raises(ValueError, match="Unknown tool"):
        _call_tool("__nonexistent_tool__", {})

    with pytest.raises(ValueError):
        _call_tool("totally_made_up_tool_xyz", {})


def test_b3_generated_code_branch_still_works():
    """B3 companion: the explicit generated_code branch must NOT raise.

    B3 only removes the silent else-fallback; the named 'generated_code' tool
    branch (executor.py:233-237) must remain intact.
    """
    from agent.executor import _call_tool
    from unittest.mock import patch

    # Mock _run_generated_code to avoid actually running code
    with patch("agent.executor._run_generated_code", return_value="generated ok") as gen_mock:
        result = _call_tool("generated_code", {"description": "test task"})
        gen_mock.assert_called_once_with("test task", speak=None)
        assert result == "generated ok"


# ─────────────────────────────────────────────────────────────────────────────
# B4 — PLANNER_PROMPT contains file_processor, NOT agent_task (TAS-5)
# ─────────────────────────────────────────────────────────────────────────────

def test_b4_planner_prompt_contains_file_processor():
    """B4 regression: PLANNER_PROMPT must include file_processor tool.

    Pre-fix failure: planner.py had no 'file_processor' entry in PLANNER_PROMPT.
    The fix (T08) adds it. No agent_task allowed (Decision C: no executor branch).
    """
    from agent.planner import PLANNER_PROMPT

    assert "file_processor" in PLANNER_PROMPT, (
        "B4: file_processor not found in PLANNER_PROMPT. "
        "This means planner cannot emit file_processor steps."
    )


def test_b4_agent_task_not_in_planner_prompt():
    """B4 companion: agent_task must NOT be in PLANNER_PROMPT.

    Decision C (frozen): agent_task has no executor branch. Post-B3, a planner-emitted
    agent_task step would raise ValueError. The spec.md TAS-5 '(and agent_task)'
    parenthetical is SUPERSEDED by Decision C.
    """
    from agent.planner import PLANNER_PROMPT

    assert "agent_task" not in PLANNER_PROMPT, (
        "agent_task should NOT be in PLANNER_PROMPT — it has no executor branch "
        "and would raise ValueError post-B3."
    )


# ─────────────────────────────────────────────────────────────────────────────
# FR-2.2 — **kwargs absorption safety net (WO-0)
# ─────────────────────────────────────────────────────────────────────────────

def test_kwargs_absorption_safety_net():
    """FR-2.2 regression: every action function must accept **kwargs so callers
    still passing legacy response= or session_memory= keyword args do not raise.

    Approach: inspect.signature(fn).bind() with stray kwargs succeeds IFF the
    signature contains a VAR_KEYWORD (**kwargs) parameter. No function is actually
    called — zero side effects.

    PRE-FIX FAILURE: old signatures had explicit `response=` and `session_memory=`
    positional params; re-passing them as keyword args would raise TypeError.
    POST-FIX: all 17 functions accept **kwargs and silently absorb any extra kwargs.
    """
    from actions.open_app import open_app
    from actions.web_search import web_search
    from actions.weather_report import weather_action
    from actions.send_message import send_message
    from actions.reminder import reminder
    from actions.youtube_video import youtube_video
    from actions.screen_processor import screen_process
    from actions.computer_settings import computer_settings
    from actions.browser_control import browser_control
    from actions.file_controller import file_controller
    from actions.desktop import desktop_control
    from actions.code_helper import code_helper
    from actions.dev_agent import dev_agent
    from actions.computer_control import computer_control
    from actions.game_updater import game_updater
    from actions.flight_finder import flight_finder
    from actions.file_processor import file_processor

    action_functions = [
        open_app,
        web_search,
        weather_action,
        send_message,
        reminder,
        youtube_video,
        screen_process,
        computer_settings,
        browser_control,
        file_controller,
        desktop_control,
        code_helper,
        dev_agent,
        computer_control,
        game_updater,
        flight_finder,
        file_processor,
    ]

    for fn in action_functions:
        try:
            inspect.signature(fn).bind({}, response="stray_resp", session_memory="stray_mem")
        except TypeError as exc:
            raise AssertionError(
                f"FR-2.2: {fn.__name__} does not absorb stray kwargs — "
                f"**kwargs is missing from its signature. bind() raised: {exc}"
            ) from exc
