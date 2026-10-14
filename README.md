# OOJ Process Automation

Automates the daily SWA India **Out-of-Jurisdiction (OOJ)** ticket process end to end:
pull tickets from SIM-T, extract tracking IDs (TIDs), enrich each TID from Amazon
internal tools, apply the remark rules, and produce a filled, downloadable Excel
workbook — replacing the manual template workflow.

Everything runs **locally** on the operator's Windows laptop. It is **free** (Python
standard library plus a few well-known packages), uses **no AI/LLM in the run path**,
and is **read-only** against Amazon source systems (it never posts to or resolves
tickets).

---

## Quick start

1. Make sure prerequisites are met (see `Docs/user-guide.md`): Python 3.13, a valid
   Midway session (`mwinit -o`), and the automation Edge profile signed in to SCC and
   the QuickSight dashboards.
2. Double-click **`Start OOJ Web.bat`** (or run `py -3.13 tools\web\server.py`).
3. Open **http://127.0.0.1:8000/** — it usually opens automatically.
4. Grant Midway access, tick **Approve ADS upload**, click **Start run**.
5. When it finishes, click **Download** — the filled workbook is also saved to your
   Desktop.

Full step-by-step: **`Docs/user-guide.md`**.

---

## What you get

- A one-click local web page that runs the whole pipeline and shows live per-source
  progress (with a % bar per tool), a live log with a **Copy log** button, per-source
  CSV download buttons, a **Stop run** button, and a gated **Download** for the final
  workbook.
- The final workbook has the Working Sheet Remark (column P) rewritten per the
  operator's rules (see `Docs/remark-rules.md`).
- A **partial** mode: if a source can't fetch, tick "Allow partial download" to still
  get the workbook with that source's sheet left blank (labelled PARTIAL).

---

## The pipeline (in order)

SIM-T tickets → EagleEye → SCC → PNAMA → DSR → GCRS → Polygon → ADS → fill template →
Excel recalc → verify (read-back) → apply Remark rules → download.

Per-stage details: **`Docs/pipeline-reference.md`**.

---

## Documentation map

| Doc | What it covers | For |
|---|---|---|
| `README.md` (this file) | Overview + entry point | Everyone |
| `Docs/project-structure.md` | Where every file lives + what goes where | Both |
| `Docs/user-guide.md` | How to run it daily, prerequisites, the web page | Operator |
| `Docs/pipeline-reference.md` | Each source/stage: what it does, how it fetches | Both |
| `Docs/developer-guide.md` | Code layout, how to change things, tests | Maintainer |
| `Docs/troubleshooting.md` | Known failures and fixes (SCC, PNAMA, ADS, Edge, ports) | Both |
| `Docs/remark-rules.md` | The Working Sheet column-P Remark logic | Both |
| `Docs/web-runner.md` | The local web app internals, endpoints, retention | Maintainer |
| `Docs/polygon-checker.md` | The local GCRS polygon/jurisdiction checker | Maintainer |
| `Docs/report-spec.md` | Final report columns | Both |
| `Docs/architecture.md` | Original design notes | Maintainer |
| `Docs/implementation.md` | Build history / current state | Maintainer |
| `Docs/context.md` | Single-source-of-truth working notes | Maintainer |
| `Docs/access-status.md` | Per-source access lane status | Maintainer |
| `Docs/edge-cases.md` | Live log of edge cases learned | Maintainer |
| `Docs/changes.md` | Live changelog | Maintainer |

---

## Safety / ground rules

- Read-only against Amazon systems. Never posts, resolves, or pushes git without
  explicit approval.
- Live data only — no stale reuse. At **server startup and on restart**, a safe
  sanitizer (`tools/sanitize_captures.py`) clears output-corrupting scratch — browser
  download folders, partial downloads (`.crdownload`/`.tmp`/`.partial`), atomic-write
  sidecars, and stale PNAMA/DSR export leftovers in `~/Downloads` — so a run can never
  pick up yesterday's or a half-finished file as fresh data. It only ever touches
  `tools/captures/` and known export names; it never touches code, docs, the template,
  tests, or a run's real output.
- Local run history under `tools/captures/` is auto-pruned after **7 days**.
- The whole thing runs as the operator, using the operator's own Midway/SSO sessions.

See `Docs/context.md` for the full standing rules.
