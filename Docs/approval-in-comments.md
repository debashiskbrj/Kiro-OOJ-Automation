# Approval in Comments (Working Sheet column AI)

Per ticket, reads the SIM-T comments and records whether a CRM or Site Lead approved.
Code: `tools/tickets_pull.py` (`comment_labels`, `scan_approval`, `scan_approvals`).

Last reviewed: 2026-11-16.

## Where it appears

- Working Sheet **AI (column 35)** "Approval in Comments" — a literal value per row.
- Tech_Data **column L** (header "Put the Quip data " is left as is).
- `tech_data.csv` column `approval_in_comments`.
- Never used for TID logic or the Remark.

## Values

| Value | When |
|---|---|
| `Approved` | A CRM or Site Lead wrote an approval phrase |
| `Site clear` | A CRM or Site Lead wrote "site clear" / "site clearance" / "site cleared" |
| `Site clear, Approved` | Both (order = first seen) |
| `Need check manually (<alias>, …)` | Only people who are NOT a CRM/Site Lead approved, or their Phonetool title could not be read (`unknown` = comment had no author) |
| blank | No approval wording, or the ticket's comments could not be read |

A CRM/Site Lead approval always wins over other people's approvals on the same ticket.

## Approval phrases (case-insensitive, per sentence)

approve / approved / approval / approvals / approving · go ahead · proceed · carry on ·
no objection · fine to (…) · ok to mark / okay to mark · mark undel / mark undelivered /
marking undel / mark as undelivered · mark valid / mark as valid · agree / agreed ·
confirmed · permitted · allowed · green light · you may (…) · site clear / clearance /
cleared.

**"continue" does not count** (operator decision).
"disapproved", "unapproved", "disagree" never count (whole-word match).

## When a phrase does NOT count

- **Within 40 characters before it, in the same sentence:** not, no, never, cannot,
  can't, don't, doesn't, didn't, won't, isn't, without, awaiting, waiting, wait, pending,
  need/needs/needed, require/requires/required, request/requested/requesting, seek,
  seeking, ask/asked/asking, unless, until, if, whether, "refer … for".
- **Within 25 characters after it:** pending, awaited, awaiting, required, needed,
  denied, rejected, declined, withdrawn, revoked, not yet / given / granted / received /
  done / provided.
- **The sentence ends with "?".**

Known gap: "no" before the phrase blocks it, so "No issue, approved" is left blank
("No objection" itself counts).

## Who counts as CRM / Site Lead

Each approving comment's author alias (`comments[].author.value`) is looked up **once per
run** on Phonetool (`ReadInternalWebsites` → `phonetool.amazon.com/users/<alias>` →
`job_title`). A title counts when it matches `\bCRM\b`, "Channel Relationship Manager",
"Site Lead" or `\bSL\b`.

Verified titles: `rccharpe` "CRM", `vddeepak` "CRM - EDSP", `zakakash` "Channel
Relationship Manager", `pkaushal` "Site Lead". Live check: ticket D572189109 → `Approved`
(vddeepak).

## Cost

One extra `get-ticket` call per ticket plus one Phonetool lookup per distinct approving
author, so the SIM-T step is slower than before. The run log prints
`approval authors: N looked up on Phonetool, M CRM/Site Lead[, K lookup(s) failed]`.

## Tests

`tests/test_approval_scan.py` (phrases, negations, CRM filter, manual-check label,
one lookup per author, CSV round trip).
