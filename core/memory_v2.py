"""MemoryV2 — feature-flagged retrieval layer (WO-4, P1 lexical lane).

This module is the *new* memory retrieval path for Mark-XL.  It runs **behind a
feature flag** (``enable_memory_v2``).  The flag DECISION is made by the caller
and the constructed object is only ever instantiated at dispatch sites when the
flag is ON — therefore this module never reads the flag itself (AC10).  When the
flag is OFF, ``MemoryV2`` is never constructed and the legacy
``memory.memory_manager`` path remains completely untouched.

Phase 1 (this file)
-------------------
Lexical-only retrieval via the Rust ``SQLiteMemory`` (FTS5) store, reached
exclusively through :mod:`core.mark_xl_rust_adapter` wrappers dispatched
off-thread with ``run_in_executor(...).result(timeout=...)`` (AC8).  No embedder
is used in P1 (``embedder=None``); the semantic lane is P2.

Identity always-on (FR-4 / AC4)
-------------------------------
The identity block is built directly from ``memory['identity']`` using a FROZEN
field order and is prepended **outside** the top-k budget, so it is present even
at ``top_k=1`` with many stored facts.  The prefix is deterministic / byte-frozen
for KV-cache stability (AC7).

Graceful degradation (FR-10)
----------------------------
``build_context`` NEVER raises and NEVER returns a silent empty result.  On any
fault (wheel absent, timeout, parse error) it falls back to the legacy whole-blob
formatter and reports ``degraded=True`` / ``lane="legacy"``.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any, Optional

from core import mark_xl_rust_adapter as adapter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Frozen identity field order (FR-4 / AC4 / AC7). Must match
# memory_manager.format_memory_for_prompt's id_fields exactly. DO NOT REORDER.
# ---------------------------------------------------------------------------
_ID_FIELDS = ["name", "age", "birthday", "city", "job", "language", "school", "nationality"]

# Off-thread call timeouts (C-3 thread-affinity/timeouts).
_RETRIEVE_TIMEOUT_S = 5
_STORE_TIMEOUT_S = 5
_CONSTRUCT_TIMEOUT_S = 5

# Idempotency sentinel for migrate() (source marker on a written-once leaf).
_MIGRATED_SENTINEL_SOURCE = "__migrated__"


class MemoryDimensionMismatch(ValueError):
    """Raised when stored FAISS index dimension != live embedder dimension.

    Declared in P1 but only raised in P2 (semantic lane).  The message must name
    BOTH dimensions plus a remediation hint (AC3 / C4).  The Rust layer will NOT
    raise on a dim mismatch (it silently ``len().min()``-compares), so this guard
    is enforced Python-side.
    """


def _get_base_dir() -> Path:
    """Return the application base directory.

    Mirrors ``memory.memory_manager.get_base_dir`` so the DB lives alongside the
    rest of the app data.  Frozen (PyInstaller) -> executable dir; otherwise the
    repository root (parent of ``core/``).
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def _build_identity_prefix(identity: dict) -> str:
    """Build the deterministic, byte-frozen identity block (FR-4 / AC7).

    Known fields first in FROZEN ``_ID_FIELDS`` order, then any extra identity
    keys in their natural dict order.  Each entry may be a ``{"value": ...}``
    dict or a bare value.  Returns ``""`` when there is nothing to show.
    """
    if not isinstance(identity, dict):
        return ""

    lines: list[str] = []
    for field in _ID_FIELDS:
        entry = identity.get(field)
        if entry:
            val = entry.get("value") if isinstance(entry, dict) else entry
            if val:
                lines.append(f"{field.title()}: {val}")

    # Extra, non-standard identity fields (deterministic: dict insertion order).
    for key, entry in identity.items():
        if key in _ID_FIELDS:
            continue
        val = entry.get("value") if isinstance(entry, dict) else entry
        if val:
            lines.append(f"{key.replace('_', ' ').title()}: {val}")

    if not lines:
        return ""
    return "[Identity]\n" + "\n".join(lines)


class MemoryV2:
    """Feature-flagged retrieval layer (P1: lexical / FTS5 lane).

    Constructed ONLY at dispatch sites when ``enable_memory_v2`` is ON — the flag
    decision is passed in by the caller, never read here (AC10).
    """

    def __init__(
        self,
        memory: dict,
        embedder: Optional[Any] = None,
        db_dir: Optional[str] = None,
    ) -> None:
        """Construct the retrieval layer.

        Parameters
        ----------
        memory:
            The loaded ``long_term`` dict (from ``memory_manager.load_memory()``).
        embedder:
            ``None`` for P1 (lexical only).  The semantic lane is P2.
        db_dir:
            Directory for the SQLite DB.  Defaults to ``<base>/data`` (gitignored).
        """
        self._memory = memory if isinstance(memory, dict) else {}
        self._embedder = embedder

        if db_dir is not None:
            base_data = Path(db_dir)
        else:
            base_data = _get_base_dir() / "data"
        # Cross-platform: always store/compare the path as a POSIX string (AC: xplat).
        self._db_path = (base_data / "memory_v2.db").as_posix()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def identity_prefix(self) -> str:
        """Return the deterministic identity block (may be empty string)."""
        identity = self._memory.get("identity", {}) if isinstance(self._memory, dict) else {}
        return _build_identity_prefix(identity)

    def build_context(self, query: str, top_k: int = 5) -> dict:
        """Build the prompt context for ``query``.

        Returns a dict with keys ``context``, ``degraded``, ``guidance``,
        ``lane``.  The identity block is ALWAYS prepended outside the top-k
        budget (AC4), so it survives ``top_k=1``.  Never raises; on any fault it
        degrades to the legacy whole-blob formatter.
        """
        identity_prefix = self.identity_prefix()

        # P1 lexical lane requires the wheel. Without it, degrade to legacy.
        if not adapter.WHEEL_AVAILABLE:
            return self._legacy_context()

        try:
            topk_block = self._lexical_topk_block(query, top_k)
        except Exception:  # pragma: no cover - defensive; lexical path catches its own
            logger.warning("MemoryV2 lexical retrieval failed — degrading to legacy", exc_info=True)
            return self._legacy_context()

        if topk_block is None:
            # Retrieval faulted (timeout / None mem / parse error) -> degrade.
            return self._legacy_context()

        if identity_prefix and topk_block:
            context = identity_prefix + "\n" + topk_block
        else:
            context = identity_prefix or topk_block

        return {
            "context": context,
            "degraded": False,
            "guidance": None,
            "lane": "lexical",
        }

    def migrate(self) -> dict:
        """Migrate legacy ``long_term.json`` leaves into the lexical store.

        P1 only needs the no-op case: ``long_term.json`` is absent in the real
        state, so this returns a skip result without writing anything.  The full
        migration (``.bak`` first, per-leaf store, round-trip verify, idempotent
        sentinel) is implemented for the present-file case.

        Returns ``{"migrated", "verified", "bak", "skipped"}``.
        """
        json_path = _get_base_dir() / "memory" / "long_term.json"

        if not json_path.exists():
            return {
                "migrated": 0,
                "verified": 0,
                "bak": None,
                "skipped": "long_term.json absent",
            }

        if not adapter.WHEEL_AVAILABLE:
            return {
                "migrated": 0,
                "verified": 0,
                "bak": None,
                "skipped": "wheel unavailable",
            }

        # --- Load source data (utf-8). ---
        try:
            raw = json_path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except Exception:
            logger.warning("MemoryV2.migrate — failed to read long_term.json", exc_info=True)
            return {
                "migrated": 0,
                "verified": 0,
                "bak": None,
                "skipped": "long_term.json unreadable",
            }
        if not isinstance(data, dict):
            return {
                "migrated": 0,
                "verified": 0,
                "bak": None,
                "skipped": "long_term.json malformed",
            }

        # --- Construct the store off-thread. ---
        mem = self._construct_store()
        if mem is None:
            return {
                "migrated": 0,
                "verified": 0,
                "bak": None,
                "skipped": "store unavailable",
            }

        # --- Idempotency: if the sentinel leaf already exists, skip. ---
        if self._already_migrated(mem):
            return {
                "migrated": 0,
                "verified": 0,
                "bak": None,
                "skipped": "already migrated",
            }

        # --- .bak FIRST, before any store write (AC6). ---
        bak_path = json_path.with_suffix(json_path.suffix + ".bak")
        try:
            bak_path.write_text(raw, encoding="utf-8")
        except Exception:
            logger.warning("MemoryV2.migrate — failed to write .bak", exc_info=True)
            return {
                "migrated": 0,
                "verified": 0,
                "bak": None,
                "skipped": "backup failed",
            }
        bak_str = bak_path.as_posix()

        migrated = 0
        verified = 0
        for category, items in data.items():
            if not isinstance(items, dict):
                continue
            for key, entry in items.items():
                value = entry.get("value") if isinstance(entry, dict) else entry
                if not value:
                    continue
                content = f"{key}: {value}"
                metadata = json.dumps(
                    {
                        "key": key,
                        "updated": entry.get("updated") if isinstance(entry, dict) else None,
                        "category": category,
                    },
                    ensure_ascii=False,
                )
                uuid = self._store_leaf(mem, content, category, metadata)
                if uuid is None:
                    continue
                migrated += 1
                # Round-trip verify: retrieving the value returns a hit containing it.
                if self._roundtrip_ok(mem, str(value)):
                    verified += 1

        # --- Write idempotency sentinel leaf (written once). ---
        self._store_leaf(mem, "migration complete", _MIGRATED_SENTINEL_SOURCE, None)

        return {
            "migrated": migrated,
            "verified": verified,
            "bak": bak_str,
            "skipped": None,
        }

    # ------------------------------------------------------------------
    # Internal helpers (all wheel calls go through run_in_executor — AC8).
    # ------------------------------------------------------------------
    def _construct_store(self) -> Optional[Any]:
        """Construct SQLiteMemory off-thread; return None on fault."""
        try:
            return adapter.run_in_executor(
                adapter.sqlite_memory, self._db_path
            ).result(timeout=_CONSTRUCT_TIMEOUT_S)
        except Exception:
            logger.warning("MemoryV2 — SQLiteMemory construction failed/timed out", exc_info=True)
            return None

    def _lexical_topk_block(self, query: str, top_k: int) -> Optional[str]:
        """Retrieve top-k hits and render them as a text block.

        Returns the rendered block (possibly empty string if there are no hits),
        or ``None`` if retrieval faulted (caller then degrades to legacy).
        """
        mem = self._construct_store()
        if mem is None:
            return None

        try:
            raw = adapter.run_in_executor(
                adapter.sqlite_retrieve, mem, query, top_k
            ).result(timeout=_RETRIEVE_TIMEOUT_S)
        except Exception:
            logger.warning("MemoryV2 — sqlite_retrieve failed/timed out", exc_info=True)
            return None

        if raw is None:
            return None

        try:
            hits = json.loads(raw)
        except Exception:
            logger.warning("MemoryV2 — failed to parse retrieve JSON", exc_info=True)
            return None

        if not isinstance(hits, list):
            return ""

        lines: list[str] = []
        for hit in hits:
            if isinstance(hit, dict):
                content = hit.get("content")
            else:
                content = hit
            if content:
                lines.append(f"- {content}")
        return "\n".join(lines)

    def _store_leaf(
        self, mem: Any, content: str, source: str, metadata: Optional[str]
    ) -> Optional[str]:
        """Store one leaf off-thread; return the minted UUID or None on fault."""
        try:
            return adapter.run_in_executor(
                adapter.sqlite_store, mem, content, source, metadata
            ).result(timeout=_STORE_TIMEOUT_S)
        except Exception:
            logger.warning("MemoryV2 — sqlite_store failed/timed out", exc_info=True)
            return None

    def _roundtrip_ok(self, mem: Any, value: str) -> bool:
        """Return True iff retrieving ``value`` yields a hit containing it."""
        try:
            raw = adapter.run_in_executor(
                adapter.sqlite_retrieve, mem, value, 5
            ).result(timeout=_RETRIEVE_TIMEOUT_S)
        except Exception:
            return False
        if raw is None:
            return False
        try:
            hits = json.loads(raw)
        except Exception:
            return False
        if not isinstance(hits, list):
            return False
        for hit in hits:
            content = hit.get("content") if isinstance(hit, dict) else hit
            if content and value in str(content):
                return True
        return False

    def _already_migrated(self, mem: Any) -> bool:
        """Return True iff the idempotency sentinel leaf is present."""
        try:
            raw = adapter.run_in_executor(
                adapter.sqlite_retrieve, mem, "migration complete", 5
            ).result(timeout=_RETRIEVE_TIMEOUT_S)
        except Exception:
            return False
        if raw is None:
            return False
        try:
            hits = json.loads(raw)
        except Exception:
            return False
        if not isinstance(hits, list):
            return False
        for hit in hits:
            if isinstance(hit, dict) and hit.get("source") == _MIGRATED_SENTINEL_SOURCE:
                return True
        return False

    def _legacy_context(self) -> dict:
        """Degraded whole-blob fallback via the legacy formatter (FR-10)."""
        try:
            from memory.memory_manager import format_memory_for_prompt

            context = format_memory_for_prompt(self._memory)
        except Exception:
            logger.warning("MemoryV2 — legacy formatter failed", exc_info=True)
            context = ""
        return {
            "context": context,
            "degraded": True,
            "guidance": None,
            "lane": "legacy",
        }
