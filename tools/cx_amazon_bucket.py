"""CX / Amazon Bucket + Bucket Reason columns (operator decision 2026-11-27).

Every VALID ticket in the Working Sheet (Remark P exactly "Valid") gets exactly one of two
buckets, plus a short reason. Every other row is left blank.

  Amazon Bucket only when ALL of these are proven:
    - Pincode Validation (O) = TRUE (customer pincode = ADS predicted pincode);
    - Station Validation (R) is known (TRUE or FALSE) or GCRS Polygon (AG) = "Outside";
    - India Post Database (AH) starts with "OK" (address served by the PIN), or (v1.5.2)
      "NOT OK — partially matches" while the Full address names a locality India Post
      lists for that PIN (e.g. "Block Pinjore" for PIN 134102 Panchkula, Pinjore);
    - the GCRS address (GCRS_Data address lines 1-3) is a FULL address.
  The Amazon reason says why: R FALSE = mapped to another station; AG Outside = outside the
  polygon; R TRUE = the station claims OOJ although pincode, station and address are all
  correct (v1.5.1, operator 2026-11-27: a full correct address is never CX).
  CX Bucket for every other Valid row, with the first failing check as the reason.

Address quality (v1.5.1, rebuilt after the operator's hand check of 224 addresses):
  GCRS cuts address lines mid-word ("Vem" + "ana Road"), so lines are re-joined first
  (join_address_lines). Phone numbers, 6+ digit runs (PIN), labels and numbers that belong
  to a sector / phase / road no / gali no / ward / PG etc. are removed (they are not house
  numbers). Landmark phrases (near / opp / behind / beside ...) are kept apart.
  Full when one of these holds:
    - a house/flat/door/plot/villa/room/shop number and 4+ distinct words;
    - another unit number (e.g. D-6/411, 8-2-370, No 30, 302, #12, c/o, 3rd floor) plus a
      street/area word, a building name or a landmark;
    - a building/society/institution name plus a street/area word or 4+ words;
    - a street word plus a landmark plus an area word (or a second landmark);
    - a rural address: village/vill/vpo/at-po plus a landmark plus 2 of post / tehsil /
      district / PS (or an area word such as ward / mohalla).
  Short = 3+ distinct words (or a unit number) without that detail. Incomplete = less.

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
R_NO_PROOF = "Station of predicted pincode unknown, no wrong-station proof"
R_IP_PARTIAL = "India Post: address only partly matches PIN"
R_IP_UNRESOLVED = "India Post: address not resolved"
R_SHORT = "Address too short (no house/street detail)"
R_INCOMPLETE = "Address incomplete"
R_AMZ_STATION = "Pincode correct, mapped to other station, full address OK"
R_AMZ_OUTSIDE = "Pincode correct, location outside station polygon, full address OK"
R_AMZ_INSIDE = "Station claims OOJ but pincode, station and address are correct"

_LANDMARK_WORDS = r"(?:near|nr|opp|opposite|behind|beside|besides|next\s+to|landmark|in\s+front\s+of)"
_LANDMARK = re.compile(rf"\b{_LANDMARK_WORDS}\b")
_LANDMARK_PHRASE = re.compile(rf"\b{_LANDMARK_WORDS}\b[^,;()]*")
_LABELS = re.compile(r"\b(?:name|phone|shipping\s+address|contact\s+buyer|mobile\s+no|m\.?\s?no)\s*[:.-]")
_LONG_DIGITS = re.compile(r"(?<!\d)\d{6,}(?!\d)")   # PIN codes and phone numbers
# numbers that name a sector / phase / road etc. are not house numbers
_CONTEXT_NUM = re.compile(
    r"\b(sector|sec|phase|ph|stage|cross|main|road\s*no|rd\s*no|rdno|street\s*no|lane\s*no|"
    r"gali\s*no|ward\s*no|ward|block\s*no|gate(?:\s*no)?|pin(?:\s*code)?|pg|lv|kms?|nh|sh|hyw)"
    r"\s*[-:.#]?\s*\d+[a-z]?\b")
_STRONG_UNIT = re.compile(
    r"\b(?:h\.?\s*no|house\s*(?:no|number)|flat|falt|ft\.?\s*no|ftno|door|plot|villa|room|qtr|"
    r"quarter|khasra|shop|showroom|apartment\s*no)\.?\s*(?:no\.?|number|#)?\s*[-:#.]?\s*[a-z]?\s*\d")
_WEAK_UNIT = re.compile(
    r"#\s*\d|\bc/o\b|\b\d+\s*(?:st|nd|rd|th)?\s*floor\b|"
    r"\b[a-z]{0,2}-?\d{1,5}[a-z]?\b(?!\s*(?:st|nd|rd|th)\b)")
_ROAD_NAME = re.compile(
    r"\b(?:school|hospital|bank|temple|mandir|college|church|masjid|office|station|club|mill|"
    r"market|stadium)\s+(road|rd|street|st|marg|lane)\b")
_BUILDING = re.compile(
    r"\b(?:apartments?|appartments?|apts?|residency|residence|enclaves?|society|soc|towers?|"
    r"heights|homes|villas?|complex|mansion|building|bldg|nivas|niwas|nilayam|sadan|bhawan|"
    r"bhavan|plaza|arcade|mall|campus|business\s+park|tech\s+park|it\s+park|pg|hostel|homestay|"
    r"hotel|resort|university|college|institute|faculty|academy|hospital|clinic|bank|office|"
    r"technologies|pvt|ltd|store|showroom|restaurant|quarters|flats|house)\b")
_STREET = re.compile(r"\b(?:road|rd|street|lane|gali|marg|salai|path|avenue|highway|cross)\b")
_AREA = re.compile(
    r"\b(?:nagar|nagara|colony|layout|phase|block|stage|vihar|mohalla|mohala|ward|extension|"
    r"extn|township|garden|gardens|puram|pura|bagh|ganj|chowk|bazar|bazaar)\b")
_SECTOR_NUM = re.compile(r"\bsec(?:tor)?\s*[-.]?\s*\d")
_RURAL = re.compile(r"\b(?:village|vill|vpo|gram)\b|\bat\.?\s*(?:and\s+)?p(?:ost|\.?\s?o)\b")
_ADMIN = {
    "post": re.compile(r"\b(?:post|po|p\.\s?o)\b"),
    "tehsil": re.compile(r"\b(?:tehsil|tahsil|taluka|tal|tq|mandal|block)\b"),
    "district": re.compile(r"\b(?:district|dist|distt)\b"),
    "police": re.compile(r"\b(?:ps|p\.\s?s|thana)\b"),
}


def join_address_lines(*lines) -> str:
    """Re-join GCRS address lines 1-3. GCRS cuts lines at a fixed width, often mid-word
    ("Vem" + "ana Road"), so a line ending in a letter followed by one starting lower-case
    (or digit followed by digit) is glued back without a separator."""
    out = ""
    for part in (str(p).strip() for p in lines if p is not None):
        if not part:
            continue
        if not out:
            out = part
        elif (out[-1].isalpha() and part[0].islower()) or (out[-1].isdigit() and part[0].isdigit()):
            out += part
        else:
            out += (" " if out[-1] in ",;" else ", ") + part
    return out


def _words(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", text) if len(w) > 1}


def address_quality(line) -> str:
    """Full / Short / Incomplete for the GCRS address (lines 1-3 joined)."""
    text = str(line or "").lower()
    text = re.sub(r"&?(?:quot|amp);", " ", text)
    text = _LONG_DIGITS.sub(" ", _LABELS.sub(" ", text))
    has_sector = bool(_SECTOR_NUM.search(text))
    text = _CONTEXT_NUM.sub(lambda m: f" {m.group(1)} ", text)
    words = _words(text)
    landmarks = len(_LANDMARK.findall(text))
    core = _ROAD_NAME.sub(lambda m: f" {m.group(1)} ", _LANDMARK_PHRASE.sub(" , ", text))
    strong = bool(_STRONG_UNIT.search(core))
    unit = strong or bool(_WEAK_UNIT.search(core))
    street = bool(_STREET.search(core))
    area = bool(_AREA.search(core)) or has_sector
    building = bool(_BUILDING.search(core))
    core_words = len(_words(core))
    rural_admin = sum(1 for rx in _ADMIN.values() if rx.search(text))
    full = (
        (strong and len(words) >= 4)
        or (unit and (street or area or building or landmarks))
        or (building and (street or area or core_words >= 4))
        or (street and landmarks and (area or landmarks >= 2))
        or (bool(_RURAL.search(text)) and landmarks
            and (rural_admin >= 2 or bool(_AREA.search(text))))
    )
    if full:
        return "Full"
    return "Short" if len(words) >= 3 or unit else "Incomplete"


_IP_AREAS = re.compile(r"\(\s*PIN\s*\d{6}\s*,([^)]*)\)", re.I)


def _squash(text) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def india_post_area_named(india_post, address) -> bool:
    """True when the address names a locality India Post gives for the PIN.
    India Post text: "NOT OK — partially matches ... (PIN 134102, Panchkula, Pinjore)" —
    the first name is the district/city, the rest are localities. Only a locality counts
    (a city name alone is too wide). v1.5.2, operator case 374689481819."""
    m = _IP_AREAS.search(str(india_post or ""))
    if not m:
        return False
    names = [n.strip() for n in m.group(1).split(",") if n.strip()][1:]
    addr = _squash(address)
    return any(len(_squash(n)) >= 4 and _squash(n) in addr for n in names)


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
    r_flag = rb.as_flag(station_validation)
    wrong_station = r_flag is False
    outside = str(gcrs_polygon or "").strip() == "Outside"
    if r_flag is None and not outside:
        return CX, R_NO_PROOF
    verdict = india_post_verdict(india_post)
    quality = address_quality(address_line)
    if verdict == "NOT OK" and not (quality == "Full"
                                    and india_post_area_named(india_post, address_line)):
        return CX, R_IP_PARTIAL
    if verdict not in ("OK", "NOT OK"):
        return CX, R_IP_UNRESOLVED
    if quality == "Short":
        return CX, R_SHORT
    if quality == "Incomplete":
        return CX, R_INCOMPLETE
    if wrong_station:
        return AMAZON, R_AMZ_STATION
    return AMAZON, (R_AMZ_OUTSIDE if outside else R_AMZ_INSIDE)


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
                    row = tuple(row) + (None,) * (11 - len(row))
                    # address lines 1-3 (C:E) re-joined; column K (comma join) as fallback
                    address[tid] = join_address_lines(*row[2:5]) or row[10]
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
