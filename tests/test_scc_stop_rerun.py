"""Stop-after-SCC feature: stop the run after the SCC upload, keep the SIM-T data and the
SCC file, then rerun the same file or upload a new one. A dummy long-running script
stands in for run_daily.py so the real kill path (taskkill /T) is exercised."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools" / "web"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import run_controller as rc  # noqa: E402

SCC_CSV = "Tracking ID,State,Destination,Source,Ship Option\n123456789012,Delivered,ST1,ST2,Std\n"
PHASE_A_SUMMARY = {"stamp": "x", "stages": {"preflight": {"ok": True, "status": "done"},
                                             "tickets": {"ok": True, "status": "done"}},
                   "tickets": {"tickets": 1, "rows": 1}}


def _setup(tmp_path, monkeypatch):
    tools = tmp_path / "tools"
    tools.mkdir()
    sleeper = tools / "sleeper.py"
    sleeper.write_text("import time\ntime.sleep(120)\n", encoding="utf-8")
    monkeypatch.setattr(rc, "TOOLS", tools)
    monkeypatch.setattr(rc, "RUN_DAILY", sleeper)
    # Resume preflight (Midway + Edge) is tested separately; no live session needed here.
    monkeypatch.setattr(rc, "_resume_preflight_problem", lambda: None)

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    for name in ("tech_data.csv", "tids.txt", "run.log", "tickets_pull.log"):
        (run_dir / name).write_text("x", encoding="utf-8")
    (run_dir / "summary.json").write_text(json.dumps(PHASE_A_SUMMARY), encoding="utf-8")
    scc_file = tmp_path / "scc.csv"
    scc_file.write_text(SCC_CSV, encoding="utf-8")

    c = rc.RunController()
    c._ever_started = True
    c._run_dir = run_dir
    c._stamp = run_dir.name
    c._phase = "scc_uploaded"
    c._scc_upload_path = scc_file
    c._run_flags = {"ads_approved": True, "allow_source_failures": False}
    return c, run_dir


def test_stop_after_scc_refused_when_not_in_after_scc_stage(tmp_path, monkeypatch):
    c, _ = _setup(tmp_path, monkeypatch)
    assert c.stop_after_scc()["stopped"] is False
    assert c._phase == "scc_uploaded"


def test_stop_keeps_simt_then_rerun_same_file_and_new_upload(tmp_path, monkeypatch):
    c, run_dir = _setup(tmp_path, monkeypatch)
    try:
        assert c.continue_after_scc()["started"] is True
        assert (run_dir / rc.PHASE_A_SUMMARY).exists()
        # Phase B writes partial outputs.
        (run_dir / "eagle.csv").write_text("TrackingId\n1\n", encoding="utf-8")
        (run_dir / "summary.json").write_text(
            json.dumps({"stages": {"eagle": {"ok": None, "status": "running"}}}), encoding="utf-8")
        status = c.get_status()
        assert status["can_stop_after_scc"] is True
        # A second Phase B cannot be spawned while one runs.
        assert c.continue_after_scc()["started"] is False

        result = c.stop_after_scc()
        assert result["stopped"] is True
        assert not c.is_running()
        assert c._phase == "scc_stopped"
        # SIM-T data stays; Phase B output moved aside (not deleted); Phase A view restored.
        for name in ("tech_data.csv", "tids.txt", "run.log", "tickets_pull.log"):
            assert (run_dir / name).exists(), name
        assert not (run_dir / "eagle.csv").exists()
        assert (run_dir / result["archived_to"] / "eagle.csv").exists()
        assert json.loads((run_dir / "summary.json").read_text(encoding="utf-8")) == PHASE_A_SUMMARY
        status = c.get_status()
        assert status["scc_phase"] == "scc_stopped"
        assert status["can_stop_after_scc"] is False
        assert status["stopped"] is False
        assert status["sources"]["tickets"]["status"] == "done"
        assert status["sources"]["eagle"]["status"] == "pending"
        assert status["scc_upload"]["uploaded"] is True
        assert "Stopped by operator" in (run_dir / "run.log").read_text(encoding="utf-8")

        # Rerun with the same file.
        assert c.continue_after_scc()["started"] is True
        assert c._phase == "running"
        assert c.stop_after_scc()["stopped"] is True

        # Or upload a new file while stopped -> accepted, ready to continue.
        uploaded = c.upload_scc_csv(SCC_CSV.encode("utf-8"), "new_scc.csv")
        assert uploaded["uploaded"] is True
        assert c._phase == "scc_uploaded"
        assert c._scc_upload_path.name.endswith("new_scc.csv")
    finally:
        if c.is_running():
            c.stop_run()


def test_existing_stop_run_unchanged(tmp_path, monkeypatch):
    c, _ = _setup(tmp_path, monkeypatch)
    try:
        assert c.continue_after_scc()["started"] is True
        assert c.stop_run()["stopped"] is True
        status = c.get_status()
        assert status["stopped"] is True
        assert status["scc_phase"] == "running"
    finally:
        if c.is_running():
            c.stop_run()
