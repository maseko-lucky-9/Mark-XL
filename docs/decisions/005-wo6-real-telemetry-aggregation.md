# ADR 005 — WO-6 real telemetry aggregation (re-open the lane; supersede the c1 zero-stub disposition)

- **Status:** Accepted
- **Date:** 2026-06-23
- **Work order:** WO-6 (Maturity)
- **Converge cycle:** c3 (3 of 3 — last allowed)
- **Deciders:** user (relayed via coordinator), pipeline-orchestrator
- **Relates:** ADR 004 (wheel write-path, arm64-only). This ADR extends the same rebuilt-wheel lane.

## Context

WO-6 Phase-7 V&V ruled AC5b PASS by accepting `TelemetryAggregator.stats()` as a read-only Rust
stub returning all-zeros, with the real aggregation deferred to WO-7 (read-side observability).
The user rejected that disposition at the pre-merge gate: the zero-stub is NOT acceptable for WO-6;
`stats()` must return real, correct, non-zero aggregates now.

Verified root cause: `crates/openjarvis-telemetry/src/aggregator.rs` already declares a complete
`AggregateStats` struct (`total_requests, total_tokens, avg_latency, avg_throughput, total_cost,
total_energy`) and the PyO3 binding (`telemetry.rs` PyTelemetryAggregator.stats) already serializes
it to JSON. But the Rust body is literally `Ok(AggregateStats::default())` — it ignores the passed
`_store`. The `telemetry` SQLite table (store.rs) already carries every needed column and is
populated by `record()`. So the gap is exclusively the missing SQL aggregation in `stats()`.

## Decision

Re-open and complete the telemetry-aggregation lane within converge cycle c3:
1. Implement `TelemetryAggregator::stats(store)` in Rust to compute the aggregates via a single SQL
   query (`SELECT COUNT(*), SUM(total_tokens), AVG(latency_seconds), AVG(throughput_tok_per_sec),
   SUM(cost_usd), SUM(energy_joules) FROM telemetry`) and map the row into the existing
   `AggregateStats`. Empty table → zeros, no error.
2. No `AggregateStats` struct change, no telemetry-row schema change, no PyO3 signature change — the
   JSON surface is identical; only the compiled computation changes.
3. Rebuild the arm64 wheel + re-vendor (WO-4 precedent: `source $HOME/.cargo/env` before maturin;
   field-by-field struct construction with `..Default::default()`). arm64-macOS ONLY.
4. Strengthen AC5b: assert REAL numbers (count == N, sums/avgs == expected) on a known-input
   round-trip, not just `count()`.

## Consequences

**Positive**
- AC5b is satisfied on its literal spec wording ("aggregator reports non-zero stats") with correct
  numeric aggregation; no capability deferred to WO-7.
- The change is small and low-risk: one SQL query into an already-defined struct; no schema, no
  binding-shape, no Python-API change.

**Negative / risks**
- A second arm64 wheel rebuild + re-vendor is required (build-env surface; same WO-4 gotchas).
- The aggregator must reach the store's `Connection` (a `Mutex<Connection>` private field); a
  `pub(crate)` accessor or an internal query method may be needed — kept minimal and crate-internal.

**Neutral**
- This is converge cycle 3 of 3 (the cap). If the lane cannot be made green within c3, the
  orchestrator STOPS and RAISES rather than exceeding the cap or fabricating a pass.

## Alternatives considered

- **Keep the c1 zero-stub + defer to WO-7:** rejected by the user.
- **Hand-roll aggregation in Python (core/telemetry.py) over the store rows:** rejected — would
  duplicate logic the Rust crate is the canonical owner of, and `stats()` already exists as the
  contracted surface; the right fix is to make the existing Rust method real.
