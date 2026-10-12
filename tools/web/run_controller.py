r"""Controller for the local one-click OOJ web runner.

This module is a *supervisor* around the proven pipeline in ``tools\run_daily.py``.
It never re-implements pipeline logic:

  * ``start_run``     spawns ``run_daily.py`` as a killable subprocess and captures
                      the ``RUN_DIR=`` pointer it prints as its first stdout line.
  * ``get_status``    tails that run's ``run.log`` and parses ``summary.json`` into a
                      structured, JSON-serialisable status for the web page.
  * ``is_publishable`` reproduces the engine's own publication/accuracy gate.
  * ``publish_to_desktop`` copies the verified workbook to the Desktop, and refuses
                      to do so unless the run is publishable.

There is no LLM anywhere in this path, and every source-system call happens inside
``run_daily.py`` exactly as it does from the command line.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent
RUNS = TOOLS / "captures" / "runs"
RUN_DAILY = TOOLS / "run_daily.py"
DESKTOP = Path.home() / "Desktop"

# Required lanes must all succeed for a full, publishable (100%-accurate) run.
REQUIRED_LANES = ("gcrs", "eagle", "scc", "pnama", "dsr", "ads", "polygon")
# The per-source cards shown on the page, in pipeline order (SCC before EagleEye).
SOURCE_ORDER = ("tickets", "scc", "eagle", "pnama", "dsr", "gcrs", "polygon", "ads")

# Each lane writes its own CSV into the run directory. This maps a source card to that
# file so the page can offer a per-step download once the step has produced data.
SOURCE_CSV = {
    "tickets": "tech_data.csv",
    "eagle": "eagle.csv",
    "scc": "scc.csv",
    "pnama": "pnama.csv",
    "dsr": "dsr.csv",
    "gcrs": "gcrs.csv",
    "polygon": "polygon.csv",
    "ads": "ads.csv",
}


# Required columns in a manually-uploaded SCC export CSV (same shape the retired
# automated SCC lane used to produce — see tools/run_daily.py::load_scc_csv).
SCC_REQUIRED_COLUMNS = ["Tracking ID", "State", "Destination", "Source", "Ship Option"]


def _validate_scc_csv(path: Path) -> dict:
    """Lightweight structural check of an uploaded SCC CSV. Never raises."""
    import csv as _csv

    try:
        with open(path, encoding="utf-8-sig", newline="") as fh:
            reader = _csv.DictReader(fh)
            rows = list(reader)
            fieldnames = reader.fieldnames or []
    except (OSError, UnicodeDecodeError, _csv.Error) as exc:
        return {"ok": False, "reason": f"could not read file as CSV: {exc}"}

    if not fieldnames:
        return {"ok": False, "reason": "file is empty or has no header row"}
    missing = [c for c in SCC_REQUIRED_COLUMNS if c not in fieldnames]
    if missing:
        return {"ok": False, "reason": f"missing required column(s): {', '.join(missing)}"}
    if not rows:
        return {"ok": False, "reason": "file has a header row but no data rows"}
    blank = [i for i, row in enumerate(rows, start=2) if not str(row.get("Tracking ID") or "").strip()]
    if blank:
        return {"ok": False, "reason": f"blank Tracking ID in row(s): {blank[:5]}"}
    return {"ok": True, "rows": len(rows)}


def source_csv_path(run_dir: Path, name: str) -> Path | None:
    """Return the CSV a given source produced in this run, if it exists yet."""
    filename = SOURCE_CSV.get(name)
    if not filename or run_dir is None:
        return None
    candidate = Path(run_dir) / filename
    return candidate if candidate.exists() else None


def midway_session_valid() -> bool:
    """True when the local Midway cookie has an unexpired session line.

    Mirrors the cookie parse in ``run_daily.preflight`` so the page and the engine
    agree on whether ``mwinit -o`` is needed.
    """
    cookie = Path.home() / ".midway" / "cookie"
    if not cookie.exists():
        return False
    now = time.time()
    for line in cookie.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.replace("#HttpOnly_", "").split("\t")
        if (
            len(parts) >= 7
            and parts[0].endswith("midway-auth.amazon.com")
            and parts[5] == "session"
        ):
            try:
                if float(parts[4]) > now:
                    return True
            except ValueError:
                continue
    return False


def edge_profile_status() -> dict:
    """Read-only Edge automation-profile status (never launches or kills anything)."""
    sys.path.insert(0, str(TOOLS))
    import edge_preflight  # noqa: E402  (path set up above)

    return edge_preflight.status()


def _environment_checklist_safe() -> dict:
    """Read-only environment checklist for the page. Fully wrapped so a probe failure
    can never break ``preflight_state``; on error returns an empty, not-ok checklist."""
    try:
        sys.path.insert(0, str(TOOLS))
        import auto_setup  # noqa: E402  (path set up above)

        return auto_setup.environment_checklist()
    except Exception as exc:
        return {"all_ok": False, "items": [], "error": f"{type(exc).__name__}: {exc}"}


def preflight_state() -> dict:
    """Everything the page needs before offering the Start button."""
    edge = edge_profile_status()
    mcscli = Path(
        r"C:\Program Files\Common Files\AmazonMidwayClientSuite\bin\mcscli.exe"
    ).exists()
    midway = midway_session_valid()
    return {
        "midway_ok": midway,
        "edge_profile_exists": bool(edge.get("profile_exists")),
        "edge_profile_busy": bool(edge.get("profile_process_ids")),
        "edge_stale_locks": edge.get("stale_lock_files") or [],
        "mcscli_present": mcscli,
        # Ready to start only when Midway is granted and the Edge profile is free.
        "ready": midway and bool(edge.get("safe_to_launch")),
        # Additive, read-only environment checklist (manual steps a human must do).
        # New key — existing keys above are unchanged; the current page ignores this.
        "environment_checklist": _environment_checklist_safe(),
    }


def launch_midway_prompt() -> dict:
    """Open a real terminal window running ``mwinit -o`` so the user can type the PIN.

    The button cannot type the PIN for the user, so it surfaces the interactive
    prompt in its own console window; the page then polls ``preflight_state`` until
    ``midway_ok`` flips to true.
    """
    if os.name != "nt":
        return {"launched": False, "reason": "Midway prompt launch is Windows-only"}
    try:
        # `start` opens a new console; `cmd /k` keeps it open so the user sees the PIN prompt.
        subprocess.Popen(
            ["cmd", "/c", "start", "Midway Login", "cmd", "/k", "mwinit -o"],
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        return {"launched": True}
    except OSError as exc:
        return {"launched": False, "reason": str(exc)}


def _build_ads_flags(ads_approved: bool, allow_source_failures: bool) -> tuple[list[str], bool]:
    """Shared ADS-approval/partial-download flag logic for both the Phase A and Phase B
    subprocess spawns, so a resumed (Phase B) run always reapplies the same flags the
    operator chose at Start.

    Returns (extra_args, partial): extra_args is appended to the run_daily.py command
    line; partial mirrors whether this run is intentionally missing ADS data.
    """
    if ads_approved:
        partial = False
        extra = ["--allow-plaintext-ads"]
    else:
        # Without ADS approval the run is intentionally partial (ADS skipped).
        partial = True
        extra = ["--skip", "ads", "--allow-partial"]
    if allow_source_failures:
        extra.append("--allow-source-failures")
    return extra, partial


class RunController:
    """Owns at most one active pipeline run at a time."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._run_dir: Path | None = None
        self._stamp: str | None = None
        self._partial: bool = False
        self._start_error: str | None = None
        self._reader: threading.Thread | None = None
        # Set when a run is launched this session. Until then, status is clean/pending
        # and we never surface a previous run's summary.
        self._started_at: float | None = None
        self._ever_started: bool = False
        self._stopped: bool = False
        self._allow_source_failures: bool = False
        # SCC's automated browser lane is retired (SCC now blocks it; tools/scc_client.py
        # was removed). The operator uploads SCC's export CSV manually instead; this path
        # is validated and stored here, then passed to run_daily.py via --scc-csv.
        self._scc_upload_path: Path | None = None
        self._scc_upload_error: str | None = None
        # Two-phase pause-for-manual-SCC-upload state machine. A run now starts with
        # Phase A (preflight + SIM-T ticket pull only) and pauses here; the SCC upload
        # control lives inside the SCC progress card while _phase == "awaiting_scc_upload".
        # A validated upload flips to "scc_uploaded", then continue_after_scc() spawns
        # Phase B (--resume-dir) and flips to "running".
        self._phase: str = "idle"
        self._scc_validation: dict | None = None
        # Remembers the ADS-approval/partial-download flags chosen at Start, so Phase B's
        # spawn can reapply the exact same flag logic via _build_ads_flags.
        self._run_flags: dict | None = None

    # --------------------------------------------------------------- lifecycle
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def scc_upload_status(self) -> dict:
        """Current state of the manual SCC upload (replaces the retired SCC automation)."""
        return {
            "uploaded": self._scc_upload_path is not None,
            "filename": self._scc_upload_path.name if self._scc_upload_path else None,
            "error": self._scc_upload_error,
        }

    def upload_scc_csv(self, file_bytes: bytes, filename: str) -> dict:
        """Validate and store a manually-uploaded SCC export CSV.

        SCC's automated browser lane is retired (SCC now blocks it; tools/scc_client.py
        was removed from the project). The operator searches the SIM-T TIDs on the SCC
        site themselves, exports the CSV by hand, and uploads it here. The file is
        validated for the same shape the old automation produced, then its path is
        stored so start_run() can pass it straight to run_daily.py --scc-csv.
        """
        with self._lock:
            # Guard: only accept an upload once Phase A has finished (no live subprocess),
            # the run directory has resolved, and we are actually waiting on SCC.
            if (
                self.is_running()
                or self._resolve_run_dir() is None
                or self._phase not in ("awaiting_scc_upload", "scc_uploaded")
            ):
                return {
                    "uploaded": False,
                    "reason": "Phase A has not finished yet; wait for the SIM-T pull to complete",
                }

            safe_name = "".join(c for c in filename if c.isalnum() or c in "._-") or "scc_upload.csv"
            dest_dir = TOOLS / "captures" / "scc_uploads"
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{safe_name}"
            try:
                dest.write_bytes(file_bytes)
            except OSError as exc:
                self._scc_upload_error = f"could not save upload: {exc}"
                return {"uploaded": False, "reason": self._scc_upload_error}

            validation = _validate_scc_csv(dest)
            if not validation["ok"]:
                self._scc_upload_error = validation["reason"]
                # Do not advance the phase on a rejected upload — stay awaiting_scc_upload.
                return {"uploaded": False, "reason": validation["reason"]}

            self._scc_upload_path = dest
            self._scc_upload_error = None
            self._scc_validation = validation
            self._phase = "scc_uploaded"
            return {"uploaded": True, "rows": validation["rows"], "filename": dest.name}

    def start_run(self, ads_approved: bool, allow_source_failures: bool = False) -> dict:
        """Spawn Phase A: preflight + the SIM-T ticket pull only. A full (publishable)
        run requires ADS approval.

        Start requires only a granted Midway session (and a free Edge profile) — SCC's
        automated browser lane is retired, so no SCC upload is required before Start.
        Phase A exits before the SCC stage; the SCC progress card then prompts for a
        manual upload, and a valid upload auto-continues as Phase B in the same run
        directory (see continue_after_scc).

        When ``allow_source_failures`` is set, a source that fails to fetch is left blank
        and the workbook is still produced (labelled PARTIAL) rather than blocking.
        """
        with self._lock:
            if self.is_running():
                return {"started": False, "reason": "A run is already in progress"}
            pre = preflight_state()
            if not pre["midway_ok"]:
                return {"started": False, "reason": "Midway session not granted yet", "preflight": pre}
            if pre["edge_profile_busy"]:
                return {
                    "started": False,
                    "reason": "The automation Edge profile is open; close that window and retry",
                    "preflight": pre,
                }

            cmd = [sys.executable, "-X", "utf8", str(RUN_DAILY), "--stop-after-tickets"]
            extra, partial = _build_ads_flags(ads_approved, allow_source_failures)
            cmd += extra
            self._partial = partial
            self._allow_source_failures = allow_source_failures
            self._run_flags = {"ads_approved": ads_approved, "allow_source_failures": allow_source_failures}

            self._start_error = None
            self._run_dir = None
            self._stamp = None
            # A fresh Start always clears any previous run's leftover upload state.
            self._scc_upload_path = None
            self._scc_upload_error = None
            self._scc_validation = None
            # Timestamp just before spawn so we can distinguish this run's directory
            # from any earlier run's `_latest.txt`.
            self._started_at = time.time()
            self._ever_started = True
            self._proc = subprocess.Popen(
                cmd,
                cwd=str(TOOLS),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            )
            self._stopped = False
            self._phase = "awaiting_scc_upload"
            self._reader = threading.Thread(target=self._drain_stdout, daemon=True)
            self._reader.start()
            return {"started": True, "partial": self._partial}

    def continue_after_scc(self) -> dict:
        """Spawn Phase B: resume the paused run in the same directory now that a valid
        SCC CSV has been uploaded. Reapplies the exact ADS-approval/partial flags Phase A
        was started with (see _build_ads_flags), then re-arms the stdout-drain thread
        exactly as start_run does.
        """
        with self._lock:
            if self._phase != "scc_uploaded":
                return {"started": False, "reason": "No validated SCC upload yet"}
            run_dir = self._resolve_run_dir()
            if run_dir is None or self._scc_upload_path is None:
                return {"started": False, "reason": "Run directory or SCC upload path not resolved"}

            cmd = [
                sys.executable, "-X", "utf8", str(RUN_DAILY),
                "--resume-dir", str(run_dir), "--scc-csv", str(self._scc_upload_path),
            ]
            extra, partial = _build_ads_flags(**self._run_flags)
            cmd += extra
            self._partial = partial

            self._proc = subprocess.Popen(
                cmd,
                cwd=str(TOOLS),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            )
            self._stopped = False
            self._phase = "running"
            self._reader = threading.Thread(target=self._drain_stdout, daemon=True)
            self._reader.start()
            return {"started": True}

    def stop_run(self) -> dict:
        """Abort the active run and its whole process tree (browser workers included)."""
        with self._lock:
            if not self.is_running():
                # No live subprocess — this covers being stopped while paused awaiting
                # the manual SCC upload (Phase A already exited cleanly). Reset to idle
                # so Start is offered again.
                self._phase = "idle"
                return {"stopped": False, "reason": "No run is in progress"}
            pid = self._proc.pid
            try:
                if os.name == "nt":
                    # The run spawns SCC/PNAMA/DSR browser subprocesses; /T kills the tree.
                    subprocess.run(
                        ["taskkill", "/PID", str(pid), "/T", "/F"],
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                else:
                    self._proc.terminate()
            except Exception as exc:
                return {"stopped": False, "reason": f"could not stop run: {exc}"}
            try:
                self._proc.wait(timeout=15)
            except Exception:
                pass
            self._stopped = True
            return {"stopped": True}

    def _drain_stdout(self) -> None:
        """Capture the RUN_DIR pointer line, then keep the pipe from filling."""
        assert self._proc is not None and self._proc.stdout is not None
        for line in self._proc.stdout:
            line = line.strip()
            if line.startswith("RUN_DIR=") and self._run_dir is None:
                self._run_dir = Path(line.split("=", 1)[1])
                self._stamp = self._run_dir.name

    def _resolve_run_dir(self) -> Path | None:
        # Never surface a previous run before the user starts one this session.
        if not self._ever_started:
            return None
        if self._run_dir is not None:
            return self._run_dir
        # The RUN_DIR line may not be captured in the first moments after spawn.
        # Fall back to the pointer file, but only accept it if it belongs to the
        # run we just started (created at/after our start time) — otherwise we would
        # show the *previous* run's data.
        pointer = RUNS / "_latest.txt"
        if pointer.exists():
            candidate = Path(pointer.read_text(encoding="utf-8").strip())
            if candidate.exists():
                created = candidate.stat().st_mtime
                if self._started_at is None or created >= self._started_at - 2:
                    self._run_dir = candidate
                    self._stamp = candidate.name
                    return candidate
        return None

    # --------------------------------------------------------------- status
    def get_status(self, log_tail: int = 40) -> dict:
        run_dir = self._resolve_run_dir()
        running = self.is_running()
        exit_code = None if running or self._proc is None else self._proc.returncode
        status = {
            "running": running,
            "stopped": self._stopped and not running,
            "exit_code": exit_code,
            "partial": self._partial,
            "run_dir": str(run_dir) if run_dir else None,
            "stamp": self._stamp,
            "log_tail": [],
            "sources": {name: {"status": "pending", "counts": {}} for name in SOURCE_ORDER},
            "summary": None,
            "publication_blocked": None,
            "publishable": False,
            "workbook_downloadable": False,
            "blank_sources": [],
            "verification": None,
            "desktop_file": None,
            "scc_upload": self.scc_upload_status(),
            "scc_phase": self._phase,
            "scc_validation": self._scc_validation,
        }
        if not run_dir:
            return status

        log_path = run_dir / "run.log"
        if log_path.exists():
            lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
            status["log_tail"] = lines[-log_tail:]

        summary = _read_summary(run_dir)
        if summary is not None:
            status["summary"] = summary
            status["publication_blocked"] = summary.get("publication_blocked")
            status["sources"] = _sources_from_summary(summary)
            polygon = summary.get("polygon") or {}
            status["verification"] = polygon.get("verification")
            status["publishable"] = is_publishable(summary, self._partial)
            status["workbook_downloadable"] = is_downloadable(summary, self._partial)
            status["blank_sources"] = blank_sources(summary)
            if status["workbook_downloadable"]:
                status["desktop_file"] = _existing_desktop_copy(self._stamp)
        # Per-source download availability and a live progress percentage so the page
        # can show how far each tool's data pull has gotten.
        for name, entry in status["sources"].items():
            entry["downloadable"] = source_csv_path(run_dir, name) is not None
            entry["percent"] = _source_percent(run_dir, name, entry.get("status", "pending"))
        return status

    def desktop_path_for_current(self) -> Path | None:
        run_dir = self._resolve_run_dir()
        if not run_dir:
            return None
        summary = _read_summary(run_dir)
        if summary is None or not is_downloadable(summary, self._partial):
            return None
        return publish_to_desktop(run_dir, self._stamp)


# ------------------------------------------------------------------- helpers
def _read_summary(run_dir: Path) -> dict | None:
    path = run_dir / "summary.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None  # summary is written atomically at end; mid-write reads are rare


def _sources_from_summary(summary: dict) -> dict:
    stages = summary.get("stages") or {}
    out: dict[str, dict] = {}
    for name in SOURCE_ORDER:
        stage = stages.get(name)
        if stage is None:
            out[name] = {"status": "pending", "counts": {}}
            continue
        # The engine writes an explicit status: running | done | error. Fall back to
        # the boolean ``ok`` only for older summaries that predate the status field.
        state = stage.get("status")
        if state == "running" or stage.get("ok") is None:
            out[name] = {"status": "running", "counts": {}}
        elif state == "done" or stage.get("ok") is True:
            out[name] = {"status": "done", "counts": _counts_for(name, summary)}
        else:
            out[name] = {"status": "error", "counts": {}, "error": stage.get("error")}
    return out


def _counts_for(name: str, summary: dict) -> dict:
    """Pull the human-meaningful counts for a source card from the engine summary."""
    if name == "tickets":
        t = summary.get("tickets") or {}
        return {"tickets": t.get("tickets"), "rows": t.get("rows")}
    if name == "eagle":
        return {"no_data": summary.get("eagle_missing")}
    if name == "scc":
        s = summary.get("scc") or {}
        return {"rows": s.get("rows"), "missing": s.get("missing")}
    if name == "pnama":
        p = summary.get("pnama") or {}
        return {k: v for k, v in p.items() if isinstance(v, int)}
    if name == "dsr":
        d = summary.get("dsr") or {}
        return {k: v for k, v in d.items() if isinstance(v, int)}
    if name == "gcrs":
        g = summary.get("gcrs") or {}
        return {"resolved": g.get("resolved"), "not_found": g.get("not_found"), "errored": g.get("errored")}
    if name == "polygon":
        p = summary.get("polygon") or {}
        return {"rows": p.get("rows"), "inside": p.get("inside"), "outside": p.get("outside"), "errors": p.get("errors")}
    if name == "ads":
        a = summary.get("ads") or {}
        return {"rows": a.get("rows"), "missing": a.get("missing")}
    return {}


def _total_requested(run_dir: Path) -> int:
    """How many lookup TIDs this run is processing (denominator for progress %)."""
    tids = run_dir / "tids.txt"
    if tids.exists():
        return sum(1 for line in tids.read_text(encoding="utf-8").splitlines() if line.strip())
    return 0


def _csv_row_count(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        with open(path, encoding="utf-8-sig", newline="") as fh:
            return max(0, sum(1 for _ in fh) - 1)  # minus header
    except OSError:
        return 0


def _source_percent(run_dir: Path, name: str, state: str) -> int:
    """A 0-100 progress estimate for a source card.

    done -> 100. running -> rows already written to that source's CSV vs total TIDs
    (capped at 99 so it only shows 100 when truly finished). pending/error -> 0.
    """
    if state == "done":
        return 100
    if state != "running":
        return 0
    total = _total_requested(run_dir)
    if total <= 0:
        return 0
    csv_path = source_csv_path(run_dir, name)
    if csv_path is None:
        return 0
    done = _csv_row_count(csv_path)
    return max(0, min(99, round(done * 100 / total)))


def is_publishable(summary: dict, partial: bool) -> bool:
    """Reproduce the engine's own publication/accuracy gate.

    A run is publishable (100%-accurate deliverable) only when:
      * it was not started as a partial (ADS-skipped) run,
      * no ``publication_blocked`` marker is present,
      * every required lane succeeded,
      * fill/recalc, read_back and apply_remarks all succeeded,
      * the polygon workbook verification reports OK.
    """
    if partial:
        return False
    if summary.get("publication_blocked"):
        return False
    stages = summary.get("stages") or {}
    for lane in REQUIRED_LANES:
        if not (stages.get(lane) or {}).get("ok"):
            return False
    for stage in ("fill_recalc", "read_back", "apply_remarks"):
        if not (stages.get(stage) or {}).get("ok"):
            return False
    verification = (summary.get("polygon") or {}).get("verification")
    if not _verification_ok(verification):
        return False
    return True


def is_downloadable(summary: dict, partial: bool) -> bool:
    """True when a workbook exists and is safe to download — either a full publishable
    run, or a PARTIAL run where some sources were intentionally left blank but the
    workbook-producing stages (fill/recalc, read_back, apply_remarks) still succeeded.
    """
    if is_publishable(summary, partial):
        return True
    if summary.get("publication_blocked"):
        return False
    stages = summary.get("stages") or {}
    for stage in ("fill_recalc", "read_back", "apply_remarks"):
        if not (stages.get(stage) or {}).get("ok"):
            return False
    # A partial workbook is downloadable even without full-run ADS approval, as long as
    # it was actually produced (the stages above passed).
    return True


def blank_sources(summary: dict) -> list:
    return summary.get("blank_sources") or []


def _verification_ok(verification) -> bool:
    """The polygon workbook verification only returns ``verify_ok: True`` on success;
    on any mismatch it raises, which the run records as a failed stage. So we require
    the explicit ``verify_ok`` flag."""
    if isinstance(verification, dict):
        return bool(verification.get("verify_ok"))
    return False


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _working_workbook(run_dir: Path, stamp: str | None) -> Path | None:
    if stamp:
        candidate = run_dir / f"OOJ_Working_{stamp}.xlsx"
        if candidate.exists():
            return candidate
    matches = sorted(run_dir.glob("OOJ_Working_*.xlsx"))
    return matches[-1] if matches else None


def _existing_desktop_copy(stamp: str | None) -> str | None:
    if not stamp:
        return None
    target = DESKTOP / f"OOJ_Process_Filled_{stamp}.xlsx"
    return str(target) if target.exists() else None


def publish_to_desktop(run_dir: Path, stamp: str | None) -> Path:
    """Copy the produced workbook to the Desktop. Refuses if no downloadable workbook.

    Full runs are named OOJ_Process_Filled_<stamp>.xlsx; PARTIAL runs (some sources left
    blank) are named OOJ_Process_Filled_PARTIAL_<stamp>.xlsx so the file itself is labelled.
    """
    summary = _read_summary(run_dir)
    if summary is None:
        raise RuntimeError("run summary not available yet")
    if not is_downloadable(summary, partial=False):
        raise RuntimeError("no downloadable workbook was produced")
    source = _working_workbook(run_dir, stamp)
    if source is None:
        raise RuntimeError("filled workbook not found in run directory")
    name_stamp = stamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    partial_tag = "PARTIAL_" if blank_sources(summary) else ""
    target = DESKTOP / f"OOJ_Process_Filled_{partial_tag}{name_stamp}.xlsx"
    shutil.copy2(source, target)
    return target


# One process-wide controller instance for the server to share.
CONTROLLER = RunController()
