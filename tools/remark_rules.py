r"""Post-recalculation Remark (Working Sheet column P) rules.

The template's column P (P2 formula, translated down) now computes the final Remark
for MOST rows on its own during Excel recalculation. After recalc, this module only
has to finish the rows the formula deliberately leaves for the automation:

Column map in "Working Sheet" (verified against the template, 1-indexed):
  P  = 16  Remark            (input: recalculated formula value; output: literal)
  J  = 10  State (SCC package state)
  AF = 32  Station Transfer Validation
  AG = 33  GCRS Agent Space (polygon status)

Decision sequence (applied per row, in order):

  1. If J (State) == "#N/A"  ->  P = "Invalid- Incorrect TID / TID Not Found".
  2. Else if the recalculated P contains "LM to Reattempt" (i.e. the formula produced
     one of the two address/pin-code "LM to Reattempt" trigger values), recompute P:
       a. If AF (Station Transfer Validation) is one of the two transfer verdicts
          -> P = that AF text.
       b. Else if AG (GCRS Agent Space) == "Invalid - It is Inside Jurisdiction as per
          GCRS Tool" -> P = that AG text.
       c. Else -> P = "Valid".
  3. Otherwise -> leave P exactly as the formula computed it (Not Valid - Delivered,
     Not Valid - OOR, Not Valid - In transit DS-DS, Invalid - Already RTOed,
     Not Valid - Doesn't belong to SWA, Valid, etc.).

Only rows matching rule 1 or rule 2 are rewritten; every other P value the formula
produced is kept verbatim.
"""
from __future__ import annotations

# --- column positions (1-indexed) in "Working Sheet" -----------------------
COL_P = 16
COL_J = 10
COL_AD = 30
COL_AF = 32
COL_AG = 33
SHEET_WORK = "Working Sheet"

VALID = "Valid"

# J (State) driven outcome.
STATE_NA = "#N/A"
REMARK_INCORRECT_TID = "Invalid- Incorrect TID / TID Not Found"

# The P2 formula emits two "LM to reattempt" address/pin-code values as a signal for the
# automation to finish the decision from AF/AG. The two template strings differ in case
# ("LM to Reattempt" vs "LM to reattempt"), so the marker is matched case-INSENSITIVELY
# on the shared substring; this also tolerates small wording/spacing drift.
LM_TO_REATTEMPT_MARKER = "lm to reattempt"

# AF (Station Transfer Validation) verdicts that are copied straight into P.
AF_TRANSFER_VERDICTS = frozenset(
    {
        "Invalid - Correct Station-A.in Shipment delivered - LM to Reattempt and Deliver",
        "Invalid - LM to Reattempt - Sent to correct Station",
    }
)

# The single AG (GCRS Agent Space) value that is copied straight into P.
AG_INSIDE_JURISDICTION = "Invalid - It is Inside Jurisdiction as per GCRS Tool"


def _clean(value) -> str:
    """Normalise a recalculated cell value to a trimmed string for comparison."""
    if value is None:
        return ""
    return str(value).strip()


def is_protected(p_value) -> bool:
    """Kept for backward compatibility with apply_remarks.py's call site.

    Under the new rules there is no separate protected-value shortcut: the formula's
    output is authoritative except for the two cases handled in ``decide_remark``. The
    row-level decision in ``decide_remark`` keeps every non-trigger P value verbatim, so
    this always returns False (nothing is pre-skipped as "protected").
    """
    return False


def _is_lm_to_reattempt(p_value) -> bool:
    return LM_TO_REATTEMPT_MARKER in _clean(p_value).lower()


def decide_remark(p_value, af_value, ad_value, ag_value, j_value=None) -> str:
    """Return the final P (Remark) value for one row.

    ``p_value`` is the recalculated current Remark (what the P2 formula produced);
    ``j_value`` is the State (column J); ``af_value``/``ag_value`` are the recalculated
    AF/AG values. ``ad_value`` is accepted for signature compatibility but no longer used.
    Pure / side-effect free.
    """
    # 1. #N/A state means the TID was not found.
    if _clean(j_value) == STATE_NA:
        return REMARK_INCORRECT_TID

    # 2. The two address/pin-code "LM to Reattempt" triggers -> finish from AF, then AG.
    if _is_lm_to_reattempt(p_value):
        af = _clean(af_value)
        if af in AF_TRANSFER_VERDICTS:
            return af
        if _clean(ag_value) == AG_INSIDE_JURISDICTION:
            return AG_INSIDE_JURISDICTION
        return VALID

    # 3. Every other row: keep exactly what the formula computed.
    return _clean(p_value)


def apply_to_worksheet(ws, first_row: int = 2, last_row: int | None = None) -> dict:
    """Rewrite column P to its final literal value for every data row.

    ``ws`` must be a *recalculated* worksheet loaded with ``data_only=True`` so J/AF/AG
    hold computed values. Writing a literal replaces P's formula, which is intended: the
    Remark becomes a fixed value per the rules above.

    Returns a small summary: rows processed, how many were kept as the formula's own
    value (reported under "protected" for backward compatibility), and a tally of the
    values written.
    """
    if last_row is None:
        last_row = ws.max_row
    from collections import Counter

    written = Counter()
    kept_verbatim = 0
    processed = 0
    for row in range(first_row, last_row + 1):
        processed += 1
        p_value = ws.cell(row=row, column=COL_P).value
        result = decide_remark(
            p_value,
            ws.cell(row=row, column=COL_AF).value,
            ws.cell(row=row, column=COL_AD).value,
            ws.cell(row=row, column=COL_AG).value,
            ws.cell(row=row, column=COL_J).value,
        )
        if result == _clean(p_value):
            kept_verbatim += 1
        ws.cell(row=row, column=COL_P).value = result
        written[result] += 1
    return {"rows": processed, "protected": kept_verbatim, "written": dict(written)}
