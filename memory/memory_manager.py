import json
from datetime import datetime
from threading import Lock
from pathlib import Path
import sys

# ---------------------------------------------------------------------------
# WO-6 — optional PyO3 wheel import guard (FR-4/FR-5/FR-6).
#
# The mark_xl_rust wheel is rebuilt for arm64 macOS only. On any platform where
# the wheel is absent, this module still imports cleanly with
# _WHEEL_AVAILABLE = False and every SessionStore path degrades to the legacy
# flat-JSON FACT behaviour (NFR-4). No store path raises solely because the
# wheel is missing.
# ---------------------------------------------------------------------------
try:
    import mark_xl_rust as _rust
    _WHEEL_AVAILABLE = True
except ImportError:
    _rust = None
    _WHEEL_AVAILABLE = False

# Shared single-writer store executor + WAL helper (zero import-time side effects).
from core import store_executor as _store_executor_mod


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR         = get_base_dir()
MEMORY_PATH      = BASE_DIR / "memory" / "long_term.json"
_lock            = Lock()
MAX_VALUE_LENGTH = 380
MEMORY_MAX_CHARS = 2200

def _empty_memory() -> dict:
    return {
        "identity":      {},
        "preferences":   {},
        "projects":      {},
        "relationships": {},
        "wishes":        {},
        "notes":         {},
    }

def load_memory() -> dict:
    if not MEMORY_PATH.exists():
        return _empty_memory()
    with _lock:
        try:
            data = json.loads(MEMORY_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                base = _empty_memory()
                for key in base:
                    if key not in data:
                        data[key] = {}
                return data
            return _empty_memory()
        except Exception as e:
            print(f"[Memory] ⚠️ Load error: {e}")
            return _empty_memory()

def _all_entries(memory: dict) -> list[tuple]:
    entries = []
    for cat, items in memory.items():
        if not isinstance(items, dict):
            continue
        for key, entry in items.items():
            if isinstance(entry, dict) and "value" in entry:
                entries.append((cat, key, entry))
    return entries


def _trim_to_limit(memory: dict) -> dict:
    if len(json.dumps(memory, ensure_ascii=False)) <= MEMORY_MAX_CHARS:
        return memory
    entries = _all_entries(memory)
    entries.sort(key=lambda t: t[2].get("updated", "0000-00-00"))
    for cat, key, _ in entries:
        if len(json.dumps(memory, ensure_ascii=False)) <= MEMORY_MAX_CHARS:
            break
        del memory[cat][key]
        print(f"[Memory] 🗑️  Trimmed {cat}/{key}")
    return memory

def save_memory(memory: dict) -> None:
    if not isinstance(memory, dict):
        return
    memory = _trim_to_limit(memory)
    MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        MEMORY_PATH.write_text(
            json.dumps(memory, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


def _truncate_value(val: str) -> str:
    if isinstance(val, str) and len(val) > MAX_VALUE_LENGTH:
        return val[:MAX_VALUE_LENGTH].rstrip() + "…"
    return val


def _recursive_update(target: dict, updates: dict) -> bool:
    changed = False
    for key, value in updates.items():
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, dict) and "value" not in value:
            if key not in target or not isinstance(target[key], dict):
                target[key] = {}
                changed = True
            if _recursive_update(target[key], value):
                changed = True
        else:
            new_val  = _truncate_value(str(value["value"] if isinstance(value, dict) else value))
            entry    = {"value": new_val, "updated": datetime.now().strftime("%Y-%m-%d")}
            existing = target.get(key, {})
            if not isinstance(existing, dict) or existing.get("value") != new_val:
                target[key] = entry
                changed = True
    return changed


def update_memory(memory_update: dict) -> dict:
    if not isinstance(memory_update, dict) or not memory_update:
        return load_memory()
    memory = load_memory()
    if _recursive_update(memory, memory_update):
        save_memory(memory)
        print(f"[Memory] 💾 Saved: {list(memory_update.keys())}")
    return memory

def format_memory_for_prompt(memory: dict | None) -> str:
    if not memory:
        return ""

    lines = []

    identity  = memory.get("identity", {})
    id_fields = ["name", "age", "birthday", "city", "job", "language", "school", "nationality"]
    for field in id_fields:
        entry = identity.get(field)
        if entry:
            val = entry.get("value") if isinstance(entry, dict) else entry
            if val:
                lines.append(f"{field.title()}: {val}")
    for key, entry in identity.items():
        if key in id_fields:
            continue
        val = entry.get("value") if isinstance(entry, dict) else entry
        if val:
            lines.append(f"{key.replace('_', ' ').title()}: {val}")

    prefs = memory.get("preferences", {})
    if prefs:
        lines.append("")
        lines.append("Preferences:")
        for key, entry in list(prefs.items())[:15]:
            val = entry.get("value") if isinstance(entry, dict) else entry
            if val:
                lines.append(f"  - {key.replace('_', ' ').title()}: {val}")

    projects = memory.get("projects", {})
    if projects:
        lines.append("")
        lines.append("Active Projects / Goals:")
        for key, entry in list(projects.items())[:8]:
            val = entry.get("value") if isinstance(entry, dict) else entry
            if val:
                lines.append(f"  - {key.replace('_', ' ').title()}: {val}")

    rels = memory.get("relationships", {})
    if rels:
        lines.append("")
        lines.append("People in their life:")
        for key, entry in list(rels.items())[:10]:
            val = entry.get("value") if isinstance(entry, dict) else entry
            if val:
                lines.append(f"  - {key.replace('_', ' ').title()}: {val}")

    wishes = memory.get("wishes", {})
    if wishes:
        lines.append("")
        lines.append("Wishes / Plans / Wants:")
        for key, entry in list(wishes.items())[:8]:
            val = entry.get("value") if isinstance(entry, dict) else entry
            if val:
                lines.append(f"  - {key.replace('_', ' ').title()}: {val}")

    notes = memory.get("notes", {})
    if notes:
        lines.append("")
        lines.append("Other notes:")
        for key, entry in list(notes.items())[:8]:
            val = entry.get("value") if isinstance(entry, dict) else entry
            if val:
                lines.append(f"  - {key}: {val}")

    if not lines:
        return ""

    header = "[WHAT YOU KNOW ABOUT THIS PERSON — use naturally, never recite like a list]\n"
    result = header + "\n".join(lines)
    if len(result) > 2000:
        result = result[:1997] + "…"

    return result + "\n"

def remember(key: str, value: str, category: str = "notes") -> str:
    valid = {"identity", "preferences", "projects", "relationships", "wishes", "notes"}
    if category not in valid:
        category = "notes"
    update_memory({category: {key: {"value": value}}})
    return f"Remembered: {category}/{key} = {value}"


def forget(key: str, category: str = "notes") -> str:
    memory = load_memory()
    cat    = memory.get(category, {})
    if key in cat:
        del cat[key]
        memory[category] = cat
        save_memory(memory)
        return f"Forgotten: {category}/{key}"
    return f"Not found: {category}/{key}"


forget_memory = forget


# ===========================================================================
# WO-6 SessionStore integration + JSON to SQLite migration (FR-4/FR-5/FR-6).
# Conversation/session memory DISTINCT from WO-4 core/memory_v2.py and from the
# flat-JSON FACT store above. The use_session_store flag is passed IN by callers
# (no flag read here). No store is constructed at import time.
# ===========================================================================


def _make_session_store(db_path: str, use_session_store: bool):
    """Return a SessionStore or None. Never raises (None == legacy mode)."""
    if not use_session_store or not _WHEEL_AVAILABLE:
        return None
    try:
        safe = Path(db_path).as_posix()
        _store_executor_mod.ensure_wal(safe)
        return _rust.SessionStore(safe)
    except Exception:
        import logging
        logging.getLogger(__name__).warning(
            "SessionStore init failed, falling back to legacy", exc_info=True
        )
        return None


# tasks.md step 1 names the factory `_session_store`; expose both spellings.
_session_store = _make_session_store


def migrate_sessions_if_needed(
    source_path: str, db_path: str, use_session_store: bool
) -> None:
    """Migrate JSON conversation history into the SessionStore SQLite DB.

    DESIGN (design.md section 7): memory/long_term.json is flat-JSON FACT memory
    with NO message log today, so the first run is a DOCUMENTED NO-OP: create an
    empty source if none exists, write a .bak backup (fsync file fd + dir fd)
    BEFORE any SQLite write, then round-trip 0 messages (0-message round-trip ==
    PASS, AC4). The same path carries real messages when a source later exists.

    Crash-safe: the .bak is durably flushed BEFORE the store is opened.
    Idempotent: if source_path + ".bak" already exists from a crashed prior
    attempt, the migration resumes from the .bak (loss-free by construction).

    use_session_store False or wheel absent: no-op. No .bak, no DB, legacy path
    untouched (FR-6, byte-identical to baseline).
    """
    if not use_session_store or not _WHEEL_AVAILABLE:
        return

    import json as _json
    import os
    import shutil
    import logging

    logger = logging.getLogger(__name__)

    source = Path(source_path)
    bak_path = Path(source_path + ".bak")

    def _fsync_path(path: Path) -> None:
        """fsync a file's bytes then fsync its containing directory (durable)."""
        try:
            with open(str(path), "ab") as fd:
                os.fsync(fd.fileno())
        except Exception:
            pass
        try:
            dir_fd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except Exception:
            pass

    # Step 1: determine the message list, writing .bak BEFORE any DB write.
    if bak_path.exists():
        # Resume from an existing .bak (a prior run crashed after writing .bak).
        logger.info("migrate_sessions_if_needed: resuming from existing .bak")
        try:
            raw = _json.loads(bak_path.read_text(encoding="utf-8"))
        except Exception:
            raw = []
        messages = raw.get("messages", []) if isinstance(raw, dict) else (
            raw if isinstance(raw, list) else []
        )
    elif source.exists():
        # Read the source, then write + fsync the .bak FIRST (crash-safe, PF-2).
        try:
            raw = _json.loads(source.read_text(encoding="utf-8"))
        except Exception:
            raw = {}
        # long_term.json is FACT memory with no "messages" key -> 0-message no-op.
        messages = raw.get("messages", []) if isinstance(raw, dict) else (
            raw if isinstance(raw, list) else []
        )
        shutil.copy2(str(source), str(bak_path))
        _fsync_path(bak_path)
    else:
        # No source exists today -- create an empty .bak, 0 messages (no-op).
        bak_path.write_text("[]", encoding="utf-8")
        _fsync_path(bak_path)
        messages = []

    if not isinstance(messages, list):
        messages = []

    # Step 2 + 3: construct the store and run the FULL migration on ONE thread.
    #
    # The PyO3 SessionStore is `unsendable` (sessions.rs:5) — it panics if any
    # method is called from a thread other than the one that constructed it. So
    # the store is built AND used entirely inside this one closure, which we hand
    # to store_executor.submit so it runs on the single-writer pool thread. That
    # both honours the unsendable contract and routes the writes through the
    # single-writer pool (contracts/store_wrappers.md §2). The path is
    # `.as_posix()`-normalised and `ensure_wal` runs on the pool thread, before
    # the ctor, inside the closure below (design.md:107-110).
    safe_db = Path(db_path).as_posix()

    def _do_migration() -> int:
        # Build the store on THIS (pool) thread — never crosses a thread boundary.
        try:
            _store_executor_mod.ensure_wal(safe_db)
            store = _rust.SessionStore(safe_db)
        except Exception as exc:  # pragma: no cover - wheel/ctor failure
            logger.warning(
                "migrate_sessions_if_needed: store construction failed: %s", exc
            )
            return -1

        # get_or_create returns a JSON Session string (serde) -> parse session_id.
        created = store.get_or_create(
            "user_default", "cli", "cli_user", "Default User"
        )
        parsed = _json.loads(created) if isinstance(created, str) else {}
        session_id = parsed.get("session_id")
        if not session_id:
            logger.warning("migrate_sessions_if_needed: no session_id, skipping")
            return -1

        migrated = 0
        for record in messages:
            if not isinstance(record, dict):
                continue
            role = record.get("role", "user")
            content = record.get("content", "")
            channel = record.get("channel", "cli")
            if not content:
                continue
            store.save_message(session_id, role, content, channel)
            migrated += 1

        # Round-trip verification — read every migrated message back identically.
        listed = store.list_sessions(False, 1000)
        sessions = _json.loads(listed) if isinstance(listed, str) else []
        read_back = 0
        for sess in sessions if isinstance(sessions, list) else []:
            if isinstance(sess, dict) and sess.get("session_id") == session_id:
                read_back = len(sess.get("messages", []) or [])
                break
        if read_back != migrated:
            logger.warning(
                "migrate_sessions_if_needed: round-trip mismatch "
                "(migrated=%d, read_back=%d)",
                migrated,
                read_back,
            )
        return migrated

    try:
        migrated = _store_executor_mod.submit(_do_migration).result(timeout=30.0)
    except Exception as e:
        logger.warning("migrate_sessions_if_needed: migration failed: %s", e)
        return

    if migrated < 0:
        return
    logger.info(
        "migrate_sessions_if_needed: migrated %d messages (no-op if 0), "
        "round-trip verified",
        migrated,
    )
