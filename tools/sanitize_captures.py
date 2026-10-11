r"""Safe, daily sanitizer for output-corrupting scratch files.

Run at server startup and on restart. It removes ONLY the files that could make a run
pick up stale/partial data as if it were a fresh source result:

  1. Everything inside every ``_browser_downloads`` directory under ``tools/captures``.
     Each lane re-snapshots that directory before it searches, so a leftover CSV from a
     previous/crashed run there could be mistaken for the current download.
  2. Partial/temporary files (``.crdownload``, ``.tmp``, ``.partial``, ``.tmp.xlsx`` and
     dot-prefixed ``.<name>.<uuid>.tmp`` atomic-write sidecars) anywhere under
     ``tools/captures``.
  3. Stale QuickSight export leftovers in the user's ``~/Downloads`` that match the exact
     export name patterns this project uses (PNAMA/DSR), which the PNAMA lane can pick up
     as a fallback location.

STRICT SAFETY MODEL — the sanitizer can only ever touch:
  * paths inside ``tools/captures/`` (verified by resolved-path containment), and
  * files in ``~/Downloads`` whose names match the project's own export patterns.
It NEVER touches source code, docs, the template, tests, steering, git, or a live run's
own output files (only scratch/partial artifacts). Every deletion is guarded so a locked
or vanished file is skipped, never raised.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

HERE = Path(__file__).resolve().parent
CAPTURES = HERE / "captures"
DOWNLOADS = Path.home() / "Downloads"

# Files that are never a finished result — safe to delete wherever they appear.
PARTIAL_SUFFIXES = (".crdownload", ".tmp", ".partial")
# Atomic-write sidecars look like ".<name>.<hex>.tmp" or ".<name>.<hex>.tmp.xlsx".
_SIDECAR_RE = re.compile(r"^\..+\.[0-9a-f]{6,}\.tmp(\.[a-z0-9]+)?$", re.IGNORECASE)
# Stale export leftovers this project creates that the PNAMA lane may read from ~/Downloads.
_DOWNLOAD_LEFTOVER_RE = re.compile(
    r"^(SWA_repeat_Prox_Pnam|DSR_Raw_data|SearchResults).*\.(csv|xlsx|crdownload|tmp|partial)$",
    re.IGNORECASE,
)


def _is_within(path: Path, root: Path) -> bool:
    """True only if ``path`` resolves to a location inside ``root`` (no escaping)."""
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def _unlink(path: Path, log: Callable[[str], None]) -> int:
    try:
        path.unlink()
        return 1
    except (OSError, PermissionError):
        return 0  # locked or already gone — never fail the sanitize pass


def _is_partial(path: Path) -> bool:
    name = path.name
    return path.suffix.lower() in PARTIAL_SUFFIXES or bool(_SIDECAR_RE.match(name))


def sanitize(log: Callable[[str], None] = print) -> dict:
    """Remove output-corrupting scratch. Returns a small count summary."""
    removed_downloads = 0
    removed_partials = 0
    removed_leftovers = 0

    if CAPTURES.exists():
        # 1 + 2: walk captures once. Clear _browser_downloads contents entirely, and
        # remove partial/temp files everywhere else under captures.
        for entry in CAPTURES.rglob("*"):
            if not entry.is_file():
                continue
            if not _is_within(entry, CAPTURES):
                continue  # defensive: never act outside captures
            in_browser_downloads = "_browser_downloads" in entry.parts
            if in_browser_downloads:
                removed_downloads += _unlink(entry, log)
            elif _is_partial(entry):
                removed_partials += _unlink(entry, log)

    # 3: stale project export leftovers in ~/Downloads (name-pattern gated).
    if DOWNLOADS.exists():
        for entry in DOWNLOADS.glob("*"):
            if entry.is_file() and _DOWNLOAD_LEFTOVER_RE.match(entry.name):
                if _is_within(entry, DOWNLOADS):
                    removed_leftovers += _unlink(entry, log)

    summary = {
        "browser_downloads": removed_downloads,
        "partial_temp": removed_partials,
        "download_leftovers": removed_leftovers,
    }
    total = removed_downloads + removed_partials + removed_leftovers
    if total:
        log(f"sanitize: removed {total} scratch file(s) "
            f"(browser_downloads={removed_downloads}, partial/temp={removed_partials}, "
            f"~/Downloads leftovers={removed_leftovers})")
    else:
        log("sanitize: nothing to remove; capture dirs are clean")
    return summary


if __name__ == "__main__":
    sanitize()
