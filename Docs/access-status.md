# access-status.md — LIVE DOC

Tracks each source's current access lane and the next verification/action. Update this
file and `changes.md` whenever live evidence changes.

Legend: WORKING = proven by a current live call · BUILT = implemented/offline-tested,
live re-verification pending · CONDITIONAL = automatic fallback required · OUT OF SCOPE
= intentionally unused.

| # | Source | Local lane | Status | Next action | Owner |
|---|---|---|---|---|---|
| 1 | SIM-T tickets | builder-mcp TicketingReadActions | WORKING — one-row live query returned 1/154 on 2026-09-18 | full daily pagination when needed | Kiro |
| 2 | EagleEye | MCP + Midway cookie jar | WORKING — live TID returned `package_type=REVERSE` on 2026-09-18 | retain known RTO fixture for regression | Kiro |
| 3 | SCC | Playwright + persistent Edge profile | WORKING — bulk auto-download 999 + small CSV recovery 496 = 1,495/1,496, 0 extras/duplicates | retain dual-path logic | Kiro |
| 4 | GCRS / SIW address lookup | `siw_client.FailoverSiwSession`: real mcscli `/gpsn` probe → one-way Edge fallback | WORKING LOCALLY — final rerun rejected mcscli HTTP 302 and Edge `/grbi` resolved 1/1 on 2026-09-18 | monitor mcscli probe; Edge remains automatic fallback | Kiro |
| 5 | Local SIW polygon checker | shared SIW session + `/gs` + `/gr` + local geometry engine | WORKING — fresh BRSG polygons, 1/1 Inside, 0 errors, XLSX `VERIFY OK` on 2026-09-18 | expand live regression set when DOC/boundary fixtures are available | Kiro |
| 6 | ADS pincode prediction | clean Edge → Streamlit UI | WORKING — approved current run returned 1,464/1,495; 31 no-prediction | plaintext HTTP remains explicit per-run approval only | Kiro / model owner |
| 7 | PNAMA missort | QuickSight via persistent Edge | WORKING — current run 1,440 unique matches, 56 no-match, 0 extras | periodic UI selector re-verification | Kiro |
| 8 | DSR Waterfall Zone | `tools\dsr_export.py` via persistent Edge | WORKING — 1,452 mappings, 44 missing + 1 blank represented blank, 0 extras/conflicts | retain no-data audit | Kiro |
| 9 | OBLT | — | OUT OF SCOPE | none | — |
| 10 | Static references | hidden template sheets | AVAILABLE; freshness unknown | identify source and refresh cadence | kabideba |

## Local SIW authentication rule

Path existence and `mcscli is-valid` are not sufficient. On 2026-09-18 mcscli reported
a valid session while direct SIW access returned HTTP 401 with redirects followed and
HTTP 302 when redirects were rejected. Every run therefore performs a real, read-only
SIW `POST /gpsn` probe. It selects mcscli only after HTTP 200 + valid non-empty JSON. Otherwise it opens the authenticated local Edge profile exactly once and keeps
that transport for the run. It never falls back to AgentSpace.

See `polygon-checker.md` for all input, retry, polygon, cache, Excel, and verification
rules.

## Daily prerequisites

1. Fresh `mwinit -o` for SIM-T and EagleEye. This does not prove SIW mcscli access.
2. Persistent Edge automation profile is not open/locked. If the fallback displays a
   Midway page, authenticate in that window.
3. Microsoft Edge, Playwright, openpyxl, and the template are available.
4. No ticket comment/resolve or other source-system write is performed without explicit
   per-run approval.
5. ADS receives full addresses over plaintext HTTP and is blocked by default in
   `run_daily.py`. It requires the explicit per-run `--allow-plaintext-ads` flag after
   approval; otherwise run with `--skip ads --allow-partial`.
