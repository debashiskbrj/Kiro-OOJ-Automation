# Implementation Plan — One-time auto-setup + preflight checklist

## Goal

Let a NEW associate who receives the project folder just restart the server and get going
with minimal manual steps. On a fresh server start, auto-install only MISSING Python
packages, and surface a read-only checklist of the things only a human can do (Midway
login, Edge sign-in, system tools). ABSOLUTE top priority: do NOT change the behavior of
the existing working pipeline. All 59 existing tests must still pass:
`py -3.13 -m pytest tests -q` (run from workspace root `c:\Users\kabideba\Desktop\Kiro OOJ Automation\`).

## Grounding facts (read from the code, must be preserved exactly)

- `tools/web/server.py` `main()` order: build server → print banner/URL → `_sanitize("startup")`
  → optionally open browser → `server.serve_forever()`. `_sanitize(reason)` wraps
  `sanitize_captures.sanitize(...)` in try/except and logs `[sanitize/<reason>] ...` so
  cleanup can NEVER block the server. This is the pattern Feature A wiring must mirror.
- `/restart` (POST) → `_relaunch_server(ACTIVE_PORT)` spawns a brand-new detached
  `server.py` process (`--no-open`) and shuts the current one down. That fresh process runs
  its own `main()`, which will run auto_setup once. So the single call added in `main()` is
  exactly one auto_setup run per fresh start — do NOT add a second call anywhere.
- `tools/web/run_controller.py` `preflight_state()` returns EXACTLY these keys (none may be
  removed or renamed — app.js and tests depend on them):
  `midway_ok`, `edge_profile_exists`, `edge_profile_busy`, `edge_stale_locks`,
  `mcscli_present`, `ready`.
  app.js (`tools/web/app.js` `refreshPreflight()`) reads `midway_ok`, `edge_profile_exists`,
  `edge_profile_busy`. A new additive key is safe; renaming/removing is not.
- `run_controller.midway_session_valid()` and `run_daily.preflight()` both parse
  `~/.midway/cookie`: tab-split each line (after stripping `#HttpOnly_`), require
  `parts[0]` ends with `midway-auth.amazon.com`, `parts[5] == "session"`, and
  `float(parts[4]) > now`. Reuse `run_controller.midway_session_valid()` to avoid drift.
- Exact paths found in `run_daily.preflight()` (hard-coded there) — the checklist must read
  these same literals:
  - mcscli: `C:\Program Files\Common Files\AmazonMidwayClientSuite\bin\mcscli.exe`
  - builder-mcp: `C:\Users\kabideba\AppData\Local\Toolbox\bin\builder-mcp.exe`
  - Edge profile: `ooj_paths.EDGE_PROFILE` (overridable via `OOJ_EDGE_PROFILE`)
  - template: `fill_template.TEMPLATE` = `Docs/Templete_OOJ Process_Date_September_KABIDEBA.xltx`
- Tests live in `tests/`, use pytest, and set `sys.path.insert(0, str(TOOLS))` and
  `str(TOOLS / "web")`. There is no conftest.py. Follow `tests/test_web_controller.py`
  for import/monkeypatch style.

## Design decisions

- **New module `tools/auto_setup.py`** holds both Feature A (`ensure_python_env`) and
  Feature B (`environment_checklist`). One module keeps the import-check logic shared
  between the two (avoids drift) and matches the project's flat `tools/` layout.
- **Surfacing choice: extend `preflight_state()`** with one additive key
  `"environment_checklist"` (rationale: the task says prefer extending; app.js already
  calls `/preflight` on a 5s poll, so no new endpoint or app.js change is needed, and no
  existing key is touched). We do NOT add a `/checklist` endpoint.
- **mcscli/builder-mcp paths**: to avoid drift, add them as module-level constants in
  `auto_setup.py` set to the exact literals from `run_daily.preflight()`. (They are not
  currently centralized in `ooj_paths.py`; introducing a shared constant there is optional
  and out of scope — keeping the literal in one new place that mirrors run_daily is
  sufficient and lower-risk than editing run_daily.) Template and Edge profile are read via
  `fill_template.TEMPLATE` and `ooj_paths.EDGE_PROFILE` (already centralized).
- **Playwright Edge detection**: treat as best-effort. `environment_checklist` only reads;
  `ensure_python_env` may run a one-time `py -3.13 -m playwright install msedge` but records
  a checklist item and never crashes on failure.

---

# Implementation Plan

- [ ] 1. Create `tools/auto_setup.py` with the package-import helper and the three
      module-level path constants.
      Add `REQUIRED_PACKAGES = ("openpyxl", "playwright", "requests")`, and constants
      `MCSCLI_PATH`, `BUILDER_MCP_PATH` set to the exact literals from
      `run_daily.preflight()` (mcscli and builder-mcp paths above). Add a private
      `_missing_packages() -> tuple[list[str], list[str]]` that uses
      `importlib.util.find_spec(pkg)` to split REQUIRED_PACKAGES into
      `(already_ok, missing)` — pure, no install, no import side effects.
      Files: `tools/auto_setup.py`
      Verify: `py -3.13 -c "import sys; sys.path.insert(0,'tools'); import auto_setup; print(auto_setup._missing_packages())"`
      prints two lists with no error (all three already installed in this env, so missing is empty).

- [ ] 2. Add `ensure_python_env(log=print) -> dict` to `tools/auto_setup.py` (Feature A).
      For each MISSING package only, run `[sys.executable, "-m", "pip", "install", pkg]`
      via `subprocess.run(..., capture_output=True, text=True)`, append stdout/stderr lines
      to `actions`/`errors`, and record installed vs failed. Do NOT pin or upgrade
      already-installed packages. Then ensure the Playwright Edge browser: if `playwright`
      is importable, best-effort probe/one-time `[sys.executable, "-m", "playwright", "install", "msedge"]`
      only when needed; on any failure append to `errors` and continue. Return
      `{"ran": bool, "installed": [...], "already_ok": [...], "actions": [...], "errors": [...]}`
      where `ran` is True only if an install/playwright action was attempted.
      CRITICAL: wrap the ENTIRE body in try/except (mirror `server.py _sanitize`) so it can
      NEVER raise to the caller; on an unexpected exception, log it and return a dict with the
      error recorded. Must NOT install Python itself, mcscli, builder-mcp, Midway client, or
      Edge-the-app; must NOT touch pipeline logic, template, docs, tests, or `tools/captures`.
      When everything is present it is a fast no-op (`ran=False`, empty `installed`).
      Files: `tools/auto_setup.py`
      Verify: covered by the unit tests in step 5 (`py -3.13 -m pytest tests/test_auto_setup.py -q`).

- [ ] 3. Add `environment_checklist() -> dict` to `tools/auto_setup.py` (Feature B).
      Purely READ-ONLY: never installs, never launches mwinit, no side effects. Build an
      items list, each `{"name": str, "ok": bool, "detail": str, "fix": str}`, for:
      (a) each required Python package (reuse `_missing_packages()`);
      (b) Playwright Edge browser present (best-effort read-only probe; `ok=False` with a
      `fix` hint if absent, never raise);
      (c) mcscli present → `Path(MCSCLI_PATH).exists()`;
      (d) builder-mcp present → `Path(BUILDER_MCP_PATH).exists()`;
      (e) Midway session valid → import `run_controller` and call
      `run_controller.midway_session_valid()` (do NOT launch mwinit);
      (f) Edge automation profile exists → `ooj_paths.EDGE_PROFILE.exists()`;
      (g) Excel template present → `fill_template.TEMPLATE.exists()`.
      Return `{"all_ok": all(item["ok"]), "items": [...]}`. Wrap any per-item probe so a
      failure yields `ok=False` with detail rather than raising.
      Files: `tools/auto_setup.py`
      Verify: covered by step 5 tests.

- [ ] 4. Wire Feature A into `server.py main()` and surface Feature B via `preflight_state()`.
      In `tools/web/server.py`: import `auto_setup` (alongside `sanitize_captures`, after
      `sys.path.insert(0, str(HERE.parent))`), and add a private `_auto_setup()` that mirrors
      `_sanitize` — calls `auto_setup.ensure_python_env(log=lambda m: print(f"[setup] {m}"))`
      inside try/except so it can never block the server. Call `_auto_setup()` in `main()`
      exactly ONCE, right after `_sanitize("startup")` and before the browser-open/
      `serve_forever()`. Do NOT add any call in `_relaunch_server` (the fresh process's own
      `main()` runs it — one run per fresh start).
      In `tools/web/run_controller.py` `preflight_state()`: add one additive key
      `"environment_checklist": _environment_checklist_safe()` where
      `_environment_checklist_safe()` imports `auto_setup` and returns
      `auto_setup.environment_checklist()` inside try/except (on failure return
      `{"all_ok": False, "items": [], "error": "..."}`). Do NOT remove/rename any existing
      key. Do NOT change app.js (the new key is ignored by the current page).
      Files: `tools/web/server.py`, `tools/web/run_controller.py`
      Verify: `py -3.13 tools\web\server.py --no-open --port 8000` starts, prints the banner,
      a `[setup] ...` line (or clean no-op), then serves; `curl http://127.0.0.1:8000/preflight`
      returns JSON containing all six original keys PLUS `environment_checklist`. Stop the
      server after checking. Also `py -3.13 -m pytest tests/test_web_controller.py -q` passes
      (confirms no existing preflight/controller behavior broke).

- [ ] 5. Add offline unit tests `tests/test_auto_setup.py` (no real pip/network/mwinit/filesystem writes).
      Follow the import/monkeypatch style of `tests/test_web_controller.py` (insert `tools`
      and `tools/web` on `sys.path`). Cover:
      - `ensure_python_env` when all packages import → no `subprocess.run` call,
        `ran is False`, `installed == []`, `already_ok` has all three (patch
        `auto_setup._missing_packages` to return `([...], [])` and assert the install
        function is not invoked);
      - exactly ONE missing package → `subprocess.run` called once with
        `[sys.executable, "-m", "pip", "install", "<thatpkg>"]` and that pkg in `installed`
        (patch `_missing_packages` to return one missing; patch `subprocess.run` to a fake
        returning rc=0);
      - `ensure_python_env` never raises even if the patched install raises (assert it
        returns a dict with the error recorded);
      - `environment_checklist` returns `all_ok`/items with correct `ok` flags and NO side
        effects: patch `Path.exists`/the path constants, patch
        `run_controller.midway_session_valid`, and assert no install/subprocess is invoked;
        assert each item has keys `name/ok/detail/fix`.
      Files: `tests/test_auto_setup.py`
      Verify: `py -3.13 -m pytest tests/test_auto_setup.py -q` — all new tests pass.

- [ ] 6. (Optional, low risk) Add an explanatory comment to `Start OOJ Web.bat`.
      Add REM line(s) noting that the first start auto-installs any MISSING Python packages
      and shows a checklist of manual steps (Midway login, Edge sign-in). Do NOT change the
      command the .bat runs (`py -3.13 "tools\web\server.py" --port 8000`).
      Files: `Start OOJ Web.bat`
      Verify: visual diff shows only added REM lines; the executed command is unchanged.

- [ ] 7. Full regression + cleanup.
      Run the whole suite from the workspace root and confirm the baseline is preserved
      (59 prior tests + the new auto_setup tests all pass). Remove any temp files created
      during manual server verification in step 4.
      Files: none
      Verify: `py -3.13 -m pytest tests -q` from `c:\Users\kabideba\Desktop\Kiro OOJ Automation\`
      — all tests pass (>= 59, i.e. the original 59 still green plus new ones).

## Verification

Ran from workspace root `c:\Users\kabideba\Desktop\Kiro OOJ Automation\`:

- `py -3.13 -m pytest tests -q` → **64 passed in 3.09s** (59 prior tests still green +
  5 new `tests/test_auto_setup.py` tests). Baseline preserved.
- `py -3.13 -c "import sys; sys.path.insert(0,'tools'); import auto_setup; print(auto_setup.environment_checklist()['all_ok'])"`
  → printed **False** (read-only; some manual item — e.g. Edge sign-in / Midway — is not
  satisfied in this environment). Installed nothing, launched no mwinit.
- `py -3.13 -c "import sys; sys.path.insert(0,'tools'); sys.path.insert(0,'tools/web'); import server"`
  → **server-import-ok** (imports cleanly, including the new `import auto_setup`).
- Extra check — `preflight_state()` keys:
  `['edge_profile_busy','edge_profile_exists','edge_stale_locks','environment_checklist','mcscli_present','midway_ok','ready']`
  → all six original keys present (`has_all_six True`) PLUS the additive
  `environment_checklist` (`has_new True`). No key removed or renamed.

Implemented: `tools/auto_setup.py` (Feature A `ensure_python_env`, Feature B
`environment_checklist`, pure `_missing_packages`, path constants mirroring
`run_daily.preflight`); wired `_auto_setup()` into `server.py main()` once after
`_sanitize("startup")` (no second call — `/restart` spawns a fresh `server.py` whose own
`main()` runs it); extended `run_controller.preflight_state()` additively; added an
explanatory REM comment to `Start OOJ Web.bat` (command unchanged); added offline tests.
No pipeline logic, template, docs, or `tools/captures` touched. Temp verification logs
cleaned up.

## Notes / assumptions

- In this environment all three required packages are already installed, so Feature A is a
  no-op at runtime here; the install path is exercised only by the patched unit tests.
- Keeping the mcscli/builder-mcp literals in `auto_setup.py` (mirroring run_daily) rather
  than editing `run_daily.preflight()` avoids any risk to the working preflight. If the team
  later wants a single source of truth, centralizing both in `ooj_paths.py` is a safe
  follow-up but is intentionally out of scope here to protect the working pipeline.
- No pipeline logic, template, steering, or `tools/captures` content is modified by any step.
