# Tasks — WO-6 (Development Phase) — Spec Kit `tasks` artifact

> Phase 4 (Development) pipeline artifact. Produced by the development-lead from
> `pipeline/design.md`, `pipeline/contracts/`, `pipeline/spec.md`, and
> `pipeline/state.json`. Architecture is LOCKED (ADR 004). Do NOT re-litigate
> the lane order or Option 3 decision.
>
> **Lane order (mandatory):**
> W (PyO3 bindings + arm64 wheel rebuild + re-vendor)
> → SHARED (core/store_executor.py)
> → A (scheduler + session + migration) / B (trace + telemetry wrappers — depends on W)
> → C (packaging + licensing + flags + CI)
>
> **TDD contract:** every test-bearing task follows red→green inside the inner loop.
> The per-task test module is `tests/test_wo6_stores.py` unless stated otherwise.
> Do NOT run the full integration suite — that is Phase 5.

---

## Lane W — PyO3 bindings + arm64 wheel rebuild + re-vendor (LANDS FIRST)

### T01 — Add `PyTraceStore` write-path bindings (`traces.rs`)

**Lane:** W
**Files touched:**
- `mark-xl-rust-fork/crates/openjarvis-python/src/traces.rs`

**FRs/NFRs satisfied:** FR-7 (converge c1), AC5
**Dependencies:** none (first task)
**Parallel-eligible:** no (W must land sequentially; T01–T05 in order)

**Steps (1–3):**
1. Add `fn save(&self, trace_json: &str) -> PyResult<()>` to `impl PyTraceStore #[pymethods]` — `serde_json::from_str::<openjarvis_core::Trace>(trace_json)? -> self.inner.save(&trace)` (Rust `store.rs:57`). Map errors via `.map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))` (established pattern, `traces.rs:20`).
2. Add `fn get(&self, trace_id: &str) -> PyResult<Option<String>>` — `self.inner.get(trace_id)? : Option<Trace> -> map(|t| serde_json::to_string(&t))` (`store.rs:92`).
3. Add `#[pyo3(signature = (limit=100, offset=0))] fn list_traces(&self, limit: usize, offset: usize) -> PyResult<Vec<String>>` — `self.inner.list_traces(limit, offset)? : Vec<Trace> -> map(serde_json::to_string)` (`store.rs:139`). No new `add_class` — `PyTraceStore` is already registered at `lib.rs:149`.

**Acceptance checks:**
- `traces.rs` compiles (`cargo check` in crate dir) with no new warnings.
- `PyTraceStore` `#[pymethods]` block contains exactly the three new methods: `save`, `get`, `list_traces`. No new `add_class` added to `lib.rs`.
- Zero new Rust storage logic — each method body is a single `serde_json` + `self.inner.*` call.

---

### T02 — Add `PyTraceCollector` record-path bindings (`traces.rs`)

**Lane:** W
**Files touched:**
- `mark-xl-rust-fork/crates/openjarvis-python/src/traces.rs`

**FRs/NFRs satisfied:** FR-7, FR-8, AC5
**Dependencies:** T01 (same file — apply in sequence)
**Parallel-eligible:** no

**Steps (1–3):**
1. Add `fn start_trace(&self, trace_id: &str, query: &str, agent: &str, model: &str)` — calls `self.inner.start_trace(trace_id, query, agent, model)` (`collector.rs:23`); infallible (no return).
2. Add `#[pyo3(signature = (trace_id, step_json))] fn add_step(&self, trace_id: &str, step_json: &str) -> PyResult<()>` — `serde_json::from_str::<openjarvis_core::TraceStep>(step_json)? -> self.inner.add_step(trace_id, step)` (`collector.rs:43`). No-op if `trace_id` not active (Rust behaviour preserved).
3. Add `#[pyo3(signature = (trace_id, result, outcome=None))] fn end_trace(&self, trace_id: &str, result: &str, outcome: Option<&str>) -> PyResult<()>` — `self.inner.end_trace(trace_id, result, outcome)` (`collector.rs:49-68`; persists via `store.save` at `collector.rs:65`). Do NOT add a `record` method (R3, `design.md:168-172`). No new `add_class` — `PyTraceCollector` already registered at `lib.rs:150`.

**Acceptance checks:**
- `start_trace`/`add_step`/`end_trace` present in `PyTraceCollector #[pymethods]`; no `record` method added.
- Lifecycle test (Rust unit level or post-build Python): `start_trace` → `add_step` → `end_trace` results in `store.count()==1`, `collector.active_count()==0` (`contracts/pyo3_bindings.md §2` acceptance).
- No new Rust types, no schema change.

---

### T03 — Add `PyTelemetryStore.record` binding + `PyTelemetryRecord` pyclass (`telemetry.rs`)

**Lane:** W
**Files touched:**
- `mark-xl-rust-fork/crates/openjarvis-python/src/telemetry.rs`

**FRs/NFRs satisfied:** FR-9, AC5b
**Dependencies:** T01, T02 (same crate — apply after T01/T02)
**Parallel-eligible:** no

**Steps (1–3):**
1. Add `PyTelemetryStore.record` with signature `#[pyo3(signature = (model_id, prompt_tokens=0, completion_tokens=0, total_tokens=0, latency_seconds=0.0, ttft=0.0, cost_usd=0.0, timestamp=None))]` and `#[allow(clippy::too_many_arguments)]`. Build `TelemetryRecord` field-by-field by name with `..Default::default()` (NEVER positionally — WO-4 PyO3 arg-order trap, `design.md:184-186`). Fields: `prompt_tokens`/`completion_tokens`/`total_tokens` are `i64`, `latency_seconds`/`ttft`/`cost_usd`/`timestamp` are `f64` (`core/types.rs:276-291`). Call `self.inner.record(&rec)` (`store.rs:68`). Include `now_secs_f64()` helper for when `timestamp` is `None` (mirror `collector.rs:24-27`). Map error to `PyRuntimeError`.
2. Add `PyTelemetryRecord` as a NEW `#[pyclass(name = "TelemetryRecord")]` with `inner: openjarvis_core::TelemetryRecord` and `#[getter]` methods: `model_id(&str)`, `prompt_tokens(i64)`, `completion_tokens(i64)`, `total_tokens(i64)`, `latency_seconds(f64)`, `ttft(f64)`, `cost_usd(f64)`, `timestamp(f64)`. Mirror `PyTelemetrySample` getter pattern (`telemetry.rs:138-169`). No new `add_class` for `PyTelemetryStore` (already at `lib.rs:138`).
3. Resolve the AC5b [UNVERIFIED] read-back path: inspect whether a `TelemetryStore.list_records` / `get` method can expose `PyTelemetryRecord` instances, OR confirm that AC5b round-trip will assert via `TelemetryAggregator.stats()`+`count()` plus the struct fields (state.json `design_carry_forward.unverified`). Document the resolution in a code comment in `telemetry.rs` and in the T03 task completion note. This is a REQUIRED resolution before T13 (the telemetry wrapper test).

**Acceptance checks:**
- `PyTelemetryStore` `#[pymethods]` contains `record`; `PyTelemetryRecord` pyclass with 8 getters is present in `telemetry.rs`.
- AC5b resolution note is written (code comment in `telemetry.rs`): states whether round-trip reads via a new accessor or via aggregator+count.
- `cargo check` clean; no positional `TelemetryRecord` construction — field assignment is explicit.

---

### T04 — Register `PyTelemetryRecord` in `lib.rs` + CI cargo-env fix

**Lane:** W
**Files touched:**
- `mark-xl-rust-fork/crates/openjarvis-python/src/lib.rs`
- `.github/workflows/ci.yml`

**FRs/NFRs satisfied:** FR-9, NFR-7, AC5b, AC7 (CI cargo-env PF-1)
**Dependencies:** T03
**Parallel-eligible:** no

**Steps (1–3):**
1. Add `m.add_class::<telemetry::PyTelemetryRecord>()?;` near `lib.rs:142`, immediately after `m.add_class::<telemetry::PyTelemetrySample>()?;` (design.md:207). This is the ONLY new `add_class` in WO-6. Confirm `PyTraceStore` (`lib.rs:149`), `PyTraceCollector` (`lib.rs:150`), and `PyTelemetryStore` (`lib.rs:138`) are already registered (no duplicates).
2. In `.github/workflows/ci.yml` wheel-build step (around `ci.yml:107-123`): add `source "$HOME/.cargo/env"` as a shell command BEFORE the maturin step, so cargo is on PATH in CI (PF-1 carry-forward, `preflight-report.md §4`). Pin to one literal runner string (`macos-14`) for the arm64 wheel-build job. Confirm `manylinux: "off"` and `sccache: true` are still present. Add a `skipif` guard comment in CI referencing `platform.machine()=='arm64'` for the store tests step.
3. `cargo check` from the crate dir with T01+T02+T03+T04 applied — must pass clean.

**Acceptance checks:**
- `lib.rs` has exactly one new `add_class` line: `telemetry::PyTelemetryRecord`. No duplicate registrations for the three existing store classes.
- CI `ci.yml` wheel-build job sources `$HOME/.cargo/env` before maturin; runner is pinned to `macos-14`.
- `cargo check` passes in `mark-xl-rust-fork/crates/openjarvis-python`.

---

### T05 — arm64 maturin rebuild + re-vendor + binding-gap verification

**Lane:** W
**Files touched:**
- `mark-xl-rust-fork/` (rebuilt `.so` via maturin develop)
- `dist/` (wheel artifact — `.whl` file, gitignored or stored per docs/VENDORING.md convention)
- `.venv-mac/lib/python3.12/site-packages/mark_xl_rust/` (updated in-place via `maturin develop`)

**FRs/NFRs satisfied:** FR-7, FR-9, AC5, AC5b (binding closure)
**Dependencies:** T01, T02, T03, T04 (all Rust edits complete)
**Parallel-eligible:** no

**Steps (1–3):**
1. Run the exact build sequence (PF-1 template): `source "$HOME/.cargo/env"` → `source /Users/ltmas/Repo/agents/mark-xl/.venv-mac/bin/activate` → `cd /Users/ltmas/Repo/agents/mark-xl/mark-xl-rust-fork/crates/openjarvis-python` → `maturin develop --release`. Capture and log any build warnings.
2. Verify the binding gap is closed (quickstart.md §2 script): `dir(mark_xl_rust.TraceStore)` must include `save`, `get`, `list_traces`; `dir(mark_xl_rust.TelemetryStore)` must include `record`; `dir(mark_xl_rust.TraceCollector)` must include `start_trace`, `add_step`, `end_trace`; `mark_xl_rust.TelemetryRecord` must be importable. Assert `WHEEL_AVAILABLE == True` on this arm64 host.
3. Re-vendor: copy the built wheel artifact into the `dist/` directory (or per the existing `docs/VENDORING.md` copy-not-subtree convention). Confirm `import mark_xl_rust` still works cleanly and flag-OFF import (`from core.mark_xl_rust_adapter import WHEEL_AVAILABLE`) succeeds without raising.

**Acceptance checks:**
- `python -c "import mark_xl_rust as m; assert {'save','get','list_traces'} <= set(dir(m.TraceStore)); assert 'record' in dir(m.TelemetryStore); assert 'start_trace' in dir(m.TraceCollector); print('OK')"` exits 0.
- `python -c "from core.mark_xl_rust_adapter import WHEEL_AVAILABLE; assert WHEEL_AVAILABLE; print('OK')"` exits 0 (arm64 host, activated venv).
- No import-time side effects: `python -c "import core.store_executor"` exits 0 (even though store_executor doesn't exist yet — this tests that the wheel import itself has zero side effects; adapt once T06 lands).

---

## SHARED — `core/store_executor.py` (lands before A and B)

### T06 — Implement `core/store_executor.py`: lazy pool + WAL helper + Qt bridge

**Lane:** SHARED
**Files touched:**
- `core/store_executor.py` (NEW)

**FRs/NFRs satisfied:** FR-10, FR-11, FR-18, NFR-1, NFR-2, NFR-3, NFR-4, AC2, AC6
**Dependencies:** T05 (wheel must be rebuilt and verified before the bridge is used; the module itself can be authored earlier, but the AC2 test requires the wheel)
**Parallel-eligible:** partially — authoring can overlap with late T05; the AC2 test runs after T05

**Steps (1–3):**
1. Implement the lazy singleton pool: `_store_executor: Optional[ThreadPoolExecutor] = None` and `_store_executor_lock: threading.Lock = threading.Lock()`. `_get_store_executor()` double-checks under the lock, constructs `ThreadPoolExecutor(max_workers=1, thread_name_prefix="mark_xl_store")` on first call, registers `atexit.register(_store_executor.shutdown, wait=False)` exactly once. Mirror `core/mark_xl_rust_adapter.py:77-92` exactly (single-writer `max_workers=1` vs adapter's `max_workers=4`). Implement `submit(fn, *args, **kwargs) -> Future` mirroring `core/mark_xl_rust_adapter.py:95-102`. **CRITICAL constraint:** the file MUST NOT contain the literal substring `get_flag` anywhere — not in code, comments, docstrings, or string literals (FR-18, `tests/test_flags.py:131` substring scan).
2. Implement `ensure_wal(path: str) -> None`: open a short-lived `sqlite3.connect(path)`, execute `PRAGMA journal_mode=WAL`, read back the mode string, assert/log it equals `"wal"` (case-insensitive), `close()`. Use `Path(path).as_posix()` defensively (R7). Raise (or log + return) if mode is not `"wal"` — do not leave the connection open on error. Idempotent on already-WAL DBs.
3. Implement `class StoreResultBridge(QObject)` with `result_ready = pyqtSignal(object)` and `dispatch(self, fn, *args) -> Future` mirroring `core/mark_xl_rust_adapter.py:226-250`. The done-callback runs on the worker thread and emits `result_ready` (queued cross-thread connection delivers it to the Qt main thread). Guard the callback so a failing store write does not silently kill the bridge (log or emit error sentinel). No Qt object may be touched from the worker thread. **No module-level construction of the pool, no DB open, no Qt object creation at import time** (AC6/R8, `design.md:309-312`).

**Acceptance checks:**
- `python -c "import core.store_executor"` exits 0 with zero side effects (no pool spawned, no DB opened, no Qt imported at module level).
- `grep -c "get_flag" core/store_executor.py` outputs `0` (FR-18 substring-scan constraint).
- TDD test `tests/test_wo6_stores.py::test_store_executor_lazy_pool`: imports the module, asserts `_store_executor is None` before first call; calls `submit(lambda: 42)`; asserts `future.result() == 42`; asserts pool has `max_workers == 1`.
- TDD test `tests/test_wo6_stores.py::test_ensure_wal_sets_wal_mode`: creates a temp SQLite path, calls `ensure_wal(path)`, opens the DB and `PRAGMA journal_mode` returns `"wal"`.

---

## Lane A — Scheduler + Session stores + Migration (parallel with B after SHARED lands)

### T07 — Add `SchedulerStore` integration to `agent/task_queue.py` (FR-1, FR-2, FR-3)

**Lane:** A
**Files touched:**
- `agent/task_queue.py`

**FRs/NFRs satisfied:** FR-1, FR-2, FR-3, NFR-1, NFR-2, NFR-3, NFR-4, AC1, AC6
**Dependencies:** T06 (store_executor.py must exist)
**Parallel-eligible:** yes (with T10, T11, T12, T13 in Lane B)

**Steps (1–3):**
1. Add a lazy double-gated `_scheduler_store(db_path: str, use_scheduler_store: bool) -> Optional[mark_xl_rust.SchedulerStore]` factory at the top of `task_queue.py` (or as a module-level helper). Gate: `use_scheduler_store AND WHEEL_AVAILABLE`. Call `store_executor.ensure_wal(db_path)` on the executor thread BEFORE constructing `mark_xl_rust.SchedulerStore(db_path)`. `db_path` MUST be `Path(...).as_posix()`. MUST NOT contain `get_flag` substring. Factory returns `None` when gated off.
2. Wire persist/reload into `TaskQueue`: on `enqueue` → `submit(store.create_task, ...)` (with status-vocab bridging: Python `TaskStatus.PENDING` → Rust `"active"` etc.); on status change → `submit(store.update_status, id, mapped_status)`; on run → `submit(store.record_run, id, ts)`; in `__init__` (or a `reload_from_store()` method) → call `store.list_tasks()`, `json.loads`, hydrate `self._queue` and `self._tasks` (`task_queue.py:39-42`). Preserve Python `TaskStatus` and `TaskPriority` enum values UNCHANGED (`task_queue.py:9-14,17-20`). The status-vocab bridge maps WITHOUT renaming either vocabulary (FR-2, `contracts/store_wrappers.md §1`): `PENDING→active`, `RUNNING→active`, `COMPLETED→completed`, `FAILED→cancelled`, `CANCELLED→cancelled` (or a documented mapping table in a comment).
3. When `use_scheduler_store` is OFF or `WHEEL_AVAILABLE` is False: the existing `list`+`Lock`+`Condition`+`dict` path (`task_queue.py:39-42`) is byte-identical to `cfcd5db` — no code change to that path. The singleton at `task_queue.py:210-221` is preserved. The flag bool is passed in by `main.py` / `agent/executor.py`, NOT read inside `task_queue.py` itself (FR-18).

**Acceptance checks:**
- `grep -c "get_flag" agent/task_queue.py` outputs `0`.
- TDD test `tests/test_wo6_stores.py::test_scheduler_store_persist_reload` (AC1): with `use_scheduler_store=True` and `WHEEL_AVAILABLE=True`, enqueue a task via `TaskQueue`, re-instantiate `TaskQueue` from the same DB path, assert the task reloads with `TaskStatus` and `TaskPriority` intact.
- TDD test `tests/test_wo6_stores.py::test_scheduler_store_off_baseline` (AC6): with `use_scheduler_store=False`, assert `TaskQueue` uses the in-memory list path, no DB file created.
- Store-write tests marked `@pytest.mark.skipif(not WHEEL_AVAILABLE or not (platform.machine()=='arm64' and sys.platform=='darwin'), reason="arm64-only wheel store test")`.

---

### T08 — Add `SessionStore` integration + migration to `memory/memory_manager.py` (FR-4, FR-5, FR-6)

**Lane:** A
**Files touched:**
- `memory/memory_manager.py`

**FRs/NFRs satisfied:** FR-4, FR-5, FR-6, NFR-1, NFR-2, NFR-3, NFR-4, NFR-5, AC4, AC6
**Dependencies:** T06
**Parallel-eligible:** yes (with T10, T11, T12, T13)

**Steps (1–3):**
1. Add lazy double-gated `_session_store(db_path: str, use_session_store: bool) -> Optional[mark_xl_rust.SessionStore]` factory. Gate: `use_session_store AND WHEEL_AVAILABLE`. `db_path` via `Path(...).as_posix()`; `ensure_wal(db_path)` before ctor. Returns `None` when gated off. MUST NOT contain `get_flag` substring. No module-level store construction.
2. Implement `migrate_sessions_if_needed(source_path: str, db_path: str, use_session_store: bool) -> None` (PF-2, AC4, `contracts/store_wrappers.md §2`): (a) runs only when `use_session_store AND WHEEL_AVAILABLE`; (b) if source exists: `shutil.copy2(source, source + ".bak")` then `os.fsync` on the `.bak` fd AND `os.fsync` on the containing directory fd — BEFORE opening the `SessionStore` write; if `source + ".bak"` already exists from a crashed prior attempt, resume from `.bak` (idempotent); (c) if no source exists (today's `memory/long_term.json` is flat-JSON FACT memory, no message log — `design.md:285-287`): treat as empty, create/operate on an empty source, run a trivial round-trip (0 messages migrate, 0 read back identically == PASS); (d) perform the JSON→SQLite migration via `store.save_message` calls through `store_executor.submit`. The FACT memory at `memory/memory_manager.py:14-28` is NOT touched — it remains the existing flat-JSON path.
3. When `use_session_store` is OFF or `WHEEL_AVAILABLE` is False: existing flat-JSON FACT path (`memory_manager.py:14-28`) is byte-identical to `cfcd5db`. No `.bak`, no migration triggered. The WO-4 `core/memory_v2.py` semantic FACT memory is NOT shared and NOT touched (FR-4 distinctness).

**Acceptance checks:**
- `grep -c "get_flag" memory/memory_manager.py` outputs `0`.
- TDD test `tests/test_wo6_stores.py::test_session_migration_bak_written` (AC4): `migrate_sessions_if_needed` with a fixture JSON source → asserts `source.bak` is written before any SQLite write; asserts 0-message round-trip == PASS.
- TDD test `tests/test_wo6_stores.py::test_session_store_off_baseline` (AC6): with `use_session_store=False`, assert no migration runs and the flat-JSON path is untouched.
- Store-write tests skipped on non-arm64/wheel-absent.

---

### T09 — Flag-OFF baseline parity tests (AC6 — covers all four stores)

**Lane:** A (runs after T07 + T08 are authored; closes the AC6 requirement before CI)
**Files touched:**
- `tests/test_wo6_stores.py` (extend, or create if not yet created by T07/T08)

**FRs/NFRs satisfied:** NFR-3, NFR-4, FR-3, FR-6, AC6
**Dependencies:** T07, T08 (modules must exist so imports can be asserted)
**Parallel-eligible:** no (consumes T07 + T08 output)

**Steps (1–3):**
1. Write `test_flag_off_baseline_all_four`: set all four flags to False (pass `False` as the `use_*`/`enable_*` bool to each factory/wrapper); assert: no DB file is created; `WHEEL_AVAILABLE` (from the adapter) does not change; the legacy in-memory/JSON path is active. This test MUST be importable and runnable even when `WHEEL_AVAILABLE is False` (Linux/Windows simulation: mock `WHEEL_AVAILABLE = False` for the duration).
2. Write `test_wheel_absent_fallback`: mock `WHEEL_AVAILABLE = False` (monkeypatch on `core.mark_xl_rust_adapter`); import each store wrapper module; assert each returns `None` from its factory function and no exception is raised. Assert no DB is opened.
3. Verify `tests/test_flags.py` is unchanged: run `python -m pytest tests/test_flags.py -q` (or call it in CI pre-commit) and assert it passes GREEN with the allowlist still `{"main.py", "agent/executor.py"}`. This is a guard-check, not an edit to `test_flags.py`.

**Acceptance checks:**
- `test_flag_off_baseline_all_four` passes on arm64 mac (wheel present, all flags OFF).
- `test_wheel_absent_fallback` passes on arm64 mac (wheel present but mocked absent) — simulates the Linux/Windows state.
- `python -m pytest tests/test_flags.py -q` exits 0 — `tests/test_flags.py` UNCHANGED and still green (FR-18 guard).

---

## Lane B — Trace + Telemetry store wrappers (parallel with A after SHARED + W land)

### T10 — Implement `core/trace_collector.py` — TraceStore + TraceCollector wrapper (FR-7, FR-8)

**Lane:** B
**Files touched:**
- `core/trace_collector.py` (NEW)

**FRs/NFRs satisfied:** FR-7, FR-8, NFR-1, NFR-2, NFR-3, NFR-4, NFR-6, AC5, AC6
**Dependencies:** T05 (wheel with new bindings), T06 (store_executor)
**Parallel-eligible:** yes (with T07, T08, T09 in Lane A)

**Steps (1–3):**
1. Implement `_trace_collector(db_path: str, enable_trace_store: bool) -> Optional[tuple[mark_xl_rust.TraceStore, mark_xl_rust.TraceCollector]]` factory. Gate: `enable_trace_store AND WHEEL_AVAILABLE`. `db_path` via `Path(...).as_posix()`; call `store_executor.ensure_wal(db_path)` on the executor thread BEFORE construction; then `store = mark_xl_rust.TraceStore(db_path)` (`traces.rs:13-24`); then `collector = mark_xl_rust.TraceCollector(store)` (`traces.rs:41-45` — ctor takes the STORE, not a path, R3). Return `(store, collector)` or `None`. NO `get_flag` substring anywhere in this file.
2. Implement the capture methods routed through `store_executor.submit`: `start(trace_id, query, agent, model) -> None` → `submit(collector.start_trace, ...)`. `add_step(trace_id, step: dict) -> None` → serialise `step` dict to JSON matching `TraceStep` schema (`core/types.rs:364-375`: `step_type`, `timestamp`, `duration_seconds`, `input`, `output`, `metadata`) then `submit(collector.add_step, trace_id, step_json)`. `end(trace_id, result, outcome=None) -> None` → `submit(collector.end_trace, trace_id, result, outcome)`. `save(trace_dict: dict) -> None` → serialise to `Trace` schema (`core/types.rs:379-408`) then `submit(store.save, trace_json)`.
3. Implement read-back methods (AC5): `get(trace_id: str) -> Optional[dict]` → `submit(store.get, trace_id)` then `json.loads`; `list(limit=100, offset=0) -> list[dict]` → `submit(store.list_traces, limit, offset)` then `json.loads` each element. When `enable_trace_store` OFF or wheel-absent: all capture/read methods are no-ops; no store constructed. No module-level construction at import time.

**Acceptance checks:**
- `python -c "import core.trace_collector"` exits 0 with no side effects; `grep -c "get_flag" core/trace_collector.py` outputs `0`.
- TDD test `tests/test_wo6_stores.py::test_trace_store_capacity_1000` (AC5, NFR-6): write 1000 traces via `save(trace_dict)`; assert `store.count() >= 1000`; assert `list(limit=10)` returns 10 items; assert `get(trace_id)` returns the dict. Marked `@pytest.mark.skipif(not WHEEL_AVAILABLE or ...)` for non-arm64.
- TDD test `tests/test_wo6_stores.py::test_trace_collector_lifecycle` (AC5): `start_trace` → `add_step` → `end_trace`; assert `store.count()==1`, `collector.active_count()==0`; deserialise the trace and assert `steps` list is non-empty with correct `step_type`.
- TDD test `tests/test_wo6_stores.py::test_trace_store_off_baseline` (AC6): `enable_trace_store=False` → no DB created, capture methods are no-ops.

---

### T11 — Implement `core/telemetry.py` — TelemetryStore + TelemetryRecord wrapper (FR-9)

**Lane:** B
**Files touched:**
- `core/telemetry.py` (NEW)

**FRs/NFRs satisfied:** FR-9, NFR-1, NFR-2, NFR-3, NFR-4, AC5b, AC6
**Dependencies:** T05 (wheel), T06 (store_executor), T03 (AC5b resolution documented)
**Parallel-eligible:** yes (with T07, T08, T09, T10)

**Steps (1–3):**
1. Implement `_telemetry_store(db_path: str, enable_telemetry: bool) -> Optional[mark_xl_rust.TelemetryStore]` factory. Gate: `enable_telemetry AND WHEEL_AVAILABLE`. `db_path` via `Path(...).as_posix()`; `store_executor.ensure_wal(db_path)` first; `mark_xl_rust.TelemetryStore(db_path)`. NO `get_flag` substring. No module-level construction.
2. Implement `record(self, model_id: str, *, prompt_tokens: int = 0, completion_tokens: int = 0, total_tokens: int = 0, latency_seconds: float = 0.0, ttft: float = 0.0, cost_usd: float = 0.0, timestamp: Optional[float] = None) -> None` → `store_executor.submit(store.record, model_id, prompt_tokens, completion_tokens, total_tokens, latency_seconds, ttft, cost_usd, timestamp)`. Token fields `int` (Rust `i64`); latency/ttft/cost `float` (Rust `f64`, NOT f32). When gated off: no-op, no store constructed.
3. Implement AC5b read-back based on the resolution from T03: if a new accessor (`list_records`/`get`) was added in T03, expose `get_record(...)` returning a `PyTelemetryRecord` instance; if AC5b round-trips via aggregator, expose `stats() -> dict` wrapping `TelemetryAggregator.stats()` and `count() -> int` wrapping `store.count()`. Either path must satisfy the AC5b assertion. Implement a `TelemetryAggregator` wrapper that returns non-zero stats after at least one `record` call.

**Acceptance checks:**
- `python -c "import core.telemetry"` exits 0 with no side effects; `grep -c "get_flag" core/telemetry.py` outputs `0`.
- TDD test `tests/test_wo6_stores.py::test_telemetry_record_roundtrip` (AC5b): with `enable_telemetry=True` and wheel available, call `record(model_id="gpt-4", prompt_tokens=10, completion_tokens=20, total_tokens=30, latency_seconds=1.5)`; assert `store.count() >= 1`; assert read-back (via accessor or aggregator) shows non-zero token/latency stats; assert `model_id` round-trips if field-level access is available. Marked `skipif` for non-arm64.
- TDD test `tests/test_wo6_stores.py::test_telemetry_off_baseline` (AC6): `enable_telemetry=False` → no DB created, `record(...)` is a no-op.

---

### T12 — AC2 concurrency test: QTimer + 100 writes + WAL assertion

**Lane:** B (requires T06 + T10 or T11 for realistic store calls; may use T06 mock if needed)
**Files touched:**
- `tests/test_wo6_stores.py`

**FRs/NFRs satisfied:** FR-10, FR-11, NFR-1, NFR-2, NFR-9, AC2
**Dependencies:** T06, T10 (or T11 for a telemetry write variant)
**Parallel-eligible:** yes (within B, after T06)

**Steps (1–3):**
1. Write `test_concurrency_100_writes_wal` (AC2): create a `QApplication` (offscreen, `QT_QPA_PLATFORM=offscreen`); instantiate a `StoreResultBridge`; start a `QTimer` counting frames at 30 Hz for 3 seconds; issue 100 `store_executor.submit(...)` calls (use `ensure_wal` or a real store write from T10/T11 on a temp DB path); join all futures; stop the timer. Assert: (a) all 100 futures completed with no exception; (b) Qt main thread was not stalled — frame count / elapsed time implies ≥30 FPS (at least 80 frames in 3 seconds is a safe threshold); (c) open the DB with `sqlite3.connect` and `PRAGMA journal_mode` returns `"wal"`.
2. Mark this test `@pytest.mark.skipif(not WHEEL_AVAILABLE or not (platform.machine()=='arm64' and sys.platform=='darwin'), reason="AC2 store concurrency test: arm64 wheel required")`.
3. Assert that the test file (`test_wo6_stores.py`) as a whole imports cleanly on Linux/Windows (no arm64-only import at module level — the skip marker is evaluated at runtime, so imports must be guarded inside the test function).

**Acceptance checks:**
- `QT_QPA_PLATFORM=offscreen pytest -q tests/test_wo6_stores.py::test_concurrency_100_writes_wal` passes (arm64 mac).
- Frame-count assertion ≥30 FPS is present and asserted.
- WAL is confirmed (`PRAGMA journal_mode == "wal"`) on the temp DB.

---

### T13 — FR-18 guard: verify `get_flag` substring-scan and `test_flags.py` still GREEN

**Lane:** B (final B-lane gate — runs after T07, T08, T10, T11 are authored)
**Files touched:**
- `tests/test_wo6_stores.py` (a single guard test, or run test_flags.py directly)
- NO changes to `tests/test_flags.py`

**FRs/NFRs satisfied:** FR-18, R4 (HIGH mitigation)
**Dependencies:** T07, T08, T10, T11 (all new production files must be authored)
**Parallel-eligible:** no (must run after all Lane A+B production files exist)

**Steps (1–3):**
1. Run `grep -rn "get_flag" core/store_executor.py core/trace_collector.py core/telemetry.py agent/task_queue.py memory/memory_manager.py` — must return 0 matches. If any match is found, the inner loop STOPS and flags the file for remediation before proceeding.
2. Run `python -m pytest tests/test_flags.py -q` — must exit 0. The `set(callers) == {"main.py", "agent/executor.py"}` assertion at `tests/test_flags.py:139` must still hold. The allowlist is UNCHANGED (preferred WO-3-style design). If this test fails, do NOT extend the allowlist without a documented review — return `status: failed` with `next_action: remediate get_flag leak in [file]`.
3. Document the result as a comment in `tests/test_wo6_stores.py`: `# FR-18 guard: test_flags.py allowlist verified GREEN at this commit`.

**Acceptance checks:**
- `grep -rn "get_flag" core/store_executor.py core/trace_collector.py core/telemetry.py agent/task_queue.py memory/memory_manager.py` exits 0 with no output.
- `pytest tests/test_flags.py -q` exits 0.
- `tests/test_flags.py` file is bit-for-bit identical to the version at `cfcd5db` (no edits made to it).

---

## Lane C — Packaging + Licensing + Flags + CI matrix gates

### T14 — Add four WO-6 flags to `config/flags.json` (FR-17)

**Lane:** C
**Files touched:**
- `config/flags.json`

**FRs/NFRs satisfied:** FR-17, NFR-3, AC6
**Dependencies:** none (pure config addition; safe to author any time after T00)
**Parallel-eligible:** yes (can run immediately)

**Steps (1–3):**
1. Read `config/flags.json` first (currently 6 bools + 3 int tunables per `design.md:128`). Add four new boolean keys all with value `false`: `"use_scheduler_store"`, `"use_session_store"`, `"enable_trace_store"`, `"enable_telemetry"`. Do NOT rename or change any existing keys (`use_*`/`enable_*` convention, `spec.md:57`).
2. Validate the resulting JSON is well-formed (`python -c "import json; json.load(open('config/flags.json'))"` exits 0).
3. Confirm that `get_flag("use_scheduler_store", False)` returns `False` in the default state (the four new flags all default `false` → NFR-3 flag-OFF == baseline).

**Acceptance checks:**
- `config/flags.json` is valid JSON; contains exactly the four new keys, all `false`.
- No existing keys changed or removed.
- `python -c "from memory.config_manager import get_flag; assert get_flag('use_scheduler_store', False) == False; print('OK')"` exits 0.

---

### T15 — Create `mark_xl/__init__.py` + `mark_xl/__main__.py` (FR-12, AC3)

**Lane:** C
**Files touched:**
- `mark_xl/__init__.py` (NEW)
- `mark_xl/__main__.py` (NEW)

**FRs/NFRs satisfied:** FR-12, AC3
**Dependencies:** none
**Parallel-eligible:** yes

**Steps (1–3):**
1. Create `mark_xl/` directory. Write `mark_xl/__init__.py` as an empty (or minimal version-comment-only) file — the package must be importable without triggering any application logic.
2. Write `mark_xl/__main__.py`: `from main import main` + `if __name__ == "__main__": main()`. Entry is `main.py:1493 def main()` guarded by `main.py:1540 if __name__ == "__main__": main()`. The shim is exactly two logical lines; do not add any flag resolution, store init, or Qt startup here.
3. Verify `python -m mark_xl --help` or `python -m mark_xl` at least reaches `main()` without an import error (AC3). The Qt application start may fail in headless environments — the acceptance criterion is a clean import path to `main.main`, not a running GUI.

**Acceptance checks:**
- `python -c "import mark_xl"` exits 0 (no side effects).
- `python -m mark_xl` triggers `main.main` (confirmed via `python -c "from mark_xl.__main__ import *"` succeeding, or via a minimal import test in `tests/test_wo6_stores.py::test_entrypoint_imports_main`).
- `mark_xl/__main__.py` contains exactly `from main import main` and `if __name__ == "__main__": main()` (plus any `# noqa` or encoding line). No `get_flag` or store init.

---

### T16 — Create `pyproject.toml` at repo root (FR-12, AC3, AC8)

**Lane:** C
**Files touched:**
- `pyproject.toml` (NEW at repo root)

**FRs/NFRs satisfied:** FR-12, AC3, AC8
**Dependencies:** T15 (`mark_xl/` package must exist for the entry point to be valid)
**Parallel-eligible:** after T15

**Steps (1–3):**
1. Create `pyproject.toml` at `/Users/ltmas/Repo/agents/mark-xl/pyproject.toml` with `[build-system]` (setuptools or hatchling — choose the one already in `.venv-mac`), `[project]` metadata (name `mark-xl`, version `0.6.0`, requires-python `>=3.11`), and `[project.scripts]` with `mark-xl = "main:main"` (entry function at `main.py:1493`). This file does NOT exist today (`analysis.md:37`).
2. Confirm the entry point path: `main:main` refers to `main.py` at the repo root's `main` function. Since `main.py` is not inside a package, verify this resolves correctly with the chosen build backend (adjust to `mark_xl.__main__:main` if `main:main` is ambiguous for the backend, but keep `main.main` as the target).
3. `pip install -e . --no-deps` (in activated venv) — the editable install must succeed without pulling new packages; `mark-xl` console script must appear in `.venv-mac/bin/mark-xl`.

**Acceptance checks:**
- `pip install -e . --no-deps` exits 0.
- `which mark-xl` (in activated venv) points inside `.venv-mac/bin/`.
- `python -m pytest tests/test_wo6_stores.py::test_entrypoint_imports_main -q` passes (AC3).

---

### T17 — Create `LICENSE` (MIT) + `NOTICE` (Apache-2.0) at repo root (FR-13, FR-14, AC8)

**Lane:** C
**Files touched:**
- `LICENSE` (NEW)
- `NOTICE` (NEW)

**FRs/NFRs satisfied:** FR-13, FR-14, AC8
**Dependencies:** none
**Parallel-eligible:** yes

**Steps (1–3):**
1. Write `LICENSE` at repo root: full MIT license text with copyright year `2024` (or the repo's actual creation year — use `2024` if unknown) and `Copyright (c) 2024 Mark-XL Contributors`. Standard MIT SPDX text.
2. Write `NOTICE` at repo root: attribution notice for the vendored OpenJarvis crates. Include: project name (`OpenJarvis`), original license (`Apache-2.0`), source URL or reference, and a statement that the vendored crates are included unmodified except for PyO3 binding additions. Reference `docs/VENDORING.md` for the vendor procedure.
3. Verify both files are non-empty and contain the required license identifiers: `MIT` in `LICENSE`, `Apache-2.0` (or `Apache License, Version 2.0`) in `NOTICE`.

**Acceptance checks:**
- `cat LICENSE | grep -i "MIT"` exits 0.
- `cat NOTICE | grep -i "Apache"` exits 0.
- Both files exist at the repo root (confirmed by `test_packaging_artifacts` in `tests/test_wo6_stores.py::test_license_notice_exist` → AC8).

---

### T18 — Pin `requirements.txt` to exact versions (FR-15, AC8)

**Lane:** C
**Files touched:**
- `requirements.txt`
- `requirements.txt.pre-pin.bak` (safety backup per `preflight-report.md §3`)

**FRs/NFRs satisfied:** FR-15, AC8
**Dependencies:** none (safe to run anytime; do NOT run during a store-test to avoid env churn)
**Parallel-eligible:** yes (but run in isolated step to avoid venv side effects)

**Steps (1–3):**
1. Backup: `cp requirements.txt requirements.txt.pre-pin.bak` before any overwrite (`preflight-report.md §3` carry-forward).
2. In the activated `.venv-mac`: `pip freeze > requirements.txt.frozen` then cross-reference with the existing `requirements.txt` (54 lines, currently 0% pinned per `analysis.md:38`). Pin every entry to its exact installed version using `==` (not `~=` or `>=`). Preserve the existing grouping/comments structure where possible. Only pin packages already in `requirements.txt` — do not add transitive dependencies that weren't listed.
3. Validate: `python -c "import pkg_resources; pkg_resources.require(open('requirements.txt').read().splitlines())"` or equivalent exits 0 on the current `.venv-mac`.

**Acceptance checks:**
- `requirements.txt.pre-pin.bak` exists (safety backup written).
- `grep -c "==" requirements.txt` equals the total number of package lines (all entries pinned).
- `grep -cE "^[a-zA-Z]" requirements.txt` (non-comment package lines) all use `==` (no `>=`, `~=`, `^` remaining).
- `pip install -r requirements.txt --dry-run` exits 0 (all pinned versions satisfiable in the current env).

---

### T19 — Extend `docs/VENDORING.md` with arm64-build procedure (FR-16, AC8)

**Lane:** C
**Files touched:**
- `docs/VENDORING.md`

**FRs/NFRs satisfied:** FR-16, AC8
**Dependencies:** T05 (the arm64 rebuild must be done so the exact procedure can be documented accurately)
**Parallel-eligible:** after T05

**Steps (1–3):**
1. Read `docs/VENDORING.md` (currently 84 lines). Confirm the existing rsync procedure is described (and uses `rsync`, not `git subtree` — `design.md:135`). Append a new section: `## Arm64 macOS Wheel Rebuild` that documents the exact build sequence from `pipeline/quickstart.md §1`: `source "$HOME/.cargo/env"` → `source .venv-mac/bin/activate` → `cd mark-xl-rust-fork/crates/openjarvis-python` → `maturin develop --release` (or `maturin build --release --out dist/ && pip install --force-reinstall ...`). Note: `manylinux: "off"` (native host build), arm64 macOS only.
2. Append a note: `## Quarterly Sync` (if not already present) or extend the existing sync section to explicitly state that the rsync procedure syncs the crate source only — the arm64 wheel must then be rebuilt and re-vendored following the above steps.
3. Drop or annotate any remaining `git-subtree` wording if present (align with `analysis.md:39` — the vendor is a plain `rsync` copy, NOT a git subtree).

**Acceptance checks:**
- `docs/VENDORING.md` contains the literal string `maturin` (arm64 build procedure documented).
- `docs/VENDORING.md` does NOT contain `git subtree` as an instruction (only rsync is the sync mechanism).
- `cat docs/VENDORING.md | grep -i "arm64"` exits 0.
- The new section is ≥6 lines (non-trivial documentation, not a stub).

---

### T20 — CI matrix gates: arm64-store-tests + Linux/Windows skip guards (NFR-7, AC7)

**Lane:** C
**Files touched:**
- `.github/workflows/ci.yml`

**FRs/NFRs satisfied:** NFR-7, AC7
**Dependencies:** T04 (CI cargo-env fix already in T04), T10, T11 (test modules must exist for CI to reference them)
**Parallel-eligible:** after T04

**Steps (1–3):**
1. In `.github/workflows/ci.yml`, add a new CI job step (or extend the `test` matrix) that runs the Rust-backed store tests (`pytest tests/test_wo6_stores.py`) ONLY on `macos-14` (arm64). Use a `if: runner.os == 'macOS' && matrix.os == 'macos-14'` (or equivalent) condition. Confirm the existing `test` matrix `os=[macos-14, macos-13, windows-latest, ubuntu-latest]` is preserved (do not remove any existing runner). The flag-OFF baseline tests (`test_flag_off_baseline_all_four`, `test_wheel_absent_fallback`) MUST run on EVERY lane (no skip condition on AC6 tests).
2. Confirm the Linux/Windows lanes remain green: on those runners `WHEEL_AVAILABLE = False` (the arm64 wheel does not import); the arm64-only store tests are SKIPPED via the `@pytest.mark.skipif(...)` markers in `tests/test_wo6_stores.py`; the AC6 flag-OFF tests run and pass. Add a comment in `ci.yml` noting: `# arm64-store tests: run only on macos-14; AC6 parity tests run everywhere`.
3. Confirm T04's CI fix is present: `source "$HOME/.cargo/env"` in the wheel-build step; runner pinned to `macos-14`; `manylinux: "off"` and `sccache: true` still present. Do not introduce a NEW independent threading mechanism or a new CI job — only extend existing jobs.

**Acceptance checks:**
- `ci.yml` contains a condition that runs `pytest tests/test_wo6_stores.py` specifically on `macos-14` (or equivalent arm64 runner).
- `ci.yml` does NOT remove any existing matrix lanes (all four `os` values preserved).
- A comment in `ci.yml` explicitly notes that AC6 parity tests run everywhere.
- `python -m pytest tests/test_wo6_stores.py -q --collect-only 2>&1 | grep "arm64"` shows at least one skip-marked test (confirms the skip markers are in the test file and ci.yml can reference them).

---

### T21 — AC3 entry-point integration test + final AC8 packaging assertions

**Lane:** C
**Files touched:**
- `tests/test_wo6_stores.py` (extend)

**FRs/NFRs satisfied:** AC3, AC8
**Dependencies:** T15, T16, T17, T18, T19
**Parallel-eligible:** no (final C-lane gate)

**Steps (1–3):**
1. Write `test_entrypoint_imports_main` (AC3): assert `python -m mark_xl` dispatches to `main.main`; verify by importing `mark_xl.__main__` and checking it contains a `main` reference pointing to `main.main` from `main.py:1493`. Do not actually launch the Qt GUI in the test — use `subprocess.run(["python", "-m", "mark_xl", "--help"], ...)` or a dry-import assertion.
2. Write `test_packaging_artifacts` (AC8): assert `LICENSE` exists at the repo root and contains `MIT`; assert `NOTICE` exists and contains `Apache`; assert all lines in `requirements.txt` that are package specs use `==` exact pinning; assert `docs/VENDORING.md` contains `maturin` and `arm64`.
3. Run the full `tests/test_wo6_stores.py` suite on arm64 mac to confirm all tasks are GREEN before handing off to Phase 5. Capture the run summary (count of passed/skipped/failed) as a comment in the test file header.

**Acceptance checks:**
- `QT_QPA_PLATFORM=offscreen pytest -q tests/test_wo6_stores.py` — all tests pass or are legitimately skipped (arm64-only tests on Linux/Windows); 0 failures.
- `test_entrypoint_imports_main` passes.
- `test_packaging_artifacts` passes — `LICENSE`, `NOTICE`, pinned `requirements.txt`, `VENDORING.md` with `maturin` + `arm64` all confirmed.

---

## Task Summary Table

| ID | Title | Lane | Files | ACs | Depends on | Parallel? |
|---|---|---|---|---|---|---|
| T01 | `PyTraceStore` write bindings | W | `traces.rs` | AC5 | — | no |
| T02 | `PyTraceCollector` record bindings | W | `traces.rs` | AC5 | T01 | no |
| T03 | `PyTelemetryStore.record` + `PyTelemetryRecord` | W | `telemetry.rs` | AC5b | T01,T02 | no |
| T04 | `lib.rs` registration + CI cargo-env fix | W | `lib.rs`, `ci.yml` | AC5b, AC7 | T03 | no |
| T05 | arm64 maturin rebuild + re-vendor + verify | W | `dist/`, `.venv-mac` SO | AC5, AC5b | T01–T04 | no |
| T06 | `core/store_executor.py` pool + WAL + bridge | SHARED | `core/store_executor.py` | AC2, AC6 | T05 | partial |
| T07 | `agent/task_queue.py` SchedulerStore integration | A | `agent/task_queue.py` | AC1, AC6 | T06 | yes |
| T08 | `memory/memory_manager.py` SessionStore + migration | A | `memory/memory_manager.py` | AC4, AC6 | T06 | yes |
| T09 | Flag-OFF parity tests + `test_flags.py` guard | A | `tests/test_wo6_stores.py` | AC6, FR-18 | T07, T08 | no |
| T10 | `core/trace_collector.py` TraceStore wrapper | B | `core/trace_collector.py` | AC5, AC6 | T05, T06 | yes |
| T11 | `core/telemetry.py` TelemetryStore wrapper | B | `core/telemetry.py` | AC5b, AC6 | T05, T06, T03 | yes |
| T12 | AC2 concurrency test (QTimer + 100 writes + WAL) | B | `tests/test_wo6_stores.py` | AC2 | T06, T10 | yes |
| T13 | FR-18 get_flag substring guard | B | `tests/test_wo6_stores.py` | FR-18 | T07,T08,T10,T11 | no |
| T14 | `config/flags.json` +4 flags | C | `config/flags.json` | AC6 | — | yes |
| T15 | `mark_xl/__init__.py` + `__main__.py` | C | `mark_xl/` (new dir) | AC3 | — | yes |
| T16 | `pyproject.toml` at repo root | C | `pyproject.toml` | AC3, AC8 | T15 | after T15 |
| T17 | `LICENSE` (MIT) + `NOTICE` (Apache-2.0) | C | `LICENSE`, `NOTICE` | AC8 | — | yes |
| T18 | Pin `requirements.txt` | C | `requirements.txt` | AC8 | — | yes |
| T19 | Extend `docs/VENDORING.md` arm64 procedure | C | `docs/VENDORING.md` | AC8 | T05 | after T05 |
| T20 | CI matrix arm64 + skip guards | C | `.github/workflows/ci.yml` | AC7 | T04, T10, T11 | after T04 |
| T21 | AC3 + AC8 integration tests (final gate) | C | `tests/test_wo6_stores.py` | AC3, AC8 | T15–T20 | no |

**Total tasks: 21** (within the 18–22 target from `spec.md:125`).

---

## Load-bearing constraints summary (baked in above, indexed for the inner loop)

1. **PF-1 (maturin/cargo env):** T05 build sequence = `source "$HOME/.cargo/env"` → `source .venv-mac/bin/activate` → maturin. Never skip cargo-env sourcing.
2. **PF-2 (crash-safe .bak):** T08 migration = `shutil.copy2` + `os.fsync` on .bak fd AND dir fd BEFORE any SQLite write; resume-from-.bak if already present.
3. **FR-18 (get_flag substring scan):** T06/T07/T08/T10/T11 files MUST contain zero occurrences of `get_flag` (including comments, docstrings, strings). T13 verifies. `tests/test_flags.py` is UNCHANGED.
4. **WO-4 PyO3 arg-order trap:** T03 `TelemetryRecord` constructed field-by-field by name with `..Default::default()`. NEVER positional.
5. **f64 (not f32):** T03/T11 — all numeric persistence fields in TelemetryRecord are `f64`.
6. **No module-level store construction:** T06/T07/T08/T10/T11 — importing any new file has zero side effects (no pool spawn, no DB open, no Qt import).
7. **Double-gate:** every store path gated on `(flag_bool passed in) AND WHEEL_AVAILABLE`. Flag bool resolved by `main.py`/`agent/executor.py` and passed in.
8. **`TraceCollector` ctor takes a store, not a path (R3):** T10 — `TraceStore(path)` first, then `TraceCollector(store)`.
9. **`.as_posix()` on ALL path strings:** T06/T07/T08/T10/T11 — before any Rust ctor or `ensure_wal`.
10. **Single-writer `max_workers=1`:** T06 — `ThreadPoolExecutor(max_workers=1)`, NOT 4 (the adapter uses 4; the store executor uses 1).
11. **AC5b [UNVERIFIED] resolved in T03:** the implementer must confirm whether read-back uses a new field accessor or `TelemetryAggregator.stats()`+`count()`.
12. **Status vocab bridge:** T07 — Python `TaskStatus` ↔ Rust SchedulerStore status bridged WITHOUT renaming either vocabulary.
13. **`gh pr checks` is the cross-OS gate:** local V&V is macOS-only. After this phase, `gh pr checks` before merge to catch Linux/Windows regressions.
