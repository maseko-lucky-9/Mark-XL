# tests/test_wo2_goldens.py
# T1: golden snapshots for byte-equality regression in T5b/T8/T9/T11
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from main import TOOL_DECLARATIONS, _to_ollama_tools
from agent.planner import PLANNER_PROMPT

# ---------------------------------------------------------------------------
# Module-level goldens (importable by T5b, T8, T9, T11)
# ---------------------------------------------------------------------------

GOLDEN_OLLAMA = _to_ollama_tools(TOOL_DECLARATIONS)   # baseline OLLAMA_TOOLS
GOLDEN_PLANNER = PLANNER_PROMPT                        # baseline planner prompt


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_middle_three_order():
    """Verify TOOL_DECLARATIONS middle-three: positions [6][7][8] ==
    screen_process / computer_settings / browser_control."""
    names = [t["name"] for t in TOOL_DECLARATIONS]
    assert names[6] == "screen_process",     f"Expected screen_process at [6], got {names[6]}"
    assert names[7] == "computer_settings",  f"Expected computer_settings at [7], got {names[7]}"
    assert names[8] == "browser_control",    f"Expected browser_control at [8], got {names[8]}"


def test_golden_ollama_middle_three():
    """GOLDEN_OLLAMA preserves middle-three order at positions [6][7][8]."""
    names = [t["function"]["name"] for t in GOLDEN_OLLAMA]
    assert names[6] == "screen_process",     f"GOLDEN_OLLAMA[6] should be screen_process, got {names[6]}"
    assert names[7] == "computer_settings",  f"GOLDEN_OLLAMA[7] should be computer_settings, got {names[7]}"
    assert names[8] == "browser_control",    f"GOLDEN_OLLAMA[8] should be browser_control, got {names[8]}"


def test_golden_ollama_is_nonempty():
    """GOLDEN_OLLAMA has 20 entries."""
    assert len(GOLDEN_OLLAMA) == 20, f"Expected 20 entries, got {len(GOLDEN_OLLAMA)}"


def test_golden_ollama_structure():
    """Every entry in GOLDEN_OLLAMA has the Ollama/OpenAI tool structure."""
    for i, tool in enumerate(GOLDEN_OLLAMA):
        assert tool.get("type") == "function", f"Entry [{i}] missing type='function'"
        fn = tool.get("function", {})
        assert "name" in fn,        f"Entry [{i}] function missing 'name'"
        assert "description" in fn, f"Entry [{i}] function missing 'description'"
        assert "parameters" in fn,  f"Entry [{i}] function missing 'parameters'"


def test_golden_planner_is_nonempty():
    """GOLDEN_PLANNER is a non-empty string."""
    assert isinstance(GOLDEN_PLANNER, str)
    assert len(GOLDEN_PLANNER) > 0
