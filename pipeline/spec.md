# Specification — WO-6 (Maturity: unified stores + packaging)

> **spec_version: 3 (converged c3, 2026-06-23).** c3 RE-OPENS the telemetry-aggregation lane
> (user-authorized via coordinator): the zero-stub `TelemetryAggregator.stats()` is NOT acceptable
> for WO-6 — it MUST compute REAL non-zero aggregates from the persisted `TelemetryRecord` rows
> (see FR-9, AC5b, and the "WO-6 telemetry-aggregation addendum (converge c3)" section). This is
> the LAST allowed converge cycle (3/3). v2 preserved at `spec.md.c2.bak`, v1 at `spec.md.c1.bak`.
>
> **spec_version: 2 (converged c1, 2026-06-23).** Phase-2 Analyze surfaced a feasibility blocker
> (the prebuilt wheel exposed only READ methods at the Python boundary for trace/telemetry).
> **Resolution (user-authorized via coordinator): Option 3 — rebuild the Rust wheel, arm64 macOS
> ONLY, full WO-6 surface retained (no WO-7 deferral).** The root cause is narrower than first
> reported: the Rust core types and store write methods ALREADY EXIST; only the PyO3 *bindings*
> fail to expose them. See §"WO-6 wheel-rebuild addendum (converge c1)". v1 preserved at `spec.md.c1.bak`.

WO-6 promotes Mark-XL from in-memory/JSON scratch state to durable, restart-surviving persistence by introducing PyO3-bound SQLite stores (scheduler, session, trace, telemetry) behind a single-writer threading layer, and turns the repository into an installable, license-clean Python package. Every change is feature-flagged OFF by default; flag-OFF (or wheel-absent) behavior is byte-identical to today. Scope is one bounded context — persistence + packaging — with the WO-7 skills engine explicitly excluded except for forward-compatibility of the trace schema. **The wheel is rebuilt for arm64 macOS only (no multi-OS wheel matrix); on Linux/Windows the Rust-backed store tests are skipped while AC6 flag-OFF byte-parity still runs green on every lane.**

## User Scenarios

1. **Restart survival of scheduled work.** An operator queues a long-running scheduled task, then the application crashes or is intentionally restarted. On the next launch the previously scheduled task is still present and resumes its lifecycle, rather than silently vanishing. (Today, `agent/task_queue.py` holds tasks in an in-memory `list` (task_queue.py:39) and a `dict` (task_queue.py:42) that are lost on exit.)

2. **Conversation continuity across restart.** A user holds a multi-turn conversation, closes Mark-XL, reopens it, and the prior conversation history for that session is still available. On first launch after the upgrade, the operator's existing JSON conversation history is migrated into SQLite with a `.bak` backup written first, so nothing is lost and the migration can be reversed. (Today `memory/memory_manager.py` has no conversation/session handling at all.)

3. **Telemetry and trace observability.** An operator wants to understand what the agent did and how expensive it was. They can inspect a durable record of tool calls (name, arguments, outcome, latency — captured as ordered `TraceStep`s on a `Trace`) and per-call telemetry records (latency, token counts, model used — `TelemetryRecord`) accumulated across runs, so they can spot slow tools, failing tools, and costly model calls without re-running the agent.

4. **Install and run as a package.** A new operator clones the repo and runs `python -m mark_xl` (or the `mark-xl` console entry point) to launch the application, without hand-assembling a launch command, because the project now ships a `pyproject.toml` with a declared entry point and pinned dependencies.

5. **License-clean distribution.** A downstream consumer needs to confirm the project's license and the attribution obligations for the vendored Rust crates before redistributing. They find a `LICENSE` (MIT) and a `NOTICE` (Apache-2.0 attribution for the vendored OJ crates) in the repo root, and a documented quarterly vendoring sync procedure.

6. **Graceful degradation when the wheel is absent.** An operator on a platform with no built `mark_xl_rust` wheel (e.g. Linux/Windows — the wheel is arm64-macOS-only), or who leaves the WO-6 flags OFF, runs the application and it behaves exactly as it does today — legacy in-memory scheduler and JSON conversation history — with no crash and no error raised merely because the wheel or the durable store is unavailable.

## Functional Requirements

### Scheduler store
- **FR-1** — `agent/task_queue.py` MUST gain a durable backing store, `SchedulerStore` (PyO3-bound wheel class, constructed hermetically with a single `path` argument), exposing `create_task`, `get_task`, `list_tasks`, `update_status`, `delete_task`, and `record_run`. When `use_scheduler_store` is ON and the wheel is available, `TaskQueue` MUST persist scheduled tasks through `SchedulerStore` and reload them on startup.
- **FR-2** — The store integration MUST preserve the existing `TaskStatus` enum (task_queue.py:9-14) and `TaskPriority` enum (task_queue.py:17-20) value semantics, and the priority-ordered `Task` dataclass field order and `compare` flags (task_queue.py:23-34). No status string or priority integer may change.
- **FR-3** — When `use_scheduler_store` is OFF, or the wheel is absent, `agent/task_queue.py` MUST retain its current in-memory `list` + `threading.Lock` + `threading.Condition` + `dict` behavior (task_queue.py:39-42) unchanged.

### Session store + migration
- **FR-4** — `memory/memory_manager.py` MUST gain a `SessionStore` (PyO3-bound wheel class, single `path` arg) exposing `get_or_create`, `save_message`, `list_sessions`, `link_channel`, `consolidate`, and `decay`, used when `use_session_store` is ON and the wheel is available. This conversation/session memory is DISTINCT from WO-4's `core/memory_v2.py` semantic FACT memory; the two MUST NOT share storage or overlap in responsibility.
- **FR-5** — On first launch with `use_session_store` ON, `memory/memory_manager.py` MUST auto-migrate the existing JSON conversation history into the `SessionStore` SQLite database, writing a `.bak` copy of the source JSON before any write. The migration MUST be loss-free and round-trippable (every message readable back identically). If no conversation-history source exists (today's `long_term.json` is FACT memory, not a message log), the first migration is a documented no-op that still satisfies `.bak`/round-trip semantics on an empty/created source.
- **FR-6** — When `use_session_store` is OFF, or the wheel is absent, `memory/memory_manager.py` MUST behave exactly as today (no session handling, JSON history untouched, no migration triggered).

### Trace store
- **FR-7** — The NEW module `core/trace_collector.py` MUST provide `TraceStore` (PyO3-bound, single `path` arg) and `TraceCollector`, capturing each tool call's name, arguments, outcome, and latency, gated behind `enable_trace_store`. Capture writes a `Trace` (with ordered `TraceStep`s) through the wheel's `TraceStore.save`. The store MUST hold at least 1000 traces. **[converge c1]** This requires exposing the already-existing Rust `TraceStore::save`/`get`/`list_traces` and the `TraceCollector` record path through PyO3 (today only `count`/`active_count` are bound) and rebuilding the wheel.
- **FR-8** — The `core/trace_collector.py` trace schema MUST be WO-7-discovery-ready: it MUST record enough structure (ordered tool-call sequences with timestamps and outcomes) for a future skill-discovery pass to read recurring tool sequences. **[converge c1]** The existing Rust `Trace`/`TraceStep` core types (types.rs:379 / types.rs:364) already provide `steps` (ordered), `started_at`/`ended_at`, `outcome`, `model`, `engine`, `total_tokens`, `total_latency_seconds` — WO-6 exposes and round-trip-tests this schema; no discovery logic is implemented in WO-6.

### Telemetry store
- **FR-9** — The NEW module `core/telemetry.py` MUST provide `TelemetryStore` (PyO3-bound, single `path` arg), a per-call telemetry RECORD type carrying model identity, token counts (prompt/completion/total) and latency, and `TelemetryAggregator`, gated behind `enable_telemetry`. **[converge c1]** The persistence row is the existing Rust `TelemetryRecord` (types.rs:276) which ALREADY carries `model_id`, `prompt_tokens`, `completion_tokens`, `total_tokens`, `latency_seconds`, `ttft`, `cost_usd` — and `TelemetryStore::record` already writes it. WO-6 exposes a `record(...)`/record-type binding through PyO3 (today only `count`/`clear` are bound; `TelemetrySample` is a SEPARATE hardware-energy ring-buffer type and is NOT the persistence row) and rebuilds the wheel. No Rust schema change is needed. **[converge c3]** `TelemetryAggregator.stats()` MUST return REAL non-zero aggregates computed from the persisted rows (not the zero-stub `AggregateStats::default()`): `total_requests` (COUNT), `total_tokens` (SUM), `avg_latency` (AVG latency_seconds), `avg_throughput` (AVG throughput_tok_per_sec), `total_cost` (SUM cost_usd), `total_energy` (SUM energy_joules) over the `telemetry` table that `record()` populates. Implement the SQL aggregation in the Rust telemetry crate's `aggregator.rs`, refresh the PyO3 stats() surface (the `AggregateStats` JSON shape is unchanged), and rebuild the arm64 wheel. No telemetry-row SCHEMA change.

### Store executor threading layer
- **FR-10** — A NEW module `core/store_executor.py` MUST route all store calls through a `ThreadPoolExecutor(max_workers=1)` (single writer), marshalling results back to the Qt main thread via Qt signals, and MUST enable the WAL pragma on every SQLite database it opens. No store call may run synchronously on the Qt main thread.
- **FR-11** — `core/store_executor.py` MUST mirror the proven WO-1 adapter pattern in `core/mark_xl_rust_adapter.py`: optional-import guard that sets a `WHEEL_AVAILABLE`-style boolean and never raises on missing wheel (mark_xl_rust_adapter.py:56-62); a lazily-created daemon-safe pool with `atexit` shutdown (`wait=False`); and a Qt-signal result bridge where results never touch Qt objects from a worker thread (mark_xl_rust_adapter.py:17-25 docstring contract). It MUST NOT introduce a second, independent threading mechanism.

### Packaging
- **FR-12** — A `pyproject.toml` MUST be created at the repo root declaring a `mark-xl` console entry point and enabling `python -m mark_xl` via a `mark_xl/__main__.py` (or equivalent package `__main__`).
- **FR-13** — A `LICENSE` file (MIT) MUST be created at the repo root.
- **FR-14** — A `NOTICE` file (Apache-2.0 attribution for the vendored OJ crates) MUST be created at the repo root.
- **FR-15** — The existing `requirements.txt` (54 lines, present, currently 0% pinned) MUST have every dependency pinned to an exact version.
- **FR-16** — The existing `docs/VENDORING.md` (84 lines, present) MUST be extended with a note describing the quarterly rsync sync procedure for the vendored crates (the file already documents rsync, not git-subtree — align the wording), **and MUST document the arm64-macOS-only wheel-build/re-vendor procedure (converge c1).**

### WO-6 flag set
- **FR-17** — `config/flags.json` MUST gain four NEW boolean flags, all default `false`, following the existing `use_*`/`enable_*` convention alongside the current keys (flags.json:2-6): `use_scheduler_store` (FR-1), `use_session_store` (FR-4), `enable_trace_store` (FR-7), and `enable_telemetry` (FR-9). One boolean flag governs each store area. **[converge c1: all four RETAINED — `enable_telemetry` is NOT dropped.]**
- **FR-18** — The WO-0 exact-set get_flag allowlist MUST be extended. The test `tests/test_flags.py::test_only_sanctioned_production_code_calls_get_flag` asserts `set(callers) == {"main.py", "agent/executor.py"}` (test_flags.py:139) via a SUBSTRING scan (`"get_flag" in src`, test_flags.py:131) over every production `.py`. Every WO-6 production file that gates on a WO-6 flag MUST be added to that exact set, and only those files; the assertion AND its failure message MUST be updated in lockstep, and no new file may contain a stray `get_flag` substring outside a sanctioned caller. PREFERRED approach (WO-3 precedent): pass resolved flag booleans INTO the store modules from `main.py`/`agent/executor.py` to keep the caller set minimal; extend the exact set only for genuinely-new sanctioned gating sites.

## Non-Functional Requirements

- **NFR-1 (Threading contract)** — No store read or write may execute synchronously or via `block_on` on the Qt main thread. All store access routes through `core/store_executor.py`'s single-worker pool with Qt-signal result marshalling.
- **NFR-2 (WAL + single writer)** — Every SQLite database opened by any WO-6 store MUST have the WAL journal pragma enabled, and all writes MUST funnel through exactly one writer thread (`max_workers=1`). The Python store-owner runs `PRAGMA journal_mode=WAL` on the path it owns (the Rust stores open the connection but do not set WAL).
- **NFR-3 (Flag-OFF == baseline)** — With all four WO-6 flags OFF, the application's observable behavior MUST be byte-identical to the pre-WO-6 baseline (cfcd5db): legacy in-memory scheduler, JSON conversation history, no trace/telemetry capture. No module-level store construction at import time.
- **NFR-4 (Wheel-optional graceful degrade)** — Every store path MUST degrade gracefully to its legacy in-memory/JSON behavior when `WHEEL_AVAILABLE` is False OR the governing WO-6 flag is OFF. No store path may raise solely because the wheel is absent. **(Material on Linux/Windows, where the arm64-macOS-only wheel is absent by design.)**
- **NFR-5 (Migration loss-free + reversible)** — Any JSON→SQLite migration MUST write a `.bak` of the source before any write and MUST be round-trippable: every migrated record reads back identically to its source.
- **NFR-6 (Trace capacity)** — `core/trace_collector.py`'s `TraceStore` MUST sustain at least 1000 stored traces without loss or error.
- **NFR-7 (CI matrix)** — CI MUST be green on the required matrix. **[converge c1]** The Rust-backed wheel + its store tests build/run on an **arm64 macOS runner only (`macos-14`/`macos-latest` — NOT `macos-13`, which is x86 and will not allocate per `markxl-wo0-ci-env-gaps`)**. The existing Linux + Windows lanes (× Python 3.11–3.13) MUST stay green: the arm64-only Rust-backed store tests are SKIPPED there (the wheel does not import), but AC6 flag-OFF byte-parity MUST pass on every lane. No new platform lanes are added; no multi-OS wheel matrix.

## Edge Cases

- **Wheel absent** — Importing any WO-6 store module MUST succeed with the `WHEEL_AVAILABLE`-style boolean set False; the governing code paths fall back to legacy in-memory/JSON behavior without raising. (This is the normal state on Linux/Windows.)
- **Flag OFF mid-store-area** — With a given WO-6 flag OFF but the wheel present, that store area MUST stay on legacy behavior; the present wheel MUST NOT be used opportunistically.
- **Corrupt / partial JSON during migration** — If the source JSON conversation history is corrupt or truncated, the migration MUST NOT destroy the source (the `.bak` is written first) and MUST fail safe (legacy JSON path retained) rather than half-writing the SQLite store.
- **Concurrent writers contending the single writer** — Multiple callers issuing store writes concurrently MUST be serialized through the single writer thread without data loss, deadlock, or Qt main-thread stalls.
- **Restart mid-task** — A task interrupted mid-run by a restart MUST be reloadable from `SchedulerStore` in a coherent state on next launch (its persisted status reflects the last recorded transition).
- **Trace store at / over capacity** — At and beyond 1000 traces the `TraceStore` MUST continue to function (per its defined retention behavior) without raising or corrupting earlier traces.
- **DB locked** — A transient SQLite "database is locked" condition MUST be handled by the single-writer/WAL design rather than surfacing as an unhandled error to the Qt main thread.
- **Qt thread-affinity violations** — Results from worker-thread store calls MUST reach the Qt main thread only via signal marshalling; no Qt object may be touched from the worker thread.

## Testing Acceptance Scenarios

- **AC1 (scheduler restart survival)** — Create and persist a scheduled task via `SchedulerStore` with `use_scheduler_store` ON; simulate process restart (re-instantiate `TaskQueue`); assert the task is reloaded with its `TaskStatus`/`TaskPriority` intact.
- **AC2 (concurrency / FPS)** — Drive a QTimer alongside 100 store writes through `core/store_executor.py`; probe the frame rate and assert ≥30 FPS (no Qt main-thread stall); assert the WAL pragma is enabled on every opened DB.
- **AC3 (entry point)** — Invoke `python -m mark_xl` (and the `mark-xl` console script) and assert the application entry point launches without error, confirming `pyproject.toml` + `mark_xl/__main__.py`.
- **AC4 (session migration loss-free)** — With `use_session_store` ON, run the JSON→SQLite session migration on a fixture JSON history; assert a `.bak` of the source was written and that every migrated message round-trips back identically.
- **AC5 (trace capacity + schema)** — Write 1000+ traces to the (rebuilt) `TraceStore` via the exposed `save`/record path; assert all are retrievable (`count`/`list_traces`/`get`) and that the recorded schema exposes ordered tool-call sequences (`TraceStep`s) with outcomes/timestamps sufficient for WO-7 discovery. **[converge c1: now satisfiable — the rebuilt wheel exposes the write path.]**
- **AC5b (telemetry round-trip + REAL aggregation) [converge c3 — STRENGTHENED]** — With `enable_telemetry` ON, write N telemetry records with KNOWN inputs (model_id + prompt/completion/total tokens + latency + cost) via the exposed `TelemetryStore.record` path; assert `count()` == N AND assert `TelemetryAggregator.stats()` returns the CORRECT computed aggregates: `total_requests` == N, `total_tokens` == sum of inputs, `avg_latency` == mean of inputs, `total_cost` == sum of inputs (all NON-ZERO and numerically correct, not just structurally present). The prior c1 'accept zero-stub / defer rollup to WO-7' disposition is SUPERSEDED.
- **AC6 (flag-OFF baseline)** — With all four WO-6 flags OFF (and separately with the wheel absent — the normal Linux/Windows state), assert observable behavior equals the pre-WO-6 baseline (cfcd5db: legacy in-memory scheduler + JSON history, no trace/telemetry capture). This AC MUST pass on every CI lane.
- **AC7 (CI matrix green)** — Run the suite on the required matrix; assert all required lanes are green: **arm64 macOS (`macos-latest`) runs the Rust-backed wheel + store tests; Linux + Windows × Python 3.11–3.13 run with the arm64-only store tests SKIPPED but AC6 parity green. `macos-13` (x86) is informational only and not required.**
- **AC8 (packaging + licensing)** — Assert `LICENSE` (MIT) and `NOTICE` (Apache-2.0 attribution) exist at the repo root, that `requirements.txt` dependencies are all exact-pinned, and that `docs/VENDORING.md` contains the quarterly sync note + the arm64-macOS-only wheel-build procedure.

## Acceptance Checklist

- [ ] **AC1** — Scheduled task survives restart (persist to `SchedulerStore`, reload on start).
- [ ] **AC2** — Concurrency test — QTimer + 100 writes holds ≥30 FPS (no Qt main-thread stalls); WAL enabled on all DBs.
- [ ] **AC3** — `python -m mark_xl` entry point works (`pyproject.toml` + `__main__`).
- [ ] **AC4** — JSON→SQLite session migration loss-free (`.bak` written + round-trip test).
- [ ] **AC5** — Trace store holds 1000+ traces; schema (ordered `TraceStep`s) is WO-7-discovery-ready; write path exposed via rebuilt wheel.
- [ ] **AC5b** — Telemetry record round-trips via `TelemetryStore.record`; `TelemetryAggregator.stats()` returns CORRECT non-zero aggregates (count/sum/avg) on a known-input round-trip [converge c3].
- [ ] **AC6** — flag-OFF == baseline (legacy in-memory/JSON behavior unchanged when WO-6 flags OFF / wheel absent); green on every lane.
- [ ] **AC7** — CI green: arm64-macOS runs Rust-backed wheel+tests; Linux/Win × Py3.11–3.13 green with arm64-only tests skipped; macOS-13 informational.
- [ ] **AC8** — `LICENSE` (MIT) + `NOTICE` (Apache-2.0) present; requirements pinned; `VENDORING.md` quarterly + arm64-build note added.

## WO-6 wheel-rebuild addendum (converge c1)

**Decision:** Option 3 — rebuild the Rust wheel (arm64 macOS only); full WO-6 surface retained.
**Root cause (refined from Phase-2):** the Phase-2 finding "no Python write path / TelemetrySample is hardware-only" was correct AT THE PYTHON BOUNDARY but the cause is binding-only. Verified in the Rust source:
- `openjarvis-traces/src/store.rs:57` `TraceStore::save(&Trace)` + `:92 get` + `:139 list_traces` + `:196 count` ALL exist; schema (store.rs:26-41) includes `steps_json` (ordered), `outcome`, `model`, `engine`, `started_at`/`ended_at`, `total_tokens`, `total_latency_seconds` — WO-7-ready.
- `openjarvis-telemetry/src/store.rs:68` `TelemetryStore::record(&TelemetryRecord)` exists; `TelemetryRecord` (core/types.rs:276) carries `model_id`, `prompt_tokens`, `completion_tokens`, `total_tokens`, `latency_seconds`, `ttft`, `cost_usd` — exactly FR-9.
- The gap is in `crates/openjarvis-python/src/traces.rs` (PyTraceStore exposes only `count`; PyTraceCollector only `active_count`) and `.../telemetry.rs` (PyTelemetryStore only `count`/`clear`; `PyTelemetrySample` is a SEPARATE hardware ring-buffer type, NOT the persistence row).

**Wheel-rebuild work (bounded; WO-4 precedent):**
1. PyO3 bindings: add `save`/`get`/`list_traces` (and a `Trace`/`TraceStep` py-type or dict round-trip) to `PyTraceStore`; add a record path to `PyTraceCollector`; add `record(...)` + a `PyTelemetryRecord` py-type (model/token/latency) to `PyTelemetryStore`. NO new Rust storage logic, NO telemetry schema change.
2. Rebuild for arm64 macOS only: `source $HOME/.cargo/env` before `maturin` (cargo off PATH — WO-4 lesson); watch the PyO3 arg-order trap (Python kw order vs Rust positional — WO-4 lesson); vectors f64 (n/a here, no embeddings).
3. Re-vendor the built wheel as in WO-4; update `docs/VENDORING.md` with the arm64-only build/re-vendor steps (FR-16).

**CI reconciliation (no new platforms):** arm64-mac runner (`macos-latest`) builds+tests the wheel and Rust-backed stores; Linux/Windows lanes skip the arm64-only store tests via a `WHEEL_AVAILABLE`/platform guard while AC6 flag-OFF parity runs green everywhere (NFR-7/AC7).

## WO-6 telemetry-aggregation addendum (converge c3)

**Decision (user-authorized via coordinator, converge cycle c3 = 3/3, LAST allowed):** the c1 disposition that accepted the zero-stub `TelemetryAggregator.stats()` (deferring the rollup to WO-7) is SUPERSEDED. WO-6 MUST ship REAL telemetry aggregation.

**Root cause (verified):** `mark-xl-rust-fork/crates/openjarvis-telemetry/src/aggregator.rs` defines a complete `AggregateStats` struct (`total_requests, total_tokens, avg_latency, avg_throughput, total_cost, total_energy`) and the PyO3 binding already serializes it to JSON — but `TelemetryAggregator::stats(_store)` IGNORES the store and returns `AggregateStats::default()` (all zeros). The `telemetry` SQLite table (store.rs:28-50) already has every needed column and is populated by `record()`. The gap is purely the missing SQL aggregation in the `stats()` body.

**c3 work (bounded; no schema/struct/binding-shape change):**
1. Rust: implement `TelemetryAggregator::stats(store)` to run a single `SELECT COUNT(*), SUM(total_tokens), AVG(latency_seconds), AVG(throughput_tok_per_sec), SUM(cost_usd), SUM(energy_joules) FROM telemetry` and map the row into `AggregateStats`. Add a `pub(crate)` connection/query accessor on `TelemetryStore` if the aggregator cannot reach `conn` directly. Handle the empty-table case (0 rows → zeros, no error).
2. PyO3: no signature change — `PyTelemetryAggregator.stats()` already returns the JSON; just rebuild so the real computation is compiled in.
3. Rebuild the arm64 wheel + re-vendor (WO-4 precedent: `source $HOME/.cargo/env` before maturin; field-by-field struct construction with `..Default::default()`). arm64-macOS ONLY.
4. Tests: `tests/test_wo6_stores.py` AC5b asserts REAL numbers on a known-input round-trip (write N records → stats() == expected count/sum/avg), not just `count()`.

**Standing constraints preserved:** AC6 flag-OFF==byte-baseline on every lane; `tests/test_flags.py:139` callers stay at 2 (no new `get_flag` substring); arm64-only CI skip pattern intact; no second threading mechanism.

## SPM

- **Scope** — One bounded context: persistence + packaging. Four PyO3 SQLite stores (`SchedulerStore`, `SessionStore`, `TraceStore`, `TelemetryStore`) plus the `core/store_executor.py` threading layer, one JSON→SQLite migration, four new feature flags, an **arm64-macOS-only PyO3-binding-exposure + wheel rebuild + re-vendor**, and the packaging artifacts (`pyproject.toml`, `LICENSE`, `NOTICE`, pinned `requirements.txt`, `VENDORING.md` extension). No new dispatch path; reuse the WO-2 registry.
- **Effort / schedule** — Tier B, ~2–3 weeks, medium risk. The wheel rebuild is bounded (binding exposure only; no new Rust logic) — risk lower than the Phase-2 worst case.
- **Key risks** — (1) Qt thread-affinity violation; (2) migration data loss (mitigated by `.bak` + round-trip); (3) PyO3 arg-order trap on the new bindings (WO-4 lesson); (4) allowlist drift (exact-set get_flag test); (5) single-writer contention; (6) arm64-only CI gating must not break existing Linux/Windows lanes (AC6 must stay green while store tests are skipped).
- **Requirement notes** — Estimated ~18–22 atomic tasks (the wheel-rebuild + binding lane adds ~3–4 over the original ~15–20). If decomposition exceeds 22 tasks, a non-blocking lane-split is suggested: Lane W = PyO3 bindings + wheel rebuild + re-vendor (lands FIRST — scheduler/session can use the existing methods, but trace/telemetry depend on it), Lane A = scheduler + session stores (incl. migration), Lane B = trace + telemetry stores (depends on Lane W), Lane C = packaging + licensing. `core/store_executor.py` is the shared threading dependency and lands before A/B.

## Out of Scope

- WO-7 skills engine / discovery — ONLY the trace SCHEMA must be WO-7-ready; no engine port, no discovery code in WO-6. **[converge c1: trace/telemetry WRITE is IN scope for WO-6, not deferred.]**
- The Rust *agent* classes (they require a live-engine bridge); only the hermetic single-`path` storage primitives are in scope.
- DSPy / GEPA / RL.
- OJ paradigm agents.
- Any NEW dispatch path — reuse the WO-2 tool registry.
- Any second threading mechanism — mirror the WO-1 `core/mark_xl_rust_adapter.py` adapter.
- **Multi-OS wheels (x86 macOS / Linux / Windows wheels)** — the wheel targets arm64 macOS ONLY; other platforms run wheel-absent/legacy.
- **New Rust storage logic or telemetry-schema changes** — the rebuild only EXPOSES existing Rust `save`/`record` methods via PyO3.

## Lessons Applied

Prior reflections consulted: harness `MEMORY.md` (WO-0..WO-5 per-WO memories + `markxl-stack-merged-to-main`), `pipeline/memory/0-constitution-reflect.md`, `pipeline/memory/phase_1-reflect.md`, `pipeline/memory/phase_2-reflect.md`. No `pipeline/tasks/learnings.md` exists yet (first WO through the 7-phase pipeline).

- **WO-0 get_flag allowlist is an EXACT-SET test via SUBSTRING scan** (test_flags.py:131,139). Applied: FR-18 requires extending the exact set AND its failure message in lockstep, with no stray `get_flag` substring; preferred WO-3-style pass-bools-in to keep callers minimal.
- **StoreExecutor = the WO-1 `mark_xl_rust_adapter` pattern; do NOT invent a second mechanism** (FR-11, pinned to mark_xl_rust_adapter.py:17-25,56-62).
- **Storage primitives are hermetic (single `path`, no Tokio reactor)** (every store FR; Rust agent classes Out of Scope).
- **flag-OFF must equal byte-identical baseline and wheel-absent must also fall back** (NFR-3 + NFR-4; AC6 tests both — now also the normal Linux/Windows state).
- **Session persistence (WO-6) is DISTINCT from WO-4's `core/memory_v2.py` semantic FACT memory** (FR-4).
- **V&V/local green is macOS-only; Windows/Linux regressions slip past local runs** (`markxl-local-vnv-is-macos-only`). Applied: NFR-7/AC7 pin arm64-mac for the wheel and require Linux/Windows green with store tests skipped; `.as_posix()` all path strings.
- **[converge c1] Verify capability by EXERCISING the wheel AND reading the Rust source, not export-name presence** (Phase-2 lesson + this converge). The Python-boundary read-only finding was real; reading `store.rs`/`types.rs` revealed the Rust write path already exists, shrinking Option 3 to binding-exposure only.
- **[converge c1] WO-4 wheel-rebuild gotchas** (`wo4-memory-shipped`): `source $HOME/.cargo/env` before maturin; PyO3 arg-order trap. Applied to the rebuild work in the addendum.
