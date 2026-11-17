# Full Project Bug Audit Bugfix Design

## Overview

This design fixes the nineteen defects recorded in `bugfix.md` (1.1–1.19) with minimal, surgical, mostly additive changes to the OOJ automation (`tools/`, `tools/web/`, `tests/`). The fixes are ordered by severity:

- **1.1 (critical, data-corrupting)** — the combined column-P (Remark) formula built in `fill_template.extend_working_sheet` propagates a `#N/A` from AF (Station Transfer Validation) or AG (GCRS Agent Space) into the final Remark. In the real 2026-10-28 run 48 of 1494 remarks were published as `#N/A`. The fix wraps each AF/AG comparison in `IFERROR(…,FALSE)` so an error cell is treated as "not a transfer verdict" / "not inside", exactly as `remark_rules.decide_remark` treats a non-matching value. The embedded template body, the array-formula kind, `_xlfn.` and `read_back`'s containment check are untouched. **Both the bug and the fix were confirmed in real Excel during design** (standalone probe workbook recalculated through `recalc_excel.ps1`: unfixed formula fails 6 of 19 branch cases, every failure an AF/AG `#N/A`; the proposed formula passes 19 of 19, length 3,026 characters).
- **1.2–1.4 (medium)** — false "does not serve" CX MISMATCH verdicts for 2-letter state codes and city names (69 of 70 verdicts in the real run were false), the sanitizer deleting the operator's manual SCC export `SearchResults*.csv`, and the fresh-run wipe deleting the post-comments feature's audit data.
- **1.5–1.10 (low–medium)** — gaps in the new pause-and-resume state machine and its UI: Phase A failure shown as "paused", no preflight before Phase B, Phase A summary stages lost in Phase B, Stop unusable while paused, page reload detaches from a paused run, and no terminal failure banner.
- **1.11–1.19 (low)** — latent crashes (`"None"` date string, zero-row DSR export, `--max-tids`/`--tids-file` Phase B), incomplete output (AI column skipped without polygon rows, missing `PARTIAL_` tag), malformed source sheet (`GCRS Agentspaces` header on row 2, text TIDs), silent approval-scan failures, missing `pandas` in auto-setup, and a headless Edge launched on every `/preflight` poll.

Every fix follows the same rules: change the least code necessary, prefer adding over rewriting, never alter behaviour outside the defect, never remove or rename an existing `preflight_state()`/`get_status()` key, keep PNAMA/DSR serial on the one Edge profile, GRBI sequential, polygon single-fetch-no-retry, ADS single-file with first column `TrackingId`, EagleEye API-only, `COLUMNS_TO_REMOVE == []`, KABIDEBA branding, and the Working Sheet / Tech_Data column layout and formulas unshifted. Everything stays read-only against Amazon systems with no AI in the run path.

## Glossary

- **Bug_Condition (C)**: for each defect 1.k, the predicate `isBugCondition_k(input)` over the inputs that reach the defective code path (a Working Sheet row, a GCRS row, a server start, a fresh Start, a Phase A exit, …). The composite `isBugCondition(input)` is true when any `isBugCondition_k` holds.
- **Property (P)**: the expected behaviour clause 2.k that the fixed code SHALL satisfy whenever `isBugCondition_k` holds.
- **Preservation**: the regression-prevention clauses 3.1–3.11 — for every input where no bug condition holds, the fixed code SHALL behave exactly as the original code.
- **Template**: `Docs/Templete_OOJ Process_Date_September_KABIDEBA.xltx`, the operator's Excel workbook; its Working Sheet row-2 formulas are the sole remark authority.
- **Combined P formula**: the formula `fill_template.extend_working_sheet` writes into Working Sheet column P (Remark): `IF(ISNA(J2),"Invalid- Incorrect TID / TID Not Found", IF(ISNUMBER(SEARCH("lm to reattempt",<inner>)), <AF→AG→Valid>, IF(ISNA(<inner>), <AF→AG→Valid>, <inner>)))`, where `<inner>` is the template's own P2 body (an `ArrayFormula`, 914 characters, using `_xlfn.IFNA`/`_xlfn.IFS`) embedded verbatim three times.
- **`_afag_resolve` (AF→AG→Valid block)**: the sub-formula `IF(OR(AF2="<AF_A>",AF2="<AF_B>"),AF2,IF(AG2="<AG_INSIDE>",AG2,"Valid"))`. AF_A = "Invalid - Correct Station-A.in Shipment delivered - LM to Reattempt and Deliver", AF_B = "Invalid - LM to Reattempt - Sent to correct Station", AG_INSIDE = "Invalid - It is Inside Jurisdiction as per GCRS Tool".
- **AF / AG / J**: Working Sheet columns `Station Transfer Validation` (AF, depends on a Missort-Data lookup and is the `#N/A` error when PNAMA returned no row), `GCRS Agent Space` (AG, a VLOOKUP into `GCRS Agentspaces ` that `fill()` overwrites with the polygon status literal when polygon rows exist) and `State` (J, a VLOOKUP into the SCC sheet; `#N/A` when the TID is unknown).
- **`read_back` containment check**: `run_daily.read_back` requires the whitespace-stripped, `_xlfn.`-stripped template P2 body (leading `=` removed) to be a substring of the generated P2.
- **Phase A / Phase B**: `run_daily.py --stop-after-tickets` (preflight + SIM-T pull, exits 0 before SCC, writes `tech_data.csv`/`tids.txt`) and `run_daily.py --resume-dir <dir> --scc-csv <upload>` (SCC-onward pipeline in the same run directory).
- **Pause phases**: `RunController._phase` values `awaiting_scc_upload` and `scc_uploaded`; no live subprocess exists but the run is not finished. `app.js` mirrors them in `SCC_PAUSE_PHASES`.
- **PIN zone**: the first digit of an Indian 6-digit PIN; `polygon_check._PIN_ZONE_STATES` maps it to the states it covers.
- **Edge automation profile**: the single persistent Edge user-data directory (`ooj_paths.EDGE_PROFILE`) shared by the PNAMA/DSR lanes, the GCRS Edge fallback and the post-comments runner; two Playwright sessions must never open it at once.
- **PARTIAL tag**: the `PARTIAL_` segment in the published Desktop name `Templete_OOJ Process_<Month>_KABIDEBA1_[PARTIAL_]<stamp>.xlsx`.
- **`num()`**: `fill_template.num`, which writes digit-only strings as `int` so Excel lookups match.

## Bug Details

### Bug Condition

The nineteen defects share no single code path; the composite bug condition is the disjunction of nineteen input-specific conditions. Each sub-condition names the input it is evaluated on and the observable defect.

**Formal Specification:**
```
FUNCTION isBugCondition(input)
  INPUT: input of type WorkingSheetRow | GcrsRow | ServerStart | FreshStart | PhaseAExit
         | SccUpload | PhaseBStart | PausedRun | PageLoad | RunEnd | Ticket | FillCall
         | DesktopPublish | AgentSheetWrite | ApprovalScan | DsrExport | PhaseACli
         | EnvSetup | PreflightPoll
  OUTPUT: boolean

  -- 1.1  combined P formula reaches the AF->AG->Valid block with an error in AF or AG
  C_1_1 := input IS WorkingSheetRow
           AND NOT ISNA(input.J)
           AND (innerFormula(input) CONTAINS "lm to reattempt" OR ISNA(innerFormula(input)))
           AND (ISERROR(input.AF) OR ISERROR(input.AG))
  -- 1.2  CX zone rule evaluated on a State token that is a 2-letter code or not a state
  C_1_2 := input IS GcrsRow AND wellFormedPin(input.pin) AND input.state <> ""
           AND (isTwoLetterStateCode(input.state) OR NOT namesAnyKnownState(input.state))
  -- 1.3  sanitizer runs while the operator's manual SCC export sits in ~/Downloads
  C_1_3 := input IS ServerStart AND EXISTS f IN Downloads: f.name MATCHES "SearchResults*.csv"
  -- 1.4  fresh Start wipes captures/ while post-comments data exists or a posting run is live
  C_1_4 := input IS FreshStart AND (EXISTS f UNDER captures/post_comments OR postCommentsRunning())
  -- 1.5  Phase A exits non-zero or without its two output files
  C_1_5 := input IS PhaseAExit AND (input.exitCode <> 0
           OR NOT EXISTS runDir/tech_data.csv OR NOT EXISTS runDir/tids.txt)
  -- 1.6  resume requested while preflight would fail, or posting started during a pause
  C_1_6 := (input IS SccUpload AND (NOT midwayValid() OR edgeProfileBusy()))
           OR (input IS PostCommentsStart AND controllerPhase IN {awaiting_scc_upload, scc_uploaded})
  -- 1.7  Phase B starts in a run dir whose summary.json already has Phase A stages
  C_1_7 := input IS PhaseBStart AND EXISTS runDir/summary.json
           AND "tickets" IN summary.stages
  -- 1.8  operator wants to abandon a paused run
  C_1_8 := input IS PausedRun AND action = Stop
  -- 1.9  page (re)loads while the controller has a live or paused run
  C_1_9 := input IS PageLoad AND (controllerRunning() OR controllerPhase IN PAUSE_PHASES)
  -- 1.10 run ends non-zero without a publication_blocked marker
  C_1_10 := input IS RunEnd AND input.exitCode <> 0 AND summary.publication_blocked IS NULL
  -- 1.11 ticket with an unparseable date reaches pull_tickets' CSV write
  C_1_11 := input IS Ticket AND (parse_iso(input.createDate) IS NULL
            OR parse_iso(input.lastUpdatedDate) IS NULL)
  -- 1.12 fill() called with tech rows but no polygon rows
  C_1_12 := input IS FillCall AND input.tech_rows <> [] AND input.polygon_rows = []
  -- 1.13 download of an ADS-skipped run without blank sources
  C_1_13 := input IS DesktopPublish AND controller.partial = TRUE AND blank_sources(summary) = []
  -- 1.14 any write to the empty template agent sheet
  C_1_14 := input IS AgentSheetWrite AND input.rows <> []
  -- 1.15 at least one get-ticket call fails during the approval scan
  C_1_15 := input IS ApprovalScan AND EXISTS t IN input.tickets: getTicket(t) RAISES
  -- 1.16 DSR export with a header but zero data rows
  C_1_16 := input IS DsrExport AND input.rows = [] AND input.out IS NOT NULL
  -- 1.17 Phase A CLI with --max-tids or --tids-file
  C_1_17 := input IS PhaseACli AND (input.max_tids IS NOT NULL OR input.tids_file IS NOT NULL)
  -- 1.18 fresh machine without pandas
  C_1_18 := input IS EnvSetup AND NOT importable("pandas")
  -- 1.19 any /preflight poll or start_run readiness evaluation
  C_1_19 := input IS PreflightPoll

  RETURN C_1_1 OR C_1_2 OR ... OR C_1_19
END FUNCTION
```

### Examples

- **1.1** — real run `20261028_152733_335009`, 48 rows: inner formula → `#N/A` (ADS/O/N/R lookups missing), AF = `#N/A` (no PNAMA row), AG = "Invalid - It is Inside Jurisdiction as per GCRS Tool" (38 rows) or "Outside" (10 rows). Expected P: the AG text / "Valid". Actual P: `#N/A`. Reproduced in real Excel during design: with AF=`#N/A` & AG=INSIDE, AF=`#N/A` & AG="Outside", AF=`#N/A` & AG=`#N/A`, and AF="Interchange…" & AG=`#N/A`, both the LM path and the `#N/A` catch-all path return `#N/A` on the unfixed formula.
- **1.2** — TID 374734082992, PIN 560076, State "Bengaluru": expected "OK —…" (zone rule not applicable), actual "MISMATCH — PIN 560076 (zone 5) does not serve Bengaluru". TID 374803703025, PIN 248197, State "Dehradun": same pattern. 22 rows with State "PB" and zone-1 PINs: expected no mismatch, actual "does not serve PB". Design-time probe against the real tables: 16 observed values all become OK/skip; `560001`+"Delhi" and `560001`+"DL" stay MISMATCH.
- **1.3** — `C:\Users\kabideba\Downloads\SearchResults.csv` (the manual SCC export) is deleted by `Start OOJ Web.bat` or "Restart server" before it has been uploaded.
- **1.4** — every `captures/post_comments/<stamp>/` older than the last Phase A is empty (e.g. `20261013_210249_723757`); the one created after it still has its `post_comments_report.xlsx`.
- **1.5** — Phase A exits 2 at preflight: `/status` keeps `scc_phase: "awaiting_scc_upload"`, the page shows "Paused — upload SCC's export CSV…", Start and Stop are both disabled, and an upload would spawn a Phase B that dies in `load_tech_data` with `FileNotFoundError`.
- **1.7** — the real run's final `summary.json` has no `tickets` key and no `preflight`/`tickets` stage; the SIM-T card shows `pending` 0 % for all of Phase B.
- **1.13** — ADS-skipped run: Desktop file `Templete_OOJ Process_October_KABIDEBA1_<stamp>.xlsx` (no `PARTIAL_`), banner "Blank source(s): none", Start-card hint "no download".
- **1.14** — real output `GCRS Agentspaces `: row 1 `(None, None, None, None)`, row 2 `('Tracking ID','Station','GCRS Status','CX Address Check')`, data from row 3, Tracking IDs as text.
- **Edge case (1.16)** — zero-row DSR export: `rows[0].keys()` raises `IndexError`, the worker exits non-zero, `lane_dsr` fails although "no data" is accepted elsewhere as blank Zone.

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- Column P for every row whose AF holds a transfer verdict, whose AG holds the inside-jurisdiction text, whose inner template formula returns any non-`#N/A`/non-LM value, or whose J is `#N/A`, still yields the AF text / AG text / template value / "Invalid- Incorrect TID / TID Not Found" through a visible array formula that embeds the template body verbatim and keeps `_xlfn.` (3.1). Confirmed in real Excel: all 13 non-error branch cases are identical before and after the fix.
- `analyze_cx_address` keeps every existing branch: junk/thin → MISMATCH, no PIN → MISMATCH, malformed PIN → MISMATCH, full state name in zone (typos tolerated) → not a zone mismatch, full state name in another zone → MISMATCH "does not serve", low confidence / non-delivery-point → MISMATCH, medium / unknown confidence → NOT OK, clean → OK; the CX column never touches Inside/Outside or Remark (3.2).
- The sanitizer still deletes `_browser_downloads` contents, `.crdownload/.tmp/.partial` files and atomic-write sidecars under `tools/captures`, and `SWA_repeat_Prox_Pnam*`/`DSR_Raw_data*` leftovers in `~/Downloads`; nothing else in Downloads/Desktop (3.3).
- The fresh-run wipe still deletes all prior run folders and all OOJ per-source artifacts regardless of age, keeps the current run directory and `scc_enum_map.json`, and prunes only yesterday-or-older `Templete_OOJ Process_*.xlsx` exports (3.4).
- Phase A still exits 0 before SCC, prints `RUN_DIR=` first, writes `_latest.txt`, a 12-column `tech_data.csv` and `tids.txt`; the controller still enters `awaiting_scc_upload`, renders the upload control in SCC card 2 preserving the chosen file, accepts a valid CSV and auto-continues Phase B with the same flags (3.5).
- Phase B lane order and contracts: SCC upload → EagleEye (API, concurrent) → PNAMA → DSR (serial, 2 attempts, one refresh) → GCRS (sequential, mcscli first, Edge fallback once) → Polygon (single fetch, no retry, realtime) → ADS (single file, `TrackingId` first) → fill/recalc → read_back; never wipes (3.6).
- Publishable/downloadable gating, Desktop naming, `_existing_desktop_copy` by stamp, PARTIAL tag for blank sources, blocked runs neither publishable nor downloadable (3.7).
- Every existing `preflight_state()`/`get_status()` key, every element id `app.js` references, the KABIDEBA credit, and Stop aborting a live process tree (3.8).
- Sheet/column targets for every writer, `COLUMNS_TO_REMOVE == []`, no column deletion/shift, `read_back`'s 25-column header contract, TID and polygon alignment and the non-`#N/A` Excel-error check (3.9).
- `scan_approvals`: one `get-ticket` call per ticket, "Site clear"/"Approved" labelling in first-seen order, carried to every exploded TID row and round-tripped into Working Sheet AI and Tech_Data L (3.10).
- Data-bearing stages still fail loudly on locked/missing files; no Tech_Data row is ever dropped or reordered (3.11).

**Scope:**
All inputs that do NOT satisfy any `isBugCondition_k` are unaffected. In particular:
- Working Sheet rows whose AF and AG are both non-error values (the formula text changes, but Excel's result for those rows is provably identical because `IFERROR(x,FALSE)` equals `x` whenever `x` is not an error).
- GCRS rows whose State is a full, known state name, or blank.
- `~/Downloads` files other than `SearchResults*.csv` (the PNAMA/DSR leftovers are still removed).
- Files under `captures/runs`, `tickets`, `eagle`, `pnama`, `dsr`, `polygons`, `polygon_local`, `ads`, `scc_uploads` (still wiped).
- Phase A exits with code 0 and both output files present (still "paused").
- Runs that end with exit 0, or with `publication_blocked` (existing banners).
- Tickets with parseable dates; `fill()` calls that include polygon rows (AG/AH logic unchanged); runs with blank sources (already tagged `PARTIAL_`); DSR exports with rows; Phase A without `--max-tids`/`--tids-file`; machines with pandas; the first probe of Playwright Edge per process.

## Hypothesized Root Cause

Every cause below was established by reading the live source; 1.1 and 1.2 were additionally confirmed by design-time probes (real Excel recalculation for 1.1; the real `_PIN_ZONE_STATES`/`_fuzzy_same` tables for 1.2).

1. **1.1 — Excel error propagation through `OR`/`IF`** (`fill_template.extend_working_sheet`, `_afag_resolve`): `AF2="<text>"` evaluates to `#N/A` when AF2 is the `#N/A` error; `OR(#N/A, …)` is `#N/A`; `IF(#N/A, …)` is `#N/A`. The same holds for `AG2="…"`. The outer structure is already error-safe (`ISNA(J2)`, `ISNUMBER(SEARCH(…))` and `ISNA(<inner>)` all return booleans even for error arguments), so the `#N/A` can only originate inside `_afag_resolve`. `remark_rules.decide_remark` has no such hole because `_clean()` turns any value into a string before the comparison.
2. **1.2 — recognition heuristic too narrow and no "not applicable" outcome** (`polygon_check.analyze_cx_address`): `_fuzzy_same` matches only full state names (plus `up/uk/mp/ap/ts/tn/wb`) via equality, substring or a 0.80 difflib ratio. "pb" vs "punjab" (ratio 0.5), "dl" vs "delhi" (0.57) and any city name fail, and a failed match is treated as a contradiction instead of "the State field does not name a state". The substring rule also lets some 2-letter codes match the wrong zone (e.g. "ka" ⊂ "jammukashmir", "ap" ⊂ "madhyapradesh").
3. **1.3 — stale leftover pattern** (`sanitize_captures._DOWNLOAD_LEFTOVER_RE`): `SearchResults` remained in the regex after the SCC browser lane was retired and the file became the operator's manual work product.
4. **1.4 — wipe is a blanket over `captures/*`** (`run_daily.wipe_old_runs` step 2): every sub-directory except `runs` is treated as an OOJ lane folder, including `post_comments/`; and `RunController.start_run` never consults the post-comments controller.
5. **1.5 — phase set at spawn, never reconciled** (`RunController.start_run` sets `_phase = "awaiting_scc_upload"` unconditionally; `get_status`/`upload_scc_csv` only check `is_running()`).
6. **1.6 — preflight only in Phase A** (`continue_after_scc` spawns without `preflight_state()`; `post_comments_controller._pipeline_running` only reads `is_running()`, which is False during the pause).
7. **1.7 — fresh `Run` object in Phase B** (`Run.__init__` always starts with `stages: {}`; `main()`'s `--resume-dir` branch never reads the existing `summary.json`).
8. **1.8 — UI/server disagree with the controller** (`tick()` disables Stop when `!status.running`; `stop_run` resets the phase but returns `stopped: False`, which `server.py` maps to 409).
9. **1.9 — boot never polls `/status`** (`app.js` boot calls only `refreshPreflight()`; `runActive` starts False so Start is enabled).
10. **1.10 — `renderFinish` has no terminal-failure branch and `tick()` updates the start-state only on `status.stopped`** although `get_status()` already exposes `exit_code`.
11. **1.11 — `str(None)`** (`pull_tickets` stringifies dates with `str(...)`; `load_tech_data` calls `datetime.fromisoformat` on any non-empty string).
12. **1.12 — AI write nested in `if polygon_rows:`** (`fill_template.fill`) although its data comes from `tech_rows`.
13. **1.13 — tag derived from `blank_sources` only** (`publish_to_desktop`), and `desktop_path_for_current` never passes the controller's `_partial`; UI texts written before the PARTIAL download existed.
14. **1.14 — `ws["A1"]` materialises a cell before `ws.append`** (`write_polygon_rows`), so `append` lands on row 2; TIDs written with `str()` instead of `num()`.
15. **1.15 — bare `except Exception: approvals[tid] = ""`** with no counter/log (`tickets_pull.scan_approvals`).
16. **1.16 — `rows[0].keys()` on an empty list** (`dsr_export.main`).
17. **1.17 — `tech_data.csv` written inside `pull_tickets` before the `--max-tids` filter, and never by `tech_from_tid_file`.**
18. **1.18 — `REQUIRED_PACKAGES` predates `post_comments_runner`'s `import pandas as pd`.**
19. **1.19 — `_playwright_edge_ok` launches Edge on every call** and is reached from `preflight_state()` → `_environment_checklist_safe()` → `environment_checklist()` on every `/preflight` poll.

## Correctness Properties

Property 1: Bug Condition - Column P never evaluates to an error from AF/AG

_For any_ Working Sheet row that reaches the AF→AG→Valid block (inner template formula is an "LM to reattempt" text or `#N/A`) and whose AF and/or AG cell is an Excel error, the fixed combined P formula SHALL evaluate to the AF transfer verdict if AF holds one, else to the AG inside-jurisdiction text if AG holds it, else to "Valid" — never to `#N/A` — while still containing the template P2 body verbatim.

**Validates: Requirements 2.1**

Property 2: Bug Condition - 2-letter state codes and non-state tokens in the CX zone rule

_For any_ GCRS row with a well-formed PIN whose State field is a recognised 2-letter code, the fixed `analyze_cx_address` SHALL compare the code's zone with the PIN zone and SHALL NOT emit "does not serve" when they agree; and _for any_ row whose State field names no known state or code, it SHALL skip the zone rule and fall through to the confidence-based verdicts.

**Validates: Requirements 2.2**

Property 3: Bug Condition - Sanitizer leaves the manual SCC export alone

_For any_ server start or restart, the fixed sanitizer SHALL leave every `~/Downloads/SearchResults*.csv` untouched while still removing PNAMA/DSR QuickSight leftovers and `_browser_downloads` contents.

**Validates: Requirements 2.3**

Property 4: Bug Condition - Fresh-run wipe is confined to OOJ lane folders

_For any_ fresh Start, the fixed wipe SHALL delete only under `captures/runs` and the allow-listed lane folders (`tickets`, `eagle`, `pnama`, `dsr`, `polygons`, `polygon_local`, `ads`, `scc_uploads`) and SHALL leave every file under `captures/post_comments` intact; and `start_run` SHALL refuse while a post-comments subprocess is running.

**Validates: Requirements 2.4**

Property 5: Bug Condition - Phase A failure is terminal, not paused

_For any_ Phase A exit with a non-zero code, or with code 0 but without `tech_data.csv`/`tids.txt`, the fixed controller SHALL report `scc_phase == "failed"` with the exit code and a `phase_error`, SHALL reject `upload_scc_csv` with a reason naming the failure, and the page SHALL show the failure, stop polling and re-enable Start.

**Validates: Requirements 2.5**

Property 6: Bug Condition - Resume is preflighted and a pause counts as busy

_For any_ validated SCC upload, the fixed `continue_after_scc` SHALL evaluate `preflight_state()` and refuse (returning the run to `awaiting_scc_upload` with the same clear reason `start_run` uses) while Midway is invalid or the Edge profile is busy; and _for any_ post-comments start attempted while the controller is in a pause phase, `_pipeline_running()` SHALL be True.

**Validates: Requirements 2.6**

Property 7: Bug Condition - Phase B restores Phase A's summary

_For any_ Phase B start in a run directory holding a valid `summary.json`, the fixed `Run` SHALL contain the `preflight` and `tickets` stages and the top-level `tickets` counts before its first `write_progress()`, and they SHALL still be present in the final summary.

**Validates: Requirements 2.7**

Property 8: Bug Condition - Stop works while paused

_For any_ run in a pause phase, the fixed Stop button SHALL be enabled, `/stop` SHALL answer HTTP 200 with `stopped: true`, the controller SHALL return to `idle` with `stopped` reported, and Start SHALL be re-enabled afterwards.

**Validates: Requirements 2.8**

Property 9: Bug Condition - Page load re-attaches to an active or paused run

_For any_ page load while the controller is running or paused, the fixed boot SHALL start polling, render the cards/log/banner/upload control and keep Start disabled until the run finishes or is stopped.

**Validates: Requirements 2.9**

Property 10: Bug Condition - Terminal failure banner

_For any_ finished run whose `exit_code` is non-zero and whose summary has no `publication_blocked`, the fixed `renderFinish` SHALL show an explicit failure banner naming the exit code (and the failing stage or phase error when known) and `tick()` SHALL update the start-state.

**Validates: Requirements 2.10**

Property 11: Bug Condition - Blank dates round-trip as None

_For any_ ticket whose `createDate`/`lastUpdatedDate` parses to `None`, the fixed Phase A SHALL write an empty field to `tech_data.csv` and `load_tech_data` SHALL read it back as `None`.

**Validates: Requirements 2.11**

Property 12: Bug Condition - AI written whenever tech rows exist

_For any_ `fill()` call with non-empty `tech_rows` and empty `polygon_rows`, the fixed `fill` SHALL write the AI1 header and every row's `approval_in_comments` literal, leaving AG as the template formula and AH untouched.

**Validates: Requirements 2.12**

Property 13: Bug Condition - ADS-skipped runs are labelled PARTIAL

_For any_ download of a run started without ADS approval, the fixed publish SHALL name the file with `PARTIAL_`, the banner SHALL list `ads` as a blank/skipped source, and the Start-card hint and start-state SHALL say a PARTIAL-labelled download will be produced.

**Validates: Requirements 2.13**

Property 14: Bug Condition - Agent sheet header on row 1 with numeric TIDs

_For any_ non-empty polygon row list, the fixed `write_polygon_rows` SHALL place the four headers in row 1, data from row 2, and write digit-only Tracking IDs through `num()`.

**Validates: Requirements 2.14**

Property 15: Bug Condition - Approval-scan failures are counted and reported

_For any_ approval scan in which one or more `get-ticket` calls raise, the fixed scan SHALL still write `""` for those tickets, SHALL log the failure count and first error, and SHALL record the count in `run.summary["tickets"]`.

**Validates: Requirements 2.15**

Property 16: Bug Condition - Zero-row DSR export is "no data"

_For any_ DSR export with zero data rows, the fixed `dsr_export.main` SHALL write `--out` with the header `tracking_id,zone` and no rows and exit 0, and `lane_dsr` SHALL accept it as blank Zone.

**Validates: Requirements 2.16**

Property 17: Bug Condition - Phase A CLI writes post-filter Tech_Data

_For any_ Phase A run with `--tids-file` or `--max-tids`, the fixed Phase A SHALL write `tech_data.csv` containing exactly the rows Phase B will process.

**Validates: Requirements 2.17**

Property 18: Bug Condition - pandas is a required package

_For any_ `ensure_python_env` run on a machine without pandas, the fixed setup SHALL install it (and only it, if everything else is present).

**Validates: Requirements 2.18**

Property 19: Bug Condition - Edge probe runs at most once per process

_For any_ sequence of `/preflight` polls or `start_run` readiness evaluations within one server process, the fixed `_playwright_edge_ok` SHALL launch Edge at most once (cached result thereafter).

**Validates: Requirements 2.19**

Property 20: Preservation - Non-error AF/AG rows and the formula contract

_For any_ Working Sheet row whose AF and AG are not errors, or whose inner formula is a non-`#N/A`/non-LM value, or whose J is `#N/A`, the fixed combined P formula SHALL produce the same value as the original formula, and the generated P2 SHALL remain an `ArrayFormula` containing the template body, keeping `_xlfn.`, under 8,192 characters.

**Validates: Requirements 3.1**

Property 21: Preservation - Existing CX verdicts

_For any_ GCRS row whose State is a full known state name or blank, or whose address is junk/thin, lacks a PIN, has a malformed PIN, or has low/medium/unknown confidence, the fixed `analyze_cx_address` SHALL return the same verdict as the original, and the CX column SHALL not influence status or remark.

**Validates: Requirements 3.2**

Property 22: Preservation - Sanitizer scope

_For any_ file under `tools/captures/_browser_downloads`, partial/sidecar file under `tools/captures`, or `SWA_repeat_Prox_Pnam*`/`DSR_Raw_data*` file in `~/Downloads`, the fixed sanitizer SHALL still delete it; _for any_ other file it SHALL not.

**Validates: Requirements 3.3**

Property 23: Preservation - Wipe scope inside OOJ folders

_For any_ file under `captures/runs` (other than the current run and `_latest.txt`) or under an allow-listed lane folder (other than `scc_enum_map.json`), the fixed wipe SHALL delete it; Desktop pruning SHALL be unchanged.

**Validates: Requirements 3.4**

Property 24: Preservation - Successful Phase A and the pause contract

_For any_ Phase A that exits 0 with both output files, the fixed controller SHALL enter `awaiting_scc_upload`, accept a valid upload and auto-continue Phase B with the same flags exactly as before.

**Validates: Requirements 3.5**

Property 25: Preservation - Phase B lane order and contracts

_For any_ Phase B run, the fixed pipeline SHALL execute the same lanes in the same order with the same concurrency, retry, single-fetch and ADS constraints and SHALL never wipe.

**Validates: Requirements 3.6**

Property 26: Preservation - Publication gating and naming

_For any_ run summary, `is_publishable`/`is_downloadable`, the Desktop name for non-partial and blank-source runs, and `_existing_desktop_copy` SHALL return the same results as before.

**Validates: Requirements 3.7**

Property 27: Preservation - Status/preflight keys, element ids, branding, Stop on a live run

_For any_ `preflight_state()`/`get_status()` call, every pre-existing key SHALL still be present with unchanged meaning; every element id `app.js` references SHALL exist; the KABIDEBA credit SHALL be intact; Stop SHALL still kill a live process tree.

**Validates: Requirements 3.8**

Property 28: Preservation - Sheet/column targets and read_back contracts

_For any_ `fill()` call, every writer SHALL target the same sheet and columns as before (Tech_Data A–L, Working Sheet AG/AH/AI literals row-aligned to `tech_rows[r-2]`, no column removal or shift) and `read_back` SHALL enforce the same header, TID, polygon and Excel-error contracts.

**Validates: Requirements 3.9**

Property 29: Preservation - Approval scan happy path

_For any_ approval scan with no failures, the fixed `scan_approvals` SHALL return the same per-ticket labels as before (one `get-ticket` call per ticket, first-seen order) and the value SHALL still round-trip into AI and Tech_Data L.

**Validates: Requirements 3.10**

Property 30: Preservation - Loud failure on locked/missing data files

_For any_ locked or missing data-bearing file, the fixed sanitizer/wipe/`load_scc_csv`/lanes/fill SHALL still fail loudly (stage error, blocked or PARTIAL) and never drop or reorder a Tech_Data row.

**Validates: Requirements 3.11**

## Fix Implementation

### Changes Required

Assuming our root cause analysis is correct (1.1 and 1.2 are confirmed by probes, the rest by code reading), the changes below are listed in implementation order. Each entry gives the file/function, the precise change, why it is minimal and safe, what could break and its guard, and the verification.

#### Fix 1.1 (CRITICAL) — error-safe AF→AG→Valid block

**File**: `tools/fill_template.py` — **Function**: `extend_working_sheet` (the `_afag_resolve` string only)

**Change** (replace the two comparison groups; nothing else in the function moves):
```python
    # Error-safe: when AF (or AG) is the #N/A error (PNAMA / polygon lookup found no row),
    # AF2="…" itself evaluates to #N/A and would propagate through OR()/IF() into P.
    # IFERROR(<comparison>,FALSE) makes an error cell behave like remark_rules._clean():
    # "not a transfer verdict" / "not inside" -> fall through to the next check / "Valid".
    _afag_resolve = (
        f'IF(OR(IFERROR(AF2="{_af_a}",FALSE),IFERROR(AF2="{_af_b}",FALSE)),AF2,'
        f'IF(IFERROR(AG2="{_ag_inside}",FALSE),AG2,"Valid"))'
    )
```

**Why minimal and safe**: only the three comparisons gain an `IFERROR(…,FALSE)` wrapper. `IFERROR` is an Excel 2007 built-in, so no `_xlfn.` prefix is needed and the template's existing `_xlfn.` tokens are untouched. The inner template body is still embedded exactly three times (unchanged), so `read_back`'s containment check passes; the `ArrayFormula` kind is preserved by the unchanged `originals["P"] = (_p_kind, _combined_p)`; the formula grows by exactly 90 characters (measured: generated P2 2,936 → 3,026; row-1500 translation ≈3,176), far below Excel's 8,192 limit. For a non-error AF/AG, `IFERROR(x,FALSE) ≡ x`, so every previously-correct branch is provably unchanged. The `Translator` shifts `AF2`/`AG2` inside `IFERROR(...)` like any other reference (verified: row-1500 translation contains `IFERROR(AF1500="` and `IFERROR(AG1500="`, and the three string literals — including `"A.in"` — are intact).

**What could break / guard**: (a) an unintended change to the outer structure — guarded by the unit test that asserts `ISNA(J2)`, the `SEARCH("lm to reattempt", …)` wrapper and the containment contract; (b) Excel evaluating differently from the design assumption — guarded by the real-Excel probe (see Testing Strategy; already executed during design: 19/19 branch cases pass with this exact text).

**Verification**: new test `TemplateIntegrationTests.test_remark_formula_guards_af_ag_errors` in `tests/test_polygon_workflow.py`: `fill()` the real template with one tech row and no polygon rows, then assert on the P2 text — `p2_text.count('IFERROR(AF2="') == 4`, `p2_text.count('IFERROR(AG2="') == 2`, `'OR(AF2="' not in p2_text`, template body containment (same `norm()` as the existing test), `"_xlfn." in p2_text`, `isinstance(P2.value, ArrayFormula)`, `len(p2_text) < 8192`, and `'IFERROR(AF3="' in P3 text`. Plus the real-Excel branch probe (19 cases) after implementation.

#### Fix 1.2 — 2-letter state codes and "not a state" skip in the CX zone rule

**File**: `tools/polygon_check.py` — **Function**: `analyze_cx_address` (+ one new module constant next to `_PIN_ZONE_STATES`)

**Change 1** (additive constant):
```python
# 2-letter state/UT codes as they appear in the GCRS/SIW "State" field -> PIN zone digit.
# Looked up EXACTLY (no fuzzy/substring matching on two letters). TG/TL/TS are all seen
# for Telangana; OR/OD for Odisha; UA/UK for Uttarakhand; CT/CG for Chhattisgarh.
_STATE_CODE_ZONE = {
    "dl": "1", "hr": "1", "pb": "1", "hp": "1", "jk": "1", "la": "1", "ch": "1",
    "up": "2", "uk": "2", "ua": "2",
    "rj": "3", "gj": "3", "dd": "3", "dn": "3",
    "mh": "4", "mp": "4", "cg": "4", "ct": "4", "ga": "4",
    "ap": "5", "tg": "5", "ts": "5", "tl": "5", "ka": "5",
    "tn": "6", "kl": "6", "py": "6", "ld": "6",
    "wb": "7", "od": "7", "or": "7", "as": "7", "sk": "7", "ar": "7", "nl": "7", "mn": "7",
    "mz": "7", "tr": "7", "ml": "7", "an": "7",
    "br": "8", "jh": "8",
}
```
**Change 2** (replace the 5-line zone block; every other branch of the function is untouched):
```python
    zone_states = _PIN_ZONE_STATES.get(pin[0], set())
    st_norm = _addr_norm(state)
    if st_norm and zone_states:
        code_zone = _STATE_CODE_ZONE.get(st_norm)
        if code_zone is not None:
            # 2-letter code: exact zone lookup.
            names_state, state_in_zone = True, code_zone == pin[0]
        else:
            # Only a token that names SOME known state can contradict the PIN zone. A
            # city/district in the State field ("Bengaluru", "Dehradun") makes the rule
            # not applicable rather than a MISMATCH.
            names_state = any(_fuzzy_same(st_norm, z)
                              for names in _PIN_ZONE_STATES.values() for z in names)
            state_in_zone = any(_fuzzy_same(st_norm, z) for z in zone_states)
        if names_state and not state_in_zone:
            return (f"MISMATCH — PIN {pin} (zone {pin[0]}) does not serve "
                    f"{state} ({ref})")
```

**Why minimal and safe**: `_PIN_ZONE_STATES`, `_fuzzy_same`, every other verdict branch and all message texts are unchanged; the only new outcome is "skip" for tokens that match no known state, and exact zone resolution for 2-letter codes. Design-time probe against the real tables: all 16 State values observed in the real run become OK/skip; `560001`+"Delhi", `560001`+"DL", `110001`+"Karnataka" remain MISMATCH; "Karnataka"/"Karnatka"/"Maharashtra"/"Tamil Nadu"/"Kerala"/"Assam"/"Orissa"/"Telangana"/"Andhra Pradesh"/"Uttar Pradesh"/"Goa"/"Daman" unchanged. One intended consequence: a 2-letter code that previously matched the wrong zone by substring (e.g. "KA" with a zone-1 PIN, "ka" ⊂ "jammukashmir") is now correctly a MISMATCH.

**What could break / guard**: a state spelled in a way that matches no table entry and no code (e.g. "Uttaranchal") now skips instead of MISMATCH — this is the intended 2.2 behaviour and is covered by a test; the CX column is advisory and never feeds Inside/Outside or Remark.

**Verification**: extend `tests/test_cx_address_check.py` — (a) parametrised: each code in `_STATE_CODE_ZONE` with a PIN of its zone (`PB`/141001, `DL`/110001, `KL`/682001, `TG`,`TL`/500001, `BR`/800001, `MH`/400001, `HR`/122001, `RJ`/302001, `GJ`/380001, `KA`/560001, `TN`/600001, `WB`/700001, `UP`/226001, `UK`/248001, `MP`/462001, `CG`/492001, `OD`/751001, `AS`/781001, `JH`/834001, `HP`/171001, `JK`/180001, `CH`/160001, `GA`/403001, `PY`/605001, `AP`/520001) → verdict does not start with "MISMATCH"; (b) "Bengaluru"/560076 and "Dehradun"/248197 → "OK —"; (c) 560001 + "DL" → MISMATCH containing "does not serve" (the existing "Delhi" test stays); (d) 110001 + "KA" → MISMATCH.

#### Fix 1.3 — stop deleting `SearchResults*.csv`

**File**: `tools/sanitize_captures.py` — `_DOWNLOAD_LEFTOVER_RE`

**Change**: `r"^(SWA_repeat_Prox_Pnam|DSR_Raw_data).*\.(csv|xlsx|crdownload|tmp|partial)$"` (drop `|SearchResults`); update the comment to say SCC's export is the operator's manual upload and must survive.

**Why minimal and safe**: one alternative removed from a regex; the captures walk and the PNAMA/DSR patterns are unchanged.

**Verification**: in `tests/test_sanitize_captures.py` add `SearchResults.csv` and `SearchResults (1).csv` to the kept files of `test_never_touches_unrelated_downloads` and to `test_removes_scratch_but_keeps_real_results` (the `download_leftovers == 2` assertion still holds).

#### Fix 1.4 — allow-listed wipe and post-comments guard

**File**: `tools/run_daily.py` — **Function**: `wipe_old_runs` (+ new module constant)

**Change**:
```python
# Per-source capture folders the OOJ lanes own; wiped at every fresh Start. Anything else
# under captures/ (notably post_comments/, the gated SIM-T comment-posting feature's uploads,
# dry-run summaries and post_comments_report.xlsx audit files) is NOT pipeline data and
# must survive the wipe.
WIPE_SOURCE_DIRS = ("tickets", "eagle", "pnama", "dsr", "polygons", "polygon_local", "ads", "scc_uploads")
```
and in step 2 replace `for source_dir in CAPTURES.iterdir(): if not source_dir.is_dir() or source_dir.name == "runs": continue` with `for name in WIPE_SOURCE_DIRS: source_dir = CAPTURES / name; if not source_dir.is_dir(): continue` (the inner `rglob` loop, the `scc_enum_map.json` skip and the counters stay as they are). Update the docstring/log text to say "OOJ lane folders".

**File**: `tools/web/run_controller.py` — **Class**: `RunController`

**Change** (additive):
```python
    @staticmethod
    def _post_comments_busy() -> bool:
        """True while the gated SIM-T comment-posting subprocess is live. Lazy import:
        post_comments_controller imports this module, so a top-level import would cycle.
        Fail-open: if the feature module cannot be imported nothing of it can be running."""
        try:
            import post_comments_controller as pcc
            return bool(pcc.CONTROLLER.is_running())
        except Exception:
            return False
```
and in `start_run`, right after the `is_running()` check: `if self._post_comments_busy(): return {"started": False, "reason": "A post-comments run is in progress; wait for it to finish before starting a run"}`.

**Why minimal and safe**: the wipe keeps its structure (runs loop, lane loop, Desktop prune) and only changes which directories the lane loop visits; `runs/` handling, the current-dir protection, `scc_enum_map.json` and Desktop pruning are untouched. The guard is one early return mirroring the existing `is_running()` guard.

**What could break / guard**: a lane folder not in the allow-list would stop being wiped — the list was built from every `captures/<folder>` the live modules write (`ads_run`, `dsr_export`, `pnama_export`, `polygon_check`, `tickets_pull`, `tid_polygon_checker`, `upload_scc_csv`) plus `eagle`; `_archive` (empty) and `post_comments` are deliberately excluded.

**Verification**: new `tests/test_run_daily_phase_a.py::test_wipe_only_touches_ooj_lane_folders` — monkeypatch `rd.CAPTURES`, `rd.RUNS`, `rd.DESKTOP` to a tmp tree with files in `tickets/`, `pnama/x/_browser_downloads/`, `scc_uploads/`, `runs/old/`, `runs/current/`, `post_comments/uploads/a.xlsx`, `post_comments/<stamp>/post_comments_report.xlsx`, `pnama/scc_enum_map.json`; call `wipe_old_runs(current)`; assert lane files and `runs/old` gone, `runs/current`, `scc_enum_map.json` and both post-comments files present. New `tests/test_run_controller_phases.py::test_start_refuses_while_post_comments_running` — monkeypatch `rc.RunController._post_comments_busy` → `True`, `rc.preflight_state` → ready, `rc.subprocess.Popen` → raise `AssertionError`; `start_run(True)` returns `started False` with "post-comments" in the reason.

#### Fix 1.5 — Phase A failure becomes a terminal `failed` phase

**File**: `tools/web/run_controller.py` — **Class**: `RunController` (new method + 3 call sites + 1 status key)

**Change** (additive):
```python
PAUSE_PHASES = ("awaiting_scc_upload", "scc_uploaded")  # module constant, mirrors app.js

    def __init__(...):
        ...
        self._phase_error: str | None = None     # set only when _phase == "failed"

    def _reconcile_phase(self) -> None:
        """Phase A has exited: decide paused vs failed. Idempotent; takes no lock (callers
        either hold it or, like get_status, already read _phase unlocked)."""
        if self._phase != "awaiting_scc_upload" or self._proc is None or self.is_running():
            return
        code = self._proc.returncode
        run_dir = self._resolve_run_dir()
        files_ok = run_dir is not None and (run_dir / "tech_data.csv").exists() and (run_dir / "tids.txt").exists()
        if code != 0 or not files_ok:
            self._phase = "failed"
            self._phase_error = (f"Phase A exited with code {code}" if code != 0
                                 else "Phase A exited without writing tech_data.csv/tids.txt")
```
Call sites: first line of `get_status()`; inside `upload_scc_csv` right after acquiring the lock — then `if self._phase == "failed": return {"uploaded": False, "reason": f"{self._phase_error}; start a new run"}` before the existing guard; `start_run` resets `self._phase_error = None` with the other per-run resets. `get_status()` gains `"phase_error": self._phase_error` (new key; `scc_phase` may now carry the new value `"failed"`).

**Why minimal and safe**: no existing phase is renamed; the reconciliation only fires in `awaiting_scc_upload` with a dead process, so a healthy pause (exit 0 + both files) is untouched; a Stop during Phase A (taskkill → exit 1) also lands in `failed`, which correctly blocks uploads while the page still shows "Run stopped by you" because `renderFinish` checks `status.stopped` first.

**Verification**: `tests/test_run_controller_phases.py` — a fake `Popen` (attributes `pid`, `returncode`, `poll()`, `stdout` = `io.StringIO("RUN_DIR=<tmp>\n")`), `rc.preflight_state` → ready, `rc.RUNS` → tmp. (a) exit 2 without files → `get_status()["scc_phase"] == "failed"`, `phase_error` contains "code 2", `upload_scc_csv(b"x", "x.csv")["uploaded"] is False` with "Phase A" in the reason; (b) exit 0 with `tech_data.csv`+`tids.txt` written → phase stays `awaiting_scc_upload` and a valid CSV upload is accepted (preservation, 3.5). Join `controller._reader` before asserting so `RUN_DIR` is captured deterministically.

#### Fix 1.6 — preflight before Phase B; pause counts as busy for post-comments

**File**: `tools/web/run_controller.py` — **Functions**: `continue_after_scc`, new `is_paused`

**Change**: in `continue_after_scc`, after the `run_dir`/upload-path check and before building `cmd`:
```python
            pre = preflight_state()
            refusal = None
            if not pre["midway_ok"]:
                refusal = "Midway session not granted yet"
            elif pre["edge_profile_busy"]:
                refusal = "The automation Edge profile is open; close that window and retry"
            if refusal:
                # Stay paused (not failed): once the operator fixes the cause they re-click
                # Upload SCC CSV and this path runs again.
                self._phase = "awaiting_scc_upload"
                return {"started": False, "reason": refusal + " — fix it, then upload the SCC CSV again", "preflight": pre}
```
and the additive public helper:
```python
    def is_paused(self) -> bool:
        """True between Phase A and Phase B (no live subprocess, run not finished)."""
        self._reconcile_phase()
        return self._phase in PAUSE_PHASES
```
**File**: `tools/web/post_comments_controller.py` — **Function**: `_pipeline_running` → `return bool(rc.CONTROLLER.is_running() or rc.CONTROLLER.is_paused())` (same `except` fallback).

**Why minimal and safe**: the two refusal reasons are the exact strings `start_run` already uses; the phase is returned to `awaiting_scc_upload` so the existing SCC card (whose file input keeps the selected file across re-renders) is the retry path — no new endpoint or UI element. `app.js sccUpload()` already surfaces `data.continue.reason` as "Upload accepted but the run could not resume: …". `is_paused()` reads existing state only; tests that monkeypatch `rc.CONTROLLER.is_running` keep passing because the fresh controller's phase is `idle`.

**Verification**: `tests/test_run_controller_phases.py::test_continue_refuses_when_preflight_fails` — controller in `scc_uploaded` with a tmp run dir and upload path, `rc.preflight_state` → `midway_ok False`, `Popen` → `AssertionError`; assert `started False`, "Midway" in reason, `_phase == "awaiting_scc_upload"`; variant with `edge_profile_busy True`. `tests/test_post_comments.py::test_confirm_blocked_while_pipeline_paused` — monkeypatch `rc.CONTROLLER.is_running` → False and `rc.CONTROLLER.is_paused` → True; `confirm_and_post()` is blocked with the pipeline reason.

#### Fix 1.7 — Phase B restores Phase A's summary

**File**: `tools/run_daily.py` — **Class**: `Run` (new method) and `main()` Phase B branch (one call)

**Change** (additive):
```python
    def restore_summary(self) -> bool:
        """Phase B: merge the summary.json Phase A left in this run directory (preflight/
        tickets stages, tickets counts) so progress never regresses to pending. Tolerant:
        a missing/invalid file keeps the fresh summary and returns False."""
        path = self.dir / "summary.json"
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        if not isinstance(previous, dict) or not isinstance(previous.get("stages"), dict):
            return False
        with self._lock:
            self.summary.update(previous)
        return True
```
In `main()` after `run = Run(Path(args.resume_dir).name, directory=Path(args.resume_dir))` and the `RUN_DIR=` print: `restored = run.restore_summary()` and include it in the existing "RESUMED (Phase B)" log line.

**Why minimal and safe**: `update()` keeps `stamp`/`schema_version` (identical values) and replaces the empty `stages` with Phase A's; Phase B stages are added on top as before; nothing else in Phase B changes.

**Verification**: `tests/test_run_daily_phase_a.py::test_restore_summary_keeps_phase_a_stages` — write a Phase A style `summary.json` into a tmp dir, construct `Run("stamp", directory=tmp)`, `restore_summary()` → True, `summary["stages"]["tickets"]["ok"] is True`, `summary["tickets"]["rows"]` preserved; then `mark_running("scc")` + re-read the file → both `tickets` and `scc` stages present. Negative cases: missing file → False; invalid JSON → False; fresh summary intact.

#### Fix 1.8 — Stop usable while paused

**File**: `tools/web/run_controller.py` — **Function**: `stop_run` (paused branch only)
```python
            if not self.is_running():
                if self._phase in PAUSE_PHASES:
                    # Abandon the paused run (Phase A already exited cleanly).
                    self._phase = "idle"
                    self._stopped = True
                    return {"stopped": True, "reason": "paused run abandoned"}
                self._phase = "idle"
                return {"stopped": False, "reason": "No run is in progress"}
```
**File**: `tools/web/app.js` — `tick()`: `$("stop-btn").disabled = !(status.running || SCC_PAUSE_PHASES.includes(status.scc_phase));`. The existing Stop click handler already sets `runActive = false`, writes the start-state and calls `refreshPreflight()` on a 200.

**Why minimal and safe**: `server.py`'s `/stop` mapping (200 when `stopped`) is unchanged; a `/stop` with nothing paused or running still returns 409 (`stopped: false`), so the no-run semantics are preserved; setting `_stopped` makes the next `tick()` take its existing finish branch and `renderFinish` show "Run stopped by you".

**Verification**: `tests/test_run_controller_phases.py::test_stop_while_paused_resets_to_idle` — controller with `_phase = "awaiting_scc_upload"`, `_proc = None`, `_ever_started = True`: `stop_run()` → `{"stopped": True, ...}`, `_phase == "idle"`, `get_status()["stopped"] is True`; and an idle controller → `stop_run()["stopped"] is False`. UI: manual smoke test (see Integration Tests).

#### Fix 1.9 — page load re-attaches to an active/paused run

**File**: `tools/web/app.js` — boot block
```js
async function attachToExistingRun() {
  let status;
  try { status = await getJSON("/status"); } catch (e) { return; }
  if (status.running || SCC_PAUSE_PHASES.includes(status.scc_phase)) {
    runActive = true;
    $("start-btn").disabled = true;
    $("start-state").textContent = status.running
      ? "Re-attached to the run in progress\u2026"
      : "Re-attached to the paused run \u2014 upload the SCC CSV in step 2 to resume.";
    startPolling();
  } else {
    tick();  // render a finished run's result (if any) once
  }
}
refreshPreflight();
attachToExistingRun();
setInterval(() => { if (!runActive) refreshPreflight(); }, 5000);
```
**Why minimal and safe**: `refreshPreflight()` only enables Start when `!runActive`, so whichever of the two boot fetches resolves first, Start ends disabled when a run is attached; with no run the extra `tick()` renders the same idle cards as today.

**Verification**: manual smoke test — start a run, reload the page during Phase A and again while paused; the cards/log/paused banner/upload control reappear and Start stays disabled. (Pure browser behaviour; no pytest coverage possible.)

#### Fix 1.10 — terminal failure banner

**File**: `tools/web/app.js` — `renderFinish` (new branch after `publication_blocked`, before `publishable`) and `tick()`
```js
  const failedExit = !status.running && status.exit_code !== null && status.exit_code !== undefined && status.exit_code !== 0;
  if (failedExit) {
    const stages = (status.summary && status.summary.stages) || {};
    const blanks = status.blank_sources || [];
    const failedStages = Object.keys(stages).filter((k) => stages[k] && stages[k].ok === false && !blanks.includes(k));
    const detail = status.phase_error || (failedStages.length ? "failed stage(s): " + failedStages.join(", ") : "");
    banner.className = "banner error";
    banner.textContent = "Run failed (exit " + status.exit_code + ")" + (detail ? " \u2014 " + detail : "") + ". See the live log for details.";
    dlBtn.disabled = true;
    return;
  }
```
In `tick()`: extend the finish condition to `(status.stamp || status.stopped || status.scc_phase === "failed")` so a Phase A crash before `RUN_DIR=` also ends polling, and add `else if (failedExit) $("start-state").textContent = "Run failed (exit N). Check the log, fix the cause, then click Start run again.";` next to the existing `status.stopped` text.

**Why minimal and safe**: the branch sits after the pause/running/stopped/blocked checks, so every existing banner keeps precedence; `exit_code` is already in `get_status()`.

**Verification**: manual smoke test with a forced Phase B failure is not possible without live systems; the logic is deterministic and reviewed against `get_status()`'s payload; a browser smoke test of the Phase A failure path should be done the first time one occurs (see "Not live-verifiable").

#### Fix 1.11 + 1.17 — blank dates and post-filter `tech_data.csv`

**File**: `tools/run_daily.py` — new helpers, `pull_tickets` (one expression), `main()` Phase A (one call)
```python
def tech_rows_for_csv(rows: list[dict]) -> list[dict]:
    """Stringify the two datetime fields for tech_data.csv, blank (not "None") when
    missing, so load_tech_data() reads them back as datetime / None."""
    return [{**r,
             "createdAt": "" if r.get("createdAt") is None else str(r["createdAt"]),
             "lastUpdatedAt": "" if r.get("lastUpdatedAt") is None else str(r["lastUpdatedAt"])}
            for r in rows]


def write_phase_a_files(run: "Run", tech: list[dict], tids: list[str]) -> None:
    """Freeze exactly the post-filter rows/TIDs Phase B will load (two-phase contract)."""
    run.save_csv("tech_data.csv", tech_rows_for_csv(tech))
    (run.dir / "tids.txt").write_text("\n".join(tids), encoding="utf-8")
```
`pull_tickets`: `run.save_csv("tech_data.csv", tech_rows_for_csv(rows))` (keeps the file appearing at the end of the tickets stage, now with blank dates). `main()` Phase A: replace `(run.dir / "tids.txt").write_text(...)` with `write_phase_a_files(run, tech, tids)` after the `--max-tids` filter.

**Why minimal and safe**: in the web flow (no `--max-tids`/`--tids-file`) the second write rewrites identical content; `load_tech_data` already maps `""` → `None`.

**Verification**: `tests/test_run_daily_phase_a.py` — `tech_rows_for_csv([{..., "createdAt": None, "lastUpdatedAt": None}])` → `""`; round trip through `Run.save_csv` + `load_tech_data` → `None`; `write_phase_a_files(run, filtered_tech, tids)` on a tmp `Run` → `load_tech_data` returns exactly `filtered_tech`'s TIDs in order and `tids.txt` matches.

#### Fix 1.12 — AI written whenever tech rows exist

**File**: `tools/fill_template.py` — **Function**: `fill` (move 4 lines out of the `if polygon_rows:` block)
```python
    working_sheet = wb[SHEET_WORK]
    if tech_rows:
        # Column 35 (AI) = Approval in Comments — from tech_rows, so it is written whether or
        # not the polygon lane produced rows (AG/AH below still need polygon rows).
        if working_sheet.cell(row=1, column=35).value in (None, ""):
            set_literal_cell(working_sheet.cell(row=1, column=35), "Approval in Comments")
        for row_number, tech_row in enumerate(tech_rows, start=2):
            set_literal_cell(working_sheet.cell(row=row_number, column=35), tech_row.get("approval_in_comments") or "")
    if polygon_rows:
        ... (AH header, AG/AH per-row literals exactly as today, AI lines removed)
```
**Why minimal and safe**: must stay after `extend_working_sheet` (which blanks rows 2..n before translating), as today; AG/AH logic is untouched; "Approval in Comments" is not in `FINAL_COLUMNS`, so `read_back`'s header contract is unaffected.

**Verification**: new `test_approval_in_comments_written_without_polygon_rows` in `tests/test_polygon_workflow.py`: `fill()` with two tech rows (approvals "Approved"/"Site clear") and `polygon_rows=None` → AI1/AI2/AI3 set, AG2 still the template formula string starting with `=VLOOKUP(`, AH1 is `None`; the existing with-polygon test keeps passing.

#### Fix 1.13 — `PARTIAL_` tag and UI wording for ADS-skipped runs

**File**: `tools/web/run_controller.py` — `publish_to_desktop(run_dir, stamp, partial: bool = False)` → `target = DESKTOP / _desktop_export_name(partial or bool(blank_sources(summary)), name_stamp)`; `desktop_path_for_current` → `publish_to_desktop(run_dir, self._stamp, partial=self._partial)`.
**File**: `tools/web/app.js` — PARTIAL banner: `const blanks = (status.blank_sources || []).slice(); if (status.partial && !blanks.includes("ads")) blanks.push("ads");` and label "Blank/skipped source(s): "; Start click: `"Running (partial \u2014 ADS not approved; the workbook will be produced and labelled PARTIAL)."`.
**File**: `tools/web/index.html` line 47 hint: "Leave unticked for a partial run (ADS sheet left blank; the download is labelled PARTIAL)."

**Why minimal and safe**: the new parameter defaults to `False`, so the existing `publish_to_desktop` tests and the `is_downloadable(summary, partial=False)` gate are unchanged; `_existing_desktop_copy`'s glob already matches the `PARTIAL_` variant.

**Verification**: `tests/test_web_controller.py::test_publish_tags_partial_when_ads_skipped` — publishable summary (no blank sources), `publish_to_desktop(run_dir, stamp, partial=True)` → name contains `_KABIDEBA1_PARTIAL_<stamp>.xlsx` and `_existing_desktop_copy(stamp)` finds it; the existing non-partial test still asserts no tag.

#### Fix 1.14 — agent sheet header on row 1, numeric TIDs

**File**: `tools/fill_template.py` — **Function**: `write_polygon_rows`
```python
    clear_rows(ws, 2)
    for column, header in enumerate(("Tracking ID", "Station", "GCRS Status", "CX Address Check"), start=1):
        ws.cell(row=1, column=column, value=header)   # explicit row 1 (ws.append would land on row 2)
    for row in rows:
        tid = row.get("Tracking ID") or row.get("tracking_id") or row.get("TID")
        append_literal_row(ws, ["" if tid is None else num(tid), ...unchanged...])
```
**Why minimal and safe**: `append_literal_row` writes at `ws.max_row + 1`, which is 2 once row 1 holds the header; `num()` is the type rule every other source sheet already uses; the Working Sheet AG literal written by `fill()` and `read_back`'s polygon alignment do not depend on this sheet.

**Verification**: new assertions in `test_polygon_status_is_written_row_by_row`: agent sheet `A1 == "Tracking ID"`, `D1 == "CX Address Check"`, `A2 == 123456789012` (int), `max_row == 3`.

#### Fix 1.15 — count, log and record approval-scan failures

**File**: `tools/tickets_pull.py` — `scan_approvals(mcp, tickets, log, stats: dict | None = None)`: count failures and the first error in the `except` branch; after the loop `if failures: log(f"approval scan: {failures} of {n} tickets failed (blank approval written); first error: {first_error}")`; `if stats is not None: stats.update({"failures": failures, "first_error": first_error})`.
**File**: `tools/run_daily.py` — `pull_tickets`: `scan_stats = {}`; pass `stats=scan_stats`; add `"approval_scan_failures": scan_stats.get("failures", 0)` (and `"approval_scan_first_error"` when non-empty) to `run.summary["tickets"]`.

**Why minimal and safe**: keyword-only additive parameter; return type unchanged; `_counts_for("tickets")` ignores the new keys.

**Verification**: `tests/test_approval_scan.py::test_scan_approvals_counts_failures` — fake `mcp.call_tool` raising for one of two tickets → values `""`/label, `stats == {"failures": 1, "first_error": "RuntimeError: …"}`, and a log line containing "1 of 2 tickets failed".

#### Fix 1.16 — zero-row DSR export

**File**: `tools/dsr_export.py` — `main()`: `fieldnames = list(rows[0].keys()) if rows else ["tracking_id", "zone"]`.

**Why minimal and safe**: `validate()` recognises `tracking_id`/`zone` (normalised `trackingid`/`zone`), so `lane_dsr` sees `zone_col` set, `extra == []`, no conflicts, and logs "accepted source no-data as blank Zone" — the same path it already takes for partially missing TIDs.

**Verification**: new `tests/test_dsr_export_zero_rows.py` — monkeypatch `dsr_export.fetch` → `([], {...})` and `sys.argv` → `["dsr_export.py", "--tids", "1", "--out", tmp]`; `main()` writes `tracking_id,zone` header only; `validate(tmp, ["1"], rows=[], columns=["tracking_id","zone"])` yields `zone_col == "zone"`, `missing == ["1"]`, `extra == []`.

#### Fix 1.18 — pandas in the required list

**File**: `tools/auto_setup.py` — `REQUIRED_PACKAGES = ("openpyxl", "playwright", "requests", "pandas")`.

**Why minimal and safe**: installed only when missing (unchanged `_missing_packages` logic); never pinned or upgraded.

**Verification**: `tests/test_auto_setup.py::test_pandas_is_required` (`"pandas" in auto_setup.REQUIRED_PACKAGES`); existing tests pass because they patch `_missing_packages` and assert only on named items.

#### Fix 1.19 — cache the Playwright-Edge probe per process

**File**: `tools/auto_setup.py`
```python
_EDGE_PROBE_RESULT: bool | None = None   # per-process cache (None = not probed yet)


def _probe_playwright_edge() -> bool:
    ...existing body of _playwright_edge_ok (launch + close), unchanged...


def _playwright_edge_ok() -> bool:
    """Cached wrapper: the launch probe runs at most once per server process so /preflight
    polling never starts a browser."""
    global _EDGE_PROBE_RESULT
    if _EDGE_PROBE_RESULT is None:
        _EDGE_PROBE_RESULT = _probe_playwright_edge()
    return _EDGE_PROBE_RESULT
```
In `ensure_python_env`, after a successful `playwright install msedge`, set `_EDGE_PROBE_RESULT = None` so the next checklist re-probes once.

**Why minimal and safe**: the public name `_playwright_edge_ok` and both callers are unchanged (existing tests monkeypatch that name wholesale and keep working); the first real probe still happens once at startup via `ensure_python_env`.

**Verification**: `tests/test_auto_setup.py::test_edge_probe_is_cached` — monkeypatch `_EDGE_PROBE_RESULT` → `None` and `_probe_playwright_edge` → counting stub; call `_playwright_edge_ok()` three times and `environment_checklist()` twice → probe count 1.

### Implementation Sequence

1. **1.1** — `fill_template._afag_resolve`; unit test; real-Excel probe (must be 19/19).
2. **1.2** — `polygon_check` code map + skip rule; tests.
3. **1.3** — sanitizer regex; tests.
4. **1.4** — wipe allow-list + `_post_comments_busy` guard; tests.
5. **1.5, 1.6, 1.8, 1.13 (controller side)** — `run_controller.py` in one pass: `PAUSE_PHASES`, `_phase_error`, `_reconcile_phase`, `is_paused`, `continue_after_scc` preflight, `stop_run` paused branch, `publish_to_desktop(partial=)`; `post_comments_controller._pipeline_running`; tests.
6. **1.7, 1.11, 1.15 (summary), 1.17** — `run_daily.py`: `Run.restore_summary`, `tech_rows_for_csv`, `write_phase_a_files`, `pull_tickets` stats; `tickets_pull.scan_approvals(stats=)`; tests.
7. **1.12, 1.14** — `fill_template.fill` / `write_polygon_rows`; tests.
8. **1.16, 1.18, 1.19** — `dsr_export.main`, `auto_setup`; tests.
9. **1.8, 1.9, 1.10, 1.13 (UI side)** — `app.js`, `index.html`; browser smoke test.
10. Full regression (pytest in the background, syntax check, Excel probe, fill_template integration check) and the operator smoke-test list.

## Testing Strategy

### Validation Approach

The testing strategy follows a two-phase approach: first, surface counterexamples that demonstrate each bug on unfixed code, then verify the fix works correctly and preserves existing behaviour. For 1.1 the first phase was already executed during design with a real-Excel probe (results below); for the remaining defects the exploratory tests are the new unit tests run once against the unfixed code (they must fail) and once after the fix (they must pass). All pytest runs use `C:\Windows\py.exe -3.13 -m pytest tests -q` started as a background process (`control_pwsh_process`) with output redirected to a temporary file that is read back and then deleted, because the foreground shell intermittently kills long commands. Baseline before any change: 94 passed.

### Exploratory Bug Condition Checking

**Goal**: Surface counterexamples that demonstrate the bug BEFORE implementing the fix. Confirm or refute the root cause analysis. If we refute, we will need to re-hypothesize.

**Test Plan**: For 1.1, build a standalone probe workbook whose Working Sheet rows hold literal `E/J/N/O/R/S/V/AF/AG` inputs (with `=NA()` for `#N/A` cells) and whose column P carries the generated combined formula (taken from a real `fill()` of the template and translated per row with `Translator`, kept as an `ArrayFormula`), recalculate it with the installed Excel through `tools/recalc_excel.ps1`, and read the values back with `data_only=True`. For every other defect, write the unit test named in its fix entry and run it against the unfixed code.

**Test Cases**:
1. **P formula branch probe (1.1)** — 19 cases: `ISNA(J)` → "Invalid- Incorrect TID / TID Not Found"; LM path with AF_A / AF_B / AG inside / neither → AF_A / AF_B / AG text / "Valid"; LM path with AF=`#N/A` & AG inside, AF=`#N/A` & AG "Outside", AF=`#N/A` & AG=`#N/A`, AF other & AG=`#N/A`; `#N/A` catch-all path with AF_B, AG inside, neither, AF=`#N/A` & AG inside, AF=`#N/A` & AG=`#N/A`; pass-through of "Not Valid - Delivered", "Invalid - Already RTOed", "Not Valid - Doesn't belong to SWA", station-list "Valid" and probability "Valid". **Executed on the unfixed formula during design: 13 pass, 6 fail — every failure returns `#N/A` and every failing case has AF or AG = `#N/A`** (will fail on unfixed code — confirmed).
2. **CX zone rule (1.2)** — the 2-letter-code and city-name tests (will fail on unfixed code: "does not serve PB", "does not serve Bengaluru"; confirmed by the design-time table probe).
3. **Sanitizer (1.3)** — `SearchResults.csv` kept (will fail on unfixed code).
4. **Wipe allow-list (1.4)** — post-comments files kept (will fail on unfixed code).
5. **Phase A failure (1.5)** — fake exit 2 → `scc_phase == "failed"` and upload rejected (will fail on unfixed code: phase stays `awaiting_scc_upload`, upload accepted).
6. **Resume preflight (1.6)** — `continue_after_scc` with `midway_ok False` refuses (will fail on unfixed code: it spawns).
7. **Summary restore (1.7)**, **Stop while paused (1.8)**, **blank dates (1.11)**, **AI without polygon (1.12)**, **PARTIAL tag (1.13)**, **agent header row (1.14)**, **scan failure stats (1.15)**, **zero-row DSR (1.16)**, **post-filter files (1.17)**, **pandas (1.18)**, **probe cache (1.19)** — each new test fails on unfixed code for the reason stated in its fix entry (`AttributeError`/`TypeError` for the new helpers and parameters, wrong value otherwise).
8. **Edge case (1.1)** — AF a non-verdict text and AG=`#N/A` on the LM path (may fail on unfixed code — confirmed: returns `#N/A`).

**Expected Counterexamples**:
- 1.1: `#N/A` wherever AF or AG is an error inside `_afag_resolve` (confirmed root cause: `OR`/`IF` error propagation; refuted alternative: the outer `ISNA`/`SEARCH` structure, which handled all 13 non-error cases correctly).
- 1.2: "does not serve" for in-zone 2-letter codes and for city names.
- Possible causes if a non-1.1 exploratory test unexpectedly passes on unfixed code: the test does not reach the defective branch (fix the fixture), or the defect was mis-located (re-read the code path before changing anything).

### Fix Checking

**Goal**: Verify that for all inputs where the bug condition holds, the fixed function produces the expected behavior.

**Pseudocode:**
```
FOR ALL input WHERE isBugCondition(input) DO
  result := fixedFunction(input)
  ASSERT expectedBehavior(result)        -- the matching clause 2.k / Property k
END FOR
```
Concretely: the 19-case Excel probe must report `failures: 0` against the real generated formula (design-time result with the designed text: 19/19 pass, 3,026 characters); every new unit test passes; the full suite reports `94 + N passed, 0 failed` (N ≈ 24 new tests).

### Preservation Checking

**Goal**: Verify that for all inputs where the bug condition does NOT hold, the fixed function produces the same result as the original function.

**Pseudocode:**
```
FOR ALL input WHERE NOT isBugCondition(input) DO
  ASSERT originalFunction(input) = fixedFunction(input)
END FOR
```

**Testing Approach**: Property-based testing is recommended for preservation checking because:
- It generates many test cases automatically across the input domain
- It catches edge cases that manual unit tests might miss
- It provides strong guarantees that behavior is unchanged for all non-buggy inputs

**Test Plan**: Observe behaviour on UNFIXED code first for non-error AF/AG rows (the 13 passing probe cases), full-state-name CX rows, non-`SearchResults` Downloads files, OOJ lane folders, successful Phase A exits, and the existing 94 tests; then keep those observations as the preservation oracle.

**Test Cases**:
1. **Formula preservation**: the 13 non-error probe cases produce identical values before and after; `_afag_resolve` appears twice, the inner body three times, `_xlfn.` kept, `ArrayFormula` kept (observed on unfixed code, re-asserted after the fix).
2. **CX verdict preservation**: every existing test in `test_cx_address_check.py` (OK / NOT OK / MISMATCH branches, typo tolerance, GCRS-row wrapper, `check_pairs` additivity) passes unchanged.
3. **Sanitizer / wipe preservation**: `_browser_downloads`, partials, sidecars, PNAMA/DSR leftovers still removed; `runs/`, lane folders still wiped; current run dir, `scc_enum_map.json`, Desktop prune unchanged.
4. **Controller preservation**: exit-0 Phase A with files → `awaiting_scc_upload`, valid upload accepted, `continue_after_scc` spawns with the same flags when preflight is ready; `stop_run` on an idle controller still returns `stopped False`; every pre-existing `get_status()` key present (assert the key set is a superset of today's 18 keys: `running, stopped, exit_code, partial, run_dir, stamp, log_tail, sources, summary, publication_blocked, publishable, workbook_downloadable, blank_sources, verification, desktop_file, scc_upload, scc_phase, scc_validation`); `publish_to_desktop` default behaviour unchanged.
5. **Template writers preservation**: AG/AH literals, Tech_Data L, Working Sheet `max_column == 35`, `FINAL_COLUMNS` present exactly once, TID alignment (existing integration tests plus the new assertions).

### Unit Tests

- `tests/test_polygon_workflow.py`: `test_remark_formula_guards_af_ag_errors` (1.1), `test_approval_in_comments_written_without_polygon_rows` (1.12), agent-sheet row-1/`num()` assertions (1.14).
- `tests/test_cx_address_check.py`: parametrised 2-letter codes, city-name skip, `DL`/`KA` contradictions (1.2).
- `tests/test_sanitize_captures.py`: `SearchResults*.csv` kept (1.3).
- `tests/test_run_daily_phase_a.py` (new): wipe allow-list (1.4), `Run.restore_summary` (1.7), `tech_rows_for_csv` round trip (1.11), `write_phase_a_files` (1.17).
- `tests/test_run_controller_phases.py` (new): post-comments guard (1.4), Phase A failed/paused reconciliation and upload rejection (1.5), `continue_after_scc` refusal and `is_paused` (1.6), stop while paused (1.8), `get_status()` key superset (3.8).
- `tests/test_post_comments.py`: blocked while paused (1.6).
- `tests/test_web_controller.py`: `PARTIAL_` tag with `partial=True` (1.13).
- `tests/test_approval_scan.py`: failure stats and log line (1.15).
- `tests/test_dsr_export_zero_rows.py` (new): header-only output and `validate` acceptance (1.16).
- `tests/test_auto_setup.py`: pandas required (1.18), probe cached (1.19).

### Property-Based Tests

- **P formula (1.1 / 3.1)**: generate random (AF, AG, inner-path) triples over {AF_A, AF_B, other text, blank, `#N/A`} × {AG_INSIDE, "Outside", blank, `#N/A`} × {LM text, `#N/A`, pass-through value}; a Python oracle (`remark_rules.decide_remark` with `#N/A` mapped to `""`) must equal the Excel result — run as an extension of the Excel probe (Excel-dependent, manual).
- **CX zone rule (1.2 / 3.2)**: generate random State tokens from {all full names in `_PIN_ZONE_STATES`, all codes in `_STATE_CODE_ZONE`, random alphabetic non-state tokens} with random PINs; assert: code → MISMATCH iff zone differs; full name → same result as the original function; non-state token → never "does not serve" (pure Python, can be added with `hypothesis` if desired; otherwise the parametrised cases stand in).
- **Sanitizer / wipe (1.3, 1.4 / 3.3, 3.4)**: random file trees under tmp captures/Downloads; assert the deleted set equals exactly the set predicted by the allow-list/regex.
- **Phase reconciliation (1.5 / 3.5)**: random (exit code, files present) pairs; assert `failed` iff code ≠ 0 or files missing.

### Integration Tests

- **Real-template fill check**: `fill()` the real `.xltx` with 3 tech rows (one DOC TID, one with `approval_in_comments`), SCC/polygon rows for all three, no DSR → assert AG/AH/AI headers and literals row-aligned, Tech_Data `L1 == "Put the Quip data "` and L2..L4 values, agent sheet header on row 1 with int TIDs, Working Sheet `max_column == 35`, every `FINAL_COLUMNS` header exactly once, P2..P4 `ArrayFormula` texts contain the template body and the IFERROR guards, column I untouched when `dsr_rows is None`.
- **Real-Excel P probe** (post-implementation, mandatory for 1.1): 19/19 cases; then open the recalculated probe once in Excel to eyeball a sample row.
- **Browser smoke test** (local, no Amazon systems needed): start the server; verify Start/Stop/banner texts; reload during an idle state; with the Edge automation window open, click Start → "Could not start: The automation Edge profile is open…" (unchanged path).
- **Regression run**: full pytest (expect `94 + N passed, 0 failed`), `C:\Windows\py.exe -3.13 -m py_compile` on every edited `.py` (`fill_template.py`, `polygon_check.py`, `sanitize_captures.py`, `run_daily.py`, `tickets_pull.py`, `dsr_export.py`, `auto_setup.py`, `web/run_controller.py`, `web/post_comments_controller.py`), a `node --check tools/web/app.js` if Node is available (otherwise load the page and check the browser console is error-free), and a restart of the local web app to confirm it serves `/preflight` and `/status` with the new keys (`phase_error`) present.

### Not Live-Verifiable Here and Operator Smoke Tests

The following cannot be exercised in this environment because they need Midway/SIM-T, SCC, QuickSight (PNAMA/DSR), SIW/GCRS, ADS or a real paused run; their logic is covered by unit tests with fakes, and the operator should smoke-test them on the next real run:

- **1.1** — after the next full run, filter the Working Sheet for `Remark = #N/A`: expected 0 rows; rows with AF `#N/A` show the AG text or "Valid"; `run.log` line `#N/A per final column` no longer lists `Remark`.
- **1.2** — in the output's "CX Address Check" column, rows whose GCRS State is `PB`/`DL`/`KL`/… or a city name no longer read "does not serve"; a row with a PIN from another zone than its named state still does.
- **1.3** — export `SearchResults.csv` from SCC into Downloads, then click "Restart server": the file is still there.
- **1.4** — after a fresh Start, `captures/post_comments/<stamp>/post_comments_report.xlsx` files still exist; starting a run while a post-comments posting pass is live is refused with the post-comments reason.
- **1.5 / 1.10** — if Phase A ever fails (e.g. SIM-T pull incomplete), the page shows "Run failed (exit N) — …", Start is re-enabled and an upload is rejected; a Phase B stage failure shows the same banner with the failing stage.
- **1.6** — let Midway expire during the pause (or open the automation Edge window), upload the CSV: the SCC card reads "Upload accepted but the run could not resume: Midway session not granted yet — fix it, then upload the SCC CSV again"; after `mwinit -o` (Midway button) re-click Upload and Phase B starts. During a pause, the post-comments "Post" button stays blocked with the pipeline reason.
- **1.7** — during Phase B the SIM-T card stays `done` with its ticket/row counts; the final `summary.json` contains `preflight`, `tickets` stages and the `tickets` counts.
- **1.8 / 1.9** — while paused: Stop is enabled, clicking it shows "Run stopped by you…" and re-enables Start; reloading the page while paused re-attaches (cards, log, paused banner, upload control; Start disabled).
- **1.13** — a run started without ADS approval downloads as `…_KABIDEBA1_PARTIAL_<stamp>.xlsx`, banner "Blank/skipped source(s): ads".
- **1.15** — if the approval scan hits failures, `run.log` shows "approval scan: N of M tickets failed…" and `summary.json` → `tickets.approval_scan_failures`.
- **1.16** — a DSR search that returns no rows no longer fails the DSR lane (blank Zone, run continues).
- **1.18** — on a fresh machine the first server start logs "installing missing package: pandas".
- **1.19** — while the page is idle, Task Manager shows no recurring `msedge.exe` launches every 5 s; `/preflight` answers in milliseconds.
