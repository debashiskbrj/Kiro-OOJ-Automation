# Working Sheet Remark (column P) rules

Last reviewed: 2026-11-16.

## How P works today (live formula)

Column **P ("Remark")** stays a **visible formula** in the delivered workbook, so the
operator can trace every remark in the formula bar. The template's Working Sheet P2
(`Docs/Templete_OOJ Process_Date_September_KABIDEBA.xltx`, an array formula) carries the
operator's complete remark formula:

- `ISNA(J2)` → `Invalid- Incorrect TID / TID Not Found`;
- an inner result containing "LM to reattempt", or an inner `#N/A` → AF verdict
  (the two named transfer verdicts) → else AG `Invalid - It is Inside Jurisdiction as per
  GCRS Tool` → else `Valid`; AF/AG are read through `IFERROR(AF2,"")` / `IFERROR(AG2,"")`
  so their `#N/A` never reaches P;
- otherwise the inner formula's own value (Delivered / OOR / DS-DS / RTO / SWA /
  pin-code checks).

The code never wraps or rebuilds it: `fill_template.extend_working_sheet` translates P2
down to every row and **refuses to run if P2 lacks `IFERROR(AF2,"")`** (stale-template
guard); `run_daily.read_back` requires the generated P2 to equal the template P2. **To
change a remark rule, edit P2 in the template** and run the tests.

## Historical rules (`remark_rules.py`, standalone CLI only)

The section below describes the older Python rules that used to rewrite P to literal
values (the `apply_remarks` stage). That stage is **no longer part of the run**; the code
is kept only as a standalone tool and for its tests.

## Columns (Working Sheet, verified against the template)

| Col | # | Header | Role |
|-----|---|--------|------|
| P  | 16 | Remark | output (live formula from template P2) |
| AD | 30 | Old shipment delivered by Station | station code, `0`, or `#N/A` |
| AF | 32 | Station Transfer Validation | max 4 distinct verdicts |
| AG | 33 | GCRS Agent Space | polygon status |

## Decision sequence (per row, in order — never skipped, never revisited)

1. **Protected P values are never changed.** There are **six** protected values:
   - `Invalid - Already RTOed`
   - `Not Valid - Doesn't belong to SWA`
   - `Not Valid - Delivered`
   - `Not Valid - In transit DS-DS`
   - `Invalid- Incorrect TID / TID Not Found` (produced by the State=#N/A rule below)
   - `Invalid - OOR` (produced by the State="In Transit (DS -> Customer)" rule below)

   If P already holds one of these, leave P as-is and stop.

1a. **State (column J) = `#N/A`** -> P = `Invalid- Incorrect TID / TID Not Found`.

1b. **State (column J) = `In Transit (DS -> Customer)`** -> P = `Invalid - OOR`.

   These two run before the AF/AD/AG logic and their outcomes are protected.

2. **Named AF transfer verdicts copy into P.** If AF is either:
   - `Invalid - Correct Station-A.in Shipment delivered - LM to Reattempt and Deliver`
   - `Invalid - LM to Reattempt - Sent to correct Station`

   set P to that AF text and stop.

3. **Otherwise** (AF is anything else, e.g. `Interchange Not Possible / Other Group`
   or `#N/A`), inspect AD:
   - **AD is a 4-character alphanumeric station code** (e.g. `HYBK`, `STVT`) -> P = `Valid`.
   - **AD is anything else** (`0`, `#N/A`, blank), inspect AG:
     - AG == `Invalid - It is Inside Jurisdiction as per GCRS Tool` -> P = that AG text.
     - otherwise (`Outside`, `Error - ...`, etc.) -> P = `Valid`.

## Files

- Live path: the template P2 formula, `tools/fill_template.py::extend_working_sheet`,
  `tools/run_daily.py::read_back` (P2 equality check).
- `tools/remark_rules.py` — historical decision logic (`decide_remark`, `apply_to_worksheet`).
- `tools/apply_remarks.py` — standalone CLI only (not called by the pipeline):
  `py -3.13 tools\apply_remarks.py "path\to\workbook.xlsx"`

## Column removal is disabled (all columns retained)

Column removal is **disabled on purpose**. `apply_remarks.COLUMNS_TO_REMOVE` is an empty
list, so `apply_remarks` deletes **no** Working Sheet columns and the downloaded workbook
keeps **all 33** columns. Deleting Working Sheet columns shifted the grid and broke the
formulas in the columns to their right, so the removal was turned off for grid/formula
safety. Do not re-enable it unless a safe, formula-aware removal is implemented.
- `tests/test_remark_rules.py` — covers every branch.

## Verified on real data

Applied to `OOJ_Process_Filled_20260922_140726_363208.xlsx` (1335 data rows): 144
protected rows preserved, 3 rows with a named AF verdict but a protected P correctly
kept protected, remainder resolved via AD/AG (670 Inside Jurisdiction, 501 Valid).
