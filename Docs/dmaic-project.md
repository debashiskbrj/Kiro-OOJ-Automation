# OOJ Process Automation: Lean Six Sigma DMAIC project

Owner & author: **KABIDEBA** (SWA India - Automation). Version: see `VERSION`.
Created 2026-11-22. Live numbers: `tools/metrics/run_history.csv` and the weekly health
report (`tools/health/`). Fields marked **[TO FILL]** need the owner's input. They cannot be
taken from the system.

---

## STAR summary

- **Situation:** SWA India works a daily queue of Out-of-Jurisdiction (OOJ) SIM-T tickets.
  Each ticket's tracking IDs (TIDs) had to be checked by hand in 8 internal tools (SIM-T,
  SCC, EagleEye, PNAMA, DSR, GCRS, Polygon, ADS) and copied into an Excel template before
  a remark (Valid / Invalid / Not Valid) could be given.
- **Task:** Cut the manual effort and copy-paste errors while keeping the remark logic
  exactly the operator's own (the template formulas stay the authority).
- **Action:** Built a local, one-click automation that pulls each source, fills the
  operator's template, recalculates it in Excel and checks the result before download.
  It adds bulk Resolve and Post comments (behind confirmations), 136 automated tests, a
  weekly health check, and a shareable zip for colleagues.
- **Result:** One complete run on 2026-11-20 handled **485 tickets, 1,431 TIDs and
  1,506 rows in 30.7 minutes** from Start to verified workbook, including the manual SCC
  export.
  Before-vs-after savings need the manual baseline **[TO FILL]** (see Measure).

---

## 1. Define

### Project charter

| Item | Content |
|---|---|
| Project title | OOJ Process Automation |
| Problem statement | The daily OOJ validation is manual: each TID is looked up in 8 tools and pasted into a template. It takes **[TO FILL] hours/day** and copy-paste mistakes cause wrong remarks and rework. |
| Goal (SMART) | Cut daily effort from **[TO FILL]** h to under 1 h per operator and reach zero copy-paste errors, by **[TO FILL date]**. Keep the template's remark logic unchanged. |
| Business case | Hours saved × operators × working days = **[TO FILL]** hours/year. Faster and more consistent ticket closure for stations. |
| In scope | SIM-T pull, SCC (manual CSV), EagleEye, PNAMA, DSR, GCRS, Polygon, ADS, template fill, Excel recalc, verification, Resolve ticket, Post comments |
| Out of scope | Changing the remark rules themselves (template P2), hosted or shared server, the automated SCC lane (retired, SCC blocks automated browsing) |
| Project lead (belt) | KABIDEBA (**[TO FILL]** belt level) |
| Champion / sponsor | **[TO FILL]** (manager) |
| Master Black Belt / reviewer | **[TO FILL]** |
| Team / users | SWA India OOJ operators |

### Voice of the customer → critical to quality (CTQ)

| Customer need | CTQ | Measure | Target |
|---|---|---|---|
| Correct remark for every TID | Template formulas unchanged, read-back check passes | Workbook verified (read_back stage ok) | 100% of runs |
| Every ticket covered | All SIM-T tickets pulled | `complete=True` in SIM-T pull | 100% |
| Fast daily turnaround | Start → verified workbook | `run_minutes` | ≤ 45 min **[confirm]** |
| Few data gaps | Rows with a missing source value | Source gaps per run | Down vs. baseline |
| Safe writes | Nothing posted or resolved without confirmation | Confirmation box + dialog | 100% |

### SIPOC

| Suppliers | Inputs | Process | Outputs | Customers |
|---|---|---|---|---|
| SIM-T, SCC, EagleEye, PNAMA, DSR, GCRS/SIW, Polygon, ADS; Midway | OOJ tickets, TIDs, scan states, missort data, zones, addresses, polygons, predicted pincodes | 1. Pull tickets → 2. Upload SCC CSV → 3. Enrich (EagleEye ∥ PNAMA → DSR → GCRS → Polygon → ADS) → 4. Fill template → 5. Excel recalc → 6. Verify → 7. Download → 8. Resolve / comment | Verified workbook `Templete_OOJ Process_<Month>_KABIDEBA1_<stamp>.xlsx`, remarks, resolve report | Operators, station teams, SWA India management |

---

## 2. Measure

### Baseline (manual process) — [TO FILL]

| Metric | Manual baseline | How to measure |
|---|---|---|
| Time per day for the full process | **[TO FILL]** h | Time 5 manual days, use the average |
| Tickets / TIDs per day | **[TO FILL]** | SIM-T count |
| People involved | **[TO FILL]** | |
| Copy-paste / lookup errors found in QA | **[TO FILL]** per 100 rows | Sample 100 rows, re-check |
| Rework time | **[TO FILL]** h/week | |

### After (automation) — measured from run logs

Complete run 2026-11-20 (`20261120_151445`):

| Metric | Value |
|---|---|
| Tickets / TIDs requested / workbook rows | 485 / 1,431 / 1,506 |
| Start → verified workbook (wall clock, incl. SIM-T pull and manual SCC) | **30.7 min** |
| Machine time, sum of stages | 15.9 min (EagleEye overlaps PNAMA/DSR) |
| Stages failed | 0 (first-pass yield 1/1) |
| Remarks | Valid 621 · Invalid 842 · Not Valid 43 |

SIM-T pull time (Phase A runs):

| Date | Tickets | Rows | Minutes |
|---|---|---|---|
| 2026-11-19 | 103 | 592 | 1.7 |
| 2026-11-20 | 169 | 404 | 2.6 |
| 2026-11-21 | 614 | 1,368 | 9.4 |
| 2026-11-22 | 632 | 1,947 | 10.5 |

Pull time grows with ticket count: about 1 minute per 60 tickets, because each ticket's
comments are read for the approval check.

### Data collection plan

Every fresh Start stores each run's counts and timings in `tools/metrics/run_history.csv`
**before** the 3-day cleanup deletes old run folders (`tools/dmaic_metrics.py`). No TIDs
or addresses are stored. Columns: outcome, tickets, rows, run_minutes, machine_minutes,
failed_stages, per-source missing counts, polygon error %, GCRS transport, remark counts.

---

## 3. Analyze

### Pareto of source data gaps (run 2026-11-20, 1,506 rows)

| Rank | Gap | Count | % of gaps | Cumulative % |
|---|---|---|---|---|
| 1 | Polygon status = error | 255 | 85.3% | 85.3% |
| 2 | DSR TID not found (+6 blank zone) | 24 | 8.0% | 93.3% |
| 3 | ADS no prediction | 11 | 3.7% | 97.0% |
| 4 | SCC TID not found | 5 | 1.7% | 98.7% |
| 5 | EagleEye no event | 4 | 1.3% | 100% |

**Polygon errors are the vital few: 85% of all gaps.** They are the first target for the
next improvement cycle.

### Fishbone: polygon errors (hypotheses to test, not yet proven)

- **Method:** each station polygon is fetched exactly once, with no retry (an operator
  rule). One failed fetch marks every TID of that station as an error.
- **Machine / system:** GCRS fell back from mcscli to Edge (`mcscli gpsn returned HTTP
  302`). The Edge path is slower and more fragile.
- **Material / data:** station not found in the polygon tool. Station codes in SCC can
  differ from the polygon tool's names.
- **Measurement:** "Error — …" mixes "station not found" with "fetch failed". These two
  need separate counts.
- **People / environment:** an expired Midway or SIW session at the time of the run.

Next step: split the 255 errors by message (`polygon.csv` "GCRS Status" text) and run a
second Pareto.

### Defect rate (provisional)

Opportunities = 1,506 rows × 5 enrichment sources = 7,530. Gaps = 299.
DPMO = 299 / 7,530 × 1,000,000 ≈ **39,700**. Process sigma ≈ **3.25** (with the 1.5 shift).
This counts missing source data, not wrong remarks. Many gaps are real "no data" cases,
for example a wrong TID. Compare it with the manual error rate once the baseline is
filled in.

### Root causes already found and fixed (5 Whys)

| Problem | Root cause | Fix |
|---|---|---|
| First run after a laptop restart failed | Edge automation profile loses the QuickSight sign-in on reboot, so PNAMA sat on a login page | Browser sign-in check at Start (`tools/browser_signin.py`) |
| Zone column always #N/A | Template I2 looked up E (station code) instead of D (TID) | I = `VLOOKUP(D2,'DSR Data'!A:V,22,0)` |
| Resolve run reported 75 of 79 tickets | An exception in a worker thread silently dropped its remaining tickets | Error logged per ticket; the worker continues |
| Resolve could change already-closed tickets | No status check | Already Resolved/Closed tickets are skipped |
| SCC export vanished from Downloads | Server-start cleanup still treated SCC's `SearchResults.csv` as a leftover of the retired SCC lane | File name removed from the cleanup list (audit 1.3) |
| Page said "Paused" after the SIM-T step failed | The page state was set at Start and never checked against how the step ended | Exit code + `tech_data.csv` checked; page shows the failure and offers Start (1.5) |
| Paused run lost after a page reload | Page start-up only checked Midway, never the server's run | Page re-attaches to the run on load (1.9) |
| Phase B could crash on a ticket with no date | Missing date written as the text "None" | Written as blank; read back safely (1.11) |

---

## 4. Improve

### Value stream: before → after

| Step | Manual (before) | Automated (after) |
|---|---|---|
| Pull tickets and TIDs | Search SIM-T, copy each ticket **[TO FILL] min** | Automatic, 2–11 min unattended |
| SCC | Export + paste | Export by hand + upload in the page (SCC blocks automation) |
| EagleEye, PNAMA, DSR, GCRS, Polygon, ADS | Search each tool, paste each export **[TO FILL] min** | Automatic, about 16 min unattended |
| Fill template, extend formulas | Paste into 8 sheets, drag formulas **[TO FILL] min** | Automatic, under 1 min |
| Check | Visual check | Read-back check of TIDs, P formula and Excel errors |
| Resolve tickets | One by one | Bulk, unique tickets only, Excel report |

**Lean wastes removed:** waiting (lookups run unattended), motion (no switching between
tools), over-processing (no manual formula drag), defects (no copy-paste errors), and
transport (one workbook, saved automatically).

### Improvements delivered

1. One-click local web page with live progress per source.
2. Template-driven remarks: the operator's formulas stay the only remark rules.
3. Verification gate: a workbook downloads only after the read-back check passes.
4. Bulk Resolve ticket and Post comments, each with a preview and confirmation.
5. Approval-in-comments scan (CRM / Site Lead approval detection).
6. DSR raw data saved in the workbook (`DSR Data` sheet) for traceability.
7. Shareable zip, weekly health check, per-user paths.
8. Browser Midway check at Start capped at 20 s (was up to 10 min), with a page popup
   "Browser accepted Midway" (v1.3.2, 2026-11-24). Time saved per run: **[TO FILL]**.
9. Remark Summary pivot sheet: volume per remark is visible on opening the workbook, no
   manual pivot needed (v1.3.3). Time saved per run: **[TO FILL]**.
11. CX / Amazon Bucket (AK) + Bucket Reason (AL), v1.5.0: two buckets for every Valid row,
   Amazon only with full proof (pincode, station/polygon, India Post, full address). Tested
   on 7 runs / 2,661 Valid rows: Amazon 5.3%, CX 94.7% (Rule B on the same 4 comparable runs:
   Amazon 23.0%). Accuracy vs manual judgement: **[TO FILL]** (planned 100-row check).
10. Rule B Bucket column (AJ): every Valid ticket classified as Customer / Amazon / Manual
   Review from pincode, probability, station and GCRS address signals (v1.3.4). First
   check (2026-11-24 data): 494 Valid = 345 Customer (70%), 78 Amazon (16%), 71 Manual (14%).

### Next improvement candidates (from Analyze)

1. Split polygon errors by cause, then target the biggest one.
2. Find out why mcscli returns HTTP 302 (it forces the slower Edge path for GCRS).
3. Collect the manual baseline to prove the savings.
4. QuickSight keeps each user's saved dashboard filters, so PNAMA/DSR can silently return
   fewer rows. Today the operator resets both dashboards by hand before a run; candidate:
   automatic Reset + check that filters are at default.

---

## 5. Control

### Control plan

| What | Measure | Target / limit | How often | Who | Reaction |
|---|---|---|---|---|---|
| Workbook correctness | read_back stage ok | 100% | Every run | Automation (blocks download) | No download; fix the cause before reuse |
| Template intact | Required sheets, DSR headers, P2 formula | Unchanged | Weekly health check | Automation → owner | Re-extract the zip / restore the template |
| Code correctness | Built-in self-test (136 tests) | All pass | Weekly + every change | Owner | Owner fixes; new version |
| Run time | run_minutes I-MR chart | Within UCL/LCL | Weekly health report | Owner | Find the special cause (network, SSO, volume) |
| Polygon error rate | polygon_error_pct I-MR chart | Within limits, falling | Weekly | Owner | Analyze per message |
| First-pass yield | Runs with no failed stage | ≥ 95% **[confirm]** | Weekly | Owner | Pareto of failed stages |
| Changes | `Docs/changes.md`, `VERSION` | Every change logged | Every change | Owner | |

### Control chart

`tools/dmaic_metrics.py` builds an individuals (I-MR) chart from `run_history.csv`:
centre line = mean, limits = mean ± 2.66 × average moving range. The weekly health check
prints it and warns when a point is outside the limits. The limits are **provisional
until 20 complete runs** have been stored.

Run it any time: `py -3.13 tools\dmaic_metrics.py`

### Standard work and documents

- SOP: `Docs/SOP-OOJ-Automation.md` · User guide: `Docs/user-guide.md`
- Colleague guide: `READ ME FIRST.txt` · Troubleshooting: `Docs/troubleshooting.md`
- Change log: `Docs/changes.md` · Standing rules: `.kiro/steering/ooj-instructions.md`

### Hand-off and ownership

The owner (KABIDEBA) maintains the code and sends new versions as a zip. Users report
problems to the owner with the health report attached. Users do not edit the code.
