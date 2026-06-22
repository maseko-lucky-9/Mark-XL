"""T14 — Cross-platform static scan for WO-5 files (NFR-4)."""
import re
from pathlib import Path

REPO = Path("/Users/ltmas/Repo/agents/mark-xl")

WO5_FILES = [
    REPO / "core/loop_guard.py",
    REPO / "tests/test_wo5_loop_guard.py",
    REPO / "tests/test_wo5_site1_main.py",
    REPO / "tests/test_wo5_site2_executor.py",
    REPO / "tests/test_wo5_site3_worker.py",
    REPO / "tests/test_wo5_reset.py",
    REPO / "tests/test_wo5_reset_background.py",
    REPO / "tests/test_wo5_signal_ui.py",
    REPO / "tests/test_wo5_config_knobs.py",
    REPO / "tests/test_wo5_flag_off_regression.py",
]


class TestXPlatScan:
    def test_no_raw_path_str(self):
        """No str(Path(...)) patterns — these render as backslashes on Windows."""
        violations = []
        for f in WO5_FILES:
            if not f.exists():
                continue
            text = f.read_text(encoding="utf-8")
            for lineno, line in enumerate(text.splitlines(), 1):
                if "str(Path(" in line:
                    violations.append(f"{f.as_posix()}:{lineno}: {line.strip()}")
        assert violations == [], "Raw str(Path() found:\n" + "\n".join(violations)

    def test_no_bare_open_without_encoding(self):
        """All open() calls in WO-5 files must have encoding='utf-8'."""
        violations = []
        for f in WO5_FILES:
            if not f.exists():
                continue
            text = f.read_text(encoding="utf-8")
            for lineno, line in enumerate(text.splitlines(), 1):
                if re.search(r'\bopen\s*\(', line) and 'encoding' not in line:
                    violations.append(f"{f.as_posix()}:{lineno}: {line.strip()}")
        assert violations == [], "Bare open() without encoding:\n" + "\n".join(violations)

    def test_loop_guard_no_get_flag(self):
        """core/loop_guard.py must have zero get_flag literals."""
        text = (REPO / "core/loop_guard.py").read_text(encoding="utf-8")
        assert "get_flag" not in text, "get_flag found in core/loop_guard.py"

    def test_task_queue_no_loop_guard_import(self):
        """agent/task_queue.py must have zero loop_guard and get_flag references."""
        text = (REPO / "agent/task_queue.py").read_text(encoding="utf-8")
        assert "loop_guard" not in text, "loop_guard found in task_queue.py"
        assert "get_flag" not in text, "get_flag found in task_queue.py"
