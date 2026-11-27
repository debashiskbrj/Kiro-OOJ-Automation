"""QuickSight dashboard links (operator, 2026-11-27): PNAMA and DSR open their direct sheet
links. browser_signin and the DSR lane must use the same constants."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import dsr_export  # noqa: E402
import pnama_export  # noqa: E402

BASE = "https://us-east-1.quicksight.aws.amazon.com/sn/account/amazonbi/dashboards/"
PNAMA_URL = (BASE + "1650383a-7bfd-409c-9406-d364810f8019/sheets/"
             "1650383a-7bfd-409c-9406-d364810f8019_e70590da-c0eb-4ee1-8b83-dc7f288292a9")
DSR_URL = (BASE + "9a951c72-3cae-4c18-bfd1-69231a4f10d1/sheets/"
           "9a951c72-3cae-4c18-bfd1-69231a4f10d1_9fe3f0ac-9f42-4ef3-b81e-6352c9158f76")


def test_pnama_and_dsr_use_the_direct_sheet_links():
    assert pnama_export.DASHBOARD_URL == PNAMA_URL
    assert dsr_export.DASHBOARD_URL == DSR_URL


def test_dsr_lane_drives_the_shared_driver_with_its_own_link():
    original = (pnama_export.DASHBOARD_URL, pnama_export.TARGET_TAB, pnama_export.CONTROL_LABEL)
    try:
        dsr_export.configure(lambda *_: None)
        assert pnama_export.DASHBOARD_URL == DSR_URL
        assert pnama_export.TARGET_TAB == "DSR Raw data"
    finally:
        pnama_export.DASHBOARD_URL, pnama_export.TARGET_TAB, pnama_export.CONTROL_LABEL = original


def test_no_other_file_hard_codes_a_dashboard_link():
    for path in list((ROOT / "tools").rglob("*.py")) + list((ROOT / "tools").rglob("*.js")):
        if path.name in ("pnama_export.py", "dsr_export.py") or "captures" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        assert "/dashboards/1650383a" not in text and "/dashboards/9a951c72" not in text, path
