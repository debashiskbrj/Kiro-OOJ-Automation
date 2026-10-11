# Implementation Plan — GCRS resilience (Feature A + Feature B)

Scope: ADD two resilience features to the working, read-only OOJ automation. Do NOT change any
existing pipeline behavior except the two additive features below. Baseline is 64 passing tests
(`py -3.13 -m pytest tests -q`); the end state must be >= 64 passing. Edit files in place (no worktree).

## Decisions settled during exploration (grounded in code)

1. **Feature B continues after a bounded-retry failure (does NOT fail the whole lane).**
   Evidence from reading the actual return contract:
   - `tools/gcrs_mcscli.py` `fetch()` already returns `(ordered_rows, not_found, errored)`. The
     `errored` list is explicitly for TIDs that could not be resolved but are NOT confirmed invalid
     (`errored = invalid_format + [tid ... not in rows and not in invalid ...]`).
   - `tools/tid_polygon_checker.py` `run_boundary_check()` already consumes `errored` and row-aligns
     a status for each one: `status = f"{ERROR_PREFIX}SIW unresolved after degraded/poison guards"`
     (ERROR_PREFIX = "Error — "). So an errored TID produces a valid, row-aligned polygon row — the
     polygon row-count contract in `run_daily.lane_polygon` (`len(rows) != len(tech_rows)` guard) is
     preserved.
   - `tools/run_daily.py` `lane_gcrs()` records `errored` as a count and writes `gcrs_errored.txt`;
     it never raises on errored TIDs. `lane_ads()` consumes only resolved `gcrs_rows` and already
     writes `ads_missing.csv` for TIDs with no row — a smaller resolved set is already handled.
   - Therefore, degrading a hopelessly-failing Edge batch to `errored` and CONTINUING the remaining
     batches matches how the lane already represents unresolved TIDs. It does NOT violate any
     all-or-nothing count contract (polygon aligns by physical row; ADS aligns by returned row).
     Correctness is preserved: no fabricated rows, no silently dropped data — the TIDs are recorded
     as errored/unresolved exactly as the existing degraded-window path records them.
   - This replaces the current behavior where one batch's `TransportError` propagates out of `fetch()`
     and fails the ENTIRE gcrs stage (blanking gcrs+polygon+ads, as in run 2026-10-04). The retry is
     attempted FIRST; continue-after-degrade is only the last resort after retries are exhausted.

2. **Feature B retry wraps the per-batch `_resolve()` call in `fetch()`'s loop, Edge transport only.**
   The failing call stack is `fetch` loop -> `_resolve` -> `_grbi` -> `session.post_json` which raises
   `TransportError("Edge SIW grbi request failed: ... Failed to fetch")`. Wrapping at the per-batch
   `_resolve()` call keeps the existing internal guards (#G1/#G2/#G3/#G3b) untouched and retries the
   WHOLE batch, which is what a `Failed to fetch` (dead in-page fetch/CDP round-trip) needs. mcscli-path
   behavior is untouched: retry only engages when `session.transport_name == "edge"`.

3. **Feature A probe is the EXISTING read-only probe, called ONCE at preflight, fully try/excepted.**
   Reuse `siw_client.FailoverSiwSession(mode="auto").ensure_ready()` which performs the real read-only
   `POST /gpsn` probe and selects mcscli (healthy) or Edge (fragile fallback). Classify from
   `session.transport_name`: `mcscli` -> healthy, `edge` -> edge-fallback (fragile), exception/None ->
   unknown. Close the session immediately. This is the SAME probe the run would do (one probe, no extra
   SIW load). Wrapped in try/except so it can never crash preflight. NOTE: `ensure_ready()` in `edge`
   selection opens the Edge profile; to avoid opening it at preflight (and to respect the
   `edge_profile_processes` hard error), the probe uses `mode="mcscli"` so it ONLY probes mcscli and
   never activates Edge — a mcscli probe failure is classified as `edge-fallback` (the run WILL fall back
   to Edge) without actually launching Edge at preflight. This is the cheapest reliable signal and never
   touches the single-Edge-profile lock.

4. **Browser-lane sign-in (Feature A check 3): recorded as "unknown — verified when lane runs".**
   A reliable read-only sign-in check for SCC/PNAMA/DSR requires opening the automation Edge profile,
   which `preflight()` already treats as a hard error (`edge_profile_processes`) and which would take the
   single-profile lock. So we do NOT open it. We record the cheap signal we DO have (EDGE_PROFILE exists,
   already in `checks["edge_profile"]`) and mark sign-in `unknown` with a reason string.

## Exact additive keys

`run.summary["preflight"]` (new nested key only; all existing keys unchanged):
```
checks["auth_gate"] = {
    "midway_session_valid": <bool>,          # mirrors existing checks["midway_session_valid"]
    "gcrs_transport": "mcscli" | "edge-fallback" | "unknown",
    "browser_signin": "unknown",             # SCC/PNAMA/DSR — verified when lane runs
    "browser_signin_reason": "not checked at preflight to avoid opening the single Edge automation profile",
}
```

`run_controller.preflight_state()` (new top-level ADDITIVE key only; no existing key removed/renamed):
```
"auth_gate": {
    "midway_session_valid": <bool>,          # = existing midway_ok
    "gcrs_transport": "mcscli" | "edge-fallback" | "unknown",
    "browser_signin": "unknown",
    "browser_signin_reason": "...",
}
```

## Exact constants (near BATCH_* in tools/gcrs_mcscli.py)

```
# Per-batch Edge retry for a dead in-page fetch (TypeError: Failed to fetch), observed on the
# 2026-10-04 run where one /grbi batch over Edge threw "Failed to fetch" and failed the whole
# GCRS lane (blanking gcrs+polygon+ads). mcscli path is unaffected. See #G8.
EDGE_BATCH_RETRY_ATTEMPTS = 3            # total attempts for a single Edge /grbi batch
EDGE_BATCH_RETRY_BACKOFF_S = (2, 4, 6)  # backoff before attempts 2 and 3 (len == attempts - 1 + tail)
```

## Ordered items

- [ ] 1. Feature B — add the retry constants and per-batch Edge retry/degrade in `tools/gcrs_mcscli.py`.
      Add `EDGE_BATCH_RETRY_ATTEMPTS` and `EDGE_BATCH_RETRY_BACKOFF_S` right after `BATCH_EDGE` with the
      comment referencing the 2026-10-04 Failed-to-fetch run and #G8. In `fetch()`'s per-batch `while`
      loop, replace the direct `_resolve(chunk, ...)` call with a helper that: if transport is `edge`,
      attempts `_resolve` up to `EDGE_BATCH_RETRY_ATTEMPTS` times, catching `TransportError` whose message
      contains "Failed to fetch" (or an empty/degraded window), sleeping the backoff between attempts; on
      the final failed attempt, log a WARNING and add the chunk's normalized TIDs to the `invalid`-less
      "errored" path by leaving them unresolved (they fall into `errored` via the existing
      `fetch()` final computation: `not in rows and not in invalid and not in invalid_format`), then
      CONTINUE the loop. For mcscli transport, call `_resolve` once exactly as today (no retry, no catch).
      Keep batches sequential; do not change BATCH_EDGE/BATCH_MCSCLI or the mcscli path.
      Files: tools/gcrs_mcscli.py
      Verify: `py -3.13 -m pytest tests/test_polygon_workflow.py -q` passes; new Feature B tests (item 3) pass.

- [ ] 2. Feature A — add the read-only auth gate in `tools/run_daily.py` `preflight()` and surface it in
      `tools/web/run_controller.py` `preflight_state()`.
      In `preflight()`, after `checks` is built and the existing require_midway gate logic runs, compute a
      best-effort `gcrs_transport` classification using `siw_client.FailoverSiwSession(mode="mcscli")`:
      try `session.ensure_ready()` then `session.transport_name` -> "mcscli"; on `TransportError` ->
      "edge-fallback"; on any other exception -> "unknown"; always `session.close()` in a finally. Wrap
      the whole block in try/except so it can NEVER raise out of preflight. Add `checks["auth_gate"]` with
      the four keys above. Log a human-readable WARNING line when `gcrs_transport` is `edge-fallback` or
      `unknown` (e.g. "WARNING: GCRS will use the fragile Edge fallback (mcscli SIW probe failed) ..."),
      and an INFO line when `mcscli`. Do NOT hard-block on this (existing require_midway gate behavior is
      unchanged). In `run_controller.preflight_state()`, add a top-level `auth_gate` key computed with the
      SAME `mode="mcscli"` probe wrapped in a `_auth_gate_safe()` helper (mirrors `_environment_checklist_safe`
      pattern) returning the four-key dict; on any failure return `{"midway_session_valid": midway,
      "gcrs_transport": "unknown", "browser_signin": "unknown", "browser_signin_reason": "..."}`.
      Do not remove/rename any existing preflight_state key.
      Files: tools/run_daily.py, tools/web/run_controller.py
      Verify: `py -3.13 -m pytest tests/test_web_controller.py -q` passes; new Feature A tests (item 4) pass.

- [ ] 3. Add offline Feature B tests in `tests/test_polygon_workflow.py` (follow the existing
      `FailurePathTests` fake-session style; patch `gcrs_mcscli.time.sleep`).
      Cover: (a) an Edge-transport fake session whose first N-1 `grbi` posts raise
      `TransportError("Edge SIW grbi request failed: ... Failed to fetch")` and the Nth succeeds —
      assert the batch is retried and rows resolve, with `errored == []`; (b) an Edge session that raises
      "Failed to fetch" on EVERY attempt for one batch but succeeds on the next batch — assert the failed
      batch's TIDs end up in `errored`, the following batch resolves, and `fetch()` does NOT raise;
      (c) an mcscli-transport fake session that raises a `TransportError` — assert it is NOT retried
      (propagates) to prove mcscli-path behavior is unchanged. Fake session exposes `transport_name` and
      `ensure_ready` so `_batch_size_for` picks the right size.
      Files: tests/test_polygon_workflow.py
      Verify: `py -3.13 -m pytest tests/test_polygon_workflow.py -q` — all pass, new tests included.

- [ ] 4. Add offline Feature A tests in `tests/test_web_controller.py` (no network; patch
      `siw_client.FailoverSiwSession`).
      Cover: (a) `preflight_state()` includes an `auth_gate` key with the four sub-keys and all existing
      keys still present (assert `midway_ok`, `environment_checklist`, etc. unchanged); (b) when the
      patched session's `ensure_ready` succeeds and `transport_name == "mcscli"`, `gcrs_transport == "mcscli"`;
      (c) when `ensure_ready` raises `TransportError`, `gcrs_transport == "edge-fallback"`; (d) when it
      raises a generic Exception, the gate degrades to `gcrs_transport == "unknown"` and `preflight_state()`
      still returns (never raises). Add a parallel `run_daily.preflight` test (patch the session) asserting
      `run.summary["preflight"]["auth_gate"]` is populated and that a probe exception does not break preflight.
      Files: tests/test_web_controller.py (and optionally tests/test_polygon_workflow.py for the run_daily side)
      Verify: `py -3.13 -m pytest tests -q` — full suite >= 68 passed (64 baseline + new).

- [ ] 5. Document both features in `Docs/changes.md` and extend `Docs/edge-cases.md` #G8 note.
      Add a dated `changes.md` entry describing Feature A (read-only preflight auth gate / gcrs_transport
      classification) and Feature B (per-batch Edge retry + degrade-to-errored, 2026-10-04 Failed-to-fetch
      run). Append to #G8 in `edge-cases.md` that a single Edge `/grbi` batch now retries up to 3x with
      2s/4s/6s backoff and, if still failing, is recorded as errored for those TIDs while remaining batches
      continue (matching the existing errored/unresolved representation). Inclusive-language and read-only
      constraints respected.
      Files: Docs/changes.md, Docs/edge-cases.md
      Verify: `py -3.13 -m pytest tests -q` still passes (docs-only change does not break tests);
      `test_daily_cli_has_no_agentspace_option_or_handoff` and other source-scanning tests still green.

- [ ] 6. Final verification: run the full suite and confirm no regressions.
      Files: (none)
      Verify: `py -3.13 -m pytest tests -q` — all pass, count >= 64 (expected >= 68 with new tests);
      confirm no existing preflight_state/summary key was removed and the mcscli GCRS path is byte-for-byte
      unchanged.

## Review contract
The implement-and-review loop stops when
`c:\Users\kabideba\Desktop\Kiro OOJ Automation\.agents\tasks\resilience-review.json` has
jsonPath `verdict` == `APPROVED`. Any replacement workflow must keep writing that same file/value.
