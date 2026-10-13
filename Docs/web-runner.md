# OOJ Web Runner (local, free, no AI)

A one-click local web page that runs the proven OOJ validation pipeline
(`tools/run_daily.py`) end to end, shows each source filling live, verifies the
result, and gives you the filled workbook.

- **Local only.** The server binds to `127.0.0.1`; it is never on the network.
- **No cost, no AI.** Python standard library only; no third-party packages, no
  cloud calls, no LLM anywhere in the run path. Every source-system call happens
  inside `run_daily.py` exactly as it does from the command line.
- **Read-only** against Amazon systems. It never posts/resolves tickets and never
  pushes git.

## Start it

Double-click **`Start OOJ Web.bat`** in the project root, or run:

```
py -3.13 tools\web\server.py --port 8000
```

It opens `http://127.0.0.1:8000/`. If a port is blocked by machine policy
(WinError 10013), the server automatically tries the next candidate and prints the
URL it settled on.

## Daily flow (steps run 1 → finish, in order)

1. **Grant Midway access** — click the button once. A terminal window opens running
   `mwinit -o`; type your PIN there and approve. The page detects the valid session
   and unlocks Start. You are only asked once per session.
2. **Start today's run** — optionally tick **Approve ADS upload** (required for a
   complete, downloadable workbook; leave unticked for a partial run with no
   download). Click **Start run**.
3. **Progress** — eight source cards (SIM-T Tickets, SCC, EagleEye, PNAMA, DSR,
   GCRS, Polygon, ADS) update in real time with live counts, plus a live log and the
   full enriched table as it fills. The page never shows a previous run's numbers:
   until you start a run this session the cards stay "pending". **The run pauses
   after the SIM-T ticket pull**: SCC's automated browser lane is retired (SCC now
   blocks automated browsing), so the SCC card shows an **"awaiting upload"** badge
   with a file picker and an **Upload SCC CSV** button. Search the SIM-T TIDs in SCC
   yourself, export SCC's native CSV, and upload it there — a valid upload is
   validated against SCC's expected columns and automatically resumes the run from
   SCC onward; a rejected upload shows the reason and lets you try again.
4. **Result** — when the run finishes, the page runs the engine's own accuracy gate
   (all required lanes ok, read-back ok, polygon VERIFY ok). Only then does the
   **Download** button enable; the workbook is also copied to your Desktop as
   `OOJ_Process_Filled_<stamp>.xlsx`. If the run was blocked or partial, the banner
   explains why and download stays disabled.

## What "100% accurate" means here

Download is gated on the same publication rules the pipeline enforces itself:

- every required lane succeeded (no failed/skipped required lane),
- `fill_recalc` and `read_back` both succeeded (row/TID/polygon contracts hold, no
  stray Excel errors, template remark formula unchanged),
- the polygon workbook verification returned `verify_ok`.

If any of these fail, the workbook is **not** offered for download.

## Files

- `Start OOJ Web.bat` — launcher.
- `tools/web/server.py` — stdlib HTTP server + endpoints.
- `tools/web/run_controller.py` — spawns/monitors `run_daily.py`, parses
  `run.log`/`summary.json`, enforces the accuracy gate, copies to Desktop.
- `tools/web/index.html`, `app.js`, `style.css` — the page.
- `tests/test_web_controller.py` — gate/parse/copy tests.

## The `/scc/upload` endpoint

`POST /scc/upload` takes the operator's manually-exported SCC CSV (base64-encoded in
the request body) and hands it to `RunController.upload_scc_csv`, which:

1. Validates the file has the expected columns (`SCC_REQUIRED_COLUMNS` — Tracking ID,
   State, Destination, Source, Ship Option). A missing/invalid file is rejected with a
   reason and the run stays paused for another attempt.
2. On a valid upload, saves it and **immediately spawns Phase B**
   (`run_daily.py --resume-dir <run> --scc-csv <upload>`) in the same call, so the
   pipeline resumes automatically — the operator does not click a separate "resume"
   action.
3. Returns `{"uploaded": true/false, "reason": ..., "continue": {...}}`; the page uses
   this to flip the SCC card from "awaiting upload" to a running step.

## Concurrency

PNAMA and DSR each drive the *same* single Edge automation profile, and Chromium
allows only one process per profile — so those two run **serially** (one after
another) to avoid a profile collision. SCC is no longer part of this app's own Edge
automation: it is a manual CSV upload the operator performs in their own browser, so
it does not compete for the shared profile. EagleEye is a pure in-process API call
(no browser), so it runs **concurrently** on a background thread alongside the browser
lanes. GCRS/Polygon and ADS run after those complete. This is the maximum safe
parallelism; the pipeline never opens two Edge sessions against the shared profile at
once.

Progress is written incrementally: `run_daily.py` flushes `summary.json` atomically
before and after every stage, so the page reflects each step as it happens rather than
only at the end.

## Notes / limits

- The Midway PIN must be typed by you in the terminal window the button opens; the
  page cannot type it. It polls until the session is valid.
- Only one run at a time; starting a run while one is active is rejected.
- The automation Edge profile must be closed before a run (the page tells you if it
  is open).

## Local history retention (fresh-run wipe)

At the start of every fresh Start (Phase A), `run_daily.wipe_old_runs` deletes **all**
prior task history from `tools/captures/`, not just old items:
- every prior run folder under `captures/runs/` (except the run that is starting now),
- every per-source capture artifact (screenshots, blob downloads, cached polygons)
  under `captures/<source>/`.

It preserves this run's own directory and `scc_enum_map.json` (the SCC State
enum ground-truth overlay), and it also prunes old published Desktop exports
(`OOJ_Process_Filled_*.xlsx` from a previous day, keeping today's). It never touches
source code, the template, tests, or any other Desktop file. The older
`run_daily.prune_history` function (a 7-day retention window) still exists in the
source but is no longer called in the live path — `wipe_old_runs` replaced it.

## Polygon location data

The polygon/jurisdiction check fetches **live** station geometry every run
(`refresh_polygons=True`) so location checks always use realtime data.

## Partial download when a source fails

The page has a checkbox **"Allow partial download if a source fails"** (default OFF).

- **Off (default):** if any required source fails to fetch, the run blocks and no
  workbook is produced (as before).
- **On:** a source that can't fetch is left **blank** (its worksheet is empty) and the
  workbook is still produced and downloadable, labelled **PARTIAL**. The result banner
  lists which source(s) are blank, and the Desktop copy is named
  `OOJ_Process_Filled_PARTIAL_<stamp>.xlsx`.

In partial mode the workbook-producing steps (fill/recalc, read_back, apply_remarks)
must still succeed for the download to unlock. Columns that depend on a blank source
will show `#N/A` — that is expected, not an error. Engine flag:
`run_daily.py --allow-source-failures`.
