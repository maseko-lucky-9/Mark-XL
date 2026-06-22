"""core/security_gate.py — 7-gate dispatch wrapper for WO-3.

Wraps ``core.tool_registry.dispatch`` at its two live call sites.
Called ONLY when ``enable_security_gates`` is ON (callers gate on the flag
and pass the gate state in as parameters).  Contains zero config-flag-read
literals — the flag is resolved by the caller, never by this module.
"""
from __future__ import annotations

import ipaddress
import json
import os
from typing import Callable, Optional
from urllib.parse import urlparse

from core import mark_xl_rust_adapter as adapter
from core.tool_registry import _REGISTRY, dispatch


# ---------------------------------------------------------------------------
# Module-level helpers (shared with audit for hash stability)
# ---------------------------------------------------------------------------

def _canonical_json(obj) -> str:
    """Stable JSON — identical to core.audit._canonical_json."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


# RFC-1918 + loopback CIDR ranges for the SSRF opt-in allowlist
_PRIVATE_NETS = [
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("127.0.0.0/8"),
]
# Cloud-metadata is NEVER in the allowlist (analysis §1)
_CLOUD_METADATA_NETS = [
    ipaddress.ip_network("169.254.0.0/16"),  # link-local / cloud metadata
]


def _ssrf_allowed(url: str, ssrf_local_nav: bool) -> bool:
    """Return True if navigation to ``url`` is permitted.

    Logic:
      1. Parse host from URL.
      2. If ``ssrf_local_nav`` and host is in {192.168/16, 10/8, 127/8}: return True
         (opt-in re-permits local nav).
      3. Cloud-metadata (169.254.x.x) is NEVER in the allowlist — honored-blocked.
      4. Call ``adapter.check_ssrf(url)`` via ``adapter.run_in_executor``; return
         True iff the result is None (URL is safe per the wheel).
    """
    try:
        host = urlparse(url).hostname or url
        addr = ipaddress.ip_address(host)
        # Cloud-metadata: never allow regardless of opt-in
        for net in _CLOUD_METADATA_NETS:
            if addr in net:
                return False
        # SSRF opt-in: permit configured private ranges
        if ssrf_local_nav:
            for net in _PRIVATE_NETS:
                if addr in net:
                    return True
    except ValueError:
        # Not an IP address — fall through to check_ssrf
        pass

    # Delegate to the wheel's SSRF checker (None == safe)
    try:
        reason = adapter.run_in_executor(adapter.check_ssrf, url).result(timeout=5)
        return reason is None
    except Exception:
        return True  # fail-open on checker fault (wheel absent logs legacy mode)


def _is_sensitive(path: str) -> bool:
    """Python wrapper union (analysis §2 BLOCKER-1).

    Returns True if the wheel marks it sensitive OR the basename is in the
    Python allowlist.  Operates on path/name; file need NOT exist on disk (FR-10).
    """
    wheel_result = adapter.is_sensitive_file(path)
    if wheel_result:  # True when wheel present and file is sensitive
        return True
    return os.path.basename(path) in {"api_keys.json"}


def _build_preview(tool: str, params: dict) -> str:
    """Build the human-readable, SECRET-REDACTED confirm-dialog preview string.

    Every value is passed through ``adapter.redact`` before inclusion.
    """
    try:
        redacted = {}
        for k, v in params.items():
            redacted[k] = adapter.redact(str(v))
        return f"Tool: {tool}\nParameters: {_canonical_json(redacted)}"
    except Exception:
        return f"Tool: {tool}"


# ---------------------------------------------------------------------------
# 7-gate wrapper
# ---------------------------------------------------------------------------

def run_gated(
    tool: str,
    params: dict,
    *,
    player,
    speak,
    confirm_fn: Callable[[str, str, int], bool],
    audit,
    ssrf_local_nav: bool = False,
) -> str:
    """Execute the 7-gate sequence and return the tool result string.

    Gate order: confirm → injection_scan → ssrf/sensitive-file →
                audit_pre → exec → audit_post.

    Called ONLY when enable_security_gates is ON.  The caller gates on the
    flag and passes the gate state in as parameters.

    Block outcomes (tool NOT executed) return a fixed string AND write an
    audit record:
      - confirm denied/timeout  → "Action cancelled."
      - injection High verdict   → "Blocked: potential injection detected."
      - ssrf out-of-allowlist    → "Blocked: destination not permitted."

    ValueError from dispatch (unknown/inline/func-None tool) propagates
    unchanged (WO-0 invariant — not caught here).
    """
    spec = _REGISTRY.get(tool)

    # ── GATE 1: CONFIRM ────────────────────────────────────────────────────
    if spec is not None and spec.requires_confirm:
        preview = _build_preview(tool, params)
        approved = confirm_fn(tool, preview, 30)
        if not approved:
            audit.write(
                tool, params, "__BLOCKED_CONFIRM__",
                confirm_required=True, approved=False,
            )
            return "Action cancelled."

    # ── GATE 2: INJECTION SCAN ─────────────────────────────────────────────
    try:
        verdict_future = adapter.run_in_executor(
            adapter.injection_scan, _canonical_json(params)
        )
        verdict = verdict_future.result(timeout=10)
    except Exception:
        verdict = {"is_clean": True, "threat_level": "low", "findings": []}
    if verdict.get("is_clean") is False and verdict.get("threat_level") == "high":
        audit.write(
            tool, params, "__BLOCKED_INJECTION__",
            confirm_required=bool(spec and spec.requires_confirm),
            approved=True,
        )
        return "Blocked: potential injection detected."

    # ── GATE 3: SSRF + SENSITIVE-FILE ──────────────────────────────────────
    if tool == "browser_control":
        url = params.get("url") or params.get("target_url", "")
        if url and not _ssrf_allowed(url, ssrf_local_nav):
            audit.write(
                tool, params, "__BLOCKED_SSRF__",
                confirm_required=False, approved=False,
            )
            return "Blocked: destination not permitted."

    if tool in ("file_controller", "file_processor"):
        path = params.get("file_path") or params.get("path") or ""
        if path and _is_sensitive(path):
            # Gate 1 already fired (these tools are requires_confirm=True).
            # The sensitive check here is the recorded justification + redaction key.
            audit.write(
                tool, params, "__SENSITIVE_FILE__",
                confirm_required=True, approved=True,
            )
            # Sensitive file access confirmed — continue to exec

    # ── GATE 4: AUDIT_PRE ──────────────────────────────────────────────────
    # Fail-closed: if this write fails persistently, the exception propagates
    # and the tool NEVER executes (design §5).
    audit.write(
        tool, params, "__PENDING__",
        confirm_required=bool(spec and spec.requires_confirm),
        approved=True,
    )

    # ── GATE 5: EXEC ───────────────────────────────────────────────────────
    # ValueError from dispatch propagates unchanged (WO-0 invariant).
    result = dispatch(tool, params, player=player, speak=speak)

    # ── GATE 6: AUDIT_POST ─────────────────────────────────────────────────
    # Fail-open: log ERROR but do NOT re-raise (tool already ran).
    try:
        audit.write(
            tool, params, result,
            confirm_required=bool(spec and spec.requires_confirm),
            approved=True,
        )
    except Exception:
        import logging
        logging.getLogger(__name__).error(
            "AUDIT_POST failed for tool %r — result NOT persisted to audit log",
            tool,
        )

    return result
