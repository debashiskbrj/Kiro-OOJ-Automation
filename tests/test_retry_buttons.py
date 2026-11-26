"""Retry buttons (operator request 2026-11-26): "Retry failed / pending" for section 5
(Resolve ticket) and section 6 (Post comments). Only failed / not-yet-done items may be
sent again; done ones must never be repeated. Fake processes only: nothing real is called."""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import openpyxl
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "web"))

import post_comments_controller as pcc_mod  # noqa: E402
import post_comments_runner as pcr  # noqa: E402
import resolve_controller as rsc  # noqa: E402
import resolve_tickets_runner as rtr  # noqa: E402
import run_controller as rc  # noqa: E402


# ----------------------------------------------------------------- Resolve ticket
def _resolve_run_dir(root: Path, ids, results, sent=()):
    out = root / "20260101_000000"
    out.mkdir()
    (out / "ids.txt").write_text("\n".join(ids), encoding="utf-8")
    with open(out / "results.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=rtr.RESULT_FIELDS)
        writer.writeheader()
        for ticket, result in results.items():
            writer.writerow({"Ticket ID": ticket, "Ticket URL": "u", "Status before": "Assigned",
                             "Result": result, "Status after": "", "Detail": "", "Time": "t"})
    with open(out / "sent.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=rtr.SENT_FIELDS)
        writer.writeheader()
        for ticket in sent:
            writer.writerow({"Ticket ID": ticket, "Status before": "Assigned", "Time": "t"})
    return out


@pytest.fixture
def resolve_ctl(tmp_path, monkeypatch):
    monkeypatch.setattr(rsc, "RT_ROOT", tmp_path)
    ctl = rsc.ResolveController()
    ctl._out_dir = _resolve_run_dir(
        tmp_path,
        ["D1", "D2", "D3", "D4", "D5"],
        {"D1": "Resolved", "D2": "Already resolved", "D3": "Failed"},
        sent=["D1", "D5"])           # D5: sent, never verified; D4: never reached
    return ctl


def test_resolve_retry_ids_are_only_failed_and_not_done(resolve_ctl):
    assert resolve_ctl.retry_ids() == ["D3", "D4", "D5"]
    status = resolve_ctl.get_status()
    assert status["retry_count"] == 3


def test_resolve_retry_starts_new_run_on_only_those_tickets(resolve_ctl, tmp_path, monkeypatch):
    spawned = []
    monkeypatch.setattr(rsc.subprocess, "Popen", lambda cmd, **kw: spawned.append(cmd) or
                        SimpleNamespace(poll=lambda: None, pid=1))
    result = resolve_ctl.retry_failed()
    assert result["started"] is True and result["total"] == 3
    new_dir = resolve_ctl._out_dir
    assert new_dir.name.endswith("_retry") and new_dir != tmp_path / "20260101_000000"
    assert (new_dir / "ids.txt").read_text(encoding="utf-8").split() == ["D3", "D4", "D5"]
    info = json.loads((new_dir / "input_check.json").read_text(encoding="utf-8"))
    assert info["retry_of"] == "20260101_000000" and info["unique"] == 3
    assert spawned and str(new_dir / "ids.txt") in spawned[0]
    # A run is now in progress: a second click is refused and the count drops to 0.
    assert resolve_ctl.retry_failed()["started"] is False
    assert resolve_ctl.get_status()["retry_count"] == 0


def test_resolve_retry_refused_when_nothing_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(rsc, "RT_ROOT", tmp_path)
    monkeypatch.setattr(rsc.subprocess, "Popen",
                        lambda *a, **k: pytest.fail("must not start a run"))
    ctl = rsc.ResolveController()
    assert ctl.retry_failed()["started"] is False                    # no earlier run
    ctl._out_dir = _resolve_run_dir(tmp_path, ["D1", "D2"],
                                    {"D1": "Resolved", "D2": "Already resolved"})
    result = ctl.retry_failed()
    assert result["started"] is False and "No failed" in result["reason"]
    assert ctl.get_status()["retry_count"] == 0


# ----------------------------------------------------------------- Post comments
def _groups(*urls):
    return [{"url": u, "group": None} for u in urls]


def _row(url, status):
    return {"TID": "T", "URL": url, "Status": status, "Time": "t", "Date": "2026-01-01"}


def test_retry_targets_failed_and_pending_but_never_posted():
    rows = [_row("a", "Success"), _row("b", "Failed - No Confirmation"),
            _row("c", "Failed - x"), _row("c", "Success")]
    failed, pending = pcr.retry_targets(_groups("a", "b", "c", "d", "e"), rows)
    assert failed == {"b"}            # c failed first but a later Success means done
    assert pending == {"d", "e"}      # never attempted
    assert "a" not in failed | pending and "c" not in failed | pending


def test_run_retry_failed_selects_failed_and_pending_only(tmp_path, monkeypatch):
    out = tmp_path / "out"
    out.mkdir()
    pd.DataFrame([_row("a", "Success"), _row("b", "Failed - x")]).to_excel(
        out / "post_comments_report.xlsx", index=False)
    captured = {}

    def fake_launch(input_path, out_dir, select):
        groups = _groups("a", "b", "c")
        captured["pending"], captured["kept"] = select(groups, out_dir)
        return {}
    monkeypatch.setattr(pcr, "_launch_and_post", fake_launch)
    pcr.run_retry_failed(tmp_path / "in.xlsx", out)
    assert [g["url"] for g in captured["pending"]] == ["b", "c"]     # a (posted) excluded
    assert [r["URL"] for r in captured["kept"]] == ["a"]             # posted row is kept


def test_controller_counts_and_retry_gate(tmp_path, monkeypatch):
    monkeypatch.setattr(rc.CONTROLLER, "is_running", lambda: False)
    upload = tmp_path / "u.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["url", "tid", "remark"])
    for url in ("http://s/a", "http://s/b", "http://s/c"):
        ws.append([url, "T1", "r"])
    wb.save(upload)
    out = tmp_path / "out"
    out.mkdir()
    pd.DataFrame([_row("http://s/a", "Success"), _row("http://s/b", "Failed - x")]).to_excel(
        out / "post_comments_report.xlsx", index=False)
    ctl = pcc_mod.PostCommentsController()
    ctl._uploaded_path, ctl._last_out_dir = upload, out
    assert ctl._retry_counts() == (1, 1)             # b failed, c pending, a posted
    status = ctl.get_status()
    assert status["retry_failed_count"] == 1 and status["retry_pending_count"] == 1

    spawned = []
    monkeypatch.setattr(ctl, "_spawn", lambda mode, d: spawned.append(mode))
    assert ctl.retry_failed()["started"] is True and spawned == ["retry-failed"]

    # Everything posted: nothing to retry, no spawn.
    pd.DataFrame([_row(u, "Success") for u in ("http://s/a", "http://s/b", "http://s/c")]).to_excel(
        out / "post_comments_report.xlsx", index=False)
    spawned.clear()
    ctl._retry_cache = None
    result = ctl.retry_failed()
    assert result["started"] is False and "No failed or pending" in result["reason"] and not spawned
    assert ctl.get_status()["retry_failed_count"] == 0


def test_page_has_both_retry_buttons():
    html = (ROOT / "tools" / "web" / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "tools" / "web" / "app.js").read_text(encoding="utf-8")
    assert 'id="rt-retry-btn"' in html and 'id="pc-retry-btn"' in html
    assert "/resolve/retry-failed" in js and "/post-comments/retry-failed" in js
