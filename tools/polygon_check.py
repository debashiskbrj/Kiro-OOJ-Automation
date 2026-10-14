r"""Local, read-only TID polygon boundary checker for SIW.

The checker preserves ordered ``(TID, station)`` pairs, fetches individual route
polygons without merging them, and evaluates points as ``(longitude, latitude)``.
Numeric TIDs use ATS; DOC-prefixed TIDs use ATA. Polygon data is cached in one
small JSON file per carrier/station and can be explicitly refreshed.
"""
from __future__ import annotations

import json
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable
from uuid import uuid4

from siw_client import FailoverSiwSession

CACHE = Path(__file__).parent / "captures" / "polygons"
CACHE.mkdir(parents=True, exist_ok=True)
# Station route polygons are stable; reuse a cached geometry when it is fresh enough to
# skip the per-station gs+gr SIW calls on repeat runs. Older caches are re-fetched so a
# changed boundary is never used stale.
POLYGON_CACHE_MAX_AGE_DAYS = 7
INSIDE_TEXT = "Invalid - It is Inside Jurisdiction as per GCRS Tool"
OUTSIDE_TEXT = "Outside"


# ── CX address recognisability check (new column, 2026-10) ───────────────────────────
# Per TID, judge whether the CX-provided delivery address is correct / recognisable, using
# ONLY data the GCRS/SIW lane already returned (resolved address + PIN + SIW's own geocoder
# verdict). 100% in-process, no external API, nothing leaves Amazon; used ONLY for the new
# "CX Address Check" column, NEVER for the Inside/Outside polygon decision.
# Logic ported verbatim from the operator's TID Polygon Checker clone (dated 2026-10-04).


def _addr_norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


# PIN first-digit -> postal region / states it covers. India's PIN zones are geographic,
# so the leading digit reliably pins the macro-region; a PIN whose zone contradicts the
# address state is a strong "wrong PIN for this place" signal. In-process table, no lookup.
_PIN_ZONE_STATES = {
    "1": {"delhi", "haryana", "punjab", "himachalpradesh", "jammukashmir",
          "jammuandkashmir", "ladakh", "chandigarh"},
    "2": {"uttarpradesh", "uttarakhand", "up", "uk"},
    "3": {"rajasthan", "gujarat", "daman", "diu", "dadra", "nagarhaveli"},
    "4": {"maharashtra", "madhyapradesh", "chhattisgarh", "goa", "mp"},
    "5": {"andhrapradesh", "telangana", "karnataka", "ap", "ts"},
    "6": {"tamilnadu", "kerala", "puducherry", "pondicherry", "lakshadweep", "tn"},
    "7": {"westbengal", "odisha", "orissa", "assam", "sikkim", "arunachalpradesh",
          "nagaland", "manipur", "mizoram", "tripura", "meghalaya", "andaman",
          "nicobar", "wb"},
    "8": {"bihar", "jharkhand"},
}


def _fuzzy_same(a: str, b: str, thresh: float = 0.80) -> bool:
    """True if two place strings are the SAME allowing only small typos
    (e.g. jmalpr~jamalpur, khnacool~khanakul). Deliberately conservative — a
    low threshold would invent false 'OK's for genuinely different places."""
    import difflib
    a, b = _addr_norm(a), _addr_norm(b)
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= thresh


def analyze_cx_address(info: dict) -> str:
    """CX-Address-Check verdict for one TID — THREE remarks only:
       1) OK          : PIN perfectly serves the address (clean high-confidence match)
       2) NOT OK      : partial / approximate match — verify
       3) MISMATCH    : address not served by this PIN, unresolved, or junk/missing
    All signals are SIW-internal (gcsConfidence/gcsType + resolved address/PIN),
    plus a conservative local typo-tolerant state/PIN-zone consistency check.
    No external lookup — nothing leaves Amazon."""
    addr = info.get("address") or {}
    conf = info.get("gcs_confidence") or ""
    gtype = (info.get("gcs_type") or "").lower()
    pin = addr.get("pin") or ""
    city = addr.get("city") or ""
    state = addr.get("state") or ""

    full = " ".join(addr.get(k, "") for k in ("line1", "line2", "line3")).strip()
    alnum = re.sub(r"[^A-Za-z0-9]", "", full)
    letters = re.sub(r"[^A-Za-z]", "", full)
    junky = bool(re.search(r"(_{2,}|x{4,}|\.{3,}|\bn/?a\b|\btest\b|\bdummy\b)", full.lower()))
    too_thin = len(alnum) < 8 or len(letters) < 4
    pin_ok_fmt = bool(re.match(r"^\d{6}$", pin))
    ref = f"PIN {pin or '—'}" + (f", {city or state}" if (city or state) else "")

    # ---- Remark 3: MISMATCH / NOT FOUND (hard problems) ------------------
    if junky or too_thin:
        return f"MISMATCH — address text not recognisable ({ref})"
    if not pin:
        return f"MISMATCH — no PIN on address ({city or '—'})"
    if not pin_ok_fmt:
        return f"MISMATCH — malformed PIN '{pin}' ({city or '—'})"
    # PIN zone vs address state: if the address NAMES a state that belongs to a
    # different PIN zone than this PIN's leading digit, the PIN does not serve
    # this address. Typo-tolerant on the state name.
    zone_states = _PIN_ZONE_STATES.get(pin[0], set())
    st_norm = _addr_norm(state)
    if st_norm and zone_states:
        state_in_zone = any(_fuzzy_same(st_norm, z) for z in zone_states)
        if not state_in_zone:
            return (f"MISMATCH — PIN {pin} (zone {pin[0]}) does not serve "
                    f"{state} ({ref})")
    if conf == "low" or (gtype and gtype != "deliverypoint" and conf != "high"):
        return f"MISMATCH — SIW could not resolve address to a delivery point ({ref})"

    # ---- Remark 2: NOT OK (partial / approximate) ------------------------
    if conf == "medium" or (gtype and gtype != "deliverypoint"):
        return f"NOT OK — partially matches, PIN serves area approximately ({ref})"
    if not conf:
        return f"NOT OK — PIN serves address but geo-confidence unknown ({ref})"

    # ---- Remark 1: OK (clean high-confidence delivery-point match) -------
    return f"OK — address correctly served by PIN ({ref})"


def _cx_info_from_gcrs_row(row: dict) -> dict:
    """Map a live GCRS/SIW row (gcrs_mcscli.COLUMNS) into the dict analyze_cx_address
    expects. Uses only fields the /grbi lane already returned — no new request."""
    return {
        "address": {
            "line1": str(row.get("Address_Line_1") or "").strip(),
            "line2": str(row.get("Address_Line_2") or "").strip(),
            "line3": str(row.get("Address_Line_3") or "").strip(),
            "city": str(row.get("City") or "").strip(),
            "state": str(row.get("State") or "").strip(),
            "pin": str(row.get("Postal_Code") or "").strip(),
        },
        "gcs_confidence": str(row.get("Confidence") or "").strip().lower(),
        "gcs_type": str(row.get("Type") or "").strip(),
    }


def cx_address_check(row: dict | None) -> str:
    """CX Address Check verdict for one GCRS row, or '' when there is no resolved row."""
    if not row:
        return ""
    try:
        return analyze_cx_address(_cx_info_from_gcrs_row(row))
    except Exception:
        # The CX column must never break the polygon lane; a bad row just yields blank.
        return ""


def normalize_tid(value: object) -> str:
    tid = str(value or "").strip().lstrip("\ufeff")
    return tid.upper() if re.fullmatch(r"DOC\d+", tid, re.IGNORECASE) else tid


def carrier_for_tid(tid: str) -> str:
    return "ATA" if normalize_tid(tid).startswith("DOC") else "ATS"


def _post(
    session: FailoverSiwSession,
    path: str,
    fields: dict[str, object],
    retries: int = 0,
) -> object:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            payload = session.post_json(path, fields, timeout_s=180).payload
            if isinstance(payload, dict) and payload.get("error"):
                raise RuntimeError(str(payload["error"])[:240])
            if "Failed to create token storage context" in str(payload):
                raise RuntimeError("Failed to create token storage context")
            return payload
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(1.5)
    raise RuntimeError(str(last_error) if last_error else f"{path} failed")


def _default_station_record(data: object, station: str, carrier: str) -> dict:
    if not isinstance(data, dict):
        raise RuntimeError("unexpected /gs response")
    groups = data.get(station) or data.get(station.upper())
    if not isinstance(groups, dict):
        raise LookupError(f"Station '{station}' not found in SIW")
    candidates = [
        record
        for key, records in groups.items()
        if key.startswith(carrier + "!")
        for record in (records or [])
        if isinstance(record, dict)
    ]
    active = [record for record in candidates if record.get("active") is True]
    default = [record for record in active if not (record.get("polygonSet") or {}).get("shipMethod")]
    pool = default or active
    if not pool:
        raise LookupError(f"No active {carrier}!DEFAULT polygon set for station {station}")
    return max(pool, key=lambda record: (record.get("polygonSet") or {}).get("effectiveDate") or 0)


def _unwrap_geometry(raw: object) -> dict | None:
    if not raw:
        return None
    if isinstance(raw, str):
        raw = json.loads(raw)
    if not isinstance(raw, dict):
        return None
    if raw.get("type") == "Feature":
        raw = raw.get("geometry")
    return raw if isinstance(raw, dict) else None


def _coordinate(value: object, label: str) -> float:
    if isinstance(value, bool):
        raise RuntimeError(f"{label} is not numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{label} is not numeric") from exc
    if not math.isfinite(number):
        raise RuntimeError(f"{label} is not finite")
    return number


def _validate_position(position: object, station: str) -> None:
    if not isinstance(position, (list, tuple)) or len(position) < 2:
        raise RuntimeError(f"Invalid coordinate position for {station}")
    lon = _coordinate(position[0], "longitude")
    lat = _coordinate(position[1], "latitude")
    if not -180 <= lon <= 180 or not -90 <= lat <= 90:
        raise RuntimeError(f"Coordinate outside geographic bounds for {station}")


def _validate_ring(ring: object, station: str) -> None:
    if not isinstance(ring, list) or len(ring) < 4:
        raise RuntimeError(f"Polygon ring is too short for {station}")
    for position in ring:
        _validate_position(position, station)
    if list(ring[0][:2]) != list(ring[-1][:2]):
        raise RuntimeError(f"Polygon ring is not closed for {station}")


def _validate_polygon_coordinates(coordinates: object, station: str) -> None:
    if not isinstance(coordinates, list) or not coordinates:
        raise RuntimeError(f"Empty Polygon coordinates for {station}")
    for ring in coordinates:
        _validate_ring(ring, station)


def _validate_geometry(geometry: object, station: str) -> None:
    if not isinstance(geometry, dict):
        raise RuntimeError(f"Invalid geometry object for {station}")
    geometry_type = geometry.get("type")
    if geometry_type == "Polygon":
        _validate_polygon_coordinates(geometry.get("coordinates"), station)
        return
    if geometry_type == "MultiPolygon":
        polygons = geometry.get("coordinates")
        if not isinstance(polygons, list) or not polygons:
            raise RuntimeError(f"Empty MultiPolygon coordinates for {station}")
        for polygon in polygons:
            _validate_polygon_coordinates(polygon, station)
        return
    if geometry_type == "GeometryCollection":
        collection = geometry.get("geometries")
        if not isinstance(collection, list) or not collection:
            raise RuntimeError(f"Empty GeometryCollection for {station}")
        for child in collection:
            _validate_geometry(child, station)
        return
    raise RuntimeError(f"Unsupported geometry type for {station}: {geometry_type}")


def _read_cache(cache_file: Path, station: str) -> list[dict] | None:
    try:
        rows = json.loads(cache_file.read_text(encoding="utf-8"))
        if not isinstance(rows, list) or not rows:
            return None
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("geometry"), dict):
                return None
            _validate_geometry(row["geometry"], station)
        return rows
    except (OSError, RuntimeError, ValueError, TypeError, json.JSONDecodeError):
        return None


def fetch_polygons(
    station: str,
    carrier: str = "ATS",
    force_refresh: bool = False,
    session: FailoverSiwSession | None = None,
) -> list[dict]:
    station = station.strip().upper()
    carrier = carrier.strip().upper()
    if carrier not in {"ATS", "ATA"}:
        raise LookupError(f"Unsupported carrier for polygon lookup: {carrier}")
    if not re.fullmatch(r"[A-Z0-9_-]{2,32}", station):
        raise LookupError(f"Invalid station code for polygon lookup: {station or '<blank>'}")
    cache_file = CACHE / f"{carrier}_{station}.json"
    cache_fresh = (
        cache_file.exists()
        and (time.time() - cache_file.stat().st_mtime) <= POLYGON_CACHE_MAX_AGE_DAYS * 86400
    )
    if cache_fresh and not force_refresh:
        cached = _read_cache(cache_file, station)
        if cached is not None:
            return cached

    owned_session = session is None
    active_session = session or FailoverSiwSession()
    try:
        station_data = _post(active_session, "gs", {"stationNames": station})
        record = _default_station_record(station_data, station, carrier)
        routes = _post(active_session, "gr", {"stationIds": record["id"], "operator": "kabideba"})
        if not isinstance(routes, list):
            raise RuntimeError(f"Unexpected response fetching routes for {station}")
        geometries: list[dict] = []
        for route in routes:
            if not isinstance(route, dict):
                raise RuntimeError(f"Route list contains a non-object entry for {station}")
            polygon = route.get("polygon") or {}
            if not isinstance(polygon, dict):
                raise RuntimeError(f"Route polygon is not an object for {station}")
            geometry = _unwrap_geometry(polygon.get("geometry"))
            if geometry is None:
                continue
            _validate_geometry(geometry, station)
            # isSortZoneActive is current. `active` is false on valid IN routes.
            if route.get("isSortZoneActive") is not False:
                geometries.append({"routeName": route.get("routeName"), "geometry": geometry})
        if not geometries:
            raise LookupError(f"No polygon geometry found for station {station}")
        temporary = cache_file.with_name(f".{cache_file.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps(geometries), encoding="utf-8")
            temporary.replace(cache_file)
        finally:
            temporary.unlink(missing_ok=True)
        return geometries
    finally:
        if owned_session:
            active_session.close()


# ------------------------------ dependency-free GeoJSON point-in-polygon -------------------
def _on_segment(px, py, ax, ay, bx, by, epsilon=1e-10) -> bool:
    cross = (px - ax) * (by - ay) - (py - ay) * (bx - ax)
    if abs(cross) > epsilon:
        return False
    return (
        min(ax, bx) - epsilon <= px <= max(ax, bx) + epsilon
        and min(ay, by) - epsilon <= py <= max(ay, by) + epsilon
    )


def _inside_ring(lon: float, lat: float, ring: list) -> bool:
    """Ray casting with the outer boundary included."""
    inside = False
    if len(ring) < 3:
        return False
    for index in range(len(ring)):
        ax, ay = ring[index - 1][:2]
        bx, by = ring[index][:2]
        if _on_segment(lon, lat, ax, ay, bx, by):
            return True
        if (ay > lat) != (by > lat):
            x_at_lat = (bx - ax) * (lat - ay) / (by - ay) + ax
            if lon < x_at_lat:
                inside = not inside
    return inside


def _inside_polygon(lon: float, lat: float, coordinates: list) -> bool:
    if not coordinates or not _inside_ring(lon, lat, coordinates[0]):
        return False
    return not any(_inside_ring(lon, lat, hole) for hole in coordinates[1:])


def point_in_geometry(lon: float, lat: float, geometry: dict) -> bool:
    if not isinstance(geometry, dict):
        raise ValueError("geometry must be an object")
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates") or []
    if geometry_type == "Polygon":
        return _inside_polygon(lon, lat, coordinates)
    if geometry_type == "MultiPolygon":
        return any(_inside_polygon(lon, lat, polygon) for polygon in coordinates)
    if geometry_type == "GeometryCollection":
        geometries = geometry.get("geometries") or []
        if not geometries:
            raise ValueError("empty GeometryCollection")
        return any(point_in_geometry(lon, lat, item) for item in geometries)
    raise ValueError(f"unsupported geometry type: {geometry_type}")


def check_pairs(
    pairs: list[tuple[str, str]],
    gcrs_rows: list[dict],
    log: Callable[[str], None] = print,
    force_refresh: bool = False,
    session: FailoverSiwSession | None = None,
    gcrs_errors: dict[str, str] | None = None,
) -> list[dict]:
    """Return one ordered output row for every supplied TID/station pair."""
    normalized_pairs = [(normalize_tid(tid), str(station or "").strip().upper()) for tid, station in pairs]
    gcrs_by_tid = {normalize_tid(row.get("Query_Item")): row for row in gcrs_rows}
    gcrs_errors = {normalize_tid(key): value for key, value in (gcrs_errors or {}).items()}
    station_keys: dict[tuple[str, str], list[dict] | None] = {}
    for tid, station in normalized_pairs:
        if station and tid in gcrs_by_tid:
            station_keys[(carrier_for_tid(tid), station)] = None

    owned_session = session is None
    active_session = session or FailoverSiwSession(log=log)
    errors: dict[tuple[str, str], str] = {}
    try:
        if station_keys:
            active_session.ensure_ready()
        log(f"polygon prefetch: {len(station_keys)} carrier/station pairs (up to 8 workers)")

        def load(key: tuple[str, str]) -> list[dict]:
            carrier, station = key
            return fetch_polygons(station, carrier, force_refresh, active_session)

        with ThreadPoolExecutor(max_workers=min(8, max(1, len(station_keys)))) as executor:
            futures = {executor.submit(load, key): key for key in station_keys}
            completed = 0
            for future in as_completed(futures):
                key = futures[future]
                try:
                    station_keys[key] = future.result()
                except Exception as exc:
                    errors[key] = str(exc)[:240]
                completed += 1
                if completed % 10 == 0 or completed == len(futures):
                    log(f"polygons {completed}/{len(futures)} (errors={len(errors)})")

        # Polygon fetch is attempted once per station. If it fails or the station
        # polygon is not found, the pair is reported as an error without retrying.
        output: list[dict] = []
        for tid, station in normalized_pairs:
            key = (carrier_for_tid(tid), station)
            if tid in gcrs_errors:
                status = f"Error — {gcrs_errors[tid]}"
            elif not station:
                status = "Error — Missing station code"
            elif tid not in gcrs_by_tid:
                status = "Error — SIW unresolved after degraded/poison guards"
            elif key in errors:
                status = f"Error — {errors[key]}"
            else:
                row = gcrs_by_tid[tid]
                try:
                    lat = float(row["Latitude"])
                    lon = float(row["Longitude"])
                    if not math.isfinite(lat) or not math.isfinite(lon):
                        raise ValueError("non-finite coordinates")
                    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
                        raise ValueError("coordinates outside geographic bounds")
                    inside = any(
                        point_in_geometry(lon, lat, route["geometry"])
                        for route in station_keys.get(key) or []
                    )
                except (TypeError, ValueError, KeyError) as exc:
                    status = f"Error — Invalid polygon/GCRS data: {str(exc)[:160]}"
                else:
                    status = INSIDE_TEXT if inside else OUTSIDE_TEXT
            # New additive column: CX Address Check. Computed from the resolved GCRS row
            # only (blank when there is no resolved row). It NEVER affects "GCRS Status".
            cx = cx_address_check(gcrs_by_tid.get(tid))
            output.append({
                "Tracking ID": tid,
                "Station": station,
                "GCRS Status": status,
                "CX Address Check": cx,
            })
    finally:
        if owned_session:
            active_session.close()

    inside_count = sum(row["GCRS Status"] == INSIDE_TEXT for row in output)
    outside_count = sum(row["GCRS Status"] == OUTSIDE_TEXT for row in output)
    error_count = sum(str(row["GCRS Status"]).startswith("Error") for row in output)
    log(f"polygon result: total={len(output)} inside={inside_count} outside={outside_count} errors={error_count}")
    return output


def check(
    tids: list[str],
    gcrs_rows: list[dict],
    station_by_tid: dict[str, str],
    log: Callable[[str], None] = print,
    force_refresh: bool = False,
    session: FailoverSiwSession | None = None,
) -> list[dict]:
    """Compatibility wrapper; new callers should use pair-preserving ``check_pairs``."""
    normalized_stations = {normalize_tid(key): value for key, value in station_by_tid.items()}
    pairs = [(tid, normalized_stations.get(normalize_tid(tid), "")) for tid in tids]
    return check_pairs(pairs, gcrs_rows, log, force_refresh, session)
