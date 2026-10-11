# Troubleshooting — known failures and fixes

Grab the log first: on the web page, open **Live log** and click **Copy log**. Failing
browser steps also save screenshots under `tools/captures/<source>/`.

---

## SCC

**"SCC not authenticated" / stuck on a login page**
The automation Edge profile isn't signed in to `logistics.amazon.in`. Sign in there
once in that profile, then re-run.

**SCC bulk search not returning / team says bulk search is down**
This is server-side on SCC's end, not the automation. Wait for the team's fix. Our
lane is read-only and low volume; it doesn't cause a team-wide outage. If you need the
file meanwhile, re-run with **Allow partial download** ticked (SCC sheet blank).

**Export to CSV never appears for a chunk**
A screenshot is saved as `tools/captures/scc/scc_chunk*_noexport_*.png` and the log
names the chunk. Usually a transient UI/load issue; re-run. The Search/Export clicks
already scroll-into-view and fall back to a JS click, so "element outside viewport" is
handled.

**Log says "N TID(s) could not be queried after retries"**
One or more chunks failed every refresh retry, so those TIDs were never actually
searched (distinct from TIDs SCC simply has no row for). They are written to
`tools/captures/scc/scc_failed_tids_<stamp>.txt`. Re-run the pipeline, or fill just
those TIDs with `scc_client.py --resume-existing <previous scc.csv>`. A chunk that
returns zero rows is now re-verified once with a refresh before being accepted, so a
transient empty export no longer silently drops real data.

*(Note: the SCC batch API path was abandoned — it returns HTTP 400/403 for our request
shape. SCC uses the browser Search → Export flow.)*

---

## PNAMA / DSR (QuickSight)

**"login page — please authenticate in the browser window"**
The QuickSight session isn't signed in to the automation Edge profile. Sign in to the
PNAMA and DSR dashboards there once, then re-run. One sign-in usually covers both.

**DSR looks stuck for a few minutes**
The DSR dashboard is heavy and can take ~3 minutes to load. Watch the live log — it
streams `dashboard loaded`, `tab selected`, `visual idle`, etc. If it truly hangs it
has a 20-minute worker timeout that kills it and reports the failure.

---

## ADS

**ADS stuck with no Edge window / "host may be down"**
The ADS Streamlit host (`44.203.89.162:8192`) has been intermittently unreachable. The
lane now uploads the whole set in one file and fails fast with a clear message if the
app doesn't load after 3 attempts. Re-run when the host is back.

**Column name**: the ADS input's first column must be `TrackingId` (kept fixed in
`ads_run.build_input_from_rows`). Don't change it.

---

## GCRS / Polygon

**mcscli returns 302/401, "switching once to authenticated Edge"**
Expected — the SIW lane probes mcscli and falls back to the authenticated Edge session
automatically. Not an error.

**Many polygon "sequential retry failed" lines / high polygon errors**
Transient SIW-side issues fetching some stations' geometry (realtime, no cache). Those
rows get an Error status in the polygon column; the run still completes. If a specific
station consistently fails, it may genuinely have no polygon.

---

## Edge profile

**"The automation Edge profile is open; close that window and retry"**
An Edge window is using the automation profile. Close it and re-run. `edge_preflight.py`
detects only that exact profile and never kills unrelated Edge/Teams/Outlook windows.

---

## Web app / ports

**Page won't load / port error (WinError 10013)**
Some ports are blocked by machine policy. `server.py` falls back through
8000/8080/8090/3000/5000/9000 and prints the URL it settled on. Check the launcher
window for the actual address.

**Old server serving stale code**
If behavior doesn't match a code change, an old server process may still hold the port.
Stop it (or `taskkill` the python PID on port 8000) and relaunch `Start OOJ Web.bat`.

---

## The downloaded file

**Columns look wrong / formula errors (#REF!, #VALUE! spreading right)**
This was caused by deleting Working Sheet columns, which shifted the grid. Column
removal is now **disabled** — all columns are kept. Existing already-trimmed files
can't be repaired; produce a fresh file with a new run.

**Download button stays disabled**
The run didn't pass the accuracy gate: check the finish banner. Either a required
source failed (and partial mode is off), or read-back/verification failed. The banner
lists the reason / blank sources.

---

## General

**A run looks frozen**
Every browser lane streams progress and has a timeout. Check the live log for the last
line to see which stage and what it's doing. Use **Stop run** to abort, fix the cause,
and start again.

**History piling up**
It doesn't — `tools/captures/` is auto-pruned to the last 7 days at the start of every
run.
