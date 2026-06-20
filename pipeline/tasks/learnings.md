# WO-0 Cross-Task Learnings

## Setup — 2026-06-21
- Repo: /Users/ltmas/Repo/agents/mark-xl, branch: wo-0-foundations
- Venv created at .venv-mac; pytest + PyQt6 installed
- No mypy/pyproject.toml — typecheckPassed means `python -m py_compile <files>` on changed files
- buildPassed = no import errors on changed files (python -m py_compile)
- Test command: QT_QPA_PLATFORM=offscreen .venv-mac/bin/pytest -q
- 17 action files, all in actions/*.py; dispatchers in agent/executor.py and main.py
- Sig change is PURELY mechanical: add speak=None, **kwargs; drop session_memory, response
- Do NOT change any function body logic beyond removing the dead session_memory read block in weather_report.py
- config/flags.json goes in the config/ dir (which already exists with config/__init__.py)

## T01 — flags schema + accessor — 2026-06-21
Status: completed
Key decisions: Used _FLAGS_FILE = Path(__file__).resolve().parent.parent / "config" / "flags.json" as module-level constant; avoids dead code and is consistent with existing BASE_DIR/CONFIG_FILE pattern.
Gotchas: Initial implementation had stray `import os as _os`, dead `_FLAGS_PATH`, and inline re-imports of json/Path inside get_flag body — all cleaned up in quality review pass.
Patterns established: Module-level path constants consistent with existing style (CONFIG_FILE etc.)
Files other tasks should know about: memory/config_manager.py (added _FLAG_SCHEMA + get_flag at line ~57+)

## T02-T05 — Sig-norm batches A-D — 2026-06-21
Status: completed (all 4 batches)
Key decisions: Pure AST/py_compile verification (no live imports) because many action modules use platform-specific deps (pyautogui, pywinauto, etc.) that may not be available in .venv-mac.
Gotchas: weather_report.py had dead session_memory READ block in body (lines 36-40) that also needed removal; other files only needed sig changes.
Patterns established: All 17 action functions now: (parameters: dict, player=None, speak=None, **kwargs) -> str
Files other tasks should know about:
- actions/weather_report.py: function name is weather_action (NOT weather_report) — executor dispatches as tool "weather_report" but the function is weather_action
- actions/desktop.py: function name is desktop_control
- actions/screen_processor.py: function name is screen_process (module is screen_processor)
- T06 must know these name mismatches for executor branches

## T06 — executor.py changes — 2026-06-21
Status: completed
Key decisions:
- _call_tool now: (tool, parameters, player=None, speak=None) -> str
- All 17 action branches forward player=player, speak=speak
- file_processor branch added (B2 fix)
- Unknown tool raises ValueError (B3 fix)
- generated_code branch kept intact
- Internal call sites at ~300 and ~340 use speak=speak (keyword) — CRITICAL binding trap fix
Gotchas:
- Internal call sites (AgentExecutor.execute()) don't pass player because AgentExecutor has no player — that's correct architecture (executor path = no UI player). Don't change this.
- Code reviewer flagged this as a WARN (not FAIL) — it's pre-existing architectural behavior
Files other tasks should know about: agent/executor.py fully updated
