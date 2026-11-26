"""Rule B Bucket column (operator spec 2026-11-25): classify every VALID ticket in the
Working Sheet as "Customer Bucket", "Amazon Bucket" or "Manual Review".

Inputs (Working Sheet, displayed/calculated values): D = TID, N = Probability,
O = Pincode Validation, P = Remark, R = Station Validation. Address check: sheet
"GCRS Agentspaces " (trailing space) column A = Tracking ID, column D = CX Address Check.

Only rows whose Remark is exactly "Valid" get a bucket; every other row is left blank.
Rules, first match wins:
  B1+B2  O = FALSE and (N >= 0.60 or AddressBad)  -> Customer Bucket
  B3     O = TRUE  and AddressOK                   -> Amazon Bucket
  B5     O = TRUE  and AddressBad                  -> Manual Review
  B4     AddressOK and R = FALSE                   -> Amazon Bucket
  B6     AddressBad                                -> Customer Bucket
  B7     anything else                             -> Manual Review

Writing: plain text, into the next empty column of row 1 (header formatting copied from
the header cell to its left), via Excel (tools/write_rule_b_column.ps1) so the pivot sheet,
array formulas and formats are untouched. No other cell is changed.

Usage (existing workbook; writes a COPY unless --in-place):
  py -3.13 rule_b_bucket.py "C:\\...\\Templete_OOJ Process_....xlsx" [--out copy.xlsx | --in-place]
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

import openpyxl

HERE = Path(__file__).resolve().parent
PS_SCRIPT = HERE / "write_rule_b_column.ps1"
HEADER = "Rule B Bucket (Pincode + GCRS Address)"
SHEET_WORK = "Working Sheet"
SHEET_AGENT = "GCRS Agentspaces "  # trailing space is part of the template name
CUSTOMER, AMAZON, MANUAL = "Customer Bucket", "Amazon Bucket", "Manual Review"
BUCKETS = (CUSTOMER, AMAZON, MANUAL)
PROB_MIN = 0.60
# Working Sheet 0-based column indexes
COL_A, COL_D, COL_N, COL_O, COL_P, COL_R = 0, 3, 13, 14, 15, 17


def _is_error(value) -> bool:
    return isinstance(value, str) and value.strip().startswith("#")


def normalize_tid(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = str(value).strip()
    return text[:-2] if text.endswith(".0") and text[:-2].isdigit() else text


def as_probability(value) -> float | None:
    """Number 0-1, or None when blank/error/unreadable (then it never satisfies >= 0.60)."""
    if value is None or isinstance(value, bool) or _is_error(value):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text[:-1]) / 100 if text.endswith("%") else float(text)
    except ValueError:
        return None


def as_flag(value) -> bool | None:
    """TRUE / FALSE, or None when blank/error (neither TRUE nor FALSE)."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().upper()
        if text == "TRUE":
            return True
        if text == "FALSE":
            return False
    return None


def address_flags(text) -> tuple[bool, bool]:
    """(AddressBad, AddressOK). Not found / Error / MISMATCH / blank -> (False, False)."""
    if text is None or _is_error(text):
        return False, False
    upper = str(text).strip().upper()
    if upper.startswith("NOT OK"):
        return True, False
    if upper.startswith("OK"):
        return False, True
    return False, False


def classify(remark, probability, pincode_validation, station_validation, address_text) -> str:
    """Bucket for one Working Sheet row; "" for every non-Valid row."""
    if remark != "Valid":
        return ""
    prob = as_probability(probability)
    o_flag = as_flag(pincode_validation)
    r_flag = as_flag(station_validation)
    bad, ok = address_flags(address_text)
    if o_flag is False and ((prob is not None and prob >= PROB_MIN) or bad):
        return CUSTOMER                                    # B1 + B2
    if o_flag is True and ok:
        return AMAZON                                      # B3
    if o_flag is True and bad:
        return MANUAL                                      # B5
    if ok and r_flag is False:
        return AMAZON                                      # B4
    if bad:
        return CUSTOMER                                    # B6
    return MANUAL                                          # B7


def compute(xlsx: Path) -> tuple[list[str], int]:
    """Bucket per Working Sheet row 2..last row with data in column A, and the Valid count."""
    wb = openpyxl.load_workbook(xlsx, read_only=True, data_only=True)
    try:
        address = {}
        if SHEET_AGENT in wb.sheetnames:
            for row in wb[SHEET_AGENT].iter_rows(min_row=2, max_col=4, values_only=True):
                tid = normalize_tid(row[0] if row else None)
                if tid and tid not in address:
                    address[tid] = row[3] if len(row) > 3 else None
        rows = []
        for row in wb[SHEET_WORK].iter_rows(min_row=2, max_col=COL_R + 1, values_only=True):
            row = tuple(row) + (None,) * (COL_R + 1 - len(row))
            rows.append(row)
    finally:
        wb.close()
    last = max((i for i, row in enumerate(rows) if row[COL_A] not in (None, "")), default=-1)
    rows = rows[:last + 1]
    values = [classify(row[COL_P], row[COL_N], row[COL_O], row[COL_R],
                       address.get(normalize_tid(row[COL_D])))
              for row in rows]
    valid = sum(1 for row in rows if row[COL_P] == "Valid")
    return values, valid


def read_column(xlsx: Path) -> list:
    """Values under the Rule B header (rows 2..), for the after-write validation."""
    wb = openpyxl.load_workbook(xlsx, read_only=True, data_only=True)
    try:
        ws = wb[SHEET_WORK]
        header = next(ws.iter_rows(min_row=1, max_row=1, values_only=True))
        col = list(header).index(HEADER)
        return [row[col] if len(row) > col else None
                for row in ws.iter_rows(min_row=2, min_col=1, max_col=col + 1, values_only=True)]
    finally:
        wb.close()


def count(values, valid: int) -> dict:
    c = Counter(v for v in values if v)
    result = {name: c.get(name, 0) for name in BUCKETS}
    result["Blank"] = sum(1 for v in values if not v)
    result["Valid"] = valid
    result["ok"] = sum(result[name] for name in BUCKETS) == valid and set(c) <= set(BUCKETS)
    return result


def apply(xlsx: Path, log=print, timeout: int = 600) -> dict:
    """Compute, write the column with Excel, re-read it and validate the counts."""
    xlsx = Path(xlsx)
    values, valid = compute(xlsx)
    fd, values_file = tempfile.mkstemp(prefix="ooj_ruleb_", suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(values))
        res = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(PS_SCRIPT),
             "-Path", str(xlsx), "-ValuesFile", values_file, "-Rows", str(len(values)),
             "-Header", HEADER],
            capture_output=True, text=True, timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))  # no console flash
    finally:
        try:
            os.remove(values_file)
        except OSError:
            pass
    line = next((ln for ln in res.stdout.splitlines() if ln.startswith("RULEB_")), "")
    if not line.startswith("RULEB_OK"):
        raise RuntimeError(f"Excel write failed: {line or res.stderr.strip()[:300]}")
    written = read_column(xlsx)[:len(values)]
    result = count(written, valid)
    result["column"] = line.split()[-1]
    result["matches_computed"] = written == [v or None for v in values]
    log(f"Rule B Bucket (column {result['column']}): Customer={result[CUSTOMER]}, "
        f"Amazon={result[AMAZON]}, Manual Review={result[MANUAL]}, Blank={result['Blank']}; "
        f"Valid in P={valid} -> {'OK' if result['ok'] and result['matches_computed'] else 'MISMATCH'}")
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("xlsx")
    ap.add_argument("--out", help="write to this copy (default: <name>_RuleB.xlsx next to the input)")
    ap.add_argument("--in-place", action="store_true", help="write into the input workbook itself")
    args = ap.parse_args()
    src = Path(args.xlsx).resolve()
    target = src if args.in_place else Path(args.out or src.with_name(src.stem + "_RuleB.xlsx")).resolve()
    if target != src:
        shutil.copy2(src, target)
    result = apply(target)
    print(f"Workbook: {target}")
    return 0 if result["ok"] and result["matches_computed"] else 1


if __name__ == "__main__":
    sys.exit(main())
