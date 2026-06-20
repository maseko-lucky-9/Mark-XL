# Tasks — WO-0 (Foundations)

> Spec Kit `tasks` artifact. Phase 4 decomposition for `wo-0-foundations`.
> 17 tasks, ordered by dependency; no forward-dependency violations.
> Every task maps to AC(s) and is bounded to ≤5 files.
> dual_client: false — single lane (no backend/frontend split).

---

## Dependency Graph (summary)

```
T01 (flags)
  └─> T16 (flag test)

T02, T03, T04, T05 (sig-norm — independent batches, parallel-eligible)
  └─> T06 (executor.py — all executor changes)
        └─> T07 (main.py callers)
              └─> T08 (planner.py)
                    └─> T09 (conftest + fixtures)
                          ├─> T10 (dual-path test)
                          ├─> T11 (inline tool tests)
                          ├─> T12 (B1 regression)
                          ├─> T13 (B2 regression)
                          ├─> T14 (B3 regression)
                          └─> T15 (B4 regression)
                    └─> T17 (CI workflow — can be authored in parallel with T10-T15)

T01 is independent of the sig-norm chain.
T16 depends on T01 only; independent of T09.
```

---

## Phase: Config / Flag Rail

### T01 — Feature-flag rail: schema + accessor
- **AC(s):** AC4
- **Files touched (2):** `memory/config_manager.py`, `config/flags.json`
- **Change:**
  1. In `memory/config_manager.py` add `_FLAG_SCHEMA` dict with all 5 flags defaulting to `False`:
     `use_rust_wheel`, `use_tool_registry`, `enable_security_gates`, `enable_memory_v2`, `enable_loopguard`.
  2. Add `get_flag(name, default=False) -> bool` that reads `config/flags.json`; returns
     `_FLAG_SCHEMA.get(name, default)` if the file or key is absent.
  3. Create `config/flags.json` with all 5 flags set to `false`.
  4. Do NOT modify `load_api_keys()` or `is_configured()` — those functions must be byte-for-byte unaffected.
  5. WO-0 wires NO gate — nothing reads `get_flag` to alter behaviour.
- **Acceptance criterion:** `get_flag("use_rust_wheel")` returns `False` when `config/flags.json`
  is absent AND when it is present with `false`. `load_api_keys()`/`is_configured()` call succeeds
  unchanged. No existing function signatures altered.
- **Tests:** TAS-7 (written in T16). This task: manual smoke —
  `python3 -c "from memory.config_manager import get_flag; assert get_flag('use_rust_wheel') == False"`.
- **Parallel-eligible:** YES (no deps on sig-norm tasks)

---

## Phase: Signature Normalization (actions/*.py)

> All 17 action functions must expose `(parameters, player=None, speak=None, **kwargs) -> str`.
> Dead params `session_memory` and `response` are dropped; `**kwargs` is kept as safety net.
> The 17 files are split into 4 batches of ≤5 to satisfy the granularity gate.

### T02 — Sig-norm batch A: 5 "no-speak" actions (open_app, web_search, weather_report, send_message, reminder)
- **AC(s):** AC2, AC3
- **Files touched (5):**
  - `actions/open_app.py` (current sig at `:224`)
  - `actions/web_search.py` (current sig at `:81`)
  - `actions/weather_report.py` (current sig at `:5`; dead `session_memory` read block at `:36-40`)
  - `actions/send_message.py` (current sig at `:233`)
  - `actions/reminder.py` (current sig at `:282`)
- **Change per file:**
  1. Add `speak=None` and `**kwargs` to the function signature.
  2. Drop `session_memory` and `response` positional/keyword params if present.
  3. `actions/weather_report.py` only: remove the orphaned `session_memory` read block at `:36-40`
     (runtime-dead; no caller passes `session_memory`; removing it is baseline-preserving per NFR-3).
  4. Do NOT alter function body logic beyond the dead-param removal.
- **Acceptance criterion:** All 5 functions accept `(parameters, player=None, speak=None, **kwargs)`.
  `weather_report.py` no longer contains the `session_memory` read block at `:36-40`. No dead
  `session_memory` or `response` params remain in any of these 5 signatures. Existing callers that
  pass only `parameters` continue to work (`**kwargs` absorbs any legacy extras).
- **Tests:** Verified implicitly by T10 (dual-path test dispatches all 17). Direct check: import each
  module and inspect the function signatures.
- **Parallel-eligible:** YES (independent of T03, T04, T05; all precede T06)

### T03 — Sig-norm batch B: 5 "no-speak" actions (computer_settings, browser_control, file_controller, desktop_control, computer_control)
- **AC(s):** AC2, AC3
- **Files touched (5):**
  - `actions/computer_settings.py` (current sig at `:590`)
  - `actions/browser_control.py` (current sig at `:813`)
  - `actions/file_controller.py` (current sig at `:470`)
  - `actions/desktop.py` (function `desktop_control`, current sig at `:389`)
  - `actions/computer_control.py` (current sig at `:353`)
- **Change per file:**
  1. Add `speak=None` and `**kwargs` to the function signature.
  2. Drop `session_memory` and `response` positional/keyword params if present.
  3. Do NOT alter function body logic.
- **Acceptance criterion:** All 5 functions accept `(parameters, player=None, speak=None, **kwargs)`.
  No dead `session_memory` or `response` params remain. Existing callers that pass only `parameters`
  continue to work.
- **Tests:** Verified implicitly by T10.
- **Parallel-eligible:** YES (independent of T02, T04, T05; all precede T06)

### T04 — Sig-norm batch C: 4 "with-speak" actions (code_helper, dev_agent, screen_process, youtube_video)
- **AC(s):** AC2
- **Files touched (4):**
  - `actions/code_helper.py` (current sig at `:460` — already has `speak`)
  - `actions/dev_agent.py` (current sig at `:495` — already has `speak`)
  - `actions/screen_processor.py` (function `screen_process`, current sig at `:225` — already has `speak`)
  - `actions/youtube_video.py` (current sig at `:387` — already has `speak`)
- **Change per file:**
  1. Add `**kwargs` to the function signature (safety net for legacy callers).
  2. Drop `session_memory` and `response` params if present in any of these 4.
  3. Do NOT add `speak=None` (already present). Do NOT alter body logic.
- **Acceptance criterion:** All 4 functions accept `(parameters, player=None, speak=None, **kwargs)`.
  `**kwargs` is present. No dead `session_memory` or `response` params remain.
- **Tests:** Verified implicitly by T10.
- **Parallel-eligible:** YES (independent of T02, T03, T05; all precede T06)

### T05 — Sig-norm batch D: 3 "with-speak" actions (file_processor, flight_finder, game_updater)
- **AC(s):** AC2, AC3
- **Files touched (3):**
  - `actions/file_processor.py` (current sig at `:777` — already has `speak`)
  - `actions/flight_finder.py` (current sig at `:284` — already has `speak`)
  - `actions/game_updater.py` (current sig at `:926` — already has `speak`)
- **Change per file:**
  1. Add `**kwargs` to the function signature.
  2. Drop `session_memory` and `response` params if present.
  3. Do NOT add `speak=None` (already present). Do NOT alter body logic.
- **Acceptance criterion:** All 3 functions accept `(parameters, player=None, speak=None, **kwargs)`.
  `**kwargs` is present. No dead `session_memory` or `response` params remain.
- **Tests:** Verified implicitly by T10 and T13 (file_processor executor dispatch).
- **Parallel-eligible:** YES (independent of T02, T03, T04; all precede T06)

---

## Phase: Dispatcher Updates

### T06 — executor.py: player param + B2 file_processor branch + B3 raise + call-site keyword fixes
- **AC(s):** AC2, AC3
- **Depends on:** T02, T03, T04, T05 (all sig-norm batches complete before any dispatcher is updated)
- **Files touched (1):** `agent/executor.py`
- **Changes (all in one task — inseparable due to the positional-binding trap):**
  1. **Player param:** Change `_call_tool(tool, parameters, speak)` at `:171` to
     `_call_tool(tool, parameters, player=None, speak=None)`.
  2. **Forward player+speak in EVERY branch:** Replace all hardcoded `player=None` in the 17 action
     branches with `player=player, speak=speak`.
  3. **B2 — add file_processor branch:** Add the missing branch:
     `from actions.file_processor import file_processor` then
     `return file_processor(parameters=parameters, player=player, speak=speak) or "Done."`.
     After this the tool set is the full 17 action tools.
  4. **B3 — replace silent fallback:** Remove the `else:` block at `:243-245`
     (`print(...) + _run_generated_code(...)`) and replace with
     `raise ValueError(f"Unknown tool: {tool}")`.
  5. **KEEP generated_code branch:** The explicit `generated_code` branch at `:233-237` is KEPT intact
     (it is a legitimate named tool, not the unknown-tool fallback).
  6. **Call-site keyword fixes (2 sites only — no third site exists per analysis):**
     - `:300` — change `_call_tool(tool, params, speak)` to `_call_tool(tool, params, speak=speak)`
     - `:340` — change `_call_tool(fixed_step["tool"], fixed_step["parameters"], speak)` to
       `_call_tool(fixed_step["tool"], fixed_step["parameters"], speak=speak)`
     Positional `speak` after adding `player` would bind `speak` to `player` and silently break
     dispatch — the keyword form is mandatory.
- **Acceptance criterion:**
  - `_call_tool` signature is `(tool, parameters, player=None, speak=None) -> str`.
  - All 17 action tool branches forward `player=player, speak=speak`.
  - `file_processor` branch exists and dispatches to `actions.file_processor.file_processor`.
  - Unknown tool name raises `ValueError` (or `KeyError`) — no silent pass.
  - `generated_code` branch at `:233-237` is intact.
  - Internal call sites at `:300` and `:340` pass `speak=` as a keyword argument.
- **Tests:** TAS-3 (B2), TAS-4 (B3) regression tests in T13, T14. TAS-1 (dual-path) in T10.
- **Parallel-eligible:** NO (depends on T02–T05)

### T07 — main.py: drop dead kwargs + add speak=self.speak to all lacking it + fix B1 (flight_finder)
- **AC(s):** AC2, AC3
- **Depends on:** T06 (executor done; sig-norm complete)
- **Files touched (1):** `main.py`
- **Changes:**
  1. **Drop dead kwargs** from the callers that still pass them (remove `response=...` and/or
     `session_memory=...` keyword arguments). Per analysis §3, the affected callers are:
     - `open_app` at `:814`
     - `send_message` at `:830`
     - `reminder` at `:834`
     - `youtube_video` at `:838`
     - `screen_process` at `:843`
     - `computer_settings` at `:847`
  2. **Add `speak=self.speak`** to every action caller in `_execute_tool` that does not yet forward
     `speak`. This includes `flight_finder` at `:896` (B1 — currently passes no `speak`). The
     implementer must read `main.py:813-897` to identify the exact callers lacking `speak`; the
     4 callers that per analysis §3 already forward `speak` are `code_helper` (`:855`), `dev_agent`
     (`:859`), `file_processor` (`:884`), and `game_updater` (`:892`) — do NOT touch these 4.
     Every other action caller in `_execute_tool` gets `speak=self.speak` added.
  3. The main path forwards `player=self.ui` on all action callers (already present — do not remove).
  4. The 3 inline tool handlers (`save_memory`, `agent_task`, `shutdown_jarvis`) are untouched.
- **Acceptance criterion:**
  - `flight_finder` call at `:896` includes `speak=self.speak` (B1 fixed).
  - No call in `_execute_tool` passes `response=` or `session_memory=` to any action function.
  - All 17 action callers in `_execute_tool` forward `speak=self.speak`.
  - All 17 action callers in `_execute_tool` forward `player=self.ui`.
  - The 3 inline tool handlers are untouched.
- **Tests:** TAS-2 (B1) regression test in T12. TAS-1 (dual-path) in T10.
- **Parallel-eligible:** NO (depends on T06)

### T08 — planner.py: add file_processor to PLANNER_PROMPT (B4)
- **AC(s):** AC3
- **Depends on:** T07
- **Files touched (1):** `agent/planner.py`
- **Change:**
  1. Add a `file_processor` tool block to `PLANNER_PROMPT` (`:22-132`), consistent with the format
     used for the 16 existing tool entries.
  2. Do NOT add `agent_task` to the planner. Decision C (frozen carry-forward): `agent_task` has no
     executor branch; post-B3, a planner-emitted `agent_task` step would raise. The TAS-5
     "(and `agent_task`)" parenthetical in spec.md is SUPERSEDED by Decision C — assert
     `file_processor` ONLY.
- **Acceptance criterion:**
  - `grep file_processor agent/planner.py` returns a match inside `PLANNER_PROMPT`.
  - `agent_task` does NOT appear in `PLANNER_PROMPT`.
  - The `PLANNER_PROMPT` string contains entries for all 17 action tool names.
  - No other changes to `planner.py`.
- **Tests:** TAS-5 (B4) regression test in T15.
- **Parallel-eligible:** NO (depends on T07)

---

## Phase: Test Harness

### T09 — conftest.py: QApplication singleton, QTest fixture, Ollama mock, thread fixtures
- **AC(s):** AC1, AC2, AC3, AC4 (enables all tests)
- **Depends on:** T08 (all production code changes complete — conftest imports from production modules)
- **Files touched (1):** `tests/conftest.py` (create from scratch)
- **Changes:**
  1. **Session-scoped QApplication singleton** — set `QT_QPA_PLATFORM=offscreen` BEFORE the
     PyQt6 import so headless CI legs do not require a display:
     ```python
     import os
     os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
     from PyQt6.QtWidgets import QApplication
     @pytest.fixture(scope="session")
     def qapp():
         app = QApplication.instance() or QApplication([])
         yield app
     ```
  2. **`qtest` fixture:** `from PyQt6.QtTest import QTest` exposed as a function-scoped fixture.
  3. **Ollama mock fixture:** monkeypatch all 3 entry points in `core/llm_client.py` so no test
     hits a live socket:
     - `core.llm_client.call_llm_text` (`:331`, primary)
     - `core.llm_client.call_llm` (`:229`)
     - `core.llm_client.call_llm_stream` (`:488`)
     Patch the module-level functions, not instance methods.
  4. **Thread fixtures:** helpers for inline-tool tests — specifically `shutdown_jarvis`'s daemon
     thread — so threads are joined/contained and never kill the runner.
- **Acceptance criterion:**
  - `pytest --collect-only` exits 0 with the conftest present.
  - A no-op test using `qapp`/`qtest` fixtures passes under `QT_QPA_PLATFORM=offscreen`.
  - A no-op test using the Ollama mock fixture does not connect to any socket.
  - No test in the suite (T10–T16) can invoke a live LLM call.
- **Tests:** Self-verifying (conftest is the test infrastructure; T10–T16 implicitly validate it).
- **Parallel-eligible:** NO (depends on T08; must precede T10–T16)

### T10 — Parametrized dual-path routing test (TAS-1, AC2)
- **AC(s):** AC2
- **Depends on:** T09
- **Files touched (1):** `tests/test_dispatch.py` (create)
- **Change:** Author the parametrized test encoding the §4 non-uniform patch matrix VERBATIM.
  The matrix is the SINGLE SOURCE OF TRUTH — do not re-derive or simplify. Encode as:

  ```python
  TOOL_MATRIX = [
    # (tool_name, main_bound_name, executor_patch_target)
    ("open_app",          "main.open_app",          "actions.open_app.open_app"),
    ("web_search",        "main.web_search_action", "actions.web_search.web_search"),
    ("weather_report",    "main.weather_action",    "actions.weather_report.weather_action"),
    ("send_message",      "main.send_message",      "actions.send_message.send_message"),
    ("reminder",          "main.reminder",          "actions.reminder.reminder"),
    ("youtube_video",     "main.youtube_video",     "actions.youtube_video.youtube_video"),
    ("screen_process",    "main.screen_process",    "actions.screen_processor.screen_process"),
    ("computer_settings", "main.computer_settings", "actions.computer_settings.computer_settings"),
    ("browser_control",   "main.browser_control",   "actions.browser_control.browser_control"),
    ("file_controller",   "main.file_controller",   "actions.file_controller.file_controller"),
    ("desktop_control",   "main.desktop_control",   "actions.desktop.desktop_control"),
    ("code_helper",       "main.code_helper",       "actions.code_helper.code_helper"),
    ("dev_agent",         "main.dev_agent",         "actions.dev_agent.dev_agent"),
    ("computer_control",  "main.computer_control",  "actions.computer_control.computer_control"),
    ("game_updater",      "main.game_updater",      "actions.game_updater.game_updater"),
    ("flight_finder",     "main.flight_finder",     "actions.flight_finder.flight_finder"),
    ("file_processor",    "main.file_processor",    "actions.file_processor.file_processor"),
  ]
  ```

  Test body per row:
  - Patch `main_bound_name` AND `executor_patch_target` each with `MagicMock(return_value="ok")`.
  - Dispatch through `main._execute_tool(tool_name, args)` with a mock `player` and mock `speak`.
  - Assert the `main_bound_name` mock was called once with `player=<mock>` and `speak=<mock>`.
  - Dispatch through `agent.executor._call_tool(tool_name, args, player=player_mock, speak=speak_mock)`.
  - Assert the `executor_patch_target` mock was called once with `player=<mock>` and `speak=<mock>`.
  - No live side effects — all 17 entry points are mocked; no real action executes.
  - TRAP: Do NOT use `patch("actions.X.X")` for the main leg — main binds funcs at module top;
    that patch target is wrong and gives FALSE GREENS. Use the exact `main_bound_name` from the
    matrix. In particular: `weather_report` -> `main.weather_action`;
    `web_search` -> `main.web_search_action`. Both differ from the tool name.

- **Acceptance criterion:**
  - `pytest tests/test_dispatch.py -q` passes with 17 parametrized test cases (one per tool),
    each asserting both the main-leg and executor-leg forwards `player=<mock>` and `speak=<mock>`.
  - The test uses the exact non-uniform patch matrix above, not a derived or simplified form.
  - No test makes a live function call (all action entry points mocked).
  - Both aliases (`main.weather_action`, `main.web_search_action`) are patched correctly.
- **Tests:** This task IS the test (TAS-1).
- **Parallel-eligible:** NO (depends on T09)

### T11 — Inline tool tests: save_memory, agent_task, shutdown_jarvis (TAS-6)
- **AC(s):** AC3
- **Depends on:** T09
- **Files touched (1):** `tests/test_inline_tools.py` (create)
- **Changes (3 tests):**
  1. **save_memory test:** dispatch `main._execute_tool("save_memory", {...})` with appropriate params;
     assert the `__SILENT__` inline handler runs (main:~800-809), not routed to `actions/`.
  2. **agent_task test:** dispatch `main._execute_tool("agent_task", {...})` with appropriate params;
     assert it enqueues to the task queue (main:~862-875), not routed to `actions/`.
  3. **shutdown_jarvis test:** Mock `main.os._exit` (the call is in a nested closure at main:~906);
     dispatch `main._execute_tool("shutdown_jarvis", {})`;
     assert `main.os._exit` was called with `0`. The test process must NOT be killed. Use the
     thread fixture from conftest to join any spawned threads.
- **Acceptance criterion:**
  - All 3 inline-tool tests pass under `QT_QPA_PLATFORM=offscreen`.
  - `main.os._exit` is mocked — the test process is never killed.
  - The `shutdown_jarvis` test asserts `_exit` was called with `0`.
  - `save_memory` and `agent_task` are NOT routed to any `actions/` module.
  - EC-3, EC-7 satisfied.
- **Tests:** This task IS the tests (TAS-6).
- **Parallel-eligible:** YES (parallel with T10, T12–T15 — all depend on T09 only)

### T12 — B1 regression test: flight_finder speak forwarding (TAS-2)
- **AC(s):** AC3
- **Depends on:** T09
- **Files touched (1):** `tests/test_regressions.py` (create)
- **Change:** Author `test_b1_flight_finder_speak_forwarded`:
  - Patch `main.flight_finder` with a `MagicMock`.
  - Create a tracked `speak` callable (e.g. `MagicMock()`).
  - Dispatch `main._execute_tool("flight_finder", args)` with that `speak`.
  - Assert `main.flight_finder` was called with `speak=<tracked_speak>`.
  - Pre-fix failure note: This test FAILS on the pre-WO-0 code (main.py:896 drops `speak`).
    The fix in T07 makes it green. Inner-loop verification: revert T07's flight_finder change ->
    test red; re-apply -> test green.
- **Acceptance criterion:**
  - Test passes with T07's fix applied.
  - The assertion explicitly checks `speak=<mock>` was passed to `flight_finder`.
  - Test name is `test_b1_flight_finder_speak_forwarded` (or equivalent descriptive name).
- **Tests:** This task IS the test (TAS-2).
- **Parallel-eligible:** YES (parallel with T10, T11, T13–T15)

### T13 — B2 regression test: executor dispatches file_processor (TAS-3)
- **AC(s):** AC3
- **Depends on:** T09
- **Files touched (1):** `tests/test_regressions.py` (append)
- **Change:** Author `test_b2_executor_dispatches_file_processor`:
  - Patch `actions.file_processor.file_processor` with a `MagicMock(return_value="ok")`.
  - Call `agent.executor._call_tool("file_processor", {"x": 1}, player=mock_player, speak=mock_speak)`.
  - Assert the patched function was called once with `player=<mock>` and `speak=<mock>`.
  - Pre-fix failure note: FAILS on pre-WO-0 executor (no `file_processor` branch -> hits the silent
    fallback or raises). The fix in T06 makes it green.
- **Acceptance criterion:**
  - Test passes with T06's B2 fix applied.
  - The patched `actions.file_processor.file_processor` is called exactly once.
  - `player` and `speak` are forwarded (wiring assertion).
- **Tests:** This task IS the test (TAS-3).
- **Parallel-eligible:** YES (parallel with T10, T11, T12, T14, T15)

### T14 — B3 regression test: unknown tool raises (TAS-4)
- **AC(s):** AC3
- **Depends on:** T09
- **Files touched (1):** `tests/test_regressions.py` (append)
- **Change:** Author `test_b3_unknown_tool_raises`:
  - Call `agent.executor._call_tool("__nonexistent_tool__", {})`.
  - Assert a `ValueError` (or `KeyError`) is raised.
  - Also verify the `generated_code` named tool is NOT broken: call
    `_call_tool("generated_code", {"description": "..."})` and assert it does NOT raise.
  - Pre-fix failure note: FAILS on pre-WO-0 executor (else fallback at `:243-245` silently calls
    `_run_generated_code` instead of raising). The fix in T06 makes it green.
  - EC-5: this is the single intended behaviour change vs. baseline — a bug correction.
- **Acceptance criterion:**
  - `pytest.raises(ValueError)` (or `KeyError`) passes for any unknown tool name.
  - `_run_generated_code` is NOT called for the unknown tool.
  - The `generated_code` named tool still dispatches correctly (does NOT raise).
- **Tests:** This task IS the test (TAS-4).
- **Parallel-eligible:** YES (parallel with T10, T11, T12, T13, T15)

### T15 — B4 regression test: PLANNER_PROMPT contains file_processor (TAS-5)
- **AC(s):** AC3
- **Depends on:** T09
- **Files touched (1):** `tests/test_regressions.py` (append)
- **Change:** Author `test_b4_planner_prompt_contains_file_processor`:
  - Import `PLANNER_PROMPT` from `agent.planner`.
  - Assert `"file_processor"` is in `PLANNER_PROMPT`.
  - Assert `"agent_task"` is NOT in `PLANNER_PROMPT` (Decision C enforced in the test: adding
    `agent_task` to the planner would manufacture a post-B3 raise; the spec.md TAS-5 parenthetical
    "(and agent_task)" is SUPERSEDED by Decision C).
  - Pre-fix failure note: FAILS on pre-WO-0 planner (no `file_processor` in `PLANNER_PROMPT`).
    The fix in T08 makes it green.
- **Acceptance criterion:**
  - `"file_processor" in PLANNER_PROMPT` is `True`.
  - `"agent_task" in PLANNER_PROMPT` is `False`.
  - No other tool names are inadvertently removed.
- **Tests:** This task IS the test (TAS-5).
- **Parallel-eligible:** YES (parallel with T10, T11, T12, T13, T14)

### T16 — Flag default-OFF test (TAS-7)
- **AC(s):** AC4
- **Depends on:** T01 (flag rail exists); independent of T09
- **Files touched (1):** `tests/test_flags.py` (create)
- **Change:** Author `test_flag_defaults_all_off`:
  1. For each of the 5 registered flags (`use_rust_wheel`, `use_tool_registry`,
     `enable_security_gates`, `enable_memory_v2`, `enable_loopguard`):
     - Assert `get_flag(name)` returns `False` when `config/flags.json` is absent (monkeypatch the
       path or use a tmp dir).
     - Assert `get_flag(name)` returns `False` when `config/flags.json` is present with `false`.
  2. Assert `get_flag("nonexistent_flag")` returns `False` (the default).
  3. Assert `load_api_keys()` and `is_configured()` are callable without error (byte-for-byte intact).
  4. Static assertion: grep the changed production files for `get_flag` calls; expect zero (no WO-0
     production code gates behaviour behind `get_flag`).
- **Acceptance criterion:**
  - All 5 named flags return `False` by default.
  - `get_flag("nonexistent_flag")` returns `False`.
  - `load_api_keys()` and `is_configured()` work unchanged.
  - No WO-0 production file calls `get_flag` to gate any behaviour (AC4: flag-OFF == baseline).
- **Tests:** This task IS the test (TAS-7).
- **Parallel-eligible:** YES with T09–T15 (depends on T01 only)

---

## Phase: CI

### T17 — CI workflow: test matrix + Rust placeholder + import-smoke (TAS-8, AC1)
- **AC(s):** AC1
- **Depends on:** T09 (conftest exists; test suite is runnable)
- **Files touched (1):** `.github/workflows/ci.yml` (create)
- **Change:** Create `.github/workflows/ci.yml` with 3 jobs:

  **Job 1 — `test` (required, all 12 legs must be green):**
  - `strategy.matrix: {os: [macos-14, macos-13, windows-latest, ubuntu-latest], python: ["3.11","3.12","3.13"]}`
  - `env: QT_QPA_PLATFORM: offscreen` on all legs
  - On `ubuntu-latest` only: `sudo apt-get install -y libegl1` (PyQt6 EGL dependency)
  - Run: `pip install -r requirements.txt pytest` then `QT_QPA_PLATFORM=offscreen pytest -q`
  - All 12 legs are REQUIRED checks (no `continue-on-error`)

  **Job 2 — `rust-wheel-placeholder` (non-blocking stub):**
  - `continue-on-error: true`
  - NOT listed as a required check — must never block the WO-0 matrix
  - Steps: `dtolnay/rust-toolchain@1.88`, stub `cargo build` or maturin invocation
  - WO-1 activates the real maturin build; this job is a seam only

  **Job 3 — `import-smoke` (skips cleanly when wheel absent):**
  - `python -c "try: import mark_xl_rust \nexcept ImportError: import sys; sys.exit(0)"`
  - Exit 0 when wheel absent (EC-2 — absent wheel is expected and not a failure in WO-0)

- **Acceptance criterion:**
  - `.github/workflows/ci.yml` exists with all 3 jobs.
  - The `test` job matrix covers 4 OS × 3 Python = 12 legs; all are required.
  - `QT_QPA_PLATFORM=offscreen` is set for all matrix legs.
  - `libegl1` is installed only on `ubuntu-latest`.
  - `rust-wheel-placeholder` has `continue-on-error: true` and is NOT a required check.
  - `import-smoke` exits 0 when `mark_xl_rust` is absent.
  - YAML is syntactically valid (`python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"` passes).
  - NFR-4: no `rlimit` usage introduced.
- **Tests:** TAS-8. Locally: validate YAML syntax. CI: the workflow itself is the test.
- **Parallel-eligible:** Can be authored in parallel with T10–T16 once T09 exists; ordered after T09.

---

## AC Coverage Map

| Task | AC1 | AC2 | AC3 | AC4 |
|---|---|---|---|---|
| T01 — flags schema + accessor | | | | AC4 |
| T02 — sig-norm batch A (5 files) | | AC2 | AC3 | |
| T03 — sig-norm batch B (5 files) | | AC2 | AC3 | |
| T04 — sig-norm batch C (4 files) | | AC2 | | |
| T05 — sig-norm batch D (3 files) | | AC2 | AC3 | |
| T06 — executor.py (player+B2+B3+call-sites) | | AC2 | AC3 | |
| T07 — main.py callers (B1+dead-kwargs) | | AC2 | AC3 | |
| T08 — planner.py (B4) | | | AC3 | |
| T09 — conftest + fixtures | AC1 | AC2 | AC3 | AC4 |
| T10 — dual-path routing test (TAS-1) | | AC2 | | |
| T11 — inline tool tests (TAS-6) | | | AC3 | |
| T12 — B1 regression test (TAS-2) | | | AC3 | |
| T13 — B2 regression test (TAS-3) | | | AC3 | |
| T14 — B3 regression test (TAS-4) | | | AC3 | |
| T15 — B4 regression test (TAS-5) | | | AC3 | |
| T16 — flag default-OFF test (TAS-7) | | | | AC4 |
| T17 — CI workflow (TAS-8) | AC1 | | | |

All 4 ACs have at least one production task and at least one test task:
- AC1: T09 (infrastructure), T17 (CI YAML)
- AC2: T02–T07 (all sig-norm + dispatchers), T10 (dual-path test)
- AC3: T02, T03, T05–T08 (bug-carrying tasks), T11–T15 (regression + inline tests)
- AC4: T01 (flag rail), T16 (flag test)

---

## Parallel Execution Opportunities

- T02, T03, T04, T05 are fully parallel (no inter-batch deps within sig-norm).
- T10, T11, T12, T13, T14, T15 are parallel after T09.
- T01 and T16 form their own chain independent of sig-norm.
- T17 can be authored in parallel with T10–T16 once T09 exists.

Serial spine: [T02, T03, T04, T05] -> T06 -> T07 -> T08 -> T09 -> [T10, T11, T12, T13, T14, T15, T17]
