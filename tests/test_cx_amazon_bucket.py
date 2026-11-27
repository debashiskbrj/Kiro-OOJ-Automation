"""CX / Amazon Bucket + Bucket Reason (operator decision 2026-11-27) and the two visible
column renames (AG -> GCRS Polygon, AH -> India Post Database). No Excel is used."""
import sys
from pathlib import Path

import openpyxl
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import cx_amazon_bucket as cab  # noqa: E402
import fill_template as ft  # noqa: E402
import run_daily  # noqa: E402
import tid_polygon_checker as tpc  # noqa: E402

FULL = "House no 104 near post office burua"
OK = "OK — address correctly served by PIN (PIN 175131, Manali)"


@pytest.mark.parametrize("remark", ["Invalid - Already RTOed", "#N/A", None, "valid"])
def test_non_valid_rows_are_blank(remark):
    assert cab.classify(remark, 0.9, True, False, "Outside", OK, FULL) == ("", "")


@pytest.mark.parametrize("args, bucket, reason", [
    ((None, None, None, "Outside", OK, FULL), cab.CX, cab.R_NO_ADS),
    ((0.8, False, False, "Outside", OK, FULL), cab.CX, cab.R_PIN_STRONG),
    ((0.2, False, False, "Outside", OK, FULL), cab.CX, cab.R_PIN_WEAK),
    ((0.9, True, True, "Error — x", OK, FULL), cab.CX, cab.R_NO_PROOF),
    ((0.9, True, True, ft.POLYGON_INSIDE_TEXT, OK, FULL), cab.CX, cab.R_NO_PROOF),
    ((0.9, True, False, "Outside", "NOT OK — partially matches", FULL), cab.CX, cab.R_IP_PARTIAL),
    ((0.9, True, False, "Outside", "MISMATCH — not resolved", FULL), cab.CX, cab.R_IP_UNRESOLVED),
    ((0.9, True, False, "Outside", "", FULL), cab.CX, cab.R_IP_UNRESOLVED),
    ((0.9, True, False, "Outside", OK, "Berli Kalan,REWARI 8199839426,."), cab.CX, cab.R_SHORT),
    ((0.9, True, False, "Outside", OK, "Maincha  Maincha"), cab.CX, cab.R_INCOMPLETE),
    ((0.9, True, False, "Error — x", OK, FULL), cab.AMAZON, cab.R_AMZ_STATION),
    ((0.4, True, True, "Outside", OK, FULL), cab.AMAZON, cab.R_AMZ_OUTSIDE),
    (("#N/A", "TRUE", "FALSE", "Outside", OK, FULL), cab.AMAZON, cab.R_AMZ_STATION),
])
def test_rules_in_order(args, bucket, reason):
    assert cab.classify("Valid", *args) == (bucket, reason)


def test_address_quality():
    assert cab.address_quality("M-1207, RG Luxury Homes, sector 16B bhan") == "Full"
    assert cab.address_quality("jai pal Marg pankaj bhati kirana store") == "Full"
    assert cab.address_quality("Berli Kalan,REWARI 8199839426,.") == "Short"
    assert cab.address_quality("Maincha  Maincha") == "Incomplete"
    assert cab.address_quality(None) == "Incomplete"


def _workbook(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = ft.SHEET_WORK
    ws.append([f"h{i}" for i in range(1, 34)])

    def row(tid, n, o, p, r, ag):
        values = [None] * 33
        values[0], values[3], values[13], values[14], values[15], values[17], values[32] = \
            "url", tid, n, o, p, r, ag
        ws.append(values)
    row(111, 0.9, True, "Valid", False, "Outside")       # Amazon (full + OK address)
    row(222, 0.9, True, "Valid", False, "Outside")       # CX: short address
    row(333, 0.7, False, "Valid", True, "Outside")       # CX: pincode differs (strong)
    row(444, 0.7, False, "Not Valid - OOR", True, "x")   # blank (not Valid)
    ws.append([None] * 33)
    ag = wb.create_sheet(ft.SHEET_AGENT)
    ag.append(ft.AGENT_HEADERS)
    for tid in (111, 222, 333):
        ag.append([tid, "X", "Outside", OK])
    g = wb.create_sheet(cab.SHEET_GCRS)
    g.append(["TrackingId"])
    g.append(["Query_Item"] + [None] * 9 + ["Combine 1"])
    g.append([111] + [None] * 9 + [FULL])
    g.append([222] + [None] * 9 + ["Berli Kalan,REWARI"])
    g.append([333] + [None] * 9 + [FULL])
    path = tmp_path / "w.xlsx"
    wb.save(path)
    return path


def test_compute_reads_all_sheets(tmp_path):
    buckets, reasons, valid = cab.compute(_workbook(tmp_path))
    assert buckets == [cab.AMAZON, cab.CX, cab.CX, ""]
    assert reasons == [cab.R_AMZ_STATION, cab.R_SHORT, cab.R_PIN_STRONG, ""]
    assert valid == 3
    assert cab.count(buckets, valid) == {cab.CX: 2, cab.AMAZON: 1, "Blank": 1, "Valid": 3, "ok": True}
    assert cab.count([cab.CX, ""], 2)["ok"] is False


def test_run_daily_adds_the_columns_after_rule_b():
    src = (ROOT / "tools" / "run_daily.py").read_text(encoding="utf-8")
    assert src.index("rule_b_bucket.apply(out") < src.index("cx_amazon_bucket.apply(out")


# ------------------------------------------------------------------ renames
def test_visible_column_names_are_renamed():
    assert ft.HEADER_POLYGON == "GCRS Polygon" and ft.HEADER_INDIA_POST == "India Post Database"
    assert "GCRS Polygon" in run_daily.FINAL_COLUMNS and "GCRS Agent Space" not in run_daily.FINAL_COLUMNS
    assert ft.AGENT_HEADERS == ["Tracking ID", "Station", "GCRS Polygon", "India Post Database"]
    assert tpc.DISPLAY_HEADERS[2:] == ["GCRS Polygon", "India Post Database"]
    assert tpc.RESULT_HEADERS[2:] == ["GCRS Status", "CX Address Check"]   # internal keys unchanged


def test_template_ag1_header_renamed_and_formulas_kept():
    wb = openpyxl.load_workbook(ft.TEMPLATE)
    try:
        ws = wb[ft.SHEET_WORK]
        assert ws["AG1"].value == "GCRS Polygon"
        assert ws["AG2"].value == "=VLOOKUP(D2,'GCRS Agentspaces '!A:C,3,0)"
    finally:
        wb.close()


def test_fill_stamps_both_headers_even_without_polygon_rows(tmp_path):
    wb = openpyxl.load_workbook(ft.TEMPLATE)
    wb.template = False
    path = tmp_path / "t.xlsx"
    wb.save(path)
    out = tmp_path / "o.xlsx"
    ft.fill(path, out, [], [], [], [], [], [], polygon_rows=[], dsr_rows=[])
    ws = openpyxl.load_workbook(out)[ft.SHEET_WORK]
    assert ws["AG1"].value == "GCRS Polygon" and ws["AH1"].value == "India Post Database"
