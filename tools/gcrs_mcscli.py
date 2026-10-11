"""Transport-neutral SIW address resolver with guarded, sequential GRBI calls.

Numeric 12-digit TIDs route to ``ATS!DEFAULT`` and ``DOC`` TIDs route to
``ATA!DEFAULT``. Valid IDs are deduplicated only for network efficiency; callers
may expand the result back to every physical input row. A TID is considered not
found only after a trustworthy SIW ``invalidIds`` response.
"""
from __future__ import annotations

import re
import time
from typing import Callable

from siw_client import FailoverSiwSession, TransportError

# Batch size is transport-aware. SIW accepts up to 500 IDs per /grbi call and the fast
# mcscli subprocess handles that comfortably (fewer round-trips on big datasets). The Edge
# fallback returns each response through a single in-page fetch/CDP round-trip, and a
# 500-ID payload there was observed to trigger degraded all-invalid windows and eventually
# "Failed to fetch" (run 20260929). Edge therefore uses the previously-proven 200.
BATCH_MCSCLI = 500
BATCH_EDGE = 200

# Per-batch retry for the Edge transport only. Real runs on 20261009 showed the mcscli
# SIW probe fail (forcing the fragile Edge fallback), then one /grbi batch out of several
# throw a transient server-side error (HTTP 502, or "Failed to fetch") while every batch
# before and after it succeeded identically. Without a retry, that one transient failure
# aborted the WHOLE fetch() call, blanking gcrs + polygon + ads for the entire run. A
# failing batch now retries in place, and if it still fails, only that batch's TIDs are
# marked errored while the loop continues through every remaining batch to the last one.
EDGE_BATCH_RETRY_ATTEMPTS = 3
EDGE_BATCH_RETRY_BACKOFF_S = (2, 4, 6)


def _batch_size_for(session: FailoverSiwSession) -> int:
    """Pick a safe /grbi batch size for the transport the session actually selected.

    Only the Edge transport needs the smaller size; mcscli (and test doubles that do not
    report an Edge transport) use the full 500.
    """
    ensure_ready = getattr(session, "ensure_ready", None)
    if callable(ensure_ready):
        try:
            ensure_ready()
        except Exception:
            return BATCH_EDGE
    return BATCH_EDGE if getattr(session, "transport_name", None) == "edge" else BATCH_MCSCLI


def _resolve_batch_with_edge_retry(
    chunk: list[str], polygon_set: str, log: Callable[[str], None], session: FailoverSiwSession
) -> tuple[dict, set, list[str]]:
    """Resolve one batch; on the Edge transport, retry a transient TransportError (a
    "Failed to fetch"-class browser failure, or an HTTP 5xx/502-class gateway error) up
    to EDGE_BATCH_RETRY_ATTEMPTS times with backoff before giving up on just that batch.

    The mcscli transport is NOT retried here — it already fails fast on a real error and
    is a single, direct call, unchanged from before.

    Returns (rows, invalid, errored_tids). On persistent failure the batch's TIDs are
    returned as ``errored_tids`` instead of raising, so the caller's loop can continue
    to the next batch all the way through the last one.
    """
    is_edge = getattr(session, "transport_name", None) == "edge"
    if not is_edge:
        chunk_rows, chunk_invalid = _resolve(chunk, polygon_set, log, session)
        return chunk_rows, chunk_invalid, []

    last_error: Exception | None = None
    for attempt in range(1, EDGE_BATCH_RETRY_ATTEMPTS + 1):
        try:
            chunk_rows, chunk_invalid = _resolve(chunk, polygon_set, log, session)
            return chunk_rows, chunk_invalid, []
        except TransportError as exc:
            last_error = exc
            log(f"  GCRS batch retry {attempt}/{EDGE_BATCH_RETRY_ATTEMPTS} failed: {str(exc)[:120]}")
            if attempt < EDGE_BATCH_RETRY_ATTEMPTS:
                time.sleep(EDGE_BATCH_RETRY_BACKOFF_S[min(attempt - 1, len(EDGE_BATCH_RETRY_BACKOFF_S) - 1)])
    log(f"  GCRS batch: still failing after {EDGE_BATCH_RETRY_ATTEMPTS} attempts "
        f"({str(last_error)[:120]}); marking these {len(chunk)} TIDs as errored and continuing")
    return {}, set(), list(chunk)
COLUMNS = [
    "Query_Item", "Address_Id", "Address_Line_1", "Address_Line_2", "Address_Line_3",
    "City", "State", "Country", "Postal_Code", "Latitude", "Longitude", "Type", "Scope",
    "Confidence", "Original_Station", "Original_Primary_Sort", "Original_Secondary_Sort",
    "Original_Routing_Code", "Updated_Station", "Updated_Primary_Sort", "Updated_Secondary_Sort",
    "Updated_Routing_Code", "Valid",
]


def normalize_tid(value: object) -> str:
    tid = str(value or "").strip().lstrip("\ufeff")
    return tid.upper() if re.fullmatch(r"DOC\d+", tid, re.IGNORECASE) else tid


def _grbi(ids: list[str], polygon_set: str, log: Callable[[str], None], session: FailoverSiwSession) -> dict:
    reply = session.post_json(
        "grbi",
        {
            "polygonSetName": polygon_set,
            "idType": "tracking_id",
            "ids": ",".join(ids),
            "effectiveDate": "",
        },
        timeout_s=180,
    )
    if not isinstance(reply.payload, dict):
        raise RuntimeError("grbi returned an unexpected non-object response")
    if reply.payload.get("error"):
        raise RuntimeError(f"grbi returned an SIW error: {str(reply.payload['error'])[:200]}")
    payload = dict(reply.payload)
    payload["_seconds"] = reply.elapsed_seconds
    return payload


def _rows_from(payload: dict) -> dict[str, dict]:
    route_details_array = payload.get("routeDetailsArray") or []
    if not isinstance(route_details_array, list):
        raise RuntimeError("grbi routeDetailsArray is not a list")
    output: dict[str, dict] = {}
    for route_details in route_details_array:
        if not isinstance(route_details, dict):
            raise RuntimeError("grbi routeDetailsArray contains a non-object entry")
        address = route_details.get("address") or {}
        geocode = route_details.get("geocodingResult") or {}
        original = route_details.get("originalDetails") or {}
        updated = route_details.get("updatedDetails") or {}
        if not all(isinstance(value, dict) for value in (address, geocode, original, updated)):
            raise RuntimeError("grbi route entry contains an invalid nested object")
        tid = normalize_tid(route_details.get("originalId"))
        if not tid:
            continue
        output[tid] = {
            "Query_Item": tid,
            "Address_Id": route_details.get("addressId"),
            "Address_Line_1": address.get("addressLine1"),
            "Address_Line_2": address.get("addressLine2"),
            "Address_Line_3": address.get("addressLine3"),
            "City": address.get("city"),
            "State": address.get("state"),
            "Country": address.get("country"),
            "Postal_Code": address.get("postalCode"),
            "Latitude": geocode.get("latitude"),
            "Longitude": geocode.get("longitude"),
            "Type": geocode.get("gcsType"),
            "Scope": geocode.get("gcsScope"),
            "Confidence": geocode.get("gcsConfidence"),
            "Original_Station": original.get("sortName"),
            "Original_Primary_Sort": original.get("primarySort"),
            "Original_Secondary_Sort": original.get("secondarySort"),
            "Original_Routing_Code": original.get("routeCode"),
            "Updated_Station": updated.get("sortName"),
            "Updated_Primary_Sort": updated.get("primarySort"),
            "Updated_Secondary_Sort": updated.get("secondarySort"),
            "Updated_Routing_Code": updated.get("routeCode"),
            "Valid": True,
        }
    return output


def _invalid_ids(payload: dict) -> set[str]:
    values = payload.get("invalidIds") or []
    if not isinstance(values, list):
        raise RuntimeError("grbi invalidIds is not a list")
    return {normalize_tid(value) for value in values if normalize_tid(value)}


def _consume_retry(
    ids: list[str],
    payload: dict,
    polygon_set: str,
    log: Callable[[str], None],
    session: FailoverSiwSession,
    depth: int,
) -> tuple[dict[str, dict], set[str]]:
    rows = _rows_from(payload)
    invalid = _invalid_ids(payload)
    if not rows and len(ids) > 1:
        log(f"  retry poison guard: zero rows for {len(ids)} ids — bisecting")
        rows, invalid = {}, set()
        halfway = len(ids) // 2
        for part in (ids[:halfway], ids[halfway:]):
            part_rows, part_invalid = _resolve(part, polygon_set, log, session, depth + 1)
            rows.update(part_rows)
            invalid |= part_invalid
    return rows, invalid


def _resolve(
    ids: list[str],
    polygon_set: str,
    log: Callable[[str], None],
    session: FailoverSiwSession,
    depth: int = 0,
) -> tuple[dict[str, dict], set[str]]:
    payload = _grbi(ids, polygon_set, log, session)
    rows = _rows_from(payload)
    invalid = _invalid_ids(payload)
    log(f"  grbi {len(ids)} ids -> {len(rows)} rows, {len(invalid)} invalid, {payload['_seconds']:.1f}s")

    # One poison ID can make SIW report the entire batch as invalid with no rows.
    # A suspicious empty response gets bounded backoff; if still empty, isolate
    # outcomes to singleton calls. A partial recovery must continue through every
    # remaining guard rather than returning early.
    if not rows and len(ids) > 1:
        if len(ids) >= 50 or payload["_seconds"] < 10:
            for wait in (2, 4, 6, 8):
                log(f"  guard G1: suspicious empty response — backoff {wait}s and re-fetch")
                time.sleep(wait)
                retry = _grbi(ids, polygon_set, log, session)
                if _rows_from(retry):
                    rows, invalid = _consume_retry(ids, retry, polygon_set, log, session, depth)
                    break
        if not rows:
            log(f"  guard G3b: zero rows for {len(ids)} ids — bisecting")
            halfway = len(ids) // 2
            rows, invalid = {}, set()
            for part in (ids[:halfway], ids[halfway:]):
                part_rows, part_invalid = _resolve(part, polygon_set, log, session, depth + 1)
                rows.update(part_rows)
                invalid |= part_invalid
            return rows, invalid

    # Back off and re-fetch only when the miss looks like a transient degraded-window
    # response: a wholly-empty multi-ID batch, or a large block of unresolved IDs. A
    # single unresolved ID that already came back empty is treated as a real miss and
    # falls through to the bisect/warn path instead of burning 20s of backoff.
    missing = [tid for tid in ids if tid not in rows and tid not in invalid]
    if missing and ((not rows and len(ids) > 1) or len(missing) >= 50):
        for wait in (2, 4, 6, 8):
            log(f"  guard G1: {len(missing)} unresolved — backoff {wait}s and re-fetch")
            time.sleep(wait)
            retry = _grbi(missing, polygon_set, log, session)
            retry_rows, retry_invalid = _consume_retry(missing, retry, polygon_set, log, session, depth)
            rows.update(retry_rows)
            invalid |= retry_invalid
            missing = [tid for tid in ids if tid not in rows and tid not in invalid]
            if not missing:
                break

    # Apply G2 after every recovery path so invalidIds introduced by a later retry
    # cannot bypass verification. At the recursion limit, downgrade the whole
    # suspicious block to unresolved rather than accepting false not-found rows.
    suspect = invalid & set(ids)
    if len(suspect) >= 50:
        if depth >= 8:
            log("  guard G2: suspicious invalid block persisted; marking unresolved")
            invalid -= suspect
        else:
            log("  guard G2: >=50 invalidIds — backing off and re-verifying")
            time.sleep(4)
            verified_rows, verified_invalid = _resolve(
                sorted(suspect),
                polygon_set,
                log,
                session,
                depth + 1,
            )
            rows.update(verified_rows)
            invalid = (invalid - suspect) | verified_invalid

    missing = [tid for tid in ids if tid not in rows and tid not in invalid]
    if missing and len(missing) > 1 and depth < 8:
        log(f"  guard G3: bisecting {len(missing)} unresolved ids")
        halfway = len(missing) // 2
        for part in (missing[:halfway], missing[halfway:]):
            part_rows, part_invalid = _resolve(part, polygon_set, log, session, depth + 1)
            rows.update(part_rows)
            invalid |= part_invalid
        missing = [tid for tid in ids if tid not in rows and tid not in invalid]
    if missing:
        log(f"  WARNING {len(missing)} ids unresolved after guards (not confirmed invalid)")
    return rows, invalid


def fetch(
    tids: list[str],
    log: Callable[[str], None] = print,
    session: FailoverSiwSession | None = None,
) -> tuple[list[dict], list[str], list[str]]:
    """Return resolved rows, confirmed-not-found TIDs, and unresolved/malformed TIDs."""
    normalized = [normalize_tid(value) for value in tids]
    unique = [tid for tid in dict.fromkeys(normalized) if tid and tid != "TID not found"]
    numeric = [tid for tid in unique if re.fullmatch(r"\d{12}", tid)]
    document = [tid for tid in unique if re.fullmatch(r"DOC\d+", tid)]
    invalid_format = [tid for tid in unique if tid not in numeric and tid not in document]
    if invalid_format:
        log(f"GCRS: rejected {len(invalid_format)} malformed TIDs")

    owned_session = session is None
    active_session = session or FailoverSiwSession(log=log)
    rows: dict[str, dict] = {}
    invalid: set[str] = set()
    batch_errored: list[str] = []
    try:
        # /grbi is issued strictly sequentially. Edge case #G4 records that parallel or
        # oversized GRBI calls caused empty responses and mass false not-found results,
        # so batches are never overlapped — correctness of the delivered file outranks
        # the wall-clock saving from concurrency.
        for polygon_set, ids in (("ATS!DEFAULT", numeric), ("ATA!DEFAULT", document)):
            offset = 0
            batch_number = 0
            while offset < len(ids):
                # Re-evaluate per batch so a mid-run mcscli -> Edge failover immediately
                # drops to the smaller, Edge-safe batch size for the remaining IDs.
                batch_size = _batch_size_for(active_session)
                chunk = ids[offset:offset + batch_size]
                batch_number += 1
                transport = getattr(active_session, "transport_name", None) or "unknown"
                log(f"GCRS batch {batch_number} ({polygon_set}): {len(chunk)} ids [{transport}]")
                # A transient per-batch failure (Edge "Failed to fetch" / HTTP 5xx) is
                # retried in place; if it still fails, only this batch's TIDs are marked
                # errored and the loop CONTINUES to every remaining batch through the
                # last one, instead of aborting the whole fetch() call.
                chunk_rows, chunk_invalid, chunk_errored = _resolve_batch_with_edge_retry(
                    chunk, polygon_set, log, active_session
                )
                rows.update(chunk_rows)
                invalid |= chunk_invalid
                batch_errored.extend(chunk_errored)
                offset += batch_size
    finally:
        if owned_session:
            active_session.close()

    ordered = [rows[tid] for tid in unique if tid in rows]
    not_found = [tid for tid in unique if tid in invalid and tid not in rows]
    errored = invalid_format + [
        tid for tid in unique if tid not in rows and tid not in invalid and tid not in invalid_format
    ]
    log(f"GCRS: requested {len(unique)}, resolved {len(ordered)}, not_found {len(not_found)}, errored {len(errored)}")
    return ordered, not_found, errored
