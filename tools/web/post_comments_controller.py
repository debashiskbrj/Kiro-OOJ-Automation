r"""Controller for the gated post-comments feature.

Mirrors ``run_controller.RunController``'s shape: a single ``threading.Lock``-guarded
instance that owns at most one post-comments subprocess at a time, spawns
``py -3.13 -X utf8 post_comments_runner.py`` with stdout piped to a daemon drain thread,
captures the ``RUN_DIR=`` pointer the runner prints, and exposes
``upload_and_preview``/``confirm_and_post``/``retry_failed``/``stop``/``get_status``.

Safety gate (never loosened):

  * Real posting (``confirm_and_post`` / ``retry_failed``) is blocked unless a prior
    DRY-RUN of the CURRENT uploaded file completed successfully (its
    ``dry_run_summary.json`` exists and is newer than the uploaded file's mtime — a
    stale summary for a different/older file does not count).
  * It is blocked while any post-comments subprocess is already running.
  * It is blocked while the main pipeline is running on the shared Edge profile
    (``run_controller.CONTROLLER.is_running()``), because the single Edge automation
    profile must never be opened by two Playwright sessions at once.

Every public method returns a dict and never raises on a blocked precondition.
"""
from __future__ import annotations

import base64
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import run_controller as rc

TOOLS = Path(__file__).resolve().parent.parent
RUNNER = TOOLS / "post_comments_runner.py"
PC_ROOT = TOOLS / "captures" / "post_comments"
UPLOADS = PC_ROOT / "uploads"
MAX_REPORT_ROWS = 2000


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")


def _safe_filename(name: str) -> str:
    """Keep only the base name and strip path separators so an upload can't escape."""
    return os.path.basename(name).replace("\\", "_").replace("/", "_")


class PostCommentsController:
    """Owns at most one active post-comments subprocess at a time."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None
        self._mode: str | None = None
        self._out_dir: Path | None = None
        self._log_path: Path | None = None
        self._uploaded_path: Path | None = None
        self._uploaded_filename: str | None = None
        self._last_out_dir: Path | None = None
        self._stopped: bool = False

    # --------------------------------------------------------------- lifecycle
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _pipeline_running(self) -> bool:
        """True when the main pipeline holds the shared Edge profile."""
        try:
            return bool(rc.CONTROLLER.is_running())
        except Exception:
            # If we cannot tell, assume busy — never risk a double Edge session.
            return True

    def _spawn(self, mode: str, out_dir: Path) -> None:
        """Spawn the runner for ``mode`` writing under ``out_dir`` (caller holds lock)."""
        out_dir.mkdir(parents=True, exist_ok=True)
        self._log_path = out_dir / "run.log"
        cmd = [
            sys.executable, "-X", "utf8", str(RUNNER),
            "--input", str(self._uploaded_path),
            "--mode", mode,
            "--out-dir", str(out_dir),
        ]
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        self._mode = mode
        self._out_dir = out_dir
        self._last_out_dir = out_dir
        self._stopped = False
        self._proc = subprocess.Popen(
            cmd,
            cwd=str(TOOLS),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            creationflags=creationflags,
        )
        self._reader = threading.Thread(target=self._drain_stdout, daemon=True)
        self._reader.start()

    def _drain_stdout(self) -> None:
        """Capture the RUN_DIR pointer and tail everything into run.log."""
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        log_path = self._log_path
        for raw in proc.stdout:
            line = raw.rstrip("\n")
            stripped = line.strip()
            if stripped.startswith("RUN_DIR=") and self._out_dir is None:
                self._out_dir = Path(stripped.split("=", 1)[1])
                self._last_out_dir = self._out_dir
            if log_path is not None:
                try:
                    with open(log_path, "a", encoding="utf-8") as fh:
                        fh.write(line + "\n")
                except OSError:
                    pass

    # ----------------------------------------------------------------- upload
    def upload_and_preview(self, file_bytes: bytes, filename: str) -> dict:
        """Save the uploaded workbook and spawn a dry-run preview of it."""
        safe = _safe_filename(filename or "")
        if not safe.lower().endswith(".xlsx"):
            return {"started": False, "reason": "Upload must be a .xlsx file"}
        with self._lock:
            if self.is_running():
                return {
                    "started": False,
                    "reason": "A post-comments task is already running",
                }
            if self._pipeline_running():
                return {
                    "started": False,
                    "reason": "The main pipeline is running on the shared Edge profile; "
                    "wait for it to finish",
                }
            UPLOADS.mkdir(parents=True, exist_ok=True)
            stamp = _stamp()
            saved = UPLOADS / f"{stamp}_{safe}"
            try:
                saved.write_bytes(file_bytes)
            except OSError as exc:
                return {"started": False, "reason": f"Could not save upload: {exc}"}
            self._uploaded_path = saved
            self._uploaded_filename = safe
            out_dir = PC_ROOT / stamp
            self._out_dir = None  # let the RUN_DIR pointer / spawn set it
            self._spawn("dry-run", out_dir)
            return {"started": True, "filename": safe, "out_dir": str(out_dir)}

    # ---------------------------------------------------------- dry-run gate
    def _dry_run_ready(self) -> tuple[bool, str]:
        """True only when a successful dry-run of the CURRENT upload exists.

        Mirrors RunController._resolve_run_dir's started_at staleness guard: the
        summary must be newer than the uploaded file's mtime so a leftover summary
        for an older/different file cannot authorise posting.
        """
        if self._uploaded_path is None or self._last_out_dir is None:
            return False, "Upload a file and run the preview first"
        summary = self._last_out_dir / "dry_run_summary.json"
        if not summary.exists():
            return False, "No successful preview for the current upload yet"
        try:
            upload_mtime = self._uploaded_path.stat().st_mtime
            summary_mtime = summary.stat().st_mtime
        except OSError:
            return False, "Could not verify the preview is current"
        if summary_mtime < upload_mtime - 2:
            return False, "The preview is stale; re-run the preview for this file"
        return True, ""

    # ----------------------------------------------------------- confirm/post
    def confirm_and_post(self) -> dict:
        """Spawn the real posting run — only when every precondition passes."""
        with self._lock:
            if self.is_running():
                return {"started": False, "reason": "A post-comments task is already running"}
            if self._pipeline_running():
                return {
                    "started": False,
                    "reason": "The main pipeline is running on the shared Edge profile; "
                    "wait for it to finish",
                }
            ready, reason = self._dry_run_ready()
            if not ready:
                return {"started": False, "reason": reason}
            self._spawn("confirmed", self._last_out_dir)
            return {"started": True, "out_dir": str(self._last_out_dir)}

    def retry_failed(self) -> dict:
        """Re-run only the failed urls from the current out-dir's report."""
        with self._lock:
            if self.is_running():
                return {"started": False, "reason": "A post-comments task is already running"}
            if self._pipeline_running():
                return {
                    "started": False,
                    "reason": "The main pipeline is running on the shared Edge profile; "
                    "wait for it to finish",
                }
            if self._last_out_dir is None:
                return {"started": False, "reason": "No post-comments run to retry"}
            report = self._last_out_dir / "post_comments_report.xlsx"
            if not report.exists():
                return {"started": False, "reason": "No report to retry yet"}
            if not self._report_has_failures(report):
                return {"started": False, "reason": "No failed rows to retry"}
            self._spawn("retry-failed", self._last_out_dir)
            return {"started": True, "out_dir": str(self._last_out_dir)}

    # ------------------------------------------------------------------- stop
    def stop(self) -> dict:
        """Abort the active post-comments subprocess and its tree."""
        with self._lock:
            if not self.is_running():
                return {"stopped": False, "reason": "No post-comments task is running"}
            pid = self._proc.pid
            try:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(pid), "/T", "/F"],
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                else:
                    self._proc.terminate()
            except Exception as exc:
                return {"stopped": False, "reason": f"could not stop task: {exc}"}
            try:
                self._proc.wait(timeout=15)
            except Exception:
                pass
            self._stopped = True
            return {"stopped": True}

    # ----------------------------------------------------------------- status
    def _read_summary(self):
        if self._last_out_dir is None:
            return None
        path = self._last_out_dir / "dry_run_summary.json"
        if not path.exists():
            return None
        try:
            import json

            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    @staticmethod
    def _report_has_failures(report: Path) -> bool:
        try:
            import pandas as pd

            df = pd.read_excel(report)
        except Exception:
            return False
        if "Status" not in df.columns:
            return False
        return bool(df["Status"].astype(str).str.startswith("Failed").any())

    def _report_rows(self):
        if self._last_out_dir is None:
            return []
        report = self._last_out_dir / "post_comments_report.xlsx"
        if not report.exists():
            return []
        try:
            import pandas as pd

            df = pd.read_excel(report)
        except Exception:
            return []
        return df.head(MAX_REPORT_ROWS).to_dict("records")

    def get_status(self, log_tail: int = 40) -> dict:
        running = self.is_running()
        exit_code = None if running or self._proc is None else self._proc.returncode
        out_dir = self._last_out_dir
        log_tail_lines: list[str] = []
        if self._log_path and self._log_path.exists():
            lines = self._log_path.read_text(encoding="utf-8", errors="replace").splitlines()
            log_tail_lines = lines[-log_tail:]
        report = out_dir / "post_comments_report.xlsx" if out_dir else None
        report_available = bool(report and report.exists())
        ready, _reason = self._dry_run_ready()
        return {
            "running": running,
            "stopped": self._stopped and not running,
            "mode": self._mode if running else (self._mode if self._mode else None),
            "exit_code": exit_code,
            "out_dir": str(out_dir) if out_dir else None,
            "uploaded_filename": self._uploaded_filename,
            "dry_run_ready": ready,
            "log_tail": log_tail_lines,
            "dry_run_summary": self._read_summary(),
            "report_available": report_available,
            "report_rows": self._report_rows() if report_available else [],
        }

    def report_path(self) -> Path | None:
        if self._last_out_dir is None:
            return None
        report = self._last_out_dir / "post_comments_report.xlsx"
        return report if report.exists() else None


# One process-wide controller instance for the server to share.
CONTROLLER = PostCommentsController()
