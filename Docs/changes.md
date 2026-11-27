# changes.md — LIVE DOC (newest first)

One dated line per meaningful change: decision, access unlocked, edge case found,
code added/changed. Details belong in the other docs; this is the timeline.

---

- **2026-11-27** — FIX (v1.5.2, operator case 374689481819): India Post "NOT OK — partially
  matches" no longer blocks Amazon when the address is Full AND names a locality India Post
  lists for that PIN (e.g. "Block Pinjore" for "PIN 134102, Panchkula, Pinjore"); the city /
  district name alone does not count. `cx_amazon_bucket.india_post_area_named`. On the 224
  OOJ Cases TIDs: Amazon 46 → 47 (only that TID moved). Only the end-of-run bucket step
  (AK/AL) changed — SIM-T, SCC, lanes, formulas and all other columns are untouched.

- **2026-11-27** — FIX (v1.5.1, operator hand check of 224 "OOJ Cases" TIDs): (1) Pincode
  correct + station correct (R TRUE) + India Post OK + Full address is now **Amazon**
  ("Station claims OOJ but pincode, station and address are correct") — before it was CX "no
  wrong-station proof" even with a full address. CX "no proof" now only when R is unknown
  (#N/A) and the polygon is not Outside. (2) Address check rebuilt: GCRS address lines C:E are
  re-joined (GCRS cuts words: "Vem"+"ana Road", "Str"+"eet"); sector / phase / road no / gali
  no / ward / PG numbers no longer count as house numbers; landmark-only text is Short;
  building / society / institution names, villa / c/o / floor units and rural village +
  post/tehsil/district + landmark addresses count as Full. On the 224 TIDs: Amazon 39 → 46,
  CX 185 → 178 (11 CX→Amazon, 4 Amazon→CX); address grade changed for 48 TIDs. Tests: 31 real
  addresses in `tests/test_cx_amazon_bucket.py`.

- **2026-11-27** — FEATURE (v1.5.0, operator decision): two new Working Sheet columns for
  every **Valid** row — **AK "CX / Amazon Bucket"** (`CX Bucket` / `Amazon Bucket`) and **AL
  "Bucket Reason"** (short reason). Amazon only when ALL are proven: Pincode Validation (O)
  TRUE; Station Validation (R) FALSE or GCRS Polygon (AG) "Outside"; India Post Database (AH)
  "OK"; GCRS address (GCRS_Data col K, lines 1-3) Full (≥ 4 words without phone numbers + a
  house/flat/door/plot number or gali/road/street/sector/block word). Everything else is CX
  with the first failing check as reason (10 reasons). Logic `tools/cx_amazon_bucket.py`
  (+ CLI for old workbooks), written by Excel after the recalc like Rule B (which stays in AJ).
  Tested on 7 real runs (2,661 Valid rows): Amazon 140 (5.3%), CX 2,521 (94.7%); on run
  20261126_145546 the automation step gives exactly CX 415 / Amazon 25 and changes no other
  cell. **Renames (visible text only, internal keys and formulas unchanged):** AG "GCRS Agent
  Space" → **"GCRS Polygon"** (template AG1 shared string edited — only AG1 used it; backup in
  `.agents/template-backup/`), AH "CX Address Check" → **"India Post Database"**, same names in
  the `GCRS Agentspaces` sheet header, the TID_Polygon_Check file and the Validation Data file.
  The sheet name `GCRS Agentspaces ` is NOT renamed (AG2 formula points to it). Tests:
  `tests/test_cx_amazon_bucket.py`.

- **2026-11-27** — FIX (v1.4.2, operator report "PNAMA/DSR stuck in between"): two runs today
  (12:30, 13:20) had a bad DSR step. Causes found in the logs: (1) 12:41 — DSR returned only 8
  of 884 TIDs (and 13:37 attempt 1 had no table data, "Export to CSV" not clickable) while
  QuickSight was slow; the lane accepted the partial result silently, which blanked Zone for
  most rows; (2) 13:45 — a network blip (`ERR_NAME_NOT_RESOLVED`) left the browser on an error
  page and `open_dashboard` only re-navigated from a blank page, so it waited the full 5
  minutes and failed. PNAMA itself finished normally (3.5 min, 17 of 1,105 = normal match
  rate). Re-test the same day: the DSR lane returned 50/50 TIDs, then the full 1,105-TID list
  → 1,094 rows (99%) in 3.7 min, so the dashboard and both links are fine. Fixes:
  `pnama_export.open_dashboard` navigates again from a `chrome-error://` page;
  `dsr_export.fetch` now makes up to 3 attempts and treats an export with fewer than 50% of the
  requested TIDs (`MIN_COVERAGE`, only for ≥ 20 TIDs) as incomplete: it refreshes and retries
  ONCE, then accepts with a `DSR WARNING: only X of Y TIDs have DSR data` log line. The first
  DSR load can legitimately take ~2 min of "busy" — that is normal, not stuck. Tests:
  `tests/test_dashboard_resilience.py`.

- **2026-11-27** — CHANGE (v1.4.1, operator request): PNAMA and DSR now open their **direct
  sheet links** — PNAMA `…/dashboards/1650383a-7bfd-409c-9406-d364810f8019/sheets/1650383a-7bfd-409c-9406-d364810f8019_e70590da-c0eb-4ee1-8b83-dc7f288292a9`
  (`pnama_export.DASHBOARD_URL`, was the dashboard link without a sheet), DSR
  `…/dashboards/9a951c72-3cae-4c18-bfd1-69231a4f10d1/sheets/9a951c72-3cae-4c18-bfd1-69231a4f10d1_9fe3f0ac-9f42-4ef3-b81e-6352c9158f76`
  (`dsr_export.DASHBOARD_URL`, already identical; comment added). `browser_signin.py`
  uses the PNAMA constant, so the start-up check opens the new link too. Live check in the
  automation Edge window (read-only): PNAMA loaded in 21 s with the tab "SWA repeat_Prox_Pnama_match
  Raw data" found and clicked by the lane's own code; DSR loaded in 14 s with "DSR Raw data".
  Only these two constants carry a dashboard link anywhere in the project (checked by
  `tests/test_dashboard_urls.py`); `Docs/architecture.md` updated. Still true: QuickSight may
  keep each user's last filter state — reset both dashboards by hand if a run looks short.

- **2026-11-26** — FULL CHECK + CLEANUP (v1.4.0, operator request "find all the file folder
  script and if any wrong correct it"): every .py compiles, pyflakes clean, app.js syntax OK,
  every tools/*.py and .ps1 is used, no secrets, README lists all 21 docs. Docs brought up to
  date with this week's rules (SCC-only TIDs, station fallback, AH/AI/AJ, Remark Summary,
  Retry buttons): `pipeline-reference.md`, `report-spec.md`, `user-guide.md`; stale
  present-tense `scc_client.py` line in `edge-cases.md` marked historical; owner path removed
  from a `post_comments_runner.py` comment. Found: the template was opened and saved in
  Excel on 26-Nov 21:24 — formulas identical, only column D (TID) widened and re-styled
  (operator edit, kept). Removed 21 one-off agent scratch scripts/logs from `.agents/` and
  the `.pytest_cache/` folder (kept `template-backup/`, `p-formula-row759.txt` and the
  LinkedIn image script `media.py`) and the outdated v1.3.9 share zip from the Desktop.

- **2026-11-26** — CHANGE (v1.3.9, operator rule): **Working Sheet E (Station Code(SCC))** is
  now a visible formula that uses the SCC Source when it is a real 4-character letter/digit
  station code, otherwise the **DSR Data `station_code` (column Q)** for the same TID; if the
  TID is not in DSR the SCC value stays (a TID not in SCC still shows #N/A). Written by
  `fill_template.extend_working_sheet` (like I = Zone); the template file is unchanged. The
  polygon check uses the same rule for its station (`fill_template.effective_station`), and
  the workbook polygon validator applies it too. Seen in runs: SCC Source sometimes holds a
  10-digit number or a long ID (1–21 rows per run); DSR had a valid code for all of them in
  the good runs. Real-Excel check on run 20261126_145546: E correct on 975/975 rows, 1 row
  corrected (`3310810422` -> `HYBK`), remark and Rule B counts identical to the original run.
  Tests: `tests/test_station_code_fallback.py`.

- **2026-11-26** — FIX (v1.3.8, operator report "Polygon file not corrected"): v1.3.7 moved
  every lookup to the SCC TIDs but the **Polygon** lane still built its input from the SIM-T
  ticket rows, so `TID_Polygon_Check_<stamp>.xlsx` listed SIM-T TIDs. Now the polygon check
  runs on ONLY the SCC file's TIDs (each once, station = SCC Source; ticket station code only
  when Source is blank) — that is the TID_Polygon_Check file (also `polygon_scc.csv`). The
  results are then matched back to the Working Sheet rows by TID; a ticket whose TID is not in
  the SCC file shows `Error — TID not in SCC file (not checked)` in AG (was "SIW unresolved").
  Replay on run 20261126_145546: 974 of 975 rows identical, the 1 other is that new label;
  workbook validator passes. Also: web progress % now counts the SCC TIDs
  (`lookup_tids.txt`), and log wording says "SIM-T ticket TIDs" / "SIM-T TIDs not in the SCC
  file" instead of "lookup TIDs" / "missing". Found: the SCC files uploaded at 20:07 and 20:53
  on 26-Nov (`SearchResults (23)/(24)/(25).csv`, 224 / 17 / 224 rows) contain NO TID of any
  SIM-T run on this laptop, so every row of those runs is #N/A — wrong SCC search list, not
  a code fault. Tests: `tests/test_scc_only_tids.py`.

- **2026-11-26** — RULE CHANGE (v1.3.7, operator decision): **every TID lookup uses ONLY the
  TIDs of the uploaded SCC file** (the operator's cleaned list: incorrect and duplicate TIDs
  already removed). EagleEye, PNAMA and DSR used the SIM-T TIDs before; GCRS (and so ADS and
  Polygon) used SCC but fell back to SIM-T when the SCC file was empty — that fallback is
  removed. SIM-T still supplies the ticket rows (URL, Short ID, dates, comments, Approval in
  Comments) and the TID list the operator searches in SCC. Working Sheet rows stay one per
  SIM-T ticket (template formulas key on them); a ticket whose TID is not in the SCC file gets
  no lookup data and the template remark handles it ("Invalid- Incorrect TID / TID Not
  Found"). An SCC upload with no Tracking IDs stops the run before any lookup. Each run logs
  the counts and writes `lookup_tids.txt`. SCC TIDs that are not in that day's SIM-T tickets
  (should never happen: the SCC file is the cleaned SIM-T list) are only HIGHLIGHTED — a
  `WARNING: SCC file does not match SIM-T` log line and a 20 s amber page popup — and the run
  continues (operator decision; seen 2026-11-26 20:07 when an old SCC file with 0 matching
  TIDs was uploaded). Tests: `tests/test_scc_only_tids.py`.

- **2026-11-26** — FEATURE (v1.3.6, operator request): **"Retry failed / pending" button** in
  section 5 (Resolve ticket) and section 6 (Post comments); it shows the count and is enabled
  only when the last run left something undone. Resolve: re-runs ONLY tickets that are Failed
  or have no result (stopped / not verified); Resolved and Already-resolved are never sent
  again; it starts a new run folder `<stamp>_retry` (own report), after a confirm dialog and
  `POST /resolve/retry-failed {"confirmed":true}`. Post comments: the button already existed
  but only redid Failed rows; it now also covers pending URLs (in the uploaded file but never
  attempted), never a URL with a Success row (any date), and keeps all earlier report rows.
  Caveat shown in the confirm dialog: a "Failed - No Confirmation" URL may already have its
  comment on the ticket. The page state is in server memory, so after a server restart the
  buttons stay disabled until the next run. Tests: `tests/test_retry_buttons.py`.

- **2026-11-26** — FIX (v1.3.5): Resolve ticket run 20261126_153227 (506 tickets): 468
  Resolved, 38 Failed. 30 failed with `get-ticket ... non-API HTML ... "403 Forbidden"` (the
  ticketing gateway blocked reads in short bursts that hit all 4 workers in the same second,
  e.g. 15:33:00, 15:35:40, 15:37:09; the normal retries 2+5+10 s = 17 s ended before the block
  lifted). None of the 30 was sent, so no ticket was changed. The other 8 were sent but
  SIM-T still showed Assigned after ~120 s. Fix: after a gateway 403 the call is retried with
  `GATEWAY_RETRY_DELAYS = (15, 30, 60, 90)` s (other errors keep 2/5/10). Re-running the list is
  safe (Resolved tickets are skipped). Tests: `tests/test_resolve_tickets.py`.

- **2026-11-25** — FEATURE (v1.3.4, operator spec): new Working Sheet column **AJ = "Rule B
  Bucket (Pincode + GCRS Address)"**, plain text, Valid rows only (P exactly "Valid"):
  Customer Bucket / Amazon Bucket / Manual Review by rules B1+B2, B3, B5, B4, B6, B7 (first
  match wins) on N (Probability ≥ 0.60), O (Pincode Validation), R (Station Validation) and
  the CX Address Check from `GCRS Agentspaces ` col D matched on TID (NOT OK = bad, OK = ok;
  not found / Error / MISMATCH / blank = neither). Logic `tools/rule_b_bucket.py`; written by
  Excel (`tools/write_rule_b_column.ps1`) after the recalc, header format copied from the
  cell to its left, no other cell changed. After writing, the column is re-read and the three
  buckets must add up to the Valid count (logged). Non-fatal. Also a CLI for an existing
  workbook (writes `<name>_RuleB.xlsx`). Checked on the operator's edited 2026-11-24 workbook:
  Valid 494 = Customer 345 + Amazon 78 + Manual 71 (reference), 0 other cells changed.
  Tests: `tests/test_rule_b_bucket.py`.

- **2026-11-24** — FEATURE (v1.3.3, operator request): new **Remark Summary** sheet (right
  after Working Sheet) with a PivotTable: volume per Remark (column P), largest first, Grand
  Total. Built by `tools/recalc_excel.ps1 -PivotRows N` in the same Excel session as the
  recalc; refreshes on file open (after editing P: right-click > Refresh). Non-fatal: a
  pivot failure logs a WARNING and the workbook is still produced. Verified on run
  20261125_152211_943727: pivot counts = the run log's remark counts (793/298/187/12/4/2/1,
  total 1,297). Browser sign-in wait raised to **20 s**. Tests: `tests/test_remark_pivot.py`.

- **2026-11-24** — CHANGE (v1.3.2, operator decision): the browser sign-in check at Start
  (`tools/browser_signin.py`) now waits **at most 10 s** (was 10 min), checks only the PNAMA
  dashboard (DSR is the same QuickSight account), closes the window and the page shows a
  popup: "Browser accepted Midway — next process starting automatically" (green) or
  "not confirmed" (amber). The run continues either way; PNAMA/DSR keep their own login wait.
  Tests: `tests/test_browser_signin.py`.

- **2026-11-23** — TEMPLATE FIX (v1.3.1, operator-approved): in the Working Sheet Remark
  formula (P2..P6) every `V<n>="REVERSE"` became `IFNA(V<n>,"")="REVERSE"` (14 places).
  A TID with no EagleEye row used to make `OR(...)` return #N/A, so a Delivered shipment got
  "Valid"; now it gets "Not Valid - Delivered" (and OOR / DS-DS likewise). Only the sheet
  XML text was edited (pivots/array formulas kept); backup in `.agents/template-backup/`.
  Checked in real Excel: Delivered, Cash At Station, OOR, DS-DS without EagleEye and RTO
  with REVERSE all give the right remark. Test: `test_template_remark_ignores_missing_eagleeye_row`.

- **2026-11-23** — FIX + CLEANUP (v1.3.0). All remaining audit items fixed, with tests in
  `tests/test_audit_fixes.py`: **1.2** CX Address Check no longer says MISMATCH for state
  codes (PB, DL, KL, TG, MH…) or city names; a real wrong-zone state is still caught.
  **1.4** a new Start keeps `captures/post_comments/` (Post comments reports). **1.6** before
  continuing after the SCC upload, Midway and the Edge profile are checked again; a problem
  gives a clear message and the SCC file can be uploaded again. **1.7** after the SCC upload
  the SIM-T card keeps its "done" result. **1.8** the main Stop button works while the run is
  paused for SCC. **1.10** a failed run shows a red "The run failed at: …" banner. **1.12**
  Approval in Comments (AI) is written even when Polygon is skipped. **1.13** a run without
  ADS approval is labelled PARTIAL (file name `…_KABIDEBA1_PARTIAL_<stamp>.xlsx`, banner,
  Start text). **1.14** the GCRS Agentspaces sheet header is on row 1; TIDs are numbers.
  **1.15** a ticket whose comments cannot be read is marked "Need check manually (comments
  could not be read)" and counted in the log. **1.16** a zero-row DSR export no longer
  crashes. **1.17** `tech_data.csv` is written from the final rows (CLI `--max-tids` /
  `--tids-file`). Removed unused code and files: `tools/apply_remarks.py`,
  `tools/remark_rules.py`, `tests/test_remark_rules.py`, `run_daily.prune_history` /
  `finalize_remarks`, `pnama_export._wait_for_file`, `run_controller._sha256`, unused
  imports, the finished audit spec `.kiro/specs/full-project-bug-audit/`, and the dropped
  hosting plan. `.neurotrace/` (local assistant tooling, contains a session token) and the
  generated `.github/copilot-instructions.md` are no longer tracked in git. Workbook
  formulas and remarks unchanged.

- **2026-11-22** — FIX (v1.2.0), audit items from `.kiro/specs/full-project-bug-audit/`:
  **1.3** server start/restart no longer deletes the operator's SCC export
  `SearchResults*.csv` from Downloads (`sanitize_captures.py`). **1.5** a failed SIM-T step
  (Phase A exit ≠ 0 or no `tech_data.csv`) now shows "stopped at the SIM-T step" and offers
  Start again, instead of a false "Paused — upload SCC" (new `scc_phase` value
  `phase_a_failed`, new additive `/status` key `start_error`; Stop during Phase A returns to
  idle). **1.9** reloading or reopening the page re-attaches to a running, paused or
  finished run (`app.js reattachToRun`). **1.11** a ticket with a missing date is written as
  blank and read back safely (`run_daily.pull_tickets` / `load_tech_data`). Workbook/report
  unchanged. Tests: `tests/test_audit_fixes.py`. Still open: 1.2, 1.4, 1.6–1.8, 1.10, 1.12–1.17.

- **2026-11-22** — DMAIC (v1.1.0): `Docs/dmaic-project.md` (STAR summary, charter, CTQ,
  SIPOC, measured after-numbers, Pareto of data gaps, fishbone, DPMO, value stream, control
  plan; manual baseline left [TO FILL]). New `tools/dmaic_metrics.py`: stores each run's
  counts/timings (no TIDs) in `tools/metrics/run_history.csv` before the 3-day wipe
  (called from `wipe_old_runs` and the weekly health check) and builds an I-MR control
  chart summary shown in the health report. Workbook/report unchanged. Standing rule:
  answer with STAR + DMAIC. Tests: `tests/test_dmaic_metrics.py`.

- **2026-11-18** — SHARING (v1.0.0): colleagues can run their own copy from a zip with no
  Kiro/LLM. New: `READ ME FIRST.txt`, `NOTICE.txt`, `VERSION`, `tools/about.py` (owner
  KABIDEBA + contact), `tools/launch.py` (finds an already-running page instead of starting
  a 2nd server; weekly health check), `tools/health_check.py` + `Run Health Check.bat`
  (weekly bug-fix run: safe auto-fixes, template check, self-test, report in `tools/health/`),
  `tools/build_share_zip.py` + `Make Share Zip.bat`. `Start OOJ Web.bat` now finds Python
  (offers winget install). Per-user paths in `tools/ooj_paths.py` (BUILDER_MCP, EDGE_PROFILE
  auto-created, USER_ALIAS; owner's paths unchanged); polygon `operator` = runner's alias.
  `pandas` added to auto-installed packages. Page footer/header show owner contact. The
  workbook/report is unchanged. Tests: `tests/test_share_package.py`.

- **2026-11-18** — TEMPLATE: new template `Docs/Templete_OOJ Process_Date_Month_KABIDEBA.xltx`
  (renamed; the old `..._Date_September_...` template was removed by the operator). It adds a
  `DSR Data` sheet with the DSR Raw data headers. `fill_template.write_dsr_data()` now saves
  every downloaded DSR row there (aligned to the headers; extra export columns appended to
  the right, never dropped) instead of a 2-column TID->zone map in `Waterfall_DATA`.
  Working Sheet I = `=VLOOKUP(D2,'DSR Data'!A:V,22,0)` keyed on D (TID); a TID missing from
  DSR shows `#N/A` (operator's choice, not blank). Operator deleted the `Waterfall_DATA`
  sheet (W/X/Y/Z are blanked out-of-scope columns). Test: `tests/test_dsr_data_sheet.py`.
  UI: visual-only CSS polish in `tools/web/style.css` (sticky header, card accents,
  styled file inputs, table striping, thin scrollbars).

- **2026-11-17** — FEATURE: **browser sign-in check at Start** (`tools/browser_signin.py`,
  called in `run_daily.py` Phase A right after preflight). After a laptop restart the
  automation Edge profile loses its QuickSight sign-in, so the first run's PNAMA sat on an
  unattended login page and failed. Now Start opens the automation Edge window on the PNAMA
  then DSR dashboard: already signed in -> closes by itself; login page -> waits up to
  10 min for PIN + YubiKey, then closes by itself and the run continues. Never fatal
  (PNAMA/DSR keep their own login wait). `--skip-browser-signin` turns it off. Read-only.
  Tests: `tests/test_browser_signin.py`.

- **2026-11-16** — FIX (resolve): an exception inside a resolve worker thread no longer
  kills the worker and silently drops its remaining tickets (the 21:33 run reported only
  75 of 79 rows). The ticket is logged in `run.log`, reported Failed ("Not processed … see
  run.log"), and the worker carries on. New test in `tests/test_resolve_tickets.py`.
  `Docs/resolve-tickets.md` updated from the old one-phase description to the two-phase run.

- **2026-11-16** — DOCS + HOUSEKEEPING: README, user-guide, web-runner and the standing
  rules (`.kiro/steering/ooj-instructions.md`) rewritten for the current behaviour; new
  `Docs/resolve-tickets.md` and `Docs/approval-in-comments.md`; stale "read-only / wipe
  everything / 7-day" statements corrected across Docs. Removed scratch: root `report.xml`,
  `.pytest_cache/`, `.agents/tasks/`, `tools/captures/_archive/`. The open audit backlog in
  `.kiro/specs/full-project-bug-audit/` is kept (not yet implemented).

- **2026-11-16** — FEATURE: **5. Resolve ticket** page section. Paste TT URLs (2k+),
  duplicates removed, invalid entries listed and never resolved; each unique ticket set to
  status Resolved + closure code Successful via builder-mcp (`update-ticket`), already
  Resolved/Closed skipped, result verified by reading the status back; 4 parallel workers,
  retries 2/5/10 s, Stop, live counts, Excel report. New `tools/resolve_tickets_runner.py`,
  `tools/web/resolve_controller.py`, `/resolve/*` routes, outputs in `tools/resolve_runs/`
  (gitignored, never wiped). Post comments renumbered to section 6.
  Tests: `tests/test_resolve_tickets.py` (fake API).

- **2026-11-16** — FEATURE: **Stop after SCC upload + rerun.** SCC card shows
  "Stop run (keep SIM-T data)" while Phase B runs; stopping moves Phase B outputs into
  `stopped_<time>/`, restores the Phase A summary and sets phase `scc_stopped`; the card then
  offers "Rerun with same file" or a new upload. New `/scc/stop`, `/scc/rerun`, status key
  `can_stop_after_scc`. Top Stop run unchanged. Tests: `tests/test_scc_stop_rerun.py`.

- **2026-11-16** — APPROVAL IN COMMENTS rules (column AI): fixed false "Approved" for
  "disapproved"/"unapproved" and "approval pending/required/denied/rejected"; added go-ahead
  phrases (carry on, no objection, fine to …); "continue" removed. Only CRM / Site Lead
  approvals give `Approved`/`Site clear` (Phonetool job title via ReadInternalWebsites, one
  lookup per author); anyone else → `Need check manually (<alias>)`. builder-mcp for SIM-T now
  loads `TicketingReadActions,ReadInternalWebsites`. See `Docs/approval-in-comments.md`.

- **2026-11-16** — RETENTION: fresh-Start clean-up now keeps run folders and Desktop
  output workbooks for **3 days** (`RUN_KEEP_DAYS = 3`) instead of deleting all prior runs
  / everything before today; per-source caches are still cleared every Start.
  Test: `tests/test_wipe_old_runs.py`.

- **2026-10-15** — Output renamed to `Templete_OOJ Process_<Month>_KABIDEBA1_<stamp>.xlsx`;
  runner opens in a standalone app window; CX Address Check column (AH); Approval in
  Comments column (AI) first added; column P kept as a traceable formula with the
  AF→AG→Valid catch-all (see git history for details).

- **2026-10-13** — REMARK FORMULA (column P) REPLACED with the operator's version for
  all rows. The new formula is saved in the template's Working Sheet P2
  (`Docs/Templete_OOJ Process_Date_September_KABIDEBA.xltx`, still an array formula; only
  that cell changed). It compares AF/AG through `IFERROR(AF2,"")` / `IFERROR(AG2,"")`, so a
  `#N/A` in AF or AG no longer propagates into P. The code no longer wraps P2:
  `fill_template.extend_working_sheet` now just translates the template's P2 down every row
  like every other formula column, and refuses to run if P2 lacks `IFERROR(AF2,"")` (stale
  template guard). `read_back` now requires the generated P2 to EQUAL the template P2
  (normalized). Tests updated/added (row-759 check against the operator's formula).

- **2026-10-12** — SCC AUTOMATION RETIRED → MANUAL CSV UPLOAD. SCC's site now blocks
  automated browsing, so the automated SCC browser lane was removed: `tools/scc_client.py`
  and `tests/test_scc_retry.py` were **deleted**. The run is now **two-phase with a pause**:
  Phase A (`run_daily.py --stop-after-tickets`) does preflight + the SIM-T ticket pull then
  exits; the web page's SCC card shows an "awaiting upload" control. The operator searches
  the TIDs in SCC by hand, exports the CSV, and uploads it (`POST /scc/upload`, validated
  against `SCC_REQUIRED_COLUMNS`); a valid upload auto-resumes Phase B
  (`run_daily.py --resume-dir ... --scc-csv ...`) which runs SCC→ADS unchanged via
  `load_scc_csv()`. Also: data retention changed from the 7-day `prune_history` window to a
  **fresh-run wipe** (`wipe_old_runs`) that clears **all** prior run folders and per-source
  capture artifacts at the start of every Start — preserving this run's data,
  `scc_enum_map.json`, and today's Desktop exports — and prunes old `OOJ_Process_Filled_*.xlsx`
  Desktop exports from a previous day; `prune_history` still exists but is no longer called.
  Docs updated across the set to match; README rewritten; repo cleaned (`.agents` gitignored)
  and backed up to GitHub. Suite: 75 passed, 0 failed.

- **2026-10-05** — DEEP DIVE (existing pipeline, read-only-behavior pass): reviewed
  `tools/*.py` against `ooj-audit-findings.md`/`rollback-plan.md` for safe fixes only.
  Applied one fix: `tools/edge_preflight.py` now imports `EDGE_PROFILE` from
  `ooj_paths` instead of carrying its own duplicate hardcoded profile-path literal
  (audit P0-1 / shortlist #4), matching `run_daily.py`/`scc_client.py`/
  `pnama_export.py`/`siw_client.py`, which already did. Everything else reviewed
  (GCRS Edge-batch retry-on-any-`TransportError`, SCC chunk-retry/no-reload path,
  resource-leak and broad-`except Exception` scan, TID-normalization duplication
  P1-4, stale-docstring checks P4-4/P4-5) was confirmed already correct or
  intentionally left alone as behavior-changing — no other code changed. Full report:
  `.agents/tasks/py-deep-dive-findings.md`. Suite stayed at 77 passed, 0 failed
  throughout.

- **2026-10-05** — FEATURE (post-comments): added a gated, dry-run-first "5. Post
  comments (optional)" lane that posts live SIM ticket comments via Playwright/Edge.
  Strictly additive: new `tools/post_comments_runner.py` (parse + dry-run preview +
  one-pass confirmed post + retry-failed; comment format `TID: {tid} | {remark}` per url
  group, adapted from the operator's reference script with its blocking `input()` retry
  loop dropped) and `tools/web/post_comments_controller.py` (RunController-shaped,
  single-subprocess-at-a-time, atomic `dry_run_summary.json`, incremental
  `post_comments_report.xlsx`). Six new routes on `server.py`
  (`/post-comments/upload|status|confirm|retry-failed|stop|report-download`), a new UI
  section + JS + CSS. SAFETY GATE (never loosened): real posting is unreachable unless a
  successful dry-run of the *current* uploaded file exists (summary newer than the
  upload), the UI confirm checkbox is ticked, no post-comments subprocess is running, and
  `run_controller.CONTROLLER.is_running()` is False (shared Edge-profile mutual exclusion).
  Output lives under `tools/captures/post_comments/` (already covered by
  `sanitize_captures.py`'s generic containment walk — no sanitizer change). Tests:
  `tests/test_post_comments.py` (+8; suite 69 -> 77 passed, 0 failed).

- **2026-10-04** — RESILIENCE (GCRS): two additive, read-only safeguards after the
  2026-10-04 `Failed to fetch` run blanked gcrs+polygon+ads.
  *Feature A — preflight auth gate:* `run_daily.preflight()` and
  `run_controller.preflight_state()` now classify the GCRS SIW transport once at
  preflight using a `FailoverSiwSession(mode='mcscli')` probe (so the single Edge
  automation profile is never opened). The result is surfaced additively as
  `summary['preflight']['auth_gate']` and a new `auth_gate` key in `preflight_state()`
  (sub-keys: `midway_session_valid`, `gcrs_transport` = `mcscli` | `edge-fallback` |
  `unknown`, `browser_signin` = `unknown`, `browser_signin_reason`). A WARNING is logged
  when the run would be forced onto the fragile Edge fallback. Warn-only — the run is not
  hard-blocked and no existing preflight key changed; the probe is fully try/excepted so
  it can never crash preflight.
  *Feature B — per-batch Edge retry:* an Edge `/grbi` batch that throws `Failed to fetch`
  (or a degraded/empty window) now retries up to 3x with 2s/4s/6s backoff, then degrades
  to `errored` for that batch's TIDs while the remaining batches continue (mcscli path
  unchanged). See #G8.

- **2026-09-30** — PROTECTION: added `tools/sanitize_captures.py`, run at server startup
  and on `/restart`. It removes only output-corrupting scratch — everything inside
  `_browser_downloads/`, partial/temp files (`.crdownload`/`.tmp`/`.partial` + atomic-write
  sidecars) under `tools/captures`, and stale `SWA_repeat_Prox_Pnam*`/`DSR_Raw_data*`/
  `SearchResults*` leftovers in `~/Downloads`. Strict allowlist: only acts inside
  `tools/captures/` (resolved-path containment) and known export names; never touches code,
  docs, template, tests, or a run's real output. Covered by `tests/test_sanitize_captures.py`
  (3 tests); suite now 59/59.

- **2026-09-30** — UI: added a **Restart server** button in the header. It POSTs
  `/restart`, which stops any in-progress run, spawns a detached relauncher that starts a
  fresh `server.py` on the same port, and shuts the old one down; the button shows a
  pulsing "Restarting…" state and the page auto-reconnects and reloads when the server is
  back. Verified end to end (server relaunched and `/preflight` returned ready).
- **2026-09-30** — UI: made all buttons responsive — hover/active/focus states, a
  "working" pulse for in-progress actions, and mobile breakpoints (full-width, tappable
  buttons and stacked layout on phones; tighter header/padding on tablets).

- **2026-09-30** — ORG: added `Docs/project-structure.md` (full file map) and linked it
  from the README. Tidied `tools/captures/`: moved 40 old one-off dev/debug scratch files
  (Sep 16–17 screenshots, capture JSON, dry-run logs, test outputs — none used at runtime)
  into `tools/captures/_archive/`. The 9 live per-source dirs are untouched; `_archive` is
  covered by the existing 7-day auto-prune. No code changed.

- **2026-09-29** — PIPELINE: GCRS/Polygon/ADS input is now the **SCC output** (genuine
  TIDs only). SCC → PNAMA → DSR run first (full ticket list); GCRS then runs on SCC's
  resolved TIDs, removing the poison/invalid batches a raw ticket list produced. EagleEye
  still overlaps on the full list. Row alignment unchanged (fill maps by TID).
- **2026-09-29** — SCC: raised search size to **5,000 TIDs** per Search with a
  count-branched result path — ≤1,000 uses the `Export to CSV` button; >1,000 uses SCC's
  automatic file download, which the automation detects and reads (#C9).
- **2026-09-29** — UI: Progress cards now render as **numbered, one-by-one steps** in
  execution order with a status-coloured left accent (SCC before GCRS).

- **2026-09-28** — ADS FIX: root-caused a hard lane failure (`UI=1992, XLSX=1993`) to a
  single address beginning with a `"` — the ADS CSV parser swallowed the next row
  boundary, counting one fewer TID. `ads_run.py` now sanitizes the address field
  (double-quotes/newlines/tabs → space, whitespace collapsed), de-duplicates input by
  `TrackingId` and drops blank TIDs, and tolerates a tiny UI-count shortfall
  (`max(3, rows/100)`) instead of blanking the whole ADS column. A large mismatch or
  `UI > file` still fails loudly.
- **2026-09-29** — GCRS FIX: made `/grbi` batch size transport-aware — 500 over mcscli,
  200 over the Edge fallback. Run 20260929 fell back to Edge (mcscli `/gpsn` 302) and the
  500-ID batches returned degraded all-invalid windows then died with `Failed to fetch`,
  blanking gcrs+polygon. 200 over Edge is the previously-proven size; size is re-checked
  per batch so a mid-run mcscli→Edge failover drops to 200 for the remaining IDs. (#G8)
- **2026-09-28** — GCRS: raised `/grbi` batch from 200 to 500 (the SIW-supported
  per-call maximum) for fewer round-trips on large datasets. Still strictly sequential
  (#G4); unique-TID selection (normalize → dedup → ATS/ATA routing) unchanged.
- **2026-09-28** — SCC RELIABILITY: separated "genuinely not-found" from
  "failed-not-queried". A chunk that returns zero rows is now re-verified once with a
  refresh before being accepted (a transient empty export no longer silently drops
  data), and a chunk that exhausts its refresh retries is recorded as failed, written to
  `scc_failed_tids_<stamp>.txt`, and called out with a WARNING + re-run hint rather than
  vanishing into generic "no-data".
- **2026-09-23** — DOCS: added top-level `README.md` and new guides
  `Docs/user-guide.md`, `Docs/pipeline-reference.md`, `Docs/developer-guide.md`,
  `Docs/troubleshooting.md` to make the project understandable end to end.
- **2026-09-23** — FIX: disabled Working Sheet column removal in `apply_remarks.py`
  (`COLUMNS_TO_REMOVE = []`). Deleting columns shifted the grid and broke formulas in
  columns to the right, causing errors in the downloaded file. All columns are now kept;
  a fresh run produces the full 33-column template. Already-trimmed files can't be
  repaired (removal was in-place).
- **2026-09-23** — ADS: upload the whole set in ONE file (200MB limit is file size, not
  TID count); removes the per-chunk browser relaunch that stalled with no window. Fails
  fast if the ADS host doesn't load. First column stays `TrackingId`.
- **2026-09-23** — SCC: abandoned the batch API (HTTP 400/403 for our request shape,
  even single-TID). Reverted to the proven browser flow: paste TIDs → Search → Export to
  CSV, chunked, with viewport-safe clicks. State text comes straight from the CSV.
- **2026-09-23** — WEB: added per-tool progress %, per-source CSV download links, a Stop
  run button, a Copy log button (`/log` endpoint), a "partial download if a source
  fails" checkbox, and 7-day auto-pruning of `tools/captures/`. Polygon reverted to
  realtime (no cache) for current location data.
- **2026-09-23** — REMARK: six protected values (added `Invalid- Incorrect TID / TID
  Not Found` from State=#N/A and `Invalid - OOR` from State="In Transit (DS -> Customer)");
  State (col J) rules run before the AF/AD/AG logic. See `remark-rules.md`.

- **2026-09-18** — CURRENT TEMPLATE DELIVERED: live snapshot 788 tickets → 1,560
  Working Sheet rows / 1,496 unique TIDs. Sources filled: EagleEye 1,507 rows, SCC
  1,495, PNAMA 1,447, GCRS 1,495, ADS 1,464, DSR 1,452, polygon 1,560. Excel
  recalculated formulas through P1561; every protected source/template hash remained
  unchanged; the Desktop copy hash matched and final review approved.
- **2026-09-18** — UI RULES: recorded EagleEye high-volume Bulk Search + Package Leg
  Detail path; SCC dual path (>1,000 checkbox + automatic download, missing recovery
  via Search Result → Export CSV); PNAMA loaded-page marker fallback; DSR no-data Zone
  remains blank. Edge clean/profile launches both passed through managed background
  verification; safe preflight never kills WebView2/user apps.

- **2026-09-18** — FINAL REVIEW/RETRY: code review and security review approved
  with no remaining critical/high findings. Offline suite now passes 18/18. A repeated
  final live run rejected mcscli `/gpsn` HTTP 302, authenticated Edge locally,
  resolved `/grbi` 1/1, refreshed BRSG polygons, produced 1 Inside / 0 Errors, and
  reopened the workbook with `Rows == Input: 1 == 1 (VERIFY OK)`.
- **2026-09-18** — LIVE LOCAL POLYGON VERIFIED: auto mode rejected mcscli after a
  real non-200 `/gpsn` response, opened authenticated Edge exactly once, resolved `/grbi`
  1/1, refreshed BRSG `/gs` + `/gr`, classified 1 Inside / 0 Outside / 0 Errors,
  and reopened the XLSX with `Rows == Input: 1 == 1 (VERIFY OK)`.

- **2026-09-18** — LOCAL POLYGON: AgentSpace runtime dependency removed. Added
  `siw_client.py`, shared guarded `gcrs_mcscli.py`, pair-preserving
  `tid_polygon_checker.py`, verified standalone XLSX output, and row-aligned daily
  template integration. `--agentspace-file` and `agentspace_input.txt` were removed.
- **2026-09-18** — AUTH: mcscli session/posture reported valid but live SIW `/grbi`
  returned HTTP 401. Root cause is SIW request authorization, not cookie expiry. Local
  code now requires a real `/gpsn` probe and switches exactly once to authenticated
  persistent Edge; it never falls back to AgentSpace.
- **2026-09-18** — ACCURACY: local polygon input now preserves all non-empty lines,
  order, duplicates, and same-TID/different-station pairs. Output is reopened and
  checked row-for-row before `VERIFY OK`; `/grbi` remains sequential with 200-ID
  batches and poison/degraded-window guards.
- **2026-09-18** — TEST: local polygon and daily-template integration suite initially
  passed 7/7. After focused reviews and Edge-preflight coverage, 18/18 tests cover partial degraded recovery,
  non-finite GPS, invalid cache recovery, incomplete workbook rejection, Edge-probe
  cleanup, same-TID station swaps, and the explicit plaintext-ADS gate. Fixed the
  polygon geometry accumulator crash and a Windows read-only workbook handle leak.
- **2026-09-18** — EAGLEEYE: live lookup returned `package_type=REVERSE`, verifying
  the template's RTO signal.

- **2026-09-17** — ACCESS: **PNAMA WORKING.** `tools\pnama_export.py` automates the
  QuickSight export (textarea filter in the `tracking_id` control → visual ⋮ → Export
  to CSV); validated 5/5 TIDs, 100 columns. Edge cases #Q6–#Q12 added (textarea
  control, toggle trap, persisted per-viewer list, `TBC` missort value).
- **2026-09-17** — BUILT: `tools\tickets_pull.py` (local Tech_Data producer via
  builder-mcp subprocess, AgentSpace-identical schema/rules). Handshake OK; first full
  run waits on a fresh `mwinit -o` (Midway session expired 11:38).
- **2026-09-17** — SPEC: `Docs\report-spec.md` created with the user's final column
  list; DSR Waterfall dropped (pending confirmation); #S2/#S3 corrected (station code
  from description only, never buildingId), #S7–#S10 added.
- **2026-09-17** — SCOPE: OBLT (EVENT_414, template col AA) removed from the
  pipeline (user). `probe_oblt.py` retired; no Mercury/LDAP access requests. #O1
  kept in edge-cases.md as reference only.
- **2026-09-17** — ACCESS: **ADS WORKING.** `tools\ads_run.py` drives the "ADS &
  Pincode Batch Inference Platform" end to end (upload XLSX → "ADS with PP Trigger"
  → "Download data as CSV"); verified on 3 live TIDs in ~15 s; output columns match
  the template's ADS Predicted Data sheet. Requires a clean Edge (edge_profile's AEA
  extension aborts raw-IP URLs, #A1). Edge cases #A1–#A8 added.
- **2026-09-17** — ADS Streamlit app (`http://44.203.89.162:8192/`) confirmed
  reachable from this laptop: HTTP 200, TornadoServer/6.4, no login redirect.
- **2026-09-16** — PNAMA QuickSight export route confirmed from the user's
  screenshot: apply the `tracking_id` filter, open the bottom-right `⋮` on the
  `SWA repeat_Prox_Pnama_match Raw data` visual, choose Export to CSV/Excel.
  Access moved BLOCKED → PARTIAL; Playwright automation + exported-TID validation
  remain pending (#Q1–#Q5).
- **2026-09-16** — architecture.md: mermaid diagrams added (pipeline flow with
  working/blocked coloring, auth-lanes map, remark-engine §4.1 + missort §4.2
  decision trees). ASCII flow replaced.
- **2026-09-16** — Doc set + template moved from workspace root into `Docs\`
  (user); context.md paths updated.
- **2026-09-16** — Doc set created: context.md, architecture.md, edge-cases.md,
  implementation.md, changes.md, access-status.md (workspace root).
- **2026-09-16** — SCOPE: template columns F (Tire 3 city), G (Station Code RAW
  Data), H (Images) dropped from requirements (user). Retires the col-F `#REF!` bug.
- **2026-09-16** — SCOPE: Tech_Data source of truth = SIM-T query (not Quip);
  pipeline order fixed as SIM → TIDs → per-TID enrichment (user).
- **2026-09-16** — ACCESS: `mcscli.exe` present on the Windows laptop and
  **`POST /grbi` to SIW works headless** (HTTP 200, full routeDetailsArray). GCRS
  no longer needs a browser; `gcrs_client.py` demoted to fallback lane.
- **2026-09-16** — ACCESS: GCRS browser lane proven end-to-end (`gcrs_client.py`):
  721 polygon sets listed, 3-TID `grbi` query, 23-column CSV. Polygon set for IN
  numeric TIDs = `ATS!DEFAULT`.
- **2026-09-16** — EDGE: AEA (Amazon Enterprise Access) extension must be enabled in
  the Playwright Edge profile (`ignore_default_args` fix); first navigation may
  `ERR_ABORTED` by design (#C5).
- **2026-09-16** — ACCESS: SCC search + Export-to-CSV automated in the browser lane;
  underlying API captured (`/os/batchGetPackageSummary`, nodeId IN_ROOT). Direct
  requests lane confirmed impossible (Sentry kerberos + corp TLS, #C2).
- **2026-09-16** — ACCESS: EagleEye MCP working from Windows via Midway cookie jar
  (hand-rolled JSON-RPC; mcp-registry CLI unsupported on Windows, #W4). Region for
  IN = EU. RTO/REVERSE field verification still open (#E2).
- **2026-09-16** — ACCESS: SIM-T production filter replicated via builder-mcp
  (649 open OOJ tickets); `aliases` quirk documented (#S1).
- **2026-09-16** — BLOCKED: OBLT OAuth loop under automation (#O1); probe script
  written, not yet run. Alternatives mapped (EagleEye events / Mercury ES).
- **2026-09-16** — CONTEXT: user shared both AgentSpace context.md docs (address
  fetch + polygon checker); their SIW guards imported into edge-cases.md (#G1–#G9).
- **2026-09-16** — Template (16 sheets, all formulas) and SOP fully decoded and
  documented; remark-engine precedence extracted (architecture.md §4).
