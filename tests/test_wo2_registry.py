"""
tests/test_wo2_registry.py — WO-2 integration tests.

D1-D6 + M1-M5 registry integration. All FLAG-ON gated where the executor /
main flag-gate is exercised, and all use import_all_tools() as the REAL
bootstrap (NOT a test-side import-everything) so the suite proves the
production bootstrap path populates the registry exactly as shipped.

Isolation: every test that calls import_all_tools() or mutates _REGISTRY
takes the `registry_snapshot` fixture (conftest.py) which snapshots and
restores _REGISTRY around the test.

Flag-ON monkeypatch pattern:
    import memory.config_manager as cm
    monkeypatch.setattr(cm, "get_flag", lambda *a, **k: True)
Both main._execute_tool and agent.executor._call_tool do a call-time
`from memory.config_manager import get_flag`, so patching the attribute on
the config_manager module takes effect on the next call.
"""
import json
import os
import time
import pytest
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Module-level bootstrap — MUST run at collection, before any registry_snapshot.
#
# WHY: the conftest `registry_snapshot` fixture captures _REGISTRY at fixture
# SETUP (before the test body's import_all_tools() runs) and restores it at
# teardown. Re-importing already-cached action modules is a NO-OP, so the
# @register_tool decorators do NOT re-fire — only _register_inline_specs() runs.
# If the FIRST registry_snapshot test in a run snapshots an EMPTY/partial
# _REGISTRY (action modules not yet imported in this process) and then restores
# it, the 17 action tools are permanently stripped and import_all_tools() can
# never bring them back. Populating _REGISTRY here, at import/collection time,
# guarantees every snapshot captures all 20 tools — so this file passes both in
# isolation AND in the full suite, with no cross-file contamination.
from core.tool_registry import import_all_tools as _bootstrap_registry
_bootstrap_registry()


# ===========================================================================
# D1 — Registry completeness via real bootstrap
# ===========================================================================

def test_d1_registry_completeness(registry_snapshot):
    """import_all_tools() populates exactly 20 entries; all OLLAMA_TOOLS_ORDER
    names present in _REGISTRY."""
    from core.tool_registry import import_all_tools, _REGISTRY, OLLAMA_TOOLS_ORDER
    import_all_tools()
    assert len(_REGISTRY) == 20, f"Expected 20 entries, got {len(_REGISTRY)}"
    for name in OLLAMA_TOOLS_ORDER:
        assert name in _REGISTRY, f"Missing: {name}"


# ===========================================================================
# D2 — dispatch unknown -> ValueError; generated_code intact
# ===========================================================================

def test_d2_dispatch_unknown_raises(registry_snapshot):
    """dispatch() raises ValueError('Unknown tool: ...') for an unregistered name."""
    from core.tool_registry import import_all_tools, dispatch
    import_all_tools()
    with pytest.raises(ValueError, match="Unknown tool:"):
        dispatch("unknown_xyz_tool", {})


def test_d2_executor_unknown_raises_flag_on(monkeypatch, registry_snapshot):
    """executor._call_tool with flag-ON raises ValueError for an unknown tool
    (dispatch raises; _call_tool does NOT swallow it)."""
    import memory.config_manager as cm
    monkeypatch.setattr(cm, "get_flag", lambda *a, **k: True)
    from core.tool_registry import import_all_tools
    import_all_tools()
    from agent.executor import _call_tool
    with pytest.raises(ValueError):
        _call_tool("unknown_xyz", {})


def test_d2_generated_code_own_branch_flag_on(monkeypatch, registry_snapshot):
    """executor._call_tool: generated_code hits its OWN explicit branch (not
    dispatch, not 'unknown'). Empty description -> dedicated ValueError."""
    import memory.config_manager as cm
    monkeypatch.setattr(cm, "get_flag", lambda *a, **k: True)
    from core.tool_registry import import_all_tools
    import_all_tools()
    from agent.executor import _call_tool
    with pytest.raises(ValueError, match="generated_code requires a 'description'"):
        _call_tool("generated_code", {})


# ===========================================================================
# D3 — Inline tools: save_memory, agent_task, shutdown_jarvis
# ===========================================================================

def test_d3_save_memory_silent(monkeypatch, registry_snapshot):
    """save_memory is inline+silent; calling its handler directly returns
    '__SILENT__' and persists via memory.memory_manager.update_memory."""
    from core.tool_registry import import_all_tools, _REGISTRY
    import_all_tools()

    spec = _REGISTRY["save_memory"]
    assert spec.inline is True
    assert spec.is_silent is True

    # The handler does `from memory.memory_manager import update_memory` INSIDE
    # the function body, so the only intercept point is the source module attr.
    captured = {}
    monkeypatch.setattr(
        "memory.memory_manager.update_memory",
        lambda d: captured.update(d),
    )

    result = spec.func(parameters={"category": "notes", "key": "test", "value": "val"})
    assert result == "__SILENT__"
    assert captured == {"notes": {"test": {"value": "val"}}}


def test_d3_agent_task_priority(monkeypatch, registry_snapshot):
    """agent_task is inline; its handler enqueues with the correctly mapped
    TaskPriority and returns a string containing the task id."""
    from core.tool_registry import import_all_tools, _REGISTRY
    import_all_tools()

    spec = _REGISTRY["agent_task"]
    assert spec.inline is True

    submitted = {}

    class MockQueue:
        def submit(self, goal, priority, speak=None):
            submitted["goal"] = goal
            submitted["priority"] = priority
            return "task-1"

    # Handler does `from agent.task_queue import get_queue, TaskPriority` at
    # call time -> patch the attribute on the agent.task_queue module object.
    import agent.task_queue as tq
    monkeypatch.setattr(tq, "get_queue", lambda: MockQueue())

    result = spec.func(parameters={"goal": "do something", "priority": "high"})
    assert "task-1" in result
    from agent.task_queue import TaskPriority
    assert submitted["goal"] == "do something"
    assert submitted["priority"] == TaskPriority.HIGH


def test_d3_shutdown_jarvis_returns_shutting_down(monkeypatch, registry_snapshot, thread_guard):
    """shutdown_jarvis is inline; handler returns 'Shutting down.' immediately and
    spawns a daemon thread that (mocked) speaks + exits.

    Race-safety: the daemon thread does `time.sleep(2.5)` then `os._exit(0)`.
    We neutralise BOTH so the thread runs to completion against the MOCKED
    os._exit *before* monkeypatch restores the real os._exit at test teardown —
    otherwise the real os._exit(0) would fire ~2.5s later and kill pytest
    mid-run. (Same race solved in test_inline_tools.py by polling.)
    """
    import threading
    from core.tool_registry import import_all_tools, _REGISTRY
    import_all_tools()

    spec = _REGISTRY["shutdown_jarvis"]
    assert spec.inline is True

    exit_mock = MagicMock()
    monkeypatch.setattr(os, "_exit", exit_mock)        # prevent real process exit
    monkeypatch.setattr(time, "sleep", lambda *_: None)  # collapse the 2.5s sleep

    result = spec.func(parameters={})
    assert result == "Shutting down."

    # Wait for the daemon thread to reach the MOCKED os._exit before teardown
    # restores the real one.
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and not exit_mock.called:
        # real time.sleep is patched; spin-yield via threading event instead
        threading.Event().wait(0.02)
    exit_mock.assert_called_once_with(0)


# ===========================================================================
# D4 — weather_report is the ONLY true func-name remap
# ===========================================================================

def test_d4_weather_report_func_name(registry_snapshot):
    """_REGISTRY['weather_report'].func.__name__ == 'weather_action' (the only
    tool whose registered func name differs from the tool name).
    desktop_control / screen_process: func name == tool name (only the *module*
    name — desktop.py / screen_processor.py — differs)."""
    from core.tool_registry import import_all_tools, _REGISTRY
    import_all_tools()
    assert _REGISTRY["weather_report"].func.__name__ == "weather_action"
    assert _REGISTRY["desktop_control"].func.__name__ == "desktop_control"
    assert _REGISTRY["screen_process"].func.__name__ == "screen_process"


# ===========================================================================
# M1 — dispatch forwards player= / speak= by keyword, raw (no coercion)
# ===========================================================================

def test_m1_dispatch_forwards_keyword_args(monkeypatch, registry_snapshot):
    """dispatch passes player= and speak= as keyword args to spec.func and
    returns the func's result RAW (no fallback/coercion inside dispatch).

    NB: registry_snapshot does dict(_REGISTRY) — a SHALLOW copy that shares the
    SAME ToolSpec objects — so a plain `.func =` mutation would survive restore.
    We use monkeypatch.setattr on the ToolSpec instance for guaranteed teardown.
    """
    from core.tool_registry import import_all_tools, _REGISTRY, dispatch
    import_all_tools()

    captured = {}

    def capturing_func(parameters, player=None, speak=None):
        captured["parameters"] = parameters
        captured["player"] = player
        captured["speak"] = speak
        return "opened"

    monkeypatch.setattr(_REGISTRY["open_app"], "func", capturing_func)

    sentinel_player = object()
    sentinel_speak = object()
    result = dispatch(
        "open_app", {"app_name": "test"},
        player=sentinel_player, speak=sentinel_speak,
    )
    assert result == "opened"            # raw — not coerced to "Done."
    assert captured["parameters"] == {"app_name": "test"}
    assert captured["player"] is sentinel_player
    assert captured["speak"] is sentinel_speak


# ===========================================================================
# M2 — assembled planner prompt (flag-ON) byte-equals GOLDEN_PLANNER
# ===========================================================================

def test_m2_assembled_planner_prompt_equals_golden(registry_snapshot):
    """build_planner_prompt(build_planner_tool_block()) == GOLDEN_PLANNER
    (byte equality)."""
    from core.tool_registry import import_all_tools, build_planner_tool_block
    from agent.planner import build_planner_prompt
    from tests.test_wo2_goldens import GOLDEN_PLANNER
    import_all_tools()
    assembled = build_planner_prompt(build_planner_tool_block())
    assert assembled == GOLDEN_PLANNER


# ===========================================================================
# M3 — main._execute_tool flag-ON/OFF result parity (screen_process)
# ===========================================================================

def test_m3_main_result_parity_screen_process(monkeypatch, registry_snapshot):
    """flag-ON main._execute_tool: dispatch returning a non-str for
    screen_process coerces to 'Screen analyzed.' (main's sentinel — distinct
    from the executor's 'Screen captured and analyzed.'), matching the flag-OFF
    baseline arm."""
    import memory.config_manager as cm
    from core.tool_registry import import_all_tools
    import_all_tools()

    monkeypatch.setattr(cm, "get_flag", lambda *a, **k: True)

    # _execute_tool does a call-time `from core.tool_registry import dispatch`,
    # so patching the attribute on the module object intercepts it.
    import core.tool_registry as reg

    def mock_dispatch(name, params, player=None, speak=None):
        if name == "screen_process":
            return None  # non-str -> must coerce to the main sentinel
        return "done"

    monkeypatch.setattr(reg, "dispatch", mock_dispatch)

    from main import JarvisLocal
    mock_self = MagicMock()
    mock_self.ui.muted = False
    mock_self.ui.current_file = None

    result = JarvisLocal._execute_tool(
        mock_self, "screen_process", {"text": "what's on screen"}
    )
    assert result == "Screen analyzed."
    mock_self.speak_error.assert_not_called()


def test_m3_main_result_parity_file_processor(monkeypatch, registry_snapshot):
    """flag-ON main._execute_tool: file_processor with no file_path in args and
    ui.current_file set -> flag-ON pre-step injects args['file_path'] == current_file,
    identical to the flag-OFF baseline arm.

    Both paths must produce the same injected file_path and the same result string.
    """
    import memory.config_manager as cm
    from core.tool_registry import import_all_tools
    import_all_tools()

    CURRENT_FILE = "/tmp/test_document.pdf"

    # Shared capture dict used by the mock handler in both flag-ON and flag-OFF paths.
    captured = {}

    def mock_file_processor(parameters, player=None, speak=None):
        # Echo back the file_path so we can assert injection happened.
        captured["file_path"] = parameters.get("file_path")
        return f"processed:{parameters.get('file_path')}"

    # ── flag-ON path ──────────────────────────────────────────────────────────
    monkeypatch.setattr(cm, "get_flag", lambda *a, **k: True)

    import core.tool_registry as reg

    def mock_dispatch(name, params, player=None, speak=None):
        if name == "file_processor":
            return mock_file_processor(parameters=params, player=player, speak=speak)
        return "done"

    monkeypatch.setattr(reg, "dispatch", mock_dispatch)

    from main import JarvisLocal
    mock_self_on = MagicMock()
    mock_self_on.ui.muted = False
    mock_self_on.ui.current_file = CURRENT_FILE

    result_on = JarvisLocal._execute_tool(
        mock_self_on, "file_processor", {}
    )

    assert captured.get("file_path") == CURRENT_FILE, (
        f"flag-ON did not inject file_path: got {captured.get('file_path')!r}"
    )
    flag_on_result = result_on
    mock_self_on.speak_error.assert_not_called()

    # Reset capture for flag-OFF path.
    captured.clear()

    # ── flag-OFF path ─────────────────────────────────────────────────────────
    monkeypatch.setattr(cm, "get_flag", lambda *a, **k: False)

    # In flag-OFF, main.py binds `file_processor` via `from actions.file_processor import file_processor`
    # at module level (line 75). Patch the name in main's namespace to intercept the call.
    import main as main_mod
    monkeypatch.setattr(main_mod, "file_processor", mock_file_processor)

    mock_self_off = MagicMock()
    mock_self_off.ui.muted = False
    mock_self_off.ui.current_file = CURRENT_FILE

    result_off = JarvisLocal._execute_tool(
        mock_self_off, "file_processor", {}
    )

    assert captured.get("file_path") == CURRENT_FILE, (
        f"flag-OFF did not inject file_path: got {captured.get('file_path')!r}"
    )
    mock_self_off.speak_error.assert_not_called()

    # ── parity assertion ──────────────────────────────────────────────────────
    assert flag_on_result == result_off, (
        f"flag-ON result {flag_on_result!r} != flag-OFF result {result_off!r}"
    )


# ===========================================================================
# M4 — build_ollama_tools() byte-equals GOLDEN_OLLAMA
# ===========================================================================

def test_m4_build_ollama_tools_equals_golden(registry_snapshot):
    """build_ollama_tools() byte-equals GOLDEN_OLLAMA under json.dumps
    (sort_keys=False -> order-sensitive)."""
    from core.tool_registry import import_all_tools, build_ollama_tools
    from tests.test_wo2_goldens import GOLDEN_OLLAMA
    import_all_tools()
    result = build_ollama_tools()
    assert json.dumps(result, sort_keys=False) == json.dumps(GOLDEN_OLLAMA, sort_keys=False)


# ===========================================================================
# M5 — executor flag-ON: screen_process non-str -> "Screen captured and analyzed."
# ===========================================================================

def test_m5_executor_screen_process_coercion_flag_on(monkeypatch, registry_snapshot):
    """flag-ON agent.executor._call_tool: dispatch returning a non-str for
    screen_process coerces to 'Screen captured and analyzed.' (executor's
    sentinel — distinct from main's 'Screen analyzed.')."""
    import memory.config_manager as cm
    from core.tool_registry import import_all_tools
    import_all_tools()

    monkeypatch.setattr(cm, "get_flag", lambda *a, **k: True)

    # _call_tool does `from core.tool_registry import dispatch` at call time.
    import core.tool_registry as reg
    monkeypatch.setattr(reg, "dispatch", lambda name, params, **kw: None)

    from agent.executor import _call_tool
    result = _call_tool("screen_process", {"text": "what's here"})
    assert result == "Screen captured and analyzed."


# ===========================================================================
# D5 — exact-set allowlist: only main.py + agent/executor.py call get_flag
# ===========================================================================

def test_d5_get_flag_allowlist_exact_set():
    """Exactly {main.py, agent/executor.py} reference get_flag in production code
    (config_manager.py — the definition site — and all of tests/ excluded)."""
    from pathlib import Path
    repo_root = Path(__file__).resolve().parent.parent
    callers = []
    for f in repo_root.rglob("*.py"):
        parts = f.parts
        if any(
            p in {"tests", "__pycache__"}
            or p.startswith(".venv")
            or p == "venv"
            or "site-packages" in p
            for p in parts
        ):
            continue
        if f.name == "config_manager.py":
            continue
        try:
            if "get_flag" in f.read_text(encoding="utf-8"):
                callers.append(str(f.relative_to(repo_root)))
        except Exception:
            pass
    assert set(callers) == {"main.py", "agent/executor.py"}, (
        f"Unexpected get_flag callers: {sorted(callers)}"
    )


# ===========================================================================
# D6 — AC1: adding one @register_tool decorator wires a new tool end-to-end
# ===========================================================================

def test_d6_add_tool_one_decorator(registry_snapshot):
    """AC1 — a single @register_tool('demo_tool') call makes the tool:
      * present in _REGISTRY,
      * dispatchable via dispatch(),
      * convertible via to_openai_function().

    NOTE: build_ollama_tools() iterates the FIXED OLLAMA_TOOLS_ORDER tuple and
    build_planner_tool_block() iterates the FIXED PLANNER_TOOLS_ORDER tuple.
    A tool registered AFTER import_all_tools() is therefore NOT in either ORDER
    manifest, so it will NOT appear in build_ollama_tools()/build_planner_tool_block()
    output — and we must NOT mutate those module-level tuples to force it, since
    registry_snapshot only restores _REGISTRY (not the ORDER tuples), which would
    leak global state into other tests. AC1's "one decorator" claim is satisfied
    by registry membership + dispatch + schema conversion.
    """
    from core.tool_registry import (
        import_all_tools, _REGISTRY, register_tool,
        build_ollama_tools, dispatch, to_openai_function,
    )
    import_all_tools()

    @register_tool(
        name="demo_tool",
        description="A demonstration tool for testing.",
        parameters={
            "type": "OBJECT",
            "properties": {"input": {"type": "STRING", "description": "input"}},
            "required": ["input"],
        },
        planner_block="demo_tool\n  input: string (required)",
        is_planner_visible=True,
    )
    def demo_action(parameters, player=None, speak=None):
        return f"demo: {parameters.get('input')}"

    # 1. Registered in _REGISTRY
    assert "demo_tool" in _REGISTRY

    # 2. Dispatchable end-to-end
    result = dispatch("demo_tool", {"input": "hello"})
    assert result == "demo: hello"

    # 3. Schema conversion works (OpenAI/Ollama function shape)
    fn = to_openai_function(_REGISTRY["demo_tool"])
    assert fn["function"]["name"] == "demo_tool"
    assert "input" in fn["function"]["parameters"]["properties"]

    # 3b. demo_tool is NOT in OLLAMA_TOOLS_ORDER (registered post-bootstrap), so
    # build_ollama_tools() must NOT contain it — proves the ORDER manifest is the
    # single source of emitted-tool truth.
    ollama_names = [t["function"]["name"] for t in build_ollama_tools()]
    assert "demo_tool" not in ollama_names

    # 4. file_controller immunity sub-check: a tool whose own schema declares a
    # property literally named "name" still round-trips — spec.name is the tool
    # name AND a "name" property survives into the emitted schema.
    fc_spec = _REGISTRY["file_controller"]
    assert fc_spec.name == "file_controller"
    fc_fn = to_openai_function(fc_spec)
    assert "name" in fc_fn["function"]["parameters"]["properties"]

    # _REGISTRY cleanup (removing demo_tool) is handled by registry_snapshot.


# ===========================================================================
# B1 — func-IDENTITY check for all 17 action tools
# ===========================================================================

def test_b1_func_identity_all_17_action_tools(registry_snapshot):
    """B1 — every one of the 17 action tools registers the EXACT callable object
    that main.py binds at module top (IS identity, not name equality).

    Name equality (`.func.__name__ == tool_name`) falsely FAILS on the two
    legitimate remaps (weather_report→weather_action, web_search→web_search_action)
    and would falsely PASS if a same-named-but-different function were registered.
    IS identity is the correct invariant: `_REGISTRY[tool].func` must be the very
    object `main` imported from the actions sub-module (the same object the
    @register_tool decorator wrapped — register_tool returns `fn` unchanged).

    The expected callable for each tool is resolved from main's namespace via the
    `main_bound_name` column of TOOL_MATRIX (e.g. "main.weather_action" →
    getattr(main_mod, "weather_action")). This auto-handles the two remaps because
    we look up the ACTUAL bound name in main, not the tool name.
    """
    from core.tool_registry import import_all_tools, _REGISTRY
    import main as main_mod
    from tests.test_dispatch import TOOL_MATRIX

    import_all_tools()

    # Sanity: the matrix covers exactly the 17 action tools.
    assert len(TOOL_MATRIX) == 17, f"Expected 17 action tools, got {len(TOOL_MATRIX)}"

    # Build EXPECTED map {tool_name: callable from main's namespace}.
    # main_bound is "main.<name>"; strip the "main." prefix and resolve via getattr.
    expected = {}
    for tool_name, main_bound, _executor_target in TOOL_MATRIX:
        assert main_bound.startswith("main."), f"unexpected main_bound: {main_bound!r}"
        bound_attr = main_bound.split(".", 1)[1]
        expected[tool_name] = getattr(main_mod, bound_attr)

    # Assert IS identity for ALL 17.
    for tool_name, expected_callable in expected.items():
        assert tool_name in _REGISTRY, f"Missing from registry: {tool_name}"
        actual = _REGISTRY[tool_name].func
        assert actual is expected_callable, (
            f"{tool_name}: registry .func ({actual!r}) is NOT the same object as "
            f"main's binding ({expected_callable!r})"
        )
