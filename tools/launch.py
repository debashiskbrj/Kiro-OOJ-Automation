"""One-click launcher used by "Start OOJ Web.bat".

Owner & author: KABIDEBA (SWA India - Automation).

1. Shows the owner / support banner.
2. If the OOJ page is ALREADY running on this laptop, just opens it again (never starts a
   second server - two servers on one port cause confusing results).
3. Once every 7 days (or after a new version is installed) runs the weekly health check.
4. Starts the local web server exactly as before (127.0.0.1:8000) and opens the page.
"""
from __future__ import annotations

import subprocess
import sys
import urllib.request
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))

import about  # noqa: E402

PORT = 8000
URL = f"http://127.0.0.1:{PORT}/"


def server_already_running() -> bool:
    try:
        with urllib.request.urlopen(URL + "status", timeout=3) as resp:
            return resp.status == 200
    except Exception:
        return False


def main(argv: list[str]) -> int:
    for line in about.banner_lines():
        print(line)

    if "--check" in argv:
        import health_check
        return health_check.main()

    if server_already_running():
        print(f"  The OOJ page is already running on {URL}")
        print("  Opening it again (no second copy is started).")
        sys.path.insert(0, str(TOOLS / "web"))
        try:
            import server
            server._open_browser(URL)
        except Exception:
            import webbrowser
            webbrowser.open(URL, new=1)
        return 0

    if "--skip-health" not in argv:
        try:
            import health_check
            if health_check.is_due():
                print("  Weekly health check is due - running it now (1-2 minutes)...")
                result = health_check.run_check(echo=print)
                print(f"  HEALTH CHECK: {result['status']}   report: {result['report']}")
                if result["status"] != health_check.OK:
                    print("  Something needs attention - read the report (opening it now).")
                    print("  " + about.support_text())
                    try:
                        import os
                        os.startfile(result["report"])
                    except Exception:
                        pass
                print("=" * 64)
        except Exception as exc:  # the health check must never block the daily work
            print(f"  (health check skipped: {type(exc).__name__}: {str(exc)[:150]})")

    server_py = TOOLS / "web" / "server.py"
    return subprocess.call([sys.executable, str(server_py), "--port", str(PORT)], cwd=str(TOOLS.parent))


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        raise SystemExit(0)
