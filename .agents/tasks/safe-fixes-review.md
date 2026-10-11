# Nine behavior-preserving fixes to the OOJ automation pipeline

The change set applies nine fixes to a mature, working, read-only OOJ automation: three doc corrections, two docstring/comment corrections, one dead-code removal, one new shared-path module wired into four consumers, one prune-policy carve-out, and one same-result CSV single-read refactor. The stated contract is that none of these alter runtime behavior. I verified each fix against the current file state (the workspace is not a git repo, so there is no `main` to diff against — review is against the live files plus the coder's recorded evidence).

Watch for: nothing blocking. The one item worth noting is non-blocking — `Docs/pipeline-reference.md` states the SCC chunk is "5,000 per search" (`SCC_UI_CHUNK`), which disagrees with the operator steering that says 500; this is a pre-existing doc value, not introduced or touched by these nine fixes, so it is out of scope here (confirmed). All nine fixes are behavior-preserving, no column removal was re-enabled, the deleted constant is referenced nowhere, and the Edge-profile default is byte-for-byte identical to the old literal.

**Verdict**: APPROVED

## High-level view

The three documentation fixes (remark-rules column-removal status, pipeline stage order, PNAMA/DSR single-control paste) are text-only and now match the code they describe: `apply_remarks.COLUMNS_TO_REMOVE` is still an empty list, `run_daily.main()` runs SCC → EagleEye (background) → PNAMA → DSR → GCRS → Polygon → ADS, and PNAMA/DSR paste the whole TID list into one QuickSight control with no chunking loop.

The two in-code text fixes (the "four" → "six" protected-remarks docstring in `finalize_remarks`, and the `_state_display` comment trim) touch only a docstring and a comment; the executable lines are unchanged.

The dead-code removal deletes `BATCH = 500` from `gcrs_mcscli.py`. A repo-wide search for the bare token `BATCH` returns nothing — only `BATCH_MCSCLI`, `BATCH_EDGE`, and the unrelated `LARGE_BATCH_MAX` identifier survive, and the live batch-size logic uses the two `BATCH_*` constants. The deletion is safe.

The new `ooj_paths.py` centralizes the Edge profile path with the same env-var-or-literal default the lanes used before. Its four named consumers import it and use it with the same type as before (Path where `.exists()`/Path default is needed, `str()` where a `user_data_dir` string was needed), so the resolved path is identical when `OOJ_EDGE_PROFILE` is unset.

The prune carve-out adds a single filename skip for `scc_enum_map.json` inside the per-source artifact loop; the 7-day cutoff and every other deletion path are untouched.

The CSV refactor has the lanes parse each PNAMA/DSR export once and pass the parsed rows plus fieldnames into `validate`, which gained optional `rows`/`columns` parameters while keeping its original file-reading path for every other caller. The parse used by the lanes is identical to validate's own, so results and counts are unchanged.

<details>
<summary>Issues (1)</summary>

1. **SCC chunk value in pipeline-reference doc** (non-blocking, out of scope) — `Docs/pipeline-reference.md` says SCC pastes "5,000 per search" while operator steering says 500. Pre-existing, not part of these nine fixes; flag for a future doc pass, not a reason to block this change set.

</details>

<details>
<summary>Details</summary>

### Fix 1 — remark-rules column removal documented as disabled

`Docs/remark-rules.md` now carries a "Column removal is disabled (all columns retained)" section stating `apply_remarks.COLUMNS_TO_REMOVE` is an empty list, no Working Sheet columns are deleted, all 33 columns are kept, and that removal was turned off because deleting columns shifted the grid and broke right-side formulas. This matches the operator's standing instruction (`COLUMNS_TO_REMOVE = []`, column removal DISABLED). The decision sequence section also correctly lists six protected remark values. This is a doc-only change; no code re-enabled removal (confirmed — the file is Markdown, and the coder's note records `apply_remarks.COLUMNS_TO_REMOVE` still empty).

### Fix 2 — pipeline stage order corrected

`Docs/pipeline-reference.md` states the order as preflight → tickets → SCC → EagleEye (concurrent, background thread) → PNAMA → DSR → GCRS → Polygon → ADS → fill/recalc → read_back → apply_remarks, and explains that SCC runs first on the main thread, EagleEye runs on a background thread (pure API, no Edge), and the browser lanes stay serial on one Edge profile. This matches the described `run_daily.main()` sequencing. Doc-only (confirmed).

### Fix 3 — PNAMA/DSR single-control paste documented

The PNAMA and DSR sections now state the entire TID list is pasted into one QuickSight control in a single shot with no chunking, and that chunking is SCC-only (`SCC_UI_CHUNK`). Consistent with the code: the DSR/PNAMA validators and exporters work on the whole TID list, and no QuickSight paste loop exists. Doc-only (confirmed).

### Fix 4 — finalize_remarks docstring "four" → "six"

`run_daily.finalize_remarks` docstring reads "preserving the six protected remarks" (line 571). The executable body is unchanged. Docstring text only (confirmed by reading the function).

### Fix 5 — _state_display comment corrected

`scc_client._state_display` (lines 395-406) carries only an accurate docstring and inline comments describing the actual behavior: known enums use the verified map, unknown enums fall back to a `.replace("_TO_", " -> ").replace("_", " ").title()` derivation. There is no stale "wrap transit arrows" claim. The executable lines (`.replace`/`.title()`) are unchanged. Comment/docstring only (confirmed).

### Fix 6 — unused BATCH constant removed

`gcrs_mcscli.py` no longer defines a bare `BATCH = 500`. A repo-wide case-sensitive search for `\bBATCH\b` across `tools/**` and `tests/**` returns no match for the bare token; only `BATCH_MCSCLI = 500` and `BATCH_EDGE = 200` exist, and the live `batch_size` logic selects between those two by transport. `scc_client.py` has an unrelated `LARGE_BATCH_MAX` identifier (not the deleted token). The deletion is safe — nothing references the removed constant (confirmed by grep).

### Fix 7 — ooj_paths.py shared Edge-profile path

`tools/ooj_paths.py` defines
`EDGE_PROFILE = Path(os.environ.get("OOJ_EDGE_PROFILE", r"C:\Users\kabideba\Desktop\My Files\My Automation\TT_Automation\edge_profile"))`.
The default literal is byte-for-byte the old path, and when `OOJ_EDGE_PROFILE` is unset the behavior is unchanged. The four named consumers wire it with the same type as before:

- `run_daily.py` — `from ooj_paths import EDGE_PROFILE`, preflight uses `EDGE_PROFILE.exists()` (Path, as before).
- `scc_client.py` — `PROFILE_DIR = str(EDGE_PROFILE)` (str for `user_data_dir`, as before).
- `pnama_export.py` — `PROFILE_DIR = str(EDGE_PROFILE)` (str, as before).
- `siw_client.py` — imports `EDGE_PROFILE` and uses it as the `profile_dir: Path = EDGE_PROFILE` default, re-wrapped with `Path(...)` in `__init__` (Path, as before).

`edge_preflight.py` keeps its own `PROFILE_DIR = Path(r"...edge_profile")` literal. It was not one of the four named consumers, so leaving it untouched is in scope and does not change behavior (the literal there equals the default). The import smoke check in the recorded evidence imported all four modules cleanly, and the `str(Path(default)) == <literal>` equivalence check returned True. Type and usage identical to before (confirmed).

### Fix 8 — prune_history excludes scc_enum_map.json

`run_daily.prune_history()` adds a single `if entry.name == "scc_enum_map.json": continue` inside the per-source artifact loop (section 2), with a comment explaining it is a long-lived SCC enum overlay kept regardless of age. The `cutoff`/`_age_ok` logic, the run-folder deletion (section 1), and the 7-day `retention_days` default are all unchanged. Only that one file is newly protected; everything else still ages out at 7 days (confirmed by reading the function).

### Fix 9 — single CSV parse passed to validate

`lane_pnama`/`lane_dsr` in `run_daily.py` open the exported CSV once with `open(output, encoding="utf-8-sig", newline="")` + `csv.DictReader`, capture `columns = reader.fieldnames or []` and `rows = list(reader)`, and pass both into `validate(output, tids, rows=rows, columns=columns)`.

`pnama_export.validate` and `dsr_export.validate` now take optional `rows`/`columns`:

```python
if rows is None:
    with open(path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        columns = reader.fieldnames or []
        rows = list(reader)
else:
    columns = columns or []
```

The `rows is None` branch is byte-for-byte the original reader, so `fetch()`, the CLI, and tests (which call `validate(path, tids)` with no rows) behave exactly as before. The lane-supplied path uses the identical parse the validator would have performed itself, and fieldnames are passed through, so the empty-rows-with-header case still validates identically (the required-column check and column derivation read from the passed `columns`, which carries the header even when `rows` is empty). Result dict keys, counts (`rows`, `requested`, `exported_unique`, `missing`, `extra`, duplicate/zone fields), and raise conditions are unchanged — the only difference is one fewer disk read. `import csv` is present at the top of `run_daily.py`, so the direct `csv.DictReader` calls resolve. Correctly implemented, not skipped (confirmed by reading all three files).

### Test and import evidence

The coder's recorded evidence (`.agents/tasks/safe-fixes-verification.md`): `py -3.13 -m pytest tests -q` → 59 passed both before and after the code changes; the four-module import smoke check printed `IMPORTS_OK`; and the Edge-profile path equivalence check returned True. Per the task instruction I did not re-run the full suite. I ran narrow spot-checks only: the bare-`BATCH` grep (no match), the `EDGE_PROFILE`/`PROFILE_DIR` consumer grep (all four wired as described), and a grep confirming `import csv` is present in `run_daily.py`.

</details>

<details>
<summary>File map</summary>

- `Docs/remark-rules.md` — added "column removal is disabled / all 33 columns retained" section; six protected remarks listed.
- `Docs/pipeline-reference.md` — corrected stage order (EagleEye concurrent background thread) and noted PNAMA/DSR single-control paste (no chunking; chunking is SCC-only).
- `tools/run_daily.py` — `finalize_remarks` docstring "four"→"six"; `prune_history` skips `scc_enum_map.json`; `lane_pnama`/`lane_dsr` parse CSV once and pass rows/columns to validate; imports `EDGE_PROFILE` from `ooj_paths`.
- `tools/scc_client.py` — `_state_display` comment trimmed to match actual `.replace`/`.title()` behavior; `PROFILE_DIR = str(EDGE_PROFILE)`.
- `tools/gcrs_mcscli.py` — removed unused `BATCH = 500` (BATCH_MCSCLI/BATCH_EDGE retained).
- `tools/ooj_paths.py` (new) — `EDGE_PROFILE` single source of truth; default byte-for-byte equal to old literal, env-overridable via `OOJ_EDGE_PROFILE`.
- `tools/pnama_export.py` — `PROFILE_DIR = str(EDGE_PROFILE)`; `validate` gains optional `rows`/`columns`.
- `tools/dsr_export.py` — `validate` gains optional `rows`/`columns`.
- `tools/siw_client.py` — `profile_dir` default now `EDGE_PROFILE` from `ooj_paths`.

No git diff available (workspace is not a git repo); review conducted against live file state plus the coder's recorded test/import evidence.

</details>
