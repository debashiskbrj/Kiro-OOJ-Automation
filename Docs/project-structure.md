# Project Structure

A map of where everything lives, so the project is easy to navigate and run.

Last reviewed: 2026-11-23.

> **Cloning this repo?** It needs Python 3.13 (`py -3.13`); missing packages are
> installed on first server start by `tools/auto_setup.py`. Per-user paths (Edge profile,
> builder-mcp) come from `tools/ooj_paths.py` (override with `OOJ_EDGE_PROFILE` /
> `OOJ_BUILDER_MCP`). `tools/captures/`, `tools/resolve_runs/`, `tools/health/`,
> `tools/metrics/` and secrets are gitignored, so a fresh clone has no real run data.

```
Kiro OOJ Automation/
├── README.md                  Overview + quick start + documentation map
├── READ ME FIRST.txt          Colleague guide (in the share zip)
├── NOTICE.txt                 Owner / author notice
├── VERSION                    Version number (bump on every change)
├── Start OOJ Web.bat          One-click launcher (finds Python, health check, page on :8000)
├── Run Health Check.bat       Weekly health check on demand
├── Make Share Zip.bat         Owner only: builds the colleague zip
├── AGENTS.md                  Agent workflow notes (NeuroTrace)
├── .gitignore                 Keeps run data, secrets, caches and scratch out of git
│
├── Docs/                      All documentation (see the map in README.md)
│   ├── Templete_OOJ Process_Date_Month_KABIDEBA.xltx   THE TEMPLATE (P2 = remark formula)
│   ├── user-guide.md             Daily steps on the web page (operator)
│   ├── SOP-OOJ-Automation.md     Standard operating procedure + checklist
│   ├── troubleshooting.md        Known failures and fixes
│   ├── resolve-tickets.md        Section 5: bulk resolve
│   ├── approval-in-comments.md   Column AI: approval rules (CRM / Site Lead)
│   ├── remark-rules.md           Column P (Remark) formula logic
│   ├── report-spec.md            Final report columns
│   ├── pipeline-reference.md     Each source/stage + data retention
│   ├── web-runner.md             Web app internals: phases, endpoints, gate
│   ├── developer-guide.md        Code layout, conventions, tests
│   ├── polygon-checker.md        GCRS polygon / jurisdiction checker
│   ├── architecture.md           Design notes
│   ├── implementation.md         Build history / current state
│   ├── context.md                Working notes and background
│   ├── access-status.md          Per-source access lane status
│   ├── edge-cases.md             Edge cases learned (#C*, #G*, #A* ...)
│   ├── PRD-OOJ-Automation.md     Product requirements
│   ├── changes.md                Changelog (newest at top)
│   ├── dmaic-project.md          Lean Six Sigma DMAIC write-up
│   └── project-structure.md      This file
│
├── tools/                     All code (one module per source/stage)
│   ├── run_daily.py             ORCHESTRATOR — Phase A (SIM-T) / Phase B (--resume-dir)
│   ├── tickets_pull.py          SIM-T tickets → Tech_Data + Approval in Comments (builder-mcp)
│   ├── eagleeye_client.py       EagleEye package leg detail (MCP API)
│   ├── pnama_export.py          PNAMA missort (QuickSight export)
│   ├── dsr_export.py            DSR Waterfall Zone (QuickSight export)
│   ├── gcrs_mcscli.py           GCRS/SIW address + geocode (mcscli / Edge fallback)
│   ├── siw_client.py            Shared SIW transport (mcscli probe → one-way Edge)
│   ├── polygon_check.py         Station polygons + point-in-polygon + CX Address Check
│   ├── tid_polygon_checker.py   Polygon orchestrator + Excel writer/verifier (also standalone)
│   ├── ads_run.py               ADS pincode prediction (Streamlit UI driver)
│   ├── fill_template.py         Fill the .xltx template from all source rows
│   ├── recalc_excel.ps1         Recalculate the workbook via Excel COM
│   ├── resolve_tickets_runner.py  Bulk resolve SIM-T tickets (section 5)
│   ├── post_comments_runner.py  Post comments to tickets (section 6)
│   ├── auto_setup.py            Install missing Python packages at server start
│   ├── sanitize_captures.py     Clear partial downloads / browser scratch at server start
│   ├── ooj_paths.py             Machine paths (OOJ_EDGE_PROFILE)
│   ├── edge_preflight.py        Edge automation-profile checks
│   ├── browser_blob_capture.py  In-page CSV blob capture helper (PNAMA/DSR QuickSight)
│   ├── browser_signin.py        Browser sign-in check at Start (PNAMA/DSR)
│   ├── dmaic_metrics.py         Stores each run's counts/timings; DMAIC control chart
│   ├── health_check.py          Weekly health check (Run Health Check.bat)
│   ├── launch.py                One-click launcher used by Start OOJ Web.bat
│   ├── about.py                 Owner / version / support contact
│   ├── build_share_zip.py       Builds the colleague zip (Make Share Zip.bat, owner only)
│   │
│   ├── web/                    The local one-click web runner
│   │   ├── server.py              HTTP server (127.0.0.1) + routes
│   │   ├── run_controller.py      Pipeline supervisor, SCC phases, stop/rerun, download gate
│   │   ├── resolve_controller.py  Resolve ticket supervisor + Excel report
│   │   ├── post_comments_controller.py  Post comments supervisor
│   │   ├── index.html             The page (sections 1–6)
│   │   ├── app.js                 Page logic
│   │   └── style.css              Styling
│   │
│   ├── captures/              RUNTIME OUTPUT ONLY (gitignored)
│   │   ├── runs/                One folder per run (kept 3 days)
│   │   ├── scc_uploads/         Uploaded SCC CSVs
│   │   └── tickets/ eagle/ pnama/ dsr/ polygons/ polygon_local/ ads/ post_comments/
│   │                            Per-source caches — cleared at every fresh Start
│   └── resolve_runs/          Resolve ticket outputs + reports (never cleaned; gitignored)
│
├── tests/                     Offline test suite (py -3.13 -m pytest tests -q) — 142 tests
│
├── .kiro/steering/ooj-instructions.md   Standing operator rules for AI assistants
└── .agents/                   Assistant scratch (gitignored). Keep: template-backup/ (template
                               before the P2 change), p-formula-row759.txt (used by a test).
```

## Where things go

- **New pipeline logic** → a module in `tools/`, wired into `tools/run_daily.py`.
- **Web page changes** → `tools/web/`. Restart the server after editing (never mid-run).
- **Docs** → `Docs/`, add a row to the map in `README.md`, and a line in `changes.md`.
- **Anything under `tools/captures/`** is disposable runtime output. Never store code or
  docs there.

## The run, in order

SIM-T tickets (+ approval scan) → **pause for a manual SCC CSV upload** → SCC → EagleEye
(parallel) + PNAMA → DSR → GCRS → Polygon → ADS → fill template → Excel recalc →
verify (read-back) → download. Details: `pipeline-reference.md`.
