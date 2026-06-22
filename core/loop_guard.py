"""core/loop_guard.py — LoopGuard integration bridge for Mark-XL.

Module-level state is lazy — _GUARD and _BRIDGE are initialized to None and
populated on first use by the public factory functions.  Zero config-flag-read
literals live here; callers resolve flags and pass them in as parameters.

Public API (stubs — full implementations land in later WO-5 tasks):
  load_knobs()       -> (max_identical, max_ping_pong, poll_budget)
  get_guard(...)     -> Optional[LoopGuard wheel object]
  check(...)         -> Optional[str]  (reason string if loop detected, else None)
  reset()            -> None
  get_bridge()       -> Optional[LoopGuardBridge]
  signal_loop(...)   -> None
"""
from __future__ import annotations

import threading
from typing import Any, Optional  # noqa: F401

from PyQt6.QtCore import QObject, pyqtSignal

import core.mark_xl_rust_adapter as adapter  # noqa: F401
from core.security_gate import _canonical_json  # noqa: F401
from memory.config_manager import get_security_config  # noqa: F401

# ---------------------------------------------------------------------------
# Module-level state (lazy — never constructed at import time)
# ---------------------------------------------------------------------------

_GUARD: Optional[Any] = None
_GUARD_LOCK: threading.Lock = threading.Lock()
_BRIDGE: Optional["LoopGuardBridge"] = None


# ---------------------------------------------------------------------------
# Qt bridge
# ---------------------------------------------------------------------------

class LoopGuardBridge(QObject):
    """Qt signal bridge that fires when LoopGuard detects a runaway loop.

    Connect ``loop_detected`` to any slot that should be notified when a
    loop is detected.  The payload is a human-readable reason string.
    """

    loop_detected = pyqtSignal(str)


# ---------------------------------------------------------------------------
# Public API (stubs — raise NotImplementedError until implemented)
# ---------------------------------------------------------------------------

def load_knobs() -> tuple[int, int, int]:
    """Load LoopGuard tuning knobs from config.

    Returns:
        (max_identical, max_ping_pong, poll_budget)
    """
    mi = int(get_security_config("loopguard_max_identical", 50))
    mp = int(get_security_config("loopguard_max_ping_pong", 4))
    pb = int(get_security_config("loopguard_poll_budget", 100))
    return mi, mp, pb


def get_guard(
    enable_loopguard: bool,
    *,
    max_identical: int,
    max_ping_pong: int,
    poll_budget: int,
) -> Optional[Any]:
    """Return (or lazily create) the LoopGuard wheel object.

    Args:
        enable_loopguard: Caller-resolved feature flag — never read here.
        max_identical:    Maximum consecutive identical tool calls before trip.
        max_ping_pong:    Maximum A→B→A ping-pong cycles before trip.
        poll_budget:      Maximum total poll iterations before trip.

    Returns:
        The LoopGuard wheel object, or None when loopguard is disabled / wheel
        is unavailable.
    """
    global _GUARD
    if not enable_loopguard:
        return None
    if _GUARD is None:
        with _GUARD_LOCK:
            if _GUARD is None:  # double-checked locking
                _GUARD = adapter.get_loop_guard(
                    max_identical=max_identical,
                    max_ping_pong=max_ping_pong,
                    poll_budget=poll_budget,
                )
    return _GUARD


def check(tool_name: str, params: dict) -> Optional[str]:
    """Check whether the current call constitutes a detected loop.

    Args:
        tool_name: Name of the tool being dispatched.
        params:    Tool parameters dict (will be canonicalised internally).

    Returns:
        A reason string if a loop is detected, None otherwise.
    """
    if _GUARD is None:
        return None
    arg_str = _canonical_json(params)
    with _GUARD_LOCK:
        return _GUARD.check(tool_name, arg_str)


def reset() -> None:
    """Reset the LoopGuard state (call between agent turns or on session start)."""
    with _GUARD_LOCK:
        if _GUARD is not None:
            _GUARD.reset()


def get_bridge() -> Optional[LoopGuardBridge]:
    """Lazily construct the LoopGuardBridge singleton on the Qt main thread.

    The bridge must be constructed on the Qt main thread for correct
    thread-affinity so Qt auto-queues cross-thread signal emissions to the
    UI thread.  In tests we run with QT_QPA_PLATFORM=offscreen which
    provides a headless Qt application.

    Returns:
        The singleton LoopGuardBridge.
    """
    global _BRIDGE
    if _BRIDGE is None:
        with _GUARD_LOCK:
            if _BRIDGE is None:
                _BRIDGE = LoopGuardBridge()
    return _BRIDGE


def signal_loop(reason: str) -> None:
    """Emit ``loop_detected`` on the module-level bridge (if initialised).

    Safe from ANY thread — Qt auto-queues if cross-thread.  No-op if
    ``_BRIDGE`` is None.  Does NOT call :func:`get_bridge` internally;
    the caller is responsible for ensuring :func:`get_bridge` has been
    called on the main thread before ``signal_loop`` is used.

    Args:
        reason: Human-readable description of the detected loop condition.
    """
    if _BRIDGE is not None:
        _BRIDGE.loop_detected.emit(reason)
