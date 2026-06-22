"""
MARK XL — Agent Executor
Replaces google.generativeai with local Ollama via core.llm_client.
"""
import json
import re
import sys
import threading
import subprocess
import tempfile
import os
from pathlib import Path
from typing import Callable

from agent.planner       import create_plan, replan
from agent.error_handler import analyze_error, generate_fix, ErrorDecision
from core.llm_client     import call_llm_text


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR = get_base_dir()


# ---------------------------------------------------------------------------
# Code generation helper (replaces _run_generated_code with Gemini)
# ---------------------------------------------------------------------------

def _run_generated_code(description: str, speak: Callable | None = None, *, scan_enabled: bool = False, audit=None) -> str:
    if speak:
        speak("Writing custom code for this task, sir.")

    home      = Path.home()
    desktop   = home / "Desktop"
    downloads = home / "Downloads"
    documents = home / "Documents"

    if not desktop.exists():
        try:
            import winreg
            key     = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders",
            )
            desktop = Path(winreg.QueryValueEx(key, "Desktop")[0])
        except Exception:
            pass

    system = (
        "You are an expert Python developer. "
        "Write clean, complete, working Python code. "
        "Use standard library + common packages. "
        "Install missing packages with subprocess + pip if needed. "
        "Return ONLY the Python code. No explanation, no markdown, no backticks.\n\n"
        f"SYSTEM PATHS:\n"
        f"  Desktop   = r'{desktop}'\n"
        f"  Downloads = r'{downloads}'\n"
        f"  Documents = r'{documents}'\n"
        f"  Home      = r'{home}'\n"
    )
    prompt = f"Write Python code to accomplish this task:\n\n{description}"

    try:
        code = call_llm_text(prompt, system=system)
        code = re.sub(r"```(?:python)?", "", code).strip().rstrip("`").strip()

        # WO-3: injection scan before subprocess.run (scan_enabled only when flag-ON)
        if scan_enabled:
            from core import mark_xl_rust_adapter as _adapter
            try:
                _verdict = _adapter.run_in_executor(_adapter.injection_scan, code).result(timeout=10)
            except Exception:
                _verdict = {"is_clean": True, "threat_level": "low", "findings": []}
            if _verdict.get("is_clean") is False and _verdict.get("threat_level") == "high":
                if audit is not None:
                    try:
                        audit.write(
                            "generated_code",
                            {"description": description},
                            "__BLOCKED_INJECTION__",
                            confirm_required=True,
                            approved=False,
                        )
                    except Exception:
                        pass
                raise RuntimeError("Blocked: generated code failed injection scan.")

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as f:
            f.write(code)
            tmp_path = f.name

        print(f"[Executor] 🐍 Running generated code: {tmp_path}")

        result = subprocess.run(
            [sys.executable, tmp_path],
            capture_output=True, text=True,
            timeout=120, cwd=str(Path.home()),
        )

        try:
            os.unlink(tmp_path)
        except Exception:
            pass

        output = result.stdout.strip()
        error  = result.stderr.strip()

        if result.returncode == 0 and output:
            return output
        elif result.returncode == 0:
            return "Task completed successfully."
        elif error:
            raise RuntimeError(f"Code error: {error[:400]}")
        return "Completed."

    except subprocess.TimeoutExpired:
        raise RuntimeError("Generated code timed out after 120 seconds.")
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"Generated code failed: {e}")


# ---------------------------------------------------------------------------
# Context injection
# ---------------------------------------------------------------------------

def _detect_language(text: str) -> str:
    try:
        return call_llm_text(
            f"What language is this text written in? "
            f"Reply with ONLY the language name in English (e.g. Turkish, English, French).\n\n"
            f"Text: {text[:200]}"
        ).strip()
    except Exception:
        return "English"


def _translate_to_goal_language(content: str, goal: str) -> str:
    if not goal:
        return content
    try:
        target_lang = _detect_language(goal)
        print(f"[Executor] 🌐 Translating to: {target_lang}")
        prompt = (
            f"You are a professional translator. "
            f"Translate the following text into {target_lang}.\n"
            f"IMPORTANT:\n"
            f"- Translate EVERYTHING, leave nothing in English\n"
            f"- Keep all facts, numbers, and data intact\n"
            f"- Keep the structure and formatting\n"
            f"- Output ONLY the translated text, nothing else\n\n"
            f"Text to translate:\n{content[:4000]}"
        )
        translated = call_llm_text(prompt)
        print(f"[Executor] ✅ Translation done ({target_lang})")
        return translated
    except Exception as e:
        print(f"[Executor] ⚠️ Translation failed: {e}")
        return content


def _inject_context(params: dict, tool: str, step_results: dict, goal: str = "") -> dict:
    if not step_results:
        return params
    params = dict(params)
    if tool == "file_controller" and params.get("action") in ("write", "create_file"):
        content = params.get("content", "")
        if not content or len(content) < 50:
            all_results = [
                v for v in step_results.values()
                if v and len(v) > 100 and v not in ("Done.", "Completed.")
            ]
            if all_results:
                combined   = "\n\n---\n\n".join(all_results)
                translated = _translate_to_goal_language(combined, goal)
                params["content"] = translated
                print("[Executor] 💉 Injected + translated content")
    return params


# ---------------------------------------------------------------------------
# Tool routing
# ---------------------------------------------------------------------------

def _call_tool(tool: str, parameters: dict, player=None, speak: Callable | None = None) -> str:
    from memory.config_manager import get_flag

    # WO-4: enable_memory_v2 dispatch gate for the executor path.
    # Memory context is owned by main.py::_build_system_prompt; this read pins
    # the executor as a sanctioned dispatch site so future inline memory
    # enrichment (P2+) can branch here without introducing a new get_flag caller.
    _memory_v2_on = get_flag("enable_memory_v2")  # noqa: F841 — WO-4 P2 dispatch-site pin

    if get_flag('use_tool_registry'):
        sec_on = get_flag('enable_security_gates')
        loopguard_on = get_flag('enable_loopguard')

        # WO-3 lazy ledger + SSRF cfg helpers (only constructed when sec_on)
        _wo3_ledger = [None]

        def _get_ledger():
            if _wo3_ledger[0] is None:
                from core.audit import AuditLedger
                import os
                base = Path(__file__).resolve().parent.parent
                data_dir = base / "data"
                data_dir.mkdir(parents=True, exist_ok=True)
                _wo3_ledger[0] = AuditLedger((data_dir / "audit.db").as_posix())
            return _wo3_ledger[0]

        def _ssrf_cfg() -> bool:
            from memory.config_manager import get_security_config
            return bool(get_security_config("security_ssrf_allow_local_nav", False))

        _no_confirm = lambda tool, preview, timeout=30: False

        # generated_code is its OWN explicit branch — NOT a registered tool, NOT unknown (WO-3 carry-forward)
        if tool == "generated_code":
            description = parameters.get("description", "")
            if not description:
                raise ValueError("generated_code requires a 'description' parameter.")
            return _run_generated_code(
                description, speak=speak,
                scan_enabled=sec_on,
                audit=_get_ledger() if sec_on else None,
            )

        if loopguard_on:
            from core import loop_guard
            mi, mp, pb = loop_guard.load_knobs()
            loop_guard.get_guard(True, max_identical=mi, max_ping_pong=mp, poll_budget=pb)
            reason = loop_guard.check(tool, parameters)
            if reason is not None:
                loop_guard.signal_loop(reason)
                return f"Loop detected: {reason}"

        from core.tool_registry import dispatch
        if sec_on:
            from core.security_gate import run_gated
            confirm_fn = player.ask_user_confirm if player else _no_confirm
            result = run_gated(tool, parameters, player=player, speak=speak,
                               confirm_fn=confirm_fn,
                               audit=_get_ledger(), ssrf_local_nav=_ssrf_cfg())
        else:
            result = dispatch(tool, parameters, player=player, speak=speak)
        if tool == "screen_process":
            return result if isinstance(result, str) else "Screen captured and analyzed."
        return result or "Done."

    else:
        # VERBATIM baseline 17-arm ladder — DO NOT REFACTOR
        if tool == "open_app":
            from actions.open_app import open_app
            return open_app(parameters=parameters, player=player, speak=speak) or "Done."

        elif tool == "web_search":
            from actions.web_search import web_search
            return web_search(parameters=parameters, player=player, speak=speak) or "Done."

        elif tool == "game_updater":
            from actions.game_updater import game_updater
            return game_updater(parameters=parameters, player=player, speak=speak) or "Done."

        elif tool == "browser_control":
            from actions.browser_control import browser_control
            return browser_control(parameters=parameters, player=player, speak=speak) or "Done."

        elif tool == "file_controller":
            from actions.file_controller import file_controller
            return file_controller(parameters=parameters, player=player, speak=speak) or "Done."

        elif tool == "code_helper":
            from actions.code_helper import code_helper
            return code_helper(parameters=parameters, player=player, speak=speak) or "Done."

        elif tool == "dev_agent":
            from actions.dev_agent import dev_agent
            return dev_agent(parameters=parameters, player=player, speak=speak) or "Done."

        elif tool == "screen_process":
            from actions.screen_processor import screen_process
            result = screen_process(parameters=parameters, player=player, speak=speak)
            return result if isinstance(result, str) else "Screen captured and analyzed."

        elif tool == "send_message":
            from actions.send_message import send_message
            return send_message(parameters=parameters, player=player, speak=speak) or "Done."

        elif tool == "reminder":
            from actions.reminder import reminder
            return reminder(parameters=parameters, player=player, speak=speak) or "Done."

        elif tool == "youtube_video":
            from actions.youtube_video import youtube_video
            return youtube_video(parameters=parameters, player=player, speak=speak) or "Done."

        elif tool == "weather_report":
            from actions.weather_report import weather_action
            return weather_action(parameters=parameters, player=player, speak=speak) or "Done."

        elif tool == "computer_settings":
            from actions.computer_settings import computer_settings
            return computer_settings(parameters=parameters, player=player, speak=speak) or "Done."

        elif tool == "desktop_control":
            from actions.desktop import desktop_control
            return desktop_control(parameters=parameters, player=player, speak=speak) or "Done."

        elif tool == "computer_control":
            from actions.computer_control import computer_control
            return computer_control(parameters=parameters, player=player, speak=speak) or "Done."

        elif tool == "generated_code":
            description = parameters.get("description", "")
            if not description:
                raise ValueError("generated_code requires a 'description' parameter.")
            return _run_generated_code(description, speak=speak)

        elif tool == "flight_finder":
            from actions.flight_finder import flight_finder
            return flight_finder(parameters=parameters, player=player, speak=speak) or "Done."

        elif tool == "file_processor":
            from actions.file_processor import file_processor
            return file_processor(parameters=parameters, player=player, speak=speak) or "Done."

        else:
            raise ValueError(f"Unknown tool: {tool}")


# ---------------------------------------------------------------------------
# AgentExecutor
# ---------------------------------------------------------------------------

class AgentExecutor:

    MAX_REPLAN_ATTEMPTS = 2

    def execute(
        self,
        goal:        str,
        speak:       Callable | None        = None,
        cancel_flag: threading.Event | None = None,
    ) -> str:
        print(f"\n[Executor] 🎯 Goal: {goal}")

        replan_attempts = 0
        completed_steps: list = []
        step_results:    dict = {}

        # Reset LoopGuard state at the start of every execute() call so that
        # background tasks never inherit trip counters from a previous execution.
        from memory.config_manager import get_flag
        if get_flag('enable_loopguard'):
            from core import loop_guard
            loop_guard.reset()

        # Consumer-gate: build registry-based planner prompt when flag is ON.
        # get_flag is imported here (never in planner.py — zero references there).
        if get_flag('use_tool_registry'):
            from core.tool_registry import build_planner_tool_block
            from agent.planner import build_planner_prompt
            _planner_system = build_planner_prompt(build_planner_tool_block())
        else:
            from agent.planner import PLANNER_PROMPT
            _planner_system = PLANNER_PROMPT

        plan = create_plan(goal, system=_planner_system)

        while True:
            steps = plan.get("steps", [])
            if not steps:
                msg = "I couldn't create a valid plan for this task, sir."
                if speak: speak(msg)
                return msg

            success      = True
            failed_step  = None
            failed_error = ""

            for step in steps:
                if cancel_flag and cancel_flag.is_set():
                    if speak: speak("Task cancelled, sir.")
                    return "Task cancelled."

                step_num = step.get("step", "?")
                tool     = step.get("tool", "generated_code")
                desc     = step.get("description", "")
                params   = step.get("parameters", {})
                params   = _inject_context(params, tool, step_results, goal=goal)

                print(f"\n[Executor] ▶️ Step {step_num}: [{tool}] {desc}")

                attempt = 1
                step_ok = False

                while attempt <= 3:
                    if cancel_flag and cancel_flag.is_set():
                        break
                    try:
                        result = _call_tool(tool, params, speak=speak)
                        step_results[step_num] = result
                        completed_steps.append(step)
                        print(f"[Executor] ✅ Step {step_num} done: {str(result)[:100]}")
                        step_ok = True
                        break

                    except Exception as e:
                        error_msg = str(e)
                        print(f"[Executor] ❌ Step {step_num} attempt {attempt} failed: {error_msg}")

                        recovery = analyze_error(step, error_msg, attempt=attempt)
                        decision = recovery["decision"]
                        user_msg = recovery.get("user_message", "")

                        if speak and user_msg:
                            speak(user_msg)

                        if decision == ErrorDecision.RETRY:
                            attempt += 1
                            import time; time.sleep(2)
                            continue

                        elif decision == ErrorDecision.SKIP:
                            print(f"[Executor] ⏭️ Skipping step {step_num}")
                            completed_steps.append(step)
                            step_ok = True
                            break

                        elif decision == ErrorDecision.ABORT:
                            msg = f"Task aborted, sir. {recovery.get('reason', '')}"
                            if speak: speak(msg)
                            return msg

                        else:  # REPLAN
                            fix_suggestion = recovery.get("fix_suggestion", "")
                            if fix_suggestion and tool != "generated_code":
                                try:
                                    fixed_step = generate_fix(step, error_msg, fix_suggestion)
                                    if speak: speak("Trying an alternative approach, sir.")
                                    res = _call_tool(
                                        fixed_step["tool"],
                                        fixed_step["parameters"],
                                        speak=speak,
                                    )
                                    step_results[step_num] = res
                                    completed_steps.append(step)
                                    step_ok = True
                                    break
                                except Exception as fix_err:
                                    print(f"[Executor] ⚠️ Fix failed: {fix_err}")

                            failed_step  = step
                            failed_error = error_msg
                            success      = False
                            break

                if not step_ok and not failed_step:
                    failed_step  = step
                    failed_error = "Max retries exceeded"
                    success      = False

                if not success:
                    break

            if success:
                return self._summarize(goal, completed_steps, speak)

            if replan_attempts >= self.MAX_REPLAN_ATTEMPTS:
                msg = f"Task failed after {replan_attempts} replan attempts, sir."
                if speak: speak(msg)
                return msg

            if speak: speak("Adjusting my approach, sir.")
            replan_attempts += 1
            plan = replan(goal, completed_steps, failed_step, failed_error, system=_planner_system)

    def _summarize(self, goal: str, completed_steps: list, speak: Callable | None) -> str:
        fallback  = f"All done, sir. Completed {len(completed_steps)} steps for: {goal[:60]}."
        steps_str = "\n".join(f"- {s.get('description', '')}" for s in completed_steps)
        prompt    = (
            f'User goal: "{goal}"\n'
            f"Completed steps:\n{steps_str}\n\n"
            "Write a single natural sentence summarising what was accomplished. "
            "Address the user as 'sir'. Be direct and positive."
        )
        try:
            summary = call_llm_text(prompt)
            if summary:
                if speak: speak(summary)
                return summary
        except Exception:
            pass
        if speak: speak(fallback)
        return fallback
