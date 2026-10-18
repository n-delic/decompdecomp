"""The app's own window (app/window.py runs app/gtkwindow.py, here a stand-in that writes down what it's told), who
the GUI answers (only that window), and what a computer without the window's system packages is told."""

import functools
import sys
import time

import pytest

from decompnet.app import window

# a window program that writes each word it's told to a file, and ends on "close" or when the app goes away
FAKE_WINDOW = """import sys
log = open(sys.argv[sys.argv.index("--url") + 1].split("log=")[1], "a")
for line in sys.stdin:
    log.write(line); log.flush()
    if line.strip() == "close":
        break
"""


@pytest.fixture
def fake_window(tmp_path, monkeypatch):
    program = tmp_path / "gtkwindow.py"
    program.write_text(FAKE_WINDOW)
    monkeypatch.setattr(window, "WINDOW_PROGRAM", program)
    monkeypatch.setattr(window, "SYSTEM_PYTHON", sys.executable)
    monkeypatch.setattr(window, "missing", functools.cache(lambda: None))  # (open() asks it to look again)
    monkeypatch.delenv("DECOMP_WINDOW", raising=False)
    log = tmp_path / "said.txt"
    closed = []
    w = window.Window(tmp_path, f"http://127.0.0.1:6464/?log={log}", "secret", lambda: closed.append(1))
    yield w, log, closed
    w.close()


def _wait(check, seconds=5):
    for _ in range(int(seconds / 0.05)):
        if check():
            return True
        time.sleep(0.05)
    return False


def test_one_window_and_running_decomp_again_brings_it_to_the_front(fake_window):
    w, log, closed = fake_window
    assert w.open() and w.is_open()
    first = w.proc
    assert w.open() and w.proc is first  # asked again: the same window, not a second
    assert _wait(lambda: log.exists() and log.read_text() == "present\n")
    first.stdin.close()  # the window ends by itself (the app didn't ask): the app hears of it
    assert _wait(lambda: closed == [1]) and not w.is_open()


def test_the_app_closing_its_window_is_not_the_user_closing_it(fake_window):
    w, log, closed = fake_window
    assert w.open()
    proc = w.proc
    w.close()
    assert proc.poll() is not None and log.read_text() == "close\n"
    time.sleep(0.2)
    assert not w.is_open() and closed == []


def test_no_window_in_tests(tmp_path, monkeypatch):
    monkeypatch.setenv("DECOMP_WINDOW", "0")
    assert not window.Window(tmp_path, "http://127.0.0.1:6464/", "s", lambda: None).open()


def test_without_webkitgtk_it_says_what_to_install(monkeypatch):
    """No browser to fall back to: the user is told the packages, with their distribution's command."""
    window.missing.cache_clear()
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(window, "SYSTEM_PYTHON", "/bin/false")
    monkeypatch.setattr(window, "_distro_family", lambda: "fedora")
    try:
        assert "sudo dnf install python3-gobject webkit2gtk4.1" in window.missing()
        window.missing.cache_clear()
        monkeypatch.setattr(window, "_distro_family", lambda: None)
        assert "package manager" in window.missing()
    finally:
        window.missing.cache_clear()


def test_with_no_desktop_it_says_so_instead_of_vanishing(monkeypatch):
    """Started over ssh, the app exited without a word: the window had nowhere to open."""
    window.missing.cache_clear()
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    try:
        assert "no desktop here" in window.missing()
    finally:
        window.missing.cache_clear()


def test_the_secret_rides_in_the_windows_user_agent():
    ua = f"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/605.1.15 {window.TOKEN_AGENT}abc_DEF-123 Safari/605.1.15"
    assert window.token_of(ua) == "abc_DEF-123"
    assert window.token_of("Mozilla/5.0 Firefox/140.0") == ""


def test_the_tray_borrows_the_systems_gtk_without_its_other_packages(monkeypatch):
    """The system's Python folder stayed on the import path after loading GTK for the tray icon: uvicorn then found
    the system's old `websockets` and the app quit at start on every desktop with a tray."""
    import os
    import sys

    from decompnet.app import tray

    system = "/usr/lib/python3/dist-packages"
    if not os.path.isdir(os.path.join(system, "gi")):
        pytest.skip("needs the system's PyGObject")
    monkeypatch.setattr(sys, "path", [p for p in sys.path if p != system])
    for name in [m for m in sys.modules if m == "gi" or m.startswith("gi.")]:
        monkeypatch.delitem(sys.modules, name)
    assert tray._gi() is not None and system not in sys.path
