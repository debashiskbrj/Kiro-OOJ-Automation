# context.md — OOJ Process Automation (single source of truth)

> Read this file at the start of every session. It says WHAT this project is, WHERE
> everything lives, and WHICH doc to open next. Companion docs:
> `architecture.md` (design), `implementation.md` (current state), `polygon-checker.md`
> (authoritative local GCRS polygon workflow), `edge-cases.md` (live), `changes.md`
> (live), `access-status.md` (live).

---

## 1. What this is (one line)

Automate the daily SWA India **Out-of-Jurisdiction (OOJ)** ticket process end to end:
pull tickets from SIM-T → extract TIDs → enrich every TID from Amazon internal tools
(SCC, EagleEye, GCRS/SIW, ADS, PNAMA/DSR dashboards) → apply the remark logic →
produce the final report + NOC list + per-ticket comments, replacing the manual
Excel-template workflow.

## 2. Who / where

| Thing | Value |
|---|---|
| Operator | kabideba (SWA India resolver group) |
| Machine | Windows 11 laptop, PowerShell, Python 3.13 (pandas, openpyxl, playwright, requests, xlsxwriter) |
| Workspace root | `c:\Users\kabideba\Desktop\Kiro OOJ Automation\` |
| Documentation (this doc set) | `Docs\` (moved from root 2026-09-16) |
| Scripts | `tools\` (probes + clients built so far — see implementation.md) |
| Capture artifacts / logs | `tools\captures\` |
| Excel template (reference logic) | `Docs\Templete_OOJ Process_Date_September_KABIDEBA.xltx` |
| SOP | `c:\Users\kabideba\Desktop\Out_of_Jurisdiction_SOP_Final_Updated.docx` |
| Older automation | `c:\Users\kabideba\Desktop\My Files\My Automation\` (run_automation.py, post_comments.py) |
| Browser automation profile | `...\My Automation\TT_Automation\edge_profile` (persistent Edge + Midway/Sentry sessions + AEA extension) |
| Midway cookie jar | `~\.midway\cookie` (written by `mwinit`; needed by EagleEye MCP probe) |
| mcscli / SIW | `mcscli` is probed against real SIW; on failure the code switches once to authenticated persistent Edge |
| Former AgentSpaces | Historical source of accuracy rules only; neither address lookup nor polygon checking depends on AgentSpace |

## 3. The process in one paragraph (from the SOP)

Two query runs daily (10:30 and 17:30 IST). Tickets must be Sev-5, **Assigned**, with
TIDs in the ticket **Overview**. All valid TIDs from both runs must be marked UNDEL
before EOD. Auto-close (never send to NOC): Delivered, In-Transit DS→DS, Out on Road,
RTOed. Validation: pincode match → reattempt; mismatch + ADS ≥ 60% → Valid/UNDEL;
mismatch + ADS < 60% + station match → reattempt; mismatch + ADS < 60% + station
mismatch → Valid. Missort branch: PNAMA reference station == SCC station → deliver;
different station with no transfer connection → UNDEL. HYT2 13+ scope borderline →
UNDEL; BOMC → UNDEL without checking. Valid → raise to NOC, resolve without remarks;
Invalid → resolve with `TID: <tid> | <remark>` comments.

## 4. Pipeline order (user-defined)

1. **SIM ticketing first.** Tech_Data comes from the SIM-T query (the exact filter is
   in architecture.md §SIM-T). This yields ticket id, ShortId, station, dates, and the
   description text containing TIDs.
2. **TID extraction** from ticket descriptions (12-digit tokens).
3. **Per-TID enrichment** from the other tools (SCC, EagleEye, GCRS, ADS,
   Missort/Waterfall dashboards) — only after the TID list exists.
4. **Remark engine** (template column-P + missort decision tree, replicated in code).
5. **Outputs**: final report (format will be specified by the user — see
   `report-spec.md` once created), NOC list, per-ticket comment lines.

## 5. Scope decisions (current)

- Template columns **F (Tire 3 city), G (Station Code RAW Data), H (Images) are NOT
  required** (user, 2026-09-16). This also retires the `#REF!` bug in column F.
- **OBLT (EVENT_414, template col AA "OBLT Event") is OUT OF SCOPE** (user,
  2026-09-17). No OBLT lookups; the remark engine ignores col AA.
- **Final report columns are fixed in `Docs\report-spec.md`** (user, 2026-09-17).
  Shipper / Baby shipper / Case1 / Case2 are gone, but **DSR Waterfall remains required
  for Zone by TID** (user correction 2026-09-17). Column→source mapping to be confirmed
  incrementally by the user.
- **Tech_Data rules = the user's AgentSpace "SWA OOJ Ticket Analyzer"** (station code
  from the description line only, `DOC…` TIDs, `TID not found`, snapshot pagination).
- Tech_Data source of truth = SIM-T query (not the Quip export).
- Everything here is **read-only** against Amazon systems until the user explicitly
  approves write actions (posting comments / resolving tickets).
- Report format: **wait for the user's guidance** once tool access is complete.

## 6. Data sources at a glance (details in architecture.md, status in access-status.md)

| # | Source | Feeds (template sheet) | Access lane | Status |
|---|---|---|---|---|
| 1 | SIM-T | Tech_Data / Exported TT_Data | builder-mcp TicketingReadActions | WORKING |
| 2 | EagleEye | Eagle_EYE data (RTO check) | EagleEye MCP over Midway cookie jar | WORKING (`package_type=REVERSE` live-verified 2026-09-18) |
| 3 | SCC | SCC_(A,D,E,L) | dual browser path: >1,000 automatic default-column download; missing/≤1,000 Search Result → Export to CSV | WORKING — 1,495/1,496 live rows, 0 extras/duplicates (2026-09-18) |
| 4 | GCRS / SIW | GCRS_Data → ADS format | real mcscli probe, then one-way authenticated-Edge fallback | WORKING LOCALLY — live auto-fallback resolved 1/1 on 2026-09-18 |
| 5 | ADS pincode prediction | ADS Predicted Data | Playwright drives the Streamlit UI (clean Edge, no auth): upload XLSX → "ADS with PP Trigger" → "Download data as CSV" | WORKING (verified 2026-09-17) |
| 6 | OBLT (EVENT_414) | OBLT Data | — | OUT OF SCOPE (user, 2026-09-17) |
| 7 | PNAMA missort dashboard (QuickSight) | Missort Data | Playwright filter + raw-data Export to CSV | WORKING — 1,440/1,496 unique matches, 56 no-match, 0 extras (2026-09-18) |
| 8 | DSR Waterfall dashboard (QuickSight) | Zone only | `tools\dsr_export.py` via authenticated Edge | WORKING — 1,452 zones, 44 no-data + 1 blank kept blank, 0 extras/conflicts (2026-09-18) |
| 9 | Local GCRS polygon checker | legacy template col AG + standalone workbook | `tools\tid_polygon_checker.py` using shared local SIW session | WORKING — live 1/1 Inside, 0 errors, `VERIFY OK` on 2026-09-18 |
| 10 | Static refs (Pin-Station, Station-Zone, Station & Sub Station) | hidden sheets | local files from template | AVAILABLE (source/refresh cadence TBD) |

## 7. Standing rules

1. Never run `git push`; never post to tickets or modify any Amazon system without
   explicit user approval per run.
2. Live data only — never reuse a stale pull to answer a new run.
3. Every output must be count-verified. Per-source enrichment may query unique TIDs,
   but final report rows follow Tech_Data and the standalone polygon result must equal
   every non-empty input line, preserving order and duplicates (see `polygon-checker.md`).
4. `mwinit -o` must be fresh (daily) for the Midway cookie jar lane; the Edge
   automation profile keeps its own sessions (re-auth in the window when prompted).
5. Log every run to a file under `tools\captures\` — PowerShell console output gets
   swallowed on long runs.
6. Update `edge-cases.md` and `changes.md` in place as things are learned (live docs).
