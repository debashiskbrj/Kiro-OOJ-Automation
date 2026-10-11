"""SCC package-data client using the authenticated local Edge profile.

SCC has two confirmed result paths:
- up to 1,000 requested TIDs: search in chunks of at most 500, wait for the result,
  then click ``Export to CSV``;
- more than 1,000 TIDs: select ``Download Default columns only``, search once, and
  wait for SCC's automatic CSV download.

Every browser attempt has a unique download directory, so a stale/partial download
cannot be mistaken for the current result. Remote behavior is query/export only.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from playwright.sync_api import sync_playwright

import browser_blob_capture
from ooj_paths import EDGE_PROFILE

PROFILE_DIR = str(EDGE_PROFILE)
SCC_URL = "https://logistics.amazon.in/station/dashboard/search"
SCC_ORIGIN = "https://logistics.amazon.in"
SCC_API_URL = "https://logistics.amazon.in/station/proxyapigateway/data"
DIRECT_APP_URL = (
    "https://ui.package-summary-inzaz.last-mile.amazon.dev/"
    "?stationCode=IN_ROOT&cycleId=AllCycles#/detailPage"
)
OUT = Path(__file__).parent / "captures" / "scc"
DOWNLOAD_ROOT = OUT / "_browser_downloads"
OUT.mkdir(parents=True, exist_ok=True)
DOWNLOAD_ROOT.mkdir(parents=True, exist_ok=True)
SMALL_MODE_MAX = 1_000
SMALL_CHUNK = 500
LARGE_BATCH_MAX = 10_000
# TIDs pasted into the SCC search box per browser search. Up to 5,000 per search.
# Result path is chosen by count (see _browser_export_chunk):
#   - <= AUTO_DOWNLOAD_THRESHOLD: the UI shows "Export to CSV"; click it and capture.
#   - >  AUTO_DOWNLOAD_THRESHOLD: SCC automatically downloads the result file; detect
#     the downloaded file and read it (no Export button appears).
SCC_UI_CHUNK = 5000
# SCC switches to automatic file download above 1,000 TrackingIds (the ">1000" checkbox).
AUTO_DOWNLOAD_THRESHOLD = 1000
# If a chunk's Search/Export fails (page didn't load, Export never appeared, download
# stalled), refresh the SCC tab and search again, up to this many attempts. A wrong/
# invalid TID is NOT a failure — it just returns no row for that TID.
SCC_CHUNK_ATTEMPTS = 3
# How often _wait_for_download emits a throttled "still waiting" heartbeat when a
# caller passes a log (this function and its optional heartbeat predate the Oct 8
# wait-resilience change and are not part of this rollback).
SCC_WAIT_HEARTBEAT_S = 30
REQUIRED_COLUMNS = ["Tracking ID", "State", "Destination", "Source", "Ship Option"]


def _new_download_directory() -> Path:
    path = DOWNLOAD_ROOT / f"run_{datetime.now():%Y%m%d_%H%M%S}_{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def _open(playwright, download_directory: Path):
    return playwright.chromium.launch_persistent_context(
        user_data_dir=PROFILE_DIR,
        channel="msedge",
        headless=False,
        viewport={"width": 1400, "height": 900},
        accept_downloads=True,
        downloads_path=str(download_directory),
        ignore_default_args=[
            "--disable-extensions",
            "--disable-component-extensions-with-background-pages",
            "--disable-component-update",
            "--disable-background-networking",
        ],
        timeout=90_000,
    )


def _configure_download_directory(context, page, download_directory: Path):
    cdp = context.new_cdp_session(page)
    parameters = {
        "behavior": "allow",
        "downloadPath": str(download_directory.resolve()),
        "eventsEnabled": True,
    }
    try:
        cdp.send("Browser.setDownloadBehavior", parameters)
    except Exception:
        cdp.send(
            "Page.setDownloadBehavior",
            {"behavior": "allow", "downloadPath": str(download_directory.resolve())},
        )
    return cdp


def _wait_for_download(
    download_directory: Path,
    timeout_s: int,
    before: set[str] | None = None,
    log=None,
) -> Path:
    # ``log`` defaults to None (silent) so every existing caller is byte-for-byte
    # unchanged; when provided, a throttled heartbeat makes a slow wait observable.
    before = before or set()
    start = time.time()
    deadline = start + timeout_s
    last_heartbeat = start
    candidate: Path | None = None
    last_size = -1
    stable_since: float | None = None
    while time.time() < deadline:
        files = [
            item
            for item in download_directory.glob("*")
            if item.is_file()
            and item.name not in before
            and item.suffix.lower() not in {".crdownload", ".tmp", ".partial"}
        ]
        if files:
            newest = max(files, key=lambda item: item.stat().st_mtime)
            size = newest.stat().st_size
            if newest != candidate or size != last_size:
                candidate, last_size, stable_since = newest, size, time.time()
                if log is not None:
                    log(f"SCC file wait: download in progress: {size} bytes")
            elif size > 0 and stable_since and time.time() - stable_since >= 2:
                return newest
        if log is not None and time.time() - last_heartbeat >= SCC_WAIT_HEARTBEAT_S:
            elapsed = time.time() - start
            log(f"SCC file wait: still waiting for a stable CSV "
                f"({int(elapsed)}s elapsed, timeout {timeout_s}s)")
            last_heartbeat = time.time()
        time.sleep(1)
    raise RuntimeError(f"SCC produced no stable CSV within {timeout_s}s")


# Text SCC shows in its popup when a search returns nothing / errors out. Matched
# case-insensitively against the page's visible text after Search is clicked.
_NOT_FOUND_MARKERS = ("not found", "no data", "no record", "no results", "no matching")


def _search_error_popup(frame) -> str | None:
    """Return the popup text if SCC showed a 'not found'/no-data error, else None."""
    try:
        body = frame.locator("body").inner_text(timeout=2000)
    except Exception:
        return None
    lowered = body.lower()
    for marker in _NOT_FOUND_MARKERS:
        if marker in lowered:
            # Return a short snippet around the marker for the log.
            start = max(0, lowered.find(marker) - 20)
            return body[start:start + 80].replace("\n", " ").strip()
    return None


def _wait_for_download_or_notfound(page, frame, download_directory: Path, before: set[str],
                                   index: int, timeout_s: int, log) -> Path:
    """After Search on a large (>1000) search, detect exactly two outcomes:

      1. a 'not found'/no-data error popup -> raise, so the caller restarts the SCC steps
         (refresh the tab and search again);
      2. SCC's automatic download -> return the freshly downloaded, stable file.
    """
    deadline = time.time() + timeout_s
    candidate: Path | None = None
    last_size = -1
    stable_since: float | None = None
    while time.time() < deadline:
        # (1) automatic download: a new, stable, non-partial file appeared.
        files = [
            item for item in download_directory.glob("*")
            if item.is_file() and item.name not in before
            and item.suffix.lower() not in {".crdownload", ".tmp", ".partial"}
        ]
        if files:
            newest = max(files, key=lambda item: item.stat().st_mtime)
            size = newest.stat().st_size
            if newest != candidate or size != last_size:
                candidate, last_size, stable_since = newest, size, time.time()
                log(f"SCC chunk {index}: download in progress: {size} bytes")
            elif size > 0 and stable_since and time.time() - stable_since >= 2:
                log(f"SCC chunk {index}: detected automatic download {newest.name}")
                return newest
        # (2) 'not found' popup: raise so the retry wrapper restarts the SCC steps. Only
        # check while no download has started yet, so a real download always wins.
        if candidate is None:
            popup = _search_error_popup(frame)
            if popup:
                page.screenshot(path=str(OUT / f"scc_chunk{index}_notfound_{datetime.now():%H%M%S}.png"))
                raise RuntimeError(f"SCC chunk {index}: 'not found' popup after Search ({popup}); restarting")
        time.sleep(1)
    page.screenshot(path=str(OUT / f"scc_chunk{index}_nodownload_{datetime.now():%H%M%S}.png"))
    raise RuntimeError(f"SCC chunk {index}: no automatic download within {timeout_s}s")


def _goto_search(page, log):
    try:
        page.goto(SCC_URL, timeout=90_000, wait_until="domcontentloaded")
    except Exception as exc:
        log(f"SCC goto interrupted (AEA): {str(exc)[:60]}")
    direct_attempted = False
    for index in range(150):
        time.sleep(2)
        url = page.url
        if url == "about:blank" and index % 3 == 2:
            try:
                page.goto(SCC_URL, timeout=60_000, wait_until="domcontentloaded")
            except Exception:
                pass
            continue
        if any(marker in url.lower() for marker in ("midway", "sentry", "/sso/")):
            if index % 10 == 0:
                log("SCC: authenticate in the opened browser window")
            continue
        frame = _search_frame(page)
        if frame is not None:
            return frame
        if index == 20 and not direct_attempted:
            direct_attempted = True
            log("SCC wrapper still loading; navigating to package-summary directly")
            try:
                page.goto(DIRECT_APP_URL, timeout=90_000, wait_until="domcontentloaded")
            except Exception as exc:
                log(f"SCC direct-app navigation interrupted: {str(exc)[:80]}")
        if index % 15 == 14:
            log(f"SCC waiting: url={url[:100]}")
            page.screenshot(path=str(OUT / f"scc_wait_{datetime.now():%H%M%S}.png"))
    page.screenshot(path=str(OUT / f"scc_fail_{datetime.now():%H%M%S}.png"))
    current = page.url.lower()
    if any(marker in current for marker in ("midway", "sentry", "/sso/", "login")):
        raise RuntimeError(
            "SCC not authenticated: the automation Edge profile is not signed in to "
            f"logistics.amazon.in. Sign in there once, then re-run. (url={page.url[:120]})"
        )
    raise RuntimeError(
        "SCC search form did not appear — likely not signed in to SCC in the automation "
        f"Edge profile, or the page failed to load. (url={page.url[:120]})"
    )


def _search_frame(page):
    frames = sorted(page.frames, key=lambda frame: "package-summary" not in frame.url)
    for frame in frames:
        try:
            if frame.locator("textarea").count() and frame.get_by_role("button", name="Search").count():
                return frame
        except Exception:
            continue
    return None


def _search_response_for(requested: list[str]):
    expected = [_normalize_tid(value) for value in requested]

    def matches(response) -> bool:
        if "/station/proxyapigateway/data" not in response.url:
            return False
        try:
            payload = json.loads(response.request.post_data or "{}")
            if payload.get("resourcePath") != "/os/batchGetPackageSummary":
                return False
            request_body = payload.get("requestBody") or {}
            if isinstance(request_body, str):
                request_body = json.loads(request_body)
            identifiers = [_normalize_tid(value) for value in request_body.get("identifiers") or []]
        except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
            return False
        return identifiers == expected

    return matches


def _normalize_tid(value: object) -> str:
    text = str(value or "").strip().replace("'", "")
    return text[:-2] if text.endswith(".0") and text[:-2].isdigit() else text


def _read_csv(path: Path) -> tuple[list[dict], list[str]]:
    with open(path, encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        return list(reader), reader.fieldnames or []


def _validate_rows(rows: list[dict], headers: list[str], requested: list[str], source: str) -> dict:
    missing_headers = sorted(set(REQUIRED_COLUMNS) - set(headers))
    if missing_headers:
        raise RuntimeError(f"SCC {source} missing required columns: {missing_headers}")
    identifiers = [_normalize_tid(row.get("Tracking ID")) for row in rows]
    blank_rows = [index for index, identifier in enumerate(identifiers, start=2) if not identifier]
    if blank_rows:
        raise RuntimeError(f"SCC {source} contains blank Tracking ID rows: {blank_rows[:5]}")
    duplicates = sorted(
        identifier for identifier, count in Counter(identifiers).items() if count > 1
    )
    extra = sorted(set(identifiers) - set(requested))
    if duplicates or extra:
        raise RuntimeError(
            f"SCC {source} identity failed: duplicates={duplicates[:5]} extra={extra[:5]}"
        )
    return {
        "requested": len(requested),
        "rows": len(rows),
        "missing": [identifier for identifier in requested if identifier not in set(identifiers)],
    }


def _canonical_rows(rows: list[dict]) -> list[dict]:
    return [{column: row.get(column) for column in REQUIRED_COLUMNS} for row in rows]


def _api_rows(response) -> list[dict]:
    if response.status != 200:
        raise RuntimeError(f"SCC search API returned HTTP {response.status}")
    try:
        payload = response.json()
    except Exception as exc:
        raise RuntimeError("SCC search API returned non-JSON") from exc
    summaries = payload.get("packageSummaryList") or []
    if not isinstance(summaries, list):
        raise RuntimeError("SCC packageSummaryList is not a list")
    rows = []
    for summary in summaries:
        if not isinstance(summary, dict):
            raise RuntimeError("SCC packageSummaryList contains a non-object entry")
        rows.append(
            {
                "Tracking ID": _normalize_tid(summary.get("trackingId")),
                "State": summary.get("currentPackageState"),
                "Destination": summary.get("destination"),
                "Source": summary.get("source"),
                "Ship Option": summary.get("shipOption"),
            }
        )
    return rows


def _assert_download_matches_api(downloaded_rows: list[dict], api_rows: list[dict]) -> None:
    """Prove the export belongs to the just-completed API response by identity.

    SCC intentionally converts internal API enum values into human-readable CSV text,
    so field strings are not expected to be byte-identical.
    """
    api_ids = {row["Tracking ID"] for row in api_rows}
    downloaded_ids = {_normalize_tid(row.get("Tracking ID")) for row in downloaded_rows}
    if api_ids != downloaded_ids:
        raise RuntimeError("SCC CSV identifiers do not match the fresh API response")


# The template's SCC columns and the raw API fields they come from. Source and Ship
# Option look like codes in both; State and Destination are the ones that may need an
# enum -> display-text mapping, which this capture proves from real data.
ENUM_MAP_PATH = OUT / "scc_enum_map.json"
_API_FIELD_FOR_COLUMN = {
    "State": "currentPackageState",
    "Destination": "destination",
    "Source": "source",
    "Ship Option": "shipOption",
}

# State enum -> CSV display text, verified from real SCC exports (scc_enum_map.json).
# Any enum not in this map is converted by a safe derivation (see _state_display).
STATE_ENUM_TO_TEXT = {
    "RECEIVED": "Received",
    "INDUCTED": "Inducted",
    "HELD": "Held",
    "DELIVERY_REJECTED": "Delivery Rejected",
    "IN_TRANSIT_TO_CUSTOMER": "In Transit (DS -> Customer)",
    "MARKED_FOR_REPROCESS": "Marked For Reprocess",
    "DELIVERY_FAILED": "Delivery Failed",
    "MARKED_FOR_PROBLEM": "Marked For Problem",
    "STOWED": "Stowed",
    "IN_TRANSIT_DS_TO_DS": "In Transit (DS -> DS)",
    "DELIVERED": "Delivered",
    "CASH_IN_ASSOCIATE": "Cash In Associate",
    # Additional states seen in exports; enum spelling matched to display text.
    "MANIFESTED_FC_TO_DS": "Manifested (FC -> DS)",
    "IN_TRANSIT_DS_TO_FC": "In Transit (DS -> FC)",
    "IN_TRANSIT": "In Transit",
    "CASH_AT_STATION": "Cash At Station",
    "MARKED_AS_MISSING": "Marked As Missing",
    "MARKED_AS_LOST": "Marked As Lost",
    "REMOVED": "Removed",
    "PICKUP_FAILED": "Pickup Failed",
    "STAGED_DS_TO_CUSTOMER": "Staged (DS -> Customer)",
    "RTO": "RTO",
    "IN_TRANSIT_CUSTOMER_TO_DS": "In Transit (Customer -> DS)",
}


def _load_verified_state_map() -> dict:
    """Overlay any locally captured (proven) State mappings on top of the built-in map."""
    mapping = dict(STATE_ENUM_TO_TEXT)
    if ENUM_MAP_PATH.exists():
        try:
            captured = json.loads(ENUM_MAP_PATH.read_text(encoding="utf-8")).get("State") or {}
            for enum_value, text in captured.items():
                mapping[str(enum_value)] = str(text)
        except (json.JSONDecodeError, OSError, AttributeError):
            pass
    return mapping


def _state_display(enum_value, state_map: dict) -> str:
    """Convert a State enum to CSV display text. Known enums use the verified map; an
    unknown enum falls back to a readable Title-Case derivation and is logged upstream."""
    key = str(enum_value or "").strip()
    if key in state_map:
        return state_map[key]
    # Derivation for an unseen enum: DS_TO_FC style -> "(DS -> FC)", words Title-Cased.
    if not key:
        return ""
    pretty = key.replace("_TO_", " -> ").replace("_", " ").title()
    return pretty


def _api_rows_with_state_text(response, state_map: dict) -> tuple[list[dict], list[str]]:
    """Build the 5 template columns from the batch API response, converting State enums
    to display text. Returns (rows, unknown_state_enums)."""
    payload = response.json()
    summaries = payload.get("packageSummaryList") or []
    if not isinstance(summaries, list):
        raise RuntimeError("SCC packageSummaryList is not a list")
    rows: list[dict] = []
    unknown: list[str] = []
    for summary in summaries:
        if not isinstance(summary, dict):
            raise RuntimeError("SCC packageSummaryList contains a non-object entry")
        state_enum = summary.get("currentPackageState")
        key = str(state_enum or "").strip()
        if key and key not in state_map:
            unknown.append(key)
        rows.append({
            "Tracking ID": _normalize_tid(summary.get("trackingId")),
            "State": _state_display(state_enum, state_map),
            "Destination": summary.get("destination"),
            "Source": summary.get("source"),
            "Ship Option": summary.get("shipOption"),
        })
    return rows, sorted(set(unknown))


def _fetch_batch_in_page(frame, ids: list[str], log) -> dict:
    """Call the SCC batch API directly from inside the authenticated page via fetch().

    This POSTs to the same endpoint the Search button uses, carrying the browser's
    session cookies, so it needs no button click (avoids 'element outside viewport'),
    no file download, and no buffering spinner. Returns the parsed JSON payload.
    """
    result = frame.evaluate(
        """
        async ({ids, apiUrl}) => {
          const resp = await fetch(apiUrl, {
            method: 'POST',
            headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
            credentials: 'include',
            body: JSON.stringify({
              resourcePath: '/os/batchGetPackageSummary',
              requestBody: {identifiers: ids}
            })
          });
          const text = await resp.text();
          return {status: resp.status, url: resp.url, text: text, origin: location.origin};
        }
        """,
        {"ids": ids, "apiUrl": SCC_API_URL},
    )
    status = result.get("status")
    if status != 200:
        body = str(result.get("text") or "")[:300]
        raise RuntimeError(
            f"SCC in-page API returned HTTP {status} for {len(ids)} ids "
            f"(page origin={result.get('origin')}, api={SCC_API_URL}); body: {body}"
        )
    try:
        return json.loads(result.get("text") or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"SCC in-page API returned non-JSON: {str(result.get('text'))[:200]}") from exc


def _rows_from_payload(payload: dict, state_map: dict) -> tuple[list[dict], list[str]]:
    """Build the 5 template columns from a batch payload, converting State to text."""
    summaries = payload.get("packageSummaryList") or []
    if not isinstance(summaries, list):
        raise RuntimeError("SCC packageSummaryList is not a list")
    rows: list[dict] = []
    unknown: list[str] = []
    for summary in summaries:
        if not isinstance(summary, dict):
            raise RuntimeError("SCC packageSummaryList contains a non-object entry")
        state_enum = summary.get("currentPackageState")
        key = str(state_enum or "").strip()
        if key and key not in state_map:
            unknown.append(key)
        rows.append({
            "Tracking ID": _normalize_tid(summary.get("trackingId")),
            "State": _state_display(state_enum, state_map),
            "Destination": summary.get("destination"),
            "Source": summary.get("source"),
            "Ship Option": summary.get("shipOption"),
        })
    return rows, sorted(set(unknown))


def _capture_enum_map(response, csv_rows: list[dict], label: str, log) -> None:
    """Record, per TID, the raw API value and the CSV display text for each SCC column.

    Appends unique (column, api_value -> csv_text) pairs to ``scc_enum_map.json`` so we
    can build and verify an enum->text mapping before switching SCC to a pure-API path.
    Output only; does not affect SCC's returned rows.
    """
    payload = response.json()
    api_by_tid = {}
    for summary in payload.get("packageSummaryList") or []:
        if isinstance(summary, dict):
            api_by_tid[_normalize_tid(summary.get("trackingId"))] = summary
    csv_by_tid = {_normalize_tid(r.get("Tracking ID")): r for r in csv_rows}

    existing = {}
    if ENUM_MAP_PATH.exists():
        try:
            existing = json.loads(ENUM_MAP_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            existing = {}
    mapping = {column: dict(existing.get(column, {})) for column in _API_FIELD_FOR_COLUMN}

    new_pairs = 0
    for tid, api_summary in api_by_tid.items():
        csv_row = csv_by_tid.get(tid)
        if not csv_row:
            continue
        for column, api_field in _API_FIELD_FOR_COLUMN.items():
            api_value = api_summary.get(api_field)
            csv_text = csv_row.get(column)
            if api_value is None or csv_text is None:
                continue
            key = str(api_value)
            if key not in mapping[column]:
                mapping[column][key] = str(csv_text)
                new_pairs += 1
            elif mapping[column][key] != str(csv_text):
                # Same enum mapping to two different texts would break a pure-API switch;
                # record the conflict so we notice it rather than silently overwrite.
                mapping.setdefault("_conflicts", {}).setdefault(column, []).append(
                    {"api": key, "seen": mapping[column][key], "also": str(csv_text)}
                )
    ENUM_MAP_PATH.write_text(json.dumps(mapping, indent=2, ensure_ascii=False), encoding="utf-8")
    log(f"SCC {label}: enum-map capture +{new_pairs} pairs -> {ENUM_MAP_PATH.name}")


def _export_small(
    page,
    frame,
    requested: list[str],
    label: str,
    download_directory: Path,
    log,
    singleton_attempt: int = 0,
    auth_attempt: int = 0,
) -> list[dict]:
    frame.locator("textarea").first.fill("\n".join(requested))
    with page.expect_response(_search_response_for(requested), timeout=240_000) as response_info:
        frame.get_by_role("button", name="Search").first.click()
    response = response_info.value
    if 300 <= response.status < 400:
        if auth_attempt >= 2:
            raise RuntimeError(f"SCC {label}: authentication redirect persisted after retries")
        log(f"SCC {label}: HTTP {response.status}; refreshing authenticated search page")
        refreshed_frame = _goto_search(page, log)
        return _export_small(
            page,
            refreshed_frame,
            requested,
            label,
            download_directory,
            log,
            singleton_attempt,
            auth_attempt + 1,
        )
    if response.status >= 500 and len(requested) > 1:
        halfway = len(requested) // 2
        log(f"SCC {label}: HTTP {response.status}; bisecting {len(requested)} IDs")
        return (
            _export_small(page, frame, requested[:halfway], f"{label}a", download_directory, log)
            + _export_small(page, frame, requested[halfway:], f"{label}b", download_directory, log)
        )
    if response.status >= 500 and len(requested) == 1:
        if singleton_attempt < 2:
            wait = 2 ** (singleton_attempt + 1)
            log(f"SCC {label}: singleton HTTP {response.status}; retrying after {wait}s")
            time.sleep(wait)
            return _export_small(
                page,
                frame,
                requested,
                label,
                download_directory,
                log,
                singleton_attempt + 1,
                auth_attempt,
            )
        raise RuntimeError(f"SCC {label}: singleton remained HTTP {response.status}; unresolved")
    api_rows = _api_rows(response)
    _validate_rows(api_rows, REQUIRED_COLUMNS, requested, "fresh API")
    time.sleep(3)

    button = frame.get_by_role("button", name="Export to CSV")
    try:
        button.first.wait_for(state="visible", timeout=240_000)
    except Exception as exc:
        raise RuntimeError(f"SCC {label}: Export to CSV did not appear") from exc
    browser_blob_capture.install(frame)
    before = {item.name for item in download_directory.glob("*")}
    button.first.click()
    captured = browser_blob_capture.wait(frame, timeout_s=120, log=log)
    if captured is not None:
        target = OUT / f"scc_{label}_{datetime.now():%Y%m%d_%H%M%S}.csv"
        target.write_text(captured["text"], encoding="utf-8")
    else:
        target = _wait_for_download(download_directory, timeout_s=240, before=before)
    rows, headers = _read_csv(target)
    _validate_rows(rows, headers, requested, "Export to CSV")
    _assert_download_matches_api(rows, api_rows)
    # Capture ground-truth API-enum <-> CSV-text pairs so we can later switch SCC to a
    # pure-API path and reproduce the exact CSV display text. Output only; SCC behavior
    # is unchanged.
    try:
        _capture_enum_map(response, rows, label, log)
    except Exception as exc:  # never let capture affect the real result
        log(f"SCC {label}: enum-map capture skipped ({str(exc)[:80]})")
    log(f"SCC {label}: exported {len(rows)} rows; missing/no-data={len(requested) - len(rows)}")
    return rows


def _export_large(page, frame, requested: list[str], download_directory: Path, log) -> list[dict]:
    """High-volume SCC batch. Reads rows from the batch API RESPONSE the Search triggers
    (converting State enums to display text), instead of waiting for a fragile automatic
    file download. This is the same data the results table shows, and it does not hang if
    the UI renders results on-page rather than auto-downloading a file."""
    frame.locator("textarea").first.fill("\n".join(requested))
    # The ">1000" checkbox is optional for the API-response path, but tick it when present
    # to match the operator's manual flow; never fail the run if it is absent.
    checkbox = frame.get_by_role(
        "checkbox",
        name="Download Default columns only (Use when submitting >1000 TrackingIds routing tasks to CO)",
        exact=True,
    )
    try:
        if checkbox.count() == 1 and not checkbox.is_checked():
            checkbox.check(force=True)
    except Exception as exc:
        log(f"SCC bulk: checkbox not set ({str(exc)[:60]}); continuing via API response")

    with page.expect_response(_search_response_for(requested), timeout=45 * 60 * 1000) as info:
        frame.get_by_role("button", name="Search").first.click()
    response = info.value
    if response.status != 200:
        raise RuntimeError(f"SCC bulk search API returned HTTP {response.status}")

    state_map = _load_verified_state_map()
    rows, unknown = _api_rows_with_state_text(response, state_map)
    _validate_rows(rows, REQUIRED_COLUMNS, requested, "bulk API response")
    if unknown:
        log(f"SCC bulk: {len(unknown)} unseen State enum(s) converted by derivation: {unknown[:8]}")
    # Persist the batch for audit, matching the previous file's shape.
    target = OUT / f"scc_bulk_{datetime.now():%Y%m%d_%H%M%S}.csv"
    with open(target, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=REQUIRED_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    missing = [tid for tid in requested if _normalize_tid(tid) not in {r["Tracking ID"] for r in rows}]
    log(f"SCC bulk: API response rows={len(rows)} requested={len(requested)} "
        f"missing/no-data={len(missing)}")
    return rows


def _fetch_rows_resilient(frame, ids: list[str], state_map: dict, log) -> tuple[list[dict], list[str]]:
    """Fetch one batch, converting to rows. On a failed/400 batch, bisect and retry each
    half down to a single id, so one bad TID or an oversized batch cannot fail the lane.
    Returns (rows, unknown_state_enums)."""
    try:
        payload = _fetch_batch_in_page(frame, ids, log)
        return _rows_from_payload(payload, state_map)
    except RuntimeError as exc:
        if len(ids) <= 1:
            log(f"SCC: dropping 1 id that the API rejected ({str(exc)[:100]})")
            return [], []
        half = len(ids) // 2
        log(f"SCC: batch of {len(ids)} rejected; bisecting")
        left_rows, left_unknown = _fetch_rows_resilient(frame, ids[:half], state_map, log)
        right_rows, right_unknown = _fetch_rows_resilient(frame, ids[half:], state_map, log)
        return left_rows + right_rows, sorted(set(left_unknown) | set(right_unknown))


def _robust_click(target, log, what: str):
    """Click an element even if it is outside the viewport: scroll it into view, then
    try a normal click, falling back to a JS dispatch that ignores actionability."""
    try:
        target.scroll_into_view_if_needed(timeout=10_000)
    except Exception:
        pass
    try:
        target.click(timeout=15_000)
        return
    except Exception as exc:
        log(f"SCC: normal click on {what} failed ({str(exc)[:60]}); using JS click")
    target.evaluate("el => el.click()")


def _ensure_large_mode_checkbox(frame, index: int, log) -> bool:
    """Tick the ">1000 TrackingIds" checkbox that switches SCC to automatic download.

    This checkbox is REQUIRED for the large path: without it SCC shows the normal Export
    button flow instead of auto-downloading, so waiting for a download would hang. The
    element often renders below the fold, so a plain ``check()`` fails with "outside of
    the viewport"; we scroll it into view and fall back to a JS click on the input/label.
    Returns True if the checkbox ends up checked.
    """
    checkbox = frame.get_by_role(
        "checkbox",
        name="Download Default columns only (Use when submitting >1000 TrackingIds routing tasks to CO)",
        exact=True,
    )
    try:
        if checkbox.count() != 1:
            log(f"SCC chunk {index}: >1000 checkbox not present")
            return False
        if checkbox.is_checked():
            return True
    except Exception as exc:
        log(f"SCC chunk {index}: could not read >1000 checkbox state ({str(exc)[:60]})")

    # Try progressively more forceful ways to tick it.
    try:
        checkbox.scroll_into_view_if_needed(timeout=10_000)
    except Exception:
        pass
    for attempt in ("check", "click", "js"):
        try:
            if attempt == "check":
                checkbox.check(force=True, timeout=10_000)
            elif attempt == "click":
                checkbox.click(force=True, timeout=10_000)
            else:
                checkbox.evaluate("el => el.click()")
        except Exception as exc:
            log(f"SCC chunk {index}: >1000 checkbox {attempt} failed ({str(exc)[:40]})")
            continue
        try:
            if checkbox.is_checked():
                log(f"SCC chunk {index}: ticked >1000 automatic-download mode via {attempt}")
                return True
        except Exception:
            pass
    log(f"SCC chunk {index}: >1000 checkbox could NOT be ticked by any method")
    return False


def _browser_export_chunk(page, frame, requested: list[str], index: int,
                          download_directory: Path, log) -> list[dict]:
    """One chunk (up to SCC_UI_CHUNK TIDs): paste -> Search -> read the result.

    The result path depends on how many TIDs are searched, exactly like the manual flow:
      - <= AUTO_DOWNLOAD_THRESHOLD (1,000): SCC shows an "Export to CSV" button; click it
        and capture the generated CSV (blob-in-page, falling back to a file download).
      - >  AUTO_DOWNLOAD_THRESHOLD: SCC ticks the ">1000" mode and AUTOMATICALLY downloads
        the result file — no Export button appears. Detect the freshly downloaded file and
        read it.
    """
    large = len(requested) > AUTO_DOWNLOAD_THRESHOLD

    # 1. Paste the TID list into the search textarea.
    textarea = frame.locator("textarea").first
    textarea.wait_for(state="visible", timeout=120_000)
    textarea.fill("\n".join(requested))

    # For a large search, tick the ">1000 TrackingIds" checkbox — this is what makes SCC
    # switch to automatic download.
    if large:
        _ensure_large_mode_checkbox(frame, index, log)

    # 2. Snapshot the download dir, then click Search (robust against viewport issues).
    before = {item.name for item in download_directory.glob("*")}
    _robust_click(frame.get_by_role("button", name="Search").first, log, "Search")

    if large:
        # 3a. Large path: after Search, detect exactly TWO outcomes —
        #   (1) a 'not found'/no-data popup -> raise so the SCC steps restart, or
        #   (2) the automatic download -> detect the file and read it.
        log(f"SCC chunk {index}: searched {len(requested)} TIDs; watching for a "
            "'not found' popup or the automatic download")
        target = _wait_for_download_or_notfound(page, frame, download_directory, before,
                                                index, timeout_s=45 * 60, log=log)
    else:
        # 3b. Small path (<=1000): SCC shows an Export button; click it and capture the
        # CSV (blob-in-page, falling back to a file download).
        export = frame.get_by_role("button", name="Export to CSV").first
        try:
            export.wait_for(state="visible", timeout=240_000)
        except Exception as exc:
            page.screenshot(path=str(OUT / f"scc_chunk{index}_noexport_{datetime.now():%H%M%S}.png"))
            raise RuntimeError(f"SCC chunk {index}: Export to CSV did not appear ({str(exc)[:80]})") from exc
        browser_blob_capture.install(frame)
        _robust_click(export, log, "Export to CSV")
        captured = browser_blob_capture.wait(frame, timeout_s=240, log=log)
        if captured is not None:
            target = OUT / f"scc_chunk{index}_{datetime.now():%Y%m%d_%H%M%S}.csv"
            target.write_text(captured["text"], encoding="utf-8")
        else:
            target = _wait_for_download(download_directory, timeout_s=240, before=before, log=log)

    rows, headers = _read_csv(target)
    _validate_rows(rows, headers, requested, f"chunk {index} export")
    # Capture enum->text pairs for audit (never fails the run).
    try:
        _capture_enum_map_from_csv(rows, log)
    except Exception:
        pass
    return _canonical_rows(rows)


def _capture_enum_map_from_csv(rows: list[dict], log) -> None:
    """Record distinct State/Ship Option display values seen in exports (audit only)."""
    if not rows:
        return
    existing = {}
    if ENUM_MAP_PATH.exists():
        try:
            existing = json.loads(ENUM_MAP_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            existing = {}
    seen = existing.get("State_display_values") or []
    seen_set = set(seen)
    for row in rows:
        value = str(row.get("State") or "").strip()
        if value and value not in seen_set:
            seen_set.add(value)
            seen.append(value)
    existing["State_display_values"] = seen
    ENUM_MAP_PATH.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")


def _ensure_api_origin(page, log):
    """Return a frame on the logistics.amazon.in origin so the in-page API fetch is
    same-origin. If the current page is on a different host (e.g. the SPA app), navigate
    to the SCC search URL first."""
    try:
        origin = page.evaluate("() => location.origin")
    except Exception:
        origin = ""
    if origin != SCC_ORIGIN:
        log(f"SCC: page origin is {origin or '<unknown>'}; navigating to {SCC_ORIGIN} for API")
        try:
            page.goto(SCC_URL, timeout=90_000, wait_until="domcontentloaded")
        except Exception as exc:
            log(f"SCC origin navigation interrupted: {str(exc)[:80]}")
        # Give any auth redirect a moment to settle back onto the SCC origin.
        for _ in range(30):
            try:
                if page.evaluate("() => location.origin") == SCC_ORIGIN:
                    break
            except Exception:
                pass
            time.sleep(2)
    # Prefer the main frame on the SCC origin; fall back to any frame there.
    for candidate in [page.main_frame, *page.frames]:
        try:
            if candidate.evaluate("() => location.origin") == SCC_ORIGIN:
                return candidate
        except Exception:
            continue
    return page.main_frame


def _export_chunk_with_retry(page, frame, chunk, index, total_chunks, download_directory, log):
    """Run one chunk's Search -> Export; on a page/UI failure, search again on the SAME
    page/frame (up to SCC_CHUNK_ATTEMPTS). Returns (rows, frame, failed) — ``failed`` is
    True only when every attempt raised.

    A chunk that succeeds with fewer rows than requested is normal (those TIDs are simply
    not in SCC). But a chunk that returns ZERO rows is treated as suspicious and retried,
    because a transient empty export is indistinguishable from a genuine all-not-found
    chunk and would otherwise silently drop real data.

    Retries do NOT reload or re-navigate the page: a full page.reload()/goto() is a
    navigation event that can collide with the AEA browser extension and destroy the
    page/context ("Target page, context or browser has been closed" — see
    Docs/edge-cases.md #C5), which made the refresh itself the cause of chunk failures
    rather than the recovery from them. Instead, a retry just re-fills the search
    textarea and clicks Search again on the existing frame.
    """
    last_error = None
    for attempt in range(1, SCC_CHUNK_ATTEMPTS + 1):
        try:
            rows = _browser_export_chunk(page, frame, chunk, index, download_directory, log)
            if not rows and attempt < SCC_CHUNK_ATTEMPTS:
                # Zero rows may be legitimate, but re-verify once before accepting it,
                # mirroring the GCRS empty-response guard.
                log(f"SCC chunk {index}/{total_chunks}: zero rows on attempt {attempt}; "
                    "re-verifying before accepting as all-not-found")
                raise RuntimeError("zero-row chunk pending re-verification")
            return rows, frame, False
        except Exception as exc:
            last_error = exc
            log(f"SCC chunk {index}/{total_chunks} attempt {attempt}/{SCC_CHUNK_ATTEMPTS} "
                f"failed ({str(exc)[:100]})")
            if attempt < SCC_CHUNK_ATTEMPTS:
                log(f"SCC chunk {index}: searching again on the same page (no refresh)")
                time.sleep(3)
    # If the only reason we are here is a persistently zero-row chunk (not a UI failure),
    # accept the empty result: SCC genuinely has no data for these TIDs.
    if isinstance(last_error, RuntimeError) and "zero-row chunk" in str(last_error):
        log(f"SCC chunk {index}/{total_chunks}: zero rows confirmed after retries; "
            f"{len(chunk)} TIDs are genuinely not-found in SCC")
        return [], frame, False
    # A real UI/page failure exhausted every attempt. Do NOT kill the whole SCC lane, but
    # mark these TIDs as FAILED (distinct from not-found) so the caller can report them.
    log(f"SCC chunk {index}/{total_chunks}: still failing after {SCC_CHUNK_ATTEMPTS} attempts "
        f"({str(last_error)[:100]}); marking these {len(chunk)} TIDs as failed (not queried)")
    return [], frame, True


def fetch(tids: list[str], log=print) -> list[dict]:
    """Fetch SCC package summaries by driving the authenticated browser UI: paste TIDs ->
    Search -> Export to CSV -> read. Each chunk retries with a tab refresh on a page/UI
    failure (SCC_CHUNK_ATTEMPTS). A wrong/invalid TID simply returns no row (not an error)."""
    requested = [
        value
        for value in dict.fromkeys(_normalize_tid(tid) for tid in tids)
        if value and value != "TID not found"
    ]
    if not requested:
        return []

    rows: list[dict] = []
    failed_tids: list[str] = []
    download_directory = _new_download_directory()
    with sync_playwright() as playwright:
        context = _open(playwright, download_directory)
        page = context.new_page()
        cdp = _configure_download_directory(context, page, download_directory)
        try:
            frame = _goto_search(page, log)
            total_chunks = (len(requested) + SCC_UI_CHUNK - 1) // SCC_UI_CHUNK
            for offset in range(0, len(requested), SCC_UI_CHUNK):
                chunk = requested[offset:offset + SCC_UI_CHUNK]
                index = offset // SCC_UI_CHUNK + 1
                chunk_rows, frame, failed = _export_chunk_with_retry(
                    page, frame, chunk, index, total_chunks, download_directory, log
                )
                rows.extend(chunk_rows)
                if failed:
                    failed_tids.extend(chunk)
                log(f"SCC chunk {index}/{total_chunks}: {len(chunk)} asked, {len(chunk_rows)} returned"
                    f"{' [CHUNK FAILED — not queried]' if failed else ''}")
        finally:
            try:
                cdp.detach()
            except Exception:
                pass
            try:
                context.close()
            except Exception:
                pass

    rows = _canonical_rows(rows)
    validation = _validate_rows(rows, REQUIRED_COLUMNS, requested, "combined result")
    # Persist the combined batch for audit.
    target = OUT / f"scc_bulk_{datetime.now():%Y%m%d_%H%M%S}.csv"
    with open(target, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=REQUIRED_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    # Separate genuine not-found (TID simply absent from SCC) from failed-and-not-queried
    # (a chunk that exhausted its refresh retries). Only the latter is a reliability
    # problem, so it is written to its own sidecar file and called out loudly.
    failed_set = {_normalize_tid(tid) for tid in failed_tids}
    genuinely_missing = [tid for tid in validation["missing"] if tid not in failed_set]
    if failed_set:
        failed_path = OUT / f"scc_failed_tids_{datetime.now():%Y%m%d_%H%M%S}.txt"
        failed_path.write_text("\n".join(sorted(failed_set)), encoding="utf-8")
        log(f"SCC WARNING: {len(failed_set)} TID(s) could not be queried after retries "
            f"(chunk failures) — written to {failed_path.name}; re-run to fill them")
    log(
        f"SCC complete: requested={validation['requested']} rows={validation['rows']} "
        f"not-found={len(genuinely_missing)} failed-not-queried={len(failed_set)}"
    )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tid-file", required=True)
    parser.add_argument("--out", default=str(OUT / "scc_result.csv"))
    parser.add_argument(
        "--resume-existing",
        help="preserve a validated existing SCC CSV and fetch only its missing requested TIDs",
    )
    args = parser.parse_args()
    tids = [
        line.strip().split()[0]
        for line in Path(args.tid_file).read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]
    if args.resume_existing:
        existing_path = Path(args.resume_existing)
        existing_rows, existing_headers = _read_csv(existing_path)
        _validate_rows(existing_rows, existing_headers, tids, "resume input")
        existing_rows = _canonical_rows(existing_rows)
        existing_ids = {_normalize_tid(row.get("Tracking ID")) for row in existing_rows}
        missing = [tracking_id for tracking_id in tids if tracking_id not in existing_ids]
        print(f"SCC resume: existing={len(existing_rows)} missing={len(missing)}", flush=True)
        rows = _canonical_rows(existing_rows + fetch(missing))
        _validate_rows(rows, REQUIRED_COLUMNS, tids, "resumed combined result")
    else:
        rows = fetch(tids)
    if not rows:
        raise RuntimeError("SCC returned no rows")
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{uuid4().hex}.tmp")
    try:
        with open(temporary, "w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=REQUIRED_COLUMNS)
            writer.writeheader()
            writer.writerows(_canonical_rows(rows))
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    print("wrote", output)


if __name__ == "__main__":
    main()
