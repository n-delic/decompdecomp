"""The TUI: decompdecomp in a terminal (docs/TUI.md). `decomp serve` starts the app's server in a background thread
and this in the foreground of the same terminal (cli.py); it is one more client of the app's HTTP API, exactly like
the window's JavaScript. tests/test_tui.py runs it against the real app, so an API change that breaks it fails there.

The first version only reads: projects, the function map with its picker and search, the agents at work and the
log. Everything that changes a project is on the roadmap (docs/TUI.md).
"""

from __future__ import annotations

import sys
import threading

from .client import Client

_running = None  # the TUI while it holds the terminal (release() gives the terminal back)
_ended = threading.Event()
_leaving = threading.Event()  # the process is ending some other way: the terminal is being given back for it


def run(port: int, secret: str, new_code, name: str = "decompdecomp", home: str = "") -> str | None:
    """The TUI, if this terminal can show one: blocks until it ends. "stop" when the user asked to stop everything;
    another word for why it ended otherwise (a bug in it: the app keeps running); None when this isn't a terminal
    (a service, a pipe) or Textual can't be imported, and `decomp serve` prints the address and the browser link
    instead. `new_code`: the TUI's `b`, one of decomp serve's one-time browser links (sessions.py). `name` and
    `home`: which decompdecomp this is (a development copy has its own, edition.py) and where its projects are."""
    global _running
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return None
    try:
        from .app import DecompApp
    except ImportError:  # a broken install: fall back to the printed link
        return None
    app = DecompApp(Client(port, secret), new_code, name=name, home=home, show_link=True)
    _ended.clear()
    _running = app
    try:
        # no mouse: it's driven from the keyboard, and the mouse stays the terminal's, so selecting and copying the
        # link (or anything) works as anywhere, inside screen and tmux too
        app.run(mouse=False)
    finally:
        _running = None
        _ended.set()
    if _leaving.is_set():  # Quit in the browser, `decomp stop` or an update: that thread ends the process now
        threading.Event().wait()
    if app.return_value == "stop":
        return "stop"
    return "an error in it" if app.return_code else "it closed"


def release(timeout: float = 3.0) -> None:
    """Give the terminal back before the process ends or restarts some other way than the TUI's own `q` (Quit in
    the browser, `decomp stop`, an update): the app ends with os._exit or becomes the new version with execve, and
    neither would let Textual put the terminal back as it found it. Called from the server's thread."""
    app = _running
    if app is None or _ended.is_set():
        return
    _leaving.set()
    try:
        app.call_from_thread(app.exit)
    except RuntimeError:  # it ended meanwhile
        return
    _ended.wait(timeout)
