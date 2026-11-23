"""Portable paths, weekly health-check schedule, and the share-zip contents."""
import json
import sys
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import pytest  # noqa: E402

import about  # noqa: E402
import health_check  # noqa: E402
import ooj_paths  # noqa: E402


def test_no_owner_specific_paths_in_tool_paths():
    assert "kabideba" not in ooj_paths.BUILDER_MCP.lower() or ooj_paths.USER_ALIAS == "kabideba"
    assert ooj_paths.BUILDER_MCP.lower().endswith(r"toolbox\bin\builder-mcp.exe")
    assert ooj_paths.EDGE_PROFILE.exists()


def test_owner_credit_and_contact():
    assert about.OWNER == "KABIDEBA"
    assert "kabideba@amazon.com" in about.support_text()
    assert any("KABIDEBA" in line for line in about.banner_lines())
    html = (ROOT / "tools" / "web" / "index.html").read_text(encoding="utf-8")
    assert "KABIDEBA" in html and "by kabideba" in html and "kabideba@amazon.com" in html


def test_health_check_is_due_weekly_and_on_new_version(tmp_path, monkeypatch):
    last = tmp_path / "last_check.json"
    monkeypatch.setattr(health_check, "LAST_CHECK", last)
    assert health_check.is_due()  # never checked
    now = datetime(2026, 11, 18, 10, 0)
    last.write_text(json.dumps({"when": (now - timedelta(days=3)).isoformat(), "version": about.version()}))
    assert not health_check.is_due(now)
    last.write_text(json.dumps({"when": (now - timedelta(days=7)).isoformat(), "version": about.version()}))
    assert health_check.is_due(now)
    last.write_text(json.dumps({"when": (now - timedelta(days=1)).isoformat(), "version": "0.0.0-old"}))
    assert health_check.is_due(now)


def test_share_zip_excludes_private_and_run_data(tmp_path):
    # The zip builder is owner-only and is not shipped inside the zip itself.
    build_share_zip = pytest.importorskip("build_share_zip")
    for rel in (".neurotrace/backend_bridge.json", ".kiro/steering/ooj-instructions.md", ".git/config",
                "tools/captures/runs/x/run.log", "tools/resolve_runs/r.xlsx", "tools/health/h.txt",
                ".agents/x.txt", "AGENTS.md", "tools/build_share_zip.py", "tools/__pycache__/a.pyc"):
        assert not build_share_zip.included(rel), rel
    for rel in ("Start OOJ Web.bat", "READ ME FIRST.txt", "NOTICE.txt", "VERSION", "tools/run_daily.py",
                "Docs/Templete_OOJ Process_Date_Month_KABIDEBA.xltx", "tests/test_dsr_data_sheet.py"):
        assert build_share_zip.included(rel), rel
    target = build_share_zip.build(tmp_path)
    with zipfile.ZipFile(target) as zf:
        names = zf.namelist()
        assert all(n.startswith(build_share_zip.FOLDER + "/") for n in names)
        assert not any("neurotrace" in n or "/captures/" in n or "/.kiro/" in n for n in names)
        assert zf.read(build_share_zip.FOLDER + "/Start OOJ Web.bat").count(b"\r\n") > 5
