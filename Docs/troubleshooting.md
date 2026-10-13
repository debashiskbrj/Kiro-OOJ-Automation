# Troubleshooting — known failures and fixes

Grab the log first: on the web page, open **Live log** and click **Copy log**. Failing
browser steps also save screenshots under `tools/captures/<source>/`.

---

## SCC

SCC's automated browser lane was **retired** (`tools/scc_client.py` and its test were
deleted; the SCC site now blocks automated browsing). SCC is a **manual CSV upload**:
the run pauses after the SIM-T ticket pull, and the SCC progress card shows an
"awaiting upload" file picker.

**Upload rejected: "missing required column(s): ..."**
The uploaded file isn't SCC's native export, or it's missing one of the expected
columns (Tracking ID, State, Destination, Source, Ship Option). Re-export fresh from
SCC and upload that file.

**"Upload accepted but the run could not resume"**
The CSV passed validation but Phase B (`run_daily.py --resume-dir ... --scc-csv ...`)
failed to start — check the live log for the reason (e.g. the run directory from
Phase A is missing or already finished). Re-run from the start if needed.

**I don't see an upload control on the SCC card**
The run hasn't reached the pause yet, or it already passed it. The card only shows the
upload control while `scc_phase` is `awaiting_scc_upload`/`scc_uploaded`; check the live
log for "Phase A done" to confirm the run is actually paused there.

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
It doesn't — `tools/captures/` is wiped at the start of every fresh run (all prior run
folders and per-source artifacts, not just old ones), and old Desktop export workbooks
from a previous day are pruned too.
