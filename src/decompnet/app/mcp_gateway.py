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
"""
from __future__ import annotations

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
    for candidate in (*_local(), downloaded()):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


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
                 call_deadline: float = CALL_DEADLINE, meta_deadline: float = META_DEADLINE):
        self.exe, self.upstream, self.log_path = exe, upstream, log_path
        self.call_deadline, self.meta_deadline = call_deadline, meta_deadline
        self.port: int | None = None
        self.process: subprocess.Popen | None = None
        self.stopping = threading.Event()
        self.lock = threading.Lock()
        self.restarts = 0

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/mcp"

    def _command(self, port: int) -> list[str]:
        return [str(self.exe), "--listen", f"127.0.0.1:{port}", "--upstream", self.upstream,
                "--call-deadline", str(self.call_deadline), "--meta-deadline", str(self.meta_deadline),
                "--max-body", str(MAX_BODY)]

    def _launch(self, port: int) -> int:
        """Start the program and return the port it listens on (it prints `LISTENING <port>` first)."""
        errors = open(self.log_path, "ab") if self.log_path else subprocess.DEVNULL  # noqa: SIM115 (kept by the child)
        try:
            process = subprocess.Popen(self._command(port), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
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
            raise RuntimeError(f"the MCP gateway didn't start (exit {process.poll()}, said {line!r})")
        self.process = process
        return int(line.split()[1])

    def start(self, preferred: int = 0) -> str:
        with self.lock:
            try:
                self.port = self._launch(preferred)
            except RuntimeError:
                if not preferred:
                    raise
                self.port = self._launch(0)  # the last port is taken now
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
                        self._launch(self.port)
                    self.restarts += 1
                    pause = 0.5
                    break
                except (OSError, RuntimeError, ValueError) as error:
                    log.warning("MCP gateway restart failed: %s", error)
                    pause = min(pause * 2, 10.0)

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


def start(app_port: int, log_path: Path | None = None, port_file: Path | None = None) -> Gateway | None:
    """Start the gateway in front of the app's /mcp, or None when the helpers should connect to the app itself.

    `port_file` keeps the gateway's port across app starts: helpers connected by hand keep a working address."""
    exe = program()
    if exe is None:
        return None
    gateway = Gateway(exe, f"http://127.0.0.1:{app_port}/mcp", log_path)
    try:
        preferred = int(port_file.read_text().strip()) if port_file and port_file.is_file() else 0
    except (OSError, ValueError):
        preferred = 0
    try:
        gateway.start(preferred if 0 < preferred < 65536 and preferred != app_port else 0)
        if port_file:
            port_file.write_text(str(gateway.port), encoding="utf-8")
    except (OSError, RuntimeError, ValueError) as error:
        log.warning("MCP gateway unavailable, helpers use the app's own endpoint: %s", error)
        print(f"MCP gateway unavailable: {error}", file=sys.stderr)
        return None
    return gateway
