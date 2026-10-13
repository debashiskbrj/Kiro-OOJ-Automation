# Project Structure

A map of where everything lives, so the project is easy to navigate and run.

> **Cloning this repo?** It needs Python 3.13 (`py -3.13`); missing packages are
> installed on first server start by `tools/auto_setup.py`. Override the machine-specific
> Edge profile path in `tools/ooj_paths.py` with the `OOJ_EDGE_PROFILE` env var rather
> than editing code. `tools/captures/` and secrets are gitignored, so a fresh clone has
> no real run data. The README has the full first-run checklist.

```
Kiro OOJ Automation/
├── README.md                  Overview + quick start + documentation map
├── Start OOJ Web.bat          One-click launcher (starts the local web app on :8000)
├── AGENTS.md                  Agent workflow notes (NeuroTrace)
│
├── Docs/                      All documentation (see the map in README.md)
│   ├── user-guide.md            How to run it daily (operator)
│   ├── pipeline-reference.md     Each source/stage: what it does, how it fetches
│   ├── developer-guide.md        Code layout, how to change things, tests
│   ├── troubleshooting.md        Known failures and fixes
│   ├── remark-rules.md           Working Sheet column-P Remark logic
│   ├── web-runner.md             Local web app internals, endpoints, retention
│   ├── polygon-checker.md        Local GCRS polygon/jurisdiction checker
│   ├── report-spec.md            Final report columns
│   ├── architecture.md           Design notes
│   ├── implementation.md         Build history / current state
│   ├── context.md                Single-source-of-truth working notes + standing rules
│   ├── access-status.md          Per-source access lane status
│   ├── edge-cases.md             Live log of edge cases learned (#C*, #G*, #A* ...)
│   ├── changes.md                Live changelog (newest at top)
│   └── project-structure.md      This file
│
├── tools/                     All pipeline code (one module per source/stage)
│   ├── run_daily.py             ORCHESTRATOR — runs the whole pipeline in order
│   ├── tickets_pull.py          SIM-T tickets → Tech_Data (via builder-mcp)
│   ├── eagleeye_client.py       EagleEye package leg detail (MCP API)
│   ├── pnama_export.py          PNAMA missort (QuickSight export)
│   ├── dsr_export.py            DSR Waterfall Zone (QuickSight export)
│   ├── gcrs_mcscli.py           GCRS/SIW address + geocode (mcscli / Edge fallback)
│   ├── siw_client.py            Shared SIW transport (mcscli probe → one-way Edge)
│   ├── polygon_check.py         Station polygon fetch + point-in-polygon engine
│   ├── tid_polygon_checker.py   Polygon orchestrator + Excel writer/verifier
│   ├── ads_run.py               ADS pincode prediction (Streamlit UI driver)
│   ├── fill_template.py         Fill the .xltx template from all source rows
│   ├── recalc_excel.ps1         Recalculate the workbook via Excel COM
│   ├── apply_remarks.py         Rewrite Working Sheet Remark (column P)
│   ├── remark_rules.py          The Remark decision logic
│   ├── edge_preflight.py        Edge automation-profile checks (read-only)
│   ├── browser_blob_capture.py  In-page CSV blob capture helper (PNAMA/DSR QuickSight)
│   │
│   ├── web/                    The local one-click web runner
│   │   ├── server.py              Stdlib HTTP server (127.0.0.1)
│   │   ├── run_controller.py      Supervises run_daily.py; parses status for the page
│   │   ├── index.html             The page
│   │   ├── app.js                 Progress cards (numbered steps), live log, download
│   │   └── style.css              Styling
│   │
│   └── captures/              RUNTIME OUTPUT ONLY (wiped at the start of every fresh run)
│       ├── runs/                One dated folder per pipeline run (the real output)
│       ├── tickets/ scc/ eagle/ pnama/ dsr/ polygons/ polygon_local/ ads/
│       │                        Per-source artifacts: CSVs, screenshots, downloads
│       └── _archive/           Old one-off dev/debug scratch (safe to delete; wiped too)
│
├── tests/                     Offline test suite (py -3.13 -m pytest tests -q)
│   ├── test_polygon_workflow.py
│   ├── test_remark_rules.py
│   └── test_web_controller.py
│
└── .kiro/steering/            Standing instructions for the agent
    └── ooj-instructions.md
```

## Where things go

- **New pipeline logic** → a module in `tools/`, wired into `tools/run_daily.py`.
- **Web page changes** → `tools/web/` (`app.js`, `style.css`, `index.html`,
  `run_controller.py`). Restart the server after editing.
- **Docs** → `Docs/`, and add a row to the map in `README.md`.
- **Anything under `tools/captures/`** is disposable runtime output. Never store source
  code or docs there; it is wiped at the start of every fresh run (not time-based).

## The run, in order

SIM-T tickets → **pause for a manual SCC CSV upload** → SCC → EagleEye → PNAMA → DSR →
GCRS → Polygon → ADS → fill template → Excel recalc → verify (read-back) → apply Remark
rules → download.

The run pulls SIM-T tickets, then stops (Phase A) so the operator can search those TIDs
in SCC by hand, export the CSV, and upload it into the SCC progress card. A valid upload
auto-resumes the run (Phase B) from SCC onward. SCC's rows produce the genuine/correct
TID set that GCRS, Polygon, and ADS then use. Per-stage detail is in
`Docs/pipeline-reference.md`.
