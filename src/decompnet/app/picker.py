"""The system's own file chooser, for paths the page can't learn: the window shows files' contents to the page, never
where they are, so "Open a saved project" asked the user to type two paths. The app runs this file with the
system's Python on Linux, or the app's Python on macOS and Windows; it prints the chosen path, or nothing when
the user cancels.

    python3 picker.py folder|file <title>
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

KINDS = ("folder", "file")
PICK_SECONDS = 30 * 60  # the chooser stays open as long as the user takes, within reason


def choose(kind: str, title: str) -> str:
    """The path the user picked, "" when they cancelled or no chooser could open (the page keeps its typed path)."""
    from . import window

    if kind not in KINDS:
        raise ValueError("pick a folder or a file")
    python = sys.executable if window.IS_WINDOWS or window.IS_MACOS else window.SYSTEM_PYTHON
    try:
        res = subprocess.run([python, "-I", str(Path(__file__)), kind, title[:200]],
                             capture_output=True, text=True, timeout=PICK_SECONDS,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if window.IS_WINDOWS else 0)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return res.stdout.strip() if res.returncode == 0 else ""


def main(kind: str, title: str) -> None:
    if sys.platform == "win32":
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        try:
            path = (filedialog.askdirectory(title=title) if kind == "folder" else
                    filedialog.askopenfilename(title=title))
            if path:
                print(path)
        finally:
            root.destroy()
        return

    if sys.platform == "darwin":
        from AppKit import NSApplication, NSModalResponseOK, NSOpenPanel

        app = NSApplication.sharedApplication()
        app.setActivationPolicy_(0)
        app.activateIgnoringOtherApps_(True)
        panel = NSOpenPanel.openPanel()
        panel.setTitle_(title)
        panel.setCanChooseDirectories_(kind == "folder")
        panel.setCanChooseFiles_(kind == "file")
        panel.setAllowsMultipleSelection_(False)
        if panel.runModal() == NSModalResponseOK:
            print(panel.URL().path())
        return

    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk

    action = Gtk.FileChooserAction.SELECT_FOLDER if kind == "folder" else Gtk.FileChooserAction.OPEN
    chooser = Gtk.FileChooserNative.new(title, None, action, "Choose", "Cancel")  # (the desktop's own, through its portal)
    if chooser.run() == Gtk.ResponseType.ACCEPT:
        print(chooser.get_filename() or "")


if __name__ == "__main__":
    main(*sys.argv[1:3])
