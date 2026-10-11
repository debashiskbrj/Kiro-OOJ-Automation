"""Tests for the local web runner controller: status parsing, the accuracy gate,
and the Desktop-copy gating. No source systems or network are touched."""
import json
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(TOOLS / "web"))

import run_controller as rc  # noqa: E402


def _publishable_summary() -> dict:
    lanes = ("gcrs", "eagle", "scc", "pnama", "dsr", "ads", "polygon")
    stages = {name: {"ok": True, "seconds": 1.0} for name in lanes}
    stages["tickets"] = {"ok": True, "seconds": 1.0}
    stages["fill_recalc"] = {"ok": True, "seconds": 1.0}
    stages["read_back"] = {"ok": True, "seconds": 1.0}
    stages["apply_remarks"] = {"ok": True, "seconds": 1.0}
    return {
        "stamp": "20260918_175108_857466",
        "stages": stages,
        "tickets": {"tickets": 788, "rows": 1560},
        "scc": {"requested": 1496, "rows": 1495, "missing": 1},
        "eagle_missing": 1,
        "pnama": {"rows": 1447, "unique": 1440},
        "dsr": {"rows": 1452},
        "gcrs": {"resolved": 1495, "not_found": 1, "errored": 0},
        "ads": {"rows": 1464, "missing": 31},
        "polygon": {
            "rows": 1560,
            "inside": 1006,
            "outside": 334,
            "errors": 220,
            "verification": {"verify_ok": True, "rows": 1560, "input_rows": 1560, "sha256": "abc"},
        },
    }


def _partial_summary() -> dict:
    """A PARTIAL run: SCC failed and was left blank, but the workbook was produced."""
    summary = _publishable_summary()
    summary["stages"]["scc"] = {"ok": False, "status": "error", "error": "SCC not authenticated"}
    summary["blank_sources"] = ["scc"]
    # No publication_blocked marker — the run continued in partial mode.
    return summary


def _blocked_summary() -> dict:
    summary = _publishable_summary()
    summary["stages"]["dsr"] = {"ok": False, "error": "DSR output failed identity validation"}
    summary["publication_blocked"] = {"failed_required": ["dsr"], "skipped_required": []}
    return summary


def test_gate_accepts_publishable():
    assert rc.is_publishable(_publishable_summary(), partial=False) is True


def test_gate_rejects_partial_even_when_stages_ok():
    assert rc.is_publishable(_publishable_summary(), partial=True) is False


def test_gate_rejects_blocked():
    assert rc.is_publishable(_blocked_summary(), partial=False) is False


# --- partial / blank-source downloadable gate -------------------------------
def test_partial_run_is_not_fully_publishable():
    # A blank source means it is not a full "Verified" run.
    assert rc.is_publishable(_partial_summary(), partial=False) is False


def test_partial_run_is_downloadable():
    # But the produced workbook is still downloadable.
    assert rc.is_downloadable(_partial_summary(), partial=False) is True
    assert rc.blank_sources(_partial_summary()) == ["scc"]


def test_full_run_is_downloadable():
    assert rc.is_downloadable(_publishable_summary(), partial=False) is True


def test_blocked_run_is_not_downloadable():
    assert rc.is_downloadable(_blocked_summary(), partial=False) is False


def test_partial_not_downloadable_if_workbook_stage_failed():
    summary = _partial_summary()
    summary["stages"]["fill_recalc"] = {"ok": False, "error": "boom"}
    assert rc.is_downloadable(summary, partial=False) is False


def test_gate_rejects_failed_verification():
    summary = _publishable_summary()
    summary["polygon"]["verification"] = {"verify_ok": False}
    assert rc.is_publishable(summary, partial=False) is False


def test_gate_rejects_missing_readback():
    summary = _publishable_summary()
    summary["stages"].pop("read_back")
    assert rc.is_publishable(summary, partial=False) is False


def test_sources_from_summary_reports_counts_and_status():
    sources = rc._sources_from_summary(_publishable_summary())
    assert sources["tickets"]["status"] == "done"
    assert sources["tickets"]["counts"]["rows"] == 1560
    assert sources["scc"]["counts"]["missing"] == 1
    assert sources["polygon"]["counts"]["inside"] == 1006


def test_sources_reports_error_stage():
    sources = rc._sources_from_summary(_blocked_summary())
    assert sources["dsr"]["status"] == "error"
    assert "DSR" in sources["dsr"]["error"]


def test_sources_pending_when_stage_absent():
    summary = {"stages": {}}
    sources = rc._sources_from_summary(summary)
    assert all(entry["status"] == "pending" for entry in sources.values())


def test_sources_reports_running_stage():
    summary = {"stages": {"scc": {"ok": None, "status": "running"}}}
    sources = rc._sources_from_summary(summary)
    assert sources["scc"]["status"] == "running"


def test_sources_running_via_null_ok_without_status():
    # Older/edge summaries: ok is None and no explicit status field.
    summary = {"stages": {"pnama": {"ok": None}}}
    sources = rc._sources_from_summary(summary)
    assert sources["pnama"]["status"] == "running"


def test_publish_refuses_when_blocked(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "summary.json").write_text(json.dumps(_blocked_summary()), encoding="utf-8")
    (run_dir / "OOJ_Working_20260918_175108_857466.xlsx").write_bytes(b"xlsx")
    with pytest.raises(RuntimeError, match="no downloadable workbook"):
        rc.publish_to_desktop(run_dir, "20260918_175108_857466")


def test_publish_copies_when_publishable(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    stamp = "20260918_175108_857466"
    (run_dir / "summary.json").write_text(json.dumps(_publishable_summary()), encoding="utf-8")
    source = run_dir / f"OOJ_Working_{stamp}.xlsx"
    source.write_bytes(b"real-workbook-bytes")

    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    monkeypatch.setattr(rc, "DESKTOP", desktop)

    target = rc.publish_to_desktop(run_dir, stamp)
    assert target.exists()
    assert target.parent == desktop
    assert target.read_bytes() == b"real-workbook-bytes"


def test_read_summary_handles_missing(tmp_path):
    assert rc._read_summary(tmp_path) is None


def test_verification_ok_requires_flag():
    assert rc._verification_ok({"verify_ok": True}) is True
    assert rc._verification_ok({"verify_ok": False}) is False
    assert rc._verification_ok(None) is False
    assert rc._verification_ok("ok") is False


