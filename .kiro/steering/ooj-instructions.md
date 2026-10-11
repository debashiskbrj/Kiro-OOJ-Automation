# OOJ Automation — Standing Instructions (KABIDEBA)

This file records the operator's durable instructions for the OOJ automation.
Always read and follow these. When the operator gives a new standing rule, append
or update it here, then follow it going forward. Keep the working pipeline intact —
never break behavior that already works.

## Environment
- Windows 11 laptop, operator alias: `kabideba` (SWA India).
- Python: `py -3.13`.
- Workspace root: `c:\Users\kabideba\Desktop\Kiro OOJ Automation\`.
- Read-only against Amazon systems. No AI in the pipeline. Local one-click web app on `127.0.0.1:8000`.

## Verification rule
- After any code change, run `py -3.13 -m pytest tests -q` and confirm all tests pass before reporting done.
- Capture command output to a temp file when the terminal truncates, then read it. Clean up temp files afterward.
- Restart / confirm the local web app when a change affects a running process.

## Branding
- The automation is credited to KABIDEBA (header, footer, terminal banner). Keep this credit intact.

## Per-source rules

### Polygon check
- Fetch each station's polygon EXACTLY ONCE. No retries.
- If the fetch fails or the station polygon is not found, report that pair as an error immediately — do not retry.
- Polygon lane runs realtime (force refresh, no stale cache) for current location data.

### SCC
- Use the browser UI flow: paste TIDs into the search box, click Search, Export to CSV — for every batch.
- Do NOT use the SCC batch API (returns 400/403; SSO 302 outside the browser).
- Chunk size: 5000 TIDs per search (`SCC_UI_CHUNK=5000`). The result path is count-branched: =1,000 shows the `Export to CSV` button (click + capture); >1,000 ticks the ">1000" checkbox and SCC auto-downloads the result file (`AUTO_DOWNLOAD_THRESHOLD=1000`). Up to 3 refresh-retry attempts per chunk (reload page + search again).
- Wrong/invalid TIDs = no data returned, which is NOT a failure.
- Keep SCC retry behavior unchanged when adjusting other sources.

### PNAMA / DSR
- If the tool fails to pull data after searching, refresh the page ONCE and try again (2 attempts total).
- DSR: column A = `tracking_id`, column V = `zone`. Column I in the template uses VLOOKUP on the
  Working Sheet (D = TID) against `Waterfall_DATA!A:B` to fill zone. Ignore any pre-existing I formula.

### GCRS
- Edge browser is NOT mandatory. Runs in `auto` mode: try mcscli first (no browser); only fall back to Edge if mcscli fails.
- During concurrent runs, GCRS is mcscli-only while browser lanes (SCC/PNAMA/DSR) are busy, and retries with Edge fallback only after those lanes finish.

### ADS
- Single-file upload (one file for all rows). First column name must always be `TrackingId`.
- Fail fast if the ADS host is unreachable after retries.

### Concurrency
- EagleEye (API) and GCRS (mcscli) run concurrently with the serial browser lanes (SCC → PNAMA → DSR share one Edge profile).

## Template / output
- Column removal is DISABLED (`COLUMNS_TO_REMOVE = []`). Deleting columns shifts the grid and breaks formulas.
- Full template = 33 Working Sheet columns. A fresh run is required to produce a complete file.

## Operating reminders
- Before a run, sign in to SCC, PNAMA, and DSR in the Edge automation profile.
- Deployment is local-per-person (each operator uses their own Midway/Edge); no hosted service.
