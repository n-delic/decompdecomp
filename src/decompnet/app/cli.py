"""`decomp`: open decompdecomp in its window (starting the app, or bringing the running one's window to the front).

`decomp serve` runs the same engine as a localhost webserver without a window or tray. The other commands act on the app: `decomp stop`, `decomp update`, `decomp setup` (the
free compilers and tools), `decomp shortcut` (the apps menu entry), `decomp commit` and `decomp push` (a project's
save repository). A development copy (run from a git checkout) is
a separate app with its own home, port and wine prefix, and never touches the installed one (edition.py).
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
from .shutdown_progress import RUNTIME_STOP_SECONDS, SHUTDOWN_SECONDS

PORTS_TO_TRY = 50
OLD_COOKIE = "decomp_live"  # 1.0.2 and older took the secret in this cookie
HEALTH_TIMEOUT_SECONDS = 1.5
QUIT_WAIT_SECONDS = SHUTDOWN_SECONDS + RUNTIME_STOP_SECONDS + 10
QUIT_POLL_SECONDS = 0.1
READY_WAIT_SECONDS = 20
READY_POLL_SECONDS = 0.2
REQUEST_BODY_LIMIT = 80 * 1024 * 1024  # a game file sent by the GUI's import


def home_dir() -> Path:
    return edition.home_dir()


def use_own_wine_prefix() -> None:
    """Give a development copy a wine prefix of its own. Both copies share the toolchains folder, so without this
    they would share its `.wine`, and quitting one would run `wineserver -k` in the other's prefix, killing the
    compiles it was running. A release keeps the prefix existing installs have; an explicit
    $DECOMPNET_WINE_PREFIX (tests, a scratch home) stays."""
    if edition.DEV and not os.environ.get("DECOMPNET_WINE_PREFIX"):
        from .. import toolchains

        os.environ["DECOMPNET_WINE_PREFIX"] = str(toolchains.root() / ".wine-dev")


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
    instance is one per home: a development copy (~/Decomp-RetroReversing-dev) never meets the installed app (~/Decomp-RetroReversing)."""
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
    else:
        if (pid := _hung_app(state_file.parent)) is not None:
            from . import instance_lock
            log(f"decompdecomp {running} didn't quit when asked; stopping it (pid {pid})")
            instance_lock.end(pid, RUNTIME_STOP_SECONDS + 10, log=log)
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
    from .support import file_log

    headless = getattr(args, 'cmd', None) == 'serve' or os.environ.get('DECOMP_SERVE') == '1'
    if headless:
        if not 1 <= args.port <= 65535:
            print('Choose a server port between 1 and 65535.', file=sys.stderr)
            return 2
        os.environ.update(DECOMP_SERVE='1', DECOMP_WINDOW='0', DECOMP_TRAY='0')
    use_own_wine_prefix()
    home = home_dir()
    from .data_directories import preferred
    if not os.environ.get('DECOMP_HOME') and preferred() and not home.is_dir():
        message = f'The remembered project directory is unavailable: {home}. Connect its drive or choose a directory using DECOMP_HOME.'
        print(message, file=sys.stderr);window.tell_desktop(edition.NAME, message)
        return 1
    internal = home / ".decomp"
    internal.mkdir(parents=True, exist_ok=True)
    log = file_log(internal / "logs" / "decomp.log")  # from the first line on: whatever stops the app is in it
    from . import build_info
    log.info("%s %s starting (%s)", edition.NAME, build_info.label(), sys.executable)
    from .. import spawner
    spawner.install()  # macOS: no subprocess forks the app (spawner.py says why)
    print(f"Starting {edition.NAME} {__import__('decompnet').__version__}…", flush=True)
    if sys.platform == "win32" and not os.environ.get("DECOMP_HOME"):  # a test or experiment leaves the user's alone
        from .launcher import update_windows_command
        try:
            if updated := update_windows_command():
                log.info("updated the old decomp command: %s", updated)
        except OSError:
            log.exception("couldn't update the old decomp command")
    state_file = internal / "server.json"
    restarted = _restart_state()
    if headless and not restarted and (existing := _state(state_file)):
        print(f"This home already has a running app at http://127.0.0.1:{existing['port']}/. "
              "Stop it with `decomp stop` first, or set DECOMP_HOME to a different folder.", file=sys.stderr)
        return 1
    prev = None if restarted else replace_older(state_file)
    if prev:
        if prev.get('mode') == 'serve':
            print(f"decompdecomp is serving http://127.0.0.1:{prev['port']}/. Use that browser page.")
            return 0
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

    # Migration can legitimately take longer than health discovery. Keep another
    # launcher from starting a second server/workers while this home is opening.
    from ..writer_gate import WriterGate
    from . import instance_lock
    instance = WriterGate(internal / 'server-instance')
    if instance.path is not None:
        instance_lock.clear(instance.path, log=lambda line: (print(line, flush=True), log.warning(line)))
    with instance.hold(blocking=bool(restarted) and not restarted.get('switch_home',False)) as admitted:
        if not admitted:
            print('decompdecomp is already starting for this home. Wait for its database upgrade to finish; '
                  'if it never opens, `decomp stop` ends it.')
            return 0
        if instance.path is not None:
            instance_lock.claim(instance.path)
        return _start(args,home,internal,state_file,log,restarted,headless)


def _start(args,home,internal,state_file,log,restarted,headless):
    from .startup_steps import ready, step
    with step("Loading the app"):
        from . import cleanup, mcp_gateway, tool_reader
        from .engine import RomNetwork
        from .runtime_server import Server
        from .server import build

    def gateway_error(error):
        message = str(error)
        log.error("%s", message)
        print(message, file=sys.stderr, flush=True)
        if not headless and window.wanted():
            window.tell_desktop(f"{edition.NAME} can't start its MCP gateway", message)
        return 1

    try:
        with step("Finding or installing the agents' MCP gateway"):
            executable = mcp_gateway.startup_program()
    except (OSError, RuntimeError, ValueError) as error:
        return gateway_error(error)

    sock = _listen(restarted["port"] if restarted else args.port, exact=bool(restarted) or headless)
    port = sock.getsockname()[1]
    secret = restarted["secret"] if restarted else secrets.token_urlsafe(24)
    state_file.write_text(json.dumps({"port": port, "secret": secret, "pid": os.getpid(), "mode": "serve" if headless else "desktop"}), encoding="utf-8")
    os.chmod(state_file, 0o600)
    cleanup.old_registrations(internal)
    cleanup.stale_uploads(internal)
    from .. import compiler_processes
    compiler_processes.configure(internal / "compiler-processes")
    from ..store import CorruptDatabaseError
    try:
        with step("Opening the projects' databases (upgrades them first when needed)"):
            net = RomNetwork(internal / "decomp.db", home,project_databases=True)
    except CorruptDatabaseError as error:
        sock.close()
        state_file.unlink(missing_ok=True)
        log.exception('Database corruption prevented startup')
        print(str(error), file=sys.stderr, flush=True)
        return 1
    # Read-only tools run in a separate process behind the gateway.
    window_reads = not headless and mcp_gateway.window_wanted()
    gateway = reader = None
    if executable is not None:
        try:
            with step("Starting the agents' MCP gateway"):
                reader = tool_reader.start(net, internal, {"secret": secret, "port": port} if window_reads else None)
                gateway = mcp_gateway.start(port, internal / "logs" / "mcp-gateway.log", internal / "mcp-gateway.port",
                                            reader=reader, window=window_reads, executable=executable)
        except (OSError, RuntimeError, ValueError) as error:
            if reader is not None:
                reader.stop()
            sock.close()
            state_file.unlink(missing_ok=True)
            return gateway_error(error)
    if os.environ.get('DECOMP_OFFLOAD', '1') != '0':
        from . import offload
        offload.install()  # the repair families run in the proposal subprocess, not in this interpreter
    mcp, manager, security, quit_app = build(net, internal, port, secret, mcp_url=gateway.url if gateway else None,
                                             reader=reader, gateway=gateway, **({"headless":True} if headless else {}))
    if restarted and restarted.get('switch_home'):
        from .data_directories import remember
        remember(home,selected=True)
    # Behind the gateway the app's MCP keeps no sessions: either side can restart without breaking the other.
    app = mcp.streamable_http_app(transport_security=security, max_request_body_size=REQUEST_BODY_LIMIT, host="127.0.0.1",
                                  stateless_http=gateway is not None, json_response=gateway is not None)
    from ..mcp_acknowledgments import Acknowledgments
    from ..ui_diagnostics import Diagnostics
    app = Acknowledgments(Diagnostics(app, stack_path=internal / "logs" / "event-loop-stacks.log"))
    # with a tray icon, helpers kept working without the window have a way back to it
    window.IN_TRAY = False
    if not headless:  # (on its own thread: the main thread never waits for the icon)
        tray.start_in_background(window.open_app, quit_app, lambda shown: setattr(window, 'IN_TRAY', shown))
    print(f"{edition.NAME} {__import__('decompnet').__version__}\nyour projects: {hostenv.shown_path(home)}"
          + ("\nproject directory switched" if restarted and restarted.get("switch_home") else "\nupdated and restarted" if restarted else "" if headless else "\nstop with Ctrl-C, or quit it in its window"), flush=True)
    # (1.0.2 and older restart into a new version without saying: they had a browser tab, which can't follow)
    if headless:
        print(f"Server: http://127.0.0.1:{port}/\nWeb sign-in code: {net.web_login.code}\nStop with Ctrl-C or `decomp stop`.", flush=True)
    elif not restarted or restarted.get("window", True):
        _open_when_ready(port)
    ready()
    try:
        Server(server_config(app), quit_app).run(sockets=[sock])
    except Exception:  # quit_app ends the process at once: say why first, or the app just vanishes
        log.exception("the server stopped")
        traceback.print_exc()
    finally:
        if gateway is not None:
            gateway.stop()
        quit_app()
    return 0


KEEP_ALIVE = 75  # seconds; longer than every client pool that reuses connections to the app (gateway: 30 s)


def server_config(app):
    """The web server's settings. Its own logging setup gives its loggers a terminal of their own, so an error
    inside it ("Exception in ASGI application") never reached the app's log: none of its own setup, then, and
    its messages go where every other one goes (support.file_log). No line per request: the page asks every second.

    Keep-alive outlasts the gateway's pooled connections (30 s idle, gateway/src/window.rs and main.rs): with
    uvicorn's 5 s default the app closed connections the gateway still held, and a request sent on one as it closed
    failed with "decompdecomp isn't answering (client error (SendRequest))", the Commit saved progress panel
    losing its job while the job carried on (most often on a busy machine, where the close arrives late)."""
    import logging

    import uvicorn

    logging.getLogger("uvicorn").setLevel(logging.WARNING)
    return uvicorn.Config(app, log_config=None, access_log=False, timeout_graceful_shutdown=5,
                          timeout_keep_alive=KEEP_ALIVE)


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


def hosted_export(args) -> int:
    from ..hosted_export import main as export
    return export(args.slug, args.dir, home_dir())


def hosted_link(args) -> int:
    from .hosted_sync import main as link
    return link(args, home_dir() / ".decomp")


def hosted_publish(args) -> int:
    if not args.token:
        print("hosted-publish needs --token (an admin's token on the server) or DECOMP_HOSTED_TOKEN", file=sys.stderr)
        return 2
    from ..hosted_export import publish_main
    return publish_main(args.slug, args.server, args.token, home_dir())


def judge_worker(args) -> int:
    from ..hosted_judge import main as judge
    return judge(home_dir(), Path(args.packs) if args.packs else None, Path(args.cache) if args.cache else None)


def install_info(args) -> int:
    """Remember how decompdecomp was installed (by the installers): where its updates come from."""
    from . import updates

    print(json.dumps(updates.record(args.channel, args.branch, args.commit, args.repo)))
    return 0


def setup(args) -> int:
    """Install what decompdecomp needs on this computer: the free compilers and the draft decompiler."""
    from .. import toolchains

    use_own_wine_prefix()
    plat = toolchains.current_platform()
    print(f"Setting up decompdecomp tools for {plat}")
    if why := toolchains.unsupported_cpu():
        print(f"  ! {why}: the games can be imported and looked at here, not matched")
    for tid in ("gcc-2.7.2-kmc", "m2c", "decomp-permuter", "objdiff-cli", "mupenre"):
        tc = toolchains.get(tid)
        if not tc.supported:
            print(f"  - {tc.name}: no build for {plat} (projects that need it can't be matched here yet)")
            continue
        try:
            toolchains.install(tid, log=lambda line: print(f"  {line}"))
        except Exception as e:  # keep going: the app shows what's missing
            print(f"  ! {tc.name}: {e}")
    # compilers installed before their sources were pinned get them now (a failure is only reported: retried next time)
    toolchains.fetch_missing_sources(log=lambda line: print(f"  {line}"))
    from . import mcp_gateway
    try:
        mcp_gateway.install(log=lambda line: print(f"  {line}"))
    except Exception as e:  # report setup failures alongside the other tools
        print(f"  ! MCP gateway: {e}")
    print("  IDO (most first-party games) is proprietary: install it from the app if you accept its license.")
    if why := window.missing():
        print(f"  ! {why}")
    return 0


def _hung_app(internal: Path) -> int | None:
    """This home's app when it holds the instance lock but its server doesn't answer (16 November 2026: a fork
    stuck before exec held its request lanes for hours, and nothing answered, quit included)."""
    from ..writer_gate import WriterGate
    from . import instance_lock
    lock = WriterGate(internal / 'server-instance').path
    return instance_lock.hung_owner(lock) if lock is not None else None


def stop(args) -> int:
    """Quit the running app of this home, and say so only once it has: it used to say "stopped" to a 1.0.2 app
    that refused the request and kept running. One that doesn't answer, or doesn't quit when asked, is ended:
    asked by SIGTERM (its shutdown has a hard deadline), then SIGKILL."""
    from . import instance_lock
    internal = home_dir() / ".decomp"
    running = _state(internal / "server.json")
    if running is None:
        if (pid := _hung_app(internal)) is None:
            print(f"{edition.NAME} is not running")
            return 0
        print(f"{edition.NAME} is running (pid {pid}) but doesn't answer; stopping it.")
    else:
        try:
            _call(running, "/api/quit", {}, timeout=5)
        except OSError:
            pass
        for _ in range(int(QUIT_WAIT_SECONDS / QUIT_POLL_SECONDS)):
            if not _alive(running["port"]):
                print(f"{edition.NAME} stopped")
                return 0
            time.sleep(QUIT_POLL_SECONDS)
        if (pid := _hung_app(internal)) is None:
            print(f"{edition.NAME} didn't stop", file=sys.stderr)
            return 1
        print(f"{edition.NAME} didn't quit when asked; stopping it (pid {pid}).")
    if instance_lock.end(pid, QUIT_WAIT_SECONDS):
        print(f"{edition.NAME} stopped")
        return 0
    print(f"{edition.NAME} didn't stop: end it with `kill -9 {pid}`", file=sys.stderr)
    return 1


IMPORT_POLL_SECONDS = 0.5


def _listening(port: int) -> bool:
    """Whether anything still accepts connections on the port (the app, however busy)."""
    try:
        socket.create_connection(("127.0.0.1", port), timeout=2).close()
        return True
    except OSError:
        return False


def import_cmd(args) -> int:
    """A game the user owns made a project by the running app, as dropping it on the home page does: the app imports
    it from where it is (a ROM, a disc image, a PC game's folder), and this prints the import's log as it goes and the
    project's slug at the end. The app must be running (this asks it, with server.json's secret, as `decomp stop`
    does): an import is the app's work, its database and its jobs, and two processes writing them would collide."""
    import urllib.parse
    game = Path(args.path).expanduser().resolve()
    if not game.exists():
        print(f"no file or folder at {game}", file=sys.stderr)
        return 2
    running = _state(home_dir() / ".decomp" / "server.json")
    if running is None:
        print(f"{edition.NAME} isn't running for {home_dir()}: start it (`decomp`), then import again", file=sys.stderr)
        return 1
    if not args.yes:
        answer = input(f"Is {game.name} your own legally obtained copy of the game? [y/N] ") if sys.stdin.isatty() else ""
        if answer.strip().lower() not in ("y", "yes"):
            print("not imported: confirm that the game is your own copy (or pass --yes)", file=sys.stderr)
            return 1
    query = {"path": str(game), "confirmed": "1"}
    if args.name:
        query["name"] = args.name
    if args.toolchain:
        query["toolchain"] = args.toolchain
    try:
        started = _call(running, "/api/import?" + urllib.parse.urlencode(query), {}, timeout=60)
    except urllib.error.HTTPError as e:
        try:
            message = json.loads(e.read() or b"{}").get("error") or str(e)
        except ValueError:
            message = str(e)
        print(f"not imported: {message}", file=sys.stderr)
        return 1
    except OSError as e:
        print(f"{edition.NAME} didn't answer: {e}", file=sys.stderr)
        return 1
    job_id = started.get("job")
    print(f"importing {game.name} into {home_dir()} (job {job_id})")
    if args.no_wait:
        return 0
    shown = 0
    while True:
        try:
            job = _call(running, "/api/job?id=" + urllib.parse.quote(str(job_id)), timeout=30)
        except OSError as e:
            # a busy app (an import's heaviest steps) can answer late, /health too: ask again while it still listens
            if _listening(running["port"]):
                time.sleep(IMPORT_POLL_SECONDS)
                continue
            print(f"{edition.NAME} stopped answering ({e}); the import may not have finished", file=sys.stderr)
            return 1
        log = job.get("log") or []
        for line in log[shown:]:
            print(line)
        shown = len(log)
        state = job.get("state")
        if state == "done":
            print(f"imported: {(job.get('result') or {}).get('slug', '')}")
            return 0
        if state in ("error", "cancelled"):
            print(f"not imported: {job.get('error') or state}", file=sys.stderr)
            return 1
        time.sleep(IMPORT_POLL_SECONDS)


AGENTS_WAIT_SECONDS = 30.0


def agents_cmd(args) -> int:
    """The running app's helpers, from the command line: list them, start new ones, restart stopped or paused ones.
    It asks the app as the window does (server.json's secret, as `decomp stop` and `decomp import`): the helpers are
    the app's processes, and only it starts them."""
    import urllib.parse
    # A busy app (just started, or at work) can take seconds to answer /health: wait for it, up to AGENTS_WAIT.
    deadline = time.monotonic() + AGENTS_WAIT_SECONDS
    while (running := _state(home_dir() / ".decomp" / "server.json")) is None and time.monotonic() < deadline:
        if not (home_dir() / ".decomp" / "server.json").is_file():
            break
        time.sleep(1)
    if running is None:
        print(f"{edition.NAME} isn't running for {home_dir()} (or didn't answer in {AGENTS_WAIT_SECONDS:.0f} s): "
              "start it (`decomp`) first", file=sys.stderr)
        return 1

    def ask(path, body=None, timeout=60):
        try:
            return _call(running, path, body, timeout=timeout)
        except urllib.error.HTTPError as e:
            try:
                message = json.loads(e.read() or b"{}").get("error") or str(e)
            except ValueError:
                message = str(e)
            raise SystemExit(f"{edition.NAME} refused: {message}") from None
        except OSError as e:
            raise SystemExit(f"{edition.NAME} didn't answer: {e}") from None

    def helpers(slug):
        return ask("/api/live?" + urllib.parse.urlencode({"slug": slug, "only": "sessions"}), timeout=60).get("sessions") or []

    try:
        if args.action == "list":
            shown = helpers(args.project)
            for h in shown:
                print(f"{h['id']}\t{h['state']:<8}\t{h.get('agent') or h.get('kind_name')}\t{h.get('model') or ''}\t"
                      f"{(h.get('why') or '')[:100]}")
            if not shown:
                print(f"no helpers on {args.project}")
            return 0
        if args.action == "start":
            options = {"model": args.model} if args.model else {}
            started = ask("/api/agents/start", {"kind": args.agent, "slug": args.project, "count": args.count,
                                                "options": options}, timeout=300)
            ids = started.get("started") or []
            print(f"started {len(ids)} {args.agent} helper{'s' if len(ids) != 1 else ''} on {args.project}: "
                  + ", ".join(map(str, ids)))
            return 0
        # restart: the ones named, else every stopped or paused one on the project (--paused: paused only)
        chosen = list(args.ids) or [h["id"] for h in helpers(args.project)
                                    if h["state"] == "paused" or (h["state"] == "stopped" and not args.paused)]
        for helper in chosen:
            ask("/api/agents/restart", {"id": str(helper)})
            print(f"restarted {helper}")
        if not chosen:
            print(f"nothing to restart on {args.project}")
        return 0
    except SystemExit as stop:
        print(stop, file=sys.stderr)
        return 1


REPOSITORY_POLL_SECONDS = 1.0


def _running_app(wait: float = AGENTS_WAIT_SECONDS) -> dict | None:
    """This home's running app, waited for up to `wait` seconds: a busy one (just started, or at work) can take
    seconds to answer /health."""
    state = home_dir() / ".decomp" / "server.json"
    deadline = time.monotonic() + wait
    while (running := _state(state)) is None and time.monotonic() < deadline and state.is_file():
        time.sleep(1)
    return running


def _ask(running: dict, path: str, body: dict | None = None, timeout: float = 60) -> dict:
    """One request to the running app, its refusal as the sentence it gave (SystemExit)."""
    try:
        return _call(running, path, body, timeout=timeout)
    except urllib.error.HTTPError as e:
        try:
            message = json.loads(e.read() or b"{}").get("error") or str(e)
        except ValueError:
            message = str(e)
        raise SystemExit(f"{edition.NAME} refused: {message}") from None
    except OSError as e:
        raise SystemExit(f"{edition.NAME} didn't answer: {e}") from None


def _follow(running: dict, job_id: str, poll: float = REPOSITORY_POLL_SECONDS) -> dict:
    """Print a job's log as it goes, and return it once it has ended (done, error or cancelled)."""
    import urllib.parse
    shown = 0
    while True:
        try:
            job = _call(running, "/api/job?id=" + urllib.parse.quote(str(job_id)), timeout=30)
        except OSError as e:
            # a busy app can answer late: ask again while it still listens
            if _listening(running["port"]):
                time.sleep(poll)
                continue
            raise SystemExit(f"{edition.NAME} stopped answering ({e}); the job may not have finished") from None
        log = job.get("log") or []
        for line in log[shown:]:
            print(line)
        shown = len(log)
        if job.get("state") in ("done", "error", "cancelled"):
            return job
        time.sleep(poll)


def _repository_job(running: dict, path: str, body: dict, what: str) -> dict:
    """Start one of the save repository's jobs as its button does, follow it, and return its result."""
    job = _follow(running, _ask(running, path, body)["job"])
    if job.get("state") != "done":
        raise SystemExit(f"{what} failed: {job.get('error') or job.get('state')}")
    return job.get("result") or {}


def repository_cmd(args) -> int:
    """Commit a project's saved progress (the save repository's Commit button) and push it (its Push button), from
    the command line. It asks the running app as the window does (server.json's secret, as `decomp agents`): the
    commit is the app's work, under its project lock and repository queue, so the app must be running, and nothing
    restarts. A push sends only commits that are already made, to the origin the repository has."""
    import urllib.parse
    running = _running_app()
    if running is None:
        print(f"{edition.NAME} isn't running for {home_dir()}: start it (`decomp`) first", file=sys.stderr)
        return 1
    try:
        if args.cmd == "commit":
            state = _repository_job(running, "/api/repository-setup",
                                    {"slug": args.project, "action": "checkpoint", "origin": ""}, "Commit")
            if not args.push:
                return 0
        else:
            state = _ask(running, "/api/repository-setup?" + urllib.parse.urlencode({"slug": args.project}))
        if not state.get("root"):
            raise SystemExit(f"{args.project} has no save repository yet: set one up in its Settings first")
        if not state.get("origin"):
            raise SystemExit(f"{args.project}'s save repository has no origin to push to: add one in its Settings")
        result = _repository_job(running, "/api/repository-push", {"slug": args.project, "folder": state["root"]}, "Push")
        if (status := result.get("status") or {}).get("destination"):
            print(f"pushed to {status['destination']}")
        return 0
    except SystemExit as stop:
        print(stop, file=sys.stderr)
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
    serve = sub.add_parser("serve", help="run a localhost webserver without a window or tray")
    serve.add_argument("--port", type=int, default=argparse.SUPPRESS, help=f"localhost port (default: {edition.FIRST_PORT})")
    sub.add_parser("setup", help="install the compilers and tools this computer needs")
    sub.add_parser("stop", help="quit the running app (and every helper it started)")
    sub.add_parser("shortcut", help="add decompdecomp to your apps menu")
    im = sub.add_parser("import", help="make a project of a game you own (the running app imports it)")
    im.add_argument("path", help="the game: a ROM, a disc image, or a PC game's folder")
    im.add_argument("--name", help="the project's name (default: the file's)")
    im.add_argument("--toolchain", help="the compiler to start with (default: the app finds it)")
    im.add_argument("--yes", action="store_true", help="confirm it's your own legally obtained copy without asking")
    im.add_argument("--no-wait", action="store_true", help="start the import and return (the app shows its progress)")
    ag = sub.add_parser("agents", help="list, start or restart the running app's helpers on a project")
    ag_sub = ag.add_subparsers(dest="action", required=True)
    ag_list = ag_sub.add_parser("list", help="the project's helpers: id, state, agent, model, why")
    ag_list.add_argument("project", help="the project's slug")
    ag_start = ag_sub.add_parser("start", help="start new helpers, as Start agents does")
    ag_start.add_argument("project", help="the project's slug")
    ag_start.add_argument("--agent", default="claude", help="the agent's kind (claude, codex, opencode, ...; default claude)")
    ag_start.add_argument("--model", default="", help="its model, as the window lists it (e.g. haiku); default the agent's own")
    ag_start.add_argument("--count", type=int, default=1, help="how many (default 1)")
    ag_restart = ag_sub.add_parser("restart", help="start stopped or paused helpers again")
    ag_restart.add_argument("project", help="the project's slug")
    ag_restart.add_argument("ids", nargs="*", help="the helpers' ids (default: every stopped or paused one)")
    ag_restart.add_argument("--paused", action="store_true", help="only paused ones, not ones stopped by hand")
    co = sub.add_parser("commit", help="commit a project's saved progress to its save repository (the running app does it)")
    co.add_argument("project", help="the project's slug")
    co.add_argument("--push", action="store_true", help="push to its origin after committing")
    pu = sub.add_parser("push", help="push a project's save repository to its origin (the running app does it)")
    pu.add_argument("project", help="the project's slug")
    up = sub.add_parser("update", help="install the newest decompdecomp (the running app restarts into it)")
    up.add_argument("--check", action="store_true", help="only say whether there is one")
    he = sub.add_parser("hosted-export", help="write a project as a small hosted file, for decomp-mcp-gateway hosted serve")
    he.add_argument("slug", help="the project's slug")
    he.add_argument("dir", help="the hosted server's folder (writes DIR/projects/SLUG.sqlite3; again to update it)")
    hp = sub.add_parser("hosted-publish", help="upload a project to a hosted server (an admin's token)")
    hp.add_argument("slug", help="the project's slug")
    hp.add_argument("--server", required=True, help="the hosted server's address, https://...")
    hp.add_argument("--token", default=os.environ.get("DECOMP_HOSTED_TOKEN", ""),
                    help="an admin's token on that server (or DECOMP_HOSTED_TOKEN)")
    jw = sub.add_parser("judge-worker", help=argparse.SUPPRESS)  # a verifier's judge (hosted_judge.py)
    jw.add_argument("--packs", help="judge from the verification packs in this folder (hosted_pack.py)")
    jw.add_argument("--cache", help="where to build their scratch projects (default: PACKS/.judge-cache)")
    hl = sub.add_parser("hosted-link", help="take verified matches from a hosted server into a project")
    hl.add_argument("slug", help="the project's slug here")
    hl.add_argument("--server", help="the hosted server's address, https://...")
    hl.add_argument("--token", default=os.environ.get("DECOMP_VERIFIER_TOKEN", ""),
                    help="a verifier's token on that server (or DECOMP_VERIFIER_TOKEN)")
    hl.add_argument("--remote", help="the project's slug on the server (default: the same)")
    hl.add_argument("--remove", action="store_true", help="stop taking matches from it")
    ii = sub.add_parser("install-info", help=argparse.SUPPRESS)  # the installers: where updates come from
    ii.add_argument("--channel", choices=["release", "branch"], default="release")
    ii.add_argument("--branch", default="")
    ii.add_argument("--commit", default="")
    ii.add_argument("--repo", default="RetroReversing/decompdecomp")
    args = ap.parse_args(argv)
    __import__("decompnet").utf8_output()
    from .. import limits
    limits.raise_open_file_limit()
    limits.no_core_dumps()
    return {"setup": setup, "stop": stop, "shortcut": shortcut, "update": update_cmd, "import": import_cmd,
            "agents": agents_cmd, "commit": repository_cmd, "push": repository_cmd,
            "install-info": install_info, "hosted-export": hosted_export,
            "judge-worker": judge_worker, "hosted-link": hosted_link,
            "hosted-publish": hosted_publish}.get(args.cmd, run)(args)


if __name__ == "__main__":
    sys.exit(main())
