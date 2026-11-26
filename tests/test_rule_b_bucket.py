"""Rule B Bucket classification (operator spec 2026-11-25). No Excel is used."""
import sys
from pathlib import Path

import openpyxl
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import rule_b_bucket as rb  # noqa: E402

C, A, M = rb.CUSTOMER, rb.AMAZON, rb.MANUAL
OK, BAD = "OK - matches", "NOT OK - wrong zone"


@pytest.mark.parametrize("remark", ["Invalid - Already RTOed", "Not Valid - OOR", "#N/A", None, "valid", "Valid "])
def test_non_valid_rows_are_blank(remark):
    assert rb.classify(remark, 0.9, False, False, BAD) == ""


@pytest.mark.parametrize("args, expected", [
    ((0.60, False, True, None), C),        # B1: O FALSE and N >= 0.60
    ((0.10, False, True, BAD), C),         # B2: O FALSE and address bad
    ((0.59, False, False, OK), A),         # O FALSE, low N, address OK, R FALSE -> B4
    ((0.59, False, True, OK), M),          # O FALSE, low N, address OK, R TRUE -> B7
    ((0.99, True, True, OK), A),           # B3
    ((0.99, True, False, BAD), M),         # B5 beats B6
    ((None, None, False, OK), A),          # B4 with O missing
    ((None, None, None, BAD), C),          # B6
    (("#N/A", "#N/A", "#N/A", None), M),   # B7: all missing
    ((0.95, None, True, "MISMATCH x"), M), # MISMATCH is neither OK nor bad
    ((0.95, True, True, "Error - SIW"), M),
    ((0.95, True, True, "  ok "), A),      # trimmed + uppercase
    (("65%", False, True, None), C),       # percent text
])
def test_rules_in_order(args, expected):
    assert rb.classify("Valid", *args) == expected


def test_missing_probability_never_satisfies_threshold():
    assert rb.classify("Valid", "#N/A", False, True, None) == M
    assert rb.classify("Valid", None, False, True, None) == M


def test_flags_accept_text_booleans():
    assert rb.classify("Valid", 0.1, "TRUE", "TRUE", "OK") == A
    assert rb.classify("Valid", 0.7, "FALSE", "TRUE", None) == C


def test_compute_reads_working_sheet_and_agentspaces(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = rb.SHEET_WORK
    ws.append([f"h{i}" for i in range(1, 19)])

    def row(a, tid, n, o, p, r):
        values = [None] * 18
        values[0], values[3], values[13], values[14], values[15], values[17] = a, tid, n, o, p, r
        ws.append(values)
    row("url1", 111, 0.8, False, "Valid", True)          # Customer (B1)
    row("url2", 222, 0.1, True, "Valid", True)           # Amazon (B3, address OK)
    row("url3", 333, 0.1, True, "Invalid- Incorrect TID / TID Not Found", True)  # blank
    row("url4", 444, 0.1, True, "Valid", True)           # TID not in Agentspaces -> Manual
    ws.append([None] * 18)                               # trailing empty rows are ignored
    ag = wb.create_sheet(rb.SHEET_AGENT)
    ag.append(["Tracking ID", "Station", "GCRS Status", "CX Address Check"])
    ag.append([222, "X", "Inside", "OK"])
    ag.append([111, "X", "Inside", "NOT OK"])
    path = tmp_path / "w.xlsx"
    wb.save(path)
    values, valid = rb.compute(path)
    assert values == [C, A, "", M]
    assert valid == 3
    counts = rb.count(values, valid)
    assert counts == {C: 1, A: 1, M: 1, "Blank": 1, "Valid": 3, "ok": True}


def test_count_flags_mismatch():
    assert rb.count([C, ""], 2)["ok"] is False
