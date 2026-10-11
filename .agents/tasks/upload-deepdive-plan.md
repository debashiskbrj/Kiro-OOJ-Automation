# Implementation Plan — Upload/Post-Comments feature + Pipeline deep dive

Baseline: `py -3.13 -m pytest tests -q` -> **69 passed in 8.26s, 0 failed** (recorded during planning, workspace root `c:\Users\kabideba\Desktop\Kiro OOJ Automation`). Every later step must keep the pass count at or above 69 with 0 failed.

This task decomposes into two independent features (disjoint file sets, very different risk profiles) and has been authored as FEAT artifacts under `.agents/tasks/task-upload-deepdive/` for the implement/review loop:
- `task.json`, `context.json`
- `features/FEAT-001.json` — Task 1: gated upload/post-comments feature (HIGH RISK, additive-only)
- `features/FEAT-002.json` — Task 2: safe read-only-behavior deep dive across existing pipeline modules (SAFE)

The sections below mirror those FEAT files for a human-readable view; the FEAT JSON files are the authoritative, step-by-step source the coder/reviewer loop consumes.

## Task 1 — Upload file -> Post comments (HIGH RISK, additive)

- [ ] 1. Create `tools/post_comments_runner.py`: parse the uploaded `.xlsx` (pandas + openpyxl engine), case-insensitive/whitespace-tolerant `url`/`tid`/`remark` headers, group rows by `url` preserving order. Implement `run_dry_run` (parse-only, no Playwright/network, writes `dry_run_summary.json` atomically via `.tmp` + `Path.replace()` mirroring `run_daily.py`'s `Run.write_progress`, prints an `OUT_DIR=`-style first stdout line and progress lines, `flush=True`).
      Files: `tools/post_comments_runner.py`
      Verify: `py -3.13 -c "import sys; sys.path.insert(0,'tools'); import post_comments_runner"` succeeds; `py -3.13 -m pytest tests -q` still 69 passed.

- [ ] 2. In the same module, implement `run_confirmed` (only under `--mode confirmed`, never implied by dry-run): adapt (not copy) the reference script `C:\Users\kabideba\Documents\TT_Automation\post_comments.py` — same selectors (`#sim-communicationActions--createComment`, `button:has-text('Post to Correspondence')`), same `TID: {tid} | {remark}` comment format, same verification loop, same per-url incremental report save to `post_comments_report.xlsx` for crash recovery, same today's-report Success dedup. DROP the blocking `input()` retry loop — one pass through all pending urls, then exit. Add `run_retry_failed` as a separate entry point that re-attempts only urls marked Failed in the report.
      Files: `tools/post_comments_runner.py`
      Verify: `py -3.13 -m pytest tests -q` still 69 passed (no Playwright call is exercised by import/argparse wiring alone).

- [ ] 3. Create `tools/web/post_comments_controller.py` with `PostCommentsController` mirroring `RunController`'s shape: `upload_and_preview(file_bytes, filename)` (saves to `tools/captures/post_comments/uploads/`, spawns the dry-run subprocess, blocks with a clear reason if a post-comments subprocess is already running or `run_controller.CONTROLLER.is_running()` is True), `confirm_and_post()` (spawns the real posting subprocess ONLY if a prior successful dry-run of the exact current upload exists AND the pipeline is not running — else returns a blocked-reason dict, never raises), `retry_failed()`, `stop()`, `get_status()`. Module-level `CONTROLLER = PostCommentsController()`.
      Files: `tools/web/post_comments_controller.py`
      Verify: `py -3.13 -m pytest tests -q` still 69 passed.

- [ ] 4. Add 6 new routes to `tools/web/server.py` (`POST /post-comments/upload`, `GET /post-comments/status`, `POST /post-comments/confirm`, `POST /post-comments/retry-failed`, `POST /post-comments/stop`, `GET /post-comments/report-download`), consistent with the existing `do_GET`/`do_POST` dispatch style and `_send_json`/`_read_body`/`_serve_download` helpers. The write-triggering POSTs return a clear 409 when the mutual-exclusion guard blocks them.
      Files: `tools/web/server.py`
      Verify: `py -3.13 -m pytest tests -q` still 69 passed (no existing route/handler touched).

- [ ] 5. Add UI: new `<section class="card" id="post-comments-card">` ("5. Post comments (optional)") AFTER the "4. Result" card in `index.html` — file picker + Upload & Preview button, a prominent dry-run preview area, a disabled-until-preview `.danger` "Post comments to tickets" button with explicit live/irreversible warning copy (mirroring the ADS `.hint` style) and a required `.checkbox` confirmation. Matching `app.js` polling (`pcTick()`/render functions following `tick()`/`renderSources()` naming) and reused `style.css` classes (`.banner`, `.pbar`, `.badge`, `button.danger`) — add new CSS only if truly needed.
      Files: `tools/web/index.html`, `tools/web/app.js`, `tools/web/style.css`
      Verify: manual check that the server starts and the new section renders after "4. Result"; `py -3.13 -m pytest tests -q` still 69 passed.

- [ ] 6. Confirm `tools/sanitize_captures.py`'s existing generic `CAPTURES.rglob("*")` containment walk already covers a new `tools/captures/post_comments/` directory with no change needed (verified during planning — no hardcoded subdirectory allowlist exists). Only touch this file if re-verification at implementation time finds otherwise, and only to add the directory to whatever allowlist exists, never weakening containment.
      Files: `tools/sanitize_captures.py` (expected: no change)
      Verify: `py -3.13 -m pytest tests -q` still 69 passed (existing `tests/test_sanitize_captures.py` unmodified and green).

- [ ] 7. Add `tests/test_post_comments.py` (new file) with OFFLINE tests only (fakes/tmp_path, no real Playwright/Edge/network): parsing/validation of a hand-built xlsx with mixed-case headers and one malformed row; the mutual-exclusion guard (patch `run_controller.CONTROLLER.is_running` True, assert `subprocess.Popen` is never called); the dry-run-required-before-confirm guard (no prior dry-run blocks; a stale/mismatched prior dry-run also blocks).
      Files: `tests/test_post_comments.py`
      Verify: `py -3.13 -m pytest tests -q` passes with 69 + new tests, 0 failed.

## Task 2 — Pipeline deep dive (SAFE, read-only behavior)

- [ ] 8. Re-read `.agents/tasks/ooj-audit-findings.md` and `.agents/tasks/rollback-plan.md` fully before any edit, to avoid re-flagging resolved items or re-proposing intentionally reverted behaviors (SCC `>1000` always-tick-best-effort, the 45-min-equivalent SCC download timeout, no-heartbeat, no-auth-gate GCRS).
      Files: none (reading only)
      Verify: n/a (gate for the following steps)

- [ ] 9. Review `tools/gcrs_mcscli.py`'s `_resolve_batch_with_edge_retry` against its own docstring contract; add OFFLINE tests for any of the three genuinely-untested branches (success-first-try, retry-then-succeed, retry-exhausted-degrades-and-continues) in a new `tests/test_gcrs_retry.py` using a fake session double, following `tests/test_scc_retry.py`'s `patch.object` style. Do not change logic unless a real bug is found (fix minimally, explain in the findings report). Delete the dead `BATCH = 500` constant (audit P3-1) if still present and confirmed unreferenced.
      Files: `tools/gcrs_mcscli.py`, `tests/test_gcrs_retry.py` (new)
      Verify: `py -3.13 -m pytest tests -q` passes, count >= baseline, 0 failed.

- [ ] 10. Verify `tools/scc_client.py`'s same-page re-search retry (`_export_chunk_with_retry`, `_wait_for_download_or_notfound`) has no leftover `page.reload()`/`page.goto()` call and no stale docstrings describing the old reload behavior (distinct from `_export_small`'s legitimate auth-redirect `goto`, which stays untouched). Fix only confirmed staleness/dead code; do not change `SCC_UI_CHUNK`/`AUTO_DOWNLOAD_THRESHOLD`/`SCC_CHUNK_ATTEMPTS` or the checkbox always-tick-best-effort behavior.
      Files: `tools/scc_client.py`
      Verify: `py -3.13 -m pytest tests -q` passes (existing `tests/test_scc_retry.py` stays green), count >= baseline, 0 failed.

- [ ] 11. Across `tools/*.py` (excluding the new Task 1 files): fix only genuine resource leaks on exception paths (missing `try/finally` around a `close()`), add a log call to any silently-swallowing bare `except Exception` that has no logging (leave documented intentional guards alone), apply audit finding P0-1's one safe fix (switch any remaining hardcoded Edge-profile-path literal in `run_daily.py`/`pnama_export.py`/`siw_client.py` to `from ooj_paths import EDGE_PROFILE`, matching what `scc_client.py` already does), and fix clearly stale docstrings/comments in files touched by recent hot-fixes. Do NOT extract the duplicated TID-normalization logic (audit P1-4 is explicitly behavior-changing — leave it, note it in the report).
      Files: various `tools/*.py` (per findings during implementation)
      Verify: `py -3.13 -m pytest tests -q` after each file's edit, count >= baseline, 0 failed; no existing test assertions modified.

- [ ] 12. Write `.agents/tasks/py-deep-dive-findings.md` with two sections: "Fixed (safe, applied)" and "Found but intentionally not changed" (one-line recommendation each, referencing `ooj-audit-findings.md` where applicable).
      Files: `.agents/tasks/py-deep-dive-findings.md`
      Verify: file exists and lists every applied fix and every intentionally-skipped finding.

## Notes

- No git exists in this workspace; do not install or initialize it.
- The stop/gate contract for the implement-review loop is `.agents/tasks/upload-deepdive-review.json` (`verdict` reaching `"APPROVED"`), written last by the reviewer step.
