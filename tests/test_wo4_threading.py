"""AC8 — threading gate: wheel calls go through adapter.run_in_executor.

All SQLiteMemory I/O calls (sqlite_memory, sqlite_store, sqlite_retrieve) in
core/memory_v2.py must be dispatched off-thread via
    adapter.run_in_executor(fn, *args).result(timeout=N)
and NEVER called directly on the calling thread.

Three tests:
    Test 1 — Static: wrappers in memory_v2.py are passed as callables, not called directly.
    Test 2 — Runtime: run_in_executor is actually invoked during build_context.
    Test 3 — Static: no raw _rust.SQLiteMemory / mem.store / mem.retrieve in memory_v2.py.
"""

from __future__ import annotations

import ast
import re
import unittest.mock as mock
from pathlib import Path

import pytest

import core.mark_xl_rust_adapter as adapter
from core.mark_xl_rust_adapter import WHEEL_AVAILABLE

# ---------------------------------------------------------------------------
# Convenience marks
# ---------------------------------------------------------------------------
_skip_no_wheel = pytest.mark.skipif(
    not WHEEL_AVAILABLE,
    reason="mark_xl_rust wheel not built for this platform",
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_MEMORY_V2_SRC = (_REPO_ROOT / "core" / "memory_v2.py").read_text(encoding="utf-8")
_MEMORY_V2_LINES = _MEMORY_V2_SRC.splitlines()


# ===========================================================================
# Test 1 — Static: adapter functions are PASSED to run_in_executor, not called
# ===========================================================================

class TestStaticNoDirectCalls:
    """AC8 static gate: sqlite_* symbols appear only as callables passed to
    run_in_executor, never invoked directly in memory_v2.py."""

    def _direct_call_lines(self, symbol: str) -> list[int]:
        """Return 1-based line numbers in memory_v2.py where ``symbol(`` appears
        OUTSIDE of a run_in_executor(...) context (i.e., called directly)."""
        # Pattern: adapter.<symbol>( NOT preceded by run_in_executor(...,  on the same line.
        # A direct call looks like: adapter.sqlite_memory( OR sqlite_memory(
        # An indirect (correct) call looks like: run_in_executor(adapter.sqlite_memory, ...)
        direct_pattern = re.compile(
            # Match adapter.SYMBOL( or bare SYMBOL( but NOT when SYMBOL is
            # the argument to run_in_executor (comma-terminated, not paren-terminated).
            r"(?<!\w)" + re.escape(symbol) + r"\s*\("
        )
        bad_lines = []
        for i, line in enumerate(_MEMORY_V2_LINES, start=1):
            stripped = line.strip()
            # Skip comments.
            if stripped.startswith("#"):
                continue
            if direct_pattern.search(line):
                # This line calls symbol() directly — that's the prohibited pattern.
                bad_lines.append(i)
        return bad_lines

    def test_sqlite_memory_never_called_directly(self):
        """adapter.sqlite_memory must not be directly called in memory_v2.py.

        The correct pattern is: run_in_executor(adapter.sqlite_memory, db_path)
        NOT:                     adapter.sqlite_memory(db_path)
        """
        bad = self._direct_call_lines("sqlite_memory")
        assert bad == [], (
            f"sqlite_memory() is called directly in memory_v2.py at line(s) {bad}. "
            f"AC8 requires all calls to go through adapter.run_in_executor."
        )

    def test_sqlite_store_never_called_directly(self):
        """adapter.sqlite_store must not be directly called in memory_v2.py."""
        bad = self._direct_call_lines("sqlite_store")
        assert bad == [], (
            f"sqlite_store() is called directly in memory_v2.py at line(s) {bad}. "
            f"AC8 requires all calls to go through adapter.run_in_executor."
        )

    def test_sqlite_retrieve_never_called_directly(self):
        """adapter.sqlite_retrieve must not be directly called in memory_v2.py."""
        bad = self._direct_call_lines("sqlite_retrieve")
        assert bad == [], (
            f"sqlite_retrieve() is called directly in memory_v2.py at line(s) {bad}. "
            f"AC8 requires all calls to go through adapter.run_in_executor."
        )

    def test_run_in_executor_used_at_every_call_site(self):
        """Every sqlite_* reference in memory_v2.py must be inside a run_in_executor call.

        AST walk: collect all Call nodes whose func is adapter.sqlite_* and assert
        they are always the FIRST argument of adapter.run_in_executor, not the
        operator of a direct Call.
        """
        tree = ast.parse(_MEMORY_V2_SRC)

        direct_calls: list[int] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            # Detect adapter.sqlite_memory(...), adapter.sqlite_store(...),
            # adapter.sqlite_retrieve(...) direct-call patterns.
            if isinstance(func, ast.Attribute) and func.attr in (
                "sqlite_memory", "sqlite_store", "sqlite_retrieve"
            ):
                direct_calls.append(node.lineno)

        assert direct_calls == [], (
            f"AST analysis found direct calls to sqlite_* functions at "
            f"memory_v2.py line(s) {direct_calls}. "
            f"AC8: all wheel calls must be passed as callables to run_in_executor."
        )


# ===========================================================================
# Test 2 — Runtime: run_in_executor is actually invoked during build_context
# ===========================================================================

class TestRuntimeRunInExecutorInvoked:
    """AC8 runtime gate: build_context routes wheel calls via run_in_executor."""

    @_skip_no_wheel
    def test_build_context_calls_run_in_executor(self, tmp_path):
        """Assert build_context routes wheel calls through run_in_executor (AC8).

        Wraps adapter.run_in_executor with a tracking shim, runs build_context,
        then verifies that sqlite_memory was submitted via the executor (not on the
        calling thread directly).
        """
        from core.memory_v2 import MemoryV2

        memory = {"identity": {"name": {"value": "TestUser"}}}
        mv2 = MemoryV2(memory, db_dir=str(tmp_path))

        original_run_in_executor = adapter.run_in_executor
        call_log: list[str] = []

        def tracking_run_in_executor(fn, *args, **kwargs):
            name = getattr(fn, "__name__", None) or str(fn)
            call_log.append(name)
            return original_run_in_executor(fn, *args, **kwargs)

        with mock.patch.object(adapter, "run_in_executor", side_effect=tracking_run_in_executor):
            result = mv2.build_context(query="test query", top_k=3)

        assert isinstance(result, dict), "build_context must return a dict"
        assert len(call_log) > 0, (
            "run_in_executor must be called at least once during build_context. "
            f"Lane returned: {result.get('lane')}. "
            "If the wheel is available, the lexical path must use the executor."
        )
        assert any("sqlite_memory" in c for c in call_log), (
            f"sqlite_memory must be submitted through run_in_executor. "
            f"Calls recorded: {call_log}. "
            "AC8: SQLiteMemory construction must be off-thread."
        )

    @_skip_no_wheel
    def test_retrieve_routed_through_run_in_executor(self, tmp_path):
        """sqlite_retrieve must also be submitted via run_in_executor during build_context.

        Pre-stores one fact so the lexical retrieve path is exercised (not just
        the construction path).
        """
        from core.memory_v2 import MemoryV2

        db_path = (tmp_path / "memory_v2.db").as_posix()

        # Pre-populate so there is something to retrieve.
        mem = adapter.run_in_executor(adapter.sqlite_memory, db_path).result(timeout=5)
        assert mem is not None, "Pre-population: sqlite_memory must return a handle"
        adapter.run_in_executor(
            adapter.sqlite_store, mem, "pre-populated fact about testing", "test_source", None
        ).result(timeout=5)

        memory = {"identity": {"name": {"value": "Probe"}}}
        mv2 = MemoryV2(memory, db_dir=str(tmp_path))

        original_run_in_executor = adapter.run_in_executor
        call_log: list[str] = []

        def tracking_run_in_executor(fn, *args, **kwargs):
            name = getattr(fn, "__name__", None) or str(fn)
            call_log.append(name)
            return original_run_in_executor(fn, *args, **kwargs)

        with mock.patch.object(adapter, "run_in_executor", side_effect=tracking_run_in_executor):
            result = mv2.build_context(query="testing", top_k=3)

        assert any("sqlite_retrieve" in c for c in call_log), (
            f"sqlite_retrieve must be submitted through run_in_executor. "
            f"Calls recorded: {call_log}. "
            "AC8: all SQLiteMemory I/O must be off-thread."
        )
        # Sanity: the result must be on the lexical lane (not degraded).
        assert result.get("lane") == "lexical", (
            f"Expected lane='lexical' but got: {result.get('lane')}. "
            f"Context: {result.get('context', '')!r}"
        )


# ===========================================================================
# Test 3 — Static: no raw _rust calls in memory_v2.py (belt-and-suspenders)
# ===========================================================================

class TestStaticNoRawRustCalls:
    """Ensure memory_v2.py never bypasses the adapter by calling _rust directly."""

    def test_no_direct_rust_import_in_memory_v2(self):
        """memory_v2.py must not import mark_xl_rust directly (the wheel).

        All Rust access must flow through the adapter module (single integration
        point principle, matching the adapter module docstring contract).
        The adapter itself (mark_xl_rust_adapter) is the only permitted import.
        """
        # Use AST to check actual import nodes, not string matching, so that
        # docstring mentions of the wheel name do not trigger false positives.
        tree = ast.parse(_MEMORY_V2_SRC)
        direct_wheel_imports: list[int] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "mark_xl_rust":
                        direct_wheel_imports.append(node.lineno)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module == "mark_xl_rust":
                    direct_wheel_imports.append(node.lineno)

        assert direct_wheel_imports == [], (
            f"core/memory_v2.py directly imports mark_xl_rust at "
            f"line(s) {direct_wheel_imports}. "
            "All wheel access must go through core.mark_xl_rust_adapter."
        )

    def test_no_raw_mem_store_call_in_memory_v2(self):
        """memory_v2.py must not call mem.store(...) directly.

        The only permitted store path is:
            adapter.run_in_executor(adapter.sqlite_store, mem, ...).result(...)
        """
        tree = ast.parse(_MEMORY_V2_SRC)

        direct_mem_calls: list[tuple[int, str]] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in ("store", "retrieve"):
                # Any .store(...) or .retrieve(...) call in memory_v2.py is
                # suspicious — these are the Rust object methods that must only
                # be called inside the adapter wrappers, never in memory_v2.
                direct_mem_calls.append((node.lineno, func.attr))

        assert direct_mem_calls == [], (
            f"memory_v2.py contains direct .store()/.retrieve() calls at "
            f"line(s): {direct_mem_calls}. "
            f"These are raw Rust object method calls that violate AC8. "
            f"Use adapter.run_in_executor(adapter.sqlite_store, ...) instead."
        )

    def test_adapter_module_is_the_sole_wheel_gateway(self):
        """memory_v2.py must only access the wheel through the adapter module.

        Verify: 'from core import mark_xl_rust_adapter as adapter' or
        'import core.mark_xl_rust_adapter' is the ONLY external wheel reference.
        There must be no 'SQLiteMemory' referenced directly in memory_v2.py
        (it can only appear quoted in docstrings/comments).
        """
        tree = ast.parse(_MEMORY_V2_SRC)

        # Check for any Name or Attribute node referencing SQLiteMemory as a
        # live identifier (not inside a string literal).
        raw_refs: list[int] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == "SQLiteMemory":
                raw_refs.append(node.lineno)
            elif isinstance(node, ast.Attribute) and node.attr == "SQLiteMemory":
                raw_refs.append(node.lineno)

        assert raw_refs == [], (
            f"memory_v2.py references SQLiteMemory as a live identifier at "
            f"line(s) {raw_refs}. All wheel type references must go through "
            f"adapter functions, not named directly in memory_v2."
        )
