r"""DSR Waterfall Zone export (QuickSight browser adapter).

Uses the proven QuickSight mechanics in pnama_export.py with DSR-specific configuration:
  dashboard/sheet = IN_SWA_DSR_Waterfall_Revamp_Revised_Dashboard / DSR Raw data
  control         = Tracking id (one TID per line in a popover textarea)
  visual/export   = DSR Raw data -> Menu options -> Export to CSV
Output is validated for no unrelated TIDs and for an actual Zone column.

Usage:
  python dsr_export.py --tid-file tids.txt --out dsr.csv
  python dsr_export.py --tids 372494929687 372435819602
"""
import argparse
import csv
import json
import re
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

import pnama_export as driver

DASHBOARD_URL = ("https://us-east-1.quicksight.aws.amazon.com/sn/account/amazonbi/dashboards/"
                 "9a951c72-3cae-4c18-bfd1-69231a4f10d1/sheets/"
                 "9a951c72-3cae-4c18-bfd1-69231a4f10d1_9fe3f0ac-9f42-4ef3-b81e-6352c9158f76")
TARGET_TAB = "DSR Raw data"
CONTROL_LABEL = "Tracking id"
OUT = Path(__file__).parent / "captures" / "dsr"
DL_DIR = OUT / "_browser_downloads"
OUT.mkdir(parents=True, exist_ok=True)
DL_DIR.mkdir(parents=True, exist_ok=True)


def configure(log_fn):
    """Configure the shared QuickSight driver for DSR (one isolated process per call)."""
    driver.DASHBOARD_URL = DASHBOARD_URL
    driver.TARGET_TAB = TARGET_TAB
    driver.CONTROL_LABEL = CONTROL_LABEL
    driver.OUT = OUT
    driver.DL_DIR = DL_DIR
    driver.STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
    driver.log = log_fn


def normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def validate(path, requested: list[str], rows=None, columns=None) -> dict:
    """Validate a DSR export against the requested TID set.

    ``path`` is read when ``rows`` is not supplied. A caller that has already parsed the
    same CSV may pass the parsed ``rows`` (and the ``columns`` / fieldnames) to avoid a
    second disk read; the result is identical to reading the file here.
    """
    if rows is None:
        with open(path, encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            columns = reader.fieldnames or []
            rows = list(reader)
    else:
        columns = columns or []
    tracking_col = next((c for c in columns if normalize(c) in {"trackingid", "tid"}), None)
    zone_col = next((c for c in columns if normalize(c) in {"zone", "deliverynodezone", "stationzone"}), None)
    exported = [str(r.get(tracking_col, "")).strip().split(".")[0] for r in rows] if tracking_col else []
    requested_set, exported_set = set(requested), set(exported)
    zones_by_tid = {}
    if tracking_col and zone_col:
        for row in rows:
            tid = str(row.get(tracking_col, "")).strip().split(".")[0]
            zone = str(row.get(zone_col, "")).strip()
            zones_by_tid.setdefault(tid, set()).add(zone)
    conflicting_zones = {tid: sorted(zones) for tid, zones in zones_by_tid.items() if len(zones - {""}) > 1}
    duplicate_tids = {tid: count for tid, count in Counter(exported).items() if count > 1}
    return {
        "rows": len(rows), "columns": columns, "tracking_col": tracking_col, "zone_col": zone_col,
        "requested": len(requested_set), "exported_unique": len(exported_set),
        "missing": sorted(requested_set - exported_set), "extra": sorted(exported_set - requested_set),
        "blank_zone": sum(1 for r in rows if zone_col and not str(r.get(zone_col, "")).strip()),
        "duplicate_tids": duplicate_tids, "conflicting_zones": conflicting_zones,
    }


def fetch(tids: list[str], log_fn=print) -> tuple[list[dict], dict]:
    tids = [value for value in dict.fromkeys(str(tid).strip().lstrip("\ufeff") for tid in tids)
            if value and value != "TID not found"]
    if not tids:
        return [], {"rows": 0, "columns": [], "tracking_col": None, "zone_col": None,
                    "requested": 0, "exported_unique": 0, "missing": [], "extra": [], "blank_zone": 0}
    configure(log_fn)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    with sync_playwright() as playwright:
        context = driver._launch(playwright)
        page = context.new_page()
        before = {item.name for item in DL_DIR.glob("*")}
        cdp = driver.configure_download_directory(context, page)
        try:
            def _drive_once(_before):
                driver.open_dashboard(page)
                driver.select_tab(page)
                driver.wait_visual_idle(page, timeout_s=120)
                driver.set_tracking_filter(page, tids)
                driver.wait_visual_idle(page, timeout_s=300)
                cap = driver.export_csv(page)
                dl = driver.wait_for_download(_before, timeout_s=420) if cap is None else None
                return cap, dl

            # If the first attempt fails to pull data, refresh the page once and retry.
            attempts = 2
            captured = downloaded = None
            for attempt in range(1, attempts + 1):
                try:
                    captured, downloaded = _drive_once(before)
                    break
                except Exception as exc:
                    log_fn(f"DSR attempt {attempt}/{attempts} failed: {type(exc).__name__}: {str(exc)[:200]}")
                    if attempt >= attempts:
                        raise
                    log_fn("DSR: refreshing the page and trying again")
                    try:
                        page.reload(timeout=90_000, wait_until="domcontentloaded")
                    except Exception as reload_exc:
                        log_fn(f"DSR reload interrupted: {str(reload_exc)[:80]}")
                    before = {item.name for item in DL_DIR.glob("*")}
                    time.sleep(3)
        finally:
            try:
                cdp.detach()
            except Exception:
                pass
            try:
                context.close()
            except Exception:
                pass
    target = OUT / f"dsr_export_{stamp}.csv"
    if captured is not None:
        target.write_text(captured["text"], encoding="utf-8")
    else:
        Path(downloaded).replace(target)
    result = validate(target, tids)
    log_fn(f"DSR: rows={result['rows']} requested={result['requested']} exported_unique={result['exported_unique']} "
           f"missing={len(result['missing'])} extra={len(result['extra'])} zone_col={result['zone_col']} "
           f"blank_zone={result['blank_zone']}")
    if result["extra"]:
        raise RuntimeError(f"DSR export contains unrelated TIDs: {result['extra'][:5]}")
    if not result["tracking_col"]:
        raise RuntimeError(f"DSR export has no tracking-ID column; columns={result['columns'][:20]}")
    if not result["zone_col"]:
        raise RuntimeError(f"DSR export has no Zone column; columns={result['columns']}")
    if result["conflicting_zones"]:
        raise RuntimeError(
            f"DSR Zone contract failed: conflicts={dict(list(result['conflicting_zones'].items())[:5])}"
        )
    if result["missing"] or result["blank_zone"]:
        log_fn(
            f"DSR no-data accepted as blank Zone: missing={len(result['missing'])} "
            f"blank_zone={result['blank_zone']}"
        )
    with open(target, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    # Canonical keys consumed by fill_template, regardless of dashboard header aliases.
    for row in rows:
        row["tracking_id"] = str(row.get(result["tracking_col"], "")).strip().split(".")[0]
        row["zone"] = str(row.get(result["zone_col"], "")).strip()
    return rows, result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tids", nargs="*", default=[])
    parser.add_argument("--tid-file")
    parser.add_argument("--out")
    args = parser.parse_args()
    tids = list(args.tids)
    if args.tid_file:
        tids += [line.strip().split()[0] for line in Path(args.tid_file).read_text(encoding="utf-8-sig").splitlines()
                 if line.strip()]
    rows, result = fetch(tids)
    if args.out:
        with open(args.out, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
    print(json.dumps({k: (len(v) if isinstance(v, list) else v) for k, v in result.items() if k != "columns"}))


if __name__ == "__main__":
    main()
