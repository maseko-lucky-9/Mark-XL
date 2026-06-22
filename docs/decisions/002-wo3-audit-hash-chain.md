# 002 — Pure-Python SQLite tamper-evident audit hash-chain (vs the wheel's unbound `AuditLogger`)

- **Status:** Accepted
- **Date:** 2026-06-22
- **Work order:** WO-3 (Security & Safety)
- **Deciders:** Design-phase lead (Phase 3), `planner`, `security-auditor`

## Context and forces

WO-3 requires a tamper-evident audit log (FR-8, AC6): every gated tool dispatch must write a record
carrying `(ts, tool, redacted_params, redacted_result, confirm_required, approved)` and expose a
`verify_chain()` that detects an altered or removed record. The spec under-scoped this to a "forward
handoff" one-liner — assuming the bundled `mark_xl_rust` wheel could write the records.

Live `.venv-mac` introspection of the wheel (Analyze phase, `pipeline/wo-3/analysis.md §1`) proved
otherwise:

1. **`mark_xl_rust.AuditLogger(path=None)` exposes `count()`, `tail_hash()`, `verify_chain()` — but NO
   bound write / append / log / record method.** `AuditLogger(tmp)` constructs; `count()→0`;
   `tail_hash()→''`; `verify_chain()→(True, None)`. There is no public way to APPEND a record through the
   wheel. AC4/AC6 cannot be satisfied by a wheel call.
2. **No Rust fork-patch is in scope.** The constitution bans a fork-patch ("the wheel API is sufficient
   as confirmed") and WO-1 is frozen. Adding a Rust `append` would be a cross-WO change.
3. **`get_flag` 2-copy allowlist (FR-14 / NFR-5).** Two static tests assert the `get_flag` caller set is
   exactly `{main.py, agent/executor.py}` (`tests/test_flags.py:139`, `tests/test_wo2_registry.py:431`).
   The audit module must not become a 3rd caller.
4. **Threading (NFR-1).** Audit writes must not block the Qt main thread; they route through the WO-1
   adapter's `ThreadPoolExecutor` (`core/mark_xl_rust_adapter.run_in_executor`).
5. **Cross-platform fragility (NFR-4).** macOS-only local V&V missed the WO-2 Windows regression. Any
   `chmod`, path stringification, or file read must be cross-platform-guarded.
6. **Secret hygiene (FR-9 / NFR-9).** No raw secret may ever reach the log; redaction must precede every
   write.

## Decision

Author a **new, self-contained pure-Python `core/audit.py`** SQLite (WAL) tamper-evident hash-chain
writer as the **source of truth** for the audit log. Each record is hash-chained:

```
hash = sha256( prev_hash.encode("utf-8")
             + canonical_json({ts, tool, redacted_params, redacted_result,
                               confirm_required, approved, prev_hash}).encode("utf-8") ).hexdigest()
canonical_json = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
```

with a genesis `prev_hash = "0"*64`. `verify_chain()` recomputes the entire chain from the stored
fields and returns False on the first mismatch or broken linkage — detecting both an altered record and
a removed one. `SecretScanner.redact` (via the adapter) is applied to params AND result **before**
serialization. The DB lives at gitignored `data/audit.db`; on creation
`if os.name != 'nt': os.chmod(path, 0o600)`; paths are stringified via `Path(...).as_posix()`; reads use
`encoding="utf-8"`. The module contains **zero** `get_flag` literals — the `enable_security_gates` bool
is read only in the two sanctioned callers and passed in as a parameter.

The wheel's `AuditLogger(path).verify_chain()` MAY be invoked as an **optional, independent
cross-check** (it reads the same file) via `core/mark_xl_rust_adapter.audit_verify_crosscheck`, but the
Python `verify_chain` is authoritative and the app functions fully when the wheel is absent
(NFR-7 graceful degradation — the ledger is pure stdlib).

## Consequences

**Positive**
- AC4 + AC6 become satisfiable without a Rust change (BLOCKER-2 resolved in pure Python — within scope).
- Hermetic: no live engine/runtime, no server, no network. Works wheel-present or wheel-absent.
- Tamper-evidence is provable by recomputation; `canonical_json` (sorted, compact) makes the hash stable
  and reproducible across the two modules that compute it.
- Secret redaction is enforced at the single write chokepoint — no raw secret can reach disk.
- The 600-perm + `os.name` guard + `.as_posix()` + utf-8 reads keep it cross-platform (NFR-4).

**Negative / trade-offs**
- Two `verify_chain` implementations now exist (Python authoritative + wheel cross-check) — the wheel's
  is advisory only, accepted to keep an independent second opinion without coupling.
- SQLite appears in a repo whose `anti-patterns.md` bans "SQLite for production." This is a LOCAL,
  single-user, append-only audit ledger in a desktop PyQt6 app — NOT a production web datastore — so it is
  explicitly outside the ban's intent (which targets SQLite as a scalable service backend). Recorded here
  so the choice is not later mistaken for a banned-stack violation. `banned_stack: pass`.
- Concurrency: SQLite under concurrent writers can lock. Mitigated by WAL mode + a single-retry backoff +
  a single shared `AuditLedger` instance per process; AUDIT_PRE fails closed on persistent failure.

## Alternatives rejected

- **Call the wheel's `AuditLogger` to write records** — impossible: no bound write/append method exists
  (introspected). This is the BLOCKER that forced the decision.
- **Fork-patch the Rust crate to add an `append`** — banned by the constitution; WO-1 is frozen; out of
  WO-3 scope.
- **Append-only JSONL hash-chain file (no SQLite)** — viable, but SQLite gives atomic transactions,
  WAL durability, and `count()`/`tail_hash()` parity with the wheel's surface for free; the chain logic is
  identical either way. Chose SQLite for crash-safe append semantics.
- **Plaintext / unchained log** — fails AC6 (no tamper-evidence) and FR-9 if unredacted.
- **Add `get_flag` to `core/audit.py` to self-gate** — would break the 2-copy allowlist tests; rejected in
  favor of passing the flag bool in as a parameter (design §8).
