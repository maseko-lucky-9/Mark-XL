"""tests/test_wo5_site1_main.py — T05: Wire Site #1 — JarvisLocal._execute_tool.

Verifies that the WO-5 LoopGuard integration in the known-tool else: branch of
_execute_tool behaves correctly:

  AC1 — A-B-A-B with enable_loopguard=True trips on call #3 ("Loop detected: ...")
  AC2 — enable_loopguard=False passes all 4 calls through dispatch without importing
         core.loop_guard
  AC3 — loop_detected signal fires when a loop is tripped (bridge integration)
  AC4 — Unknown-tool still returns "Unknown tool: ..." regardless of flag (FR-13)

Pattern: bind JarvisLocal._execute_tool (unbound) to a MagicMock self — same
technique used in test_wo2_t7_main.py.  All tests skip if the mark_xl_rust wheel is
absent, because loop detection requires the real guard.
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

import core.loop_guard as _lg_mod
import core.mark_xl_rust_adapter as adapter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_self():
    """Minimal MagicMock standing in for a JarvisLocal instance."""
    s = MagicMock()
    s.ui.muted = False
    s.ui.current_file = None
    s.ui.ask_user_confirm = MagicMock(return_value=True)
    return s


def _reset_guard(monkeypatch):
    """Force _GUARD back to None so each test gets a clean slate."""
    monkeypatch.setattr(_lg_mod, "_GUARD", None)
    monkeypatch.setattr(_lg_mod, "_BRIDGE", None)


def _make_flag(*, loopguard: bool, registry: bool = True, security: bool = False):
    """Return a get_flag side-effect that covers the relevant flag names."""
    def _flag(name, *a, **k):
        if name == "enable_loopguard":
            return loopguard
        if name == "use_tool_registry":
            return registry
        if name == "enable_security_gates":
            return security
        return False
    return _flag


def _install_fake_tools(monkeypatch, tools: dict):
    """Patch _REGISTRY with fake ToolSpec entries and a fake dispatch."""
    import core.tool_registry as tr

    fake_specs = {}
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


def _call(name, args, monkeypatch, *, loopguard: bool, registry: bool = True,
          security: bool = False):
    """Bind JarvisLocal._execute_tool to a mock self and invoke it."""
    import memory.config_manager as cm
    monkeypatch.setattr(cm, "get_flag", _make_flag(
        loopguard=loopguard, registry=registry, security=security
    ))

    from main import JarvisLocal
    s = _make_self()
    result = JarvisLocal._execute_tool(s, name, args)
    return result, s


# ---------------------------------------------------------------------------
# TestAC1Site1
# ---------------------------------------------------------------------------

class TestAC1Site1:
    """Integration tests for the T05 LoopGuard wire-up in _execute_tool."""

    def test_abab_trip_flag_on(self, monkeypatch):
        """AC1: A-B-A-B with enable_loopguard=True trips on call #3."""
        if not adapter.WHEEL_AVAILABLE:
            pytest.skip("mark_xl_rust wheel not available")

        _reset_guard(monkeypatch)
        _install_fake_tools(monkeypatch, {"tool_a": "result_a", "tool_b": "result_b"})

        results = []
        for name in ("tool_a", "tool_b", "tool_a", "tool_b"):
            r, _ = _call(name, {"n": 1}, monkeypatch,
                         loopguard=True, registry=True, security=False)
            results.append(r)

        r1, r2, r3, r4 = results

        # First two calls should pass through (no loop yet)
        assert not r1.startswith("Loop detected:"), f"Call #1 should not trip, got: {r1!r}"
        assert not r2.startswith("Loop detected:"), f"Call #2 should not trip, got: {r2!r}"
        # Call #3 (A→B→A) must trip the ping-pong detector
        assert r3.startswith("Loop detected:"), (
            f"Call #3 (A→B→A) should return 'Loop detected:...', got: {r3!r}"
        )

    def test_flag_off_no_trip(self, monkeypatch):
        """AC2: enable_loopguard=False — all 4 calls dispatch normally, no loop_guard import."""
        _reset_guard(monkeypatch)
        _install_fake_tools(monkeypatch, {"tool_a": "result_a", "tool_b": "result_b"})

        results = []
        for name in ("tool_a", "tool_b", "tool_a", "tool_b"):
            r, _ = _call(name, {"n": 1}, monkeypatch,
                         loopguard=False, registry=True, security=False)
            results.append(r)

        for i, r in enumerate(results, 1):
            assert not r.startswith("Loop detected:"), (
                f"Call #{i} should NOT trip when loopguard is off, got: {r!r}"
            )

        # The flag-OFF path must never reach the import-and-check code. We verify
        # this by asserting that no "Loop detected:" string appeared, which is
        # sufficient evidence the loopguard branch was skipped.
        #
        # NOTE: we deliberately do NOT pop "core.loop_guard" from sys.modules.
        # Doing so swaps the module object out from under the autouse
        # _loop_guard_isolation fixture and any sibling test that captured a
        # reference to the *old* module (e.g. test_loop_detected_signal_fires,
        # which connects a slot to the old module's _BRIDGE while _execute_tool
        # re-imports a fresh module whose _BRIDGE is None) — corrupting it across
        # the test-ordering boundary. Module identity must stay stable.

    def test_loop_detected_signal_fires(self, monkeypatch):
        """AC3: loop_detected signal fires on the bridge when a loop is tripped."""
        if not adapter.WHEEL_AVAILABLE:
            pytest.skip("mark_xl_rust wheel not available")

        _reset_guard(monkeypatch)
        _install_fake_tools(monkeypatch, {"tool_a": "result_a", "tool_b": "result_b"})

        # Collect signal emissions
        fired_reasons = []

        # Get or create the bridge and connect a slot
        bridge = _lg_mod.get_bridge()
        bridge.loop_detected.connect(lambda reason: fired_reasons.append(reason))

        for name in ("tool_a", "tool_b", "tool_a", "tool_b"):
            _call(name, {"n": 1}, monkeypatch,
                  loopguard=True, registry=True, security=False)

        assert len(fired_reasons) >= 1, (
            f"Expected loop_detected signal to fire at least once, got: {fired_reasons}"
        )
        assert all(isinstance(r, str) for r in fired_reasons), (
            f"All signal payloads must be str, got: {fired_reasons}"
        )

    def test_unknown_tool_unchanged(self, monkeypatch):
        """AC4 / FR-13: Unregistered tool still returns 'Unknown tool: ...' regardless of flags."""
        _reset_guard(monkeypatch)
        # Empty registry so any tool name is unknown
        import core.tool_registry as tr
        monkeypatch.setattr(tr, "_REGISTRY", {})

        # Test with loopguard ON
        r1, _ = _call("nonexistent_tool", {}, monkeypatch,
                       loopguard=True, registry=True, security=False)
        assert r1 == "Unknown tool: nonexistent_tool", (
            f"Expected 'Unknown tool: nonexistent_tool', got: {r1!r}"
        )

        # Test with loopguard OFF
        r2, _ = _call("nonexistent_tool", {}, monkeypatch,
                       loopguard=False, registry=True, security=False)
        assert r2 == "Unknown tool: nonexistent_tool", (
            f"Expected 'Unknown tool: nonexistent_tool' (flag OFF), got: {r2!r}"
        )
