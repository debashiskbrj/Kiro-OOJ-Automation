# Python Deep Dive — Findings (FEAT-002)

Scope: safe, read-only-behavior deep dive across the existing `tools/*.py` pipeline
modules (excluding the Task 1 files: `tools/post_comments_runner.py`,
`tools/web/post_comments_controller.py`, `tests/test_post_comments.py`, and the
additive Task 1 routes/UI). Re-read `.agents/tasks/ooj-audit-findings.md` and
`.agents/tasks/rollback-plan.md` in full before changing anything, per FEAT-002 step 1.

Test baseline before any change: `py -3.13 -m pytest tests -q` → **77 passed, 0
failed** (run from the workspace root). Re-run after every edit in this pass; stayed
at 77 passed, 0 failed throughout.

---

## Fixed (safe, applied)

1. **`tools/edge_preflight.py` — centralize the Edge profile path (audit P0-1 / top
   shortlist #4).** `PROFILE_DIR` was still its own hardcoded literal
   (`C:\Users\kabideba\...\TT_Automation\edge_profile`), identical in value to
   `ooj_paths.EDGE_PROFILE`'s default, while `run_daily.py`, `scc_client.py`,
   `pnama_export.py`, and `siw_client.py` already import `EDGE_PROFILE` from
   `ooj_paths`. Changed `edge_preflight.py` to `from ooj_paths import EDGE_PROFILE` and
   set `PROFILE_DIR = EDGE_PROFILE`, so this is the last of the four P0-1 consumers to
   go through the single, override-compatible (`OOJ_EDGE_PROFILE`) source of truth. No
   other line in the file changed; all call sites still default to the same
   `PROFILE_DIR` parameter. Covered by `tests/test_polygon_workflow.py::EdgePreflightTests`
   (which exercises `_user_data_dir`/`_normalized_path` with explicit profile literals,
   not the module default, so it is unaffected) and the full suite
   (`py -3.13 -m pytest tests -q` → 77 passed, 0 failed, immediately after this edit).

---

## Found but intentionally not changed

- **`gcrs_mcscli._resolve_batch_with_edge_retry` retry-on-any-`TransportError` (vs.
  rollback-plan.md's narrower `"Failed to fetch"`-only filter).** Reviewed against its
  own docstring contract: on the Edge transport it retries any `TransportError` up to
  `EDGE_BATCH_RETRY_ATTEMPTS` (3) with `EDGE_BATCH_RETRY_BACKOFF_S` (2,4,6)s backoff,
  then returns the chunk's TIDs as `errored` instead of raising; on any non-Edge
  transport it calls `_resolve` once with no retry/catch. The implementation matches
  the docstring exactly and is correct. This is a separate, already-landed 2026-10-04
  design (not the Oct-8 rollback-plan.md draft, which used a narrower
  `_is_failed_to_fetch` filter that is not present in the live code) — left exactly
  as-is, per the explicit instruction not to narrow the retry filter.
- **GCRS Edge-retry branch test coverage.** All three branches already have offline
  tests in `tests/test_polygon_workflow.py::FailurePathTests`:
  `test_edge_batch_failed_to_fetch_is_retried_then_resolves` (retry-then-succeed),
  `test_edge_batch_failed_to_fetch_degrades_but_other_batch_resolves`
  (retry-exhausted-degrades, other batches continue), and
  `test_mcscli_transport_error_is_not_retried` (non-Edge, no retry). No new test file
  was needed.
- **`gcrs_mcscli.py` dead constant `BATCH = 500` (audit P3-1).** Not present in the
  current file — only `BATCH_MCSCLI`/`BATCH_EDGE` exist, referenced by
  `_batch_size_for`. Already resolved (or never present in this version); nothing to
  delete.
- **`scc_client.py` retry-path consistency.** Confirmed no `page.reload()`/`page.goto()`
  exists anywhere in the chunk-retry path (`_export_chunk_with_retry`,
  `_wait_for_download_or_notfound`, `_browser_export_chunk`) — a retry only re-fills the
  textarea and clicks Search again on the same frame, matching the #C5 fix and the
  existing `tests/test_scc_retry.py` assertions. `_export_small`'s own `_goto_search`
  call is a distinct, legitimate case (a 300–400 HTTP auth-redirect retry, not a
  chunk-retry-after-UI-failure) and was left untouched, as instructed. Docstrings on
  `_wait_for_download_or_notfound` and `_export_chunk_with_retry` already describe the
  current no-reload behavior accurately; no stale wording found.
- **`scc_client._ensure_large_mode_checkbox` / `_browser_export_chunk`'s
  always-tick-best-effort call.** Confirmed `_browser_export_chunk` calls
  `_ensure_large_mode_checkbox(frame, index, log)` and ignores its boolean return value,
  never raising on a failed tick — this matches rollback-plan.md's intended
  "always-tick-best-effort" behavior exactly. Left untouched per the explicit
  instruction not to re-revert it. `SCC_UI_CHUNK`, `AUTO_DOWNLOAD_THRESHOLD`,
  `SCC_CHUNK_ATTEMPTS`, and the inline `45 * 60` download timeout literal were also
  confirmed unchanged.
- **Resource leaks (Playwright contexts/pages/sessions).** Checked every
  `*_client.py`/`*_export.py`/`*_run.py` browser-context lifecycle: `scc_client.fetch()`,
  `dsr_export.py`'s driver call, and `pnama_export.py`'s export functions all wrap
  `cdp.detach()`/`context.close()` in a `try/finally`, and `gcrs_mcscli.fetch()` wraps
  `active_session.close()` in `try/finally`. No exception path skips a close(). No fix
  needed.
- **Broad `except Exception` swallows.** Audited every bare `except Exception:` /
  `except Exception as exc:` in `tools/*.py` (excluding Task 1 files). All fall into one
  of two already-acceptable categories: (a) documented, intentional "never let cleanup
  / a best-effort probe crash the caller" guards (e.g. `cdp.detach()`/`context.close()`
  teardown in `scc_client.py`/`pnama_export.py`/`dsr_export.py`/`siw_client.py`;
  `tickets_pull.McpStdio.close()`'s subprocess teardown; `auto_setup._playwright_edge_ok()`,
  whose docstring explicitly contracts "never raises"; `sanitize_captures._unlink()`,
  already narrowly scoped to `(OSError, PermissionError)` with an inline comment), or
  (b) retry/navigation-interruption handlers that already log the exception immediately
  (e.g. `scc_client._goto_search`'s AEA-interrupted navigation, `siw_client`'s Edge
  navigation retries, `run_daily.Run.stage`'s per-stage error capture). No genuinely
  silent swallow (an exception with no logging and no documented contract) was found.
  No fix needed.
- **TID normalization duplication (audit P1-4).** Re-confirmed the duplication across
  `run_daily.normalize_tid`, `fill_template._normalized_tid_text`,
  `scc_client._normalize_tid`, `gcrs_mcscli.normalize_tid`/`polygon_check.normalize_tid`,
  and `eagleeye_client._normalize_tid`. `scc_client._normalize_tid` still does not
  uppercase a `doc...`-prefixed TID, while `gcrs_mcscli`/`polygon_check` do — the
  duplication is not byte-for-byte identical across modules, so it is not safely
  extractable without a behavior decision (SCC's DOC-case handling would change). Left
  alone, as flagged in `ooj-audit-findings.md` P1-4; this is a `BEHAVIOR-CHANGING` item,
  not in scope for this pass.
- **Hardcoded paths elsewhere (audit P0-1, remaining consumers).** `run_daily.py`,
  `scc_client.py`, `pnama_export.py`, and `siw_client.py` already import `EDGE_PROFILE`
  from `ooj_paths` (confirmed by reading each file) — only `edge_preflight.py` still had
  its own literal, which is the one fix applied above. `dsr_export.py` reuses
  `pnama_export.py`'s driver and so is already covered transitively. `ads_run.py` does
  not use the shared Edge automation profile at all (a separate Streamlit app, no
  `launch_persistent_context`), so it is out of scope for this centralization.
- **Stale docstrings/comments (audit P4-4, P4-5, and a general scan).**
  `run_daily.finalize_remarks`'s docstring already says "six protected remarks"
  (matching `remark_rules.PROTECTED_REMARKS`'s 6 entries — P4-4 already fixed, nothing
  to do). `scc_client._state_display`'s comment already matches its actual behavior
  ("readable Title-Case derivation" — P4-5 already fixed, nothing to do). No other
  stale comment describing removed reload/goto behavior or a wrong constant name was
  found in `scc_client.py` or `gcrs_mcscli.py`.

---

## Verification

`py -3.13 -m pytest tests -q` was run after the one applied edit (and matches the
pre-edit baseline run at the start of this pass): **77 passed, 0 failed** both times.
No existing test file's assertions were changed.
