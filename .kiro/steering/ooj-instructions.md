# OOJ Automation — Standing Instructions (KABIDEBA)

This file records the operator's durable instructions for the OOJ automation.
Always read and follow these. When the operator gives a new standing rule, append
or update it here, then follow it going forward. Keep the working pipeline intact —
never break behavior that already works.

Last reviewed: 2026-11-16.

## Environment
- Windows 11 laptop, operator alias: `kabideba` (SWA India).
- Python: `py -3.13` (use `C:\Windows\py.exe -3.13` if `py` is not found).
- Workspace root: `c:\Users\kabideba\Desktop\Kiro OOJ Automation\`.
- No AI in the pipeline. Local one-click web app on `127.0.0.1:8000`.
- The pipeline only READS Amazon systems. The only writes are page sections
  5 (Resolve ticket) and 6 (Post comments), each behind a confirmation box + dialog.

## Verification rule
- After any code change, run `py -3.13 -m pytest tests -q` and confirm all tests pass before reporting done.
- Capture command output to a temp file when the terminal truncates, then read it. Clean up temp files afterward.
- Restart / confirm the local web app when a change affects a running process — but never while a run is active.
- Only ONE `server.py` may listen on port 8000; close extra copies before starting a new one.
- No git commit or push unless the operator asks.

## Branding
- The automation is credited to KABIDEBA (header, footer, terminal banner). Keep this credit intact.

## Per-source rules

### SIM-T tickets
- Pulled via builder-mcp (TicketingReadActions). The first 11 Tech_Data columns are referenced by
  template formulas BY POSITION — never reorder them.
- **Approval in Comments** (Working Sheet AI, Tech_Data L): see `Docs/approval-in-comments.md`.
  - CRM / Site Lead approval → `Approved` / `Site clear` (title from Phonetool: CRM,
    Channel Relationship Manager, Site Lead, SL).
  - Approval by anyone else (or title unreadable) → `Need check manually (<alias>)` — never ignored.
  - "continue" is NOT an approval word.

### SCC (manual)
- SCC blocks automated browsing; the automated SCC lane is RETIRED. Do not reintroduce it and do
  not use the SCC batch API.
- The run pauses after SIM-T; the operator exports SCC's CSV by hand and uploads it in the SCC card
  (required columns: Tracking ID, State, Destination, Source, Ship Option).
- After the upload, the SCC card offers **Stop run (keep SIM-T data)**; after stopping, **Rerun with
  same file** or upload a new file. SIM-T is never pulled again for that run.
- Wrong/invalid TIDs = no data returned, which is NOT a failure.
- **TID source rule (2026-11-26):** every TID lookup (EagleEye, PNAMA, DSR, GCRS, ADS,
  Polygon) uses ONLY the TIDs in the operator's uploaded SCC file (already cleaned of
  incorrect + duplicate TIDs). Never look up SIM-T TIDs, on any day, and never fall back to
  them. SIM-T still gives ticket rows, URL, Short ID, dates, comments. No SCC TIDs = stop.
  SCC TIDs not matching SIM-T = highlight only (log WARNING + page popup), never stop.

### Polygon check
- Fetch each station's polygon EXACTLY ONCE. No retries.
- If the fetch fails or the station polygon is not found, report that pair as an error immediately — do not retry.
- Polygon lane runs realtime (force refresh, no stale cache) for current location data.

### PNAMA / DSR
- If the tool fails to pull data after searching, refresh the page ONCE and try again (2 attempts total).
- DSR: the FULL DSR Raw data export is saved in the template's `DSR Data` sheet (row 1 = the
  operator's DSR header row; values placed under matching headers, unknown export columns appended
  to the right). Column A = `tracking_id`, column V = `zone`. Working Sheet I = Zone via
  `=VLOOKUP(D2,'DSR Data'!A:V,22,0)` keyed on D (TID), same as the template I2 (corrected by the
  operator 2026-11-18). A TID missing from DSR shows `#N/A` — operator wants #N/A, NOT blank (no IFERROR).
  The `Waterfall_DATA` sheet was deleted from the template; Working Sheet W/X (now `#REF!` in the
  template) and Y/Z are out-of-scope columns and are blanked in the output.

### GCRS
- Edge browser is NOT mandatory. Runs in `auto` mode: try mcscli first (no browser); only fall back to Edge if mcscli fails.
- GCRS runs after the PNAMA/DSR browser lanes finish (the Edge profile is then free for the fallback).
- Do not change batch sizes (500 mcscli / 200 Edge) or the one-way fallback.

### ADS
- Single-file upload (one file for all rows). First column name must always be `TrackingId`.
- Fail fast if the ADS host is unreachable after retries.

### Concurrency
- EagleEye (API) runs concurrently with the serial browser lanes PNAMA → DSR (they share one Edge
  profile and must never run in parallel). GCRS, Polygon, ADS run after.

## Ticket write features
### Resolve ticket (section 5) — `Docs/resolve-tickets.md`
- Input: pasted TT URLs (2k+). Resolve each UNIQUE ticket: status Resolved + closure code Successful
  ONLY. No comment, no other field. Already Resolved/Closed tickets are skipped.
- After finishing show a visual report and a downloadable Excel report.
- First use after a change: resolve ONE ticket and check it in SIM-T before bulk use.
### Post comments (section 6)
- Dry-run preview first; nothing is posted until the operator confirms.

## Template / output
- Never delete Working Sheet columns. Deleting columns shifts the grid and breaks formulas.
- Full template = 33 Working Sheet columns (A..AG; AG header renamed "GCRS Polygon"
  2026-11-27). Automation adds AH = India Post Database (was CX Address Check), AI = Approval
  in Comments, AJ = Rule B Bucket (Pincode + GCRS Address), AK = CX / Amazon Bucket,
  AL = Bucket Reason; next free column is AM.
- CX / Amazon Bucket (2026-11-27, `tools/cx_amazon_bucket.py`): Valid rows only, two buckets.
  Amazon only if O TRUE + (R FALSE or AG Outside) + India Post OK + full GCRS address; else CX
  with a short reason in AL. Keep Rule B (AJ) as is.
- Rule B Bucket (2026-11-25, `tools/rule_b_bucket.py`): Valid rows only, plain text
  Customer Bucket / Amazon Bucket / Manual Review, rules in order B1+B2, B3, B5, B4, B6, B7.
  Buckets must add up to the Valid count in P. Reference: Valid 494 = 345 + 78 + 71.
- Column P (Remark) stays a visible formula copied from the template P2 — never flatten it to values.
- Column E (Station Code(SCC), 2026-11-26): SCC Source if it is a 4-character letter/digit
  code, else VLOOKUP of the TID in 'DSR Data' column Q (station_code). Polygon uses the same
  station rule.
- `Remark Summary` sheet (2026-11-24): PivotTable of volume per Remark (P), largest first,
  added by `tools/recalc_excel.ps1` after the recalc. Non-fatal if it fails.
- Output name: `Templete_OOJ Process_<Month>_KABIDEBA1_<stamp>.xlsx` (`PARTIAL_` before the stamp for partial runs).

## Retention
- Each fresh Start deletes run folders and Desktop output workbooks older than 3 days; newer ones are kept.
- Per-source caches are cleared on every Start (fresh data only). No other Desktop file is ever touched.
- Resolve reports in `tools/resolve_runs/` and Post comments reports in `tools/captures/post_comments/` are kept.
- Server start/restart never deletes SCC's own export `SearchResults*.csv` from Downloads.

## How to answer the operator (2026-11-22)
- Answer every question about this project with the **STAR method** (Situation, Task, Action,
  Result) and tie it to the **Lean Six Sigma DMAIC** phase it belongs to (Define, Measure,
  Analyze, Improve, Control). Use real numbers from `tools/metrics/run_history.csv`, run logs
  and tests. Never invent a number. Mark unknowns **[TO FILL]**.
- The DMAIC write-up lives in `Docs/dmaic-project.md`. Update it when a change affects a phase
  (new fix = Improve, new check = Control, new root cause = Analyze).
- Keep STAR short for short questions: one line per letter is fine.

## Sharing with colleagues (2026-11-18)
- KABIDEBA is the owner & author. Credit + contact (kabideba@amazon.com) must stay in the page,
  server banner, .bat files, `READ ME FIRST.txt`, `NOTICE.txt`, `tools/about.py`, health reports.
  Colleagues contact ONLY the owner for help/corrections.
- Colleagues use it WITHOUT Kiro/LLM: unzip → `Start OOJ Web.bat`. Keep it one-click.
- Share zip: `Make Share Zip.bat` (`tools/build_share_zip.py`). Never pack captures, run data,
  resolve/health reports, `.git`, `.kiro`, `.neurotrace` (token), `.agents`.
- Weekly health check (bug-fix run) must stay: every 7 days + after a VERSION change.
- No per-person paths in code — use `tools/ooj_paths.py`. The owner's own setup must stay unchanged.
- The report/workbook content and output name must not change because of sharing work.
- After any change: bump `VERSION`, run tests, rebuild the zip.

## Operating reminders
- Two Midway steps: terminal `mwinit -o` (Grant Midway) for SIM-T/EagleEye/GCRS, and the
  browser sign-in check that Start runs automatically (`tools/browser_signin.py`): the
  automation Edge window opens on PNAMA, waits at most 20 s (operator decision 2026-11-24:
  the browser picks up Midway by itself), closes, and the page pops up "Browser accepted
  Midway"; the run continues automatically. Keep it non-fatal and read-only.
- QuickSight saves each user's last filter state on the dashboard; the automation's Edge
  profile is the same user. Until an automatic reset is built, the operator resets the
  PNAMA and DSR dashboards (Reset / clear filters) before each run.
- Sign in to SCC in your normal browser.
- Deployment is local-per-person (each operator uses their own Midway/Edge); no hosted service.
