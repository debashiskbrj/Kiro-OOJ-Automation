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
from ooj_paths import EDGE_PROFILE

HERE = Path(__file__).parent
CAPTURES = HERE / "captures"
RUNS = CAPTURES / "runs"
DESKTOP = Path.home() / "Desktop"
# The final filled workbook is published to the Desktop as
# "Templete_OOJ Process_<Month>_KABIDEBA1_<stamp>.xlsx" (full) or with a PARTIAL_ tag
# before the stamp (partial). Older such exports are pruned at the start of each fresh run
# so only today's output remains (see wipe_old_runs). This prefix MUST stay in sync with
# tools/web/run_controller.py's DESKTOP_EXPORT_PREFIX so the wipe matches what is published.
DESKTOP_EXPORT_PREFIX = "Templete_OOJ Process_"
# Local task history (run folders and per-source capture artifacts) is kept this many
# days; anything older is deleted automatically at the start of each run.
HISTORY_RETENTION_DAYS = 7
FINAL_COLUMNS = [
    "IssueUrl", "CreateDate", "LastUpdatedDate", "TID", "Station Code(SCC)", "Zone", "State",
    "Destination", "CX Pincode", "ADS Predicted Pincode", "Probability", "Pincode Validation", "Remark",
    "Predicted Pincode served by station", "Station Validation", "In - Swa", "Short ID", "URL",
    "RTO Status", "Past delivered record", "Delivered Old shipment", "Old shipment delivered by Station",
    "SCC & misort Station validation", "Station Transfer Validation", "GCRS Agent Space",
]


def prune_history(retention_days: int = HISTORY_RETENTION_DAYS, log=print) -> dict:
    """Delete local task history older than the retention window.

    Removes old run folders under captures/runs and old per-source capture artifacts
    (screenshots, blob downloads, cached polygons) under captures/<source>/. Keeps
    everything modified within the last ``retention_days`` days. Never touches source
    code, the template, tests, or anything outside captures/. Safe if a file is locked.
    """
    if not CAPTURES.exists():
        return {"removed_dirs": 0, "removed_files": 0}
    cutoff = time.time() - retention_days * 86400
    removed_dirs = 0
    removed_files = 0

    def _age_ok(path: Path) -> bool:
        try:
            return path.stat().st_mtime < cutoff
        except OSError:
            return False

    cutoff_dt = datetime.now() - timedelta(days=retention_days)

    def _name_is_old(name: str) -> bool | None:
        """Parse a run folder's YYYYMMDD_HHMMSS name stamp; None if it doesn't parse."""
        match = re.match(r"(\d{8}_\d{6})", name)
        if not match:
            return None
        try:
            return datetime.strptime(match.group(1), "%Y%m%d_%H%M%S") < cutoff_dt
        except ValueError:
            return None

    # 1. Old run folders (each run is one dated directory under captures/runs). Prefer the
    # timestamp in the folder name (reliable) and fall back to mtime if it doesn't parse.
    if RUNS.exists():
        for entry in RUNS.iterdir():
            if entry.name == "_latest.txt":
                continue
            if entry.is_dir():
                by_name = _name_is_old(entry.name)
                is_old = by_name if by_name is not None else _age_ok(entry)
                if is_old:
                    shutil.rmtree(entry, ignore_errors=True)
                    removed_dirs += 1
            elif entry.is_file() and _age_ok(entry):
                try:
                    entry.unlink()
                    removed_files += 1
                except OSError:
                    pass

    # 2. Old per-source capture artifacts (screenshots, downloads, cached polygons).
    # scc_enum_map.json is a long-lived overlay (accumulated SCC State enum->text ground
    # truth), so it is kept regardless of age.
    for source_dir in CAPTURES.iterdir():
        if not source_dir.is_dir() or source_dir.name == "runs":
            continue
        for entry in source_dir.rglob("*"):
            if entry.name == "scc_enum_map.json":
                continue
            if entry.is_file() and _age_ok(entry):
                try:
                    entry.unlink()
                    removed_files += 1
                except OSError:
                    pass

    log(f"history prune: removed {removed_dirs} old run folders and {removed_files} old files "
        f"(older than {retention_days} days)")
    return {"removed_dirs": removed_dirs, "removed_files": removed_files}


def wipe_old_runs(current_dir: Path, log=print) -> dict:
    """Delete ALL prior run data at the start of a fresh run (operator rule).

    Each automation run must work only on data it freshly fetched during that run, so
    at the start of every fresh Start (Phase A) we remove every previous run folder
    under captures/runs and every per-source capture artifact under captures/<source>/,
    regardless of age — a hard wipe, not a retention window.

    It also prunes OLD published exports from the Desktop ("Templete_OOJ Process_*.xlsx"),
    keeping only files produced today, so stale output workbooks do not pile up.

    Deliberately preserved:
      * ``current_dir`` — the run directory this very run just created (its freshly
        pulled SIM-T data must survive the wipe).
      * ``scc_enum_map.json`` — the long-lived SCC State enum->text ground-truth overlay.
      * Desktop exports produced TODAY — only exports from a previous day are removed, so
        a second run on the same day never deletes an export you made earlier today.
    Never touches source code, the template, tests, or any other Desktop file (only the
    automation's own "Templete_OOJ Process_*" workbooks). Safe if a file is locked (never raises).
    """
    if not CAPTURES.exists():
        return {"removed_dirs": 0, "removed_files": 0}
    try:
        current_resolved = current_dir.resolve()
    except OSError:
        current_resolved = current_dir
    removed_dirs = 0
    removed_files = 0

    # 1. Every prior run folder under captures/runs, except this run's own directory.
    if RUNS.exists():
        for entry in RUNS.iterdir():
            if entry.name == "_latest.txt":
                continue
            try:
                if entry.resolve() == current_resolved:
                    continue
            except OSError:
                pass
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
    for source_dir in CAPTURES.iterdir():
        if not source_dir.is_dir() or source_dir.name == "runs":
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
    # Keep today's; remove any from a previous day so stale outputs do not accumulate.
    # Only the automation's own export files are matched — no other Desktop file is touched.
    removed_exports = 0
    try:
        today = datetime.now().date()
        if DESKTOP.is_dir():
            for entry in DESKTOP.glob(f"{DESKTOP_EXPORT_PREFIX}*.xlsx"):
                if not entry.is_file():
                    continue
                try:
                    produced = datetime.fromtimestamp(entry.stat().st_mtime).date()
                except OSError:
                    continue
                if produced < today:
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
        f"(keeping this run's data, scc_enum_map.json, and today's exports)")
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
                value = row.get(key)
                row[key] = datetime.fromisoformat(value) if value else None
            rows.append(row)
    return rows


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
        "builder_mcp": Path(r"C:\Users\kabideba\AppData\Local\Toolbox\bin\builder-mcp.exe").exists(),
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
    finally:
        mcp.close()
    rows = tp.explode(tickets)
    complete = len(tickets) == total
    run.log(f"tickets: {len(tickets)} / totalResults {total} (complete={complete}); rows={len(rows)}")
    if not complete and not max_pages:
        raise RuntimeError(f"ticket pull incomplete: {len(tickets)} of {total}")
    run.save_csv("tech_data.csv", [{**r, "createdAt": str(r["createdAt"]), "lastUpdatedAt": str(r["lastUpdatedAt"])}
                                   for r in rows])
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
    run.log(f"SCC (manual upload): accepted {len(rows)} output rows; missing/no-data={len(missing)}")
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


def lane_polygon(run: Run, gcrs_result, scc_rows, tech_rows, session):
    import tid_polygon_checker as checker

    scc_station = {
        normalize_tid(row.get("Tracking ID")): str(row.get("Source") or "").strip().upper()
        for row in (scc_rows or [])
    }
    pairs = []
    for row in tech_rows:
        tid = str(row.get("impacted_tracking_ids") or "").strip()
        station = scc_station.get(normalize_tid(tid)) or str(row.get("station_code") or "").strip().upper()
        pairs.append((tid, station))
    inputs = checker.build_inputs_from_pairs(pairs)
    boundary_run = checker.run_boundary_check(
        inputs,
        session,
        log=run.log,
        # Always fetch live station geometry so polygon/location checks use realtime data.
        refresh_polygons=True,
        gcrs_result=gcrs_result,
    )
    rows = [result.as_row() for result in boundary_run.results]
    if len(rows) != len(tech_rows):
        raise RuntimeError(f"polygon row-count mismatch: {len(rows)} vs Tech_Data {len(tech_rows)}")
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
                          str(HERE / "recalc_excel.ps1"), "-Path", str(out)], capture_output=True, text=True, timeout=1800)
    if "RECALC_OK" not in res.stdout:
        raise RuntimeError(f"Excel recalc failed: {res.stderr[:300]}")
    run.log("Excel recalculation done")
    return out


def finalize_remarks(run: Run, xlsx: Path, tech_rows: list[dict]):
    """Rewrite Working Sheet column P to its final literal value per the operator's
    AF -> AD -> AG rules, preserving the six protected remarks. Runs after recalc and
    read_back so AF/AD/AG hold computed values and the integrity check has passed."""
    import apply_remarks

    summary = apply_remarks.apply_to_file(xlsx, xlsx, last_row=len(tech_rows) + 1)
    # Sanity: every Tech_Data row must have been processed (no silent row loss).
    if summary["rows"] != len(tech_rows):
        raise RuntimeError(
            f"remark rules processed {summary['rows']} rows but Tech_Data has "
            f"{len(tech_rows)}"
        )
    run.summary["remarks_applied"] = {
        "rows": summary["rows"],
        "protected": summary["protected"],
        "written": summary["written"],
    }
    run.log(f"remarks applied: {json.dumps(run.summary['remarks_applied'], default=str)}")
    return summary


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
    normalize_formula = lambda text: re.sub(r"\s+", "", text or "").replace("_xlfn.", "")
    if normalize_formula(generated_p2) != normalize_formula(template_p2):
        raise RuntimeError("Working Sheet P2 no longer matches the template remark formula")
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
        tech = load_tech_data(run.dir / "tech_data.csv")
        tids = [
            line
            for line in (run.dir / "tids.txt").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        run.log(f"Phase B: loaded {len(tech)} Tech_Data rows and {len(tids)} lookup TIDs from disk")
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
        # Operator rule: each run works only on freshly fetched data, so wipe ALL prior
        # run folders and captures at the start of every fresh Start (keeping only this
        # run's own directory and the scc_enum_map.json overlay). This runs in Phase A
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
        run.log(f"lookup TIDs: {len(tids)}; ordered Tech_Data rows: {len(tech)}")
        (run.dir / "tids.txt").write_text("\n".join(tids), encoding="utf-8")

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

    # Step 2: EagleEye (API) starts now and overlaps the remaining browser lanes.
    if "eagle" not in skip:
        def _run_eagle():
            concurrent["eagle"] = run.stage("eagle", lane_eagle, run, tids) or []
        t = threading.Thread(target=_run_eagle, name="eagle-lane", daemon=True)
        t.start(); threads.append(t)

    # Remaining serial browser lanes (shared Edge profile) on the main thread.
    pnama = run.stage("pnama", lane_pnama, run, tids) if "pnama" not in skip else []
    dsr = run.stage("dsr", lane_dsr, run, tids) if "dsr" not in skip else []

    for t in threads:
        t.join()
    eagle = concurrent["eagle"]

    # GCRS input = the genuine TIDs SCC returned, in order, de-duplicated. If SCC was
    # skipped or produced nothing, fall back to the full ticket list so GCRS still runs.
    scc_tids = list(dict.fromkeys(
        normalize_tid(row.get("Tracking ID"))
        for row in (scc or [])
        if normalize_tid(row.get("Tracking ID"))
    ))
    gcrs_input = scc_tids if scc_tids else tids
    if "scc" not in skip:
        run.log(f"GCRS/Polygon/ADS input = {len(gcrs_input)} SCC-resolved TIDs "
                f"(from {len(tids)} ticket TIDs)")

    gcrs_result = None
    polygon_rows = []
    siw_session = None
    if "gcrs" not in skip:
        siw_session = FailoverSiwSession(mode="auto", log=run.log)
        try:
            gcrs_result = run.stage("gcrs", lane_gcrs, run, gcrs_input, siw_session)
            if gcrs_result is not None and "polygon" not in skip:
                polygon_rows = run.stage(
                    "polygon", lane_polygon, run, gcrs_result, scc, tech, siw_session,
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
        read_ok = run.stage("read_back", read_back, run, xlsx, tech, polygon_rows) is not None
        # Apply the operator's Remark (column P) rules as the final finishing step,
        # AFTER read_back has verified the raw filled output (which still needs P as the
        # template formula). This turns P into its final literal value for the download.
        if read_ok:
            run.stage("apply_remarks", finalize_remarks, run, xlsx, tech)

    run.write_progress()
    run.log(f"=== done. Workbook: {xlsx}")
    failed = [name for name, value in run.summary["stages"].items() if value.get("ok") is False]
    # In partial mode the intentionally-blanked source lanes are expected to have failed;
    # only treat OTHER stage failures (fill/recalc/read_back/apply_remarks) as a run error.
    blank_sources = set(run.summary.get("blank_sources") or [])
    unexpected = [name for name in failed if name not in blank_sources]
    if unexpected:
        run.log(f"stages with errors: {unexpected}")
        sys.exit(1)
    if failed:
        run.log(f"partial run complete; blank sources: {sorted(blank_sources)}")


if __name__ == "__main__":
    main()
