"""
conftest.py — WO-0 test harness for Mark-XL.

Fixtures:
- qapp: session-scoped PyQt6 QApplication singleton (headless)
- qtest: function-scoped QTest reference
- mock_llm: function-scoped monkeypatch of all 3 LLM entry points in core.llm_client
- thread_guard: function-scoped fixture to join daemon threads spawned by tests
"""
import os
import sys
import threading

# Set headless display BEFORE any PyQt6 import so CI legs without a display work.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

# ---------------------------------------------------------------------------
# Ensure project root is on sys.path so imports like "from core.X" work.
# ---------------------------------------------------------------------------
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ---------------------------------------------------------------------------
# QApplication — session-scoped singleton
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def qapp():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv[:1])
    yield app
    # Do NOT call app.quit() here — it would destroy the singleton between modules.


# ---------------------------------------------------------------------------
# QTest — function-scoped (stateless reference, safe to reuse)
# ---------------------------------------------------------------------------
@pytest.fixture
def qtest():
    from PyQt6.QtTest import QTest
    return QTest


# ---------------------------------------------------------------------------
# Ollama / LLM mock — function-scoped (uses monkeypatch; no pytest-mock needed)
# ---------------------------------------------------------------------------
@pytest.fixture
def mock_llm(monkeypatch):
    """Monkeypatch all 3 LLM entry points so no test hits a live Ollama socket."""
    monkeypatch.setattr("core.llm_client.call_llm_text",   lambda *a, **kw: "mocked_response")
    monkeypatch.setattr("core.llm_client.call_llm",         lambda *a, **kw: "mocked_response")
    monkeypatch.setattr("core.llm_client.call_llm_stream",  lambda *a, **kw: iter(["mocked"]))


# ---------------------------------------------------------------------------
# Thread guard — function-scoped helper for tests that spawn daemon threads
# (e.g. shutdown_jarvis spawns a daemon thread calling os._exit(0))
# ---------------------------------------------------------------------------
@pytest.fixture
def thread_guard():
    """Collect threads spawned during the test and join them (with timeout)."""
    before = set(threading.enumerate())
    yield
    after = set(threading.enumerate())
    new_threads = after - before
    for t in new_threads:
        if t.daemon:
            t.join(timeout=0.5)  # best-effort; daemon threads die with the process anyway
