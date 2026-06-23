# Quick Start — WO-6 Maturity (manual-test / real-usage path)

> Phase-3 (Design) pipeline artifact, paired with `pipeline/design.md`. This is the
> **Deploy DoD real-usage path**: the exact, copy-pasteable commands to rebuild the
> arm64 wheel, verify the new bindings, launch the app, flip the four WO-6 flags, and
> run the tests. Every command uses an absolute path or a documented `cd` first.
>
> **Architecture is LOCKED (ADR 004):** rebuild the Rust wheel, **arm64 macOS ONLY**,
> binding-exposure-only (no new Rust logic). On Linux/Windows the wheel is absent **by
> design** — the app runs the legacy in-memory/JSON path and needs no build (`design.md:7,316-320`).

---

## 0. Environment facts (read once)

| Fact | Value |
|---|---|
| Repo root | `/Users/ltmas/Repo/agents/mark-xl` |
| venv (python 3.12.9) | `/Users/ltmas/Repo/agents/mark-xl/.venv-mac` — `source .venv-mac/bin/activate` |
| `maturin` 1.14.1 | lives **IN the venv** (`.venv-mac/bin/maturin`), NOT on system PATH |
| `cargo`/`rustc` 1.96.0 | **OFF the default PATH** → `source "$HOME/.cargo/env"` FIRST (WO-4 lesson; `design.md:218,339-340`) |
| Toolchain pin | `rust-toolchain.toml` pins channel **1.88** (minimum); local **1.96.0** satisfies it |
| Wheel build dir (the wheel member) | `/Users/ltmas/Repo/agents/mark-xl/mark-xl-rust-fork/crates/openjarvis-python` |
| App entry | `main.py:1493` `def main()`, guarded `main.py:1540` `if __name__ == "__main__"` |
| WO-6 flags (`config/flags.json`, all default `false`) | `use_scheduler_store`, `use_session_store`, `enable_trace_store`, `enable_telemetry` (`design.md:128`) |
| `WHEEL_AVAILABLE` | set in `core/mark_xl_rust_adapter.py:56-62` (never raises on a missing wheel) |

> **Platform gate:** Steps 1–2 (build + verify bindings) are **arm64-macOS-only**. On Linux
> or Windows skip straight to Step 3 — the app launches and runs legacy; flag-OFF parity is
> byte-identical to `cfcd5db` (`design.md:316-320`).

---

## 1. Build the arm64 wheel (arm64 macOS only)

The build closes the binding gap surfaced in Phase 2: the prebuilt wheel exposes only
READ-only trace/telemetry methods; the rebuild surfaces the existing Rust write path
(`design.md:20-27`). The exact sequence — cargo env FIRST, then the venv, then `maturin
develop` from the wheel-member dir:

```bash
# 1a. cargo/rustc onto PATH (they are OFF the default PATH) — MUST be first.
source "$HOME/.cargo/env"

# 1b. Activate the venv (this is where maturin 1.14.1 lives).
cd /Users/ltmas/Repo/agents/mark-xl
source .venv-mac/bin/activate

# 1c. Build + install the rebuilt bindings straight into .venv-mac
#     (maturin develop installs into the active venv's site-packages —
#      the prior wheel already lives at
#      .venv-mac/lib/python3.12/site-packages/mark_xl_rust/mark_xl_rust.abi3.so;
#      this overwrites it in place).
cd /Users/ltmas/Repo/agents/mark-xl/mark-xl-rust-fork/crates/openjarvis-python
maturin develop --release
```

**Or** produce a distributable wheel artifact (mirrors `ci.yml:107-123` — same
`working-directory`, `manylinux: "off"`, `--out` to repo-root `dist/`), then install it:

```bash
# from the same wheel-member dir, cargo+venv already sourced:
maturin build --release --out ../../../dist          # → /Users/ltmas/Repo/agents/mark-xl/dist/
cd /Users/ltmas/Repo/agents/mark-xl
pip install --force-reinstall --no-index --find-links dist mark_xl_rust
```

**Re-vendor (only when syncing new upstream Rust source — NOT every build).** The
`mark-xl-rust-fork/` tree is a **plain copy, not a git subtree** (`docs/VENDORING.md`;
`design.md:224`). If you pulled new OpenJarvis source, re-vendor with the canonical
`rsync` from `docs/VENDORING.md` *before* rebuilding:

```bash
cd /Users/ltmas/Repo/agents/mark-xl
rsync -a --delete --exclude 'target/' --exclude '.git/' \
  /Users/ltmas/Repo/agents/openjarvis/rust/ ./mark-xl-rust-fork/
# then update the "Vendored from commit" SHA in docs/VENDORING.md and rebuild (Step 1c).
```

> `manylinux: "off"` (CI) means a native host build — N/A for this local arm64 host build.
> CI also enables `sccache` (`ci.yml:107-123`); locally the first cold build of the crate
> graph can take 20–40 min, subsequent builds are fast.

---

## 2. Verify the rebuilt bindings (arm64 macOS only)

Before the rebuild the bindings show only `TraceStore: ['count']` and
`TelemetryStore: ['clear', 'count']` (verified on the live tree — the binding gap).
After Step 1 they MUST expose the write path. One-liner asserting the gap is closed:

```bash
cd /Users/ltmas/Repo/agents/mark-xl
source .venv-mac/bin/activate
python - <<'PY'
import mark_xl_rust as m
ts  = set(dir(m.TraceStore))
tel = set(dir(m.TelemetryStore))
assert {"save", "get", "list_traces"} <= ts,  f"TraceStore missing write path: {sorted(ts)}"
assert "record" in tel,                        f"TelemetryStore missing record: {sorted(tel)}"
print("OK — bindings closed: TraceStore has save/get/list_traces; TelemetryStore has record")
PY
```

Confirm flag-OFF still imports clean and `WHEEL_AVAILABLE` reflects the wheel either way
(present here, `False` on Linux/Windows — both must import without raising,
`core/mark_xl_rust_adapter.py:56-62`):

```bash
python - <<'PY'
from core.mark_xl_rust_adapter import WHEEL_AVAILABLE
from memory.config_manager import get_flag
print("WHEEL_AVAILABLE:", WHEEL_AVAILABLE)              # True on this arm64 mac; False (legacy) elsewhere
for f in ("use_scheduler_store", "use_session_store", "enable_trace_store", "enable_telemetry"):
    print(f, "=", get_flag(f, False))                   # all False by default → baseline
print("OK — flag-OFF imports clean")
PY
```

---

## 3. Run the app

WO-6 adds `mark_xl/__main__.py` and a `[project.scripts] mark-xl = "main:main"` entry in
a NEW root `pyproject.toml`, so **both** launch paths satisfy AC3 (`design.md:130-131,349-356`).
Run from the repo root in the activated venv:

```bash
cd /Users/ltmas/Repo/agents/mark-xl
source .venv-mac/bin/activate

# (a) module form
python -m mark_xl

# (b) console-script form (after `pip install -e .` of the new root pyproject)
pip install -e .          # one-time; registers the mark-xl console script
mark-xl
```

Both dispatch to `main.main` (`main.py:1493`). The legacy `python main.py` still works.

---

## 4. Toggle the four WO-6 flags

All four live in `/Users/ltmas/Repo/agents/mark-xl/config/flags.json`, read via
`get_flag(...)` at `memory/config_manager.py:68`. **All default `false` → flag-OFF ==
`cfcd5db` baseline** (`design.md:128,316-320`). Flip to `true` to exercise a durable store;
flip back to `false` (or run where the wheel is absent) to restore baseline.

```bash
cd /Users/ltmas/Repo/agents/mark-xl
source .venv-mac/bin/activate

# Turn ON the scheduler store (jq edit; back up first):
cp config/flags.json config/flags.json.bak
jq '.use_scheduler_store = true' config/flags.json > config/flags.json.tmp && mv config/flags.json.tmp config/flags.json

# Same pattern for the other three:
jq '.use_session_store  = true' config/flags.json > t && mv t config/flags.json
jq '.enable_trace_store = true' config/flags.json > t && mv t config/flags.json
jq '.enable_telemetry   = true' config/flags.json > t && mv t config/flags.json

# Restore baseline (all four OFF):
jq '.use_scheduler_store=false | .use_session_store=false | .enable_trace_store=false | .enable_telemetry=false' \
   config/flags.json > t && mv t config/flags.json
```

> Note: trace/telemetry stores ALSO require `WHEEL_AVAILABLE` (the rebuilt wheel). Each
> store path is gated on **(flag bool) AND `WHEEL_AVAILABLE`** (`design.md:313`), so on
> Linux/Windows the flags-ON path still falls back to legacy.

### Manual smoke — AC1 scheduler restart-survival (real-usage path)

With `use_scheduler_store` ON, a queued task must survive a process restart
(`design.md:124,390`). The store-backed queue persists via `SchedulerStore`; the legacy
queue is in-memory only (`agent/task_queue.py:210-221`).

```bash
cd /Users/ltmas/Repo/agents/mark-xl
source .venv-mac/bin/activate

# 1) Ensure use_scheduler_store = true (Step 4 above).
# 2) Run the app, queue a scheduled task through the UI, then quit:
python -m mark_xl
#    → enqueue a task, then close the window.

# 3) Restart and confirm the task survived:
python -m mark_xl
#    → the previously-queued task is reloaded from SchedulerStore (PASS = it is present).

# 4) Counter-check baseline: set use_scheduler_store=false, queue a task, restart →
#    the task is GONE (legacy in-memory path), proving flag-OFF restores baseline.
```

---

## 5. Run the tests

`pytest` with **offscreen Qt** (CI sets `QT_QPA_PLATFORM=offscreen` at `ci.yml:38,64,70`;
mouseinfo/pyautogui open an X socket at import, so the env var is mandatory locally too):

```bash
cd /Users/ltmas/Repo/agents/mark-xl
source .venv-mac/bin/activate

# Full suite:
QT_QPA_PLATFORM=offscreen pytest -q

# Just the WO-6 store tests (scheduler/session/trace/telemetry + flag-OFF parity):
QT_QPA_PLATFORM=offscreen pytest -q tests/test_wo6_stores.py
# (use the actual WO-6 test module name(s) once Development lands them, e.g. test_wo6_*.py)
```

**Platform behaviour of the test suite:**
- **Rust-backed store tests** (trace `save`/`get`/`list_traces`, telemetry `record`
  round-trip) run **only on arm64 macOS** — they are gated with a skip marker
  `skipif(not WHEEL_AVAILABLE or not (platform.machine()=='arm64' and sys.platform=='darwin'))`
  and SKIP on Linux/Windows (wheel absent by design) (`design.md:240-248`).
- **flag-OFF byte-parity (AC6)** runs **everywhere** and MUST be green on every lane
  (all four flags OFF; and wheel-absent) (`design.md:249-251,307-320`).
- `macos-13` (x86) is **informational, not required** — Intel runners won't allocate
  (`design.md:252`; `markxl-wo0-ci-env-gaps`).

> **Cross-OS gate caveat:** local V&V is **macOS-only** (`.venv-mac`), so a Windows/Linux
> regression can pass local tests yet fail CI (`markxl-local-vnv-is-macos-only`). The real
> cross-OS gate before merge is **`gh pr checks`**:
>
> ```bash
> gh pr checks            # the authoritative cross-OS green-light before merge
> ```
