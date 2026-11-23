"""DMAIC Control-phase metrics: harvest run numbers before the 3-day wipe, I-MR limits."""
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import dmaic_metrics as dm  # noqa: E402


def _run(runs: Path, stamp: str, stages: dict, extra: dict | None = None, log=("[15:00:00] a", "[15:31:00] b")):
    folder = runs / stamp
    folder.mkdir(parents=True)
    summary = {"stamp": stamp, "stages": stages}
    summary.update(extra or {})
    (folder / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    (folder / "run.log").write_text("\n".join(log), encoding="utf-8")
    old = time.time() - 2 * 86400
    os.utime(folder / "summary.json", (old, old))
    return folder


def test_harvest_stores_numbers_once_and_no_tids(tmp_path, monkeypatch):
    monkeypatch.setattr(dm, "METRICS_DIR", tmp_path / "metrics")
    monkeypatch.setattr(dm, "HISTORY", tmp_path / "metrics" / "run_history.csv")
    runs = tmp_path / "runs"
    ok = {"ok": True, "seconds": 60}
    _run(runs, "20261120_150000_1", {"tickets": ok, "dsr": ok, "fill_recalc": ok, "read_back": ok},
         {"polygon": {"rows": 100, "errors": 17}, "dsr": {"missing": 3},
          "remarks": {"Valid": 40, "Invalid - Already RTOed": 50, "Not Valid - OOR": 10}})
    _run(runs, "20261121_150000_1", {"tickets": ok})  # stopped after SIM-T
    _run(runs, "20261122_150000_1", {"preflight": ok})  # never reached SIM-T -> not stored
    assert dm.harvest(runs) == 2
    assert dm.harvest(runs) == 0  # idempotent
    rows = dm.read_history()
    first = rows[0]
    assert first["outcome"] == "complete"
    assert first["run_minutes"] == "31.0" and first["machine_minutes"] == "4.0"
    assert first["polygon_error_pct"] == "17.0" and first["dsr_missing"] == "3"
    assert (first["valid"], first["invalid"], first["not_valid"]) == ("40", "50", "10")
    assert rows[1]["outcome"] == "stopped_after_simt"
    text = dm.HISTORY.read_text(encoding="utf-8")
    assert "tracking" not in text.lower()


def test_imr_limits_flag_outlier():
    lim = dm.imr_limits([30, 31, 29, 30, 32, 30, 31, 90])
    assert lim["out_of_control"] >= 1
    assert dm.imr_limits([5]) is None
