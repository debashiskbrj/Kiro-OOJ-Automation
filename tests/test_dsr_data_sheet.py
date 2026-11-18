"""DSR Raw data is saved into the template's "DSR Data" sheet and Working Sheet I
looks the zone up by TID (column D) from that sheet."""
import sys
import tempfile
import unittest
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import fill_template  # noqa: E402


class DsrDataSheetTests(unittest.TestCase):
    def test_template_has_dsr_data_sheet_with_zone_in_v(self):
        workbook = openpyxl.load_workbook(fill_template.TEMPLATE)
        try:
            sheet = workbook[fill_template.SHEET_DSR]
            self.assertEqual("tracking_id", sheet["A1"].value)
            self.assertEqual("zone", sheet["V1"].value)
            self.assertEqual(22, fill_template.dsr_zone_column(sheet))
        finally:
            workbook.close()

    def test_raw_rows_saved_under_headers_and_zone_lookup_keys_on_tid(self):
        tech_rows = [
            {"Sl": 1, "case_id": "C1", "impacted_tracking_ids": "123456789012", "station_code": "ST1"},
            {"Sl": 2, "case_id": "C2", "impacted_tracking_ids": "223456789012", "station_code": "ST2"},
        ]
        dsr_rows = [
            {"tracking_id": "123456789012", "station_code": "ST1", "zone": "South",
             "shipper_name": "ACME", "Extra Col": "x1"},
            {"tracking_id": "223456789012", "station_code": "ST2", "zone": "",
             "shipper_name": "=HACK()", "Extra Col": "x2"},
        ]
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory) / "working.xlsx"
            stats = fill_template.fill(
                fill_template.TEMPLATE, output, tech_rows, [], [], [], [], [], dsr_rows=dsr_rows,
            )
            self.assertEqual(2, stats["dsr_rows"])
            self.assertEqual(1, stats["dsr_zone"])
            workbook = openpyxl.load_workbook(output, data_only=False)
            try:
                dsr = workbook[fill_template.SHEET_DSR]
                headers = [c.value for c in dsr[1]]
                col = {name: headers.index(name) + 1 for name in headers if name}
                self.assertEqual(123456789012, dsr.cell(row=2, column=1).value)
                self.assertEqual("South", dsr.cell(row=2, column=22).value)
                self.assertEqual("ACME", dsr.cell(row=2, column=col["shipper_name"]).value)
                self.assertEqual("ST2", dsr.cell(row=3, column=col["station_code"]).value)
                # Formula-looking source text stays a literal string.
                cell = dsr.cell(row=3, column=col["shipper_name"])
                self.assertEqual("s", cell.data_type)
                # Unknown export column appended after the template headers, not dropped.
                self.assertIn("Extra Col", col)
                self.assertGreater(col["Extra Col"], 91)
                self.assertEqual("x1", dsr.cell(row=2, column=col["Extra Col"]).value)
                work = workbook[fill_template.SHEET_WORK]
                # Missing TID must show #N/A (no IFERROR), same as the template formula.
                self.assertEqual("=VLOOKUP(D2,'DSR Data'!A:V,22,0)", work["I2"].value)
                self.assertEqual("=VLOOKUP(D3,'DSR Data'!A:V,22,0)", work["I3"].value)
            finally:
                workbook.close()


if __name__ == "__main__":
    unittest.main()
