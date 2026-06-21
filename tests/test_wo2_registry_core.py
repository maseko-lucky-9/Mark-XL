"""
tests/test_wo2_registry_core.py — WO-2 registry-core byte-equality gate (T5b).

Three flag-independent unit checks that prove the registry core is correct
BEFORE the T6-T9 migration tasks swap call sites in main.py.

Boundary constants (module-level — T9 inherits these):
    PREAMBLE_END_MARKER  — marks end of PLANNER_PROMPT preamble; tool region starts after
    FOOTER_START_MARKER  — marks start of PLANNER_PROMPT footer; tool region ends before

NOTE on FOOTER_START_MARKER:
    The canonical value is "\n\nOUTPUT — return ONLY valid JSON" (two leading newlines).
    In agent/planner.py the last tool block is followed by a blank line before "OUTPUT",
    so the raw text is "...result\n\nOUTPUT". Using "\nOUTPUT" (one newline) would
    include a trailing "\n" in the extracted region and fail byte equality.
    The two-newline form is also the correct T9 round-trip anchor:
        reconstructed = prompt[:tool_start] + build_planner_tool_block() + prompt[footer_idx:]
        assert reconstructed == PLANNER_PROMPT  # exact
"""

# ---------------------------------------------------------------------------
# Canonical boundary markers — reused by T9 for planner prompt reconstruction
# ---------------------------------------------------------------------------
PREAMBLE_END_MARKER = "AVAILABLE TOOLS AND THEIR PARAMETERS:\n\n"
FOOTER_START_MARKER = "\n\nOUTPUT — return ONLY valid JSON"


# ---------------------------------------------------------------------------
# Test 1 — IDEMPOTENCY
# ---------------------------------------------------------------------------

def test_import_all_tools_idempotent():
    """Call import_all_tools() TWICE — no ValueError, len(_REGISTRY)==20 both times."""
    from core.tool_registry import import_all_tools, _REGISTRY
    import_all_tools()
    import_all_tools()  # must NOT raise
    assert len(_REGISTRY) == 20


# ---------------------------------------------------------------------------
# Test 2 — M4-as-unit (OLLAMA byte equality)
# ---------------------------------------------------------------------------

def test_build_ollama_tools_equals_baseline():
    """build_ollama_tools() == _to_ollama_tools(TOOL_DECLARATIONS) — full 20-tool byte equality.

    Serialized via json.dumps(sort_keys=False) to catch key-order drift.
    """
    import json
    from core.tool_registry import import_all_tools, build_ollama_tools
    from main import TOOL_DECLARATIONS, _to_ollama_tools
    import_all_tools()
    registry_result = build_ollama_tools()
    baseline_result = _to_ollama_tools(TOOL_DECLARATIONS)
    assert json.dumps(registry_result, sort_keys=False) == json.dumps(baseline_result, sort_keys=False), \
        "build_ollama_tools() must be byte-identical to _to_ollama_tools(TOOL_DECLARATIONS)"


# ---------------------------------------------------------------------------
# Test 3 — M2-as-unit (PLANNER byte equality)
# ---------------------------------------------------------------------------

def test_build_planner_tool_block_equals_baseline():
    """build_planner_tool_block() == baseline PLANNER_PROMPT tool region.
    Proves 17 planner_block captures + PLANNER_TOOLS_ORDER are byte-correct."""
    from core.tool_registry import import_all_tools, build_planner_tool_block
    from agent.planner import PLANNER_PROMPT
    import_all_tools()

    # Extract tool region from baseline PLANNER_PROMPT
    preamble_idx = PLANNER_PROMPT.index(PREAMBLE_END_MARKER)
    tool_start = preamble_idx + len(PREAMBLE_END_MARKER)
    footer_idx = PLANNER_PROMPT.index(FOOTER_START_MARKER, tool_start)
    baseline_tool_region = PLANNER_PROMPT[tool_start:footer_idx]

    registry_tool_block = build_planner_tool_block()
    assert registry_tool_block == baseline_tool_region, (
        f"build_planner_tool_block() must equal PLANNER_PROMPT tool region.\n"
        f"Registry len={len(registry_tool_block)}, baseline len={len(baseline_tool_region)}"
    )
