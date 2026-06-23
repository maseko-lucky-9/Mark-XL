# Pipeline Constitution — WO-6 (Maturity: unified stores + packaging)

> Derived from the approved Intake Package §1 + the WO-6 section (`plans/cosmic-enchanting-treasure.md`)
> and its post-WO-0/WO-1 verified corrections. Pre-approved by the team-lead ("proceed with WO-6"); running unattended.

## Project
Mark-XL fork (`maseko-lucky-9/Mark-XL`), branch `wo-6-maturity` off `origin/main@cfcd5db` (the merged WO-0..WO-5 stack).
Tier B. Adopt OpenJarvis's bundled-SQLite stores (scheduler/session/trace/telemetry) under the §1 threading contract,
finalize packaging, and make the trace store ready for WO-7 discovery.

## Non-negotiable principles (apply to every WO-6 change)
1. **Thin pure-Python adapters over the wheel** — do NOT adopt OJ Python ToolExecutor/EventBus. WO-6 wraps the PyO3
   store classes (SchedulerStore/SessionStore/TraceStore/TraceCollector/TelemetryStore) via a thread-safe StoreExecutor.
2. **Never call sync/block_on Rust from the Qt main thread.** All store calls go through `core/store_executor.py`
   (`ThreadPoolExecutor(max_workers=1)` + Qt signals), mirroring the proven WO-1 `core/mark_xl_rust_adapter.py`
   pattern. **WAL on all DBs; single writer thread.**
3. **Every change ships behind a feature flag** (flag-OFF == current behavior) with flag-OFF/ON tests. New WO-6 flags
   default OFF. flag-OFF must equal today's behavior.
4. **task-queue loss on restart is acceptable; session continuity is NOT.** Scheduled tasks must survive restart.
5. **Wheel is optional** — every store path must degrade gracefully (legacy in-memory/JSON behavior) when
   `WHEEL_AVAILABLE` is False or the WO-6 flag is OFF. No store path raises just because the wheel is absent.
6. **Tests + CI are the gate.** CI green on the established matrix (macOS-arm64 + Win + Linux × Py3.11–3.13;
   macOS-13/x86 informational, NOT required — per post-WO-0 correction).
7. **Migrations are loss-free + reversible**: write a `.bak` before any JSON→SQLite migration; round-trip test.

## Tech mandates
- Reuse the WO-1 adapter threading pattern for StoreExecutor (do not invent a second mechanism).
- Stores are PyO3-bound + construct hermetically with a single `path` arg (verified): SchedulerStore, SessionStore,
  TraceStore, TraceCollector, TelemetryStore. No Tokio reactor needed (WO-1 carry-forward holds for storage primitives).
- WAL pragma on every SQLite DB; single writer via `max_workers=1`.
- Packaging: create `pyproject.toml` (entry point `mark-xl` / `python -m mark_xl`), add MISSING `LICENSE` (MIT) + `NOTICE`
  (Apache-2.0 attribution for vendored OJ crates); pin `requirements.txt`; `docs/VENDORING.md` already exists — extend.

## Git discipline (mandatory)
- Work only in the fork `~/Repo/agents/mark-xl`. Branch `wo-6-maturity` off `origin/main`. Commit/push to `origin` only.
- PR targets the fork's own `main` (origin) — NEVER `upstream`. Co-Authored-By trailer. Merge is the user's gate.

## Banned / out-of-scope for WO-6
WO-7 skills engine/discovery (only the trace SCHEMA must be ready); DSPy/GEPA/RL; OJ paradigm agents; the Rust *agent*
classes (need a live-engine bridge — WO-6 uses only storage primitives). No new dispatch path (reuse WO-2 registry).

## Definition of Done (WO-6)
Scheduled task survives restart; concurrency test (QTimer + 100 writes) holds ≥30 FPS; WAL enabled on all DBs;
`python -m mark_xl` entry point works; JSON→SQLite migration loss-free (.bak + round-trip); trace store holds 1000+
traces (ready for WO-7); flag-OFF == baseline; CI green on the required matrix.
