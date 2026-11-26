"""Working Sheet E / polygon station (operator rule 2026-11-26): SCC Source when it is a real
4-character letter/digit station code, otherwise DSR Data station_code for the same TID."""
import sys
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import fill_template as ft  # noqa: E402
import run_daily as rd  # noqa: E402


def test_station_code_shape():
    for good in ("NZMF", "bru1", " HYBK ", "1234"):
        assert ft.is_station_code(good)
    for bad in ("3310810422", "c0a1a08c-3835-4039-8c04-c7ca14b4b34d", "AB1", "ABCDE", "", None, "#N/A", "AB-1"):
        assert not ft.is_station_code(bad)


def test_effective_station_order():
    assert ft.effective_station("NZMF", "HYBK", "QQQQ") == "NZMF"          # valid SCC wins
    assert ft.effective_station("3310810422", "hybk", "QQQQ") == "HYBK"     # bad SCC -> DSR
    assert ft.effective_station("3310810422", "", "QQQQ") == "3310810422"   # no DSR: SCC as is
    assert ft.effective_station("", "", "qqqq") == "QQQQ"                   # nothing: ticket


def test_e_formula_uses_scc_then_dsr_station_code():
    f = ft.station_formula(2, 17)
    assert f.startswith("=IF(") and "'SCC_(A,D,E,L)'!A:D,4,0" in f and "'DSR Data'!A:Q,17,0" in f
    assert "LEN(TRIM(" in f and "{1,2,3,4}" in f


def test_extend_working_sheet_writes_e_formula_on_every_row():
    wb = openpyxl.load_workbook(ft.TEMPLATE)
    ws = wb[ft.SHEET_WORK]
    ft.extend_working_sheet(ws, 3, use_dsr_zone=True, dsr_zone_col=22,
                            dsr_station_col=ft.dsr_station_column(wb[ft.SHEET_DSR]))
    assert ft.dsr_station_column(wb[ft.SHEET_DSR]) == 17
    assert ws["E2"].value == ft.station_formula(2, 17)
    assert "VLOOKUP(D4," in ws["E4"].value and "VLOOKUP(D2," not in ws["E4"].value


def test_polygon_station_uses_dsr_when_scc_source_is_not_a_station():
    scc = [{"Tracking ID": "375403897527", "Source": "3310810422"},
           {"Tracking ID": "111111111111", "Source": "NZMF"}]
    dsr = [{"tracking_id": "375403897527", "station_code": "HYBK"},
           {"tracking_id": "111111111111", "station_code": "BRUD"}]
    tech = [{"impacted_tracking_ids": "375403897527", "station_code": "XXXX"},
            {"impacted_tracking_ids": "111111111111", "station_code": "YYYY"}]
    pairs = rd.polygon_pairs_from_scc(scc, tech, dsr)
    assert pairs == [("375403897527", "HYBK"), ("111111111111", "NZMF")]
    rows = rd.align_polygon_rows([{"Tracking ID": t, "Station": s, "GCRS Status": "Outside"}
                                  for t, s in pairs], tech)
    # The workbook validator applies the same station rule and accepts these rows.
    assert ft.validate_polygon_rows(tech, scc, rows, dsr) == {"inside": 0, "outside": 2, "errors": 0}
