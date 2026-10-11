# User Guide — running the OOJ automation

This is the day-to-day guide for the operator. It covers what you need set up once,
and the exact steps to run the pipeline and get your filled workbook.

---

## One-time prerequisites

You need these on the machine (they're already set up on kabideba's laptop):

- **Python 3.13** on PATH (`py -3.13`), with `openpyxl`, `playwright`, `requests`.
- **Playwright + Edge**: the browser lanes drive Microsoft Edge (`channel="msedge"`).
- **Midway**: `mwinit -o` produces the session cookie the API lanes need.
- **mcscli**: installed at
  `C:\Program Files\Common Files\AmazonMidwayClientSuite\bin\mcscli.exe` (for SIW/GCRS).
- **builder-mcp**: at `C:\Users\kabideba\AppData\Local\Toolbox\bin\builder-mcp.exe`
  (for SIM-T ticket pull).
- **The automation Edge profile** at
  `C:\Users\kabideba\Desktop\My Files\My Automation\TT_Automation\edge_profile`,
  signed in to:
  - SCC (`logistics.amazon.in`)
  - the PNAMA and DSR QuickSight dashboards

If a browser lane fails with a login/authentication message, it means that site isn't
signed in to the automation Edge profile — sign in there once and re-run.

---

## Daily run — the web page

1. **Start the app.** Double-click **`Start OOJ Web.bat`** in the project root, or run:
   ```
   py -3.13 tools\web\server.py
   ```
   It serves **http://127.0.0.1:8000/** and opens your browser.

2. **Grant Midway access (step 1 on the page).** Click **Grant Midway access**. A
   terminal window opens running `mwinit -o` — type your PIN there and approve. The
   page unlocks Start once the session is valid. You only do this once per session.

3. **Start today's run (step 2).**
   - Tick **Approve ADS upload** — required for a complete, downloadable workbook (ADS
     uploads TIDs and addresses to its endpoint).
   - Optionally tick **Allow partial download if a source fails** — if a source can't
     fetch, its sheet is left blank and you still get the workbook (labelled PARTIAL).
     Leave it off for a strict full run.
   - Click **Start run**.

4. **Watch progress (step 3).** Eight source cards (SIM-T Tickets, EagleEye, SCC,
   PNAMA, DSR, GCRS, Polygon, ADS) show running/done/error with a **% bar** and live
   counts. Below them: the full enriched table as it fills, and the **Live log** with a
   **Copy log** button (use it to share the log if something fails).
   - Each source shows a **Download CSV** link once it has data, so you can grab an
     individual source's output mid-run.
   - **Stop run** aborts the current run (and its browser) if you need to.

5. **Get the result (step 4).** When the run finishes, the page runs verification
   (read-back + polygon check) automatically. The **Download** button unlocks only when
   the workbook is valid. The file is also copied to your **Desktop** as
   `OOJ_Process_Filled_<stamp>.xlsx` (or `..._PARTIAL_<stamp>.xlsx` for a partial run).

---

## What the final file contains

- The Excel template, filled from all sources, with formulas recalculated.
- The Working Sheet **Remark (column P)** rewritten to final values per the rules in
  `remark-rules.md` (six protected values, plus the State-driven rules).
- All original template columns are kept.

---

## Rough timing

A full run of ~1,400–1,500 TIDs takes roughly 15–25 minutes, dominated by the browser
lanes (SCC/PNAMA/DSR each a few minutes; DSR's dashboard is the slowest to load) and
GCRS. EagleEye and the fill/verify steps are fast.

---

## If something goes wrong

- Use **Copy log** and check `Docs/troubleshooting.md` — it lists the known failures
  (SCC bulk search down, PNAMA/SCC not signed in, ADS host down, Edge profile open,
  blocked port) and how to fix each.
- Screenshots of failing browser steps are saved under `tools/captures/<source>/`.
- If you just need the file despite one source being down, re-run with **Allow partial
  download** ticked.
