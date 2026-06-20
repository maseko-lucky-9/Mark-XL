# Phase 4 Reflection (Development / `tasks`) — RARV

- **The 5-file granularity gate forced the right split, not just a compliance split.** The 17 action
  files naturally separate into 4 cohesive batches: "no-speak" group (10 files → T02/T03, 5 each)
  and "with-speak" group (7 files → T04/T05, 4+3). This matches the cognitive grain of the inner
  loop — each batch is a mechanical, uniform operation with a clear before/after signature contract,
  no business-logic risk, and a single acceptance criterion. The split was not arbitrary.

- **Bundling all executor.py changes into T06 is the only safe design.** Adding `player=None` to
  `_call_tool` and fixing the 2 internal call sites to `speak=speak` (keyword) are inseparable: if
  the call sites are left positional after the param is inserted, `speak` silently binds to `player`
  and the dispatcher is broken in a way that may not surface immediately (no TypeError, just wrong
  player forwarded). Any decomposition that splits "add player param" from "fix call-site keyword" is
  a latent correctness trap. T06 owns all six sub-changes as one atomic unit.

- **The non-uniform patch matrix (§4 of the contract) is the top correctness trap, not the largest
  task.** The dual-path test (T10) is only one file and a medium-size test, but it carries the highest
  failure risk: naively patching `actions.X.X` for the main leg gives FALSE GREENS because main binds
  functions at module top. Two aliases (`main.weather_action`, `main.web_search_action`) are
  particularly easy to miss. T10's acceptance criterion explicitly names both aliases and flags the
  naive-patch trap so the inner loop cannot overlook it.

- **Decision C (agent_task stays out of PLANNER_PROMPT) must be enforced at the test level.** T15
  (TAS-5) asserts BOTH that `file_processor` IS in `PLANNER_PROMPT` AND that `agent_task` is NOT.
  The spec.md TAS-5 parenthetical "(and agent_task)" is superseded by Decision C; baking the
  negative assertion into T15's acceptance criterion prevents a spec-faithful-but-broken
  implementation that adds both (which would manufacture the post-B3 raise the fix is meant to
  prevent).

- **The regression tests are most valuable as reversal tests, not just passing tests.** Each B1–B4
  regression test (T12–T15) is annotated with its "pre-fix failure note" — the exact condition under
  which it MUST be red. The inner loop's Red/Green/Refactor cycle requires genuine Red; documenting
  the revert path in the task body (e.g., "revert T07's flight_finder change -> test red") ensures
  AC3's "fails pre-fix" claim is verifiable, not assumed.
