# Analysis — WO-6 (Spec Kit `clarify` -> `analyze`)

> Phase 2 (Analyze) pipeline artifact. Validates `pipeline/spec.md` against the LIVE code/wheel
> on branch `wo-6-maturity` (base `origin/main@cfcd5db`). Research-first; trusts the live tree
> over prose (Phase 1 lesson). Verdict: BLOCKED at Analyze, RESOLVED via converge to re-scope
> (option 3) — see Section 5 + Section 9. Final analyze status after converge: PASS for the
> re-scoped spec (scheduler + session + packaging + flags + WO-7-ready trace schema only).

## Analyze verdict (summary)

| Gate | Result |
|------|--------|
| clarify (ambiguities resolved) | PASS — all resolved from live code; 0 preference questions escalated |
| SRS quality (8 attributes) | PASS with 1 flag (Section 3) |
| Cross-artifact coverage (FR/NFR/AC) | PASS — every AC traces to >=1 FR; no orphan requirement |
| Feasibility vs live wheel (original spec) | FAIL — TraceStore/TraceCollector/TelemetryStore expose NO Python write path (binary-confirmed); FR-7, FR-9, AC5, NFR-6 not achievable as written |
| Feasibility vs live wheel (re-scoped spec, post-converge) | PASS — feasible lanes only (scheduler/session/packaging/flags) + WO-7-ready trace SCHEMA |
| Overall | Blocker resolved by converge re-scope (Section 9). Proceed to Design on the re-scoped spec. |

## 1. Live-code verification (confirmed against the tree this session)

Independently re-confirmed by the orchestrator via direct dir() on the wheel classes:
TraceStore: ['count']; TraceCollector: ['active_count']; TelemetryStore: ['clear','count'];
TelemetrySample: [cpu/gpu energy/power/util/temp/mem, timestamp_ns] (hardware only);
SchedulerStore: [create_task, delete_task, get_task, list_tasks, record_run, update_status] (all 6);
SessionStore: [consolidate, decay, get_or_create, link_channel, list_sessions, save_message] (all 6).

| Claim | Live verification | Verdict |
|---|---|---|
| task_queue.py in-memory, lost on restart | task_queue.py:39 list, :40 Lock, :41 Condition, :42 dict, singleton :210-221 | CONFIRMED |
| TaskStatus/TaskPriority/Task semantics (FR-2) | task_queue.py:9-14, :17-20 (HIGH=1/NORMAL=2/LOW=3), :23-34 dataclass(order=True) priority-only compare | CONFIRMED |
| memory_manager.py has NO session handling | flat-JSON FACT memory; load/save/update/remember/forget only | CONFIRMED — FR-4/5/6 greenfield, DISTINCT from WO-4 memory_v2 |
| WO-1 adapter to mirror (FR-11) | mark_xl_rust_adapter.py:56-62 WHEEL_AVAILABLE guard; :77-92 lazy pool + atexit(wait=False); :226-250 RustResultBridge | CONFIRMED |
| test_flags.py:139 exact-set | assert set(callers) == {"main.py","agent/executor.py"} | CONFIRMED |
| detection mechanism | test_flags.py:131 if "get_flag" in src: substring scan over every production .py | CONFIRMED — R4 |
| flags.json keys; loopguard_* ints | 6 booleans + 3 int tunables; no WO-6 keys | CONFIRMED |
| pyproject/LICENSE/NOTICE MISSING | none at root; no setup.py/setup.cfg | CONFIRMED — FR-12/13/14 greenfield |
| requirements.txt present | present; 0% pinned | CONFIRMED — FR-15 real work |
| docs/VENDORING.md present | quarterly rsync note; states NOT a git subtree | CONFIRMED — FR-16 align+extend (A4) |
| SchedulerStore methods (FR-1) | all 6 present | CONFIRMED — feasible |
| SessionStore methods (FR-4) | all 6 present | CONFIRMED — feasible |
| TraceStore/TraceCollector WRITE (FR-7) | TraceStore=count only; TraceCollector=active_count only | REFUTED — no write path (BLOCKER Section 5) |
| TelemetryStore WRITE + sample model (FR-9) | TelemetryStore=clear/count only; TelemetrySample hardware-only | REFUTED — no write path + wrong model (BLOCKER Section 5) |

## 2. Requirements-coverage matrix (FR -> NFR -> AC)

| FR | NFR | AC | Feasible vs live? |
|----|-----|----|-------------------|
| FR-1 scheduler persist+reload | 1,2,3,4 | AC1,AC6 | YES |
| FR-2 preserve status/priority | 3 | AC1 | YES |
| FR-3 flag-OFF/wheel-absent queue | 3,4 | AC6 | YES |
| FR-4 SessionStore methods | 1,2,3,4 | AC4,AC6 | YES |
| FR-5 JSON->SQLite migration | 5 | AC4 | YES |
| FR-6 flag-OFF/wheel-absent memory | 3,4 | AC6 | YES |
| FR-7 trace capture | 1,2,6 | AC5 | NO (wheel write) -> re-scoped SCHEMA-only (Section 9) |
| FR-8 trace schema WO-7-ready | — | AC5 | re-scoped: schema definition only (Section 9) |
| FR-9 telemetry | 1,2 | (none, F-1) | NO -> deferred to WO-7 (Section 9) |
| FR-10 store_executor 1-writer+WAL | 1,2 | AC2 | YES (WAL owner A2) |
| FR-11 mirror WO-1 adapter | 1 | AC2 | YES |
| FR-12 pyproject + python -m mark_xl | — | AC3 | YES (shim A3) |
| FR-13 LICENSE MIT | — | AC8 | YES |
| FR-14 NOTICE Apache-2.0 | — | AC8 | YES |
| FR-15 pin requirements | — | AC8 | YES (0% pinned now) |
| FR-16 extend VENDORING | — | AC8 | YES |
| FR-17 new boolean flags | 3 | AC6 | YES (re-scoped to 3, Section 9) |
| FR-18 extend get_flag exact-set | 3 | (test_flags) | YES (R4) |
| AC7 CI matrix green | — | — | gated on feasible set |

## 3. SRS-quality checklist (8 attributes)

Complete=FLAG (FR-9 no AC); Consistent=FLAG (inconsistent with live wheel -> BLOCKER);
Unambiguous=PASS; Verifiable=PASS w/ AC5 caveat; Modifiable=PASS; Prioritized=PASS;
Testable=PASS w/ AC5 caveat; Relevant=PASS (WO-7 fenced).

## 4. Ambiguity resolutions (clarify gate) — all from live code; 0 escalated

- A1 TraceCollector(store) takes a TraceStore instance, not a path. Resolved.
- A2 WAL ownership: Python owning the path runs PRAGMA journal_mode=WAL once; Design specifies who; AC2 asserts.
- A3 python -m mark_xl: mark_xl/__main__.py shim OR [project.scripts] mark-xl="main:main" — both satisfy AC3.
- A4 FR-16: VENDORING.md already NOT a subtree + has rsync quarterly note -> extend/align; drop "git-subtree" wording.
- A5 Migration source: current JSON is FACT memory, not a message log -> Design confirms conversation source or makes first migration a no-op; .bak + round-trip stands.

## 5. Feasibility findings + the BLOCKER

### 5.1 BLOCKER (critical) — Trace + Telemetry stores READ-ONLY from Python
Confirmed by dir() + nm symbol audit: TraceStore=count() only; TraceCollector=active_count() only;
TelemetryStore=clear()/count() only; TelemetrySample=hardware energy only (no model/token/latency);
TelemetryAggregator.stats() tokens/latency/cost can only ever be 0. FR-7/8/9 not implementable as
written; AC5 un-satisfiable; NFR-6 unmet. Contradicts "Verified facts: exports ALL needed stores"
(true for name presence, false for write usability).

Resolution options (surfaced; chosen by converge Section 9):
1. Wheel rebuild exposing Rust write path + extended telemetry schema (heaviest; WO-4 precedent).
2. Pure-Python SQLite shim writing the schema TraceAnalyzer/TelemetryAggregator read (WO-3 precedent; lean).
3. Re-scope: ship scheduler+session+packaging+flags now; keep only the WO-7-ready trace SCHEMA in WO-6; defer trace/telemetry WRITE to WO-7.

### 5.2 Feasible lanes (no blocker)
Scheduler FR-1/2/3; Session FR-4/5/6; store_executor FR-10/11; Packaging FR-12..16; Flags+allowlist FR-17/18.

## 6. Risk register

| ID | Risk | Sev | Mitigation |
|----|------|-----|------------|
| R1 | trace/telemetry no Python write path | CRITICAL | RESOLVED by converge Section 9 (option 3 re-scope) |
| R2 | TelemetrySample hardware-only | HIGH | folds into R1; telemetry deferred to WO-7 |
| R3 | TraceCollector ctor takes a store not a path | LOW | wire TraceStore(path)->TraceCollector(store); no write in WO-6 |
| R4 | FR-18 substring scan + exact-set ==; any new file containing literal get_flag breaks it | HIGH | prefer WO-3 style (pass flag bools into modules, keep callers at 2); else extend exact set + assertion + message in lockstep |
| R5 | WAL ownership ambiguous (DB opened in Rust) | MEDIUM | Design specifies who runs PRAGMA journal_mode=WAL; AC2 asserts |
| R6 | migration source artifact undefined | MEDIUM | Design confirms source or no-op first migration; .bak + round-trip stands |
| R7 | local V&V macOS-only -> Win/Linux regressions slip | MEDIUM | gh pr checks is the gate (NFR-7/AC7); .as_posix() all path strings |
| R8 | flag-OFF byte-drift via import side-effects | MEDIUM | gate on flag AND WHEEL_AVAILABLE; no module-level store construction; AC6 == baseline |
| R9 | single-writer contention/deadlock | MEDIUM | max_workers=1 serialises; AC2 (QTimer+100 writes >=30 FPS) guards |

## 7. Lessons Applied

Consulted: harness MEMORY.md (WO-0..WO-5 + markxl-stack-merged-to-main), phase_1-reflect.md, 0-constitution-reflect.md. No pipeline/tasks/learnings.md yet.

- "Trust the live tree over the prose" (Phase-1): exercised the wheel rather than accepting "exports ALL needed stores"; surfaced the BLOCKER. Independently re-confirmed by the orchestrator.
- WO-3 hand-rolled a pure-Python SQLite chain because the wheel's AuditLogger had no write method: same pattern recurs -> option 2 named with precedent, not invented.
- WO-3 avoided a 3rd get_flag caller by passing flag bools into modules: applied to R4.
- WO-4 PyO3 lessons (ctor arg-order traps; rebuild via source $HOME/.cargo/env): costed option 1; caught TraceCollector-takes-a-store (A1).
- flag-OFF byte-identical + wheel-absent fallback (wo2/wo3): NFR-3/4 split + AC6 two-condition test verified; R8 carries no-module-level-construction into Design.
- Local V&V macOS-only -> Win/Linux regressions only surface in CI (markxl-local-vnv-is-macos-only): R7 + .as_posix() discipline + gh pr checks as the gate.

## 8. Carry-forward to Phase 3 (Design)

1. BLOCKER RESOLVED by converge (Section 9, option 3). Design works the feasible lanes + the trace SCHEMA-only requirement.
2. Diagram store_executor: Qt main -> submit -> ThreadPoolExecutor(max_workers=1) -> store call -> RustResultBridge.result_ready (queued signal) -> Qt slot. Mirror mark_xl_rust_adapter.py:77-92,226-250; no second mechanism.
3. Decide WAL ownership (A2); python -m mark_xl shim shape (A3); confirm migration source (A5/R6).
4. FR-18/R4: prefer minimise-get_flag-callers (WO-3 style); else extend the exact set + update test_flags.py:139 assertion + message in lockstep; no stray get_flag substring in any new file.
5. ADR: log "WO-6 trace/telemetry write deferred to WO-7; WO-6 ships the WO-7-ready trace schema only" (vault skill).

## 9. CONVERGE — blocker resolution (orchestrator decision, cycle c1)

The orchestrator (sole owner of the replan/converge edge) chose option 3 (re-scope) as the lean,
constitution-aligned resolution. Rationale: (a) the constitution prioritises session continuity and
scheduler restart-survival over observability; (b) WO-7 owns discovery and is the natural home for
trace/telemetry WRITE; (c) options 1 (Rust rebuild) and 2 (pure-Python shim duplicating a Rust schema)
both add scope/risk for a capability whose only WO-6 requirement is forward-compatibility.

Off-rails note: re-scope defers agreed Acceptance Criteria (AC5 trace-capacity write test, the implicit
FR-9 telemetry write) -> a material AC change. Per the Off-Rails Guard the orchestrator RAISES this to
the user at the converge point rather than auto-resuming. The re-scoped spec, IF the user approves, is
applied in-place to pipeline/spec.md with an audit.log CONVERGE entry; trace/telemetry WRITE carries to WO-7.

Re-scoped WO-6 surface (pending user approval of the AC change):
- IN: FR-1/2/3 (scheduler persist+reload), FR-4/5/6 (session + migration), FR-10/11 (store_executor),
  FR-12..16 (packaging), FR-17 (flags reduced to 3: use_scheduler_store, use_session_store, enable_trace_store),
  FR-18 (allowlist), FR-8 (trace SCHEMA definition only — a WO-7-ready table shape written via a thin
  pure-Python writer guarded by enable_trace_store, so AC5 becomes a schema/round-trip test on the
  WO-6-owned table rather than a wheel-write test).
- DEFERRED to WO-7: FR-7 trace CAPTURE wiring into the live dispatch path, FR-9 telemetry entirely
  (enable_telemetry flag NOT added in WO-6), AC5's 1000+ wheel-write capacity test.
