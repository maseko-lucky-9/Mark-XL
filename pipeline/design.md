# Design — WO-6 (Spec Kit `plan` → Architecture Plan)

> Phase 3 (Design) pipeline artifact. Blueprint only — no application code bodies; PyO3
> signature sketches and the threading diagram are design-level. Every load-bearing claim
> cites `file:line` against the live tree (branch `wo-6-maturity`, base `origin/main@cfcd5db`).
> The architecture decision is **LOCKED** (ADR 004): Option 3 — rebuild the Rust wheel,
> **arm64 macOS ONLY**, full WO-6 surface retained, **binding-exposure-only** (no new Rust
> storage logic, no schema change). This document does NOT re-litigate that decision.

---

## 1. Overview + the locked decision

WO-6 promotes Mark-XL from in-memory/JSON scratch state to durable, restart-surviving
persistence across four stores (scheduler, session, trace, telemetry) behind a single-writer
Qt-safe threading layer, and turns the repo into an installable, license-clean package. Every
change is feature-flagged OFF by default; flag-OFF (or wheel-absent) behaviour is byte-identical
to `cfcd5db`.

Phase-2 Analyze surfaced a feasibility blocker — at the Python boundary the prebuilt
`mark_xl_rust` wheel exposed READ-only methods for trace/telemetry
(`analysis.md:42-43`, `analysis.md:85-90`). On inspection of the Rust source the gap is
**binding-only**: the write path already exists in Rust
(`mark-xl-rust-fork/crates/openjarvis-traces/src/store.rs:57` `save`,
`mark-xl-rust-fork/crates/openjarvis-telemetry/src/store.rs:68` `record`); only the PyO3 layer
does not expose it. The orchestrator/user chose **Option 3** — rebuild the wheel, arm64-mac only,
retain the full surface (`docs/decisions/004-wo6-wheel-write-path-arm64.md:27-46`). The
`enable_telemetry` flag is **RETAINED** and AC5b added (ADR 004 line 61) — this **supersedes** the
3-flag re-scope sketched in the now-stale `analysis.md:146-153`. The canonical surface is the
4-flag spec (`pipeline/spec.md:83-103`).

---

## 2. `core/store_executor.py` threading design (NEW, shared, no flag of its own)

Mirrors the WO-1 adapter mechanism (`core/mark_xl_rust_adapter.py`) — same `ThreadPoolExecutor`
+ Qt-signal-bridge approach — but with a **single-writer** pool (`max_workers=1`), distinct from
the adapter's shared `max_workers=4` pool (`core/mark_xl_rust_adapter.py:77-92`). It introduces
**no second threading *mechanism*** and **reads no flags itself** — callers pass resolved flag
bools in (see §6).

```
                         Qt MAIN THREAD (event loop)
   ┌───────────────────────────────────────────────────────────────────────┐
   │  caller (task_queue / memory_manager / trace_collector / telemetry)     │
   │  resolves flag bool via get_flag(...)  ── flag stays in caller (§6) ──┐  │
   │                                                                       │  │
   │  store_executor.submit(store_call_fn, *args, flag_bool_passed_in) ────┼──┼─┐
   └───────────────────────────────────────────────────────────────────────┘  │ │
                                                                                │ │ submit()
                  ┌─────────────────────────────────────────────────────────────┘ │
                  ▼                                                                 │
   SINGLE-WRITER POOL  ThreadPoolExecutor(max_workers=1, thread_name_prefix=...)    │
   (lazy module-level, under a Lock; atexit.register(_pool.shutdown, wait=False))   │
   pattern mirrors core/mark_xl_rust_adapter.py:77-92 (their pool is max_workers=4) │
                  │                                                                 │
                  ▼  serialised — exactly one writer, no write contention           │
   Rust store call:  TraceStore.save / TraceStore.get / TraceStore.list_traces      │
                     TelemetryStore.record / SchedulerStore.* / SessionStore.*       │
                  │  (path already PRAGMA journal_mode=WAL by the owner — see §3)    │
                  ▼                                                                  │
   Future.add_done_callback ─► RustResultBridge-style QObject emits result_ready     │
   (pyqtSignal(object)) ── QUEUED cross-thread connection ───────────────────────────┘
                  │  mirrors core/mark_xl_rust_adapter.py:226-250
                  ▼
   Qt SLOT runs back ON the Qt main thread (no Qt object ever touched from the worker —
   the Qt-affinity contract, core/mark_xl_rust_adapter.py:17-25)
```

Cited contract points:
- Lazy module-level pool under a Lock + `atexit.register(_executor.shutdown, wait=False)`:
  mirror `core/mark_xl_rust_adapter.py:77-92`; WO-6 pool uses `max_workers=1` (single writer).
- `run_in_executor(fn, *args, **kwargs) -> Future`: mirror `core/mark_xl_rust_adapter.py:95-102`.
- `RustResultBridge(QObject)` with `result_ready = pyqtSignal(object)` + `.dispatch(fn, *args)`
  that emits on `add_done_callback`: mirror `core/mark_xl_rust_adapter.py:226-250` — the queued
  emit marshals the result back to the Qt main thread.
- No Qt object touched from a worker thread; results returned only via the signal: the affinity
  docstring contract at `core/mark_xl_rust_adapter.py:17-25`.

**Single-writer + WAL + no-second-mechanism contract (one sentence):** `store_executor`
serialises all four stores' writes through one `max_workers=1` pool (so single-writer
guarantees no SQLite write contention regardless of store, R9), every owned DB path is opened
WAL by the Python owner before the Rust store is constructed on it (§3, AC2), and it reuses the
WO-1 `ThreadPoolExecutor`+Qt-signal mechanism rather than inventing a new one.

---

## 3. WAL ownership (resolves A2 / R5 — concrete)

The Rust stores open the SQLite connection but do **not** set WAL:
`mark-xl-rust-fork/crates/openjarvis-traces/src/store.rs:21` (`Connection::open`) creates the
table (store.rs:25-41) with no `PRAGMA journal_mode`; the telemetry store is the same
(`mark-xl-rust-fork/crates/openjarvis-telemetry/src/store.rs:28-50`).

**Design:** the Python store-owner runs `PRAGMA journal_mode=WAL` on the path **it owns, before
constructing the Rust store on that path**:

1. Owner resolves the absolute DB path (use `Path(...).as_posix()` for all path *strings* —
   R7 / `markxl-local-vnv-is-macos-only`).
2. Open a short-lived `sqlite3.connect(path)`; execute `PRAGMA journal_mode=WAL`; read the
   returned mode to confirm; `close()`. (WAL is a persistent per-DB attribute, so one-time
   application sticks for subsequent Rust opens of the same file.)
3. **Then** construct the Rust store: `mark_xl_rust.TraceStore(path)` /
   `mark_xl_rust.TelemetryStore(path)` / `SchedulerStore` / `SessionStore` — each takes a path
   string (PyO3 ctor `traces.rs:13-24`, `telemetry.rs:13-24`).

This WAL-pragma step lives in `core/store_executor.py` as a helper
(`ensure_wal(path) -> None`) so all four store-owners reuse one implementation; the owner calls
it on the executor thread before the first write. AC2 asserts WAL is enabled on every opened DB
(`pipeline/spec.md:84`). Single writer (`max_workers=1`) means no write contention regardless of
journal mode; WAL additionally lets the Qt main thread read without blocking the writer.

---

## 4. Module map

Legend — **Lane W** = depends on the wheel rebuild (the new PyO3 bindings); landing it first
unblocks trace/telemetry. Scheduler/session/packaging do **not** depend on Lane W (their PyO3
methods already exist — `analysis.md:25-26`, `analysis.md:40-41`).

| File | NEW / MOD | Responsibility | Flag gate | Lane W? |
|---|---|---|---|---|
| `core/store_executor.py` | NEW | Single-writer `ThreadPoolExecutor(max_workers=1)` + `RustResultBridge`-style Qt bridge + `ensure_wal(path)`. No flag read; flag bools passed in. | none (shared) | No (mechanism only) |
| `agent/task_queue.py` | MOD | Persist/reload scheduled tasks via `SchedulerStore`; preserve `TaskStatus`/`TaskPriority` (`agent/task_queue.py:9-14,17-20,23-34`); singleton at `:210-221`. flag bool resolved by caller `main.py`/`agent/executor.py` and passed in. | `use_scheduler_store` | No (SchedulerStore methods present — `analysis.md:25`) |
| `memory/memory_manager.py` | MOD | Session continuity via `SessionStore`; JSON→SQLite migration with `.bak` (§7). Flat-JSON FACT store today (`memory/memory_manager.py:14-28`). | `use_session_store` | No (SessionStore methods present — `analysis.md:26`) |
| `core/trace_collector.py` | NEW | Wrap rebuilt `TraceStore`/`TraceCollector`; route `save`/`start_trace`+`end_trace` through `store_executor`. | `enable_trace_store` | **Yes** |
| `core/telemetry.py` | NEW | Wrap rebuilt `TelemetryStore.record` + `PyTelemetryRecord` read-back; route through `store_executor`. | `enable_telemetry` | **Yes** |
| `config/flags.json` | MOD | Add 4 booleans default `false`: `use_scheduler_store`, `use_session_store`, `enable_trace_store`, `enable_telemetry`. Existing keys at `config/flags.json` (6 bools + 3 int tunables) unchanged. | — | No |
| `tests/test_flags.py` | UNCHANGED (preferred) | Allowlist `assert set(callers)=={"main.py","agent/executor.py"}` (`tests/test_flags.py:139`); substring scan `"get_flag" in src` (`:131`). UNCHANGED under the preferred design (§6). | — | No |
| `mark_xl/__main__.py` | NEW | `python -m mark_xl` shim → calls `main.main` (`main.py:1493`). (§10) | — | No |
| `pyproject.toml` | NEW (root) | Build metadata + `[project.scripts] mark-xl = "main:main"` (§10). No root pyproject today (`analysis.md:37`). | — | No |
| `LICENSE` | NEW | MIT (AC8). | — | No |
| `NOTICE` | NEW | Apache-2.0 attribution for vendored OpenJarvis (AC8). | — | No |
| `requirements.txt` | MOD | Exact-pin all deps (0% pinned today — `analysis.md:38`). | — | No |
| `docs/VENDORING.md` | MOD (exists) | Extend with arm64-macOS-only wheel build/re-vendor procedure (`docs/VENDORING.md` present; A4 — drop "git-subtree" wording). | — | documents Lane W |
| `mark-xl-rust-fork/crates/openjarvis-python/src/traces.rs` | MOD | Add `save`/`get`/`list_traces` to `PyTraceStore`; add a record path to `PyTraceCollector` (§5). | — | **Yes** |
| `mark-xl-rust-fork/crates/openjarvis-python/src/telemetry.rs` | MOD | Add `record(...)` to `PyTelemetryStore`; add `PyTelemetryRecord` pyclass (§5). | — | **Yes** |
| `mark-xl-rust-fork/crates/openjarvis-python/src/lib.rs` | MOD | Register `PyTelemetryRecord` near `lib.rs:142`; no new add_class for the stores (already at `lib.rs:138,149`). | — | **Yes** |
| `.github/workflows/ci.yml` | MOD | Gate new store tests to arm64-mac; source `$HOME/.cargo/env` in the wheel build (§5). | — | gates Lane W |

---

## 5. Lane W — the wheel-rebuild lane (lands FIRST)

Lane W lands first because `core/trace_collector.py` and `core/telemetry.py` cannot write
without the new bindings; scheduler/session/packaging do not depend on it and can proceed in
parallel.

### 5.1 Exact PyO3 additions (binding-exposure only; no new Rust logic)

All four Rust methods already exist — we only surface them. Recommended boundary is the
**JSON-string round-trip** for traces (the Rust schema already serdes `steps_json`/`metadata_json`
at `store.rs:58-59,81-82`), avoiding nested `PyTrace`/`PyTraceStep` pyclasses and the PyO3
arg-order trap on nested types.

**`PyTraceStore`** (`traces.rs`, currently only `new` + `count` at `traces.rs:13-30`):
```rust
// sketch — add to impl PyTraceStore #[pymethods]
fn save(&self, trace_json: &str) -> PyResult<()>;
    // serde_json::from_str::<Trace>(trace_json) -> self.inner.save(&trace)  (store.rs:57)
fn get(&self, trace_id: &str) -> PyResult<Option<String>>;
    // self.inner.get(trace_id)? -> Option<Trace> -> serde_json::to_string  (store.rs:92)
#[pyo3(signature = (limit=100, offset=0))]
fn list_traces(&self, limit: usize, offset: usize) -> PyResult<Vec<String>>;
    // self.inner.list_traces(limit, offset)? -> map each Trace -> JSON str  (store.rs:139)
```

**`PyTraceCollector`** (`traces.rs:33-50`, currently only `new` + `active_count`). The real Rust
API is `start_trace(trace_id,query,agent,model)` + `add_step` + `end_trace(trace_id,result,
outcome) -> Result` which calls `store.save` internally
(`mark-xl-rust-fork/crates/openjarvis-traces/src/collector.rs:23,43,49-68`). Expose these real
methods (do NOT invent a `record`):
```rust
fn start_trace(&self, trace_id: &str, query: &str, agent: &str, model: &str);  // collector.rs:23
#[pyo3(signature = (trace_id, step_json))]
fn add_step(&self, trace_id: &str, step_json: &str) -> PyResult<()>;
    // serde_json::from_str::<TraceStep>(step_json) -> inner.add_step  (collector.rs:43; TraceStep core/types.rs:364)
#[pyo3(signature = (trace_id, result, outcome=None))]
fn end_trace(&self, trace_id: &str, result: &str, outcome: Option<&str>) -> PyResult<()>;
    // inner.end_trace(...) — persists via store.save  (collector.rs:49-68)
```

**`PyTelemetryStore`** (`telemetry.rs:6-37`, currently only `new`+`count`+`clear`). Build the
`TelemetryRecord` **field-by-field by name**, NEVER positionally — `TelemetryRecord` is a struct
with ~38 `#[serde(default)]` fields (`mark-xl-rust-fork/crates/openjarvis-core/src/types.rs:276`);
Python kw order vs Rust struct-field order can silently swap (WO-4 PyO3 arg-order trap):
```rust
#[pyo3(signature = (model_id, prompt_tokens=0, completion_tokens=0, total_tokens=0,
                    latency_seconds=0.0, ttft=0.0, cost_usd=0.0, timestamp=None))]
#[allow(clippy::too_many_arguments)]
fn record(&self, model_id: &str, prompt_tokens: i64, completion_tokens: i64,
          total_tokens: i64, latency_seconds: f64, ttft: f64, cost_usd: f64,
          timestamp: Option<f64>) -> PyResult<()> {
    let rec = openjarvis_core::TelemetryRecord {
        model_id: model_id.to_string(),
        prompt_tokens, completion_tokens, total_tokens,          // i64 (types.rs:276)
        latency_seconds, ttft, cost_usd,                         // f64 — NOT f32 (no embeddings here, but stated)
        timestamp: timestamp.unwrap_or_else(now_secs_f64),
        ..Default::default()                                     // ~38 serde(default) fields incl energy/gpu DEFAULT 0
    };
    self.inner.record(&rec)  // store.rs:68
}
```

**`PyTelemetryRecord`** (NEW pyclass, for AC5b read-back) with getters
`model_id`/`prompt_tokens`/`completion_tokens`/`total_tokens`/`latency_seconds`/`ttft`/`cost_usd`.
Register it in `lib.rs` near `lib.rs:142` (`m.add_class::<telemetry::PyTelemetryRecord>()?;`).
**No** new `add_class` for the stores — `PyTraceStore`/`PyTelemetryStore` are already registered
(`lib.rs:138,149`); only their `#[pymethods]` grow.

**Hard constraints:** NO new Rust storage logic; NO telemetry schema change; numeric persistence
fields are `f64` (no embeddings in this lane, so the WO-4 f32/f64 vector trap is N/A — stated for
completeness).

### 5.2 arm64-macOS-only maturin rebuild + re-vendor

```bash
source "$HOME/.cargo/env"                                  # cargo 1.96.0 is OFF default PATH (WO-4 lesson)
source /Users/ltmas/Repo/agents/mark-xl/.venv-mac/bin/activate   # maturin 1.14.1 lives in the venv, not system PATH
cd /Users/ltmas/Repo/agents/mark-xl/mark-xl-rust-fork/crates/openjarvis-python   # the wheel member
maturin build --release --out ../../../dist                # mirrors ci.yml:112-113 working-directory + --out
# rust-toolchain.toml pins channel 1.88 (min); 1.96.0 satisfies. manylinux N/A (native arm64 host build).
```
Re-vendor the built wheel into the repo as in WO-4 (**copy-not-subtree** — `docs/VENDORING.md` is
explicitly NOT a git subtree, `analysis.md:39`). Verify by exercising the wheel
(`python -c "import mark_xl_rust as m; print(sorted(n for n in dir(m.TraceStore())))"` must now
show `save`/`get`/`list_traces`, `TelemetryStore` must show `record`).

### 5.3 Concrete CI changes (`.github/workflows/ci.yml`)

Current shape (read this session): job `test` matrix `os=[macos-14, macos-13, windows-latest,
ubuntu-latest] × py3.11-3.13` (`ci.yml:34-35`); job `rust-wheel` matrix `os=[macos-14,
ubuntu-latest, windows-latest]` (`ci.yml:94`); job `import-smoke` (`ci.yml:163`).

- **Wheel build step** (`ci.yml:107-123`): keep `working-directory:
  mark-xl-rust-fork/crates/openjarvis-python`, `manylinux: "off"`, `sccache: true`. Add a step (or
  a `before`/shell wrapper) that runs `source "$HOME/.cargo/env"` before maturin (Pre-Flight
  finding — cargo off default PATH). The `dtolnay/rust-toolchain@1.88` pin (`ci.yml:104-105`) is
  satisfied by the local 1.96.0.
- **New Rust-backed STORE tests** (trace `save`/`list_traces`/`get`, telemetry `record` round-trip)
  run **only on arm64 macOS** (`macos-14` / `macos-latest`, NOT `macos-13` x86 — won't allocate,
  `markxl-wo0-ci-env-gaps`; `pipeline/spec.md:90`). Gate each with a pytest skip marker:
  `skipif(not WHEEL_AVAILABLE or not (platform.machine()=='arm64' and sys.platform=='darwin'))`.
  Because the arm64-only wheel won't import elsewhere, `WHEEL_AVAILABLE` alone is sufficient on
  Linux/Windows; the platform clause is belt-and-braces on a mac that lacks the wheel.
- **Linux/Windows lanes** continue to build the wheel in `rust-wheel` (their builds may stay) but
  their store-write tests are SKIPPED (wheel-absent path); they run the legacy in-memory/JSON
  fallback.
- **AC6 flag-OFF byte-parity** MUST stay green on EVERY lane (`pipeline/spec.md:89,101`;
  `ci.yml:185-192` import-smoke already asserts wheel-absent graceful degrade — extend the spirit
  to the store wrappers).
- `macos-13` (x86) is **informational, not required** (`pipeline/spec.md:90,102`).

---

## 6. FR-18 allowlist strategy (preferred + fallback)

Detection: `tests/test_flags.py:131` scans every production `.py` for the literal substring
`"get_flag"`; `:139` asserts `set(callers) == {"main.py","agent/executor.py"}` (R4, HIGH —
`analysis.md:107`).

**Preferred (WO-3 style — keeps the allowlist UNCHANGED):** the new store modules
(`core/store_executor.py`, `core/trace_collector.py`, `core/telemetry.py`, and the modified
`agent/task_queue.py` if it isn't already a caller) **never contain the literal `get_flag`
substring**. The two existing callers resolve the flag bools and pass them in:
- `main.py` is already a `get_flag` caller (`main.py:536-872` range verified) — it resolves
  `use_scheduler_store` / `use_session_store` / `enable_trace_store` / `enable_telemetry` and
  passes the bools to the store wrappers / `store_executor.submit(...)`.
- `agent/executor.py` is already a caller — passes its needed bool(s) in.

Result: `set(callers)` stays exactly `{"main.py","agent/executor.py"}` and
`tests/test_flags.py` is **UNCHANGED**. **Substring-scan trap:** ensure NO stray `get_flag` text
appears in comments, docstrings, or string literals of any new file (the scan is a dumb substring
match, not an AST call-graph).

**Fallback (only if a new file genuinely must call `get_flag`):** extend the exact set at
`tests/test_flags.py:139`, and update the `:140-143` failure message, **in lockstep** — add the
new module name to both the allowlist set and the message. This is a config-file-adjacent test
change; flag it for review.

---

## 7. Migration design (resolves A5 / R6)

VERIFIED: `memory/memory_manager.py:14-28` is a flat-JSON **FACT** store
(identity/preferences/projects/relationships/wishes/notes) — there is **no message/conversation
log** today, so there is no conversation source to migrate.

**First-session migration = a DOCUMENTED NO-OP that still satisfies `.bak` + round-trip (AC4):**
1. On first run with `use_session_store` ON, if no conversation-history source file exists, treat
   the source as empty (create an empty source or operate on empty).
2. **Crash-safe `.bak` BEFORE any SQLite write** (Pre-Flight LOW finding): `shutil.copy2(source,
   source + ".bak")` then `os.fsync` the `.bak` file descriptor, and `fsync` the containing
   directory, **before** opening the `SessionStore` write. If `source.bak` already exists from a
   crashed prior attempt, resume from `.bak` (idempotent, loss-free by construction).
3. Run a trivial round-trip: 0 messages migrate, 0 read back identically == PASS (AC4
   `.bak`-written + round-trip-identical, `pipeline/spec.md:86`).

`SessionStore` PyO3 methods are **all already present**
(`get_or_create`/`save_message`/`list_sessions`/`link_channel`/`consolidate`/`decay` —
`analysis.md:26`), so the session lane is **NOT blocked on the wheel rebuild** (not Lane W). When
a real conversation source later exists, the same `.bak`-then-migrate path carries actual
messages; the design is forward-compatible.

---

## 8. flag-OFF byte-parity design (NFR-3 / R8)

- **NO module-level store construction at import time** in any new/modified module (R8,
  `analysis.md:111`). Importing `core/trace_collector.py`/`core/telemetry.py`/`core/store_executor.py`
  must have zero side effects (no pool spawned, no DB opened) — the pool is lazy (mirrors
  `core/mark_xl_rust_adapter.py:77-92`).
- Every store path is gated on **(flag bool passed in) AND `WHEEL_AVAILABLE`**
  (`core/mark_xl_rust_adapter.py:56-62` sets `WHEEL_AVAILABLE`, never raises on a missing wheel).
  Stores are constructed lazily only when **both** are true.
- flag-OFF **or** wheel-absent ⇒ the legacy in-memory list / JSON path
  (`agent/task_queue.py:39-42` list+Lock+Condition+dict; `memory/memory_manager.py` flat JSON),
  byte-identical to `cfcd5db`.
- AC6 tests BOTH conditions (all four flags OFF; and wheel absent — the normal Linux/Windows
  state) and MUST be green on every CI lane (`pipeline/spec.md:89,101`).

---

## 9. Risk mitigations (carry R1–R9 from `analysis.md:104-112` → concrete design decisions)

| ID | Risk (sev) | Concrete design decision |
|---|---|---|
| R1 | trace/telemetry no Python write path (CRITICAL) | **RESOLVED by the rebuild** (ADR 004; §5 binding additions expose `save`/`record`). No longer a blocker. |
| R2 | `TelemetrySample` hardware-only (HIGH) | Folded into R1 — telemetry persistence uses the SEPARATE `TelemetryRecord` (`types.rs:276`) via the NEW `record` binding, NOT `PyTelemetrySample` (the hardware ring-buffer, `telemetry.rs:98-170`). |
| R3 | `TraceCollector` ctor takes a store not a path (LOW) | Wire `TraceStore(path)` → `TraceCollector(store)` (PyO3 ctor `traces.rs:41-45`; Rust `collector.rs:16`); confirmed real API in §5.1. |
| **R4** | FR-18 substring scan + exact-set `==` (HIGH) | §6 preferred design: pass flag bools in; new files contain NO `get_flag` substring; allowlist UNCHANGED. Fallback documented in lockstep. |
| R5 | WAL ownership ambiguous (MEDIUM) | §3: Python owner runs `PRAGMA journal_mode=WAL` via `store_executor.ensure_wal(path)` BEFORE constructing the Rust store; AC2 asserts. |
| R6 | migration source undefined (MEDIUM) | §7: documented NO-OP first migration; `.bak` + round-trip stands; crash-safe resume. |
| R7 | local V&V macOS-only → Win/Linux slip (MEDIUM) | `gh pr checks` is the gate before merge (`markxl-local-vnv-is-macos-only`); `.as_posix()` all path *strings*; CI Linux/Win lanes run the legacy path green. |
| **R8** | flag-OFF byte-drift via import side-effects (MEDIUM) | §8: no module-level store construction; gate on flag AND `WHEEL_AVAILABLE`; AC6 == baseline on every lane. |
| R9 | single-writer contention/deadlock (MEDIUM) | §2: `max_workers=1` serialises all writes (no contention by construction); AC2 QTimer + 100 writes ≥30 FPS guards the Qt main thread. |

**Pre-Flight carry-forwards:**
- **PF-1 (maturin/cargo env-sourcing):** `source "$HOME/.cargo/env"` before maturin, both locally
  (§5.2) and in the CI wheel-build step (§5.3) — cargo 1.96.0 is off the default PATH (WO-4).
- **PF-2 (crash-safe `.bak`):** `shutil.copy2` + `fsync` the `.bak` (and dir) BEFORE opening the
  SQLite write; resume from an existing `.bak` (§7).

Every HIGH/CRITICAL risk has a recorded mitigation: R1 (CRITICAL) RESOLVED; R4 (HIGH) §6;
R2 (HIGH) folded into R1.

---

## 10. `python -m mark_xl` shim shape (A3)

Both mechanisms satisfy AC3 (`pipeline/spec.md:85`), and the design ships both for robustness:
- **`mark_xl/__main__.py`** (NEW): a thin shim — `from main import main; main()` — so
  `python -m mark_xl` works. CONFIRMED entry: `main.py:1493` `def main() -> None:` guarded by
  `main.py:1540` `if __name__ == "__main__": main()`.
- **`pyproject.toml`** (NEW, root): `[project.scripts]\nmark-xl = "main:main"` provides the
  `mark-xl` console script. (No root pyproject/setup today — `analysis.md:37`.)

A `mark_xl/` package dir holding `__main__.py` (plus `__init__.py`) is the minimal addition;
`main.py` stays at root and remains the single entry function (`main:main`).

---

## 11. Lessons Applied

- **WO-1 adapter mirror:** `store_executor` reuses the exact `ThreadPoolExecutor` + `atexit
  wait=False` + `RustResultBridge` Qt-signal mechanism (`core/mark_xl_rust_adapter.py:77-92,
  226-250, 17-25`) — single-writer `max_workers=1`, no second mechanism (§2).
- **WO-3 pass-bools-in to keep the allowlist at 2:** new files never contain `get_flag`; callers
  stay `{"main.py","agent/executor.py"}`; `test_flags.py` UNCHANGED (§6, R4).
- **WO-4 maturin/cargo-env + PyO3 arg-order trap + f64:** `source "$HOME/.cargo/env"` before
  maturin (§5.2/5.3); build `TelemetryRecord` field-by-field NOT positionally (§5.1); numeric
  fields `f64` (§5.1).
- **flag-OFF byte-parity (WO-2/WO-3):** no module-level store construction; gate on flag AND
  `WHEEL_AVAILABLE`; AC6 == `cfcd5db` baseline on every lane (§8, R8).
- **Trust-the-live-tree (Phase 1/2):** the binding gap and the real `TraceCollector` API
  (`start_trace`/`end_trace`, NOT an invented `record`) were read from the Rust source
  (`collector.rs:23,49-68`), not the prose (§5.1, R3).
- **`markxl-local-vnv-is-macos-only` → `.as_posix()` + `gh pr checks` gate:** all path strings
  `.as_posix()`; `gh pr checks` is the pre-merge gate; Linux/Win lanes run the legacy path green
  (§3, R7).
- **Verify-by-exercising-the-wheel:** acceptance of Lane W is `dir()` on the rebuilt classes
  showing `save`/`record`, then the AC5/AC5b round-trip tests — not symbol presence alone (§5.2).

---

## 12. AC traceability

| AC | Design element |
|---|---|
| **AC1** (scheduler restart) | `agent/task_queue.py` MOD → `SchedulerStore` persist/reload, preserve `TaskStatus`/`TaskPriority` (`task_queue.py:9-34`); `use_scheduler_store` gate (§4, §8). |
| **AC2** (concurrency/FPS + WAL) | `core/store_executor.py` single-writer pool + Qt bridge (§2); `ensure_wal(path)` (§3); R9 max_workers=1. |
| **AC3** (entry point) | `mark_xl/__main__.py` + `pyproject.toml [project.scripts]` → `main:main` (`main.py:1493,1540`) (§10). |
| **AC4** (migration loss-free) | `memory/memory_manager.py` MOD: crash-safe `.bak` + round-trip NO-OP first migration (§7, PF-2). |
| **AC5** (trace capacity + schema) | Lane W `PyTraceStore.save`/`get`/`list_traces` (§5.1); `enable_trace_store`; WO-7-ready schema (`store.rs:26-41`, `steps_json` ordered). |
| **AC5b** (telemetry round-trip) | Lane W `PyTelemetryStore.record` + `PyTelemetryRecord` getters (§5.1); `enable_telemetry`; aggregator non-zero (`telemetry.rs:55-59`). |
| **AC6** (flag-OFF baseline) | §8 no module-level construction, gate on flag AND `WHEEL_AVAILABLE`; green on every lane (§5.3). |
| **AC7** (CI matrix green) | §5.3 arm64-mac runs wheel+store tests; Linux/Win skip arm64-only tests; macos-13 informational. |
| **AC8** (packaging + licensing) | `LICENSE` (MIT), `NOTICE` (Apache-2.0), pinned `requirements.txt`, extended `docs/VENDORING.md` (§4). |

