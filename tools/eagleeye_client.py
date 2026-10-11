r"""EagleEye data via the MCP API (fast, no browser).

The browser bulk UI renders a blank page inside the automation Edge context, so this
client uses the EagleEye MCP endpoint directly with the local Midway cookie jar. It
returns exactly the two fields the template's Eagle_EYE writer consumes -- ``searchId``
(the TID) and ``type`` (FORWARD / REVERSE) -- plus a few audit fields, and adds no-data
TIDs back with a blank type so the result stays row-complete.

Read-only: the endpoint is queried, nothing is written remotely. Chunks of 50 IDs keep
each request small and scale to the full daily volume.
"""
from __future__ import annotations

import argparse
import csv
import http.cookiejar
import json
import os
import tempfile
import time
from pathlib import Path

import requests

MCP_URL = "https://mcp.eagleeye-api.ats.amazon.dev/mcp"
COOKIE_FILE = os.path.join(os.path.expanduser("~"), ".midway", "cookie")
CHUNK = 50
REGION = "EU"
TIMEZONE = "Asia/Kolkata"
OUTPUT_COLUMNS = ["searchId", "type", "trackingId", "shipmentId", "scannableId", "ERROR"]


def _normalize_tid(value) -> str:
    text = str(value or "").strip().lstrip("\ufeff").replace("'", "")
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    return text.upper() if text.upper().startswith("DOC") else text


def _load_cookies() -> http.cookiejar.MozillaCookieJar:
    # mwinit writes HttpOnly cookies with a "#HttpOnly_" prefix that MozillaCookieJar
    # treats as comments; load a temp copy with the prefix stripped and remove it.
    with open(COOKIE_FILE, encoding="utf-8") as fh:
        raw = fh.read().replace("#HttpOnly_", "")
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", prefix="ooj-ee-",
                                         suffix=".cookies", delete=False) as tmp:
            tmp.write(raw)
            name = tmp.name
        jar = http.cookiejar.MozillaCookieJar(name)
        jar.load(ignore_discard=True, ignore_expires=True)
        return jar
    finally:
        if name:
            try:
                os.remove(name)
            except FileNotFoundError:
                pass


def _rpc(session, method, params, req_id):
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    body = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
    return session.post(MCP_URL, headers=headers, data=json.dumps(body), timeout=90, allow_redirects=True)


def _parse(resp):
    if "text/event-stream" in resp.headers.get("Content-Type", ""):
        lines = [ln[5:].strip() for ln in resp.text.splitlines() if ln.startswith("data:")]
        return json.loads(lines[-1]) if lines else None
    try:
        return resp.json()
    except ValueError:
        return None


def _new_session() -> requests.Session:
    session = requests.Session()
    session.cookies = _load_cookies()
    init = _rpc(session, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                        "clientInfo": {"name": "ooj-eagle", "version": "1.0"}}, 1)
    if init.status_code != 200:
        raise RuntimeError(f"EagleEye initialize failed: HTTP {init.status_code}")
    return session


def _search_chunk(session, chunk: list[str], attempt: int = 0):
    resp = _rpc(session, "tools/call",
                {"name": "eagle_eye_scannable_search",
                 "arguments": {"scannable_ids": chunk, "region": REGION, "timezone": TIMEZONE}}, 3)
    if resp.status_code != 200:
        if attempt < 2:
            time.sleep(2 ** (attempt + 1))
            return _search_chunk(session, chunk, attempt + 1)
        raise RuntimeError(f"EagleEye search failed: HTTP {resp.status_code}")
    payload = _parse(resp)
    try:
        return json.loads(payload["result"]["content"][0]["text"])
    except Exception as exc:
        raise RuntimeError(f"EagleEye unexpected payload: {json.dumps(payload)[:300]}") from exc


def fetch(tids: list[str], log=print) -> list[dict]:
    requested = [
        value
        for value in dict.fromkeys(_normalize_tid(tid) for tid in tids)
        if value and value != "TID not found"
    ]
    if not requested:
        return []
    session = _new_session()
    rows: list[dict] = []
    got: set[str] = set()
    for offset in range(0, len(requested), CHUNK):
        chunk = requested[offset:offset + CHUNK]
        data = _search_chunk(session, chunk)
        for entry in data.get("data") or []:
            pkg = entry.get("package") or {}
            tid = _normalize_tid(pkg.get("trackingId") or pkg.get("inputIdentifier"))
            got.add(tid)
            rows.append({
                "searchId": tid,
                "type": str(pkg.get("package_type") or pkg.get("transPlanType") or "").replace("'", "").strip().upper(),
                "trackingId": tid,
                "shipmentId": str(pkg.get("orderingShipmentId") or pkg.get("encryptedShipmentId") or "").strip(),
                "scannableId": str(pkg.get("scannableId") or "").strip(),
                "ERROR": "",
            })
        log(f"EagleEye chunk {offset // CHUNK + 1}: {len(chunk)} asked, {len(got)} found so far")
    missing = [tid for tid in requested if tid not in got]
    rows.extend({
        "searchId": tid, "type": "", "trackingId": "", "shipmentId": "",
        "scannableId": "", "ERROR": f"Data not found for search id {tid}",
    } for tid in missing)
    log(f"EagleEye: {len(rows)} rows, {len(missing)} without data (no-data TIDs)")
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="EagleEye MCP API fetch")
    parser.add_argument("--tid-file", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    tids = [ln.strip() for ln in Path(args.tid_file).read_text(encoding="utf-8").splitlines() if ln.strip()]
    rows = fetch(tids)
    with open(args.out, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"EagleEye wrote {len(rows)} rows -> {args.out}")


if __name__ == "__main__":
    main()
