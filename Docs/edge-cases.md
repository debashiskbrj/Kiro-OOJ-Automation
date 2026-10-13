# edge-cases.md — LIVE DOC

> Every gotcha, failure signature, and hard-won rule. **Update in place** when a new
> one is found; add a dated line to `changes.md`. Check this before debugging —
> the answer is probably already here.

Format: `#ID — [source] symptom → cause → rule`.

---

## SIM-T

- **#S1 — `shortId` is not a responseField.** API rejects it; request `aliases` and
  read `aliases[0].id`.
- **#S2 — TIDs live in free text.** Extract with `DOC\d+|\b\d{12}\b` over the
  description (user's SWA OOJ Ticket Analyzer rule): captures 12-digit numeric AND
  `DOC`-prefixed TIDs; unique, order preserved. Pin codes are 6 digits, phones 10, so
  they never match. A ticket with no TID still emits ONE row with the literal
  `TID not found` (never blank).
- **#S3 — station code comes from the description ONLY (CORRECTED 2026-09-17).**
  Rule: `Station\s*code\s*:\s*([A-Za-z0-9]+)`; blank if the line is absent.
  **NEVER use `extensions.tt.buildingId`** — it is the *routed* building and often
  differs from the real station (observed buildingId=IXEE vs description IXEP).
  Earlier guidance here preferred buildingId; that was wrong. Normalize to uppercase
  (`vtzh` seen).
- **#S7 — zone from the `Zone:` line** (`Zone:\s*(\w+)`), blank if absent.
- **#S8 — one TID per row (explode).** Ticket-level fields repeat per row; keep a
  globally unique `Sl` index. Verify `rows == sum(len(tids) or 1)`.
- **#S9 — point-in-time snapshot.** Add `createDate:[* TO <UTC now>]` to the query so
  cursor pagination stays stable while new tickets arrive; loop until
  `collected == totalResults`, an empty page, or a non-advancing `startToken`. TIDs
  appended to a ticket after the run legitimately won't be in that run's file
  (P483125010 audit) — re-run rather than "fix".
- **#S10 — live backlog swings.** Totals range roughly 56 → ~1,800 day to day; a big
  change is expected, not a defect. Re-probe `totalResults` if a run lands exactly on
  a page boundary.
- **#S4 — external tickets** (`[EXTERNAL] - ...`, created by gmail users) are part of
  the normal flow — do not filter them out.
- **#S5 — duplicate TIDs** within one ticket and across tickets. Dedup for enrichment
  but keep the ticket↔TID mapping so every ticket gets its comment lines.
- **#S6 — SOP cut-off semantics**: edits to a ticket after its query run are not
  reflected in that run's file. When comparing with the user's manual raw files,
  match on the run timestamp, not "now".

## EagleEye

- **#E1 — Region must be `EU` for India.** With `region: NA` or omitted, IN TIDs can
  return nothing or slower multi-region scans.
- **#E2 — RTO signal verified (2026-09-18).** A live EagleEye lookup returned
  `package_type: REVERSE` for an RTO shipment. The template rule `REVERSE` →
  `Invalid - Already RTOed` is enabled. Keep the field uppercase and retain a known
  fixture for regression checks.
- **#E3 — stale TIDs return "TCDA: No data found"** (seen in the UI for an old TID).
  Treat as no-data, not as an error; the SCC/SIW lanes may still resolve it.
- **#E4 — 50 IDs max per `eagle_eye_scannable_search` call.** Chunk accordingly.
- **#E5 — cookie jar parsing**: `~\.midway\cookie` is Netscape format but mwinit
  writes `#HttpOnly_` prefixes that `MozillaCookieJar` treats as comments. Strip the
  prefix before loading (done in `test_eagleeye_mcp.load_midway_cookies`).
- **#E6 — quote characters**: the manual process strips `'` from EagleEye bulk-search
  exports before pasting into the template. Not applicable to the MCP lane (JSON),
  only if a UI export is ever used again.

- **#E7 — high-volume EagleEye UI path (user-confirmed 2026-09-18).** On Home,
  choose format `Leg`, enable `Bulk Search`, select `Package Leg Detail`, then click
  `Bulk Search`; the UI supports the large downloadable search-result path (up to
  10,000 entries per user guidance). The current run used the MCP lane successfully;
  preserve this UI path as the preferred large-volume browser fallback.

## SCC

> **RETIRED 2026-10-12 — SCC automation is gone.** The SCC site now blocks automated
> browsing, so the automated browser lane was removed: `tools/scc_client.py` and
> `tests/test_scc_retry.py` were **deleted and no longer exist**. SCC is now a **manual
> CSV upload** — the operator searches the SIM-T TIDs in SCC by hand, exports the native
> CSV, and uploads it through the web page, which loads it via `run_daily.load_scc_csv`
> into the same handoff shape. The entries below (#C1–#C9) are kept as a **historical
> log** of the retired lane; they describe code that is no longer present. Constants
> like `SCC_UI_CHUNK` / `AUTO_DOWNLOAD_THRESHOLD` and files like
> `scc_failed_tids_<stamp>.txt` no longer exist in the live path.

- **#C1 — the search UI lives in an iframe** (`ui.package-summary-inzaz.last-mile
  .amazon.dev`). `page.locator("textarea")` on the top page finds nothing; iterate
  frames and use the one containing a textarea.
- **#C2 — direct HTTP fails outside the browser.** `requests` + midway jar →
  Sentry SSO redirect (`sentry.amazon.com /SSO/redirect ... acr_values=kerberos`)
  then corporate TLS interception (`CERTIFICATE_VERIFY_FAILED`). Rule: call the SCC
  API from inside the authenticated page (`page.evaluate(fetch)`) or use the UI's
  Export to CSV. (`mcscli curl` against logistics.amazon.in: untested, worth one try.)
- **#C3 — Export to CSV returns only the visible/default columns.** The API JSON is
  richer (newStationCode, reasonCode, timestamps). Prefer the API response; keep CSV
  as the human-comparable artifact.
- **#C4 — >1000 TIDs**: the UI has a "Download Default columns only (Use when
  submitting >1000 TrackingIds...)" checkbox — large batches are expected to be
  chunked; assume ~1000 per search to be safe.
- **#C5 — first Playwright navigation may abort.** The AEA (Amazon Enterprise
  Access) extension intercepts and re-navigates → `net::ERR_ABORTED` on `goto`.
  Catch it, wait, poll `page.url` until the app loads. NEVER launch the profile with
  extensions disabled (Playwright default) — pass
  `ignore_default_args=["--disable-extensions", "--disable-component-extensions-with-background-pages", "--disable-component-update", "--disable-background-networking"]`,
  otherwise the page shows "AEA extension not installed" and nothing authenticates.

- **#C6 — SCC has two result paths (user-confirmed 2026-09-18).** For ≤1,000
  requested TIDs, wait for Search Result and click `Export to CSV`; process in ≤500
  chunks so the fresh API response and CSV can be correlated. For >1,000, select the
  exact `Download Default columns only` checkbox, click Search once, and wait for the
  automatic download. Use a unique download directory per attempt.
- **#C7 — bulk automatic download caps at 999 data rows.** The 1,496-TID run
  downloaded 999 rows automatically. Compute the exact missing set, then recover it
  with the small Search Result → Export-to-CSV path. Current result: 1,495 rows,
  one SCC no-data TID, zero extras, zero duplicates.
- **#C9 — 5,000-per-search with count-branched result path (added 2026-09-29).** SCC now
  pastes up to 5,000 TIDs per Search. The result path is chosen by count: **≤1,000** shows
  the `Export to CSV` button (click + capture, as before); **>1,000** ticks the ">1000"
  checkbox and SCC **automatically downloads** the result file — no Export button appears,
  so the automation detects the freshly downloaded file in the per-attempt download dir and
  reads it. Constants: `SCC_UI_CHUNK=5000`, `AUTO_DOWNLOAD_THRESHOLD=1000`.
- **#C8 — failed-not-queried ≠ not-found (added 2026-09-28).** A chunk whose
  Search/Export fails every refresh retry used to be dropped as generic "no-data",
  indistinguishable from TIDs SCC genuinely has no row for. `scc_client.py` now marks
  such TIDs as *failed-not-queried*, writes them to `scc_failed_tids_<stamp>.txt`, and
  logs them separately from real not-found. Re-run (or `--resume-existing`) to fill
  them. Separately, a chunk that returns **zero rows** is re-verified once with a tab
  refresh before being accepted, so a transient empty export cannot silently drop a
  whole chunk of real data.

## GCRS / SIW

- **#G1 — degraded window v1 (empty-200).** A `/grbi` batch that normally takes
  ~30 s returns in ~6 s with a valid-but-empty 200. If a large block of TIDs is
  still unresolved after a quick retry, back off 2/4/6/8 s and re-fetch before
  accepting any "not found". Timing is the tell: short batch = bad batch.
- **#G2 — degraded window v2 (false invalidIds).** Same bad window can dump 50–400
  VALID TIDs into `invalidIds`. If `invalidIds ∩ batch ≥ 50` in one batch, back off
  and re-verify before accepting. A real invalid stays invalid on a healthy retry.
- **#G3 — poison batch.** One genuinely bad TID can void a whole response. If a
  multi-TID chunk comes back all-empty, bisect it to isolate the true invalids.
- **#G3b — poison batches come back as `invalidIds`, not as gaps (VERIFIED locally
  2026-09-17).** `grbi` with 5 good TIDs + `818000000000` returned 0 rows and listed
  **all 6** in `invalidIds` (1.0 s); the same 5 alone returned 5 rows. So #G6
  ("invalidIds is the only not-found signal") only holds for batches with ≥1 row.
  Rule in `gcrs_mcscli.py`: a zero-row batch with >1 TID is never trusted — bisect to
  singletons first, then accept `invalidIds`. Expect one such poison per run whenever
  a ticket contains a typo'd/foreign 12-digit number (#G7).
- **#G4 — concurrency.** `/grbi` is strictly sequential; parallel or oversized
  calls caused empty responses and mass false not-found results. Polygon `/gs` + `/gr`
  prefetch may use up to 8 workers because carrier/station lookups are independent.
  When Edge is selected, its owner-thread queue safely serializes browser operations.
- **#G5 — TID → polygon set routing**: numeric → `ATS!DEFAULT`, `DOC…` →
  `ATA!DEFAULT`. DOCx are valid TIDs, never reject as non-numeric.
- **#G6 — invalidIds is the only "not found" signal.** Missing-from-response ≠
  invalid (could be #G1/#G3).
- **#G7 — 8xx-series 12-digit numbers** (818…–825…, and stray ids like 671714571213)
  are real 12-digit tokens but not ATS TIDs — expected `not_found`, not a bug.
- **#G8 — transport-aware sequential batch + real auth probe.** `/grbi` is issued
  sequentially with a transport-aware batch size: **500** over the fast mcscli subprocess,
  but **200** over the Edge fallback. Run 20260929 showed 500-ID batches over Edge
  returning degraded all-invalid windows (batch 1 took 70 s; batches 2–3 came back
  `0 rows, 500 invalid`) and eventually dying with `TypeError: Failed to fetch` — the
  large JSON response overwhelms the single in-page fetch/CDP round-trip. The size is
  re-evaluated per batch, so a mid-run mcscli→Edge failover immediately drops to 200. A
  path-exists or `mcscli is-valid` check is not authentication proof;
  require a real read-only `POST /gpsn` returning HTTP 200 + non-empty JSON before
  selecting mcscli. **Mitigations (2026-10-04).** A single Edge `/grbi` batch that
  throws `Failed to fetch` (or returns a degraded/empty window) now retries up to 3x
  with 2s/4s/6s backoff; if it still fails, that batch's TIDs are recorded as `errored`
  (row-aligned `Error — SIW unresolved…`) while the remaining batches continue, so one
  bad batch no longer blanks the whole gcrs+polygon+ads chain. Additionally, a read-only
  preflight auth gate probes SIW once with `mode='mcscli'` (never opening the Edge
  profile) and classifies `gcrs_transport` as `mcscli` / `edge-fallback` / `unknown`,
  logging a WARNING when the run would be forced onto the fragile Edge fallback
  (warn-only; the run is not hard-blocked).
- **#G9 — polygon semantics are local and authoritative.** A point is Inside if it
  falls in ANY one individual route polygon. NEVER `unary_union`; use
  `Point(longitude, latitude)`. `/gs` + `/gr` may prefetch up to 8 carrier/station
  pairs. Cache one small `ATS_<STATION>.json` / `ATA_<STATION>.json` file per pair,
  never a monolithic cache.
- **#G10 — mcscli token-valid does not mean SIW-authorized (verified 2026-09-18).**
  On Windows, both mcscli session and posture reported valid while direct SIW requests
  returned HTTP 401 when redirects were followed and HTTP 302 when redirects were
  correctly rejected. Root cause is an authorization/auth-mechanism mismatch at the
  SIW request, not an expired `~\.midway\cookie`. `siw_client.py` therefore probes
  SIW itself and switches once to authenticated Edge. Do not repeatedly run
  `mwinit` as an SIW fix.
- **#G11 — one-way transport fallback.** A failed real mcscli probe selects the
  persistent Edge same-origin-fetch lane for the whole run. Never switch back and
  never use AgentSpace as a runtime fallback. Playwright objects stay on one owner
  thread; worker calls are queued.
- **#G12 — preserve physical polygon input rows.** Network lookups may deduplicate
  TIDs and `(carrier, station)` pairs, but standalone output must preserve every
  non-empty input line, order, duplicates, and same-TID/different-station pairs.
  Final polygon row count must equal parsed input count.
- **#G13 — row-aligned template status.** The legacy `GCRS Agentspaces ` sheet name
  remains for workbook compatibility, but Working Sheet AG is written directly by
  Tech_Data row. A TID-only VLOOKUP cannot safely represent one TID checked against
  two stations.
- **#G14 — verify after save.** Reopen the final polygon XLSX and compare exact
  headers, ordered `(TID, station, status)` rows, status vocabulary, and summary
  counts. Only then print `VERIFY OK` and calculate SHA-256.
- **#G15 — local result delivery.** Windows has no DevSpace proxy. Return/attach the
  verified local XLSX path; never start an unauthenticated laptop HTTP server or
  invent a DevSpace URL. Complete procedure: `Docs\polygon-checker.md`.

## OBLT (OUT OF SCOPE since 2026-09-17 — kept for reference only)

- **#O1 — OAuth loop under automation.** The Mercury SPA bounces to
  `/apps/oblt/authorize?code=...` (Cognito + STS) and, under Playwright, lands on a
  blank authorize page instead of the app. Re-navigating immediately re-triggers
  authorize. Not pursued further: the user removed OBLT from the pipeline scope.

## ADS Streamlit

- **#A1 — do NOT open ADS in edge_profile.** The AEA extension aborts navigation to
  the raw-IP URL (`net::ERR_ABORTED`, page stays `about:blank`) and never re-navigates.
  ADS needs no auth, so launch a clean Edge (`chromium.launch(channel="msedge")` +
  `new_context`) instead. Verified: same URL loads instantly there.
- **#A2 — the file input is hidden by design.** Streamlit's
  `[data-testid=stFileUploaderDropzoneInput]` is `display:none` behind the dropzone;
  `wait_for_selector` (default state=visible) times out. Wait with `state="attached"`
  and call `set_input_files` directly — it works on hidden inputs.
- **#A3 — XLSX only, two columns, exact headers.** The app says "Choose the File in
  XLSX format"; input must be `TrackingId`, `Concatenated Address Field` (the
  template's "ADS format" sheet). Build with openpyxl; keep TrackingId as a string.
- **#A4 — click the trigger AFTER the upload registers.** The button exists before
  upload; clicking early does nothing useful. Wait until the file name and size appear
  in the page text (and the preview dataframe renders), then click.
- **#A5 — completion signal = download button present AND no spinner.** Streamlit
  reruns the script on every interaction; poll `stDownloadButton` + `stSpinner`
  rather than fixed sleeps. 3 TIDs took ~15 s; expect roughly linear growth — budget
  minutes, not seconds, for 1–2k TIDs, and chunk large runs (e.g. 500/upload) so a
  single failure does not lose the whole batch.
- **#A6 — output quirks.** Leading unnamed pandas index column; `predicted_pincode_
  BiLSTM` is a float (`335504.0`) — cast via int before comparing with the 6-digit CX
  pincode string; `TRACKINGID` may come back numeric — normalize to string. Suggested
  download name is always `ads_pp.csv`; rename per run.
- **#A7 — verify counts from the UI text.** The app prints "No of Tracking IDS in the
  File: N" — assert it equals the uploaded row count and the CSV row count.
- **#A8 — the tool has no owner-facing API.** Everything travels over the Streamlit
  WebSocket; there is nothing to call with requests/mcscli. A SageMaker/batch endpoint
  from the model team (contact rissahay) would be the only headless alternative.

## QuickSight / PNAMA

- **#Q1 — use the visual menu, not the dashboard menu.** After applying the
  `tracking_id` filter, target the `SWA repeat_Prox_Pnama_match Raw data` visual.
  Its export control is the bottom-right `⋮` menu shown in the user screenshot;
  choose **Export to CSV** (preferred) or **Export to Excel**.
- **#Q2 — avoid fixed screen coordinates.** QuickSight layout changes with viewport,
  zoom, loading and hidden controls. In Playwright, locate the raw-data visual by its
  title, then scope the `More options`/ellipsis locator to that visual. Use the
  screenshot coordinate only as a last-resort fallback.
- **#Q3 — filters must be proven in the exported file.** Do not treat a completed
  download as success. Normalize tracking IDs as strings, then assert every exported
  TID belongs to the requested set and report requested/exported/missing/extra counts.
  Fail the run if unrelated TIDs appear.
- **#Q4 — wait for the visual to refresh.** After changing `tracking_id`, wait for
  QuickSight loading indicators to clear and for the raw-data visual to render before
  opening `⋮`; otherwise the export can contain the previous filter state.
- **#Q5 — WORKING since 2026-09-17** (`pnama_export.py`, 5/5 validated). Kept as the
  reminder that "download happened" ≠ "filter applied" — #Q3 stays mandatory.
- **#Q6 — the tracking_id control is a MUI Select whose popover is ONE TEXTAREA**
  (`[role=listbox] textarea`), one TID per line. It is NOT a checkbox list and has no
  search box; `page.keyboard.type` into the closed select does nothing. Open the
  select, Ctrl+A in the textarea, `fill("\n".join(tids))`, then **Escape** — closing
  the popover is what applies the value.
- **#Q7 — the Controls bar is a collapsed strip.** Its `sheet-control-panel-toggle-
  expand` button COLLAPSES an already-open panel (it toggles). Reliable path: click
  the `tracking_id equals …` chip text itself, which expands the grid.
- **#Q8 — the per-viewer state persists.** The control retained a 1,146-line TID
  list from earlier manual work ("Original dashboard (Modified)"). Always replace the
  whole textarea; never append. "Reset to original" is available in the navbar if the
  sheet ever gets into a strange state.
- **#Q9 — refresh takes ~40 s and the table may render 0 grid cells to the DOM probe**
  even when visible (virtualized). Don't gate on cell counts alone; gate on the
  loading indicators clearing plus a fixed settle, then validate the export.
- **#Q10 — export download name is `SWA_repeat_Prox_Pnam_<epoch>.csv`;** rename per
  run. TIDs export as clean 12-digit strings (no float artefacts).
- **#Q11 — `station_missort` has a third value `TBC`** besides 0/1 (no matched earlier
  delivery yet); the remark engine must treat non-0/1 as "fall back to ADS/AQS".
- **#Q12 — QuickSight login in the automation profile:** first ever run lands on the
  Midway/federation page (`/sn/auth/signin` → midway-auth). Complete it once in the
  window; the profile remembers it (subsequent runs loaded with no prompt).

- **#Q13 — loaded QuickSight page may expose no role-tab initially.** A visible
  dashboard with `/sn/account/` URL, target-tab text, or Controls + tracking field is
  loaded even if `[role=tab]` is temporarily absent. Do not misclassify it as login;
  downstream exact tab, filter, exported-ID, and schema checks remain mandatory.
- **#Q14 — DSR missing/blank Zone is source no-data, not a fabricated value.** Reject
  extras, duplicates, conflicts, or missing required columns. Keep missing/blank Zone
  blank in the template using a blank-safe lookup; never let Excel convert it to `0`.

## Template / Excel

- **#T1 — col F `#REF!`**: `VLOOKUP(E2,#REF!,2,0)` — source sheet was deleted.
  MOOT: column dropped from scope 2026-09-16 (with G and H).
- **#T2 — H2 formula points at a fixed row** (`Tech_Data!H1886`) — fragile
  row-dependent check; do not replicate.
- **#T3 — array formulas** use `_xlfn.IFNA/_xlfn.IFS/_xlfn.LET/_xlpm.` prefixes in
  the XML; openpyxl reads them but writing them back needs care (write values, not
  formulas, into output workbooks).
- **#T4 — remark strings must match EXACTLY** (stations key off them). Note the
  en-dash in "mismatch found – LM to reattempt" and the trailing spaces in some
  headers ("Destination ", "CX Pincode "). Copy strings from the template, never
  retype.

## Windows / PowerShell / tooling

- **#W1 — console output gets swallowed** on long Playwright runs (PSReadLine +
  redirection). Rule: scripts write their own log files under `tools\captures\`;
  read the file, don't trust the console.
- **#W2 — `Expand-Archive` progress bars** flood tool output; silence with
  `*> $null` or use Python zipfile.
- **#W3 — Playwright + Edge profile lock**: a crashed run leaves `msedge.exe`
  holding the profile; kill via
  `Get-CimInstance Win32_Process -Filter "Name='msedge.exe'" | ? CommandLine -like '*edge_profile*'`
  before relaunching.
- **#W4 — mcp-registry CLI is not available for Windows** (`toolbox install
  mcp-registry` → unsupported OS). EagleEye MCP is reached with the hand-rolled
  JSON-RPC client instead — works fine.
- **#W5 — corporate TLS interception** breaks Python `requests` to some SSO hosts
  regardless of cookies (see #C2). Don't chase cert bundles; switch lanes
  (browser-context fetch or mcscli).

- **#W6 — foreground command wrapper can falsely report Edge launch failure.** The
  same clean/profile Playwright launch printed no completion marker through the
  foreground executor but printed `*_LAUNCHED` and `*_CLOSED` through a managed
  background process. Verify GUI launches with managed process output.
- **#W7 — do not auto-kill all WebView2 applications.** WebView2 processes respawned
  and Edge still launched successfully, so they were not proven to be the blocker.
  `edge_preflight.py` only detects exact `--user-data-dir` ownership and may remove
  dedicated-profile lock files with explicit `--repair` when no owner exists; it never
  kills Edge, Teams, Outlook, Widgets, or WebView2.

## Process / SOP

- **#P1 — write actions are irreversible-ish**: posting comments/resolving tickets
  at 500-ticket scale must stay behind an explicit per-run approval + a dry-run diff
  ("would post X comments to Y tickets").
- **#P2 — GCTD / HYT2 / BOMC special-cases** override normal logic (all Valid per
  template; HYT2 13+ borderline → UNDEL; BOMC → UNDEL unchecked). Keep these in a
  config block, they change by leadership instruction.
- **#P3 — critical shipper lists** (Decathalon; mamaearth/derma co/aqualogica/
  bblunt/staze/Dr. Sheth's) are hardcoded in the template today; move to config,
  expect edits.
