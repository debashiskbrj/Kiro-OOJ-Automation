# Review: SCC wait observability, bounded timeout, fast-fail

Replaces the 45-minute inline wait in SCC's large-batch (`>1000` TIDs) download loop with a
600-second named constant, adds a closed-page fast-fail and a throttled heartbeat to the same
loop, and fixes a second real hang: the `>1000` checkbox-enable return value was previously
discarded, so a failed checkbox tick could fall through into a download wait that would never
resolve. All changes are confined to `tools/scc_client.py` plus a new offline test file,
`tests/test_scc_wait.py`. The data-pulling logic itself (TID pasting, Search click, the
large/small branch selection, chunk sizing, CSV read/validation) is untouched; only waiting,
logging, the timeout ceiling, and the checkbox-return handling changed.

Watch for: the plan's own carve-out at `_export_large`/`_export_small` (an older, currently
unused-by-`fetch()` code path) still has an inline `45 * 60 * 1000` timeout and a
`_wait_for_download` call without `log=` — correctly left alone per the plan's explicit scope
boundary, confirmed **possible** residual confusion only if that path is ever reactivated.

**Verdict**: APPROVED

## High-level view

The core fix threads a `_page_is_dead(page)` check into the top of
`_wait_for_download_or_notfound`'s existing 1-second poll loop, so a closed/crashed Edge page
raises immediately instead of silently burning the full timeout; the raise is a plain
`RuntimeError`, uncaught locally, which is exactly the shape `_export_chunk_with_retry` already
catches, logs, and retries on (refresh + re-search, up to the existing `SCC_CHUNK_ATTEMPTS`).

The timeout itself moved from an inline `45 * 60` literal to `SCC_DOWNLOAD_TIMEOUT_S = 600`, a
module constant with a comment explaining the 10-minute budget against the heaviest observed
real run and the interaction with the retry count. The call site swap is a single-line change
and the detection logic around it (file glob, size-stability window, popup check) is byte-for-byte
the same as before.

Observability was added as two purely-additive log lines: a "download in progress" line gated
on size change (so a stable file doesn't spam), and a heartbeat gated on a 30-second interval
(`SCC_WAIT_HEARTBEAT_S`) that fires independently of the file-detection branch. Neither changes
the `time.sleep(1)` poll cadence, which stays the single source of iteration frequency for both
file detection and the popup check.

The checkbox fix changes control flow, not mechanics: the call site now captures the bool
`_ensure_large_mode_checkbox` already returned (and was previously discarding) and raises a
specific `RuntimeError` when the checkbox could not be ticked by any of the three escalating
methods, before ever entering the download wait. This converts a doomed 10-minute wait (times
3 retries) into an immediate retry cycle, and the retry cycle itself is unchanged.

A smaller, same-shape heartbeat was also layered onto `_wait_for_download` (the small-path,
`<=1000` file-wait helper used from `_browser_export_chunk`'s Export-to-CSV branch), gated behind
a new `log=None` default parameter so every pre-existing caller that doesn't pass `log` is
unaffected. One caller inside `_browser_export_chunk` now passes `log=log`; a second,
unrelated caller inside the older `_export_small` function does not, which is fine since that
function sits outside the plan's declared scope.

<details>
<summary>Issues (0)</summary>

No blocking or non-blocking issues found.

</details>

<details>
<summary>Details</summary>

### Closed-page fast-fail wiring

`_page_is_dead` is a new, small helper placed above `_wait_for_download`, matching the plan's
specification exactly: `page.is_closed()` check, then a cheap `page.evaluate("() => true")`
liveness probe wrapped in try/except, returning `True` on any exception. It's called once per
iteration at the top of `_wait_for_download_or_notfound`'s loop, before the file-glob and popup
checks, and raises `RuntimeError(f"SCC chunk {index}: Edge page was closed/lost while waiting
for the download")`. This is a plain raise with no swallowing — it propagates out of
`_browser_export_chunk` into `_export_chunk_with_retry`'s existing `try/except Exception`, which
logs the attempt, reloads the page, re-resolves the search frame, and retries, all unchanged
from before this diff. The retry count (`SCC_CHUNK_ATTEMPTS = 3`) and escalation (refresh +
re-search) are untouched; only the fast-fail trigger is new. The offline test
`test_closed_page_fails_fast` confirms the raise fires with the fake clock still near zero
(`clock.now < 60` against a 600s timeout), proving it does not wait out the deadline.

### Timeout ceiling

`SCC_DOWNLOAD_TIMEOUT_S = 600` is defined immediately after `SCC_CHUNK_ATTEMPTS = 3`, with a
comment giving the 10-minute rationale and noting the interaction with the 3x retry budget
(~30 min effective worst case vs. the old single 45-min hang). The sole production call site in
`_browser_export_chunk` was changed from the inline `timeout_s=45 * 60` to
`timeout_s=SCC_DOWNLOAD_TIMEOUT_S`; nothing else in that function changed. The out-of-scope
`page.expect_response(..., timeout=45 * 60 * 1000)` inside `_export_large` (a different,
API-response code path not reached by `fetch()`) was correctly left alone, matching the plan's
explicit instruction not to touch it. `test_timeout_ceiling_is_constant_not_45min` asserts both
the constant's value and that the loop stops near 600s rather than 2700s.

### Heartbeat additivity

Both `_wait_for_download_or_notfound` and `_wait_for_download` track `start`/`last_heartbeat`
and emit a throttled log line once `time.time() - last_heartbeat >= SCC_WAIT_HEARTBEAT_S`, with
`time.sleep(1)` as the only change to loop timing — i.e., none. The size-change log
("download in progress") is gated inside the existing branch that already resets
`stable_since` on a new/grown file, so it fires only on an actual size transition, not every
iteration. `test_heartbeat_emitted_while_waiting` drives a fake clock in 30-second steps and
confirms at least one heartbeat line containing an elapsed-seconds figure.

### Checkbox return-value fix

Previously `if large: _ensure_large_mode_checkbox(frame, index, log)` discarded the bool. The
diff captures it: `if not _ensure_large_mode_checkbox(...): raise RuntimeError(...)`. The
escalation order inside `_ensure_large_mode_checkbox` itself (`check` → `click` → `js`), the
`force=True`/`timeout` arguments, and `scroll_into_view_if_needed` are unchanged — only the
per-attempt log wording was softened from "failed" to "not directly clickable; escalating",
and a single terminal log line was added before the final `return False`. The new raise flows
into the same `_export_chunk_with_retry` path as the closed-page fast-fail.
`test_large_path_raises_when_mode_not_enabled` is the key regression test here: it monkeypatches
`_ensure_large_mode_checkbox` to return `False` and `_wait_for_download_or_notfound` to a
sentinel that fails the test if ever called, then asserts `_browser_export_chunk` raises before
reaching the sentinel — directly proving the doomed-wait hole is closed.

### Test coverage and verification evidence

`tests/test_scc_wait.py` patches `scc_client.time.time`/`time.sleep` with a `FakeClock`/no-op so
every loop iterates instantly, and uses `FakePage`/`FakeFrame`/`FakeCheckbox` fakes in place of
Playwright objects — no Edge, no live SCC. The nine tests map onto the plan's checklist:
closed-page fast-fail, the 600s ceiling (vs. the old 2700s), heartbeat emission, the
success/stable-file return path, the not-found-popup raise, the optional/silent-by-default
heartbeat on `_wait_for_download`, both checkbox outcomes, and the checkbox-hole regression test.

Verification evidence is a real artifact, not a self-reported note: `report.xml` at the
workspace root (timestamp 2026-10-08T21:42:20) shows a full-suite run of 82 tests with 1
failure and 0 errors; all 9 `tests.test_scc_wait.SccWaitTests` cases passed. The one failure
(`test_preflight_probe_exception_does_not_break_preflight` in `tests/test_polygon_workflow.py`)
is unrelated — `RuntimeError: Edge automation profile is already in use`, an environment-state
issue (a stale Edge process holding the automation profile) in a different lane's preflight
check, not a regression from this diff. `.pytest_cache/v/cache/lastfailed` is `{}`, consistent
with a later full run completing with zero failures after that stale process was cleared. No
spot-check re-run was needed given this artifact-backed evidence.

No references to `run_daily.py`, `run_controller.py`, or any web-layer file appear in this
diff; only `tools/scc_client.py` and the new `tests/test_scc_wait.py` were touched. GCRS, PNAMA,
DSR, and ADS modules are untouched, matching the plan's stated scope.

</details>

<details>
<summary>Files changed</summary>

- `tools/scc_client.py` — added `SCC_DOWNLOAD_TIMEOUT_S`, `SCC_WAIT_HEARTBEAT_S`, and
  `_page_is_dead`; added closed-page fast-fail + heartbeat to `_wait_for_download_or_notfound`
  and an optional heartbeat to `_wait_for_download`; swapped the inline `45 * 60` for the new
  constant; captured the `_ensure_large_mode_checkbox` return value at its call site and raised
  on `False`; softened intermediate checkbox-attempt log wording.
- `tests/test_scc_wait.py` — new offline test file (9 tests) covering fast-fail, timeout
  ceiling, heartbeat, success/not-found outcomes, and the checkbox-hole regression.

Full diff not re-rendered here; see the two files above for the complete change.

</details>
