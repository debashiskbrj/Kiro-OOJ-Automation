r"""DMAIC Control-phase metrics for the OOJ Process Automation.

Owner & author: KABIDEBA (SWA India - Automation).

Run folders are deleted after 3 days, so their numbers would be lost. This module copies
the KEY NUMBERS of every run (counts and timings only - no TIDs, no addresses) into one
long-lived file, ``tools/metrics/run_history.csv``, and builds a control-chart summary
(individuals / I-MR chart: centre line and 3-sigma limits) for the Control phase.

* ``harvest()`` is called at every fresh Start just BEFORE old run folders are wiped, and
  by the weekly health check. It only adds rows for runs it has not stored yet.
* ``summary_text()`` / ``python tools\dmaic_metrics.py`` prints the control summary.

Read-only towards the pipeline: it never changes a run folder, the template or a report,
and every failure is swallowed so it can never stop a run.
"""
from __future__ import annotations

import csv
import json
import re
import statistics
import sys
from datetime import datetime
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
RUNS = TOOLS / "captures" / "runs"
METRICS_DIR = TOOLS / "metrics"
HISTORY = METRICS_DIR / "run_history.csv"

FIELDS = [
    "stamp", "date", "outcome", "tickets", "rows", "run_minutes", "machine_minutes",
    "failed_stages", "scc_missing", "eagle_missing", "dsr_missing", "ads_missing",
    "polygon_rows", "polygon_errors", "polygon_error_pct", "gcrs_transport",
    "valid", "invalid", "not_valid", "report_mode",
]
_TIME = re.compile(r"^\[(\d{2}):(\d{2}):(\d{2})\]")


def _log_minutes(run_dir: Path) -> float | None:
    """Wall-clock minutes from the first to the last run.log line (includes manual SCC wait)."""
    log = run_dir / "run.log"
    try:
        lines = [line for line in log.read_text(encoding="utf-8", errors="replace").splitlines()
                 if _TIME.match(line)]
    except OSError:
        return None
    if len(lines) < 2:
        return None
    to_s = lambda m: int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))  # noqa: E731
    start, end = to_s(_TIME.match(lines[0])), to_s(_TIME.match(lines[-1]))
    seconds = end - start if end >= start else end + 86400 - start
    return round(seconds / 60, 1)


def _rows(summary: dict, tickets: dict, polygon_rows):
    final = summary.get("final_data")
    if isinstance(final, dict) and isinstance(final.get("rows"), int):
        return final["rows"]
    if isinstance(polygon_rows, int):
        return polygon_rows
    return tickets.get("rows", "")


def row_for_run(run_dir: Path) -> dict | None:
    try:
        summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    except Exception:
        return None
    stamp = summary.get("stamp") or run_dir.name
    stages = summary.get("stages") or {}
    failed = sorted(name for name, info in stages.items() if isinstance(info, dict) and info.get("ok") is False)
    complete = "read_back" in stages and not failed
    if "read_back" in stages or "fill_recalc" in stages:
        outcome = "complete" if complete else "failed"
    elif "tickets" in stages:
        outcome = "stopped_after_simt"
    else:
        outcome = "not_started"
    machine = sum(float(info.get("seconds") or 0) for info in stages.values() if isinstance(info, dict))
    remarks = summary.get("remarks") or {}
    valid = sum(v for k, v in remarks.items() if str(k).strip().lower() == "valid")
    not_valid = sum(v for k, v in remarks.items() if str(k).lower().startswith("not valid"))
    invalid = sum(v for k, v in remarks.items() if str(k).lower().startswith("invalid"))
    polygon = summary.get("polygon") or {}
    p_rows, p_err = polygon.get("rows"), polygon.get("errors")
    tickets = summary.get("tickets") or {}
    if not tickets:  # two-phase runs keep the SIM-T numbers in Phase A's summary
        try:
            phase_a = json.loads((run_dir / "summary_phase_a.json").read_text(encoding="utf-8"))
            tickets = phase_a.get("tickets") or {}
        except Exception:
            pass
    try:
        day = datetime.strptime(stamp[:8], "%Y%m%d").strftime("%Y-%m-%d")
    except ValueError:
        day = ""
    return {
        "stamp": stamp, "date": day, "outcome": outcome,
        "tickets": tickets.get("tickets", ""),
        "rows": _rows(summary, tickets, p_rows),
        "run_minutes": _log_minutes(run_dir) or "",
        "machine_minutes": round(machine / 60, 1),
        "failed_stages": ";".join(failed),
        "scc_missing": (summary.get("scc") or {}).get("missing", ""),
        "eagle_missing": summary.get("eagle_missing", ""),
        "dsr_missing": (summary.get("dsr") or {}).get("missing", ""),
        "ads_missing": (summary.get("ads") or {}).get("missing", ""),
        "polygon_rows": p_rows if p_rows is not None else "",
        "polygon_errors": p_err if p_err is not None else "",
        "polygon_error_pct": round(100 * p_err / p_rows, 1) if p_rows and p_err is not None else "",
        "gcrs_transport": ((summary.get("gcrs") or {}).get("transport") or {}).get("selected_transport", ""),
        "valid": valid if remarks else "", "invalid": invalid if remarks else "",
        "not_valid": not_valid if remarks else "",
        "report_mode": summary.get("report_mode", ""),
    }


def read_history() -> list[dict]:
    try:
        with open(HISTORY, encoding="utf-8", newline="") as fh:
            return list(csv.DictReader(fh))
    except OSError:
        return []


def harvest(runs_dir: Path | None = None, log=lambda m: None) -> int:
    """Store new runs' numbers in run_history.csv. Returns rows added. Never raises."""
    try:
        runs_dir = runs_dir or RUNS
        if not runs_dir.exists():
            return 0
        known = {row.get("stamp") for row in read_history()}
        new_rows = []
        for run_dir in sorted(p for p in runs_dir.iterdir() if p.is_dir()):
            row = row_for_run(run_dir)
            if not row or row["stamp"] in known or row["outcome"] == "not_started":
                continue
            if row["outcome"] == "stopped_after_simt" and (run_dir / "summary.json").stat().st_mtime > \
                    datetime.now().timestamp() - 6 * 3600:
                continue  # may still be paused at SCC; store it on a later harvest
            new_rows.append(row)
        if not new_rows:
            return 0
        METRICS_DIR.mkdir(parents=True, exist_ok=True)
        write_header = not HISTORY.exists()
        with open(HISTORY, "a", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
            if write_header:
                writer.writeheader()
            writer.writerows(new_rows)
        log(f"DMAIC metrics: stored {len(new_rows)} run(s) in {HISTORY.name}")
        return len(new_rows)
    except Exception as exc:  # metrics must never block a run
        log(f"DMAIC metrics skipped ({type(exc).__name__}: {str(exc)[:120]})")
        return 0


def imr_limits(values: list[float]) -> dict | None:
    """Individuals chart: centre = mean, limits = mean +/- 2.66 x average moving range."""
    if len(values) < 2:
        return None
    mean = statistics.fmean(values)
    mr_bar = statistics.fmean(abs(b - a) for a, b in zip(values, values[1:]))
    ucl, lcl = mean + 2.66 * mr_bar, max(0.0, mean - 2.66 * mr_bar)
    out = [v for v in values if v > ucl or v < lcl]
    return {"n": len(values), "mean": mean, "ucl": ucl, "lcl": lcl, "last": values[-1], "out_of_control": len(out)}


def summary_text(rows: list[dict] | None = None) -> str:
    rows = read_history() if rows is None else rows
    finished = [r for r in rows if r.get("outcome") in ("complete", "failed")]
    lines = [f"DMAIC control summary - {len(rows)} run(s) stored, {len(finished)} reached the workbook stage"]
    if finished:
        ok = sum(1 for r in finished if r["outcome"] == "complete")
        lines.append(f"  First-pass yield (runs with no failed stage): {ok}/{len(finished)} = {100 * ok / len(finished):.0f}%")
    complete = [r for r in finished if r["outcome"] == "complete"]

    def series(key):
        vals = []
        for r in complete:
            try:
                vals.append(float(r[key]))
            except (TypeError, ValueError, KeyError):
                pass
        return vals

    for key, label, unit in (("run_minutes", "Run time (Start -> workbook, incl. manual SCC)", "min"),
                             ("machine_minutes", "Machine time (sum of stages)", "min"),
                             ("polygon_error_pct", "Polygon error rate", "%"),
                             ("rows", "Rows processed", "")):
        lim = imr_limits(series(key))
        if lim is None:
            lines.append(f"  {label}: need at least 2 complete runs")
            continue
        flag = "IN CONTROL" if lim["out_of_control"] == 0 else f"{lim['out_of_control']} point(s) OUT OF CONTROL"
        lines.append(f"  {label}: mean {lim['mean']:.1f}{unit}  UCL {lim['ucl']:.1f}  LCL {lim['lcl']:.1f}  "
                     f"last {lim['last']:.1f}  (n={lim['n']}) -> {flag}")
    if len(complete) < 20:
        lines.append(f"  Note: control limits are provisional until 20 complete runs (now {len(complete)}).")
    return "\n".join(lines)


def main() -> int:
    added = harvest(log=print)
    print(f"stored {added} new run(s); history file: {HISTORY}")
    print(summary_text())
    return 0


if __name__ == "__main__":
    sys.exit(main())
