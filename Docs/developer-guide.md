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
  remark_rules.py          column-P Remark decision logic (pure functions)
  apply_remarks.py         apply remark rules to a recalculated workbook
  edge_preflight.py        safe Edge automation-profile checks
  browser_blob_capture.py  capture QuickSight blob downloads in-page (PNAMA/DSR)
  recalc_excel.ps1         open Excel to recalculate formulas
  web/
    server.py              stdlib http.server: page + JSON endpoints
    run_controller.py      spawns/monitors run_daily, parses status, gates download
    index.html app.js style.css   the one-click page
  captures/                run artifacts + per-source screenshots/CSVs (wiped every fresh run)
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
   then `fill_and_recalc`, `read_back`, `finalize_remarks`.
4. Flags: `--allow-plaintext-ads` (ADS), `--allow-partial` (skip lanes),
   `--allow-source-failures` (blank a failed source and still publish).

Progress is machine-readable in `summary.json` (`stages`, per-source counts,
`blank_sources`, `publication_blocked`, `polygon.verification`). The web controller
reads this — it never re-implements pipeline logic.

---

## The web layer

- `web/server.py` — stdlib `ThreadingHTTPServer` bound to `127.0.0.1`, with port
  fallback. Endpoints: `/preflight`, `/midway`, `/run`, `/stop`, `/status`, `/table`,
  `/log`, `/download`, `/source-download`.
- `web/run_controller.py` — `RunController` owns one active run: spawns `run_daily.py`,
  captures `RUN_DIR`, tails `run.log`, parses `summary.json`, computes
  `is_publishable` / `is_downloadable`, and copies the verified workbook to the Desktop.
- The page polls `/status` (~2s) and renders source cards, the table, the log, and the
  finish banner.

---

## Conventions

- **Read-only** against Amazon systems. No writes, no git push, no ticket actions.
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
Covers: the polygon workflow (`test_polygon_workflow.py`), the web controller gates
(`test_web_controller.py`), and the Remark rules (`test_remark_rules.py`). Keep them
green before shipping a change. The browser lanes and live API calls are not unit-tested
(they need live systems) — verify those with a real run and the copied log.

---

## Making changes safely

- Verify a module imports cleanly (`py -3.13 -c "import sys; sys.path.insert(0,'tools'); import <mod>"`)
  and run the tests before/after.
- For a new source/stage: add a `lane_*` in `run_daily.py`, wire it into `main()` in
  order, add its counts to the web controller's `_counts_for`, and (if it produces a
  CSV) to `SOURCE_CSV` for the per-source download.
- Don't delete Working Sheet columns — it shifts the grid and breaks formulas (that
  bug is why `apply_remarks.COLUMNS_TO_REMOVE` is empty).
- Keep hard-coded paths (`edge_profile`, endpoints) in mind if porting to another
  machine; they'd need to be made configurable first.
