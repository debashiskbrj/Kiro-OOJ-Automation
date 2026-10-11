r"""Shared filesystem paths for the OOJ automation.

Single source of truth for paths that several lanes need to agree on. Centralising
them here keeps the lanes consistent and makes the location overridable without
editing code (set the matching environment variable before launching a run).
"""
from __future__ import annotations

import os
from pathlib import Path

# The signed-in Edge automation profile shared by the browser lanes (SCC/PNAMA/DSR) and
# the SIW Edge fallback. Overridable via OOJ_EDGE_PROFILE; the default is the operator's
# current profile path (unchanged behavior when the env var is unset).
EDGE_PROFILE = Path(
    os.environ.get(
        "OOJ_EDGE_PROFILE",
        r"C:\Users\kabideba\Desktop\My Files\My Automation\TT_Automation\edge_profile",
    )
)
