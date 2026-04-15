"""
utils.py
--------
Cross-platform utility for locating the Hearthstone installation directory
and its ``Power.log`` file.

Supported platforms
~~~~~~~~~~~~~~~~~~~
* **Windows** – searches the Windows Registry (``HKEY_LOCAL_MACHINE`` and
  ``HKEY_CURRENT_USER``) for the Blizzard / Battle.net launcher entries,
  then falls back to the most common ``Program Files`` paths.
* **macOS** – checks the standard ``~/Library/Logs/Blizzard/Hearthstone/``
  path and the Applications folder.
* **Linux** – checks common Lutris / Wine prefixes.

If none of the automatic methods succeed, a ``tkinter.filedialog`` prompt
asks the user to select the Hearthstone logs folder manually.

Usage
~~~~~
    from utils import find_hs_log_path

    log_path = find_hs_log_path()
    print(f"Power.log is at: {log_path}")
"""

from __future__ import annotations

import os
import platform
import sys
from pathlib import Path
from typing import List, Optional


# ---------------------------------------------------------------------------
# Platform helpers
# ---------------------------------------------------------------------------

_SYSTEM = platform.system()


def _candidate_paths_windows() -> List[Path]:
    """Return candidate ``Power.log`` paths on Windows."""
    candidates: List[Path] = []

    # 1. Windows Registry – Battle.net / Blizzard launcher
    try:
        import winreg  # type: ignore[import]

        _REG_KEYS = [
            (winreg.HKEY_LOCAL_MACHINE,
             r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Hearthstone"),
            (winreg.HKEY_LOCAL_MACHINE,
             r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Hearthstone"),
            (winreg.HKEY_CURRENT_USER,
             r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Hearthstone"),
            (winreg.HKEY_LOCAL_MACHINE,
             r"SOFTWARE\WOW6432Node\Blizzard Entertainment\Hearthstone"),
        ]

        for hive, key_path in _REG_KEYS:
            try:
                key = winreg.OpenKey(hive, key_path)
                install_dir, _ = winreg.QueryValueEx(key, "InstallLocation")
                winreg.CloseKey(key)
                if install_dir:
                    candidates.append(
                        Path(install_dir) / "Logs" / "Power.log"
                    )
            except (FileNotFoundError, OSError):
                pass
    except ImportError:
        pass  # winreg not available (running tests on non-Windows)

    # 2. Common installation directories (multiple drives)
    _COMMON_DIRS = [
        r"Program Files\Hearthstone",
        r"Program Files (x86)\Hearthstone",
        r"Games\Hearthstone",
        r"Hearthstone",
        r"Battle.net\Games\Hearthstone",
    ]
    drives = _get_windows_drives()
    for drive in drives:
        for rel in _COMMON_DIRS:
            candidates.append(Path(drive) / rel / "Logs" / "Power.log")

    # 3. AppData log path (used by newer installs)
    app_data = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA", "")
    if app_data:
        candidates.append(
            Path(app_data) / "Blizzard" / "Hearthstone" / "Logs" / "Power.log"
        )

    return candidates


def _get_windows_drives() -> List[str]:
    """Return a list of drive letters present on this Windows system."""
    drives: List[str] = []
    try:
        import ctypes
        bitmask = ctypes.windll.kernel32.GetLogicalDrives()  # type: ignore[attr-defined]
        for i in range(26):
            if bitmask & (1 << i):
                drives.append(chr(65 + i) + ":\\")
    except Exception:
        drives = ["C:\\", "D:\\", "E:\\"]
    return drives


def _candidate_paths_macos() -> List[Path]:
    """Return candidate ``Power.log`` paths on macOS."""
    home = Path.home()
    return [
        home / "Library" / "Logs" / "Blizzard" / "Hearthstone" / "Power.log",
        home / "Library" / "Application Support" / "Blizzard" / "Hearthstone"
        / "Logs" / "Power.log",
        Path("/Applications/Hearthstone/Logs/Power.log"),
        home / "Applications" / "Hearthstone" / "Logs" / "Power.log",
    ]


def _candidate_paths_linux() -> List[Path]:
    """Return candidate ``Power.log`` paths on Linux (Wine / Lutris)."""
    home = Path.home()
    return [
        # Lutris default Wine prefix
        home / "Games" / "hearthstone" / "drive_c" / "Program Files (x86)"
        / "Hearthstone" / "Logs" / "Power.log",
        home / ".wine" / "drive_c" / "Program Files (x86)"
        / "Hearthstone" / "Logs" / "Power.log",
        home / ".local" / "share" / "lutris" / "runners" / "wine"
        / "hearthstone" / "drive_c" / "Program Files (x86)"
        / "Hearthstone" / "Logs" / "Power.log",
    ]


def _ask_user_for_path() -> Optional[Path]:
    """Open a Tkinter file-dialog so the user can locate the log manually."""
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo(
            "Hearthstone AI Assistant",
            "Could not locate Power.log automatically.\n"
            "Please select the Hearthstone Logs folder.",
        )
        folder = filedialog.askdirectory(
            title="Select Hearthstone Logs folder",
            mustexist=True,
        )
        root.destroy()
        if folder:
            return Path(folder) / "Power.log"
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def find_hs_log_path(ask_if_missing: bool = True) -> Path:
    """
    Return the absolute path to Hearthstone's ``Power.log`` file.

    Search order:
    1. Environment variable ``HS_LOG_PATH`` (useful for testing).
    2. Platform-specific candidate paths.
    3. Interactive ``tkinter`` folder picker (when *ask_if_missing* is True).

    Parameters
    ----------
    ask_if_missing:
        When ``True`` (default) and the log cannot be found automatically,
        a GUI dialog is shown asking the user to locate it.  Set to ``False``
        in automated / headless contexts.

    Returns
    -------
    Path
        Path to ``Power.log``.

    Raises
    ------
    FileNotFoundError
        When the log cannot be found and *ask_if_missing* is ``False``, or
        when the user cancels the dialog.
    """
    # 1. Environment override
    env_path = os.environ.get("HS_LOG_PATH")
    if env_path:
        p = Path(env_path)
        if p.exists():
            return p
        # Honour the override even if the file doesn't exist yet
        return p

    # 2. Platform-specific candidates
    if _SYSTEM == "Windows":
        candidates = _candidate_paths_windows()
    elif _SYSTEM == "Darwin":
        candidates = _candidate_paths_macos()
    else:
        candidates = _candidate_paths_linux()

    for path in candidates:
        if path.exists():
            return path

    # 3. GUI fallback
    if ask_if_missing:
        result = _ask_user_for_path()
        if result is not None:
            return result

    raise FileNotFoundError(
        "Could not find Hearthstone Power.log.\n"
        "Set the HS_LOG_PATH environment variable to its absolute path, "
        "or re-run with ask_if_missing=True."
    )


def find_hs_install_dir(ask_if_missing: bool = True) -> Path:
    """
    Return the Hearthstone installation directory.

    This is the parent of the ``Logs`` folder discovered by
    :func:`find_hs_log_path`.
    """
    log_path = find_hs_log_path(ask_if_missing=ask_if_missing)
    # Logs/Power.log → two levels up → install dir
    return log_path.parent.parent
