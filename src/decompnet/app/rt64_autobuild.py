"""Build the RT64 adapter in the background when the app starts, so N64ModernRuntime's Play is there to choose.

The build (consoles/n64/rt64_build.py) fetches pinned sources and compiles for several minutes, so it runs once per
change of the pinned sources or the adapter's code, in its own low-priority process, as a row in Background work with
Stop. It never installs Xcode or its Metal Toolchain: without them it stays quiet and says why in the log. A failure
is remembered for that stamp, so a broken build is not retried at every start; running
`tools/build_rt64_adapter.py` by hand tries again. `DECOMP_RT64_AUTOBUILD=0` turns it off."""

import json
import logging
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

from ..consoles.n64 import port, rt64_build

log = logging.getLogger("decompdecomp")
FAILED = "build-failed.json"
START_AFTER_SECONDS = 60  # the app is open and its first work is under way before a long build starts
BUILD_SECONDS = 3 * 3600


def unavailable() -> str:
    """Why the app doesn't build the adapter itself now, or '' when it should."""
    if sys.platform != "darwin":
        return "only macOS has the adapter"
    if os.environ.get("DECOMP_RT64_AUTOBUILD") == "0":
        return "DECOMP_RT64_AUTOBUILD=0"
    if os.environ.get("DECOMP_AUTO_TOOLS", "on") == "off":
        return "DECOMP_AUTO_TOOLS=off"
    if os.environ.get("DECOMPNET_RT64_ADAPTER"):
        return "DECOMPNET_RT64_ADAPTER names an adapter of the user's own"
    bundled = Path(sys.executable).parent.parent.parent / "Frameworks/rt64/libdecompnet-rt64.dylib"
    if bundled.is_file():
        return "the app carries its own adapter"
    if rt64_build.built():
        return "the adapter is current"
    try:
        if json.loads((rt64_build.folder() / FAILED).read_text()) == rt64_build.stamp():
            return "this build already failed here; run tools/build_rt64_adapter.py to try again"
    except (OSError, ValueError):
        pass
    return rt64_build.problem()


class AutoBuild:
    """`snapshot` is its row in Background work."""

    def __init__(self, delay: float = START_AFTER_SECONDS):
        self.delay = delay
        self.stopping = threading.Event()
        self.thread = None
        self.process = None
        self.phase = ""

    def start(self) -> None:
        self.thread = threading.Thread(target=self._run, daemon=True, name="rt64-adapter-build")
        self.thread.start()

    def _run(self) -> None:
        if self.stopping.wait(self.delay):
            return
        try:
            if reason := unavailable():
                log.info("not building the RT64 adapter: %s", reason)
                return
            self.phase = "Starting"
            command = [sys.executable, "-m", "decompnet.consoles.n64.rt64_build"]
            if shutil.which("nice"):
                command = ["nice", "-n", "10", *command]
            log.info("building the RT64 adapter in the background")
            # half the cores at most: the machine stays the user's
            env = dict(os.environ, DECOMP_RT64_JOBS=str(max(1, (os.cpu_count() or 2) // 2)))
            self.process = subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                            text=True)
            tail = []
            for line in self.process.stdout:
                line = line.strip()
                if line:
                    tail = [*tail, line][-30:]
                    if not line.startswith(("[", "-- ", "ninja")):
                        self.phase = line[:120]
            code = self.process.wait(timeout=BUILD_SECONDS)
            if self.stopping.is_set():
                return
            if code:
                log.warning("building the RT64 adapter failed (%s): %s", code, "\n".join(tail)[-1500:])
                folder = rt64_build.folder()
                folder.mkdir(parents=True, exist_ok=True)
                (folder / FAILED).write_text(json.dumps(rt64_build.stamp()), encoding="utf-8")
            else:
                (rt64_build.folder() / FAILED).unlink(missing_ok=True)
                log.info("built the RT64 adapter: %s", port.rt64_adapter())
        except Exception:  # an optional extra: its thread says why and ends
            log.exception("building the RT64 adapter stopped")
        finally:
            self.process = None
            self.phase = ""

    def stop(self) -> None:
        self.stopping.set()
        if process := self.process:
            process.terminate()

    def snapshot(self) -> dict | None:
        if not self.process:
            return None
        return {"state": "stopping" if self.stopping.is_set() else "running",
                "detail": (self.phase or "Working") + ". N64ModernRuntime's Play uses it once it is built."}


build = AutoBuild()
