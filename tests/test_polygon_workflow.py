from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import openpyxl

import edge_preflight
import fill_template
import gcrs_mcscli
import polygon_check
import run_daily
import siw_client
import tid_polygon_checker
from siw_client import SiwReply


class FakeSiwSession:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def ensure_ready(self):
        return self

    def post_json(self, path: str, fields: dict, timeout_s: int = 180) -> SiwReply:
        del timeout_s
        self.calls.append((path, fields))
        if path == "grbi":
            tids = fields["ids"].split(",") if fields.get("ids") else []
            rows = []
            for tid in tids:
                rows.append({
                    "originalId": tid,
                    "addressId": f"address-{tid}",
                    "address": {"postalCode": "560001"},
                    "geocodingResult": {"latitude": 0.5, "longitude": 0.5},
                    "originalDetails": {},
                    "updatedDetails": {},
                })
            return SiwReply({"routeDetailsArray": rows, "invalidIds": []}, 12.0)
        if path == "gs":
            station = fields["stationNames"]
            return SiwReply({
                station: {
                    "ATS!DEFAULT": [{
                        "active": True,
                        "id": f"ATS-{station}",
                        "polygonSet": {"shipMethod": None, "effectiveDate": 1},
                    }],
                    "ATA!DEFAULT": [{
                        "active": True,
                        "id": f"ATA-{station}",
                        "polygonSet": {"shipMethod": None, "effectiveDate": 1},
                    }],
                }
            }, 0.1)
        if path == "gr":
            station_id = fields["stationIds"]
            if station_id.endswith("ST2"):
                coordinates = [[[10, 10], [11, 10], [11, 11], [10, 11], [10, 10]]]
            else:
                coordinates = [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]
            return SiwReply([{
                "routeName": "R1",
                "isSortZoneActive": True,
                "polygon": {"geometry": {"type": "Polygon", "coordinates": coordinates}},
            }], 0.1)
        if path == "gpsn":
            return SiwReply(["ATS!DEFAULT", "ATA!DEFAULT"], 0.1)
        raise AssertionError(path)

    def summary(self):
        return {
            "requested_mode": "fake",
            "selected_transport": "fake",
            "fallback_count": 0,
            "fallback_reason": "",
        }

    def close(self):
        return None


class EdgePreflightTests(unittest.TestCase):
    def test_user_data_dir_is_parsed_as_an_exact_argument(self):
        profile = r"C:\Users\Example User\edge_profile"
        command_lines = [
            rf'"C:\Program Files\Microsoft\Edge\msedge.exe" "--user-data-dir={profile}"',
            rf'"C:\Program Files\Microsoft\Edge\msedge.exe" --user-data-dir "{profile}"',
        ]
        for command_line in command_lines:
            self.assertEqual(profile, edge_preflight._user_data_dir(command_line))

    def test_lookalikes_and_unrelated_arguments_do_not_match(self):
        profile = r"C:\Users\Example User\edge_profile"
        backup = profile + "_backup"
        self.assertNotEqual(
            edge_preflight._normalized_path(profile),
            edge_preflight._normalized_path(backup),
        )
        self.assertIsNone(
            edge_preflight._user_data_dir(
                rf'"C:\Edge\msedge.exe" "https://example.invalid/?path={profile}"'
            )
        )


    def test_process_command_line_parse_failure_is_fail_closed(self):
        completed = subprocess.CompletedProcess(
            [],
            0,
            stdout='{"ProcessId":123,"CommandLine":"bad"}',
            stderr="",
        )
        with patch.object(edge_preflight.subprocess, "run", return_value=completed), patch.object(
            edge_preflight,
            "_user_data_dir",
            side_effect=OSError("parse failed"),
        ):
            with self.assertRaises(OSError):
                edge_preflight.profile_process_ids()
            with self.assertRaises(OSError):
                edge_preflight.repair_stale_locks()


class InputAndWorkbookTests(unittest.TestCase):
    def test_preserves_order_duplicates_and_pair_identity(self):
        text = (
            "123456789012\tST1\n"
            "123456789012\tST1\n"
            "123456789012\tST2\n"
            "DOC12345\tST1\n"
            "bad input line\n"
            "123456789013\t\n"
        )
        inputs = tid_polygon_checker.parse_input_text(text)
        self.assertEqual(6, len(inputs))
        self.assertEqual([0, 1, 2, 3, 4, 5], [item.sequence for item in inputs])
        self.assertEqual("ATA", inputs[3].carrier)
        self.assertIsNotNone(inputs[4].parse_error)

        session = FakeSiwSession()
        with tempfile.TemporaryDirectory() as cache_directory:
            with patch.object(polygon_check, "CACHE", Path(cache_directory)):
                run = tid_polygon_checker.run_boundary_check(inputs, session, log=lambda _: None)
                statuses = [result.status for result in run.results]
                self.assertEqual(polygon_check.INSIDE_TEXT, statuses[0])
                self.assertEqual(polygon_check.INSIDE_TEXT, statuses[1])
                self.assertEqual(polygon_check.OUTSIDE_TEXT, statuses[2])
                self.assertEqual(polygon_check.INSIDE_TEXT, statuses[3])
                self.assertTrue(statuses[4].startswith("Error — Invalid input line"))
                self.assertEqual("Error — Missing station code", statuses[5])

                with tempfile.TemporaryDirectory() as output_directory:
                    output = Path(output_directory) / "result.xlsx"
                    path, verification = tid_polygon_checker.write_results_xlsx(run, output)
                    self.assertEqual(output, path)
                    self.assertTrue(verification["verify_ok"])
                    self.assertEqual(6, verification["rows"])

        grbi_calls = [fields for path, fields in session.calls if path == "grbi"]
        self.assertEqual(2, len(grbi_calls))
        self.assertEqual("ATS!DEFAULT", grbi_calls[0]["polygonSetName"])
        self.assertEqual("ATA!DEFAULT", grbi_calls[1]["polygonSetName"])
        self.assertEqual("123456789012,123456789013", grbi_calls[0]["ids"])

    def test_geometry_keeps_inter_route_gap_outside(self):
        left = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}
        right = {"type": "Polygon", "coordinates": [[[2, 0], [3, 0], [3, 1], [2, 1], [2, 0]]]}
        self.assertTrue(polygon_check.point_in_geometry(0.5, 0.5, left))
        self.assertTrue(polygon_check.point_in_geometry(2.5, 0.5, right))
        self.assertFalse(any(polygon_check.point_in_geometry(1.5, 0.5, geometry) for geometry in (left, right)))


class FailoverTests(unittest.TestCase):
    def test_failed_mcscli_probe_switches_to_edge_once(self):
        class FailingMcs:
            name = "mcscli"

            def probe(self):
                raise siw_client.TransportError("HTTP 401")

            def close(self):
                return None

        class WorkingEdge:
            name = "edge"
            instances = 0

            def __init__(self, log=print):
                del log
                WorkingEdge.instances += 1

            def probe(self):
                return None

            def post_form(self, path, fields, timeout_s=180):
                del path, fields, timeout_s
                return SiwReply({}, 0.1)

            def close(self):
                return None

        with patch.object(siw_client, "McsCliTransport", FailingMcs), patch.object(siw_client, "EdgeInPageTransport", WorkingEdge):
            session = siw_client.FailoverSiwSession(log=lambda _: None)
            selected = session.ensure_ready()
            self.assertEqual("edge", selected.name)
            self.assertEqual(1, WorkingEdge.instances)
            self.assertEqual(1, session.summary()["fallback_count"])
            session.close()


class GuardTests(unittest.TestCase):
    def test_network_lookup_is_sequential_and_deduplicated(self):
        session = FakeSiwSession()
        rows, missing, errors = gcrs_mcscli.fetch(
            ["123456789012", "123456789012", "DOC12345"],
            log=lambda _: None,
            session=session,
        )
        self.assertEqual(2, len(rows))
        self.assertEqual([], missing)
        self.assertEqual([], errors)
        calls = [fields for path, fields in session.calls if path == "grbi"]
        self.assertEqual(2, len(calls))
        self.assertEqual("123456789012", calls[0]["ids"])
        self.assertEqual("DOC12345", calls[1]["ids"])


class FailurePathTests(unittest.TestCase):
    def test_partial_g1_recovery_still_revalidates_mass_invalid_ids(self):
        tids = [str(100000000000 + index) for index in range(60)]

        class PartialRecoverySession:
            def __init__(self):
                self.calls = 0

            def post_json(self, path, fields, timeout_s=180):
                del path, timeout_s
                self.calls += 1
                requested = fields["ids"].split(",")
                if self.calls == 1:
                    return SiwReply({"routeDetailsArray": [], "invalidIds": requested}, 1.0)
                if self.calls == 2:
                    resolved = requested[:1]
                    invalid = requested[1:]
                else:
                    resolved = requested
                    invalid = []
                rows = [
                    {
                        "originalId": tid,
                        "address": {},
                        "geocodingResult": {"latitude": 0.5, "longitude": 0.5},
                        "originalDetails": {},
                        "updatedDetails": {},
                    }
                    for tid in resolved
                ]
                return SiwReply({"routeDetailsArray": rows, "invalidIds": invalid}, 12.0)

            def close(self):
                return None

        session = PartialRecoverySession()
        with patch.object(gcrs_mcscli.time, "sleep", lambda _: None):
            rows, not_found, errored = gcrs_mcscli.fetch(tids, log=lambda _: None, session=session)
        self.assertEqual(60, len(rows))
        self.assertEqual([], not_found)
        self.assertEqual([], errored)
        self.assertEqual(3, session.calls)

    def test_edge_batch_failed_to_fetch_is_retried_then_resolves(self):
        class EdgeRetrySession:
            transport_name = "edge"

            def __init__(self):
                self.calls = 0

            def ensure_ready(self):
                return self

            def post_json(self, path, fields, timeout_s=180):
                del path, timeout_s
                self.calls += 1
                requested = fields["ids"].split(",") if fields.get("ids") else []
                if self.calls == 1:
                    raise siw_client.TransportError(
                        "Edge SIW grbi request failed: Page.evaluate: "
                        "TypeError: Failed to fetch"
                    )
                rows = [
                    {
                        "originalId": tid,
                        "address": {},
                        "geocodingResult": {"latitude": 0.5, "longitude": 0.5},
                        "originalDetails": {},
                        "updatedDetails": {},
                    }
                    for tid in requested
                ]
                return SiwReply({"routeDetailsArray": rows, "invalidIds": []}, 12.0)

            def close(self):
                return None

        session = EdgeRetrySession()
        with patch.object(gcrs_mcscli.time, "sleep", lambda _: None):
            rows, not_found, errored = gcrs_mcscli.fetch(
                ["123456789012", "123456789013"],
                log=lambda _: None,
                session=session,
            )
        self.assertEqual(2, len(rows))
        self.assertEqual([], not_found)
        self.assertEqual([], errored)
        self.assertEqual(2, session.calls)

    def test_edge_batch_failed_to_fetch_degrades_but_other_batch_resolves(self):
        first_batch = [str(100000000000 + index) for index in range(gcrs_mcscli.BATCH_EDGE)]
        second_batch = [str(200000000000 + index) for index in range(5)]

        class EdgeDegradeSession:
            transport_name = "edge"

            def ensure_ready(self):
                return self

            def post_json(self, path, fields, timeout_s=180):
                del path, timeout_s
                requested = fields["ids"].split(",") if fields.get("ids") else []
                if any(tid in first_batch for tid in requested):
                    raise siw_client.TransportError(
                        "Edge SIW grbi request failed: Failed to fetch"
                    )
                rows = [
                    {
                        "originalId": tid,
                        "address": {},
                        "geocodingResult": {"latitude": 0.5, "longitude": 0.5},
                        "originalDetails": {},
                        "updatedDetails": {},
                    }
                    for tid in requested
                ]
                return SiwReply({"routeDetailsArray": rows, "invalidIds": []}, 12.0)

            def close(self):
                return None

        session = EdgeDegradeSession()
        with patch.object(gcrs_mcscli.time, "sleep", lambda _: None):
            rows, not_found, errored = gcrs_mcscli.fetch(
                first_batch + second_batch,
                log=lambda _: None,
                session=session,
            )
        resolved_ids = {row["Query_Item"] for row in rows}
        self.assertEqual(set(second_batch), resolved_ids)
        self.assertEqual([], not_found)
        self.assertEqual(set(first_batch), set(errored))

    def test_mcscli_transport_error_is_not_retried(self):
        class McscliFailSession:
            transport_name = "mcscli"

            def __init__(self):
                self.calls = 0

            def ensure_ready(self):
                return self

            def post_json(self, path, fields, timeout_s=180):
                del path, fields, timeout_s
                self.calls += 1
                raise siw_client.TransportError("mcscli subprocess failed: Failed to fetch")

            def close(self):
                return None

        session = McscliFailSession()
        with patch.object(gcrs_mcscli.time, "sleep", lambda _: None):
            with self.assertRaises(siw_client.TransportError):
                gcrs_mcscli.fetch(["123456789012"], log=lambda _: None, session=session)
        self.assertEqual(1, session.calls)

    def test_non_finite_gps_becomes_error_not_outside(self):
        session = FakeSiwSession()
        gcrs_rows = [{"Query_Item": "123456789012", "Latitude": "nan", "Longitude": 0.5}]
        with tempfile.TemporaryDirectory() as cache_directory:
            with patch.object(polygon_check, "CACHE", Path(cache_directory)):
                rows = polygon_check.check_pairs(
                    [("123456789012", "ST1")],
                    gcrs_rows,
                    log=lambda _: None,
                    session=session,
                )
        self.assertTrue(rows[0]["GCRS Status"].startswith("Error —"))
        self.assertNotEqual(polygon_check.OUTSIDE_TEXT, rows[0]["GCRS Status"])

    def test_invalid_cache_is_refetched(self):
        session = FakeSiwSession()
        with tempfile.TemporaryDirectory() as cache_directory:
            cache = Path(cache_directory)
            (cache / "ATS_ST1.json").write_text(
                '[{"routeName":"bad","geometry":{"type":"Point","coordinates":[0,0]}}]',
                encoding="utf-8",
            )
            with patch.object(polygon_check, "CACHE", cache):
                rows = polygon_check.fetch_polygons("ST1", "ATS", session=session)
        self.assertEqual(1, len(rows))
        self.assertIn(("gs", {"stationNames": "ST1"}), session.calls)

    def test_incomplete_result_cannot_be_written_or_verified(self):
        inputs = tid_polygon_checker.parse_input_text("123456789012\tST1\n123456789013\tST1\n")
        incomplete = tid_polygon_checker.BoundaryRun(
            inputs=inputs,
            results=[tid_polygon_checker.BoundaryResult(inputs[0], polygon_check.OUTSIDE_TEXT)],
            gcrs_rows=[],
            not_found=[],
            errored=[],
            summary={"Total": 1, "Inside": 0, "Outside": 1, "Errors": 0},
            transport={},
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            with self.assertRaises(RuntimeError):
                tid_polygon_checker.write_results_xlsx(
                    incomplete,
                    Path(temporary_directory) / "incomplete.xlsx",
                )

    def test_mcscli_local_io_failures_are_transport_errors(self):
        transport = siw_client.McsCliTransport(executable=ROOT / "tools" / "siw_client.py")

        with patch.object(siw_client.tempfile, "mkstemp", side_effect=OSError("create")):
            with self.assertRaises(siw_client.TransportError):
                transport.post_form("gpsn", {})

        with patch.object(Path, "write_text", side_effect=OSError("write")):
            with self.assertRaises(siw_client.TransportError):
                transport.post_form("gpsn", {})

        with patch.object(siw_client.subprocess, "run", side_effect=OSError("execute")):
            with self.assertRaises(siw_client.TransportError):
                transport.post_form("gpsn", {})

        completed = subprocess.CompletedProcess([], 0, stdout="200", stderr="")
        with patch.object(siw_client.subprocess, "run", return_value=completed), patch.object(
            Path,
            "read_text",
            side_effect=OSError("read"),
        ):
            with self.assertRaises(siw_client.TransportError):
                transport.post_form("gpsn", {})

        original_mkstemp = tempfile.mkstemp
        with tempfile.TemporaryDirectory() as temporary_directory:
            def local_mkstemp(*, prefix, suffix):
                return original_mkstemp(prefix=prefix, suffix=suffix, dir=temporary_directory)

            with patch.object(siw_client.tempfile, "mkstemp", side_effect=local_mkstemp), patch.object(
                siw_client.subprocess,
                "run",
                side_effect=OSError("execute"),
            ), patch.object(Path, "unlink", side_effect=OSError("unlink")):
                with self.assertRaises(siw_client.TransportError) as raised:
                    transport.post_form("gpsn", {})
                self.assertIn("could not execute", str(raised.exception))

    def test_failed_edge_probe_is_closed(self):
        class FailingMcs:
            name = "mcscli"

            def probe(self):
                raise siw_client.TransportError("HTTP 401")

            def close(self):
                return None

        class FailingEdge:
            name = "edge"
            closed = False

            def __init__(self, log=print):
                del log

            def probe(self):
                raise siw_client.TransportError("probe failed")

            def close(self):
                FailingEdge.closed = True

        with patch.object(siw_client, "McsCliTransport", FailingMcs), patch.object(
            siw_client,
            "EdgeInPageTransport",
            FailingEdge,
        ):
            session = siw_client.FailoverSiwSession(log=lambda _: None)
            with self.assertRaises(siw_client.TransportError):
                session.ensure_ready()
        self.assertTrue(FailingEdge.closed)


class TemplateIntegrationTests(unittest.TestCase):
    def test_polygon_status_is_written_row_by_row(self):
        tech_rows = [
            {
                "Sl": index,
                "case_id": f"case-{index}",
                "createdAt": None,
                "lastUpdatedAt": None,
                "station_code": station,
                "zone": "",
                "impacted_tracking_ids": "123456789012",
                "attachments": "",
                "status": "Assigned",
                "IssueUrl": "",
                "ShortId": f"P{index}",
            }
            for index, station in enumerate(("ST1", "ST2"))
        ]
        polygon_rows = [
            {"Tracking ID": "123456789012", "Station": "ST1", "GCRS Status": polygon_check.INSIDE_TEXT},
            {"Tracking ID": "123456789012", "Station": "ST2", "GCRS Status": polygon_check.OUTSIDE_TEXT},
        ]
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory) / "working.xlsx"
            fill_template.fill(
                fill_template.TEMPLATE,
                output,
                tech_rows,
                [],
                [],
                [],
                [],
                [],
                polygon_rows=polygon_rows,
            )
            workbook = openpyxl.load_workbook(output, data_only=False)
            try:
                sheet = workbook[fill_template.SHEET_WORK]
                self.assertEqual(polygon_check.INSIDE_TEXT, sheet["AG2"].value)
                self.assertEqual(polygon_check.OUTSIDE_TEXT, sheet["AG3"].value)
            finally:
                workbook.close()

    def test_polygon_alignment_mismatch_is_rejected(self):
        tech = [{"impacted_tracking_ids": "123456789012"}]
        polygon = [{"Tracking ID": "999999999999", "Station": "ST1", "GCRS Status": "Outside"}]
        with tempfile.TemporaryDirectory() as temporary_directory:
            with self.assertRaises(ValueError):
                fill_template.fill(
                    fill_template.TEMPLATE,
                    Path(temporary_directory) / "bad.xlsx",
                    tech,
                    [],
                    [],
                    [],
                    [],
                    [],
                    polygon_rows=polygon,
                )

    def test_swapped_same_tid_stations_are_rejected(self):
        tech = [
            {"impacted_tracking_ids": "123456789012", "station_code": "ST1"},
            {"impacted_tracking_ids": "123456789012", "station_code": "ST2"},
        ]
        polygon = [
            {"Tracking ID": "123456789012", "Station": "ST2", "GCRS Status": "Outside"},
            {"Tracking ID": "123456789012", "Station": "ST1", "GCRS Status": "Outside"},
        ]
        with self.assertRaises(ValueError):
            fill_template.validate_polygon_rows(tech, [], polygon)

    def test_daily_requires_explicit_plaintext_ads_approval(self):
        completed = subprocess.run(
            [
                sys.executable,
                "-B",
                str(ROOT / "tools" / "run_daily.py"),
                "--tids-file",
                "unused.txt",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(2, completed.returncode)
        self.assertIn("--allow-plaintext-ads", completed.stderr)

    def test_daily_cli_has_no_agentspace_option_or_handoff(self):
        source = (ROOT / "tools" / "run_daily.py").read_text(encoding="utf-8").lower()
        self.assertNotIn("--agentspace-file", source)
        self.assertNotIn("agentspace_input.txt", source)
        self.assertNotIn("def handoffs", source)


if __name__ == "__main__":
    unittest.main()
