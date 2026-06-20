"""
T10 — Parametrized dual-path routing test.

Verifies that both dispatch paths (main._execute_tool and agent.executor._call_tool)
correctly forward `player` and `speak` kwargs for all 17 tools.

The non-uniform patch matrix is intentional and load-bearing:
  - main leg patches `main.<bound_name>` because main.py binds functions at module top
  - executor leg patches `actions.<mod>.<fn>` because _call_tool re-imports at call time
"""
import sys
import pytest
from unittest.mock import MagicMock, patch

# Ensure project root is importable (conftest already does this, but belt-and-suspenders)
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ---------------------------------------------------------------------------
# Non-uniform patch matrix — VERBATIM, do NOT simplify or derive from first principles.
# Each row: (tool_name, main_bound_name, executor_patch_target)
# ---------------------------------------------------------------------------
TOOL_MATRIX = [
    ("open_app",          "main.open_app",          "actions.open_app.open_app"),
    ("web_search",        "main.web_search_action", "actions.web_search.web_search"),
    ("weather_report",    "main.weather_action",    "actions.weather_report.weather_action"),
    ("send_message",      "main.send_message",      "actions.send_message.send_message"),
    ("reminder",          "main.reminder",          "actions.reminder.reminder"),
    ("youtube_video",     "main.youtube_video",     "actions.youtube_video.youtube_video"),
    ("screen_process",    "main.screen_process",    "actions.screen_processor.screen_process"),
    ("computer_settings", "main.computer_settings", "actions.computer_settings.computer_settings"),
    ("browser_control",   "main.browser_control",   "actions.browser_control.browser_control"),
    ("file_controller",   "main.file_controller",   "actions.file_controller.file_controller"),
    ("desktop_control",   "main.desktop_control",   "actions.desktop.desktop_control"),
    ("code_helper",       "main.code_helper",       "actions.code_helper.code_helper"),
    ("dev_agent",         "main.dev_agent",         "actions.dev_agent.dev_agent"),
    ("computer_control",  "main.computer_control",  "actions.computer_control.computer_control"),
    ("game_updater",      "main.game_updater",      "actions.game_updater.game_updater"),
    ("flight_finder",     "main.flight_finder",     "actions.flight_finder.flight_finder"),
    ("file_processor",    "main.file_processor",    "actions.file_processor.file_processor"),
]


# ---------------------------------------------------------------------------
# Helper: build a minimal fake JarvisLocal instance
# ---------------------------------------------------------------------------
def _make_fake_jarvis(player_mock, speak_mock):
    """
    Create a minimal mock 'self' for JarvisLocal._execute_tool.

    We call _execute_tool as an unbound method so we control `self` entirely.
    Attributes accessed in _execute_tool:
      - self.ui.set_state(...)   → safe; MagicMock absorbs calls
      - self.ui.muted            → bool-like; MagicMock is truthy by default,
                                   so the `if not self.ui.muted` guard is False
                                   meaning set_state("LISTENING") won't be called
                                   (harmless; no actual assertion on it)
      - self.ui.current_file     → set to None for file_processor special case
      - self.speak               → our speak_mock
      - self.speak_error(...)    → auto-absorbed by MagicMock
    """
    fake = MagicMock()
    fake.ui = player_mock
    fake.speak = speak_mock
    # file_processor special case: prevent args mutation from MagicMock truthy value
    fake.ui.current_file = None
    return fake


# ---------------------------------------------------------------------------
# Parametrized test: one case per tool, 17 total
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("tool_name,main_bound,executor_target", TOOL_MATRIX)
def test_dual_path_routes_player_and_speak(tool_name, main_bound, executor_target, qapp):
    """Both dispatch paths forward player and speak for each of the 17 tools."""
    player_mock = MagicMock(name="player")
    speak_mock = MagicMock(name="speak")
    # file_processor special case: no file_path in args, current_file must be None
    player_mock.current_file = None

    args = {}

    # ── Main leg ─────────────────────────────────────────────────────────────
    # Patch the module-level binding in main (not the source module) because
    # main.py imports and binds at module top (lines 75-91).
    import main as main_module
    from main import JarvisLocal

    with patch(main_bound, new=MagicMock(return_value="ok")) as main_mock:
        fake_self = _make_fake_jarvis(player_mock, speak_mock)

        # Call _execute_tool as an unbound method, passing fake_self as `self`
        JarvisLocal._execute_tool(fake_self, tool_name, args)

        main_mock.assert_called_once()
        call_kwargs = main_mock.call_args.kwargs
        assert call_kwargs.get("player") == player_mock, (
            f"Main leg: player not forwarded for {tool_name}. "
            f"Got call_args: {main_mock.call_args}"
        )
        assert call_kwargs.get("speak") == speak_mock, (
            f"Main leg: speak not forwarded for {tool_name}. "
            f"Got call_args: {main_mock.call_args}"
        )

    # ── Executor leg ──────────────────────────────────────────────────────────
    # Patch the function inside the actions sub-module because _call_tool
    # re-imports at call time using `from actions.X import fn`, so patching
    # the source module's attribute is the correct intercept point.
    with patch(executor_target, new=MagicMock(return_value="ok")) as exec_mock:
        from agent.executor import _call_tool
        _call_tool(tool_name, args, player=player_mock, speak=speak_mock)

        exec_mock.assert_called_once()
        call_kwargs = exec_mock.call_args.kwargs
        assert call_kwargs.get("player") == player_mock, (
            f"Executor leg: player not forwarded for {tool_name}. "
            f"Got call_args: {exec_mock.call_args}"
        )
        assert call_kwargs.get("speak") == speak_mock, (
            f"Executor leg: speak not forwarded for {tool_name}. "
            f"Got call_args: {exec_mock.call_args}"
        )
