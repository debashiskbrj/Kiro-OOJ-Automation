# Pipeline Reference — stages and sources

Orchestration lives in `tools/run_daily.py` (`main()`). Each stage is wrapped by
`Run.stage()`, which writes live progress to `summary.json` so the web page can show
status. The run is split into **two phases with a manual pause in between**: Phase A
(preflight + SIM-T ticket pull) stops and waits for the operator to upload SCC's export;
Phase B then runs the rest of the enrichment lanes. Within Phase B the lanes are mostly
serial, with one overlap: **EagleEye runs on a background thread** (it is a pure API, no
Edge) and **overlaps the serial browser lanes** PNAMA and DSR. The browser lanes
(PNAMA/DSR) share one Edge profile and stay serial.

Order: **preflight → tickets → [pause for manual SCC upload] → SCC (loaded from the
upload) → EagleEye (concurrent, background thread) → PNAMA → DSR → GCRS → Polygon → ADS
→ fill/recalc → read_back → apply_remarks**.

---

## 0. Preflight
`run_daily.preflight` — checks a valid Midway session, mcscli, the Edge profile (and
that it is not already open), the template, and builder-mcp. The fresh-run data wipe
(`wipe_old_runs()`) runs separately at the start of Phase A — see "Data retention" below.

## 1. Tickets (SIM-T)
`tickets_pull.py` via builder-mcp `TicketingReadActions`. Pulls the day's OOJ tickets,
explodes them into **Tech_Data** rows (one TID per row). This defines the TID list and
row order everything else aligns to. **Access:** builder-mcp. Fast.

Phase A (`run_daily.py --stop-after-tickets`) stops right after this stage and exits —
it does not run SCC or any later lane itself. The web page's SCC card then shows an
**"awaiting upload"** file-upload control (see stage 2).

## 2. SCC (Station Command Center) — manual CSV upload
SCC's website now blocks automated browsing, so the automated browser lane was
**retired** (`tools/scc_client.py` and its test were deleted; they no longer exist in
this project). SCC is now a **manual step with a pause**:

1. Phase A finishes the SIM-T pull and exits. The web page's SCC progress card shows an
   **"awaiting upload"** badge with a file-upload control.
2. The operator takes the SIM-T TIDs, searches them in SCC in a regular browser, and
   exports SCC's native result CSV by hand.
3. The operator uploads that CSV into the SCC card. The server validates it has the
   expected columns (`SCC_REQUIRED_COLUMNS` — Tracking ID, State, Destination, Source,
   Ship Option) and, on success, **automatically resumes** the run as Phase B
   (`run_daily.py --resume-dir <run> --scc-csv <upload>`), which loads the CSV via
   `load_scc_csv()` into the exact same shape the old lane used to produce (same
   columns, same `scc.csv` handoff file, same `summary.json["scc"]` shape), so every
   downstream lane (EagleEye, PNAMA, DSR, GCRS, Polygon, ADS) is unaffected.

Provides State, Destination, Source (station), Ship Option. There is no automated
browsing, no chunking, and no SCC batch API involved in the live path.

## 3. EagleEye
`eagleeye_client.py` — **API** (MCP endpoint `mcp.eagleeye-api.ats.amazon.dev`) using
the Midway cookie jar. Chunks of 50 TIDs. Returns `searchId` (TID) and `type`
(FORWARD/REVERSE) for the RTO check. No browser, no Edge profile, so once SCC's upload is
loaded it runs on a **background thread that overlaps the PNAMA and DSR browser lanes**.
Fast and reliable.

## 4. PNAMA (missort dashboard)
`pnama_export.py` — **browser** QuickSight dashboard. Opens the dashboard, filters by
the TID list, and exports the raw-data visual to CSV. Feeds the Missort Data sheet. The
**entire** TID list is pasted into one QuickSight control textarea in a single shot — no
chunking.

## 5. DSR (Waterfall dashboard)
`dsr_export.py` (reuses the PNAMA QuickSight driver) — **browser**. Provides the
**Zone** per TID. Like PNAMA, the **whole** TID list goes into one QuickSight control at
once (no chunking). The DSR dashboard is heavy and can take ~3 minutes just to load;
progress streams to the log so it is visible rather than looking frozen.

## 6. GCRS / SIW
`gcrs_mcscli.py` via `siw_client.py` (`FailoverSiwSession`). Calls the SIW `grbi`
endpoint (sequential; 500 per call over mcscli, 200 over the Edge fallback) to resolve each TID's geocoded address + lat/lon and routing
details. **Access:** mcscli first; if mcscli auth fails it switches **once** to the
authenticated Edge session. This is effectively an API path.

## 7. Polygon (jurisdiction check)
`tid_polygon_checker.py` + `polygon_check.py`. Reuses the GCRS lat/lon (no re-fetch),
fetches each station's route polygons (SIW `gs`+`gr`, **realtime** — no cache), and
runs an in-memory point-in-polygon test to decide Inside/Outside jurisdiction. Produces
the "GCRS Agent Space" column and a standalone verification workbook. See
`polygon-checker.md`.

## 8. ADS (pincode prediction)
`ads_run.py` — **browser** driving the ADS Streamlit app. Builds one XLSX
(`TrackingId`, `Concatenated Address Field`) from the GCRS rows and uploads the whole
set in **one file** (the 200MB limit is on file size, not TID count), clicks **ADS with
PP Trigger**, waits for results, and downloads the CSV. Requires per-run approval
(`--allow-plaintext-ads` / the page checkbox) because it uploads addresses to a
plaintext endpoint.

## 9. Fill + recalc
`fill_template.py` writes every source into its template sheet and translates the
Working Sheet row-2 formulas down to all rows. `recalc_excel.ps1` opens Excel to
recalculate so formula columns (State, Zone, AF/AD/AG, etc.) have real values.

## 10. Read-back (verification)
`run_daily.read_back` loads the recalculated workbook and verifies row/TID alignment,
the 25-column final report contract, and that no unexpected Excel errors exist. The
download stays locked unless this passes.

## 11. Apply Remark rules
`apply_remarks.py` + `remark_rules.py` rewrite Working Sheet column **P** to final
literal values per the operator's rules (see `remark-rules.md`). Runs **after**
read-back so it doesn't disturb the verification. Column removal is **disabled** — all
columns are kept (removing them shifted the grid and broke formulas).

---

## Access lanes at a glance

| Source | Lane | Browser? |
|---|---|---|
| SIM-T | builder-mcp | no |
| SCC | manual CSV upload (operator exports from SCC, uploads via the web page) | no (operator's own browser, outside this app) |
| EagleEye | MCP API (Midway cookie) | no |
| PNAMA | QuickSight UI export | yes |
| DSR | QuickSight UI export | yes |
| GCRS | mcscli → Edge fallback | (fallback only) |
| Polygon | SIW gs/gr (via GCRS session) | no |
| ADS | Streamlit UI | yes |

The browser lanes (PNAMA/DSR/ADS) need the automation Edge profile signed in to each
site. EagleEye/GCRS use the Midway session directly. SCC is no longer one of this app's
own browser lanes — the operator exports it manually and uploads the CSV.

## Data retention (fresh-run wipe)

At the start of every fresh Start (Phase A), `run_daily.wipe_old_runs()` deletes **all**
prior run folders under `tools/captures/runs/` and all per-source capture artifacts under
`tools/captures/<source>/` — a hard wipe, not a time-based window. It preserves this
run's own directory and `scc_enum_map.json` (the SCC State enum ground-truth overlay). It
also prunes old published Desktop exports (`OOJ_Process_Filled_*.xlsx` from a previous
day), keeping today's. `prune_history()` (the old 7-day-window pruner) still exists in
`run_daily.py` but is no longer called from the live path — `wipe_old_runs` replaced it
at the Phase-A call site. The startup sanitizer `tools/sanitize_captures.py` (browser
scratch, `.crdownload`/`.tmp`/`.partial`, `~/Downloads` leftovers) still runs at server
start/restart, unchanged.
