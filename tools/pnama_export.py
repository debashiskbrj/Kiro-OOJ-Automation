r"""PNAMA (IN_SWA_PNAMA_Dashboard) export driver.

Flow (matches the user's manual procedure):
  open dashboard (edge_profile, Midway session) -> tab "SWA repeat_Prox_Pnama_match Raw data"
  -> Controls: click the "tracking_id equals" chip -> replace value with our TID list -> apply
  -> wait for the raw-data visual to finish loading -> visual top-right "⋮" -> "Export to CSV"
  -> save download -> validate exported TIDs against the requested set.

Usage:
  python pnama_export.py --tids 372494929687 372435819602 ...
  python pnama_export.py --tid-file tids.txt [--out result.csv]

Outputs (tools\captures\pnama\): pnama_export_<stamp>.csv, step screenshots, pnama_export.log
Read-only against QuickSight (filter + export only; "Reset to original" is never needed
because dashboard state is per-viewer session).
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

import browser_blob_capture
from ooj_paths import EDGE_PROFILE

PROFILE_DIR = str(EDGE_PROFILE)
DASHBOARD_URL = ("https://us-east-1.quicksight.aws.amazon.com/sn/account/amazonbi/dashboards/"
                 "1650383a-7bfd-409c-9406-d364810f8019")
TARGET_TAB = "SWA repeat_Prox_Pnama_match Raw data"
CONTROL_LABEL = "tracking_id"
OUT = Path(__file__).parent / "captures" / "pnama"
OUT.mkdir(parents=True, exist_ok=True)
LOG = OUT / "pnama_export.log"
STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")


def log(msg):
    line = f"[{datetime.now():%H:%M:%S}] {msg}"
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    print(line, flush=True)


def shot(page, name):
    page.screenshot(path=str(OUT / f"{STAMP}_{name}.png"), full_page=False)


def dump_dom_hints(page, name):
    """Save a compact inventory of interactive elements for debugging selector issues."""
    info = page.evaluate("""
    () => ({
      inputs: Array.from(document.querySelectorAll('input, textarea, [contenteditable="true"]')).slice(0,40).map(e => ({
        tag: e.tagName, type: e.getAttribute('type'), placeholder: e.getAttribute('placeholder'),
        aria: e.getAttribute('aria-label'), id: e.id, cls: (e.className||'').toString().slice(0,60), value: (e.value||e.innerText||'').slice(0,80)})),
      buttons: Array.from(document.querySelectorAll('button, [role="button"], [role="menuitem"]')).slice(0,120).map(e => ({
        aria: e.getAttribute('aria-label'), title: e.getAttribute('title'), text: (e.innerText||'').trim().slice(0,40),
        testid: e.getAttribute('data-testid') || e.getAttribute('data-automation-id')})).filter(b => b.aria || b.title || b.text || b.testid),
    })""")
    (OUT / f"{STAMP}_{name}_dom.json").write_text(json.dumps(info, indent=1, ensure_ascii=False), encoding="utf-8")
    return info


def open_dashboard(page):
    def nav():
        try:
            page.goto(DASHBOARD_URL, timeout=90_000, wait_until="domcontentloaded")
        except Exception as exc:
            log(f"goto interrupted (AEA): {str(exc)[:70]}")
    nav()
    for i in range(150):
        time.sleep(2)
        url = page.url
        if url == "about:blank" and i % 3 == 2:
            nav()
            continue
        if "midway" in url.lower() or "federate" in url.lower() or "/sn/auth/" in url:
            if i % 10 == 0:
                log("login page — please authenticate in the browser window...")
            continue
        if "/sn/account/" in url:
            try:
                body = page.locator("body").inner_text(timeout=5_000)
            except Exception:
                body = ""
            dashboard_markers = (
                page.locator("[role='tab']").count() > 0
                or TARGET_TAB.lower() in body.lower()
                or ("controls" in body.lower() and "tracking_id" in body.lower())
            )
            if dashboard_markers:
                break
    else:
        raise RuntimeError("dashboard did not load in time")
    time.sleep(5)
    log(f"dashboard loaded: {page.title()}")


def select_tab(page):
    tab = page.get_by_role("tab", name=TARGET_TAB)
    if not tab.count():
        # tab strip may be scrolled; try the overflow arrows then text match
        tab = page.get_by_text(TARGET_TAB, exact=True)
    tab.first.click()
    time.sleep(6)
    log(f"tab selected: {TARGET_TAB}")
    shot(page, "01_tab")


def wait_visual_idle(page, timeout_s=180):
    """Wait for QuickSight loading indicators to clear.

    The table is virtualized and often exposes zero DOM grid cells even while visibly
    populated, so cell count is only an early-success hint. Twelve continuously idle
    seconds is the fallback completion signal (#Q9).
    """
    deadline = time.time() + timeout_s
    last = None
    idle_since = None
    while time.time() < deadline:
        time.sleep(2)
        state = page.evaluate("""
        () => {
          const busy = document.querySelectorAll('[class*="loading"], [class*="Loading"], [class*="spinner"], [class*="Spinner"], [aria-busy="true"]').length;
          const cells = document.querySelectorAll('[role="gridcell"], [role="cell"], td').length;
          const rows = document.querySelectorAll('[role="row"], tr').length;
          const empty = /no data|No data|didn\\'t return any data|returned no data/.test(document.body.innerText);
          return {busy, cells, rows, empty};
        }""")
        if state != last:
            log(f"visual state: {state}")
            last = state
        if state["busy"]:
            idle_since = None
            continue
        if state["cells"] > 0 or state["empty"]:
            return state
        idle_since = idle_since or time.time()
        if time.time() - idle_since >= 12:
            log("visual idle for 12 s (virtualized table; zero DOM cells is expected)")
            return state
    log("WARNING: visual did not become idle within timeout")
    return last


def set_tracking_filter(page, tids):
    """Replace the tracking_id control's value list with our TIDs.

    The control is a MUI Select whose popover contains a single TEXTAREA (Vega text field)
    holding one TID per line — the same thing the user pastes manually.
    """
    # 1. The collapsed Controls strip: clicking its label/value chip expands the grid.
    # PNAMA labels this "tracking_id equals"; DSR labels it "Tracking id".
    label_re = re.compile(rf"{re.escape(CONTROL_LABEL)}(?:\s+equals)?", re.I)
    chip = page.locator("span.sheet-control-label-not-expanded, h5").filter(has_text=label_re).filter(visible=True).first
    if not chip.count():
        chip = page.get_by_text(label_re).filter(visible=True).first
    chip.click()
    time.sleep(3)
    # 2. Open the select that currently shows the TID list (or "All" if it was reset).
    selects = page.locator("[class*='MuiSelect-select'], [role='button'][aria-haspopup='listbox']").filter(visible=True)
    target = None
    for i in range(selects.count()):
        txt = (selects.nth(i).inner_text() or "").strip()
        if re.search(r"\d{12}", txt):
            target = selects.nth(i)
            break
    if target is None:
        # first select after the control label
        target = page.get_by_text(re.compile(rf"^{re.escape(CONTROL_LABEL)}(?:\s+equals)?$", re.I)).first.locator(
            "xpath=following::*[contains(@class,'MuiSelect-select')][1]")
    target.click()
    time.sleep(2)
    shot(page, "02_control_open")

    # 3. The popover textarea.
    ta = page.locator("[role='listbox'] textarea, .MuiPopover-paper textarea, [class*='MuiMenu-paper'] textarea").filter(visible=True).first
    ta.wait_for(state="visible", timeout=15_000)
    old = ta.input_value()
    log(f"textarea current value: {len(old.splitlines())} lines, first={old.splitlines()[:1]}")
    ta.click()
    page.keyboard.press("Control+A")
    page.keyboard.press("Delete")
    ta.fill("\n".join(tids))
    time.sleep(1)
    new = ta.input_value()
    log(f"textarea new value: {len(new.splitlines())} lines")
    shot(page, "03_filled")
    # 4. Close the popover; QuickSight applies the control value on close.
    page.keyboard.press("Escape")
    time.sleep(3)
    shot(page, "04_applied")
    # confirm the select now shows our first TID
    shown = page.locator("[class*='MuiSelect-select']").filter(has_text=tids[0]).count()
    log(f"filter applied with {len(tids)} TIDs; select shows first TID: {bool(shown)}")


def configure_download_directory(ctx, page):
    """Force Chromium/Edge to persist downloads itself, outside Playwright temp storage.

    QuickSight closes the entire automation context milliseconds after its blob download
    event. Browser-level CDP download behavior lets Edge finish writing the CSV even when
    Playwright can no longer access the Download object (#Q13).
    """
    DL_DIR.mkdir(parents=True, exist_ok=True)
    cdp = ctx.new_cdp_session(page)
    params = {"behavior": "allow", "downloadPath": str(DL_DIR.resolve()), "eventsEnabled": True}
    try:
        cdp.send("Browser.setDownloadBehavior", params)
        log(f"CDP Browser.setDownloadBehavior -> {DL_DIR}")
    except Exception as browser_error:
        try:
            cdp.send("Page.setDownloadBehavior", {"behavior": "allow", "downloadPath": str(DL_DIR.resolve())})
            log(f"CDP Page.setDownloadBehavior fallback -> {DL_DIR}")
        except Exception as page_error:
            raise RuntimeError(
                f"could not configure browser download path: Browser={browser_error}; Page={page_error}"
            ) from page_error
    return cdp


def wait_for_download(before: set[str], timeout_s: int = 360) -> Path:
    """Return a new stable CSV written by Edge to DL_DIR."""
    deadline = time.time() + timeout_s
    candidate = None
    last_size = -1
    stable_since = None
    while time.time() < deadline:
        current = [p for p in DL_DIR.glob("*")
                   if p.name not in before and p.suffix.lower() not in (".crdownload", ".tmp", ".partial")]
        if current:
            newest = max(current, key=lambda p: p.stat().st_mtime)
            size = newest.stat().st_size
            if candidate != newest or size != last_size:
                candidate, last_size, stable_since = newest, size, time.time()
            elif size > 0 and stable_since and time.time() - stable_since >= 2:
                return newest
        time.sleep(1)
    raise RuntimeError(f"Export to CSV produced no stable file in {DL_DIR} within {timeout_s}s")


def export_csv(page):
    """Hover the raw-data visual, open its top-right menu, and request Export to CSV."""
    # Target the VISUAL, not the identically named sheet tab. QuickSight gives every
    # visual an accessibility label such as "Table, DSR Raw data".
    visual_label = page.locator("label.sn-visually-hidden").filter(
        has_text=re.compile(rf"^Table,\s*{re.escape(TARGET_TAB)}$", re.I)
    )
    visual = visual_label.first.locator("xpath=ancestor::*[contains(@class,'grid-block')][1]") \
        if visual_label.count() else None
    # Some QuickSight table states omit the hidden label. The exact `tracking_id` table
    # header is unambiguous (the control label is "Tracking id" with a space).
    table_header = page.get_by_text("tracking_id", exact=True)
    header_box = table_header.last.bounding_box() if table_header.count() else None
    if visual is None or not visual.count():
        if header_box:
            log(f"visual anchor via tracking_id table header box={header_box}")
        else:
            log("tracking_id table header not found")
    if visual is not None and visual.count():
        log(f"visual accessibility label found; container class={(visual.get_attribute('class') or '')[:100]!r}; box={visual.bounding_box()}")
    else:
        log("visual accessibility label/container not found; using text fallback")

    hover_attempt = 0

    def hover_visual():
        nonlocal hover_attempt
        hover_attempt += 1
        if table_header.count():
            # QuickSight mounts the toolbar from React hover state. Dispatch bubbling pointer/
            # mouse events through the visual ancestry before moving the real mouse.
            table_header.last.evaluate("""
                element => {
                  for (let node = element; node; node = node.parentElement) {
                    for (const type of ['pointerover', 'mouseover', 'mouseenter', 'mousemove']) {
                      node.dispatchEvent(new MouseEvent(type, {bubbles: true, cancelable: true, view: window}));
                    }
                  }
                }
            """)
        if visual is not None and visual.count():
            box = visual.bounding_box()
            if box:
                # Stay inside the visual (not on its scrollbar). Cycle a few header positions.
                offsets = ((80, 18), (45, 32), (120, 28), (200, 20))
                right, down = offsets[(hover_attempt - 1) % len(offsets)]
                page.mouse.move(box["x"] + box["width"] - right, box["y"] + down)
                return
        if header_box:
            # Virtualized tables may have no stable ancestor. Anchor Y to the actual table
            # header and X to the viewport's right edge where the visual toolbar is mounted.
            positions = ((45, -18), (80, -12), (120, -22), (35, 5))
            right, delta_y = positions[(hover_attempt - 1) % len(positions)]
            viewport_width = (page.viewport_size or {"width": 1600})["width"]
            page.mouse.move(viewport_width - right, max(5, header_box["y"] + delta_y))
            return
        # Last fallback for visuals whose accessibility/header anchors are absent.
        page.get_by_text(TARGET_TAB, exact=True).last.hover(force=True)

    # The overlay is mounted only after the virtualized visual is actually ready; QuickSight's
    # generic loading indicators can clear before that. Re-hover until the visual menu appears.
    deadline = time.time() + 180
    cands = []
    while time.time() < deadline:
        hover_visual()
        time.sleep(2)
        menu_btn = page.locator(
            "button[aria-label*='Menu options' i], button[aria-label*='menu' i], "
            "button[data-testid*='menu' i]"
        ).filter(visible=True)
        cands = [menu_btn.nth(i) for i in range(menu_btn.count())
                 if not (menu_btn.nth(i).get_attribute("aria-label") or "").startswith("Options - ")]
        if cands:
            break
        time.sleep(3)
    log(f"visual menu candidates: {len(cands)}")
    if not cands:
        dump_dom_hints(page, "05_visual_hover")
        shot(page, "05_visual_hover")
        raise RuntimeError("visual menu button not found after 180 s — see *_05_visual_hover diagnostics")
    cands[-1].click()
    time.sleep(2)
    shot(page, "06_menu")
    items = [t.strip() for t in page.locator("[role='menuitem'], [role='menu'] li").all_inner_texts() if t.strip()]
    log(f"menu items: {items}")

    browser_blob_capture.install(page)
    exp = page.get_by_role("menuitem", name=re.compile(r"Export to CSV", re.I))
    if not exp.count():
        exp = page.get_by_text(re.compile(r"^Export to CSV$", re.I))
    exp.first.click()
    log("clicked Export to CSV; capturing QuickSight's generated blob in-page")
    return browser_blob_capture.wait(page, timeout_s=360, log=log)


click_export = export_csv


def validate(csv_path, requested: list[str], rows=None, columns=None) -> dict:
    """Validate a PNAMA export against the requested TID set.

    ``csv_path`` is read when ``rows`` is not supplied. A caller that has already parsed
    the same CSV may pass the parsed ``rows`` (and the ``columns`` / fieldnames) to avoid
    a second disk read; the result is identical to reading the file here.
    """
    if rows is None:
        with open(csv_path, encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            cols = reader.fieldnames or []
            rows = list(reader)
    else:
        cols = columns or []
    required = {"tracking_id", "station_missort", "reference_tid", "reference_station"}
    missing_columns = sorted(required - set(cols))
    if missing_columns:
        raise RuntimeError(f"PNAMA export missing required columns: {missing_columns}")
    col = next((c for c in cols if c.strip().lower() == "tracking_id"), None)
    exported = [str(r[col]).strip().split(".")[0] for r in rows] if col else []
    req = set(requested)
    exp = set(exported)
    multiplicity = {tid: count for tid, count in Counter(exported).items() if count > 1}
    result = {"rows": len(rows), "columns": cols, "tracking_col": col,
              "requested": len(req), "exported_unique": len(exp),
              "missing": sorted(req - exp), "extra": sorted(exp - req),
              "multi_match_tids": multiplicity, "additional_match_rows": sum(count - 1 for count in multiplicity.values())}
    return result


DL_DIR = OUT / "_browser_downloads"


def _launch(p):
    """Persistent Edge context whose OWN download folder is DL_DIR (browser writes the file itself)."""
    DL_DIR.mkdir(parents=True, exist_ok=True)
    return p.chromium.launch_persistent_context(
        user_data_dir=PROFILE_DIR, channel="msedge", headless=False,
        viewport={"width": 1600, "height": 1000}, accept_downloads=True, downloads_path=str(DL_DIR),
        ignore_default_args=["--disable-extensions", "--disable-component-extensions-with-background-pages",
                             "--disable-component-update", "--disable-background-networking"],
        timeout=90_000,
    )


def _wait_for_file(before: set[str], timeout_s: int = 300) -> Path | None:
    """Wait for a new, fully written CSV in Edge's download dir (or the user's Downloads as fallback)."""
    folders = [DL_DIR, Path.home() / "Downloads"]
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        for folder in folders:
            for f in folder.glob("SWA_repeat_Prox_Pnam*"):
                if str(f) in before or f.suffix.lower() in (".crdownload", ".tmp", ".partial"):
                    continue
                s1 = f.stat().st_size
                time.sleep(1.5)
                if f.exists() and f.stat().st_size == s1 and s1 > 0:
                    return f
        time.sleep(2)
    return None


def fetch(tids: list[str], log_fn=None) -> tuple[list[dict], dict]:
    """Pipeline entry: export PNAMA rows for the TIDs; returns (rows, validation)."""
    global log
    if log_fn:
        log = log_fn  # route this module's logging into the caller's log
    tids = [t for t in dict.fromkeys(str(t).strip().lstrip("\ufeff") for t in tids) if t and t != "TID not found"]
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    downloaded = None
    captured = None
    with sync_playwright() as p:
        ctx = _launch(p)
        page = ctx.new_page()
        before = {item.name for item in DL_DIR.glob("*")}
        cdp = configure_download_directory(ctx, page)
        try:
            open_dashboard(page)
            select_tab(page)
            wait_visual_idle(page, timeout_s=90)
            set_tracking_filter(page, tids)
            wait_visual_idle(page, timeout_s=240)
            captured = export_csv(page)
            if captured is None:  # CDP/native-download fallback
                downloaded = wait_for_download(before)
        finally:
            try:
                cdp.detach()
            except Exception:
                pass
            try:
                ctx.close()
            except Exception:
                pass
    target = OUT / f"pnama_export_{stamp}.csv"
    if captured is not None:
        target.write_text(captured["text"], encoding="utf-8")
        log(f"PNAMA: wrote captured blob -> {target.name}")
    else:
        Path(downloaded).replace(target)
        log(f"PNAMA: saved {target.name} (from {Path(downloaded).name})")
    res = validate(target, tids)
    log(f"PNAMA: rows={res['rows']} requested={res['requested']} exported_unique={res['exported_unique']} "
        f"missing={len(res['missing'])} extra={len(res['extra'])}")
    if res["extra"]:
        raise RuntimeError(f"PNAMA export contains TIDs outside the request (filter not applied?): {res['extra'][:5]}")
    with open(target, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    return rows, res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tids", nargs="*", default=[])
    ap.add_argument("--tid-file")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    tids = list(args.tids)
    if args.tid_file:
        tids += [line.strip().split()[0] for line in Path(args.tid_file).read_text(encoding="utf-8-sig").splitlines()
                 if line.strip()]
    tids = list(dict.fromkeys(value.replace("'", "") for value in tids))
    if not tids:
        raise SystemExit("no TIDs given")
    log(f"=== PNAMA export {STAMP}: {len(tids)} TIDs")

    with sync_playwright() as p:
        ctx = _launch(p)
        page = ctx.new_page()
        cdp = configure_download_directory(ctx, page)
        before = {item.name for item in DL_DIR.glob("*")}
        target = Path(args.out) if args.out else OUT / f"pnama_export_{STAMP}.csv"

        def _run_once(_before):
            open_dashboard(page)
            select_tab(page)
            wait_visual_idle(page, timeout_s=120)
            set_tracking_filter(page, tids)
            wait_visual_idle(page, timeout_s=180)
            shot(page, "05_filtered")
            captured = export_csv(page)
            if captured is not None:
                target.write_text(captured["text"], encoding="utf-8")
            else:
                downloaded = wait_for_download(_before)
                Path(downloaded).replace(target)
            log(f"saved {target}")
            res = validate(target, tids)
            log(f"validation: rows={res['rows']} tracking_col={res['tracking_col']} requested={res['requested']} "
                f"exported_unique={res['exported_unique']} missing={len(res['missing'])} extra={len(res['extra'])}")
            if res["extra"]:
                raise RuntimeError(f"PNAMA export contains unrelated TIDs: {res['extra'][:10]}")
            if res["missing"]:
                log(f"missing (no PNAMA match — expected for some): {res['missing'][:20]}")
            log(f"columns ({len(res['columns'])}): {res['columns'][:30]}")
            (OUT / f"pnama_export_{STAMP}_validation.json").write_text(json.dumps(res, indent=2), encoding="utf-8")

        try:
            # If the first attempt fails to pull data, refresh the page once and retry.
            attempts = 2
            for attempt in range(1, attempts + 1):
                try:
                    _run_once(before)
                    break
                except Exception as exc:
                    log(f"PNAMA attempt {attempt}/{attempts} failed: {type(exc).__name__}: {str(exc)[:200]}")
                    try:
                        shot(page, f"9{attempt}_error")
                        dump_dom_hints(page, f"9{attempt}_error")
                    except Exception:
                        pass
                    if attempt >= attempts:
                        raise
                    log("PNAMA: refreshing the page and trying again")
                    try:
                        page.reload(timeout=90_000, wait_until="domcontentloaded")
                    except Exception as reload_exc:
                        log(f"PNAMA reload interrupted: {str(reload_exc)[:80]}")
                    before = {item.name for item in DL_DIR.glob("*")}
                    time.sleep(3)
        finally:
            try:
                cdp.detach()
            except Exception:
                pass
            try:
                ctx.close()
            except Exception:
                pass
    log("=== done")


if __name__ == "__main__":
    main()
