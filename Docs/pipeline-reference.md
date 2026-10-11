# Pipeline Reference — stages and sources

Orchestration lives in `tools/run_daily.py` (`main()`). Each stage is wrapped by
`Run.stage()`, which writes live progress to `summary.json` so the web page can show
status. The enrichment lanes are mostly serial, with one overlap: **SCC runs first on
the main thread** (its resolved TIDs feed GCRS/Polygon/ADS), then **EagleEye runs on a
background thread** (it is a pure API, no Edge) and **overlaps the serial browser lanes**
PNAMA and DSR. The browser lanes (SCC/PNAMA/DSR) share one Edge profile and stay serial.

Order: **preflight → tickets → SCC → EagleEye (concurrent, background thread) → PNAMA →
DSR → GCRS → Polygon → ADS → fill/recalc → read_back → apply_remarks**.

---

## 0. Preflight
`run_daily.preflight` — checks a valid Midway session, mcscli, the Edge profile (and
that it is not already open), the template, and builder-mcp. Also runs
`prune_history()` to delete run artifacts older than 7 days.

## 1. Tickets (SIM-T)
`tickets_pull.py` via builder-mcp `TicketingReadActions`. Pulls the day's OOJ tickets,
explodes them into **Tech_Data** rows (one TID per row). This defines the TID list and
row order everything else aligns to. **Access:** builder-mcp. Fast.

## 2. SCC (Station Command Center)
`scc_client.py` — **browser** (`logistics.amazon.in`), using the authenticated Edge
profile. Runs **first** on the main thread because its resolved TIDs become the input
for GCRS/Polygon/ADS. Flow: paste TIDs into the search box → click **Search** → read the
result. TIDs are pasted in chunks of up to **5,000 per search** (`SCC_UI_CHUNK`), with a
count-branched result path: ≤1,000 shows the **Export to CSV** button (click + capture);
>1,000 makes SCC **automatically download** the result file, which the automation detects
and reads. Provides State, Destination, Source (station), Ship Option.
*(The SCC batch API was tried and abandoned — it rejects our request shape with HTTP
400/403; the browser flow is the reliable path.)*

## 3. EagleEye
`eagleeye_client.py` — **API** (MCP endpoint `mcp.eagleeye-api.ats.amazon.dev`) using
the Midway cookie jar. Chunks of 50 TIDs. Returns `searchId` (TID) and `type`
(FORWARD/REVERSE) for the RTO check. No browser, no Edge profile, so once SCC finishes it
runs on a **background thread that overlaps the PNAMA and DSR browser lanes**. Fast and
reliable.

## 4. PNAMA (missort dashboard)
`pnama_export.py` — **browser** QuickSight dashboard. Opens the dashboard, filters by
the TID list, and exports the raw-data visual to CSV. Feeds the Missort Data sheet. The
**entire** TID list is pasted into one QuickSight control textarea in a single shot (no
chunking); chunking is SCC-only (`SCC_UI_CHUNK`).

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
| EagleEye | MCP API (Midway cookie) | no |
| SCC | UI Search → Export CSV | yes |
| PNAMA | QuickSight UI export | yes |
| DSR | QuickSight UI export | yes |
| GCRS | mcscli → Edge fallback | (fallback only) |
| Polygon | SIW gs/gr (via GCRS session) | no |
| ADS | Streamlit UI | yes |

The browser lanes (SCC/PNAMA/DSR/ADS) need the automation Edge profile signed in to
each site. EagleEye/GCRS use the Midway session directly.
