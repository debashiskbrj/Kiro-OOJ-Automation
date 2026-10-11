r"""Post-comments runner — gated, dry-run-first SIM ticket comment poster.

This is a HIGH-RISK, write-capable lane that is deliberately kept out of the main
``run_daily.py`` pipeline. It posts live, irreversible comments to SIM tickets via
Playwright/Edge, so it is only ever reached through an explicit operator gate:

  * ``--mode dry-run``  (default)  parses the uploaded workbook and writes a preview
                                   summary. It NEVER imports Playwright, opens a
                                   browser, or touches the network.
  * ``--mode confirmed``           performs the real posting, one pass over every
                                   pending url, saving an incremental report for
                                   crash recovery. Reachable only after a successful
                                   dry-run of the exact same file (gate enforced by
                                   ``post_comments_controller.py``).
  * ``--mode retry-failed``        re-runs only the urls whose latest today-dated
                                   report row failed, one pass, no blocking prompts.

The comment-posting flow is adapted (not copied) from the operator's reference script
``C:\Users\kabideba\Documents\TT_Automation\post_comments.py``: same per-url grouped
comment format ``TID: {tid} | {remark}``, same selectors, same multi-selector +
empty-textbox verification loop, same incremental same-day Status==Success dedup for
crash recovery. The reference script's blocking ``input()`` retry-until-no loop is
DROPPED in favour of a single pass plus a separate operator-triggered retry-failed run.

The shared Edge automation profile comes from ``ooj_paths.EDGE_PROFILE`` (never
hardcoded). The input workbook path is always supplied at runtime via ``--input``.

Usage:
  py -3.13 -X utf8 post_comments_runner.py --input <xlsx> --mode dry-run     --out-dir <dir>
  py -3.13 -X utf8 post_comments_runner.py --input <xlsx> --mode confirmed   --out-dir <dir>
  py -3.13 -X utf8 post_comments_runner.py --input <xlsx> --mode retry-failed --out-dir <dir>
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

# Columns required in the uploaded workbook (matched case-insensitively after strip()).
REQUIRED_COLUMNS = ("url", "tid", "remark")
# How many grouped comments the dry-run preview shows.
PREVIEW_LIMIT = 20
# Cap report rows parsed/written so a huge workbook cannot blow up the page.
MAX_REPORT_ROWS = 2000

# Playwright selectors (identical to the reference script).
COMMENT_BOX = "#sim-communicationActions--createComment"
POST_BUTTON = "button:has-text('Post to Correspondence')"
SUCCESS_SELECTORS = (
    'text="Comment added"',
    ".awsui-toast-type-success",
    'text="Success"',
)


# --------------------------------------------------------------------- logging
def _emit(line: str) -> None:
    """Print a progress line to stdout, flushed, for the controller's drain thread."""
    print(line, flush=True)


# --------------------------------------------------------------- atomic writes
def _write_json_atomic(path: Path, payload: dict) -> None:
    """Write JSON atomically (``<name>.tmp`` then ``Path.replace``), mirroring
    ``run_daily.Run.write_progress`` so an external reader never sees a half-written file."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


# --------------------------------------------------------------------- parsing
def _clean_cell(value) -> str:
    """Normalise a single cell to a stripped string ('' for blank/NaN)."""
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    text = str(value)
    if text.strip().lower() in ("nan", "nat"):
        return ""
    return text.strip()


def _comment_text(group: pd.DataFrame) -> str:
    """Build the exact per-url comment: 'TID: {tid} | {remark}' lines joined by newline."""
    return "\n".join(
        f"TID: {row['tid']} | {row['remark']}" for _, row in group.iterrows()
    )


def parse_workbook(input_path: Path) -> dict:
    """Parse and validate the uploaded workbook.

    Returns a dict with:
      total_rows        : number of data rows read
      unique_url_count  : number of distinct urls among the valid rows
      malformed_rows    : list of {row_index, reason} for rows missing url/tid/remark
      groups            : list of {url, group_df} in first-seen url order (valid rows only)

    A row with a blank/missing url, tid, or remark is treated as malformed: it is
    excluded from ``groups`` and listed in ``malformed_rows``. Parsing never raises for
    malformed data — a malformed-rows report is part of the dry-run deliverable.
    """
    df = pd.read_excel(input_path, engine="openpyxl")
    # Lower-case + strip every header; accept 'URL', ' Tid ', 'Remark', etc.
    df.columns = [str(column).strip().lower() for column in df.columns]

    missing_cols = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing_cols:
        raise ValueError(
            f"workbook is missing required column(s): {', '.join(missing_cols)} "
            f"(found: {', '.join(df.columns) or 'none'})"
        )

    total_rows = int(len(df))

    # Strip whitespace from every cell in the three columns we use.
    for column in REQUIRED_COLUMNS:
        df[column] = df[column].map(_clean_cell)

    malformed_rows: list[dict] = []
    valid_mask = []
    for index, row in df.iterrows():
        missing = [c for c in REQUIRED_COLUMNS if not row[c]]
        if missing:
            malformed_rows.append(
                {
                    # +2 => human-friendly 1-based spreadsheet row (header is row 1).
                    "row_index": int(index) + 2,
                    "reason": f"blank/missing {', '.join(missing)}",
                }
            )
            valid_mask.append(False)
        else:
            valid_mask.append(True)

    valid_df = df[pd.Series(valid_mask, index=df.index)]

    # Group by url preserving first-seen url order and row order within each url.
    groups = [
        {"url": url, "group": group}
        for url, group in valid_df.groupby("url", sort=False)
    ]

    return {
        "total_rows": total_rows,
        "unique_url_count": len(groups),
        "malformed_rows": malformed_rows,
        "groups": groups,
    }


# --------------------------------------------------------------------- dry-run
def run_dry_run(input_path: Path, out_dir: Path) -> dict:
    """Parse only — no Playwright, no browser, no network. Write a preview summary.

    Returns the summary dict (also written atomically to out_dir/dry_run_summary.json).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    _emit(f"RUN_DIR={out_dir}")

    parsed = parse_workbook(input_path)
    preview = [
        {
            "url": entry["url"],
            "tid_count": int(len(entry["group"])),
            "comment_text": _comment_text(entry["group"]),
        }
        for entry in parsed["groups"][:PREVIEW_LIMIT]
    ]

    summary = {
        "mode": "dry-run",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "input_file": input_path.name,
        "total_rows": parsed["total_rows"],
        "unique_url_count": parsed["unique_url_count"],
        "malformed_rows": parsed["malformed_rows"],
        "preview": preview,
    }
    _write_json_atomic(out_dir / "dry_run_summary.json", summary)

    _emit(
        f"parsed {summary['total_rows']} rows, "
        f"{summary['unique_url_count']} unique urls, "
        f"{len(summary['malformed_rows'])} malformed"
    )
    _emit("dry-run complete")
    return summary


# ------------------------------------------------------------ posting (shared)
def _group_results(url: str, tids, status: str) -> list[dict]:
    """One report record per tid in a url group (mirrors the reference report shape)."""
    now = datetime.now()
    return [
        {
            "TID": tid,
            "URL": url,
            "Status": status,
            "Time": now.strftime("%H:%M:%S"),
            "Date": now.strftime("%Y-%m-%d"),
        }
        for tid in tids
    ]


def _save_report(out_dir: Path, results: list[dict]) -> None:
    """Write the incremental report after each url (crash recovery)."""
    pd.DataFrame(results).to_excel(out_dir / "post_comments_report.xlsx", index=False)


def _post_one_url(page, url: str, comment: str, log) -> str:
    """Post a single url's comment and verify. Returns a Status string.

    Adapted from the reference script's per-url flow: fill the comment box, click
    Post to Correspondence, then poll the success selectors (and the box going empty)
    for up to 10s.
    """
    page.goto(url, timeout=60000, wait_until="domcontentloaded")
    page.wait_for_selector(COMMENT_BOX, timeout=20000)
    page.fill(COMMENT_BOX, comment)
    page.click(POST_BUTTON)

    start = datetime.now()
    verified = False
    while (datetime.now() - start).total_seconds() < 10:
        for selector in SUCCESS_SELECTORS:
            if page.is_visible(selector):
                verified = True
                break
        if not verified and page.input_value(COMMENT_BOX) == "":
            verified = True
        if verified:
            return "Success"
        page.wait_for_timeout(200)
    log(f"   !!! verification failed for {url}")
    return "Failed - No Confirmation"


def _post_groups(page, groups: list[dict], results: list[dict], out_dir: Path, log) -> None:
    """Post each pending url group exactly once, saving the report after each url."""
    total = len(groups)
    for index, entry in enumerate(groups):
        url = entry["url"]
        group = entry["group"]
        tids = list(group["tid"])
        log(f"[{index + 1}/{total}] {url}")
        try:
            status = _post_one_url(page, url, _comment_text(group), log)
        except Exception as exc:  # noqa: BLE001 — record and continue, never abort the pass
            status = f"Failed - {str(exc)[:80]}"
            log(f"   error: {status}")
        results.extend(_group_results(url, tids, status))
        _save_report(out_dir, results)


def _completed_urls_today(out_dir: Path) -> tuple[set, list[dict]]:
    """Read an existing report; return (urls Success today, existing today-dated rows)."""
    report = out_dir / "post_comments_report.xlsx"
    if not report.exists():
        return set(), []
    try:
        existing = pd.read_excel(report)
    except Exception:  # noqa: BLE001 — a corrupt/partial report just means no resume
        return set(), []
    if "Date" not in existing.columns:
        return set(), []
    today = datetime.now().strftime("%Y-%m-%d")
    today_rows = existing[existing["Date"].astype(str) == today]
    completed = set(
        today_rows[today_rows["Status"].astype(str) == "Success"]["URL"].astype(str)
    )
    return completed, today_rows.to_dict("records")


def _launch_and_post(input_path: Path, out_dir: Path, select_urls) -> dict:
    """Shared Playwright driver for confirmed + retry runs.

    ``select_urls(all_groups, out_dir)`` returns (pending_groups, seed_results): the
    url groups to post this pass and the pre-existing report rows to carry forward.
    """
    # Import Playwright lazily so the dry-run path never pulls it in.
    from playwright.sync_api import sync_playwright  # noqa: PLC0415

    from ooj_paths import EDGE_PROFILE  # noqa: PLC0415

    parsed = parse_workbook(input_path)
    pending, results = select_urls(parsed["groups"], out_dir)

    _emit(f"posting {len(pending)} url(s) this pass")
    if not pending:
        _save_report(out_dir, results) if results else None
        _emit("nothing to post")
        return {"posted_urls": 0}

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(EDGE_PROFILE),
            channel="msedge",
            headless=False,
            viewport={"width": 1280, "height": 800},
        )
        page = context.new_page()
        try:
            _post_groups(page, pending, results, out_dir, _emit)
        finally:
            context.close()

    _emit("posting pass complete")
    return {"posted_urls": len(pending)}


# ------------------------------------------------------------------ confirmed
def run_confirmed(input_path: Path, out_dir: Path) -> dict:
    """Post every pending url exactly once (one pass, no blocking prompts).

    Resume-by-today's-report dedup: a url whose latest today-dated row is Success is
    skipped so a crashed/restarted run does not double-post.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    _emit(f"RUN_DIR={out_dir}")

    def select(all_groups, directory):
        completed, seed = _completed_urls_today(directory)
        if completed:
            _emit(f"resuming: {len(completed)} url(s) already posted today")
        pending = [g for g in all_groups if g["url"] not in completed]
        return pending, seed

    return _launch_and_post(input_path, out_dir, select)


# --------------------------------------------------------------- retry-failed
def run_retry_failed(input_path: Path, out_dir: Path) -> dict:
    """Re-run only urls whose latest today-dated report row failed, one pass."""
    out_dir.mkdir(parents=True, exist_ok=True)
    _emit(f"RUN_DIR={out_dir}")

    def select(all_groups, directory):
        _completed, seed = _completed_urls_today(directory)
        failed_urls = {
            str(row["URL"])
            for row in seed
            if str(row.get("Status", "")).startswith("Failed")
        }
        # A url is still failed only if its latest today row is a failure (a later
        # Success for the same url means it is already done).
        success_urls = {
            str(row["URL"])
            for row in seed
            if str(row.get("Status", "")) == "Success"
        }
        retry_urls = failed_urls - success_urls
        _emit(f"retry targets: {len(retry_urls)} failed url(s)")
        pending = [g for g in all_groups if g["url"] in retry_urls]
        # Carry forward every existing row except the failed ones we are re-posting
        # (those get fresh rows appended by the posting pass).
        kept = [row for row in seed if str(row["URL"]) not in retry_urls]
        return pending, kept

    return _launch_and_post(input_path, out_dir, select)


# ----------------------------------------------------------------------- CLI
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="OOJ post-comments runner (gated)")
    parser.add_argument("--input", required=True, help="path to the uploaded .xlsx")
    parser.add_argument(
        "--mode",
        choices=("dry-run", "confirmed", "retry-failed"),
        default="dry-run",
        help="dry-run (default, parse only) | confirmed (post live) | retry-failed",
    )
    parser.add_argument("--out-dir", required=True, help="directory for summary/report/log")
    args = parser.parse_args(argv)

    input_path = Path(args.input)
    out_dir = Path(args.out_dir)

    if not input_path.exists():
        _emit(f"ERROR: input file not found: {input_path}")
        return 2

    if args.mode == "dry-run":
        run_dry_run(input_path, out_dir)
    elif args.mode == "confirmed":
        run_confirmed(input_path, out_dir)
    else:
        run_retry_failed(input_path, out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
