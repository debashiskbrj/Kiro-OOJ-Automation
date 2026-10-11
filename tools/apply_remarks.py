r"""Apply the Working Sheet Remark (P) rules to a recalculated OOJ workbook.

Reads the recalculated (cached) values of P/AD/AF/AG, computes the final Remark per
``remark_rules.decide_remark``, and writes the literal result into column P, saving to
an output workbook (default: alongside the input with a _Remarks suffix).

Because openpyxl exposes formula cells as their cached value only when the file is
loaded with data_only=True, and cannot write while in that mode, we load twice:
  * data_only=True  -> read computed AD/AF/AG/P
  * data_only=False -> the writable workbook we save (P becomes a literal)

Usage:
  py -3.13 apply_remarks.py "path\to\OOJ_Process_Filled_*.xlsx" [--out PATH] [--in-place]
"""
from __future__ import annotations

import argparse
from pathlib import Path

import openpyxl

import remark_rules as rr


def apply_to_file(src: Path, out: Path, last_row: int | None = None) -> dict:
    """Apply the Remark rules to ``src`` and save to ``out``.

    When ``last_row`` is given (the pipeline passes ``len(tech_rows) + 1``), exactly
    rows 2..last_row are processed so the result stays row-aligned with the rest of the
    workbook. Otherwise every row down to ``max_row`` is processed, skipping trailing
    fully-empty rows (no TID and no remark) so blanks are never stamped "Valid".
    """
    values_wb = openpyxl.load_workbook(src, data_only=True)
    values_ws = values_wb[rr.SHEET_WORK]
    write_wb = openpyxl.load_workbook(src, data_only=False)
    write_ws = write_wb[rr.SHEET_WORK]

    if write_ws.max_row != values_ws.max_row:
        raise RuntimeError(
            f"row count mismatch between value/formula views: "
            f"{write_ws.max_row} != {values_ws.max_row}"
        )
    explicit_last = last_row is not None
    end_row = last_row if explicit_last else values_ws.max_row

    from collections import Counter

    written = Counter()
    protected = 0
    processed = 0
    for row in range(2, end_row + 1):
        p_value = values_ws.cell(row=row, column=rr.COL_P).value
        if not explicit_last:
            # Skip trailing fully-empty rows (no TID) so we don't stamp "Valid" on blanks.
            tid = values_ws.cell(row=row, column=4).value  # D = TID
            if tid in (None, "") and (p_value in (None, "")):
                continue
        processed += 1
        if rr.is_protected(p_value):
            protected += 1
            written[rr._clean(p_value)] += 1
            continue
        result = rr.decide_remark(
            p_value,
            values_ws.cell(row=row, column=rr.COL_AF).value,
            values_ws.cell(row=row, column=rr.COL_AD).value,
            values_ws.cell(row=row, column=rr.COL_AG).value,
            values_ws.cell(row=row, column=rr.COL_J).value,
        )
        write_ws.cell(row=row, column=rr.COL_P).value = result
        written[result] += 1

    removed = _remove_unwanted_columns(write_ws)

    write_wb.save(out)
    return {
        "rows": processed,
        "protected": protected,
        "written": dict(written),
        "removed_columns": removed,
        "out": str(out),
    }


# Column removal is DISABLED: deleting Working Sheet columns shifted the grid and broke
# formulas in the columns to their right, producing errors in the downloaded file. We keep
# every column so the template stays intact and fully correct. (List left empty on purpose;
# do not add columns here unless a safe, formula-aware removal is implemented.)
COLUMNS_TO_REMOVE: list[str] = []


def _remove_unwanted_columns(ws) -> list[str]:
    """Delete the not-required columns by header name, right-to-left so indices stay valid.

    Matches headers by normalised (whitespace-trimmed) text so trailing spaces in the
    template do not prevent a match. Returns the headers actually removed.
    """
    def norm(value) -> str:
        return " ".join(str(value).split()) if value is not None else ""

    wanted = {norm(name) for name in COLUMNS_TO_REMOVE}
    # Map each target header to its current column index.
    to_delete = []
    for col in range(1, ws.max_column + 1):
        header = norm(ws.cell(row=1, column=col).value)
        if header in wanted:
            to_delete.append((col, header))
    removed = []
    for col, header in sorted(to_delete, key=lambda pair: pair[0], reverse=True):
        ws.delete_cols(col, 1)
        removed.append(header)
    return list(reversed(removed))


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply Working Sheet Remark (P) rules")
    parser.add_argument("workbook", help="path to a recalculated OOJ workbook")
    parser.add_argument("--out", help="output path (default: <input>_Remarks.xlsx)")
    parser.add_argument("--in-place", action="store_true", help="overwrite the input file")
    args = parser.parse_args()

    src = Path(args.workbook)
    if not src.exists():
        raise SystemExit(f"file not found: {src}")
    if args.in_place:
        out = src
    elif args.out:
        out = Path(args.out)
    else:
        out = src.with_name(src.stem + "_Remarks" + src.suffix)

    summary = apply_to_file(src, out)
    print("Remark rules applied:")
    print(f"  rows processed : {summary['rows']}")
    print(f"  protected kept : {summary['protected']}")
    print(f"  output         : {summary['out']}")
    print("  written tally  :")
    for value, count in sorted(summary["written"].items(), key=lambda kv: -kv[1]):
        print(f"    {count:5d}  {value}")


if __name__ == "__main__":
    main()
