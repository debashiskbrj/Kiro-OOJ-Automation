"""The daily sanitizer removes output-corrupting scratch and nothing else."""
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))

import sanitize_captures as sc  # noqa: E402


def _touch(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_removes_scratch_but_keeps_real_results():
    with tempfile.TemporaryDirectory() as cap_dir, tempfile.TemporaryDirectory() as dl_dir:
        captures = Path(cap_dir)
        downloads = Path(dl_dir)

        # Scratch that MUST be removed.
        bd_leftover = _touch(captures / "pnama" / "_browser_downloads" / "old.csv")
        bd_partial = _touch(captures / "scc" / "_browser_downloads" / "run_x" / "part.crdownload")
        stray_partial = _touch(captures / "dsr" / "something.crdownload")
        sidecar = _touch(captures / "polygons" / ".ATS_ST1.json.deadbeef123.tmp")
        sidecar_xlsx = _touch(captures / "runs" / "r1" / ".OOJ.abc123def.tmp.xlsx")
        dl_leftover = _touch(downloads / "SWA_repeat_Prox_Pnam_123.csv")
        dl_leftover2 = _touch(downloads / "DSR_Raw_data_9.csv")

        # Real files that MUST be kept.
        real_result = _touch(captures / "runs" / "r1" / "scc.csv")
        real_polygon = _touch(captures / "polygons" / "ATS_ST1.json")
        unrelated_download = _touch(downloads / "my_personal_notes.csv")

        with patch.object(sc, "CAPTURES", captures), patch.object(sc, "DOWNLOADS", downloads):
            summary = sc.sanitize(log=lambda _: None)

        # Removed
        assert not bd_leftover.exists()
        assert not bd_partial.exists()
        assert not stray_partial.exists()
        assert not sidecar.exists()
        assert not sidecar_xlsx.exists()
        assert not dl_leftover.exists()
        assert not dl_leftover2.exists()

        # Kept
        assert real_result.exists()
        assert real_polygon.exists()
        assert unrelated_download.exists()

        assert summary["browser_downloads"] == 2
        assert summary["partial_temp"] == 3  # stray_partial + sidecar + sidecar_xlsx
        assert summary["download_leftovers"] == 2


def test_missing_dirs_are_safe():
    with tempfile.TemporaryDirectory() as base:
        missing_cap = Path(base) / "no_captures"
        missing_dl = Path(base) / "no_downloads"
        with patch.object(sc, "CAPTURES", missing_cap), patch.object(sc, "DOWNLOADS", missing_dl):
            summary = sc.sanitize(log=lambda _: None)
        assert summary == {"browser_downloads": 0, "partial_temp": 0, "download_leftovers": 0}


def test_never_touches_unrelated_downloads():
    with tempfile.TemporaryDirectory() as cap_dir, tempfile.TemporaryDirectory() as dl_dir:
        captures = Path(cap_dir)
        downloads = Path(dl_dir)
        keep = [
            _touch(downloads / "report_final.xlsx"),
            _touch(downloads / "invoice.csv"),
            _touch(downloads / "SearchResultsSummary.docx"),  # wrong extension -> keep
        ]
        with patch.object(sc, "CAPTURES", captures), patch.object(sc, "DOWNLOADS", downloads):
            sc.sanitize(log=lambda _: None)
        for f in keep:
            assert f.exists()
