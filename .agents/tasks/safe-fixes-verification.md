# Safe fixes — verification note

All 9 behavior-preserving fixes applied. No runtime behavior changed (docs, comments,
docstrings, dead-code removal, and same-value refactors only).

## Commands run and results

- Baseline (before code changes 7-9): `py -3.13 -m pytest tests -q` -> **59 passed**.
- After all changes: `py -3.13 -m pytest tests -q` -> **59 passed in 3.23s**.
- Import smoke check (fix 7):
  `py -3.13 -c "import sys; sys.path.insert(0,'tools'); import run_daily, scc_client, pnama_export, siw_client"`
  -> printed `IMPORTS_OK` (all four modules, including the playwright-backed scc_client
  and pnama_export, imported cleanly).
- Edge-profile path equivalence check: `str(Path(default)) == <literal>` -> **True**, so
  the str consumers (scc_client, pnama_export) keep the byte-for-byte identical path when
  `OOJ_EDGE_PROFILE` is unset.

## Files changed

1. `Docs/remark-rules.md` — rewrote the column-removal section to state removal is disabled
   on purpose (grid/formula safety) and all 33 columns are retained. (`COLUMNS_TO_REMOVE`
   left empty in code — not re-enabled.)
2. `Docs/pipeline-reference.md` — corrected stage order to
   SCC -> EagleEye (concurrent, background thread) -> PNAMA -> DSR -> GCRS -> Polygon -> ADS,
   and noted EagleEye overlaps the serial browser lanes. Verified against `run_daily.main()`.
3. `Docs/pipeline-reference.md` — added that PNAMA and DSR paste the entire TID list into a
   single QuickSight control (no chunking); chunking is SCC-only (`SCC_UI_CHUNK`). Confirmed
   against `pnama_export.set_tracking_filter` and `dsr_export` (`"\n".join(tids)`, no loop).
4. `tools/run_daily.py` — `finalize_remarks` docstring: "four protected remarks" -> "six"
   (PROTECTED_REMARKS has 6 entries). Docstring text only.
5. `tools/scc_client.py` — `_state_display`: removed the stale trailing comment that claimed
   it would "wrap transit arrows"; the function only does the `.replace`/`.title()` already
   on the line above. Comment only.
6. `tools/gcrs_mcscli.py` — removed the unused module-level constant `BATCH = 500`. Repo-wide
   grep for bare `\bBATCH\b` confirmed only `BATCH_MCSCLI`/`BATCH_EDGE` are referenced.
7. `tools/ooj_paths.py` (new) — single source of truth
   `EDGE_PROFILE = Path(os.environ.get("OOJ_EDGE_PROFILE", r"C:\Users\kabideba\Desktop\My Files\My Automation\TT_Automation\edge_profile"))`.
   Wired into the four named consumers:
   - `tools/run_daily.py` preflight: `EDGE_PROFILE.exists()` (Path, same as before).
   - `tools/scc_client.py`: `PROFILE_DIR = str(EDGE_PROFILE)` (str, same as before).
   - `tools/pnama_export.py`: `PROFILE_DIR = str(EDGE_PROFILE)` (str, same as before).
   - `tools/siw_client.py`: `from ooj_paths import EDGE_PROFILE` (Path, same as before).
   (The occurrence in `tools/edge_preflight.py` was not in the four named consumers and was
   left untouched to stay within scope.)
8. `tools/run_daily.py` — `prune_history()`: skip `scc_enum_map.json` by filename in the
   per-source deletion loop so the long-lived SCC enum overlay is never deleted by age. The
   7-day policy is unchanged for everything else.
9. `tools/run_daily.py` + `tools/pnama_export.py` + `tools/dsr_export.py` — read each exported
   CSV once in `lane_pnama`/`lane_dsr` and pass the already-parsed rows (plus fieldnames) to
   `validate`, which now accepts optional `rows`/`columns`. The file-path path still works for
   any other caller/test. The single read uses the identical parse
   (`csv.DictReader`, `encoding="utf-8-sig"`) as both `fill_template.read_csv` and validate's
   own reader, and fieldnames are passed through so the empty-rows-with-header case validates
   identically. Validation results and all counts are unchanged — purely one fewer disk read.

## Behavior confirmation

- Same default Edge profile path (byte-for-byte when `OOJ_EDGE_PROFILE` unset).
- Same validation results (PNAMA/DSR), same counts, same raise conditions.
- Same prune policy except the one protected file (`scc_enum_map.json`).
- Same control flow, timing, retry, and auth logic.
- `apply_remarks.COLUMNS_TO_REMOVE` still empty (not re-enabled).
