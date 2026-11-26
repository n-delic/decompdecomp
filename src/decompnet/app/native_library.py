"""Keep the repair library compiled, so the app runs its compiled code without anybody building it.

`uv sync` installs decomprepair as `.py` files and `.pyx` kernels and compiles nothing, so a checkout, and every update
of an installed copy, ran interpreted until somebody remembered to run the library's `native_build.py`. Now the app
runs it itself, in two parts:

- `ensure_kernels`, as the app starts and before anything imports the library (its modules choose between a kernel and
  the plain Python once, when imported): the typed kernels, seconds.
- `Compiler`, afterwards in the background: the plain modules the library lists, at low priority on a few cores.
  That is a minute or two the first time and only what changed after an update. Processes that start later (the
  repair proposals, permuter workers) use them as they arrive; the app itself from its next start.

Nothing here is needed for correct results: without a C compiler, in a copy that can't be written to, or with
`DECOMP_NATIVE_LIBRARY=0`, the library stays as it is and behaves the same. This module must not import decomprepair.
"""
import importlib.util
import logging
import os
import shutil
import subprocess
import sys
import sysconfig
import threading
from pathlib import Path

from .. import limits
from ..hostenv import NO_WINDOW

log = logging.getLogger("decompdecomp")

KERNEL_SECONDS = 600
BATCH_SECONDS = 1800  # the largest modules take minutes each
MODULES_PER_JOB = 4
START_AFTER_SECONDS = 60  # leave the app's own startup work the machine first


def package_folder() -> Path | None:
    """Where the installed library is, found without importing it."""
    spec = importlib.util.find_spec("decomprepair")
    places = list(spec.submodule_search_locations or []) if spec else []
    return Path(places[0]) if places else None


def _has_c_compiler() -> bool:
    if sys.platform == "win32":
        return True  # setuptools looks for Visual C++ itself, and the build says so when there is none
    if sys.platform == "darwin":
        # asked first: running `cc` without the Command Line Tools opens Apple's install dialog
        try:
            return subprocess.run(["xcode-select", "-p"], capture_output=True, timeout=30).returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False
    compiler = (sysconfig.get_config_var("CC") or "cc").split()
    return shutil.which(compiler[0]) is not None


def unavailable(package: Path | None) -> str:
    """Why this copy can't compile the library, or '' when it can."""
    if os.environ.get("DECOMP_NATIVE_LIBRARY") == "0":
        return "DECOMP_NATIVE_LIBRARY=0"
    if package is None or not (package / "native_build.py").is_file():
        return "this decomprepair has no native_build.py"
    if "--missing" not in (package / "native_build.py").read_text(encoding="utf-8"):
        return "this decomprepair is too old to build only what is missing"
    if any(part.endswith(".app") for part in package.parts):
        return "a signed application bundle is compiled when it is built"
    if not os.access(package, os.W_OK):
        return "the installed library can't be written to"
    if any(importlib.util.find_spec(name) is None for name in ("Cython", "setuptools")):
        return "Cython or setuptools is not installed (run `uv sync`)"
    if not _has_c_compiler():
        return "no C compiler (macOS: `xcode-select --install`; Linux: gcc)"
    return ""


def _command(package: Path, *arguments: str) -> list[str]:
    # -P: the library's flat module names must not shadow what the build itself imports
    return [sys.executable, "-P", str(package / "native_build.py"), "--package", str(package), *arguments]


def _run(command: list[str], seconds: float, jobs: int = 1) -> str:
    """The build's output; '' with a log line when it couldn't run. It never stops the app."""
    priority = {"creationflags": NO_WINDOW}
    if sys.platform == "win32":
        priority = {"creationflags": NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS}
    elif shutil.which("nice"):
        command = ["nice", "-n", "10", *command]
    try:
        done = subprocess.run(limits.capped(command), capture_output=True, text=True, timeout=seconds,
                              env=dict(os.environ, DECOMPREPAIR_NATIVE_JOBS=str(jobs)), **priority)
    except (OSError, subprocess.SubprocessError) as error:
        log.warning("compiling the repair library failed: %s", error)
        return ""
    if done.returncode or "not built" in done.stdout:
        log.warning("compiling the repair library: %s", (done.stdout + done.stderr).strip()[-1000:])
    return done.stdout


def missing(package: Path, everything: bool = False) -> list[str]:
    return _run(_command(package, "--list-missing", *(["--all"] if everything else [])), KERNEL_SECONDS).split()


def ensure_kernels(package: Path | None = None) -> bool:
    """Build the typed kernels that have no current build. True when they are all built afterwards."""
    package = package or package_folder()
    if unavailable(package):  # the app's log says why once it is open (cli.run)
        return False
    wanted = missing(package)
    if not wanted:
        return True
    print(f"    compiling {', '.join(wanted)} (once per library update)…", flush=True)
    _run(_command(package, "--missing"), KERNEL_SECONDS)
    left = missing(package)
    if left:
        log.warning("the repair library's kernels were not built: %s", ", ".join(left))
    return not left


class Compiler:
    """Builds the plain modules in the background; `snapshot` is its row in Background work."""

    def __init__(self, package: Path | None = None, *, delay: float = START_AFTER_SECONDS, jobs: int | None = None):
        self.package = package
        self.delay = delay
        self.jobs = jobs or max(1, min(4, (os.cpu_count() or 1) // 4))  # the machine stays the user's
        self.stopping = threading.Event()
        self.thread = None
        self.total = self.done = 0

    def start(self) -> None:
        self.thread = threading.Thread(target=self._run, daemon=True, name="compile-library")
        self.thread.start()

    def _run(self) -> None:
        if self.stopping.wait(self.delay):
            return
        try:
            self.package = self.package or package_folder()
            # without the kernels there is no working compiler, and a long run of failures helps nobody
            if unavailable(self.package) or missing(self.package):
                return
            names = missing(self.package, everything=True)
            self.total = len(names)
            # several modules per core in each run: a run per module spent its time starting Python and waiting for
            # the slowest, and Stop still takes effect within seconds, between runs
            size = self.jobs * MODULES_PER_JOB
            for start in range(0, len(names), size):
                if self.stopping.is_set():
                    return
                group = names[start:start + size]
                _run(_command(self.package, "--only", ",".join(group), "--missing"), BATCH_SECONDS, self.jobs)
                self.done += len(group)
            if names:
                left = missing(self.package, everything=True)
                log.info("compiled %d of %d repair library modules", len(names) - len(left), len(names))
        except Exception:  # an optional speed-up: its thread says why and ends
            log.exception("compiling the repair library stopped")
        finally:
            self.total = self.done = 0

    def stop(self) -> None:
        self.stopping.set()

    def snapshot(self) -> dict | None:
        total, done = self.total, self.done
        if not total:
            return None
        return {"state": "stopping" if self.stopping.is_set() else "running",
                "detail": f"{done} of {total} modules compiled. The app uses them from its next start."}


compiler = Compiler()
