r"""OOJ daily run — one command that performs the local validation workflow.

  Stage 0  preflight        Midway session, edge_profile, template, local tools
  Stage 1  SIM-T            tickets_pull -> Tech_Data rows (one TID per row)
  Stage 2  TIDs             unique lookup TIDs plus the full ordered Tech_Data rows
  Stage 3  enrichment       SCC · EagleEye · PNAMA · DSR · SIW/GCRS · ADS
  Stage 4  local polygons   SIW route polygons, row-aligned with Tech_Data (no AgentSpace)
  Stage 5  fill/recalculate template and publish validation outputs

Usage:
  python run_daily.py --allow-plaintext-ads  # only after explicit approval for this run
  python run_daily.py --skip ads --allow-partial
  python run_daily.py --skip scc,pnama,dsr,ads,polygon --allow-partial --max-tids 50

Everything is read-only against Amazon source systems. Output: tools\captures\runs\<stamp>\
"""
import argparse
import csv
import json
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import openpyxl

import fill_template as ft
from ooj_paths import BUILDER_MCP, EDGE_PROFILE

HERE = Path(__file__).parent
CAPTURES = HERE / "captures"
RUNS = CAPTURES / "runs"
DESKTOP = Path.home() / "Desktop"
# The final filled workbook is published to the Desktop as
# "Templete_OOJ Process_<Month>_KABIDEBA1_<stamp>.xlsx" (full) or with a PARTIAL_ tag
# before the stamp (partial). Exports older than RUN_KEEP_DAYS are pruned at the start of
# each fresh run (see wipe_old_runs). This prefix MUST stay in sync with
# tools/web/run_controller.py's DESKTOP_EXPORT_PREFIX so the wipe matches what is published.
DESKTOP_EXPORT_PREFIX = "Templete_OOJ Process_"
# Operator rule (fresh Start): keep prior run folders and Desktop export workbooks for
# this many days (anything older is deleted by wipe_old_runs). Per-source capture caches
# are still wiped on every fresh Start so a run never reuses another run's data.
RUN_KEEP_DAYS = 3
FINAL_COLUMNS = [
    "IssueUrl", "CreateDate", "LastUpdatedDate", "TID", "Station Code(SCC)", "Zone", "State",
    "Destination", "CX Pincode", "ADS Predicted Pincode", "Probability", "Pincode Validation", "Remark",
    "Predicted Pincode served by station", "Station Validation", "In - Swa", "Short ID", "URL",
    "RTO Status", "Past delivered record", "Delivered Old shipment", "Old shipment delivered by Station",
    "SCC & misort Station validation", "Station Transfer Validation", "GCRS Agent Space",
]


def wipe_old_runs(current_dir: Path, log=print, keep_days: int = RUN_KEEP_DAYS) -> dict:
    """Clean up prior run data at the start of a fresh run (operator rule).

    Each automation run must work only on data it freshly fetched during that run, so
    at the start of every fresh Start (Phase A) we remove every per-source capture
    artifact under captures/<source>/, regardless of age.

    Prior run folders under captures/runs and published Desktop exports
    ("Templete_OOJ Process_*.xlsx") are kept for ``keep_days`` days (operator rule:
    3 days) and deleted once older. No run reads another run's folder, so keeping them
    does not affect freshness.

    Deliberately preserved:
      * ``current_dir`` — the run directory this very run just created (its freshly
        pulled SIM-T data must survive the wipe).
      * ``scc_enum_map.json`` — the long-lived SCC State enum->text ground-truth overlay.
      * Run folders and Desktop exports from the last ``keep_days`` days.
    Never touches source code, the template, tests, or any other Desktop file (only the
    automation's own "Templete_OOJ Process_*" workbooks). Safe if a file is locked (never raises).
    """
    if not CAPTURES.exists():
        return {"removed_dirs": 0, "removed_files": 0}
    # DMAIC Control phase: copy every run's key numbers (counts/timings only) into
    # tools/metrics/run_history.csv BEFORE old run folders are deleted. Never raises.
    try:
        import dmaic_metrics
        dmaic_metrics.harvest(RUNS, log=log)
    except Exception:
        pass
    try:
        current_resolved = current_dir.resolve()
    except OSError:
        current_resolved = current_dir
    removed_dirs = 0
    removed_files = 0
    cutoff_dt = datetime.now() - timedelta(days=keep_days)

    def _is_old(path: Path) -> bool:
        """Older than the keep window: run-folder name stamp first, else mtime."""
        match = re.match(r"(\d{8}_\d{6})", path.name)
        if match:
            try:
                return datetime.strptime(match.group(1), "%Y%m%d_%H%M%S") < cutoff_dt
            except ValueError:
                pass
        try:
            return datetime.fromtimestamp(path.stat().st_mtime) < cutoff_dt
        except OSError:
            return False

    # 1. Prior run folders under captures/runs older than keep_days (never this run's own).
    if RUNS.exists():
        for entry in RUNS.iterdir():
            if entry.name == "_latest.txt":
                continue
            try:
                if entry.resolve() == current_resolved:
                    continue
            except OSError:
                pass
            if not _is_old(entry):
                continue
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
                removed_dirs += 1
            elif entry.is_file():
                try:
                    entry.unlink()
                    removed_files += 1
                except OSError:
                    pass

    # 2. Every per-source capture artifact, keeping only the SCC enum ground-truth overlay.
    # captures/post_comments/ is NOT a source cache: it holds the Post comments uploads and
    # reports, the only record of which live SIM-T comments were posted, so it is kept
    # (audit 1.4), the same as tools/resolve_runs/.
    for source_dir in CAPTURES.iterdir():
        if not source_dir.is_dir() or source_dir.name in ("runs", "post_comments"):
            continue
        for entry in source_dir.rglob("*"):
            if entry.name == "scc_enum_map.json":
                continue
            if entry.is_file():
                try:
                    entry.unlink()
                    removed_files += 1
                except OSError:
                    pass

    # 3. Old published export workbooks on the Desktop ("Templete_OOJ Process_*.xlsx").
    # Keep the last keep_days days; remove older ones so stale outputs do not accumulate.
    # Only the automation's own export files are matched — no other Desktop file is touched.
    removed_exports = 0
    try:
        if DESKTOP.is_dir():
            for entry in DESKTOP.glob(f"{DESKTOP_EXPORT_PREFIX}*.xlsx"):
                if not entry.is_file():
                    continue
                try:
                    produced = datetime.fromtimestamp(entry.stat().st_mtime)
                except OSError:
                    continue
                if produced < cutoff_dt:
                    try:
                        entry.unlink()
                        removed_exports += 1
                    except OSError:
                        pass
    except Exception:
        # Desktop pruning is best-effort and must never block a run.
        pass

    log(f"fresh-run wipe: removed {removed_dirs} prior run folders, {removed_files} "
        f"capture files, and {removed_exports} old Desktop export(s) "
        f"(keeping this run's data, scc_enum_map.json, and run folders/exports from the "
        f"last {keep_days} days)")
    return {"removed_dirs": removed_dirs, "removed_files": removed_files,
            "removed_exports": removed_exports}


def clean_header(value) -> str:
    """Normalize only whitespace in template headers; wording/case remain authoritative."""
    return " ".join(str(value).split()) if value is not None else ""


def normalize_tid(value) -> str:
    if value is None:
        return ""
    text = str(value).strip().lstrip("\ufeff").replace("'", "")
    return text[:-2] if text.endswith(".0") and text[:-2].isdigit() else text


def csv_safe(value):
    """Neutralize spreadsheet formula prefixes in any human-openable CSV."""
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def scc_lookup_tids(scc_rows) -> list[str]:
    """Unique, ordered TIDs of the uploaded SCC file: the ONLY TIDs any lane looks up
    (operator rule 2026-11-26)."""
    return list(dict.fromkeys(
        normalize_tid(row.get("Tracking ID"))
        for row in (scc_rows or [])
        if normalize_tid(row.get("Tracking ID"))
    ))


SCC_MISMATCH_TAG = "WARNING: SCC file does not match SIM-T"  # app.js shows a popup for it


def scc_match_check(scc_tids, sim_tids) -> dict:
    """Compare the uploaded SCC TIDs with the SIM-T TIDs. Never stops the run: returns the
    counts and a warning line ("" when every SCC TID is also a SIM-T TID)."""
    scc_set = set(scc_tids)
    sim_set = set(sim_tids)
    matching = len(scc_set & sim_set)
    not_in_sim = sorted(scc_set - sim_set)
    warning = ""
    if scc_set and not matching:
        warning = (f"{SCC_MISMATCH_TAG}: NONE of the {len(scc_set)} SCC TIDs are in today's SIM-T "
                   f"tickets (e.g. SCC {not_in_sim[0]}). Probably an old or wrong SCC file. "
                   "The run continues; check the SCC upload.")
    elif not_in_sim:
        warning = (f"{SCC_MISMATCH_TAG}: {len(not_in_sim)} of {len(scc_set)} SCC TIDs are not in "
                   f"today's SIM-T tickets (e.g. {', '.join(not_in_sim[:3])}). They are looked up "
                   "but have no ticket row. The run continues.")
    return {"matching": matching, "not_in_sim_t": len(not_in_sim), "warning": warning}


def derive_tids(tech: list[dict]) -> list[str]:
    """Unique, ordered lookup TIDs from Tech_Data rows (drops "TID not found")."""
    tids = [
        str(row["impacted_tracking_ids"])
        for row in tech
        if row["impacted_tracking_ids"] != "TID not found"
    ]
    return list(dict.fromkeys(tids))


EXCEL_ERRORS = {"#REF!", "#NAME?", "#VALUE!", "#DIV/0!", "#NUM!", "#NULL!", "#SPILL!", "#CALC!"}


class Run:
    def __init__(self, stamp: str, directory: Path | None = None):
        self.stamp = stamp
        self.dir = Path(directory) if directory is not None else RUNS / stamp
        self.dir.mkdir(parents=True, exist_ok=directory is not None)
        self.log_path = self.dir / "run.log"
        self.summary = {"stamp": stamp, "schema_version": 2, "stages": {}}
        # Guards summary mutations and file writes when the in-process EagleEye lane
        # runs on a background thread alongside the serial browser lanes.
        self._lock = threading.Lock()

    def log(self, msg: str):
        line = f"[{datetime.now():%H:%M:%S}] {msg}"
        with self._lock:
            with open(self.log_path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        print(line, flush=True)

    def save_csv(self, name: str, rows: list[dict]):
        if not rows:
            return
        path = self.dir / name
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(
                {key: csv_safe(value) for key, value in row.items()}
                for row in rows
            )

    def write_progress(self):
        """Persist the current summary atomically so an external reader (the web
        runner) sees live, per-stage progress instead of only the final state."""
        with self._lock:
            payload = json.dumps(self.summary, indent=2, default=str)
            tmp = self.dir / "summary.json.tmp"
            try:
                tmp.write_text(payload, encoding="utf-8")
                tmp.replace(self.dir / "summary.json")
            except OSError:
                # A transient reader lock must never crash the pipeline.
                pass

    def _set_stage(self, name: str, value: dict):
        with self._lock:
            self.summary["stages"][name] = value

    def set_summary(self, key: str, value):
        """Thread-safe assignment of a top-level summary key (used by the concurrent
        EagleEye lane)."""
        with self._lock:
            self.summary[key] = value

    def mark_running(self, name: str):
        """Mark a stage as in-flight and flush progress before the work starts."""
        self._set_stage(name, {"ok": None, "status": "running"})
        self.write_progress()

    def stage(self, name: str, fn, *args, **kwargs):
        t0 = time.time()
        self.log(f"--- {name} ---")
        self.mark_running(name)
        try:
            result = fn(*args, **kwargs)
            self._set_stage(name, {"ok": True, "status": "done",
                                   "seconds": round(time.time() - t0, 1)})
            self.write_progress()
            return result
        except Exception as exc:
            self._set_stage(name, {"ok": False, "status": "error",
                                   "seconds": round(time.time() - t0, 1),
                                   "error": f"{type(exc).__name__}: {str(exc)[:300]}"})
            self.log(f"!!! {name} FAILED: {type(exc).__name__}: {str(exc)[:300]}")
            (self.dir / f"{name}_traceback.txt").write_text(traceback.format_exc(), encoding="utf-8")
            self.write_progress()
            return None


# ----------------------------------------------------------------------------- stage 0
def tech_from_tid_file(run: Run, path: str) -> list[dict]:
    """Ad-hoc Tech_Data rows from a plain TID list (optionally TID<TAB>station per line)."""
    rows = []
    now = datetime.now()
    for i, line in enumerate(Path(path).read_text(encoding="utf-8-sig").splitlines()):
        line = line.strip()
        if not line:
            continue
        tid, _, station = line.partition("\t")
        rows.append({"Sl": i, "case_id": "adhoc", "createdAt": now, "lastUpdatedAt": now,
                     "station_code": station.strip().upper(), "zone": "", "impacted_tracking_ids": tid.strip(),
                     "attachments": "", "status": "", "IssueUrl": "", "ShortId": ""})
    run.log(f"ad-hoc mode: {len(rows)} TIDs from {path}")
    return rows


def load_tech_data(path: Path) -> list[dict]:
    """Re-load Phase A's tech_data.csv for Phase B (two-phase pause-for-manual-SCC
    design — see --resume-dir below). Re-parses createdAt/lastUpdatedAt back into
    datetimes (blank -> None) to match the in-memory shape pull_tickets/
    tech_from_tid_file produce; every other field stays the string DictReader read."""
    rows = []
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            row = dict(row)
            for key in ("createdAt", "lastUpdatedAt"):
                value = (row.get(key) or "").strip()
                # Fix (audit 1.11): blank, "None" (older Phase A files) or an unreadable
                # date becomes None instead of crashing Phase B before its first stage.
                try:
                    row[key] = datetime.fromisoformat(value) if value and value != "None" else None
                except ValueError:
                    row[key] = None
            rows.append(row)
    return rows


def save_tech_data(run: "Run", rows: list[dict]) -> None:
    """Write tech_data.csv for Phase B. Fix (audit 1.11): a missing SIM-T date is written
    as "" (not "None"), so load_tech_data reads it back as a blank date."""
    def _text(value):
        return "" if value is None else str(value)

    run.save_csv("tech_data.csv", [{**r, "createdAt": _text(r.get("createdAt")),
                                    "lastUpdatedAt": _text(r.get("lastUpdatedAt"))} for r in rows])


def carry_over_phase_a(run: "Run") -> None:
    """Fix (audit 1.7): Phase B starts a fresh summary, which used to drop Phase A's
    preflight / SIM-T results, so the SIM-T card showed "pending" for the rest of the run.
    Copy them from Phase A's saved summary (summary_phase_a.json, else summary.json)."""
    for name in ("summary_phase_a.json", "summary.json"):
        try:
            previous = json.loads((run.dir / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        stages = previous.get("stages") or {}
        for stage in ("preflight", "tickets"):
            if isinstance(stages.get(stage), dict):
                run.summary["stages"][stage] = stages[stage]
        for key in ("preflight", "tickets"):
            if key in previous:
                run.summary[key] = previous[key]
        return


def preflight(run: Run, require_midway: bool = True, require_builder_mcp: bool = True):
    import time as _t
    cookie = Path.home() / ".midway" / "cookie"
    midway_ok = False
    if cookie.exists():
        now = _t.time()
        for line in cookie.read_text(encoding="utf-8").splitlines():
            parts = line.replace("#HttpOnly_", "").split("\t")
            if len(parts) >= 7 and parts[0].endswith("midway-auth.amazon.com") and parts[5] == "session":
                midway_ok = float(parts[4]) > now
    import edge_preflight

    edge_profile_processes = edge_preflight.profile_process_ids()
    checks = {
        "midway_session_valid": midway_ok,
        "mcscli": Path(r"C:\Program Files\Common Files\AmazonMidwayClientSuite\bin\mcscli.exe").exists(),
        "edge_profile": EDGE_PROFILE.exists(),
        "edge_profile_processes": edge_profile_processes,
        "template": ft.TEMPLATE.exists(),
        "builder_mcp": Path(BUILDER_MCP).exists(),
    }
    run.log(f"preflight: {checks}")
    run.summary["preflight"] = checks
    if require_midway and not checks["midway_session_valid"]:
        raise RuntimeError("Midway session expired — run `mwinit -o` and start again")
    if not checks["midway_session_valid"]:
        run.log("WARNING: Midway session expired — SIM-T and EagleEye lanes will fail; continuing (ad-hoc)")
    for key in ("edge_profile", "template"):
        if not checks[key]:
            raise RuntimeError(f"preflight failed: {key} missing")
    if edge_profile_processes:
        raise RuntimeError(
            "Edge automation profile is already in use; close that Edge window normally and retry"
        )
    if require_builder_mcp and not checks["builder_mcp"]:
        raise RuntimeError("preflight failed: builder_mcp missing")
    if not checks["mcscli"]:
        run.log("SIW mcscli is unavailable; authenticated Edge fallback will be used")
    return checks


# ----------------------------------------------------------------------------- stage 1
def pull_tickets(run: Run, max_pages: int | None):
    import tickets_pull as tp
    tp.LOG = run.dir / "tickets_pull.log"
    mcp = tp.open_mcp()
    try:
        tickets, total, pages = tp.fetch_all(mcp, max_pages)
        approvals = tp.scan_approvals(mcp, tickets, run.log)
    finally:
        mcp.close()
    rows = tp.explode(tickets, approvals)
    complete = len(tickets) == total
    run.log(f"tickets: {len(tickets)} / totalResults {total} (complete={complete}); rows={len(rows)}")
    if not complete and not max_pages:
        raise RuntimeError(f"ticket pull incomplete: {len(tickets)} of {total}")
    save_tech_data(run, rows)
    run.summary["tickets"] = {"tickets": len(tickets), "total_at_fetch": total, "rows": len(rows)}
    return rows


# ----------------------------------------------------------------------------- stage 3
def run_browser_worker(run: Run, name: str, command: list[str], timeout_s: int) -> None:
    """Run a browser lane in a killable process tree so a UI hang cannot stall the daily run.

    The worker's output is streamed into the run log line-by-line as it happens, so slow
    steps (e.g. the heavy DSR dashboard, which can take ~3 min to load) show live progress
    instead of looking frozen until the very end.
    """
    log_path = run.dir / f"{name}_worker.log"
    run.log(f"{name}: worker start (timeout {timeout_s}s)")
    log_file = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(
        command,
        cwd=HERE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )

    def _pump():
        # Forward every worker line to both the worker log file and the live run log.
        for line in proc.stdout:
            line = line.rstrip("\n")
            log_file.write(line + "\n")
            log_file.flush()
            run.log(f"{name}> {line}")

    pump = threading.Thread(target=_pump, name=f"{name}-pump", daemon=True)
    pump.start()
    try:
        return_code = proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        pump.join(timeout=5)
        log_file.close()
        raise RuntimeError(f"{name} browser worker exceeded {timeout_s}s and was terminated") from exc
    pump.join(timeout=10)
    log_file.close()
    if return_code:
        raise RuntimeError(f"{name} worker exited {return_code}; see {log_path}")


def write_tid_file(run: Run, name: str, tids: list[str]) -> Path:
    path = run.dir / f"{name}_tids.txt"
    path.write_text("\n".join(tids), encoding="utf-8")
    return path


SCC_REQUIRED_COLUMNS = ["Tracking ID", "State", "Destination", "Source", "Ship Option"]


def load_scc_csv(run: Run, tids: list[str], scc_csv_path: str) -> list[dict]:
    """Load a manually-uploaded SCC export in place of the retired browser lane.

    RETIRED 2026-10-12: SCC now blocks the automated browser flow (search + Export to
    CSV), and tools/scc_client.py (the module that drove that flow) has been removed
    from the project. SCC is now a manual step: the operator searches the SIM-T TIDs
    on the SCC site themselves and exports the CSV by hand, then uploads it through
    the web UI, which passes it to this run via --scc-csv. This loader reproduces the
    exact same output shape lane_scc used to produce (same columns, same scc.csv
    handoff file, same run.summary["scc"] shape), so every downstream lane (GCRS,
    Polygon, ADS, template fill) is unaffected and needs no changes.
    """
    src = Path(scc_csv_path)
    if not src.is_file():
        raise RuntimeError(f"--scc-csv file not found: {src}")
    output = run.dir / "scc.csv"
    shutil.copyfile(src, output)
    rows = ft.read_csv(output)
    missing_cols = [c for c in SCC_REQUIRED_COLUMNS if rows and c not in rows[0]]
    if rows and missing_cols:
        raise RuntimeError(f"uploaded SCC CSV is missing required columns: {missing_cols}")
    returned = {normalize_tid(row.get("Tracking ID")) for row in rows}
    missing = [tid for tid in tids if normalize_tid(tid) not in returned]
    run.summary["scc"] = {"requested": len(tids), "rows": len(rows), "missing": len(missing)}
    run.log(f"SCC (manual upload): accepted {len(rows)} output rows; "
            f"SIM-T TIDs not in the SCC file={len(missing)}")
    return rows


def lane_eagle(run: Run, tids):
    """EagleEye via the MCP API (fast, no browser).

    The browser bulk UI renders blank inside the automation Edge context, so we query
    the EagleEye endpoint directly. This is in-process and does not touch the Edge
    profile, so it can overlap the serial browser lanes.
    """
    import eagleeye_client

    rows = eagleeye_client.fetch(tids, log=run.log)
    run.save_csv("eagle.csv", rows)
    missing = sum(1 for row in rows if not row.get("type"))
    run.set_summary("eagle_missing", missing)
    return rows


def lane_gcrs(run: Run, tids, session):
    import gcrs_mcscli

    result = gcrs_mcscli.fetch(tids, log=run.log, session=session)
    rows, not_found, errored = result
    run.save_csv("gcrs.csv", rows)
    # Lock-safe: this lane runs on a background thread alongside the browser lanes.
    run.set_summary("gcrs", {
        "resolved": len(rows),
        "not_found": len(not_found),
        "errored": len(errored),
        "transport": session.summary(),
    })
    (run.dir / "gcrs_not_found.txt").write_text("\n".join(not_found), encoding="utf-8")
    (run.dir / "gcrs_errored.txt").write_text("\n".join(errored), encoding="utf-8")
    return result


def lane_pnama(run: Run, tids):
    import pnama_export

    tid_file = write_tid_file(run, "pnama", tids)
    output = run.dir / "pnama.csv"
    run_browser_worker(
        run,
        "pnama",
        [sys.executable, "-u", "-X", "utf8", str(HERE / "pnama_export.py"), "--tid-file", str(tid_file), "--out", str(output)],
        timeout_s=20 * 60,
    )
    # Read the exported CSV once and reuse the parsed rows for validation (validate would
    # otherwise re-open the same file). fieldnames preserve validate's own column view.
    with open(output, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        columns = reader.fieldnames or []
        rows = list(reader)
    res = pnama_export.validate(output, tids, rows=rows, columns=columns)
    run.summary["pnama"] = {k: (len(v) if isinstance(v, list) else v) for k, v in res.items() if k != "columns"}
    run.log(f"PNAMA: accepted rows={len(rows)}, missing={len(res['missing'])}, extra={len(res['extra'])}")
    if res["extra"]:
        raise RuntimeError(f"PNAMA output contains unrelated TIDs: {res['extra'][:5]}")
    return rows


def lane_dsr(run: Run, tids):
    import dsr_export

    tid_file = write_tid_file(run, "dsr", tids)
    output = run.dir / "dsr.csv"
    run_browser_worker(
        run,
        "dsr",
        [sys.executable, "-u", "-X", "utf8", str(HERE / "dsr_export.py"), "--tid-file", str(tid_file), "--out", str(output)],
        timeout_s=20 * 60,
    )
    # Read the exported CSV once and reuse the parsed rows for validation (validate would
    # otherwise re-open the same file). fieldnames preserve validate's own column view.
    with open(output, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        columns = reader.fieldnames or []
        rows = list(reader)
    result = dsr_export.validate(output, tids, rows=rows, columns=columns)
    run.summary["dsr"] = {key: (len(value) if isinstance(value, list) else value)
                          for key, value in result.items() if key != "columns"}
    run.log(f"DSR: accepted rows={len(rows)}, zone_col={result['zone_col']}, "
            f"blank_zone={result['blank_zone']}, extra={len(result['extra'])}")
    if result["extra"] or result.get("conflicting_zones") or not result["zone_col"]:
        raise RuntimeError("DSR output failed TID/Zone identity validation")
    if result["missing"] or result["blank_zone"]:
        run.log(
            f"DSR accepted source no-data as blank Zone: missing={len(result['missing'])} "
            f"blank={result['blank_zone']}"
        )
    return rows


POLYGON_NOT_IN_SCC = "Error — TID not in SCC file (not checked)"


def polygon_pairs_from_scc(scc_rows, tech_rows, dsr_rows=None) -> list[tuple[str, str]]:
    """(TID, station) pairs for the polygon check: ONLY the uploaded SCC file's TIDs
    (operator rule 2026-11-26), unique and in SCC order. Station = SCC Source when it is a
    real 4-character code, else the DSR station_code for that TID (same rule as Working
    Sheet E), else the ticket's SIM-T station code (a station, not a TID)."""
    tech_station = {}
    for row in tech_rows or []:
        tid = normalize_tid(row.get("impacted_tracking_ids"))
        if tid and tid not in tech_station:
            tech_station[tid] = str(row.get("station_code") or "").strip().upper()
    dsr_station = ft.dsr_station_map(dsr_rows)
    sources: dict[str, str] = {}
    for row in scc_rows or []:
        tid = normalize_tid(row.get("Tracking ID"))
        if not tid:
            continue
        source = str(row.get("Source") or "").strip().upper()
        if tid not in sources or (source and not sources[tid]):
            sources[tid] = source
    return [(tid, ft.effective_station(src, dsr_station.get(tid), tech_station.get(tid, "")))
            for tid, src in sources.items()]


def align_polygon_rows(scc_results: list[dict], tech_rows) -> list[dict]:
    """One polygon row per Working Sheet (SIM-T ticket) row, matched by TID. A ticket whose
    TID is not in the SCC file was never checked and says so."""
    by_tid = {normalize_tid(row.get("Tracking ID")): row for row in scc_results}
    aligned = []
    for row in tech_rows:
        tid_text = str(row.get("impacted_tracking_ids") or "").strip()
        hit = by_tid.get(normalize_tid(tid_text))
        if hit is not None:
            aligned.append({"Tracking ID": tid_text, "Station": hit.get("Station", ""),
                            "GCRS Status": hit.get("GCRS Status", ""),
                            "CX Address Check": hit.get("CX Address Check", "")})
        else:
            aligned.append({"Tracking ID": tid_text,
                            "Station": str(row.get("station_code") or "").strip().upper(),
                            "GCRS Status": POLYGON_NOT_IN_SCC, "CX Address Check": ""})
    return aligned


def lane_polygon(run: Run, gcrs_result, scc_rows, tech_rows, session, dsr_rows=None):
    import tid_polygon_checker as checker

    pairs = polygon_pairs_from_scc(scc_rows, tech_rows, dsr_rows)
    fixed = sum(1 for row in scc_rows or [] if not ft.is_station_code(row.get("Source")))
    if fixed:
        run.log(f"polygon: {fixed} SCC row(s) have no valid 4-character Source station; "
                "using the DSR station_code for those TIDs")
    inputs = checker.build_inputs_from_pairs(pairs)
    boundary_run = checker.run_boundary_check(
        inputs,
        session,
        log=run.log,
        # Always fetch live station geometry so polygon/location checks use realtime data.
        refresh_polygons=True,
        gcrs_result=gcrs_result,
    )
    scc_results = [result.as_row() for result in boundary_run.results]
    run.save_csv("polygon_scc.csv", scc_results)  # = the TID_Polygon_Check file content
    rows = align_polygon_rows(scc_results, tech_rows)
    if len(rows) != len(tech_rows):
        raise RuntimeError(f"polygon row-count mismatch: {len(rows)} vs Tech_Data {len(tech_rows)}")
    not_checked = sum(row["GCRS Status"] == POLYGON_NOT_IN_SCC for row in rows)
    run.log(f"polygon: checked {len(scc_results)} SCC TIDs; {not_checked} ticket row(s) have a TID "
            "that is not in the SCC file (marked 'not checked')")
    run.save_csv("polygon.csv", rows)
    polygon_workbook = run.dir / f"TID_Polygon_Check_{run.stamp}.xlsx"
    _, verification = checker.write_results_xlsx(boundary_run, polygon_workbook, overwrite=True)
    run.summary["polygon"] = {
        "rows": len(rows),
        "inside": boundary_run.summary["Inside"],
        "outside": boundary_run.summary["Outside"],
        "errors": boundary_run.summary["Errors"],
        "transport": session.summary(),
        "verification": verification,
        "workbook": str(polygon_workbook),
    }
    return rows


def lane_ads(run: Run, gcrs_rows, plaintext_upload_approved: bool = False):
    if not plaintext_upload_approved:
        raise RuntimeError(
            "ADS upload blocked: pass --allow-plaintext-ads only after explicit approval for this run"
        )
    import ads_run

    rows = ads_run.fetch(gcrs_rows, log_fn=run.log)
    run.save_csv("ads.csv", rows)
    requested_by_tid = {str(row.get("Query_Item")): row for row in gcrs_rows}
    returned = {str(row.get("TRACKINGID") or "").strip().split(".")[0] for row in rows}
    missing_tids = [tid for tid in requested_by_tid if tid not in returned]
    missing_rows = [{**requested_by_tid[tid], "ADS_Result": "No prediction returned"} for tid in missing_tids]
    run.save_csv("ads_missing.csv", missing_rows)
    missing_pincodes = Counter(str(requested_by_tid[tid].get("Postal_Code")) for tid in missing_tids)
    run.summary["ads"] = {
        "requested": len(requested_by_tid),
        "rows": len(rows),
        "missing": len(missing_tids),
        "missing_pincodes": dict(missing_pincodes),
        "transport": "PLAINTEXT_HTTP",
        "production_allowed": False,
    }
    run.log(f"ADS: requested {len(requested_by_tid)}, returned {len(rows)}, missing {len(missing_tids)}; "
            f"missing pincodes={dict(missing_pincodes)}")
    return rows


# ----------------------------------------------------------------------------- stage 4/5
def fill_and_recalc(
    run: Run,
    tech,
    scc,
    eagle,
    missort,
    gcrs,
    ads,
    dsr,
    polygon_rows,
    template_path: Path | None = None,
):
    template = Path(template_path) if template_path is not None else ft.TEMPLATE
    out = run.dir / f"OOJ_Working_{run.stamp}.xlsx"
    stats = ft.fill(
        template,
        out,
        tech,
        scc or [],
        eagle or [],
        missort or [],
        gcrs or [],
        ads or [],
        polygon_rows=polygon_rows or [],
        dsr_rows=dsr or [],
    )
    run.log(f"template filled: {json.dumps({k: v for k, v in stats.items() if k != 'working'}, default=str)}")
    res = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                          str(HERE / "recalc_excel.ps1"), "-Path", str(out),
                          "-PivotRows", str(len(tech))], capture_output=True, text=True, timeout=1800,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))  # no console flash
    if "RECALC_OK" not in res.stdout:
        raise RuntimeError(f"Excel recalc failed: {res.stderr[:300]}")
    run.log("Excel recalculation done")
    # Remark Summary pivot (operator request 2026-11-24). Never fatal.
    pivot_line = next((line for line in res.stdout.splitlines() if line.startswith("PIVOT_")), "")
    if pivot_line == "PIVOT_OK":
        run.log("Remark Summary sheet added (pivot: volume per Remark)")
    else:
        run.log(f"WARNING: Remark Summary pivot not added ({pivot_line or 'no result'}); workbook is otherwise complete")
    # Rule B Bucket column (operator spec 2026-11-25): plain-text bucket for every Valid
    # row, next empty Working Sheet column (AJ). Needs the recalculated values. Never fatal.
    try:
        import rule_b_bucket
        run.summary["rule_b_bucket"] = rule_b_bucket.apply(out, log=run.log)
    except Exception as exc:
        run.log(f"WARNING: Rule B Bucket column not added ({type(exc).__name__}: {str(exc)[:200]}); "
                "workbook is otherwise complete")
    return out


def read_back(
    run: Run,
    xlsx: Path,
    tech_rows: list[dict],
    polygon_rows: list[dict] | None = None,
    template_path: Path | None = None,
):
    """Read recalculated values and verify the 25-column row-aligned output."""
    template = Path(template_path) if template_path is not None else ft.TEMPLATE
    n_rows = len(tech_rows)
    wb = openpyxl.load_workbook(xlsx, data_only=True)
    formula_wb = openpyxl.load_workbook(xlsx, data_only=False)
    template_wb = openpyxl.load_workbook(template, data_only=False)
    generated_p2 = getattr(formula_wb[ft.SHEET_WORK]["P2"].value, "text", "")
    template_p2 = getattr(template_wb[ft.SHEET_WORK]["P2"].value, "text", "")
    normalize_formula = lambda text: re.sub(r"\s+", "", text or "").replace("_xlfn.", "").lstrip("=")
    # P stays a VISIBLE FORMULA: the template's P2 carries the operator's complete remark
    # formula and fill_template translates it down unchanged, so for row 2 the generated
    # P2 must EQUAL the template P2 (whitespace / "_xlfn." / leading "=" normalized).
    template_body = normalize_formula(template_p2)
    if not template_body or normalize_formula(generated_p2) != template_body:
        raise RuntimeError("Working Sheet P2 does not match the template remark formula")
    ws = wb[ft.SHEET_WORK]
    headers = [clean_header(cell.value) for cell in ws[1]]
    header_counts = Counter(headers)
    missing_headers = [column for column in FINAL_COLUMNS if header_counts[column] == 0]
    duplicate_headers = [column for column in FINAL_COLUMNS if header_counts[column] != 1]
    if missing_headers or duplicate_headers:
        raise RuntimeError(
            f"Working Sheet final-report header contract failed: missing={missing_headers}, "
            f"non_unique={duplicate_headers}"
        )

    working_rows = [dict(zip(headers, values))
                    for values in ws.iter_rows(min_row=2, max_row=1 + n_rows, values_only=True)]
    final_rows = [{column: row.get(column) for column in FINAL_COLUMNS} for row in working_rows]
    # Working Sheet A is an internal case_id lookup key despite its "IssueUrl" header in
    # the template. Correct the final presentation from Tech_Data without touching formulas.
    for index, final_row in enumerate(final_rows):
        tech_row = tech_rows[index]
        final_row["IssueUrl"] = tech_row.get("IssueUrl") or ""
        final_row["CreateDate"] = tech_row.get("createdAt")
        final_row["LastUpdatedDate"] = tech_row.get("lastUpdatedAt")

    expected_tids = [normalize_tid(row.get("impacted_tracking_ids")) for row in tech_rows]
    actual_tids = [normalize_tid(row.get("TID")) for row in final_rows]
    if len(final_rows) != len(tech_rows) or actual_tids != expected_tids:
        first_mismatch = next(
            (index for index, pair in enumerate(zip(expected_tids, actual_tids)) if pair[0] != pair[1]),
            None,
        )
        raise RuntimeError(
            f"final row/TID contract failed: final={len(final_rows)} tech={len(tech_rows)} "
            f"first_mismatch={first_mismatch}"
        )
    if polygon_rows:
        expected_polygon = [str(row.get("GCRS Status") or "") for row in polygon_rows]
        actual_polygon = [str(row.get("GCRS Agent Space") or "") for row in final_rows]
        if actual_polygon != expected_polygon:
            first_mismatch = next(
                (
                    index
                    for index, pair in enumerate(zip(expected_polygon, actual_polygon))
                    if pair[0] != pair[1]
                ),
                None,
            )
            raise RuntimeError(f"final row/polygon contract failed at row {first_mismatch}")
    bad_errors = [(row_index + 2, column, value)
                  for row_index, row in enumerate(final_rows)
                  for column, value in row.items() if value in EXCEL_ERRORS]
    if bad_errors:
        raise RuntimeError(f"non-#N/A Excel errors in final data: {bad_errors[:10]}")

    remarks = Counter(str(row.get("Remark")) for row in final_rows)
    na_cols = Counter()
    for row in final_rows:
        for header, value in row.items():
            if value == "#N/A":
                na_cols[header] += 1
    run.log(f"remarks (template P formula only): {dict(remarks.most_common())}")
    run.log(f"#N/A per final column: {dict(na_cols.most_common(10))}")
    run.summary["remarks"] = dict(remarks)
    run.summary["na_per_column"] = dict(na_cols)
    run.summary["validation_status"] = {
        "production_ready": False,
        "label": "VALIDATION ONLY",
        "review_required": bool(na_cols),
        "reasons": [
            "manual invalid/#N/A remark handling rules are not implemented yet",
            "ADS uploads TIDs/full addresses to a user-approved but plaintext HTTP endpoint",
            *([f"spreadsheet contains #N/A values in {len(na_cols)} final columns"] if na_cols else []),
        ],
    }

    full_csv = run.dir / f"working_sheet_{run.stamp}.csv"
    with open(full_csv, "w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=headers)
        writer.writeheader()
        writer.writerows(
            {header: csv_safe(value) for header, value in row.items()}
            for row in working_rows
        )

    final_csv = run.dir / f"OOJ_Validation_Data_{run.stamp}.csv"
    with open(final_csv, "w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=FINAL_COLUMNS)
        writer.writeheader()
        writer.writerows({column: csv_safe(row[column]) for column in FINAL_COLUMNS} for row in final_rows)

    final_xlsx = run.dir / f"OOJ_Validation_Data_{run.stamp}.xlsx"
    output_wb = openpyxl.Workbook()
    output_ws = output_wb.active
    output_ws.title = "Validation Data"
    output_ws.append(FINAL_COLUMNS)
    for row in final_rows:
        ft.append_literal_row(output_ws, [row[column] for column in FINAL_COLUMNS])
    for date_column in (2, 3):
        for cell in output_ws.iter_cols(min_col=date_column, max_col=date_column, min_row=2,
                                        max_row=len(final_rows) + 1):
            for value_cell in cell:
                value_cell.number_format = "yyyy-mm-dd hh:mm:ss"
    output_ws.freeze_panes = "A2"
    output_ws.auto_filter.ref = f"A1:Y{len(final_rows) + 1}"
    output_wb.save(final_xlsx)
    run.summary["final_data"] = {"rows": len(final_rows), "xlsx": str(final_xlsx), "csv": str(final_csv)}
    run.log(f"validation data: {len(final_rows)} rows, 25 columns -> {final_xlsx.name}")
    return final_rows


# ----------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--skip",
        default="",
        help="comma list of lanes to skip: scc,eagle,gcrs,pnama,dsr,ads,polygon",
    )
    ap.add_argument("--max-tids", type=int, default=None)
    ap.add_argument("--max-pages", type=int, default=None, help="limit SIM-T pages (testing)")
    ap.add_argument(
        "--tids-file",
        default=None,
        help="ad-hoc mode: TID list (one per line, optional <TAB>station) instead of SIM-T",
    )
    ap.add_argument("--no-midway-check", action="store_true", help="skip Midway check for ad-hoc lanes")
    ap.add_argument(
        "--scc-csv",
        default=None,
        help="path to a manually-uploaded SCC export CSV (replaces the retired automated SCC lane)",
    )
    # Two-phase pause-for-manual-SCC-upload design: Phase A (--stop-after-tickets) does
    # preflight + the SIM-T ticket pull only, then exits before the SCC stage so the
    # operator can search the TIDs on the SCC site and export the CSV by hand. Phase B
    # (--resume-dir) resumes in that SAME run directory once the operator has uploaded
    # the export, loading the frozen tech_data.csv/tids.txt Phase A wrote and then
    # running the unchanged SCC-onward pipeline (load_scc_csv -> EagleEye -> PNAMA ->
    # DSR -> GCRS -> Polygon -> ADS -> fill/recalc) exactly as a single-shot run would.
    ap.add_argument(
        "--stop-after-tickets",
        action="store_true",
        help="Phase A: preflight + SIM-T ticket pull only, then exit before the SCC stage",
    )
    ap.add_argument(
        "--resume-dir",
        default=None,
        help="Phase B: resume a run paused by --stop-after-tickets in this run directory",
    )
    ap.add_argument(
        "--allow-plaintext-ads",
        action="store_true",
        help="explicitly approve this run's TID/address upload to the plaintext ADS endpoint",
    )
    ap.add_argument(
        "--allow-partial",
        action="store_true",
        help="testing only: allow explicitly skipped lanes and label the output partial",
    )
    ap.add_argument(
        "--allow-source-failures",
        action="store_true",
        help="if a source fails to fetch, leave its sheet blank and still produce the "
             "downloadable workbook (labelled PARTIAL) instead of blocking the run",
    )
    ap.add_argument(
        "--skip-browser-signin",
        action="store_true",
        help="do not open the Edge window at Start to check the PNAMA/DSR sign-in",
    )
    args = ap.parse_args()
    skip = {value.strip().lower() for value in args.skip.split(",") if value.strip()}
    allowed_lanes = {"gcrs", "eagle", "scc", "pnama", "dsr", "ads", "polygon"}
    unknown_lanes = skip - allowed_lanes
    if unknown_lanes:
        ap.error(f"unknown --skip lanes: {sorted(unknown_lanes)}")
    if "gcrs" in skip and "polygon" not in skip:
        ap.error("polygon requires GCRS coordinates; skip polygon when skipping gcrs")
    if "ads" not in skip and not args.allow_plaintext_ads:
        ap.error(
            "ADS uploads TIDs and full addresses over plaintext HTTP; pass "
            "--allow-plaintext-ads only with explicit approval, or skip ads"
        )

    # --- Phase A/Phase B split point ---------------------------------------------
    # Two-phase pause-for-manual-SCC-upload design (see --stop-after-tickets/
    # --resume-dir help above): Phase B resumes an existing run directory that Phase A
    # already created and populated with tech_data.csv/tids.txt, instead of starting a
    # fresh stamped Run. Everything from here through the end of main() stays the same
    # source code for both phases (and for an unsplit, single-shot invocation) — only
    # how `run`, `tech` and `tids` get populated differs.
    if args.resume_dir:
        run = Run(Path(args.resume_dir).name, directory=Path(args.resume_dir))
        print(f"RUN_DIR={run.dir}", flush=True)
        run.log(f"=== OOJ daily run {run.stamp} RESUMED (Phase B) after manual SCC upload")
        carry_over_phase_a(run)
        tech = load_tech_data(run.dir / "tech_data.csv")
        tids = [
            line
            for line in (run.dir / "tids.txt").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        run.log(f"Phase B: loaded {len(tech)} Tech_Data rows and {len(tids)} SIM-T ticket TIDs from disk "
                "(lookups will use the SCC file's TIDs)")
    else:
        run = Run(datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
        # Make the active run directory discoverable to an external supervisor (the local
        # web runner) without guessing by mtime: print it as the first stdout line and
        # record a stable pointer file. This changes no pipeline behavior.
        print(f"RUN_DIR={run.dir}", flush=True)
        try:
            (RUNS / "_latest.txt").write_text(str(run.dir), encoding="utf-8")
        except OSError:
            pass
        run.log(
            f"=== OOJ daily run {run.stamp} skip={sorted(skip)} "
            f"max_tids={args.max_tids} tids_file={args.tids_file}"
        )
        # Operator rule: each run works only on freshly fetched data, so wipe ALL per-source
        # captures at the start of every fresh Start, and delete run folders / Desktop
        # exports older than RUN_KEEP_DAYS (3 days). This runs in Phase A
        # only; Phase B (--resume-dir) above never wipes — it resumes this same run dir.
        try:
            wipe_old_runs(run.dir, log=run.log)
        except Exception as exc:
            run.log(f"fresh-run wipe skipped: {type(exc).__name__}: {str(exc)[:120]}")

        if run.stage(
            "preflight",
            preflight,
            run,
            require_midway=not args.no_midway_check,
            require_builder_mcp=not bool(args.tids_file),
        ) is None:
            sys.exit(2)
        # Browser sign-in for the QuickSight lanes (PNAMA + DSR) while the operator is at
        # the laptop: opens the automation Edge window, waits at most 20 s for the dashboard
        # (Midway is picked up by the browser itself), then closes and the page pops up
        # "Browser accepted Midway". Never fatal — PNAMA/DSR keep their own login wait.
        # The Edge profile is free here (preflight checked it).
        if not args.skip_browser_signin and not {"pnama", "dsr"} <= skip:
            try:
                import browser_signin
                browser_signin.ensure_signed_in(log=run.log)
            except Exception as exc:
                run.log(f"browser sign-in skipped: {type(exc).__name__}: {str(exc)[:120]}")
        if args.tids_file:
            tech = run.stage("tickets", tech_from_tid_file, run, args.tids_file)
        else:
            tech = run.stage("tickets", pull_tickets, run, args.max_pages)
        if not tech:
            sys.exit(2)

        tids = derive_tids(tech)
        if args.max_tids:
            tids = tids[:args.max_tids]
            selected = set(tids)
            tech = [
                row
                for row in tech
                if str(row["impacted_tracking_ids"]) in selected
                or row["impacted_tracking_ids"] == "TID not found"
            ]
        run.log(f"SIM-T ticket TIDs (search these in SCC): {len(tids)}; ordered Tech_Data rows: {len(tech)}")
        (run.dir / "tids.txt").write_text("\n".join(tids), encoding="utf-8")
        # Fix (audit 1.17): Phase B reloads tech_data.csv, so it must hold exactly the rows
        # this phase kept: also after --max-tids filtering and for --tids-file runs.
        save_tech_data(run, tech)

        if args.stop_after_tickets:
            # Phase A stops here, before the SCC stage. The web controller's Phase B
            # spawn (--resume-dir) picks this run directory back up once the operator
            # has uploaded SCC's export CSV.
            run.write_progress()
            run.log(
                "=== Phase A done: preflight + SIM-T ticket pull complete. Stopping before "
                "the SCC stage; resume with --resume-dir once the SCC CSV is uploaded."
            )
            sys.exit(0)

    # Execution order shown step by step: SCC → EagleEye → PNAMA → DSR → GCRS → Polygon → ADS.
    #  * SCC runs first on the main thread (shared Edge profile); it produces the genuine
    #    TID set the later lanes use.
    #  * EagleEye is a pure API (no Edge), so once SCC is done it starts on a background
    #    thread and overlaps the remaining serial browser lanes (PNAMA, DSR). It uses the
    #    full ticket TID list.
    #  * PNAMA, DSR share the Edge profile with SCC, so they stay serial on the main thread
    #    and each uses the full ticket TID list.
    #  * GCRS input is the SCC OUTPUT (genuine/correct TIDs only): SCC returns no row for a
    #    wrong/invalid TID, so feeding SCC's resolved TIDs into GCRS removes the poison and
    #    degraded-window batches a raw ticket list produced. GCRS runs after the browser
    #    lanes (the Edge profile is then free for its Edge fallback).
    #  * Polygon needs SCC (station) + GCRS (coords); ADS needs GCRS. Both run on the
    #    reduced SCC-derived set.
    from siw_client import FailoverSiwSession

    concurrent: dict[str, object] = {"eagle": []}
    threads: list[threading.Thread] = []

    # Step 1 of the enrichment lanes: SCC first. RETIRED 2026-10-12: the automated
    # browser lane (shared Edge profile) is gone; SCC is now a manual upload (see
    # load_scc_csv above). When --scc-csv is given, load that upload here in SCC's
    # old pipeline position so every downstream lane is unaffected.
    if args.scc_csv:
        scc = run.stage("scc", load_scc_csv, run, tids, args.scc_csv)
    elif "scc" not in skip:
        scc = []
        run.log("SCC: no --scc-csv provided and automated SCC is retired; proceeding with 0 rows")
    else:
        scc = []

    # OPERATOR RULE (2026-11-26): every TID lookup (EagleEye, PNAMA, DSR, GCRS, ADS,
    # Polygon) uses ONLY the TIDs of the operator's uploaded SCC file (already cleaned:
    # incorrect and duplicate TIDs removed). SIM-T TIDs are never looked up; SIM-T still
    # supplies the ticket rows (URL, Short ID, dates, comments). No SCC TIDs = no lookups.
    lookup_tids = scc_lookup_tids(scc)
    if not lookup_tids and "scc" not in skip:
        run.log("STOPPED: the uploaded SCC file has no Tracking IDs. TID lookups use ONLY the "
                "SCC file, so nothing can be looked up. Upload the SCC export and run again.")
        run.summary["publication_blocked"] = {"failed_required": ["scc"], "skipped_required": [],
                                              "reason": "no TIDs in the uploaded SCC file"}
        run.write_progress()
        sys.exit(1)
    if not lookup_tids:
        lookup_tids = tids  # developer CLI only (--skip scc); the web page always uploads SCC
    sim_set = set(map(normalize_tid, tids))
    sim_only = len(sim_set - set(lookup_tids))
    run.log(f"TID lookups use the SCC file only: {len(lookup_tids)} TIDs "
            f"({len(tids)} ticket TIDs in SIM-T; {sim_only} of them are not in the SCC file "
            "and are not looked up)")
    # The SCC file is the SIM-T TID list after cleaning, so every SCC TID should also be a
    # SIM-T TID. If not, HIGHLIGHT it (log + page popup) but keep running (operator rule).
    match = scc_match_check(lookup_tids, sim_set)
    if match["warning"]:
        run.log(match["warning"])
    run.summary["lookup_tids"] = {"source": "scc", "count": len(lookup_tids),
                                  "sim_t_tids": len(tids), "sim_t_not_in_scc": sim_only,
                                  "scc_not_in_sim_t": match["not_in_sim_t"],
                                  "scc_matching_sim_t": match["matching"]}
    (run.dir / "lookup_tids.txt").write_text("\n".join(lookup_tids), encoding="utf-8")

    # Step 2: EagleEye (API) starts now and overlaps the remaining browser lanes.
    if "eagle" not in skip:
        def _run_eagle():
            concurrent["eagle"] = run.stage("eagle", lane_eagle, run, lookup_tids) or []
        t = threading.Thread(target=_run_eagle, name="eagle-lane", daemon=True)
        t.start(); threads.append(t)

    # Remaining serial browser lanes (shared Edge profile) on the main thread.
    pnama = run.stage("pnama", lane_pnama, run, lookup_tids) if "pnama" not in skip else []
    dsr = run.stage("dsr", lane_dsr, run, lookup_tids) if "dsr" not in skip else []

    for t in threads:
        t.join()
    eagle = concurrent["eagle"]

    # GCRS (and from it ADS + Polygon) = the same SCC lookup list. No SIM-T fallback.
    gcrs_input = lookup_tids

    gcrs_result = None
    polygon_rows = []
    siw_session = None
    if "gcrs" not in skip:
        siw_session = FailoverSiwSession(mode="auto", log=run.log)
        try:
            gcrs_result = run.stage("gcrs", lane_gcrs, run, gcrs_input, siw_session)
            if gcrs_result is not None and "polygon" not in skip:
                polygon_rows = run.stage(
                    "polygon", lane_polygon, run, gcrs_result, scc, tech, siw_session, dsr,
                ) or []
            run.summary["siw_transport"] = siw_session.summary()
        finally:
            if siw_session is not None:
                siw_session.close()
    gcrs_rows = gcrs_result[0] if gcrs_result is not None else []
    ads = run.stage(
        "ads",
        lane_ads,
        run,
        gcrs_rows,
        args.allow_plaintext_ads,
    ) if ("ads" not in skip and gcrs_rows) else []

    required_lanes = {"gcrs", "eagle", "scc", "pnama", "dsr", "ads", "polygon"}
    failed_required = [
        name
        for name in required_lanes
        if name not in skip and not run.summary["stages"].get(name, {}).get("ok", False)
    ]
    skipped_required = sorted(required_lanes & skip)
    # With --allow-source-failures, a failed source does NOT block: its sheet is left
    # blank (the lane already returned [] on failure) and the workbook is still produced,
    # labelled PARTIAL with the list of blank sources.
    if failed_required and args.allow_source_failures:
        run.summary["blank_sources"] = sorted(failed_required)
        run.log(
            f"PARTIAL MODE: sources failed and will be left blank: {sorted(failed_required)}; "
            "producing the workbook with the remaining data"
        )
        failed_required = []
    if failed_required or (skipped_required and not args.allow_partial):
        run.summary["publication_blocked"] = {
            "failed_required": sorted(failed_required),
            "skipped_required": skipped_required,
        }
        run.write_progress()
        run.log(
            f"PUBLICATION BLOCKED: failed={sorted(failed_required)} "
            f"skipped={skipped_required}; no workbook created"
        )
        sys.exit(1)
    blank_sources = run.summary.get("blank_sources") or []
    run.summary["report_mode"] = (
        "VALIDATION_PARTIAL" if (skipped_required or blank_sources) else "VALIDATION"
    )

    xlsx = run.stage(
        "fill_recalc",
        fill_and_recalc,
        run,
        tech,
        scc,
        eagle,
        pnama,
        gcrs_rows,
        ads,
        dsr,
        polygon_rows,
    )
    if xlsx:
        run.stage("read_back", read_back, run, xlsx, tech, polygon_rows)
        # NOTE: the Remark (column P) is NO LONGER flattened to a literal. P carries the
        # template's own P2 formula (the operator's complete remark formula, including the
        # #N/A / LM-to-reattempt AF->AG->Valid rules), translated down unchanged by
        # fill_template.extend_working_sheet, so the operator can trace every remark in the formula bar and
        # the recalc'd file still shows the correct final value.

    run.write_progress()
    run.log(f"=== done. Workbook: {xlsx}")
    failed = [name for name, value in run.summary["stages"].items() if value.get("ok") is False]
    # In partial mode the intentionally-blanked source lanes are expected to have failed;
    # only treat OTHER stage failures (fill/recalc/read_back) as a run error.
    blank_sources = set(run.summary.get("blank_sources") or [])
    unexpected = [name for name in failed if name not in blank_sources]
    if unexpected:
        run.log(f"stages with errors: {unexpected}")
        sys.exit(1)
    if failed:
        run.log(f"partial run complete; blank sources: {sorted(blank_sources)}")


if __name__ == "__main__":
    main()
