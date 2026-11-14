"""Play: the app's button that builds a game's WebAssembly build and runs it in the browser (docs/PORTING.md).

The game opens in a window of its own, made by the app window's program (app/window.py), and the browser where no
window can open (a browser session, tests); closing the window stops the game. The build comes from the project's
save repository (the folder Save writes, its progress/ inside), as
`python -m decompnet.port.wasm` builds it; only what changed is rebuilt. The game then runs with its live view
(the host's PORT_LIVE, on 127.0.0.1 only, on a free port), its saves in the build folder (game.eep, beside the
program: game data, kept out of git), until it's stopped or the app quits. One game runs per project: Play again
returns the one running.
"""

from __future__ import annotations

import atexit
import os
import socket
import subprocess
import threading
import time
from pathlib import Path

from ...network import NetworkError

STARTING = "Starting the game"  # the progress bar's last step, after the build's
_running: dict[str, tuple[subprocess.Popen, str]] = {}
_windows: dict[str, subprocess.Popen] = {}  # each game's own window, closed when the game stops
_lock = threading.Lock()


def save_repository(folder: Path) -> Path:
    """The save repository holding `folder`, the project's progress folder: the build reads its progress/ and port/."""
    folder = Path(folder)
    for repo in (folder.parent, folder):
        if (repo / "progress" / "decompdecomp.json").is_file() and (repo / "progress" / "src").is_dir():
            return repo
    raise NetworkError(f"{folder} isn't a saved project yet: Save the project first, then Play builds from the save")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _answers(url_port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", url_port), timeout=0.5):
            return True
    except OSError:
        return False


def running(slug: str) -> str | None:
    """The live view's address of the project's game, while it runs."""
    with _lock:
        entry = _running.get(slug)
        if entry and entry[0].poll() is None:
            return entry[1]
        _running.pop(slug, None)
        return None


def stop(slug: str) -> bool:
    with _lock:
        entry = _running.pop(slug, None)
        win = _windows.pop(slug, None)
    if win is not None and win.poll() is None:
        win.terminate()
    if entry and entry[0].poll() is None:
        entry[0].terminate()
        return True
    return False


@atexit.register
def _stop_all() -> None:
    for slug in list(_running):
        stop(slug)


def game(net, slug: str) -> tuple[Path, dict, Path]:
    """The project's save repository, its config and its ROM, for Play and Export."""
    from ...app import saves

    # Without synchronizing the save folder: the build reads the save as it is on disk, and a sync waits behind the
    # helpers' upkeep (minutes while they work, with nothing in Play's log)
    with net.progress.view():
        proj = net._project(slug)
    if not net.is_rom(proj):
        raise NetworkError("Play builds games imported from a ROM; this project isn't one")
    cfg = net.cfg(proj)
    rom = Path(proj["root"]) / cfg["rom"]["file"]
    if not rom.is_file():
        raise NetworkError(f"the game's ROM isn't at {rom} any more: import it again to play")
    return save_repository(saves.default_folder(net, slug)), cfg, rom


def play(net, slug: str, log=print) -> dict:
    """Build the project's game for the PC if anything changed, start it with its live view, and return its address
    once it answers: {"url": ...}."""
    from . import build

    if (url := running(slug)):
        log(f"already running at {url}")
        return {"url": url}
    repo, cfg, rom = game(net, slug)
    steps = (*build.STEPS, STARTING)
    program = build.build(repo, console=cfg.get("platform", "n64"), log=log, steps=steps)
    build.Progress(log, steps).start(STARTING)
    port = _free_port()
    out = program.parent
    env = {**os.environ, "PORT_LIVE": str(port), "PORT_SAVE": str(out / "game.eep")}
    log_file = open(out / "play.log", "w", encoding="utf-8")
    proc = subprocess.Popen([str(program), str(rom), "0"], cwd=out, env=env, stdout=log_file, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL)
    log_file.close()
    url = f"http://127.0.0.1:{port}/"
    for _ in range(100):  # the host listens before the game boots
        if proc.poll() is not None:
            raise NetworkError(f"the game stopped as it started: see {out / 'play.log'}")
        if _answers(port):
            break
        time.sleep(0.1)
    else:
        proc.terminate()
        raise NetworkError(f"the game didn't start its live view: see {out / 'play.log'}")
    with _lock:
        _running[slug] = (proc, url)
    log(f"playing at {url}")
    return {"url": url, "window": _open_window(slug, url, proc.pid, f"{cfg['rom'].get('title') or slug} (PC port)")}


def _open_window(slug: str, url: str, pid: int, title: str) -> bool:
    """The game's own window; closing it stops the game. False when none opened (the page then goes to the browser)."""
    from ...app import window
    win = window.open_page(url, title, (960, 800))
    if win is None:
        return False
    with _lock:
        _windows[slug] = win

    def watch():
        win.wait()
        with _lock:
            entry = _running.get(slug)
        if entry and entry[0].pid == pid:  # (not a game started again since)
            stop(slug)
    threading.Thread(target=watch, daemon=True, name=f"play-{slug}").start()
    return True
