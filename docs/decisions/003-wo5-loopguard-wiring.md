# 003 — Single global LoopGuard: separate policy module, inline+locked, depth-knob dropped, signal-marshalled

- **Status:** Accepted
- **Date:** 2026-06-22
- **Work order:** WO-5 (LoopGuard across the 3 dispatch sites + task_queue)
- **Deciders:** Design-phase lead (Phase 3), `planner`, `security-auditor`

## Context and forces

WO-5 must detect degenerate tool loops (A→B→A→B or repeated identical calls) across THREE dispatch
sites — `main.JarvisApp._execute_tool` (main.py:815), `agent.executor._call_tool` (executor.py:192),
and the `task_queue` worker path (task_queue.py:142 → `executor.execute` → `_call_tool`) — behind the
`enable_loopguard` flag (default OFF), with per-query reset, and flag-OFF byte-identical to the
129/0 wo-3-security baseline. Live `.venv-mac` wheel introspection (Analyze, `pipeline/wo-5/analysis.md`)
resolved the forces:

1. **`LoopGuard.check(tool_name, arguments) → Optional[str]`** (None=ok, reason str=loop); does NOT
   raise; trips on the 2nd identical `(name, canonical-args)`. 2nd arg MUST be a string (raw dict raises
   `TypeError`) → serialize via `core/security_gate.py:24 _canonical_json` (I-6 / I-1d).
2. **Constructor accepts EXACTLY `LoopGuard(max_identical=50, max_ping_pong=4, poll_budget=100)`** —
   `loop_guard_max_depth` / `max_depth` / `ping_pong` are ALL rejected (OQ-7). No fork-patch.
3. **`.check`/`.reset` are hermetic + non-blocking** — no Tokio reactor, 0.302 µs / 0.034 µs per call
   (I-3). But the task_queue worker runs on a fresh daemon thread (task_queue.py:159) concurrently with
   the Qt main thread (NFR-2).
4. **`get_flag` 2-copy allowlist** pinned EXACT-SET `{main.py, agent/executor.py}` (test_flags.py:139,
   test_wo2_registry.py:434) — no new caller (FR-9 / CP-4).
5. **A worker-thread detection must reach the UI** without touching Qt from the worker thread (NFR-3).
6. **The WO-1 adapter** (`core/mark_xl_rust_adapter.py:get_loop_guard`, adapter.py:109) is a stateless
   one-line passthrough and is frozen; the WO-3 `security_gate.run_gated` 7-gate wrapper must not regress
   (CP-5).

## Decision

Four load-bearing decisions, recorded together because they are one coherent wiring:

**(A) A new self-contained policy module `core/loop_guard.py`, NOT an extension of the adapter.**
The adapter is a stateless passthrough; the singleton + lock + `_canonical_json` serialization + 3-knob
config reads + the signal carrier are WO-5 POLICY (mirroring how WO-3's `core/security_gate.py` is a
separate policy module that *consumes* the adapter). `core/loop_guard.py` holds the ONE shared
`mark_xl_rust.LoopGuard` (lazy module-level singleton, double-checked under a lock), constructs it via
`adapter.get_loop_guard(max_identical=, max_ping_pong=, poll_budget=)`, and exposes
`get_guard / check / reset / load_knobs / get_bridge / signal_loop`. `check()` serializes the params
dict via `_canonical_json` INTERNALLY (single chokepoint — call sites pass a raw dict and cannot forget).
The module contains **zero** `get_flag` literals — the resolved `enable_loopguard` bool is passed in.

**(B) Inline check/reset under a `threading.Lock`, NOT a `ThreadPoolExecutor`.**
`.check`/`.reset` are hermetic and ~0.3 µs (I-3) — off-threading a 0.3 µs call would ADD latency and a
freeze surface, not remove one. A single `_GUARD_LOCK` wraps construct + every check + every reset so the
one shared instance is safe under the worker-thread vs. Qt-main-thread race; `max_concurrent=1`
(task_queue.py:38) bounds worker concurrency.

**(C) DROP `loop_guard_max_depth` as a LoopGuard knob.**
No wheel constructor kwarg exists for it (OQ-7), and tool-call depth is ALREADY capped by
`MAX_TOOL_ROUNDS=6` (main.py:1047) and `attempt<=3` (executor.py:382). Canonical knob set = the 3
numerics (max_identical=50, max_ping_pong=4, poll_budget=100), read via
`memory.config_manager.get_security_config` — NOT new boolean `flags.json` / `_FLAG_SCHEMA` entries
(CP-6 / FR-10), and read ONLY on the flag-ON path.

**(D) `loop_detected` is marshalled to the UI thread via a `QObject` `pyqtSignal(str)` — always-emit.**
`LoopGuardBridge(QObject)` carries `loop_detected = pyqtSignal(str)` (the reason string), constructed on
the Qt main thread so its connections marshal to the UI thread (the proven `RustResultBridge` pattern,
adapter.py:226). Because `_call_tool` serves both the main-thread and worker-thread paths, `signal_loop`
always emits and lets Qt auto-select the connection type (direct on the main thread, `QueuedConnection`
cross-thread) — no thread-detection branching in the shared dispatch path. A detected loop short-circuits
the dispatch (returns/sets a `"Loop detected: <reason>"` string BEFORE `run_gated`/`dispatch`), surfacing
the loop without ever raising into the UI.

The LoopGuard check is inserted FIRST/EARLY at each site, gated on `enable_loopguard` only and
INDEPENDENT of `enable_security_gates` (it precedes the `sec_on` branch, never lives inside `run_gated` —
CP-1 / FR-5 / FR-6). Per-query `.reset()` is called at two boundaries on the SAME shared guard:
`main._process_message` (main.py:1016, interactive) and `executor.execute` (executor.py:330, background).
Site #3 (task_queue) has NO separate insertion — it rides site #2's `_call_tool` check on the worker
thread, so `task_queue.py` stays at ZERO `get_flag` (I-5) and the allowlist stays at 2 (FR-9 / AC-7).

## Consequences

**Positive**
- One shared guard across all 3 sites with a single serialization chokepoint; cross-site loops detected.
- No fork-patch, no new wheel build — the 3 real knobs pass straight through the adapter (OQ-7).
- Flag-OFF stays byte-identical: lazy import only inside the flag-ON branch, knobs never read when OFF,
  flag-OFF ladders (main.py:926, executor.py:241) untouched, no schema change (`enable_loopguard` already
  in `_FLAG_SCHEMA`, config_manager.py:64), allowlist unchanged at 2 (NFR-1 / AC-6).
- Worker-thread detections reach the UI safely via the queued signal; the inline µs check keeps the main
  thread responsive (NFR-2 / NFR-3); `_gil_probe`-style no-freeze assertion proves it (AC-5).
- The WO-1 adapter and the WO-3 7-gate wrapper are untouched (read-only `_canonical_json` reuse) — CP-5.

**Negative / trade-offs**
- `core/loop_guard.py` adds a module rather than living in the adapter — accepted to keep the adapter a
  thin stateless passthrough and to isolate WO-5 policy + tests.
- `loop_guard_max_depth` named in the original plan/CP-6 is intentionally NOT honored as a knob (D/C) —
  recorded here so the omission is not later read as a gap; depth is covered by the existing loop caps.
- A single `_GUARD_LOCK` serializes all check/reset calls — negligible at µs cost, accepted for the
  thread-safety guarantee under `max_concurrent=1`.

## Alternatives rejected

- **Extend `mark_xl_rust_adapter.get_loop_guard` to hold the singleton + policy** — would bloat the
  single integration point with WO-5 state and risk regressing WO-1's adapter tests. Rejected for a
  separate policy module (mirrors WO-3).
- **Route `.check`/`.reset` off-thread via `ThreadPoolExecutor`** — unnecessary: the calls are hermetic
  and non-blocking (I-3); off-threading adds latency/complexity. Rejected for inline + lock.
- **Fork-patch the Rust crate to add `max_depth` / `loop_guard_max_depth`** — banned by the constitution;
  WO-1 frozen; and depth is already bounded by `MAX_TOOL_ROUNDS=6` + `attempt<=3`. Rejected.
- **Bury the check inside `core/security_gate.run_gated`** — it would only run when
  `enable_security_gates` is ON, violating CP-1 / FR-5. Rejected for a first/early check at each site,
  independent of the security flag.
- **Emit `loop_detected` by calling a UI slot directly from the worker thread** — undefined behaviour /
  crash on a Qt widget touched off the main thread. Rejected for the queued `pyqtSignal` marshalling.
- **Add a 3rd `get_flag` caller (task_queue.py)** — would trip the 2-copy allowlist tests by design.
  Rejected for the caller-resolved-bool path; task_queue rides `executor._call_tool` (already allowlisted).
