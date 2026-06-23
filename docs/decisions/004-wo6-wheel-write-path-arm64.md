# ADR 004 — WO-6 trace/telemetry write path via wheel rebuild, arm64-macOS-only

- **Status:** Accepted
- **Date:** 2026-06-23
- **Work order:** WO-6 (Maturity: unified stores + packaging)
- **Deciders:** team-lead / user (relayed), pipeline-orchestrator (converge c1)
- **Supersedes/relates:** ADR 001 (thin pure-Python tool registry), ADR 002 (WO-3 audit hash-chain), ADR 003 (WO-5 LoopGuard wiring)

## Context

WO-6 Phase-2 (Analyze) surfaced a feasibility blocker: at the Python boundary the prebuilt
`mark_xl_rust` wheel exposed only READ methods for the trace/telemetry stores —
`TraceStore.count`, `TraceCollector.active_count`, `TelemetryStore.count`/`clear` — and the
Python `TelemetrySample` carried only hardware-energy fields (no model / token / latency).
This made FR-7 (trace capture), FR-9 (telemetry), AC5 (1000+ trace writes) and NFR-6 un-buildable
as written.

On deeper inspection of the Rust source (not just the Python surface), the gap is **binding-only**:
- `openjarvis-traces/src/store.rs:57` already implements `TraceStore::save(&Trace)` (+ `get`, `list_traces`, `count`); the `traces` SQLite schema (store.rs:26-41) already carries `steps_json` (ordered tool-call steps), `outcome`, `model`, `engine`, `started_at`/`ended_at`, `total_tokens`, `total_latency_seconds` — i.e. it is already WO-7-discovery-ready.
- `openjarvis-telemetry/src/store.rs:68` already implements `TelemetryStore::record(&TelemetryRecord)`; `TelemetryRecord` (core/types.rs:276) already carries `model_id`, `prompt_tokens`, `completion_tokens`, `total_tokens`, `latency_seconds`, `ttft`, `cost_usd` — exactly FR-9's required fields.
- The PyO3 binding layer (`crates/openjarvis-python/src/traces.rs`, `.../telemetry.rs`) simply does not expose these `save`/`record` methods, and `PyTelemetrySample` is a separate hardware ring-buffer type mistaken for the persistence row.

A hard user constraint applies: **target Apple Silicon (arm64 macOS) ONLY** — no x86 macOS, Linux,
or Windows wheels. This collapses the usual "multi-OS wheel matrix" downside of a rebuild to a
single target.

## Decision

Adopt **Option 3 — rebuild the Rust wheel (arm64 macOS only) and retain the full WO-6 surface**
(scheduler + session + trace + telemetry + packaging), rather than re-scoping trace/telemetry to
WO-7 (Option-3-defer, rejected) or hand-rolling a parallel pure-Python SQLite shim (Option 2,
rejected — would duplicate a schema the Rust store already owns).

The rebuild is bounded to **PyO3 binding exposure only**:
1. Expose `TraceStore.save`/`get`/`list_traces` and the `TraceCollector` record path; expose
   `TelemetryStore.record` + a `PyTelemetryRecord` py-type (model/token/latency). No new Rust
   storage logic; no telemetry-schema change.
2. Rebuild for arm64 macOS with `source $HOME/.cargo/env` before `maturin` (cargo off PATH — WO-4
   lesson); guard against the PyO3 arg-order trap (WO-4 lesson).
3. Re-vendor the built wheel as in WO-4; document the arm64-only build/re-vendor steps in
   `docs/VENDORING.md`.

CI is reconciled WITHOUT adding platforms: the arm64-mac runner (`macos-latest`, NOT `macos-13`
x86 which will not allocate per `markxl-wo0-ci-env-gaps`) builds and tests the wheel + Rust-backed
stores; the existing Linux + Windows lanes (× Py 3.11–3.13) SKIP the arm64-only store tests (the
wheel does not import there) while AC6 flag-OFF byte-parity stays green on every lane.

## Consequences

**Positive**
- Full WO-6 surface ships; no capability deferred to WO-7. Trace schema is genuinely WO-7-ready (the real Rust schema, exercised end-to-end).
- Scope is smaller than the Phase-2 worst case (binding exposure, not new Rust logic or schema work).
- arm64-only collapses the multi-OS wheel-matrix risk to one target; matches the existing `macos13_informational` carry-forward.

**Negative / risks**
- A wheel rebuild + re-vendor is required (build-environment surface; WO-4 maturin/cargo-env and PyO3 arg-order gotchas apply).
- Linux/Windows users run wheel-absent (legacy in-memory/JSON) for trace/telemetry — acceptable per NFR-4 and the user's arm64-only intent.
- CI must gate the arm64-only store tests carefully so the existing non-mac lanes are not broken; AC6 parity must remain green on every lane (named as SPM risk 6).

**Neutral**
- `enable_telemetry` flag is RETAINED (not dropped). AC5b (telemetry round-trip) is added, closing the prior SRS gap that FR-9 had no acceptance criterion.

## Alternatives considered

- **Option 1 (rebuild, multi-OS):** rejected by the arm64-only user constraint.
- **Option 2 (pure-Python SQLite shim):** rejected — duplicates the Rust store's existing schema and write logic; higher long-term drift risk than exposing the canonical Rust path.
- **Option 3-defer (re-scope trace/telemetry write to WO-7):** rejected by the user — the full WO-6 surface is to land now.
