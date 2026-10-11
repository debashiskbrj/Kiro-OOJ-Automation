# OOJ Automation — Prioritized Audit Findings

## Summary

The OOJ pipeline is well-engineered, defensively coded, and genuinely verification-driven: nearly every lane cross-checks its own output (identity/extra/duplicate checks in SCC/PNAMA/DSR/ADS, a 25-column header + ordered-TID contract in `read_back`, a polygon workbook that re-verifies itself, and a row-count sanity check in `finalize_remarks`). All 59 unit tests pass (`py -3.13 -m pytest tests -q` → `59 passed in 3.28s`). I found **no data-fabricating or silently-dropping defect in the core enrichment math**, and the remark precedence in `remark_rules.py` matches `Docs/remark-rules.md` exactly. The highest-value finding is an **accuracy/consistency gap** (P1): the stage order runs `read_back` *before* `apply_remarks`, so the `OOJ_Validation_Data_*.csv/.xlsx` deliverables carry the pre-rule template-formula Remark while the published `.xlsx` carries the final remark — two "final" artifacts from one run disagree in column P. Beyond that the issues are smaller: a handful of doc/code drifts (notably `COLUMNS_TO_REMOVE` disabled vs. docs that still describe column removal, and a pipeline-order doc that lists EagleEye before SCC when the code runs SCC first), dead code, and several safe speed wins (redundant workbook loads, redundant file re-reads). Nothing here requires re-enabling column removal or touching the per-source auth/retry/sequential rules.

---

## P0 — Bugs / Correctness

No defect in this audit rises to "produces a wrong delivered workbook that passes verification." The items below are correctness-adjacent risks, ordered by severity. The most consequential accuracy issue is tracked as P1-1 because it concerns which artifact is authoritative rather than a wrong computation.

### P0-1. `preflight` hard-codes an operator-specific Edge profile path; a wrong/missing path aborts every run
- **file:line**: `tools/run_daily.py` `preflight()` ~L300–L320 (`"edge_profile": Path(r"C:\Users\kabideba\Desktop\My Files\My Automation\TT_Automation\edge_profile").exists()`), and the same literal in `tools/scc_client.py:PROFILE_DIR`, `tools/pnama_export.py:PROFILE_DIR`, `tools/siw_client.py:EDGE_PROFILE`.
- **evidence**: The absolute profile path is repeated verbatim in four modules; `preflight` raises `RuntimeError("preflight failed: edge_profile missing")` if it is absent.
- **impact**: Not a bug on the current machine, but any path change (new laptop, moved folder) silently breaks all browser lanes at four places that must be edited in lockstep. This is the single biggest maintainability landmine.
- **fix**: Centralize the profile path in one module-level constant (or env var `OOJ_EDGE_PROFILE`) imported by the four consumers. **Risk: SAFE** (pure refactor, same default value).

### P0-2. `_batch_size_for` swallows the readiness error and silently downshifts to the Edge batch size
- **file:line**: `tools/gcrs_mcscli.py:27-39` (`_batch_size_for`), specifically the `except Exception: return BATCH_EDGE`.
- **evidence**: `ensure_ready()` is called purely to settle transport selection; any exception it raises is caught and the function returns `BATCH_EDGE` (200). But `ensure_ready` raising means the transport is genuinely unavailable — the subsequent `_grbi` call will then fail anyway.
- **impact**: Low. The downshift is harmless because a broken session fails on the next call; but swallowing the exception hides the real cause from the log. A transient mcscli hiccup during readiness would also permanently pin the run to 200/batch for no reason.
- **fix**: Narrow the `except` or let `ensure_ready`'s error propagate (the caller already handles transport errors). At minimum log the swallowed exception. **Risk: SAFE**.

### P0-3. SCC "not found" popup markers can misclassify a legitimate export containing the substring "no data"
- **file:line**: `tools/scc_client.py` `_NOT_FOUND_MARKERS` and `_search_error_popup()` (~L120–L145), used by `_wait_for_download_or_notfound`.
- **evidence**: The large-search path scans the entire page body text case-insensitively for `"not found"`, `"no data"`, `"no record"`, etc., and raises to trigger a refresh/retry if found. A results table that renders a cell value or column header containing one of these phrases would be read as an error popup.
- **impact**: Low-to-moderate and self-limiting: a false positive only causes an extra refresh+retry (bounded by `SCC_CHUNK_ATTEMPTS`), not wrong data — the download/API path "always wins" once a file starts arriving (`if candidate is None:` guard). Worth noting because it is a text-heuristic over the whole DOM.
- **fix**: Scope the marker scan to the known popup/toast container rather than `body.inner_text()`, if a stable selector exists. **Risk: BEHAVIOR-CHANGING** (changes retry triggering) — leave as-is unless false retries are observed.

### P0-4. `prune_history` deletes capture artifacts by mtime with no lower bound, including `scc_enum_map.json`
- **file:line**: `tools/run_daily.py` `prune_history()` step 2 (~L95–L120): `for source_dir in CAPTURES.iterdir(): ... for entry in source_dir.rglob("*"): if entry.is_file() and _age_ok(entry): entry.unlink()`.
- **evidence**: Any file under `captures/<source>/` older than 7 days is deleted, including `captures/scc/scc_enum_map.json` (the accumulated, verified SCC State enum→text ground truth) and `captures/polygons/*.json` (station polygon cache, max-age 7 days anyway).
- **impact**: Low. The enum map is an *overlay* on top of the built-in `STATE_ENUM_TO_TEXT`, so losing it only reverts to the hard-coded map (still correct for all known states) and re-accumulates. But it is a quiet loss of accumulated verification data every 7 days.
- **fix**: Exclude known long-lived artifacts (`scc_enum_map.json`) from the prune, or move them out of `captures/`. **Risk: SAFE**.

---

## P1 — Accuracy (could be wrong without failing loudly)

### P1-1. (Most important) The downloadable `OOJ_Validation_Data` CSV/XLSX carry the PRE-rule Remark; only the working workbook gets the final Remark
- **file:line**: `tools/run_daily.py` `main()` end-sequence: `read_back` runs, then `finalize_remarks` (`run.stage("read_back", ...)` then `run.stage("apply_remarks", finalize_remarks, ...)`). `read_back` writes `OOJ_Validation_Data_<stamp>.csv` and `OOJ_Validation_Data_<stamp>.xlsx` from `final_rows["Remark"]`, which it reads from the recalculated template **P formula** (`working_rows = [dict(zip(headers, values)) ...]`). `finalize_remarks` → `apply_remarks.apply_to_file(xlsx, xlsx, ...)` rewrites column P **only in `OOJ_Working_<stamp>.xlsx`**, after the validation files are already written.
- **evidence**: In `run_daily.read_back`, the Remark column comes straight from the recalculated Working Sheet; the comment there even says "remarks (template P formula only)". `apply_remarks` modifies the `OOJ_Working_*.xlsx` in place and never regenerates the `OOJ_Validation_Data_*` files. The web controller publishes `OOJ_Working_*.xlsx` (`run_controller._working_workbook` → `publish_to_desktop`), so the *published* file is correct — but the `OOJ_Validation_Data_*.csv/.xlsx` left in the run folder (and offered implicitly as per-run artifacts) are not.
- **impact**: Two artifacts from the same run disagree in the Remark column. Anyone who opens `OOJ_Validation_Data_*.xlsx` (a plausibly "final"-looking name) sees the raw formula verdict (e.g. `#N/A`, template P text) instead of the operator's final remark (`Valid`, `Invalid - OOR`, protected values, etc.). No error is raised; the mismatch is silent.
- **fix (SAFE, recommended)**: Move `apply_remarks` to run **before** the `OOJ_Validation_Data_*` files are written — i.e. apply the P rules to the recalculated workbook, then have `read_back` read the post-rule P when building the final CSV/XLSX. Because `read_back` currently also enforces the "P2 still matches the template formula" contract and the Excel-error scan, keep that contract check on the raw recalc workbook and re-read P values after `apply_remarks` for the final output. Alternatively (smaller change) regenerate the `OOJ_Validation_Data_*` files from the post-rule workbook at the end of `finalize_remarks`. **Risk: BEHAVIOR-CHANGING** (it changes the contents of the validation files to match the published workbook — which is the intent — so confirm with the operator that the validation files *should* reflect final remarks).

### P1-2. `read_back` validation runs on pre-remark P, so the `#N/A`/Excel-error gate never sees the final remark values
- **file:line**: `tools/run_daily.py` `read_back` — `na_cols`, `bad_errors`, and `validation_status.reasons` are computed from the template-formula P; `apply_remarks` later turns many of those `#N/A`/formula results into literal remarks.
- **evidence**: `validation_status.reasons` includes `"spreadsheet contains #N/A values in N final columns"` based on the raw recalc; the apply-remarks step would resolve the Remark column's `#N/A`-style outcomes to protected/`Valid`/`Invalid-*` literals. The gate reports on a column state the final file no longer has.
- **impact**: The `review_required` / reasons flags can overstate problems in the Remark column (reporting `#N/A` that `apply_remarks` would have resolved), or understate them if the rules introduce an unexpected value. It does not corrupt data, but the surfaced "accuracy status" is about the wrong snapshot.
- **fix**: Tie this to the P1-1 reordering — compute `na_cols`/status after remarks are applied for the Remark column, or explicitly document that the gate measures the pre-remark workbook. **Risk: BEHAVIOR-CHANGING**.

### P1-3. GCRS "errored" (unresolved-after-guards) TIDs are treated as not-OOJ-decidable but ADS/polygon exclude them inconsistently
- **file:line**: `tools/gcrs_mcscli.py` `fetch()` return (`not_found` vs `errored`); `tools/tid_polygon_checker.py` `run_boundary_check` marks errored TIDs `Error — SIW unresolved after degraded/poison guards`; `tools/run_daily.py` `lane_ads` builds its request from `gcrs_rows` only (resolved), so errored/not-found TIDs silently get no ADS prediction.
- **evidence**: Polygon correctly produces an explicit `Error — ...` status row for unresolved TIDs (good — row-aligned, visible). ADS, however, requests only resolved GCRS rows; unresolved TIDs appear in `ads_missing.csv` only if they were in `requested_by_tid` (which is `gcrs_rows`), so a TID GCRS could not resolve is absent from both ADS success and ADS missing.
- **impact**: Low and arguably correct (no address ⇒ no ADS prediction is meaningful), but the asymmetry (polygon flags it, ADS omits it) means the final per-TID picture for an unresolved TID is only partially annotated. Not a loud failure.
- **fix**: Document the intended handling of GCRS-unresolved TIDs across ADS vs polygon; optionally add unresolved TIDs to `ads_missing.csv` with a reason. **Risk: SAFE** (reporting-only).

### P1-4. TID normalization is duplicated in ≥6 modules with subtly different rules
- **file:line**: `normalize_tid`/`_normalize_tid`/`_normalized_tid_text` in `tools/run_daily.py`, `tools/fill_template.py` (`_normalized_tid_text` uppercases DOC and strips `.0`), `tools/scc_client.py` (`_normalize_tid` strips `'` and `.0`, no DOC uppercasing), `tools/gcrs_mcscli.py` + `tools/polygon_check.py` (uppercase DOC, no `.0` strip), `tools/eagleeye_client.py` (`.0` strip + DOC uppercase), `tools/dsr_export.py`/`tools/pnama_export.py` (strip `.0` via `.split(".")[0]`).
- **evidence**: `scc_client._normalize_tid` does not uppercase a `doc...` TID, whereas `gcrs_mcscli.normalize_tid` does. Numeric 12-digit TIDs (the overwhelming majority) are handled identically everywhere, so the risk is confined to DOC-prefixed TIDs with inconsistent case.
- **impact**: Low in practice (ticket descriptions appear to produce `DOC`-uppercase already, and SCC may not return DOC rows), but a lowercase `doc...` TID could fail to join between SCC and GCRS. No test covers cross-module DOC-case consistency.
- **fix**: Extract one shared `normalize_tid` (e.g. in a small `tid_utils.py`) and import it everywhere; add a test asserting all modules agree on a `doc...` input. **Risk: BEHAVIOR-CHANGING** (SCC's DOC handling would start uppercasing) — stage carefully, but the consistency win is real.

### P1-5. `write_dsr_zone` dedups by TID keeping the FIRST row and drops rows with blank zone, which can mask a later non-blank zone
- **file:line**: `tools/fill_template.py` `write_dsr_zone()` (`if not tid or tid in seen or not zone: continue`).
- **evidence**: If the DSR export returns the same TID twice — once with a blank zone, once with a real zone — the writer keeps whichever appears first and skips the rest. A blank-zone first occurrence is skipped entirely (`not zone` continue), so a later real zone *would* be taken; but a real-zone-then-different-real-zone case keeps only the first. `dsr_export.validate` already rejects *conflicting* non-blank zones upstream, so the dangerous case is pre-filtered — this is defensive-but-redundant rather than a live bug.
- **impact**: Very low given the upstream conflict check. Noted for completeness because the dedup rule lives in two places with slightly different semantics (validate rejects conflicts; writer silently first-wins).
- **fix**: None required; optionally add a comment that `validate` guarantees no non-blank conflicts reach here. **Risk: SAFE**.

---

## P2 — Speed (safe opportunities only)

None of these touch the sequential browser lanes (SCC→PNAMA→DSR share one Edge profile), the strictly-sequential `/grbi`, the single polygon fetch, or ADS single-file upload. The already-safe parallelism (EagleEye API overlapping browser lanes; polygon `/gs`+`/gr` up to 8 workers) is correctly implemented and should be kept.

### P2-1. `read_back` loads the generated workbook twice and the template once; `apply_remarks` loads the generated workbook twice more
- **file:line**: `tools/run_daily.py` `read_back` (`openpyxl.load_workbook(xlsx, data_only=True)` + `openpyxl.load_workbook(xlsx, data_only=False)` + `openpyxl.load_workbook(template, ...)`); `tools/apply_remarks.py` `apply_to_file` (`load_workbook(src, data_only=True)` + `load_workbook(src, data_only=False)`).
- **evidence**: For a ~1,300-row workbook with ~33 columns, that's up to five full `openpyxl` loads of the same file(s) per run. openpyxl load dominates the Python-side time of the finalize phase.
- **impact**: Measurable (seconds) on large daily workbooks, but these loads are inherently needed because openpyxl can't both read cached values and write formulas from one handle. The redundancy is the *template* reload and the fact that `read_back` and `apply_remarks` each reload independently.
- **fix (SAFE)**: Cache the template's P2 formula once at module load instead of reloading the whole template in `read_back`. If P1-1 is implemented (apply remarks before writing validation files), the two phases can share the already-loaded workbooks, removing two of the five loads. **Risk: SAFE** (if sharing is done carefully) to **BEHAVIOR-CHANGING** (if reordered).

### P2-2. Several lanes write a CSV and immediately re-read it from disk
- **file:line**: `tools/run_daily.py` `lane_scc` (worker writes `scc.csv`, then `ft.read_csv(output)`), `lane_pnama` (`pnama.csv` → `ft.read_csv` + `pnama_export.validate(output,...)` re-opens the same file), `lane_dsr` (same pattern), `lane_ads` CSV round-trips.
- **evidence**: `lane_pnama` reads `output` via `ft.read_csv`, then `pnama_export.validate(output, tids)` opens the same path again; `lane_dsr` does the identical double-open.
- **impact**: Minor (disk re-read of a few MB). The browser-worker boundary legitimately forces one write (subprocess → file), but the subsequent double-open of the same CSV in the same function is avoidable.
- **fix (SAFE)**: Have `validate` accept already-parsed rows (or return rows) so the file is read once. **Risk: SAFE**.

### P2-3. QuickSight idle-wait uses a fixed 12-second settle after indicators clear
- **file:line**: `tools/pnama_export.py` `wait_visual_idle` (`if time.time() - idle_since >= 12: return`), reused by DSR.
- **evidence**: After loading indicators clear and before export, the code waits a flat 12s because the virtualized table reports zero DOM cells (documented as #Q9).
- **impact**: Up to ~12s per QuickSight lane (PNAMA + DSR) when the table *did* render cells quickly. The early-exit on `state["cells"] > 0` already covers the fast path, so the 12s only applies to the genuinely-virtualized zero-cell case — which #Q9 says is the normal case.
- **fix**: This is already event-driven where it safely can be; the 12s is the documented fallback for an unobservable state. **Leave as-is** — reducing it risks exporting a half-loaded table. **Risk: RISKY** (do not change).

### P2-4. `prune_history` walks every file under `captures/` on every run
- **file:line**: `tools/run_daily.py` `prune_history` step 2 (`for source_dir in CAPTURES.iterdir(): for entry in source_dir.rglob("*")`).
- **evidence**: `captures/polygons/` alone holds ~250 station JSONs; `rglob("*")` + `stat()` on each runs at the start of every pipeline run.
- **impact**: Small (hundreds of stats), negligible next to the browser lanes. Noted only as a micro-cost.
- **fix**: None warranted. **Risk: SAFE** (no change recommended).

---

## P3 — Structure / Maintainability

### P3-1. Dead constant `BATCH = 500` in `gcrs_mcscli.py`
- **file:line**: `tools/gcrs_mcscli.py:21` (`BATCH = 500`).
- **evidence**: Only `BATCH_MCSCLI` (L23) and `BATCH_EDGE` (L24) are referenced by `_batch_size_for`; `BATCH` is never read.
- **impact**: Confusing — a reader may think `BATCH` is the authoritative size.
- **fix**: Delete `BATCH` (keep `BATCH_MCSCLI`/`BATCH_EDGE`). **Risk: SAFE**.

### P3-2. Hard-coded absolute endpoints and paths scattered across modules
- **file:line**: `tools/ads_run.py:ADS_URL = "http://44.203.89.162:8192/"` (bare IP, plaintext HTTP); `tools/scc_client.py` SCC/API URLs; `tools/pnama_export.py`/`tools/dsr_export.py` dashboard UUID URLs; `tools/siw_client.py:BASE_URL`; the Edge/mcscli/builder-mcp paths in `run_daily.preflight`.
- **evidence**: Each endpoint/path is a module-level literal. The ADS endpoint is a raw IP over HTTP (already flagged in-code and gated behind `--allow-plaintext-ads`).
- **impact**: Maintainability; any endpoint rotation touches multiple files. The ADS plaintext-HTTP IP is a known, consciously-gated risk (not a new finding).
- **fix**: Consolidate endpoints/paths into a single `config.py` or env-driven settings module. **Risk: SAFE**.

### P3-3. Large multi-responsibility functions
- **file:line**: `tools/run_daily.py` `read_back` (~130 lines: header contract + TID contract + polygon contract + Excel-error scan + 3 output writers + summary), `main()` (~180 lines); `tools/scc_client.py` `_export_small` and `_goto_search`; `tools/pnama_export.py` `export_csv` (hover/menu/export heuristics).
- **evidence**: `read_back` both verifies and writes three output files and the summary; splitting verification from output generation would also make P1-1 easier to fix.
- **impact**: Harder to test individual concerns offline; verification and output are entangled (which is exactly why P1-1 is awkward to fix).
- **fix**: Extract `verify_working_sheet(...)`, `build_final_rows(...)`, and `write_outputs(...)` from `read_back`. **Risk: BEHAVIOR-CHANGING** (refactor; cover with tests first) — do after P1-1.

### P3-4. `remark_rules.is_station_code` has a redundant explicit `#N/A` guard
- **file:line**: `tools/remark_rules.py` `is_station_code` — the regex `^[A-Za-z0-9]{4}$` already rejects `#N/A` (contains `#`, and length 4 but `#` is non-alphanumeric), making the subsequent `if text in {"#N/A", "#N/A "}` branch unreachable.
- **evidence**: `#N/A` fails `_STATION_CODE.match` (the `#` and `/` are not in `[A-Za-z0-9]`), so the explicit guard never fires. The code comment acknowledges it is for "clarity."
- **impact**: None functional; minor dead-ish branch.
- **fix**: Keep for clarity or remove; either is fine. **Risk: SAFE**.

### P3-5. Duplicated QuickSight download-wait logic across modules
- **file:line**: `tools/scc_client.py:_wait_for_download`, `tools/pnama_export.py:wait_for_download`, `tools/dsr_export.py` (reuses PNAMA's), plus `pnama_export._wait_for_file`.
- **evidence**: Three near-identical "poll a download dir for a new, stable, non-partial file" loops with the same 2-second-stable heuristic.
- **impact**: Maintainability; a fix to the stability heuristic must be applied in multiple places.
- **fix**: Extract a shared `wait_for_stable_download(dir, before, timeout)` helper. **Risk: SAFE** (behavior-preserving if the heuristic is copied exactly).

---

## P4 — Docs (drift between `Docs/` and code)

### P4-1. `apply_remarks.COLUMNS_TO_REMOVE` is disabled, but `Docs/remark-rules.md` still describes removing 7 columns
- **file:line**: `tools/apply_remarks.py:COLUMNS_TO_REMOVE = []` (with a clear in-code explanation that removal is disabled on purpose) vs. `Docs/remark-rules.md` "Columns removed from the downloaded workbook" section, which states `apply_remarks` deletes `Tire 3 city, Station Code(RAW Data), Shipper, Baby shipper, Case1, Case2, OBLT Event` and that "33 columns → 26."
- **evidence**: The code's `_remove_unwanted_columns` iterates an empty list, so **no** columns are removed; the doc says seven are. This directly contradicts the steering rule that column removal is intentionally disabled.
- **impact**: A reader trusting `remark-rules.md` would expect a 26-column workbook; the actual output keeps all 33. High-confusion drift.
- **fix**: Update `Docs/remark-rules.md` to state column removal is disabled (grid/formula safety) and the workbook retains all columns. **Risk: SAFE** (doc only; do NOT re-enable removal).

### P4-2. `Docs/pipeline-reference.md` lists stage order as EagleEye → SCC; the code runs SCC first, then EagleEye concurrently
- **file:line**: `Docs/pipeline-reference.md` ("Order: preflight → tickets → **EagleEye → SCC** → PNAMA → DSR → GCRS → Polygon → ADS …") vs. `tools/run_daily.py` `main()` which runs `lane_scc` first on the main thread, then starts EagleEye on a background thread, and whose own comment says "SCC → EagleEye → PNAMA → DSR → GCRS → Polygon → ADS."
- **evidence**: The code comment and the actual call sequence put SCC first (because SCC's resolved TIDs feed GCRS/Polygon/ADS); `pipeline-reference.md` lists EagleEye before SCC and describes them as strictly sequential, omitting that EagleEye overlaps the browser lanes.
- **impact**: The reference doc misstates both order and the concurrency model.
- **fix**: Correct the order to `SCC → EagleEye (concurrent) → PNAMA → DSR → GCRS → Polygon → ADS` and note EagleEye runs on a background thread overlapping PNAMA/DSR. **Risk: SAFE** (doc only).

### P4-3. Steering/edge-cases imply PNAMA uses "500-TID chunks"; PNAMA and DSR actually paste the full TID list in one control
- **file:line**: Steering `ooj-instructions.md` SCC rule mentions 500-TID chunks (SCC-specific); `tools/pnama_export.py:set_tracking_filter` and `tools/dsr_export.py` paste `"\n".join(tids)` (all TIDs) into one textarea with no chunking.
- **evidence**: Only SCC chunks (`SCC_UI_CHUNK`/`SMALL_CHUNK`); PNAMA/DSR fill the entire list at once. The 500-chunk rule is SCC-only, so this is a *potential* misreading rather than a code bug — but the docs don't state PNAMA/DSR send everything in one filter.
- **impact**: Low; could mislead someone into thinking PNAMA chunks. Worth a one-line clarification in `pipeline-reference.md`.
- **fix**: Note explicitly that PNAMA/DSR apply the full TID list in a single QuickSight control (no chunking); chunking is SCC-only. **Risk: SAFE** (doc only).

### P4-4. `finalize_remarks` docstring says "four protected remarks"; there are six
- **file:line**: `tools/run_daily.py` `finalize_remarks` docstring ("preserving the four protected remarks") vs. `tools/remark_rules.py:PROTECTED_REMARKS` (six values) and `Docs/remark-rules.md` (correctly says six).
- **evidence**: `PROTECTED_REMARKS` has six entries (the four static plus the two State-driven outcomes); the test `test_new_state_outcomes_are_protected` asserts `len == 6`.
- **impact**: Minor stale docstring.
- **fix**: Change "four" to "six" in the `finalize_remarks` docstring. **Risk: SAFE**.

### P4-5. Minor: `_state_display` comment promises arrow-wrapping it does not do
- **file:line**: `tools/scc_client.py:_state_display` — the trailing comment says it will "wrap transit arrows," but the function returns `pretty` after only `.replace("_TO_", " -> ").replace("_", " ").title()` with no arrow re-wrapping.
- **evidence**: The derivation path is a best-effort fallback for *unseen* enums (all known enums use the verified map), so the imperfect comment has no data impact.
- **impact**: Negligible; cosmetic only, and only for an unseen enum.
- **fix**: Trim the comment to match the behavior. **Risk: SAFE**.

---

## P5 — Tests

### P5-1. Test suite result
- **Command**: `py -3.13 -m pytest tests -q` (from workspace root).
- **Result**: **`59 passed in 3.28s`** — all green. Suites: `test_remark_rules.py`, `test_polygon_workflow.py`, `test_web_controller.py`, `test_sanitize_captures.py`.

### P5-2. `remark_rules.py` branch coverage is strong; cross-module TID normalization is untested
- **file:line**: `tests/test_remark_rules.py` covers every documented branch (protected, State #N/A / In-Transit, AF verdicts, AD station code, AG inside, worksheet application) — this matches `Docs/remark-rules.md` precedence exactly.
- **gap**: There is no test asserting that `normalize_tid` behaves identically across `run_daily`, `fill_template`, `scc_client`, `gcrs_mcscli`, `polygon_check`, `eagleeye_client` (see P1-4), especially for `doc...`-case and `.0`-suffixed inputs.
- **fix**: Add an offline unit test (no live systems) that feeds a fixed set — `"372494929687"`, `"372494929687.0"`, `"DOC123"`, `"doc123"`, `"'372494929687"`, `""` — through each module's normalizer and asserts agreement (or documents the intended differences). **Risk: SAFE** (test-only).

### P5-3. `fill_template` and `apply_remarks` file-level behavior is only covered indirectly
- **file:line**: `tools/fill_template.py` (`extend_working_sheet`, `validate_polygon_rows`, `write_dsr_zone`) and `tools/apply_remarks.apply_to_file` have no direct unit tests; `remark_rules.apply_to_worksheet` is tested via a fake worksheet but `apply_remarks.apply_to_file`'s trailing-empty-row skip and row-count mismatch guard are not.
- **gap**: `validate_polygon_rows` (ordered TID + effective-station identity, the strongest accuracy guard in the fill stage) and `apply_to_file`'s "skip blank trailing rows so blanks aren't stamped Valid" logic are unit-testable offline with a tiny in-memory workbook.
- **fix**: Add offline tests building a small openpyxl workbook to exercise `validate_polygon_rows` (mismatch raises) and `apply_to_file` (explicit `last_row` vs. trailing-blank skip). **Risk: SAFE** (test-only).

---

## Top shortlist (do first)

1. **P1-1 — Make the `OOJ_Validation_Data_*` deliverables carry the final Remark** by applying remark rules before those files are written (or regenerating them after). This is the one place two run artifacts silently disagree. **BEHAVIOR-CHANGING** — confirm intent with operator.
2. **P4-1 — Fix `Docs/remark-rules.md`** to say column removal is disabled and all columns are retained (do NOT re-enable removal). **SAFE**.
3. **P4-2 — Fix `Docs/pipeline-reference.md`** stage order to SCC-first with EagleEye overlapping. **SAFE**.
4. **P0-1 — Centralize the Edge profile path** (one constant/env var) used by `run_daily`, `scc_client`, `pnama_export`, `siw_client`. **SAFE**.
5. **P1-4 / P5-2 — Extract one shared `normalize_tid`** and add a cross-module agreement test (watch SCC's DOC-case change). **BEHAVIOR-CHANGING** (small).
6. **P3-1 — Delete dead `BATCH = 500`** in `gcrs_mcscli.py`. **SAFE**.
7. **P2-2 — Stop double-opening the PNAMA/DSR CSVs** (`validate` takes parsed rows). **SAFE**.
8. **P4-4 — Correct "four" → "six" protected remarks** in the `finalize_remarks` docstring. **SAFE**.

---

### Notes on scope honored
- No files were modified; this report is the only artifact created.
- No recommendation re-enables `COLUMNS_TO_REMOVE`, parallelizes the shared-Edge browser lanes (SCC/PNAMA/DSR), parallelizes `/grbi`, adds polygon re-fetches, or changes ADS single-file upload — all intentional per steering and `Docs/edge-cases.md`.
- The already-safe parallelism (EagleEye API overlapping browser lanes per the code comment; polygon `/gs`+`/gr` up to 8 workers per #G9) is correct and should be preserved.
