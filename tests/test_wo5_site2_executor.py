"""WO-5 T06: Site 2 — agent.executor._call_tool loopguard integration tests.

AC-2 acceptance criteria:
  - A-B-A-B with flag ON trips on call #3 (returns "Loop detected: ...")
  - generated_code branch executes BEFORE loop_guard check (FR-13)
  - Flag OFF: no loop_guard import, all calls pass through unchanged
  - signal_loop fires when a loop is detected
"""
import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean_guard():
    """Hard-reset core.loop_guard._GUARD and _BRIDGE before and after every test.

    monkeypatch saves/restores the attribute value at the time setattr is called.
    If a prior test left _GUARD pointing to a live LoopGuard object, monkeypatch
    would restore that object on teardown — leaking state into later tests.
    This fixture bypasses monkeypatch by directly writing None before and after.
    """
    import core.loop_guard as lg
    lg._GUARD = None
    lg._BRIDGE = None
    yield
    lg._GUARD = None
    lg._BRIDGE = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reset_guard(monkeypatch):
    """Reset core.loop_guard._GUARD between tests to avoid state leak.

    NOTE: _clean_guard autouse fixture already handles this; this helper is
    kept for explicitness / future use within a test body.
    """
    import core.loop_guard as lg
    lg._GUARD = None
    lg._BRIDGE = None


def _flag_selector(loopguard: bool, registry: bool = True, sec: bool = False):
    """Return a get_flag side-effect that selects specific flag values."""
    mapping = {
        "use_tool_registry": registry,
        "enable_loopguard": loopguard,
        "enable_security_gates": sec,
    }
    return lambda name, *a, **k: mapping.get(name, False)


# ---------------------------------------------------------------------------
# TestAC2Site2
# ---------------------------------------------------------------------------

class TestAC2Site2:

    # -----------------------------------------------------------------------
    # 1. A-B-A-B with flag ON trips on call #3
    # -----------------------------------------------------------------------

    def test_abab_trip_flag_on(self, monkeypatch):
        """With enable_loopguard=True, A-B-A-B pattern trips on call #3.

        Uses low knobs (max_identical=2, max_ping_pong=2) so the ping-pong
        detector fires on the third call (second A→B→A cycle close-out).
        """
        import memory.config_manager as cm
        monkeypatch.setattr(cm, "get_flag", _flag_selector(loopguard=True))

        # Patch get_security_config to return low knobs
        monkeypatch.setattr(
            cm, "get_security_config",
            lambda key, default=None: {
                "loopguard_max_identical": 2,
                "loopguard_max_ping_pong": 2,
                "loopguard_poll_budget": 100,
            }.get(key, default),
        )

        # Reset guard state before test
        _reset_guard(monkeypatch)

        # Register two fake tools in the tool registry
        import core.tool_registry as tr
        original_dispatch = tr.dispatch

        def fake_dispatch(name, params, **kw):
            return f"ok:{name}"

        monkeypatch.setattr(tr, "dispatch", fake_dispatch)

        from agent.executor import _call_tool

        result1 = _call_tool("tool_a", {}, player=None, speak=None)
        result2 = _call_tool("tool_b", {}, player=None, speak=None)
        result3 = _call_tool("tool_a", {}, player=None, speak=None)

        # Call #1 and #2 must pass through cleanly
        assert result1 == "ok:tool_a", f"Call #1 should pass, got {result1!r}"
        assert result2 == "ok:tool_b", f"Call #2 should pass, got {result2!r}"
        # Call #3 must be the trip
        assert result3.startswith("Loop detected:"), (
            f"Call #3 should be tripped by loopguard, got {result3!r}"
        )

    # -----------------------------------------------------------------------
    # 2. generated_code executes BEFORE loop_guard check (FR-13)
    # -----------------------------------------------------------------------

    def test_generated_code_returns_before_loop_guard(self, monkeypatch):
        """generated_code branch returns BEFORE the loopguard check is reached.

        Strategy: patch loop_guard.check to raise an exception. If _call_tool
        with tool='generated_code' does NOT raise, it bypassed loop_guard.
        We also need _run_generated_code to not error, so we patch it too.
        """
        import memory.config_manager as cm
        monkeypatch.setattr(cm, "get_flag", _flag_selector(loopguard=True))
        monkeypatch.setattr(
            cm, "get_security_config",
            lambda key, default=None: default,
        )

        _reset_guard(monkeypatch)

        # Patch loop_guard.check to raise if called
        import core.loop_guard as lg
        monkeypatch.setattr(
            lg, "check",
            lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("loop_guard.check must NOT be called for generated_code")
            ),
        )

        # Patch _run_generated_code so it succeeds without side-effects
        import agent.executor as exe
        monkeypatch.setattr(exe, "_run_generated_code", lambda desc, speak=None, scan_enabled=False, audit=None: "code-ran")

        from agent.executor import _call_tool
        # Must NOT raise — generated_code branch returns before reaching loopguard
        result = _call_tool("generated_code", {"description": "x = 1"}, player=None, speak=None)
        assert result == "code-ran", f"Expected 'code-ran', got {result!r}"

    # -----------------------------------------------------------------------
    # 3. Flag OFF: no loop_guard import, all calls pass through
    # -----------------------------------------------------------------------

    def test_flag_off_no_trip(self, monkeypatch):
        """With enable_loopguard=False, A-B-A-B passes through with no trip.

        Also verifies that the flag-OFF dispatch path never reaches the LoopGuard
        check/knob-read code.

        IMPORTANT — module identity must stay stable.  We deliberately do NOT
        ``sys.modules.pop('core.loop_guard')`` here.  Popping it splits module
        identity: the lazy ``from core import loop_guard`` inside
        ``executor._call_tool`` re-imports a SECOND module object, so a later
        ``patch('core.loop_guard.get_security_config', ...)`` in a sibling test
        lands on the wrong copy and ``load_knobs()`` returns stale defaults —
        the exact order-fragility bug this regression guards against.  The
        sibling warning at tests/test_wo5_site1_main.py:147-153 documents the
        same hazard.

        Instead we use the poisoned-sentinel pattern from
        tests/test_wo5_flag_off_regression.py: monkeypatch ``check`` and
        ``load_knobs`` so that ANY entry into the flag-ON LoopGuard branch fails
        the test loudly, then assert the A-B-A-B sequence produces no
        "Loop detected:" outcome.
        """
        import memory.config_manager as cm
        monkeypatch.setattr(cm, "get_flag", _flag_selector(loopguard=False))

        _reset_guard(monkeypatch)

        # Poisoned sentinels — if the flag-OFF path ever reaches the LoopGuard
        # branch, these fire and the test fails immediately. No module identity
        # is mutated (module object stays the one the autouse fixture resets).
        import core.loop_guard as lg
        monkeypatch.setattr(
            lg, "check",
            lambda *a, **k: pytest.fail(
                "loop_guard.check called when enable_loopguard=False (site 2)"
            ),
        )
        monkeypatch.setattr(
            lg, "load_knobs",
            lambda: pytest.fail(
                "loop_guard.load_knobs called when enable_loopguard=False (site 2)"
            ),
        )

        import core.tool_registry as tr
        monkeypatch.setattr(tr, "dispatch", lambda name, params, **kw: f"ok:{name}")

        from agent.executor import _call_tool

        r1 = _call_tool("tool_a", {}, player=None, speak=None)
        r2 = _call_tool("tool_b", {}, player=None, speak=None)
        r3 = _call_tool("tool_a", {}, player=None, speak=None)
        r4 = _call_tool("tool_b", {}, player=None, speak=None)

        for i, r in enumerate([r1, r2, r3, r4], 1):
            assert not r.startswith("Loop detected:"), (
                f"Call #{i} should NOT be tripped when loopguard is OFF, got {r!r}"
            )

    # -----------------------------------------------------------------------
    # 4. signal_loop fires when loop is detected
    # -----------------------------------------------------------------------

    def test_loop_detected_signal_fires(self, monkeypatch):
        """When loopguard trips on A-B-A-B, signal_loop is called with the reason."""
        import memory.config_manager as cm
        monkeypatch.setattr(cm, "get_flag", _flag_selector(loopguard=True))
        monkeypatch.setattr(
            cm, "get_security_config",
            lambda key, default=None: {
                "loopguard_max_identical": 2,
                "loopguard_max_ping_pong": 2,
                "loopguard_poll_budget": 100,
            }.get(key, default),
        )

        _reset_guard(monkeypatch)

        import core.tool_registry as tr
        monkeypatch.setattr(tr, "dispatch", lambda name, params, **kw: f"ok:{name}")

        # Intercept signal_loop calls
        signals_emitted = []
        import core.loop_guard as lg
        original_signal = lg.signal_loop
        monkeypatch.setattr(lg, "signal_loop", lambda reason: signals_emitted.append(reason))

        from agent.executor import _call_tool

        _call_tool("tool_a", {}, player=None, speak=None)
        _call_tool("tool_b", {}, player=None, speak=None)
        _call_tool("tool_a", {}, player=None, speak=None)  # should trip

        assert len(signals_emitted) >= 1, (
            "signal_loop must be called at least once when loopguard trips"
        )
        assert all(isinstance(r, str) and len(r) > 0 for r in signals_emitted), (
            f"signal_loop must receive a non-empty string reason, got {signals_emitted!r}"
        )
