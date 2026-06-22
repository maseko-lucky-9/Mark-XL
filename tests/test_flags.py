"""
test_flags.py — TAS-7: Feature flag default-OFF verification (WO-0).

Tests the flag rail in memory/config_manager.py (get_flag function added in T01):
- All 5 flags return False by default
- get_flag("nonexistent_flag") returns False
- load_api_keys() and is_configured() are callable without error
- No WO-0 production file calls get_flag to gate any behaviour
"""
import json
import sys
from pathlib import Path, PureWindowsPath

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memory.config_manager import get_flag, load_api_keys, is_configured

ALL_FLAGS = [
    "use_rust_wheel",
    "use_tool_registry",
    "enable_security_gates",
    "enable_memory_v2",
    "enable_loopguard",
]


class TestFlagDefaults:
    """Feature flag default-OFF behaviour tests."""

    def test_flag_defaults_all_off_when_file_absent(self, tmp_path, monkeypatch):
        """All 5 flags return False when flags.json is absent."""
        import memory.config_manager as cm
        # Point _FLAGS_FILE to a non-existent path
        monkeypatch.setattr(cm, "_FLAGS_FILE", tmp_path / "nonexistent_flags.json")
        for flag in ALL_FLAGS:
            assert get_flag(flag) is False, f"Expected {flag} to be False when file absent"

    def test_flag_defaults_all_off_when_file_present_with_false(self, tmp_path, monkeypatch):
        """All 5 flags return False when flags.json is present with false values."""
        import memory.config_manager as cm
        flags_file = tmp_path / "flags.json"
        flags_data = {flag: False for flag in ALL_FLAGS}
        flags_file.write_text(json.dumps(flags_data), encoding="utf-8")
        monkeypatch.setattr(cm, "_FLAGS_FILE", flags_file)
        for flag in ALL_FLAGS:
            assert get_flag(flag) is False, f"Expected {flag} to be False when set to false in JSON"

    def test_nonexistent_flag_returns_false(self):
        """get_flag with an unknown flag name returns False (the default)."""
        assert get_flag("nonexistent_flag") is False
        assert get_flag("__nonexistent__") is False
        assert get_flag("completely_unknown_flag_xyz") is False

    def test_all_flags_in_schema_return_false(self):
        """Each of the 5 defined flags returns False by default."""
        for flag in ALL_FLAGS:
            assert get_flag(flag) is False, f"Expected {flag} to default to False"


class TestConfigFunctions:
    """Config loading functions must be callable without error."""

    def test_load_api_keys_callable(self):
        """load_api_keys() is callable and returns a dict without error."""
        result = load_api_keys()
        assert isinstance(result, dict), "load_api_keys() must return a dict"

    def test_is_configured_callable(self):
        """is_configured() is callable and returns a bool without error."""
        result = is_configured()
        assert isinstance(result, bool), "is_configured() must return a bool"


class TestStaticAssertions:
    """Static assertions about production code structure."""

    def test_no_production_code_calls_get_flag(self):
        """Static assertion: no WO-0 production file gates behaviour behind get_flag.

        This ensures flags are not yet in use; they are infrastructure for future
        feature-gating work (TAS-8 onwards).
        """
        import ast
        repo_root = Path(__file__).resolve().parent.parent

        # Production file predicate — separator-agnostic via Path.parts membership.
        # Path.parts splits on the OS-native separator on real paths, and
        # PureWindowsPath.parts splits on backslashes even when running on POSIX,
        # so this logic is correct on both Windows and POSIX without string-substring hacks.
        # Exclusion rules:
        #   - "tests" in parts          → test directories (works on Windows: parts=(…, 'tests', …))
        #   - "__pycache__" in parts    → compiled-bytecode directories
        #   - part.startswith(".venv")  → .venv, .venv-mac, .venv-win, etc.
        #   - part == "venv"            → bare venv directory
        #   - "site-packages" in part   → installed third-party packages
        #   - f.name == "config_manager.py" → the defining module is exempt
        def _is_production(f) -> bool:
            return (
                not any(
                    p in {"tests", "__pycache__"}
                    or p.startswith(".venv")
                    or p == "venv"
                    or "site-packages" in p
                    for p in f.parts
                )
                and f.name != "config_manager.py"
            )

        # Prove the filter is separator-agnostic: PureWindowsPath splits on '\' even on
        # POSIX, so this assertion validates Windows behaviour without needing a Windows box.
        assert not _is_production(
            PureWindowsPath(r"C:\repo\tests\test_flags.py")
        ), "Filter must exclude Windows-style test paths (parts-based check is separator-agnostic)"
        # Also confirm production paths are NOT over-excluded.
        assert _is_production(
            PureWindowsPath(r"C:\repo\agent\executor.py")
        ), "Filter must NOT exclude legitimate production files"

        production_files = [f for f in repo_root.rglob("*.py") if _is_production(f)]

        callers = []
        for py_file in production_files:
            try:
                src = py_file.read_text(encoding="utf-8")
                # Check for get_flag calls (simple string search sufficient for this gate)
                if "get_flag" in src:
                    callers.append(str(py_file.relative_to(repo_root)))
            except Exception:
                # Skip files that can't be read (e.g., binary)
                pass

        assert callers == [], (
            f"WO-0 production code must NOT call get_flag to gate behaviour. "
            f"Callers found: {callers}"
        )
