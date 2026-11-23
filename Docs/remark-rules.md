# Working Sheet Remark (column P) rules

Last reviewed: 2026-11-23.

## How P works (live formula)

Column **P ("Remark")** stays a **visible formula** in the delivered workbook, so the
operator can trace every remark in the formula bar. The template's Working Sheet P2
(`Docs/Templete_OOJ Process_Date_Month_KABIDEBA.xltx`, an array formula) carries the
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

The older Python remark rules (`remark_rules.py` / `apply_remarks.py`, which rewrote P to
literal values) were removed on 2026-11-23. They had not been part of the run since P
became a live template formula.

## Columns P depends on (Working Sheet)

| Col | # | Header | Role |
|-----|---|--------|------|
| J  | 10 | State | SCC state; `#N/A` = TID not found |
| P  | 16 | Remark | output (live formula from template P2) |
| AD | 30 | Old shipment delivered by Station | station code, `0`, or `#N/A` |
| AF | 32 | Station Transfer Validation | transfer verdict |
| AG | 33 | GCRS Agent Space | polygon status (written as a literal) |

## Never delete Working Sheet columns

Every column of the 33-column template (A..AG) is kept. Deleting a column shifts the grid
and breaks the formulas to its right. The automation only ADDS columns after AG:
AH = CX Address Check, AI = Approval in Comments.

## Files

- The template P2 formula.
- `tools/fill_template.py::extend_working_sheet` (translates P2 down).
- `tools/run_daily.py::read_back` (P2 equality check).
- Tests: `tests/test_polygon_workflow.py` (P2 contract).
