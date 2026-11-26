"""Bulk-resolve SIM-T tickets (status Resolved, closure code Successful).

Operator feature (web page section "5. Resolve tickets"). Input is a list of unique
SIM-T ticket IDs (the web controller extracts them from pasted TT URLs and removes
duplicates with ``parse_ticket_input``). Two phases:

  Phase 1 (resolve, no waiting) — for every ticket:
    1. read its current status (TicketingReadActions get-ticket, metadata only);
    2. already Resolved / Closed  -> "Already resolved" (nothing is changed);
    3. otherwise update-ticket status=Resolved, closureCode=Successful
       (TicketingWriteActions, under the operator's own Midway login) -> sent.csv.
  Phase 2 (verify) — VERIFY_WAIT seconds later, read every sent ticket: Resolved ->
    "Resolved"; not yet -> re-read after each VERIFY_RETRY_DELAYS step; still not ->
    "Failed" with a recheck hint. SIM-T keeps showing the old status for a while after a
    successful update, which is why verification is a separate, later phase.

No comment is added and no other field is touched. Final results are appended to
results.csv as soon as they are known; sent.csv records every accepted resolve, so a
stopped run is still reported correctly. Worker threads (each with its own builder-mcp
process) run in parallel so a 2,000+ ticket list finishes in reasonable time.

Usage:
    py -3.13 resolve_tickets_runner.py --input ids.txt --out-dir <dir> [--workers 4]
"""
from __future__ import annotations

import argparse
import csv
import json
import queue
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))

RESOLVED_STATUS = "Resolved"
CLOSURE_CODE = "Successful"
DONE_STATUSES = {"resolved", "closed"}
TICKET_URL = "https://t.corp.amazon.com/{}"
RESULT_FIELDS = ["Ticket ID", "Ticket URL", "Status before", "Result", "Status after",
                 "Detail", "Time"]
RESULT_RESOLVED = "Resolved"
RESULT_ALREADY = "Already resolved"
RESULT_FAILED = "Failed"
RESULT_NOT_PROCESSED = "Not processed (stopped)"
DEFAULT_WORKERS = 4
RETRY_DELAYS = (2, 5, 10)  # seconds between attempts of one API call
GATEWAY_RETRY_DELAYS = (15, 30, 60, 90)  # same, after a gateway "403 Forbidden" block
# SIM-T accepts update-ticket but keeps returning the OLD status for a while (seen live
# 2026-11-16: 78 tickets read back as "Assigned" right after a successful resolve, all
# Resolved/Successful when checked later). So the run is two-phase: phase 1 resolves
# every ticket without waiting; phase 2 starts VERIFY_WAIT seconds later and reads every
# sent ticket; tickets not yet Resolved are re-read after each VERIFY_RETRY_DELAYS step.
VERIFY_WAIT = 30
VERIFY_RETRY_DELAYS = (30, 60)
RESULT_SENT_UNVERIFIED = "Resolve sent, not verified (stopped)"
MAX_TICKETS = 10000

# --- input parsing (pure; also used by the web controller) ----------------------------
_RE_TCORP = re.compile(r"t\.corp\.amazon\.com/(?:issues/)?([A-Za-z0-9-]+)", re.IGNORECASE)
_RE_SHORT_ID = re.compile(r"^[A-Za-z]\d{5,14}$")
_RE_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
                      re.IGNORECASE)
_RE_SPLIT = re.compile(r"[\s,;|]+")


def _normalize_id(raw: str) -> str | None:
    raw = raw.strip().strip("\"'()[]<>")
    if _RE_SHORT_ID.match(raw):
        return raw.upper()
    if _RE_UUID.match(raw):
        return raw.lower()
    return None


def parse_ticket_input(text: str) -> dict:
    """Extract unique SIM-T ticket IDs from pasted text (TT URLs or bare IDs).

    Every whitespace/comma separated piece is checked. A piece that contains a
    t.corp.amazon.com link yields the ID from the link; a bare ShortID (D572189109)
    or ticket UUID is accepted as is. Anything else is reported as invalid and never
    resolved. Order of first appearance is kept; duplicates are counted and dropped.
    """
    ids: list[str] = []
    seen: set[str] = set()
    invalid: list[str] = []
    pieces = 0
    duplicates = 0
    for piece in _RE_SPLIT.split(text or ""):
        if not piece:
            continue
        pieces += 1
        match = _RE_TCORP.search(piece)
        ticket = _normalize_id(match.group(1)) if match else _normalize_id(piece)
        if ticket is None:
            invalid.append(piece[:200])
            continue
        if ticket in seen:
            duplicates += 1
            continue
        seen.add(ticket)
        ids.append(ticket)
    return {"ids": ids, "invalid": invalid, "pieces": pieces, "duplicates": duplicates}


# --- builder-mcp helpers ----------------------------------------------------------------
def open_mcp():
    import tickets_pull as tp
    mcp = tp.McpStdio(tp.BUILDER_MCP, ["--include-tools=TicketingReadActions,TicketingWriteActions"])
    mcp.request("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                               "clientInfo": {"name": "ooj-resolve-tickets", "version": "0.1"}},
                timeout=120)
    mcp.notify("notifications/initialized")
    return mcp


def _is_gateway_block(exc: Exception) -> bool:
    """The ticketing gateway answered with an HTML "403 Forbidden" page instead of the API."""
    text = str(exc)
    return "403 Forbidden" in text or "non-API HTML" in text or "edge/gateway filter" in text


def _with_retries(fn):
    attempt = 0
    while True:
        try:
            return fn()
        except Exception as exc:  # network / throttling / transient MCP errors
            # A gateway 403 is a short-lived block on request rate (it hit all 4 workers in
            # the same second on 2026-11-26 and outlasted the 17 s of normal retries), so
            # back off much longer before asking again.
            delays = GATEWAY_RETRY_DELAYS if _is_gateway_block(exc) else RETRY_DELAYS
            if attempt >= len(delays):
                raise
            time.sleep(delays[attempt])
            attempt += 1


def read_status(mcp, ticket_id: str) -> str:
    def call():
        data = mcp.call_tool("TicketingReadActions", {"action": "get-ticket", "input": {
            "ticketId": ticket_id, "threads": [], "includeEngagements": False,
            "includeMedic": False}}, timeout=120)
        if data.get("status") != "success":
            raise RuntimeError(f"get-ticket returned {str(data)[:200]}")
        status = ((data.get("data") or {}).get("ticket") or {}).get("status")
        if not status:
            raise RuntimeError("ticket has no status field")
        return str(status)
    return _with_retries(call)


def resolve(mcp, ticket_id: str) -> None:
    _with_retries(lambda: mcp.call_tool("TicketingWriteActions", {
        "action": "update-ticket", "ticketId": ticket_id,
        "status": RESOLVED_STATUS, "closureCode": CLOSURE_CODE}, timeout=120))


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _row(ticket_id: str, **fields) -> dict:
    row = {"Ticket ID": ticket_id, "Ticket URL": TICKET_URL.format(ticket_id),
           "Status before": "", "Result": RESULT_FAILED, "Status after": "", "Detail": "",
           "Time": _now()}
    row.update(fields)
    return row


def resolve_step(mcp, ticket_id: str) -> dict:
    """Phase 1 for one ticket. Never raises. Returns either a FINAL report row
    (Already resolved / Failed) or {"sent": True, ...} when the resolve was accepted and
    still has to be verified in phase 2."""
    before = ""
    try:
        before = read_status(mcp, ticket_id)
        if before.lower() in DONE_STATUSES:
            return _row(ticket_id, **{"Status before": before, "Result": RESULT_ALREADY,
                                      "Status after": before, "Detail": "No change made"})
        resolve(mcp, ticket_id)
        return {"sent": True, "Ticket ID": ticket_id, "Status before": before, "Time": _now()}
    except Exception as exc:
        return _row(ticket_id, **{"Status before": before,
                                  "Detail": f"{type(exc).__name__}: {str(exc)[:300]}"})


def verify_step(mcp, ticket_id: str) -> str | None:
    """Phase 2 read for one ticket: its current status, or None if it could not be read."""
    try:
        return read_status(mcp, ticket_id)
    except Exception:
        return None


# --- run ---------------------------------------------------------------------------------
def _emit(line: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {line}", flush=True)


def _write_json_atomic(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


SENT_FIELDS = ["Ticket ID", "Status before", "Time"]


def _parallel(items: list[str], workers: int, mcp_factory, fn) -> list[str]:
    """Run ``fn(mcp, item)`` over ``items`` with up to ``workers`` threads, each with its own
    builder-mcp. Returns the items that were not handled: no worker could take them (no
    builder-mcp available) or ``fn`` raised for them. A raising item never stops its
    worker, so one bad ticket cannot silently drop the rest of the queue."""
    work: queue.Queue = queue.Queue()
    for item in items:
        work.put(item)
    crashed: list[str] = []
    crashed_lock = threading.Lock()

    def worker(index: int) -> None:
        try:
            mcp = mcp_factory()
        except Exception as exc:
            _emit(f"worker {index}: could not start builder-mcp: {exc}")
            return
        try:
            while True:
                try:
                    item = work.get_nowait()
                except queue.Empty:
                    return
                try:
                    fn(mcp, item)
                except Exception as exc:  # keep the worker alive; report the item
                    _emit(f"worker {index}: internal error on {item}: "
                          f"{type(exc).__name__}: {str(exc)[:300]}")
                    with crashed_lock:
                        crashed.append(item)
        finally:
            try:
                mcp.close()
            except Exception:
                pass

    threads = [threading.Thread(target=worker, args=(i + 1,), daemon=True)
               for i in range(max(1, min(workers, len(items) or 1)))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    left = list(crashed)
    while True:
        try:
            left.append(work.get_nowait())
        except queue.Empty:
            return left


def run(ids: list[str], out_dir: Path, workers: int, mcp_factory=open_mcp) -> dict:
    """Two-phase bulk resolve. Phase 1 sends Resolved + Successful to every open ticket
    (no waiting). Phase 2, VERIFY_WAIT seconds later, reads every sent ticket and re-reads
    the ones SIM-T has not caught up with yet. results.csv gets one FINAL row per ticket;
    sent.csv lists every ticket whose resolve was accepted (so a Stop during phase 2 is
    still reported correctly)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    results_csv = out_dir / "results.csv"
    sent_csv = out_dir / "sent.csv"
    progress_json = out_dir / "progress.json"
    lock = threading.Lock()
    counts = {RESULT_RESOLVED: 0, RESULT_ALREADY: 0, RESULT_FAILED: 0}
    state = {"done": 0, "checked": 0, "phase": "resolving", "to_verify": 0}
    sent: dict[str, dict] = {}
    total = len(ids)
    started = _now()

    with open(results_csv, "w", newline="", encoding="utf-8") as fh:
        csv.DictWriter(fh, fieldnames=RESULT_FIELDS).writeheader()
    with open(sent_csv, "w", newline="", encoding="utf-8") as fh:
        csv.DictWriter(fh, fieldnames=SENT_FIELDS).writeheader()

    def progress(finished: bool = False) -> None:
        _write_json_atomic(progress_json, {
            "total": total, "done": state["done"], "sent": len(sent),
            "checked": state["checked"], "to_verify": state["to_verify"],
            "phase": "finished" if finished else state["phase"],
            "counts": dict(counts), "started": started, "finished": finished})

    def record(row: dict) -> None:
        with lock:
            with open(results_csv, "a", newline="", encoding="utf-8") as fh:
                csv.DictWriter(fh, fieldnames=RESULT_FIELDS).writerow(row)
            counts[row["Result"]] = counts.get(row["Result"], 0) + 1
            state["done"] += 1
            progress()
        if row["Result"] == RESULT_FAILED:
            _emit(f"{row['Ticket ID']}: FAILED - {row['Detail']}")

    def phase1(mcp, ticket: str) -> None:
        out = resolve_step(mcp, ticket)
        if out.get("sent"):
            with lock:
                sent[ticket] = out
                with open(sent_csv, "a", newline="", encoding="utf-8") as fh:
                    csv.DictWriter(fh, fieldnames=SENT_FIELDS, extrasaction="ignore").writerow(out)
                state["checked"] += 1
                progress()
        else:
            with lock:
                state["checked"] += 1
            record(out)
        if state["checked"] % 50 == 0 or state["checked"] == total:
            _emit(f"phase 1: {state['checked']} / {total} checked, {len(sent)} resolve sent")

    # ---- phase 1: resolve everything (no waiting) -------------------------------------
    progress()
    _emit(f"phase 1: resolving {total} unique ticket(s) with {workers} worker(s): "
          f"status {RESOLVED_STATUS}, closure code {CLOSURE_CODE}")
    for ticket in _parallel(ids, workers, mcp_factory, phase1):
        record(_row(ticket, Detail="Not processed: builder-mcp is not available (check Midway / "
                                   "mwinit) or an internal error (see run.log). Check it in "
                                   "SIM-T or run the list again."))
    phase1_end = time.time()

    # ---- phase 2: verify every sent ticket --------------------------------------------
    pending = list(sent)
    state.update(phase="verifying", to_verify=len(pending))
    progress()
    if pending:
        wait = max(0.0, VERIFY_WAIT - (time.time() - phase1_end))
        _emit(f"phase 2: waiting {round(wait)} s for SIM-T to update, then verifying "
              f"{len(pending)} ticket(s)")
        time.sleep(wait)
    last_seen: dict[str, str | None] = {}
    delays = (0,) + tuple(VERIFY_RETRY_DELAYS)
    for round_index, delay in enumerate(delays):
        if not pending:
            break
        if delay:
            _emit(f"phase 2: {len(pending)} ticket(s) not Resolved yet; re-checking in {delay} s")
            time.sleep(delay)
        still: list[str] = []

        def phase2(mcp, ticket: str) -> None:
            status = verify_step(mcp, ticket)
            last_seen[ticket] = status
            if status is not None and status.lower() == RESOLVED_STATUS.lower():
                record(_row(ticket, **{"Status before": sent[ticket]["Status before"],
                                       "Result": RESULT_RESOLVED, "Status after": status,
                                       "Detail": f"Closure code {CLOSURE_CODE} (verified)"}))
            else:
                with lock:
                    still.append(ticket)

        still.extend(_parallel(pending, workers, mcp_factory, phase2))
        pending = still
    waited = VERIFY_WAIT + sum(VERIFY_RETRY_DELAYS)
    for ticket in pending:
        seen = last_seen.get(ticket)
        shown = repr(seen) if seen is not None else "an unreadable status"
        record(_row(ticket, **{"Status before": sent[ticket]["Status before"],
                               "Status after": seen or "",
                               "Detail": f"Resolve was accepted, but after about {waited} s SIM-T "
                                         f"still shows {shown}. Check it in SIM-T or run the list "
                                         "again."}))
    progress(finished=True)
    _emit(f"=== finished: resolved {counts[RESULT_RESOLVED]}, already resolved "
          f"{counts[RESULT_ALREADY]}, failed {counts[RESULT_FAILED]} (of {total})")
    return {"total": total, "counts": counts}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="text file: one ticket ID per line")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    args = parser.parse_args(argv)
    ids = [line.strip() for line in Path(args.input).read_text(encoding="utf-8").splitlines()
           if line.strip()]
    if len(ids) > MAX_TICKETS:
        _emit(f"refusing: {len(ids)} tickets is more than the {MAX_TICKETS} limit")
        return 2
    run(ids, Path(args.out_dir), args.workers)
    return 0


if __name__ == "__main__":
    sys.exit(main())
