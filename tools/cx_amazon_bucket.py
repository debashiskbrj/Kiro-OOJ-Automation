"""CX / Amazon Bucket + Bucket Reason columns (operator decision 2026-11-27).

Every VALID ticket in the Working Sheet (Remark P exactly "Valid") gets exactly one of two
buckets, plus a short reason. Every other row is left blank.

  Amazon Bucket only when ALL of these are proven:
    - Pincode Validation (O) = TRUE (customer pincode = ADS predicted pincode);
    - Station Validation (R) = FALSE (pincode belongs to another station) OR
      GCRS Polygon (AG) = "Outside" (location outside the station polygon);
    - India Post Database (AH) starts with "OK" (address served by the PIN);
    - the GCRS address (GCRS_Data column K, address lines 1-3) is a FULL address.
  CX Bucket for every other Valid row, with the first failing check as the reason.

Full address = at least 4 words once phone numbers are removed, with a house/flat/door/plot
number or a gali/road/street/sector/block word. Short = 3+ words without such detail.
Incomplete = fewer than 3 words.

Written after the Excel recalc (needs calculated values) by tools/write_rule_b_column.ps1
into the next empty Working Sheet columns (AK, AL), plain text, no other cell changed.
Rule B (AJ) is not touched. Non-fatal: the run logs a WARNING if this step fails.

Usage (existing workbook; writes a COPY unless --in-place):
  py -3.13 cx_amazon_bucket.py "C:\\...\\Templete_OOJ Process_....xlsx" [--out copy.xlsx | --in-place]
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

import openpyxl

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import rule_b_bucket as rb  # noqa: E402  (shared value helpers + the Excel column writer)

PS_SCRIPT = HERE / "write_rule_b_column.ps1"
HEADER_BUCKET = "CX / Amazon Bucket"
HEADER_REASON = "Bucket Reason"
CX = "CX Bucket"
AMAZON = "Amazon Bucket"
BUCKETS = (CX, AMAZON)
SHEET_GCRS = "GCRS_Data"
PROB_STRONG = 0.60
# Working Sheet 0-based column indexes
COL_A, COL_D, COL_N, COL_O, COL_P, COL_R, COL_AG = 0, 3, 13, 14, 15, 17, 32

R_NO_ADS = "No ADS pincode prediction"
R_PIN_STRONG = "Pincode differs from prediction (strong, >=60%)"
R_PIN_WEAK = "Pincode differs from prediction (weak, <60%)"
R_NO_PROOF = "Pincode and station correct, no wrong-station proof"
R_IP_PARTIAL = "India Post: address only partly matches PIN"
R_IP_UNRESOLVED = "India Post: address not resolved"
R_SHORT = "Address too short (no house/street detail)"
R_INCOMPLETE = "Address incomplete"
R_AMZ_STATION = "Pincode correct, mapped to other station, full address OK"
R_AMZ_OUTSIDE = "Pincode correct, location outside station polygon, full address OK"

_HOUSE = re.compile(
    r"\b(h\.?\s*no|house|flat|door|plot|room|block|tower|apt|apartment|floor|shop|gali|lane|"
    r"street|road|marg|sector|ward|khasra|qtr|quarter)\b|#|\b[a-z]?-?\d{1,5}[a-z]?\b", re.I)
_PHONE = re.compile(r"\b[6-9]\d{9}\b")


def address_quality(line) -> str:
    """Full / Short / Incomplete for the GCRS address lines 1-3."""
    text = re.sub(r"[.,\-_/]+", " ", _PHONE.sub(" ", str(line or ""))).strip()
    words = [w for w in text.split() if len(w) > 1]
    if len(words) < 3:
        return "Incomplete"
    return "Full" if _HOUSE.search(text) and len(words) >= 4 else "Short"


def india_post_verdict(text) -> str:
    """OK / NOT OK / MISMATCH / NONE from the India Post Database (AH) text."""
    upper = str(text or "").strip().upper()
    if upper.startswith("NOT OK"):
        return "NOT OK"
    if upper.startswith("OK"):
        return "OK"
    if upper.startswith("MISMATCH"):
        return "MISMATCH"
    return "NONE"


def classify(remark, probability, pincode_validation, station_validation, gcrs_polygon,
             india_post, address_line) -> tuple[str, str]:
    """(bucket, reason) for one Working Sheet row; ("", "") for every non-Valid row."""
    if remark != "Valid":
        return "", ""
    o_flag = rb.as_flag(pincode_validation)
    if o_flag is None:
        return CX, R_NO_ADS
    if o_flag is False:
        prob = rb.as_probability(probability)
        return CX, (R_PIN_STRONG if prob is not None and prob >= PROB_STRONG else R_PIN_WEAK)
    wrong_station = rb.as_flag(station_validation) is False
    outside = str(gcrs_polygon or "").strip() == "Outside"
    if not (wrong_station or outside):
        return CX, R_NO_PROOF
    verdict = india_post_verdict(india_post)
    if verdict == "NOT OK":
        return CX, R_IP_PARTIAL
    if verdict != "OK":
        return CX, R_IP_UNRESOLVED
    quality = address_quality(address_line)
    if quality == "Short":
        return CX, R_SHORT
    if quality == "Incomplete":
        return CX, R_INCOMPLETE
    return AMAZON, (R_AMZ_STATION if wrong_station else R_AMZ_OUTSIDE)


def compute(xlsx: Path) -> tuple[list[str], list[str], int]:
    """Bucket + reason per Working Sheet row 2..last row with data in column A, and the
    Valid count."""
    wb = openpyxl.load_workbook(xlsx, read_only=True, data_only=True)
    try:
        india_post, address = {}, {}
        if rb.SHEET_AGENT in wb.sheetnames:
            for row in wb[rb.SHEET_AGENT].iter_rows(min_row=2, max_col=4, values_only=True):
                tid = rb.normalize_tid(row[0] if row else None)
                if tid and tid not in india_post:
                    india_post[tid] = row[3] if len(row) > 3 else None
        if SHEET_GCRS in wb.sheetnames:
            for row in wb[SHEET_GCRS].iter_rows(min_row=3, max_col=11, values_only=True):
                tid = rb.normalize_tid(row[0] if row else None)
                if tid and tid not in address:
                    address[tid] = row[10] if len(row) > 10 else None
        rows = []
        for row in wb[rb.SHEET_WORK].iter_rows(min_row=2, max_col=COL_AG + 1, values_only=True):
            rows.append(tuple(row) + (None,) * (COL_AG + 1 - len(row)))
    finally:
        wb.close()
    last = max((i for i, row in enumerate(rows) if row[COL_A] not in (None, "")), default=-1)
    rows = rows[:last + 1]
    buckets, reasons = [], []
    for row in rows:
        tid = rb.normalize_tid(row[COL_D])
        bucket, reason = classify(row[COL_P], row[COL_N], row[COL_O], row[COL_R], row[COL_AG],
                                  india_post.get(tid), address.get(tid))
        buckets.append(bucket)
        reasons.append(reason)
    valid = sum(1 for row in rows if row[COL_P] == "Valid")
    return buckets, reasons, valid


def _read_column(xlsx: Path, header: str) -> list:
    wb = openpyxl.load_workbook(xlsx, read_only=True, data_only=True)
    try:
        ws = wb[rb.SHEET_WORK]
        head = list(next(ws.iter_rows(min_row=1, max_row=1, values_only=True)))
        col = head.index(header)
        return [row[col] if len(row) > col else None
                for row in ws.iter_rows(min_row=2, min_col=1, max_col=col + 1, values_only=True)]
    finally:
        wb.close()


def _write_column(xlsx: Path, header: str, values: list[str], timeout: int) -> str:
    """Write one plain-text column with Excel; returns its letter."""
    fd, values_file = tempfile.mkstemp(prefix="ooj_cxamz_", suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(values))
        res = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(PS_SCRIPT),
             "-Path", str(xlsx), "-ValuesFile", values_file, "-Rows", str(len(values)),
             "-Header", header],
            capture_output=True, text=True, timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))  # no console flash
    finally:
        try:
            os.remove(values_file)
        except OSError:
            pass
    line = next((ln for ln in res.stdout.splitlines() if ln.startswith("RULEB_")), "")
    if not line.startswith("RULEB_OK"):
        raise RuntimeError(f"Excel write failed for '{header}': {line or res.stderr.strip()[:300]}")
    return line.split()[-1]


def count(buckets, valid: int) -> dict:
    c = Counter(v for v in buckets if v)
    result = {name: c.get(name, 0) for name in BUCKETS}
    result["Blank"] = sum(1 for v in buckets if not v)
    result["Valid"] = valid
    result["ok"] = sum(result[name] for name in BUCKETS) == valid and set(c) <= set(BUCKETS)
    return result


def apply(xlsx: Path, log=print, timeout: int = 600) -> dict:
    """Compute, write both columns with Excel, re-read them and validate the counts."""
    xlsx = Path(xlsx)
    buckets, reasons, valid = compute(xlsx)
    col_bucket = _write_column(xlsx, HEADER_BUCKET, buckets, timeout)
    col_reason = _write_column(xlsx, HEADER_REASON, reasons, timeout)
    written_b = _read_column(xlsx, HEADER_BUCKET)[:len(buckets)]
    written_r = _read_column(xlsx, HEADER_REASON)[:len(reasons)]
    result = count(written_b, valid)
    result["columns"] = f"{col_bucket},{col_reason}"
    result["matches_computed"] = (written_b == [v or None for v in buckets]
                                  and written_r == [v or None for v in reasons])
    result["reasons"] = dict(Counter(r for r in reasons if r).most_common())
    ok = result["ok"] and result["matches_computed"]
    log(f"CX / Amazon Bucket (columns {col_bucket}, {col_reason}): CX={result[CX]}, "
        f"Amazon={result[AMAZON]}, Blank={result['Blank']}; Valid in P={valid} -> "
        f"{'OK' if ok else 'MISMATCH'}")
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("xlsx")
    ap.add_argument("--out", help="write to this copy (default: <name>_Bucket.xlsx next to the input)")
    ap.add_argument("--in-place", action="store_true", help="write into the input workbook itself")
    args = ap.parse_args()
    src = Path(args.xlsx).resolve()
    target = src if args.in_place else Path(args.out or src.with_name(src.stem + "_Bucket.xlsx")).resolve()
    if target != src:
        shutil.copy2(src, target)
    result = apply(target)
    print(f"Workbook: {target}")
    return 0 if result["ok"] and result["matches_computed"] else 1


if __name__ == "__main__":
    sys.exit(main())
