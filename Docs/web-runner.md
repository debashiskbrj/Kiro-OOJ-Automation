# OOJ Web Runner — internals

The local web page that drives the pipeline (`tools/run_daily.py`), shows each source
live, verifies the result, and hosts the two optional ticket-write features
(Resolve ticket, Post comments). Operator steps: `user-guide.md`.

Last reviewed: 2026-11-16.

- **Local only.** Binds to `127.0.0.1`; never reachable from the network.
- **No AI.** No LLM anywhere in the run path.
- **Reads** Amazon systems for the pipeline. **Writes** to SIM-T only in sections 5 and
  6, only after the operator ticks a confirmation box and confirms a dialog.

## Start it

Double-click **`Start OOJ Web.bat`**, or `py -3.13 tools\web\server.py --port 8000`.
The page opens in a separate app-mode Edge/Chrome window. If port 8000 is blocked by
policy (WinError 10013) the server tries the next candidate and prints the URL.

**Only one server may listen on port 8000.** Two copies can both appear to bind on
Windows and the page then talks to a stale one. Use the page's **Restart server**
button, or close every `server.py` process before starting a new one.

## Page sections

| # | Section | What it does |
|---|---|---|
| 1 | Midway access | Opens a terminal running `mwinit -o`; polls until the cookie is valid |
| 2 | Start today's run | ADS approval + partial-download checkboxes, Start run, Stop run |
| 3 | Progress | 8 cards: tickets, scc, eagle, pnama, dsr, gcrs, polygon, ads; live log |
| 4 | Result | Verification banner; Download filled workbook |
| 5 | Resolve ticket | Bulk resolve SIM-T tickets (see `resolve-tickets.md`) |
| 6 | Post comments (optional) | Upload xlsx → dry run → confirm → post |

## Run phases (controller `_phase`)

| Phase | Meaning |
|---|---|
| `idle` | Nothing started this session |
| `awaiting_scc_upload` | Phase A (preflight + SIM-T) finished; waiting for the SCC CSV |
| `scc_uploaded` | Valid upload stored; Phase B about to start |
| `running` | Phase B (`--resume-dir`) running: SCC load → EagleEye/PNAMA/DSR → GCRS → Polygon → ADS → fill |
| `scc_stopped` | Operator stopped Phase B; SIM-T data + SCC file kept; rerun or upload new file |

`SCC_PAUSE_PHASES` in `app.js` = `awaiting_scc_upload`, `scc_uploaded`, `scc_stopped`.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/`, `/app.js`, `/style.css` | The page |
| GET | `/preflight` | Midway / Edge-profile readiness |
| POST | `/midway` | Open the `mwinit -o` terminal |
| POST | `/run` | Start Phase A (`{"ads_approved", "allow_source_failures"}`) |
| POST | `/stop` | Stop the whole run (process tree) |
| GET | `/status` | Run status; includes `scc_phase`, `scc_upload`, `can_stop_after_scc` |
| POST | `/scc/upload` | Validate + store SCC CSV, then auto-start Phase B |
| POST | `/scc/stop` | Stop Phase B only; keep SIM-T data and the SCC file |
| POST | `/scc/rerun` | Rerun Phase B with the same SCC file |
| GET | `/table`, `/log`, `/download`, `/source-download?name=` | Data and downloads |
| POST | `/restart` | Stop any pipeline / resolve run, relaunch the server |
| POST | `/resolve/check` | Parse pasted TT URLs (local only, changes nothing) |
| POST | `/resolve/start` | LIVE resolve; requires `{"confirmed": true}` |
| POST | `/resolve/stop` | Stop the resolve run |
| GET | `/resolve/status`, `/resolve/report-download` | Resolve progress and Excel report |
| POST | `/post-comments/upload`, `/confirm`, `/retry-failed`, `/stop` | Post comments |
| GET | `/post-comments/status`, `/post-comments/report-download` | Post comments status/report |

`/status` keys are used by `app.js` and tests: **only add keys, never rename/remove.**

## SCC upload, stop and rerun

- `RunController.upload_scc_csv` accepts a file in phases `awaiting_scc_upload`,
  `scc_uploaded`, `scc_stopped`; validates `SCC_REQUIRED_COLUMNS`; stores it under
  `tools/captures/scc_uploads/`; the server then calls `continue_after_scc`.
- `continue_after_scc` (phases `scc_uploaded` or `scc_stopped`; refuses if a process is
  running or the file is gone) copies Phase A's `summary.json` to `summary_phase_a.json`
  once, then spawns `run_daily.py --resume-dir <run> --scc-csv <file>`.
- `stop_after_scc` (phase `running` only) kills the process tree, moves every run-folder
  entry except `tech_data.csv`, `tids.txt`, `run.log`, `tickets_pull.log`,
  `summary_phase_a.json` into `stopped_<HHMMSS>/`, restores `summary.json` from
  `summary_phase_a.json`, and sets phase `scc_stopped`.
- The top **Stop run** (`stop_run`) is unchanged: it ends the whole run.

## Download gate ("100% accurate")

Download unlocks only when every required lane succeeded (or partial mode left it blank),
`fill_recalc` and `read_back` succeeded (row/TID/polygon contracts, no stray Excel
errors, generated P2 equals the template P2), and the polygon verification returned
`verify_ok`. The Desktop copy is `Templete_OOJ Process_<Month>_KABIDEBA1_<stamp>.xlsx`
(`PARTIAL_` before the stamp for partial runs). `DESKTOP_EXPORT_PREFIX` must stay the
same in `run_controller.py` and `run_daily.py`.

## Concurrency

PNAMA and DSR share one Edge automation profile, so they run one after the other.
EagleEye (API) runs on a background thread alongside them. GCRS, Polygon and ADS run
after. Never run two Edge sessions on the shared profile. Resolve ticket uses
builder-mcp only (no Edge), so it can run while the pipeline runs.

## Local history retention

At every fresh Start (Phase A), `run_daily.wipe_old_runs` (keep window
`RUN_KEEP_DAYS = 3`):

- deletes run folders in `tools/captures/runs/` older than 3 days (never the current run);
- deletes every per-source cache file under `tools/captures/<source>/` (fresh-data rule),
  except `scc_enum_map.json`;
- deletes Desktop `Templete_OOJ Process_*.xlsx` files older than 3 days; no other
  Desktop file is touched.

Phase B never wipes. `tools/resolve_runs/` is outside `captures/` and is never wiped.
At server start/restart `tools/sanitize_captures.py` also clears partial downloads and
browser scratch.

## Partial download

**Allow partial download if a source fails** (default off): a failed source's sheet is
left blank, the workbook is still produced and labelled PARTIAL, and the banner lists the
blank sources. Columns that depend on a blank source show `#N/A` — expected.
Engine flag: `run_daily.py --allow-source-failures`.

## Files

- `Start OOJ Web.bat` — launcher.
- `tools/web/server.py` — HTTP server and routes.
- `tools/web/run_controller.py` — pipeline supervisor, SCC phases, download gate.
- `tools/web/resolve_controller.py` — Resolve ticket supervisor + Excel report.
- `tools/web/post_comments_controller.py` — Post comments supervisor.
- `tools/web/index.html`, `app.js`, `style.css` — the page.
- Tests: `tests/test_web_controller.py`, `tests/test_scc_stop_rerun.py`,
  `tests/test_resolve_tickets.py`, `tests/test_post_comments.py`.
