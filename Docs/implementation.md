# implementation.md — Current State & Next Work

Status date: 2026-09-18. `context.md` defines project scope;
`polygon-checker.md` is authoritative for the local SIW polygon workflow.

## 1. Current implementation

| Capability | Implementation | Evidence / status |
|---|---|---|
| SIM-T pull and Tech_Data explosion | `tools\tickets_pull.py` | live one-row query 1/154 on 2026-09-18; parser offline-tested |
| EagleEye RTO lookup | `tools\test_eagleeye_mcp.py` | live package returned `REVERSE` on 2026-09-18 |
| SCC package data | `tools\scc_client.py` | live 1,495/1,496 via 999-row bulk auto-download + 496-row small CSV recovery; 0 extras/duplicates |
| SIW address/GPS | `tools\siw_client.py` + `gcrs_mcscli.py` | current snapshot resolved 1,495/1,496 through Edge fallback |
| Local polygon checking | `tools\tid_polygon_checker.py` + `polygon_check.py` | current snapshot 1,560/1,560 verified: 1,006 Inside, 334 Outside, 220 explicit errors |
| PNAMA missort | `tools\pnama_export.py` | current snapshot 1,440 unique matches, 56 no-match, 0 extras |
| DSR Zone | `tools\dsr_export.py` | current snapshot 1,452 mappings; 44 missing and 1 blank preserved blank; 0 extras/conflicts |
| ADS prediction | `tools\ads_run.py` | approved current run 1,464/1,495; plaintext HTTP remains explicitly gated |
| Template fill/recalculation | `tools\fill_template.py` + `recalc_excel.ps1` | exact 25-column/template contract and Excel COM verified locally |
| Daily orchestration | `tools\run_daily.py` | all sources wired; local row-aligned polygons replace AgentSpace handoff |
| Offline report rebuild | `tools\rebuild_report.py` | consumes row-aligned `polygon.csv`; rejects incomplete legacy runs |

## 2. Local polygon implementation

The supplied AgentSpace instructions were ported into Windows code rather than treated
as a continuing dependency:

- `siw_client.py` restricts requests to the fixed SIW host and read-only endpoint
  allowlist. It probes mcscli through real `POST /gpsn`; failures switch once to the
  authenticated persistent Edge profile.
- Edge Playwright objects live on one owner thread. Parallel polygon workers queue
  browser requests instead of touching a page from multiple threads.
- `gcrs_mcscli.py` contains the only GRBI batching/guard implementation. Calls are
  sequential with a transport-aware batch size (500 over mcscli, 200 over Edge) plus
  empty/degraded, large-invalid, and poison-batch
  protections.
- `polygon_check.py` selects active/newest default station records, retrieves individual
  route geometries, never unions them, uses `(longitude, latitude)`, and caches one JSON
  per carrier/station.
- `tid_polygon_checker.py` preserves every non-empty input line, duplicates, order, and
  same-TID/different-station identity; writes a standalone Desktop XLSX; reopens it and
  validates every ordered row and summary count before printing `VERIFY OK`.
- `run_daily.py` builds polygon inputs from every ordered Tech_Data row and writes
  Working Sheet AG directly by row. It no longer accepts or creates an AgentSpace
  handoff file.

Full rules and commands: `Docs\polygon-checker.md`.

## 3. Validation completed

- Python 3.13.2; PowerShell 5.1; Excel COM 16.0.
- All 26 pre-existing Python scripts parsed before this change; new modules also import.
- Required Python packages are installed: openpyxl 3.1.5, Playwright 1.63.0,
  requests 2.32.3, pandas 3.0.5, XlsxWriter 3.2.9.
- Template has 16 sheets, all required source sheets, exact 25-column final contract,
  and the authoritative P2 remark formula.
- `tests\test_polygon_workflow.py`: 18/18 passing after focused review fixes and
  exact Edge-profile ownership tests. Coverage
  includes duplicate/order preservation, same TID at two stations, ATS/ATA routing,
  no-union gap behavior, exactly-once fallback, sequential/deduplicated lookup,
  verified XLSX, direct AG row alignment, mismatch rejection, and absence of an
  AgentSpace runtime handoff.
- One prior polygon accumulator defect and one Windows read-only workbook handle leak
  were reproduced and fixed.

## 4. Current blockers / safety restrictions

1. **mcscli SIW authorization currently fails.** The real `/gpsn` probe returns
   HTTP 302 with redirects rejected (the earlier followed response ended at 401) even
   while `mcscli is-valid` reports valid. This is not a runtime blocker because the
   authenticated Edge fallback is repeatedly live-verified 1/1, but mcscli remains
   conditional and monitored.
2. **DSR selectors need a current live recheck.** Code exists; docs no longer call it
   unimplemented.
3. **ADS uses plaintext HTTP.** Do not send production addresses unless that transfer is
   explicitly approved; a secure authenticated endpoint is preferred.
4. **Static reference freshness is unknown.** Pin-Station and Station & Sub Station
   still come from template sheets.
5. **Final production outputs are incomplete.** NOC-list and grouped comment-line files
   are not yet generated in this workspace; ticket write actions remain intentionally
   absent and approval-gated.
6. **Station-source business decision remains open.** Daily polygons currently use SCC
   `Source`, falling back to the ticket-description station.

## 5. Remaining next work

1. Add a known live DOC TID and a portal-confirmed boundary-edge fixture when available.
2. Recheck SCC, PNAMA, and DSR serially because all use the same persistent Edge profile.
3. Confirm static-reference ownership and refresh cadence.
4. Implement final NOC and grouped comment-line outputs after source reconciliation.
5. Never post comments or resolve tickets without explicit approval for that run.
