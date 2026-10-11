"""Offline tests for the gated post-comments feature.

No real Playwright/Edge/network is used anywhere: the dry-run path never imports
Playwright, and the controller guard tests patch subprocess.Popen to fail if a
forbidden spawn is attempted (mirroring tests/test_scc_retry.py's forbidden-call
assertion style).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import openpyxl
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "web"))

import post_comments_runner as pcr  # noqa: E402
import post_comments_controller as pcc_mod  # noqa: E402
import run_controller as rc  # noqa: E402


# --------------------------------------------------------------------- helpers
def _write_xlsx(path: Path, headers, rows) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(list(headers))
    for row in rows:
        ws.append(list(row))
    wb.save(path)


# ----------------------------------------------------- (a) parsing / dry-run
def test_dry_run_parses_mixed_headers_and_whitespace(tmp_path):
    xlsx = tmp_path / "data.xlsx"
    # Mixed-case/whitespace headers; whitespace in cells; one malformed row (blank tid).
    _write_xlsx(
        xlsx,
        ["URL", " Tid ", "Remark"],
        [
            ["http://sim/A", " T1 ", " first remark "],
            ["http://sim/A", "T2", "second remark"],
            ["http://sim/B", "T3", "third remark"],
            ["http://sim/B", "", "orphan — blank tid"],
        ],
    )
    out_dir = tmp_path / "out"
    summary = pcr.run_dry_run(xlsx, out_dir)

    # Shape of the returned summary.
    assert summary["total_rows"] == 4
    assert summary["unique_url_count"] == 2
    assert len(summary["malformed_rows"]) == 1
    bad = summary["malformed_rows"][0]
    assert bad["row_index"] == 5  # header row 1 + 0-based index 3 + 1
    assert "tid" in bad["reason"]

    # The written JSON matches the returned summary.
    written = json.loads((out_dir / "dry_run_summary.json").read_text(encoding="utf-8"))
    assert written["total_rows"] == 4
    assert written["unique_url_count"] == 2

    # Preview comment format: whitespace stripped, exact 'TID: {tid} | {remark}' lines.
    preview = {item["url"]: item for item in summary["preview"]}
    assert preview["http://sim/A"]["tid_count"] == 2
    assert preview["http://sim/A"]["comment_text"] == (
        "TID: T1 | first remark\nTID: T2 | second remark"
    )
    assert preview["http://sim/B"]["tid_count"] == 1
    assert preview["http://sim/B"]["comment_text"] == "TID: T3 | third remark"


def test_dry_run_never_imports_playwright(tmp_path, monkeypatch):
    # If anything in the dry-run path tried to import playwright, this would fail.
    monkeypatch.setitem(sys.modules, "playwright", None)
    xlsx = tmp_path / "d.xlsx"
    _write_xlsx(xlsx, ["url", "tid", "remark"], [["http://sim/A", "T1", "r"]])
    summary = pcr.run_dry_run(xlsx, tmp_path / "out")
    assert summary["unique_url_count"] == 1


def test_parse_raises_on_missing_required_column(tmp_path):
    xlsx = tmp_path / "bad.xlsx"
    _write_xlsx(xlsx, ["url", "tid"], [["http://sim/A", "T1"]])
    with pytest.raises(ValueError, match="remark"):
        pcr.parse_workbook(xlsx)


# ------------------------------------------- (b) mutual-exclusion guard
def _no_spawn(monkeypatch):
    """Patch Popen so any spawn attempt fails the test loudly."""
    def _boom(*args, **kwargs):
        raise AssertionError("subprocess.Popen must not be called when blocked")

    monkeypatch.setattr(pcc_mod.subprocess, "Popen", _boom)


def test_confirm_blocked_while_pipeline_running(tmp_path, monkeypatch):
    _no_spawn(monkeypatch)
    monkeypatch.setattr(rc.CONTROLLER, "is_running", lambda: True)
    controller = pcc_mod.PostCommentsController()
    # Pretend a valid, current dry-run exists so only the pipeline guard can block.
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "dry_run_summary.json").write_text("{}", encoding="utf-8")
    upload = tmp_path / "u.xlsx"
    upload.write_bytes(b"x")
    controller._uploaded_path = upload
    controller._last_out_dir = out_dir

    result = controller.confirm_and_post()
    assert result["started"] is False
    assert "pipeline" in result["reason"].lower()

    retry = controller.retry_failed()
    assert retry["started"] is False
    assert "pipeline" in retry["reason"].lower()


# --------------------------------- (c) dry-run-required-before-confirm guard
def test_confirm_blocked_without_prior_dry_run(monkeypatch):
    _no_spawn(monkeypatch)
    monkeypatch.setattr(rc.CONTROLLER, "is_running", lambda: False)
    controller = pcc_mod.PostCommentsController()  # no upload, no dry-run yet
    result = controller.confirm_and_post()
    assert result["started"] is False
    assert result["reason"]  # a clear non-empty reason


def test_confirm_blocked_when_summary_older_than_upload(tmp_path, monkeypatch):
    _no_spawn(monkeypatch)
    monkeypatch.setattr(rc.CONTROLLER, "is_running", lambda: False)
    controller = pcc_mod.PostCommentsController()
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    summary = out_dir / "dry_run_summary.json"
    summary.write_text("{}", encoding="utf-8")
    upload = tmp_path / "u.xlsx"
    upload.write_bytes(b"x")

    # Make the summary clearly OLDER than the uploaded file (stale dry-run).
    old = time.time() - 3600
    import os

    os.utime(summary, (old, old))
    controller._uploaded_path = upload
    controller._last_out_dir = out_dir

    result = controller.confirm_and_post()
    assert result["started"] is False
    assert "stale" in result["reason"].lower()


def test_confirm_proceeds_when_all_preconditions_pass(tmp_path, monkeypatch):
    """When the gate passes, the controller spawns exactly one subprocess."""
    monkeypatch.setattr(rc.CONTROLLER, "is_running", lambda: False)
    spawned = {"n": 0}

    class _FakeProc:
        def __init__(self, *a, **k):
            self.stdout = iter(())
            self.pid = 1234

        def poll(self):
            return 0  # already finished so is_running() stays False after spawn

    def _fake_popen(*args, **kwargs):
        spawned["n"] += 1
        return _FakeProc()

    monkeypatch.setattr(pcc_mod.subprocess, "Popen", _fake_popen)

    controller = pcc_mod.PostCommentsController()
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    upload = tmp_path / "u.xlsx"
    upload.write_bytes(b"x")
    # Summary must be newer than the upload to pass the staleness guard.
    summary = out_dir / "dry_run_summary.json"
    summary.write_text("{}", encoding="utf-8")
    controller._uploaded_path = upload
    controller._last_out_dir = out_dir

    result = controller.confirm_and_post()
    assert result["started"] is True
    assert spawned["n"] == 1


def test_upload_rejects_non_xlsx(monkeypatch):
    _no_spawn(monkeypatch)
    monkeypatch.setattr(rc.CONTROLLER, "is_running", lambda: False)
    controller = pcc_mod.PostCommentsController()
    result = controller.upload_and_preview(b"data", "notes.txt")
    assert result["started"] is False
    assert ".xlsx" in result["reason"]
