"""`decomp`: open decompdecomp in its window (starting the app, or bringing the running one's window to the front).

That's the one way to run it. The other commands act on the app: `decomp stop`, `decomp update`, `decomp setup` (the
free compilers and tools), `decomp shortcut` (the apps menu entry). A development copy (run from a git checkout) is
a separate app with its own home and port, and never touches the installed one (edition.py).
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import socket
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path

from .. import hostenv
from . import edition, tray, window

PORTS_TO_TRY = 50
OLD_COOKIE = "decomp_live"  # 1.0.2 and older took the secret in this cookie
HEALTH_TIMEOUT_SECONDS = 1.5
QUIT_WAIT_SECONDS = 10
QUIT_POLL_SECONDS = 0.1
READY_WAIT_SECONDS = 20
READY_POLL_SECONDS = 0.2
REQUEST_BODY_LIMIT = 80 * 1024 * 1024  # a game file sent by the GUI's import


def home_dir() -> Path:
    return edition.home_dir()


def _alive(port: int) -> bool:
    return _running_version(port) is not None


def _running_version(port: int) -> str | None:
    """The version of the decompdecomp answering on this port ("" when it doesn't say), or None."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=HEALTH_TIMEOUT_SECONDS) as r:
            data = json.loads(r.read())
            return str(data.get("version") or "") if data.get("app") == "decomp-wtf" else None
    except (OSError, ValueError):
        return None


def _version_key(v: str) -> tuple[int, ...]:
    from .updates import version_key  # one rule for which version is newer (pre-releases come before their release)
    return version_key(v)


def _call(info: dict, path: str, body: dict | None = None, timeout: float = 10) -> dict:
    """Call the running app's API as `decomp` itself: its user agent carries the secret from server.json (the
    user's own file, 0600), like the window's (window.token_of). The cookie carries it too, for a running 1.0.2 or
    older, which knew only that: without it, `decomp stop` and the installer couldn't stop the old app."""
    port = info["port"]
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method="POST" if body is not None else "GET",
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"User-Agent": f"{window.TOKEN_AGENT}{info['secret']}", "X-Decomp": "1",
                                          "Cookie": f"{OLD_COOKIE}={info['secret']}",
                                          "Host": f"127.0.0.1:{port}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def _state(state_file: Path) -> dict | None:
    """The running app of this home, from its server.json: {port, secret, pid}, or None when none answers."""
    try:
        info = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return info if isinstance(info, dict) and _alive(info.get("port") or 0) else None


def replace_older(state_file: Path, log=print) -> dict | None:
    """The running app's {port, secret} when it's this version or newer. An older one (still running after an
    upgrade) is stopped, so the new version starts in its place: returns None then, and when nothing runs. One
    instance is one per home: a development copy (~/Decomp-dev) never meets the installed app (~/Decomp)."""
    prev = _state(state_file)
    if prev is None:
        return None
    running = _running_version(prev["port"]) or ""
    mine = __import__("decompnet").__version__
    if running == mine or _version_key(running) > _version_key(mine):
        return prev
    log(f"decompdecomp {running} is still running; restarting it as {mine}")
    try:
        _call(prev, "/api/quit", {})
    except OSError:
        pass
    for _ in range(int(QUIT_WAIT_SECONDS / QUIT_POLL_SECONDS)):
        if not _alive(prev["port"]):
            break
        time.sleep(QUIT_POLL_SECONDS)
    return None


def _listen(start: int, exact: bool = False) -> socket.socket:
    """A listening socket on 127.0.0.1, on the first free port from `start`, kept open and handed to the server: two
    instances starting at the same moment can't both pick the same port. `exact`: that port or nothing (a restart
    into an update keeps its port)."""
    for port in range(start, start + (1 if exact else PORTS_TO_TRY)):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # the last run's connections linger in TIME_WAIT
        for _attempt in range(PORTS_TO_TRY if exact else 1):
            try:
                sock.bind(("127.0.0.1", port))
                sock.listen(128)
                return sock
            except OSError:
                time.sleep(READY_POLL_SECONDS if exact else 0)
        sock.close()
    raise SystemExit(f"port {start} is taken" if exact else "no free port for decompdecomp")


def _restart_state() -> dict | None:
    """Set by updates.restart: this process just became the new version and keeps the old one's port and secret."""
    raw = os.environ.pop("DECOMP_RESTART", "")
    try:
        data = json.loads(raw) if raw else None
        return data if isinstance(data, dict) and data.get("port") and data.get("secret") else None
    except ValueError:
        return None


def _open_when_ready(port: int) -> None:
    def wait():
        for _ in range(int(READY_WAIT_SECONDS / READY_POLL_SECONDS)):
            if _alive(port):
                window.open_app()
                return
            time.sleep(READY_POLL_SECONDS)
    threading.Thread(target=wait, daemon=True, name="open-window").start()


def run(args) -> int:
    import uvicorn

    from . import cleanup
    from .engine import RomNetwork
    from .server import build
    from .support import file_log

    home = home_dir()
    internal = home / ".decomp"
    internal.mkdir(parents=True, exist_ok=True)
    log = file_log(internal / "logs" / "decomp.log")  # from the first line on: whatever stops the app is in it
    log.info("%s %s starting (%s)", edition.NAME, __import__("decompnet").__version__, sys.executable)
    state_file = internal / "server.json"
    restarted = _restart_state()
    prev = None if restarted else replace_older(state_file)
    if prev:
        try:
            shown = _call(prev, "/api/window", {}).get("window")
        except (OSError, ValueError):
            shown = False
        print(f"{edition.NAME} is already running" + ("" if shown else ": its window couldn't open (see above)"))
        return 0
    if window.wanted() and (why := window.missing()):
        log.error("no window: %s", why)
        print(why, file=sys.stderr)
        window.tell_desktop(f"{edition.NAME} can't open its window", why)
        if not restarted:
            return 1
        # an update from 1.0.2 landed here: quitting would stop the helpers mid-work; they carry on, and `decomp`
        # opens the window once the packages are in (Window.open looks again)

    sock = _listen(restarted["port"] if restarted else args.port, exact=bool(restarted))
    port = sock.getsockname()[1]
    secret = restarted["secret"] if restarted else secrets.token_urlsafe(24)
    state_file.write_text(json.dumps({"port": port, "secret": secret, "pid": os.getpid()}), encoding="utf-8")
    os.chmod(state_file, 0o600)
    cleanup.old_registrations(internal)
    net = RomNetwork(internal / "decomp.db", home)
    mcp, manager, security, quit_app = build(net, internal, port, secret)
    app = mcp.streamable_http_app(transport_security=security, max_request_body_size=REQUEST_BODY_LIMIT, host="127.0.0.1")
    # with a tray icon, helpers kept working without the window have a way back to it
    window.IN_TRAY = tray.start(window.open_app, quit_app)
    print(f"{edition.NAME} {__import__('decompnet').__version__}\nyour projects: {hostenv.shown_path(home)}"
          + ("\nupdated and restarted" if restarted else "\nstop with Ctrl-C, or quit it in its window"), flush=True)
    # (1.0.2 and older restart into a new version without saying: they had a browser tab, which can't follow)
    if not restarted or restarted.get("window", True):
        _open_when_ready(port)
    try:
        uvicorn.Server(server_config(app)).run(sockets=[sock])
    except Exception:  # quit_app ends the process at once: say why first, or the app just vanishes
        log.exception("the server stopped")
        traceback.print_exc()
    finally:
        quit_app()
    return 0


def server_config(app):
    """The web server's settings. Its own logging setup gives its loggers a terminal of their own, so an error
    inside it ("Exception in ASGI application") never reached the app's log: none of its own setup, then, and
    its messages go where every other one goes (support.file_log). No line per request: the page asks every second."""
    import logging

    import uvicorn

    logging.getLogger("uvicorn").setLevel(logging.WARNING)
    return uvicorn.Config(app, log_config=None, access_log=False)


def update_cmd(args) -> int:
    """Look for a newer decompdecomp and install it (the running app restarts into it)."""
    from . import updates

    running = _state(home_dir() / ".decomp" / "server.json")
    found = updates.check()
    cur = found["current"]
    where = f" ({cur['branch']} @ {(cur.get('commit') or '?')[:7]})" if cur["channel"] == "branch" else ""
    if found.get("error"):
        print(found["error"])
        return 1
    if not found["available"]:
        print(f"decompdecomp {cur['version']}{where} is up to date")
        return 0
    latest = found["latest"]
    print(f"update: {latest.get('version') or ''} {('@ ' + latest['commit'][:7]) if latest.get('commit') else ''}".strip())
    for note in found.get("notes") or []:
        print(f"  - {note}")
    if args.check:
        return 0
    if running:  # the app installs it and restarts itself
        try:
            _call(running, "/api/update", {"apply": True}, timeout=120)
        except urllib.error.HTTPError as e:
            raise SystemExit(json.loads(e.read() or b"{}").get("error") or str(e)) from None
        print("decompdecomp is installing the update and restarts when it's done")
        return 0
    updates.install(found, log=print)
    print("updated")
    return 0


def install_info(args) -> int:
    """Remember how decompdecomp was installed (by the installers): where its updates come from."""
    from . import updates

    print(json.dumps(updates.record(args.channel, args.branch, args.commit, args.repo)))
    return 0


def setup(args) -> int:
    """Install what decompdecomp needs on this computer: the free compilers and the draft decompiler."""
    from .. import toolchains

    plat = toolchains.current_platform()
    print(f"Setting up decompdecomp tools for {plat}")
    if why := toolchains.unsupported_cpu():
        print(f"  ! {why}: the games can be imported and looked at here, not matched")
    for tid in ("gcc-2.7.2-kmc", "m2c", "decomp-permuter", "objdiff-cli"):
        tc = toolchains.get(tid)
        if not tc.supported:
            print(f"  - {tc.name}: no build for {plat} (projects that need it can't be matched here yet)")
            continue
        try:
            toolchains.install(tid, log=lambda line: print(f"  {line}"))
        except Exception as e:  # keep going: the app shows what's missing
            print(f"  ! {tc.name}: {e}")
    print("  IDO (most first-party games) is proprietary: install it from the app if you accept its license.")
    if why := window.missing():
        print(f"  ! {why}")
    return 0


def stop(args) -> int:
    """Quit the running app of this home, and say so only once it has: it used to say "stopped" to a 1.0.2 app
    that refused the request and kept running."""
    running = _state(home_dir() / ".decomp" / "server.json")
    if running is None:
        print(f"{edition.NAME} is not running")
        return 0
    try:
        _call(running, "/api/quit", {}, timeout=5)
    except OSError:
        pass
    for _ in range(int(QUIT_WAIT_SECONDS / QUIT_POLL_SECONDS)):
        if not _alive(running["port"]):
            print(f"{edition.NAME} stopped")
            return 0
        time.sleep(QUIT_POLL_SECONDS)
    pid = running.get("pid")
    print(f"{edition.NAME} didn't stop" + (f": end it with `kill {pid}`" if pid else ""), file=sys.stderr)
    return 1


def shortcut(args) -> int:
    from .launcher import install_launcher

    print(install_launcher())
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="decomp", description="decompdecomp: AI agents decompile the games you own, byte for byte")
    ap.add_argument("--port", type=int, default=edition.FIRST_PORT, help=argparse.SUPPRESS)
    # what 1.0.2 and older pass when they restart into an update: refusing it would end the app mid-update
    ap.add_argument("--no-browser", action="store_true", help=argparse.SUPPRESS)
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("setup", help="install the compilers and tools this computer needs")
    sub.add_parser("stop", help="quit the running app (and every helper it started)")
    sub.add_parser("shortcut", help="add decompdecomp to your apps menu")
    up = sub.add_parser("update", help="install the newest decompdecomp (the running app restarts into it)")
    up.add_argument("--check", action="store_true", help="only say whether there is one")
    ii = sub.add_parser("install-info", help=argparse.SUPPRESS)  # the installers: where updates come from
    ii.add_argument("--channel", choices=["release", "branch"], default="release")
    ii.add_argument("--branch", default="")
    ii.add_argument("--commit", default="")
    ii.add_argument("--repo", default="n-delic/decompdecomp")
    args = ap.parse_args(argv)
    __import__("decompnet").utf8_output()
    return {"setup": setup, "stop": stop, "shortcut": shortcut, "update": update_cmd,
            "install-info": install_info}.get(args.cmd, run)(args)


if __name__ == "__main__":
    sys.exit(main())
