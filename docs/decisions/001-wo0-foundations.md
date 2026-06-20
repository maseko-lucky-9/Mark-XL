# ADR-001 — WO-0 Foundations: dispatch-layer, flag rail, Rust seam, and dual-path test decisions

- **Status:** Accepted
- **Date:** 2026-06-20
- **Work Order:** WO-0 (Foundations), branch `wo-0-foundations` (Mark-XL fork)
- **Supersedes / Superseded by:** none

---

## Context

WO-0 hardens the Mark-XL dispatch layer (the two routing surfaces `main._execute_tool` and
`agent/executor._call_tool`) and stands up the verification foundation (pytest harness + CI)
that downstream Work Orders WO-1..WO-5 depend on. Several cross-cutting decisions affect the
foundation and the future WOs and must be recorded so later authors do not re-litigate them. All
were corroborated against live code on `wo-0-foundations` (see `pipeline/spec.md`,
`pipeline/analysis.md`, `pipeline/design.md`, `pipeline/contracts/tool-signature.md`).

---

## Decision

### (a) abi3=true for the WO-1 Rust wheel

The WO-1 Rust wheel (`mark_xl_rust`) will be built with **`abi3=true`**: one wheel per platform,
compatible with **Python 3.11+** across the matrix. Recorded now (NFR-5) so the WO-0 CI Rust
**placeholder** (`dtolnay/rust-toolchain@1.88` + maturin + sccache, non-blocking) is shaped for
that target. WO-0 builds no real wheel.

### (b) Feature flags default all-OFF, stored in a dedicated `config/flags.json`

WO-0 builds an inert feature-flag rail: a `_FLAG_SCHEMA` (all defaults `False`) and a
`get_flag(name, default=False) -> bool` accessor in `memory/config_manager.py`. The five
registered flags — `use_rust_wheel` (WO-1), `use_tool_registry` (WO-2), `enable_security_gates`
(WO-3), `enable_memory_v2` (WO-4), `enable_loopguard` (WO-5) — all default **OFF**, and WO-0
**wires none of them to behaviour** (flag-OFF == baseline; NFR-3 / AC4).

Flags are stored in a **dedicated `config/flags.json`**, NOT in `config/api_keys.json`. Rationale:
`api_keys.json` is the credentials surface scanned by the Pre-Flight secrets gate; keeping inert
flags in a separate file keeps the flag rail OFF that surface, leaves `load_api_keys()` /
`is_configured()` byte-for-byte unaffected, and isolates inert config from credentials. With no
`flags.json` present, every flag reads OFF — the WO-0 default state.

### (c) `agent/executor._call_tool` gains a `player` param (forwarded on both paths)

The live executor dispatcher `_call_tool(tool, parameters, speak)` (`agent/executor.py:171`) has
no `player` param and hardcodes `player=None` in every branch, making AC2 ("player forwarded on
BOTH paths") unmeetable on the executor leg. Decision: normalize to
`_call_tool(tool, parameters, player=None, speak=None)` and forward `player=player, speak=speak`
in every action branch. Production `execute()` leaves `player` at `None`; the test injects a mock
`player`. The two internal call sites (`agent/executor.py:300` and `:340`) MUST pass `speak` as a
**keyword** arg, since positional would bind `speak` to the new `player` param. The main path
forwards `player=self.ui` and (after the B1 fix) `speak=self.speak`.

### (d) The dual-path test is a routing/wiring assertion, with the non-uniform patch matrix as SoT

The parametrized dual-path test (TAS-1 / AC2) is a **routing/wiring assertion**: each tool's entry
point is patched with a `MagicMock` and the test asserts the dispatcher forwarded `player` and
`speak` — there are **no live side effects** (several tools mutate the system; `shutdown_jarvis`
calls `os._exit(0)`). The **single source of truth** is the **non-uniform patch matrix**: the
**main** leg patches the symbol as bound in `main` (`main.<func>`, with two aliases
`main.weather_action` and `main.web_search_action`), while the **executor** leg patches the
lazy-imported `actions.<module>.<func>`. A naive `patch("actions.X.X")` false-greens the main leg
and misses the two aliases. The matrix is frozen in `pipeline/contracts/tool-signature.md`.

#### (d-note) Planner addition is `file_processor` ONLY — `agent_task` is excluded

Related decision recorded here for completeness: B4 corrects `PLANNER_PROMPT` by adding
`file_processor` **only**. `agent_task` is NOT added to the planner, even though spec TAS-5's
parenthetical mentions it: `agent_task` is a main-only inline tool with no branch in
`agent/executor._call_tool`, so after B3 lands (unknown tool -> raise) a planner-emitted
`agent_task` step would raise. The planner feeds the executor, so the executor's tool set bounds
what the planner may emit.

---

## Consequences

- **Positive.** AC2 becomes meetable on both legs (player param); the dual-path routing test
  catches a missed caller (R-1) without triggering side effects; the flag rail is ready for
  WO-1..WO-5 with zero baseline behaviour change; the Rust seam is green-today and shaped for the
  abi3 wheel; the patch matrix prevents the #1 false-green trap (R-3).
- **Negative / trade-offs.** The executor signature change ripples to its 2 call sites (must use
  keyword `speak=`). `**kwargs` is kept on all 17 signatures as a safety net, so a genuinely
  wrong kwarg is silently absorbed rather than raising — accepted deliberately (belt-and-suspenders
  with the dual-path test as the real guard).
- **Behaviour change.** Exactly one intended behavioural change vs. baseline: an unknown tool in
  `agent/executor._call_tool` now **raises** instead of silently calling `_run_generated_code`
  (B3 / EC-5). This is a bug correction, asserted by the B3 regression test (TAS-4).
- **Scope.** WO-0 only. No registry refactor (WO-2), no security gates (WO-3), no memory work
  (WO-4), no loopguard (WO-5), no real Rust wheel (WO-1). The flag system is built but gates
  nothing.
