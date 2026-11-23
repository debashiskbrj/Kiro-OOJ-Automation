"""Build the shareable zip of the OOJ Process Automation (owner-only tool).

Owner & author: KABIDEBA. Run "Make Share Zip.bat" (or this script) on the owner's laptop.
Output: Desktop\\OOJ_Automation_by_KABIDEBA_v<version>_<date>.zip

Only project source files are packed. Never packed: run data and captures (real TIDs),
resolve reports, health reports, the git folder, Kiro / NeuroTrace / agent files
(NeuroTrace holds a session token), caches, and this builder itself.
"""
from __future__ import annotations

import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
sys.path.insert(0, str(TOOLS))
import about  # noqa: E402

FOLDER = "OOJ Automation by KABIDEBA"
EXCLUDE_PREFIXES = (
    ".git/", ".neurotrace/", ".kiro/", ".github/", ".agents/", ".pytest_cache/", ".vscode/",
    "tools/captures/", "tools/resolve_runs/", "tools/health/", "tools/metrics/", "tools/output/",
)
EXCLUDE_FILES = {"AGENTS.md", ".gitignore", "tools/build_share_zip.py", "Make Share Zip.bat"}
CRLF_SUFFIXES = (".bat", ".txt")


def _candidate_files() -> list[str]:
    try:
        out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                             cwd=ROOT, capture_output=True, text=True, check=True,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        return sorted(set(out.splitlines()))
    except Exception:
        return sorted(p.relative_to(ROOT).as_posix() for p in ROOT.rglob("*") if p.is_file())


def included(rel: str) -> bool:
    if rel in EXCLUDE_FILES or rel.startswith(EXCLUDE_PREFIXES):
        return False
    parts = rel.split("/")
    if "__pycache__" in parts or "captures" in parts:
        return False
    return not rel.endswith((".pyc", ".log", ".tmp", ".db"))


def build(dest_dir: Path | None = None) -> Path:
    dest_dir = dest_dir or (Path.home() / "Desktop")
    name = f"OOJ_Automation_by_KABIDEBA_v{about.version()}_{datetime.now():%Y%m%d}.zip"
    target = dest_dir / name
    files = [rel for rel in _candidate_files() if included(rel) and (ROOT / rel).is_file()]
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for rel in files:
            data = (ROOT / rel).read_bytes()
            if rel.endswith(CRLF_SUFFIXES):
                data = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
            zf.writestr(f"{FOLDER}/{rel}", data)
    return target


def main() -> int:
    for line in about.banner_lines():
        print(line)
    target = build()
    with zipfile.ZipFile(target) as zf:
        names = zf.namelist()
    print(f"  Files packed: {len(names)}")
    print(f"  Zip created : {target}")
    print("  Share this zip with colleagues. They unzip it and double-click")
    print('  "Start OOJ Web.bat".')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
