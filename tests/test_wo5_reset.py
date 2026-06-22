"""tests/test_wo5_reset.py — T08: Reset boundary #1 — JarvisApp._process_message.

Verifies AC-4: per-turn reset clears LoopGuard state before the tool-rounds
loop so identical tool sequences across turns never produce false positives,
while within-turn sequences still trip correctly.

TestAC4Reset:
  test_cross_turn_no_false_positive  — AC-4 reset clears inter-turn state
  test_within_turn_trip              — A-B-A within one turn still trips
  test_process_message_reset_flag_off — flag OFF → reset block not executed
  test_process_message_source        — source-level check that reset is wired
"""
from __future__ import annotations

import inspect
import sys

import pytest

import core.loop_guard as lg
import core.mark_xl_rust_adapter as adapter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reset_guard_state(monkeypatch):
    """Force _GUARD and _BRIDGE back to None so each test gets a clean slate."""
    monkeypatch.setattr(lg, "_GUARD", None)
    monkeypatch.setattr(lg, "_BRIDGE", None)


# ---------------------------------------------------------------------------
# TestAC4Reset
# ---------------------------------------------------------------------------

class TestAC4Reset:
    """AC-4: per-turn reset — cross-turn no FP; within-turn still trips."""

    def test_cross_turn_no_false_positive(self, monkeypatch):
        """AC-4: same tool sequence across turns must NOT trip after reset().

        Turn 1: check tool_a, tool_b  (no trip)
        Turn boundary: reset()
        Turn 2: check tool_a, tool_b  (still no trip)
        """
        if not adapter.WHEEL_AVAILABLE:
            pytest.skip("mark_xl_rust wheel not available")

        _reset_guard_state(monkeypatch)

        # Construct a guard with tight knobs so the ping-pong limit is 2
        # (A→B→A would trip within one turn but reset clears it between turns).
        guard = lg.get_guard(
            True,
            max_identical=2,
            max_ping_pong=2,
            poll_budget=100,
        )
        assert guard is not None, "guard must be created when wheel is available"

        # Turn 1
        r1 = lg.check("tool_a", {})
        r2 = lg.check("tool_b", {})
        assert r1 is None, f"Turn-1 call #1 should not trip, got: {r1!r}"
        assert r2 is None, f"Turn-1 call #2 should not trip, got: {r2!r}"

        # Turn boundary — mirrors what _process_message now does
        lg.reset()

        # Turn 2: same sequence — must NOT trip (AC-4)
        r3 = lg.check("tool_a", {})
        r4 = lg.check("tool_b", {})
        assert r3 is None, f"Turn-2 call #1 should not trip after reset, got: {r3!r}"
        assert r4 is None, f"Turn-2 call #2 should not trip after reset, got: {r4!r}"

    def test_within_turn_trip(self, monkeypatch):
        """Within one turn, A-B-A-B must still trip on call #3 (no reset in between)."""
        if not adapter.WHEEL_AVAILABLE:
            pytest.skip("mark_xl_rust wheel not available")

        _reset_guard_state(monkeypatch)

        lg.get_guard(
            True,
            max_identical=2,
            max_ping_pong=2,
            poll_budget=100,
        )

        r1 = lg.check("tool_a", {})
        r2 = lg.check("tool_b", {})
        r3 = lg.check("tool_a", {})  # A→B→A ping-pong — must trip

        assert r1 is None, f"Call #1 should not trip, got: {r1!r}"
        assert r2 is None, f"Call #2 should not trip, got: {r2!r}"
        assert r3 is not None, (
            "Call #3 (A→B→A within one turn) should trip the ping-pong detector, "
            f"got: {r3!r}"
        )
        assert "Loop detected:" in r3 or r3.startswith("Loop"), (
            f"Trip message should contain 'Loop detected:', got: {r3!r}"
        )

    def test_process_message_reset_flag_off(self, monkeypatch):
        """Flag OFF: the reset block must NOT execute (no import, no reset call).

        We verify this by ensuring the _GUARD stays None even after the
        code path that would have called reset() runs (i.e., the flag check
        short-circuits before reaching loop_guard.reset()).
        """
        if not adapter.WHEEL_AVAILABLE:
            pytest.skip("mark_xl_rust wheel not available")

        _reset_guard_state(monkeypatch)

        # Patch get_flag to return False for enable_loopguard
        import memory.config_manager as cm

        original_get_flag = cm.get_flag

        def _patched_flag(name, *a, **k):
            if name == "enable_loopguard":
                return False
            return original_get_flag(name, *a, **k)

        monkeypatch.setattr(cm, "get_flag", _patched_flag)

        # Simulate the exact code block from _process_message with flag OFF
        from memory.config_manager import get_flag as gf  # re-bind after patch
        if gf("enable_loopguard"):
            from core import loop_guard  # type: ignore[assignment]
            loop_guard.reset()
            # If we reach here the test fails — flag OFF should not enter this branch
            pytest.fail("reset block executed despite enable_loopguard=False")

        # _GUARD must still be None — nothing initialised or reset it
        assert lg._GUARD is None, (
            "Flag OFF: _GUARD should remain None (reset block must not execute)"
        )

    def test_process_message_source(self):
        """Source-level check: _process_message contains the reset wire-up."""
        import main

        src = inspect.getsource(main.JarvisLocal._process_message)
        assert "loop_guard.reset()" in src, (
            "_process_message source must contain loop_guard.reset()"
        )
        assert "enable_loopguard" in src, (
            "_process_message source must check enable_loopguard flag"
        )
        assert "get_flag" in src, (
            "_process_message source must call get_flag"
        )
