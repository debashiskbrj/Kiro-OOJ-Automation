"""Operator rule 2026-11-26: every TID lookup uses ONLY the uploaded SCC file's TIDs."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import run_daily  # noqa: E402


def test_scc_lookup_tids_are_unique_ordered_and_skip_blanks():
    rows = [{"Tracking ID": "375450935067"}, {"Tracking ID": " 375450935067 "},
            {"Tracking ID": ""}, {"Tracking ID": None}, {"Tracking ID": "123456789012"}]
    assert run_daily.scc_lookup_tids(rows) == ["375450935067", "123456789012"]
    assert run_daily.scc_lookup_tids([]) == [] and run_daily.scc_lookup_tids(None) == []


def test_every_lookup_lane_uses_the_scc_list_not_sim_t():
    src = (ROOT / "tools" / "run_daily.py").read_text(encoding="utf-8")
    assert re.search(r'run\.stage\("eagle", lane_eagle, run, lookup_tids\)', src)
    assert re.search(r'run\.stage\("pnama", lane_pnama, run, lookup_tids\)', src)
    assert re.search(r'run\.stage\("dsr", lane_dsr, run, lookup_tids\)', src)
    assert "gcrs_input = lookup_tids" in src
    assert "gcrs_input = scc_tids if scc_tids else tids" not in src   # old SIM-T fallback gone
    for lane in ("lane_eagle", "lane_pnama", "lane_dsr"):
        assert not re.search(rf'run\.stage\("\w+", {lane}, run, tids\)', src)


def test_scc_subset_of_sim_t_gives_no_warning():
    m = run_daily.scc_match_check(["1", "2"], ["1", "2", "3"])
    assert m == {"matching": 2, "not_in_sim_t": 0, "warning": ""}


def test_scc_not_matching_sim_t_is_highlighted_not_stopped():
    m = run_daily.scc_match_check(["1", "9"], ["1", "2"])
    assert m["matching"] == 1 and m["not_in_sim_t"] == 1
    assert m["warning"].startswith(run_daily.SCC_MISMATCH_TAG) and "continues" in m["warning"]
    none = run_daily.scc_match_check(["372939061095"], ["375573493031"])
    assert none["matching"] == 0 and "NONE" in none["warning"] and "old or wrong" in none["warning"]
    src = (ROOT / "tools" / "run_daily.py").read_text(encoding="utf-8")
    block = src[src.index("match = scc_match_check("):src.index("lane_eagle, run, lookup_tids")]
    assert "sys.exit" not in block                      # a mismatch never stops the run
    js = (ROOT / "tools" / "web" / "app.js").read_text(encoding="utf-8")
    assert run_daily.SCC_MISMATCH_TAG in js              # page popup matches the log line


def test_polygon_checks_only_scc_tids_once_with_scc_station():
    scc = [{"Tracking ID": "111111111111", "Source": "abc1"},
           {"Tracking ID": "111111111111", "Source": "ABC1"},       # duplicate: checked once
           {"Tracking ID": "222222222222", "Source": ""}]           # blank Source
    tech = [{"impacted_tracking_ids": "222222222222", "station_code": "xyz9"},
            {"impacted_tracking_ids": "999999999999", "station_code": "QQQ1"}]   # SIM-T only
    assert run_daily.polygon_pairs_from_scc(scc, tech) == [("111111111111", "ABC1"),
                                                          ("222222222222", "XYZ9")]


def test_polygon_rows_align_to_ticket_rows_by_tid():
    import fill_template as ft
    scc = [{"Tracking ID": "111111111111", "Source": "ABC1"}]
    tech = [{"impacted_tracking_ids": "999999999999", "station_code": "qqq1"},
            {"impacted_tracking_ids": "111111111111", "station_code": "ZZZ1"},
            {"impacted_tracking_ids": "111111111111", "station_code": "ZZZ1"}]   # same TID twice
    checked = [{"Tracking ID": "111111111111", "Station": "ABC1",
                "GCRS Status": ft.POLYGON_INSIDE_TEXT, "CX Address Check": "OK"}]
    rows = run_daily.align_polygon_rows(checked, tech)
    assert [r["GCRS Status"] for r in rows] == [run_daily.POLYGON_NOT_IN_SCC,
                                                ft.POLYGON_INSIDE_TEXT, ft.POLYGON_INSIDE_TEXT]
    assert rows[0]["Station"] == "QQQ1" and rows[1]["CX Address Check"] == "OK"
    # The workbook's own polygon validator accepts the aligned rows.
    assert ft.validate_polygon_rows(tech, scc, rows) == {"inside": 2, "outside": 0, "errors": 1}


def test_run_stops_when_scc_upload_has_no_tids():
    src = (ROOT / "tools" / "run_daily.py").read_text(encoding="utf-8")
    block = src[src.index("lookup_tids = scc_lookup_tids(scc)"):]
    assert block.index("sys.exit(1)") < block.index("lane_eagle")
