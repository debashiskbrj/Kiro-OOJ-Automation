"""wipe_old_runs keeps run folders and Desktop exports for RUN_KEEP_DAYS (3) days,
while still clearing per-source capture caches on every fresh Start."""
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import run_daily as rd  # noqa: E402


def _age(path: Path, days: float) -> None:
    stamp = time.time() - days * 86400
    os.utime(path, (stamp, stamp))


def test_keeps_last_three_days_and_wipes_caches(tmp_path, monkeypatch):
    captures = tmp_path / "captures"
    runs = captures / "runs"
    desktop = tmp_path / "Desktop"
    runs.mkdir(parents=True)
    desktop.mkdir()
    monkeypatch.setattr(rd, "CAPTURES", captures)
    monkeypatch.setattr(rd, "RUNS", runs)
    monkeypatch.setattr(rd, "DESKTOP", desktop)

    now = datetime.now()
    recent = runs / (now - timedelta(days=2)).strftime("%Y%m%d_%H%M%S")
    old = runs / (now - timedelta(days=4)).strftime("%Y%m%d_%H%M%S")
    current = runs / now.strftime("%Y%m%d_%H%M%S")
    for folder in (recent, old, current):
        folder.mkdir()

    cache_dir = captures / "pnama"
    cache_dir.mkdir()
    fresh_cache = cache_dir / "download.csv"
    fresh_cache.write_text("x")
    enum_map = cache_dir / "scc_enum_map.json"
    enum_map.write_text("{}")

    recent_export = desktop / f"{rd.DESKTOP_EXPORT_PREFIX}recent.xlsx"
    old_export = desktop / f"{rd.DESKTOP_EXPORT_PREFIX}old.xlsx"
    other_file = desktop / "notes.xlsx"
    for path in (recent_export, old_export, other_file):
        path.write_text("x")
    _age(recent_export, 2)
    _age(old_export, 4)
    _age(other_file, 30)

    result = rd.wipe_old_runs(current, log=lambda *_: None)

    assert current.exists() and recent.exists()
    assert not old.exists()
    assert not fresh_cache.exists()          # caches still wiped every Start
    assert enum_map.exists()
    assert recent_export.exists()
    assert not old_export.exists()
    assert other_file.exists()               # non-automation Desktop files never touched
    assert result == {"removed_dirs": 1, "removed_files": 1, "removed_exports": 1}
