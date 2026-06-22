"""core/audit.py — Tamper-evident SQLite (WAL) audit ledger for WO-3.

Pure-Python implementation.  The wheel's AuditLogger has no write method;
this module is the authoritative writer.  No config-flag reads in this file.
ADR: docs/decisions/002-wo3-audit-hash-chain.md
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from core import mark_xl_rust_adapter as adapter


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _canonical_json(obj) -> str:
    """Stable JSON serialisation — sort_keys so hashes are reproducible."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _row_hash(prev_hash: str, record_minus_hash: dict) -> str:
    """sha256(prev_hash‖canonical_json(record)) → hex digest."""
    payload = prev_hash.encode("utf-8") + _canonical_json(record_minus_hash).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


# ---------------------------------------------------------------------------
# AuditLedger
# ---------------------------------------------------------------------------

class AuditLedger:
    """SQLite WAL tamper-evident hash-chain ledger.

    Parameters
    ----------
    db_path:
        Absolute or relative path to the SQLite file.  The parent directory
        is created if absent.  Path is stringified via ``Path.as_posix()``.
    ssrf_local_nav:
        Convenience carry-through for the caller (passed into run_gated);
        this module does not read config flags directly.
    """

    _CREATE_TABLE = (
        "CREATE TABLE IF NOT EXISTS audit_log ("
        "seq               INTEGER PRIMARY KEY AUTOINCREMENT,"
        "ts                TEXT    NOT NULL,"
        "tool              TEXT    NOT NULL,"
        "redacted_params   TEXT    NOT NULL,"
        "redacted_result   TEXT    NOT NULL,"
        "confirm_required  INTEGER NOT NULL,"
        "approved          INTEGER NOT NULL,"
        "prev_hash         TEXT    NOT NULL,"
        "hash              TEXT    NOT NULL"
        ")"
    )

    def __init__(self, db_path: str, *, ssrf_local_nav: bool = False) -> None:
        self.ssrf_local_nav = ssrf_local_nav
        # Normalise to POSIX path string (NFR-4)
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path: str = path.as_posix()

        # Connect and configure WAL
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute(self._CREATE_TABLE)
        self._conn.commit()

        # chmod 600 — guarded for Windows (NFR-4, NFR-8)
        if os.name != "nt":
            os.chmod(self._db_path, 0o600)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _tail(self) -> Optional[str]:
        """Return the hash of the last row, or None if the table is empty."""
        row = self._conn.execute(
            "SELECT hash FROM audit_log ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        return row[0] if row else None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def write(
        self,
        tool: str,
        params: dict,
        result,
        *,
        confirm_required: bool,
        approved: bool,
    ) -> str:
        """Append one tamper-evident record; return its hex hash.

        Redaction (FR-9): every value is passed through
        ``adapter.redact()`` BEFORE serialisation — no raw secret reaches
        the SQLite file.

        On ``sqlite3.OperationalError`` (DB locked) retries ONCE with a
        short backoff; re-raises on persistent failure so the caller can
        fail-closed on AUDIT_PRE (design §5).
        """
        ts = datetime.now(timezone.utc).isoformat()

        # --- Redact before writing (FR-9) ---
        raw_params = _canonical_json(params)
        raw_result = result if isinstance(result, str) else _canonical_json(result)
        redacted_params = adapter.redact(raw_params)
        redacted_result = adapter.redact(raw_result)

        # --- Hash chain ---
        prev_hash = self._tail() or ("0" * 64)
        record_minus_hash = {
            "ts": ts,
            "tool": tool,
            "redacted_params": redacted_params,
            "redacted_result": redacted_result,
            "confirm_required": int(confirm_required),
            "approved": int(approved),
            "prev_hash": prev_hash,
        }
        row_hash = _row_hash(prev_hash, record_minus_hash)

        def _insert():
            self._conn.execute(
                "INSERT INTO audit_log "
                "(ts, tool, redacted_params, redacted_result, "
                "confirm_required, approved, prev_hash, hash) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    ts, tool,
                    redacted_params, redacted_result,
                    int(confirm_required), int(approved),
                    prev_hash, row_hash,
                ),
            )
            self._conn.commit()

        try:
            _insert()
        except sqlite3.OperationalError:
            time.sleep(0.05)
            _insert()  # re-raises on second failure (fail-closed for AUDIT_PRE)

        return row_hash

    def verify_chain(self) -> bool:
        """Recompute the entire hash chain; return False on any mismatch."""
        rows = self._conn.execute(
            "SELECT ts, tool, redacted_params, redacted_result, "
            "confirm_required, approved, prev_hash, hash "
            "FROM audit_log ORDER BY seq ASC"
        ).fetchall()

        running_prev = "0" * 64
        for row in rows:
            ts, tool, rp, rr, cr, ap, prev_hash, stored_hash = row
            if prev_hash != running_prev:
                return False
            record_minus_hash = {
                "ts": ts,
                "tool": tool,
                "redacted_params": rp,
                "redacted_result": rr,
                "confirm_required": cr,
                "approved": ap,
                "prev_hash": prev_hash,
            }
            expected = _row_hash(prev_hash, record_minus_hash)
            if expected != stored_hash:
                return False
            running_prev = stored_hash
        return True

    def count(self) -> int:
        """Return the number of records."""
        row = self._conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()
        return row[0] if row else 0

    def tail_hash(self) -> str:
        """Return the most recent record's hash, or '' when empty."""
        return self._tail() or ""
