"""CX Address Check verdict (new additive Polygon-step column).

analyze_cx_address judges, per TID, whether the CX-provided delivery address is
correct/recognisable using ONLY SIW/GCRS data already fetched. THREE verdicts:
OK / NOT OK / MISMATCH. Pure function, no network.
"""
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))

import polygon_check as pc  # noqa: E402


def _addr(line1="12 MG Road near the big temple", city="Bengaluru", state="Karnataka",
          pin="560001", conf="high", gtype="DeliveryPoint"):
    return {
        "address": {"line1": line1, "line2": "", "line3": "", "city": city,
                    "state": state, "pin": pin},
        "gcs_confidence": conf,
        "gcs_type": gtype,
    }


# --- OK -----------------------------------------------------------------------
def test_ok_clean_high_confidence_delivery_point():
    verdict = pc.analyze_cx_address(_addr())
    assert verdict.startswith("OK —")


# --- NOT OK -------------------------------------------------------------------
def test_not_ok_medium_confidence():
    verdict = pc.analyze_cx_address(_addr(conf="medium"))
    assert verdict.startswith("NOT OK —")


def test_not_ok_confidence_unknown_but_delivery_point():
    verdict = pc.analyze_cx_address(_addr(conf="", gtype="DeliveryPoint"))
    assert verdict.startswith("NOT OK —")


def test_not_ok_non_delivery_point_type():
    verdict = pc.analyze_cx_address(_addr(conf="high", gtype="Street"))
    # high confidence but not a delivery point -> partial, verify
    assert verdict.startswith("NOT OK —")


# --- MISMATCH -----------------------------------------------------------------
def test_mismatch_junky_address():
    verdict = pc.analyze_cx_address(_addr(line1="test dummy xxxx ____"))
    assert verdict.startswith("MISMATCH —")


def test_mismatch_too_thin_address():
    verdict = pc.analyze_cx_address(_addr(line1="ab", city="", state=""))
    assert verdict.startswith("MISMATCH —")


def test_mismatch_no_pin():
    verdict = pc.analyze_cx_address(_addr(pin=""))
    assert verdict.startswith("MISMATCH —")


def test_mismatch_malformed_pin():
    verdict = pc.analyze_cx_address(_addr(pin="56X01"))
    assert verdict.startswith("MISMATCH —")


def test_mismatch_pin_zone_contradicts_state():
    # PIN 560001 is zone 5 (Karnataka/AP/Telangana); naming "Delhi" (zone 1) contradicts it.
    verdict = pc.analyze_cx_address(_addr(state="Delhi"))
    assert verdict.startswith("MISMATCH —")
    assert "does not serve" in verdict


def test_mismatch_low_confidence():
    verdict = pc.analyze_cx_address(_addr(conf="low"))
    assert verdict.startswith("MISMATCH —")


# --- PIN zone check is typo-tolerant on the state name ------------------------
def test_pin_zone_allows_small_typo_in_state():
    # "Karnataka" slightly misspelt should still match zone 5, so NOT a mismatch.
    verdict = pc.analyze_cx_address(_addr(state="Karnatka"))
    assert not verdict.startswith("MISMATCH —")


# --- mapping + wrapper from a live GCRS row -----------------------------------
def test_cx_address_check_from_gcrs_row():
    row = {
        "Address_Line_1": "12 MG Road near the big temple",
        "Address_Line_2": "", "Address_Line_3": "",
        "City": "Bengaluru", "State": "Karnataka", "Postal_Code": "560001",
        "Confidence": "high", "Type": "DeliveryPoint",
    }
    assert pc.cx_address_check(row).startswith("OK —")


def test_cx_address_check_blank_for_no_row():
    assert pc.cx_address_check(None) == ""
    assert pc.cx_address_check({}) in ("", )  # empty row -> blank or non-crashing verdict


# --- check_pairs carries the new column additively ----------------------------
def test_check_pairs_adds_cx_column_without_changing_status(monkeypatch):
    # Stub the polygon fetch so no network is touched; one TID resolves "inside".
    gcrs_rows = [{
        "Query_Item": "123456789012", "Latitude": 0.5, "Longitude": 0.5,
        "Address_Line_1": "12 MG Road near the big temple", "City": "Bengaluru",
        "State": "Karnataka", "Postal_Code": "560001", "Confidence": "high",
        "Type": "DeliveryPoint",
    }]
    square = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}
    monkeypatch.setattr(pc, "fetch_polygons", lambda *a, **k: [{"geometry": square}])

    class _NoSession:
        def ensure_ready(self):
            pass

        def close(self):
            pass

    rows = pc.check_pairs([("123456789012", "ST1")], gcrs_rows, log=lambda _: None,
                          session=_NoSession())
    assert rows[0]["GCRS Status"] == pc.INSIDE_TEXT          # unchanged decision
    assert rows[0]["CX Address Check"].startswith("OK —")   # new additive column


def test_check_pairs_cx_blank_on_error_row():
    # A pair whose TID is not in gcrs_rows -> error status AND blank CX.
    rows = pc.check_pairs([("999999999999", "ST1")], [], log=lambda _: None,
                          session=type("S", (), {"ensure_ready": lambda s: None,
                                                 "close": lambda s: None})())
    assert rows[0]["GCRS Status"].startswith("Error —")
    assert rows[0]["CX Address Check"] == ""
