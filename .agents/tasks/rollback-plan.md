# Rollback Plan — restore Oct-7-2026 behavior (SCC / GCRS / preflight auth-gate)

Context: this is an intentional, user-directed rollback. It reintroduces three known,
previously-fixed issues on purpose:
- SCC's `>1000` checkbox data-capture shortfall (large-path checkbox tick becomes
  best-effort again; its result is ignored).
- The silent 45-minute SCC download wait (no heartbeat, no dead-page fast-fail).
- GCRS single-attempt, no-retry Edge behavior (`"Failed to fetch"` on an Edge batch
  propagates immediately instead of retrying/degrading).

Do not "fix" or soften these while reverting — that is the point of the rollback.

No git/version control exists in this workspace. All edits are made directly to the
files below, in place. There is nothing to branch or commit.

Test command (run after edits, from the workspace root):
```
py -3.13 -m pytest tests -q
```

---

## Files to change (4)
1. `tools/scc_client.py`
2. `tools/gcrs_mcscli.py`
3. `tools/run_daily.py`
4. `tools/web/run_controller.py`

## Test files to change (3)
1. `tests/test_scc_wait.py` — delete entirely
2. `tests/test_polygon_workflow.py` — remove 3 specific tests, keep the rest
3. `tests/test_web_controller.py` — remove 4 specific tests, keep the rest

## Files to leave COMPLETELY untouched
- `tools/siw_client.py`
- `tools/ooj_paths.py`
- `tools/auto_setup.py`
- `tools/web/server.py`
- every `Docs/*.md` file
- `tests/test_auto_setup.py`
- `tests/test_remark_rules.py`
- `tests/test_sanitize_captures.py`
- every part of `tools/run_daily.py` and `tools/web/run_controller.py` not explicitly
  called out below (ticket pulling, lane_scc/lane_eagle/lane_gcrs/lane_pnama/lane_dsr/
  lane_polygon/lane_ads, fill_and_recalc, finalize_remarks, read_back, prune_history,
  `environment_checklist` / `_environment_checklist_safe()`, the six original
  `preflight_state()` keys, etc.)

---

## 1. `tools/scc_client.py`

### 1.1 Remove the two Oct-8 wait-tuning constants and their doc comments

Current (lines ~56-66, immediately after `SCC_CHUNK_ATTEMPTS = 3` and its comment):
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
REQUIRED_COLUMNS = ["Tracking ID", "State", "Destination", "Source", "Ship Option"]
```
Change to (delete both constants + their comments, keep `REQUIRED_COLUMNS`):
```python
REQUIRED_COLUMNS = ["Tracking ID", "State", "Destination", "Source", "Ship Option"]
```
Do not touch `SCC_UI_CHUNK`, `AUTO_DOWNLOAD_THRESHOLD`, or `SCC_CHUNK_ATTEMPTS` and their
comments above this block — those are unrelated to Oct 8 and must stay exactly as-is.

### 1.2 Remove the `_page_is_dead(page)` helper entirely

Current (immediately after the constants block, lines ~69-79):
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
Change to: delete the whole function. Nothing in the reverted file should reference
`_page_is_dead` after this change (verify with the call-site removal in 1.4 below).

### 1.3 `_wait_for_download` — this function is NOT in scope

`_wait_for_download` (the small-path, generic download waiter used by `_export_small`
and the small path of `_browser_export_chunk`) already defaults `log=None` and only
heartbeats when a caller passes `log`. This function was not called out in the Oct 8
"SCC wait resilience" revert description as something to silence — it predates Oct 8
in its current form apart from the optional `log` parameter plumbing, which callers
rely on (e.g. `test_wait_for_download_heartbeat_optional`, which is being deleted
anyway because the whole file is deleted). Leave `_wait_for_download` exactly as-is.
Only `_wait_for_download_or_notfound` (the large/auto-download path, section 1.4) is
in scope — that is where the heartbeat log and dead-page check were added for the
Oct 8 change and where the 45-minute literal timeout lived before Oct 8.

### 1.4 `_wait_for_download_or_notfound` — remove dead-page fast-fail and heartbeat; restore silent polling

Current full function (lines ~172-217):
```python
def _wait_for_download_or_notfound(page, frame, download_directory: Path, before: set[str],
                                   index: int, timeout_s: int, log) -> Path:
    """After Search on a large (>1000) search, detect exactly two outcomes:

      1. a 'not found'/no-data error popup -> raise, so the caller restarts the SCC steps
         (refresh the tab and search again);
      2. SCC's automatic download -> return the freshly downloaded, stable file.
    """
    start = time.time()
    deadline = start + timeout_s
    last_heartbeat = start
    candidate: Path | None = None
    last_size = -1
    stable_since: float | None = None
    while time.time() < deadline:
        # (0) fast-fail: if Edge closed/crashed or navigated away (e.g. an SSO redirect),
        # stop immediately instead of burning the whole timeout. The error flows into
        # _export_chunk_with_retry (refresh + retry), which is the desired recovery.
        if _page_is_dead(page):
            raise RuntimeError(
                f"SCC chunk {index}: Edge page was closed/lost while waiting for the download"
            )
        # (1) automatic download: a new, stable, non-partial file appeared.
        files = [
            item for item in download_directory.glob("*")
            if item.is_file() and item.name not in before
            and item.suffix.lower() not in {".crdownload", ".tmp", ".partial"}
        ]
        if files:
            newest = max(files, key=lambda item: item.stat().st_mtime)
            size = newest.stat().st_size
            if newest != candidate or size != last_size:
                candidate, last_size, stable_since = newest, size, time.time()
                log(f"SCC chunk {index}: download in progress: {size} bytes")
            elif size > 0 and stable_since and time.time() - stable_since >= 2:
                log(f"SCC chunk {index}: detected automatic download {newest.name}")
                return newest
        # (2) 'not found' popup: raise so the retry wrapper restarts the SCC steps. Only
        # check while no download has started yet, so a real download always wins.
        if candidate is None:
            popup = _search_error_popup(frame)
            if popup:
                page.screenshot(path=str(OUT / f"scc_chunk{index}_notfound_{datetime.now():%H%M%S}.png"))
                raise RuntimeError(f"SCC chunk {index}: 'not found' popup after Search ({popup}); restarting")
        # Throttled heartbeat so a slow/dead SCC does not look like a silent hang. This is
        # ONLY a log line; the detection poll below still runs once per second.
        if time.time() - last_heartbeat >= SCC_WAIT_HEARTBEAT_S:
            elapsed = time.time() - start
            log(f"SCC chunk {index}: still waiting for the automatic download "
                f"({int(elapsed)}s elapsed, timeout {timeout_s}s)")
            last_heartbeat = time.time()
        time.sleep(1)
    page.screenshot(path=str(OUT / f"scc_chunk{index}_nodownload_{datetime.now():%H%M%S}.png"))
    raise RuntimeError(f"SCC chunk {index}: no automatic download within {timeout_s}s")
```
Change to (remove the `_page_is_dead` fast-fail block entirely, remove the heartbeat
block entirely, remove the now-unused `start`/`last_heartbeat` bookkeeping, keep the
two real outcomes — download detection and not-found popup — and the final timeout
raise; `page` parameter stays in the signature since the not-found screenshot still
uses it, and so does the call site):
```python
def _wait_for_download_or_notfound(page, frame, download_directory: Path, before: set[str],
                                   index: int, timeout_s: int, log) -> Path:
    """After Search on a large (>1000) search, detect exactly two outcomes:

      1. a 'not found'/no-data error popup -> raise, so the caller restarts the SCC steps
         (refresh the tab and search again);
      2. SCC's automatic download -> return the freshly downloaded, stable file.
    """
    deadline = time.time() + timeout_s
    candidate: Path | None = None
    last_size = -1
    stable_since: float | None = None
    while time.time() < deadline:
        # (1) automatic download: a new, stable, non-partial file appeared.
        files = [
            item for item in download_directory.glob("*")
            if item.is_file() and item.name not in before
            and item.suffix.lower() not in {".crdownload", ".tmp", ".partial"}
        ]
        if files:
            newest = max(files, key=lambda item: item.stat().st_mtime)
            size = newest.stat().st_size
            if newest != candidate or size != last_size:
                candidate, last_size, stable_since = newest, size, time.time()
                log(f"SCC chunk {index}: download in progress: {size} bytes")
            elif size > 0 and stable_since and time.time() - stable_since >= 2:
                log(f"SCC chunk {index}: detected automatic download {newest.name}")
                return newest
        # (2) 'not found' popup: raise so the retry wrapper restarts the SCC steps. Only
        # check while no download has started yet, so a real download always wins.
        if candidate is None:
            popup = _search_error_popup(frame)
            if popup:
                page.screenshot(path=str(OUT / f"scc_chunk{index}_notfound_{datetime.now():%H%M%S}.png"))
                raise RuntimeError(f"SCC chunk {index}: 'not found' popup after Search ({popup}); restarting")
        time.sleep(1)
    page.screenshot(path=str(OUT / f"scc_chunk{index}_nodownload_{datetime.now():%H%M%S}.png"))
    raise RuntimeError(f"SCC chunk {index}: no automatic download within {timeout_s}s")
```
Note: this still takes `timeout_s` as a parameter (used by the call site, see 1.5) —
only the inline literal at the OLD call site goes back to `45 * 60`; the function
itself keeps a `timeout_s` parameter because `_wait_for_download_or_notfound` always
had one (it's `_wait_for_download`-style). The revert is about the VALUE passed in
and the body's behavior (no heartbeat, no dead-page check), not the signature.

### 1.5 `_browser_export_chunk` call site — restore the inline 45-minute literal

Current (inside `_browser_export_chunk`, large-path branch, lines ~1086-1092 by content):
```python
        target = _wait_for_download_or_notfound(page, frame, download_directory, before,
                                                index, timeout_s=SCC_DOWNLOAD_TIMEOUT_S, log=log)
```
Change to:
```python
        target = _wait_for_download_or_notfound(page, frame, download_directory, before,
                                                index, timeout_s=45 * 60, log=log)
```

### 1.6 `_ensure_large_mode_checkbox` — restore original log wording; keep structure

Current function (lines ~1012-1053):
```python
def _ensure_large_mode_checkbox(frame, index: int, log) -> bool:
    """Tick the ">1000 TrackingIds" checkbox that switches SCC to automatic download.

    This checkbox is REQUIRED for the large path: without it SCC shows the normal Export
    button flow instead of auto-downloading, so waiting for a download would hang. The
    element often renders below the fold, so a plain ``check()`` fails with "outside of
    the viewport"; we scroll it into view and fall back to a JS click on the input/label.
    Returns True if the checkbox ends up checked.
    """
    checkbox = frame.get_by_role(
        "checkbox",
        name="Download Default columns only (Use when submitting >1000 TrackingIds routing tasks to CO)",
        exact=True,
    )
    try:
        if checkbox.count() != 1:
            log(f"SCC chunk {index}: >1000 checkbox not present")
            return False
        if checkbox.is_checked():
            return True
    except Exception as exc:
        log(f"SCC chunk {index}: could not read >1000 checkbox state ({str(exc)[:60]})")

    # Try progressively more forceful ways to tick it.
    try:
        checkbox.scroll_into_view_if_needed(timeout=10_000)
    except Exception:
        pass
    for attempt in ("check", "click", "js"):
        try:
            if attempt == "check":
                checkbox.check(force=True, timeout=10_000)
            elif attempt == "click":
                checkbox.click(force=True, timeout=10_000)
            else:
                checkbox.evaluate("el => el.click()")
        except Exception as exc:
            # A failed check/click is routinely followed by a successful escalation, so
            # phrase it as "escalating" rather than "failed" to avoid false alarms.
            log(f"SCC chunk {index}: >1000 checkbox {attempt} not directly clickable; "
                f"escalating ({str(exc)[:40]})")
            continue
        try:
            if checkbox.is_checked():
                log(f"SCC chunk {index}: ticked >1000 automatic-download mode via {attempt}")
                return True
        except Exception:
            pass
    log(f"SCC chunk {index}: >1000 checkbox could NOT be ticked by any method")
    return False
```
Change: keep the function's structure (count/is_checked guard, scroll, the
check/click/js escalation loop) exactly as-is — it is still needed as the best-effort
ticking mechanism used by section 1.7's reverted call site. Only change the per-attempt
failure log line's wording back to a plain "failed" phrasing (the exact pre-Oct-8
string was not recoverable from the current file, so use the most likely original
wording per the task's instruction to prioritize ticking BEHAVIOR over exact wording):

Replace:
```python
        except Exception as exc:
            # A failed check/click is routinely followed by a successful escalation, so
            # phrase it as "escalating" rather than "failed" to avoid false alarms.
            log(f"SCC chunk {index}: >1000 checkbox {attempt} not directly clickable; "
                f"escalating ({str(exc)[:40]})")
            continue
```
with:
```python
        except Exception as exc:
            log(f"SCC chunk {index}: >1000 checkbox {attempt} failed ({str(exc)[:40]})")
            continue
```
Leave the return type (`bool`) and the rest of the function body (docstring, the
`count()`/`is_checked()` guard, the scroll, the success log/return inside the loop,
and the final "could NOT be ticked by any method" line) unchanged — the instruction
says restore the TICKING BEHAVIOR over exact wording, and the function's actual tick
attempts are unchanged; only the escalation-labelled failure line's wording reverts.

### 1.7 `_browser_export_chunk` — restore best-effort ticking (ignore the return value)

Current (large-path branch inside `_browser_export_chunk`, lines ~1081-1086):
```python
    if large:
        if not _ensure_large_mode_checkbox(frame, index, log):
            page.screenshot(path=str(OUT / f"scc_chunk{index}_nocheckbox_{datetime.now():%H%M%S}.png"))
            raise RuntimeError(
                f"SCC chunk {index}: could not enable >1000 automatic-download mode"
            )
```
Change to (CRITICAL — call it but ignore the return value; never raise on a failed tick):
```python
    if large:
        _ensure_large_mode_checkbox(frame, index, log)
```
This is the core of the reintroduced bug: the large path now proceeds into the Search
+ download wait regardless of whether the `>1000` checkbox actually got ticked. If SCC
quietly stays on the normal Export-button flow because the checkbox did not take, the
large-path wait (`_wait_for_download_or_notfound`, expecting an automatic download) can
run the full 45 minutes before failing — this is the known, accepted-by-the-user
"data-capture shortfall" being restored.

After this change, remove the now-unused screenshot artifact naming conflict risk: no
other code references `scc_chunk{index}_nocheckbox_...png`, so no further cleanup is
needed beyond deleting that one `page.screenshot(...)` line above (already deleted by
the replacement block).

### 1.8 Nothing else in `scc_client.py` changes

Do not touch: TID pasting/`textarea.fill`, the `Search` button click flow, `SCC_UI_CHUNK`,
`AUTO_DOWNLOAD_THRESHOLD`, `SCC_CHUNK_ATTEMPTS`, CSV reading (`_read_csv`), validation
(`_validate_rows`), `_search_error_popup` / `_NOT_FOUND_MARKERS`, the small
(`<=1000`) `Export to CSV` path inside `_browser_export_chunk`, `_export_small`,
`_export_large`, `_fetch_batch_in_page`, `_rows_from_payload`, `_capture_enum_map*`,
`_robust_click`, `_export_chunk_with_retry`, `fetch()`, or `main()`. These are all
unrelated to the Oct 8 wait-resilience change.

---

## 2. `tools/gcrs_mcscli.py`

### 2.1 Remove the two Oct-4 retry constants and their doc comment

Current (lines ~16-23, right after the `BATCH_MCSCLI`/`BATCH_EDGE` block):
```python
# Per-batch Edge retry for transient "Failed to fetch" / degraded-window failures.
# The 2026-10-04 run (see Docs/edge-cases.md #G8) lost the entire GCRS lane — and,
# transitively, polygon + ads — when a single Edge /grbi batch threw "Failed to fetch".
# Retrying only that batch (and degrading it to unresolved on persistent failure) keeps
# the remaining batches running instead of blanking the whole lane.
EDGE_BATCH_RETRY_ATTEMPTS = 3
EDGE_BATCH_RETRY_BACKOFF_S = (2, 4, 6)
```
Change to: delete this whole block (both constants and the comment). The
`BATCH_MCSCLI = 500` / `BATCH_EDGE = 200` lines directly above it, and the
`_batch_size_for` function directly below it, stay exactly as-is.

### 2.2 Remove `_is_failed_to_fetch()` helper

Current (lines ~230-237):
```python
def _is_failed_to_fetch(error: BaseException) -> bool:
    """True when a TransportError looks like the transient Edge "Failed to fetch"/degraded window.

    Only this family of errors is retried-then-degraded on the Edge transport; any other
    TransportError is a genuine failure and must propagate unchanged.
    """
    message = str(error).lower()
    return "failed to fetch" in message or "degraded" in message
```
Change to: delete this function entirely.

### 2.3 Remove `_resolve_batch_with_edge_retry()` helper

Current (lines ~240-266):
```python
def _resolve_batch_with_edge_retry(
    chunk: list[str],
    polygon_set: str,
    log: Callable[[str], None],
    session: FailoverSiwSession,
) -> tuple[dict[str, dict], set[str]]:
    """Resolve one /grbi batch, retrying only transient Edge "Failed to fetch" failures.

    On the mcscli (or any non-Edge) transport this calls ``_resolve`` exactly once with no
    retry and no catch, so that path is byte-for-byte unchanged. On the Edge transport a
    "Failed to fetch"/degraded-window ``TransportError`` is retried up to
    ``EDGE_BATCH_RETRY_ATTEMPTS`` with ``EDGE_BATCH_RETRY_BACKOFF_S`` backoff; if every
    attempt fails the batch is degraded to unresolved (empty rows, empty invalid) so its
    TIDs fall into ``fetch()``'s ``errored`` list and the remaining batches still run.
    Any other ``TransportError`` re-raises unchanged.
    """
    if getattr(session, "transport_name", None) != "edge":
        return _resolve(chunk, polygon_set, log, session)

    for attempt in range(EDGE_BATCH_RETRY_ATTEMPTS):
        try:
            return _resolve(chunk, polygon_set, log, session)
        except TransportError as error:
            if not _is_failed_to_fetch(error):
                raise
            if attempt < EDGE_BATCH_RETRY_ATTEMPTS - 1:
                wait = EDGE_BATCH_RETRY_BACKOFF_S[min(attempt, len(EDGE_BATCH_RETRY_BACKOFF_S) - 1)]
                log(
                    f"  WARNING GCRS Edge batch failed ({error}); "
                    f"retry {attempt + 1}/{EDGE_BATCH_RETRY_ATTEMPTS - 1} after {wait}s backoff"
                )
                time.sleep(wait)
            else:
                log(
                    f"  WARNING GCRS batch degraded to unresolved after "
                    f"{EDGE_BATCH_RETRY_ATTEMPTS} Edge retries (Failed to fetch); "
                    f"continuing remaining batches"
                )
                return {}, set()
    # Unreachable: the loop either returns or raises on every path above.
    return {}, set()
```
Change to: delete this function entirely.

### 2.4 `fetch()` call site — call `_resolve` directly, no retry wrapper

Current (inside `fetch()`'s batch loop, lines ~298-306):
```python
                batch_size = _batch_size_for(active_session)
                chunk = ids[offset:offset + batch_size]
                batch_number += 1
                transport = getattr(active_session, "transport_name", None) or "unknown"
                log(f"GCRS batch {batch_number} ({polygon_set}): {len(chunk)} ids [{transport}]")
                chunk_rows, chunk_invalid = _resolve_batch_with_edge_retry(
                    chunk, polygon_set, log, active_session
                )
                rows.update(chunk_rows)
                invalid |= chunk_invalid
                offset += batch_size
```
Change to:
```python
                batch_size = _batch_size_for(active_session)
                chunk = ids[offset:offset + batch_size]
                batch_number += 1
                transport = getattr(active_session, "transport_name", None) or "unknown"
                log(f"GCRS batch {batch_number} ({polygon_set}): {len(chunk)} ids [{transport}]")
                chunk_rows, chunk_invalid = _resolve(chunk, polygon_set, log, active_session)
                rows.update(chunk_rows)
                invalid |= chunk_invalid
                offset += batch_size
```
This makes an Edge `"Failed to fetch"` (or any `TransportError`) on a batch propagate
immediately out of `fetch()` — exactly like the mcscli path already behaves, and
exactly as it did before the Oct 4 change — instead of being retried/degraded.

### 2.5 Nothing else in `gcrs_mcscli.py` changes

`BATCH_MCSCLI = 500`, `BATCH_EDGE = 200`, `_batch_size_for`, `_grbi`, `_rows_from`,
`_invalid_ids`, `_consume_retry`, `_resolve` (the G1/G2/G3/G3b guard ladder — this is
unrelated SIW-correctness logic, not the Oct 4 Edge-retry feature), and the rest of
`fetch()` (dedup, numeric/document split, sequential polygon-set loop, final
ordered/not_found/errored computation) all stay exactly as-is.

---

## 3. `tools/run_daily.py`

### 3.1 Remove ONLY the Oct-8 preflight auth-gate probe block

Current (inside `preflight()`, immediately after the `if not checks["mcscli"]:` block,
lines ~262-298 by content — this is the full block to delete):
```python
    if not checks["mcscli"]:
        run.log("SIW mcscli is unavailable; authenticated Edge fallback will be used")
    # Best-effort, read-only GCRS transport classification (additive; warn-only).
    # Probes SIW with mode='mcscli' ONLY, so the single Edge automation profile is
    # never opened here. An mcscli probe failure classifies as 'edge-fallback'
    # (the run would be forced onto the fragile Edge lane that, on the 2026-10-04
    # Failed-to-fetch run, blanked gcrs+polygon+ads — see #G8). This block is fully
    # try/excepted so it can never crash preflight and never hard-blocks the run.
    try:
        import siw_client
        gcrs_transport = "unknown"
        session = siw_client.FailoverSiwSession(mode="mcscli", log=run.log)
        try:
            session.ensure_ready()
            gcrs_transport = session.transport_name or "unknown"
        except siw_client.TransportError:
            gcrs_transport = "edge-fallback"
        finally:
            session.close()
        checks["auth_gate"] = {
            "midway_session_valid": checks["midway_session_valid"],
            "gcrs_transport": gcrs_transport,
            "browser_signin": "unknown",
            "browser_signin_reason": "not checked at preflight to avoid opening the single Edge automation profile",
        }
        run.summary["preflight"] = checks
        if gcrs_transport in ("edge-fallback", "unknown"):
            run.log(
                "WARNING: GCRS will use the fragile Edge fallback (mcscli SIW probe failed); "
                "one bad batch previously blanked gcrs+polygon+ads — see #G8"
            )
        else:
            run.log("auth gate: GCRS has the fast mcscli lane")
    except Exception as exc:
        checks["auth_gate"] = {
            "midway_session_valid": checks["midway_session_valid"],
            "gcrs_transport": "unknown",
            "browser_signin": "unknown",
            "browser_signin_reason": "not checked at preflight to avoid opening the single Edge automation profile",
        }
        run.summary["preflight"] = checks
        run.log(f"auth gate: GCRS transport classification skipped ({type(exc).__name__})")
    return checks
```
Change to (keep the one line that predates Oct 8, delete everything after it, keep `return checks`):
```python
    if not checks["mcscli"]:
        run.log("SIW mcscli is unavailable; authenticated Edge fallback will be used")
    return checks
```
After this edit, `preflight()` no longer imports `siw_client`, never adds an
`auth_gate` key to `checks`, and never logs the "GCRS will use the fragile Edge
fallback" warning. `run.summary["preflight"] = checks` is still assigned exactly once,
earlier in the function (`run.log(f"preflight: {checks}")` / `run.summary["preflight"]
= checks` lines that already exist above the deleted block) — verify that assignment
is still present after the edit (it is the pre-existing line a few lines above the
deleted block; do not remove it).

### 3.2 Do not touch anything else in `run_daily.py`

In particular, do not touch: `prune_history`, `clean_header`, `normalize_tid`,
`csv_safe`, `EXCEL_ERRORS`, the `Run` class, `tech_from_tid_file`, `pull_tickets`,
`run_browser_worker`, `write_tid_file`, `lane_scc`, `lane_eagle`, `lane_gcrs`,
`lane_pnama`, `lane_dsr`, `lane_polygon`, `lane_ads`, `fill_and_recalc`,
`finalize_remarks`, `read_back`, or `main()`. (These were read in full during
exploration and contain no other Oct-8/Oct-4 markers — only the auth-gate block in
`preflight()` is in scope.)

---

## 4. `tools/web/run_controller.py`

### 4.1 Remove `_auth_gate_safe()` helper entirely

Current (lines ~104-128):
```python
def _auth_gate_safe() -> dict:
    """Read-only GCRS transport classification for the page. Fully wrapped so a probe
    failure can never break ``preflight_state``.

    Probes SIW with mode='mcscli' ONLY, so the single Edge automation profile is never
    opened here. A mcscli probe failure classifies GCRS as 'edge-fallback' (the fragile
    Edge lane that blanked gcrs+polygon+ads on the 2026-10-04 Failed-to-fetch run — see
    #G8). Browser-lane sign-in is reported as 'unknown' because a reliable check would
    require opening that Edge profile. Warn-only: the page does not hard-block on this."""
    try:
        sys.path.insert(0, str(TOOLS))
        import siw_client  # noqa: E402  (path set up above)

        gcrs_transport = "unknown"
        session = siw_client.FailoverSiwSession(mode="mcscli", log=lambda _m: None)
        try:
            session.ensure_ready()
            gcrs_transport = session.transport_name or "unknown"
        except siw_client.TransportError:
            gcrs_transport = "edge-fallback"
        finally:
            session.close()
        return {
            "midway_session_valid": midway_session_valid(),
            "gcrs_transport": gcrs_transport,
            "browser_signin": "unknown",
            "browser_signin_reason": "not checked at preflight to avoid opening the single Edge automation profile",
        }
    except Exception:
        return {
            "midway_session_valid": midway_session_valid(),
            "gcrs_transport": "unknown",
            "browser_signin": "unknown",
            "browser_signin_reason": "not checked at preflight to avoid opening the single Edge automation profile",
        }
```
Change to: delete this function entirely.

### 4.2 `preflight_state()` — remove the additive `auth_gate` key only

Current (lines ~137-152):
```python
def preflight_state() -> dict:
    """Everything the page needs before offering the Start button."""
    edge = edge_profile_status()
    mcscli = Path(
        r"C:\Program Files\Common Files\AmazonMidwayClientSuite\bin\mcscli.exe"
    ).exists()
    midway = midway_session_valid()
    return {
        "midway_ok": midway,
        "edge_profile_exists": bool(edge.get("profile_exists")),
        "edge_profile_busy": bool(edge.get("profile_process_ids")),
        "edge_stale_locks": edge.get("stale_lock_files") or [],
        "mcscli_present": mcscli,
        # Ready to start only when Midway is granted and the Edge profile is free.
        "ready": midway and bool(edge.get("safe_to_launch")),
        # Additive, read-only environment checklist (manual steps a human must do).
        # New key — existing keys above are unchanged; the current page ignores this.
        "environment_checklist": _environment_checklist_safe(),
        # Additive, read-only GCRS transport classification (warn-only; see #G8).
        # New key — existing keys above are unchanged; never raises.
        "auth_gate": _auth_gate_safe(),
    }
```
Change to (delete only the `auth_gate` key and its two comment lines; keep
`environment_checklist` and every other key exactly as-is):
```python
def preflight_state() -> dict:
    """Everything the page needs before offering the Start button."""
    edge = edge_profile_status()
    mcscli = Path(
        r"C:\Program Files\Common Files\AmazonMidwayClientSuite\bin\mcscli.exe"
    ).exists()
    midway = midway_session_valid()
    return {
        "midway_ok": midway,
        "edge_profile_exists": bool(edge.get("profile_exists")),
        "edge_profile_busy": bool(edge.get("profile_process_ids")),
        "edge_stale_locks": edge.get("stale_lock_files") or [],
        "mcscli_present": mcscli,
        # Ready to start only when Midway is granted and the Edge profile is free.
        "ready": midway and bool(edge.get("safe_to_launch")),
        # Additive, read-only environment checklist (manual steps a human must do).
        # New key — existing keys above are unchanged; the current page ignores this.
        "environment_checklist": _environment_checklist_safe(),
    }
```
After this edit, the six original keys (`midway_ok`, `edge_profile_exists`,
`edge_profile_busy`, `edge_stale_locks`, `mcscli_present`, `ready`) plus the Oct-3
`environment_checklist` key remain. `midway_session_valid()`, `edge_profile_status()`,
`_environment_checklist_safe()`, `launch_midway_prompt()`, the `RunController` class,
and every helper function below `preflight_state()` are untouched.

### 4.3 Nothing else in `run_controller.py` changes

Do not touch `_environment_checklist_safe()`, `midway_session_valid()`,
`edge_profile_status()`, `launch_midway_prompt()`, `RunController` (and all its
methods), `_read_summary`, `_sources_from_summary`, `_counts_for`,
`_total_requested`, `_csv_row_count`, `_source_percent`, `is_publishable`,
`is_downloadable`, `blank_sources`, `_verification_ok`, `_sha256`,
`_working_workbook`, `_existing_desktop_copy`, `publish_to_desktop`, or the module
docstring/imports.

---

## 5. Test deletions / edits

### 5.1 `tests/test_scc_wait.py` — delete the file entirely

All 9 test functions in this file exercise code being reverted in section 1 (the
heartbeat, the dead-page fast-fail, the `SCC_DOWNLOAD_TIMEOUT_S` constant, and the
"raise if checkbox could not be enabled" call-site guard). None of them test code that
survives the revert:
- `test_closed_page_fails_fast` — tests the deleted `_page_is_dead` fast-fail path.
- `test_timeout_ceiling_is_constant_not_45min` — asserts
  `scc_client.SCC_DOWNLOAD_TIMEOUT_S == 600`; that constant is deleted.
- `test_heartbeat_emitted_while_waiting` — tests the deleted heartbeat log.
- `test_success_returns_stable_file` — passes `timeout_s=600` and checks a
  `_wait_for_download_or_notfound` success path; still structurally callable post-revert,
  but it exists only to round out this file's coverage of the function being reverted,
  and the function's post-revert signature/behavior (silent polling) makes this
  redundant with manual verification — delete with the rest of the file per the task's
  "delete the file entirely unless tests still validate remaining code" instruction;
  no other test file in the suite independently covers
  `_wait_for_download_or_notfound`'s success path, so losing this one test is accepted
  as part of the rollback (consistent with reintroducing the known behavior).
- `test_notfound_popup_raises` — same reasoning as above (covers behavior that still
  exists post-revert, but only in this now-deleted file).
- `test_wait_for_download_heartbeat_optional` — tests the OTHER function,
  `_wait_for_download` (not in scope, see 1.3), but specifically tests its optional
  heartbeat-logging behavior that was ALSO added for observability; since the whole
  file is being deleted per the task's instruction and this function is not otherwise
  under test elsewhere, delete it with the file.
- `test_checkbox_js_fallback_succeeds` — tests `_ensure_large_mode_checkbox`'s
  escalation log wording indirectly (checks for "ticked >1000 automatic-download mode
  via js", which still appears post-revert) and its JS-fallback ticking, which also
  still exists post-revert. However this duplicates coverage of a function whose
  wording changed (section 1.6) and whose caller contract changed (section 1.7); delete
  with the file rather than partially salvage, since the task says use judgement and
  default to deleting the file.
- `test_checkbox_all_methods_fail_returns_false` — tests `_ensure_large_mode_checkbox`
  returning `False`; same reasoning — delete with the file.
- `test_large_path_raises_when_mode_not_enabled` — tests the OLD call-site guard in
  `_browser_export_chunk` (`if not _ensure_large_mode_checkbox(...): raise ...`) that
  section 1.7 removes. This directly contradicts the reverted behavior (large path must
  now proceed regardless) — must be deleted.

Action: delete `tests/test_scc_wait.py` in full (the file itself, not just its
contents).

### 5.2 `tests/test_polygon_workflow.py` — remove 3 tests, keep the rest

Remove these 3 tests from the `FailurePathTests` class (named with "edge_retry" /
"degrade", testing the Oct-4 Edge-batch-retry feature removed in section 2):
- `test_edge_batch_failed_to_fetch_is_retried_then_resolves` — asserts an Edge
  `TransportError("...Failed to fetch")` is retried and the batch eventually resolves
  (`session.calls == 2`). This directly tests `_resolve_batch_with_edge_retry`, which
  is deleted; post-revert, `fetch()` calls `_resolve` once and the error propagates, so
  this test's assertions no longer hold.
- `test_edge_batch_failed_to_fetch_degrades_but_other_batch_resolves` — asserts a
  failed Edge batch degrades to `errored` while a sibling batch still resolves. Tests
  the "degrade to unresolved and continue" behavior being removed; post-revert, that
  first batch's `TransportError` propagates out of `fetch()` immediately instead.
- `test_mcscli_transport_error_is_not_retried` — asserts an mcscli `TransportError`
  propagates with exactly 1 call (no retry). This test's assertion technically still
  holds after the revert (mcscli was never retried, before or after Oct 4), but it was
  added as the Oct-4 feature's "control" test proving mcscli stayed unaffected by the
  new Edge-only retry wrapper. Since `_resolve_batch_with_edge_retry` (the thing it was
  proving mcscli was exempt from) no longer exists, this test is testing a distinction
  that no longer exists in the code (both transports now behave identically — one
  direct `_resolve` call, no wrapper) — remove it per the task's explicit "3 offline
  tests added for the Oct 4 edge-retry/degrade feature" framing (this is the 3rd).

Leave every other test in this file untouched, including (non-exhaustive — do not
remove any of these): `EdgePreflightTests.*`, `InputAndWorkbookTests.*`,
`FailoverTests.test_failed_mcscli_probe_switches_to_edge_once`,
`GuardTests.test_network_lookup_is_sequential_and_deduplicated`,
`FailurePathTests.test_partial_g1_recovery_still_revalidates_mass_invalid_ids`,
`FailurePathTests.test_non_finite_gps_becomes_error_not_outside`,
`FailurePathTests.test_invalid_cache_is_refetched`,
`FailurePathTests.test_incomplete_result_cannot_be_written_or_verified`,
`FailurePathTests.test_mcscli_local_io_failures_are_transport_errors`,
`FailurePathTests.test_failed_edge_probe_is_closed`,
`TemplateIntegrationTests.*`, and `PreflightAuthGateTests.*`.

IMPORTANT CAVEAT about `PreflightAuthGateTests`: this class (methods
`test_preflight_populates_auth_gate_mcscli` and
`test_preflight_probe_exception_does_not_break_preflight`) tests
`run_daily.preflight()`'s `auth_gate` key — the exact thing section 3.1 removes. The
task's explicit instruction for this file says only "remove the 3 offline tests added
for the Oct 4 edge-retry/degrade feature... leave all other tests untouched," and does
NOT list `PreflightAuthGateTests` among the files/tests to touch for the Oct-8
auth-gate revert (that revert's test cleanup is scoped by the task only to
`test_web_controller.py`'s 4 `auth_gate` tests, not to this file). However, after
section 3.1's revert, `run_daily.preflight()` no longer sets `checks["auth_gate"]` at
all, so both `PreflightAuthGateTests` methods will fail (`KeyError` on
`run.summary["preflight"]["auth_gate"]`).
  Resolution: remove `PreflightAuthGateTests` (both methods and the class) from
  `tests/test_polygon_workflow.py` as a necessary consequence of the `run_daily.py`
  revert in section 3.1, even though the task's bullet list for this file did not name
  it explicitly — leaving it in place would leave the test suite red for a reason
  unrelated to a real regression (the class exists solely to test the removed
  `auth_gate` feature in `run_daily.py`, mirroring the 4 analogous tests the task does
  call out in `test_web_controller.py`). Flag this to the user/implementer: if this
  deviation is unwanted, the alternative is leaving `PreflightAuthGateTests` in place
  and accepting 2 failing tests in `tests/test_polygon_workflow.py` after the revert.

Net removals from `tests/test_polygon_workflow.py`: the 3 named `FailurePathTests`
methods above, plus the `PreflightAuthGateTests` class (2 methods) for the reason
just given — 5 test functions total. Also remove the now-unused `run_daily` and
`siw_client` imports from this file's header ONLY IF nothing else in the file still
uses them — `siw_client` is still used by `FailoverTests` (via
`siw_client.McsCliTransport`/`EdgeInPageTransport`/`TransportError`) and
`FailurePathTests.test_mcscli_local_io_failures_are_transport_errors`
(`siw_client.McsCliTransport`, `siw_client.tempfile`, `siw_client.subprocess`), so keep
the `import siw_client` line. `run_daily` becomes unused once `PreflightAuthGateTests`
is removed (it is only referenced inside that class) — remove the `import run_daily`
line from the top of the file in that case.

### 5.3 `tests/test_web_controller.py` — remove 4 tests, keep the rest

Remove these 4 tests (all reference `auth_gate`, testing `_auth_gate_safe()` /
`preflight_state()`'s `auth_gate` key removed in section 4):
- `test_auth_gate_present_with_all_existing_keys`
- `test_auth_gate_classifies_mcscli`
- `test_auth_gate_classifies_edge_fallback_on_transport_error`
- `test_auth_gate_unknown_on_generic_exception_never_raises`

Also remove the `_FakeMcscliSession` class and the `_import_siw_client()` helper
function that precede these 4 tests (both exist solely to support them, and nothing
else in the file uses `_FakeMcscliSession` or `_import_siw_client`).

Leave every other test in this file untouched, including (non-exhaustive):
`test_gate_accepts_publishable`, `test_gate_rejects_partial_even_when_stages_ok`,
`test_gate_rejects_blocked`, `test_partial_run_is_not_fully_publishable`,
`test_partial_run_is_downloadable`, `test_full_run_is_downloadable`,
`test_blocked_run_is_not_downloadable`,
`test_partial_not_downloadable_if_workbook_stage_failed`,
`test_gate_rejects_failed_verification`, `test_gate_rejects_missing_readback`,
`test_sources_from_summary_reports_counts_and_status`,
`test_sources_reports_error_stage`, `test_sources_pending_when_stage_absent`,
`test_sources_reports_running_stage`,
`test_sources_running_via_null_ok_without_status`,
`test_publish_refuses_when_blocked`, `test_publish_copies_when_publishable`,
`test_read_summary_handles_missing`, `test_verification_ok_requires_flag`.

Note: `preflight_state()` itself is still called indirectly by nothing else in this
file after the 4 removals (no remaining test calls `rc.preflight_state()` or
`rc._auth_gate_safe()`), so no further cleanup is needed in this file beyond deleting
the 4 tests and the 2 support objects above.

---

## 6. Post-edit verification checklist for the implementer

1. Apply all edits in sections 1-4.
2. Delete `tests/test_scc_wait.py`.
3. Edit `tests/test_polygon_workflow.py` and `tests/test_web_controller.py` per
   section 5.
4. Run `py -3.13 -m pytest tests -q` from the workspace root.
5. Confirm no test imports or references a deleted symbol: `SCC_DOWNLOAD_TIMEOUT_S`,
   `SCC_WAIT_HEARTBEAT_S`, `_page_is_dead`, `EDGE_BATCH_RETRY_ATTEMPTS`,
   `EDGE_BATCH_RETRY_BACKOFF_S`, `_is_failed_to_fetch`,
   `_resolve_batch_with_edge_retry`, `_auth_gate_safe`, or `checks["auth_gate"]` /
   `state["auth_gate"]`. A leftover reference will surface as a collection error or
   `AttributeError`/`KeyError` when pytest runs.
6. Confirm the full suite passes (or, if the `PreflightAuthGateTests` deviation in 5.2
   was deliberately NOT taken, confirm exactly those 2 tests are the only failures and
   that this was the user's informed choice).
