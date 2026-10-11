"""Every branch of the Working Sheet Remark (P) decision tree."""
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))

import remark_rules as rr  # noqa: E402

AF_A = "Invalid - Correct Station-A.in Shipment delivered - LM to Reattempt and Deliver"
AF_B = "Invalid - LM to Reattempt - Sent to correct Station"
AF_REST = "Interchange Not Possible / Other Group"
AG_INSIDE = "Invalid - It is Inside Jurisdiction as per GCRS Tool"


# --- step 1: protected values are never changed -----------------------------
def test_protected_values_are_preserved_regardless_of_other_columns():
    for protected in rr.PROTECTED_REMARKS:
        # Even with an AF verdict / station code / AG inside, protected P wins.
        assert rr.decide_remark(protected, AF_A, "HYBK", AG_INSIDE) == protected
        assert rr.decide_remark(protected, AF_REST, "0", "Outside") == protected


def test_protected_check_trims_whitespace():
    assert rr.is_protected("  Invalid - Already RTOed  ") is True
    assert rr.is_protected("Not Valid - OOR") is False


# --- J (State) driven rules -------------------------------------------------
def test_state_na_yields_incorrect_tid():
    assert rr.decide_remark("anything", AF_REST, "0", "Outside", j_value="#N/A") == \
        "Invalid- Incorrect TID / TID Not Found"


def test_state_in_transit_customer_yields_oor():
    assert rr.decide_remark("anything", AF_REST, "0", "Outside",
                            j_value="In Transit (DS -> Customer)") == "Invalid - OOR"


def test_state_rules_do_not_override_protected():
    # A protected P still wins over the State rules.
    assert rr.decide_remark("Invalid - Already RTOed", AF_REST, "0", "Outside",
                            j_value="#N/A") == "Invalid - Already RTOed"


def test_state_rules_win_over_af_ad_ag():
    # State #N/A beats an AF verdict / station code.
    assert rr.decide_remark("x", AF_A, "HYBK", AG_INSIDE, j_value="#N/A") == \
        "Invalid- Incorrect TID / TID Not Found"


def test_new_state_outcomes_are_protected():
    assert "Invalid- Incorrect TID / TID Not Found" in rr.PROTECTED_REMARKS
    assert "Invalid - OOR" in rr.PROTECTED_REMARKS
    assert len(rr.PROTECTED_REMARKS) == 6
    # Once written, they are never overwritten.
    assert rr.decide_remark("Invalid - OOR", AF_A, "HYBK", AG_INSIDE) == "Invalid - OOR"


def test_other_states_fall_through_to_existing_logic():
    # A normal state (e.g. Delivered) does not trigger the J rules.
    assert rr.decide_remark("x", AF_REST, "HYBK", "Outside", j_value="Delivered") == "Valid"


# --- step 2: named AF verdicts copy into P ----------------------------------
def test_af_named_verdict_copied_when_not_protected():
    assert rr.decide_remark("anything", AF_A, "0", "Outside") == AF_A
    assert rr.decide_remark("Valid", AF_B, "HYBK", AG_INSIDE) == AF_B


def test_af_named_verdict_does_not_override_protected():
    assert rr.decide_remark("Invalid - Already RTOed", AF_A, "0", "x") == "Invalid - Already RTOed"


# --- step 3a: 4-char station code in AD -> Valid ----------------------------
def test_station_code_in_ad_yields_valid():
    assert rr.decide_remark("old remark", AF_REST, "HYBK", AG_INSIDE) == "Valid"
    assert rr.decide_remark("old remark", "#N/A", "STVT", "Outside") == "Valid"


def test_station_code_recogniser():
    assert rr.is_station_code("HYBK") is True
    assert rr.is_station_code("stvt") is True
    assert rr.is_station_code("1234") is True   # 4 alphanumeric digits count as a code
    assert rr.is_station_code("0") is False
    assert rr.is_station_code("#N/A") is False
    assert rr.is_station_code("") is False
    assert rr.is_station_code("ABC") is False   # 3 chars
    assert rr.is_station_code("ABCDE") is False  # 5 chars
    assert rr.is_station_code(0) is False


# --- step 3b: AD not a code -> defer to AG ----------------------------------
def test_ad_zero_defers_to_ag_inside():
    assert rr.decide_remark("old", AF_REST, "0", AG_INSIDE) == AG_INSIDE


def test_ad_na_defers_to_ag_inside():
    assert rr.decide_remark("old", AF_REST, "#N/A", AG_INSIDE) == AG_INSIDE


def test_ad_zero_ag_outside_yields_valid():
    assert rr.decide_remark("old", AF_REST, "0", "Outside") == "Valid"


def test_ad_zero_ag_error_yields_valid():
    assert rr.decide_remark("old", AF_REST, "0", "Error — java.lang...") == "Valid"


def test_ad_na_ag_blank_yields_valid():
    assert rr.decide_remark("old", "#N/A", "#N/A", "") == "Valid"


# --- worksheet application --------------------------------------------------
class _FakeCell:
    def __init__(self, value):
        self.value = value


class _FakeWS:
    def __init__(self, rows):
        # rows: list of dict {P, AD, AF, AG, optional J}; row index starts at 2
        self.max_row = len(rows) + 1
        # Cache one cell object per (row, column) so writes persist, like openpyxl.
        self._cells = {}
        for i, data in enumerate(rows):
            row = i + 2
            for col, key in ((rr.COL_P, "P"), (rr.COL_AD, "AD"),
                             (rr.COL_AF, "AF"), (rr.COL_AG, "AG"), (rr.COL_J, "J")):
                self._cells[(row, col)] = _FakeCell(data.get(key))

    def cell(self, row, column):
        return self._cells[(row, column)]


def test_apply_to_worksheet_mixed():
    rows = [
        {"P": "Invalid - Already RTOed", "AD": "HYBK", "AF": AF_A, "AG": AG_INSIDE},  # protected
        {"P": "whatever", "AD": "0", "AF": AF_A, "AG": "Outside"},                    # AF named
        {"P": "whatever", "AD": "HYBK", "AF": AF_REST, "AG": "Outside"},              # station -> Valid
        {"P": "whatever", "AD": "0", "AF": AF_REST, "AG": AG_INSIDE},                 # AG inside
        {"P": "whatever", "AD": "#N/A", "AF": AF_REST, "AG": "Error — x"},            # -> Valid
    ]
    ws = _FakeWS(rows)
    summary = rr.apply_to_worksheet(ws)
    assert summary["rows"] == 5
    assert summary["protected"] == 1
    assert ws.cell(2, rr.COL_P).value == "Invalid - Already RTOed"
    assert ws.cell(3, rr.COL_P).value == AF_A
    assert ws.cell(4, rr.COL_P).value == "Valid"
    assert ws.cell(5, rr.COL_P).value == AG_INSIDE
    assert ws.cell(6, rr.COL_P).value == "Valid"


def test_apply_to_worksheet_state_rules():
    rows = [
        {"P": "x", "AD": "0", "AF": AF_REST, "AG": "Outside", "J": "#N/A"},
        {"P": "x", "AD": "HYBK", "AF": AF_A, "AG": AG_INSIDE, "J": "In Transit (DS -> Customer)"},
        {"P": "x", "AD": "HYBK", "AF": AF_REST, "AG": "Outside", "J": "Delivered"},
    ]
    ws = _FakeWS(rows)
    rr.apply_to_worksheet(ws)
    assert ws.cell(2, rr.COL_P).value == "Invalid- Incorrect TID / TID Not Found"
    assert ws.cell(3, rr.COL_P).value == "Invalid - OOR"
    assert ws.cell(4, rr.COL_P).value == "Valid"
