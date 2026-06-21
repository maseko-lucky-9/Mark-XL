# 001 — Thin pure-Python tool registry with a single flag-gated dispatch chokepoint

- **Status:** Accepted
- **Date:** 2026-06-21
- **Work order:** WO-2 (Extensibility)
- **Deciders:** Design-phase lead (Phase 3), `planner`, `security-auditor`

## Context and forces

Adding a Mark-XL tool today requires 4 coordinated edits: `TOOL_DECLARATIONS` (`main.py`), two
`if/elif` dispatch ladders (`main._execute_tool`, `agent/executor._call_tool`), and the hardcoded
tool list inside the `PLANNER_PROMPT` string (`agent/planner.py`). The planner list drifts from the
two dispatch paths — the structural root-cause defect.

Forces that constrain the solution:

1. **Three serialization orders DIVERGE.** The planner block (planner.py L34-124, 17 tools), the
   `TOOL_DECLARATIONS` order (main.py, 20 entries, drives `OLLAMA_TOOLS`), and main.py's import
   order are all different. They split at position 3 (`game_updater` vs `weather_report`), and the
   3 inline tools are INTERLEAVED in `TOOL_DECLARATIONS` (positions 14/18/20), not appended. A
   single registry iteration order cannot reproduce both byte targets.
2. **The planner block is hand-authored prose**, not derivable from the JSON schema (e.g. "write a
   clear, focused search query"). It cannot be regenerated from `parameters`.
3. **Flag-OFF must be byte-for-byte baseline** (AC6) — the constitution and the WO-2 spec forbid any
   observable change when `use_tool_registry` is OFF.
4. **`file_controller` has a parameter literally named `"name"`** — tool identity and parameter
   names share a key namespace risk.
5. **Constitution §1.1 forbids adopting OpenJarvis** `ToolExecutor`/`EventBus`/`BaseTool`.

## Decision

Introduce a NEW pure-Python `core/tool_registry.py` (ZERO OpenJarvis imports) providing:

- a `ToolSpec` dataclass (`name`, `func`, `description`, `parameters` [UPPERCASE Gemini-style],
  `planner_block` [verbatim prose], `is_planner_visible`, `is_silent`, `requires_confirm` [reserved],
  `inline`);
- a `register()` decorator that RAISES on duplicate name (fail-fast);
- a single `dispatch(name, params, player, speak)` chokepoint that forwards `player`/`speak` by
  keyword, returns the RAW result, and on an unknown name returns an explicit error string and NEVER
  execs;
- `to_openai_function(spec)` that REUSES the existing `_convert_props`/`_TYPE_MAP` conversion so its
  output is byte-identical to the baseline `_to_ollama_tools` element by construction;
- `import_all_tools()` — eager EXPLICIT import of the 17 action modules (mirroring main.py's
  top-level imports; `actions/` is not a package) + registration of the 3 inline tools;
- TWO frozen order manifests — `OLLAMA_TOOLS_ORDER` (20 names, `TOOL_DECLARATIONS` order) and
  `PLANNER_TOOLS_ORDER` (17 names, planner order) — driving the two generators, since one iteration
  order cannot satisfy both byte targets.

Adding a tool becomes **1 decorator** (+ one line in each manifest for a NEW tool). Every migrated
site (`main._execute_tool`, `OLLAMA_TOOLS`, `executor._call_tool`, planner-gen) is gated by
`use_tool_registry` (default OFF): `if get_flag('use_tool_registry'): <registry> else: <verbatim
baseline>`. The baseline `_to_ollama_tools` / `TOOL_DECLARATIONS` / dispatch ladders are PRESERVED
verbatim in the else branch — NOT deleted — so flag-OFF is byte-for-byte.

The schema source is the existing UPPERCASE Gemini-style `TOOL_DECLARATIONS`, carried verbatim in
`ToolSpec.parameters` and converted on emit, so byte-equality is provable by construction. Tool
identity always comes from `@register("…")`, never from recursing into `properties` — neutralizing
the `file_controller` `"name"` collision. The 3 inline tools (`save_memory`, `agent_task`,
`shutdown_jarvis`) stay encapsulated main-only handlers (`inline=True`; `dispatch` refuses them),
preserving `save_memory`'s pre-try `__SILENT__` early-return and set_state ordering.

## Consequences

**Positive**
- Adding a tool = 1 decorator (AC1).
- The planner can never advertise a tool the dispatcher can't run (US-2 — root-cause eliminated).
- Flag-OFF == baseline byte-for-byte (AC6); flag-ON is independently testable.
- `generated_code` executor branch left intact (WO-3 carry-forward), distinct from the unknown path.
- `requires_confirm` reserved for WO-3 confirm-gate logic without re-plumbing.

**Negative / trade-offs**
- Two frozen order manifests must be kept in sync when a tool is added — mitigated by the
  byte-equality golden-fixture tests that fail loudly if a manifest is stale.
- The hand-authored planner prose lives in `ToolSpec.planner_block` (duplication of human text)
  rather than being generated — accepted because the prose is not recoverable from schema.

## Alternatives rejected

- **Adopt OpenJarvis `ToolExecutor`/`EventBus`/`BaseTool`** — forbidden by constitution §1.1.
- **Single registry insertion order** — cannot satisfy both the 20-tool `OLLAMA_TOOLS` and 17-tool
  planner byte targets (the orders diverge).
- **Derive the planner block from schema** — the prose annotations are not recoverable.
- **Delete `_to_ollama_tools`** (per the stale plan) — breaks flag-OFF byte-for-byte (AC6).
