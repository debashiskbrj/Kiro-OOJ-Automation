from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import scc_client


class _FakePage:
    """Minimal page double that fails the test if navigated during a retry."""

    def reload(self, *args, **kwargs):
        raise AssertionError("page.reload() must not be called during a chunk retry")

    def goto(self, *args, **kwargs):
        raise AssertionError("page.goto() must not be called during a chunk retry")


class _FakeFrame:
    pass


class ExportChunkRetryTests(unittest.TestCase):
    """_export_chunk_with_retry must retry a failed/zero-row chunk on the SAME page and
    frame — no page.reload() / _goto_search() — because a full navigation can collide
    with the AEA browser extension and destroy the page/context (edge-cases.md #C5)."""

    def test_retry_does_not_reload_or_renavigate_page(self):
        page = _FakePage()
        frame = _FakeFrame()
        calls = {"n": 0}

        def fake_export(_page, _frame, _chunk, _index, _download_directory, _log):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("transient UI failure")
            return [{"Tracking ID": "1"}]

        with patch.object(scc_client, "_browser_export_chunk", fake_export), \
                patch.object(scc_client, "_goto_search") as goto_search, \
                patch.object(scc_client.time, "sleep", lambda _s: None):
            rows, returned_frame, failed = scc_client._export_chunk_with_retry(
                page, frame, ["1"], index=1, total_chunks=1,
                download_directory=Path("."), log=lambda _m: None,
            )

        self.assertEqual(calls["n"], 2)
        self.assertFalse(failed)
        self.assertEqual(rows, [{"Tracking ID": "1"}])
        self.assertIs(returned_frame, frame)
        goto_search.assert_not_called()

    def test_persistent_failure_still_marks_chunk_failed_without_reload(self):
        page = _FakePage()
        frame = _FakeFrame()

        def always_fails(_page, _frame, _chunk, _index, _download_directory, _log):
            raise RuntimeError("page/UI failure")

        with patch.object(scc_client, "_browser_export_chunk", always_fails), \
                patch.object(scc_client, "_goto_search") as goto_search, \
                patch.object(scc_client.time, "sleep", lambda _s: None):
            rows, returned_frame, failed = scc_client._export_chunk_with_retry(
                page, frame, ["1"], index=1, total_chunks=1,
                download_directory=Path("."), log=lambda _m: None,
            )

        self.assertTrue(failed)
        self.assertEqual(rows, [])
        self.assertIs(returned_frame, frame)
        goto_search.assert_not_called()


if __name__ == "__main__":
    unittest.main()
