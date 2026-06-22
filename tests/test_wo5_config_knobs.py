"""T11 — Config-knob plumbing tests for WO-5 LoopGuard."""
import pytest
from unittest.mock import patch


class TestKnobPlumbing:
    def test_load_knobs_override(self):
        """load_knobs() returns overridden values from get_security_config."""
        with patch("core.loop_guard.get_security_config") as mock_gsc:
            def side_effect(key, default):
                return {"loopguard_max_identical": 3, "loopguard_max_ping_pong": 2, "loopguard_poll_budget": 10}.get(key, default)
            mock_gsc.side_effect = side_effect
            import core.loop_guard as lg
            result = lg.load_knobs()
            assert result == (3, 2, 10)

    def test_load_knobs_string_coercion(self):
        """String values in config are int-coerced without error."""
        with patch("core.loop_guard.get_security_config") as mock_gsc:
            def side_effect(key, default):
                return {"loopguard_max_identical": "5", "loopguard_max_ping_pong": "3", "loopguard_poll_budget": "20"}.get(key, str(default))
            mock_gsc.side_effect = side_effect
            import core.loop_guard as lg
            result = lg.load_knobs()
            assert result == (5, 3, 20)
            assert all(isinstance(x, int) for x in result)

    def test_flag_off_never_calls_load_knobs(self):
        """Flag-OFF: load_knobs() is never called (FR-10)."""
        import core.loop_guard as lg
        with patch.object(lg, 'load_knobs', side_effect=RuntimeError("Should not be called")) as mock_lk:
            # When enable_loopguard=False, get_guard returns None without calling load_knobs
            result = lg.get_guard(False, max_identical=50, max_ping_pong=4, poll_budget=100)
            assert result is None
            mock_lk.assert_not_called()

    def test_max_depth_not_a_kwarg(self):
        """loop_guard_max_depth is NOT a wheel kwarg (ADR-C / OQ-7)."""
        import core.mark_xl_rust_adapter as adapter
        if not adapter.WHEEL_AVAILABLE:
            pytest.skip("mark_xl_rust wheel not available")
        # Valid kwargs work
        g = adapter.get_loop_guard(max_identical=2, max_ping_pong=2, poll_budget=5)
        assert g is not None
        # max_depth raises TypeError
        with pytest.raises(TypeError):
            adapter.get_loop_guard(max_depth=2)

    def test_flag_schema_no_numeric_entries(self):
        """Numeric knob keys are NOT in _FLAG_SCHEMA."""
        from memory.config_manager import _FLAG_SCHEMA
        numeric_keys = {"loopguard_max_identical", "loopguard_max_ping_pong", "loopguard_poll_budget"}
        schema_keys = set(_FLAG_SCHEMA.keys())
        overlap = numeric_keys & schema_keys
        assert overlap == set(), f"Numeric knobs must not be in _FLAG_SCHEMA, found: {overlap}"

    def test_flags_json_numeric_keys_present(self):
        """flags.json contains the 3 numeric knob keys."""
        import json
        from pathlib import Path
        flags_path = Path("/Users/ltmas/Repo/agents/mark-xl/config/flags.json")
        data = json.loads(flags_path.read_text(encoding="utf-8"))
        assert "loopguard_max_identical" in data
        assert "loopguard_max_ping_pong" in data
        assert "loopguard_poll_budget" in data
        assert isinstance(data["loopguard_max_identical"], int)
        assert isinstance(data["loopguard_max_ping_pong"], int)
        assert isinstance(data["loopguard_poll_budget"], int)
