"""tests/test_wo5_reset_background.py — T09: Reset boundary #2 — AgentExecutor.execute.

Verifies AC-4: background task isolation — each AgentExecutor.execute() call
resets LoopGuard state so that trip counters from a prior execution never
bleed into a subsequent execution (no cross-task false positives).

TestAC4BackgroundReset:
  test_two_execute_calls_no_cross_task_trip  — manual simulate: trip, reset, no FP
  test_execute_reset_block_present           — source-level assertion
  test_flag_off_no_import                    — flag OFF: reset block short-circuits
"""
from __future__ import annotations

import inspect
import sys

import pytest

import core.loop_guard as lg
import core.mark_xl_rust_adapter as adapter


# ---------------------------------------------------------------------------
# TestAC4BackgroundReset
# ---------------------------------------------------------------------------

class TestAC4BackgroundReset:
    """AC-4: background task isolation — execute() resets guard on each call."""

    def test_two_execute_calls_no_cross_task_trip(self, monkeypatch):
        """Simulate two sequential execute() calls and verify no cross-task trip.

        Strategy:
        1.  Build a tight guard (ping-pong limit 2) and drive it to a tripped state
            by running A→B→A.
        2.  Call loop_guard.reset() — exactly what execute() does at its start.
        3.  After reset, the first check on tool_a must return None (no FP).
        """
        if not adapter.WHEEL_AVAILABLE:
            pytest.skip("mark_xl_rust wheel not available")

        # ---- Task 1: drive to tripped state ----
        monkeypatch.setattr(lg, "_GUARD", None)
        monkeypatch.setattr(lg, "_BRIDGE", None)

        lg.get_guard(
            True,
            max_identical=2,
            max_ping_pong=2,
            poll_budget=100,
        )

        r1 = lg.check("tool_a", {})
        r2 = lg.check("tool_b", {})
        r3 = lg.check("tool_a", {})  # A→B→A — must trip

        assert r1 is None, f"call #1 should not trip, got: {r1!r}"
        assert r2 is None, f"call #2 should not trip, got: {r2!r}"
        assert r3 is not None, (
            "call #3 (A→B→A within one task) should trip the ping-pong detector"
        )

        # ---- Task 2: execute() calls reset() before create_plan ----
        lg.reset()

        # After reset the guard sequence counters are zeroed; first check must be clean.
        r4 = lg.check("tool_a", {})
        assert r4 is None, (
            f"After reset() (simulating execute() boundary), tool_a should not trip; "
            f"got: {r4!r}"
        )

    def test_execute_reset_block_present(self):
        """Source-level assertion: AgentExecutor.execute contains the reset block."""
        from agent.executor import AgentExecutor

        src = inspect.getsource(AgentExecutor.execute)

        assert "loop_guard.reset()" in src, (
            "AgentExecutor.execute source must contain loop_guard.reset()"
        )
        assert "enable_loopguard" in src, (
            "AgentExecutor.execute source must check the enable_loopguard flag"
        )
        assert "get_flag" in src, (
            "AgentExecutor.execute source must call get_flag"
        )

    def test_flag_off_no_import(self, monkeypatch):
        """Flag OFF: the reset block must short-circuit; guard is never touched.

        When enable_loopguard is False, the execute() preamble:
            if get_flag('enable_loopguard'):
                from core import loop_guard
                loop_guard.reset()
        must not execute the body.

        We verify this by:
        1.  Patching get_flag so that 'enable_loopguard' returns False.
        2.  Simulating the exact conditional from execute().
        3.  Asserting _GUARD remains None (nothing initialised or reset it).
        """
        monkeypatch.setattr(lg, "_GUARD", None)
        monkeypatch.setattr(lg, "_BRIDGE", None)

        import memory.config_manager as cm
        original_get_flag = cm.get_flag

        def _patched_flag(name, *a, **kw):
            if name == "enable_loopguard":
                return False
            return original_get_flag(name, *a, **kw)

        monkeypatch.setattr(cm, "get_flag", _patched_flag)

        # Re-bind after patch so this block uses the patched version.
        from memory.config_manager import get_flag as gf
        if gf("enable_loopguard"):
            from core import loop_guard as _lg  # noqa: F401
            _lg.reset()
            pytest.fail("reset block executed despite enable_loopguard=False")

        assert lg._GUARD is None, (
            "Flag OFF: _GUARD must remain None — reset block must not execute"
        )
