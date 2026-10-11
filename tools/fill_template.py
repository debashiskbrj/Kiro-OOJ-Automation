"""Fill the OOJ Excel template exactly the way the daily manual process does.

Each writer targets one worksheet of the template and mirrors the manual paste:
  Tech_Data           <- SIM ticket rows (11 cols, one TID per row)
  SCC_(A,D,E,L)       <- Tracking ID | State | Destination | Source | Ship Option
  Eagle_EYE data      <- searchId | type (FORWARD / REVERSE)
  Missort Data        <- PNAMA export, all columns aligned to the sheet's header row
  GCRS_Data           <- GCRS export from row 3 (A..I), K/L concat formulas per row
  ADS Predicted Data  <- ADS csv (A blank | index | TRACKINGID | ADS | pincode | proba)
  GCRS Agentspaces    <- polygon-check export (Tracking ID | Station | GCRS Status), optional
Then the Working Sheet formulas of row 2 are translated down to the last Tech_Data row
(columns out of scope — F, G, H, W, X, Y, Z, AA — are left blank).

Type rule: every TID and pincode that is purely digits is written as an int so the
template's VLOOKUP/= comparisons match (mirrors what Excel does when the user pastes
CSV values). DOC-prefixed TIDs stay text.
"""
import csv
import math
import re
from datetime import datetime
from pathlib import Path

import openpyxl
from openpyxl.formula.translate import Translator
from openpyxl.worksheet.formula import ArrayFormula

TEMPLATE = Path(__file__).parent.parent / "Docs" / "Templete_OOJ Process_Date_September_KABIDEBA.xltx"

SHEET_TECH = "Tech_Data"
SHEET_SCC = "SCC_(A,D,E,L)"
SHEET_EAGLE = "Eagle_EYE data"
SHEET_MISSORT = "Missort Data"
SHEET_GCRS = "GCRS_Data"
SHEET_ADS = "ADS Predicted Data"
SHEET_DSR = "Waterfall_DATA"  # repurposed to the required TID -> Zone mapping
SHEET_AGENT = "GCRS Agentspaces "  # trailing space is part of the template name
SHEET_WORK = "Working Sheet"

TECH_COLS = ["Sl", "case_id", "createdAt", "lastUpdatedAt", "station_code", "zone",
             "impacted_tracking_ids", "attachments", "status", "IssueUrl", "ShortId"]
# Working Sheet columns whose formulas must NOT be extended (dropped from scope).
SKIP_WORK_COLS = {"F", "G", "H", "W", "X", "Y", "Z", "AA"}


def num(v):
    """Digits-only strings -> int (so Excel lookups match); everything else unchanged."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v) if float(v).is_integer() else v
    s = str(v).strip()
    if re.fullmatch(r"\d+", s):
        return int(s)
    if re.fullmatch(r"\d+\.0+", s):
        return int(float(s))
    return s


def clear_rows(ws, first_row: int):
    """Delete all rows from first_row down (keeps headers/format above)."""
    if ws.max_row >= first_row:
        ws.delete_rows(first_row, ws.max_row - first_row + 1)


def set_literal_cell(cell, value):
    """Write untrusted source data as a literal, never as an Excel formula."""
    cell.value = value
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")):
        cell.data_type = "s"
    return cell


def append_literal_row(ws, values):
    row_number = ws.max_row + 1
    for column, value in enumerate(values, start=1):
        set_literal_cell(ws.cell(row=row_number, column=column), value)
    return row_number


def read_csv(path) -> list[dict]:
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


# ------------------------------------------------------------------ writers
def write_tech_data(ws, rows: list[dict]) -> int:
    clear_rows(ws, 2)
    for r in rows:
        append_literal_row(ws, [
            r.get("Sl"), r.get("case_id"), r.get("createdAt"), r.get("lastUpdatedAt"),
            r.get("station_code"), r.get("zone"), num(r.get("impacted_tracking_ids")),
            r.get("attachments"), r.get("status"), r.get("IssueUrl"), r.get("ShortId"),
        ])
    for col in ("C", "D"):
        for cell in ws[col][1:]:
            if isinstance(cell.value, datetime):
                cell.number_format = "yyyy-mm-dd hh:mm:ss"
    return len(rows)


def write_scc(ws, rows: list[dict]) -> int:
    """rows: dicts from the SCC SearchResults.csv (header names as exported)."""
    clear_rows(ws, 2)
    for r in rows:
        append_literal_row(ws, [num(r.get("Tracking ID")), r.get("State"), r.get("Destination"),
                                r.get("Source"), r.get("Ship Option")])
    return len(rows)


def write_eagle(ws, rows: list[dict]) -> int:
    """Write ONLY searchId + type, matching the manual EagleEye export paste.

    EagleEye CSV cells can be prefixed with an inverted comma to preserve long IDs in
    Excel. Strip every apostrophe before writing; keep no-data TIDs with a blank type.
    """
    clear_rows(ws, 2)
    for r in rows:
        search_id = str(r.get("searchId") or "").replace("'", "").strip()
        event_type = str(r.get("type") or "").replace("'", "").strip().upper()
        append_literal_row(ws, [num(search_id), event_type])
    return len(rows)


def write_missort(ws, rows: list[dict]) -> int:
    """Align the PNAMA export to the sheet's own header row (row 1)."""
    headers = [c.value for c in ws[1]]
    clear_rows(ws, 2)
    n_unknown = 0
    if rows:
        n_unknown = len(set(rows[0].keys()) - set(headers))
    for r in rows:
        append_literal_row(ws, [num(r.get(h)) if h in ("tracking_id", "reference_tid", "swa_tid", "ain_tid", "prox_tid")
                                else r.get(h) for h in headers])
    return len(rows), n_unknown


def write_gcrs(ws, rows: list[dict]) -> int:
    """GCRS export rows from row 3: A..I values, J blank, K/L = concat formulas (from template K3/L3)."""
    k_tpl = ws["K3"].value
    l_tpl = ws["L3"].value
    clear_rows(ws, 3)
    fields = ["Query_Item", "Address_Id", "Address_Line_1", "Address_Line_2", "Address_Line_3",
              "City", "State", "Country", "Postal_Code"]
    for i, r in enumerate(rows):
        row = 3 + i
        vals = [r.get(f) for f in fields]
        vals[0] = num(vals[0])
        vals[8] = num(vals[8])
        for j, v in enumerate(vals):
            set_literal_cell(ws.cell(row=row, column=1 + j), v)
        if isinstance(k_tpl, str) and k_tpl.startswith("="):
            ws.cell(row=row, column=11, value=Translator(k_tpl, origin="K3").translate_formula(f"K{row}"))
            ws.cell(row=row, column=12, value=Translator(l_tpl, origin="L3").translate_formula(f"L{row}"))
        else:  # template lost its formulas: write the concatenation as a value
            lines = [x for x in (r.get("Address_Line_1"), r.get("Address_Line_2"), r.get("Address_Line_3")) if x]
            set_literal_cell(ws.cell(row=row, column=11), ",".join(lines))
            set_literal_cell(ws.cell(row=row, column=12), ",".join([",".join(lines), r.get("City") or "", r.get("State") or "",
                                                                   str(r.get("Postal_Code") or ""), r.get("Country") or ""]))
    return len(rows)


def write_ads(ws, rows: list[dict]) -> int:
    """ADS csv rows: keys '', index, TRACKINGID, ADS, predicted_pincode_BiLSTM, predicted_pincode_proba_BiLSTM."""
    clear_rows(ws, 2)
    for r in rows:
        first = r.get("") if "" in r else None
        append_literal_row(ws, [num(first), num(r.get("index")), num(r.get("TRACKINGID")),
                                _required_float(r.get("ADS"), "ADS"),
                                _required_integer(r.get("predicted_pincode_BiLSTM"), "predicted_pincode_BiLSTM"),
                                _probability(r.get("predicted_pincode_proba_BiLSTM"))])
    return len(rows)


def write_dsr_zone(ws, rows: list[dict]) -> int:
    """Write only the required DSR mapping: tracking_id -> zone."""
    clear_rows(ws, 2)
    ws["A1"] = "tracking_id"
    ws["B1"] = "zone"
    seen = set()
    count = 0
    for row in rows:
        tid = str(row.get("tracking_id") or row.get("Tracking id") or "").strip()
        if tid.endswith(".0") and tid[:-2].isdigit():
            tid = tid[:-2]
        zone = str(row.get("zone") or row.get("Zone") or "").strip()
        if not tid or tid in seen or not zone:
            continue
        seen.add(tid)
        append_literal_row(ws, [num(tid), zone])
        count += 1
    return count


def write_polygon_rows(ws, rows: list[dict]) -> int:
    """Write local polygon audit rows to the template's legacy-named source sheet."""
    clear_rows(ws, 2)
    if ws.max_row < 1 or ws["A1"].value is None:
        ws.append(["Tracking ID", "Station", "GCRS Status"])
    for row in rows:
        tid = row.get("Tracking ID") or row.get("tracking_id") or row.get("TID")
        append_literal_row(
            ws,
            [
                "" if tid is None else str(tid),
                row.get("Station") or row.get("station"),
                row.get("GCRS Status") or row.get("status"),
            ],
        )
    return len(rows)


def _required_float(value, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"ADS field {field} is not numeric: {value!r}") from exc
    if not math.isfinite(number):
        raise ValueError(f"ADS field {field} is not finite: {value!r}")
    return number


def _required_integer(value, field: str) -> int:
    number = _required_float(value, field)
    if not number.is_integer():
        raise ValueError(f"ADS field {field} is not an integer: {value!r}")
    integer = int(number)
    if field == "predicted_pincode_BiLSTM" and not 100000 <= integer <= 999999:
        raise ValueError(f"ADS predicted pincode is not six digits: {integer}")
    return integer


def _probability(value) -> float:
    probability = _required_float(value, "predicted_pincode_proba_BiLSTM")
    if not 0.0 <= probability <= 1.0:
        raise ValueError(f"ADS probability outside [0,1]: {probability}")
    return probability


# ------------------------------------------------------------------ working sheet
def extend_working_sheet(ws, n_rows: int, use_dsr_zone: bool = False) -> dict:
    """Translate the TEMPLATE'S row-2 formulas to rows 2..n_rows+1.

    Column P is never reimplemented in Python: its exact template formula is translated
    row-by-row and remains the sole remark authority until the user supplies manual-invalid
    handling rules. Out-of-scope columns are blanked.
    """
    originals = {}
    for cell in ws[2]:
        col = cell.column_letter
        if col in SKIP_WORK_COLS:
            continue
        v = cell.value
        if isinstance(v, ArrayFormula):
            originals[col] = ("array", v.text)
        elif isinstance(v, str) and v.startswith("="):
            originals[col] = ("formula", v)
    if "P" not in originals:
        raise RuntimeError("Working Sheet P2 remark formula is missing — refusing to create a report")
    if use_dsr_zone:
        # User-confirmed source: Zone is mapped by TID from the DSR Raw data export.
        # This changes only column I; the authoritative remark formula in P is untouched.
        originals["I"] = (
            "formula",
            '=IFERROR(IF(VLOOKUP(D2,Waterfall_DATA!A:B,2,FALSE)="","",'
            'VLOOKUP(D2,Waterfall_DATA!A:B,2,FALSE)),"")',
        )
    last_row = max(ws.max_row, n_rows + 1)
    for r in range(2, last_row + 1):
        for cell in ws[r]:
            cell.value = None
    for r in range(2, n_rows + 2):
        for col, (kind, text) in originals.items():
            translated = Translator(text if text.startswith("=") else "=" + text, origin=f"{col}2").translate_formula(f"{col}{r}")
            if kind == "array":
                ws[f"{col}{r}"] = ArrayFormula(ref=f"{col}{r}", text=translated)
            else:
                ws[f"{col}{r}"] = translated
    if ws.auto_filter.ref:
        ws.auto_filter.ref = f"A1:AG{n_rows + 1}"
    return {"formula_columns": sorted(originals.keys(), key=lambda c: (len(c), c)), "rows": n_rows}


# ------------------------------------------------------------------ driver
POLYGON_INSIDE_TEXT = "Invalid - It is Inside Jurisdiction as per GCRS Tool"
POLYGON_OUTSIDE_TEXT = "Outside"
POLYGON_ERROR_PREFIX = "Error — "


def _normalized_tid_text(value) -> str:
    text = str(value or "").strip().lstrip("\ufeff").replace("'", "")
    if text.endswith(".0") and text[:-2].isdigit():
        return text[:-2]
    return text.upper() if text.upper().startswith("DOC") else text


def validate_polygon_rows(tech_rows, scc_rows, polygon_rows) -> dict[str, int]:
    """Verify exact ordered TID/effective-station identity and status coverage."""
    if len(polygon_rows) != len(tech_rows):
        raise ValueError(f"polygon rows must align with Tech_Data: {len(polygon_rows)} != {len(tech_rows)}")

    scc_station: dict[str, str] = {}
    for row in scc_rows:
        tid = _normalized_tid_text(row.get("Tracking ID"))
        station = str(row.get("Source") or "").strip().upper()
        if not tid or not station:
            continue
        if tid in scc_station and scc_station[tid] != station:
            raise ValueError(f"conflicting SCC Source stations for TID {tid}")
        scc_station[tid] = station

    counts = {"inside": 0, "outside": 0, "errors": 0}
    for index, (tech_row, polygon_row) in enumerate(zip(tech_rows, polygon_rows), start=1):
        required = {"Tracking ID", "Station", "GCRS Status"}
        if not required.issubset(polygon_row):
            raise ValueError(f"polygon row {index} is missing required columns")
        tech_tid = _normalized_tid_text(tech_row.get("impacted_tracking_ids"))
        polygon_tid = _normalized_tid_text(polygon_row.get("Tracking ID"))
        if tech_tid != polygon_tid:
            raise ValueError(f"polygon/Tech_Data TID mismatch at row {index}: {polygon_tid!r} != {tech_tid!r}")
        expected_station = scc_station.get(tech_tid) or str(tech_row.get("station_code") or "").strip().upper()
        polygon_station = str(polygon_row.get("Station") or "").strip().upper()
        if polygon_station != expected_station:
            raise ValueError(
                f"polygon/effective-station mismatch at row {index}: "
                f"{polygon_station!r} != {expected_station!r}"
            )
        status = str(polygon_row.get("GCRS Status") or "")
        if status == POLYGON_INSIDE_TEXT:
            counts["inside"] += 1
        elif status == POLYGON_OUTSIDE_TEXT:
            counts["outside"] += 1
        elif status.startswith(POLYGON_ERROR_PREFIX) and len(status) > len(POLYGON_ERROR_PREFIX):
            counts["errors"] += 1
        else:
            raise ValueError(f"invalid polygon status at row {index}: {status!r}")
    if sum(counts.values()) != len(polygon_rows):
        raise ValueError("polygon status counts do not cover every row")
    return counts


def fill(template: Path, out_path: Path, tech_rows, scc_rows, eagle_rows, missort_rows, gcrs_rows, ads_rows,
         polygon_rows=None, dsr_rows=None) -> dict:
    dsr_rows_provided = dsr_rows is not None
    dsr_rows = dsr_rows or []
    polygon_rows = polygon_rows or []
    polygon_counts = validate_polygon_rows(tech_rows, scc_rows, polygon_rows) if polygon_rows else {
        "inside": 0,
        "outside": 0,
        "errors": 0,
    }

    wb = openpyxl.load_workbook(template)
    wb.template = False
    stats = {}
    stats["tech"] = write_tech_data(wb[SHEET_TECH], tech_rows)
    stats["scc"] = write_scc(wb[SHEET_SCC], scc_rows)
    stats["eagle"] = write_eagle(wb[SHEET_EAGLE], eagle_rows)
    stats["missort"], stats["missort_unknown_cols"] = write_missort(wb[SHEET_MISSORT], missort_rows)
    stats["gcrs"] = write_gcrs(wb[SHEET_GCRS], gcrs_rows)
    stats["ads"] = write_ads(wb[SHEET_ADS], ads_rows)
    stats["dsr_zone"] = write_dsr_zone(wb[SHEET_DSR], dsr_rows)
    stats["polygon"] = write_polygon_rows(wb[SHEET_AGENT], polygon_rows)
    stats["polygon_counts"] = polygon_counts
    stats["working"] = extend_working_sheet(
        wb[SHEET_WORK],
        len(tech_rows),
        use_dsr_zone=dsr_rows_provided,
    )
    if polygon_rows:
        working_sheet = wb[SHEET_WORK]
        for row_number, polygon_row in enumerate(polygon_rows, start=2):
            set_literal_cell(working_sheet.cell(row=row_number, column=33), polygon_row.get("GCRS Status") or "")
    wb.save(out_path)
    stats["out"] = str(out_path)
    return stats
