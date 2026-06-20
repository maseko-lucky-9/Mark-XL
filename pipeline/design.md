# Design — WO-0 (Foundations)

> Spec Kit `plan` artifact (Architecture / Design Plan) for WO-0 of the Mark-XL fork
> (`maseko-lucky-9/Mark-XL`, branch `wo-0-foundations`). This is the **How** that satisfies
> the **What** in `pipeline/spec.md`. Every claim is cited to live code on `wo-0-foundations`
> (`file:line`). Paths are repo-relative; the dispatchers live under `agent/`
> (`agent/executor.py`, `agent/planner.py`), the action funcs under `actions/*.py`.
>
> **Count discipline:** there are **17** action-backed dual-path tools (NOT 18 — the "18"
> figure is a confirmed arithmetic miscount; see `## Lessons Applied`). The 3 inline tools are
> main-only and excluded from the dual-path set.

---

## 1. Architecture Overview

WO-0 hardens the **dispatch layer** — the two routing surfaces every downstream Work Order
(WO-1..WO-5) depends on — without changing any flag-OFF (baseline) behaviour. There are two
dispatch paths and two tool classes:

```
                        +----------------------------+
   voice / UI  ------>  | main._execute_tool         |  (main path)
                        |  - 17 action tools         |---> actions.<module>.<func>(
                        |  - 3 inline tools          |        parameters, player=self.ui,
                        |    (save_memory,           |        speak=self.speak, **kwargs)
                        |     agent_task,            |
                        |     shutdown_jarvis)       |
                        +----------------------------+
                                                            (inline tools handled in-place,
                                                             NOT routed to actions/*)

   planner step ----->  +----------------------------+
   (PLANNER_PROMPT)     | agent/executor._call_tool  |  (executor path)
                        |  - 17 action tools         |---> actions.<module>.<func>(
                        |  - generated_code branch   |        parameters, player=player,
                        |  - unknown -> RAISE (B3)    |        speak=speak, **kwargs)
                        +----------------------------+
```

**Tool classes:**
- **17 action tools (dual-path):** open_app, web_search, weather_report, send_message,
  reminder, youtube_video, screen_process, computer_settings, browser_control,
  file_controller, desktop_control, code_helper, dev_agent, computer_control, game_updater,
  flight_finder, file_processor. Reachable through **both** `main._execute_tool` and
  `agent/executor._call_tool`.
- **3 inline tools (main-only):** save_memory (`__SILENT__`), agent_task (task queue),
  shutdown_jarvis (spawns a daemon thread -> `os._exit(0)`; MUST be mocked in tests). These
  are handled inside `main.py` and have **no** branch in `agent/executor._call_tool`; they are
  excluded from the dual-path parametrization and tested individually.

WO-0 introduces **no** new architecture: no registry, no event bus, no tool-executor
abstraction. It fixes 4 live bugs, normalizes 17 signatures + their callers, builds an inert
feature-flag rail, and stands up the pytest harness + CI.

---

## 2. Normalized Signature Contract + Decision

### 2.1 The contract

All **17** action functions in `actions/*.py` expose the uniform signature:

```python
def <action>(parameters, player=None, speak=None, **kwargs) -> str: ...
```

The executor dispatcher is normalized to:

```python
def _call_tool(tool, parameters, player=None, speak=None) -> str: ...
```

(Current live signature: `_call_tool(tool: str, parameters: dict, speak: Callable | None) -> str`
at `agent/executor.py:171` — it has **no** `player` param; every branch hardcodes `player=None`.)

### 2.2 Decision — KEEP `**kwargs` AND update all callers in the same pass (belt-and-suspenders)

The dead params `session_memory` and `response` are dropped from the 17 signatures. Two
independent safety mechanisms run together:

1. **Update every caller in the same pass** (`main._execute_tool` + `agent/executor._call_tool`)
   so the live call graph is clean and forwards `player`/`speak` everywhere.
2. **KEEP `**kwargs` as a safety net** so that any un-updated or future caller that still passes
   a dropped param (`session_memory=`, `response=`) is silently absorbed instead of raising
   `TypeError`. This satisfies EC-4 ("dead params on un-updated callers must not break").

This is deliberately redundant. `**kwargs` alone would hide a missed caller (silent dispatch
breakage, R-1); updating callers alone would make any stray legacy kwarg fatal. Both together
give a clean call graph **and** a soft-landing for the transition. `response` is never read in
any body; `session_memory` is read only at `actions/weather_report.py:36-40` but **no caller
passes it** (runtime-dead) — dropping it deletes orphaned, never-executed code, so NFR-3
(flag-OFF == baseline) is preserved.

---

## 3. Module-by-Module Change Plan

### 3.1 `actions/*.py` — 17 action functions (FR-2.1, FR-2.2)

Normalize all 17 to `(parameters, player=None, speak=None, **kwargs) -> str`; drop
`session_memory`/`response`.

- **Already accept `speak` (7):** code_helper (`actions/code_helper.py:460`),
  dev_agent (`actions/dev_agent.py:495`), screen_process (`actions/screen_processor.py:225`),
  youtube_video (`actions/youtube_video.py:387`), file_processor (`actions/file_processor.py:777`),
  flight_finder (`actions/flight_finder.py:284`), game_updater (`actions/game_updater.py:926`).
  Change: add `**kwargs`, drop dead params if present.
- **Do NOT yet accept `speak` (10):** open_app (`actions/open_app.py:224`),
  web_search (`actions/web_search.py:81`), weather_action (`actions/weather_report.py:5`),
  send_message (`actions/send_message.py:233`), reminder (`actions/reminder.py:282`),
  computer_settings (`actions/computer_settings.py:590`),
  browser_control (`actions/browser_control.py:813`),
  file_controller (`actions/file_controller.py:470`),
  desktop_control (`actions/desktop.py:389`),
  computer_control (`actions/computer_control.py:353`).
  Change: add `speak=None` + `**kwargs`, drop dead params.
- **`actions/weather_report.py:36-40`** — remove the orphaned `session_memory` read block
  (runtime-dead; no caller passes it).

### 3.2 `main.py` `_execute_tool` callers (FR-2.3, FR-1.1=B1)

`_execute_tool` spans `main.py:813-897`. All 17 action funcs are module-top imports
(`main.py:75-91`); two are aliased:
- **weather_action** — imported `from actions.weather_report import weather_action` (`main.py:78`),
  called at `main.py:818`.
- **web_search_action** — imported `from actions.web_search import web_search as web_search_action`
  (`main.py:89`), called at `main.py:878`.

Changes:
- **Drop removed `response=`/`session_memory=` kwargs** at: open_app (`main.py:814`),
  send_message (`main.py:830`), reminder (`main.py:834`), youtube_video (`main.py:838`),
  screen_process (`main.py:843`), computer_settings (`main.py:847`).
- **Add `speak=self.speak`** to the 10 callers lacking it, **including** flight_finder
  (`main.py:896`) which is **B1** (live: `flight_finder(parameters=args, player=self.ui)` — drops
  `speak`). The 4 callers that already pass `speak` are unchanged: code_helper (`main.py:855`),
  dev_agent (`main.py:859`), file_processor (`main.py:884`), game_updater (`main.py:892`).
- Main path forwards `player=self.ui` (already present on the action callers) and, after the
  fix, `speak=self.speak` on all 17.

### 3.3 `agent/executor.py` `_call_tool` (FR-1.2=B2, FR-1.3=B3, FR-2.3)

Current: `_call_tool(tool, parameters, speak)` at `agent/executor.py:171`; every branch
hardcodes `player=None`; 17-branch chain ends at flight_finder (`agent/executor.py:240-241`);
the explicit `generated_code` branch is `agent/executor.py:233-237`; the silent fallback `else`
is `agent/executor.py:243-245`.

Changes:
1. **Add `player=None` to the signature** -> `_call_tool(tool, parameters, player=None, speak=None)`.
   Required or AC2 (player forwarded on BOTH paths) is unmeetable on the executor leg
   (analysis §1, FR-2.3 "CRITICAL GAP"). Production `execute()` leaves `player` at its default
   `None`; the **test** injects a mock player by calling/patching `_call_tool(player=mock)`.
2. **Forward `player=player, speak=speak` in EVERY branch**, replacing the hardcoded
   `player=None`.
3. **B2 — add the missing `file_processor` branch.** Live: `grep file_processor agent/executor.py`
   returns nothing; main has it. Add `from actions.file_processor import file_processor;
   return file_processor(parameters=parameters, player=player, speak=speak) or "Done."` and
   reconcile the branch set to the full 17 action tools (§ contract).
4. **B3 — replace the silent fallback `else` (`agent/executor.py:243-245`) with a raise**
   (`ValueError`/`KeyError`) for unknown tools. This removes the `print(...) +
   _run_generated_code(...)` fallback. EC-5: this is the single **intended** behaviour change vs.
   baseline — a bug correction, not a regression.
5. **KEEP the explicit `generated_code` branch (`agent/executor.py:233-237`).** It is LEGIT: it
   reads `description` from `parameters`, raises if absent, and calls `_run_generated_code`. Do
   NOT remove it; only the unknown-tool `else` is removed.
6. **Update the 2 internal call sites to pass `speak=` as a KEYWORD arg:**
   - `agent/executor.py:300` — currently `_call_tool(tool, params, speak)`. Positional `speak`
     would bind to the new `player` param and break dispatch. Change to
     `_call_tool(tool, params, speak=speak)`.
   - `agent/executor.py:340` — currently `res = _call_tool(fixed_step["tool"],
     fixed_step["parameters"], speak)`. Change to
     `_call_tool(fixed_step["tool"], fixed_step["parameters"], speak=speak)`.
   These are the **only 2** internal call sites; there is no third dynamic dispatch.

### 3.4 `agent/planner.py` `PLANNER_PROMPT` (FR-1.4=B4 + decision C)

`PLANNER_PROMPT` spans `agent/planner.py:22-132` and lists 16 tools; `file_processor` is absent
(`grep file_processor agent/planner.py` returns no match).

Change: **add `file_processor` ONLY** to the planner-visible tool list.

**Design Decision C — do NOT add `agent_task` to the planner.** Spec TAS-5's parenthetical
mentions "(and `agent_task`)", but the HARD constraint overrides it: `agent_task` is a **main-only
inline tool** with **no branch in `agent/executor._call_tool`**. After B3 lands (unknown tool ->
raise), a planner-emitted `agent_task` step routed through the executor would **raise**. Adding
`agent_task` to the planner would therefore manufacture a runtime failure. Decision: the planner
addition is `file_processor` **only**; `agent_task` stays out of `PLANNER_PROMPT`. This divergence
from TAS-5's parenthetical is intentional and recorded in ADR-001.

### 3.5 `memory/config_manager.py` — flag rail (FR-3.1, FR-3.2)

`memory/config_manager.py` (~55 lines) currently has `save_config(cfg)`, `load_api_keys()`,
`is_configured()` (checks `os_system`/`llm_model`/`stt_engine`/`tts_engine`) reading/merging into
`config/api_keys.json` (which does **not** currently exist — only `config/__init__.py`).

Add:
- **`_FLAG_SCHEMA`** — a dict mapping flag name -> default; **all defaults `False`**.
- **`get_flag(name, default=False) -> bool`** — reads the dedicated `config/flags.json`; returns
  the schema/`default` value if the file or key is absent.

See `## 4. Flag System Design` for the full schema and the `flags.json` justification. WO-0
**wires no gate** — the rail exists, nothing reads it to alter behaviour (NFR-3, AC4).

---

## 4. Flag System Design (FR-3)

### 4.1 The 5 registered flags (all default OFF)

WO-0 registers the flags WO-1..WO-5 will consume; **all default `False`**, **none wired to
behaviour** in WO-0:

| Flag | Reserved for | Default |
|---|---|---|
| `use_rust_wheel`        | WO-1 — Rust wheel (`mark_xl_rust`) | `False` |
| `use_tool_registry`     | WO-2 — tool registry / auto-gen tool list | `False` |
| `enable_security_gates` | WO-3 — security gates | `False` |
| `enable_memory_v2`      | WO-4 — memory work | `False` |
| `enable_loopguard`      | WO-5 — loopguard | `False` |

### 4.2 Decision — store flags in a DEDICATED `config/flags.json` (NOT in `api_keys.json`)

Rationale:
1. **Keeps flags OFF the secrets surface.** `config/api_keys.json` is the credentials file the
   Pre-Flight secrets/risk/scope gate scans. Storing inert config flags in a **separate**
   `config/flags.json` keeps the flag rail off that surface, so a flag write can never be
   mistaken for a secret and a secrets scan never trips on a flag.
2. **Does not perturb `load_api_keys()`.** Flags live in their own file, so `load_api_keys()` and
   `is_configured()` are byte-for-byte unaffected (NFR-3).
3. **Isolates inert config from credentials.** Different lifecycles, different review rules.

`get_flag(name, default=False)` reads `config/flags.json`; if the file is missing or the key is
absent it returns the `_FLAG_SCHEMA` default (or `default`). `config/flags.json` does **not**
currently exist (confirmed: only `config/__init__.py` + the absent `config/api_keys.json`), so
WO-0 introduces it; with no file present, every flag reads OFF — exactly the WO-0 default state.

---

## 5. Test Architecture (FR-4)

### 5.1 `conftest.py` fixtures

1. **Session-scoped `QApplication` singleton** — `from PyQt6.QtWidgets import QApplication`;
   create one instance per test session under `QT_QPA_PLATFORM=offscreen` (set before the import
   so headless legs do not require a display). UI-thread tests use a **real** `QApplication`, not a
   mock (FR-4.1).
2. **`qtest` helper** — `from PyQt6.QtTest import QTest` exposed as a fixture for UI-thread driving.
3. **Ollama mock fixture** — monkeypatch **all three** entry points in `core/llm_client.py` so no
   test hits a live socket: `call_llm_text` (`core/llm_client.py:331`, primary), `call_llm`
   (`core/llm_client.py:229`), `call_llm_stream` (`core/llm_client.py:488`). Patch the
   module-level functions.
4. **Thread fixtures** — helpers for the inline-tool tests (esp. `shutdown_jarvis`'s daemon
   thread) so threads are joined/contained and never kill the runner.

### 5.2 Parametrized dual-path routing test (TAS-1 / AC2) — NON-UNIFORM patch matrix (VERBATIM)

This matrix is the **single source of truth** for the dual-path test and the **#1 correctness
trap**: a naive `patch("actions.X.X")` gives FALSE GREENS on the **main** leg (main binds the func
at module top, so the executor-style import path is the wrong patch target there) and misses the
**two aliases**. Encode this table directly as the test's parametrization.

- **main leg** -> patch the symbol **as bound in `main`** (module-top imports). All bind as
  `main.<func>` **except** two aliases: weather_report's func is bound as `main.weather_action`;
  web_search's func is bound as `main.web_search_action`.
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

**Test body:** for each row, patch both entry points with a `MagicMock`, dispatch through
`main._execute_tool` **and** `agent/executor._call_tool` with a mock `player` and mock `speak`, and
assert each patched entry point was called **once** with `player=<mock>` and `speak=<mock>`
forwarded. Routing/wiring assertion only — **no live side effects** (L-2).

### 5.3 Three inline-tool tests (main-only) (TAS-6 / FR-4.3)

`save_memory`, `agent_task`, `shutdown_jarvis` are main-only; dispatch each through
`main._execute_tool`:
- **save_memory** — `__SILENT__` handler (main: ~800-809); assert the inline handler runs.
- **agent_task** — task-queue handler (main: ~862-875); assert it enqueues, not routed to actions.
- **shutdown_jarvis** — spawns a daemon thread -> `os._exit(0)` (main: ~899-909). **Mock
  `os._exit`** so the test process is not killed. Patch target: `main.os._exit` (the call is in a
  nested closure at ~main:906, so patch the module-level `os._exit` as seen from `main`). EC-3/NFR-8.

### 5.4 Four regression tests (TAS-2..TAS-5 / AC3)

Each must fail on pre-fix code and pass on fixed code:
- **TAS-2 (B1)** — main-path dispatch of `flight_finder` forwards a tracked `speak` (fails pre-fix
  where `speak` is dropped at `main.py:896`).
- **TAS-3 (B2)** — `agent/executor._call_tool("file_processor", ...)` dispatches to
  `actions.file_processor` (fails pre-fix where the branch is missing).
- **TAS-4 (B3)** — `agent/executor._call_tool` with an unknown tool name **raises** (fails pre-fix
  where it silently calls `_run_generated_code`).
- **TAS-5 (B4)** — `PLANNER_PROMPT` contains `file_processor` (fails pre-fix where it is omitted).
  Per Decision C, the test asserts `file_processor` only; it does **not** assert `agent_task` in the
  planner.

### 5.5 AC -> Test map

| AC | Statement | Verifying tests | FR coverage |
|---|---|---|---|
| **AC1** | CI green on all matrix legs + non-blocking Rust placeholder + `import mark_xl_rust` smoke skips clean | TAS-8 (CI workflow) | FR-5 |
| **AC2** | All 17 action tools dispatch through BOTH `main._execute_tool` and `executor._call_tool` with `player`+`speak` forwarded | TAS-1 (parametrized dual-path) | FR-1.1, FR-1.2, FR-2, FR-4.2 |
| **AC3** | B1..B4 fixed; each has a dedicated regression test failing pre-fix / passing post-fix | TAS-2, TAS-3, TAS-4, TAS-5 | FR-1 |
| **AC4** | flag-OFF == baseline: schema exists, `get_flag` returns OFF by default, nothing in WO-0 is gated | TAS-7 (flag default-OFF) | FR-3, NFR-3 |

---

## 6. CI Design (FR-5)

`.github/workflows/*.yml`:

1. **Test matrix (required / green on every leg):**
   - `os: [macos-14, macos-13, windows-latest, ubuntu-latest]`
     x `python: ["3.11", "3.12", "3.13"]`.
   - `env: QT_QPA_PLATFORM=offscreen` on **all** legs (headless Qt).
   - On `ubuntu-latest`: `apt-get install -y libegl1` (PyQt6 EGL dependency). `xvfb` is the
     **fallback** only if a windowed test needs it; `offscreen` should avoid xvfb — note it but do
     not add it unless a leg actually fails without a display.
   - Run: `QT_QPA_PLATFORM=offscreen pytest -q`. All legs REQUIRED.
2. **Rust wheel placeholder job (non-blocking stub; WO-1 activates):**
   - `dtolnay/rust-toolchain@1.88` + maturin + sccache.
   - `continue-on-error: true` and **NOT** a required check — it must never block the WO-0 matrix.
3. **`mark_xl_rust` import-smoke job:** `python -c` guarded by `pytest.importorskip("mark_xl_rust")`
   (or a `try/except ImportError` that exits 0) — **skips cleanly** when the wheel is absent (EC-2).

NFR notes recorded for WO-1: **abi3=true** (one wheel/platform, Py3.11+) for the wheel build;
**rlimit is Unix-only** and any future use must be gated (EC-1/NFR-4) — WO-0 introduces no rlimit
use.

---

## 7. Risk & Mitigation

| ID | Sev | Risk | Mitigation |
|---|---|---|---|
| **R-1** | HIGH | Signature normalization + caller updates touch **17 action files + both dispatchers**; a missed caller silently breaks dispatch | **`**kwargs` absorption** (a stray dropped kwarg lands soft, not fatal) **+** the **dual-path routing test (TAS-1)** which exercises every tool through both paths and would catch a non-forwarded `player`/`speak` |
| **R-3** | MED | **Non-uniform patch matrix** — naive `patch("actions.X.X")` false-greens the main leg and misses the two aliases (`main.weather_action`, `main.web_search_action`) | The **§5.2 verbatim matrix as the single test source of truth**; the test parametrizes from that table, distinguishing the main-bound name from the executor `actions.<module>.<func>` target |
| **R-5** | LOW | Headless Qt flakiness across 4 OS x 3 Py legs (`QApplication` needs a display; Windows lacks `rlimit`) | **`QT_QPA_PLATFORM=offscreen`** on all legs + a **real session-scoped `QApplication`** fixture + `libegl1` on Linux; rlimit gated per NFR-4/EC-1 (WO-0 introduces none) |

---

## 8. Lessons Applied

- **"17 not 18" — count discipline.** The intake's "18 tools" is a confirmed arithmetic miscount
  (10 missing `speak` + 7 with `speak` = 17 action files). This design fixes the count at 17
  verbatim and separates the 3 inline tools as main-only — never re-derive the count.
- **Dual-path testing is a routing/wiring assertion, not live execution.** Patch each entry point
  with a `MagicMock` and assert `player`/`speak` forwarding; never trigger real side effects
  (several tools mutate the system; `shutdown_jarvis` calls `os._exit(0)`).
- **Do NOT flag the bug fixes.** B1..B4 + signature normalization are unconditional corrections;
  the flag rail is built but gates nothing in WO-0 (NFR-3 / AC4).
- **The non-uniform patch matrix is the top trap.** Main binds funcs at module top (with two
  aliases); the executor lazy-imports `actions.<module>.<func>`. Patch each leg at the surface it
  actually resolves — the §5.2 matrix is the SoT.
- **The planner feeds the executor — no standalone `agent_task` in the planner.** Add
  `file_processor` only to `PLANNER_PROMPT`; `agent_task` has no executor branch, so a
  planner-emitted `agent_task` step would raise post-B3 (Decision C).

---

## Banned-Stack Note

`torch`/`transformers` already exist in the **upstream** STT/TTS code (`main.py`, `core/stt.py`,
`core/tts.py`, `core/installer.py`). These are **PRE-EXISTING**, not introduced by WO-0. The WO-0
design **introduces no new** `torch`/`transformers`/`DSPy`/`GEPA` and **no** OpenJarvis
`ToolExecutor`/`EventBus` imports (NFR-2, constitution "Banned"). The distinction is explicit:
pre-existing upstream ML deps are out of scope and untouched; WO-0 adds none.

---

## Scope

**WO-0 only.** This design covers the 4 bug fixes (B1..B4), the 17-signature normalization +
caller updates, the inert default-OFF feature-flag rail (`config/flags.json`), the from-zero
pytest harness, and the GitHub Actions CI matrix with a non-blocking Rust placeholder. It does
**NOT** include the registry refactor (WO-2), security gates (WO-3), memory work (WO-4), loopguard
(WO-5), or the real Rust wheel build (WO-1) — those are out of scope.
