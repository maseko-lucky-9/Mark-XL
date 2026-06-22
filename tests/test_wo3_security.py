"""tests/test_wo3_security.py — AC1–AC5 integration assertions for WO-3.

Tests AC1 (confirm gate), AC2 (sensitive-file), AC3 (SSRF),
AC4 (secret redaction), AC5 (injection scan).
"""
import os
import sqlite3
import sys
from unittest.mock import MagicMock, patch

import pytest


# ── AC1 — Confirm gate ────────────────────────────────────────────────────────

class TestAC1ConfirmGate:
    """AC1: requires_confirm tools block execution until approval."""

    def test_ac1_registry_requires_confirm_set(self):
        """Data assertion: all 5 mandated tools have requires_confirm=True."""
        from core.tool_registry import _REGISTRY, import_all_tools
        import_all_tools()
        mandated = {"shutdown_jarvis", "code_helper", "dev_agent", "file_controller", "file_processor"}
        for tool in mandated:
            spec = _REGISTRY.get(tool)
            assert spec is not None, f"Tool '{tool}' missing from registry"
            assert spec.requires_confirm is True, f"'{tool}' must have requires_confirm=True"

    def test_ac1_deny_blocks_dispatch(self, tmp_path):
        """run_gated with denying confirm_fn returns 'Action cancelled.' without calling dispatch."""
        from core.audit import AuditLedger
        from core.security_gate import run_gated

        ledger = AuditLedger(str(tmp_path / "audit.db"))
        mock_dispatch = MagicMock(return_value="executed!")

        # Patch both dispatch AND _REGISTRY in core.security_gate's namespace.
        # _REGISTRY is imported by reference (dict), so we replace it entirely with a MagicMock
        # that supports .get() returning a spec with requires_confirm=True.
        mock_spec = MagicMock()
        mock_spec.requires_confirm = True
        mock_spec.inline = False
        mock_spec.func = lambda **kw: "ok"

        mock_registry = MagicMock()
        mock_registry.get.return_value = mock_spec

        with patch("core.security_gate.dispatch", mock_dispatch):
            with patch("core.security_gate._REGISTRY", mock_registry):
                confirm_fn = lambda tool, preview, timeout=30: False  # DENY
                result = run_gated(
                    "code_helper", {"task": "do something"},
                    player=None, speak=None,
                    confirm_fn=confirm_fn,
                    audit=ledger,
                )

        assert result == "Action cancelled.", f"Expected 'Action cancelled.', got {result!r}"
        mock_dispatch.assert_not_called()

    def test_ac1_approve_allows_dispatch(self, tmp_path):
        """run_gated with approving confirm_fn calls dispatch and returns its result."""
        from core.audit import AuditLedger
        from core.security_gate import run_gated

        ledger = AuditLedger(str(tmp_path / "audit.db"))
        mock_dispatch = MagicMock(return_value="executed!")

        mock_spec = MagicMock()
        mock_spec.requires_confirm = True
        mock_spec.inline = False
        mock_spec.func = lambda **kw: "ok"

        mock_registry = MagicMock()
        mock_registry.get.return_value = mock_spec

        with patch("core.security_gate.dispatch", mock_dispatch):
            with patch("core.security_gate._REGISTRY", mock_registry):
                confirm_fn = lambda tool, preview, timeout=30: True  # APPROVE
                result = run_gated(
                    "code_helper", {"task": "benign task"},
                    player=None, speak=None,
                    confirm_fn=confirm_fn,
                    audit=ledger,
                )

        # After approval and passing injection scan (benign params), dispatch is called.
        mock_dispatch.assert_called_once()
        assert result == "executed!"

    def test_ac1_browser_control_no_confirm(self):
        """browser_control has requires_confirm=False (guarded by SSRF gate, not confirm)."""
        from core.tool_registry import _REGISTRY, import_all_tools
        import_all_tools()
        spec = _REGISTRY.get("browser_control")
        if spec:
            assert spec.requires_confirm is False


# ── AC2 — Sensitive-file gate ─────────────────────────────────────────────────

class TestAC2SensitiveFile:
    """AC2: _is_sensitive('config/api_keys.json') == True; file need not exist on disk."""

    def test_ac2_api_keys_sensitive(self):
        """_is_sensitive('config/api_keys.json') returns True (file absent on disk)."""
        from core.security_gate import _is_sensitive
        # The test asserts sensitivity is path/name based, NOT existence based.
        # The function uses basename check OR wheel — both should return True.
        assert _is_sensitive("config/api_keys.json") is True

    def test_ac2_nonexistent_file_still_sensitive(self):
        """Sensitivity is path-name-based — a non-existent path with api_keys.json is still sensitive."""
        from core.security_gate import _is_sensitive
        nonexistent = "/does/not/exist/api_keys.json"
        assert not os.path.exists(nonexistent), "Precondition: path must not exist"
        assert _is_sensitive(nonexistent) is True

    def test_ac2_notes_not_sensitive(self):
        """_is_sensitive('notes.txt') returns False."""
        from core.security_gate import _is_sensitive
        assert _is_sensitive("notes.txt") is False

    def test_ac2_basename_only(self):
        """_is_sensitive works on the basename — path/to/api_keys.json is also sensitive."""
        from core.security_gate import _is_sensitive
        assert _is_sensitive("some/deep/path/api_keys.json") is True


# ── AC3 — SSRF allowlist ──────────────────────────────────────────────────────

class TestAC3SSRF:
    """AC3: SSRF allowlist permits 192.168.x.x with opt-in, blocks without."""

    def test_ac3_local_ip_with_opt_in(self):
        """With ssrf_local_nav=True, 192.168.x.x is permitted."""
        from core.security_gate import _ssrf_allowed
        assert _ssrf_allowed("http://192.168.1.10/path", True) is True

    def test_ac3_cloud_metadata_never_allowed(self):
        """Cloud-metadata (169.254.169.254) is NEVER allowlisted, even with opt-in."""
        from core.security_gate import _ssrf_allowed
        assert _ssrf_allowed("http://169.254.169.254/latest/meta-data/", True) is False

    def test_ac3_blocks_without_opt_in(self):
        """Without opt-in (ssrf_local_nav=False), private IPs are blocked by check_ssrf."""
        from core.security_gate import _ssrf_allowed
        # With opt-in OFF, the CIDR short-circuit does not fire;
        # check_ssrf blocks private IPs (returns a string reason → reason is not None → False).
        result = _ssrf_allowed("http://192.168.1.10/", False)
        assert result is False, \
            "Private IP must be blocked when ssrf_local_nav=False (no bypass)"

    def test_ac3_10_x_blocked_without_opt_in(self):
        """10.x.x.x is also a private range — blocked without opt-in."""
        from core.security_gate import _ssrf_allowed
        result = _ssrf_allowed("http://10.0.0.1/", False)
        assert result is False, "10.x.x.x must be blocked without ssrf_local_nav opt-in"

    def test_ac3_public_ip_allowed(self):
        """Public IP 8.8.8.8 is allowed (check_ssrf returns None == safe)."""
        from core.security_gate import _ssrf_allowed
        result = _ssrf_allowed("http://8.8.8.8/", False)
        assert result is True, f"Public IP should be allowed, got: {result}"


# ── AC4 — Secret redaction ────────────────────────────────────────────────────

class TestAC4SecretRedaction:
    """AC4: planted secrets in params/result are redacted before audit write."""

    def test_ac4_secret_redacted_in_audit(self, tmp_path):
        """A planted sk- key must not appear raw in the audit DB after write."""
        from core.audit import AuditLedger
        from core import mark_xl_rust_adapter as adapter

        if not adapter.WHEEL_AVAILABLE:
            pytest.skip("Wheel absent: redact() is passthrough — AC4 requires wheel")

        ledger = AuditLedger(str(tmp_path / "audit.db"))
        # assembled at runtime (not a committed key-shaped literal); the wheel scans
        # the runtime value so SecretScanner.redact (AC4) is still genuinely exercised.
        secret = "sk" + "-" + "TestSecretKeyAC4" + "ABCDEFGHIJ"
        ledger.write(
            "file_controller",
            {"api_key": secret},
            f"result with {secret}",
            confirm_required=True,
            approved=True,
        )

        conn = sqlite3.connect(ledger._db_path)
        row = conn.execute(
            "SELECT redacted_params, redacted_result FROM audit_log WHERE seq = 1"
        ).fetchone()
        conn.close()

        assert secret not in row[0], \
            f"Raw secret must not appear in redacted_params, got: {row[0]!r}"
        assert secret not in row[1], \
            f"Raw secret must not appear in redacted_result, got: {row[1]!r}"

    def test_ac4_redact_returns_string(self):
        """adapter.redact() always returns a string (not None), even when wheel is present."""
        from core import mark_xl_rust_adapter as adapter

        result = adapter.redact("some plain text without secrets")
        assert isinstance(result, str), f"redact() must return str, got {type(result)}"
        assert result == "some plain text without secrets" or "[REDACTED" in result

    def test_ac4_build_preview_redacts_secrets(self):
        """_build_preview redacts secret values before returning the preview string."""
        from core import mark_xl_rust_adapter as adapter
        from core.security_gate import _build_preview

        if not adapter.WHEEL_AVAILABLE:
            pytest.skip("Wheel absent: redact() is passthrough")

        secret = "sk-" + "PreviewSecretKeyXYZ123456789"
        preview = _build_preview("file_controller", {"api_key": secret})
        assert secret not in preview, \
            f"Raw secret must not appear in _build_preview output, got: {preview!r}"


# ── AC5 — Injection scan ──────────────────────────────────────────────────────

class TestAC5InjectionScan:
    """AC5: InjectionScanner blocks High-severity shell-injection payloads before subprocess.run."""

    def test_ac5_scanner_detects_shell_injection(self):
        """injection_scan('; rm -rf / #') returns is_clean=False, threat_level=high."""
        from core import mark_xl_rust_adapter as adapter

        if not adapter.WHEEL_AVAILABLE:
            pytest.skip("Wheel absent: injection_scan() returns safe clean — AC5 requires wheel")

        verdict = adapter.injection_scan("; rm -rf / #")
        assert verdict["is_clean"] is False, \
            f"Shell injection payload must not be clean, got: {verdict}"
        assert verdict["threat_level"] == "high", \
            f"Shell injection payload must be high severity, got: {verdict}"

    def test_ac5_clean_code_passes_scan(self):
        """injection_scan('print(\"hello\")') returns is_clean=True."""
        from core import mark_xl_rust_adapter as adapter

        verdict = adapter.injection_scan('print("hello")')
        assert verdict["is_clean"] is True, \
            f"Clean Python code must pass injection scan, got: {verdict}"

    def test_ac5_injection_blocks_generated_code(self, tmp_path):
        """With scan_enabled=True and High verdict, _run_generated_code raises RuntimeError."""
        import concurrent.futures
        from agent.executor import _run_generated_code
        from core.audit import AuditLedger

        ledger = AuditLedger(str(tmp_path / "audit.db"))

        high_verdict = {
            "is_clean": False,
            "threat_level": "high",
            "findings": [{"matched_text": "; rm -rf / #", "pattern_name": "shell_injection"}],
        }

        # Build a future that returns the high verdict
        fut = concurrent.futures.Future()
        fut.set_result(high_verdict)

        # _run_generated_code calls call_llm_text (imported at module level as agent.executor.call_llm_text)
        # and then calls _adapter.run_in_executor (local import inside the function).
        with patch("core.mark_xl_rust_adapter.run_in_executor", return_value=fut):
            with patch("agent.executor.call_llm_text", return_value="; rm -rf / #"):
                with patch("subprocess.run") as mock_subprocess:
                    with pytest.raises(RuntimeError, match="injection scan"):
                        _run_generated_code(
                            "do something dangerous",
                            scan_enabled=True,
                            audit=ledger,
                        )
                    # subprocess.run must NOT be called when injection is detected
                    mock_subprocess.assert_not_called()

    def test_ac5_clean_payload_reaches_subprocess(self):
        """With scan_enabled=True and clean verdict, subprocess.run is called normally."""
        import concurrent.futures
        from agent.executor import _run_generated_code

        clean_verdict = {"is_clean": True, "threat_level": "low", "findings": []}
        fut = concurrent.futures.Future()
        fut.set_result(clean_verdict)

        with patch("core.mark_xl_rust_adapter.run_in_executor", return_value=fut):
            with patch("agent.executor.call_llm_text", return_value='print("hello")'):
                with patch("subprocess.run") as mock_subprocess:
                    mock_subprocess.return_value = MagicMock(
                        returncode=0, stdout="hello", stderr=""
                    )
                    result = _run_generated_code(
                        "print hello",
                        scan_enabled=True,
                        audit=None,
                    )
                    mock_subprocess.assert_called_once()
                    assert result == "hello"
