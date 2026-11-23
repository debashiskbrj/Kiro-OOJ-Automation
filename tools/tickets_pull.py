r"""Tech_Data producer: pull all open SWA OOJ tickets from SIM-T and explode to one TID per row.

Local port of the user's AgentSpace "SWA OOJ Ticket Analyzer" (same CTI filter, same
11-column schema, same extraction rules). Data path = builder-mcp.exe spawned as a stdio
MCP subprocess -> TicketingReadActions / search-tickets. Read-only.

Output (tools\captures\tickets\):
  Tech_Data_<YYYYMMDD_HHMM>.xlsx  (11 cols: Sl, case_id, createdAt, lastUpdatedAt,
                                   station_code, zone, impacted_tracking_ids, attachments,
                                   status, IssueUrl, ShortId)
  Tech_Data_<YYYYMMDD_HHMM>.csv   (same rows, for the pipeline)
  tickets_pull.log

Usage:  python tickets_pull.py [--max-pages N] [--keep-old]
Requires: a valid Midway session (mwinit -o) for builder-mcp.
"""
import argparse
import csv
import json
import queue
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import openpyxl
from openpyxl.utils import get_column_letter

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ooj_paths import BUILDER_MCP  # noqa: E402  (per-user %LOCALAPPDATA%\Toolbox path)

OUT = Path(__file__).parent / "captures" / "tickets"
OUT.mkdir(parents=True, exist_ok=True)
LOG = OUT / "tickets_pull.log"

# --- CTI filter (authoritative, matches the user's saved t.corp query) ---------------
ASSIGNED_GROUPS = ["SWA India", "SWA-CO-NOC", "in-swa-co"]
STATUSES = ["Assigned", "Researching", "Work In Progress", "Pending"]
QUERY = ('extensions.tt.category:"SWA" AND extensions.tt.type:"Out of Jurisdiction" '
         'AND extensions.tt.item:"Pin code not serviceable by station"')
RESPONSE_FIELDS = ["id", "aliases", "title", "status", "createDate", "lastUpdatedDate",
                   "extensions.tt.status", "extensions.tt.assignedGroup", "description", "attachments"]
PAGE_SIZE = 100

# --- extraction rules (exact, from the AgentSpace context.md) -----------------------
RE_TID = re.compile(r"DOC\d+|\b\d{12}\b")
RE_ZONE = re.compile(r"Zone:\s*(\w+)")
RE_STATION = re.compile(r"Station\s*code\s*:\s*([A-Za-z0-9]+)", re.IGNORECASE)
RE_ALIAS = re.compile(r"^[A-Z]\d+$")
# --- approval detection (accuracy-improved, 2026-11) ------------------------------------
# A real approval is written in many ways ("approved", "approval", "go ahead", "please
# proceed", "ok to mark undel", "site clear", ...), so the detector matches a broad grant
# vocabulary at SENTENCE level and then REJECTS the sentence if a negating / pending word
# sits near the match ("not approved", "awaiting approval", "refer the TT for approval",
# "need approval", "can't approve"). Any person's comment counts (including the operator's
# own). Labels stay "Site clear" / "Approved" so downstream consumers are unchanged.
RE_APPROVAL = re.compile(
    r"site\s*clear(?:ance|ed)?"                        # site clear / clearance / cleared
    r"|\bapprov(?:e|ed|al|als|ing)\b"                 # approve / approved / approval(s) / approving
                                                      # (leading \b: "disapproved"/"unapproved" are NOT grants)
    r"|\bagreed?\b"                                   # agree / agreed
    r"|\bgo\s*ahead\b"                                # go ahead
    r"|\bproceed\b"                                   # proceed
    r"|\bcarry\s+on\b"                                # carry on
    r"|\bno\s+objection\b"                            # no objection
    r"|\bfine\s+to\b"                                 # fine to (mark / proceed / go)
    r"|\bok(?:ay)?\s+to\s+mark\b"                     # ok to mark / okay to mark
    r"|\bmark(?:ing)?\s+(?:as\s+)?(?:undel\w*|valid)\b"  # mark undel / marking undelivered / mark valid
    r"|\bconfirmed\b"                                 # confirmed
    r"|\bpermitted\b|\ballowed\b"                     # permitted / allowed
    r"|\bgreen\s*light\b"                             # green light
    r"|\byou\s+may\b",                                # you may (proceed / mark ...)
    re.IGNORECASE,
)
# Words that, when they appear shortly BEFORE a grant word in the same sentence, turn it
# into a refusal, a pending state or a request — none of which is an approval.
_RE_NEGATION_BEFORE = re.compile(
    r"\b(?:not|no|never|cannot|can't|cant|don't|dont|doesn't|doesnt|didn't|didnt|won't|wont|"
    r"isn't|isnt|without|awaiting|pending|waiting|wait|need(?:s|ed)?|require[sd]?|"
    r"request(?:ed|ing)?|seeking|seek|refer(?:red|ring)?\s+(?:\w+\s+){0,4}for|"
    r"ask(?:ed|ing)?|unless|until|if|whether|\?)\b",
    re.IGNORECASE,
)
# Words that, when they appear shortly AFTER a grant word in the same sentence, mean the
# grant has not happened ("approval pending", "Approval: awaited", "approval not given").
_RE_NEGATION_AFTER = re.compile(
    r"\b(?:pending|awaited|awaiting|required|needed|denied|rejected|declined|withdrawn|"
    r"revoked|not\s+(?:yet|given|granted|received|done|provided))\b",
    re.IGNORECASE,
)
# Operator rule: only approvals written by a CRM or Site Lead count. Matched against the
# comment author's Phonetool job_title. Verified titles: "CRM" (rccharpe), "CRM - EDSP"
# (vddeepak), "Channel Relationship Manager" (zakakash), "Site Lead" (pkaushal).
RE_APPROVER_TITLE = re.compile(
    r"\bCRM\b|\bchannel\s+relationship\s+manager\b|\bsite\s*lead\b|\bSL\b",
    re.IGNORECASE,
)
# Shown when the only approvals on a ticket come from people who are not a CRM / Site Lead
# (or whose Phonetool title could not be read), followed by their aliases in brackets.
MANUAL_CHECK_LABEL = "Need check manually"
UNREADABLE_LABEL = f"{MANUAL_CHECK_LABEL} (comments could not be read)"
_RE_SENTENCE_SPLIT = re.compile(r"(?<=[.!?\n])\s+|\n+")
_NEGATION_WINDOW = 40  # characters of the sentence immediately before the match to inspect
_NEGATION_AFTER_WINDOW = 25  # characters of the sentence immediately after the match to inspect

COLUMNS = ["Sl", "case_id", "createdAt", "lastUpdatedAt", "station_code", "zone",
           "impacted_tracking_ids", "attachments", "status", "IssueUrl", "ShortId",
           "approval_in_comments"]


def log(msg: str):
    line = f"[{datetime.now():%H:%M:%S}] {msg}"
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    print(line, flush=True)


# --- minimal stdio MCP client ----------------------------------------------------------
class McpStdio:
    def __init__(self, exe: str, args: list[str]):
        self.proc = subprocess.Popen([exe, *args], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1)
        self._id = 0
        self._stderr_lines = []
        self._stdout_queue = queue.Queue()
        threading.Thread(target=self._drain_stderr, daemon=True).start()
        threading.Thread(target=self._drain_stdout, daemon=True).start()

    def _drain_stderr(self):
        for line in self.proc.stderr:
            self._stderr_lines.append(line.rstrip())

    def _drain_stdout(self):
        for line in self.proc.stdout:
            self._stdout_queue.put(line)
        self._stdout_queue.put(None)

    def _send(self, obj: dict):
        self.proc.stdin.write(json.dumps(obj) + "\n")
        self.proc.stdin.flush()

    def request(self, method: str, params: dict | None = None, timeout: float = 300) -> dict:
        self._id += 1
        rid = self._id
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        deadline = time.time() + timeout
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                raise TimeoutError(f"{method} timed out after {timeout}s")
            try:
                line = self._stdout_queue.get(timeout=remaining)
            except queue.Empty as exc:
                raise TimeoutError(f"{method} timed out after {timeout}s") from exc
            if line is None:
                raise RuntimeError(f"builder-mcp closed stdout. stderr tail: {self._stderr_lines[-5:]}")
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("id") == rid:
                if "error" in msg:
                    raise RuntimeError(f"{method} error: {msg['error']}")
                return msg.get("result", {})

    def notify(self, method: str, params: dict | None = None):
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def call_tool(self, name: str, arguments: dict, timeout: float = 300) -> dict:
        res = self.request("tools/call", {"name": name, "arguments": arguments}, timeout=timeout)
        content = res.get("content") or []
        text = "".join(c.get("text", "") for c in content if c.get("type") == "text")
        if res.get("isError"):
            raise RuntimeError(f"tool {name} returned error: {text[:500]}")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            raise RuntimeError(f"tool {name} returned non-JSON: {text[:500]}")

    def close(self):
        try:
            if self.proc.stdin and not self.proc.stdin.closed:
                self.proc.stdin.close()
            self.proc.terminate()
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=5)
        except Exception:
            pass


def open_mcp() -> McpStdio:
    # ReadInternalWebsites is loaded only for the read-only Phonetool job-title lookup of
    # approval comment authors (see approver_title / scan_approvals).
    mcp = McpStdio(BUILDER_MCP, ["--include-tools=TicketingReadActions,ReadInternalWebsites"])
    init = mcp.request("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                      "clientInfo": {"name": "ooj-tickets-pull", "version": "0.1"}}, timeout=120)
    mcp.notify("notifications/initialized")
    log(f"builder-mcp ready: {init.get('serverInfo')}")
    return mcp


# --- fetch --------------------------------------------------------------------------------
def fetch_all(mcp: McpStdio, max_pages: int | None) -> tuple[list[dict], int, int]:
    snapshot = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    query = f"{QUERY} AND createDate:[* TO {snapshot}]"
    log(f"snapshot upper bound: {snapshot}")

    tickets: dict[str, dict] = {}
    token = None
    total = None
    page = 0
    while True:
        page += 1
        inp = {"assignedGroup": ASSIGNED_GROUPS, "status": STATUSES, "query": query,
               "responseFields": RESPONSE_FIELDS, "sort": "createDate desc", "rows": PAGE_SIZE}
        if token:
            inp["startToken"] = token
        data = mcp.call_tool("TicketingReadActions", {"action": "search-tickets", "input": inp})
        if data.get("status") != "success":
            raise RuntimeError(f"search-tickets failed: {json.dumps(data)[:500]}")
        d = data["data"]
        total = d.get("totalResults", total)
        batch = d.get("tickets") or []
        new_token = d.get("startToken")
        for t in batch:
            tickets[t["id"]] = t
        log(f"page {page}: got {len(batch)}, collected {len(tickets)} / totalResults {total}")
        if not batch or (total is not None and len(tickets) >= total):
            break
        if not new_token or new_token == token:
            log("startToken did not advance — stopping pagination")
            break
        token = new_token
        if max_pages and page >= max_pages:
            log(f"stopping at --max-pages {max_pages}")
            break
    ordered = sorted(tickets.values(), key=lambda t: t.get("createDate", ""), reverse=True)
    return ordered, total or 0, page


# --- parse / explode -----------------------------------------------------------------------
def parse_iso(s: str | None):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def short_id(ticket: dict) -> str:
    for a in ticket.get("aliases") or []:
        aid = a.get("id") or a.get("aliasId") or ""
        if RE_ALIAS.match(aid):
            return aid
    return ""


def attachments_summary(ticket: dict) -> str:
    att = ticket.get("attachments")
    items = att if isinstance(att, list) else (att.get("attachments") if isinstance(att, dict) else None)
    if not items:
        return "{'message': 'No attachments found.'}"
    counts: dict[str, int] = {}
    for a in items:
        name = (a.get("fileName") or a.get("name") or a.get("title") or "")
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else (a.get("contentType") or "unknown")
        counts[ext] = counts.get(ext, 0) + 1
    return str({"total_attachments": len(items), "counts_by_type": counts})


def _is_negated(sentence: str, match_start: int, match_end: int | None = None) -> bool:
    """True when a negating / pending / request word appears in the text window just
    before the grant match (same sentence), or a pending / refusal word appears just
    after it. Turns "not approved", "awaiting approval", "refer the TT for approval",
    "need approval", "can't approve", "approved?", "approval pending" into
    non-approvals."""
    window = sentence[max(0, match_start - _NEGATION_WINDOW):match_start]
    if _RE_NEGATION_BEFORE.search(window):
        return True
    if match_end is not None:
        after = sentence[match_end:match_end + _NEGATION_AFTER_WINDOW]
        if _RE_NEGATION_AFTER.search(after):
            return True
    # A trailing question mark on the sentence means it is a question, not a grant.
    return sentence.rstrip().endswith("?")


def comment_labels(text: str) -> list[str]:
    """Approval labels ("Site clear" / "Approved") found in one comment's text."""
    labels: list[str] = []
    for sentence in _RE_SENTENCE_SPLIT.split(text or ""):
        if not sentence or not sentence.strip():
            continue
        for m in RE_APPROVAL.finditer(sentence):
            if _is_negated(sentence, m.start(), m.end()):
                continue
            label = "Site clear" if m.group(0).strip().lower().startswith("site") else "Approved"
            if label not in labels:
                labels.append(label)
    return labels


def comment_author(comment: dict) -> str:
    """Midway alias of a comment's author ("" when absent)."""
    author = comment.get("author") or comment.get("submitter") or {}
    return str(author.get("value") or "").strip() if isinstance(author, dict) else ""


def is_approver_title(job_title: str | None) -> bool:
    return bool(job_title) and bool(RE_APPROVER_TITLE.search(job_title))


def approver_title(mcp: "McpStdio", alias: str) -> str | None:
    """Read-only Phonetool job_title lookup via builder-mcp ReadInternalWebsites.
    Returns None on any failure (never guesses)."""
    if not re.fullmatch(r"[a-z0-9_-]{2,32}", alias or ""):
        return None
    try:
        data = mcp.call_tool("ReadInternalWebsites",
                             {"inputs": [f"https://phonetool.amazon.com/users/{alias}"]},
                             timeout=120)
        content = (data.get("content") or {})
        if content.get("status") != "success":
            return None
        title = (content.get("content") or {}).get("job_title")
        return str(title) if title else None
    except Exception:
        return None


def scan_approval(comments, is_approver=None) -> str:
    """Scan a ticket's CORRESPONDENCE comments for a genuine approval / site-clear.

    ``is_approver(alias) -> bool``: when given, a comment counts only if its author passes
    (operator rule: CRM / Site Lead only). It is called only for comments that already
    contain an approval, so Phonetool is looked up as little as possible. When omitted,
    every author counts.

    For each comment the text is read from the first present field among "message",
    "body", "text" (do NOT invent other fields). The comment is split into sentences and
    each sentence is checked against the broad RE_APPROVAL grant vocabulary; a match is
    accepted only if no negating / pending / request word precedes it, and no pending /
    refusal word follows it, in that sentence (see _is_negated), so refusals, pending
    states, requests and questions are NOT counted.

    A match starting with "site" maps to "Site clear", every other grant maps to
    "Approved". Distinct labels are kept in first-seen order and joined with ", ";
    returns "" when there is no genuine approval.
    """
    hits: list[str] = []
    manual: list[str] = []  # authors of approvals who are not (confirmed) CRM / Site Lead
    for c in comments or []:
        text = c.get("message") or c.get("body") or c.get("text") or ""
        if not text:
            continue
        labels = comment_labels(text)
        if not labels:
            continue
        if is_approver is not None:
            alias = comment_author(c)
            if not is_approver(alias):
                name = alias or "unknown"
                if name not in manual:
                    manual.append(name)
                continue
        for label in labels:
            if label not in hits:
                hits.append(label)
    if hits:
        return ", ".join(hits)
    # Operator rule: an approval by anyone else is not ignored — flag it for a manual check.
    return f"{MANUAL_CHECK_LABEL} ({', '.join(manual)})" if manual else ""


def scan_approvals(mcp: McpStdio, tickets: list[dict], log) -> dict:
    """Per-ticket approval scan via one get-ticket CORRESPONDENCE call each.

    NOTE: this adds ONE get-ticket round-trip per ticket (~1000 extra MCP round-trips on
    a full run), so the SIM-T step is inherently slower than before. Reuses the SAME live
    McpStdio instance passed in. On ANY failure for a ticket the value is UNREADABLE_LABEL
    ("Need check manually (comments could not be read)") — never guess or raise.
    """
    approvals: dict[str, str] = {}
    titles: dict[str, str | None] = {}  # alias -> Phonetool job_title (one lookup per person)

    def is_approver(alias: str) -> bool:
        if alias not in titles:
            titles[alias] = approver_title(mcp, alias)
        return is_approver_title(titles[alias])

    n = len(tickets)
    unreadable: list[str] = []
    for i, t in enumerate(tickets, 1):
        tid = t["id"]
        try:
            data = mcp.call_tool("TicketingReadActions", {"action": "get-ticket",
                "input": {"ticketId": short_id(t) or tid, "threads": ["CORRESPONDENCE"],
                          "maxCorrespondence": 100}})
            approvals[tid] = scan_approval(data.get("data", {}).get("comments", []), is_approver)
        except Exception:
            # Fix (audit 1.15): never guess, but never stay silent either. A blank looked
            # the same as "no approval mentioned", so flag the ticket for a manual check.
            approvals[tid] = UNREADABLE_LABEL
            unreadable.append(short_id(t) or tid)
        if i % 100 == 0 or i == n:
            log(f"approval scan: {i} / {n}")
    if unreadable:
        log(f"approval scan: {len(unreadable)} ticket(s) could not be read, marked "
            f"'{UNREADABLE_LABEL}': {unreadable[:20]}")
    failed = sorted(a for a, title in titles.items() if title is None)
    approvers = sorted(a for a, title in titles.items() if is_approver_title(title))
    log(f"approval authors: {len(titles)} looked up on Phonetool, {len(approvers)} CRM/Site Lead"
        + (f", {len(failed)} lookup(s) failed (marked '{MANUAL_CHECK_LABEL}'): {failed[:20]}"
           if failed else ""))
    return approvals


def explode(tickets: list[dict], approvals: dict | None = None) -> list[dict]:
    rows = []
    sl = 0
    for t in tickets:
        desc = t.get("description") or ""
        tids = list(dict.fromkeys(RE_TID.findall(desc))) or ["TID not found"]
        m_station = RE_STATION.search(desc)
        m_zone = RE_ZONE.search(desc)
        sid = short_id(t)
        base = {
            "case_id": t.get("id"),
            "createdAt": parse_iso(t.get("createDate")),
            "lastUpdatedAt": parse_iso(t.get("lastUpdatedDate")),
            "station_code": m_station.group(1).upper() if m_station else "",
            "zone": m_zone.group(1) if m_zone else "",
            "attachments": attachments_summary(t),
            "status": (t.get("extensions") or {}).get("tt", {}).get("status") or t.get("status") or "",
            "IssueUrl": f"https://t.corp.amazon.com/issues/{sid}" if sid else "",
            "ShortId": sid,
            "approval_in_comments": (approvals or {}).get(t.get("id"), ""),
        }
        for tid in tids:
            rows.append({"Sl": sl, **base, "impacted_tracking_ids": tid})
            sl += 1
    return rows


# --- write -------------------------------------------------------------------------------------
def write_outputs(rows: list[dict], stamp: str) -> tuple[Path, Path]:
    xlsx = OUT / f"Tech_Data_{stamp}.xlsx"
    csvp = OUT / f"Tech_Data_{stamp}.csv"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Tech_Data"
    ws.append(COLUMNS)
    for r in rows:
        ws.append([r.get(c) for c in COLUMNS])
    for col in ("C", "D"):
        for cell in ws[col][1:]:
            cell.number_format = "yyyy-mm-dd hh:mm:ss"
    for i, width in enumerate([6, 38, 20, 20, 12, 8, 20, 40, 18, 44, 14, 22], start=1):
        ws.column_dimensions[get_column_letter(i)].width = width
    wb.save(xlsx)
    with open(csvp, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({**r, "createdAt": r["createdAt"].isoformat(sep=" ") if r["createdAt"] else "",
                        "lastUpdatedAt": r["lastUpdatedAt"].isoformat(sep=" ") if r["lastUpdatedAt"] else ""})
    return xlsx, csvp


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-pages", type=int, default=None, help="debug: stop after N pages")
    ap.add_argument("--keep-old", action="store_true", help="do not delete previous Tech_Data_* files")
    args = ap.parse_args()

    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    log(f"=== tickets_pull {stamp}")
    if not args.keep_old:
        for old in OUT.glob("Tech_Data_*.*"):
            old.unlink()
        log("deleted older Tech_Data_* files (delete-old-first rule)")

    mcp = open_mcp()
    try:
        tickets, total, pages = fetch_all(mcp, args.max_pages)
        approvals = scan_approvals(mcp, tickets, log)
    finally:
        mcp.close()

    rows = explode(tickets, approvals)
    expected = sum(max(1, len(set(RE_TID.findall(t.get("description") or "")))) for t in tickets)
    n_no_tid = sum(1 for r in rows if r["impacted_tracking_ids"] == "TID not found")
    n_no_station = sum(1 for t in tickets if not RE_STATION.search(t.get("description") or ""))
    xlsx, csvp = write_outputs(rows, stamp)

    complete = (len(tickets) == total) or bool(args.max_pages)
    log(f"WROTE: {xlsx.name} data_rows={len(rows)} tickets={len(tickets)} total_at_fetch={total} "
        f"pages={pages} complete={complete}")
    log(f"checks: rows==expected_explosion {len(rows) == expected} ({len(rows)} vs {expected}); "
        f"tickets_without_TID={n_no_tid}; tickets_without_station_line={n_no_station}")
    if not complete:
        log("WARNING: collected != totalResults — do NOT treat this file as complete")
    print(json.dumps({"xlsx": str(xlsx), "csv": str(csvp), "rows": len(rows), "tickets": len(tickets),
                      "total_at_fetch": total, "complete": complete}))


if __name__ == "__main__":
    main()
