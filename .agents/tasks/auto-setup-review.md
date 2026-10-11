# One-time auto-setup + read-only preflight checklist

A new `tools/auto_setup.py` module adds two capabilities for a fresh associate who receives the project folder: `ensure_python_env` installs only the Python packages that are missing (never touching already-installed ones) on a fresh server start, and `environment_checklist` returns a read-only snapshot of the manual steps only a human can do (Midway login, Edge sign-in, system tools). Feature A is wired into `server.py main()` once after `_sanitize("startup")`; Feature B is surfaced through one additive `environment_checklist` key on `preflight_state()`. The existing stdlib pipeline, template, docs, and `tools/captures` are untouched. Both features are fully wrapped so neither can block the server or break preflight.

Watch for: nothing blocking. The install path and the read-only checklist are both guarded against raising; the mcscli/builder-mcp path literals are duplicated (mirrored, by design) from `run_daily.preflight()`, which is the one maintenance note (confirmed, non-blocking).

**Verdict**: APPROVED

## High-level view

The no-impact requirement holds. No existing pipeline logic, template, doc, or test was altered. `preflight_state()` keeps all six original keys (`midway_ok`, `edge_profile_exists`, `edge_profile_busy`, `edge_stale_locks`, `mcscli_present`, `ready`) verbatim and adds exactly one additive key, `environment_checklist`; `app.js` reads only `midway_ok`, `edge_profile_exists`, `edge_profile_busy` and ignores the new key, so the page is unaffected.

`ensure_python_env` installs only packages that `importlib.util.find_spec` reports as missing, via `pip install <pkg>` with no version pin, upgrade, or uninstall. The entire body is wrapped in a top-level try/except that mirrors `server.py._sanitize`, and the Playwright msedge step is a nested best-effort block that records errors instead of raising. When all three required packages are present it is a fast no-op (`ran=False`). It does not install Python, mcscli, builder-mcp, the Midway client, or the Edge application.

`environment_checklist` is purely read-only: it probes package presence (reusing `_missing_packages`), a best-effort Playwright launch that closes immediately, `Path(...).exists()` on the two tool literals, `run_controller.midway_session_valid()` (reused to avoid drift, never launching mwinit), `ooj_paths.EDGE_PROFILE.exists()`, and `fill_template.TEMPLATE.exists()`. Every probe is individually wrapped so a failure yields `ok=False` with a detail rather than raising.

The server wiring calls `_auto_setup()` exactly once in `main()`, after `_sanitize("startup")` and before browser-open/`serve_forever()`. `/restart` spawns a fresh detached `server.py` whose own `main()` runs setup — so there is no duplicate call; `_relaunch_server` adds none.

The new offline tests cover the no-op, single-missing-install, never-raises-on-install-error, read-only all-ok, and reports-missing cases, with pip and `subprocess.run` monkeypatched to assert no real install occurs.

<details>
<summary>Issues (1)</summary>

1. **Duplicated path literals (non-blocking)** — the mcscli and builder-mcp paths are hard-coded in both `auto_setup.py` and `run_daily.preflight()`. This is a deliberate, documented choice to avoid editing the working preflight; if either path ever moves, both sites must be updated. Optional future follow-up: centralize in `ooj_paths.py`.

</details>

<details>
<summary>Details</summary>

### No existing pipeline logic changed

The change set is `tools/auto_setup.py` (new), `tests/test_auto_setup.py` (new), additive edits to `tools/web/server.py` and `tools/web/run_controller.py`, and REM-only comment lines in `Start OOJ Web.bat`. Nothing under `tools/captures`, the Excel template, the docs, or the existing tests was modified. In `run_controller.py` the only additions are the `_environment_checklist_safe()` helper and the single new dict entry in `preflight_state()`'s return — `RunController`, `start_run`, `get_status`, the publication/download gates, and the summary parsing are byte-for-byte the proven logic.

### preflight_state() is additive-only

`preflight_state()` returns the six original keys unchanged — `midway_ok`, `edge_profile_exists`, `edge_profile_busy`, `edge_stale_locks`, `mcscli_present`, `ready` — plus `"environment_checklist": _environment_checklist_safe()`. No key was removed or renamed. `_environment_checklist_safe()` wraps the `auto_setup.environment_checklist()` call in try/except and returns `{"all_ok": False, "items": [], "error": ...}` on any failure, so a checklist probe error can never corrupt the preflight payload the page depends on. `app.js` consumes only `midway_ok`, `edge_profile_exists`, and `edge_profile_busy`, confirming the new key is inert for the current page.

### ensure_python_env installs missing-only and can never raise

The install loop iterates `missing` from `_missing_packages()` and calls `_pip_install(pkg)` → `[sys.executable, "-m", "pip", "install", pkg]` with no `--upgrade`, no version specifier, and no uninstall. `already_ok` packages are never acted on. The Playwright msedge step runs only when `playwright` is importable and `_playwright_edge_ok()` returns False, and lives in its own try/except that records to `errors`. The whole body sits inside a final `except Exception` that appends `auto_setup: <type>: <msg>` and returns the result dict — so an unexpected failure degrades to a recorded error, never a raised exception. The `test_ensure_env_never_raises_on_install_error` test confirms this by making `_pip_install` raise and asserting a dict is returned with the package in `errors`. When nothing is missing and Edge is usable, `ran` stays False and no subprocess is spawned (`test_ensure_env_noop_when_all_present` asserts `subprocess.run` is never called).

The module does not install Python, mcscli, builder-mcp, the Midway client, or Edge-the-app — it only pip-installs the three `REQUIRED_PACKAGES` and best-effort runs `playwright install msedge`.

### environment_checklist is read-only with no drift

Each probe is independently guarded. The Midway check reuses `run_controller.midway_session_valid()` rather than re-parsing the cookie, so the page and the engine cannot drift on session validity. The template and Edge-profile checks read `fill_template.TEMPLATE` and `ooj_paths.EDGE_PROFILE`, the already-centralized constants. `_playwright_edge_ok()` launches and immediately closes a browser inside a broad try/except, returning False on any failure. `test_checklist_all_ok_no_side_effects` monkeypatches `_pip_install` and `subprocess.run` to raise if invoked and asserts the checklist completes without touching them, proving the read-only guarantee.

The mcscli and builder-mcp path literals are duplicated from `run_daily.preflight()`. They match exactly (verified against `run_daily.py` lines 255 and 259). This duplication is the single maintenance note: if a path moves, both locations need updating. The plan documents this as an intentional lower-risk choice than editing the working preflight, and centralizing in `ooj_paths.py` is left as an optional follow-up.

### Server wiring runs setup exactly once per fresh start

`main()` calls `_auto_setup()` immediately after `_sanitize("startup")` and before the browser-open/`serve_forever()` block. `_auto_setup()` mirrors `_sanitize`: it calls `auto_setup.ensure_python_env(...)` inside try/except and logs `[setup] skipped (...)` on failure, so setup can never block serving. `/restart` → `_relaunch_server` spawns a brand-new detached `server.py --no-open` process and shuts the current one down; the fresh process's own `main()` runs `_auto_setup()` once. No second call exists anywhere, so restart does not double-run setup.

### Tests are offline

`tests/test_auto_setup.py` follows the `test_web_controller.py` import/monkeypatch style, inserting `tools` and `tools/web` on `sys.path`. It patches `_missing_packages`, `_playwright_edge_ok`, `_pip_install`, `subprocess.run`, `Path.exists`, and `run_controller.midway_session_valid`, so no real pip, playwright, mwinit, filesystem write, or network call occurs. Coverage spans the three Feature A cases (no-op, single-missing-install, never-raises) and two Feature B cases (all-ok no-side-effects, reports-missing). Spot-check: running `py -3.13 -m pytest tests/test_auto_setup.py -q` locally produced 5 passed, matching the coder's recorded evidence (64 total: 59 prior + 5 new).

</details>

<details>
<summary>File map</summary>

- `tools/auto_setup.py` — new module: `_missing_packages`, `_pip_install`, `_playwright_edge_ok`, `ensure_python_env` (Feature A), `environment_checklist` (Feature B), path constants mirroring `run_daily.preflight`.
- `tools/web/server.py` — added `import auto_setup` and `_auto_setup()`; one call in `main()` after `_sanitize("startup")`. No pipeline logic changed.
- `tools/web/run_controller.py` — added `_environment_checklist_safe()` and one additive `environment_checklist` key on `preflight_state()`. Six original keys unchanged.
- `tests/test_auto_setup.py` — new offline tests (5 cases).
- `Start OOJ Web.bat` — REM comment lines only; executed command unchanged.

Note: no git repo is present in the workspace, so this review is based on reading the implemented files directly against the implementation plan rather than a `git diff`.

</details>
