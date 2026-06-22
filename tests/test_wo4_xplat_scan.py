"""T6/T18 — Cross-platform fragility gate for WO-4 P1 + P2 modules (AC11).

Static scan only — no application code is imported.  Each check reads file
text and greps for patterns that would break on Windows (or on any non-UTF-8
locale).  The test reports ALL violations in one assert message so a single
run identifies every issue.

AC11 coverage:
  P1 files — core/memory_v2.py, core/mark_xl_rust_adapter.py,
              memory/config_manager.py, main.py, agent/executor.py,
              tests/test_wo4_p1_memory.py
  P2 files — core/embeddings.py, tests/test_wo4_p2_memory.py
"""
import re
from pathlib import Path

REPO = Path("/Users/ltmas/Repo/agents/mark-xl")

# ---------------------------------------------------------------------------
# P1 module and test files to scan.
# Files that do not exist yet are silently skipped (T5 test file may arrive later).
# ---------------------------------------------------------------------------
P1_FILES = [
    REPO / "core/memory_v2.py",
    REPO / "core/mark_xl_rust_adapter.py",
    REPO / "memory/config_manager.py",
    REPO / "main.py",
    REPO / "agent/executor.py",
    # T5 test file — skip gracefully when absent.
    REPO / "tests/test_wo4_p1_memory.py",
]

# ---------------------------------------------------------------------------
# P2 module and test files to scan (T18).
# Files that do not exist yet are silently skipped.
# ---------------------------------------------------------------------------
P2_FILES = [
    REPO / "core/embeddings.py",
    REPO / "tests/test_wo4_p2_memory.py",
]

ALL_FILES = P1_FILES + P2_FILES


def _read(path: Path):
    """Return (text, lines) or (None, None) when file is absent."""
    if not path.exists():
        return None, None
    text = path.read_text(encoding="utf-8")
    return text, text.splitlines()


class TestWO4XplatScan:
    """AC11 — cross-platform fragility gate for WO-4 P1 + P2 files."""

    # ------------------------------------------------------------------
    # Check 1 — Path separator hygiene
    #
    # Variables named *_path, *_dir, db_path, bak_path, json_path must
    # not be assigned via raw string concatenation with "/" as separator.
    # The invariant: every filesystem-path variable is built with Path(...)
    # or .as_posix(), never with string "/" concatenation like:
    #   bad:  db_path = base + "/" + "file.db"
    #   bad:  db_path = f"{base}/file.db"   (only if base is a FS variable)
    #
    # We use a practical heuristic:
    #   a) Flag any string literal containing "\\" used in an assignment
    #      context (Windows backslash path hardcoded as a string).
    #   b) Flag f-strings that embed a "/" between two filesystem-path
    #      variables.  We detect this by looking for the pattern:
    #        <path-var> = f"...{<something>}/..." or "..." + "/" + "..."
    #      where the LHS is a known path-variable name.
    # ------------------------------------------------------------------
    def test_no_raw_path_separator(self):
        """Filesystem paths must use Path objects, not string concatenation with /."""
        violations = []

        # Pattern A: backslash literal in an assignment (e.g. path = "C:\\Users\\...")
        pat_backslash = re.compile(r'''[=:]\s*["'][^"']*\\\\[^"']*["']''')

        # Pattern B: path-named variable assigned via f-string with "/" or string + "/" + string.
        # LHS must match a known path-variable naming convention.
        path_var_names = re.compile(
            r'\b(?:db_path|bak_path|json_path|'
            r'\w+_path|\w+_dir)\s*='
        )
        # RHS contains a "/" inside an f-string interpolation or string concat.
        # We look for either:
        #   f"...{var}/..."     or    something + "/" + something
        fstring_slash = re.compile(r'f"[^"]*\{[^}]+\}/[^"]*"')
        string_concat_slash = re.compile(r'["\']\s*\+\s*["\']\/["\']\s*\+\s*["\']')

        for fpath in ALL_FILES:
            text, lines = _read(fpath)
            if text is None:
                continue

            for lineno, line in enumerate(lines, 1):
                stripped = line.strip()
                # Skip comments and blank lines.
                if stripped.startswith("#") or not stripped:
                    continue

                # Pattern A — hardcoded backslash path literal.
                if pat_backslash.search(line):
                    violations.append(
                        (fpath.as_posix(), lineno, "hardcoded backslash path literal", stripped)
                    )

                # Pattern B — f-string or concat with "/" on a path-named LHS.
                if path_var_names.search(line):
                    if fstring_slash.search(line):
                        violations.append(
                            (fpath.as_posix(), lineno, "f-string '/' separator on path variable", stripped)
                        )
                    if string_concat_slash.search(line):
                        violations.append(
                            (fpath.as_posix(), lineno, "string concat '/' separator on path variable", stripped)
                        )

        _assert_no_violations(violations, "Check 1 — path separator")

    # ------------------------------------------------------------------
    # Check 2 — All file reads/writes must pass encoding="utf-8"
    #
    # .read_text(   — must contain encoding="utf-8"
    # .write_text(  — must contain encoding="utf-8"
    # open(         — must contain encoding="utf-8"  OR be binary mode
    #                 ("rb", "wb", "ab", "r+b", etc.)
    # ------------------------------------------------------------------
    def test_utf8_encoding_on_file_io(self):
        """All text-mode file I/O must specify encoding='utf-8'."""
        violations = []

        pat_read_text  = re.compile(r'\.read_text\s*\(')
        pat_write_text = re.compile(r'\.write_text\s*\(')
        pat_open       = re.compile(r'\bopen\s*\(')
        pat_binary     = re.compile(r'["\'](?:r|w|a|x)b[+]?["\']')

        for fpath in ALL_FILES:
            text, lines = _read(fpath)
            if text is None:
                continue

            for lineno, line in enumerate(lines, 1):
                stripped = line.strip()
                if stripped.startswith("#") or not stripped:
                    continue

                if pat_read_text.search(line) and 'encoding' not in line:
                    violations.append(
                        (fpath.as_posix(), lineno, ".read_text() without encoding='utf-8'", stripped)
                    )

                if pat_write_text.search(line) and 'encoding' not in line:
                    violations.append(
                        (fpath.as_posix(), lineno, ".write_text() without encoding='utf-8'", stripped)
                    )

                if pat_open.search(line):
                    # Binary-mode opens are exempt.
                    if pat_binary.search(line):
                        continue
                    if 'encoding' not in line:
                        violations.append(
                            (fpath.as_posix(), lineno, "open() without encoding='utf-8'", stripped)
                        )

        _assert_no_violations(violations, "Check 2 — utf-8 encoding")

    # ------------------------------------------------------------------
    # Check 3 — Line-ending assumptions
    #
    # .split("\n") or .split('\n') applied to content read from a file
    # object is fragile (misses "\r\n" on Windows when newline='' was
    # passed to open()).  .splitlines() is cross-platform; flag .split("\n")
    # when it appears on the same logical line as a file-read expression.
    #
    # Practical rule: flag any line that contains BOTH a file-read pattern
    # (.read(), .read_text(), f.read()) AND .split("\n") or .split('\n').
    #
    # Stand-alone .split("\n") without a file-read on the same line is
    # only flagged when a surrounding context makes it suspicious; however,
    # to keep the scan deterministic and unsurprising we only flag the
    # combined case.
    # ------------------------------------------------------------------
    def test_no_split_newline_on_file_content(self):
        """File content must use .splitlines(), not .split('\\n')."""
        violations = []

        pat_file_read = re.compile(r'\.read(?:_text)?\s*\(')
        pat_split_nl  = re.compile(r'''\.split\s*\(\s*['"]\\n['"]\s*\)''')

        for fpath in ALL_FILES:
            text, lines = _read(fpath)
            if text is None:
                continue

            for lineno, line in enumerate(lines, 1):
                stripped = line.strip()
                if stripped.startswith("#") or not stripped:
                    continue

                if pat_file_read.search(line) and pat_split_nl.search(line):
                    violations.append(
                        (fpath.as_posix(), lineno, ".split('\\n') on file content — use .splitlines()", stripped)
                    )

        _assert_no_violations(violations, "Check 3 — line-ending assumptions")

    # ------------------------------------------------------------------
    # Check 4 — chmod must be guarded by os.name != 'nt'
    #
    # Any os.chmod call (or bare chmod from a subprocess) in Python source
    # must be inside an `if os.name != 'nt':` guard.  If no chmod calls
    # exist the check passes trivially.
    # ------------------------------------------------------------------
    def test_chmod_guarded_by_os_name(self):
        """os.chmod calls must be inside an os.name != 'nt' guard."""
        violations = []

        pat_chmod = re.compile(r'\bos\.chmod\b')

        for fpath in ALL_FILES:
            text, lines = _read(fpath)
            if text is None:
                continue

            for lineno, line in enumerate(lines, 1):
                stripped = line.strip()
                if stripped.startswith("#") or not stripped:
                    continue

                if pat_chmod.search(line):
                    # Walk back up to 10 lines to find a guard.
                    context_start = max(0, lineno - 11)
                    context = lines[context_start : lineno - 1]
                    guarded = any(
                        re.search(r"os\.name\s*!=\s*['\"]nt['\"]", ctx_line)
                        or re.search(r"os\.name\s*==\s*['\"]posix['\"]", ctx_line)
                        for ctx_line in context
                    )
                    if not guarded:
                        violations.append(
                            (fpath.as_posix(), lineno, "os.chmod without os.name != 'nt' guard", stripped)
                        )

        _assert_no_violations(violations, "Check 4 — chmod guard")


# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------

def _assert_no_violations(
    violations: list[tuple],
    check_name: str,
) -> None:
    """Assert violations is empty; print a structured report on failure."""
    if not violations:
        return
    lines = [f"\n{check_name} — {len(violations)} violation(s) found:\n"]
    for file_path, lineno, description, source_line in violations:
        lines.append(f"  {file_path}:{lineno}  [{description}]")
        lines.append(f"    {source_line}")
    assert False, "\n".join(lines)
