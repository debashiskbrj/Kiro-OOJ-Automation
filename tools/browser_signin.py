"""Browser sign-in check for the QuickSight lanes (PNAMA + DSR), run at Start.

Why: the terminal Midway step (``mwinit -o``) only covers SIM-T, EagleEye and GCRS
(mcscli). PNAMA and DSR use the automation Edge profile's OWN QuickSight sign-in, which
is lost after a laptop restart. Without this check the first run after a restart reaches
PNAMA much later, sits on a login page nobody is watching, and fails.

What it does (read-only; no data is fetched):
  1. opens the automation Edge profile on the PNAMA dashboard and brings it to the front;
  2. already signed in -> the dashboard loads, the window closes by itself;
  3. login page -> waits (up to SIGNIN_TIMEOUT_S) for the operator's PIN + YubiKey,
     then closes by itself as soon as the dashboard loads;
  4. repeats the check for the DSR dashboard (same QuickSight account, normally instant).

It never raises: the caller gets a result dict and the run continues either way
(PNAMA/DSR keep their own login wait, exactly as before).

Usage (manual check):  py -3.13 browser_signin.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))

SIGNIN_TIMEOUT_S = 600   # operator is at the laptop (just clicked Start)
POLL_S = 2
LOGIN_MARKERS = ("midway", "federate", "/sn/auth/")  # same markers as pnama_export


def classify_url(url: str) -> str:
    """'login' for a sign-in page, 'dashboard' for a QuickSight dashboard, else 'other'."""
    low = (url or "").lower()
    if any(marker in low for marker in LOGIN_MARKERS):
        return "login"
    if "quicksight.aws.amazon.com" in low and "/sn/account/" in low:
        return "dashboard"
    return "other"


def _dashboard_ready(page, tab_name: str = "") -> bool:
    """Same dashboard markers pnama_export.open_dashboard uses."""
    try:
        if page.locator("[role='tab']").count() > 0:
            return True
        if tab_name:
            body = page.locator("body").inner_text(timeout=5_000)
            return tab_name.lower() in body.lower()
    except Exception:
        pass
    return False


def _wait_signed_in(page, url: str, name: str, log, timeout_s: float,
                    tab_name: str = "") -> bool:
    try:
        page.goto(url, timeout=90_000, wait_until="domcontentloaded")
    except Exception as exc:  # AEA/SSO redirects interrupt the first navigation
        log(f"browser sign-in: {name} navigation interrupted ({str(exc)[:70]}); waiting")
    try:
        page.bring_to_front()
    except Exception:
        pass
    deadline = time.time() + timeout_s
    asked = False
    renavigated = 0
    while time.time() < deadline:
        kind = classify_url(page.url)
        if kind == "login":
            if not asked:
                log(f"browser sign-in: {name} needs Midway — enter your PIN and touch the "
                    "YubiKey in the Edge window that just opened")
                asked = True
                try:
                    page.bring_to_front()
                except Exception:
                    pass
        elif kind == "dashboard" and _dashboard_ready(page, tab_name):
            log(f"browser sign-in: {name} signed in")
            return True
        elif page.url == "about:blank" and renavigated < 3:
            renavigated += 1
            try:
                page.goto(url, timeout=90_000, wait_until="domcontentloaded")
            except Exception:
                pass
        time.sleep(POLL_S)
    log(f"browser sign-in: {name} not signed in after {int(timeout_s)} s")
    return False


def ensure_signed_in(log=print, timeout_s: float = SIGNIN_TIMEOUT_S) -> dict:
    """Open the automation Edge profile, make sure PNAMA + DSR are signed in, close it."""
    result = {"ok": False, "pnama": False, "dsr": False, "error": None}
    try:
        from playwright.sync_api import sync_playwright

        import dsr_export
        import pnama_export
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        log(f"browser sign-in skipped: {result['error']}")
        return result
    log("browser sign-in: opening the automation Edge window to check PNAMA / DSR sign-in")
    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=pnama_export.PROFILE_DIR, channel="msedge", headless=False,
                viewport={"width": 1400, "height": 900},
                ignore_default_args=["--disable-extensions",
                                     "--disable-component-extensions-with-background-pages",
                                     "--disable-component-update",
                                     "--disable-background-networking"],
                timeout=90_000,
            )
            try:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                start = time.time()
                result["pnama"] = _wait_signed_in(page, pnama_export.DASHBOARD_URL, "PNAMA",
                                                  log, timeout_s, pnama_export.TARGET_TAB)
                if result["pnama"]:
                    left = max(60.0, timeout_s - (time.time() - start))
                    result["dsr"] = _wait_signed_in(page, dsr_export.DASHBOARD_URL, "DSR",
                                                    log, left, dsr_export.TARGET_TAB)
            finally:
                try:
                    ctx.close()
                except Exception:
                    pass
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        log(f"browser sign-in check failed: {result['error']}")
    result["ok"] = bool(result["pnama"] and result["dsr"])
    log("browser sign-in: done, window closed — continuing" if result["ok"] else
        "browser sign-in: NOT confirmed — continuing; PNAMA/DSR will ask again in the Edge window")
    return result


if __name__ == "__main__":
    sys.exit(0 if ensure_signed_in()["ok"] else 1)
