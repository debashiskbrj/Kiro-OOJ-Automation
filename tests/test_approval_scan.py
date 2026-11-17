"""Unit tests for the SIM-T `approval_in_comments` scan (pure functions, no MCP/network).

Covers tickets_pull.scan_approval (comment-text scan + label mapping/order), the
explode() carry-through of a ticket's approval value across all its exploded TID rows,
and a write_outputs round-trip proving the 12th column lands in both xlsx and csv.
"""
import csv
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))

import openpyxl  # noqa: E402

import tickets_pull as tp  # noqa: E402


# --- schema -------------------------------------------------------------------
def test_columns_has_approval_as_last_of_twelve():
    assert tp.COLUMNS[-1] == "approval_in_comments"
    assert len(tp.COLUMNS) == 12


# --- scan_approval ------------------------------------------------------------
def test_site_clear_only():
    assert tp.scan_approval([{"message": "Station confirms Site Clear for this TID"}]) == "Site clear"


def test_approved_only():
    assert tp.scan_approval([{"message": "This has been approved by ops"}]) == "Approved"


def test_both_join_order_site_then_approved():
    # "Site Clear ... approved" -> both labels, first-seen order, distinct.
    assert tp.scan_approval([{"message": "Site Clear and then approved"}]) == "Site clear, Approved"


def test_distinct_labels_collapse_duplicates():
    comments = [{"message": "approved"}, {"message": "APPROVED again"}, {"message": "site clear"}]
    assert tp.scan_approval(comments) == "Approved, Site clear"


def test_body_and_text_fallbacks():
    assert tp.scan_approval([{"body": "APPROVED"}]) == "Approved"
    assert tp.scan_approval([{"text": "site  clear"}]) == "Site clear"


def test_no_match_and_empty_inputs_blank():
    assert tp.scan_approval([{"message": "nothing relevant here"}]) == ""
    assert tp.scan_approval([]) == ""
    assert tp.scan_approval(None) == ""


# --- accuracy: broad grant vocabulary (2026-11) -------------------------------
# Regression for real ticket D572189109, whose leader approval was written as
# "please go ahead with marking undel" and was MISSED by the old approved|site clear rule.
def test_real_ticket_D572189109_leader_go_ahead_is_approved():
    leader = {"message": ("Though these might be falling in station's jurisdiction, addresses "
                          "mentioned are generic which isn't allowing the station to assign these "
                          "to the correct DAs. Hence, please go ahead with marking undel on these.")}
    assert tp.scan_approval([leader]) == "Approved"


def test_real_ticket_D572189109_whole_thread_is_approved():
    thread = [
        {"message": "Hence, please go ahead with marking undel on these."},
        {"message": "TID: 374902870192 | Invalid - It is Inside Jurisdiction as per GCRS Tool"},
        {"message": "@dabdeepa\n\nthanks for the approval in the comment.\ntoday EOD we will mark valid on the basis of your approval.\n\nthanks"},
        {"message": "https://t.corp.amazon.com/D567656535/overview    Refer the TT for approval in case.  @kabideba  to look into it. "},
        {"message": " @aasinghp @vddeepak  request intervention here please. "},
        {"message": "@dasasour required undelivered scan asap.please do not resolve the TT without that. "},
    ]
    assert tp.scan_approval(thread) == "Approved"


def test_own_comment_counts_as_approval():
    # Any person's comment counts, including the operator's own acknowledgement.
    own = {"message": "thanks for the approval in the comment. we will mark valid on the basis of your approval."}
    assert tp.scan_approval([own]) == "Approved"


def test_grant_phrases_are_approved():
    for text in (
        "Approved by leader",
        "Approval given, go ahead",
        "Please proceed with undel",
        "ok to mark undel",
        "You may mark these valid",
        "Agreed, mark it",
        "Confirmed, green light",
    ):
        assert tp.scan_approval([{"message": text}]) == "Approved", text


def test_site_clear_variants():
    assert tp.scan_approval([{"message": "site clearance received"}]) == "Site clear"
    assert tp.scan_approval([{"message": "Site cleared by station"}]) == "Site clear"


# --- accuracy: refusals, pending states, requests and questions are NOT approvals ---
def test_negated_and_pending_are_not_approved():
    for text in (
        "Not approved",
        "This is not approved yet",
        "Awaiting approval",
        "Pending approval from leader",
        "Need approval before marking",
        "Requesting approval",
        "waiting for approval",
        "Cannot approve this",
        "Do not proceed",
        "Refer the TT for approval in case.",
    ):
        assert tp.scan_approval([{"message": text}]) == "", text


def test_disapproved_and_unapproved_are_not_approved():
    for text in ("Disapproved", "This is unapproved", "Request disapproved by leader"):
        assert tp.scan_approval([{"message": text}]) == "", text


def test_pending_after_grant_word_is_not_approved():
    for text in (
        "approval pending",
        "Approval: pending",
        "Approval required from leader",
        "approval not given",
        "Approval denied",
        "Approval rejected by station",
    ):
        assert tp.scan_approval([{"message": text}]) == "", text
    # A grant followed by unrelated words still counts.
    assert tp.scan_approval([{"message": "Approved for undel"}]) == "Approved"


def test_sl_crm_agreement_to_continue_is_approved():
    for text in (
        "approved",
        "go ahead",
        "carry on with undel",
        "No objection, please proceed",
        "Thanks for sharing, approved",
        "fine to mark undel",
    ):
        assert tp.scan_approval([{"message": text}]) == "Approved", text


def test_questions_are_not_approved():
    assert tp.scan_approval([{"message": "Is this approved?"}]) == ""
    assert tp.scan_approval([{"message": "Can we proceed?"}]) == ""


def test_positive_sentence_wins_over_negative_sentence_in_same_comment():
    # Sentence-level: an earlier pending sentence does not cancel a later genuine grant.
    assert tp.scan_approval([{"message": "Was awaiting approval. Now approved."}]) == "Approved"


# --- CRM / Site Lead filter (operator rule) -----------------------------------
def test_approver_titles_verified_on_phonetool():
    for title in ("CRM", "CRM - EDSP", "Channel Relationship Manager", "Site Lead"):
        assert tp.is_approver_title(title), title
    for title in ("Associate", "Station Manager", "Program Manager", "", None):
        assert not tp.is_approver_title(title), title


def _c(alias, message):
    return {"author": {"namespace": "MIDWAY", "value": alias}, "message": message}


def test_only_crm_or_site_lead_comments_count():
    titles = {"vddeepak": "CRM - EDSP", "pkaushal": "Site Lead", "kabideba": "Associate"}
    check = lambda alias: tp.is_approver_title(titles.get(alias))
    assert tp.scan_approval([_c("vddeepak", "please go ahead with marking undel")], check) == "Approved"
    assert tp.scan_approval([_c("pkaushal", "Site clear")], check) == "Site clear"
    # Anyone else is NOT ignored: flagged for a manual check with their alias.
    assert (tp.scan_approval([_c("kabideba", "thanks for the approval, will mark valid")], check)
            == "Need check manually (kabideba)")
    assert tp.scan_approval([{"message": "approved"}], check) == "Need check manually (unknown)"
    assert (tp.scan_approval([_c("abc", "approved"), _c("xyz", "go ahead"), _c("abc", "ok to mark")], check)
            == "Need check manually (abc, xyz)")
    # A CRM / Site Lead approval wins over other people's approvals on the same ticket.
    assert tp.scan_approval([_c("kabideba", "approved"), _c("vddeepak", "go ahead")], check) == "Approved"
    # No approval wording at all -> blank.
    assert tp.scan_approval([_c("kabideba", "Noted")], check) == ""


class _FakeMcp:
    def __init__(self, comments, titles):
        self.comments, self.titles, self.lookups = comments, titles, []

    def call_tool(self, name, arguments, timeout=300):
        if name == "TicketingReadActions":
            return {"data": {"comments": self.comments}}
        alias = arguments["inputs"][0].rsplit("/", 1)[-1]
        self.lookups.append(alias)
        if alias not in self.titles:
            raise RuntimeError("lookup failed")
        return {"content": {"status": "success", "content": {"job_title": self.titles[alias]}}}


def test_scan_approvals_looks_up_each_author_once_and_skips_failures():
    comments = [_c("vddeepak", "go ahead"), _c("vddeepak", "approved"),
                _c("ghost", "approved"), _c("kabideba", "Noted")]
    mcp = _FakeMcp(comments, {"vddeepak": "CRM - EDSP"})
    logs = []
    result = tp.scan_approvals(mcp, [{"id": "T1"}, {"id": "T2"}], logs.append)
    assert result == {"T1": "Approved", "T2": "Approved"}
    # Failed lookup with no CRM approval on the ticket -> manual check.
    only_ghost = _FakeMcp([_c("ghost", "approved")], {})
    assert tp.scan_approvals(only_ghost, [{"id": "T3"}], lambda *_: None) == {
        "T3": "Need check manually (ghost)"}
    # One lookup per person; "Noted" has no approval so kabideba is never looked up.
    assert sorted(mcp.lookups) == ["ghost", "vddeepak"]
    assert any("1 lookup(s) failed" in line for line in logs)


# --- explode carry-through ----------------------------------------------------
def test_explode_spreads_approval_across_all_tid_rows():
    rows = tp.explode(
        [{"id": "V1", "description": "TID 123456789012 and 123456789013", "aliases": [{"id": "A1"}]}],
        {"V1": "Approved"},
    )
    assert len(rows) == 2
    assert all(r["approval_in_comments"] == "Approved" for r in rows)


def test_explode_defaults_blank_when_ticket_missing_from_approvals():
    rows = tp.explode([{"id": "V2", "description": "no tids"}], None)
    assert rows[0]["approval_in_comments"] == ""
    rows2 = tp.explode([{"id": "V3", "description": "no tids"}], {"OTHER": "Approved"})
    assert rows2[0]["approval_in_comments"] == ""


# --- write_outputs round-trip -------------------------------------------------
def test_write_outputs_round_trip_twelve_columns(tmp_path, monkeypatch):
    monkeypatch.setattr(tp, "OUT", tmp_path)
    rows = tp.explode(
        [{"id": "V1", "description": "TID 123456789012", "aliases": [{"id": "A1"}]}],
        {"V1": "Approved, Site clear"},
    )
    xlsx, csvp = tp.write_outputs(rows, "testtmp")

    wb = openpyxl.load_workbook(xlsx)
    try:
        header = [c.value for c in wb.active[1]]
    finally:
        wb.close()
    assert len(header) == 12
    assert header[-1] == "approval_in_comments"

    with open(csvp, encoding="utf-8", newline="") as fh:
        read = list(csv.DictReader(fh))
    assert read[0]["approval_in_comments"] == "Approved, Site clear"


# --- run_daily Phase A -> B round trip ----------------------------------------
def test_run_daily_tech_data_csv_round_trips_approval(tmp_path):
    """tech_data.csv must carry approval_in_comments as a plain string that
    load_tech_data() reads back verbatim, while createdAt still reparses to datetime."""
    import datetime as _dt

    import run_daily as rd  # noqa: E402

    rows = tp.explode(
        [{"id": "V1", "description": "TID 123456789012", "aliases": [{"id": "A1"}]}],
        {"V1": "Site clear"},
    )
    now = _dt.datetime(2026, 1, 2, 3, 4, 5)
    # Mirror pull_tickets' CSV shape: createdAt/lastUpdatedAt stringified for the writer.
    csv_rows = [{**r, "createdAt": str(now), "lastUpdatedAt": str(now)} for r in rows]
    path = tmp_path / "tech_data.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(csv_rows[0].keys()))
        writer.writeheader()
        writer.writerows(csv_rows)

    loaded = rd.load_tech_data(path)
    assert loaded[0]["approval_in_comments"] == "Site clear"
    assert isinstance(loaded[0]["createdAt"], _dt.datetime)
