# Pipeline Constitution — WO-0 (Foundations)

> Derived from the approved Intake Package §1 (`plans/cosmic-enchanting-treasure.md`).
> Governs the WO-0 build only. Pre-approved by the team-lead dispatch (running unattended).

## Project
Mark-XL fork (`maseko-lucky-9/Mark-XL`) — pure-Python local voice assistant. WO-0 lays the
verification foundation (CI, pytest, feature flags) and fixes live dispatch bugs + heterogeneous
tool signatures. Pure-Python only; the Rust wheel (WO-1) is a CI placeholder here.

## Non-negotiable principles (apply to every change in WO-0)
1. **Thin pure-Python only.** Do NOT adopt OpenJarvis `ToolExecutor`/`EventBus`. No OJ imports in WO-0.
2. **Every future capability ships behind a feature flag** (flag-OFF == current behavior) with
   flag-OFF/ON tests. WO-0 *builds* the flag system (all gates default OFF) but gates **nothing** —
   the bug fixes + signature normalization are unconditional corrections, not flagged.
3. **Tests + CI are the gate.** No change is "done" without green CI.
4. **Never break the legacy path.** flag-OFF must equal today's behavior, byte-for-byte semantics.
5. **abi3 decision recorded here** (for WO-1): build with `abi3=true`, one wheel/platform, Py3.11+.
   `rlimit` is Unix-only (gate any future use). The Rust wheel-build CI job is a placeholder stub.

## Tech mandates
- Python 3.11–3.13; pytest harness with a **real** `QApplication`/`QTest` (UI-thread tests need it,
  not mocks) + an Ollama mock + thread fixtures.
- CI: GitHub Actions matrix — macOS `macos-14` (arm64) + `macos-13` (x86), Windows, Linux × Py 3.11/3.12/3.13.
- Rust toolchain pinned for the placeholder: `dtolnay/rust-toolchain@1.88`, maturin, sccache (WO-1 activates).

## Git discipline (mandatory)
- Work only in the fork `~/Repo/agents/mark-xl`. Branch `wo-0-foundations` off `origin/main`.
- Commit/push to `origin` only. PR targets the fork's own `main` (origin) — NEVER `upstream`.
- Co-Authored-By trailer on commits. Nothing leaves the fork.

## Banned / out-of-scope for WO-0
Anything beyond foundations: the Rust wheel build itself, registry refactor (WO-2), security gates
(WO-3), memory (WO-4), loopguard (WO-5). DSPy/GEPA/RL and OJ paradigm agents are banned globally.

## Definition of Done (WO-0)
CI green on the full matrix (Rust wheel job = placeholder stub, non-blocking); `import mark_xl_rust`
smoke job present (allowed to skip when wheel absent); all 17 action tools dispatch through BOTH
`main._execute_tool` and `executor._call_tool` in parametrized tests; 4 live bugs fixed with
regression tests; flag-OFF == baseline.
