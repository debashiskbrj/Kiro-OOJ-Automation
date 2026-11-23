r"""Shared filesystem paths for the OOJ automation.

Single source of truth for paths that several lanes need to agree on. Centralising
them here keeps the lanes consistent and makes the location overridable without
editing code (set the matching environment variable before launching a run).

Portable for every associate: nothing here is tied to one person's laptop. On the
owner's laptop (KABIDEBA) every value resolves to exactly the path used before.
"""
from __future__ import annotations

import getpass
import os
from pathlib import Path

_HOME = Path.home()
_LOCALAPPDATA = Path(os.environ.get("LOCALAPPDATA") or (_HOME / "AppData" / "Local"))

# Windows login alias of whoever runs the automation (e.g. "kabideba").
try:
    USER_ALIAS = (os.environ.get("USERNAME") or getpass.getuser() or "").strip().lower()
except Exception:  # never let alias lookup break an import
    USER_ALIAS = ""

# builder-mcp (Amazon Toolbox) — installed per user under %LOCALAPPDATA%\Toolbox\bin.
BUILDER_MCP = os.environ.get("OOJ_BUILDER_MCP") or str(_LOCALAPPDATA / "Toolbox" / "bin" / "builder-mcp.exe")


def _default_edge_profile() -> Path:
    # The owner's long-standing, already signed-in profile location is kept as-is so
    # his runs are unchanged. Everyone else gets a per-user profile under LOCALAPPDATA.
    legacy = _HOME / "Desktop" / "My Files" / "My Automation" / "TT_Automation" / "edge_profile"
    if legacy.exists():
        return legacy
    return _LOCALAPPDATA / "OOJ_Automation" / "edge_profile"


# The signed-in Edge automation profile shared by the browser lanes (PNAMA/DSR) and the
# SIW Edge fallback. Overridable via OOJ_EDGE_PROFILE.
EDGE_PROFILE = Path(os.environ.get("OOJ_EDGE_PROFILE") or _default_edge_profile())

# A brand-new associate has no profile folder yet. Create the empty folder so the
# preflight check passes; Edge fills it on the first sign-in (done automatically by the
# browser sign-in check at Start).
try:
    EDGE_PROFILE.mkdir(parents=True, exist_ok=True)
except OSError:
    pass
