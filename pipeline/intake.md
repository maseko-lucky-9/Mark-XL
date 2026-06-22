# Intake — WO-0 (Foundations)

## Problem statement
Mark-XL has no tests, no CI, no feature-flag system, four live dispatch bugs, and heterogeneous tool
signatures; nothing downstream is safely verifiable until these are fixed.

## Complexity tier
**Complex** (Tier 0, 8–12 d, high risk). Single bounded context (the dispatch + foundations layer);
no scope-decomposition warning. Not dual-client (`intake.dual_client = false`).

## Scope (verified against the real code on branch wo-0-foundations @ origin/main fb46b5e)
1. **Fix 4 live bugs** (all confirmed against the code):
   - B1 `main.py:896` — `flight_finder(parameters=args, player=self.ui)` drops `speak`; `flight_finder.py:284`
     accepts `speak=None`. Fix: pass `speak=self.speak`.
   - B2 `agent/executor.py:_call_tool` — missing `file_processor`; tool list out of sync. Fix: add it (and
     reconcile to the full action-tool set), normalize calls to forward `player`/`speak`.
   - B3 `agent/executor.py:243-245` — silent generated-code fallback for unknown tools. Fix: delete; raise on unknown.
   - B4 `agent/planner.py:22-132` — `PLANNER_PROMPT` lists 16 tools, missing `file_processor` (and `agent_task`).
     Fix: add the missing planner-visible tools (WO-2 will auto-generate this; WO-0 just corrects the list).
2. **Normalize 17 action tool signatures** in `actions/*.py` to `(parameters, player=None, speak=None, **kwargs) -> str`.
   10 currently lack `speak`. Drop the dead `session_memory` AND dead `response` params (both unused;
   `**kwargs` absorbs them from any un-updated caller). Update callers in `main.py`/`executor.py` in the same pass.
3. **Feature-flag system** — extend `memory/config_manager.py` with a flag schema + `get_flag()`; all gates default OFF.
4. **pytest harness from zero** — `conftest.py` + fixtures (real `QApplication`/`QTest`, Ollama mock, thread fixtures);
   parametrized test dispatching every action tool through BOTH `main._execute_tool` and `executor._call_tool`
   with mock `player`/`speak` (routing/wiring assertion via patched action entry points — NOT live side effects).
5. **CI (GitHub Actions)** — test matrix (macOS macos-14 + macos-13, Windows, Linux × Py 3.11–3.13). Rust
   wheel-build job = **placeholder stub** (WO-1 produces `mark_xl_rust`); do not block WO-0 on it. Record abi3=true.

## SPEC CORRECTION (factual, recorded per advisor + cross-check)
The intake package says "18 tools" and "normalize 18 signatures". The **verified count is 17 action-backed
tools** (one function per `actions/*.py`, 17 files). The math is conclusive: 10 missing `speak` + 7 with `speak`
(code_helper, dev_agent, screen_process, youtube_video, file_processor, flight_finder, game_updater) = 17.
The "18th" is `agent_task` (and/or `save_memory`/`shutdown_jarvis`), which are **inline-handled in main only** and
absent from `executor._call_tool`, so they categorically cannot "dispatch through both paths". They get
main-only individual tests instead. This is a spec arithmetic fix, not a scope change → proceed autonomously;
logged for the final report.

## Canonical tool enumeration (source of truth for the parametrized test)
**17 dual-path action tools:** open_app, web_search, weather_report, send_message, reminder, youtube_video,
screen_process, computer_settings, browser_control, file_controller, desktop_control, code_helper, dev_agent,
computer_control, game_updater, flight_finder, file_processor.
**3 special inline tools (main-only, individual tests):** save_memory (`__SILENT__`), agent_task (task queue),
shutdown_jarvis (spawns daemon thread → `os._exit(0)`; MUST be mocked in tests).

## Acceptance criteria
- AC1: CI green on all matrix legs; Rust wheel job present as a non-blocking placeholder; `import mark_xl_rust`
  smoke job present (skips cleanly when wheel absent).
- AC2: All 17 action tools dispatch through BOTH paths in the parametrized test, with `player` and `speak` forwarded.
- AC3: The 4 bugs are fixed, each with a regression test.
- AC4: flag-OFF == baseline (trivially: nothing in WO-0 is gated; flag system defaults all OFF).
