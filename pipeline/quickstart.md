# Quick Start — WO-0 (Foundations) Test Suite

> Spec Kit `plan` Quick Start Guide for WO-0. How to run the dispatch-layer suite locally and
> how CI runs it. Repo: `~/Repo/agents/mark-xl`, branch `wo-0-foundations`.

---

## 1. Prerequisites

- Python **3.11–3.13** (CI matrix range; NFR-6).
- A virtualenv. Use the project's standard path if present, else create one:
  - If `.venv-mac/` exists -> use `.venv-mac/bin/python`.
  - Else use `.venv/bin/python` (create with `python3 -m venv .venv`).
  - (No venv currently exists at the repo root; create one before first run.)
- Install deps: `<venv>/bin/pip install -r requirements.txt` plus `pytest` and `PyQt6`
  (`PyQt6` is pinned at `requirements.txt:11`).

---

## 2. Run the full suite locally (headless Qt)

Qt is **PyQt6**; tests need a real `QApplication`. Run headless with the offscreen platform
plugin (no display required):

```bash
# from the repo root: ~/Repo/agents/mark-xl
QT_QPA_PLATFORM=offscreen .venv-mac/bin/python -m pytest -q
# or, if using .venv:
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q
# fallback if neither venv exists:
QT_QPA_PLATFORM=offscreen python3 -m pytest -q
```

`QT_QPA_PLATFORM=offscreen` is REQUIRED — without it the `QApplication` fixture tries to open a
display and fails on headless machines / CI.

No test hits a live Ollama socket: the conftest Ollama fixture monkeypatches
`core/llm_client.call_llm_text`, `call_llm`, and `call_llm_stream`.

---

## 3. Run a single regression test

```bash
# B1 — flight_finder receives speak on the main leg (TAS-2)
QT_QPA_PLATFORM=offscreen python3 -m pytest -q -k "flight_finder and speak"

# B2 — executor dispatches file_processor (TAS-3)
QT_QPA_PLATFORM=offscreen python3 -m pytest -q -k "executor and file_processor"

# B3 — unknown tool raises (TAS-4)
QT_QPA_PLATFORM=offscreen python3 -m pytest -q -k "unknown and raise"

# B4 — PLANNER_PROMPT contains file_processor (TAS-5)
QT_QPA_PLATFORM=offscreen python3 -m pytest -q -k "planner and file_processor"

# Run one node directly (adjust to the actual test path/name):
QT_QPA_PLATFORM=offscreen python3 -m pytest -q tests/test_regressions.py::test_b3_unknown_tool_raises
```

(Use the `-k` substring or the explicit `path::test_name` node id; swap in the venv python.)

---

## 4. How CI runs it

GitHub Actions (`.github/workflows/*.yml`):

- **Test matrix (required, green on every leg):**
  `os = [macos-14, macos-13, windows-latest, ubuntu-latest]` x
  `python = [3.11, 3.12, 3.13]`, with `QT_QPA_PLATFORM=offscreen` on **all** legs.
  On `ubuntu-latest`, the job installs `libegl1` (`apt-get install -y libegl1`) for PyQt6;
  `xvfb` is only a fallback if a windowed test needs a display (offscreen should avoid it).
  Command per leg: `QT_QPA_PLATFORM=offscreen pytest -q`.
- **Rust wheel placeholder job** — `dtolnay/rust-toolchain@1.88` + maturin + sccache,
  `continue-on-error: true`, NOT a required check (non-blocking; WO-1 activates the real build).
- **`import mark_xl_rust` smoke job** — guarded by `pytest.importorskip("mark_xl_rust")` (or a
  `try/except ImportError` exiting 0); skips cleanly when the wheel is absent.

---

## 5. Manual sanity check — confirm a tool routes through both paths

Confirm a single action tool is reachable through both dispatchers (routing/wiring, no live
side effects):

```bash
QT_QPA_PLATFORM=offscreen python3 - <<'PY'
from unittest.mock import MagicMock, patch

# Executor leg: patch the lazy-imported actions entry point, assert player+speak forwarded.
with patch("actions.file_processor.file_processor", MagicMock(return_value="ok")) as m:
    from agent import executor
    player, speak = MagicMock(), MagicMock()
    executor._call_tool("file_processor", {"x": 1}, player=player, speak=speak)
    m.assert_called_once()
    kw = m.call_args.kwargs
    assert kw.get("player") is player and kw.get("speak") is speak
    print("executor leg OK: player+speak forwarded")
PY
```

For the **main** leg, patch the symbol **as bound in `main`** (e.g. `main.file_processor`, or the
aliases `main.weather_action` / `main.web_search_action`) per the patch matrix in
`pipeline/contracts/tool-signature.md`, then dispatch through `main._execute_tool` and assert the
same forwarding. Naively patching `actions.X.X` for the main leg false-greens — use the matrix.
