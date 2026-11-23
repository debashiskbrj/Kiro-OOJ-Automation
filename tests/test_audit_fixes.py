"""Regression tests for every fix from the full-project audit (items 1.2 to 1.17; 1.1,
1.18 and 1.19 were fixed earlier and are covered by other test files)."""
import csv
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "web"))
sys.path.insert(0, str(ROOT / "tools"))

import run_controller as rc  # noqa: E402
import run_daily as rd  # noqa: E402
import sanitize_captures as sc  # noqa: E402


# ---------------------------------------------------------------- 1.3
def test_server_start_keeps_operators_scc_export(tmp_path):
    captures, downloads = tmp_path / "captures", tmp_path / "Downloads"
    downloads.mkdir()
    scc_export = downloads / "SearchResults.csv"
    scc_export.write_text("Tracking ID,State\n1,Delivered\n", encoding="utf-8")
    scc_export2 = downloads / "SearchResults (1).csv"
    scc_export2.write_text("x", encoding="utf-8")
    pnama_leftover = downloads / "SWA_repeat_Prox_Pnam_1.csv"
    pnama_leftover.write_text("x", encoding="utf-8")
    with patch.object(sc, "CAPTURES", captures), patch.object(sc, "DOWNLOADS", downloads):
        sc.sanitize(log=lambda _: None)
    assert scc_export.exists() and scc_export2.exists()
    assert not pnama_leftover.exists()  # real automation leftovers are still cleaned


# ---------------------------------------------------------------- 1.5
def _controller_with_finished_phase_a(tmp_path, exit_code: int, write_tech: bool):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    if write_tech:
        (run_dir / "tech_data.csv").write_text("Sl\n1\n", encoding="utf-8")
    c = rc.RunController()
    c._ever_started = True
    c._run_dir = run_dir
    c._stamp = run_dir.name
    c._phase = "awaiting_scc_upload"
    c._proc = subprocess.Popen([sys.executable, "-c", f"import sys; sys.exit({exit_code})"])
    c._proc.wait(timeout=30)
    return c


def test_failed_simt_step_is_not_shown_as_paused(tmp_path):
    c = _controller_with_finished_phase_a(tmp_path, exit_code=2, write_tech=False)
    status = c.get_status()
    assert status["scc_phase"] == "phase_a_failed"
    assert "exit code 2" in status["start_error"]
    # The SCC upload is refused: there is nothing to resume.
    assert c.upload_scc_csv(b"Tracking ID,State,Destination,Source,Ship Option\n", "s.csv")["uploaded"] is False


def test_successful_simt_step_still_pauses_for_scc(tmp_path):
    c = _controller_with_finished_phase_a(tmp_path, exit_code=0, write_tech=True)
    status = c.get_status()
    assert status["scc_phase"] == "awaiting_scc_upload"
    assert status["start_error"] is None


def test_stop_during_simt_returns_to_idle(tmp_path):
    c = _controller_with_finished_phase_a(tmp_path, exit_code=1, write_tech=False)
    c._stopped = True
    assert c.get_status()["scc_phase"] == "idle"


def test_other_phases_untouched(tmp_path):
    c = _controller_with_finished_phase_a(tmp_path, exit_code=1, write_tech=False)
    for phase in ("scc_uploaded", "scc_stopped", "running", "idle"):
        c._phase = phase
        c.get_status()
        assert c._phase == phase


# ---------------------------------------------------------------- 1.9
def test_page_reattaches_to_run_after_reload():
    js = (ROOT / "tools" / "web" / "app.js").read_text(encoding="utf-8")
    assert "async function reattachToRun()" in js
    assert "reattachToRun().finally(refreshPreflight);" in js
    boot = js.split("// -------------------------------------------------------- boot", 1)[1]
    assert "SCC_PAUSE_PHASES.includes(status.scc_phase)" in boot and "startPolling();" in boot


# ---------------------------------------------------------------- 1.2
def test_cx_state_codes_and_city_names_are_not_false_mismatch():
    import polygon_check as pc

    def verdict(pin, state, city="X"):
        return pc.analyze_cx_address({
            "address": {"line1": "House 12 Main Road Sector 4", "line2": "", "line3": "",
                        "city": city, "state": state, "pin": pin},
            "gcs_confidence": "high", "gcs_type": "deliveryPoint"})

    assert verdict("141001", "PB").startswith("OK")          # Punjab code, PIN zone 1
    assert verdict("110001", "DL").startswith("OK")
    assert verdict("682001", "KL").startswith("OK")
    assert verdict("500001", "TG").startswith("OK")
    assert verdict("560076", "Bengaluru").startswith("OK")   # city name: no zone verdict
    assert verdict("248197", "Dehradun").startswith("OK")
    assert verdict("560076", "Punjab").startswith("MISMATCH")  # real wrong-zone still caught
    assert verdict("560076", "PB").startswith("MISMATCH")


# ---------------------------------------------------------------- 1.4
def test_new_start_keeps_post_comments_reports(tmp_path, monkeypatch):
    captures = tmp_path / "captures"
    (captures / "runs").mkdir(parents=True)
    report = captures / "post_comments" / "20261101_100000" / "post_comments_report.xlsx"
    report.parent.mkdir(parents=True)
    report.write_text("x", encoding="utf-8")
    cache = captures / "pnama" / "old.csv"
    cache.parent.mkdir()
    cache.write_text("x", encoding="utf-8")
    monkeypatch.setattr(rd, "CAPTURES", captures)
    monkeypatch.setattr(rd, "RUNS", captures / "runs")
    monkeypatch.setattr(rd, "DESKTOP", tmp_path / "Desktop")
    rd.wipe_old_runs(captures / "runs" / "now", log=lambda *_: None)
    assert report.exists() and not cache.exists()


# ---------------------------------------------------------------- 1.6
def test_resume_refused_with_clear_reason_when_midway_expired(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    scc = tmp_path / "scc.csv"
    scc.write_text("x", encoding="utf-8")
    c = rc.RunController()
    c._ever_started, c._run_dir, c._stamp = True, run_dir, run_dir.name
    c._phase, c._scc_upload_path = "scc_uploaded", scc
    c._run_flags = {"ads_approved": True, "allow_source_failures": False}
    monkeypatch.setattr(rc, "midway_session_valid", lambda: False)
    result = c.continue_after_scc()
    assert result["started"] is False and "Midway" in result["reason"]
    assert c._phase == "awaiting_scc_upload"  # a fresh upload is accepted again
    assert c._proc is None


# ---------------------------------------------------------------- 1.7
def test_phase_b_keeps_simt_results(tmp_path):
    import json
    (tmp_path / "summary_phase_a.json").write_text(json.dumps({
        "stages": {"preflight": {"ok": True, "status": "done"}, "tickets": {"ok": True, "status": "done"}},
        "tickets": {"tickets": 5, "rows": 9}, "preflight": {"midway_session_valid": True}}), encoding="utf-8")
    run = rd.Run("x", directory=tmp_path)
    rd.carry_over_phase_a(run)
    assert run.summary["stages"]["tickets"]["status"] == "done"
    assert run.summary["tickets"] == {"tickets": 5, "rows": 9}


# ---------------------------------------------------------------- 1.8
def test_stop_while_paused_returns_to_idle():
    c = rc.RunController()
    c._phase = "awaiting_scc_upload"
    assert c.stop_run() == {"stopped": True}
    assert c._phase == "idle" and c._stopped is True
    assert c.stop_run()["stopped"] is False  # nothing left to stop


# ---------------------------------------------------------------- 1.10
def test_page_shows_failed_run():
    js = (ROOT / "tools" / "web" / "app.js").read_text(encoding="utf-8")
    assert "function runFailed(status)" in js and "The run failed" in js


# ---------------------------------------------------------------- 1.12 / 1.14
def test_approval_column_written_without_polygon_and_agentspaces_header_on_row_1(tmp_path):
    import openpyxl
    import fill_template as ft

    tech = [{"Sl": 1, "case_id": "C1", "impacted_tracking_ids": "123456789012",
             "station_code": "ST1", "approval_in_comments": "Approved"}]
    out = tmp_path / "w.xlsx"
    ft.fill(ft.TEMPLATE, out, tech, [], [], [], [], [])  # no polygon rows
    wb = openpyxl.load_workbook(out)
    try:
        ws = wb[ft.SHEET_WORK]
        assert ws["AI1"].value == "Approval in Comments" and ws["AI2"].value == "Approved"
    finally:
        wb.close()
    out2 = tmp_path / "w2.xlsx"
    polygon = [{"Tracking ID": "123456789012", "Station": "ST1", "GCRS Status": "Outside"}]
    ft.fill(ft.TEMPLATE, out2, tech, [], [], [], [], [], polygon_rows=polygon)
    wb = openpyxl.load_workbook(out2)
    try:
        agent = wb[ft.SHEET_AGENT]
        assert agent["A1"].value == "Tracking ID"
        assert agent["A2"].value == 123456789012 and agent["C2"].value == "Outside"
    finally:
        wb.close()


# ---------------------------------------------------------------- 1.13
def test_no_ads_workbook_is_labelled_partial(tmp_path, monkeypatch):
    import json
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    stamp = "20261123_100000_1"
    (run_dir / f"OOJ_Working_{stamp}.xlsx").write_text("x", encoding="utf-8")
    (run_dir / "summary.json").write_text(json.dumps({"stages": {
        "fill_recalc": {"ok": True}, "read_back": {"ok": True}}}), encoding="utf-8")
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    monkeypatch.setattr(rc, "DESKTOP", desktop)
    target = rc.publish_to_desktop(run_dir, stamp, partial=True)
    assert f"_KABIDEBA1_PARTIAL_{stamp}" in target.name


# ---------------------------------------------------------------- 1.15
def test_unreadable_ticket_comments_are_flagged_not_blank():
    import tickets_pull as tp

    class DeadMcp:
        def call_tool(self, *a, **k):
            raise RuntimeError("pipe closed")

    approvals = tp.scan_approvals(DeadMcp(), [{"id": "T1"}], log=lambda *_: None)
    assert approvals["T1"] == tp.UNREADABLE_LABEL
    assert approvals["T1"].startswith(tp.MANUAL_CHECK_LABEL)


# ---------------------------------------------------------------- 1.17
def test_tech_data_written_from_final_rows(tmp_path):
    from datetime import datetime
    run = rd.Run("x", directory=tmp_path)
    rd.save_tech_data(run, [{"Sl": 1, "createdAt": datetime(2026, 11, 23, 9, 0), "lastUpdatedAt": None}])
    rows = rd.load_tech_data(tmp_path / "tech_data.csv")
    assert len(rows) == 1 and rows[0]["createdAt"].day == 23 and rows[0]["lastUpdatedAt"] is None


# ---------------------------------------------------------------- 1.11
def test_ticket_without_dates_does_not_crash_phase_b(tmp_path):
    path = tmp_path / "tech_data.csv"
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["Sl", "createdAt", "lastUpdatedAt"])
        writer.writeheader()
        writer.writerow({"Sl": 1, "createdAt": "2026-11-20 10:00:00", "lastUpdatedAt": ""})
        writer.writerow({"Sl": 2, "createdAt": "None", "lastUpdatedAt": "not a date"})
    rows = rd.load_tech_data(path)
    assert rows[0]["createdAt"].year == 2026 and rows[0]["lastUpdatedAt"] is None
    assert rows[1]["createdAt"] is None and rows[1]["lastUpdatedAt"] is None
