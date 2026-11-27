"""QuickSight lanes must not get stuck or silently accept a partial export (2026-11-27):
 - a browser network-error page makes the loader navigate again;
 - a DSR export with under half of the requested TIDs is retried once, then warned about.
Fake browser objects only; no real browser or network."""
import sys
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import dsr_export  # noqa: E402
import pnama_export as pn  # noqa: E402


# ------------------------------------------------------------------ completeness helpers
def test_exported_tid_count_and_incomplete_rule():
    text = "tracking_id,zone\n111,N\n222,S\n222,S\n,x\n"
    assert dsr_export.exported_tid_count(text) == 2
    assert dsr_export.exported_tid_count("Tracking id,zone\n333,N\n") == 1
    assert dsr_export.exported_tid_count("a,b\n1,2\n") == 0 and dsr_export.exported_tid_count("") == 0
    assert dsr_export.is_incomplete(884, 8) is True
    assert dsr_export.is_incomplete(1105, 552) is True           # just under half (552.5)
    assert dsr_export.is_incomplete(1105, 553) is False          # half or more is accepted
    assert dsr_export.is_incomplete(1105, 1094) is False         # the normal ~99%
    assert dsr_export.is_incomplete(10, 0) is False              # tiny requests are never retried


# ------------------------------------------------------------------ DSR retry loop
class _Cdp:
    def detach(self):
        pass


class _Page:
    def reload(self, **_):
        pass


class _Ctx:
    def new_page(self):
        return _Page()

    def close(self):
        pass


@contextmanager
def _fake_playwright():
    yield object()


def _run_fetch(monkeypatch, tmp_path, tids, texts):
    """Run dsr_export.fetch with a fake browser whose Export-to-CSV returns ``texts`` in order."""
    calls = []
    monkeypatch.setattr(dsr_export, "sync_playwright", _fake_playwright)
    monkeypatch.setattr(dsr_export, "OUT", tmp_path)
    monkeypatch.setattr(dsr_export, "DL_DIR", tmp_path / "dl")
    (tmp_path / "dl").mkdir(exist_ok=True)
    monkeypatch.setattr(dsr_export.time, "sleep", lambda *_: None)
    monkeypatch.setattr(pn, "_launch", lambda _p: _Ctx())
    monkeypatch.setattr(pn, "configure_download_directory", lambda *_: _Cdp())
    for name in ("open_dashboard", "select_tab", "wait_visual_idle", "set_tracking_filter"):
        monkeypatch.setattr(pn, name, lambda *a, **k: None)

    def export_csv(_page):
        calls.append(1)
        return {"text": texts[min(len(calls), len(texts)) - 1]}
    monkeypatch.setattr(pn, "export_csv", export_csv)
    saved = (pn.DASHBOARD_URL, pn.TARGET_TAB, pn.CONTROL_LABEL, pn.OUT, pn.DL_DIR, pn.log)
    logs = []
    try:
        rows, result = dsr_export.fetch(tids, log_fn=logs.append)
    finally:
        pn.DASHBOARD_URL, pn.TARGET_TAB, pn.CONTROL_LABEL, pn.OUT, pn.DL_DIR, pn.log = saved
    return rows, result, calls, logs


def _csv(tids):
    return "tracking_id,zone\n" + "".join(f"{t},N\n" for t in tids)


def test_partial_dsr_export_is_retried_once_and_the_full_result_wins(monkeypatch, tmp_path):
    tids = [str(100000000000 + i) for i in range(100)]
    rows, result, calls, logs = _run_fetch(monkeypatch, tmp_path, tids, [_csv(tids[:3]), _csv(tids)])
    assert len(calls) == 2 and len(rows) == 100 and result["exported_unique"] == 100
    assert any("incomplete" in line for line in logs)
    assert not any("DSR WARNING" in line for line in logs)


def test_still_partial_after_one_retry_is_accepted_with_a_warning(monkeypatch, tmp_path):
    tids = [str(100000000000 + i) for i in range(100)]
    rows, _, calls, logs = _run_fetch(monkeypatch, tmp_path, tids, [_csv(tids[:3])])
    assert len(calls) == 2 and len(rows) == 3                    # exactly one extra attempt
    assert any("DSR WARNING: only 3 of 100" in line for line in logs)


def test_small_or_complete_requests_are_not_retried(monkeypatch, tmp_path):
    few = [str(100000000000 + i) for i in range(5)]
    _, _, calls, _ = _run_fetch(monkeypatch, tmp_path, few, [_csv(few[:1])])
    assert len(calls) == 1
    many = [str(100000000000 + i) for i in range(100)]
    _, _, calls, _ = _run_fetch(monkeypatch, tmp_path, many, [_csv(many[:97])])
    assert len(calls) == 1


# ------------------------------------------------------------------ network-error page
class _Locator:
    def __init__(self, text="", count=0):
        self._text, self._count = text, count

    def inner_text(self, **_):
        return self._text

    def count(self):
        return self._count


class _NetPage:
    """Shows a browser error page until the loader has navigated twice."""

    def __init__(self):
        self.gotos = 0

    def goto(self, *_a, **_k):
        self.gotos += 1

    @property
    def url(self):
        if self.gotos < 2:
            return "chrome-error://chromewebdata/"
        return "https://us-east-1.quicksight.aws.amazon.com/sn/account/amazonbi/dashboards/x"

    def locator(self, selector):
        return _Locator("tabs", 3) if "tab" in selector else _Locator("tabs")

    def title(self):
        return "Quick"


def test_loader_navigates_again_from_a_network_error_page(monkeypatch):
    monkeypatch.setattr(pn.time, "sleep", lambda *_: None)
    logs = []
    monkeypatch.setattr(pn, "log", logs.append)
    page = _NetPage()
    pn.open_dashboard(page)
    assert page.gotos == 2                                        # 1st goto, then one re-navigation
    assert any("network error page" in line for line in logs)
