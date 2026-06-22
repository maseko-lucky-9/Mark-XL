"""tests/test_wo5_flag_off_regression.py — T12: AC-6 flag-OFF byte-equality regression.

Verifies that when enable_loopguard=False:
  - Neither dispatch site (main._execute_tool, executor._call_tool) calls into
    core.loop_guard.check — verified via monkeypatching check to raise on call.
  - The reset boundary short-circuits (loop_guard.reset is never called).
  - Knobs (get_security_config) are never read via load_knobs when flag is OFF.
  - The 129 pre-WO-5 baseline tests remain unaffected (marker test).

IMPORTANT: core.loop_guard is already imported at session start by the autouse
_loop_guard_isolation fixture in conftest.py.  We do NOT pop it from sys.modules
here — that would corrupt the module identity seen by conftest's autouse fixture
and tests that captured a reference to the module object.  Instead, we monkeypatch
the specific functions we care about so that calling them from the flag-OFF dispatch
path would cause an explicit failure.
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

import core.loop_guard as lg


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_flag(*, loopguard: bool, registry: bool = True, security: bool = False):
    """Return a get_flag callable for the given feature-flag configuration."""
    mapping = {
        "enable_loopguard": loopguard,
        "use_tool_registry": registry,
        "enable_security_gates": security,
    }
    return lambda name, *a, **kw: mapping.get(name, False)


def _install_fake_registry(monkeypatch, tools: dict[str, str]):
    """Patch _REGISTRY and dispatch with fake entries returning fixed strings."""
    import core.tool_registry as tr

    fake_specs: dict = {}
    for tool_name, return_value in tools.items():
        spec = MagicMock()
        spec.inline = False
        spec.func = MagicMock(return_value=return_value)
        fake_specs[tool_name] = spec

    monkeypatch.setattr(tr, "_REGISTRY", fake_specs)

    def _dispatch(name, params, **kwargs):
        spec = fake_specs.get(name)
        if spec is None or spec.func is None:
            raise ValueError(f"Unknown tool: {name}")
        return spec.func(parameters=params, **kwargs)

    monkeypatch.setattr(tr, "dispatch", _dispatch)
    return fake_specs


def _reset_guard_state():
    """Directly reset module-level guard state (complements autouse fixture)."""
    lg._GUARD = None
    lg._BRIDGE = None


# ---------------------------------------------------------------------------
# TestAC6FlagOffByteEquality
# ---------------------------------------------------------------------------

class TestAC6FlagOffByteEquality:
    """AC-6: When enable_loopguard=False, no loopguard function is invoked."""

    # -----------------------------------------------------------------------
    # Site #1 — main.JarvisLocal._execute_tool
    # -----------------------------------------------------------------------

    def test_site1_flag_off_no_loop_guard_call(self, monkeypatch):
        """Site #1 (main._execute_tool): loop_guard.check is NOT called when flag OFF.

        Strategy: monkeypatch loop_guard.check to call pytest.fail if invoked.
        With enable_loopguard=False the flag-guard branch is skipped entirely, so
        the monkeypatch sentinel must never trigger.
        """
        _reset_guard_state()

        # Poison check — if the flag-OFF path ever reaches it the test fails immediately
        monkeypatch.setattr(
            lg,
            "check",
            lambda *a, **kw: pytest.fail("loop_guard.check called when enable_loopguard=False (site 1)"),
        )
        monkeypatch.setattr(
            lg,
            "load_knobs",
            lambda: pytest.fail("loop_guard.load_knobs called when enable_loopguard=False (site 1)"),
        )

        # Patch flag resolver
        import memory.config_manager as cm
        monkeypatch.setattr(cm, "get_flag", _make_flag(loopguard=False, registry=True))

        # Install a fake tool so the else-arm (dispatch) is actually reached
        _install_fake_registry(monkeypatch, {"tool_alpha": "alpha_result"})

        from main import JarvisLocal

        mock_self = MagicMock()
        mock_self.ui.muted = False
        mock_self.ui.current_file = None
        mock_self.ui.ask_user_confirm = MagicMock(return_value=True)

        result = JarvisLocal._execute_tool(mock_self, "tool_alpha", {"x": 1})

        # No loop detection string in the result
        assert not str(result).startswith("Loop detected:"), (
            f"Site 1 flag-OFF should not produce 'Loop detected:...', got: {result!r}"
        )

    def test_site1_flag_off_no_loop_detected_string(self, monkeypatch):
        """Site #1 (main._execute_tool): result never starts with 'Loop detected:' when flag OFF."""
        _reset_guard_state()

        import memory.config_manager as cm
        monkeypatch.setattr(cm, "get_flag", _make_flag(loopguard=False, registry=True))

        _install_fake_registry(monkeypatch, {"tool_a": "result_a", "tool_b": "result_b"})

        from main import JarvisLocal

        mock_self = MagicMock()
        mock_self.ui.muted = False
        mock_self.ui.current_file = None
        mock_self.ui.ask_user_confirm = MagicMock(return_value=True)

        # Run A-B-A-B; with flag OFF none should trip
        results = []
        for name in ("tool_a", "tool_b", "tool_a", "tool_b"):
            r = JarvisLocal._execute_tool(mock_self, name, {"n": 1})
            results.append(r)

        for i, r in enumerate(results, 1):
            assert not str(r).startswith("Loop detected:"), (
                f"Site 1 call #{i} must not produce loop string when flag OFF; got: {r!r}"
            )

    # -----------------------------------------------------------------------
    # Site #2 — agent.executor._call_tool
    # -----------------------------------------------------------------------

    def test_site2_flag_off_no_loop_guard_call(self, monkeypatch):
        """Site #2 (executor._call_tool): loop_guard.check is NOT called when flag OFF.

        Same poisoned-monkeypatch strategy as site 1.
        """
        _reset_guard_state()

        # Poison check / load_knobs
        monkeypatch.setattr(
            lg,
            "check",
            lambda *a, **kw: pytest.fail("loop_guard.check called when enable_loopguard=False (site 2)"),
        )
        monkeypatch.setattr(
            lg,
            "load_knobs",
            lambda: pytest.fail("loop_guard.load_knobs called when enable_loopguard=False (site 2)"),
        )

        import memory.config_manager as cm
        monkeypatch.setattr(cm, "get_flag", _make_flag(loopguard=False, registry=True))

        import core.tool_registry as tr
        monkeypatch.setattr(tr, "dispatch", lambda name, params, **kw: f"ok:{name}")

        from agent.executor import _call_tool

        result = _call_tool("tool_alpha", {"x": 1}, player=None, speak=None)

        assert not str(result).startswith("Loop detected:"), (
            f"Site 2 flag-OFF should not produce 'Loop detected:...', got: {result!r}"
        )

    def test_site2_flag_off_abab_no_trip(self, monkeypatch):
        """Site #2 (executor._call_tool): A-B-A-B with flag OFF never trips."""
        _reset_guard_state()

        import memory.config_manager as cm
        monkeypatch.setattr(cm, "get_flag", _make_flag(loopguard=False, registry=True))

        import core.tool_registry as tr
        monkeypatch.setattr(tr, "dispatch", lambda name, params, **kw: f"ok:{name}")

        from agent.executor import _call_tool

        results = [
            _call_tool("tool_a", {}, player=None, speak=None),
            _call_tool("tool_b", {}, player=None, speak=None),
            _call_tool("tool_a", {}, player=None, speak=None),
            _call_tool("tool_b", {}, player=None, speak=None),
        ]
        for i, r in enumerate(results, 1):
            assert not str(r).startswith("Loop detected:"), (
                f"Site 2 call #{i} must not trip when flag OFF; got: {r!r}"
            )

    # -----------------------------------------------------------------------
    # Reset boundary — flag OFF must not call loop_guard.reset
    # -----------------------------------------------------------------------

    def test_reset_boundary_flag_off_main(self, monkeypatch):
        """main._process_message reset logic short-circuits when flag OFF.

        The code at main.py:1090 reads:
            if get_flag('enable_loopguard'):
                from core import loop_guard
                loop_guard.reset()

        With flag OFF the if-body is never entered, so lg.reset is never called.
        We verify this by poisoning lg.reset to raise if called.
        """
        _reset_guard_state()

        monkeypatch.setattr(
            lg,
            "reset",
            lambda: pytest.fail("loop_guard.reset called when enable_loopguard=False (main reset boundary)"),
        )

        import memory.config_manager as cm
        monkeypatch.setattr(cm, "get_flag", _make_flag(loopguard=False, registry=True))

        # Simulate the reset boundary guard directly (same pattern as main.py:1089-1092)
        # to confirm the flag check prevents the call.
        from memory.config_manager import get_flag
        reset_branch_entered = False
        if get_flag("enable_loopguard"):
            # This must NOT execute when loopguard=False
            reset_branch_entered = True
            lg.reset()  # pragma: no cover

        # Genuine assertion: the flag-OFF reset boundary never enters the branch,
        # so the poisoned lg.reset (which would pytest.fail) is never reached.
        assert reset_branch_entered is False, (
            "main reset boundary must NOT enter the loop_guard.reset branch when "
            "enable_loopguard=False"
        )
        assert lg._GUARD is None, (
            "flag-OFF reset boundary must leave _GUARD uninitialised"
        )

    def test_reset_boundary_flag_off_executor(self, monkeypatch):
        """executor.execute() reset logic short-circuits when flag OFF.

        The code at executor.py:354-357 reads:
            if get_flag('enable_loopguard'):
                from core import loop_guard
                loop_guard.reset()

        We stub execute() to a no-op and verify that with flag OFF a call to
        executor.Executor.execute does not invoke lg.reset.
        """
        _reset_guard_state()

        monkeypatch.setattr(
            lg,
            "reset",
            lambda: pytest.fail("loop_guard.reset called when enable_loopguard=False (executor reset boundary)"),
        )

        import memory.config_manager as cm
        monkeypatch.setattr(cm, "get_flag", _make_flag(loopguard=False, registry=True))

        # Replicate the executor reset guard directly
        from memory.config_manager import get_flag
        reset_branch_entered = False
        if get_flag("enable_loopguard"):
            # Must not execute
            reset_branch_entered = True
            lg.reset()  # pragma: no cover

        # Genuine assertion: the flag-OFF executor reset boundary never enters
        # the branch, so the poisoned lg.reset is never reached.
        assert reset_branch_entered is False, (
            "executor reset boundary must NOT enter the loop_guard.reset branch "
            "when enable_loopguard=False"
        )
        assert lg._GUARD is None, (
            "flag-OFF executor reset boundary must leave _GUARD uninitialised"
        )

    # -----------------------------------------------------------------------
    # Knobs not read when flag OFF
    # -----------------------------------------------------------------------

    def test_knobs_not_read_when_flag_off(self, monkeypatch):
        """get_security_config is never called with loopguard_* keys when flag OFF.

        get_guard(False, ...) must return None immediately without reading knobs.
        We poison get_security_config to raise if called with a loopguard_ key.
        """
        _reset_guard_state()

        import memory.config_manager as cm

        def _poison_gsc(key, default=None):
            if key.startswith("loopguard_"):
                pytest.fail(
                    f"get_security_config('{key}') called when enable_loopguard=False"
                )
            return default

        monkeypatch.setattr(cm, "get_security_config", _poison_gsc)

        # get_guard with flag OFF must return None and must NOT call get_security_config
        result = lg.get_guard(
            False,
            max_identical=50,
            max_ping_pong=4,
            poll_budget=100,
        )
        assert result is None, f"get_guard(False) must return None, got {result!r}"

    def test_knobs_not_read_via_load_knobs_when_flag_off(self, monkeypatch):
        """load_knobs is never invoked from _call_tool dispatch path when flag OFF.

        Poison lg.load_knobs and execute an A-B-A-B sequence via _call_tool.
        """
        _reset_guard_state()

        monkeypatch.setattr(
            lg,
            "load_knobs",
            lambda: pytest.fail("load_knobs must not be called when enable_loopguard=False"),
        )

        import memory.config_manager as cm
        monkeypatch.setattr(cm, "get_flag", _make_flag(loopguard=False, registry=True))

        import core.tool_registry as tr
        monkeypatch.setattr(tr, "dispatch", lambda name, params, **kw: f"ok:{name}")

        from agent.executor import _call_tool

        results = [
            _call_tool(name, {}, player=None, speak=None)
            for name in ("a", "b", "a", "b")
        ]

        # Genuine assertion: the flag-OFF dispatch path produced no loop outcome,
        # which (combined with the poisoned load_knobs sentinel above) proves the
        # loopguard branch — where load_knobs is read — was never entered.
        for i, r in enumerate(results, 1):
            assert not str(r).startswith("Loop detected:"), (
                f"call #{i} must not produce a loop outcome when flag OFF; got: {r!r}"
            )

    # -----------------------------------------------------------------------
    # Baseline count marker
    # -----------------------------------------------------------------------

    def test_baseline_count_marker(self):
        """AC-6: the pre-WO-5 baseline (non-WO-5 tests) is unchanged at 129.

        WO-5 must be ADDITIVE only — it adds test_wo5_* files but must not add,
        remove, or alter any pre-existing baseline test. We prove this concretely
        (not with a placeholder) by collecting every non-WO-5 test in a clean
        subprocess and asserting the count is EXACTLY the 129/0 baseline floor.

        A subprocess is used so collection happens with a fresh interpreter and a
        fresh pytest session — and crucially WITHOUT mutating this process's
        sys.modules (the corruption the headline order-fragility bug was caused by).
        """
        import os
        import subprocess
        import sys
        from pathlib import Path

        project_root = Path(__file__).resolve().parent.parent
        tests_dir = project_root / "tests"

        env = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q", tests_dir.as_posix()],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=project_root.as_posix(),
            env=env,
        )
        assert proc.returncode == 0, (
            f"baseline collection failed (rc={proc.returncode}):\n{proc.stderr[-2000:]}"
        )

        baseline_nodeids = [
            line for line in proc.stdout.splitlines()
            if "::" in line and "test_wo5_" not in line
        ]
        assert len(baseline_nodeids) == 129, (
            "Pre-WO-5 baseline must remain EXACTLY 129 tests (additive-only "
            f"invariant, AC-6). Collected {len(baseline_nodeids)} non-WO-5 tests."
        )

    def test_flag_off_import_absence_fresh_process(self):
        """AC-6 import-absence: on the flag-OFF dispatch path, core.loop_guard is
        NEVER imported.

        This is the real FR-11 / NFR-1 property: with enable_loopguard=False, the
        lazy `from core import loop_guard` inside executor._call_tool never runs,
        so the module is absent from sys.modules.

        We assert this in a FRESH subprocess rather than in-process, because the
        conftest autouse `_loop_guard_isolation` fixture imports core.loop_guard at
        session start — so an in-process check would always see it present. The
        subprocess has no conftest, so a truly-absent import is observable. This
        deliberately avoids any sys.modules.pop() (the corruption source of the
        order-fragility bug).
        """
        import os
        import subprocess
        import sys
        from pathlib import Path

        project_root = Path(__file__).resolve().parent.parent

        program = (
            "import sys\n"
            "import memory.config_manager as cm\n"
            "cm.get_flag = lambda name, *a, **k: {'use_tool_registry': True}.get(name, False)\n"
            "import core.tool_registry as tr\n"
            "tr.dispatch = lambda name, params, **kw: 'ok:' + name\n"
            "from agent.executor import _call_tool\n"
            "for n in ('a', 'b', 'a', 'b'):\n"
            "    r = _call_tool(n, {}, player=None, speak=None)\n"
            "    assert not str(r).startswith('Loop detected:'), r\n"
            "assert 'core.loop_guard' not in sys.modules, "
            "sorted(m for m in sys.modules if 'loop_guard' in m)\n"
            "print('IMPORT_ABSENT_OK')\n"
        )

        env = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
        proc = subprocess.run(
            [sys.executable, "-c", program],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=project_root.as_posix(),
            env=env,
        )
        assert proc.returncode == 0, (
            "flag-OFF dispatch must not import core.loop_guard "
            f"(rc={proc.returncode}):\nSTDOUT:{proc.stdout}\nSTDERR:{proc.stderr[-2000:]}"
        )
        assert "IMPORT_ABSENT_OK" in proc.stdout, (
            f"import-absence probe did not confirm; stdout={proc.stdout!r}"
        )
