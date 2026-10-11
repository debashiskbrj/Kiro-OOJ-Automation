"""Offline tests for the one-time auto-setup + read-only environment checklist.

No real pip/network/mwinit/filesystem writes: the import check and the subprocess
runner are monkeypatched, and filesystem/path probes are patched. Follows the
import/monkeypatch style of ``test_web_controller.py``."""
import subprocess
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(TOOLS / "web"))

import auto_setup  # noqa: E402


class _FakeProc:
    def __init__(self, returncode=0, stdout="ok", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


# --------------------------------------------------------------- Feature A
def test_ensure_env_noop_when_all_present(monkeypatch):
    monkeypatch.setattr(auto_setup, "_missing_packages",
                        lambda: (list(auto_setup.REQUIRED_PACKAGES), []))
    # Edge browser already usable -> no playwright install either.
    monkeypatch.setattr(auto_setup, "_playwright_edge_ok", lambda: True)

    calls = []
    monkeypatch.setattr(auto_setup, "_pip_install",
                        lambda pkg: calls.append(pkg) or _FakeProc())
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no subprocess")))

    result = auto_setup.ensure_python_env(log=lambda m: None)

    assert calls == []  # no install attempted
    assert result["ran"] is False
    assert result["installed"] == []
    assert set(result["already_ok"]) == set(auto_setup.REQUIRED_PACKAGES)
    assert result["errors"] == []


def test_ensure_env_installs_exactly_one_missing(monkeypatch):
    monkeypatch.setattr(auto_setup, "_missing_packages", lambda: (["openpyxl", "playwright"], ["requests"]))
    monkeypatch.setattr(auto_setup, "_playwright_edge_ok", lambda: True)

    issued = []

    def fake_pip(pkg):
        issued.append([sys.executable, "-m", "pip", "install", pkg])
        return _FakeProc(returncode=0, stdout="installed requests")

    monkeypatch.setattr(auto_setup, "_pip_install", fake_pip)

    result = auto_setup.ensure_python_env(log=lambda m: None)

    assert issued == [[sys.executable, "-m", "pip", "install", "requests"]]
    assert result["ran"] is True
    assert result["installed"] == ["requests"]
    assert "requests" not in result["already_ok"]


def test_ensure_env_never_raises_on_install_error(monkeypatch):
    monkeypatch.setattr(auto_setup, "_missing_packages", lambda: ([], ["requests"]))
    monkeypatch.setattr(auto_setup, "_playwright_edge_ok", lambda: True)

    def boom(pkg):
        raise RuntimeError("pip exploded")

    monkeypatch.setattr(auto_setup, "_pip_install", boom)

    result = auto_setup.ensure_python_env(log=lambda m: None)

    assert isinstance(result, dict)
    assert result["installed"] == []
    assert any("requests" in e for e in result["errors"])


# --------------------------------------------------------------- Feature B
def test_checklist_all_ok_no_side_effects(monkeypatch):
    monkeypatch.setattr(auto_setup, "_missing_packages",
                        lambda: (list(auto_setup.REQUIRED_PACKAGES), []))
    monkeypatch.setattr(auto_setup, "_playwright_edge_ok", lambda: True)
    monkeypatch.setattr(auto_setup.Path, "exists", lambda self: True)

    import run_controller
    monkeypatch.setattr(run_controller, "midway_session_valid", lambda: True)

    # Guard: no install / subprocess may be invoked by the read-only checklist.
    monkeypatch.setattr(auto_setup, "_pip_install",
                        lambda pkg: (_ for _ in ()).throw(AssertionError("no install")))
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no subprocess")))

    result = auto_setup.environment_checklist()

    assert result["all_ok"] is True
    assert result["items"]
    for item in result["items"]:
        assert set(item.keys()) == {"name", "ok", "detail", "fix"}


def test_checklist_reports_missing_items(monkeypatch):
    # One package missing, Edge browser missing, Midway expired, everything else absent.
    monkeypatch.setattr(auto_setup, "_missing_packages", lambda: (["openpyxl", "requests"], ["playwright"]))
    monkeypatch.setattr(auto_setup, "_playwright_edge_ok", lambda: False)
    monkeypatch.setattr(auto_setup.Path, "exists", lambda self: False)

    import run_controller
    monkeypatch.setattr(run_controller, "midway_session_valid", lambda: False)

    result = auto_setup.environment_checklist()

    assert result["all_ok"] is False
    by_name = {item["name"]: item for item in result["items"]}
    assert by_name["Python package: playwright"]["ok"] is False
    assert by_name["Python package: openpyxl"]["ok"] is True
    assert by_name["Playwright Edge browser"]["ok"] is False
    assert by_name["Midway session"]["ok"] is False
    # Missing items carry a non-empty fix hint.
    assert by_name["Python package: playwright"]["fix"]
    assert by_name["Midway session"]["fix"]
