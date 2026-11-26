"""Browser sign-in check at Start (PNAMA/DSR). No real browser is opened by these tests."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import browser_signin as bs  # noqa: E402


def test_classify_url():
    assert bs.classify_url("https://midway-auth.amazon.com/login?next=x") == "login"
    assert bs.classify_url("https://us-east-1.quicksight.aws.amazon.com/sn/auth/signin") == "login"
    assert bs.classify_url("https://idp.federate.amazon.com/api/oauth2") == "login"
    assert bs.classify_url(
        "https://us-east-1.quicksight.aws.amazon.com/sn/account/amazonbi/dashboards/abc") == "dashboard"
    assert bs.classify_url("about:blank") == "other"
    assert bs.classify_url("") == "other"


class FakePage:
    """URL sequence: login page for ``login_polls`` polls, then the dashboard."""

    def __init__(self, login_polls):
        self.login_polls = login_polls
        self.polls = 0
        self.target = ""

    def goto(self, url, **_):
        self.target = url

    def bring_to_front(self):
        pass

    @property
    def url(self):
        self.polls += 1
        if self.polls <= self.login_polls:
            return "https://midway-auth.amazon.com/login"
        return self.target

    def locator(self, _sel):
        class L:
            def count(self_inner):
                return 3
        return L()


def test_waits_for_login_then_detects_dashboard(monkeypatch):
    monkeypatch.setattr(bs, "POLL_S", 0)
    lines = []
    page = FakePage(login_polls=3)
    url = "https://us-east-1.quicksight.aws.amazon.com/sn/account/amazonbi/dashboards/x"
    assert bs._wait_signed_in(page, url, "PNAMA", lines.append, timeout_s=5) is True
    assert any("needs Midway" in line for line in lines)
    assert sum("needs Midway" in line for line in lines) == 1      # asked once, not spammed


def test_already_signed_in_does_not_ask(monkeypatch):
    monkeypatch.setattr(bs, "POLL_S", 0)
    lines = []
    url = "https://us-east-1.quicksight.aws.amazon.com/sn/account/amazonbi/dashboards/x"
    assert bs._wait_signed_in(FakePage(0), url, "DSR", lines.append, timeout_s=5) is True
    assert not any("needs Midway" in line for line in lines)


def test_wait_is_capped_at_20_seconds():
    assert bs.SIGNIN_TIMEOUT_S == 20


def test_gives_up_when_login_never_clears(monkeypatch):
    monkeypatch.setattr(bs, "POLL_S", 0)
    lines = []
    url = "https://us-east-1.quicksight.aws.amazon.com/sn/account/amazonbi/dashboards/x"
    assert bs._wait_signed_in(FakePage(10**9), url, "PNAMA", lines.append, timeout_s=0.2) is False
    assert any("not signed in" in line for line in lines)


def _fake_playwright(monkeypatch, page):
    import playwright.sync_api as pw

    class Ctx:
        pages = [page]
        def close(self):
            pass

    class Chromium:
        def launch_persistent_context(self, **_):
            return Ctx()

    class P:
        chromium = Chromium()
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    monkeypatch.setattr(pw, "sync_playwright", lambda: P())


def test_signed_in_logs_accepted_message(monkeypatch):
    monkeypatch.setattr(bs, "POLL_S", 0)
    _fake_playwright(monkeypatch, FakePage(0))
    lines = []
    result = bs.ensure_signed_in(log=lines.append)
    assert result["ok"] is True and result["dsr"] is True
    assert any(bs.ACCEPTED_MSG in line for line in lines)


def test_not_confirmed_logs_warning_and_continues(monkeypatch):
    monkeypatch.setattr(bs, "POLL_S", 0)
    _fake_playwright(monkeypatch, FakePage(10**9))
    lines = []
    result = bs.ensure_signed_in(log=lines.append, timeout_s=0.2)
    assert result["ok"] is False and result["error"] is None
    assert any(bs.NOT_CONFIRMED_MSG in line for line in lines)


def test_never_raises_when_browser_cannot_start(monkeypatch):
    import playwright.sync_api as pw

    def boom():
        raise RuntimeError("no edge")
    monkeypatch.setattr(pw, "sync_playwright", boom)
    lines = []
    result = bs.ensure_signed_in(log=lines.append, timeout_s=1)
    assert result["ok"] is False and "no edge" in result["error"]
    assert any("continuing" in line for line in lines)
