"""T8: OLLAMA_TOOLS flag-gate tests."""
import pytest


def test_flag_off_ollama_tools_baseline():
    """flag-OFF is the default — verify current OLLAMA_TOOLS matches golden."""
    import json
    from main import OLLAMA_TOOLS
    from tests.test_wo2_goldens import GOLDEN_OLLAMA
    assert json.dumps(OLLAMA_TOOLS, sort_keys=False) == json.dumps(GOLDEN_OLLAMA, sort_keys=False)


def test_flag_on_path_build_ollama_tools_equals_golden():
    """flag-ON path: build_ollama_tools() after import_all_tools() == GOLDEN_OLLAMA."""
    import json
    from core.tool_registry import import_all_tools, build_ollama_tools
    from tests.test_wo2_goldens import GOLDEN_OLLAMA
    import_all_tools()
    result = build_ollama_tools()
    assert json.dumps(result, sort_keys=False) == json.dumps(GOLDEN_OLLAMA, sort_keys=False)
