# changes.md — LIVE DOC (newest first)

One dated line per meaningful change: decision, access unlocked, edge case found,
code added/changed. Details belong in the other docs; this is the timeline.

---

- **2026-10-05** — DEEP DIVE (existing pipeline, read-only-behavior pass): reviewed
  `tools/*.py` against `ooj-audit-findings.md`/`rollback-plan.md` for safe fixes only.
  Applied one fix: `tools/edge_preflight.py` now imports `EDGE_PROFILE` from
  `ooj_paths` instead of carrying its own duplicate hardcoded profile-path literal
  (audit P0-1 / shortlist #4), matching `run_daily.py`/`scc_client.py`/
  `pnama_export.py`/`siw_client.py`, which already did. Everything else reviewed
  (GCRS Edge-batch retry-on-any-`TransportError`, SCC chunk-retry/no-reload path,
  resource-leak and broad-`except Exception` scan, TID-normalization duplication
  P1-4, stale-docstring checks P4-4/P4-5) was confirmed already correct or
  intentionally left alone as behavior-changing — no other code changed. Full report:
  `.agents/tasks/py-deep-dive-findings.md`. Suite stayed at 77 passed, 0 failed
  throughout.

- **2026-10-05** — FEATURE (post-comments): added a gated, dry-run-first "5. Post
  comments (optional)" lane that posts live SIM ticket comments via Playwright/Edge.
  Strictly additive: new `tools/post_comments_runner.py` (parse + dry-run preview +
  one-pass confirmed post + retry-failed; comment format `TID: {tid} | {remark}` per url
  group, adapted from the operator's reference script with its blocking `input()` retry
  loop dropped) and `tools/web/post_comments_controller.py` (RunController-shaped,
  single-subprocess-at-a-time, atomic `dry_run_summary.json`, incremental
  `post_comments_report.xlsx`). Six new routes on `server.py`
  (`/post-comments/upload|status|confirm|retry-failed|stop|report-download`), a new UI
  section + JS + CSS. SAFETY GATE (never loosened): real posting is unreachable unless a
  successful dry-run of the *current* uploaded file exists (summary newer than the
  upload), the UI confirm checkbox is ticked, no post-comments subprocess is running, and
  `run_controller.CONTROLLER.is_running()` is False (shared Edge-profile mutual exclusion).
  Output lives under `tools/captures/post_comments/` (already covered by
  `sanitize_captures.py`'s generic containment walk — no sanitizer change). Tests:
  `tests/test_post_comments.py` (+8; suite 69 -> 77 passed, 0 failed).

- **2026-10-04** — RESILIENCE (GCRS): two additive, read-only safeguards after the
  2026-10-04 `Failed to fetch` run blanked gcrs+polygon+ads.
  *Feature A — preflight auth gate:* `run_daily.preflight()` and
  `run_controller.preflight_state()` now classify the GCRS SIW transport once at
  preflight using a `FailoverSiwSession(mode='mcscli')` probe (so the single Edge
  automation profile is never opened). The result is surfaced additively as
  `summary['preflight']['auth_gate']` and a new `auth_gate` key in `preflight_state()`
  (sub-keys: `midway_session_valid`, `gcrs_transport` = `mcscli` | `edge-fallback` |
  `unknown`, `browser_signin` = `unknown`, `browser_signin_reason`). A WARNING is logged
  when the run would be forced onto the fragile Edge fallback. Warn-only — the run is not
  hard-blocked and no existing preflight key changed; the probe is fully try/excepted so
  it can never crash preflight.
  *Feature B — per-batch Edge retry:* an Edge `/grbi` batch that throws `Failed to fetch`
  (or a degraded/empty window) now retries up to 3x with 2s/4s/6s backoff, then degrades
  to `errored` for that batch's TIDs while the remaining batches continue (mcscli path
  unchanged). See #G8.

- **2026-09-30** — PROTECTION: added `tools/sanitize_captures.py`, run at server startup
  and on `/restart`. It removes only output-corrupting scratch — everything inside
  `_browser_downloads/`, partial/temp files (`.crdownload`/`.tmp`/`.partial` + atomic-write
  sidecars) under `tools/captures`, and stale `SWA_repeat_Prox_Pnam*`/`DSR_Raw_data*`/
  `SearchResults*` leftovers in `~/Downloads`. Strict allowlist: only acts inside
  `tools/captures/` (resolved-path containment) and known export names; never touches code,
  docs, template, tests, or a run's real output. Covered by `tests/test_sanitize_captures.py`
  (3 tests); suite now 59/59.

- **2026-09-30** — UI: added a **Restart server** button in the header. It POSTs
  `/restart`, which stops any in-progress run, spawns a detached relauncher that starts a
  fresh `server.py` on the same port, and shuts the old one down; the button shows a
  pulsing "Restarting…" state and the page auto-reconnects and reloads when the server is
  back. Verified end to end (server relaunched and `/preflight` returned ready).
- **2026-09-30** — UI: made all buttons responsive — hover/active/focus states, a
  "working" pulse for in-progress actions, and mobile breakpoints (full-width, tappable
  buttons and stacked layout on phones; tighter header/padding on tablets).

- **2026-09-30** — ORG: added `Docs/project-structure.md` (full file map) and linked it
  from the README. Tidied `tools/captures/`: moved 40 old one-off dev/debug scratch files
  (Sep 16–17 screenshots, capture JSON, dry-run logs, test outputs — none used at runtime)
  into `tools/captures/_archive/`. The 9 live per-source dirs are untouched; `_archive` is
  covered by the existing 7-day auto-prune. No code changed.

- **2026-09-29** — PIPELINE: GCRS/Polygon/ADS input is now the **SCC output** (genuine
  TIDs only). SCC → PNAMA → DSR run first (full ticket list); GCRS then runs on SCC's
  resolved TIDs, removing the poison/invalid batches a raw ticket list produced. EagleEye
  still overlaps on the full list. Row alignment unchanged (fill maps by TID).
- **2026-09-29** — SCC: raised search size to **5,000 TIDs** per Search with a
  count-branched result path — ≤1,000 uses the `Export to CSV` button; >1,000 uses SCC's
  automatic file download, which the automation detects and reads (#C9).
- **2026-09-29** — UI: Progress cards now render as **numbered, one-by-one steps** in
  execution order with a status-coloured left accent (SCC before GCRS).

- **2026-09-28** — ADS FIX: root-caused a hard lane failure (`UI=1992, XLSX=1993`) to a
  single address beginning with a `"` — the ADS CSV parser swallowed the next row
  boundary, counting one fewer TID. `ads_run.py` now sanitizes the address field
  (double-quotes/newlines/tabs → space, whitespace collapsed), de-duplicates input by
  `TrackingId` and drops blank TIDs, and tolerates a tiny UI-count shortfall
  (`max(3, rows/100)`) instead of blanking the whole ADS column. A large mismatch or
  `UI > file` still fails loudly.
- **2026-09-29** — GCRS FIX: made `/grbi` batch size transport-aware — 500 over mcscli,
  200 over the Edge fallback. Run 20260929 fell back to Edge (mcscli `/gpsn` 302) and the
  500-ID batches returned degraded all-invalid windows then died with `Failed to fetch`,
  blanking gcrs+polygon. 200 over Edge is the previously-proven size; size is re-checked
  per batch so a mid-run mcscli→Edge failover drops to 200 for the remaining IDs. (#G8)
- **2026-09-28** — GCRS: raised `/grbi` batch from 200 to 500 (the SIW-supported
  per-call maximum) for fewer round-trips on large datasets. Still strictly sequential
  (#G4); unique-TID selection (normalize → dedup → ATS/ATA routing) unchanged.
- **2026-09-28** — SCC RELIABILITY: separated "genuinely not-found" from
  "failed-not-queried". A chunk that returns zero rows is now re-verified once with a
  refresh before being accepted (a transient empty export no longer silently drops
  data), and a chunk that exhausts its refresh retries is recorded as failed, written to
  `scc_failed_tids_<stamp>.txt`, and called out with a WARNING + re-run hint rather than
  vanishing into generic "no-data".
- **2026-09-23** — DOCS: added top-level `README.md` and new guides
  `Docs/user-guide.md`, `Docs/pipeline-reference.md`, `Docs/developer-guide.md`,
  `Docs/troubleshooting.md` to make the project understandable end to end.
- **2026-09-23** — FIX: disabled Working Sheet column removal in `apply_remarks.py`
  (`COLUMNS_TO_REMOVE = []`). Deleting columns shifted the grid and broke formulas in
  columns to the right, causing errors in the downloaded file. All columns are now kept;
  a fresh run produces the full 33-column template. Already-trimmed files can't be
  repaired (removal was in-place).
- **2026-09-23** — ADS: upload the whole set in ONE file (200MB limit is file size, not
  TID count); removes the per-chunk browser relaunch that stalled with no window. Fails
  fast if the ADS host doesn't load. First column stays `TrackingId`.
- **2026-09-23** — SCC: abandoned the batch API (HTTP 400/403 for our request shape,
  even single-TID). Reverted to the proven browser flow: paste TIDs → Search → Export to
  CSV, chunked, with viewport-safe clicks. State text comes straight from the CSV.
- **2026-09-23** — WEB: added per-tool progress %, per-source CSV download links, a Stop
  run button, a Copy log button (`/log` endpoint), a "partial download if a source
  fails" checkbox, and 7-day auto-pruning of `tools/captures/`. Polygon reverted to
  realtime (no cache) for current location data.
- **2026-09-23** — REMARK: six protected values (added `Invalid- Incorrect TID / TID
  Not Found` from State=#N/A and `Invalid - OOR` from State="In Transit (DS -> Customer)");
  State (col J) rules run before the AF/AD/AG logic. See `remark-rules.md`.

- **2026-09-18** — CURRENT TEMPLATE DELIVERED: live snapshot 788 tickets → 1,560
  Working Sheet rows / 1,496 unique TIDs. Sources filled: EagleEye 1,507 rows, SCC
  1,495, PNAMA 1,447, GCRS 1,495, ADS 1,464, DSR 1,452, polygon 1,560. Excel
  recalculated formulas through P1561; every protected source/template hash remained
  unchanged; the Desktop copy hash matched and final review approved.
- **2026-09-18** — UI RULES: recorded EagleEye high-volume Bulk Search + Package Leg
  Detail path; SCC dual path (>1,000 checkbox + automatic download, missing recovery
  via Search Result → Export CSV); PNAMA loaded-page marker fallback; DSR no-data Zone
  remains blank. Edge clean/profile launches both passed through managed background
  verification; safe preflight never kills WebView2/user apps.

- **2026-09-18** — FINAL REVIEW/RETRY: code review and security review approved
  with no remaining critical/high findings. Offline suite now passes 18/18. A repeated
  final live run rejected mcscli `/gpsn` HTTP 302, authenticated Edge locally,
  resolved `/grbi` 1/1, refreshed BRSG polygons, produced 1 Inside / 0 Errors, and
  reopened the workbook with `Rows == Input: 1 == 1 (VERIFY OK)`.
- **2026-09-18** — LIVE LOCAL POLYGON VERIFIED: auto mode rejected mcscli after a
  real non-200 `/gpsn` response, opened authenticated Edge exactly once, resolved `/grbi`
  1/1, refreshed BRSG `/gs` + `/gr`, classified 1 Inside / 0 Outside / 0 Errors,
  and reopened the XLSX with `Rows == Input: 1 == 1 (VERIFY OK)`.

- **2026-09-18** — LOCAL POLYGON: AgentSpace runtime dependency removed. Added
  `siw_client.py`, shared guarded `gcrs_mcscli.py`, pair-preserving
  `tid_polygon_checker.py`, verified standalone XLSX output, and row-aligned daily
  template integration. `--agentspace-file` and `agentspace_input.txt` were removed.
- **2026-09-18** — AUTH: mcscli session/posture reported valid but live SIW `/grbi`
  returned HTTP 401. Root cause is SIW request authorization, not cookie expiry. Local
  code now requires a real `/gpsn` probe and switches exactly once to authenticated
  persistent Edge; it never falls back to AgentSpace.
- **2026-09-18** — ACCURACY: local polygon input now preserves all non-empty lines,
  order, duplicates, and same-TID/different-station pairs. Output is reopened and
  checked row-for-row before `VERIFY OK`; `/grbi` remains sequential with 200-ID
  batches and poison/degraded-window guards.
- **2026-09-18** — TEST: local polygon and daily-template integration suite initially
  passed 7/7. After focused reviews and Edge-preflight coverage, 18/18 tests cover partial degraded recovery,
  non-finite GPS, invalid cache recovery, incomplete workbook rejection, Edge-probe
  cleanup, same-TID station swaps, and the explicit plaintext-ADS gate. Fixed the
  polygon geometry accumulator crash and a Windows read-only workbook handle leak.
- **2026-09-18** — EAGLEEYE: live lookup returned `package_type=REVERSE`, verifying
  the template's RTO signal.

- **2026-09-17** — ACCESS: **PNAMA WORKING.** `tools\pnama_export.py` automates the
  QuickSight export (textarea filter in the `tracking_id` control → visual ⋮ → Export
  to CSV); validated 5/5 TIDs, 100 columns. Edge cases #Q6–#Q12 added (textarea
  control, toggle trap, persisted per-viewer list, `TBC` missort value).
- **2026-09-17** — BUILT: `tools\tickets_pull.py` (local Tech_Data producer via
  builder-mcp subprocess, AgentSpace-identical schema/rules). Handshake OK; first full
  run waits on a fresh `mwinit -o` (Midway session expired 11:38).
- **2026-09-17** — SPEC: `Docs\report-spec.md` created with the user's final column
  list; DSR Waterfall dropped (pending confirmation); #S2/#S3 corrected (station code
  from description only, never buildingId), #S7–#S10 added.
- **2026-09-17** — SCOPE: OBLT (EVENT_414, template col AA) removed from the
  pipeline (user). `probe_oblt.py` retired; no Mercury/LDAP access requests. #O1
  kept in edge-cases.md as reference only.
- **2026-09-17** — ACCESS: **ADS WORKING.** `tools\ads_run.py` drives the "ADS &
  Pincode Batch Inference Platform" end to end (upload XLSX → "ADS with PP Trigger"
  → "Download data as CSV"); verified on 3 live TIDs in ~15 s; output columns match
  the template's ADS Predicted Data sheet. Requires a clean Edge (edge_profile's AEA
  extension aborts raw-IP URLs, #A1). Edge cases #A1–#A8 added.
- **2026-09-17** — ADS Streamlit app (`http://44.203.89.162:8192/`) confirmed
  reachable from this laptop: HTTP 200, TornadoServer/6.4, no login redirect.
- **2026-09-16** — PNAMA QuickSight export route confirmed from the user's
  screenshot: apply the `tracking_id` filter, open the bottom-right `⋮` on the
  `SWA repeat_Prox_Pnama_match Raw data` visual, choose Export to CSV/Excel.
  Access moved BLOCKED → PARTIAL; Playwright automation + exported-TID validation
  remain pending (#Q1–#Q5).
- **2026-09-16** — architecture.md: mermaid diagrams added (pipeline flow with
  working/blocked coloring, auth-lanes map, remark-engine §4.1 + missort §4.2
  decision trees). ASCII flow replaced.
- **2026-09-16** — Doc set + template moved from workspace root into `Docs\`
  (user); context.md paths updated.
- **2026-09-16** — Doc set created: context.md, architecture.md, edge-cases.md,
  implementation.md, changes.md, access-status.md (workspace root).
- **2026-09-16** — SCOPE: template columns F (Tire 3 city), G (Station Code RAW
  Data), H (Images) dropped from requirements (user). Retires the col-F `#REF!` bug.
- **2026-09-16** — SCOPE: Tech_Data source of truth = SIM-T query (not Quip);
  pipeline order fixed as SIM → TIDs → per-TID enrichment (user).
- **2026-09-16** — ACCESS: `mcscli.exe` present on the Windows laptop and
  **`POST /grbi` to SIW works headless** (HTTP 200, full routeDetailsArray). GCRS
  no longer needs a browser; `gcrs_client.py` demoted to fallback lane.
- **2026-09-16** — ACCESS: GCRS browser lane proven end-to-end (`gcrs_client.py`):
  721 polygon sets listed, 3-TID `grbi` query, 23-column CSV. Polygon set for IN
  numeric TIDs = `ATS!DEFAULT`.
- **2026-09-16** — EDGE: AEA (Amazon Enterprise Access) extension must be enabled in
  the Playwright Edge profile (`ignore_default_args` fix); first navigation may
  `ERR_ABORTED` by design (#C5).
- **2026-09-16** — ACCESS: SCC search + Export-to-CSV automated in the browser lane;
  underlying API captured (`/os/batchGetPackageSummary`, nodeId IN_ROOT). Direct
  requests lane confirmed impossible (Sentry kerberos + corp TLS, #C2).
- **2026-09-16** — ACCESS: EagleEye MCP working from Windows via Midway cookie jar
  (hand-rolled JSON-RPC; mcp-registry CLI unsupported on Windows, #W4). Region for
  IN = EU. RTO/REVERSE field verification still open (#E2).
- **2026-09-16** — ACCESS: SIM-T production filter replicated via builder-mcp
  (649 open OOJ tickets); `aliases` quirk documented (#S1).
- **2026-09-16** — BLOCKED: OBLT OAuth loop under automation (#O1); probe script
  written, not yet run. Alternatives mapped (EagleEye events / Mercury ES).
- **2026-09-16** — CONTEXT: user shared both AgentSpace context.md docs (address
  fetch + polygon checker); their SIW guards imported into edge-cases.md (#G1–#G9).
- **2026-09-16** — Template (16 sheets, all formulas) and SOP fully decoded and
  documented; remark-engine precedence extracted (architecture.md §4).
