r"""Post-recalculation Remark (Working Sheet column P) rules.

The template's column P starts life as a formula. After Excel recalculates the
workbook, this module rewrites P to a final literal value per the operator's
manual-invalid handling process, driven by columns AF, AD and AG.

Column map in "Working Sheet" (verified against the template, 1-indexed):
  P  = 16  Remark            (input: recalculated formula value; output: literal)
  J  = 10  State             (SCC package state)
  AD = 30  Old shipment delivered by Station   (station code | 0 | #N/A)
  AF = 32  Station Transfer Validation         (max 4 distinct values)
  AG = 33  GCRS Agent Space                    (polygon status)

Decision sequence (applied per row, in order — never skipped, never revisited):

  1. If P already holds a PROTECTED value -> leave P unchanged.
  2. Else if J (State) == "#N/A" -> P = "Invalid- Incorrect TID / TID Not Found".
  3. Else if J (State) == "In Transit (DS -> Customer)" -> P = "Invalid - OOR".
  4. Else if AF is one of the two "LM to Reattempt" transfer verdicts
     -> P = that AF text.
  5. Else (AF is anything else, e.g. "Interchange Not Possible / Other Group" or
     "#N/A") inspect AD:
       a. AD is a 4-character alphanumeric station code -> P = "Valid".
       b. AD is anything else (0, #N/A, blank, ...) inspect AG:
            - AG == "Invalid - It is Inside Jurisdiction as per GCRS Tool"
              -> P = that AG text.
            - otherwise -> P = "Valid".

The two J-driven outcomes are themselves PROTECTED values: once written they are
never overwritten (there are six protected values in total).
"""
from __future__ import annotations

import re

# --- column positions (1-indexed) in "Working Sheet" -----------------------
COL_P = 16
COL_J = 10
COL_AD = 30
COL_AF = 32
COL_AG = 33
SHEET_WORK = "Working Sheet"

VALID = "Valid"

# J (State) driven outcomes.
STATE_NA = "#N/A"
STATE_IN_TRANSIT_CUSTOMER = "In Transit (DS -> Customer)"
REMARK_INCORRECT_TID = "Invalid- Incorrect TID / TID Not Found"
REMARK_OOR = "Invalid - OOR"

# P values that must never be overwritten. The two J-driven outcomes are protected too,
# so there are six protected values in total.
PROTECTED_REMARKS = frozenset(
    {
        "Invalid - Already RTOed",
        "Not Valid - Doesn't belong to SWA",
        "Not Valid - Delivered",
        "Not Valid - In transit DS-DS",
        REMARK_INCORRECT_TID,
        REMARK_OOR,
    }
)

# AF verdicts that are copied straight into P.
AF_TRANSFER_VERDICTS = frozenset(
    {
        "Invalid - Correct Station-A.in Shipment delivered - LM to Reattempt and Deliver",
        "Invalid - LM to Reattempt - Sent to correct Station",
    }
)

# The single AG value that is copied straight into P.
AG_INSIDE_JURISDICTION = "Invalid - It is Inside Jurisdiction as per GCRS Tool"

# A station code is exactly four alphanumeric characters (e.g. HYBK, STVT, 4-digit codes).
_STATION_CODE = re.compile(r"^[A-Za-z0-9]{4}$")


def _clean(value) -> str:
    """Normalise a recalculated cell value to a trimmed string for comparison."""
    if value is None:
        return ""
    return str(value).strip()


def is_protected(p_value) -> bool:
    return _clean(p_value) in PROTECTED_REMARKS


def is_station_code(ad_value) -> bool:
    """True only for a 4-character alphanumeric station code. 0, #N/A, blank -> False."""
    text = _clean(ad_value)
    if not _STATION_CODE.match(text):
        return False
    # A purely numeric 4-char value that is really "0000"-style is still alphanumeric,
    # but the sentinel non-codes seen in data are "0" and "#N/A", which fail the regex
    # (length != 4 / contains '#'). Guard the explicit sentinels for clarity.
    if text in {"#N/A", "#N/A "}:
        return False
    return True


def decide_remark(p_value, af_value, ad_value, ag_value, j_value=None) -> str:
    """Return the final P (Remark) value for one row.

    ``p_value`` is the recalculated current Remark; ``j_value`` is the State (column J);
    the AF/AD/AG values are the recalculated values of those columns. Pure/side-effect free.
    """
    # 1. Protected remarks are never changed.
    if is_protected(p_value):
        return _clean(p_value)

    # 2/3. State-driven outcomes (themselves protected values).
    state = _clean(j_value)
    if state == STATE_NA:
        return REMARK_INCORRECT_TID
    if state == STATE_IN_TRANSIT_CUSTOMER:
        return REMARK_OOR

    # 4. Named AF transfer verdicts win outright.
    af = _clean(af_value)
    if af in AF_TRANSFER_VERDICTS:
        return af

    # 5a. A real 4-char station code in AD -> Valid.
    if is_station_code(ad_value):
        return VALID

    # 5b. Otherwise defer to AG.
    if _clean(ag_value) == AG_INSIDE_JURISDICTION:
        return AG_INSIDE_JURISDICTION
    return VALID


def apply_to_worksheet(ws, first_row: int = 2, last_row: int | None = None) -> dict:
    """Rewrite column P to its final literal value for every data row.

    ``ws`` must be a *recalculated* worksheet loaded with ``data_only=True`` so AF/AD/AG
    hold computed values. Writing a literal replaces P's formula, which is intended:
    the Remark becomes a fixed value per the rules above.

    Returns a small summary: rows processed, how many were protected (left as-is), and
    a tally of the values written.
    """
    if last_row is None:
        last_row = ws.max_row
    from collections import Counter

    written = Counter()
    protected = 0
    processed = 0
    for row in range(first_row, last_row + 1):
        processed += 1
        p_value = ws.cell(row=row, column=COL_P).value
        if is_protected(p_value):
            protected += 1
            written[_clean(p_value)] += 1
            continue
        result = decide_remark(
            p_value,
            ws.cell(row=row, column=COL_AF).value,
            ws.cell(row=row, column=COL_AD).value,
            ws.cell(row=row, column=COL_AG).value,
            ws.cell(row=row, column=COL_J).value,
        )
        ws.cell(row=row, column=COL_P).value = result
        written[result] += 1
    return {"rows": processed, "protected": protected, "written": dict(written)}
