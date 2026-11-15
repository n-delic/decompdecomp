"""The agents' MCP endpoint in its own small process (`gateway/`, Rust): it always answers.

The app's own MCP server runs in this busy Python process. When the process stalled, every tool call waited until
the helper's client gave up ("MCP error -32001"), and the helper spent tokens retrying. The gateway takes the
helpers' connections instead and forwards each request to the app's /mcp with a deadline: `initialize` and
`tools/list` fall back to their last good answer, and a tool call that is still running answers "still running,
repeat the same call" while it keeps running. A repeat attaches to the call already running, so nothing runs
twice. Behind the gateway the app's MCP server is stateless (no tool uses an MCP session), so either side can
restart without breaking the other.

The gateway is optional. Packages carry its program (packaging/build_gateway.py); an install from source gets the
release's copy from `decomp setup` (install below). Without it (a checkout that hasn't run `cargo build --release` in
`gateway/`, a platform with no build) or with DECOMP_MCP_GATEWAY=0 the helpers connect to the app's /mcp as before.
DECOMP_MCP_GATEWAY=<path> names the program. The app holds the gateway's standard input open: when the app ends,
for any reason, the gateway sees end-of-file and ends too. If the gateway ends while the app runs, it's started
again on the same port, so the helpers' MCP settings stay right.

It is the app window's address too (gateway/src/window.rs): a second listener that forwards every request to the
app unchanged, except a few read-only pages the reader process answers first (app/window_reads.py). The window uses
it while it answers (window_url) and the app's own address otherwise, so a gateway that's missing, failed or too old
to have the listener never breaks the window. DECOMP_WINDOW_GATEWAY=0 turns it off.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

log = logging.getLogger("decompnet.gateway")

NAME = "decomp-mcp-gateway" + (".exe" if os.name == "nt" else "")
# Under the clients' own limits (Codex: 60 s per tool call), so the helper hears "still running", not an error.
CALL_DEADLINE = 45.0
META_DEADLINE = 15.0
MAX_BODY = 16 * 1024 * 1024
START_SECONDS = 10.0
ROUTES_SECONDS = 1.0
OLD_GATEWAY_EXIT = 2  # what a gateway without the window's options says to one (an older downloaded build)
WINDOW_CHECK_SECONDS = 2.0


def window_wanted() -> bool:
    return os.environ.get("DECOMP_WINDOW_GATEWAY", "").strip().lower() not in ("0", "off", "false", "no")


class StartError(RuntimeError):
    def __init__(self, message: str, code: int | None):
        super().__init__(message)
        self.code = code


def program() -> Path | None:
    """The gateway's program, or None when the helpers should use the app's own endpoint."""
    chosen = os.environ.get("DECOMP_MCP_GATEWAY", "").strip()
    if chosen.lower() in ("0", "off", "false", "no"):
        return None
    if chosen and chosen.lower() not in ("1", "on", "true", "yes"):
        path = Path(chosen).expanduser()
        return path if path.is_file() else None
    return built()


def built() -> Path | None:
    """The gateway program shipped in the package, built in this checkout or downloaded by `decomp setup`, whatever
    the setting says."""
    # The newer of a package's copy and a checkout's cargo build: in a checkout that once ran
    # packaging/build_gateway.py, the copy it left must not shadow later builds.
    local = [p for p in _local() if p.is_file() and os.access(p, os.X_OK)]
    if local:
        return max(local, key=lambda p: p.stat().st_mtime)
    candidate = downloaded()
    return candidate if candidate.is_file() and os.access(candidate, os.X_OK) else None


def _local() -> tuple[Path, Path]:
    package = Path(__file__).resolve().parents[1]
    return (package / "bin" / NAME,  # a packaged build
            package.parents[1] / "gateway" / "target" / "release" / NAME)  # a checkout's cargo build


def downloaded() -> Path:
    """Where `decomp setup` keeps the release's copy, beside the toolchains (outside uv's tool environment, which
    every update replaces)."""
    from .. import toolchains
    return toolchains.root() / "mcp-gateway" / NAME


def asset_name(plat: str | None = None) -> str:
    """The release asset for a platform (packaging/build_gateway.py --asset)."""
    from .. import toolchains
    plat = plat or toolchains.current_platform()
    return f"decomp-mcp-gateway-{plat}" + (".exe" if plat.startswith("windows") else "")


def install(log=print, repo: str | None = None, version: str | None = None) -> Path | None:
    """For an install from source (uv tool install of GitHub's archive, which has no Rust build): the release's
    gateway for this platform, checked against its .sha256. The release of this version first, else the latest
    (the gateway only forwards requests: any recent one serves). None, and the app's own endpoint, when neither
    has one; a copy downloaded before stays."""
    import urllib.error
    import urllib.request

    from .. import __version__
    if any(p.is_file() and os.access(p, os.X_OK) for p in _local()):
        return built()
    if repo is None:
        from . import updates
        repo = updates.installed().get("repo") or updates.REPO
    name = asset_name()
    target = downloaded()
    for release in (f"download/v{version or __version__}", "latest/download"):
        base = f"https://github.com/{repo}/releases/{release}/{name}"
        try:
            with urllib.request.urlopen(urllib.request.Request(base + ".sha256", headers=HEADERS), timeout=30) as r:
                expected = r.read(4096).decode("ascii", "replace").split()[0].lower()
            if target.is_file() and _sha256(target.read_bytes()) == expected:
                log(f"MCP gateway: up to date ({target})")
                return target
            with urllib.request.urlopen(urllib.request.Request(base, headers=HEADERS), timeout=120) as r:
                data = r.read(MAX_PROGRAM + 1)
        except (urllib.error.URLError, OSError, IndexError, ValueError):
            continue  # no such release or asset, or offline
        if len(data) > MAX_PROGRAM or _sha256(data) != expected:
            log(f"MCP gateway: {base} didn't match its checksum; not installed")
            return built()
        target.parent.mkdir(parents=True, exist_ok=True)
        part = target.with_name(f"{target.name}.{os.getpid()}.part")
        try:
            part.write_bytes(data)
            part.chmod(0o755)
            os.replace(part, target)
        finally:
            part.unlink(missing_ok=True)
        log(f"MCP gateway: installed {target}")
        return target
    log("MCP gateway: no release build for this computer; helpers use the app's own endpoint")
    return built()


HEADERS = {"User-Agent": "decompnet-toolchain-installer"}
MAX_PROGRAM = 64 * 1024 * 1024


def _sha256(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()


class Gateway:
    """The running gateway process, restarted on the same port if it ends while the app runs."""

    def __init__(self, exe: Path, upstream: str, log_path: Path | None = None, *,
                 call_deadline: float = CALL_DEADLINE, meta_deadline: float = META_DEADLINE,
                 reader: str | None = None, read_tools=(), state: Path | None = None, window: dict | None = None):
        self.exe, self.upstream, self.log_path = exe, upstream, log_path
        # The window's listener: {"upstream": the app's address, "reader": the reader's or None, "reads": paths}
        # (gateway/src/window.rs). None when there's no window, or the program is too old to have it.
        self.window = window
        self.window_port: int | None = None
        # The tool lists the gateway gave, kept across restarts: a helper connecting while the app is busy or
        # starting gets its project's tools from there instead of "busy or starting" (gateway/src/main.rs).
        self.state = state
        self.reader, self.read_tools = reader, sorted(read_tools)
        self.call_deadline, self.meta_deadline = call_deadline, meta_deadline
        self.port: int | None = None
        self.process: subprocess.Popen | None = None
        self.stopping = threading.Event()
        self.lock = threading.Lock()
        self.pipe = threading.Lock()  # held only to write routes: a helper starting never waits for a restart
        self.restarts = 0
        # The app's routes (app/tool_reader.py routes) for the tools the gateway answers itself (native.rs), and
        # what to do with its reports of those calls.
        self.routes = None
        self.report = None
        self.guides = None  # (platforms) -> guide texts (tool_reader.guides), sent once per platform
        self.guided: set[str] = set()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/mcp"

    @property
    def window_address(self) -> str | None:
        return f"http://127.0.0.1:{self.window_port}/" if self.window_port else None

    def _command(self, port: int, window_port: int = 0) -> list[str]:
        window = self.window
        return [str(self.exe), "--listen", f"127.0.0.1:{port}", "--upstream", self.upstream,
                "--call-deadline", str(self.call_deadline), "--meta-deadline", str(self.meta_deadline),
                "--max-body", str(MAX_BODY)] + (
                    ["--reader", self.reader, "--read-tools", ",".join(self.read_tools)] if self.reader else []) + (
                    ["--state", str(self.state)] if self.state else []) + (
                    ["--window-listen", f"127.0.0.1:{window_port}", "--window-upstream", window["upstream"]]
                    + (["--window-reader", window["reader"], "--window-reads", ",".join(window["reads"])]
                       if window.get("reader") else [])
                    + (["--window-reader-limit", str(window["reader_limit"])] if window.get("reader_limit") else [])
                    if window else [])

    def _launch(self, port: int, window_port: int | None = None) -> int:
        """Start the program and return the port it listens on (it prints `LISTENING <port>` first, and
        `WINDOW <port>` after it with a window listener)."""
        window_port = (self.window_port or 0) if window_port is None else window_port
        errors = open(self.log_path, "ab") if self.log_path else subprocess.DEVNULL  # noqa: SIM115 (kept by the child)
        try:
            process = subprocess.Popen(self._command(port, window_port), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=errors, close_fds=True,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        finally:
            if errors is not subprocess.DEVNULL:
                errors.close()
        found: list[str] = []
        reader = threading.Thread(target=lambda: found.append(process.stdout.readline().decode("ascii", "replace")),
                                  daemon=True, name="gateway-start")
        reader.start()
        reader.join(START_SECONDS)
        line = found[0].strip() if found else ""
        if not line.startswith("LISTENING "):
            process.kill()
            process.wait(timeout=5)
            raise StartError(f"the MCP gateway didn't start (exit {process.poll()}, said {line!r})", process.poll())
        words = line.split()
        self.window_port = int(words[3]) if self.window and len(words) >= 4 and words[2] == "WINDOW" else None
        self.process = process
        self.guided = set()  # (a new process has none)
        threading.Thread(target=self._reports, args=(process,), daemon=True, name="gateway-reports").start()
        self._send_routes()
        return int(line.split()[1])

    def attach(self, routes, report, guides=None) -> None:
        """Let the gateway answer some reads itself: `routes()` is sent every second and at `changed()`; each
        call it answers comes back to `report`; `guides(platforms)` gives guide's texts for new platforms."""
        self.routes, self.report, self.guides = routes, report, guides
        self._send_routes()
        threading.Thread(target=self._push, daemon=True, name="gateway-routes").start()

    def changed(self) -> None:
        self._send_routes()

    def _push(self) -> None:
        while not self.stopping.wait(ROUTES_SECONDS):
            self._send_routes()

    def _send_routes(self) -> None:
        if self.routes is None:
            return
        try:
            routes = self.routes()
            line = (json.dumps({"routes": routes}) + "\n").encode()
            platforms = {p["platform"] for p in routes.get("projects", {}).values() if p.get("platform")}
            if self.guides is not None and not platforms <= self.guided:
                line = (json.dumps({"guides": self.guides(platforms | self.guided)}) + "\n").encode() + line
                self.guided = platforms | self.guided
        except Exception:  # the gateway stops answering itself once its routes are stale; the app answers
            log.exception("MCP gateway routes failed")
            return
        with self.pipe:
            process = self.process
            if process is None or process.poll() is not None or process.stdin is None:
                return
            try:
                process.stdin.write(line)
                process.stdin.flush()
            except OSError:
                pass

    def _reports(self, process: subprocess.Popen) -> None:
        for line in process.stdout:
            if self.report is None:
                continue
            try:
                self.report(json.loads(line))
            except Exception:
                log.exception("MCP gateway report failed")

    def _first(self, preferred: int, window_preferred: int) -> int:
        try:
            return self._launch(preferred, window_preferred)
        except StartError as error:
            if not self.window or error.code != OLD_GATEWAY_EXIT:
                raise
            # A gateway from before the window's listener (a downloaded release older than this app): it still
            # serves the helpers, and the window uses the app's own address.
            log.info("This MCP gateway has no window listener; the window uses the app's address")
            self.window = None
            return self._launch(preferred, 0)

    def start(self, preferred: int = 0, window_preferred: int = 0) -> str:
        with self.lock:
            try:
                self.port = self._first(preferred, window_preferred)
            except RuntimeError:
                if not preferred and not window_preferred:
                    raise
                self.port = self._first(0, 0)  # the last ports are taken now
        threading.Thread(target=self._watch, daemon=True, name="gateway-watch").start()
        log.info("MCP gateway listening on %s (app endpoint %s)", self.url, self.upstream)
        return self.url

    def _watch(self) -> None:
        pause = 0.5
        while not self.stopping.is_set():
            process = self.process
            if process is None:
                return
            code = process.wait()
            if self.stopping.is_set():
                return
            log.warning("MCP gateway ended (exit %s); starting it again on port %s", code, self.port)
            while not self.stopping.is_set():
                time.sleep(pause)
                try:
                    with self.lock:
                        if self.stopping.is_set():
                            return
                        self._launch(self.port)  # (and the window's listener on its port)
                    self.restarts += 1
                    pause = 0.5
                    break
                except (OSError, RuntimeError, ValueError) as error:
                    log.warning("MCP gateway restart failed: %s", error)
                    pause = min(pause * 2, 10.0)

    def window_url(self) -> str | None:
        """The window's address through the gateway while it answers there, else None (the app's own address).
        Asked when the window opens: the gateway's own health says its window listener is the one we started (asked
        of the gateway, not through it: a busy app is no reason to leave the gateway)."""
        address = self.window_address
        process = self.process
        if not address or process is None or process.poll() is not None:
            return None
        import httpx
        try:
            reply = httpx.get(f"http://127.0.0.1:{self.port}/health", timeout=WINDOW_CHECK_SECONDS, trust_env=False)
            window = reply.json().get("window") if reply.status_code == 200 else None
            return address if isinstance(window, dict) and window.get("port") == self.window_port else None
        except (httpx.HTTPError, ValueError, AttributeError):
            return None

    def stop(self) -> None:
        self.stopping.set()
        with self.lock:
            process = self.process
        if process is None:
            return
        try:
            if process.stdin:
                process.stdin.close()  # the gateway ends on end-of-file
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            process.kill()


def _saved_port(path: Path | None, *taken: int) -> int:
    try:
        port = int(path.read_text().strip()) if path and path.is_file() else 0
    except (OSError, ValueError):
        return 0
    return port if 0 < port < 65536 and port not in taken else 0


def start(app_port: int, log_path: Path | None = None, port_file: Path | None = None, reader=None,
          window: bool = False) -> Gateway | None:
    """Start the gateway in front of the app's /mcp, or None when the helpers should connect to the app itself.

    `port_file` keeps the gateway's port across app starts: helpers connected by hand keep a working address.
    `reader` (app/tool_reader.py) answers the read-only tools it can before the app is asked. `window`: listen for
    the app's window too (its port kept beside `port_file`, so the page's origin, and what it keeps in the browser's
    storage, stays the same across starts)."""
    exe = program()
    if exe is None:
        return None
    from .tool_reader import READER_TOOLS
    from .window_reads import GATEWAY_READS
    window_reader = getattr(reader, "window_url", None) if reader is not None else None
    window_spec = ({"upstream": f"http://127.0.0.1:{app_port}", "reader": window_reader, "reads": list(GATEWAY_READS)}
                   if window and window_wanted() else None)
    gateway = Gateway(exe, f"http://127.0.0.1:{app_port}/mcp", log_path,
                      reader=reader.url if reader is not None else None, read_tools=READER_TOOLS,
                      state=port_file.with_name("mcp-gateway-answers.json") if port_file else None, window=window_spec)
    window_file = port_file.with_name("window-gateway.port") if port_file else None
    preferred = _saved_port(port_file, app_port)
    try:
        gateway.start(preferred, _saved_port(window_file, app_port, preferred) if window_spec else 0)
        if port_file:
            port_file.write_text(str(gateway.port), encoding="utf-8")
        if window_file and gateway.window_port:
            window_file.write_text(str(gateway.window_port), encoding="utf-8")
    except (OSError, RuntimeError, ValueError) as error:
        log.warning("MCP gateway unavailable, helpers use the app's own endpoint: %s", error)
        print(f"MCP gateway unavailable: {error}", file=sys.stderr)
        return None
    return gateway
