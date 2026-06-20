# Phase 3 Pre-Flight Gate Report — WO-0 (Foundations)

Generated: 2026-06-20
Branch: wo-0-foundations
Gate executor: security-auditor (claude-sonnet-4-6)

---

## Machine-Readable Contract

```
secrets_status: clean
destructive_ops: none
risk_tier: medium
scope_status: within
banned_stack: pass
```

---

## Gate 1: Secrets Scan

**Tool used:** gitleaks (installed at /opt/homebrew/bin/gitleaks)

**Command:**
```
gitleaks detect --source /Users/ltmas/Repo/agents/mark-xl --no-banner -v
```

**Result:**
```
16 commits scanned.
scanned ~894377 bytes (894.38 KB) in 67.4ms
no leaks found
```

**Supplementary manual grep coverage:**

The gitleaks scan covers standard secret patterns including: OpenAI API keys, AWS access key IDs,
GitHub personal access tokens, Google API keys, Slack tokens, PEM private keys, and hardcoded
literals for password/api_key/token/secret assignments with substantive values.

1. All JSON files in tree: only `pipeline/state.json` exists — contains pipeline run metadata
   (run_id, phase status, acceptance criteria). No keys, tokens, or credentials. Inspected directly.

2. `config/` directory: only `config/__init__.py` exists. Reads `api_keys.json` at runtime via
   `open(_CONFIG_PATH)`. `config/api_keys.json` does NOT currently exist in the tree — confirmed
   by `find config/ -type f` returning only `config/__init__.py`. No secrets planted.

3. `memory/config_manager.py`: Reads `config/api_keys.json` at runtime via `CONFIG_FILE.read_text()`.
   No hardcoded literals, no embedded keys.

4. `requirements.txt`: Package list only. No credentials.

5. No `.env` files found anywhere in the tree.

**VERDICT: secrets_status: clean**

No real secret exists anywhere in the working tree. gitleaks 16-commit history scan returned
"no leaks found". The design explicitly uses PLACEHOLDERS only for the planned `config/api_keys.json`
(which does not yet exist on-branch).

---

## Gate 2: Banned-Stack Check

**Scope of scan:** WO-0 change surface only:
- actions/*.py (17 files)
- main.py
- agent/executor.py
- agent/planner.py
- memory/config_manager.py
- requirements.txt

**Grep pattern checked:** torch, transformers, DSPy, dspy, GEPA, openjarvis, OpenJarvis,
ToolExecutor, EventBus

**Findings:**

`torch` and `transformers` appear in `main.py` at:
- main.py:11 — comment block re: USE_TF=0 and transformers lazy-loader (documentation only)
- main.py:1256-1266 — `import torch` inside a local STT/TTS GPU-detection block
- main.py:1349-1357 — `_preload_torch()` background thread for TTS warm-up

**Assessment:** These are ALL pre-existing upstream STT/TTS stack references present on
`origin/main` before WO-0 branched. They are not introduced by WO-0. Per gate instructions:
"torch/transformers ALREADY EXIST in the upstream STT/TTS stack (main.py, core/stt.py, core/tts.py,
core/installer.py) — those are PRE-EXISTING, not introduced by WO-0, and are out of scope to purge."

**WO-0 change surface files (agent/, actions/, memory/config_manager.py):**
ZERO torch / transformers / DSPy / GEPA / openjarvis / ToolExecutor / EventBus imports found.

**requirements.txt:** No torch, transformers, DSPy, GEPA, or openjarvis listed.

**Prose-only references:** `pipeline/spec.md:106` and `pipeline/constitution.md` mention
ToolExecutor/EventBus in the ban statement — these are documentation prose, not code imports.

**VERDICT: banned_stack: pass**

No new banned dependency is introduced by the WO-0 change surface.

---

## Gate 3: Destructive Operations

**Enumeration of all destructive operations introduced by WO-0 design:**

NONE.

**Analysis of each change surface:**

- `actions/*.py` — signature normalization only (parameter list changes). No file deletes,
  no DB ops, no data mutation.
- `main.py` — one caller update at main.py:896 to forward `speak` to `flight_finder`.
  Not destructive.
- `agent/executor.py` — two changes: (a) add `file_processor` branch, (b) replace silent
  `else` fallback with `raise`. The `raise` is EC-5 (a bug correction). A raise on
  unknown-tool-name is not a destructive operation — it is an error-surfacing correction
  that previously silently called `_run_generated_code`.
- `agent/planner.py` — add `file_processor` to `PLANNER_PROMPT` text. String change only.
- `memory/config_manager.py` — add `_FLAG_SCHEMA` dict and `get_flag()` accessor. No writes,
  no deletes, no mutations of existing data paths.
- `config/flags.json` (NEW) — a new inert config file with all flags defaulting to False.
  Not destructive; additive only.
- `conftest.py` + `tests/` (NEW) — test fixtures and test files. No production data touched.
- `.github/workflows/*.yml` (NEW) — CI pipeline definition. No destructive git operations.
- `docs/decisions/001-*.md` (NEW) — ADR document. Additive only.

**The single intended behaviour change (EC-5 / B3):** `executor._call_tool` will raise
`ValueError` or `KeyError` on an unknown tool name instead of silently calling
`_run_generated_code`. This is explicitly classified in spec.md:134 as a bug *correction*,
not a flagged feature. It is not a destructive operation.

**No file deletes, DB drops, force-push, history rewrite, or irreversible data mutations
are present in the WO-0 design.**

**VERDICT: destructive_ops: none**

---

## Gate 4: Scope Check

**Reference:** `/Users/ltmas/Repo/agents/mark-xl/pipeline/spec.md §7 Out of Scope`
(design.md was not yet authored at time of this gate execution — per instructions, base on
spec.md §7 + this prompt when design.md is absent.)

**WO-0 defined scope (from spec.md):**
1. 4 bug fixes (B1-B4): FR-1.1 to FR-1.4
2. 17-signature normalization: FR-2.1 to FR-2.3
3. Flag rail (inert, gates nothing): FR-3.1 to FR-3.2
4. pytest harness from zero: FR-4.1 to FR-4.4
5. CI matrix with non-blocking Rust placeholder: FR-5.1 to FR-5.3

**Explicit out-of-scope items per spec.md §7:**
- Real Rust wheel build (WO-1)
- Registry refactor / auto-generation of tool list (WO-2)
- Security gates (WO-3)
- Memory work (WO-4)
- Loopguard (WO-5)
- Gating any WO-0 change behind a flag

**Scope verification against change surface:**

| Change | In-scope? | Rationale |
|--------|-----------|-----------|
| actions/*.py signature normalization | YES | FR-2 |
| main.py B1 caller fix (flight_finder speak) | YES | FR-1.1 |
| executor.py B2 (add file_processor branch) | YES | FR-1.2 |
| executor.py B3 (raise on unknown tool) | YES | FR-1.3 |
| planner.py B4 (add file_processor to prompt) | YES | FR-1.4 |
| memory/config_manager.py flag schema + get_flag() | YES | FR-3.1 |
| config/flags.json (all OFF) | YES | FR-3.2 |
| conftest.py + tests/ | YES | FR-4 |
| .github/workflows/*.yml | YES | FR-5 |
| docs/decisions/001-*.md (ADR) | YES | NFR (non-trivial decision record) |
| Registry auto-generation | NOT PRESENT — correctly deferred to WO-2 |
| Security gate wiring | NOT PRESENT — correctly deferred to WO-3 |
| Memory subsystem changes | NOT PRESENT — correctly deferred to WO-4 |
| Loopguard | NOT PRESENT — correctly deferred to WO-5 |
| Flag-gated bug fix | NOT PRESENT — spec.md FR-3.2 explicitly forbids it |

The planner-prompt correction in planner.py (B4) adds `file_processor` by hand. This is
explicitly noted in spec.md FR-1.4: "WO-2 will auto-generate this list; WO-0 only corrects
it by hand." This is in-scope.

**VERDICT: scope_status: within**

---

## Risk Tier

**Assigned tier: medium**

**Justification:**

WO-0 touches 17 action files + 2 dispatcher files (main._execute_tool + executor._call_tool).
This is a broad surface area (19-20 files). However, the risk profile is materially mitigated:

1. **`**kwargs` absorption (EC-4):** Dead parameters (`session_memory`, `response`) on any
   un-updated caller will be silently absorbed by `**kwargs` rather than raising a TypeError.
   This is the primary breakage vector and it is mechanically closed.

2. **Pure-Python, no live side effects in WO-0:** No network calls, no file writes to
   production paths, no DB connections are introduced. The dispatch routing is tested via
   mock-patching, not live execution.

3. **TAS-1 dual-path routing test:** The parametrized test covers all 17 tools through
   both dispatch paths with mock `player` and `speak`, giving a deterministic routing
   regression signal before any downstream WO extends behaviour.

4. **Single intentional behaviour change (B3/EC-5):** The `raise` on unknown tool is
   well-bounded — it only fires on a tool name that the planner should never emit (and the
   planner now has the correct tool list). The regression test TAS-4 asserts this explicitly.

5. **Inert flag rail:** `config/flags.json` with all defaults OFF introduces zero observable
   behaviour change. `get_flag()` returns False for every key by default. Nothing in WO-0
   reads a flag at runtime.

**Why not low:** 17 files touched; a missed caller or a missed `**kwargs` gap could silently
break dispatch on an action that lacks a test. Broad surface warrants medium.

**Why not high/critical:** No secrets, no live network/auth surface, no data mutations,
pure-Python, strong mechanical mitigations, dual-path routing test closes the primary
regression vector.

**Mitigations on record:**
- kwargs absorption: spec.md §4 EC-4
- TAS-1 dual-path routing test: spec.md §5 TAS-1

Design.md (currently being authored) must confirm both mitigations are carried forward.

---

## Pre-Flight Gate Summary

| Gate | Verdict | Notes |
|------|---------|-------|
| Gate 1: Secrets | PASS (clean) | gitleaks 16-commit scan clean; no api_keys.json on-branch |
| Gate 2: Banned Stack | PASS | torch/transformers are pre-existing upstream, not WO-0 introductions |
| Gate 3: Destructive Ops | PASS (none) | B3 raise is a bug correction, not a destructive op |
| Gate 4: Scope | PASS (within) | All 5 WO-0 deliverables confirmed; no out-of-scope item present |
| Risk Tier | medium | Broad signature surface + strong kwargs+TAS-1 mitigations |

**Phase 3 (Design) Pre-Flight: CLEARED for development handoff.**

No hard-fail condition exists. secrets_status is clean; banned_stack passes; no destructive
ops; scope is within bounds; risk tier is medium with mitigations on record.

---

## Positive Observations

- No secrets exist in the tree. The design correctly defers `config/api_keys.json` as
  runtime-written (not source-controlled); only `config/__init__.py` is committed.
- The `**kwargs` absorption strategy is the right mechanical fix for the dead-parameter
  problem — it prevents caller breakage during a rolling normalization pass without
  requiring a single-commit atomic rewrite of all callers.
- spec.md §6 provides a canonical, numbered tool enumeration that eliminates count drift
  (the "18 vs 17" miscount was already caught and recorded in state.json:spec_corrections).
- B3 (raise on unknown tool) is the correct safety posture — silent fallback to LLM-generated
  code execution on an unknown tool name is a security-adjacent footgun, and the correction
  is unconditional.
- The non-blocking Rust placeholder pattern (FR-5.2/FR-5.3) correctly separates CI greenness
  from wheel availability, avoiding WO-0 being blocked by WO-1 work.
- The three main-only inline tools (save_memory, agent_task, shutdown_jarvis) are correctly
  excluded from the dual-path parametrization and tested individually, with shutdown_jarvis
  mocked to prevent os._exit(0) killing the test runner.
