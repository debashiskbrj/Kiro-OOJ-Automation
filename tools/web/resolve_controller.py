"""Web supervisor for "5. Resolve tickets" (bulk resolve SIM-T tickets).

Flow: the operator pastes TT URLs -> ``check`` extracts the unique ticket IDs locally
(no network, nothing changed) -> the operator ticks the confirmation box -> ``start``
spawns ``tools/resolve_tickets_runner.py`` as a killable subprocess -> ``get_status``
reports live progress from its progress.json / results.csv -> ``report_path`` builds the
downloadable Excel report (also after a Stop).

Outputs live in tools/resolve_runs/<stamp>/ — deliberately OUTSIDE tools/captures so the
pipeline's fresh-Start wipe and the server's capture sanitizer never touch them.
"""
from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOLS))

import resolve_tickets_runner as rtr  # noqa: E402

RUNNER = TOOLS / "resolve_tickets_runner.py"
RT_ROOT = TOOLS / "resolve_runs"
MAX_INPUT_CHARS = 3_000_000
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # background runs never open a console
MAX_TABLE_ROWS = 3000


class ResolveController:
    """Owns at most one resolve run at a time."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._out_dir: Path | None = None
        self._checked: dict | None = None
        self._stopped = False
        self._report: Path | None = None

    # ----------------------------------------------------------------- state
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    # ----------------------------------------------------------------- check
    def check(self, text: str) -> dict:
        """Parse pasted URLs/IDs locally. Nothing is sent anywhere, nothing changes."""
        if len(text or "") > MAX_INPUT_CHARS:
            return {"ok": False, "reason": "The pasted text is too large"}
        with self._lock:
            if self.is_running():
                return {"ok": False, "reason": "A resolve run is already in progress"}
            parsed = rtr.parse_ticket_input(text)
            if not parsed["ids"]:
                self._checked = None
                return {"ok": False, "reason": "No valid TT URL or ticket ID found",
                        "invalid": parsed["invalid"][:50], "invalid_count": len(parsed["invalid"])}
            if len(parsed["ids"]) > rtr.MAX_TICKETS:
                self._checked = None
                return {"ok": False,
                        "reason": f"{len(parsed['ids'])} tickets is more than the {rtr.MAX_TICKETS} limit"}
            self._checked = parsed
            return {"ok": True, "unique": len(parsed["ids"]), "pieces": parsed["pieces"],
                    "duplicates": parsed["duplicates"], "invalid_count": len(parsed["invalid"]),
                    "invalid": parsed["invalid"][:50], "sample": parsed["ids"][:10]}

    # ----------------------------------------------------------------- run
    def start(self) -> dict:
        with self._lock:
            if self.is_running():
                return {"started": False, "reason": "A resolve run is already in progress"}
            if not self._checked or not self._checked["ids"]:
                return {"started": False, "reason": "Paste the TT URLs and click Check list first"}
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            out_dir = RT_ROOT / stamp
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "ids.txt").write_text("\n".join(self._checked["ids"]), encoding="utf-8")
            (out_dir / "input_check.json").write_text(json.dumps({
                "unique": len(self._checked["ids"]), "pieces": self._checked["pieces"],
                "duplicates": self._checked["duplicates"],
                "invalid": self._checked["invalid"]}, indent=2), encoding="utf-8")
            total = self._spawn(out_dir)
            # A list is used once: the next run needs a fresh Check list.
            self._checked = None
            return {"started": True, "total": total}

    def _spawn(self, out_dir: Path) -> int:
        """Start the runner on ``out_dir/ids.txt`` (caller holds the lock). Returns the count."""
        cmd = [sys.executable, "-X", "utf8", str(RUNNER), "--input", str(out_dir / "ids.txt"),
               "--out-dir", str(out_dir)]
        log = open(out_dir / "run.log", "a", encoding="utf-8")
        creationflags = (subprocess.CREATE_NEW_PROCESS_GROUP | NO_WINDOW) if os.name == "nt" else 0
        self._proc = subprocess.Popen(cmd, cwd=str(TOOLS), stdout=log, stderr=subprocess.STDOUT,
                                      text=True, creationflags=creationflags)
        log.close()
        self._out_dir = out_dir
        self._stopped = False
        self._report = None
        return sum(1 for line in (out_dir / "ids.txt").read_text(encoding="utf-8").splitlines()
                   if line.strip())

    # ----------------------------------------------------------------- retry
    def retry_ids(self) -> list[str]:
        """Tickets of the latest run that are NOT done: every Failed one, plus every ticket
        with no result (the run was stopped, or it ended before the ticket was verified).
        Resolved and Already-resolved tickets are never included."""
        if not self._out_dir or self.is_running():
            return []
        ids_path = self._out_dir / "ids.txt"
        if not ids_path.exists():
            return []
        all_ids = [line.strip() for line in ids_path.read_text(encoding="utf-8").splitlines()
                   if line.strip()]
        result_by_id = {r.get("Ticket ID"): r.get("Result") for r in self._results()}
        done = {rtr.RESULT_RESOLVED, rtr.RESULT_ALREADY}
        return [t for t in all_ids if result_by_id.get(t) not in done]

    def retry_failed(self) -> dict:
        """Start a NEW run on only the failed / not-done tickets of the latest run."""
        with self._lock:
            if self.is_running():
                return {"started": False, "reason": "A resolve run is already in progress"}
            if not self._out_dir:
                return {"started": False, "reason": "There is no earlier resolve run to retry"}
            ids = self.retry_ids()
            if not ids:
                return {"started": False, "reason": "No failed or pending tickets to retry"}
            out_dir = RT_ROOT / (datetime.now().strftime("%Y%m%d_%H%M%S") + "_retry")
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "ids.txt").write_text("\n".join(ids), encoding="utf-8")
            (out_dir / "input_check.json").write_text(json.dumps({
                "unique": len(ids), "pieces": len(ids), "duplicates": 0, "invalid": [],
                "retry_of": self._out_dir.name}, indent=2), encoding="utf-8")
            total = self._spawn(out_dir)
            self._checked = None
            return {"started": True, "total": total, "retry_of": out_dir.name}

    def stop(self) -> dict:
        with self._lock:
            if not self.is_running():
                return {"stopped": False, "reason": "No resolve run is in progress"}
            try:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(self._proc.pid), "/T", "/F"],
                                   capture_output=True, text=True, timeout=30,
                                   creationflags=NO_WINDOW)
                else:
                    self._proc.terminate()
                self._proc.wait(timeout=15)
            except Exception as exc:
                return {"stopped": False, "reason": f"could not stop: {exc}"}
            self._stopped = True
            return {"stopped": True}

    # ----------------------------------------------------------------- status
    def _results(self) -> list[dict]:
        if not self._out_dir:
            return []
        path = self._out_dir / "results.csv"
        if not path.exists():
            return []
        try:
            with open(path, encoding="utf-8", newline="") as fh:
                return list(csv.DictReader(fh))
        except OSError:
            return []

    def _input_check(self) -> dict:
        try:
            return json.loads((self._out_dir / "input_check.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, AttributeError):
            return {}

    def get_status(self, log_tail: int = 30) -> dict:
        running = self.is_running()
        status = {"running": running, "stopped": self._stopped and not running,
                  "has_run": self._out_dir is not None, "total": 0, "done": 0,
                  "counts": {rtr.RESULT_RESOLVED: 0, rtr.RESULT_ALREADY: 0, rtr.RESULT_FAILED: 0},
                  "finished": False, "rows": [], "log_tail": [], "input": {},
                  "report_ready": False, "retry_count": 0}
        if not self._out_dir:
            return status
        try:
            progress = json.loads((self._out_dir / "progress.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            progress = {}
        rows = self._results()
        counts = {rtr.RESULT_RESOLVED: 0, rtr.RESULT_ALREADY: 0, rtr.RESULT_FAILED: 0}
        for row in rows:
            counts[row.get("Result", "")] = counts.get(row.get("Result", ""), 0) + 1
        ids_path = self._out_dir / "ids.txt"
        total = progress.get("total") or (
            sum(1 for line in ids_path.read_text(encoding="utf-8").splitlines() if line.strip())
            if ids_path.exists() else 0)
        # Failures first so the operator sees what needs attention.
        order = {rtr.RESULT_FAILED: 0, rtr.RESULT_RESOLVED: 1, rtr.RESULT_ALREADY: 2}
        rows_sorted = sorted(rows, key=lambda r: order.get(r.get("Result"), 3))
        status.update({"total": total, "done": len(rows), "counts": counts,
                       "finished": bool(progress.get("finished")),
                       # Two-phase progress (additive keys): resolving -> verifying -> finished.
                       "phase": progress.get("phase") or ("finished" if progress.get("finished") else "resolving"),
                       "checked": progress.get("checked", 0), "sent": progress.get("sent", 0),
                       "to_verify": progress.get("to_verify", 0),
                       "rows": rows_sorted[:MAX_TABLE_ROWS], "input": self._input_check(),
                       "report_ready": (not running) and (bool(rows) or bool(progress.get("finished"))),
                       # Failed + not-done tickets the "Retry failed / pending" button would redo.
                       "retry_count": len(self.retry_ids())})
        log_path = self._out_dir / "run.log"
        if log_path.exists():
            status["log_tail"] = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-log_tail:]
        return status

    # ----------------------------------------------------------------- report
    def report_path(self) -> Path | None:
        """Build (or rebuild) the Excel report for the latest run. Tickets the runner never
        reached (after a Stop) are listed as "Not processed (stopped)"."""
        if not self._out_dir or self.is_running():
            return None
        import openpyxl
        from openpyxl.styles import Font, PatternFill

        rows = self._results()
        done_ids = {r.get("Ticket ID") for r in rows}
        ids_path = self._out_dir / "ids.txt"
        all_ids = [line.strip() for line in ids_path.read_text(encoding="utf-8").splitlines()
                   if line.strip()] if ids_path.exists() else []
        sent: dict[str, dict] = {}
        sent_csv = self._out_dir / "sent.csv"
        if sent_csv.exists():
            with open(sent_csv, encoding="utf-8", newline="") as fh:
                sent = {r["Ticket ID"]: r for r in csv.DictReader(fh)}
        for ticket in all_ids:
            if ticket in done_ids:
                continue
            if ticket in sent:
                # Resolve was accepted in phase 1 but the run stopped before phase 2 checked it.
                rows.append({"Ticket ID": ticket, "Ticket URL": rtr.TICKET_URL.format(ticket),
                             "Status before": sent[ticket].get("Status before", ""),
                             "Result": rtr.RESULT_SENT_UNVERIFIED, "Status after": "",
                             "Detail": "Resolve was accepted; the run stopped before it was "
                                       "verified. Run the list again to check.",
                             "Time": sent[ticket].get("Time", "")})
            else:
                rows.append({"Ticket ID": ticket, "Ticket URL": rtr.TICKET_URL.format(ticket),
                             "Status before": "", "Result": rtr.RESULT_NOT_PROCESSED,
                             "Status after": "", "Detail": "Run was stopped before this ticket",
                             "Time": ""})
        check = self._input_check()
        counts: dict[str, int] = {}
        for row in rows:
            counts[row["Result"]] = counts.get(row["Result"], 0) + 1

        wb = openpyxl.Workbook()
        summary = wb.active
        summary.title = "Summary"
        bold = Font(bold=True)
        summary.append(["Resolve tickets report", self._out_dir.name])
        summary["A1"].font = bold
        summary.append(["Status set", f"{rtr.RESOLVED_STATUS} (closure code {rtr.CLOSURE_CODE})"])
        summary.append([])
        for label, value in (
            ("Pasted entries", check.get("pieces", "")),
            ("Duplicates removed", check.get("duplicates", "")),
            ("Invalid entries (not resolved)", len(check.get("invalid", []))),
            ("Unique tickets", len(all_ids)),
            (rtr.RESULT_RESOLVED, counts.get(rtr.RESULT_RESOLVED, 0)),
            (rtr.RESULT_ALREADY, counts.get(rtr.RESULT_ALREADY, 0)),
            (rtr.RESULT_FAILED, counts.get(rtr.RESULT_FAILED, 0)),
            (rtr.RESULT_SENT_UNVERIFIED, counts.get(rtr.RESULT_SENT_UNVERIFIED, 0)),
            (rtr.RESULT_NOT_PROCESSED, counts.get(rtr.RESULT_NOT_PROCESSED, 0)),
        ):
            summary.append([label, value])
        summary.column_dimensions["A"].width = 34
        summary.column_dimensions["B"].width = 40

        sheet = wb.create_sheet("Results")
        sheet.append(["Sl"] + rtr.RESULT_FIELDS)
        for cell in sheet[1]:
            cell.font = bold
        fills = {rtr.RESULT_RESOLVED: "C6EFCE", rtr.RESULT_ALREADY: "DDEBF7",
                 rtr.RESULT_FAILED: "FFC7CE", rtr.RESULT_NOT_PROCESSED: "FFEB9C",
                 rtr.RESULT_SENT_UNVERIFIED: "FFEB9C"}
        for index, row in enumerate(rows, 1):
            sheet.append([index] + [row.get(field, "") for field in rtr.RESULT_FIELDS])
            fill = fills.get(row.get("Result"))
            if fill:
                sheet.cell(row=index + 1, column=5).fill = PatternFill("solid", fgColor=fill)
        for letter, width in zip("ABCDEFGH", (6, 16, 40, 16, 24, 16, 50, 20)):
            sheet.column_dimensions[letter].width = width
        sheet.freeze_panes = "A2"

        invalid = check.get("invalid") or []
        if invalid:
            bad = wb.create_sheet("Invalid input")
            bad.append(["Entry (not a TT URL / ticket ID, not resolved)"])
            bad["A1"].font = bold
            for entry in invalid:
                bad.append([entry])
            bad.column_dimensions["A"].width = 80

        path = self._out_dir / f"Resolve_Report_{self._out_dir.name}.xlsx"
        wb.save(path)
        self._report = path
        return path


CONTROLLER = ResolveController()
