"""
core/tool_registry.py — pure-Python tool registry for WO-2.
ZERO imports from main.py (avoids circular import).
"""
from __future__ import annotations

import dataclasses
import importlib
from typing import Callable, Dict, Optional

# ── Schema conversion (verbatim lift from main.py) ─────────────────────────
# _TYPE_MAP, _convert_type, _convert_props lifted from main.py VERBATIM.
# Two copies (main + core) is CORRECT per design. main.py NOT deleted.
_TYPE_MAP = {
    "OBJECT": "object", "STRING": "string", "ARRAY": "array",
    "INTEGER": "integer", "BOOLEAN": "boolean", "NUMBER": "number",
}


def _convert_type(t: str) -> str:
    return _TYPE_MAP.get(t, t.lower()) if isinstance(t, str) else t


def _convert_props(props: dict) -> dict:
    out = {}
    for k, v in props.items():
        nv = dict(v)
        if "type" in nv:
            nv["type"] = _convert_type(nv["type"])
        if "items" in nv and isinstance(nv["items"], dict):
            nv["items"] = {"type": _convert_type(nv["items"].get("type", "string"))}
        out[k] = nv
    return out


# ── ToolSpec dataclass ──────────────────────────────────────────────────────
@dataclasses.dataclass
class ToolSpec:
    name: str
    func: Optional[Callable]
    description: str
    parameters: dict            # Gemini UPPERCASE schema — converted ON EMIT
    planner_block: str = ""     # verbatim per-tool planner text; "" if not planner-visible
    is_planner_visible: bool = True
    is_silent: bool = False
    requires_confirm: bool = False
    inline: bool = False        # True for save_memory/agent_task/shutdown_jarvis


# ── Registry ────────────────────────────────────────────────────────────────
_REGISTRY: Dict[str, ToolSpec] = {}


def register_tool(name, description, parameters, planner_block="", *,
                  is_planner_visible=True, is_silent=False,
                  requires_confirm=False, inline=False):
    """Decorator. RAISES ValueError on duplicate name (Q2 fail-fast)."""
    def decorator(fn):
        if name in _REGISTRY:
            raise ValueError(f"Duplicate tool registration: {name!r}")
        _REGISTRY[name] = ToolSpec(
            name=name, func=fn, description=description,
            parameters=parameters, planner_block=planner_block,
            is_planner_visible=is_planner_visible, is_silent=is_silent,
            requires_confirm=requires_confirm, inline=inline,
        )
        return fn   # return fn UNCHANGED (existing direct calls still work)
    return decorator


# ── dispatch ────────────────────────────────────────────────────────────────
def dispatch(name: str, params: dict, player=None, speak=None) -> str:
    """Single dispatch chokepoint. Returns RAW result — NO fallback/coercion inside.
    Raises ValueError for unknown/inline/func-None tools."""
    spec = _REGISTRY.get(name)
    if spec is None or spec.inline or spec.func is None:
        raise ValueError(f"Unknown tool: {name}")
    return spec.func(parameters=params, player=player, speak=speak)


# ── to_openai_function ──────────────────────────────────────────────────────
def to_openai_function(spec: ToolSpec) -> dict:
    """Convert ToolSpec to OpenAI/Ollama function tool format.
    Verbatim lift of _to_ollama_tools loop body — byte-identical by construction."""
    new_params: dict = {
        "type":       "object",
        "properties": _convert_props(spec.parameters.get("properties", {})),
    }
    req = spec.parameters.get("required")
    if req:
        new_params["required"] = req
    return {
        "type": "function",
        "function": {
            "name":        spec.name,
            "description": spec.description,
            "parameters":  new_params,
        },
    }


# ── Order manifests + builders (stubs — full content added in T3) ───────────
OLLAMA_TOOLS_ORDER: tuple = (
    "open_app",
    "web_search",
    "weather_report",
    "send_message",
    "reminder",
    "youtube_video",
    "screen_process",
    "computer_settings",
    "browser_control",
    "file_controller",
    "desktop_control",
    "code_helper",
    "dev_agent",
    "agent_task",
    "computer_control",
    "game_updater",
    "flight_finder",
    "shutdown_jarvis",
    "file_processor",
    "save_memory",
)

PLANNER_TOOLS_ORDER: tuple = (
    "open_app",
    "web_search",
    "game_updater",
    "browser_control",
    "file_controller",
    "computer_settings",
    "computer_control",
    "screen_process",
    "send_message",
    "reminder",
    "desktop_control",
    "youtube_video",
    "weather_report",
    "flight_finder",
    "code_helper",
    "dev_agent",
    "file_processor",
)

_TOOL_MODULES: tuple = (
    "actions.file_processor",
    "actions.flight_finder",
    "actions.open_app",
    "actions.weather_report",
    "actions.send_message",
    "actions.reminder",
    "actions.computer_settings",
    "actions.screen_processor",
    "actions.youtube_video",
    "actions.desktop",
    "actions.browser_control",
    "actions.file_controller",
    "actions.code_helper",
    "actions.dev_agent",
    "actions.web_search",
    "actions.computer_control",
    "actions.game_updater",
)


# ── Inline tool handlers ────────────────────────────────────────────────────

def _save_memory_handler(parameters, player=None, speak=None, **kwargs):
    """save_memory inline handler — silently persists a memory key/value pair."""
    from memory.memory_manager import update_memory
    category = parameters.get("category", "notes")
    key      = parameters.get("key", "")
    value    = parameters.get("value", "")
    if key and value:
        update_memory({category: {key: {"value": value}}})
    return "__SILENT__"


def _agent_task_handler(parameters, player=None, speak=None, **kwargs):
    """agent_task inline handler — enqueues a goal to the background task queue."""
    from agent.task_queue import get_queue, TaskPriority
    priority_map = {
        "low":    TaskPriority.LOW,
        "normal": TaskPriority.NORMAL,
        "high":   TaskPriority.HIGH,
    }
    priority = priority_map.get(
        parameters.get("priority", "normal").lower(), TaskPriority.NORMAL
    )
    task_id = get_queue().submit(
        goal=parameters.get("goal", ""), priority=priority, speak=speak
    )
    return f"Task started (ID: {task_id})."


def _shutdown_jarvis_handler(parameters, player=None, speak=None, **kwargs):
    """shutdown_jarvis inline handler — logs the request and spawns a daemon thread
    that speaks goodbye then exits the process."""
    import threading
    if player is not None and hasattr(player, "write_log"):
        player.write_log("SYS: Shutdown requested.")

    def _shutdown():
        import time
        import os
        if speak is not None:
            speak("Goodbye.")
        time.sleep(2.5)
        os._exit(0)

    threading.Thread(target=_shutdown, daemon=True).start()
    return "Shutting down."


def _register_inline_specs() -> None:
    """Register the 3 inline tools. Re-entrant: safe to call multiple times.
    Each inline registration guarded with `if name not in _REGISTRY` so Q2 raise
    in register_tool is NOT loosened — accidental collisions from action modules
    still raise, but double-bootstrap is safe."""
    if "save_memory" not in _REGISTRY:
        _REGISTRY["save_memory"] = ToolSpec(
            name="save_memory",
            func=_save_memory_handler,
            description=(
                "Save a personal fact about the user to permanent long-term memory. "
                "MANDATORY: call this IMMEDIATELY (without asking) whenever the user states or corrects: "
                "their name, age, city, job, school, language, nationality, a preference, a goal, or a relationship. "
                "Examples: "
                "'my name is Fatih' → (identity, name, Fatih) | "
                "'not Travis, Fatih' → (identity, name, Fatih) | "
                "'I am 22' → (identity, age, 22) | "
                "'I live in Ankara' → (identity, city, Ankara) | "
                "'I prefer dark mode' → (preferences, ui_theme, dark mode). "
                "Call SILENTLY alongside your verbal reply — never announce that you are saving."
            ),
            parameters={
                "type": "OBJECT",
                "properties": {
                    "category": {
                        "type": "STRING",
                        "description": (
                            "identity (name/age/city/job/school/nationality) | "
                            "preferences (likes/dislikes/habits) | "
                            "projects (active work/goals) | "
                            "relationships (people in their life) | "
                            "wishes (future plans/wants) | "
                            "notes (anything else)"
                        )
                    },
                    "key":   {"type": "STRING", "description": "Short snake_case key, e.g. 'name', 'age', 'favorite_color'"},
                    "value": {"type": "STRING", "description": "Concise value in English"},
                },
                "required": ["category", "key", "value"]
            },
            planner_block="",
            is_planner_visible=False,
            is_silent=True,
            inline=True,
        )
    if "agent_task" not in _REGISTRY:
        _REGISTRY["agent_task"] = ToolSpec(
            name="agent_task",
            func=_agent_task_handler,
            description=(
                "Executes complex multi-step tasks requiring multiple different tools. "
                "Examples: 'research X and save to file', 'find and organize files'. "
                "DO NOT use for single commands."
            ),
            parameters={
                "type": "OBJECT",
                "properties": {
                    "goal":     {"type": "STRING", "description": "Complete description of what to accomplish"},
                    "priority": {"type": "STRING", "description": "low | normal | high"}
                },
                "required": ["goal"]
            },
            planner_block="",
            is_planner_visible=False,
            inline=True,
        )
    if "shutdown_jarvis" not in _REGISTRY:
        _REGISTRY["shutdown_jarvis"] = ToolSpec(
            name="shutdown_jarvis",
            func=_shutdown_jarvis_handler,
            description=(
                "Shuts down the assistant completely. "
                "Call this when the user expresses intent to end the conversation, "
                "close the assistant, say goodbye, or stop Jarvis."
            ),
            parameters={"type": "OBJECT", "properties": {}},
            planner_block="",
            is_planner_visible=False,
            requires_confirm=True,
            inline=True,
        )


def import_all_tools() -> None:
    """Eager explicit import of all 17 action modules + inline specs.
    Safe to call multiple times (Python module caching + re-entrant _register_inline_specs)."""
    for module_path in _TOOL_MODULES:
        importlib.import_module(module_path)
    _register_inline_specs()


def build_ollama_tools() -> list:
    """Build OLLAMA_TOOLS from registry. Returns empty list if OLLAMA_TOOLS_ORDER is empty (T3 stub)."""
    return [to_openai_function(_REGISTRY[n]) for n in OLLAMA_TOOLS_ORDER]


def build_planner_tool_block() -> str:
    """Build planner tool region from registry. Returns empty string (T3 stub)."""
    return "\n\n".join(_REGISTRY[n].planner_block for n in PLANNER_TOOLS_ORDER)
