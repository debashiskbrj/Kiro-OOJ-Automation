"""Safe Edge automation-profile preflight for Windows.

This helper never terminates Edge, WebView2, Edge Update, Teams, Outlook, Widgets,
or any other process. It detects only ``msedge.exe`` processes whose command line
contains the dedicated automation-profile path. With ``--repair``, it removes stale
Chromium lock markers only from that dedicated profile and only when no process owns it.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
from pathlib import Path

from ooj_paths import EDGE_PROFILE

PROFILE_DIR = EDGE_PROFILE
LOCK_NAMES = ("lockfile", "SingletonLock", "SingletonSocket", "SingletonCookie")


def _windows_argv(command_line: str) -> list[str]:
    argument_count = ctypes.c_int()
    command_line_to_argv = ctypes.windll.shell32.CommandLineToArgvW
    command_line_to_argv.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
    command_line_to_argv.restype = ctypes.POINTER(ctypes.c_wchar_p)
    pointer = command_line_to_argv(command_line, ctypes.byref(argument_count))
    if not pointer:
        raise OSError("CommandLineToArgvW failed")
    try:
        return [pointer[index] for index in range(argument_count.value)]
    finally:
        local_free = ctypes.windll.kernel32.LocalFree
        local_free.argtypes = [ctypes.c_void_p]
        local_free.restype = ctypes.c_void_p
        local_free(pointer)


def _user_data_dir(command_line: str) -> str | None:
    arguments = _windows_argv(command_line)
    for index, argument in enumerate(arguments):
        lowered = argument.lower()
        if lowered.startswith("--user-data-dir="):
            return argument.split("=", 1)[1]
        if lowered == "--user-data-dir" and index + 1 < len(arguments):
            return arguments[index + 1]
    return None


def _normalized_path(value: str | Path) -> str:
    return os.path.normcase(os.path.abspath(os.path.expandvars(str(value)))).rstrip("\\/")


def profile_process_ids(profile_dir: Path = PROFILE_DIR) -> list[int]:
    script = (
        "$items=@(Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" | "
        "Select-Object ProcessId,CommandLine); "
        "$items | ConvertTo-Json -Compress"
    )
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode != 0:
        raise RuntimeError("Unable to inspect Edge automation-profile ownership")
    output = completed.stdout.strip()
    if not output:
        return []
    payload = json.loads(output)
    processes = payload if isinstance(payload, list) else [payload]
    expected = _normalized_path(profile_dir)
    owners = []
    for process in processes:
        command_line = process.get("CommandLine") if isinstance(process, dict) else None
        if not command_line:
            continue
        candidate = _user_data_dir(command_line)
        if candidate and _normalized_path(candidate) == expected:
            owners.append(int(process["ProcessId"]))
    return sorted(set(owners))


def stale_lock_paths(profile_dir: Path = PROFILE_DIR) -> list[Path]:
    return [profile_dir / name for name in LOCK_NAMES if (profile_dir / name).exists()]


def repair_stale_locks(profile_dir: Path = PROFILE_DIR) -> list[Path]:
    owners = profile_process_ids(profile_dir)
    if owners:
        raise RuntimeError(
            "Automation Edge profile is in use; close that browser normally before repairing locks"
        )
    removed = []
    for path in stale_lock_paths(profile_dir):
        if path.is_dir() and not path.is_symlink():
            raise RuntimeError(f"Refusing to remove unexpected lock directory: {path.name}")
        path.unlink()
        removed.append(path)
    return removed


def status(profile_dir: Path = PROFILE_DIR) -> dict[str, object]:
    owners = profile_process_ids(profile_dir)
    return {
        "profile_exists": profile_dir.exists(),
        "profile_process_ids": owners,
        "stale_lock_files": [path.name for path in stale_lock_paths(profile_dir)],
        "safe_to_launch": profile_dir.exists() and not owners,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Safely inspect the dedicated Edge automation profile")
    parser.add_argument(
        "--repair",
        action="store_true",
        help="remove stale lock files only when no automation-profile Edge process exists",
    )
    args = parser.parse_args()
    removed = repair_stale_locks() if args.repair else []
    result = status()
    result["removed_lock_files"] = [path.name for path in removed]
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
