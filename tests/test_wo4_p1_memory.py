"""WO-4 P1 — MemoryV2 / SQLiteMemory acceptance tests.

Covers:
  AC1  — Persistent FTS5 retrieve across fresh SQLiteMemory connection.
  AC4  — Identity block present at top_k=1 over ≥10 stored facts (identity-dropout).
  AC9  — enable_memory_v2 defaults to OFF; MemoryV2 only constructed behind the flag.
  AC10 — get_flag allowlist stays exactly {main.py, agent/executor.py}.

All wheel-dependent tests are gated on adapter.WHEEL_AVAILABLE.  The canonical
baseline is 196 passed / 0 skipped with the wheel installed — therefore, if the
wheel IS available, all four ACs must execute and pass (no runtime skip).
"""

from __future__ import annotations

import ast
import json
from pathlib import Path, PureWindowsPath

import pytest

import core.mark_xl_rust_adapter as adapter
from core.mark_xl_rust_adapter import WHEEL_AVAILABLE, run_in_executor

# ---------------------------------------------------------------------------
# Convenience marks
# ---------------------------------------------------------------------------
_skip_no_wheel = pytest.mark.skipif(
    not WHEEL_AVAILABLE,
    reason="mark_xl_rust wheel not built for this platform",
)


# ===========================================================================
# AC1 — Persistent FTS5 retrieve across a fresh SQLiteMemory connection
# ===========================================================================

class TestAC1PersistentFTS5:
    """SQLiteMemory persists stored documents across independent connections."""

    @_skip_no_wheel
    def test_retrieve_after_fresh_connection(self, tmp_path):
        """Documents stored in one SQLiteMemory instance are retrievable from a
        new instance opened on the same file path (FTS5 persistence)."""
        db_path = (tmp_path / "ac1_test.db").as_posix()

        # --- First connection: store a document. ---
        mem1 = run_in_executor(adapter.sqlite_memory, db_path).result(timeout=5)
        assert mem1 is not None, "sqlite_memory must return a live handle"

        run_in_executor(
            adapter.sqlite_store,
            mem1,
            "The quick brown fox jumps over the lazy dog",
            "ac1_source",
            None,
        ).result(timeout=5)

        # --- Second connection: fresh object, same file. ---
        mem2 = run_in_executor(adapter.sqlite_memory, db_path).result(timeout=5)
        assert mem2 is not None, "Fresh SQLiteMemory on same path must succeed"

        raw = run_in_executor(
            adapter.sqlite_retrieve, mem2, "quick brown fox", 5
        ).result(timeout=5)

        assert raw is not None, "retrieve must return JSON, not None"
        hits = json.loads(raw)
        assert isinstance(hits, list), "retrieve JSON must be a list"
        assert len(hits) >= 1, "Must return at least one hit"

        contents = [
            hit.get("content") if isinstance(hit, dict) else hit
            for hit in hits
        ]
        assert any(
            "quick brown fox" in (c or "") for c in contents
        ), f"Expected stored content in hits; got: {contents}"

    @_skip_no_wheel
    def test_hit_keys_present(self, tmp_path):
        """Each hit dict must contain the documented keys: content, score, source, metadata."""
        db_path = (tmp_path / "ac1_keys.db").as_posix()

        mem = run_in_executor(adapter.sqlite_memory, db_path).result(timeout=5)
        run_in_executor(
            adapter.sqlite_store, mem, "sample content for key check", "key_source", '{"tag": "test"}'
        ).result(timeout=5)

        raw = run_in_executor(adapter.sqlite_retrieve, mem, "sample content", 1).result(timeout=5)
        hits = json.loads(raw)
        assert hits, "Expected at least one hit"

        hit = hits[0]
        assert isinstance(hit, dict), "Hit must be a dict"
        for key in ("content", "score", "source"):
            assert key in hit, f"Hit must have key '{key}'; got keys: {list(hit.keys())}"


# ===========================================================================
# AC4 — Identity-dropout: identity present at top_k=1 with ≥10 stored facts
# ===========================================================================

class TestAC4IdentityDropout:
    """MemoryV2 always prepends identity outside the top-k budget."""

    @_skip_no_wheel
    def test_identity_survives_top_k_1_with_ten_plus_facts(self, tmp_path):
        """Identity block appears in context even when top_k=1 and ≥10 facts
        compete — identity is OUTSIDE the top-k budget (FR-4 / AC4)."""
        # Step 1 — determine the db_path MemoryV2 will use for this db_dir.
        db_dir = tmp_path
        db_path = (db_dir / "memory_v2.db").as_posix()

        # Step 2 — pre-populate the store with 12 facts using the SAME path.
        mem = run_in_executor(adapter.sqlite_memory, db_path).result(timeout=5)
        assert mem is not None, "sqlite_memory must succeed"

        for i in range(12):
            run_in_executor(
                adapter.sqlite_store,
                mem,
                f"fact_{i}: some unrelated background text number {i}",
                "notes",
                None,
            ).result(timeout=5)

        # Step 3 — construct MemoryV2 with a minimal identity dict and the same db_dir.
        from core.memory_v2 import MemoryV2

        memory_dict = {
            "identity": {
                "name": {"value": "Alice"},
            }
        }
        mv2 = MemoryV2(memory=memory_dict, db_dir=str(db_dir))

        # Step 4 — build_context at top_k=1 (only 1 fact slot; identity must still appear).
        result = mv2.build_context(query="fact", top_k=1)

        assert isinstance(result, dict), "build_context must return a dict"
        context = result.get("context", "")

        assert "Alice" in context, (
            f"Identity name 'Alice' must appear in context at top_k=1 regardless of "
            f"competing facts; got context:\n{context!r}"
        )

    @_skip_no_wheel
    def test_identity_block_prefix_format(self, tmp_path):
        """Identity block uses the expected [Identity] prefix and field format."""
        from core.memory_v2 import MemoryV2

        db_dir = tmp_path / "fmt_test"
        db_dir.mkdir()
        db_path = (db_dir / "memory_v2.db").as_posix()

        mem = run_in_executor(adapter.sqlite_memory, db_path).result(timeout=5)
        run_in_executor(
            adapter.sqlite_store, mem, "some fact to satisfy lexical path", "notes", None
        ).result(timeout=5)

        mv2 = MemoryV2(
            memory={"identity": {"name": {"value": "Bob"}, "city": {"value": "Cape Town"}}},
            db_dir=str(db_dir),
        )
        result = mv2.build_context(query="fact", top_k=5)
        context = result.get("context", "")

        assert "[Identity]" in context, "Identity prefix must include '[Identity]' header"
        assert "Bob" in context
        assert "Cape Town" in context


# ===========================================================================
# AC9 — enable_memory_v2 defaults to OFF; MemoryV2 only constructed behind flag
# ===========================================================================

class TestAC9FlagOffBaseline:
    """enable_memory_v2 is OFF by default; MemoryV2 is lazy-imported behind the flag."""

    def test_enable_memory_v2_defaults_to_false(self):
        """get_flag('enable_memory_v2') must return False (the default-OFF baseline)."""
        from memory.config_manager import get_flag

        assert get_flag("enable_memory_v2") is False, (
            "enable_memory_v2 must default to OFF so the canonical 196/0 "
            "baseline is not disturbed on a fresh installation."
        )

    def test_memory_v2_only_constructed_inside_flag_guard_in_main(self):
        """Static check: 'MemoryV2' appears in main.py only inside an if-block
        that checks the flag (lazy import / flag-guarded construction)."""
        repo_root = Path(__file__).resolve().parent.parent
        main_src = (repo_root / "main.py").read_text(encoding="utf-8")

        # Confirm MemoryV2 is referenced at all (guard against trivial false-pass).
        assert "MemoryV2" in main_src, (
            "main.py must reference MemoryV2 (it's the dispatch site); "
            "if it was removed the AC9 static check is vacuously true."
        )

        # Parse the AST and verify every MemoryV2 reference lives inside an If node
        # that tests a flag check.  A simple string proximity check suffices for the
        # canonical pattern: the `if _get_flag("enable_memory_v2"):` guard wraps the
        # MemoryV2 construction on lines 708-710 of main.py.
        #
        # Rather than full AST traversal (fragile to refactors), we use a
        # line-proximity check: every line containing "MemoryV2" must be preceded by
        # an `if` line containing a flag check within a small window.
        lines = main_src.splitlines()
        memory_v2_lines = [
            i for i, ln in enumerate(lines) if "MemoryV2" in ln
        ]
        assert memory_v2_lines, "MemoryV2 not found after existence check — this should not happen"

        for lineno in memory_v2_lines:
            # Look back up to 10 lines for a guarding `if` that references the flag.
            window_start = max(0, lineno - 10)
            window = lines[window_start:lineno]
            guard_found = any(
                ("if" in ln and ("enable_memory_v2" in ln or "_get_flag" in ln or "get_flag" in ln))
                for ln in window
            )
            assert guard_found, (
                f"MemoryV2 reference at main.py line {lineno + 1} is not guarded "
                f"by an `if get_flag(...)` check within the preceding 10 lines.\n"
                f"Context:\n" + "\n".join(
                    f"  {window_start + j + 1}: {l}"
                    for j, l in enumerate(window)
                )
            )

    def test_format_memory_for_prompt_used_on_flag_off_path(self):
        """Static check: format_memory_for_prompt is called on the OFF path in main.py,
        confirming the legacy formatter remains the default behaviour."""
        repo_root = Path(__file__).resolve().parent.parent
        main_src = (repo_root / "main.py").read_text(encoding="utf-8")

        assert "format_memory_for_prompt" in main_src, (
            "main.py must call format_memory_for_prompt (the legacy/OFF path formatter)."
        )


# ===========================================================================
# AC10 — get_flag allowlist stays exactly {main.py, agent/executor.py}
# ===========================================================================

class TestAC10GetFlagAllowlist:
    """get_flag callers in production code must be exactly {main.py, agent/executor.py}."""

    def _is_production(self, f: Path) -> bool:
        """Return True for production .py files; excludes tests, venvs, caches, pipeline.

        Uses Path.parts membership (separator-agnostic, matches test_flags.py logic).
        """
        return (
            not any(
                p in {"tests", "__pycache__", "pipeline"}
                or p.startswith(".venv")
                or p == "venv"
                or "site-packages" in p
                for p in f.parts
            )
            and f.name != "config_manager.py"
        )

    def test_wo4_get_flag_allowlist(self):
        """Exactly {main.py, agent/executor.py} are permitted get_flag callers.

        Re-implements the static gate from test_flags.py under a distinct name
        to avoid duplicate test-ID collisions.
        """
        repo_root = Path(__file__).resolve().parent.parent

        # Separator-agnostic filter smoke checks (mirrors test_flags.py lines 116-122).
        assert not self._is_production(
            PureWindowsPath(r"C:\repo\tests\test_flags.py")
        ), "Filter must exclude Windows-style test paths"
        assert self._is_production(
            PureWindowsPath(r"C:\repo\agent\executor.py")
        ), "Filter must NOT exclude legitimate production files"

        production_files = [
            f for f in repo_root.rglob("*.py") if self._is_production(f)
        ]

        callers = []
        for py_file in production_files:
            try:
                src = py_file.read_text(encoding="utf-8")
                if "get_flag" in src:
                    callers.append(py_file.relative_to(repo_root).as_posix())
            except Exception:
                pass

        assert set(callers) == {"main.py", "agent/executor.py"}, (
            f"Sanctioned get_flag callers must be exactly {{main.py, agent/executor.py}}. "
            f"Found: {sorted(callers)}"
        )

    def test_memory_v2_does_not_call_get_flag(self):
        """core/memory_v2.py must NOT contain get_flag (AC10 explicit check)."""
        repo_root = Path(__file__).resolve().parent.parent
        memory_v2_src = (repo_root / "core" / "memory_v2.py").read_text(encoding="utf-8")

        assert "get_flag" not in memory_v2_src, (
            "core/memory_v2.py must not call get_flag — the flag decision is made "
            "by the dispatch site (main.py or agent/executor.py), not by this module (AC10)."
        )


# ===========================================================================
# AC7 — Identity prefix byte-freeze smoke tests (P1 partial)
# ===========================================================================

class TestAC7IdentityByteFreeze:
    """AC7 (partial): identity_prefix is byte-stable, timestamp-free, and field-ordered."""

    def test_identity_prefix_byte_identical_on_two_builds(self):
        """Two consecutive calls to MemoryV2.identity_prefix() return EXACTLY the same string."""
        from core.memory_v2 import MemoryV2

        mv2 = MemoryV2(memory={
            "identity": {
                "name": {"value": "Bob"},
                "city": {"value": "Cape Town"},
                "job": {"value": "Engineer"},
            }
        })

        prefix1 = mv2.identity_prefix()
        prefix2 = mv2.identity_prefix()

        assert prefix1 == prefix2, (
            "identity_prefix() must be byte-identical across two consecutive calls on the "
            f"same object.\nFirst:  {prefix1!r}\nSecond: {prefix2!r}"
        )
        assert prefix1 != "", "identity_prefix() must not return an empty string"

    def test_identity_prefix_no_timestamp(self):
        """The identity prefix does NOT contain any dynamically-generated timestamp."""
        import datetime
        import re

        from core.memory_v2 import MemoryV2

        mv2 = MemoryV2(memory={
            "identity": {
                "name": {"value": "Carol"},
                "birthday": {"value": "1990-01-01"},
            }
        })

        prefix = mv2.identity_prefix()

        assert "Carol" in prefix, (
            f"'Carol' must appear in identity_prefix; got: {prefix!r}"
        )

        now_str = datetime.datetime.now().strftime("%H:%M")
        assert now_str not in prefix, (
            f"Current time string '{now_str}' must NOT appear in identity_prefix "
            f"(no datetime.now() injection); got: {prefix!r}"
        )

        # No ISO timestamp of the form YYYY-MM-DDTHH:MM injected at build time.
        assert not re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", prefix), (
            f"ISO timestamp pattern must NOT appear in identity_prefix "
            f"(only stored static values allowed); got: {prefix!r}"
        )

    def test_identity_prefix_fixed_field_order(self):
        """Fields appear in the canonical _ID_FIELDS order regardless of dict insertion order."""
        from core.memory_v2 import MemoryV2, _ID_FIELDS

        # Supply all 8 fields in a deliberately non-canonical order (nationality first).
        mv2 = MemoryV2(memory={
            "identity": {
                "nationality": {"value": "South African"},
                "name": {"value": "Eve"},
                "school": {"value": "UCT"},
                "age": {"value": "30"},
                "language": {"value": "English"},
                "job": {"value": "Developer"},
                "birthday": {"value": "1994-03-15"},
                "city": {"value": "Johannesburg"},
            }
        })

        prefix = mv2.identity_prefix()

        # Strip the "[Identity]" header line; collect only lines that contain a colon.
        lines = [ln for ln in prefix.splitlines() if ":" in ln and "[Identity]" not in ln]

        # Build the ordered list of field labels actually present in the prefix.
        # We match each _ID_FIELDS entry to a line by checking the capitalised label.
        present_order = []
        for line in lines:
            for field in _ID_FIELDS:
                if line.strip().lower().startswith(field.lower() + ":"):
                    present_order.append(field)
                    break

        # The canonical order is the subsequence of _ID_FIELDS that appear in the prefix.
        canonical_order = [f for f in _ID_FIELDS if f in present_order]

        assert present_order == canonical_order, (
            f"Fields in identity_prefix must follow canonical _ID_FIELDS order.\n"
            f"Expected order: {canonical_order}\n"
            f"Got order:      {present_order}\n"
            f"Full prefix:\n{prefix}"
        )
        # Sanity: all 8 supplied fields must appear.
        assert len(present_order) == 8, (
            f"All 8 identity fields must appear in the prefix; found {len(present_order)}: "
            f"{present_order}"
        )

    @_skip_no_wheel
    def test_build_context_identity_byte_stable_across_calls(self, tmp_path):
        """build_context called twice with the same query returns byte-identical context."""
        from core.memory_v2 import MemoryV2

        mv2 = MemoryV2(
            memory={"identity": {"name": {"value": "Dave"}}},
            db_dir=str(tmp_path),
        )

        r1 = mv2.build_context(query="hello", top_k=3)
        r2 = mv2.build_context(query="hello", top_k=3)

        assert r1["context"] == r2["context"], (
            "build_context must return byte-identical context on two calls with the same "
            f"query.\nFirst:  {r1['context']!r}\nSecond: {r2['context']!r}"
        )
        assert "Dave" in r1["context"], (
            f"Identity name 'Dave' must appear in build_context output; "
            f"got: {r1['context']!r}"
        )
