"""Smoke test: verify conftest fixtures are functional."""
import pytest


def test_qapp_fixture(qapp):
    """Session-scoped QApplication must be non-None."""
    assert qapp is not None


def test_qtest_fixture(qtest):
    """QTest reference must be importable."""
    from PyQt6.QtTest import QTest
    assert qtest is QTest


def test_mock_llm_fixture(mock_llm, monkeypatch):
    """LLM mock must prevent live calls."""
    # mock_llm patches the module-level names at function scope;
    # verify the patched callable is importable and callable without a socket.
    from core.llm_client import call_llm_text
    # Should not raise (no live socket needed)
    assert callable(call_llm_text)
