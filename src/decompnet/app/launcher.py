"""Add decompdecomp to the Linux apps menu, macOS Finder, or Windows Start menu and desktop.

The entry is named like the .deb's and the .rpm's (decompdecomp.desktop), so a desktop shows one decompdecomp even
when a package is installed too: the user's own entry takes the place of the system's. The entry older versions made
(decomp-wtf.desktop) goes: next to the package's, the owner's menu showed decompdecomp twice.
"""

from __future__ import annotations

import base64
import os
import plistlib
import shlex
import shutil
import subprocess
import sys
from pathlib import Path, PureWindowsPath

from . import edition

ENTRY_NAME = "decompdecomp"
OLD_ENTRY_NAME = "decomp-wtf"


def _decomp_exe() -> str:
    return shutil.which("decomp") or str(Path(sys.argv[0]).resolve())


def _windows_folder(folder: int) -> Path:
    """Ask Windows for redirected folders too (Desktop may live in OneDrive or on another drive)."""
    import ctypes

    buffer = ctypes.create_unicode_buffer(260)
    if ctypes.windll.shell32.SHGetFolderPathW(None, folder, None, 0, buffer):
        raise OSError("Windows couldn't find the shortcut folder")
    return Path(buffer.value)


def _shortcut_app_id(shortcut: Path) -> None:
    """Use the window's identity so a pinned shortcut and its running window share one taskbar button."""
    import pythoncom
    from win32com.propsys import propsys
    from win32com.shell import shellcon

    pythoncom.CoInitialize()
    try:
        store = propsys.SHGetPropertyStoreFromParsingName(str(shortcut), None, shellcon.GPS_READWRITE,
                                                        propsys.IID_IPropertyStore)
        key = propsys.PSGetPropertyKeyFromName("System.AppUserModel.ID")
        store.SetValue(key, propsys.PROPVARIANTType(ENTRY_NAME, pythoncom.VT_LPWSTR))
        store.Commit()
        store = None
    finally:
        pythoncom.CoUninitialize()


def install_launcher() -> str:
    """Put decompdecomp in the apps menu (a .desktop entry and its icon). A development copy gets none: it's started
    from its checkout."""
    if edition.DEV:
        return "a development copy gets no launcher: run it with `uv run decomp` in the checkout"
    if sys.platform == "darwin":
        bundle = Path.home() / "Applications" / "decompdecomp.app" / "Contents"
        executable = bundle / "MacOS" / "decompdecomp"
        resources = bundle / "Resources"
        executable.parent.mkdir(parents=True, exist_ok=True)
        resources.mkdir(parents=True, exist_ok=True)
        # Finder doesn't read the user's shell profile. Preserve this interpreter and discover uv explicitly.
        executable.write_text("#!/bin/sh\nexport PATH=" + shlex.quote(os.environ.get("PATH", "/usr/bin:/bin"))
                              + "\nexec " + shlex.quote(sys.executable)
                              + " -m decompnet.app.cli \"$@\"\n", encoding="utf-8")
        executable.chmod(0o755)
        with (bundle / "Info.plist").open("wb") as stream:
            plistlib.dump({"CFBundleName": "decompdecomp", "CFBundleDisplayName": "decompdecomp",
                          "CFBundleIdentifier": "com.ndelic.decompdecomp", "CFBundlePackageType": "APPL",
                          "CFBundleExecutable": "decompdecomp", "LSMinimumSystemVersion": "12.0",
                          "NSHighResolutionCapable": True}, stream)
        return f"added decompdecomp to Finder ({bundle.parent})"
    if sys.platform == "win32":
        desktop = _windows_folder(0x10)  # CSIDL_DESKTOPDIRECTORY
        programs = _windows_folder(0x02)  # CSIDL_PROGRAMS: the current user's Start menu
        desktop.mkdir(parents=True, exist_ok=True)
        programs.mkdir(parents=True, exist_ok=True)
        shortcut = desktop / "decompdecomp.lnk"
        icons = _windows_folder(0x1c) / "decompnet"  # CSIDL_LOCAL_APPDATA
        icons.mkdir(parents=True, exist_ok=True)
        icon = icons / "decompdecomp.ico"
        icon.write_bytes((Path(__file__).with_name("static") / "favicon.ico").read_bytes())
        # pythonw uses this installation's environment without leaving a console behind the app. The MSI's bundled
        # runtime loads the app from its adjacent packages folder through this small launcher script.
        bundled = os.environ.get("DECOMP_PACKAGE") == "windows"
        executable = PureWindowsPath(sys.executable) if sys.platform == "win32" else Path(sys.executable)
        target = str(executable.with_name("pythonw.exe"))
        def ps(text: str) -> str:
            return text.replace("'", "''")
        arguments = (f"'{ps(str(executable.parent.parent / 'decomp-launch.pyw'))}'"
                     if bundled else "'-m decompnet.app.cli'")
        script = (f"$ErrorActionPreference='Stop'; $w=New-Object -ComObject WScript.Shell; "
                  f"$s=$w.CreateShortcut('{ps(str(shortcut))}'); "
                  f"$s.TargetPath='{ps(target)}'; $s.Arguments={arguments}; "
                  f"$s.WorkingDirectory=(Split-Path '{ps(target)}'); "
                  f"$s.IconLocation='{ps(str(icon))},0'; $s.Description='AI game decompilation'; $s.Save()")
        script += f"; Copy-Item -LiteralPath '{ps(str(shortcut))}' -Destination '{ps(str(programs / shortcut.name))}' -Force"
        encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
        res = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                             capture_output=True, text=True, timeout=30,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if res.returncode:
            return f"couldn't add the desktop shortcut: {res.stderr.strip() or res.returncode}"
        for link in (shortcut, programs / shortcut.name):
            _shortcut_app_id(link)
        return f"added decompdecomp to the desktop and Start menu ({shortcut})"
    share = Path.home() / ".local" / "share"
    icons = share / "icons" / "hicolor" / "scalable" / "apps"
    apps = share / "applications"
    icons.mkdir(parents=True, exist_ok=True)
    apps.mkdir(parents=True, exist_ok=True)
    (icons / f"{ENTRY_NAME}.svg").write_text(edition.icon_svg(), encoding="utf-8")
    entry = apps / f"{ENTRY_NAME}.desktop"
    entry.write_text("[Desktop Entry]\nType=Application\nName=decompdecomp\n"
                     "Comment=AI agents decompile the games you own, byte for byte\n"
                     f"Exec=\"{_decomp_exe()}\"\nIcon={ENTRY_NAME}\nTerminal=false\nCategories=Development;\n"
                     f"StartupWMClass={edition.WINDOW_CLASS}\n", encoding="utf-8")
    os.chmod(entry, 0o755)
    for old in (apps / f"{OLD_ENTRY_NAME}.desktop", icons / f"{OLD_ENTRY_NAME}.svg"):
        old.unlink(missing_ok=True)
    return f"added decompdecomp to your apps menu ({entry})"
