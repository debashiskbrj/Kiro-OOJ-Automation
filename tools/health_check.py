r"""Weekly health check ("bug-fix run") for the OOJ Process Automation.

Owner & author: KABIDEBA (SWA India - Automation).

Runs automatically from the launcher once every 7 days (and whenever a new version is
installed), or on demand with ``Run Health Check.bat``. No AI / LLM is involved.

What it does:
  * SAFE AUTO-FIXES: installs missing Python packages and the Playwright Edge browser
    (never upgrades/removes anything), installs pytest for the self-test if missing,
    clears stale Python bytecode caches.
  * CHECKS: Python version, Excel installed, the Excel template is intact (required
    sheets, DSR Data headers, Remark formula), the full built-in self-test suite, the
    machine checklist (mcscli, builder-mcp, Midway, Edge profile), free disk space.
  * REPORT: writes tools/health/health_<stamp>.txt. If something is wrong, it tells the
    user to send that report to the owner (KABIDEBA) for a correction.

It never touches run data, the operator's workbooks, or any Amazon system.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(TOOLS / "web"))

import about  # noqa: E402

HEALTH_DIR = TOOLS / "health"
LAST_CHECK = HEALTH_DIR / "last_check.json"
CHECK_EVERY_DAYS = 7
KEEP_REPORTS = 12
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

REQUIRED_SHEETS = ("Working Sheet", "Tech_Data", "SCC_(A,D,E,L)", "Eagle_EYE data", "DSR Data",
                   "Missort Data", "GCRS_Data", "GCRS Agentspaces ", "ADS Predicted Data")

OK, WARN, FAIL = "OK", "WARN", "FAIL"


def is_due(now: datetime | None = None) -> bool:
    """True when the last check is missing, older than 7 days, or was for another version."""
    now = now or datetime.now()
    try:
        data = json.loads(LAST_CHECK.read_text(encoding="utf-8"))
        when = datetime.fromisoformat(data["when"])
    except Exception:
        return True
    if data.get("version") != about.version():
        return True
    return now - when >= timedelta(days=CHECK_EVERY_DAYS)


def _run(cmd, timeout=900):
    try:
        p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout,
                           creationflags=NO_WINDOW, encoding="utf-8", errors="replace")
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return -1, f"timed out after {timeout}s"
    except Exception as exc:
        return -1, f"{type(exc).__name__}: {exc}"


# ------------------------------------------------------------------ individual steps
def step_python():
    v = sys.version_info
    detail = f"Python {v.major}.{v.minor}.{v.micro} ({sys.executable})"
    if v < (3, 11):
        return FAIL, detail, "Install Python 3.13 (Software Center / python.org) and start again"
    return OK, detail, ""


def step_packages():
    import auto_setup
    result = auto_setup.ensure_python_env(log=lambda m: None)
    fixed = result.get("installed") or []
    errors = result.get("errors") or []
    if errors:
        return FAIL, "install problems: " + "; ".join(e[:150] for e in errors), \
            "Check the network / proxy, then run the health check again"
    detail = "all required packages present"
    if fixed:
        detail += f" (auto-installed: {', '.join(fixed)})"
    return OK, detail, ""


def step_pytest():
    import importlib.util
    if importlib.util.find_spec("pytest") is not None:
        return OK, "pytest present", ""
    code, out = _run([sys.executable, "-m", "pip", "install", "pytest"], timeout=600)
    if code == 0:
        return OK, "pytest auto-installed for the self-test", ""
    return WARN, "could not install pytest: " + out.strip()[-200:], "Self-test skipped"


def step_clear_caches():
    removed = 0
    for base in (TOOLS, ROOT / "tests"):
        for cache in base.rglob("__pycache__"):
            if "captures" in cache.parts:
                continue
            shutil.rmtree(cache, ignore_errors=True)
            removed += 1
    return OK, f"cleared {removed} stale bytecode cache folder(s)", ""


def step_excel():
    try:
        import winreg
        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"Excel.Application"))
        return OK, "Microsoft Excel installed", ""
    except Exception:
        return FAIL, "Microsoft Excel not found", "Install Microsoft Excel (needed to calculate the workbook)"


def step_template():
    import fill_template as ft
    import openpyxl
    if not ft.TEMPLATE.exists():
        return FAIL, f"template missing: {ft.TEMPLATE.name}", "Re-extract the automation zip from the owner"
    wb = openpyxl.load_workbook(ft.TEMPLATE)
    try:
        missing = [s for s in REQUIRED_SHEETS if s not in wb.sheetnames]
        if missing:
            return FAIL, f"template sheets missing: {missing}", "Re-extract the automation zip from the owner"
        dsr = wb[ft.SHEET_DSR]
        if str(dsr["A1"].value).strip() != "tracking_id" or str(dsr["V1"].value).strip() != "zone":
            return FAIL, "DSR Data header changed (A1 must be tracking_id, V1 zone)", \
                "Re-extract the automation zip from the owner"
        p2 = wb[ft.SHEET_WORK]["P2"].value
        p2 = getattr(p2, "text", p2) or ""
        if 'IFERROR(AF2,"")' not in p2:
            return FAIL, "Working Sheet P2 remark formula changed", "Re-extract the automation zip from the owner"
    finally:
        wb.close()
    return OK, f"{ft.TEMPLATE.name} intact", ""


def step_selftest():
    import importlib.util
    if importlib.util.find_spec("pytest") is None:
        return WARN, "self-test skipped (pytest unavailable)", ""
    code, out = _run([sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider"], timeout=900)
    last = (out.strip().splitlines() or ["(no output)"])[-1]
    if code == 0:
        return OK, f"self-test passed: {last}", ""
    failed = [line for line in out.splitlines() if line.startswith(("FAILED", "ERROR"))][:15]
    return FAIL, f"self-test FAILED: {last}\n      " + "\n      ".join(failed), \
        "Send this report to the owner for a correction"


def step_machine():
    import auto_setup
    lines, worst = [], OK
    for item in auto_setup.environment_checklist()["items"]:
        if item["name"].startswith("Python package"):
            continue
        mark = "ok " if item["ok"] else "-- "
        lines.append(f"{mark}{item['name']}: {item['detail']}" + ("" if item["ok"] else f"  -> {item['fix']}"))
        if not item["ok"] and item["name"] in ("builder-mcp", "Excel template"):
            worst = FAIL
        elif not item["ok"] and worst == OK and item["name"] != "Midway session":
            worst = WARN
    return worst, "\n      ".join(lines), ("" if worst == OK else "Fix the items marked -- (see the guide)")


def step_disk():
    free_gb = shutil.disk_usage(ROOT).free / (1024 ** 3)
    if free_gb < 2:
        return WARN, f"only {free_gb:.1f} GB free", "Free some disk space"
    return OK, f"{free_gb:.0f} GB free", ""


def step_dmaic():
    """DMAIC Control phase: store new runs' numbers and report the control-chart status."""
    import dmaic_metrics
    dmaic_metrics.harvest()
    text = dmaic_metrics.summary_text()
    status = WARN if "OUT OF CONTROL" in text else OK
    fix = "Look at the out-of-control run(s); tell the owner if the cause is not known" if status == WARN else ""
    return status, text.replace("\n", "\n      "), fix


STEPS = [
    ("Python", step_python),
    ("Python packages (auto-fix)", step_packages),
    ("Self-test tool (auto-fix)", step_pytest),
    ("Stale caches (auto-fix)", step_clear_caches),
    ("Microsoft Excel", step_excel),
    ("Excel template", step_template),
    ("Built-in self-test", step_selftest),
    ("Machine checklist", step_machine),
    ("Disk space", step_disk),
    ("DMAIC control chart", step_dmaic),
]


def run_check(echo=print) -> dict:
    HEALTH_DIR.mkdir(parents=True, exist_ok=True)
    started = datetime.now()
    stamp = started.strftime("%Y%m%d_%H%M%S")
    report = HEALTH_DIR / f"health_{stamp}.txt"
    lines = about.banner_lines() + [
        "  WEEKLY HEALTH CHECK REPORT",
        f"  Date: {started:%Y-%m-%d %H:%M}   User: {os.environ.get('USERNAME', '?')}   "
        f"Machine: {os.environ.get('COMPUTERNAME', '?')}",
        "=" * 64,
    ]
    results = []
    for name, fn in STEPS:
        echo(f"  checking: {name} ...")
        try:
            status, detail, fix = fn()
        except Exception as exc:
            status, detail, fix = FAIL, f"{type(exc).__name__}: {str(exc)[:300]}", "Send this report to the owner"
        results.append((name, status, detail, fix))
        lines.append(f"[{status:4}] {name}: {detail}")
        if fix:
            lines.append(f"       fix: {fix}")
        echo(f"  [{status}] {name}")
    overall = FAIL if any(r[1] == FAIL for r in results) else WARN if any(r[1] == WARN for r in results) else OK
    lines += ["=" * 64, f"OVERALL: {overall}"]
    if overall != OK:
        lines += ["", "Something needs attention. If you cannot fix it with the hint above,",
                  "send THIS FILE to the owner for a correction:", "  " + about.support_text()]
    else:
        lines += ["", "All good. Nothing to do."]
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    LAST_CHECK.write_text(json.dumps({"when": started.isoformat(timespec="seconds"), "status": overall,
                                      "version": about.version(), "report": report.name}), encoding="utf-8")
    for old in sorted(HEALTH_DIR.glob("health_*.txt"))[:-KEEP_REPORTS]:
        try:
            old.unlink()
        except OSError:
            pass
    return {"status": overall, "report": str(report), "results": results}


def main():
    for line in about.banner_lines():
        print(line)
    print("  Weekly health check (takes 1-2 minutes)...")
    result = run_check()
    print("=" * 64)
    print(f"  HEALTH CHECK: {result['status']}")
    print(f"  Report: {result['report']}")
    if result["status"] != OK:
        print("  Please read the report. If you cannot fix it, send it to the owner:")
        print("  " + about.support_text())
        try:
            os.startfile(result["report"])  # opens in Notepad
        except Exception:
            pass
    return 0 if result["status"] != FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
