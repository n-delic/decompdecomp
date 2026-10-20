"""decompdecomp's one window, tied to the app.

The window is a program of its own (gtkwindow.py, run by the system's python3 on the system's WebKitGTK), so the
app's Python needs no GTK. The app starts it, talks to it on its stdin, and watches it: when the window ends without
the app asking (it crashed, or the page couldn't answer a close), the app quits, unless helpers or a job are still
working; then they carry on in the background, the tray icon or `decomp` opens the window again.

No browser is involved: the page is served only to a window whose user agent carries the per-launch secret.
"""

from __future__ import annotations

import functools
import logging
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Callable

from . import edition

log = logging.getLogger("decompdecomp")
SYSTEM_PYTHON = "/usr/bin/python3"
WINDOW_PROGRAM = Path(__file__).with_name("gtkwindow.py")
TOKEN_AGENT = "decompdecomp-window/"  # gtkwindow.py puts this, then the secret, in its user agent
CHECK_SECONDS = 20
CLOSE_SECONDS = 5  # a window told to close that's still there after this long is ended
NVIDIA_MODULE = Path("/sys/module/nvidia")  # there while NVIDIA's own driver is loaded

# what the window needs from the system, per family of distributions (/etc/os-release ID and ID_LIKE)
INSTALL_COMMANDS = {
    "debian": "sudo apt install python3-gi gir1.2-webkit2-4.1",
    "ubuntu": "sudo apt install python3-gi gir1.2-webkit2-4.1",
    "fedora": "sudo dnf install python3-gobject webkit2gtk4.1",
    "rhel": "sudo dnf install python3-gobject webkit2gtk4.1",
    "arch": "sudo pacman -S python-gobject webkit2gtk-4.1",
    "suse": "sudo zypper install python3-gobject typelib-1_0-WebKit2-4_1",
}
PACKAGES_NEEDED = "PyGObject and WebKitGTK 4.1 (with its GObject introspection data)"


def _distro_family() -> str | None:
    try:
        fields = dict(line.split("=", 1) for line in Path("/etc/os-release").read_text().splitlines() if "=" in line)
    except OSError:
        return None
    for name in [fields.get("ID", "")] + fields.get("ID_LIKE", "").split():
        name = name.strip('"')
        for family in INSTALL_COMMANDS:
            if name == family or name.startswith(family):
                return family
    return None


@functools.cache
def missing() -> str | None:
    """Why the window can't open here (what to install, and how), or None when it can."""
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):  # over ssh, or a text console
        return ("decompdecomp opens its own window, and there's no desktop here to show it on. "
                "Start decompdecomp from a terminal in your desktop session (or from the apps menu).")
    probe = 'import gi; gi.require_version("Gtk", "3.0"); gi.require_version("WebKit2", "4.1")'
    try:
        ok = subprocess.run([SYSTEM_PYTHON, "-I", "-c", probe], capture_output=True, timeout=CHECK_SECONDS).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        ok = False
    if ok:
        return None
    command = INSTALL_COMMANDS.get(_distro_family() or "")
    return (f"decompdecomp's window needs {PACKAGES_NEEDED} from your system. "
            + (f"Install them with:\n  {command}\nthen start decompdecomp again." if command
               else "Install them with your distribution's package manager, then start decompdecomp again."))


def tell_desktop(title: str, text: str) -> None:
    """A desktop notification: started from the apps menu (an AppImage without WebKitGTK), there's no terminal to
    read what `decomp` printed. gdbus comes with GLib, which every desktop has."""
    gdbus = shutil.which("gdbus")
    if not gdbus:
        return
    try:
        subprocess.run([gdbus, "call", "--session", "--dest", "org.freedesktop.Notifications", "--object-path",
                        "/org/freedesktop/Notifications", "--method", "org.freedesktop.Notifications.Notify",
                        edition.NAME, "0", "", title, text, "[]", "{}", "0"],
                       capture_output=True, timeout=CHECK_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        pass


def wanted() -> bool:
    """False when the window must not open: tests, and anything else that sets DECOMP_WINDOW=0."""
    return os.environ.get("DECOMP_WINDOW") != "0"


class Window:
    """The one app window: open() starts it (or brings the open one to the front), close() ends it, and on_close
    runs when it ends without being asked to."""

    def __init__(self, internal: Path, url: str, secret: str, on_close: Callable[[], None]):
        self.internal, self.url, self.secret, self.on_close = internal, url, secret, on_close
        self.proc: subprocess.Popen | None = None
        self._lock = threading.Lock()

    def is_open(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def open(self) -> bool:
        """True when the window is open (just opened, or already open and now in front)."""
        with self._lock:
            if self.is_open():
                return self._say("present")
            missing.cache_clear()  # the packages may have been installed since the app started
            if not wanted() or missing():
                return False
            icon = self.internal / "window-icon.svg"
            icon.write_text(edition.icon_svg(), encoding="utf-8")
            url = self.url
            if os.environ.get("DECOMP_THEME") in ("light", "dark"):
                url += ("&" if "?" in url else "?") + "system-theme=" + os.environ["DECOMP_THEME"]
            argv = [SYSTEM_PYTHON, "-I", str(WINDOW_PROGRAM), "--url", url, "--title", edition.NAME,
                    "--window-class", edition.WINDOW_CLASS, "--icon", str(icon)] + (["--dev"] if edition.DEV else [])
            try:
                self.proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                             stderr=subprocess.STDOUT, text=True, errors="replace",
                                             env=window_env(self.secret))
            except OSError:
                log.exception("the window didn't start")
                return False
            threading.Thread(target=self._watch, args=(self.proc,), daemon=True, name="window").start()
            threading.Thread(target=self._relay, args=(self.proc,), daemon=True, name="window-output").start()
            return True

    def close(self) -> None:
        """End the window (the app quits, or the user keeps helpers working without it)."""
        with self._lock:
            proc, self.proc = self.proc, None  # (so the watcher doesn't take this for the window ending by itself)
        if proc is not None and proc.poll() is None:
            self._say("close", proc)
            try:
                proc.wait(timeout=CLOSE_SECONDS)
            except subprocess.TimeoutExpired:
                proc.kill()

    def _say(self, word: str, proc: subprocess.Popen | None = None) -> bool:
        proc = proc or self.proc
        try:
            proc.stdin.write(word + "\n")
            proc.stdin.flush()
            return True
        except (OSError, ValueError, AttributeError):
            return False

    @staticmethod
    def _relay(proc: subprocess.Popen) -> None:
        """What the window program says (GTK and WebKit warnings, the page's console messages) into the app's log."""
        for line in proc.stdout:
            log.info("window: %s", line.rstrip())

    def _watch(self, proc: subprocess.Popen) -> None:
        proc.wait()
        if proc.returncode:
            log.warning("the window ended with exit code %s", proc.returncode)
        if proc is self.proc:
            self.proc = None
            self.on_close()


def window_env(secret: str) -> dict[str, str]:
    """The window program's environment. On NVIDIA's driver WebKitGTK's DMA-BUF renderer kills the window at once
    under Wayland ("Error 71 (Protocol error) dispatching to Wayland display", the app quits with it), so there it
    draws the older way; a user's own setting of the variable wins."""
    env = dict(os.environ, DECOMP_WINDOW_TOKEN=secret)
    if NVIDIA_MODULE.exists():
        env.setdefault("WEBKIT_DISABLE_DMABUF_RENDERER", "1")
    return env


WINDOW: Window | None = None  # set by the server (server.build)
IN_TRAY = False  # set by cli.run when the tray icon shows: then helpers kept working have a way back


def open_app() -> bool:
    return WINDOW is not None and WINDOW.open()


def close_app() -> None:
    if WINDOW is not None:
        WINDOW.close()


def is_open() -> bool:
    return WINDOW is not None and WINDOW.is_open()


def token_of(user_agent: str) -> str:
    """The secret a request's user agent carries ("" when none): the window's (gtkwindow.py), or `decomp`'s own
    commands talking to the running app (cli.py)."""
    _, found, rest = user_agent.partition(TOKEN_AGENT)
    return rest.split(" ", 1)[0] if found else ""
