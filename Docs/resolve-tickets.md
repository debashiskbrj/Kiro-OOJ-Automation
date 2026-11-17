# Resolve ticket (page section 5)

Bulk-resolves SIM-T tickets from a pasted list of TT URLs. Added 2026-11-16.

Last reviewed: 2026-11-16.

## What it changes on each ticket

- **Status → Resolved**, **closure code → Successful**. Nothing else: no comment, no
  root cause, no other field.
- Tickets already **Resolved** or **Closed** are skipped (no write).
- Runs under the operator's own Midway login via builder-mcp, so the operator's normal
  permissions apply (SWA India queue).

## Input rules (`resolve_tickets_runner.parse_ticket_input`)

- The box is split on spaces, new lines, commas, semicolons and `|`.
- `https://t.corp.amazon.com/<id>` and `https://t.corp.amazon.com/issues/<id>` links
  (with or without `/overview`) give `<id>`.
- Bare ShortIDs (`D572189109`, letter + 5–14 digits; upper-cased) and ticket UUIDs are
  accepted.
- Anything else (including `sim.amazon.com` / Taskei links) is **invalid**: listed on the
  page and in the report, **never resolved**.
- Duplicates are removed (first appearance kept). Limit: 10,000 unique tickets per run.

## Two-phase run (`resolve_step`, `verify_step`, `run`)

Phase 1 — resolve (no waiting), for every ticket:

1. `TicketingReadActions get-ticket` (metadata only) → current status.
2. Resolved/Closed → result **Already resolved**.
3. Otherwise `TicketingWriteActions update-ticket`
   `{ticketId, status: "Resolved", closureCode: "Successful"}`
   (write-tool parameters are flat, not inside `input`) → recorded in `sent.csv`.

Phase 2 — verify: `VERIFY_WAIT` (30 s) after phase 1 ends, every sent ticket is read
again, because SIM-T keeps returning the old status for a while after a successful
update (seen live 2026-11-16). Resolved → **Resolved**; not yet → re-read after each
`VERIFY_RETRY_DELAYS` step (30 s, 60 s); still not Resolved → **Failed** with "Resolve
was accepted, but … SIM-T still shows …". Running the same list again rechecks: tickets
that did resolve show as **Already resolved**.

Each API call is retried after 2, 5 and 10 seconds before the ticket is marked
**Failed** with the error text. 4 worker threads run in parallel, each with its own
builder-mcp process. If builder-mcp cannot start at all (Midway expired), every ticket
is marked Failed and nothing is changed. An internal error on one ticket is logged in
`run.log`, that ticket is reported Failed ("Not processed … see run.log"), and the
worker carries on with the rest (added 2026-11-16 after a crash dropped 4 of 79 rows).

## Outputs

`tools/resolve_runs/<stamp>/` (outside `tools/captures/`, so the Start clean-up never
deletes it; gitignored):

| File | Content |
|---|---|
| `ids.txt` | The unique ticket IDs sent to the runner |
| `input_check.json` | Entries pasted, duplicates, invalid entries |
| `results.csv` | One FINAL row per ticket, appended as soon as it is known |
| `sent.csv` | Every ticket whose resolve was accepted in phase 1 |
| `progress.json` | Total / done / sent / checked / to_verify / phase / counts / finished |
| `run.log` | Runner log (failures and every 50 tickets) |
| `Resolve_Report_<stamp>.xlsx` | Built on download: Summary, Results (colour-coded), Invalid input |

Results values: `Resolved`, `Already resolved`, `Failed`, and (only in the report after a
Stop) `Resolve sent, not verified (stopped)` or `Not processed (stopped)`.

## Safety

- **Check list** is local parsing only.
- **Resolve tickets** needs the checkbox, a confirm dialog, and the server requires
  `{"confirmed": true}`; a list is used once (a new run needs a new Check list).
- **Stop** kills the runner; finished tickets stay resolved, the rest are untouched.
- **Restart server** also stops a resolve run.
- Re-running the same list is safe: resolved tickets are skipped.

## Files and tests

- `tools/resolve_tickets_runner.py` — parsing, per-ticket logic, worker pool.
- `tools/web/resolve_controller.py` — check / start / stop / status / report.
- `tools/web/server.py` — `/resolve/*` routes.
- `tools/web/index.html` (`#resolve-card`, `rt-*` ids), `app.js` ("5. Resolve ticket"
  block), `style.css` (`.rt-*`).
- `tests/test_resolve_tickets.py` — fake ticket API only; no real ticket is touched.

## Before relying on it

Resolve **one** real ticket first and confirm in SIM-T that it shows Resolved /
Successful. Then use the full list.
