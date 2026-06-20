# Analysis — WO-0 (Foundations)

> Spec Kit `clarify` -> `analyze` artifact for WO-0 of the Mark-XL fork
> (`maseko-lucky-9/Mark-XL`, branch `wo-0-foundations`). Validates `pipeline/spec.md`
> against the live code (file:line cited) for SRS quality + technical feasibility before Design.

## 0. Method
Every FR checked against the live code on `wo-0-foundations`, not the spec's prose.
Files read: all 17 `actions/*.py`, `agent/executor.py`, `agent/planner.py`, `main.py`
(`_execute_tool` + module-top imports), `core/llm_client.py`, `memory/config_manager.py`,
`requirements.txt`. Greps: every `actions.*` call site, every `response`/`session_memory`
reference, all 17 action signatures.

## 1. Feasibility Verdict — per FR
- FR-1.1 (B1 flight_finder drops speak): FEASIBLE — main.py:896 passes no speak; flight_finder.py:284 accepts speak=None; executor already forwards speak (executor.py:241). Fix: add speak=self.speak at main:896.
- FR-1.2 (B2 executor missing file_processor): FEASIBLE — executor._call_tool (171-245) has NO file_processor branch; main HAS it (881). Fix: add branch + reconcile to full 17-tool set.
- FR-1.3 (B3 silent generated-code fallback): FEASIBLE — executor.py:243-245 else: calls _run_generated_code for unknown tools. Fix: raise. KEEP the explicit generated_code branch (233-237).
- FR-1.4 (B4 planner lists 16): FEASIBLE — PLANNER_PROMPT (planner.py:22-132) lists 16 tools; file_processor absent. Fix: add file_processor (agent_task: see A-1, defer to Design).
- FR-2.1 (uniform sig, 17 tools): FEASIBLE (10+7=17). WITH speak (7): code_helper:460, dev_agent:495, screen_process:225, youtube_video:387, file_processor:777, flight_finder:284, game_updater:926. WITHOUT (10): open_app:224, web_search:81, weather_action(weather_report.py:5), send_message:233, reminder:282, computer_settings:590, browser_control:813, file_controller:470, desktop_control(desktop.py:389), computer_control:353.
- FR-2.2 (drop dead session_memory + response): PASS with note — response never read in any body; session_memory read ONLY at weather_report.py:36-40 but NO caller passes it (runtime-dead). Dropping deletes that orphaned block; never executed -> NFR-3 preserved. Not a deviation.
- FR-2.3 (update callers in main + executor): FEASIBLE — CRITICAL GAP: executor._call_tool signature is (tool, parameters, speak) — NO player param (executor.py:171); every branch hardcodes player=None. AC2/TAS-1 need player+speak on BOTH paths -> executor MUST grow player=None or AC2 unmeetable on the executor leg.
- FR-3.1/3.2 (flag schema + get_flag, default OFF): FEASIBLE — config_manager.py (55 lines) has no flags. Add _FLAG_SCHEMA (all False) + get_flag(name). WO-0 gates nothing.
- FR-4.1 (conftest: QApplication/QTest, Ollama mock, threads): FEASIBLE — Qt=PyQt6 (requirements.txt:11). from PyQt6.QtWidgets import QApplication; from PyQt6.QtTest import QTest. Session-scoped singleton; headless CI QT_QPA_PLATFORM=offscreen.
- FR-4.2 (parametrized dual-path, 17 tools): FEASIBLE — non-uniform patch points; see R-3 / §4.
- FR-4.3 (3 inline tests; mock shutdown): FEASIBLE — save_memory (main:800-809 __SILENT__), agent_task (main:862-875), shutdown_jarvis (main:899-909 -> os._exit(0)). Mock target: os._exit (main:906 nested closure).
- FR-4.4 (4 regression tests): FEASIBLE — each bug has a discrete observable pre-fix failure.
- FR-5.1 (CI matrix): FEASIBLE — pure-Python; PyQt6 wheels for all 3 OS x Py3.11-3.13. Headless Qt needs offscreen + (Linux) libegl1/xvfb.
- FR-5.2 (Rust placeholder, non-blocking): FEASIBLE — stub job, continue-on-error / not required-check.
- FR-5.3 (import mark_xl_rust skips clean): FEASIBLE — pytest.importorskip("mark_xl_rust") skips green when wheel absent.

Overall: every FR FEASIBLE. No infeasibility, no critical unresolved ambiguity.

## 2. SRS-Quality Checklist (8 axes)
Complete PASS (gap closed: executor player param surfaced) - Consistent PASS - Unambiguous PASS (A-1 resolved) - Verifiable PASS (FR->TAS->AC) - Modifiable PASS (§6 single source) - Prioritized PASS - Testable PASS - Relevant PASS. SRS verdict: PASS on all 8 axes.

## 3. Caller-Update List (for Design / Development)
main.py _execute_tool (813-897):
- Drop removed response=/session_memory= kwargs at: open_app(814), send_message(830), reminder(834), youtube_video(838), screen_process(843), computer_settings(847).
- Add speak=self.speak to the 10 callers lacking it, incl. flight_finder(896)=B1. (code_helper:855, dev_agent:859, file_processor:884, game_updater:892 already pass speak.)
- Name bindings (load-bearing for tests): weather_action (main:78/818), web_search_action (imported `as` main:89, called 878).
agent/executor.py _call_tool (171-245):
- ADD player=None param; pass it at the 3 internal call sites (executor.py:300,340, dispatch). Forward player=player (was hardcoded None) + speak=speak in every branch.
- Add file_processor branch (B2). Replace else: silent fallback (243-245) with a raise (B3). KEEP generated_code branch (233-237).
agent/planner.py PLANNER_PROMPT (B4): add a file_processor tool block. (agent_task: see A-1.)

## 4. Per-Tool Dual-Path Patch Matrix (FR-4.2 / TAS-1 directive)
Routing/wiring assertion — no live side effects. Patch targets NOT uniform:
- main leg -> patch symbol AS BOUND in `main` (imports main:75-91). Two mismatches: weather_report -> main.weather_action; web_search -> main.web_search_action.
- executor leg -> patch actions.<module>.<func> (lazy import inside _call_tool).
tool -> module.func: open_app->open_app.open_app; web_search->web_search.web_search; weather_report->weather_report.weather_action; send_message->send_message.send_message; reminder->reminder.reminder; youtube_video->youtube_video.youtube_video; screen_process->screen_processor.screen_process; computer_settings->computer_settings.computer_settings; browser_control->browser_control.browser_control; file_controller->file_controller.file_controller; desktop_control->desktop.desktop_control; code_helper->code_helper.code_helper; dev_agent->dev_agent.dev_agent; computer_control->computer_control.computer_control; game_updater->game_updater.game_updater; flight_finder->flight_finder.flight_finder; file_processor->file_processor.file_processor.

## 5. Resolved Ambiguities (clarify)
- A-1 (preference-only) — add agent_task to PLANNER_PROMPT? CORRECTED: planner feeds the EXECUTOR (executor.py:15,267), not the main voice loop. executor._call_tool has NO agent_task branch -> a planner-emitted agent_task step, post-B3, would RAISE. TAS-5 hard-asserts only file_processor. DECISION (unattended): add ONLY file_processor to the planner. FLAG agent_task to Design — add to planner ONLY if an executor agent_task branch is added, or instruct the planner never to emit it standalone. Do not silently add.
- A-2 (resolved by code) — response is dead. Drop safe.
- A-3 (resolved by code) — session_memory runtime-dead. Drop safe, baseline-preserving.
- No critical user-preference ambiguity required AskUserQuestion.

## 6. Risks / Edge Cases for Design
- R-1 (medium) — executor _call_tool has no player param -> AC2/TAS-1 unmeetable on executor leg without player=None. Production execute() still passes player=None; the TEST injects a mock player.
- R-2 (low) — caller-update vs **kwargs: recommended = update callers AND keep **kwargs.
- R-3 (medium) — non-uniform patch points (§4). Naive patch("actions.X.X") gives false greens on main leg + misses weather_action/web_search_action.
- R-4 (low) — weather_action has neither speak nor response today. Normalization adds speak+**kwargs, drops session_memory, deletes the dead set_last_search block.
- R-5 (medium) — headless Qt on CI needs QT_QPA_PLATFORM=offscreen (+Linux libegl1/xvfb).
- R-6 (low) — shutdown_jarvis: os._exit(0) in nested closure (main:906). Patch module-level os._exit.
- R-7 (low) — generated_code is a real tool. B3 removes ONLY the else fallback (243-245); keep the explicit branch (233-237).
- R-8 (low, carried) — "18 tools" miscount. §6 spec verbatim truth: 17 action-backed (10+7), 3 inline main-only.

## 7. Confirmed Design-Phase Facts
- Qt: PyQt6 (requirements.txt:11). from PyQt6.QtWidgets import QApplication; from PyQt6.QtTest import QTest. Headless: QT_QPA_PLATFORM=offscreen.
- Ollama mock seam: core/llm_client.py. Primary call_llm_text (llm_client.py:331). ALSO mock call_llm (229) and call_llm_stream (488) so no test hits a live socket. Monkeypatch the functions.
- 17 action tools confirmed. desktop_control<-actions/desktop.py; weather_report tool<-weather_action.

## Lessons Applied
- L-1 ("17 not 18") — re-confirmed via ls actions/*.py -> 17 + 10/7 speak audit.
- L-2 (dual-path = routing assertion) — §4 patches entry points + asserts forwarding; os._exit mock pinned.
- L-3 (don't flag bug fixes) — FR-3 builds rail only; all fixes + executor player addition unconditional; NFR-3 holds.
- L-4 (inline tools out of dual-path) — save_memory/agent_task/shutdown_jarvis main-only, excluded from §4.
- L-5 (B3 = correction, not feature) — keep the legit generated_code branch.

## Verdict
SRS: PASS (8/8). Feasibility: every FR FEASIBLE; one must-fix surfaced for Design (executor player, R-1) — not a spec defect. Critical ambiguities: none unresolved. Scope: on track.
