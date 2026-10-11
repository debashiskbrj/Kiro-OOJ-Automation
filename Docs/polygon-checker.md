# Local TID Polygon Boundary Checker — authoritative instructions

Status date: 2026-09-18. This file is the source of truth for running the GCRS/SIW
polygon workflow on this Windows laptop. It preserves the accuracy rules learned in
the former AgentSpace workflow, but **does not use or depend on AgentSpace**.

## 1. Purpose and result

For every non-empty `TID<TAB>STATION` input line, the local checker:

1. resolves the shipment GPS from SIW `/grbi`;
2. obtains the assigned station's live route-zone polygons from `/gs` and `/gr`;
3. tests `Point(longitude, latitude)` against each individual route polygon;
4. classifies the row as Inside, Outside, or Error;
5. writes and reopens a verified Excel workbook.

Every non-empty input line produces one output row. Input order, duplicate lines, and
the same TID paired with different stations are preserved. Network calls may deduplicate
TIDs and carrier/station pairs for efficiency, but materialized output must never be
deduplicated.

The source-system interaction is read-only. No SIW mutation endpoint, ticket write,
local HTTP server, or external file-sharing service is used.

## 2. Local files

| File | Role |
|---|---|
| `tools\tid_polygon_checker.py` | Standalone parser, orchestrator, Excel writer, and verifier |
| `tools\siw_client.py` | Fixed-host SIW authentication transports and one-way failover |
| `tools\gcrs_mcscli.py` | The single sequential `/grbi` resolver and accuracy guards |
| `tools\polygon_check.py` | Station polygon selection, cache, retries, and point-in-polygon logic |
| `tools\gcrs_client.py` | Edge-only SIW diagnostic CLI; delegates GRBI to the shared resolver |
| `tools\captures\polygons\` | One small cached JSON file per `CARRIER_STATION` |
| `tools\captures\polygon_local\` | Standalone run logs |
| `tests\test_polygon_workflow.py` | Offline regression coverage |

The OOJ template sheet named `GCRS Agentspaces ` and final column named
`GCRS Agent Space` are retained only because those are existing Excel contracts. They
do not imply an AgentSpace runtime dependency.

## 3. Input contract — non-negotiable

Input is a UTF-8 or UTF-8-BOM text file. Each non-empty physical line is:

```text
<TID><TAB><STATION>
```

Rules:

- A valid numeric TID is exactly 12 digits.
- A valid document TID is `DOC` followed by digits; matching is case-insensitive and
  the SIW lookup key is normalized to uppercase.
- Numeric TIDs route to `ATS!DEFAULT`.
- `DOC...` TIDs route to `ATA!DEFAULT`; never reject them for being non-numeric.
- Blank lines are ignored. Every other line is retained.
- A missing tab, more than one tab, blank/invalid TID, or other malformed line becomes
  one `Error — Invalid input line: ...` row; it is never silently dropped.
- A valid TID with an empty station becomes `Error — Missing station code`.
- `#N/A` station text is preserved and normally becomes an explicit station lookup
  error.
- Duplicates remain duplicates. The same TID at two stations is evaluated twice,
  once against each station's polygons.

Before accepting a result, the verifier must prove:

```text
Excel result rows == parsed non-empty input rows
```

It also compares every ordered `(Tracking ID, Station, GCRS Status)` row after reopening
the saved workbook.

## 4. Local authentication and failover

SIW host is fixed to:

```text
https://siw-in-dub.dub.proxy.amazon.com
```

The fixed-host data POST allowlist is: `gpsn`, `grbi`, `gs`, and `gr`. Edge startup
also navigates to the same SIW origin's `/query` page and reads `/sso/login` solely to
establish authenticated session state.

`tools\siw_client.py` uses this state machine:

1. In default `auto` mode, call SIW `POST /gpsn` through `mcscli`.
2. Select mcscli only when the process succeeds, HTTP is 200, JSON parses, and a
   non-empty polygon-set list is returned.
3. On missing mcscli, HTTP 401/403, timeout, non-JSON/login HTML, or another transport
   failure, switch exactly once to same-origin `fetch` inside the persistent local Edge
   profile.
4. Edge uses the existing Amazon Enterprise Access/Midway session and
   `credentials: include`. If a login page appears, authenticate in the opened window.
5. All Playwright operations are owned by one worker thread. Python polygon workers
   submit calls to that thread, so the Edge page is never used concurrently from
   multiple threads.
6. After Edge is selected, never switch back to mcscli during the run and never fall
   back to AgentSpace.

On 2026-09-18, `mcscli is-valid` reported a valid local session while direct SIW
requests returned HTTP 401 with redirects followed and HTTP 302 when redirects were
correctly rejected. This proves that token freshness alone is insufficient; the real
SIW API probe is mandatory. Repeated live local auto-mode runs rejected mcscli, opened
authenticated Edge once, resolved `/grbi` 1/1, refreshed BRSG `/gs` + `/gr`, classified
1 Inside / 0 Outside / 0 Errors, and reopened the XLSX with `VERIFY OK`. Edge is the
verified local fallback until a future mcscli probe succeeds again.

`mwinit -o` remains required for the separate SIM-T/EagleEye cookie-jar lanes. It is
not used as proof that SIW mcscli works. The former AgentSpace instruction “never run
mwinit” applied only inside that managed cloud environment, not to this Windows laptop.

## 5. SIW address/GPS resolution rules

`POST /grbi` fields:

```text
polygonSetName = ATS!DEFAULT or ATA!DEFAULT
idType          = tracking_id
ids             = comma-separated TIDs
effectiveDate   = empty
```

Accuracy rules:

1. `/grbi` is strictly sequential. Never issue parallel GRBI requests.
2. Batch size is transport-aware: 500 over mcscli (the SIW-supported maximum), 200 over
   the Edge fallback (500 over Edge triggers degraded all-invalid windows and "Failed to
   fetch"). Re-evaluated per batch so a mid-run failover drops to 200 immediately.
3. A TID is “not found” only when a trustworthy SIW response explicitly lists it in
   `invalidIds`. Missing from `routeDetailsArray` is not sufficient.
4. A zero-row response for more than one TID is never trusted, even when every TID is
   listed in `invalidIds`; isolate outcomes by recursive bisection.
5. Degraded-window v1: suspicious fast/empty or large unresolved responses are retried
   with 2/4/6/8-second backoff before accepting outcomes.
6. Degraded-window v2: if at least 50 requested IDs appear in one `invalidIds` block,
   back off and re-verify those IDs.
7. A poison TID can invalidate a whole batch. Bisection continues until valid rows and
   genuine singleton invalid IDs are separated.
8. IDs still absent after all guards are unresolved errors, not confirmed not-found.
9. Numeric and DOC TIDs are queried in their respective polygon sets; malformed IDs
   are never sent to SIW.

## 6. Polygon retrieval and cache

For every unique `(carrier, station)` that has resolved coordinates:

1. `POST /gs` with `stationNames=<station>`.
2. Select active records for the required carrier.
3. Prefer the active default record with no explicit ship method; otherwise use the
   active carrier record.
4. When several records qualify, choose the newest `effectiveDate`.
5. `POST /gr` with the station record ID and operator.
6. Retry transient route-fetch failures twice, approximately 1.5 seconds apart.
7. Keep every active route geometry separately.

Polygon requests may be prefetched with up to eight workers. mcscli requests can run
in parallel. When Edge is active, the transport safely serializes browser fetches on
its owner thread.

Cache files are:

```text
tools\captures\polygons\ATS_<STATION>.json
tools\captures\polygons\ATA_<STATION>.json
```

They contain individual route geometries, not a merged station polygon. Normal
standalone runs may reuse a valid cache. Use `--refresh-polygons` for live redraws or
a clean baseline. The daily OOJ pipeline always refreshes polygon data. A corrupt or
schema-invalid cache is ignored and fetched again. Writes use a temporary file followed
by atomic replacement.

## 7. Geometry rules — never change casually

- Evaluate GeoJSON as `longitude, latitude`; never swap coordinate order.
- A shipment is Inside when its point falls in **any one individual route geometry**.
- Never use `unary_union` or otherwise merge route polygons. Merging can fill natural
  inter-route gaps and create false Inside results.
- Supported geometry types are Polygon, MultiPolygon, and GeometryCollection.
- The dependency-free local ray-casting implementation currently treats an outer
  polygon boundary as Inside and a hole/hole-boundary as Outside. Exact edge-point
  parity with the portal remains an explicit validation item if a known boundary
  fixture becomes available.

## 8. Exact output contract

The standalone workbook is written to the Windows Desktop by default with a name like:

```text
TID_Check_BULK_<N>stns_<YYYYMMDD_HHMMSS>.xlsx
```

`Results` sheet columns are exactly:

```text
Tracking ID | Station | GCRS Status
```

Status cell values are exactly:

- Inside: `Invalid - It is Inside Jurisdiction as per GCRS Tool`
- Outside: `Outside`
- Error: `Error — <reason>`

The workbook also contains a `Summary` sheet with Total, Inside, Outside, Errors,
unique lookup TIDs, unique carrier/station pairs, selected transport, fallback count
and reason, and the explicit `Rows == Input` value.

The writer:

- stores TIDs and stations as literal text, not formulas or floating-point numbers;
- uses green/ red/ amber status fills;
- saves through a same-directory temporary file and atomically replaces the target;
- reopens the final XLSX;
- verifies exact headers, row count, ordered row identity, allowed status vocabulary,
  and summary totals;
- calculates SHA-256 only after the reopened workbook passes.

There is no DevSpace proxy on the laptop. Return the absolute local path (and attach the
file in chat when requested); do not start an unauthenticated local HTTP server or
invent a DevSpace URL.

## 9. Error text and action

| Result | Meaning / action |
|---|---|
| `Error — Invalid input line: ...` | Bad physical input; correct the source row |
| `Error — Missing station code` | Valid TID but no station supplied |
| `Error — TID not found in SIW` | Trustworthy `invalidIds` confirmation |
| `Error — SIW unresolved after degraded/poison guards` | SIW did not provide a trustworthy outcome; retry later |
| `Error — SIW unavailable: ...` | Both selected authentication/transport paths failed |
| `Error — Station '<X>' not found in SIW` | Station not registered for the selected carrier |
| `Error — No active <carrier>!DEFAULT polygon set...` | Station exists but no qualifying active set |
| `Error — No polygon geometry found...` | No usable route geometry is published |
| `Error — Invalid polygon/GCRS data: ...` | Missing coordinates or malformed geometry |

A download or file save alone is not success. Only `VERIFY OK` plus equal row counts
allows the result to be presented as complete.

## 10. How to run locally

1. Ensure no other Edge process is using the automation profile. Do not kill a user's
   browser automatically; close the profile normally if it is locked.
2. Save the pasted batch exactly to a UTF-8 text file, preserving tabs and duplicate
   lines. Count non-empty lines.
3. Run from the workspace root:

```cmd
py -3.13 -B tools\tid_polygon_checker.py path\to\input.txt --refresh-polygons
```

Useful options:

```cmd
--output C:\path\result.xlsx
--transport auto|mcscli|edge
--refresh-polygons
--overwrite
```

4. If Edge opens a Midway page, authenticate in that window and let the command
   continue.
5. Accept the output only when logs show all of:

```text
SAVED <absolute path>
Total=<N> Inside=<N> Outside=<N> Errors=<N>
Rows == Input: <N> == <N> (VERIFY OK)
```

6. For “link not working” on Windows, provide or attach the same verified local file;
   do not rerun SIW unless the user requests fresh data.

## 11. Daily OOJ integration

`tools\run_daily.py` uses the same local components:

1. SCC, PNAMA, and DSR browser workers finish and release the persistent profile.
2. One `FailoverSiwSession` is opened.
3. Unique TIDs are resolved through the shared sequential GRBI engine.
4. Polygon inputs are built from every ordered Tech_Data row using SCC Source as the
   current station source, with ticket-description station as fallback.
5. The same SIW session fetches polygons and produces one row per Tech_Data row.
6. `polygon.csv` and a verified `TID_Polygon_Check_<stamp>.xlsx` are saved in the run
   directory.
7. The template's legacy `GCRS Agentspaces ` sheet receives the audit rows.
8. Working Sheet column AG is written directly row-by-row. It no longer relies on a
   TID-only VLOOKUP, so duplicate/same-TID-different-station rows remain correct.
9. No AgentSpace file, handoff text, or cloud-side polygon workflow is required.

## 12. Preflight and postflight checklist

Before:

- [ ] Input file is UTF-8 and tabs are preserved.
- [ ] Non-empty input line count is known.
- [ ] Edge automation profile is not locked.
- [ ] For daily runs, `mwinit -o` is fresh for SIM-T/EagleEye.
- [ ] No write action to SIM-T or another source system is enabled.

After:

- [ ] Result rows equal non-empty input rows.
- [ ] Ordered duplicate `(TID, station)` pairs are preserved.
- [ ] Output has only the exact Inside, Outside, or `Error — reason` forms.
- [ ] Summary totals match recalculated result totals.
- [ ] `VERIFY OK` is present.
- [ ] The reported path points to the current timestamped workbook.
- [ ] Raw TIDs, GPS, station polygons, and files remain on approved Amazon/local
      corporate storage; only aggregate counts are used outside that context.
