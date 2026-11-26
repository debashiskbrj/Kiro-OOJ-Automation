"""Remark Summary pivot (operator request 2026-11-24): recalc_excel.ps1 is asked to add a
pivot of Working Sheet column P, and a pivot failure never fails the run. No Excel is used."""
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import run_daily  # noqa: E402


class FakeRun:
    def __init__(self, tmp_path):
        self.dir = tmp_path
        self.stamp = "20261124_000000_000000"
        self.lines = []
        self.summary = {}

    def log(self, line):
        self.lines.append(line)


def _call(monkeypatch, tmp_path, stdout):
    calls = []
    import rule_b_bucket
    monkeypatch.setattr(run_daily.ft, "fill", lambda *a, **k: {"tech": 3})
    monkeypatch.setattr(rule_b_bucket, "apply", lambda *a, **k: {"ok": True})

    def fake_run(cmd, **_):
        calls.append(cmd)
        return SimpleNamespace(stdout=stdout, stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)
    run = FakeRun(tmp_path)
    out = run_daily.fill_and_recalc(run, [{}, {}, {}], [], [], [], [], [], [], [])
    return run, calls, out


def test_pivot_requested_with_row_count(monkeypatch, tmp_path):
    run, calls, out = _call(monkeypatch, tmp_path, "PIVOT_OK\nRECALC_OK x\n")
    cmd = calls[0]
    assert cmd[cmd.index("-PivotRows") + 1] == "3"
    assert any("Remark Summary sheet added" in line for line in run.lines)
    assert out.name.startswith("OOJ_Working_")


def test_pivot_failure_is_only_a_warning(monkeypatch, tmp_path):
    run, _, out = _call(monkeypatch, tmp_path, "PIVOT_FAILED boom\nRECALC_OK x\n")
    assert any("WARNING: Remark Summary pivot not added (PIVOT_FAILED boom)" in line
               for line in run.lines)
    assert out.name.startswith("OOJ_Working_")


def test_recalc_script_builds_pivot_on_remark_column():
    script = (ROOT / "tools" / "recalc_excel.ps1").read_text(encoding="utf-8")
    assert "[int]$PivotRows = 0" in script
    assert "Remark Summary" in script and "R1C1:R{0}C16" in script   # A..P incl. Remark
    assert "-4112" in script and "AutoSort(2" in script              # count, largest first
    assert "PIVOT_FAILED" in script                                  # non-fatal
