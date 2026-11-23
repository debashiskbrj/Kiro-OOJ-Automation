"""Owner, author and support contact for the OOJ Process Automation.

OOJ Process Automation is conceived, designed, developed and owned by KABIDEBA
(SWA India - Automation). For any problem, question or correction, contact the owner.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PRODUCT = "OOJ Process Automation"
OWNER = "KABIDEBA"
OWNER_TEAM = "SWA India - Automation"
CONTACT_EMAIL = "kabideba@amazon.com"
CONTACT_PHONETOOL = "https://phonetool.amazon.com/users/kabideba"


def version() -> str:
    try:
        return (ROOT / "VERSION").read_text(encoding="utf-8").strip() or "unknown"
    except OSError:
        return "unknown"


def banner_lines() -> list[str]:
    return [
        "=" * 64,
        f"  {PRODUCT}   v{version()}",
        f"  Owner & author: {OWNER}  ({OWNER_TEAM})",
        f"  Help / problems / corrections: {CONTACT_EMAIL}",
        f"  Phonetool: {CONTACT_PHONETOOL}",
        "=" * 64,
    ]


def support_text() -> str:
    return (f"For help, problems or corrections contact the owner {OWNER}: "
            f"{CONTACT_EMAIL}  ({CONTACT_PHONETOOL})")
