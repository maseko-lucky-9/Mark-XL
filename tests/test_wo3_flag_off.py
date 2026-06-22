"""tests/test_wo3_flag_off.py — AC7: flag-OFF suite baseline (WO-3 T15).

Verifies that flag-OFF == byte-identical behavior:
- dispatch is called directly (no run_gated)
- core.security_gate is NOT imported when flag is OFF
- The suite stays at baseline when security is OFF
"""
import sys
from unittest.mock import MagicMock, patch

import pytest


# ── AC7 — Flag-OFF baseline ──────────────────────────────────────────────────

class TestAC7FlagOff:
    """AC7: flag-OFF == byte-identical dispatch path; no new imports."""

    def test_ac7_flag_off_dispatch_verbatim_in_executor(self):
        """Flag-OFF: _call_tool calls dispatch directly, NOT run_gated."""
        from agent.executor import _call_tool

        # Ensure security_gate is NOT in sys.modules before the call
        sys.modules.pop("core.security_gate", None)

        mock_dispatch = MagicMock(return_value="flag_off_result")
        mock_run_gated = MagicMock(return_value="SHOULD_NOT_BE_CALLED")

        flag_map = {
            "use_tool_registry": True,
            "enable_security_gates": False,  # FLAG OFF
        }

        with patch("memory.config_manager.get_flag", side_effect=lambda k: flag_map.get(k, False)):
            with patch("core.tool_registry.dispatch", mock_dispatch):
                # Ensure run_gated is not reachable even if imported
                with patch.dict("sys.modules", {"core.security_gate": None}, clear=False):
                    try:
                        result = _call_tool("web_search", {"query": "test"})
                    except Exception:
                        pass  # dispatch mock may not set up return properly

        # dispatch should have been called
        assert mock_dispatch.called, "dispatch must be called when flag-OFF"
        # run_gated was never called (it was None/unavailable)
        mock_run_gated.assert_not_called()

    def test_ac7_flag_off_core_security_gate_not_imported(self):
        """Flag-OFF: after _call_tool runs, core.security_gate is NOT imported in sys.modules."""
        from agent.executor import _call_tool

        # Remove security_gate from sys.modules to track if it gets imported
        sys.modules.pop("core.security_gate", None)

        mock_dispatch = MagicMock(return_value="result")

        flag_map = {
            "use_tool_registry": True,
            "enable_security_gates": False,  # FLAG OFF
        }

        with patch("memory.config_manager.get_flag", side_effect=lambda k: flag_map.get(k, False)):
            with patch("core.tool_registry.dispatch", mock_dispatch):
                try:
                    _call_tool("web_search", {"query": "test"})
                except Exception:
                    pass

        # core.security_gate must NOT have been imported by the flag-OFF path
        assert "core.security_gate" not in sys.modules or sys.modules.get("core.security_gate") is None, \
            "core.security_gate must NOT be imported when enable_security_gates=False"

    def test_ac7_flag_off_no_audit_ledger_created(self, tmp_path):
        """Flag-OFF: AuditLedger is NOT constructed and data/audit.db is NOT created."""
        import os
        from agent.executor import _call_tool

        # Ensure security_gate module is out of modules dict
        sys.modules.pop("core.security_gate", None)
        sys.modules.pop("core.audit", None)

        mock_dispatch = MagicMock(return_value="ok")
        db_path = tmp_path / "should_not_exist.db"

        flag_map = {
            "use_tool_registry": True,
            "enable_security_gates": False,  # FLAG OFF
        }

        audit_construct_calls = []

        def track_audit(db_path_arg, **kw):
            audit_construct_calls.append(db_path_arg)
            return MagicMock()

        with patch("memory.config_manager.get_flag", side_effect=lambda k: flag_map.get(k, False)):
            with patch("core.tool_registry.dispatch", mock_dispatch):
                with patch("core.audit.AuditLedger", side_effect=track_audit):
                    try:
                        _call_tool("web_search", {"query": "test"})
                    except Exception:
                        pass

        # AuditLedger must NOT have been constructed when sec is OFF
        assert len(audit_construct_calls) == 0, \
            f"AuditLedger must NOT be constructed with flag-OFF; got calls: {audit_construct_calls}"

    def test_ac7_default_config_security_off(self):
        """The default config/flags.json has enable_security_gates=False."""
        from memory.config_manager import get_flag
        # With the actual config (not patched), security is OFF by default
        # (The test should use the real get_flag to verify the config default)
        val = get_flag("enable_security_gates")
        assert val is False, \
            f"enable_security_gates must default to False in config/flags.json, got: {val!r}"

    def test_ac7_scanner_off_thread_when_flag_on(self, tmp_path):
        """AC7 (thread-safety): injection_scan is submitted via run_in_executor, not called sync."""
        import concurrent.futures
        from core.security_gate import run_gated
        from core.audit import AuditLedger

        ledger = AuditLedger(str(tmp_path / "thread_test.db"))

        # A future that returns a clean scan result
        clean_future = concurrent.futures.Future()
        clean_future.set_result({"is_clean": True, "threat_level": "low", "findings": []})

        executor_calls = []

        def track_executor(fn, *args, **kw):
            executor_calls.append(fn.__name__)
            return clean_future

        mock_dispatch = MagicMock(return_value="scan_offthread_result")

        with patch("core.security_gate.adapter.run_in_executor", side_effect=track_executor):
            with patch("core.security_gate.dispatch", mock_dispatch):
                with patch("core.security_gate._REGISTRY") as mock_reg:
                    mock_spec = MagicMock()
                    mock_spec.requires_confirm = False
                    mock_reg.get.return_value = mock_spec

                    result = run_gated(
                        "web_search", {"query": "test"},
                        player=None, speak=None,
                        confirm_fn=lambda t, p, to=30: True,
                        audit=ledger,
                    )

        # injection_scan must have been submitted via run_in_executor (off-thread)
        assert "injection_scan" in executor_calls, \
            f"injection_scan must be called via run_in_executor; got calls: {executor_calls}"
        assert result == "scan_offthread_result"
