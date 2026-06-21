"""T6: executor flag-gate tests.

Verifies that _call_tool routes through core.tool_registry.dispatch when
use_tool_registry is ON, and falls back to the verbatim 17-arm baseline when OFF.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ---------------------------------------------------------------------------
# 1. flag-OFF: open_app hits action directly; dispatch is NOT called
# ---------------------------------------------------------------------------

def test_flag_off_baseline_open_app(monkeypatch):
    """flag-OFF: _call_tool('open_app', ...) calls the action module directly.

    Verifies the verbatim baseline ladder is taken when use_tool_registry=False,
    and that dispatch is never invoked.
    """
    # Force flag OFF
    import memory.config_manager as cm
    monkeypatch.setattr(cm, "get_flag", lambda *a, **k: False)

    # Track whether dispatch is called
    dispatch_called = []

    import core.tool_registry as tr
    monkeypatch.setattr(tr, "dispatch", lambda *a, **k: dispatch_called.append(True) or "dispatched")

    # Mock the open_app action to avoid side-effects
    import actions.open_app as oa
    monkeypatch.setattr(oa, "open_app", lambda parameters, player, speak: "opened")

    from agent.executor import _call_tool
    result = _call_tool("open_app", {"app": "Finder"}, player=None, speak=None)

    assert result == "opened"
    assert dispatch_called == [], "dispatch must NOT be called when flag is OFF"


# ---------------------------------------------------------------------------
# 2. flag-ON: unknown tool raises ValueError (via dispatch)
# ---------------------------------------------------------------------------

def test_flag_on_unknown_raises_ValueError(monkeypatch):
    """flag-ON: _call_tool('unknown_xyz', {}) raises ValueError via dispatch."""
    import memory.config_manager as cm
    monkeypatch.setattr(cm, "get_flag", lambda *a, **k: True)

    import core.tool_registry as tr
    monkeypatch.setattr(tr, "dispatch", lambda name, params, **kw: (_ for _ in ()).throw(
        ValueError(f"Unknown tool: {name}")
    ))

    from agent.executor import _call_tool
    with pytest.raises(ValueError, match="Unknown tool"):
        _call_tool("unknown_xyz", {}, player=None, speak=None)


# ---------------------------------------------------------------------------
# 3. flag-ON: generated_code hits OWN branch (not dispatch), empty desc raises
# ---------------------------------------------------------------------------

def test_flag_on_generated_code_own_branch(monkeypatch):
    """flag-ON: generated_code hits its OWN explicit branch, not dispatch.

    With an empty description, raises ValueError before dispatch is ever called.
    """
    import memory.config_manager as cm
    monkeypatch.setattr(cm, "get_flag", lambda *a, **k: True)

    dispatch_called = []

    import core.tool_registry as tr
    monkeypatch.setattr(tr, "dispatch", lambda *a, **k: dispatch_called.append(True) or "dispatched")

    from agent.executor import _call_tool
    with pytest.raises(ValueError, match="generated_code requires a 'description' parameter"):
        _call_tool("generated_code", {}, player=None, speak=None)

    assert dispatch_called == [], "dispatch must NOT be called for generated_code (own branch)"


# ---------------------------------------------------------------------------
# 4. flag-ON: screen_process non-str result coerced to "Screen captured and analyzed."
# ---------------------------------------------------------------------------

def test_flag_on_screen_process_non_str_coercion(monkeypatch):
    """flag-ON: if dispatch returns a non-str for screen_process, coerce to sentinel string."""
    import memory.config_manager as cm
    monkeypatch.setattr(cm, "get_flag", lambda *a, **k: True)

    # dispatch returns a non-str (e.g. None or a dict)
    import core.tool_registry as tr
    monkeypatch.setattr(tr, "dispatch", lambda name, params, **kw: None)

    from agent.executor import _call_tool
    result = _call_tool("screen_process", {}, player=None, speak=None)

    assert result == "Screen captured and analyzed.", (
        f"Expected 'Screen captured and analyzed.' but got {result!r}"
    )


# ---------------------------------------------------------------------------
# 5. flag-ON: generic registered tool returns dispatch result (line 186)
# ---------------------------------------------------------------------------

def test_flag_on_generic_tool_returns_dispatch_result(monkeypatch):
    """flag-ON: a non-screen_process tool returns the real dispatch result (line 186).

    Verifies the `return result or "Done."` branch for a generic registered tool
    (e.g. open_app) when dispatch returns a non-empty string.
    """
    import memory.config_manager as cm
    monkeypatch.setattr(cm, "get_flag", lambda *a, **k: True)

    import core.tool_registry as tr
    monkeypatch.setattr(tr, "dispatch", lambda name, params, **kw: "app opened")

    from agent.executor import _call_tool
    result = _call_tool("open_app", {"app": "Finder"}, player=None, speak=None)

    assert result == "app opened", (
        f"Expected dispatch result 'app opened' but got {result!r}"
    )


# ---------------------------------------------------------------------------
# 6. flag-ON: generated_code with non-empty description calls _run_generated_code (line 180)
# ---------------------------------------------------------------------------

def test_flag_on_generated_code_nonempty_calls_run(monkeypatch):
    """flag-ON: generated_code with a non-empty description reaches _run_generated_code (line 180).

    Verifies the path past the empty-description guard; _run_generated_code is
    monkeypatched to avoid side-effects.
    """
    import memory.config_manager as cm
    monkeypatch.setattr(cm, "get_flag", lambda *a, **k: True)

    monkeypatch.setattr("agent.executor._run_generated_code", lambda desc, speak=None: "code ran")

    from agent.executor import _call_tool
    result = _call_tool("generated_code", {"description": "print hello"}, player=None, speak=None)

    assert result == "code ran", (
        f"Expected 'code ran' from monkeypatched _run_generated_code but got {result!r}"
    )
