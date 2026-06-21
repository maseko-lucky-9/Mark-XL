"""T7: main.py JarvisLocal._execute_tool flag-gate tests.

Verifies that _execute_tool routes generic tools through core.tool_registry.dispatch
when use_tool_registry is ON, falls back to the verbatim 17-arm baseline when OFF,
and that the save_memory PRE-TRY early-return stays flag-independent and never
touches the try block / dispatch.

_execute_tool is a method on JarvisLocal, which needs a QApplication-backed UI to
instantiate.  We avoid that by binding the *unbound* method to a MagicMock `self`
(only .ui.set_state, .ui.muted, .speak, .speak_error are exercised).
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ---------------------------------------------------------------------------
# Helper: a MagicMock standing in for `self` (a JarvisLocal instance)
# ---------------------------------------------------------------------------

def _make_self():
    """Build a MagicMock `self` with the attributes _execute_tool touches."""
    s = MagicMock()
    s.ui.muted = False
    s.ui.current_file = None
    return s


def _run(name, args, monkeypatch, *, flag, dispatch=None):
    """Bind JarvisLocal._execute_tool to a mock self and call it.

    flag      → value returned by the patched memory.config_manager.get_flag
    dispatch  → if given, monkeypatches core.tool_registry.dispatch to it
    Returns (result, self_mock).
    """
    import memory.config_manager as cm
    monkeypatch.setattr(cm, "get_flag", lambda *a, **k: flag)

    if dispatch is not None:
        import core.tool_registry as tr
        monkeypatch.setattr(tr, "dispatch", dispatch)

    from main import JarvisLocal
    s = _make_self()
    result = JarvisLocal._execute_tool(s, name, args)
    return result, s


# ---------------------------------------------------------------------------
# 1. flag-OFF: save_memory returns "__SILENT__" BEFORE the try block
# ---------------------------------------------------------------------------

def test_flag_off_save_memory_pre_try(monkeypatch):
    """flag-OFF: save_memory short-circuits to '__SILENT__' without dispatch."""
    import main
    saved = []
    monkeypatch.setattr(main, "update_memory", lambda d: saved.append(d))

    dispatch_called = []

    def _boom_dispatch(*a, **k):
        dispatch_called.append(True)
        raise AssertionError("dispatch must not be reached for save_memory")

    result, s = _run(
        "save_memory",
        {"category": "identity", "key": "name", "value": "Fatih"},
        monkeypatch,
        flag=False,
        dispatch=_boom_dispatch,
    )

    assert result == "__SILENT__"
    assert dispatch_called == [], "save_memory must never route through dispatch"
    assert saved == [{"identity": {"name": {"value": "Fatih"}}}]
    s.speak_error.assert_not_called()


# ---------------------------------------------------------------------------
# 2. flag-ON: save_memory STILL returns "__SILENT__" (flag-independent, pre-try)
# ---------------------------------------------------------------------------

def test_flag_on_save_memory_pre_try(monkeypatch):
    """flag-ON: save_memory still short-circuits — proves the early-return sits
    BEFORE the try block and is not affected by use_tool_registry.

    dispatch is patched to explode; if save_memory ever entered the try block it
    would route through dispatch and raise — the test would fail.
    """
    import main
    saved = []
    monkeypatch.setattr(main, "update_memory", lambda d: saved.append(d))

    dispatch_called = []

    def _boom_dispatch(*a, **k):
        dispatch_called.append(True)
        raise AssertionError("dispatch must not be reached for save_memory")

    result, s = _run(
        "save_memory",
        {"category": "identity", "key": "city", "value": "Ankara"},
        monkeypatch,
        flag=True,
        dispatch=_boom_dispatch,
    )

    assert result == "__SILENT__"
    assert dispatch_called == [], "save_memory must never route through dispatch"
    assert saved == [{"identity": {"city": {"value": "Ankara"}}}]
    s.speak_error.assert_not_called()


# ---------------------------------------------------------------------------
# 3. flag-ON: dispatch result empty → open_app fallback string applied
# ---------------------------------------------------------------------------

def test_flag_on_dispatch_with_fallback_open_app(monkeypatch):
    """flag-ON: dispatch returns '' for open_app → fallback 'Opened {app_name}.'"""
    dispatch_calls = []

    def _dispatch(name, params, **kw):
        dispatch_calls.append((name, params))
        return ""

    result, s = _run(
        "open_app",
        {"app_name": "Finder"},
        monkeypatch,
        flag=True,
        dispatch=_dispatch,
    )

    assert result == "Opened Finder."
    assert dispatch_calls and dispatch_calls[0][0] == "open_app"
    s.speak_error.assert_not_called()


# ---------------------------------------------------------------------------
# 4. flag-ON: dispatch returns non-str for screen_process → "Screen analyzed."
# ---------------------------------------------------------------------------

def test_flag_on_dispatch_screen_process_non_str(monkeypatch):
    """flag-ON: non-str dispatch result for screen_process coerces to the main
    sentinel 'Screen analyzed.' (differs from executor's 'Screen captured and
    analyzed.')."""
    result, s = _run(
        "screen_process",
        {"text": "what is on screen"},
        monkeypatch,
        flag=True,
        dispatch=lambda name, params, **kw: None,
    )

    assert result == "Screen analyzed."
    s.speak_error.assert_not_called()


# ---------------------------------------------------------------------------
# 5. flag-ON: dispatch raises ValueError for unknown tool → caught by except
# ---------------------------------------------------------------------------

def test_flag_on_unknown_tool(monkeypatch):
    """flag-ON: an unknown tool is intercepted by the registry pre-check BEFORE
    dispatch is ever reached. The pre-check (spec is None/inline/func is None)
    returns the clean 'Unknown tool: {name}' string — byte-identical to the
    flag-OFF baseline arm — so dispatch is NOT called and speak_error is NOT
    invoked (no error path)."""
    dispatch_calls = []

    def _dispatch(name, params, **kw):
        # Recording mock: must NOT be reached for an unknown tool (pre-check
        # intercepts first). Records the call so the test can prove it wasn't.
        dispatch_calls.append((name, params))
        raise ValueError(f"Unknown tool: {name}")

    result, s = _run(
        "unknown_xyz",
        {},
        monkeypatch,
        flag=True,
        dispatch=_dispatch,
    )

    assert result == "Unknown tool: unknown_xyz"
    assert dispatch_calls == [], "pre-check must intercept before dispatch is reached"
    s.speak_error.assert_not_called()


# ---------------------------------------------------------------------------
# 6. flag-ON: other tool (send_message) uses its EXACT fallback string
# ---------------------------------------------------------------------------

def test_flag_on_dispatch_send_message_fallback(monkeypatch):
    """flag-ON: empty dispatch result for send_message → 'Message sent to {receiver}.'"""
    result, s = _run(
        "send_message",
        {"receiver": "mom", "message_text": "hi", "platform": "WhatsApp"},
        monkeypatch,
        flag=True,
        dispatch=lambda name, params, **kw: "",
    )

    assert result == "Message sent to mom."
    s.speak_error.assert_not_called()


# ---------------------------------------------------------------------------
# 7. flag-OFF: unknown tool hits the verbatim baseline 'Unknown tool:' arm
# ---------------------------------------------------------------------------

def test_flag_off_unknown_tool_baseline(monkeypatch):
    """flag-OFF: unknown tool returns the verbatim 'Unknown tool: {name}' string
    (the baseline else-arm), and dispatch is never invoked."""
    dispatch_called = []

    def _boom_dispatch(*a, **k):
        dispatch_called.append(True)
        raise AssertionError("dispatch must not be reached when flag is OFF")

    result, s = _run(
        "unknown_xyz",
        {},
        monkeypatch,
        flag=False,
        dispatch=_boom_dispatch,
    )

    assert result == "Unknown tool: unknown_xyz"
    assert dispatch_called == []
    s.speak_error.assert_not_called()


# ---------------------------------------------------------------------------
# 8. flag-ON: dispatch returns '' for weather_report → fallback "Weather delivered."
# ---------------------------------------------------------------------------

def test_flag_on_dispatch_weather_report_fallback(monkeypatch):
    """flag-ON: empty dispatch result for weather_report → 'Weather delivered.'
    Covers the elif name == 'weather_report' branch (line 862).
    """
    result, s = _run(
        "weather_report",
        {"location": "Cape Town"},
        monkeypatch,
        flag=True,
        dispatch=lambda name, params, **kw: "",
    )

    assert result == "Weather delivered."
    s.speak_error.assert_not_called()


# ---------------------------------------------------------------------------
# 9. flag-ON: dispatch returns '' for reminder → fallback "Reminder set."
# ---------------------------------------------------------------------------

def test_flag_on_dispatch_reminder_fallback(monkeypatch):
    """flag-ON: empty dispatch result for reminder → 'Reminder set.'
    Covers the elif name == 'reminder' branch (line 866).
    """
    result, s = _run(
        "reminder",
        {"task": "buy milk", "time": "08:00"},
        monkeypatch,
        flag=True,
        dispatch=lambda name, params, **kw: "",
    )

    assert result == "Reminder set."
    s.speak_error.assert_not_called()


# ---------------------------------------------------------------------------
# 10. B2 — flag-ON unknown-tool parity with flag-OFF (clean string, no error)
# ---------------------------------------------------------------------------

def test_b2_flag_on_unknown_tool_parity(monkeypatch):
    """B2 — flag-ON and flag-OFF produce the BYTE-IDENTICAL 'Unknown tool: {name}'
    string for an unknown tool, with no speak_error and no dispatch call.

    flag-ON: the registry pre-check (spec is None) intercepts the unknown name
    BEFORE dispatch, returning the clean baseline string instead of letting
    dispatch raise (which the except would rewrap into 'Tool ... failed:').
    flag-OFF: the verbatim baseline else-arm returns the same string directly.
    This proves the two arms are at parity for the unknown-tool case.
    """
    # Populate the registry so the flag-ON pre-check sees a real (unknown-name)
    # miss rather than an empty-registry miss — proves the name is genuinely
    # absent, not merely un-bootstrapped.
    from core.tool_registry import import_all_tools, _REGISTRY
    import_all_tools()

    name = "definitely_not_a_real_tool_xyz"
    assert name not in _REGISTRY, "test name must be absent from the registry"

    dispatch_calls = []

    def _dispatch(tool_name, params, **kw):
        # Must NOT be reached when flag is ON (pre-check intercepts first).
        dispatch_calls.append((tool_name, params))
        raise ValueError(f"Unknown tool: {tool_name}")

    # ── flag-ON ──────────────────────────────────────────────────────────────
    result_on, s_on = _run(name, {}, monkeypatch, flag=True, dispatch=_dispatch)

    assert result_on == f"Unknown tool: {name}"
    assert dispatch_calls == [], "flag-ON pre-check must intercept before dispatch"
    s_on.speak_error.assert_not_called()

    # ── flag-OFF ─────────────────────────────────────────────────────────────
    def _boom_dispatch(*a, **k):
        raise AssertionError("dispatch must not be reached when flag is OFF")

    result_off, s_off = _run(name, {}, monkeypatch, flag=False, dispatch=_boom_dispatch)

    assert result_off == f"Unknown tool: {name}"
    s_off.speak_error.assert_not_called()

    # ── parity assertion (byte-identical) ────────────────────────────────────
    assert result_on == result_off, (
        f"flag-ON {result_on!r} != flag-OFF {result_off!r}"
    )
