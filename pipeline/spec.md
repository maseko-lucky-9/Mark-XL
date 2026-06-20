# Spec — WO-0 (Foundations)

> Spec Kit `specify` artifact for WO-0 of the Mark-XL fork (`maseko-lucky-9/Mark-XL`,
> branch `wo-0-foundations` off `origin/main fb46b5e`). This is the **What**, not the **How** —
> design/implementation choices belong to Phase 3+ (Design) and the per-task development phase.
> All facts below are corroborated against the live code on `wo-0-foundations` (file:line cited);
> the digest count of "17 action tools" was re-verified (`ls actions/*.py` → 17 files).

---

## 1. User Scenarios

WO-0 has no end-user-facing feature; its "users" are the downstream Work Orders (WO-1..WO-5) and
the CI gate that must trust the dispatch layer. The scenarios are the verification foundation they
each depend on.

- **US-1 — Maintainer runs the suite.** As a Mark-XL maintainer, I run `pytest` locally (and CI runs
  it on every push) and get a deterministic green/red signal for the dispatch layer, so no downstream
  change is merged on top of a broken foundation.
- **US-2 — Downstream WO author trusts dispatch.** As the author of WO-1..WO-5, I rely on every action
  tool being reachable through **both** dispatch paths (`main._execute_tool` and
  `executor._call_tool`) with `player` and `speak` forwarded, so I can extend behaviour behind a flag
  without re-auditing the legacy routing.
- **US-3 — Voice user keeps current behaviour.** As an end user of the assistant, my existing
  interactions behave exactly as they do today: every WO-0 change is either a bug correction or an
  inert (default-OFF) flag schema; nothing changes the flag-OFF (baseline) behaviour.
- **US-4 — Future capability author has a flag rail.** As the author of a future gated capability, I
  call `get_flag(name)` against a schema that already exists (all defaults OFF), so I can ship behind a
  flag from WO-1 onward without building the flag system first.
- **US-5 — Release engineer sees the Rust seam.** As the release engineer, I see a non-blocking Rust
  wheel-build placeholder and an `import mark_xl_rust` smoke job that skips cleanly when the wheel is
  absent, so the CI matrix is green today and ready for WO-1 to activate the real build.

---

## 2. Functional Requirements (FR)

### FR-1 — Fix the 4 live dispatch bugs (unconditional corrections; NOT flagged)

- **FR-1.1 (B1) — `main.py:896` `flight_finder` drops `speak`.** Live: `flight_finder(parameters=args,
  player=self.ui)` (main.py:896); `actions/flight_finder.py:284` accepts `speak=None`. Required outcome:
  the `main`-path dispatch of `flight_finder` forwards the active `speak` callable, so `flight_finder`
  receives `speak` from both dispatch paths. (`executor._call_tool` already forwards `speak` at
  executor.py:241 — corroborated.)
- **FR-1.2 (B2) — `executor._call_tool` is missing `file_processor`.** Required outcome: `file_processor`
  is dispatchable through `executor._call_tool`, and the executor tool set is reconciled to the full
  17-tool action set (the canonical enumeration in §6), each call forwarding `player` and `speak`.
- **FR-1.3 (B3) — `executor.py:243-245` silent generated-code fallback for unknown tools.** Live: the
  `else` branch prints a warning and calls `_run_generated_code(...)` for any unknown tool. Required
  outcome: an unknown tool **raises** (e.g. `ValueError`/`KeyError`) instead of silently falling back to
  generated code; the silent fallback is removed.
- **FR-1.4 (B4) — `planner.py` `PLANNER_PROMPT` lists 16 tools, missing `file_processor` (and
  `agent_task`).** Live: a `grep` for `file_processor`/`agent_task` in `planner.py` returns no match.
  Required outcome: the planner-visible tool list includes the missing tools so the planner can emit
  steps for them. (WO-2 will auto-generate this list; WO-0 only corrects it by hand.)

### FR-2 — Normalize the 17 action tool signatures

- **FR-2.1.** All 17 functions in `actions/*.py` (the canonical set in §6) expose the uniform signature
  `(parameters, player=None, speak=None, **kwargs) -> str`. 10 currently lack `speak`; 7 already have it
  (code_helper, dev_agent, screen_process[screen_processor], youtube_video, file_processor,
  flight_finder, game_updater).
- **FR-2.2.** The dead `session_memory` and dead `response` parameters are dropped from the signatures;
  `**kwargs` absorbs them from any un-updated caller (so no caller breaks during the transition).
- **FR-2.3.** All callers in `main.py` (`_execute_tool`) and `agent/executor.py` (`_call_tool`) are
  updated in the same pass to forward `player` and `speak` and to match the normalized signatures.

### FR-3 — Feature-flag system (builds the rail; gates nothing in WO-0)

- **FR-3.1.** `memory/config_manager.py` is extended with a flag **schema** and a `get_flag(name)`
  accessor.
- **FR-3.2.** Every flag defaults **OFF**. WO-0 wraps **no** behaviour in a flag — the bug fixes and
  signature normalization are unconditional corrections, not gated changes.

### FR-4 — pytest harness from zero

- **FR-4.1.** A `conftest.py` plus fixtures provide a **real** `QApplication`/`QTest` (UI-thread tests
  need it, not mocks), an Ollama mock, and thread fixtures.
- **FR-4.2.** A parametrized test dispatches **every** one of the 17 action tools (§6) through **both**
  `main._execute_tool` and `executor._call_tool`, with mock `player` and `speak`. The assertion is a
  **routing/wiring assertion** — patch each `actions.X` entry point with a mock and assert the dispatcher
  forwards `player` and `speak` — **not** live side-effect execution.
- **FR-4.3.** The 3 inline (main-only) tools — `save_memory`, `agent_task`, `shutdown_jarvis` — get
  individual main-only tests. `shutdown_jarvis` (spawns a daemon thread → `os._exit(0)`) **must be
  mocked** so the test process is not killed.
- **FR-4.4.** Each of the 4 bug fixes (FR-1.1..FR-1.4) has a dedicated regression test that fails on the
  pre-fix code and passes on the fixed code.

### FR-5 — CI (GitHub Actions)

- **FR-5.1.** A test matrix runs on macOS `macos-14` (arm64) + `macos-13` (x86), Windows, and Linux ×
  Python 3.11 / 3.12 / 3.13, and must be green on every leg.
- **FR-5.2.** A Rust wheel-build job is present as a **non-blocking placeholder stub** (WO-1 produces
  `mark_xl_rust`); it does not block WO-0. Rust toolchain is pinned for the placeholder
  (`dtolnay/rust-toolchain@1.88`, maturin, sccache) for WO-1 to activate.
- **FR-5.3.** An `import mark_xl_rust` smoke job is present and **skips cleanly** when the wheel is
  absent.

---

## 3. Non-Functional Requirements (NFR)

- **NFR-1 — Pure-Python only.** No Rust runtime dependency in WO-0; the wheel is a CI placeholder.
- **NFR-2 — No OpenJarvis imports.** Do NOT adopt OJ `ToolExecutor`/`EventBus`; **no OJ imports** in any
  WO-0 file (constitution §1).
- **NFR-3 — flag-OFF == baseline, byte-for-byte semantics.** With all flags OFF (the WO-0 default state),
  observable behaviour equals today's behaviour. This is trivially met because WO-0 gates nothing; the
  requirement still binds any code added.
- **NFR-4 — Unix-only `rlimit` gated.** Any use of `rlimit` is Unix-only and must be gated so Windows
  matrix legs do not fail on its absence (recorded for WO-1; WO-0 introduces no ungated `rlimit` use).
- **NFR-5 — abi3 decision recorded.** WO-1 builds the wheel with `abi3=true`, one wheel/platform, Py3.11+
  (recorded here for the Rust placeholder; not built in WO-0).
- **NFR-6 — Python 3.11–3.13 compatibility.** All WO-0 Python runs on the full 3.11/3.12/3.13 range.
- **NFR-7 — Fork-only git discipline.** Work only in the fork `~/Repo/agents/mark-xl`; branch
  `wo-0-foundations` off `origin/main`; commit/push to `origin` only; PRs target the fork's own `main`,
  **never** `upstream`; Co-Authored-By trailer on commits.
- **NFR-8 — Deterministic, isolated tests.** Tests must not kill the runner (`shutdown_jarvis` mocked),
  must not perform live side effects in the dual-path routing assertion, and must be deterministic across
  matrix legs.

---

## 4. Edge Cases

- **EC-1 — `rlimit` absent on Windows.** Windows matrix legs lack `resource.setrlimit`; any rlimit-gated
  path must be skipped/guarded so those legs stay green.
- **EC-2 — Rust wheel absent.** The `import mark_xl_rust` smoke job runs with no wheel present and must
  **skip** (not fail). The wheel-build placeholder must not block the matrix.
- **EC-3 — `shutdown_jarvis` self-termination.** `shutdown_jarvis` spawns a daemon thread that calls
  `os._exit(0)`; its test must mock the exit so the pytest process survives.
- **EC-4 — Dead params on un-updated callers.** After signatures drop `session_memory`/`response`, any
  caller still passing them must not break — `**kwargs` absorbs the extras.
- **EC-5 — Unknown tool now raises (behaviour change is intentional, not a regression).** Post-FR-1.3,
  `executor._call_tool` with an unrecognized tool raises rather than silently generating code. The
  regression test for B3 asserts the raise. (This is the single intended behavioural change vs. baseline;
  it is a bug *correction*, not a flagged feature.)
- **EC-6 — `screen_process` vs `screen_processor` naming.** The action file is `screen_processor.py`; the
  tool name dispatched is `screen_process`. The enumeration in §6 is the authoritative tool-name list.
- **EC-7 — Inline tools cannot dispatch through both paths.** `save_memory`, `agent_task`,
  `shutdown_jarvis` are main-only (absent from `executor._call_tool`); they are excluded from the
  dual-path parametrization and tested individually (FR-4.3). Asserting them through both paths would be
  a false requirement.

---

## 5. Testing Acceptance Scenarios (Given / When / Then)

- **TAS-1 — Dual-path routing for all 17 action tools.**
  *Given* each tool `X` in the §6 dual-path set with its `actions.X` entry point patched by a mock,
  *when* the tool is dispatched through `main._execute_tool` **and** through `executor._call_tool` with a
  mock `player` and a mock `speak`, *then* the patched entry point is invoked on **both** paths with
  `player` and `speak` forwarded. (Routing/wiring assertion — no live side effects.)
- **TAS-2 — B1 regression (flight_finder speak).** *Given* the `main`-path dispatch of `flight_finder`,
  *when* a tracked `speak` is supplied, *then* `flight_finder` receives that `speak` (fails on pre-fix
  code where `speak` is dropped at main.py:896).
- **TAS-3 — B2 regression (executor file_processor).** *Given* `executor._call_tool`, *when* called with
  `file_processor`, *then* it dispatches to `actions.file_processor` (fails on pre-fix code where the
  branch is missing).
- **TAS-4 — B3 regression (unknown tool raises).** *Given* `executor._call_tool`, *when* called with an
  unknown tool name, *then* it raises (fails on pre-fix code that silently calls `_run_generated_code`).
- **TAS-5 — B4 regression (planner lists missing tools).** *Given* `PLANNER_PROMPT`, *when* inspected,
  *then* it includes `file_processor` (and `agent_task`) (fails on pre-fix code that omits them).
- **TAS-6 — Inline tools (main-only).** *Given* each of `save_memory`, `agent_task`, `shutdown_jarvis`,
  *when* dispatched through `main._execute_tool` (with `shutdown_jarvis`'s `os._exit` mocked), *then* the
  expected inline handler runs without killing the test process.
- **TAS-7 — Flag system default-OFF.** *Given* the flag schema, *when* `get_flag(name)` is read with no
  override, *then* every flag returns OFF, and no WO-0 fix/normalization is wrapped in a flag.
- **TAS-8 — CI matrix + Rust placeholder.** *Given* the GitHub Actions workflow, *when* CI runs, *then*
  every matrix leg (macos-14, macos-13, Windows, Linux × Py3.11/3.12/3.13) is green, the Rust wheel job is
  present and non-blocking, and the `import mark_xl_rust` smoke job skips cleanly when the wheel is absent.

---

## 6. Canonical Tool Enumeration (source of truth for the parametrized test)

> Copied **verbatim** from `pipeline/intake.md:40-44`. This is the test's source of truth; do not
> paraphrase or re-derive (the "18 tools" figure is a confirmed miscount — see Lessons Applied).

**17 dual-path action tools:** open_app, web_search, weather_report, send_message, reminder, youtube_video,
screen_process, computer_settings, browser_control, file_controller, desktop_control, code_helper, dev_agent,
computer_control, game_updater, flight_finder, file_processor.

**3 special inline tools (main-only, individual tests):** save_memory (`__SILENT__`), agent_task (task queue),
shutdown_jarvis (spawns daemon thread → `os._exit(0)`; MUST be mocked in tests).

---

## 7. Out of Scope (explicit)

WO-0 is **only** the verification foundation + the 4 bug fixes + signature normalization. Explicitly
excluded:

- **The real Rust wheel build** (WO-1) — WO-0 ships only a non-blocking CI placeholder + the abi3
  decision record.
- **Registry refactor** (WO-2) — WO-0 corrects the planner tool list by hand; it does not auto-generate
  it.
- **Security gates** (WO-3).
- **Memory work** (WO-4).
- **Loopguard** (WO-5).
- **OpenJarvis paradigm agents, `ToolExecutor`/`EventBus`, DSPy/GEPA/RL** — banned globally
  (constitution §"Banned").
- **Gating WO-0 changes behind flags** — the flag system is built but gates nothing.

---

## 8. SPM — Software Project Management

- **Scope.** Single bounded context: the dispatch + foundations layer. 4 bug fixes, 17-signature
  normalization + caller updates, a default-OFF feature-flag schema, a from-zero pytest harness, and a
  GitHub Actions CI matrix with a non-blocking Rust placeholder. No scope decomposition warning; not
  dual-client (`intake.dual_client = false`).
- **Effort / schedule estimate.** Complex (Tier 0), **8–12 days**, high risk.
- **Key risks.**
  - **R-1 (high):** Signature normalization + caller updates touch 17 action files plus both dispatchers;
    a missed caller silently breaks dispatch — mitigated by `**kwargs` absorption and the dual-path
    routing test (TAS-1).
  - **R-2 (medium):** CI matrix flakiness across 4 OS legs × 3 Python versions (Qt/`QApplication` headless,
    Windows `rlimit` absence) — mitigated by NFR-4/EC-1 gating and real-`QApplication` fixtures.
  - **R-3 (medium):** B3's behaviour change (unknown tool now raises) could surface latent callers relying
    on the silent fallback — mitigated by the B3 regression test (TAS-4) and the intentional-change note
    (EC-5).
  - **R-4 (low):** `shutdown_jarvis` killing the test runner if its mock is missed — mitigated by FR-4.3 /
    EC-3.
  - **R-5 (low):** Re-introduction of the "18 tools" miscount by a fresh authoring pass — mitigated by the
    verbatim §6 enumeration and the Lessons Applied note.
- **Requirement notes.** The intake "18 tools / normalize 18 signatures" figure is a confirmed arithmetic
  miscount; the verified count is **17** action-backed tools (10 missing `speak` + 7 with `speak`). The
  3 inline tools are main-only and tested individually, not through both paths.

---

## 9. Acceptance Checklist (mirrors intake AC1–AC4; each item is testing-phase-verifiable)

- [ ] **AC1 — CI green + Rust placeholder + smoke job.** CI is green on all matrix legs (macos-14,
  macos-13, Windows, Linux × Py3.11/3.12/3.13); the Rust wheel-build job is present as a non-blocking
  placeholder; the `import mark_xl_rust` smoke job is present and skips cleanly when the wheel is absent.
  (Verifies FR-5, TAS-8.)
- [ ] **AC2 — Dual-path dispatch for all 17 action tools.** All 17 action tools (§6) dispatch through
  **both** `main._execute_tool` and `executor._call_tool` in the parametrized test, with `player` and
  `speak` forwarded on both paths. (Verifies FR-1.1, FR-1.2, FR-2, FR-4.2, TAS-1.)
- [ ] **AC3 — 4 bugs fixed, each with a regression test.** B1, B2, B3, B4 are fixed and each has a
  dedicated regression test that fails pre-fix and passes post-fix. (Verifies FR-1, TAS-2..TAS-5.)
- [ ] **AC4 — flag-OFF == baseline.** The flag system defaults all flags OFF and nothing in WO-0 is gated;
  with flags OFF, behaviour equals baseline. Verifiable: the flag schema exists, `get_flag` returns OFF by
  default, and no WO-0 fix/normalization is wrapped in a flag. (Verifies FR-3, NFR-3, TAS-7.)

---

## Lessons Applied

Source: `pipeline/memory/0-constitution-reflect.md` (Phase 0 RARV).

- **L-1 — "17 tools, not 18" (the miscount).** The intake/spec package's "18 tools" is a confirmed
  arithmetic miscount (10-missing-`speak` + 7-with-`speak` = 17 action files, verified by `ls actions/*.py`
  → 17). *How it shaped this spec:* §6 fixes the canonical count at 17, copied verbatim from intake; the
  3 inline tools (`agent_task`/`save_memory`/`shutdown_jarvis`) are explicitly separated as main-only
  (FR-4.3, EC-7); R-5 flags re-introduction of the miscount as a tracked risk.
- **L-2 — Dual-path testing is a routing/wiring assertion, not live execution.** Many tools have real
  side effects and `shutdown_jarvis` calls `os._exit(0)`. *How it shaped this spec:* FR-4.2 and TAS-1
  specify patching each `actions.X` entry point with a mock and asserting forwarding of `player`+`speak`,
  with no live side effects; EC-3 and FR-4.3 mandate mocking `shutdown_jarvis`'s exit.
- **L-3 — Do NOT flag the bug fixes (WO-0 gates nothing).** The bug fixes and signature normalization are
  unconditional corrections, not flagged changes; the flag system is built but gates nothing. *How it
  shaped this spec:* FR-1/FR-2 are stated as unconditional; FR-3.2, NFR-3, the Out-of-Scope list, AC4, and
  TAS-7 all assert "no WO-0 change is flag-wrapped; all flags default OFF."

No additional applicable lessons were found in a cross-project durable store for this project (the harness
auto-memory loaded this session belongs to a different project, `personal-finance-analyzer`; no
`pipeline/tasks/learnings.md` exists yet for Mark-XL).
