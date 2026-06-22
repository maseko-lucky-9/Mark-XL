# Vendoring: `mark-xl-rust-fork/`

The `mark-xl-rust-fork/` directory is a **vendored copy** of the OpenJarvis Rust
workspace (`rust/`), patched for Mark-XL. It is built into the optional
`mark_xl_rust` maturin wheel.

It is a **plain copy**, not a `git subtree`/`submodule`: the upstream OpenJarvis
repo is ~145 MB and we only need the ~1 MB `rust/` workspace. Re-syncability is
preserved by recording the exact source commit + patch set below.

## Source of truth

| Field | Value |
|-------|-------|
| OpenJarvis fork (owned) | `git@github.com:maseko-lucky-9/OpenJarvis.git` (origin) |
| Upstream (never pushed to) | `https://github.com/open-jarvis/OpenJarvis.git` |
| Patch branch | `mark-xl-patches` |
| Vendored from commit | `95a667a5777d3c52e65aeab46dd694297e39a7ea` |
| OpenJarvis `main` base (pre-patch) | `d4eb6308b1d5d4f0d270ea659740f7a19584dfc6` |
| Copied subtree | `rust/` (excluding `target/`, `.git/`) |
| Vendored on | 2026-06-21 |
| Toolchain | Rust 1.88 (pinned via `rust-toolchain.toml`) |

## Fork-patches applied (on `mark-xl-patches`, before vendoring)

1. **GIL release** — `crates/openjarvis-python/src/agents.rs`
   All 5 agent `run()` sites (`PySimpleAgent`, `PyOrchestratorAgent`,
   `PyNativeReActAgent`, `PyNativeOpenHandsAgent`, `PyMonitorOperativeAgent`)
   now take `py: Python<'_>` and wrap the synchronous
   `RUNTIME.block_on(self.inner.run(...))` in `py.allow_threads(|| ...)`, so a
   calling host UI thread is never frozen while Rust drives the agent. The
   closure is `Ungil`/`Send` (`OjAgent: Send + Sync`; no `PyObject` captured).
   Closure form (`Python::allow_threads`) is used rather than the version-gated
   `#[pyo3(allow_threads)]` attribute, for pyo3-0.23 compatibility.

2. **Module rename** — `openjarvis_rust` -> `mark_xl_rust`:
   - `crates/openjarvis-python/src/lib.rs` — `#[pymodule] fn mark_xl_rust`
   - `crates/openjarvis-python/Cargo.toml` — `[lib] name = "mark_xl_rust"`
   - `crates/openjarvis-python/pyproject.toml` — project `name = "mark-xl-rust"`

3. **abi3** — `Cargo.toml` (workspace) adds `abi3-py311` to the (binding-only)
   `pyo3` features; `crates/openjarvis-python/pyproject.toml` sets
   `requires-python = ">=3.11"`. One wheel per platform, Python 3.11+.

4. **Test probe** — `crates/openjarvis-python/src/lib.rs`
   Adds a test-only `#[pyfunction] _gil_probe(millis)` that mirrors the exact
   `py.allow_threads(|| RUNTIME.block_on(...))` pattern with no network, so the
   AC5 no-freeze test is hermetic. (Rust agent constructors panic outside a
   running Tokio reactor — `get_engine_static`/rig-core — so real agent `run()`
   is not reachable from Python without a live engine; the probe proves the
   identical mechanism the 5 `run()` sites share.)

## Re-sync procedure (quarterly or on upstream security fix)

```bash
# 1. In the OpenJarvis fork, refresh the patch branch off upstream:
cd ~/Repo/agents/openjarvis
git fetch upstream && git checkout mark-xl-patches
git rebase upstream/main            # re-apply the 3 patches on new upstream
# (resolve conflicts in agents.rs / lib.rs / Cargo.toml / pyproject.toml)
cargo check -p openjarvis-python    # must pass on Rust 1.88 before vendoring
git commit && git push origin mark-xl-patches

# 2. Re-vendor into Mark-XL (plain copy, excluding build + git):
cd ~/Repo/agents/mark-xl
rsync -a --delete --exclude 'target/' --exclude '.git/' \
  ~/Repo/agents/openjarvis/rust/ ./mark-xl-rust-fork/

# 3. Update the "Vendored from commit" SHA above, rebuild the wheel, run tests.
```

## Build

```bash
cd ~/Repo/agents/mark-xl/mark-xl-rust-fork/crates/openjarvis-python
source "$HOME/.cargo/env"
../../../.venv-mac/bin/maturin develop --release   # or `maturin build --release`
python -c "import mark_xl_rust; print(mark_xl_rust.__name__)"
```

## License

OpenJarvis is Apache-2.0. The vendored tree carries its license; Mark-XL ships a
`NOTICE` and an MIT `LICENSE` (see WO-6 packaging). Apache-2.0 -> MIT is compatible.
