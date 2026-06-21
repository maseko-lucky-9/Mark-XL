"""
tests/test_wo2_t9_planner.py — WO-2 T9: planner split + consumer-gate tests.

Verifies:
1. PLANNER_PROMPT is unchanged (byte equality vs GOLDEN_PLANNER).
2. build_planner_prompt(build_planner_tool_block()) == PLANNER_PROMPT (round-trip).
3. agent/planner.py contains ZERO get_flag references.
4. create_plan() uses a custom system= arg when provided (not PLANNER_PROMPT).
5. flag-ON: build_planner_prompt(build_planner_tool_block()) == GOLDEN_PLANNER.
"""
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ---------------------------------------------------------------------------
# 1. PLANNER_PROMPT unchanged
# ---------------------------------------------------------------------------

def test_planner_prompt_unchanged():
    """PLANNER_PROMPT must still equal GOLDEN_PLANNER from test_wo2_goldens."""
    from agent.planner import PLANNER_PROMPT
    from tests.test_wo2_goldens import GOLDEN_PLANNER
    assert PLANNER_PROMPT == GOLDEN_PLANNER, (
        "PLANNER_PROMPT has drifted from GOLDEN_PLANNER — T9 must not modify the constant."
    )


# ---------------------------------------------------------------------------
# 2. Round-trip: build_planner_prompt(build_planner_tool_block()) == PLANNER_PROMPT
# ---------------------------------------------------------------------------

def test_build_planner_prompt_roundtrip():
    """build_planner_prompt(build_planner_tool_block()) must equal PLANNER_PROMPT exactly."""
    from core.tool_registry import import_all_tools, build_planner_tool_block
    from agent.planner import PLANNER_PROMPT, build_planner_prompt

    import_all_tools()
    result = build_planner_prompt(build_planner_tool_block())
    assert result == PLANNER_PROMPT, (
        f"Round-trip failed.\n"
        f"Result length: {len(result)}, PLANNER_PROMPT length: {len(PLANNER_PROMPT)}"
    )


# ---------------------------------------------------------------------------
# 3. Zero get_flag references in agent/planner.py
# ---------------------------------------------------------------------------

def test_planner_no_get_flag():
    """agent/planner.py must contain ZERO occurrences of 'get_flag'."""
    planner_path = Path(__file__).resolve().parent.parent / "agent" / "planner.py"
    content = planner_path.read_text(encoding="utf-8")
    count = content.count("get_flag")
    assert count == 0, (
        f"agent/planner.py contains {count} occurrence(s) of 'get_flag' — must be 0."
    )


# ---------------------------------------------------------------------------
# 4. create_plan uses custom system= when provided
# ---------------------------------------------------------------------------

def test_create_plan_uses_custom_system():
    """create_plan('search for X', system='custom_system') must pass 'custom_system'
    to call_llm_text, not PLANNER_PROMPT."""
    captured = {}

    def _fake_llm(user_input, system=None):
        captured["system"] = system
        # Return minimal valid JSON plan so create_plan doesn't fall back
        import json
        return json.dumps({
            "goal": "search for X",
            "steps": [
                {
                    "step": 1,
                    "tool": "web_search",
                    "description": "search",
                    "parameters": {"query": "X"},
                    "critical": True,
                }
            ],
        })

    with patch("agent.planner.call_llm_text", side_effect=_fake_llm):
        from agent.planner import create_plan
        create_plan("search for X", system="custom_system")

    assert captured.get("system") == "custom_system", (
        f"Expected system='custom_system', got system={captured.get('system')!r}"
    )


# ---------------------------------------------------------------------------
# 5. flag-ON: build_planner_prompt(build_planner_tool_block()) == GOLDEN_PLANNER
# ---------------------------------------------------------------------------

def test_flag_on_planner_prompt_matches_golden():
    """When flag use_tool_registry is ON, build_planner_prompt(build_planner_tool_block())
    must equal GOLDEN_PLANNER (string equality, no json.dumps needed)."""
    from core.tool_registry import import_all_tools, build_planner_tool_block
    from agent.planner import build_planner_prompt
    from tests.test_wo2_goldens import GOLDEN_PLANNER

    import_all_tools()
    result = build_planner_prompt(build_planner_tool_block())
    assert result == GOLDEN_PLANNER, (
        f"flag-ON prompt does not match GOLDEN_PLANNER.\n"
        f"Result length: {len(result)}, golden length: {len(GOLDEN_PLANNER)}"
    )
