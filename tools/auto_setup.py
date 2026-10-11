r"""One-time environment auto-setup and a read-only preflight checklist.

A new associate who receives this project folder should be able to just restart the
server and get going. This module provides two capabilities, both of which are called
from the web runner so no manual step is required:

  * ``ensure_python_env`` (Feature A) — on a fresh server start, install ONLY the
    Python packages that are MISSING (never pin/upgrade/uninstall an already-installed
    package) and best-effort ensure the Playwright Edge browser is available. It is
    wrapped so it can NEVER raise to the caller; when everything is present it is a
    fast no-op that changes nothing.

  * ``environment_checklist`` (Feature B) — a purely READ-ONLY snapshot of the few
    things only a human can do (Midway login, Edge sign-in, system tools). It never
    installs anything and never launches ``mwinit``.

This module deliberately does NOT touch pipeline logic, the Excel template, docs,
tests, or anything under ``tools/captures``. It does not install Python itself,
mcscli, builder-mcp, the Midway client, or the Edge application.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

# The third-party Python packages the pipeline needs at runtime. Only these are
# auto-installed, and only when missing.
REQUIRED_PACKAGES = ("openpyxl", "playwright", "requests")

# System tool paths — these mirror the exact literals hard-coded in
# ``run_daily.preflight()`` so the checklist and the engine agree on where these live.
# (They are not centralised in ooj_paths.py; mirroring the literal here is lower-risk
# than editing the working preflight.)
MCSCLI_PATH = r"C:\Program Files\Common Files\AmazonMidwayClientSuite\bin\mcscli.exe"
BUILDER_MCP_PATH = r"C:\Users\kabideba\AppData\Local\Toolbox\bin\builder-mcp.exe"


def _missing_packages() -> tuple[list[str], list[str]]:
    """Split REQUIRED_PACKAGES into (already_ok, missing).

    Pure and side-effect free: it only checks whether each package is importable via
    ``importlib.util.find_spec`` — it does not import the package or install anything.
    """
    already_ok: list[str] = []
    missing: list[str] = []
    for pkg in REQUIRED_PACKAGES:
        try:
            found = importlib.util.find_spec(pkg) is not None
        except (ImportError, ValueError, ModuleNotFoundError):
            found = False
        (already_ok if found else missing).append(pkg)
    return already_ok, missing


def _pip_install(pkg: str) -> subprocess.CompletedProcess:
    """Install a single MISSING package. Never pins/upgrades/uninstalls anything."""
    return subprocess.run(
        [sys.executable, "-m", "pip", "install", pkg],
        capture_output=True,
        text=True,
    )


def _playwright_edge_ok() -> bool:
    """Best-effort, read-only probe for a usable Playwright Edge (msedge) browser.

    Returns False (never raises) if Playwright is not importable or the browser is not
    installed/launchable. Launching is cheap and is immediately closed.
    """
    try:
        if importlib.util.find_spec("playwright") is None:
            return False
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel="msedge")
            browser.close()
        return True
    except Exception:
        return False


def ensure_python_env(log=print) -> dict:
    """Install MISSING Python packages and best-effort ensure Playwright Edge (Feature A).

    Behaviour:
      * For each MISSING required package only, run ``pip install <pkg>`` and capture
        its output. Already-installed packages are left untouched (never pinned,
        upgraded, or uninstalled).
      * If Playwright is importable but the Edge browser is not usable, run a one-time
        ``playwright install msedge``. This is best-effort: any failure is recorded and
        never crashes.

    Returns ``{"ran", "installed", "already_ok", "actions", "errors"}`` where ``ran`` is
    True only if an install/playwright action was actually attempted.

    The ENTIRE body is wrapped so this can NEVER raise to the caller (mirrors the
    ``server.py`` ``_sanitize`` pattern). When everything is present it is a fast no-op
    (``ran=False``, empty ``installed``).
    """
    result = {
        "ran": False,
        "installed": [],
        "already_ok": [],
        "actions": [],
        "errors": [],
    }
    try:
        already_ok, missing = _missing_packages()
        result["already_ok"] = list(already_ok)

        for pkg in missing:
            result["ran"] = True
            log(f"installing missing package: {pkg}")
            try:
                proc = _pip_install(pkg)
                if proc.stdout:
                    result["actions"].append(proc.stdout.strip())
                if proc.returncode == 0:
                    result["installed"].append(pkg)
                    log(f"installed: {pkg}")
                else:
                    err = (proc.stderr or "").strip() or f"pip exited {proc.returncode}"
                    result["errors"].append(f"{pkg}: {err}")
                    log(f"install failed: {pkg} ({err[:120]})")
            except Exception as exc:  # a failed install must not crash startup
                result["errors"].append(f"{pkg}: {type(exc).__name__}: {exc}")
                log(f"install error: {pkg} ({type(exc).__name__})")

        # Best-effort Playwright Edge browser. Only attempt when Playwright is present
        # (either already there, or just installed above) and the browser is not usable.
        try:
            if importlib.util.find_spec("playwright") is not None and not _playwright_edge_ok():
                result["ran"] = True
                log("installing Playwright Edge (msedge) browser")
                proc = subprocess.run(
                    [sys.executable, "-m", "playwright", "install", "msedge"],
                    capture_output=True,
                    text=True,
                )
                if proc.stdout:
                    result["actions"].append(proc.stdout.strip())
                if proc.returncode == 0:
                    result["actions"].append("playwright edge: installed")
                else:
                    err = (proc.stderr or "").strip() or f"playwright exited {proc.returncode}"
                    result["errors"].append(f"playwright-edge: {err}")
        except Exception as exc:  # best-effort — never crash
            result["errors"].append(f"playwright-edge: {type(exc).__name__}: {exc}")
    except Exception as exc:  # final guard: ensure_python_env can NEVER raise
        result["errors"].append(f"auto_setup: {type(exc).__name__}: {exc}")
    return result


def _item(name: str, ok: bool, detail: str, fix: str) -> dict:
    return {"name": name, "ok": bool(ok), "detail": detail, "fix": fix}


def environment_checklist() -> dict:
    """Read-only snapshot of environment readiness (Feature B).

    Purely READ-ONLY: it never installs, never launches ``mwinit``, and has no side
    effects. Each item is ``{"name", "ok", "detail", "fix"}``; returns
    ``{"all_ok": bool, "items": [...]}``. Any per-item probe is wrapped so a failure
    yields ``ok=False`` with a detail rather than raising.
    """
    items: list[dict] = []

    # (a) Required Python packages — reuse the pure import check.
    try:
        already_ok, missing = _missing_packages()
    except Exception as exc:
        already_ok, missing = [], list(REQUIRED_PACKAGES)
        items.append(_item("Python packages", False, f"check failed: {exc}",
                           "Restart the server to auto-install missing packages"))
    else:
        for pkg in REQUIRED_PACKAGES:
            present = pkg in already_ok
            items.append(_item(
                f"Python package: {pkg}",
                present,
                "installed" if present else "missing",
                "" if present else "Restart the server; it auto-installs missing packages",
            ))

    # (b) Playwright Edge browser present (best-effort, read-only).
    try:
        edge_browser = _playwright_edge_ok()
    except Exception as exc:
        edge_browser = False
        items.append(_item("Playwright Edge browser", False, f"probe failed: {exc}",
                           "Run: py -3.13 -m playwright install msedge"))
    else:
        items.append(_item(
            "Playwright Edge browser",
            edge_browser,
            "available" if edge_browser else "not installed",
            "" if edge_browser else "Run: py -3.13 -m playwright install msedge",
        ))

    # (c) mcscli present.
    try:
        mcscli_ok = Path(MCSCLI_PATH).exists()
    except Exception as exc:
        mcscli_ok = False
        detail = f"check failed: {exc}"
    else:
        detail = "found" if mcscli_ok else "not found"
    items.append(_item(
        "mcscli (SIW)",
        mcscli_ok,
        detail,
        "" if mcscli_ok else "Install the Amazon Midway Client Suite (SIW mcscli)",
    ))

    # (d) builder-mcp present.
    try:
        builder_ok = Path(BUILDER_MCP_PATH).exists()
    except Exception as exc:
        builder_ok = False
        detail = f"check failed: {exc}"
    else:
        detail = "found" if builder_ok else "not found"
    items.append(_item(
        "builder-mcp",
        builder_ok,
        detail,
        "" if builder_ok else "Install builder-mcp (Amazon Toolbox)",
    ))

    # (e) Midway session valid — reuse the engine's detection; never launches mwinit.
    try:
        import run_controller  # noqa: E402  (web dir is on sys.path when wired)

        midway_ok = run_controller.midway_session_valid()
        detail = "session valid" if midway_ok else "session expired or absent"
    except Exception as exc:
        midway_ok = False
        detail = f"check failed: {exc}"
    items.append(_item(
        "Midway session",
        midway_ok,
        detail,
        "" if midway_ok else "Run: mwinit -o  (or use the Midway login button)",
    ))

    # (f) Edge automation profile exists.
    try:
        import ooj_paths  # noqa: E402

        profile_ok = ooj_paths.EDGE_PROFILE.exists()
        detail = "found" if profile_ok else f"not found: {ooj_paths.EDGE_PROFILE}"
    except Exception as exc:
        profile_ok = False
        detail = f"check failed: {exc}"
    items.append(_item(
        "Edge automation profile",
        profile_ok,
        detail,
        "" if profile_ok else "Sign in once in the automation Edge profile (SCC/PNAMA/DSR)",
    ))

    # (g) Excel template present.
    try:
        import fill_template  # noqa: E402

        template_ok = fill_template.TEMPLATE.exists()
        detail = "found" if template_ok else f"not found: {fill_template.TEMPLATE}"
    except Exception as exc:
        template_ok = False
        detail = f"check failed: {exc}"
    items.append(_item(
        "Excel template",
        template_ok,
        detail,
        "" if template_ok else "Restore the template under Docs/ (ships with the project)",
    ))

    return {"all_ok": all(item["ok"] for item in items), "items": items}
