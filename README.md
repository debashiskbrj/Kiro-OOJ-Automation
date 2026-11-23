# OOJ Process Automation

Automates the daily SWA India **Out-of-Jurisdiction (OOJ)** ticket process: pull tickets
from SIM-T, extract tracking IDs (TIDs), enrich each TID from Amazon internal tools, and
produce the filled Excel workbook — replacing the manual template workflow. The same local
page can also bulk-resolve tickets and post comments, each behind an explicit confirmation.

Built by **KABIDEBA**, SWA India. Runs **locally** on the operator's Windows laptop, is
**free**, and uses **no AI/LLM** in the run path.

Last reviewed: 2026-11-18. Version: see `VERSION`.

**Owner & author: KABIDEBA.** Help, problems or corrections: kabideba@amazon.com
([Phonetool](https://phonetool.amazon.com/users/kabideba)). Colleagues: start with
**`READ ME FIRST.txt`**.

---

## Sharing with colleagues

- Owner runs **`Make Share Zip.bat`** → `Desktop\OOJ_Automation_by_KABIDEBA_v<version>_<date>.zip`
  (`tools/build_share_zip.py`). Never packs run data, reports, git, Kiro/NeuroTrace files or tokens.
- Colleague unzips and double-clicks **`Start OOJ Web.bat`** (finds/installs Python, auto-installs
  packages, opens the page). No Kiro/LLM needed. Each person runs it under their own login.
- **Weekly health check** (`tools/health_check.py`): runs from the launcher every 7 days and after a
  new version; also `Run Health Check.bat`. Safe auto-fixes + template check + self-test; report in
  `tools/health/` to send to the owner.
- Paths are per user (`tools/ooj_paths.py`): builder-mcp under `%LOCALAPPDATA%\Toolbox\bin`, Edge
  profile = the owner's existing folder if present, else `%LOCALAPPDATA%\OOJ_Automation\edge_profile`.
- When you change anything: bump `VERSION`, run tests, rebuild the zip, send it out.

---

## Quick start

1. Prerequisites (once): Python 3.13, Midway (`mwinit -o`), the automation Edge profile
   signed in to the PNAMA/DSR dashboards, your normal browser signed in to SCC.
   Details: `Docs/user-guide.md`.
2. Double-click **`Start OOJ Web.bat`** — the page opens at http://127.0.0.1:8000/
   (if it is already running, the launcher just reopens it instead of starting a second copy).
3. **Grant Midway access** → tick **Approve ADS upload** → **Start run**.
4. When the run pauses at SCC, export SCC's CSV by hand and upload it in the SCC card.
5. When it finishes, **Download filled workbook** (also saved to your Desktop as
   `Templete_OOJ Process_<Month>_KABIDEBA1_<stamp>.xlsx`).

---

## The pipeline (in order)

SIM-T tickets (+ approval scan) → **pause for manual SCC upload** → SCC → EagleEye ∥
PNAMA → DSR → GCRS → Polygon → ADS → fill template → Excel recalc → verify (read-back) →
download.

Per-stage details: `Docs/pipeline-reference.md`.

## Page sections

1. Midway access · 2. Start today's run · 3. Progress (8 cards, SCC upload / Stop / Rerun)
· 4. Result · **5. Resolve ticket** · **6. Post comments (optional)**.

---

## Documentation map

| Doc | What it covers | For |
|---|---|---|
| `README.md` | Overview, entry point, ground rules | Everyone |
| `Docs/user-guide.md` | Daily steps on the web page, prerequisites, timing | Operator |
| `Docs/SOP-OOJ-Automation.md` | Standard operating procedure + checklist | Operator |
| `Docs/troubleshooting.md` | Known failures and fixes | Both |
| `Docs/resolve-tickets.md` | Section 5: bulk resolve rules, outputs, safety | Both |
| `Docs/approval-in-comments.md` | Column AI: approval phrases, CRM/Site Lead rule | Both |
| `Docs/remark-rules.md` | Working Sheet column P (Remark) formula logic | Both |
| `Docs/report-spec.md` | Final report columns | Both |
| `Docs/pipeline-reference.md` | Each source/stage, data retention | Both |
| `Docs/web-runner.md` | Web app internals: phases, endpoints, gate, retention | Maintainer |
| `Docs/developer-guide.md` | Code layout, how to change things, tests | Maintainer |
| `Docs/project-structure.md` | Where every file lives | Maintainer |
| `Docs/polygon-checker.md` | GCRS polygon / jurisdiction checker | Maintainer |
| `Docs/architecture.md` | Design notes | Maintainer |
| `Docs/implementation.md` | Build history / current state | Maintainer |
| `Docs/context.md` | Working notes and background | Maintainer |
| `Docs/access-status.md` | Per-source access lane status | Maintainer |
| `Docs/edge-cases.md` | Edge cases learned | Maintainer |
| `Docs/PRD-OOJ-Automation.md` | Product requirements | Maintainer |
| `Docs/changes.md` | Changelog (newest first) | Maintainer |
| `Docs/dmaic-project.md` | Lean Six Sigma DMAIC write-up: charter, SIPOC, Pareto, control plan | Owner / reviewers |
| `.kiro/steering/ooj-instructions.md` | Standing operator rules for AI assistants | Maintainer |

---

## Ground rules

- **Reads** Amazon systems for the pipeline. **Writes** only in Resolve ticket and Post
  comments, and only after the operator ticks a confirmation box and confirms. Never
  pushes git without an explicit request.
- **Fresh data only.** Each fresh Start clears every source cache; at server start/restart
  `tools/sanitize_captures.py` clears partial downloads and browser scratch.
- **Retention.** Run folders and Desktop output workbooks older than **3 days** are deleted
  at each fresh Start; newer ones are kept. Resolve reports (`tools/resolve_runs/`) are kept.
- **Don't break what works.** Changes must be additive; run
  `py -3.13 -m pytest tests -q` (all must pass) before and after any change.
- Runs as the operator with the operator's own Midway/SSO sessions.
- KABIDEBA credit stays in the page header/footer and the server banner.

Standing rules in full: `.kiro/steering/ooj-instructions.md`.
