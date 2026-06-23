# Intake — WO-6 (Maturity: unified stores + packaging)

## Problem statement
Mark-XL's task queue is in-memory (lost on restart), there is no telemetry/trace persistence, and there is no
packaging. Adopt OJ's bundled-SQLite stores under the threading contract, finalize packaging, and make the trace
store ready for WO-7 discovery.

## Complexity tier
**Complex** (Tier B, 2–3 wk, med risk). Single bounded context (the persistence/packaging layer). Not dual-client.
`intake.dual_client = false`.

## Dependencies (all satisfied)
WO-0 (CI, flags, tests), WO-1 (wheel + adapter threading pattern), WO-2 (registry — no 2nd dispatch). All merged to
fork `main@cfcd5db`. WO-3/4/5 also merged (not strict deps but present).

## Scope (verified against live code on wo-6-maturity @ origin/main cfcd5db)
1. **Scheduler store** — replace `agent/task_queue.py` in-memory list (confirmed: list+lock, lines 37-210) with
   `SchedulerStore` (methods: create_task, get_task, list_tasks, update_status, delete_task, record_run). Preserve
   priority/status semantics. Route through StoreExecutor. Scheduled task survives restart.
2. **Session store** — add conversation persistence via `SessionStore` (get_or_create, save_message, list_sessions,
   link_channel, consolidate, decay) in `memory/memory_manager.py`. Auto-migrate the JSON conversation history;
   write `.bak`. NOTE: WO-4 added `core/memory_v2.py` (semantic FACT memory) — that is distinct; WO-6 adds CONVERSATION
   session persistence. memory_manager.py currently has NO conversation/session handling (verified).
3. **Trace store** (new `core/trace_collector.py`) — capture tool calls / outcome / latency via `TraceStore` +
   `TraceCollector`. Must hold 1000+ traces. **Feeds WO-7** (the trace schema must be WO-7-ready).
4. **Telemetry store** (new `core/telemetry.py`) — latency/tokens/model per call via `TelemetryStore` +
   `TelemetrySample` (+ TelemetryAggregator).
5. **Threading contract** (new `core/store_executor.py`) — `ThreadPoolExecutor(max_workers=1)` + Qt signals + WAL +
   single writer. **Mirror the proven WO-1 `core/mark_xl_rust_adapter.py` pattern** (lazy daemon-safe pool, atexit
   shutdown wait=False, RustResultBridge-style Qt signal marshalling). Reuse, do not reinvent.
6. **Packaging** — create MISSING `pyproject.toml` (entry point `mark-xl` + `python -m mark_xl`), MISSING `LICENSE`
   (MIT), MISSING `NOTICE` (Apache-2.0 attribution for vendored OJ crates); pin `requirements.txt` (present, 54 lines);
   extend existing `docs/VENDORING.md` (present, 84 lines) with the quarterly git-subtree/rsync sync note.

## Verified facts (from orientation — do not re-derive)
- Wheel exports ALL needed stores: SchedulerStore, SessionStore, TraceStore, TraceCollector, TelemetryStore,
  TelemetrySample, TelemetryAggregator, TelemetrySessionCore, TraceAnalyzer, TraceDrivenPolicy, A2ATaskStore (89 total).
- All four stores construct HERMETICALLY with a single `path` arg — no Tokio reactor (WO-1 carry-forward confirmed for
  storage primitives; only the Rust *agent* classes need a live engine bridge).
- Existing flag rail `config/flags.json` (9 keys, incl. WO-1..WO-5 flags). WO-6 adds new flags, all default OFF.
- **WO-0 recurring test defect (carry-forward #64):** `tests/test_flags.py::test_no_production_code_calls_get_flag`
  asserts ZERO production get_flag callers. WO-6 adds sanctioned callers -> MUST extend that allowlist (it should
  become a maintained allowlist at the WO-0 source; each WO that adds a caller updates it).
- No existing SessionStore/SchedulerStore/TraceStore/TelemetryStore/store_executor/trace_collector references in py
  (clean greenfield for the stores).
- Packaging: pyproject.toml MISSING, LICENSE MISSING, NOTICE MISSING; requirements.txt + docs/VENDORING.md PRESENT.

## Constraints
Never call sync stores from the Qt main thread; WAL on all DBs; single writer; task-queue loss on restart is
acceptable, session continuity is NOT; trace store must hold 1000+ traces for WO-7. Wheel optional -> graceful
degrade when WHEEL_AVAILABLE False or flag OFF. flag-OFF == baseline.

## Acceptance criteria
- AC1: Scheduled task survives restart (persist to SchedulerStore, reload on start).
- AC2: Concurrency test — QTimer + 100 writes holds ≥30 FPS (no Qt main-thread stalls); WAL enabled on all DBs.
- AC3: `python -m mark_xl` entry point works (pyproject.toml + __main__).
- AC4: JSON→SQLite session migration loss-free (.bak written + round-trip test).
- AC5: Trace store holds 1000+ traces; schema is WO-7-discovery-ready.
- AC6: flag-OFF == baseline (legacy in-memory/JSON behavior unchanged when WO-6 flags OFF / wheel absent).
- AC7: CI green on the required matrix (macOS-arm64 + Win + Linux × Py3.11–3.13; macOS-13 informational).
- AC8: LICENSE (MIT) + NOTICE (Apache-2.0 attribution) present; requirements pinned; VENDORING.md sync note added.

## Scope-decomposition note (non-blocking)
WO-6 spans 4 new store integrations + packaging = ~5 bounded sub-areas but ONE context (persistence/packaging). Under
the >3-context / >20-task heuristic this is borderline; the task decomposition (Phase 4) should keep it to ~15-20
tasks. If Development finds it exceeds ~20 tasks, flag for a split (scheduler+session as lane A, trace+telemetry as
lane B, packaging as lane C) — non-blocking suggestion, not a hard split.
