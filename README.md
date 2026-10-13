# OOJ Process Automation

Automates the daily SWA India **Out-of-Jurisdiction (OOJ)** ticket process end to end:
pull tickets from SIM-T, extract tracking IDs (TIDs), enrich each TID from Amazon
internal tools, apply the remark rules, and produce a filled, downloadable Excel
workbook — replacing the manual template workflow.

Everything runs **locally** on the operator's Windows laptop. It is **free** (Python
standard library plus a few well-known packages), uses **no AI/LLM in the run path**,
and is **read-only** against Amazon source systems (it never posts to or resolves
tickets).

> **Designed & built by KABIDEBA — SWA India, Automation.**

---

## How a run works (the important part)

The run **pauses in the middle** for one manual step: SCC. SCC's site now blocks
automated browsing, so the operator exports SCC's CSV by hand and uploads it into the
page; everything before and after SCC is automated.

```
1. SIM-T Tickets   -- automated --  pulls tickets, extracts TIDs, then the run PAUSES
2. SCC             -- MANUAL     --  you upload SCC's exported CSV in the SCC card
3. EagleEye        -- automated --+
4. PNAMA           -- automated --|
5. DSR             -- automated --|  resume automatically after the SCC upload
6. GCRS            -- automated --|
7. Polygon         -- automated --|
8. ADS             -- automated --+
   -> fill template -> Excel recalc -> verify (read-back) -> apply Remark rules -> download
```

Per-stage details: **`Docs/pipeline-reference.md`**.

---

## Quick start (daily use)

1. Prerequisites (one-time, see `Docs/user-guide.md`): Python 3.13, a valid Midway
   session (`mwinit -o`), and the automation Edge profile signed in to SCC and the
   QuickSight dashboards.
2. Double-click **`Start OOJ Web.bat`** (or run `py -3.13 tools\web\server.py`).
3. Open **http://127.0.0.1:8000/** — it usually opens automatically.
4. Grant Midway access, then tick:
   - **Approve ADS upload for this run** (required for a complete, downloadable workbook), and
   - **Allow partial download if a source fails** (optional).
5. Click **Start run**. The page pulls SIM-T tickets, then **pauses at the SCC card**.
6. In the **SCC card (step 2)**, use **Download CSV** on the SIM-T card to get the TIDs,
   search them in SCC yourself, export SCC's native CSV, then **choose that file and
   click "Upload SCC CSV"**. The run resumes automatically.
7. When it finishes, click **Download** — the filled workbook is also saved to your
   Desktop as `OOJ_Process_Filled_<timestamp>.xlsx`.

Full step-by-step: **`Docs/user-guide.md`**.

---

## Clone & set up (new machine)

```bat
git clone https://github.com/debashiskbrj/Kiro-OOJ-Automation.git
cd Kiro-OOJ-Automation
```

- **Python 3.13** must be installed and launchable as `py -3.13`.
- On the first server start, `tools/auto_setup.py` installs any **missing** Python
  packages (e.g. `openpyxl`, `playwright`, `requests`) — it never upgrades or pins over
  a working install. It also prints a read-only checklist of the manual prerequisites
  only you can do (Midway login, Edge profile sign-in).
- **Edge automation profile path:** the default is machine-specific. Point it at your
  own profile with the `OOJ_EDGE_PROFILE` environment variable (see `tools/ooj_paths.py`),
  e.g.:
  ```bat
  setx OOJ_EDGE_PROFILE "C:\path\to\your\edge_profile"
  ```
- Operational data (`tools/captures/`), caches, and secrets are **git-ignored** — a
  clone contains source, docs, tests, and the Excel template only, never real ticket
  data.

Run the tests to confirm a healthy checkout:

```bat
py -3.13 -m pytest tests -q
```

---

## What you get

- A one-click local web page that runs the whole pipeline and shows live per-source
  progress (with a % bar per tool), a live log with a **Copy log** button, per-source
  CSV download buttons, a **Stop run** button, the **SCC upload** control that appears
  when the run pauses, and a gated **Download** for the final workbook.
- The final workbook has the Working Sheet Remark (column P) rewritten per the
  operator's rules (see `Docs/remark-rules.md`).
- A **partial** mode: if a source can't fetch, tick "Allow partial download" to still
  get the workbook with that source's sheet left blank (labelled PARTIAL).

---

## Documentation map

| Doc | What it covers | For |
|---|---|---|
| `README.md` (this file) | Overview + entry point | Everyone |
| `Docs/project-structure.md` | Where every file lives + what goes where | Both |
| `Docs/user-guide.md` | How to run it daily, prerequisites, the web page | Operator |
| `Docs/pipeline-reference.md` | Each source/stage: what it does, how it fetches | Both |
| `Docs/developer-guide.md` | Code layout, how to change things, tests | Maintainer |
| `Docs/troubleshooting.md` | Known failures and fixes (SCC, PNAMA, ADS, Edge, ports) | Both |
| `Docs/remark-rules.md` | The Working Sheet column-P Remark logic | Both |
| `Docs/web-runner.md` | The local web app internals, endpoints, data wipe | Maintainer |
| `Docs/polygon-checker.md` | The local GCRS polygon/jurisdiction checker | Maintainer |
| `Docs/report-spec.md` | Final report columns | Both |
| `Docs/architecture.md` | Original design notes | Maintainer |
| `Docs/implementation.md` | Build history / current state | Maintainer |
| `Docs/context.md` | Single-source-of-truth working notes | Maintainer |
| `Docs/access-status.md` | Per-source access lane status | Maintainer |
| `Docs/edge-cases.md` | Live log of edge cases learned | Maintainer |
| `Docs/changes.md` | Live changelog | Maintainer |

---

## Safety / ground rules

- **Read-only** against Amazon systems. Never posts, resolves, or pushes git without
  explicit approval.
- **Fresh data only — no stale reuse.** At the start of **every run**, the automation
  wipes all prior run folders and per-source captures under `tools/captures/` (keeping
  only the current run and the long-lived `scc_enum_map.json` overlay) and prunes old
  `OOJ_Process_Filled_*.xlsx` exports from the Desktop (keeping only today's), so a run
  can never pick up yesterday's data or output. In addition, a startup sanitizer
  (`tools/sanitize_captures.py`) clears output-corrupting scratch (browser download
  folders, partial `.crdownload`/`.tmp`/`.partial` files, atomic-write sidecars, and
  stale PNAMA/DSR leftovers in `~/Downloads`). Neither ever touches code, docs, the
  template, tests, or any non-automation Desktop file.
- The whole thing runs as the operator, using the operator's own Midway/SSO sessions.

See `Docs/context.md` for the full standing rules.
