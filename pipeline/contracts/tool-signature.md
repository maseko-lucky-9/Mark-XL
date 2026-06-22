# Contract — Tool Signature & Dual-Path Dispatch (WO-0, FROZEN)

> Spec Kit `plan` service contract for WO-0. This is the **frozen** interface contract for the
> dispatch layer and the **single source of truth for test authors** writing the dual-path
> routing test. Cited to live code on `wo-0-foundations`. The dispatchers live under `agent/`.

---

## 1. Normalized action-function signature (all 17 action tools)

Every one of the 17 action functions in `actions/*.py` MUST expose this exact signature:

```python
def <action>(parameters, player=None, speak=None, **kwargs) -> str: ...
```

- `parameters` — the tool arguments dict (positional, required).
- `player=None` — UI/player handle; `None` in production executor path, a mock in tests.
- `speak=None` — the active speak callable; `None` when no voice surface is bound.
- `**kwargs` — safety net that absorbs the dropped dead params (`session_memory`, `response`)
  from any un-updated caller, so no caller raises `TypeError` during the transition (EC-4).
- Returns `str`.

The dropped dead params (`session_memory`, `response`) MUST NOT appear in any of the 17
signatures. `**kwargs` is KEPT even though all live callers are updated in the same pass
(belt-and-suspenders).

The 17 action tools (verbatim, source of truth — do not re-order or paraphrase):
open_app, web_search, weather_report, send_message, reminder, youtube_video, screen_process,
computer_settings, browser_control, file_controller, desktop_control, code_helper, dev_agent,
computer_control, game_updater, flight_finder, file_processor.

---

## 2. Executor dispatcher contract — `agent/executor._call_tool`

**Current (pre-WO-0):** `_call_tool(tool: str, parameters: dict, speak: Callable | None) -> str`
(`agent/executor.py:171`) — **no `player` param**; every branch hardcodes `player=None`.

**Normalized (WO-0):**

```python
def _call_tool(tool, parameters, player=None, speak=None) -> str: ...
```

Contract:
- Forwards **`player=player, speak=speak` in EVERY action branch** (replacing the hardcoded
  `player=None`).
- Production `execute()` leaves `player` at its default `None`; the **test** injects a mock by
  calling/patching `_call_tool(player=<mock>)`.
- KEEP the explicit `generated_code` branch (`agent/executor.py:233-237`).
- Unknown tool -> **RAISE** (`ValueError`/`KeyError`); the silent `_run_generated_code` fallback
  (`agent/executor.py:243-245`) is removed (B3 / EC-5).
- The **2 (and only 2)** internal call sites MUST pass `speak` as a **keyword** arg (positional
  would bind `speak` to the new `player` param and break dispatch):
  - `agent/executor.py:300` -> `_call_tool(tool, params, speak=speak)`
  - `agent/executor.py:340` -> `_call_tool(fixed_step["tool"], fixed_step["parameters"], speak=speak)`

**What the executor path forwards:** `player` (default `None` in production; mock in tests) and
`speak` (the callable threaded in from `execute()` / the test).

---

## 3. Main dispatcher contract — `main._execute_tool`

`_execute_tool` spans `main.py:813-897`. All 17 action funcs are module-top imports
(`main.py:75-91`); two are aliased (`weather_action` at `main.py:78`; `web_search as
web_search_action` at `main.py:89`).

**What the main path forwards:** `player=self.ui` (already present on the action callers) and
`speak=self.speak`. B1 (`main.py:896`, `flight_finder`) currently drops `speak`; after the fix,
all 17 action callers forward `speak=self.speak`. Dead `response=`/`session_memory=` kwargs are
dropped from the 6 callers that pass them (open_app `main.py:814`, send_message `main.py:830`,
reminder `main.py:834`, youtube_video `main.py:838`, screen_process `main.py:843`,
computer_settings `main.py:847`).

The 3 inline tools (`save_memory`, `agent_task`, `shutdown_jarvis`) are main-only, handled inside
`main.py`, and are **NOT** part of this dual-path contract (no executor branch).

---

## 4. Per-tool patch matrix (FROZEN — test-author SoT)

Encode this table directly as the dual-path test's parametrization. **NON-UNIFORM**: a naive
`patch("actions.X.X")` gives FALSE GREENS on the main leg and misses the two aliases.

- **main leg** -> patch the symbol **as bound in `main`** (module-top import). All bind as
  `main.<func>` EXCEPT two aliases: `main.weather_action` (weather_report) and
  `main.web_search_action` (web_search).
- **executor leg** -> patch `actions.<module>.<func>` (the lazy import inside `_call_tool`).

| tool | main-bound name | executor patch target (`actions.<module>.<func>`) |
|---|---|---|
| open_app          | `main.open_app`          | `actions.open_app.open_app` |
| web_search        | `main.web_search_action` | `actions.web_search.web_search` |
| weather_report    | `main.weather_action`    | `actions.weather_report.weather_action` |
| send_message      | `main.send_message`      | `actions.send_message.send_message` |
| reminder          | `main.reminder`          | `actions.reminder.reminder` |
| youtube_video     | `main.youtube_video`     | `actions.youtube_video.youtube_video` |
| screen_process    | `main.screen_process`    | `actions.screen_processor.screen_process` |
| computer_settings | `main.computer_settings` | `actions.computer_settings.computer_settings` |
| browser_control   | `main.browser_control`   | `actions.browser_control.browser_control` |
| file_controller   | `main.file_controller`   | `actions.file_controller.file_controller` |
| desktop_control   | `main.desktop_control`   | `actions.desktop.desktop_control` |
| code_helper       | `main.code_helper`       | `actions.code_helper.code_helper` |
| dev_agent         | `main.dev_agent`         | `actions.dev_agent.dev_agent` |
| computer_control  | `main.computer_control`  | `actions.computer_control.computer_control` |
| game_updater      | `main.game_updater`      | `actions.game_updater.game_updater` |
| flight_finder     | `main.flight_finder`     | `actions.flight_finder.flight_finder` |
| file_processor    | `main.file_processor`    | `actions.file_processor.file_processor` |

**Assertion contract:** for each row, patch both targets with a `MagicMock`, dispatch through
`main._execute_tool` AND `agent/executor._call_tool` with a mock `player` and mock `speak`, and
assert each patched entry point is called **once** with `player=<mock>` and `speak=<mock>`
forwarded. Routing/wiring assertion only — no live side effects.
