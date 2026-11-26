"""Bulk resolve (web section "5. Resolve ticket"). Uses a FAKE ticket API only; no real
ticket is ever read or changed by these tests."""
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "web"))

import openpyxl  # noqa: E402
import pytest  # noqa: E402

import resolve_tickets_runner as rtr  # noqa: E402
import resolve_controller as rsc  # noqa: E402


@pytest.fixture(autouse=True)
def _no_retry_wait(monkeypatch):
    monkeypatch.setattr(rtr, "RETRY_DELAYS", (0, 0, 0))
    monkeypatch.setattr(rtr, "GATEWAY_RETRY_DELAYS", (0, 0, 0, 0))
    monkeypatch.setattr(rtr, "VERIFY_WAIT", 0)
    monkeypatch.setattr(rtr, "VERIFY_RETRY_DELAYS", (0, 0))


GATEWAY_403 = ("get-ticket returned {'status': 'error', 'error': 'Received a non-API HTML "
               "response from the ticketing endpoint: \"403 Forbidden\". The request was likely "
               "blocked by an edge/gateway filter'}")


def test_gateway_403_gets_longer_backoff_then_succeeds(monkeypatch):
    sleeps, calls = [], []
    monkeypatch.setattr(rtr.time, "sleep", sleeps.append)
    monkeypatch.setattr(rtr, "GATEWAY_RETRY_DELAYS", (15, 30, 60, 90))

    def flaky():
        calls.append(1)
        if len(calls) < 4:
            raise RuntimeError(GATEWAY_403)
        return "Assigned"
    assert rtr._with_retries(flaky) == "Assigned"
    assert sleeps == [15, 30, 60]


def test_gateway_403_gives_up_after_all_long_retries(monkeypatch):
    sleeps = []
    monkeypatch.setattr(rtr.time, "sleep", sleeps.append)
    monkeypatch.setattr(rtr, "GATEWAY_RETRY_DELAYS", (15, 30, 60, 90))
    with pytest.raises(RuntimeError, match="403 Forbidden"):
        rtr._with_retries(lambda: (_ for _ in ()).throw(RuntimeError(GATEWAY_403)))
    assert sleeps == [15, 30, 60, 90]


def test_other_errors_keep_the_short_retries(monkeypatch):
    sleeps = []
    monkeypatch.setattr(rtr.time, "sleep", sleeps.append)
    monkeypatch.setattr(rtr, "RETRY_DELAYS", (2, 5, 10))
    with pytest.raises(RuntimeError, match="timeout"):
        rtr._with_retries(lambda: (_ for _ in ()).throw(RuntimeError("timeout")))
    assert sleeps == [2, 5, 10]


def _results(path):
    with open(path / "results.csv", encoding="utf-8", newline="") as fh:
        return {r["Ticket ID"]: r for r in csv.DictReader(fh)}


# --- parsing --------------------------------------------------------------------
def test_parse_urls_ids_duplicates_and_invalid():
    text = "\n".join([
        "https://t.corp.amazon.com/D572189109",
        "https://t.corp.amazon.com/D572189109/overview",       # duplicate (suffix)
        "https://t.corp.amazon.com/issues/d572189110",         # /issues/ + lowercase
        "D572189109",                                          # duplicate (bare)
        "  ",
        "D572189111, https://t.corp.amazon.com/D572189112",    # two on one line
        "hello",                                               # invalid
        "https://sim.amazon.com/issues/ABC-1",                 # not SIM-T -> invalid
        "97791220-9fc1-4c03-8bb5-92c4b0e67e34",                # ticket UUID
    ])
    parsed = rtr.parse_ticket_input(text)
    assert parsed["ids"] == ["D572189109", "D572189110", "D572189111", "D572189112",
                             "97791220-9fc1-4c03-8bb5-92c4b0e67e34"]
    assert parsed["duplicates"] == 2
    assert parsed["invalid"] == ["hello", "https://sim.amazon.com/issues/ABC-1"]
    assert parsed["pieces"] == 9


def test_parse_2000_plus_urls_with_duplicates():
    urls = [f"https://t.corp.amazon.com/D{570000000 + i}" for i in range(2200)]
    parsed = rtr.parse_ticket_input("\n".join(urls + urls[:300]))
    assert len(parsed["ids"]) == 2200
    assert parsed["duplicates"] == 300
    assert parsed["invalid"] == []


# --- one ticket -----------------------------------------------------------------
class FakeMcp:
    """Fake SIM-T. ``stale_reads``: after a resolve, this many reads of that ticket still
    return the OLD status (the delay seen live on 2026-11-16)."""

    def __init__(self, statuses, fail_reads=(), ignore_update=(), stale_reads=0):
        self.statuses = dict(statuses)
        self.fail_reads = set(fail_reads)
        self.ignore_update = set(ignore_update)
        self.stale_reads = stale_reads
        self.lag: dict[str, int] = {}
        self.updates = []

    def call_tool(self, name, arguments, timeout=300):
        if name == "TicketingReadActions":
            tid = arguments["input"]["ticketId"]
            if tid in self.fail_reads or tid not in self.statuses:
                raise RuntimeError("ticket not found")
            if self.lag.get(tid, 0) > 0:
                self.lag[tid] -= 1
                return {"status": "success", "data": {"ticket": {"status": "Assigned"}}}
            return {"status": "success", "data": {"ticket": {"status": self.statuses[tid]}}}
        assert name == "TicketingWriteActions"
        assert arguments["action"] == "update-ticket"
        assert arguments["status"] == "Resolved" and arguments["closureCode"] == "Successful"
        assert set(arguments) == {"action", "ticketId", "status", "closureCode"}  # nothing else
        self.updates.append(arguments["ticketId"])
        if arguments["ticketId"] not in self.ignore_update:
            self.statuses[arguments["ticketId"]] = "Resolved"
            self.lag[arguments["ticketId"]] = self.stale_reads
        return {"status": "success"}

    def close(self):
        pass


def test_resolve_step_sends_or_skips_without_waiting():
    mcp = FakeMcp({"D1": "Assigned", "D2": "Resolved", "D3": "Closed"})
    assert rtr.resolve_step(mcp, "D1")["sent"] is True
    assert rtr.resolve_step(mcp, "D2")["Result"] == "Already resolved"
    assert rtr.resolve_step(mcp, "D3")["Result"] == "Already resolved"
    assert mcp.updates == ["D1"]
    row = rtr.resolve_step(FakeMcp({}), "D9")
    assert row["Result"] == "Failed" and "not found" in row["Detail"]


def test_simt_delay_is_still_reported_resolved(tmp_path):
    """Live case: right after the resolve SIM-T still says Assigned (first verify round),
    later rounds see Resolved -> reported Resolved, not Failed."""
    mcp = FakeMcp({"D585273548": "Assigned", "D2": "Assigned"}, stale_reads=2)
    result = rtr.run(["D585273548", "D2"], tmp_path, workers=2, mcp_factory=lambda: mcp)
    rows = _results(tmp_path)
    assert result["counts"] == {"Resolved": 2, "Already resolved": 0, "Failed": 0}
    assert rows["D585273548"]["Status after"] == "Resolved"
    assert sorted(mcp.updates) == ["D2", "D585273548"]     # each written exactly once


def test_status_never_updated_is_failed_with_recheck_hint(tmp_path):
    mcp = FakeMcp({"D1": "Assigned"}, ignore_update={"D1"})
    rtr.run(["D1"], tmp_path, workers=1, mcp_factory=lambda: mcp)
    row = _results(tmp_path)["D1"]
    assert row["Result"] == "Failed" and "accepted" in row["Detail"] and "again" in row["Detail"]
    assert mcp.updates == ["D1"]                            # no second write


# --- whole run ------------------------------------------------------------------
def test_run_parallel_records_every_ticket(tmp_path):
    statuses = {f"D{i}": ("Resolved" if i % 3 == 0 else "Assigned") for i in range(30)}
    shared = FakeMcp(statuses)
    result = rtr.run(list(statuses), tmp_path, workers=4, mcp_factory=lambda: shared)
    with open(tmp_path / "results.csv", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 30 and {r["Ticket ID"] for r in rows} == set(statuses)
    assert result["counts"] == {"Resolved": 20, "Already resolved": 10, "Failed": 0}
    progress = json.loads((tmp_path / "progress.json").read_text(encoding="utf-8"))
    assert progress["finished"] is True and progress["done"] == 30


def test_internal_error_on_one_ticket_does_not_drop_the_rest(tmp_path, monkeypatch):
    """21:33 run on 2026-11-16: a crash inside a worker killed it and its tickets vanished
    (75 of 79 rows). Now a raising ticket is reported and the worker keeps going."""
    real_step = rtr.resolve_step

    def flaky(mcp, ticket):
        if ticket == "D3":
            raise NameError("boom")
        return real_step(mcp, ticket)

    monkeypatch.setattr(rtr, "resolve_step", flaky)
    statuses = {f"D{i}": "Assigned" for i in range(8)}
    shared = FakeMcp(statuses)
    result = rtr.run(list(statuses), tmp_path, workers=1, mcp_factory=lambda: shared)
    rows = _results(tmp_path)
    assert set(rows) == set(statuses)
    assert rows["D3"]["Result"] == "Failed" and "run.log" in rows["D3"]["Detail"]
    assert result["counts"] == {"Resolved": 7, "Already resolved": 0, "Failed": 1}


def test_run_without_builder_mcp_fails_cleanly_and_changes_nothing(tmp_path):
    def broken():
        raise RuntimeError("no midway")
    result = rtr.run(["D1", "D2"], tmp_path, workers=2, mcp_factory=broken)
    assert result["counts"]["Failed"] == 2


# --- controller -----------------------------------------------------------------
def test_controller_requires_check_and_reports_stopped_tickets(tmp_path, monkeypatch):
    monkeypatch.setattr(rsc, "RT_ROOT", tmp_path)
    c = rsc.ResolveController()
    assert c.start()["started"] is False                    # no Check list yet
    assert c.check("nothing valid")["ok"] is False
    checked = c.check("https://t.corp.amazon.com/D1000001\nD1000001\nD1000002\nbad")
    assert checked["ok"] and checked["unique"] == 2 and checked["duplicates"] == 1
    assert checked["invalid_count"] == 1

    # Simulate a stopped run: 1 of 3 tickets processed.
    out = tmp_path / "20260101_000000"
    out.mkdir()
    (out / "ids.txt").write_text("D1000001\nD1000002\nD1000003", encoding="utf-8")
    (out / "input_check.json").write_text(json.dumps(
        {"unique": 3, "pieces": 5, "duplicates": 1, "invalid": ["bad"]}), encoding="utf-8")
    with open(out / "results.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=rtr.RESULT_FIELDS)
        writer.writeheader()
        writer.writerow({"Ticket ID": "D1000001", "Ticket URL": "u", "Status before": "Assigned",
                         "Result": "Resolved", "Status after": "Resolved", "Detail": "", "Time": "t"})
    # D1000002: resolve sent in phase 1, run stopped before phase 2 verified it.
    with open(out / "sent.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=rtr.SENT_FIELDS)
        writer.writeheader()
        writer.writerow({"Ticket ID": "D1000001", "Status before": "Assigned", "Time": "t"})
        writer.writerow({"Ticket ID": "D1000002", "Status before": "Assigned", "Time": "t"})
    c._out_dir = out
    c._stopped = True
    status = c.get_status()
    assert status["done"] == 1 and status["total"] == 3 and status["report_ready"] is True

    report = c.report_path()
    wb = openpyxl.load_workbook(report)
    try:
        results = [[cell.value for cell in row] for row in wb["Results"].iter_rows(min_row=2)]
        assert [r[4] for r in results] == ["Resolved", "Resolve sent, not verified (stopped)",
                                           "Not processed (stopped)"]
        assert wb["Invalid input"]["A2"].value == "bad"
        summary = {row[0].value: row[1].value for row in wb["Summary"].iter_rows() if row[0].value}
        assert summary["Duplicates removed"] == 1 and summary["Unique tickets"] == 3
        assert summary["Resolve sent, not verified (stopped)"] == 1
    finally:
        wb.close()
