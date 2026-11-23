# Developer Guide — code layout and how to change things

For whoever maintains or extends this automation.

---

## Layout

```
Start OOJ Web.bat          one-click launcher for the local web app
README.md                  project entry point
Docs/                      documentation (this folder)
tests/                     pytest suite
tools/
  run_daily.py             orchestrator: main() runs the pipeline stage by stage
  tickets_pull.py          SIM-T ticket pull (builder-mcp)
  eagleeye_client.py       EagleEye MCP API client (chunks of 50)
  pnama_export.py          PNAMA QuickSight dashboard export (shared QS driver)
  dsr_export.py            DSR QuickSight export (imports pnama_export as the driver)
  gcrs_mcscli.py           GCRS via SIW grbi (sequential; 500 mcscli / 200 Edge)
  siw_client.py            FailoverSiwSession: mcscli, one-time Edge fallback
  polygon_check.py         station polygon fetch (gs/gr) + point-in-polygon
  tid_polygon_checker.py   boundary run + standalone verification workbook
  ads_run.py               ADS Streamlit UI driver (single-file upload)
  fill_template.py         write all sheets + translate Working Sheet formulas
  resolve_tickets_runner.py  bulk resolve SIM-T tickets (page section 5)
  post_comments_runner.py  post comments to tickets (page section 6)
  auto_setup.py            installs missing Python packages at server start
  sanitize_captures.py     clears partial downloads / browser scratch at server start
  ooj_paths.py             machine paths (Edge profile via OOJ_EDGE_PROFILE)
  edge_preflight.py        safe Edge automation-profile checks
  browser_blob_capture.py  capture QuickSight blob downloads in-page (PNAMA/DSR)
  recalc_excel.ps1         open Excel to recalculate formulas
  web/
    server.py              stdlib http.server: page + JSON endpoints
    run_controller.py      spawns/monitors run_daily, SCC phases + stop/rerun, gates download
    resolve_controller.py  Resolve ticket supervisor + Excel report
    post_comments_controller.py  Post comments supervisor
    index.html app.js style.css   the one-click page
  captures/                run artifacts + per-source caches (caches cleared every Start; runs kept 3 days)
  resolve_runs/            Resolve ticket outputs (never cleaned; gitignored)
```

SCC's automated browser lane (`scc_client.py`) was retired — the SCC site now blocks
automated browsing. SCC is handled directly in `run_daily.py`: `--stop-after-tickets`
pauses the run after the ticket pull, the operator uploads SCC's manually-exported CSV
through the web page, and `load_scc_csv()` loads it (validated against
`SCC_REQUIRED_COLUMNS`) before `--resume-dir` continues the run from SCC onward.

---

## How the pipeline is wired

`run_daily.main()`:
1. Builds a `Run` object (a stamped directory under `tools/captures/runs/`), prints
   `RUN_DIR=...` and writes `_latest.txt` so the web layer can find it.
2. Runs each stage via `Run.stage(name, fn, ...)`. `stage()` marks the stage
   `running` in `summary.json`, runs `fn`, then marks it `done`/`error` — this is what
   drives the live per-source cards.
3. Lanes return their rows (or `[]` on failure). After all sources: publication gate,
   then `fill_and_recalc` and `read_back` (column P stays the template formula; there is
   no remark-rewrite stage).
4. Flags: `--allow-plaintext-ads` (ADS), `--allow-partial` (skip lanes),
   `--allow-source-failures` (blank a failed source and still publish).

Progress is machine-readable in `summary.json` (`stages`, per-source counts,
`blank_sources`, `publication_blocked`, `polygon.verification`). The web controller
reads this — it never re-implements pipeline logic.

---

## The web layer

- `web/server.py` — stdlib `ThreadingHTTPServer` bound to `127.0.0.1`, with port
  fallback. Full endpoint table: `web-runner.md` (pipeline, `/scc/*`, `/resolve/*`,
  `/post-comments/*`, `/restart`).
- `web/run_controller.py` — `RunController` owns one active run: spawns `run_daily.py`,
  captures `RUN_DIR`, tails `run.log`, parses `summary.json`, computes
  `is_publishable` / `is_downloadable`, and copies the verified workbook to the Desktop.
- The page polls `/status` (~2s) and renders source cards, the table, the log, and the
  finish banner.

---

## Conventions

- The **pipeline is read-only** against Amazon systems. Ticket writes exist only in
  Resolve ticket and Post comments, behind an operator confirmation; never add a write
  to the pipeline. No git push without an explicit request.
- Only **add** keys to `/status` and `preflight_state()` — `app.js` and tests depend on them.
- The first 11 Tech_Data columns are referenced by template formulas by position; never
  reorder them. New Working Sheet columns go to the right (next free: AJ).
- Browser lanes (PNAMA/DSR/ADS) run as subprocess "workers" via
  `run_daily.run_browser_worker`, which streams their output live and enforces a
  timeout with a killable process tree. SCC is no longer one of these — it is a manual
  CSV upload loaded by `load_scc_csv()`.
- PNAMA/DSR share **one** Edge profile, so they must run serially (Chromium allows one
  process per profile). EagleEye/GCRS are API-style and don't touch the profile.
- Untrusted CSV values are written as literals (never as Excel formulas) — see
  `fill_template.set_literal_cell` and `csv_safe`.

---

## Tests

Run the suite:
```
py -3.13 -m pytest tests -q
```
142 tests as of 2026-11-23, all must pass. Files: `test_polygon_workflow.py` (fill,
template P2, polygon), `test_web_controller.py` (download gates), `test_scc_stop_rerun.py`
(stop/rerun after SCC), `test_resolve_tickets.py` (Resolve ticket, fake API),
`test_approval_scan.py` (approval column), `test_wipe_old_runs.py` (3-day retention),
`test_audit_fixes.py` (audit bug fixes), `test_dsr_data_sheet.py`, `test_dmaic_metrics.py`,
`test_share_package.py`, `test_cx_address_check.py`, `test_post_comments.py`,
`test_browser_signin.py`, `test_sanitize_captures.py`, `test_auto_setup.py`. Keep them
green before shipping a change. The browser lanes and live API calls are not unit-tested
(they need live systems) — verify those with a real run and the copied log.

---

## Making changes safely

- Verify a module imports cleanly (`py -3.13 -c "import sys; sys.path.insert(0,'tools'); import <mod>"`)
  and run the tests before/after.
- For a new source/stage: add a `lane_*` in `run_daily.py`, wire it into `main()` in
  order, add its counts to the web controller's `_counts_for`, and (if it produces a
  CSV) to `SOURCE_CSV` for the per-source download.
- Don't delete Working Sheet columns — it shifts the grid and breaks formulas. Only add
  columns after AG (next free: AJ).
- Per-user paths (Edge profile, builder-mcp) live in `tools/ooj_paths.py`; never hard-code
  a path to one person's laptop.
