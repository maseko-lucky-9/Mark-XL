"""
MARK XL — Task Planner
Replaces google.generativeai with local Ollama via core.llm_client.
"""
import json
import re
import sys
from pathlib import Path

from core.llm_client import call_llm_text


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR = get_base_dir()


PLANNER_PROMPT = """You are the planning module of MARK XL, a personal AI assistant.
Your job: break any user goal into a sequence of steps using ONLY the tools listed below.

ABSOLUTE RULES:
- NEVER use generated_code or write Python scripts. It does not exist.
- NEVER reference previous step results in parameters. Every step is independent.
- Use web_search for ANY information retrieval, research, or current data.
- Use file_controller to save content to disk.
- Max 5 steps. Use the minimum steps needed.

AVAILABLE TOOLS AND THEIR PARAMETERS:

open_app
  app_name: string (required)

web_search
  query: string (required) — write a clear, focused search query
  mode: "search" or "compare" (optional, default: search)
  items: list of strings (optional, for compare mode)
  aspect: string (optional, for compare mode)

game_updater
  action: "update" | "install" | "list" | "download_status" | "schedule" (required)
  platform: "steam" | "epic" | "both" (optional, default: both)
  game_name: string (optional)
  app_id: string (optional)
  shutdown_when_done: boolean (optional)

browser_control
  action: "go_to" | "search" | "click" | "type" | "scroll" | "get_text" | "press" | "close" (required)
  url: string (for go_to)
  query: string (for search)
  text: string (for click/type)
  direction: "up" | "down" (for scroll)

file_controller
  action: "write" | "create_file" | "read" | "list" | "delete" | "move" | "copy" | "find" | "disk_usage" (required)
  path: string — use "desktop" for Desktop folder
  name: string — filename
  content: string — file content (for write/create_file)

computer_settings
  action: string (required)
  description: string — natural language description
  value: string (optional)

computer_control
  action: "type" | "click" | "hotkey" | "press" | "scroll" | "screenshot" | "screen_find" | "screen_click" (required)
  text: string (for type)
  x, y: int (for click)
  keys: string (for hotkey, e.g. "ctrl+c")
  key: string (for press)
  direction: "up" | "down" (for scroll)
  description: string (for screen_find/screen_click)

screen_process
  text: string (required) — what to analyze or ask about the screen
  angle: "screen" | "camera" (optional)

send_message
  receiver: string (required)
  message_text: string (required)
  platform: string (required)

reminder
  date: string YYYY-MM-DD (required)
  time: string HH:MM (required)
  message: string (required)

desktop_control
  action: "wallpaper" | "organize" | "clean" | "list" | "task" (required)
  path: string (optional)
  task: string (optional)

youtube_video
  action: "play" | "summarize" | "trending" (required)
  query: string (for play)

weather_report
  city: string (required)

flight_finder
  origin: string (required)
  destination: string (required)
  date: string (required)

code_helper
  action: "write" | "edit" | "run" | "explain" (required)
  description: string (required)
  language: string (optional)
  output_path: string (optional)
  file_path: string (optional)

dev_agent
  description: string (required)
  language: string (optional)

file_processor
  file_path: string (required) — path to the file to process
  action: "summarize" | "translate" | "rewrite" | "extract" | "custom" (required)
  instruction: string (optional) — for custom actions
  language: string (optional) — for translate action
  output_path: string (optional) — where to save the result

OUTPUT — return ONLY valid JSON, no markdown, no explanation, no code blocks:
{
  "goal": "...",
  "steps": [
    {
      "step": 1,
      "tool": "tool_name",
      "description": "what this step does",
      "parameters": {},
      "critical": true
    }
  ]
}
"""


# ---------------------------------------------------------------------------
# Prompt split — flag-free; the flag is read by the CONSUMER (main.py)
# ---------------------------------------------------------------------------

_PREAMBLE_END_MARKER = "AVAILABLE TOOLS AND THEIR PARAMETERS:\n\n"
_FOOTER_START_MARKER = "\n\nOUTPUT — return ONLY valid JSON"

_preamble_idx = PLANNER_PROMPT.index(_PREAMBLE_END_MARKER)
_tool_start   = _preamble_idx + len(_PREAMBLE_END_MARKER)
_footer_idx   = PLANNER_PROMPT.index(_FOOTER_START_MARKER, _tool_start)

# _PLANNER_PREAMBLE: everything up to and including "AVAILABLE TOOLS AND THEIR PARAMETERS:\n\n"
_PLANNER_PREAMBLE: str = PLANNER_PROMPT[:_tool_start]
# _FOOTER_START_MARKER starts with "\n\n"; build_planner_prompt adds "\n\n" before footer,
# so _PLANNER_FOOTER starts with "OUTPUT..." (strips the leading "\n\n").
_PLANNER_FOOTER: str   = PLANNER_PROMPT[_footer_idx + len("\n\n"):]


def build_planner_prompt(tool_block: str) -> str:
    """Assemble a planner prompt with a registry-built tool block.
    Keeps _PLANNER_PREAMBLE and _PLANNER_FOOTER verbatim.
    Flag-FREE: the flag is read by the CONSUMER (main.py), never here."""
    return _PLANNER_PREAMBLE + tool_block + "\n\n" + _PLANNER_FOOTER


# ---------------------------------------------------------------------------


def create_plan(goal: str, context: str = "", system: str | None = None) -> dict:
    user_input = f"Goal: {goal}"
    if context:
        user_input += f"\n\nContext: {context}"

    effective_system = system if system is not None else PLANNER_PROMPT
    try:
        text = call_llm_text(user_input, system=effective_system)
        text = re.sub(r"```(?:json)?", "", text).strip().rstrip("`").strip()

        plan = json.loads(text)
        if "steps" not in plan or not isinstance(plan["steps"], list):
            raise ValueError("Invalid plan structure")

        for step in plan["steps"]:
            if step.get("tool") == "generated_code":
                print(f"[Planner] ⚠️ generated_code in step {step.get('step')} — replacing with web_search")
                step["tool"]       = "web_search"
                step["parameters"] = {"query": step.get("description", goal)[:200]}

        print(f"[Planner] ✅ Plan: {len(plan['steps'])} steps")
        for s in plan["steps"]:
            print(f"  Step {s['step']}: [{s['tool']}] {s['description']}")
        return plan

    except json.JSONDecodeError as e:
        print(f"[Planner] ⚠️ JSON parse failed: {e}")
        return _fallback_plan(goal)
    except Exception as e:
        print(f"[Planner] ⚠️ Planning failed: {e}")
        return _fallback_plan(goal)


def _fallback_plan(goal: str) -> dict:
    print("[Planner] 🔄 Fallback plan")
    return {
        "goal":  goal,
        "steps": [
            {
                "step":        1,
                "tool":        "web_search",
                "description": f"Search for: {goal}",
                "parameters":  {"query": goal},
                "critical":    True,
            }
        ],
    }


def replan(
    goal: str,
    completed_steps: list,
    failed_step: dict,
    error: str,
    system: str | None = None,
) -> dict:
    completed_summary = "\n".join(
        f"  - Step {s['step']} ({s['tool']}): DONE" for s in completed_steps
    )
    prompt = f"""Goal: {goal}

Already completed:
{completed_summary if completed_summary else '  (none)'}

Failed step: [{failed_step.get('tool')}] {failed_step.get('description')}
Error: {error}

Create a REVISED plan for the remaining work only. Do not repeat completed steps."""

    effective_system = system if system is not None else PLANNER_PROMPT
    try:
        text = call_llm_text(prompt, system=effective_system)
        text = re.sub(r"```(?:json)?", "", text).strip().rstrip("`").strip()
        plan = json.loads(text)

        for step in plan.get("steps", []):
            if step.get("tool") == "generated_code":
                step["tool"]       = "web_search"
                step["parameters"] = {"query": step.get("description", goal)[:200]}

        print(f"[Planner] 🔄 Revised plan: {len(plan['steps'])} steps")
        return plan
    except Exception as e:
        print(f"[Planner] ⚠️ Replan failed: {e}")
        return _fallback_plan(goal)
