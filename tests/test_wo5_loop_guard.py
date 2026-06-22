"""tests/test_wo5_loop_guard.py — Unit tests for load_knobs(), get_guard(), check(), and reset().

Covers:
  - load_knobs() default values and config override behaviour
  - get_guard() disabled path (returns None, _GUARD stays None)
  - get_guard() enabled path (returns non-None, idempotent on 2nd call)
  - get_guard() thread-safety (double-checked locking produces exactly 1 construction)
  - get_guard() wheel-absent legacy mode (adapter returns None → get_guard returns None)
  - check() A→B→A→B ping-pong trip (real wheel required)
  - check() identical-arg trip (real wheel required)
  - check() differing args never trip (real wheel required)
  - check() returns None when _GUARD is None (fail-open)
  - reset() no-op when _GUARD is None
  - reset() clears state so next identical call returns None (real wheel required)
  - reset() prevents cross-turn false positives (real wheel required)
  - _canonical_json is called with the raw dict before reaching _GUARD.check
"""
from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtCore import QObject
from PyQt6.QtWidgets import QApplication

import core.loop_guard as _mod
import core.loop_guard as loop_guard
import core.mark_xl_rust_adapter as adapter
from core.loop_guard import check, get_guard, load_knobs, reset


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_KNOBS = dict(max_identical=50, max_ping_pong=4, poll_budget=100)


def _reset_guard(monkeypatch):
    """Reset _GUARD to None so each test starts from a clean slate."""
    monkeypatch.setattr(_mod, "_GUARD", None)


# ---------------------------------------------------------------------------
# TestLoadKnobs
# ---------------------------------------------------------------------------

class TestLoadKnobs:
    """Tests for load_knobs()."""

    def test_defaults(self):
        """With no config overrides, load_knobs returns (50, 4, 100)."""
        with patch("core.loop_guard.get_security_config", side_effect=lambda key, default=None: default):
            result = load_knobs()
        assert result == (50, 4, 100)

    def test_int_coercion(self):
        """String values in config are coerced to int."""
        def _cfg(key, default=None):
            mapping = {
                "loopguard_max_identical": "3",
                "loopguard_max_ping_pong": "2",
                "loopguard_poll_budget": "10",
            }
            return mapping.get(key, default)

        with patch("core.loop_guard.get_security_config", side_effect=_cfg):
            mi, mp, pb = load_knobs()

        assert mi == 3
        assert mp == 2
        assert pb == 10
        assert isinstance(mi, int)
        assert isinstance(mp, int)
        assert isinstance(pb, int)

    def test_all_overrides(self):
        """Custom int values propagate correctly through load_knobs."""
        def _cfg(key, default=None):
            mapping = {
                "loopguard_max_identical": 7,
                "loopguard_max_ping_pong": 3,
                "loopguard_poll_budget": 25,
            }
            return mapping.get(key, default)

        with patch("core.loop_guard.get_security_config", side_effect=_cfg):
            result = load_knobs()

        assert result == (7, 3, 25)


# ---------------------------------------------------------------------------
# TestGetGuard
# ---------------------------------------------------------------------------

class TestGetGuard:
    """Tests for get_guard()."""

    def test_false_returns_none(self, monkeypatch):
        """get_guard(False, ...) returns None and never mutates _GUARD."""
        _reset_guard(monkeypatch)
        result = get_guard(False, **_KNOBS)
        assert result is None
        assert _mod._GUARD is None

    def test_true_constructs(self, monkeypatch):
        """get_guard(True, ...) returns a non-None guard when adapter works."""
        _reset_guard(monkeypatch)
        fake_guard = MagicMock(name="FakeGuard")
        with patch("core.mark_xl_rust_adapter.get_loop_guard", return_value=fake_guard):
            result = get_guard(True, **_KNOBS)
        assert result is fake_guard

    def test_idempotent(self, monkeypatch):
        """Two successive calls with enable=True return the exact same object."""
        _reset_guard(monkeypatch)
        fake_guard = MagicMock(name="FakeGuard")
        with patch("core.mark_xl_rust_adapter.get_loop_guard", return_value=fake_guard):
            r1 = get_guard(True, **_KNOBS)
            r2 = get_guard(True, **_KNOBS)
        assert r1 is r2

    def test_thread_safe(self, monkeypatch):
        """Concurrent calls to get_guard(True, ...) construct the guard exactly once."""
        _reset_guard(monkeypatch)
        construct_count = []
        barrier = threading.Barrier(2)
        fake_guard = MagicMock(name="FakeGuard")

        def _counted_get_loop_guard(**kwargs):
            construct_count.append(1)
            return fake_guard

        errors: list[Exception] = []

        def _worker():
            try:
                barrier.wait()  # both threads enter simultaneously
                get_guard(True, **_KNOBS)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        with patch("core.mark_xl_rust_adapter.get_loop_guard", side_effect=_counted_get_loop_guard):
            t1 = threading.Thread(target=_worker)
            t2 = threading.Thread(target=_worker)
            t1.start()
            t2.start()
            t1.join(timeout=5)
            t2.join(timeout=5)

        assert not errors, f"Thread errors: {errors}"
        assert len(construct_count) == 1, (
            f"Expected exactly 1 construction, got {len(construct_count)}"
        )

    def test_wheel_absent_legacy(self, monkeypatch):
        """When adapter.get_loop_guard returns None, get_guard returns None."""
        _reset_guard(monkeypatch)
        with patch("core.mark_xl_rust_adapter.get_loop_guard", return_value=None):
            result = get_guard(True, **_KNOBS)
        assert result is None


# ---------------------------------------------------------------------------
# TestCheck
# ---------------------------------------------------------------------------

class TestCheck:
    """Tests for check() — serialization, loop detection, fail-open guard."""

    def _make_guard(self, monkeypatch, **knob_overrides):
        """Reset _GUARD to None, then construct a real wheel guard."""
        monkeypatch.setattr(_mod, "_GUARD", None)
        knobs = {**_KNOBS, **knob_overrides}
        return get_guard(True, **knobs)

    def test_abab_trip(self, monkeypatch):
        """A→B→A→B with max_ping_pong=2: calls 1,2 return None; call 3 non-None."""
        if not adapter.WHEEL_AVAILABLE:
            pytest.skip("mark_xl_rust wheel not available")
        self._make_guard(monkeypatch, max_identical=50, max_ping_pong=2, poll_budget=100)
        r1 = check("tool_a", {"n": 1})
        r2 = check("tool_b", {"n": 1})
        r3 = check("tool_a", {"n": 1})
        r4 = check("tool_b", {"n": 1})  # noqa: F841  (result may vary; r3 must be non-None)
        assert r1 is None, f"call 1 should be None, got {r1!r}"
        assert r2 is None, f"call 2 should be None, got {r2!r}"
        assert r3 is not None, "call 3 (A→B→A trip) should return a reason string"
        assert isinstance(r3, str), f"reason must be str, got {type(r3)}"
        monkeypatch.setattr(_mod, "_GUARD", None)

    def test_identical_arg_trip(self, monkeypatch):
        """Calling same tool+args max_identical+1 times trips the guard."""
        if not adapter.WHEEL_AVAILABLE:
            pytest.skip("mark_xl_rust wheel not available")
        self._make_guard(monkeypatch, max_identical=1, max_ping_pong=4, poll_budget=100)
        r1 = check("same_tool", {"k": "v"})
        r2 = check("same_tool", {"k": "v"})
        assert r1 is None, f"first call should be None, got {r1!r}"
        assert r2 is not None, "second identical call should return a reason string"
        assert isinstance(r2, str)
        monkeypatch.setattr(_mod, "_GUARD", None)

    def test_differing_args_no_trip(self, monkeypatch):
        """Calls with different args never trip the identical-call detector."""
        if not adapter.WHEEL_AVAILABLE:
            pytest.skip("mark_xl_rust wheel not available")
        self._make_guard(monkeypatch, max_identical=2, max_ping_pong=4, poll_budget=100)
        results = [check("tool", {"n": i}) for i in range(8)]
        assert all(r is None for r in results), (
            f"Expected all None for differing args, got {results}"
        )
        monkeypatch.setattr(_mod, "_GUARD", None)

    def test_returns_none_when_guard_none(self, monkeypatch):
        """check() returns None (fail-open) when _GUARD is None."""
        monkeypatch.setattr(_mod, "_GUARD", None)
        result = check("any", {})
        assert result is None


# ---------------------------------------------------------------------------
# TestReset
# ---------------------------------------------------------------------------

class TestReset:
    """Tests for reset() — state clearing and cross-turn isolation."""

    def _make_guard(self, monkeypatch, **knob_overrides):
        monkeypatch.setattr(_mod, "_GUARD", None)
        knobs = {**_KNOBS, **knob_overrides}
        return get_guard(True, **knobs)

    def test_reset_noop_no_guard(self, monkeypatch):
        """reset() does not raise when _GUARD is None."""
        monkeypatch.setattr(_mod, "_GUARD", None)
        reset()  # must not raise

    def test_reset_clears_state(self, monkeypatch):
        """After tripping with max_identical=1, reset() allows the next call to return None."""
        if not adapter.WHEEL_AVAILABLE:
            pytest.skip("mark_xl_rust wheel not available")
        self._make_guard(monkeypatch, max_identical=1, max_ping_pong=4, poll_budget=100)
        check("tool_a", {})   # call 1 → None
        r_trip = check("tool_a", {})  # call 2 → should trip
        assert r_trip is not None, "Expected a trip on call 2"
        reset()
        r_after = check("tool_a", {})  # call after reset → None (state cleared)
        assert r_after is None, f"Expected None after reset, got {r_after!r}"
        monkeypatch.setattr(_mod, "_GUARD", None)

    def test_cross_turn_no_trip(self, monkeypatch):
        """Identical call in turn 2 (after reset) does not inherit state from turn 1."""
        if not adapter.WHEEL_AVAILABLE:
            pytest.skip("mark_xl_rust wheel not available")
        self._make_guard(monkeypatch, max_identical=2, max_ping_pong=4, poll_budget=100)
        # Turn 1: one call, then reset
        check("tool_a", {})
        reset()
        # Turn 2: same call again — should NOT trip because state was cleared
        r = check("tool_a", {})
        assert r is None, f"Expected None in turn 2 after reset, got {r!r}"
        monkeypatch.setattr(_mod, "_GUARD", None)


# ---------------------------------------------------------------------------
# TestSerialize
# ---------------------------------------------------------------------------

class TestSerialize:
    """Tests verifying that params dict is serialized before reaching _GUARD.check."""

    def test_canonical_json_called(self, monkeypatch):
        """check() passes the raw dict to _canonical_json."""
        monkeypatch.setattr(_mod, "_GUARD", None)
        # Set up a fake guard that records what it receives
        fake_guard = MagicMock(name="FakeGuard")
        fake_guard.check.return_value = None
        monkeypatch.setattr(_mod, "_GUARD", fake_guard)

        captured_calls = []
        original_canonical_json = _mod._canonical_json

        def _spy_canonical_json(obj):
            captured_calls.append(obj)
            return original_canonical_json(obj)

        monkeypatch.setattr(_mod, "_canonical_json", _spy_canonical_json)

        params = {"b": 2, "a": 1}
        check("tool", params)

        assert len(captured_calls) == 1, f"Expected 1 call to _canonical_json, got {len(captured_calls)}"
        assert captured_calls[0] == {"b": 2, "a": 1}, (
            f"_canonical_json received wrong value: {captured_calls[0]!r}"
        )
        monkeypatch.setattr(_mod, "_GUARD", None)

    def test_raw_dict_never_reaches_guard(self, monkeypatch):
        """The value received by _GUARD.check() is a str, not a dict."""
        monkeypatch.setattr(_mod, "_GUARD", None)
        fake_guard = MagicMock(name="FakeGuard")
        fake_guard.check.return_value = None
        monkeypatch.setattr(_mod, "_GUARD", fake_guard)

        check("tool", {"key": "value"})

        assert fake_guard.check.called, "_GUARD.check should have been called"
        call_args = fake_guard.check.call_args
        # Second positional arg is arg_str
        arg_str = call_args[0][1]
        assert isinstance(arg_str, str), (
            f"_GUARD.check() must receive a str, got {type(arg_str)}: {arg_str!r}"
        )
        monkeypatch.setattr(_mod, "_GUARD", None)


# ---------------------------------------------------------------------------
# TestSignalLoop
# ---------------------------------------------------------------------------

class TestSignalLoop:
    """Tests for get_bridge() and signal_loop()."""

    def test_bridge_is_qobject_subclass(self, qapp, monkeypatch):
        """get_bridge() returns LoopGuardBridge which is a QObject subclass."""
        monkeypatch.setattr(loop_guard, "_BRIDGE", None)
        bridge = loop_guard.get_bridge()
        assert isinstance(bridge, QObject)
        assert isinstance(bridge, loop_guard.LoopGuardBridge)
        monkeypatch.setattr(loop_guard, "_BRIDGE", None)

    def test_signal_fires_with_correct_reason(self, qapp, monkeypatch):
        """signal_loop emits loop_detected with the reason string."""
        monkeypatch.setattr(loop_guard, "_BRIDGE", None)
        received = []
        bridge = loop_guard.get_bridge()
        bridge.loop_detected.connect(lambda r: received.append(r))
        loop_guard.signal_loop("test reason")
        QApplication.processEvents()
        assert received == ["test reason"]
        monkeypatch.setattr(loop_guard, "_BRIDGE", None)

    def test_signal_loop_noop_when_bridge_none(self, monkeypatch):
        """signal_loop with _BRIDGE=None does not raise."""
        monkeypatch.setattr(loop_guard, "_BRIDGE", None)
        loop_guard.signal_loop("x")  # must not raise

    def test_get_bridge_returns_same_object(self, qapp, monkeypatch):
        """get_bridge() returns singleton — same object on second call."""
        monkeypatch.setattr(loop_guard, "_BRIDGE", None)
        b1 = loop_guard.get_bridge()
        b2 = loop_guard.get_bridge()
        assert b1 is b2
        monkeypatch.setattr(loop_guard, "_BRIDGE", None)
