r"""Local one-click web runner for the OOJ pipeline.

Serves a single page (index.html) and a handful of JSON endpoints that drive the
proven ``run_daily.py`` pipeline. Stdlib only (no third-party packages, no network
egress, no LLM). Binds to 127.0.0.1 so it is never exposed on the network.

Endpoints
  GET  /              -> index.html (+ app.js, style.css as static assets)
  GET  /preflight     -> {midway_ok, edge_profile_busy, ready, ...}
  POST /midway        -> launches `mwinit -o` in a new console for PIN entry
  POST /run           -> {started, ...}   body: {"ads_approved": bool}
  GET  /status        -> structured run status (polled by the page)
  GET  /table         -> current enriched rows (JSON) once available
  GET  /download      -> the verified workbook (blocked until publishable)
  POST /restart       -> stops any run, relaunches a fresh server on the same port

Run:  py -3.13 tools\web\server.py  [--port 8765]
"""
from __future__ import annotations

import argparse
import base64
import binascii
import csv
import io
import json
import os
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import run_controller as rc
import post_comments_controller as pcc

HERE = Path(__file__).resolve().parent
# tools/ (the parent of tools/web) holds the shared pipeline modules; ensure it is
# importable so we can load the sanitizer.
sys.path.insert(0, str(HERE.parent))
import sanitize_captures  # noqa: E402  (path set up just above)
import auto_setup  # noqa: E402  (path set up just above)
# Set in main() so the /restart endpoint knows which port to relaunch on and can stop
# the running server cleanly.
ACTIVE_PORT = 8000
SERVER = None


def _open_browser(url: str) -> None:
    """Open the page in a SEPARATE, standalone browser window containing only this tab.

    Operator preference: a dedicated window for the runner, not a new tab inside the
    operator's existing browsing window. We use the browser's "app mode" (--app=<url>),
    which opens a chrome-less, single-page window isolated from the normal browser windows.
    A unique --user-data-dir makes the app window its own instance so it never merges into
    an already-open Edge/Chrome window. Falls back to webbrowser if neither is found.
    """
    import shutil
    import tempfile

    candidates = []
    if os.name == "nt":
        program_files = [os.environ.get("PROGRAMFILES", r"C:\Program Files"),
                         os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
                         os.environ.get("LOCALAPPDATA", "")]
        edge = [os.path.join(p, r"Microsoft\Edge\Application\msedge.exe") for p in program_files if p]
        chrome = [os.path.join(p, r"Google\Chrome\Application\chrome.exe") for p in program_files if p]
        candidates = edge + chrome
    # Also try PATH-resolved names (covers non-standard installs / other OSes).
    for name in ("msedge", "chrome", "google-chrome", "chromium"):
        found = shutil.which(name)
        if found:
            candidates.append(found)

    app_profile = os.path.join(tempfile.gettempdir(), "ooj_web_app_profile")
    # De-dup while preserving order; only keep full paths that exist (which()-resolved
    # entries are already full, existing paths).
    seen = set()
    for exe in candidates:
        if not exe or exe in seen or not os.path.exists(exe):
            continue
        seen.add(exe)
        try:
            subprocess.Popen(
                [exe, f"--app={url}", f"--user-data-dir={app_profile}", "--new-window"],
                close_fds=True,
            )
            return
        except OSError:
            continue
    # Fallback: best-effort new window via the default browser.
    try:
        webbrowser.open(url, new=1)
    except OSError:
        pass


def _sanitize(reason: str) -> None:
    """Remove output-corrupting scratch (browser downloads, partial/temp files) so a run
    can never pick up stale/partial data. Runs at server startup and on restart."""
    try:
        sanitize_captures.sanitize(log=lambda m: print(f"[sanitize/{reason}] {m}"))
    except Exception as exc:  # never let cleanup block the server from serving
        print(f"[sanitize/{reason}] skipped ({type(exc).__name__}: {str(exc)[:120]})")


def _auto_setup() -> None:
    """One-time environment setup: install only MISSING Python packages and best-effort
    ensure the Playwright Edge browser so a new associate can just restart the server.

    Mirrors ``_sanitize`` — fully wrapped so it can NEVER block the server from serving.
    Already-installed packages are never pinned/upgraded/uninstalled; when everything is
    present it is a fast no-op that changes nothing."""
    try:
        auto_setup.ensure_python_env(log=lambda m: print(f"[setup] {m}"))
    except Exception as exc:  # never let setup block the server from serving
        print(f"[setup] skipped ({type(exc).__name__}: {str(exc)[:120]})")


def _relaunch_server(port: int) -> None:
    """Spawn a fresh, detached server process on the same port, then stop this one.

    A separate process waits a moment for this server to release the socket, then starts
    ``server.py`` again. The page polls ``/preflight`` and reconnects once it is back up.
    Any in-progress pipeline run is stopped first so nothing is left half-running.
    """
    try:
        rc.CONTROLLER.stop_run()
    except Exception:
        pass
    _sanitize("restart")
    python = sys.executable
    # A tiny inline waiter: sleep so the old socket is freed, then exec the server.
    # Relaunch WITHOUT --no-open so the restarted server opens the page in a fresh browser
    # window (operator preference: a restart should pop a new browser, not rely on the
    # existing tab reconnecting).
    waiter = (
        "import time,subprocess,sys;"
        "time.sleep(2);"
        f"subprocess.Popen([r'{python}', '-X', 'utf8', r'{HERE / 'server.py'}',"
        f" '--port', '{port}'])"
    )
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "DETACHED_PROCESS", 0)
    try:
        subprocess.Popen([python, "-c", waiter], creationflags=creationflags, close_fds=True)
    except OSError:
        return
    # Give the response a beat to flush to the browser, then shut this server down.
    def _shutdown():
        time.sleep(1)
        if SERVER is not None:
            SERVER.shutdown()
    threading.Thread(target=_shutdown, daemon=True).start()
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "application/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}


class Handler(BaseHTTPRequestHandler):
    # Quieter logging; one line per request is plenty for a local tool.
    def log_message(self, fmt, *args):  # noqa: D401
        print(f"[web] {self.address_string()} {fmt % args}")

    # ------------------------------------------------------------- helpers
    def _send_json(self, payload, status=200):
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_static(self, filename, content_type):
        path = HERE / filename
        if not path.exists():
            self.send_error(404, f"{filename} not found")
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    # ------------------------------------------------------------- GET
    def do_GET(self):
        route = self.path.split("?", 1)[0]
        if route in STATIC:
            self._send_static(*STATIC[route])
            return
        if route == "/preflight":
            self._send_json(rc.preflight_state())
            return
        if route == "/status":
            self._send_json(rc.CONTROLLER.get_status())
            return
        if route == "/table":
            self._send_json(self._current_table())
            return
        if route == "/log":
            self._send_json(self._full_log())
            return
        if route == "/download":
            self._serve_download()
            return
        if route == "/source-download":
            self._serve_source_download()
            return
        if route == "/post-comments/status":
            self._send_json(pcc.CONTROLLER.get_status())
            return
        if route == "/post-comments/report-download":
            self._serve_report_download()
            return
        self.send_error(404, "unknown route")

    # ------------------------------------------------------------- POST
    def do_POST(self):
        route = self.path.split("?", 1)[0]
        if route == "/midway":
            self._send_json(rc.launch_midway_prompt())
            return
        if route == "/run":
            body = self._read_body()
            result = rc.CONTROLLER.start_run(
                bool(body.get("ads_approved")),
                allow_source_failures=bool(body.get("allow_source_failures")),
            )
            self._send_json(result, status=200 if result.get("started") else 409)
            return
        if route == "/stop":
            result = rc.CONTROLLER.stop_run()
            self._send_json(result, status=200 if result.get("stopped") else 409)
            return
        if route == "/restart":
            # Acknowledge first, then relaunch a fresh server on the same port and stop
            # this one. The page shows a "Restarting…" message and reconnects when ready.
            self._send_json({"restarting": True, "port": ACTIVE_PORT})
            _relaunch_server(ACTIVE_PORT)
            return
        if route == "/scc/upload":
            self._scc_upload()
            return
        if route == "/post-comments/upload":
            self._post_comments_upload()
            return
        if route == "/post-comments/confirm":
            result = pcc.CONTROLLER.confirm_and_post()
            self._send_json(result, status=200 if result.get("started") else 409)
            return
        if route == "/post-comments/retry-failed":
            result = pcc.CONTROLLER.retry_failed()
            self._send_json(result, status=200 if result.get("started") else 409)
            return
        if route == "/post-comments/stop":
            result = pcc.CONTROLLER.stop()
            self._send_json(result, status=200 if result.get("stopped") else 409)
            return
        self.send_error(404, "unknown route")

    # ------------------------------------------------------------- feature impls
    def _current_table(self) -> dict:
        status = rc.CONTROLLER.get_status(log_tail=1)
        run_dir = status.get("run_dir")
        if not run_dir:
            return {"rows": [], "columns": [], "count": 0}
        run_path = Path(run_dir)
        stamp = status.get("stamp")
        # Prefer the fully enriched working-sheet CSV; fall back to Tech_Data early on.
        candidates = []
        if stamp:
            candidates.append(run_path / f"working_sheet_{stamp}.csv")
        candidates += sorted(run_path.glob("working_sheet_*.csv"))
        candidates.append(run_path / "tech_data.csv")
        for candidate in candidates:
            if candidate.exists():
                return _read_csv_as_table(candidate, limit=2000)
        return {"rows": [], "columns": [], "count": 0}

    def _full_log(self) -> dict:
        status = rc.CONTROLLER.get_status(log_tail=1)
        run_dir = status.get("run_dir")
        if not run_dir:
            return {"text": "", "lines": 0}
        log_path = Path(run_dir) / "run.log"
        if not log_path.exists():
            return {"text": "", "lines": 0}
        text = log_path.read_text(encoding="utf-8", errors="replace")
        return {"text": text, "lines": text.count("\n"), "stamp": status.get("stamp")}

    def _serve_download(self):
        try:
            target = rc.CONTROLLER.desktop_path_for_current()
        except RuntimeError as exc:
            self._send_json({"error": str(exc)}, status=409)
            return
        if target is None or not Path(target).exists():
            self._send_json(
                {"error": "Workbook is not ready yet or the run did not pass verification"},
                status=409,
            )
            return
        data = Path(target).read_bytes()
        self.send_response(200)
        self.send_header(
            "Content-Type",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.send_header("Content-Disposition", f'attachment; filename="{Path(target).name}"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _scc_upload(self):
        """Accept {filename, content_base64} for the manually-exported SCC CSV.

        SCC's automated browser lane is retired (SCC now blocks it; scc_client.py was
        removed). The operator uploads SCC's native export here instead; run_controller
        validates it and start_run() passes it to run_daily.py via --scc-csv.
        """
        body = self._read_body()
        filename = body.get("filename") or ""
        content_b64 = body.get("content_base64") or ""
        if not content_b64:
            self._send_json({"uploaded": False, "reason": "No file content received"}, status=409)
            return
        try:
            raw = base64.b64decode(content_b64, validate=True)
        except (ValueError, binascii.Error):
            self._send_json({"uploaded": False, "reason": "Invalid file encoding"}, status=409)
            return
        result = rc.CONTROLLER.upload_scc_csv(raw, filename)
        if result.get("uploaded"):
            # Server-triggers-continue: a validated upload immediately resumes the paused
            # run (Phase B) in the same call, so the frontend only needs this one request.
            result["continue"] = rc.CONTROLLER.continue_after_scc()
        self._send_json(result, status=200 if result.get("uploaded") else 409)

    def _post_comments_upload(self):
        """Accept {filename, content_base64}, decode, and start a dry-run preview."""
        body = self._read_body()
        filename = body.get("filename") or ""
        content_b64 = body.get("content_base64") or ""
        if not content_b64:
            self._send_json({"started": False, "reason": "No file content received"}, status=409)
            return
        try:
            raw = base64.b64decode(content_b64, validate=True)
        except (ValueError, binascii.Error):
            self._send_json({"started": False, "reason": "Invalid file encoding"}, status=409)
            return
        result = pcc.CONTROLLER.upload_and_preview(raw, filename)
        self._send_json(result, status=200 if result.get("started") else 409)

    def _serve_report_download(self):
        target = pcc.CONTROLLER.report_path()
        if target is None or not Path(target).exists():
            self._send_json({"error": "No post-comments report exists yet"}, status=409)
            return
        data = Path(target).read_bytes()
        self.send_response(200)
        self.send_header(
            "Content-Type",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.send_header("Content-Disposition", f'attachment; filename="{Path(target).name}"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_source_download(self):
        query = urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
        name = (query.get("name") or [""])[0]
        status = rc.CONTROLLER.get_status(log_tail=1)
        run_dir = status.get("run_dir")
        if not run_dir:
            self._send_json({"error": "No active run"}, status=409)
            return
        path = rc.source_csv_path(Path(run_dir), name)
        if path is None:
            self._send_json({"error": f"No data for source '{name}' yet"}, status=409)
            return
        data = path.read_bytes()
        stamp = status.get("stamp") or "run"
        filename = f"{name}_{stamp}.csv"
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _read_csv_as_table(path: Path, limit: int) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return {"rows": [], "columns": [], "count": 0}
    header, body = rows[0], rows[1:]
    return {"columns": header, "rows": body[:limit], "count": len(body)}


def main():
    parser = argparse.ArgumentParser(description="Local one-click OOJ web runner")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-open", action="store_true", help="do not open a browser")
    args = parser.parse_args()

    # Some corporate machines block binding on certain ports (WinError 10013) even
    # when the port is free, so fall back through a few candidates.
    candidates = [args.port, 8000, 8080, 8090, 3000, 5000, 9000]
    server = None
    for port in dict.fromkeys(candidates):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            args.port = port
            break
        except (PermissionError, OSError) as exc:
            print(f"port {port} unavailable ({exc}); trying next")
    if server is None:
        raise SystemExit("No usable local port found; check firewall/port policy")
    global ACTIVE_PORT, SERVER
    ACTIVE_PORT = args.port
    SERVER = server
    url = f"http://127.0.0.1:{args.port}/"
    print("=" * 60)
    print("  OOJ Process Automation")
    print("  Designed & built by KABIDEBA  (SWA India - Automation)")
    print("=" * 60)
    print(f"OOJ web runner on {url}  (Ctrl+C to stop)")
    # Daily protection: clear output-corrupting scratch before the first run can start.
    _sanitize("startup")
    # One-time environment setup: auto-install any MISSING Python packages so a new
    # associate just needs to restart the server. Runs exactly once per fresh start
    # (the /restart relauncher spawns a brand-new server.py whose own main() runs this).
    _auto_setup()
    if not args.no_open:
        # Open a SEPARATE standalone window (app mode) containing only this page, so a
        # fresh start or a restart gives the operator a dedicated runner window rather than
        # a tab in their existing browser.
        _open_browser(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
        server.shutdown()


if __name__ == "__main__":
    main()
