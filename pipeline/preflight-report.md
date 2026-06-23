# Pre-Flight Report — WO-6 (Phase 3 Design gate)

> Phase-3 Pre-Flight security/risk/scope gate. Branch `wo-6-maturity` (base `origin/main@cfcd5db`).
> Run by the design-lead; secrets scan + tooling delegated to `security-auditor`. Date 2026-06-23.
> Contract fields (for the YAML handoff): `secrets_status`, `destructive_ops`, `risk_tier`, `scope_status`.

## Gate verdict (summary)

| Field | Value |
|-------|-------|
| `secrets_status` | **clean** (HARD-FAIL gate PASSED) |
| `destructive_ops` | enumerated (all have safety nets — see section 3) |
| `risk_tier` | **medium** (highest individual: R4 allowlist=HIGH, mitigated; R1 critical=RESOLVED by the rebuild decision) |
| `scope_status` | **within** (no AC drift beyond converged spec v2) |
| banned-stack | **PASS** (one justified SQLite exemption — see section 2) |
| Overall | **PASS** — proceed to Development |

## 1. Secrets scan (HARD FAIL gate)

- **Tool:** gitleaks (installed; used directly). Command: `gitleaks detect --no-banner --redact -v` over `/Users/ltmas/Repo/agents/mark-xl`.
- **Scope:** 31 commits, ~2.47 MB working tree. A fallback high-signal grep (OpenAI-style key prefixes, AWS access-key prefixes, PEM private-key headers, and `api_key`/`token`/`password` assignment forms) was run over tracked `.py`/`.json`/`.toml`/`.md`, excluding `.venv-mac/`, `mark-xl-rust-fork/target/`, `.git/`, `dist/`.
- **Findings:** 0 real secrets. 0 grep matches.
- **Context:** WO-3 ships SYNTHETIC test keys assembled at runtime (harness memory `wo3-shipped-security-gates`) — none present as string literals; no test fixture triggered a hit, consistent with the WO-3 design.
- **`secrets_status = clean`.** Gate PASSED. (Per the role contract, a `dirty` verdict would be a HARD FAIL → `status: failed`, STOP.)

## 2. Banned-stack check

Per `~/.claude/reference/anti-patterns.md` and `pipeline/constitution.md` section "Banned / out-of-scope".

| Banned item | Present in WO-6 design? | Verdict |
|-------------|------------------------|---------|
| WordPress / PHP / Ruby / Laravel | No | PASS |
| jQuery | No (PyQt6 desktop app) | PASS |
| DSPy / GEPA / RL | No (explicitly Out of Scope, spec:131) | PASS |
| OJ paradigm agents (Python ToolExecutor/EventBus) | No (constitution principle 1: thin pure-Python adapters over the wheel only) | PASS |
| Second threading mechanism | No (`core/store_executor.py` reuses the WO-1 ThreadPoolExecutor + Qt-signal pattern; max_workers=1) | PASS |
| New dispatch path | No (reuses WO-2 tool registry; spec:122,133) | PASS |
| Rust *agent* classes | No (only hermetic single-`path` storage primitives) | PASS |
| Multi-OS wheels | No (arm64 macOS ONLY, per ADR 004) | PASS |
| **SQLite "for production"** | Yes — the four PyO3 stores are SQLite-backed | **PASS (justified exemption)** |

**SQLite exemption rationale:** `anti-patterns.md` bans "SQLite for production" in the context of multi-tenant network services. Mark-XL is a single-user PyQt6 desktop assistant; SQLite is the correct embedded-store choice (it is the vendored Rust stores' own backing, and WO-3 audit + WO-4 memory already ship SQLite on this app). The WAL + single-writer design (NFR-1/NFR-2) addresses the only real SQLite-concurrency hazard. Not a violation.

## 3. Destructive-ops enumeration

| Operation | Type | Safety net |
|-----------|------|-----------|
| `config/flags.json` +4 flags | **additive** | new keys only; flag-OFF default → byte-identical (NFR-3) |
| JSON→SQLite session migration (FR-5) | **destructive** (writes new DB, reads source) | `.bak` written BEFORE any write; loss-free + round-trippable (NFR-5). **Carry-forward: must be crash-safe** — `shutil.copy2` + fsync the `.bak` before opening the SQLite write; resume-from-`.bak` if it already exists (Pre-Flight LOW finding) |
| New SQLite DB files (scheduler/session/trace/telemetry) | **additive** | flag-OFF/wheel-absent → no DB created (NFR-4) |
| Rust wheel rebuild + re-vendor | **destructive** (overwrites vendored binding artifact) | prior wheel importable until replaced; maturin installs atomically; arm64-only |
| `pyproject.toml` / `LICENSE` / `NOTICE` creation | **additive** | greenfield — no prior file at repo root (verified) |
| `requirements.txt` pinning (FR-15) | **destructive** (overwrites unpinned file) | git history is recovery path. **Carry-forward: `cp requirements.txt requirements.txt.pre-pin.bak` (or note pre-pin SHA) before overwrite** (Pre-Flight LOW finding) |
| `docs/VENDORING.md` extend (FR-16) | **additive** | existing 84 lines untouched; file EXISTS (verified) |

## 4. Tooling availability (wheel-rebuild lane feasibility)

| Tool | Present | Version | Note |
|------|---------|---------|------|
| Python (`.venv-mac`) | YES | 3.12.9 | activate before any python/pytest |
| `mark_xl_rust` wheel | YES | importable | all 5 store classes present |
| maturin | YES (venv only) | 1.14.1 | NOT on system PATH — `source .venv-mac/bin/activate` first |
| cargo | YES | 1.96.0 | OFF default PATH — `source $HOME/.cargo/env` first (WO-4 lesson) |
| rustc | YES | 1.96.0 | satisfies `rust-toolchain.toml` channel 1.88 (min) |

**Binding gap re-confirmed live this session:** `TraceStore`=`['count']`, `TelemetryStore`=`['clear','count']`, `TraceCollector`=`['active_count']` — `save`/`get`/`list_traces`/`record` ABSENT today. The rebuild is feasible (toolchain present once venv + cargo-env are sourced).

**CI carry-forward:** the arm64 wheel-build step MUST source BOTH `$HOME/.cargo/env` AND `.venv-mac` before `maturin` (else "command not found").

## 5. Risk register (carried from analysis.md section 6)

| ID | Risk | Tier | Mitigation (design decision) |
|----|------|------|------------------------------|
| R1 | trace/telemetry no Python write path | CRITICAL | **RESOLVED** by ADR 004 (rebuild exposes existing Rust `save`/`record`) |
| R2 | TelemetrySample hardware-only | HIGH | folds into R1; the new `PyTelemetryRecord` is the persistence row, NOT `PyTelemetrySample` |
| R3 | TraceCollector ctor takes a store not a path | LOW | wired `TraceStore(path)` then `TraceCollector(store)`; real API `start_trace`/`add_step`/`end_trace` |
| R4 | FR-18 substring-scan + exact-set equality | **HIGH** | **MITIGATED** — pass flag bools INTO new modules; no `get_flag` substring in new files → allowlist stays `{main.py, agent/executor.py}`; `test_flags.py` UNCHANGED |
| R5 | WAL ownership ambiguous (DB opened in Rust) | MEDIUM | Python runs `PRAGMA journal_mode=WAL` on the owned path before Rust ctor; AC2 asserts |
| R6 | migration source undefined | MEDIUM | confirmed flat-JSON FACT memory has no message log → first migration is a documented no-op satisfying `.bak`+round-trip |
| R7 | local V&V macOS-only → Win/Linux regressions slip | MEDIUM | `gh pr checks` is the gate (NFR-7/AC7); `.as_posix()` all path strings |
| R8 | flag-OFF byte-drift via import side-effects | MEDIUM | no module-level store construction; gate on flag AND WHEEL_AVAILABLE; AC6 == baseline |
| R9 | single-writer contention/deadlock | MEDIUM | `max_workers=1` serialises; AC2 (QTimer+100 writes >=30 FPS) guards |
| PF-1 | maturin/cargo off PATH in CI | MEDIUM | source `$HOME/.cargo/env` + `.venv-mac` in the wheel build step |
| PF-2 | `.bak` write not atomic | LOW | fsync after copy; resume-from-`.bak` on re-entry |

`risk_tier = medium`. The only CRITICAL (R1) is RESOLVED by the locked decision; the two HIGH (R2/R4) have recorded mitigations in `design.md` section 9. No unmitigated high/critical remains.

## 6. Scope confirmation

`scope_status = within`. The converged spec v2 (ADR 004) restores the FULL WO-6 surface — scheduler + session + trace + telemetry + packaging — with **all four flags** (`enable_telemetry` retained, NOT dropped) and AC5b added. The design implements exactly that surface; no AC is added, removed, or weakened beyond the converged spec. (Note: `analysis.md` section 9's interim 3-flag re-scope is SUPERSEDED by ADR 004 / spec v2 — the design correctly follows spec v2, the source of truth.) No off-rails RAISE required.

## 7. Git baseline

- Branch `wo-6-maturity`; base `cfcd5db` (WO-0..WO-5 merged to fork main). Working tree has pipeline phase artifacts only — no production code changes yet. Correct entry state for Development.
