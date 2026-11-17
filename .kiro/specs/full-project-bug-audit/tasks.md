# Implementation Plan

## Overview

This plan fixes the nineteen audited defects through bug-condition exploration, observation-first preservation tests, severity-ordered implementation, and complete local/live verification. Requirements and design are already approved; this phase changes only the implementation task plan.

## Task Dependency Graph

```json
{
  "waves": [
    {"wave": 1, "tasks": ["1"], "dependsOn": []},
    {"wave": 2, "tasks": ["2"], "dependsOn": ["1"]},
    {"wave": 3, "tasks": ["3"], "dependsOn": ["2"]},
    {"wave": 4, "tasks": ["4"], "dependsOn": ["3"]},
    {"wave": 5, "tasks": ["5"], "dependsOn": ["4"]},
    {"wave": 6, "tasks": ["6"], "dependsOn": ["5"]},
    {"wave": 7, "tasks": ["7"], "dependsOn": ["6"]},
    {"wave": 8, "tasks": ["8"], "dependsOn": ["7"]},
    {"wave": 9, "tasks": ["9"], "dependsOn": ["8"]},
    {"wave": 10, "tasks": ["10"], "dependsOn": ["9"]},
    {"wave": 11, "tasks": ["11"], "dependsOn": ["10"]},
    {"wave": 12, "tasks": ["12"], "dependsOn": ["11"]},
    {"wave": 13, "tasks": ["13"], "dependsOn": ["12"]},
    {"wave": 14, "tasks": ["14"], "dependsOn": ["13"]},
    {"wave": 15, "tasks": ["15"], "dependsOn": ["14"]},
    {"wave": 16, "tasks": ["16"], "dependsOn": ["15"]},
    {"wave": 17, "tasks": ["17"], "dependsOn": ["16"]},
    {"wave": 18, "tasks": ["18"], "dependsOn": ["17"]},
    {"wave": 19, "tasks": ["19"], "dependsOn": ["18"]},
    {"wave": 20, "tasks": ["20"], "dependsOn": ["19"]},
    {"wave": 21, "tasks": ["21"], "dependsOn": ["20"]},
    {"wave": 22, "tasks": ["22"], "dependsOn": ["21"]},
    {"wave": 23, "tasks": ["23"], "dependsOn": ["22"]},
    {"wave": 24, "tasks": ["24"], "dependsOn": ["23"]},
    {"wave": 25, "tasks": ["25"], "dependsOn": ["24"]},
    {"wave": 26, "tasks": ["26"], "dependsOn": ["25"]},
    {"wave": 27, "tasks": ["27"], "dependsOn": ["26"]},
    {"wave": 28, "tasks": ["28"], "dependsOn": ["27"]}
  ]
}
```

- Tasks 1 → 2 establish the unfixed bug and preservation baselines before any implementation.
- Critical path: 2 → 3 → 4. The mandatory real-Excel probe in task 4 gates every later fix.
- Severity-ordered implementation: 4 → 5 → 6 → 7 → 8 → 9 → 10 → 11 → 12 → 13 → 14 → 15 → 16 → 17 → 18 → 19 → 20 → 21 → 22.
- Final verification: tasks 3–22 → 23 → 24 → 25 → 26 → 27 → 28.
- Within each implementation task, coding precedes re-running the unchanged bug-condition test, which precedes its preservation check.

## Notes

- Do not redo or modify `bugfix.md` or `design.md`, and do not create commit or push work.
- Preserve the read-only Amazon-system posture, no-AI run path, KABIDEBA branding, additive status/preflight contracts, workbook layout, and every lane/concurrency/retry constraint named below.
- Leave a live-only smoke item open when its prerequisite is unavailable; never substitute an unsafe production action for verification.

## Tasks

- [ ] 1. Write bug-condition exploration tests before changing project code
  - **Property 1: Bug Condition** - Composite nineteen-defect audit counterexamples
  - **CRITICAL**: Write and run these focused tests against the UNFIXED code before implementing any fix; the expected failures prove that the documented defects are reachable. Do not weaken the assertions or change project code when they fail.
  - Exercise the defects in severity order and record at least one concrete counterexample per clause:
    - 1.1: add `TemplateIntegrationTests.test_remark_formula_guards_af_ag_errors` in `tests/test_polygon_workflow.py`; generate P2/P3 through `fill_template.fill`/`extend_working_sheet` and assert AF/AG comparisons are error-safe. On unfixed code, document that the guards are absent and AF/AG `#N/A` reaches Remark.
    - 1.2: extend `tests/test_cx_address_check.py` for in-zone 2-letter codes (`PB`, `DL`, `KL`, `TG`/`TL`, and the complete code set from the design), city/district tokens (`Bengaluru`, `Dehradun`), and contradictory code/PIN pairs. On unfixed code, document the false `does not serve` verdicts.
    - 1.3: extend `tests/test_sanitize_captures.py` with `SearchResults.csv` and `SearchResults (1).csv` in Downloads and assert `sanitize_captures.sanitize` preserves them. On unfixed code, document their deletion.
    - 1.4: create `tests/test_run_daily_phase_a.py::test_wipe_only_touches_ooj_lane_folders` and `tests/test_run_controller_phases.py::test_start_refuses_while_post_comments_running`; assert post-comments files survive and `RunController.start_run` refuses while posting is active. On unfixed code, document the deleted audit files and missing guard.
    - 1.5: in `tests/test_run_controller_phases.py`, use a completed fake Phase A process to assert non-zero exit or missing `tech_data.csv`/`tids.txt` becomes `scc_phase == "failed"` and SCC upload is rejected. On unfixed code, document the false paused state and accepted upload.
    - 1.6: in `tests/test_run_controller_phases.py` and `tests/test_post_comments.py`, assert `RunController.continue_after_scc` refuses failed Midway/Edge preflight and `post_comments_controller._pipeline_running` treats a pause as busy. On unfixed code, document the spawned Phase B and unblocked posting path.
    - 1.7: add `tests/test_run_daily_phase_a.py::test_restore_summary_keeps_phase_a_stages`; assert the first Phase B progress write retains Phase A `preflight`, `tickets`, and ticket counts. On unfixed code, document their loss.
    - 1.8: add `tests/test_run_controller_phases.py::test_stop_while_paused_resets_to_idle` and a focused local UI check for `app.js::tick`; assert a paused Stop succeeds and returns the controller to idle. On unfixed code, document HTTP 409/disabled Stop.
    - 1.9: use a local browser/status stub to load `tools/web/app.js` with `/status` reporting running and paused states; assert boot attaches, polls, renders state, and disables Start. On unfixed code, document the idle page and enabled Start.
    - 1.10: use the same local UI stub with a non-zero terminal `exit_code` and no `publication_blocked`; assert `renderFinish`/`tick` shows a failure and updates start-state. On unfixed code, document the stale running/paused banner.
    - 1.11: in `tests/test_run_daily_phase_a.py`, round-trip missing/unparseable ticket dates through the Phase A CSV path and `run_daily.load_tech_data`; assert empty fields become `None`. On unfixed code, document the literal `"None"` and parse failure.
    - 1.12: add `tests/test_polygon_workflow.py::test_approval_in_comments_written_without_polygon_rows`; call `fill_template.fill` with tech rows and no polygon rows and assert AI is still populated. On unfixed code, document missing AI.
    - 1.13: add `tests/test_web_controller.py::test_publish_tags_partial_when_ads_skipped` and a focused UI-text check; assert ADS-skipped output is `PARTIAL_` and names `ads` as skipped. On unfixed code, document the untagged name and contradictory text.
    - 1.14: extend `tests/test_polygon_workflow.py::test_polygon_status_is_written_row_by_row`; assert `fill_template.write_polygon_rows` writes headers in row 1 and digit-only TIDs as numbers. On unfixed code, document the blank first row and text TIDs.
    - 1.15: add `tests/test_approval_scan.py::test_scan_approvals_counts_failures`; make one `tickets_pull.scan_approvals` call fail and assert a blank value plus failure statistics/logging. On unfixed code, document the silent failure.
    - 1.16: create `tests/test_dsr_export_zero_rows.py`; drive `dsr_export.main` with zero rows and assert a canonical header-only CSV plus successful `run_daily.validate` acceptance. On unfixed code, document `IndexError`.
    - 1.17: in `tests/test_run_daily_phase_a.py`, drive the Phase A file writer with filtered rows for both `--max-tids` and `--tids-file`; assert `tech_data.csv` and `tids.txt` represent exactly the same ordered rows. On unfixed code, document missing or unfiltered Tech_Data.
    - 1.18: add `tests/test_auto_setup.py::test_pandas_is_required`; assert `auto_setup.REQUIRED_PACKAGES` includes `pandas`. On unfixed code, document its absence.
    - 1.19: add `tests/test_auto_setup.py::test_edge_probe_is_cached`; call `_playwright_edge_ok` and `environment_checklist` repeatedly with a counting probe and assert one launch. On unfixed code, document repeated launches.
  - Scope deterministic tests to the concrete cases above, run only the new focused tests, save the exact failing assertions/counterexamples in the implementation record, and mark this task complete only when every defect is represented and failures match the design rather than fixture mistakes.
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 1.10, 1.11, 1.12, 1.13, 1.14, 1.15, 1.16, 1.17, 1.18, 1.19, 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10, 2.11, 2.12, 2.13, 2.14, 2.15, 2.16, 2.17, 2.18, 2.19_

- [ ] 2. Write preservation property tests before implementing fixes
  - **Property 2: Preservation** - Existing OOJ behavior outside all nineteen bug conditions
  - **IMPORTANT**: Follow observation-first methodology. Run the UNFIXED implementation for non-buggy inputs, record the observed outputs, encode those outputs as parameterized/generative pytest properties using the existing test stack, and confirm the preservation tests pass before changing code. Do not add a new test dependency solely for property generation.
  - Capture the 13 already-correct column-P branch cases and assert identical values, visible formula text, verbatim embedded template P2 body, `ArrayFormula` type, retained `_xlfn.`, translated row references, and length below Excel's 8,192-character limit.
  - Preserve all existing `analyze_cx_address` outcomes for full state names, typo tolerance, blank/junk/thin addresses, missing/malformed PINs, and low/medium/unknown SIW confidence; assert CX advisory data never changes polygon Inside/Outside status or Remark.
  - Generate temporary capture/Downloads trees and assert only the defined sanitizer leftovers and OOJ-owned wipe paths are removed; current runs, `scc_enum_map.json`, post-comments data, unrelated Downloads/Desktop files, source, tests, docs, and the template remain untouched.
  - Cover the successful Phase A pause/resume path, valid SCC upload, ready preflight, unchanged ADS/partial flags, idle Stop behavior, live-process Stop behavior, default publication naming/gating, and a `get_status()` key-set superset containing all existing keys: `running`, `stopped`, `exit_code`, `partial`, `run_dir`, `stamp`, `log_tail`, `sources`, `summary`, `publication_blocked`, `publishable`, `workbook_downloadable`, `blank_sources`, `verification`, `desktop_file`, `scc_upload`, `scc_phase`, and `scc_validation`.
  - Assert the full Phase B contract remains SCC upload → EagleEye API concurrently with the browser lane → PNAMA then DSR serially on one Edge profile → GCRS `/grbi` sequentially (mcscli first, one Edge fallback) → polygon realtime with exactly one fetch per station and no retry → ADS with one file for all rows and `TrackingId` first → fill/recalc → read_back; Phase B must never wipe.
  - Assert `COLUMNS_TO_REMOVE == []`, no column deletion or shift, Tech_Data remains A–L, Working Sheet AG/AH/AI stays row-aligned to `tech_rows[r-2]`, Working Sheet never exceeds column 35, all 25 `FINAL_COLUMNS` occur exactly once, and `read_back` retains TID/polygon alignment and Excel-error checks.
  - Assert approval-scan success still makes exactly one `get-ticket` CORRESPONDENCE call per ticket, preserves first-seen `Site clear`/`Approved` labels, and round-trips values into Tech_Data L and Working Sheet AI.
  - Assert all Amazon-system interaction in the OOJ pipeline remains read-only, no AI enters the run path, every `preflight_state()`/`get_status()` change is additive, every `app.js` element id remains in `index.html`, and KABIDEBA branding remains in the title, header, footer, server banner, and published filename.
  - Confirm these properties and the recorded 94-test baseline pass on the UNFIXED code before proceeding.
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10, 3.11_

- [ ] 3. Fix critical defect 1.1: make the column-P AF/AG resolution error-safe

  - [ ] 3.1 Implement the surgical formula change in `tools/fill_template.py::extend_working_sheet`
    - Change only the local `_afag_resolve` string: wrap both AF verdict comparisons and the AG inside-jurisdiction comparison with `IFERROR(<comparison>,FALSE)`.
    - Keep the outer `ISNA(J)`, LM-search, and inner-`ISNA` branches unchanged; keep the template P2 body embedded verbatim three times and preserve `ArrayFormula`, `_xlfn.`, translation, and the under-8,192-character contract.
    - Do not alter any column, header, formula authority, or `COLUMNS_TO_REMOVE`.
    - _Bug_Condition: C_1_1 — a row reaches AF→AG→Valid while AF or AG is an Excel error_
    - _Expected_Behavior: errors mean “not this verdict,” so resolve to AF verdict, else AG inside text, else `Valid`, never `#N/A`_
    - _Preservation: non-error branches and the embedded template formula remain byte-for-byte/behaviorally equivalent as specified by design Property 20_
    - _Requirements: 1.1, 2.1, 3.1, 3.9_

  - [ ] 3.2 Verify the same defect-1.1 exploration test now passes
    - **Property 1: Expected Behavior** - Column P never propagates AF/AG errors
    - Re-run the defect-1.1 test created in task 1; do not replace it with a new or weaker test.
    - Assert P2 contains four `IFERROR(AF2="` and two `IFERROR(AG2="` occurrences, contains no unguarded `OR(AF2="`, contains the normalized original P2 body, retains `_xlfn.`, is an `ArrayFormula`, is below 8,192 characters, and translates guards to row 3.
    - _Requirements: 2.1_

  - [ ] 3.3 Verify formula preservation tests still pass
    - **Property 2: Preservation** - Non-error column-P branches and formula contract
    - Re-run the task-2 formula cases without changing their observations; require all 13 non-error branch results to remain identical.
    - _Requirements: 3.1, 3.9_

- [ ] 4. Run the mandatory 19-case real-Excel formula probe immediately after fix 1.1
  - **Property 1: Expected Behavior** - Real Excel validates every column-P branch
  - Build a temporary standalone workbook from the generated formula produced by a real-template `fill()`, translate it row by row with `openpyxl.formula.translate.Translator`, retain `ArrayFormula`, and use `=NA()` for error-cell inputs.
  - Cover exactly the 19 design cases: J `#N/A`; four ordinary LM-resolution branches; four LM branches with AF and/or AG `#N/A`; three ordinary inner-`#N/A` resolution branches; two inner-`#N/A` branches with AF and/or AG `#N/A`; and five pass-through outcomes (`Not Valid - Delivered`, `Invalid - Already RTOed`, `Not Valid - Doesn't belong to SWA`, station-list `Valid`, probability `Valid`).
  - Recalculate with installed desktop Excel through `tools/recalc_excel.ps1`, reopen with `data_only=True`, and require 19/19 expected values, zero failures, and no Remark `#N/A`; record the pass count and generated formula length.
  - Also verify the generated formula retains the embedded template body, `_xlfn.`, `ArrayFormula`, valid translated AF/AG references, intact string literals (including `HYT2`/`A.in`), and length below Excel's 8,192-character limit.
  - Keep the probe and recalculated copy temporary; task 26 removes them after evidence is recorded.
  - _Requirements: 2.1, 3.1, 3.9_

- [ ] 5. Fix medium defect 1.2: distinguish state codes from non-state tokens

  - [ ] 5.1 Update `tools/polygon_check.py::analyze_cx_address` and add `_STATE_CODE_ZONE`
    - Add the exact lowercase 2-letter state/UT-to-PIN-zone map specified in the design, including aliases such as `tg`/`tl`/`ts`, `or`/`od`, `ua`/`uk`, and `ct`/`cg`.
    - Resolve a recognized 2-letter token by exact code lookup only; for other tokens, determine whether they name any known state before allowing the PIN-zone rule to emit `does not serve`.
    - Skip only the zone-consistency rule for city/district/unknown tokens, then continue through the existing confidence verdicts; leave every other branch and message unchanged.
    - _Bug_Condition: C_1_2 — a well-formed PIN is paired with a 2-letter state code or a token that names no state_
    - _Expected_Behavior: exact code zones are checked; non-state tokens cannot create a false zone mismatch_
    - _Preservation: full-state-name, typo, malformed-address/PIN, and confidence behavior remains unchanged_
    - _Requirements: 1.2, 2.2, 3.2_

  - [ ] 5.2 Verify the same defect-1.2 exploration tests now pass
    - **Property 1: Expected Behavior** - Codes and non-state tokens use the correct zone semantics
    - Re-run the task-1 parameterized code set, `Bengaluru`/560076, `Dehradun`/248197, and contradictory `DL`/560001 plus `KA`/110001 cases.
    - _Requirements: 2.2_

  - [ ] 5.3 Verify CX preservation tests still pass
    - **Property 2: Preservation** - Existing full-state and confidence verdicts
    - Re-run all pre-existing `tests/test_cx_address_check.py` cases and the task-2 non-bug-condition corpus; confirm CX remains advisory only.
    - _Requirements: 3.2_

- [ ] 6. Fix medium defect 1.3: preserve manual SCC exports during sanitization

  - [ ] 6.1 Narrow `tools/sanitize_captures.py::_DOWNLOAD_LEFTOVER_RE`
    - Remove only the `SearchResults` alternative and update the adjacent comment to identify it as the operator-owned SCC upload.
    - Retain PNAMA/DSR QuickSight leftover matching and all capture scratch/sidecar cleanup.
    - _Bug_Condition: C_1_3 — startup/restart sanitization sees `SearchResults*.csv` in Downloads_
    - _Expected_Behavior: every manual SCC export survives startup and restart_
    - _Preservation: QuickSight leftovers, `_browser_downloads`, partial files, and atomic sidecars are still removed_
    - _Requirements: 1.3, 2.3, 3.3_

  - [ ] 6.2 Verify the same sanitizer exploration tests now pass
    - **Property 1: Expected Behavior** - Manual SCC exports are never deleted
    - Re-run the task-1 `SearchResults.csv`/`SearchResults (1).csv` cases and require both files to remain.
    - _Requirements: 2.3_

  - [ ] 6.3 Verify sanitizer preservation tests still pass
    - **Property 2: Preservation** - Scratch and QuickSight cleanup scope
    - Re-run temporary-tree tests and require the predicted PNAMA/DSR/scratch set, and only that set, to be removed.
    - _Requirements: 3.3, 3.11_

- [ ] 7. Fix medium defect 1.4: allow-list fresh-run cleanup and guard post-comments

  - [ ] 7.1 Restrict `tools/run_daily.py::wipe_old_runs` with `WIPE_SOURCE_DIRS`
    - Add the exact OOJ-owned folder tuple `tickets`, `eagle`, `pnama`, `dsr`, `polygons`, `polygon_local`, `ads`, and `scc_uploads`; iterate only those folders in the source-artifact wipe.
    - Keep prior-run deletion, current-run `resolve()` protection, `scc_enum_map.json`, Desktop date/name pruning, loud file errors, and all counters unchanged.
    - _Bug_Condition: C_1_4 — a fresh run starts while post-comments data exists_
    - _Expected_Behavior: OOJ artifacts are wiped while post-comments uploads/reports/summaries survive_
    - _Preservation: current run, lane artifacts, enum map, and Desktop pruning retain their existing semantics_
    - _Requirements: 1.4, 2.4, 3.4, 3.11_

  - [ ] 7.2 Add `RunController._post_comments_busy` and the `start_run` guard in `tools/web/run_controller.py`
    - Use a lazy import of `post_comments_controller` to avoid the import cycle; return a clear refusal before preflight/spawn when its controller is running.
    - Keep the guard additive and do not change any existing status/preflight key.
    - _Bug_Condition: C_1_4 — a fresh run is requested while a post-comments subprocess is live_
    - _Expected_Behavior: start is refused without touching captures_
    - _Preservation: ordinary starts and the read-only OOJ pipeline are unchanged_
    - _Requirements: 1.4, 2.4, 3.4, 3.8_

  - [ ] 7.3 Verify the same defect-1.4 exploration tests now pass
    - **Property 1: Expected Behavior** - Post-comments data and active work are protected
    - Re-run both task-1 tests; require OOJ files/old runs removed, current run/enum map/post-comments files retained, and `Popen` never called while post-comments is busy.
    - _Requirements: 2.4_

  - [ ] 7.4 Verify wipe/start preservation tests still pass
    - **Property 2: Preservation** - Existing OOJ cleanup and normal start behavior
    - Re-run task-2 generated file-tree cases and successful-start cases.
    - _Requirements: 3.4, 3.8, 3.11_

- [ ] 8. Fix pause/resume defect 1.5: make failed Phase A terminal

  - [ ] 8.1 Add failed-phase reconciliation to `tools/web/run_controller.py::RunController`
    - Add module-level `PAUSE_PHASES`, additive `_phase_error`, and idempotent `_reconcile_phase`; reconcile only a dead Phase A process currently in `awaiting_scc_upload`.
    - Treat non-zero exit or missing `tech_data.csv`/`tids.txt` as `_phase == "failed"`; call reconciliation from `get_status` and `upload_scc_csv`, reset the error on a fresh start, reject uploads after failure, and add only the new `phase_error` status key.
    - Leave exit-0 Phase A with both files in `awaiting_scc_upload`.
    - _Bug_Condition: C_1_5 — Phase A exits non-zero or lacks either handoff file_
    - _Expected_Behavior: status is terminal failed, upload is rejected, and the failure reason/exit code is exposed_
    - _Preservation: successful pause/upload and all existing status keys remain intact_
    - _Requirements: 1.5, 2.5, 3.5, 3.8_

  - [ ] 8.2 Verify the same Phase A failure exploration test now passes
    - **Property 1: Expected Behavior** - Failed Phase A cannot masquerade as paused
    - Re-run the task-1 fake-process cases for exit 2, exit 0 without files, and crash before `RUN_DIR=`; join the stdout reader before assertions and require failed status plus rejected upload.
    - _Requirements: 2.5_

  - [ ] 8.3 Verify successful Phase A preservation still passes
    - **Property 2: Preservation** - Exit 0 plus both files still pauses
    - Re-run the same controller fixture with both handoff files and require `awaiting_scc_upload`, valid upload acceptance, and unchanged status keys.
    - _Requirements: 3.5, 3.8_

- [ ] 9. Fix pause/resume defect 1.6: preflight Phase B and make pauses busy

  - [ ] 9.1 Update `RunController.continue_after_scc` and add `RunController.is_paused` in `tools/web/run_controller.py`
    - Re-run `preflight_state()` after resolving the SCC upload and before `Popen`; refuse invalid Midway or a busy Edge profile with the same clear reasons used by `start_run`.
    - Return to `awaiting_scc_upload` on refusal so the operator can fix readiness and upload again; do not consume, reorder, or rewrite Phase A data.
    - _Bug_Condition: C_1_6 — resume is requested while Midway is invalid or the Edge automation profile is busy_
    - _Expected_Behavior: Phase B does not spawn until readiness passes_
    - _Preservation: ready resume uses the same run directory, SCC path, and ADS/partial flags_
    - _Requirements: 1.6, 2.6, 3.5, 3.6_

  - [ ] 9.2 Update `tools/web/post_comments_controller.py::_pipeline_running`
    - Return true when the OOJ controller is either running or `is_paused()`; retain the existing defensive fallback.
    - _Bug_Condition: C_1_6 — post-comments starts during `awaiting_scc_upload`/`scc_uploaded`_
    - _Expected_Behavior: posting is blocked throughout the pause_
    - _Preservation: idle behavior and live-process detection remain unchanged_
    - _Requirements: 1.6, 2.6, 3.8_

  - [ ] 9.3 Verify the same resume/busy exploration tests now pass
    - **Property 1: Expected Behavior** - Resume is readiness-gated and pause owns the profile
    - Re-run Midway-false and Edge-busy variants with `Popen` forbidden, plus the paused post-comments guard test.
    - _Requirements: 2.6_

  - [ ] 9.4 Verify ready-resume preservation still passes
    - **Property 2: Preservation** - Valid upload resumes the unchanged Phase B contract
    - Re-run with ready preflight and assert the original command, run directory, flags, lane order, and serial/shared-profile constraints.
    - _Requirements: 3.5, 3.6, 3.8_

- [ ] 10. Fix pause/resume defect 1.7: restore Phase A summary in Phase B

  - [ ] 10.1 Add `Run.restore_summary` and invoke it in the resume branch of `tools/run_daily.py::main`
    - Tolerantly read the existing `summary.json`, require a dictionary with a `stages` dictionary, merge it under the run lock, and preserve a fresh summary on missing/invalid input.
    - Restore before Phase B's first `write_progress`; include the restore result in the existing resumed log line.
    - _Bug_Condition: C_1_7 — Phase B creates a fresh Run over a directory with Phase A stages_
    - _Expected_Behavior: `preflight`, `tickets`, and ticket counts survive every Phase B write_
    - _Preservation: later Phase B stages append normally and malformed/missing summaries remain non-fatal_
    - _Requirements: 1.7, 2.7, 3.5, 3.6_

  - [ ] 10.2 Verify the same summary-loss exploration test now passes
    - **Property 1: Expected Behavior** - Phase B retains Phase A progress
    - Re-run the task-1 valid-summary test through `mark_running("scc")` and re-read disk; also cover missing and invalid JSON.
    - _Requirements: 2.7_

  - [ ] 10.3 Verify summary preservation behavior still passes
    - **Property 2: Preservation** - Phase B stage updates remain additive
    - Require restored Phase A data and newly written Phase B stages to coexist without changing schema/stamp semantics.
    - _Requirements: 3.5, 3.6_

- [ ] 11. Fix pause/resume defect 1.8: enable Stop while paused

  - [ ] 11.1 Update `tools/web/run_controller.py::RunController.stop_run`
    - When no subprocess is live but `_phase` is in `PAUSE_PHASES`, set idle/stopped and return `stopped: true` with a paused-run reason; retain HTTP-409 semantics for a truly idle controller.
    - Do not change live-process-tree termination.
    - _Bug_Condition: C_1_8 — Stop is requested while paused_
    - _Expected_Behavior: the paused run is abandoned and the controller returns to idle_
    - _Preservation: idle Stop still returns false and live Stop still terminates the process tree_
    - _Requirements: 1.8, 2.8, 3.8_

  - [ ] 11.2 Update `tools/web/app.js::tick`
    - Enable `stop-btn` while either `status.running` or `status.scc_phase` is in `SCC_PAUSE_PHASES`; use the existing successful Stop handler to clear `runActive`, update text, and refresh preflight.
    - Keep existing element ids and KABIDEBA branding unchanged.
    - _Requirements: 1.8, 2.8, 3.8_

  - [ ] 11.3 Verify the same paused-Stop exploration test now passes
    - **Property 1: Expected Behavior** - Paused Stop succeeds and re-enables Start
    - Re-run the controller test and focused local UI interaction; require server success, idle phase, stopped status, enabled Start, and disabled Stop afterwards.
    - _Requirements: 2.8_

  - [ ] 11.4 Verify Stop preservation still passes
    - **Property 2: Preservation** - Idle and live Stop semantics
    - Re-run idle and live-process-tree tests without changing expected results.
    - _Requirements: 3.8_

- [ ] 12. Fix pause/resume defect 1.9: re-attach the UI after page load

  - [ ] 12.1 Add `attachToExistingRun` to the boot path in `tools/web/app.js`
    - Fetch `/status` at boot; for running or paused status set `runActive`, disable Start, set the matching re-attachment message, and call `startPolling`.
    - When no run is active, call `tick()` once so a completed result can render; keep idle preflight polling unchanged and race-safe by honoring `runActive`.
    - _Bug_Condition: C_1_9 — page loads while the controller is running or paused_
    - _Expected_Behavior: cards, logs, banner, SCC upload control, and disabled Start are restored_
    - _Preservation: an idle page remains startable and a finished result renders once_
    - _Requirements: 1.9, 2.9, 3.5, 3.8_

  - [ ] 12.2 Verify the same page-load exploration scenarios now pass
    - **Property 1: Expected Behavior** - Reload attaches instead of starting over
    - With a local status stub, reload once with `running: true` and once with `scc_phase: "awaiting_scc_upload"`; require polling/rendering and disabled Start, including the SCC input retaining its selected file across ticks.
    - _Requirements: 2.9_

  - [ ] 12.3 Verify idle/finished UI preservation still passes
    - **Property 2: Preservation** - Idle start and completed-result rendering
    - Load with idle and terminal-success payloads; require existing controls, ids, text precedence, and preflight behavior.
    - _Requirements: 3.5, 3.8_

- [ ] 13. Fix pause/resume defect 1.10: render every non-blocked terminal failure

  - [ ] 13.1 Update `tools/web/app.js::renderFinish` and `tick`
    - Add the non-zero-exit branch after `publication_blocked` and before publishable success; show exit code plus `phase_error` or failed stage names, use error styling, and keep download disabled.
    - End polling for a failed SCC phase even when no stamp was captured; update `start-state` for non-zero failures and re-enable Start after terminal failure.
    - Preserve precedence for running, paused, stopped-by-user, publication-blocked, publishable, and partial banners.
    - _Bug_Condition: C_1_10 — a run ends non-zero without `publication_blocked`_
    - _Expected_Behavior: an explicit failure banner and start-state replace stale running/paused text_
    - _Preservation: all existing terminal/banner branches retain their order and wording_
    - _Requirements: 1.10, 2.5, 2.10, 3.7, 3.8_

  - [ ] 13.2 Verify the same terminal-failure exploration scenarios now pass
    - **Property 1: Expected Behavior** - Every unblocked non-zero exit is visible
    - Feed local UI fixtures for Phase A failure without `RUN_DIR`, failed `fill_recalc`, failed `read_back`, and unhandled Phase B exit; require exit/stage detail, stopped polling, disabled download, and enabled Start.
    - _Requirements: 2.5, 2.10_

  - [ ] 13.3 Verify banner preservation still passes
    - **Property 2: Preservation** - Existing stopped, blocked, success, and partial banners
    - Re-run the task-2 status payloads and require existing precedence and publication gating.
    - _Requirements: 3.7, 3.8_

- [ ] 14. Fix low defect 1.11: round-trip blank ticket dates safely

  - [ ] 14.1 Add `tech_rows_for_csv` and use it in `tools/run_daily.py::pull_tickets`
    - Serialize `createdAt` and `lastUpdatedAt` as `""` when `None`, otherwise as their existing string form; retain every other field and row order.
    - Keep `load_tech_data`'s existing empty-string-to-`None` behavior.
    - _Bug_Condition: C_1_11 — either ticket date is missing or unparseable_
    - _Expected_Behavior: Phase A writes blanks and Phase B reads `None` without crashing_
    - _Preservation: parseable datetimes and all other Tech_Data values round-trip unchanged_
    - _Requirements: 1.11, 2.11, 3.9, 3.11_

  - [ ] 14.2 Verify the same blank-date exploration test now passes
    - **Property 1: Expected Behavior** - Missing dates survive the Phase A/B boundary
    - Re-run the task-1 CSV round trip and require both date fields to return `None`.
    - _Requirements: 2.11_

  - [ ] 14.3 Verify Tech_Data preservation still passes
    - **Property 2: Preservation** - Parseable dates, 12 columns, and row order
    - Re-run non-empty date cases and assert no field, header, or row-order change.
    - _Requirements: 3.9, 3.11_

- [ ] 15. Fix low defect 1.12: write Approval in Comments without polygon rows

  - [ ] 15.1 Move the AI writer in `tools/fill_template.py::fill`
    - After `extend_working_sheet`, write AI1 and AI2..n from `tech_rows` whenever tech rows exist; remove only the duplicate AI lines from the `if polygon_rows` block.
    - Leave AG/AH polygon logic, formulas, and layout unchanged; keep AI as column 35 and do not add it to `FINAL_COLUMNS`.
    - _Bug_Condition: C_1_12 — tech rows exist but polygon rows are empty/skipped_
    - _Expected_Behavior: AI header and values are complete in partial output_
    - _Preservation: AG remains its template formula without polygon data and AG/AH literals remain unchanged with polygon data_
    - _Requirements: 1.12, 2.12, 3.9, 3.10_

  - [ ] 15.2 Verify the same AI-without-polygon exploration test now passes
    - **Property 1: Expected Behavior** - Tech-row approvals always populate AI
    - Re-run with `polygon_rows=None`; require AI1/AI2/AI3, untouched AG formula, and untouched AH.
    - _Requirements: 2.12_

  - [ ] 15.3 Verify template writer preservation still passes
    - **Property 2: Preservation** - Polygon-present AG/AH/AI alignment
    - Re-run existing polygon integration tests and assert Working Sheet max column 35 with no shifts.
    - _Requirements: 3.9, 3.10_

- [ ] 16. Fix low defect 1.13: label ADS-skipped downloads as PARTIAL

  - [ ] 16.1 Thread partial state through `tools/web/run_controller.py::publish_to_desktop` and `desktop_path_for_current`
    - Add a defaulted `partial: bool = False`; include `PARTIAL_` when controller partial is true or `blank_sources(summary)` is non-empty; pass `self._partial` from the controller path.
    - Preserve ordinary naming, blank-source naming, `_existing_desktop_copy`, and publish/download gating.
    - _Bug_Condition: C_1_13 — ADS is skipped while `blank_sources` is empty_
    - _Expected_Behavior: published filename includes `PARTIAL_`_
    - _Preservation: approved and existing blank-source runs retain their names and gates_
    - _Requirements: 1.13, 2.13, 3.7_

  - [ ] 16.2 Correct partial wording in `tools/web/app.js` and `tools/web/index.html`
    - Add `ads` to the displayed blank/skipped list for a partial run, change the label to `Blank/skipped source(s)`, and state that a PARTIAL-labelled workbook will be produced.
    - Keep every referenced element id and all KABIDEBA branding intact.
    - _Requirements: 1.13, 2.13, 3.8_

  - [ ] 16.3 Verify the same ADS-skipped exploration tests now pass
    - **Property 1: Expected Behavior** - ADS-skipped output is consistently partial
    - Re-run publication and UI fixtures; require `_KABIDEBA1_PARTIAL_<stamp>.xlsx`, discovery by stamp, and `ads` in the banner.
    - _Requirements: 2.13_

  - [ ] 16.4 Verify publication preservation still passes
    - **Property 2: Preservation** - Approved, blank-source, and blocked publication semantics
    - Re-run existing web-controller publication tests using the default parameter and unchanged gates.
    - _Requirements: 3.7, 3.8_

- [ ] 17. Fix low defect 1.14: normalize the GCRS Agentspaces sheet

  - [ ] 17.1 Update `tools/fill_template.py::write_polygon_rows`
    - Clear data from row 2 onward, write the four headers explicitly into row 1, and append data from row 2.
    - Resolve the TID from supported keys and pass every digit-only value through `num()` so source-sheet lookups match numeric Working Sheet D.
    - Keep station, status, CX text, Working Sheet AG/AH literals, and row alignment unchanged.
    - _Bug_Condition: C_1_14 — polygon rows are written to the empty template agent sheet_
    - _Expected_Behavior: headers occupy row 1 and digit-only TIDs are numeric_
    - _Preservation: polygon status/CX values and TID ordering remain unchanged_
    - _Requirements: 1.14, 2.14, 3.9_

  - [ ] 17.2 Verify the same agent-sheet exploration test now passes
    - **Property 1: Expected Behavior** - Agent sheet is lookup-compatible
    - Require A1/D1 headers, first data in row 2, integer TIDs, and expected `max_row` for the fixture.
    - _Requirements: 2.14_

  - [ ] 17.3 Verify polygon writer preservation still passes
    - **Property 2: Preservation** - Agent/Working Sheet row identity
    - Re-run existing polygon alignment and `read_back` tests without changing their expected order or values.
    - _Requirements: 3.9, 3.11_

- [ ] 18. Fix low defect 1.15: report approval-scan failures

  - [ ] 18.1 Extend `tools/tickets_pull.py::scan_approvals` with optional stats and logging
    - Add the optional `stats` argument without changing the return type; count exceptions, retain the first exception type/message, continue writing `""` for failed tickets, and log one aggregate line after the scan.
    - Do not add retries or additional `get-ticket` calls.
    - _Bug_Condition: C_1_15 — one or more approval `get-ticket` calls raise_
    - _Expected_Behavior: blanks remain but failure count and first error are observable_
    - _Preservation: successful labels, first-seen order, and one call per ticket remain unchanged_
    - _Requirements: 1.15, 2.15, 3.10_

  - [ ] 18.2 Record stats in `tools/run_daily.py::pull_tickets`
    - Pass a stats dictionary and add `approval_scan_failures` plus non-empty `approval_scan_first_error` to `run.summary["tickets"]`; keep existing count keys and `_counts_for` behavior.
    - _Requirements: 1.15, 2.15, 3.8, 3.10_

  - [ ] 18.3 Verify the same approval-failure exploration test now passes
    - **Property 1: Expected Behavior** - Scan failures are distinguishable from no approval
    - Re-run one-failure/two-ticket fixture; require blank plus successful label, one failure, first error, aggregate log, and summary keys.
    - _Requirements: 2.15_

  - [ ] 18.4 Verify approval happy-path preservation still passes
    - **Property 2: Preservation** - Exactly one correspondence read per ticket
    - Re-run success tests and Tech_Data L/Working Sheet AI round trip; require no extra calls and unchanged labels.
    - _Requirements: 3.10_

- [ ] 19. Fix low defect 1.16: accept zero-row DSR exports

  - [ ] 19.1 Update `tools/dsr_export.py::main`
    - Select `list(rows[0].keys())` when rows exist and canonical `tracking_id`, `zone` fields otherwise; write the header and exit successfully.
    - Do not alter DSR browser retries, refresh count, serial sharing with PNAMA, or lane validation.
    - _Bug_Condition: C_1_16 — the export has a header context but zero data rows_
    - _Expected_Behavior: header-only output is accepted as blank Zone_
    - _Preservation: non-empty exports and partially missing TIDs validate exactly as before_
    - _Requirements: 1.16, 2.16, 3.6, 3.11_

  - [ ] 19.2 Verify the same zero-row DSR exploration test now passes
    - **Property 1: Expected Behavior** - DSR no-data is not a lane crash
    - Re-run `tests/test_dsr_export_zero_rows.py`; require exact header, no rows, exit 0, `zone_col == "zone"`, expected missing TIDs, and no extras.
    - _Requirements: 2.16_

  - [ ] 19.3 Verify DSR preservation still passes
    - **Property 2: Preservation** - Existing exports and browser-lane policy
    - Re-run non-empty validation/lane tests and assert PNAMA then DSR remains serial with at most one refresh retry (two attempts total).
    - _Requirements: 3.6, 3.11_

- [ ] 20. Fix low defect 1.17: freeze post-filter Phase A handoff files

  - [ ] 20.1 Add `write_phase_a_files` and use it after filtering in `tools/run_daily.py::main`
    - Write `tech_data.csv` through `tech_rows_for_csv` and `tids.txt` from the exact final ordered `tech`/`tids` values after `--max-tids` or `--tids-file` processing.
    - Keep `pull_tickets`' end-of-stage write, then deliberately rewrite the same content in the ordinary web path; do not change `_latest.txt` or `RUN_DIR=` timing.
    - _Bug_Condition: C_1_17 — Phase A uses `--max-tids` or `--tids-file`_
    - _Expected_Behavior: both handoff files describe exactly the rows Phase B will process_
    - _Preservation: ordinary web Phase A produces identical 12-column files and row order_
    - _Requirements: 1.17, 2.17, 3.5, 3.9, 3.11_

  - [ ] 20.2 Verify the same filtered-handoff exploration tests now pass
    - **Property 1: Expected Behavior** - CLI Phase A and Phase B use one row set
    - Re-run `--max-tids` and `--tids-file` fixtures; require exact TID identity/order across in-memory rows, `tech_data.csv`, and `tids.txt`.
    - _Requirements: 2.17_

  - [ ] 20.3 Verify ordinary Phase A preservation still passes
    - **Property 2: Preservation** - Web handoff files and pause contract
    - Re-run the unfiltered successful Phase A test; require `RUN_DIR=` first, `_latest.txt`, 12 Tech_Data columns, and unchanged pause behavior.
    - _Requirements: 3.5, 3.9, 3.11_

- [ ] 21. Fix low defect 1.18: install pandas on fresh machines

  - [ ] 21.1 Add `pandas` to `tools/auto_setup.py::REQUIRED_PACKAGES`
    - Preserve existing missing-package detection/install behavior; install only missing packages and do not pin, upgrade, or add unrelated dependencies.
    - _Bug_Condition: C_1_18 — environment setup runs without importable pandas_
    - _Expected_Behavior: post-comments runner is importable after setup_
    - _Preservation: already-complete environments perform no install and existing package handling is unchanged_
    - _Requirements: 1.18, 2.18, 3.8_

  - [ ] 21.2 Verify the same pandas exploration test now passes
    - **Property 1: Expected Behavior** - Fresh setup includes the post-comments dependency
    - Re-run the task-1 membership/missing-package fixture and require pandas alone when it is the only missing package.
    - _Requirements: 2.18_

  - [ ] 21.3 Verify setup preservation still passes
    - **Property 2: Preservation** - Existing dependency checks remain idempotent
    - Re-run all pre-existing `tests/test_auto_setup.py` cases.
    - _Requirements: 3.8_

- [ ] 22. Fix low defect 1.19: cache the Edge availability probe

  - [ ] 22.1 Split and cache the probe in `tools/auto_setup.py`
    - Move the current launch/close body into `_probe_playwright_edge`, add process-local `_EDGE_PROBE_RESULT: bool | None`, and keep `_playwright_edge_ok` as the cached public wrapper used by existing callers.
    - Reset the cache only after a successful `playwright install msedge`; do not cache Edge-profile-busy state or weaken Midway/readiness checks.
    - _Bug_Condition: C_1_19 — repeated `/preflight` polls or start readiness calls invoke the browser probe_
    - _Expected_Behavior: Edge launches at most once per server process_
    - _Preservation: the first probe still verifies availability and later explicit install can trigger one fresh probe_
    - _Requirements: 1.19, 2.19, 3.8_

  - [ ] 22.2 Verify the same Edge-probe exploration test now passes
    - **Property 1: Expected Behavior** - Polling does not repeatedly launch Edge
    - Re-run three wrapper calls plus two `environment_checklist` calls with a counting stub and require count 1; test cache reset after successful install.
    - _Requirements: 2.19_

  - [ ] 22.3 Verify preflight preservation still passes
    - **Property 2: Preservation** - Readiness keys and first-probe result
    - Re-run existing preflight/setup tests and assert every pre-existing key remains present with unchanged meaning.
    - _Requirements: 3.8_

- [ ] 23. Run the real-template fill integration test
  - Fill `Docs/Templete_OOJ Process_Date_September_KABIDEBA.xltx` with three ordered tech rows (including a DOC TID and populated `approval_in_comments`), matching SCC/polygon rows for all three, and `dsr_rows=None`.
  - Assert Working Sheet AG/AH/AI headers and row values are aligned to `tech_rows[r-2]`; AI remains populated from tech rows, AG/AH contain the intended polygon/CX literals, and column I remains the untouched template formula when DSR is absent.
  - Assert Tech_Data remains A–L, `L1 == "Put the Quip data "`, and L2:L4 contain the three approval values.
  - Assert `GCRS Agentspaces ` has its header in row 1, data in row 2 onward, and digit-only Tracking IDs stored numerically.
  - Assert Working Sheet `max_column == 35`, `COLUMNS_TO_REMOVE == []`, no header or column shifted, and each of the 25 `FINAL_COLUMNS` appears exactly once.
  - Assert P2:P4 remain `ArrayFormula` values, contain the normalized embedded template P2 body, retain `_xlfn.`, include translated error-safe AF/AG guards, and stay below Excel's 8,192-character formula limit; explicitly rerun the `read_back` P2 containment check.
  - Require TID identity/order, polygon alignment, and non-`#N/A` final-column validation to pass without weakening `read_back`.
  - _Requirements: 2.1, 2.12, 2.14, 3.1, 3.9, 3.10, 3.11_

- [ ] 24. Run final syntax and static contract checks
  - Run `C:\Windows\py.exe -3.13 -m py_compile` against every edited Python module: `tools/fill_template.py`, `tools/polygon_check.py`, `tools/sanitize_captures.py`, `tools/run_daily.py`, `tools/tickets_pull.py`, `tools/dsr_export.py`, `tools/auto_setup.py`, `tools/web/run_controller.py`, and `tools/web/post_comments_controller.py`.
  - Run `node --check tools/web/app.js` when Node is available; otherwise load the local page and require an error-free browser console before completion.
  - Verify every element id referenced by `app.js` still exists in `tools/web/index.html`, existing status/preflight keys are a subset of the post-fix keys, and `phase_error` is additive.
  - Search the final diff for accidental changes to PNAMA/DSR serial execution, GRBI sequencing, polygon fetch/retry logic, ADS upload shape, `COLUMNS_TO_REMOVE`, columns/formulas, read-only behavior, and KABIDEBA branding; revert any unrelated change.
  - _Requirements: 3.1, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10, 3.11_

- [ ] 25. Run the complete pytest suite in a background process
  - Start the exact command `C:\Windows\py.exe -3.13 -m pytest tests -q` as a background process, not as a foreground shell command; redirect output to a uniquely named temporary log if terminal output could truncate, then poll/read the process output until it exits.
  - Require the recorded baseline 94 tests plus every newly added test to pass with zero failures; do not accept a result that ran only a subset or silently omitted new test files.
  - If any test fails, fix the root cause, rerun the relevant focused test, then rerun this full background suite until it exits successfully with zero failures.
  - Record the final passed-test count and exit status for the implementation report.
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10, 2.11, 2.12, 2.13, 2.14, 2.15, 2.16, 2.17, 2.18, 2.19, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10, 3.11_

- [ ] 26. Clean up probes and temporary validation files
  - Delete only temporary real-Excel probe workbooks/recalculated copies, temporary CSV/file-tree fixtures, transient scripts, and redirected pytest/syntax logs created for this implementation.
  - Confirm no probe, scratch workbook, test log, browser download, `__pycache__`, or other generated validation artifact is included among intended changes.
  - Do not delete or alter the real template, source evidence, operator Downloads/Desktop files, post-comments audit data, existing capture runs, or any Amazon-system data.
  - _Requirements: 3.3, 3.4, 3.9, 3.11_

- [ ] 27. Complete the final operator smoke-test checklist

  - [ ] 27.1 Restart and verify the local KABIDEBA web app
    - Restart/confirm the app on `127.0.0.1:8000`; verify `/preflight` and `/status` respond, all old keys remain, `phase_error` is additive, the browser console is clean, and title/header/footer/server banner retain KABIDEBA credit.
    - While idle, confirm repeated `/preflight` polling does not repeatedly launch `msedge.exe`.
    - _Requirements: 2.19, 3.8_

  - [ ] 27.2 Verify Midway and SIM-T Phase A behavior without writes
    - With valid Midway, start a read-only Phase A and confirm `RUN_DIR=`/`_latest.txt`, `tech_data.csv`, `tids.txt`, ticket/approval counts, and the paused SCC state.
    - Use a safe induced local/preflight failure (not a production mutation) to confirm terminal failure text, upload rejection, stopped polling, and re-enabled Start.
    - Confirm no SIM-T comment is posted; when an approval read fails naturally or through a local fake, verify the aggregate log and summary fields.
    - _Requirements: 2.5, 2.7, 2.11, 2.15, 2.17, 3.5, 3.10_

  - [ ] 27.3 Verify the manual SCC handoff and pause UI
    - Export SCC `SearchResults.csv` through the required browser UI flow, restart the local server, and confirm the file remains in Downloads.
    - Upload it in SCC card 2; while paused verify page reload re-attaches, the selected file survives polls, Start stays disabled, and Stop abandons the run then re-enables Start.
    - Let Midway expire or open the automation Edge profile before resume; confirm the clear preflight refusal, then restore readiness and re-upload to continue.
    - _Requirements: 2.3, 2.5, 2.6, 2.8, 2.9, 3.5, 3.8_

  - [ ] 27.4 Verify QuickSight, GCRS/SIW, and polygon live-only behavior
    - Confirm PNAMA completes before DSR on the shared Edge profile, each retains its one-refresh/two-attempt policy, and a zero-row DSR result continues with blank Zone.
    - Confirm GCRS `/grbi` station work remains sequential, mcscli-first with Edge fallback only after browser lanes finish; verify in-zone state codes and city/district State values no longer produce false `does not serve`, while a genuine cross-zone state contradiction still does.
    - Confirm polygon data is realtime and each station polygon is fetched exactly once with no retry; any failed/not-found station must be reported immediately.
    - _Requirements: 2.2, 2.16, 3.2, 3.6_

  - [ ] 27.5 Verify ADS and final workbook behavior
    - For an approved run, confirm ADS receives one file for all rows with `TrackingId` as its first column; for an unapproved run, confirm the workbook is still downloadable with `_PARTIAL_` in its name and `ads` listed as skipped.
    - Open the recalculated output in real Excel: require zero Remark `#N/A`; rows with AF `#N/A` resolve to AG inside text or `Valid`; AG/AH/AI and Tech_Data L are complete/aligned; the GCRS Agentspaces header is row 1 with numeric TIDs; no column shifted and max column is 35.
    - Confirm the SIM-T card remains done with counts through Phase B, success/failure/blocked/partial banners are correct, and a non-zero Phase B failure names its exit/stage.
    - Keep all live Amazon interactions read-only and do not disable any safety protection.
    - _Requirements: 2.1, 2.7, 2.10, 2.12, 2.13, 2.14, 3.1, 3.6, 3.7, 3.9, 3.10, 3.11_

  - [ ] 27.6 Verify post-comments isolation without posting a live comment
    - Confirm prior uploads, dry-run summaries, and `post_comments_report.xlsx` files survive a fresh OOJ Start.
    - Using a local fake/dry-run or an already operator-authorized active process, confirm OOJ Start is refused while post-comments is running and post-comments is blocked while OOJ is paused; do not create an irreversible SIM-T comment for this smoke test.
    - _Requirements: 2.4, 2.6, 3.4, 3.8_

- [ ] 28. Checkpoint - confirm the audit fix is ready for implementation completion
  - Confirm tasks 1 and 2 used the unfixed code as their baseline, every defect-specific expected-behavior test was rerun unchanged after its fix, all preservation tests stayed green, the real-Excel probe passed 19/19, real-template integration passed, syntax checks passed, and the background full suite passed with baseline 94 plus new tests and zero failures.
  - Confirm temporary files are gone, only intended source/test/spec files changed, all hard constraints remain intact, and no commit or push was performed as part of this plan.
  - If any live-only checklist item cannot yet be exercised, leave that item open and report the exact prerequisite rather than claiming completion.
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10, 2.11, 2.12, 2.13, 2.14, 2.15, 2.16, 2.17, 2.18, 2.19, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10, 3.11_
