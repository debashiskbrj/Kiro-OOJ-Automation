# User Guide — running the OOJ automation

The day-to-day guide for the operator: what must be set up once, and the exact steps
to run the pipeline, get the filled workbook, resolve tickets and post comments.

Last reviewed: 2026-11-16.

---

## One-time prerequisites

These are already set up on kabideba's laptop:

- **Python 3.13** (`py -3.13`, or `C:\Windows\py.exe -3.13`). Missing packages
  (`openpyxl`, `playwright`, `requests`) are installed automatically the first time the
  server starts (`tools/auto_setup.py`).
- **Playwright + Microsoft Edge**: the PNAMA/DSR/ADS lanes drive Edge (`channel="msedge"`).
- **Midway**: `mwinit -o` produces the session cookie that SIM-T, EagleEye and GCRS use.
- **mcscli**: `C:\Program Files\Common Files\AmazonMidwayClientSuite\bin\mcscli.exe`
  (GCRS / SIW).
- **builder-mcp**: `C:\Users\kabideba\AppData\Local\Toolbox\bin\builder-mcp.exe`
  (SIM-T ticket pull, Phonetool title lookup, Resolve ticket).
- **The automation Edge profile** at
  `C:\Users\kabideba\Desktop\My Files\My Automation\TT_Automation\edge_profile`,
  signed in to the PNAMA and DSR QuickSight dashboards. Another machine can point to its
  own profile with the `OOJ_EDGE_PROFILE` environment variable (`tools/ooj_paths.py`).
- **Your normal browser signed in to SCC** (`logistics.amazon.in`). SCC blocks
  automated browsing, so you search and export SCC by hand.

If PNAMA or DSR fails with a login message, sign in to that dashboard inside the
automation Edge profile once and run again.

---

## Daily run — the web page

1. **Start the app.** Double-click **`Start OOJ Web.bat`** (or run
   `py -3.13 tools\web\server.py`). The page opens in its own window at
   **http://127.0.0.1:8000/**. If the page looks old after an update, press **Ctrl+F5**.

2. **1. Midway access.** Click **Grant Midway access**. A terminal window runs
   `mwinit -o`; type your PIN there and touch your security key. Start unlocks when the
   session is valid.

3. **2. Start today's run.**
   - Tick **Approve ADS upload** (required for a complete workbook).
   - Optionally tick **Allow partial download if a source fails** (a failed source's
     sheet is left blank and the file is labelled PARTIAL).
   - Click **Start run**.

4. **3. Progress.** Eight cards — SIM-T Tickets, SCC, EagleEye, PNAMA, DSR, GCRS,
   Polygon, ADS — show status, a % bar, counts and a **Download CSV** link once a source
   has data. The **Live log** has a **Copy log** button.

5. **The SCC pause (card 2).** After the SIM-T pull the run pauses:
   - Download the SIM-T TIDs from card 1, search them in SCC, export SCC's CSV.
   - In the SCC card choose the file and click **Upload SCC CSV**. A valid file (columns
     Tracking ID, State, Destination, Source, Ship Option) resumes the run automatically.
     A rejected file shows the reason; fix it and upload again.
   - **Every lookup after SCC (EagleEye, PNAMA, DSR, GCRS, ADS, Polygon) uses only the
     TIDs in your SCC file.** A file with no Tracking IDs stops the run. If the SCC TIDs do
     not match today's SIM-T tickets, an amber popup warns you ("probably an old or wrong
     SCC file") and the run continues — check you uploaded today's export.

6. **Stop after the SCC upload (and rerun).** While the steps after SCC are running, the
   SCC card shows a red **Stop run (keep SIM-T data)** button.
   - Click it and confirm. The run stops; the SIM-T data and your SCC file are kept.
   - The SCC card then shows **stopped** with two choices:
     **Rerun with same file**, or choose a new SCC CSV and click **Upload SCC CSV**.
   - Files the stopped attempt had already made are moved to a `stopped_<time>` folder
     inside that run's folder (not deleted).
   - The top **Stop run** button still ends the whole run.

7. **4. Result.** When the run finishes, verification (read-back + polygon check) runs
   automatically. **Download filled workbook** unlocks only for a valid file. A copy is
   saved to your Desktop as
   `Templete_OOJ Process_<Month>_KABIDEBA1_<stamp>.xlsx`
   (`..._PARTIAL_<stamp>.xlsx` for a partial run).

---

## 5. Resolve ticket (optional, changes real tickets)

Resolves SIM-T tickets in bulk with status **Resolved** and closure code
**Successful**. No comment is added and nothing else is changed. It runs under your own
login. Full details: `resolve-tickets.md`.

1. Paste the TT URLs into the box, one per line (2,000+ is fine). ShortIDs such as
   `D572189109` also work.
2. Click **Check list**. Nothing is changed yet. You see entries pasted, duplicates
   removed, invalid entries (listed, never resolved) and unique tickets to resolve.
3. Tick **I have checked the list…**, click **Resolve tickets**, confirm.
4. Watch the counts: resolved now, already resolved, failed, remaining. Failures are
   listed first in the table.
5. **Stop** is available at any time; tickets already resolved stay resolved.
6. Click **Download resolved report** (Excel: Summary, Results, Invalid input).
7. If tickets failed or were not reached, **Retry failed / pending (N)** re-runs ONLY those
   tickets (new report). Resolved ones are never sent again. The button works for the last
   run since the page server started.

**First time on a new machine or after an update: try ONE ticket first** and confirm in
SIM-T that it shows Resolved / Successful. Pasting the same list again is safe: tickets
that are already resolved are skipped.

---

## 6. Post comments (optional, changes real tickets)

Upload an `.xlsx` with **url**, **tid** and **remark** columns. A dry-run preview runs
first; nothing is posted until you tick the confirmation and click **Post comments to
tickets**. **Retry failed / pending (N)** re-posts only the URLs that failed or were never
reached; URLs already posted are never repeated.

---

## What the final workbook contains

- The Excel template filled from all sources, with formulas recalculated.
- **Remark (Working Sheet column P)** is a live formula from the template, so you can
  trace each remark in the formula bar (rules: `remark-rules.md`).
- **AH — CX Address Check**: OK / NOT OK / MISMATCH for the customer address.
- **AI — Approval in Comments**: from the ticket comments (`approval-in-comments.md`):
  `Approved` / `Site clear` when a CRM or Site Lead approved,
  `Need check manually (<alias>)` when only someone else approved, blank when nobody did.
- **AJ — Rule B Bucket (Pincode + GCRS Address)**: for Valid rows only — Customer Bucket /
  Amazon Bucket / Manual Review (rules in `tools/rule_b_bucket.py`).
- **AK — CX / Amazon Bucket** and **AL — Bucket Reason**: for Valid rows only — `CX Bucket`
  or `Amazon Bucket` with a short reason. Amazon only when the pincode is correct, the
  station or polygon shows the wrong station, India Post says OK and the GCRS address is a
  full address (rules in `tools/cx_amazon_bucket.py`).
- **AG** is now called **GCRS Polygon** and **AH** is called **India Post Database** (same
  values as before).
- **E — Station Code(SCC)**: SCC Source, or the DSR station code when SCC's Source is not a
  real 4-character station code.
- **Remark Summary** sheet: a pivot of the volume per Remark (right-click > Refresh after
  you edit column P).

---

## Local files and clean-up

- Each fresh **Start run** deletes run folders and Desktop output workbooks older than
  **3 days**. Newer ones are kept. Your other Desktop files are never touched.
- Each source's cached downloads are cleared on every Start, so a run only uses fresh data.
- Resolve ticket reports are kept in `tools/resolve_runs/` and are not cleaned up.

---

## Rough timing

- Full run of ~1,400–1,500 TIDs: about 15–25 minutes of automated time, plus your SCC
  search/export time. SIM-T takes longer than before because every ticket's comments are
  read for the approval column.
- Resolve ticket: 4 tickets at a time; roughly 30–60 minutes for 2,000 tickets
  (estimate, depends on SIM-T speed).

---

## If something goes wrong

- **Copy log** and check `troubleshooting.md`.
- Page shows old buttons or odd state: press **Ctrl+F5**. If still wrong, click
  **Restart server** at the top right (only when nothing is running).
- Need the file despite one source being down: run again with **Allow partial download**.
