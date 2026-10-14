"""Working Sheet Remark (P) post-recalc rules.

The template's P2 formula now computes the final Remark for most rows. After recalc the
automation only finishes two cases:
  1. J (State) == "#N/A"                 -> "Invalid- Incorrect TID / TID Not Found"
  2. P contains "LM to Reattempt"        -> decide from AF, then AG, else "Valid"
Every other P value the formula produced is kept verbatim.
"""
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))

import remark_rules as rr  # noqa: E402

AF_A = "Invalid - Correct Station-A.in Shipment delivered - LM to Reattempt and Deliver"
AF_B = "Invalid - LM to Reattempt - Sent to correct Station"
AF_REST = "Interchange Not Possible / Other Group"
AG_INSIDE = "Invalid - It is Inside Jurisdiction as per GCRS Tool"

# The two "LM to Reattempt" trigger values the P2 formula emits for the automation.
P_TRIGGER_PINCODE_CORRECT = "Invalid - Address Pin-Code Correct - LM to Reattempt"
P_TRIGGER_PINCODE_MISMATCH = (
    "Address-Pin-code mismatch found – LM to reattempt as the right pin-code is served "
    "by same station Station code match"
)


# --- rule 1: J (State) == "#N/A" -> incorrect TID ---------------------------
def test_state_na_yields_incorrect_tid():
    # #N/A state always means TID-not-found, regardless of P / AF / AG.
    assert rr.decide_remark("anything", AF_REST, "0", "Outside", j_value="#N/A") == \
        "Invalid- Incorrect TID / TID Not Found"
    assert rr.decide_remark(P_TRIGGER_PINCODE_CORRECT, AF_A, "HYBK", AG_INSIDE, j_value="#N/A") == \
        "Invalid- Incorrect TID / TID Not Found"


def test_state_na_trims_whitespace():
    assert rr.decide_remark("x", AF_REST, "0", "Outside", j_value="  #N/A  ") == \
        "Invalid- Incorrect TID / TID Not Found"


# --- rule 2: "LM to Reattempt" trigger -> AF, then AG, else Valid -----------
def test_lm_trigger_af_named_verdict_wins():
    assert rr.decide_remark(P_TRIGGER_PINCODE_CORRECT, AF_A, "0", "Outside") == AF_A
    assert rr.decide_remark(P_TRIGGER_PINCODE_MISMATCH, AF_B, "HYBK", AG_INSIDE) == AF_B


def test_lm_trigger_defers_to_ag_inside_when_af_not_a_verdict():
    assert rr.decide_remark(P_TRIGGER_PINCODE_CORRECT, AF_REST, "0", AG_INSIDE) == AG_INSIDE
    assert rr.decide_remark(P_TRIGGER_PINCODE_MISMATCH, "#N/A", "0", AG_INSIDE) == AG_INSIDE


def test_lm_trigger_falls_back_to_valid():
    assert rr.decide_remark(P_TRIGGER_PINCODE_CORRECT, AF_REST, "0", "Outside") == "Valid"
    assert rr.decide_remark(P_TRIGGER_PINCODE_MISMATCH, AF_REST, "0", "Error — x") == "Valid"


def test_lm_trigger_matched_by_substring():
    # Any P value carrying the "LM to Reattempt" marker triggers the AF/AG recompute.
    assert rr.decide_remark("something LM to Reattempt something", AF_A, "0", "Outside") == AF_A


# --- rule 3: every other P value is kept verbatim ---------------------------
def test_non_trigger_values_are_kept_verbatim():
    for value in (
        "Not Valid - Delivered",
        "Not Valid - OOR",
        "Not Valid - In transit DS-DS",
        "Invalid - Already RTOed",
        "Not Valid - Doesn't belong to SWA",
        "Valid",
    ):
        # AF/AG/AD are irrelevant for a non-trigger P; the formula's value stands.
        assert rr.decide_remark(value, AF_A, "HYBK", AG_INSIDE, j_value="Delivered") == value


def test_non_trigger_value_is_trimmed():
    assert rr.decide_remark("  Valid  ", AF_REST, "0", "Outside", j_value="Delivered") == "Valid"


def test_na_state_beats_a_non_trigger_value():
    # Rule 1 (J == #N/A) is checked before keeping P verbatim.
    assert rr.decide_remark("Valid", AF_REST, "0", "Outside", j_value="#N/A") == \
        "Invalid- Incorrect TID / TID Not Found"


# --- worksheet application --------------------------------------------------
class _FakeCell:
    def __init__(self, value):
        self.value = value


class _FakeWS:
    def __init__(self, rows):
        # rows: list of dict {P, AD, AF, AG, optional J}; row index starts at 2
        self.max_row = len(rows) + 1
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
        {"P": "Not Valid - Delivered", "AD": "HYBK", "AF": AF_A, "AG": AG_INSIDE},          # kept verbatim
        {"P": P_TRIGGER_PINCODE_CORRECT, "AD": "0", "AF": AF_A, "AG": "Outside"},           # trigger -> AF
        {"P": P_TRIGGER_PINCODE_MISMATCH, "AD": "0", "AF": AF_REST, "AG": AG_INSIDE},       # trigger -> AG
        {"P": P_TRIGGER_PINCODE_CORRECT, "AD": "0", "AF": AF_REST, "AG": "Outside"},        # trigger -> Valid
        {"P": "x", "AD": "0", "AF": AF_REST, "AG": "Outside", "J": "#N/A"},                 # #N/A state
    ]
    ws = _FakeWS(rows)
    summary = rr.apply_to_worksheet(ws)
    assert summary["rows"] == 5
    assert ws.cell(2, rr.COL_P).value == "Not Valid - Delivered"
    assert ws.cell(3, rr.COL_P).value == AF_A
    assert ws.cell(4, rr.COL_P).value == AG_INSIDE
    assert ws.cell(5, rr.COL_P).value == "Valid"
    assert ws.cell(6, rr.COL_P).value == "Invalid- Incorrect TID / TID Not Found"
    # Row 2's formula value was kept verbatim (reported as "protected" for compat).
    assert summary["protected"] == 1


def test_apply_to_worksheet_keeps_formula_values():
    rows = [
        {"P": "Not Valid - OOR", "AD": "0", "AF": AF_REST, "AG": "Outside", "J": "In Transit (DS -> Customer)"},
        {"P": "Invalid - Already RTOed", "AD": "HYBK", "AF": AF_A, "AG": AG_INSIDE, "J": "Delivered"},
    ]
    ws = _FakeWS(rows)
    rr.apply_to_worksheet(ws)
    # Both are non-trigger formula outputs -> kept exactly as the formula produced them.
    assert ws.cell(2, rr.COL_P).value == "Not Valid - OOR"
    assert ws.cell(3, rr.COL_P).value == "Invalid - Already RTOed"
