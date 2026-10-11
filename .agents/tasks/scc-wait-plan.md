# SCC Wait Observability & Fast-Fail — Implementation Plan

## Context (verified by reading the code, not assumed)

- File in scope: `tools/scc_client.py` only (+ a new test file under `tests/`). No other lane,
  no `run_daily.py` / `run_controller.py`, no web files (a concurrent workflow owns those).
- Baseline: `py -3.13 -m pytest tests -q` currently reports **73 passed** (~10s). This must
  stay green; new offline tests add to that count.
- The reported hang is REAL (run 20261008, 2,392 TIDs, the `>1000` auto-download path). It is
  NOT already handled. Confirmed root causes:
  - `_browser_export_chunk` (line ~783) calls
    `_wait_for_download_or_notfound(..., timeout_s=45 * 60, log=log)` — a **45-minute** ceiling.
  - `_wait_for_download_or_notfound` (lines ~149-186) polls once per second for either a
    "not found" popup or a stable downloaded file, and logs **nothing** between the initial
    "watching for ..." line and either `return` or the final `RuntimeError(... no automatic
    download within {timeout_s}s)` — so a slow/dead SCC looks like a silent hang for up to 45m.
  - Neither wait loop checks whether the Edge page/context was closed or navigated to a login
    page, so a closed/crashed Edge or an SSO redirect also burns the full 45 minutes.
  - `_wait_for_download` (lines ~100-126) is the small-path/file-wait loop with the same
    silent-wait shape (1s poll, no heartbeat), used by both the `<=1000` Export-to-CSV
    fallback (`timeout_s=240`) and reachable with `before=` snapshots.
- Existing retry wrapper `_export_chunk_with_retry` (lines ~865-913) already catches any
  exception from the wait, refreshes the tab, and retries up to `SCC_CHUNK_ATTEMPTS = 3`.
  A fast-fail raised from the wait flows straight into this. **This must stay unchanged.**
- SECOND ISSUE from the same real run (checkbox / doomed-wait hole), confirmed by reading
  `_ensure_large_mode_checkbox` (lines ~699-748) and its call site in `_browser_export_chunk`:
  - `_ensure_large_mode_checkbox(frame, index, log) -> bool` escalates `check` -> `click` ->
    `js` (`checkbox.evaluate("el => el.click()")`), already calls `scroll_into_view_if_needed`
    first, already logs a SUCCESS line `ticked >1000 automatic-download mode via {attempt}`,
    and already **returns a bool** (True if the checkbox ends up checked, else False). In the
    real run `check` and `click` failed with "outside of the viewport" but `js` succeeded, so
    the run continued correctly.
  - THE HOLE: the call site is `if large: _ensure_large_mode_checkbox(frame, index, log)`
    (line ~775) and it **ignores the returned bool**. If all three methods ever fail, SCC never
    switches to automatic-download mode, yet the code proceeds into
    `_wait_for_download_or_notfound(...)` and waits for a download SCC will never produce — a
    second silent hang. The fix is at the call site: capture the bool and raise when False so
    it flows into `_export_chunk_with_retry` (refresh + retry) instead of a doomed wait.
  - The per-attempt `>1000 checkbox {attempt} failed (...)` lines read like hard failures even
    when a later attempt succeeds (cosmetic noise only).

## HARD CONSTRAINTS (do not violate)

- Do NOT change: TID pasting, the Search click, the `>1000` checkbox / auto-download vs
  Export-to-CSV branch, `SCC_UI_CHUNK` (5000), `AUTO_DOWNLOAD_THRESHOLD` (1000),
  `SCC_CHUNK_ATTEMPTS` (3), the file size-stability detection (newest file, `size > 0`,
  stable >= 2s, skip `.crdownload/.tmp/.partial`), CSV reading/validation, or the rows returned.
- SCC's data-pulling logic stays **byte-for-byte unchanged**. Only waiting, logging, the
  timeout ceiling, and checkbox-return-handling change. For the checkbox specifically: do NOT
  change the escalation order (`check` -> `click` -> `js`), the `force=True`/`timeout` flags,
  or the `scroll_into_view_if_needed` call. The only functional change is to STOP proceeding
  into a doomed download wait when the mode was not enabled.
- The 1-second detection poll cadence stays. The heartbeat is an ADDITIONAL throttled log line,
  not a change to how often we check for the file/popup.
- The two outcomes stay intact: (a) raise on a detected "not found"/no-data popup; (b) return
  the freshly downloaded, stable file on success.
- OUT OF SCOPE (do not touch): the separate `page.expect_response(..., timeout=45 * 60 * 1000)`
  at line ~645 lives in a different (API-response) code path, not the download-wait path the
  hang was observed in. Changing it risks altering fetch behavior. Leave it alone; note it in
  a one-line code comment only if convenient, otherwise ignore.

---

## Implementation steps (ordered)

- [ ] 1. Add the `SCC_DOWNLOAD_TIMEOUT_S` constant next to the other SCC constants.
      Add, just after `SCC_CHUNK_ATTEMPTS = 3` (line ~55), a module constant:
      ```python
      # Max time ONE attempt waits for SCC's automatic (>1000) download before giving up and
      # letting _export_chunk_with_retry refresh + retry. 10 minutes: the heaviest real day
      # observed (~2,400 TIDs) produces a file in well under this, and the lane still gets
      # SCC_CHUNK_ATTEMPTS (3) tries, so the effective worst case is ~30 min total instead of
      # a single silent 45-min hang. Replaces the old inline 45 * 60.
      SCC_DOWNLOAD_TIMEOUT_S = 600
      # How often to emit a throttled "still waiting" heartbeat during a download wait. This is
      # ONLY a log cadence — the actual file/popup detection still polls once per second.
      SCC_WAIT_HEARTBEAT_S = 30
      ```
      Files: `tools/scc_client.py`
      Verify: `py -3.13 -c "import sys; sys.path.insert(0,'tools'); import scc_client as s; print(s.SCC_DOWNLOAD_TIMEOUT_S, s.SCC_WAIT_HEARTBEAT_S)"` prints `600 30`.

- [ ] 2. Add a tiny shared helper to detect a closed/broken page, mirroring Playwright usage.
      Add a module-level helper above `_wait_for_download` (so both loops can use it):
      ```python
      def _page_is_dead(page) -> bool:
          """True if the Edge page/context was closed or is otherwise unusable. Mirrors the
          existing pattern of guarding Playwright calls in try/except: Playwright's
          page.is_closed() returns True once the page/context is gone, and touching a dead
          page raises — either signal means we must stop waiting and fail fast."""
          try:
              if page.is_closed():
                  return True
              # Cheap liveness probe; a closed/crashed context raises here.
              page.evaluate("() => true")
              return False
          except Exception:
              return True
      ```
      Rationale: the existing code already wraps `page.evaluate(...)`/`frame.evaluate(...)` in
      try/except throughout (`_ensure_api_origin`, `_goto_search`), so this matches house style.
      Files: `tools/scc_client.py`
      Verify: `py -3.13 -m pytest tests -q` still **73 passed** (helper is unused so far; import stays clean).

- [ ] 3. Rework `_wait_for_download_or_notfound` to add a heartbeat and closed-page fast-fail,
      without changing detection logic. Keep the signature, the `deadline`/`candidate`/
      `last_size`/`stable_since` state, the file-glob + size-stable detection, the popup check
      (only while `candidate is None`), the two screenshots, and the two raises. Changes ONLY:
      - Track `start = time.time()` and `last_heartbeat = start` before the loop.
      - At the top of each iteration, if `_page_is_dead(page)`: raise
        `RuntimeError(f"SCC chunk {index}: Edge page was closed/lost while waiting for the download")`.
        (Do not screenshot a dead page.)
      - When a candidate file first appears or grows, emit
        `log(f"SCC chunk {index}: download in progress: {size} bytes")` on size change
        (gate it so it only fires when `size != last_size`, i.e. inside the branch that resets
        `stable_since`, so a stable file does not spam).
      - Once per `SCC_WAIT_HEARTBEAT_S`, emit
        `log(f"SCC chunk {index}: still waiting for the automatic download ({int(elapsed)}s elapsed, timeout {timeout_s}s)")`
        where `elapsed = time.time() - start`; update `last_heartbeat`.
      - Keep `time.sleep(1)` as the poll cadence. The final `RuntimeError(... no automatic
        download within {timeout_s}s)` message stays byte-for-byte.
      Files: `tools/scc_client.py`
      Verify: `py -3.13 -m pytest tests -q` still green; new tests from step 6 pass.

- [ ] 4. Replace the inline `45 * 60` at the call site with the new constant.
      In `_browser_export_chunk` (line ~783) change
      `timeout_s=45 * 60` to `timeout_s=SCC_DOWNLOAD_TIMEOUT_S`. Nothing else in that function changes.
      Files: `tools/scc_client.py`
      Verify: `py -3.13 -c "import sys,inspect; sys.path.insert(0,'tools'); import scc_client as s; src=inspect.getsource(s._browser_export_chunk); assert 'SCC_DOWNLOAD_TIMEOUT_S' in src and '45 * 60' not in src; print('ok')"` prints `ok`.

- [ ] 4b. FIX THE CHECKBOX HOLE: capture the `_ensure_large_mode_checkbox` return and fail
      fast when the mode could not be enabled. In `_browser_export_chunk`, change the large-mode
      block (line ~775) from:
      ```python
      if large:
          _ensure_large_mode_checkbox(frame, index, log)
      ```
      to:
      ```python
      if large:
          if not _ensure_large_mode_checkbox(frame, index, log):
              page.screenshot(path=str(OUT / f"scc_chunk{index}_nocheckbox_{datetime.now():%H%M%S}.png"))
              raise RuntimeError(
                  f"SCC chunk {index}: could not enable >1000 automatic-download mode"
              )
      ```
      This raise flows into the existing `_export_chunk_with_retry` (refresh + retry up to
      `SCC_CHUNK_ATTEMPTS`) instead of proceeding into `_wait_for_download_or_notfound`, which
      would otherwise wait for a download SCC never produces. Do NOT change `SCC_CHUNK_ATTEMPTS`,
      the escalation order, the force flags, or any fetch logic. (The screenshot mirrors the
      other failure-path screenshots in this module; drop it only if `page` screenshotting is
      awkward in context — the raise is the required part.)
      Files: `tools/scc_client.py`
      Verify: `py -3.13 -c "import sys,inspect; sys.path.insert(0,'tools'); import scc_client as s; src=inspect.getsource(s._browser_export_chunk); assert 'could not enable >1000 automatic-download mode' in src; print('ok')"` prints `ok`; full suite still green.

- [ ] 4c. (Optional, cosmetic) Demote the intermediate checkbox-attempt noise in
      `_ensure_large_mode_checkbox`. Keep the escalation (`check` -> `click` -> `js`), the force
      flags, and `scroll_into_view_if_needed` exactly as-is. Only soften the per-attempt log so a
      later success does not look like a failure: when a non-final attempt (`check`/`click`)
      raises, log something like
      `log(f"SCC chunk {index}: >1000 checkbox {attempt} not directly clickable; escalating ({str(exc)[:40]})")`
      instead of `... {attempt} failed ...`. KEEP the existing SUCCESS line
      `ticked >1000 automatic-download mode via {attempt}` unchanged, and KEEP a single loud line
      only when ALL methods fail — add, just before the final `return False`, a
      `log(f"SCC chunk {index}: >1000 checkbox could NOT be ticked by any method")`.
      Do not alter control flow or return values.
      Files: `tools/scc_client.py`
      Verify: `py -3.13 -m pytest tests -q` still green (behavior unchanged; log text only).

- [ ] 5. Apply the same heartbeat + closed-page treatment to `_wait_for_download` where safe.
      `_wait_for_download(download_directory, timeout_s, before=None)` has **no `page`
      argument** today, so a closed-page check cannot be added without changing its signature
      and its two call sites. To respect "only add if trivially safe," do the SAFE subset:
      - Add the heartbeat only (no page arg needed): track `start`/`last_heartbeat`, and once
        per `SCC_WAIT_HEARTBEAT_S` emit a log line. BUT `_wait_for_download` is called without a
        `log` argument at lines ~611 and ~800, so add an optional `log=None` parameter
        (default `None` = silent, preserving every existing caller byte-for-byte) and only
        emit when `log` is not None. Message:
        `log(f"SCC file wait: still waiting for a stable CSV ({int(elapsed)}s elapsed, timeout {timeout_s}s)")`
        and on size change `log(f"SCC file wait: download in progress: {size} bytes")`.
      - Do NOT add a page/closed-page check here (would require threading `page` through two
        call sites and risks the small-path fetch behavior). Leave the small (<=1000) path's
        fetch logic untouched; the heartbeat is additive and off by default.
      - The small-path callers at ~611/~800 may optionally pass `log=log` to surface the
        heartbeat; doing so changes no fetch behavior (same timeout, same detection). Prefer
        passing `log=log` at both sites so the small path also stops looking silent.
      Files: `tools/scc_client.py`
      Verify: `py -3.13 -c "import sys,inspect; sys.path.insert(0,'tools'); import scc_client as s; p=inspect.signature(s._wait_for_download).parameters; assert 'log' in p and p['log'].default is None; print('ok')"` prints `ok`; full suite still green.

- [ ] 6. Add offline tests in a new file `tests/test_scc_wait.py` (unittest style, matching
      `tests/test_polygon_workflow.py`: `sys.path.insert(0, str(ROOT / "tools"))` then
      `import scc_client`). All tests are fully offline — no Playwright, no browser, no real
      sleeping. Use a fake `page`, a fake `frame`, a real `tmp_path` download dir, and
      monkeypatch `scc_client.time.sleep` (to a no-op) and `scc_client.time.time` (a controllable
      fake clock) so the loops iterate instantly and deterministically.

      Shared fakes to define in the test module:
      - `FakePage(is_closed_returns=False, evaluate_raises=False, closes_after=None)`: records
        `.is_closed()` calls; `closes_after=N` flips `is_closed()` to True after N calls;
        `screenshot(path=...)` is a no-op that records the path.
      - `FakeFrame(body_text="")`: `locator("body").inner_text(timeout=...)` returns `body_text`
        (so `_search_error_popup` can be driven to find or not find a marker).
      - A `FakeClock` helper that returns monotonically increasing times by a fixed step each
        call, patched onto `scc_client.time.time`; patch `scc_client.time.sleep` to a no-op.
      - A `logs = []` capture `log = logs.append`.

      Tests (each asserts behavior, runs in milliseconds):
      1. `test_closed_page_fails_fast`: `page.is_closed()` True from the start →
         `_wait_for_download_or_notfound(page, frame, tmp_path, before=set(), index=1,
         timeout_s=600, log=log)` raises `RuntimeError` whose message contains
         `"Edge page was closed/lost"`, and the fake clock advanced far LESS than `timeout_s`
         (prove it did not wait out the timeout). No file is ever created in `tmp_path`.
      2. `test_timeout_ceiling_is_constant_not_45min`: no file ever appears, page alive, no
         popup. Drive the fake clock so each loop adds e.g. 30s. Assert the loop raises
         `RuntimeError` containing `"no automatic download within 600s"` and that the clock
         never exceeded ~`SCC_DOWNLOAD_TIMEOUT_S` (+ one step), i.e. it stopped at the new
         600s ceiling, NOT 2700s. Also assert `scc_client.SCC_DOWNLOAD_TIMEOUT_S == 600`.
      3. `test_heartbeat_emitted_while_waiting`: same no-file/alive-page setup but with a
         smaller injected `timeout_s` (e.g. 120) and a clock stepping 30s/iter; assert at least
         one `log` line matches `"still waiting for the automatic download"` and includes an
         elapsed-seconds figure. (Confirms the throttled heartbeat fires.)
      4. `test_success_returns_stable_file`: before the call, place a non-partial file in
         `tmp_path` (e.g. `result.csv` with some bytes) that is NOT in `before`. Drive the clock
         so that after the first couple of iterations `time.time() - stable_since >= 2`. Assert
         the function RETURNS that `Path`, equal to the file, and that a
         `"detected automatic download"` log line was emitted. Confirms the success outcome and
         size-stable logic are unchanged.
      5. `test_notfound_popup_raises`: `FakeFrame(body_text="... No data found ...")`, no file in
         `tmp_path`. Assert the function raises `RuntimeError` containing `"'not found' popup"`.
         Confirms outcome (a) still works. (`page.screenshot` is a no-op fake.)
      6. `test_wait_for_download_heartbeat_optional`: call `_wait_for_download(tmp_path,
         timeout_s=5, before=set(), log=log)` with no file present and the fake clock → it
         raises `"no stable CSV within 5s"` and, because `log` was passed, emitted at least one
         `"still waiting for a stable CSV"` line. Then call it again WITHOUT `log` and assert it
         still raises and emits nothing extra (proves `log=None` default stays silent / callers
         unchanged).

      Checkbox fakes + tests (for the step 4b/4c changes). Define a `FakeCheckbox` with
      configurable behavior: `count()` returns 1, `is_checked()` returns the current ticked
      state, `scroll_into_view_if_needed(...)` is a no-op, and `check()/click()/evaluate(...)`
      either raise `Exception("Element is outside of the viewport")` or set ticked=True
      according to the scenario. Build a `FakeFrame` whose `get_by_role("checkbox", ...)`
      returns the configured `FakeCheckbox`.
      7. `test_checkbox_js_fallback_succeeds`: `check()` and `click()` raise "outside of the
         viewport" but `evaluate()` ticks it → `_ensure_large_mode_checkbox(frame, 1, log)`
         returns `True` and a `"ticked >1000 automatic-download mode via js"` log line is
         emitted. (Mirrors the real run: the large path then proceeds normally.)
      8. `test_checkbox_all_methods_fail_returns_false`: all of `check()/click()/evaluate()`
         raise and `is_checked()` stays False → `_ensure_large_mode_checkbox(...)` returns
         `False` (and, if step 4c applied, the single "could NOT be ticked by any method" line
         is logged).
      9. `test_large_path_raises_when_mode_not_enabled`: the real hole. Drive
         `_browser_export_chunk` down the large path (len(requested) > `AUTO_DOWNLOAD_THRESHOLD`)
         with monkeypatched `scc_client._ensure_large_mode_checkbox` returning `False`, and
         monkeypatch `scc_client._wait_for_download_or_notfound` to a sentinel that records if it
         was called. Use fakes for the textarea/Search-button interactions (patch
         `scc_client._robust_click` to a no-op and give `FakeFrame.locator("textarea").first` a
         fake with `wait_for`/`fill`). Assert `_browser_export_chunk(...)` raises `RuntimeError`
         containing `"could not enable >1000 automatic-download mode"` AND that the
         `_wait_for_download_or_notfound` sentinel was NEVER called (proves it does not proceed
         into the doomed wait). This is the key regression test for the new fix.

      Files: `tests/test_scc_wait.py`
      Verify: `py -3.13 -m pytest tests/test_scc_wait.py -q` — all new tests pass; then
      `py -3.13 -m pytest tests -q` — total is **73 + (new count) passed**, 0 failed.

- [ ] 7. Full regression + cleanup.
      Run the whole suite one final time and confirm no regressions, then remove any temp
      logs/reports created during verification (`build_test.log`, `bt2.log`, `junit.xml`, etc.).
      Files: none (verification only)
      Verify: `py -3.13 -m pytest tests -q` prints `N passed` with `N >= 73 + new tests` and 0
      failures/errors. (Capture pytest output to a temp file and read it — this shell truncates
      inline stdout; file readback is reliable.)

---

## Notes / assumptions

- Shell quirk: in this environment `execute_pwsh` often returns an empty stdout + exit code
  -1 even on success; redirect pytest to a file and read the file to see the real summary.
- `_page_is_dead` uses `page.evaluate("() => true")` as a liveness probe because the module
  already relies on `page.evaluate` elsewhere; if that proves flaky in practice, `page.is_closed()`
  alone (wrapped in try/except) is the minimum and still satisfies the fast-fail requirement.
- The small-path (`<=1000`) fetch logic (Export-to-CSV click, blob capture, 240s timeouts) is
  left byte-for-byte; only an optional, default-off heartbeat log is added to `_wait_for_download`.
- CONFIRMED: SCC's data-pulling logic is unchanged. Across all steps the only things that change
  are (1) waiting (heartbeat + closed-page fast-fail), (2) the timeout ceiling
  (`SCC_DOWNLOAD_TIMEOUT_S` replacing the inline 45-min wait), (3) log wording, and (4)
  checkbox-return handling (raise instead of hang when `>1000` mode could not be enabled). TID
  pasting, Search, the `>1000` vs Export branch, chunk sizes, `SCC_CHUNK_ATTEMPTS`, the checkbox
  escalation order/flags, file-stability detection, CSV reading/validation, and the rows
  returned are all untouched.
- Review gate for the implementing loop: write `.agents/tasks/scc-wait-review.json` with
  `{"verdict": "APPROVED"}` only once step 7 passes.
