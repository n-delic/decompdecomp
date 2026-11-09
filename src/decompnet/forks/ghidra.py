"""Ghidra drafts: decompiled C for platforms m2c doesn't read (x86, AArch64, Xbox 360 PowerPC, SuperH...).

Optional: Ghidra is large (540 MB) and needs a Java 21+ JDK: the computer's, or the pinned one (java_home). Headless Ghidra analyses a unit's target object once
and decompiles all its functions; the result is cached next to the project, so the cost (tens of seconds,
mostly starting the JVM) is paid once per source file.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
from collections import defaultdict
from importlib import resources
from pathlib import Path

from .. import toolchains
from ..hostenv import NO_WINDOW
from ..limits import capped, default_cap, killed_for_memory
from .project import ForkError

JAVA_NEEDED = 21  # what the pinned Ghidra needs
HEAP_DEFAULT = 2 << 30  # analyzeHeadless's own Java heap (MAXMEM_DEFAULT)


def java_home() -> Path | None:
    """The JDK Ghidra runs with: None for the computer's own (a JDK, new enough), else the pinned one (toolchains.toml
    `jdk`), installed or not."""
    return None if toolchains.jdk_version() >= JAVA_NEEDED else toolchains.path("jdk")


def available() -> bool:
    home = java_home()
    return toolchains.installed("ghidra") is not None and (home is None or toolchains.installed("jdk") is not None)


def _env() -> dict:
    """Ghidra's environment: its JDK (java_home), and a Java heap of half its memory cap (limits.capped), not
    analyzeHeadless's fixed 2 GB, so a bigger game's analysis has room; the other half is for the decompiler
    processes its analysis starts. A heap the user set (GHIDRA_MAXMEM, GHIDRA_HEADLESS_MAXMEM) stays."""
    env = dict(os.environ)
    if not {"GHIDRA_MAXMEM", "GHIDRA_HEADLESS_MAXMEM"} & env.keys():
        env["GHIDRA_HEADLESS_MAXMEM"] = f"{max(HEAP_DEFAULT, default_cap() // 2) >> 20}M"
    if (home := java_home()) is not None:
        env.update(JAVA_HOME=str(home), PATH=os.pathsep.join([str(home / "bin"), env.get("PATH", "")]))
    return env


def _headless() -> Path:
    base = toolchains.path("ghidra") / "support"
    return base / ("analyzeHeadless.bat" if os.name == "nt" else "analyzeHeadless")


def _run_script(binary: Path, script: str, timeout: float, *, analyse: bool = True,
                said: list[str] | None = None) -> list[dict]:
    """Import `binary` into a throwaway Ghidra project, run one of our post-scripts on it and return the JSON objects
    it wrote, one per line ([] when it took longer than `timeout`). A Ghidra that didn't get to run the script says
    why (ForkError): that was "found no functions" before, which sent the user after packers and DRM. `said`, when
    given, gets what Ghidra printed, a line each."""
    with tempfile.TemporaryDirectory(prefix="decompwtf-ghidra-") as tmp:
        scripts = Path(tmp) / "scripts"
        scripts.mkdir()
        src = resources.files("decompnet.forks").joinpath("ghidra_scripts", script)
        (scripts / script).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        out = Path(tmp) / "out.jsonl"
        target = Path(tmp) / binary.name
        shutil.copyfile(binary, target)
        argv = [str(_headless()), tmp, "decompwtf", "-import", str(target), "-scriptPath", str(scripts),
                "-postScript", script, str(out), "-deleteProject", *([] if analyse else ["-noanalysis"])]
        try:
            # (stop_all: a decompiler process killed at the cap is Ghidra's to replace, not the end of the analysis)
            res = subprocess.run(capped(argv, stop_all=False), capture_output=True, text=True, timeout=timeout, env=_env(),
                                 encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
        except subprocess.TimeoutExpired:
            return []
        except OSError as e:
            raise ForkError(f"Ghidra couldn't start: {e}") from e
        if said is not None:
            said.extend((res.stdout + res.stderr).splitlines())
        if not out.exists():
            said = [ln for ln in (res.stdout + res.stderr).splitlines() if ln.strip()]
            if killed_for_memory(res.returncode) or any("java.lang.OutOfMemoryError" in ln for ln in said):
                raise ForkError(f"Ghidra ran out of memory analysing {binary.name}: it needed more than the "
                                f"{default_cap() / (1 << 30):.0f} GB decompdecomp lets one tool use (half this computer's "
                                "memory, at most 8 GB), so it was stopped before it could slow down or freeze your "
                                "computer. A computer with more memory may import it.")
            why = next((ln for ln in said if "ERROR" in ln), said[-1] if said else f"exit status {res.returncode}")
            if "JDK" in why:
                why += f" (Ghidra needs {toolchains.JDK_HINT})"
            raise ForkError(f"Ghidra didn't run: {why.strip()}")
        rows = []
        if out.exists():
            for line in out.read_text(encoding="utf-8").splitlines():
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
        return rows


# The background drafter asks for every function of a PC game, a source file's object at a time, on the workers
# every project shares: one Ghidra per object (the functions of one ask for it together), and a few at once (each
# starts a Java of its own, with a heap of half the memory cap)
_RUNS = threading.Semaphore(2)
_OBJECTS: dict[str, threading.Lock] = defaultdict(threading.Lock)


def draft_key(obj: Path) -> str:
    """Repairing the native decompiler invalidates both disk drafts and the engine's cached empty seeds (#139)."""
    native = toolchains.ghidra_decompiler(toolchains.path("ghidra"))
    try:
        stat = native.stat()
        identity = f"{stat.st_size}:{stat.st_mtime_ns}"
    except OSError:
        identity = "missing"
    return hashlib.sha1(b"native-drafts-v1\0" + identity.encode() + b"\0" + obj.read_bytes()).hexdigest()[:16]


def decompile_object(obj: Path, cache_dir: Path, timeout: float = 900) -> dict[str, str]:
    """name -> decompiled C for every function in an object file (cached by the object's content)."""
    key = draft_key(obj)
    cache = cache_dir / f"{obj.stem}-{key}.json"
    with _OBJECTS[str(cache)]:
        if cache.exists():
            return json.loads(cache.read_text(encoding="utf-8"))
        if not available():
            return {}
        try:
            with _RUNS:
                said: list[str] = []
                rows = _run_script(obj, "DecompWtfExport.java", timeout, said=said)
                if any(message in line for line in said for message in _NO_DECOMPILER):
                    return {}  # never cache a native-decompiler failure as an empty successful draft
                result = {row["name"]: row["c"] for row in rows}
        except ForkError:  # a draft is optional: none this time, and nothing cached, so it's tried again
            return {}
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(result), encoding="utf-8")
        return result


# What Ghidra prints when its decompiler is missing or won't start (DecompileProcessFactory, DecompileProcess)
_NO_DECOMPILER = ("decompile does not exist", "decompile.exe does not exist", "Decompiler executable may not be compatible")


def functions(program: Path, timeout: float = 3600) -> list[dict]:
    """Every function Ghidra's analysis finds in a whole program (a Windows .exe: Ghidra reads its imports, exports and
    the C runtime's functions too): {"entry", "end", "name", "source", "purge", "convention", "params", "thunk"}.

    Ghidra tells functions' calling conventions with its decompiler, a native program. Without it the analysis goes
    on, and every function's convention is "unknown": a Mac's Ghidra had none, and every __fastcall and __thiscall
    function of its games was imported as __cdecl. A decompiler that is missing or won't start stops the import
    (ForkError) instead. One that can't be run at all says nothing: no function with a convention says it then."""
    if not available():  # (an import and a reimport install it first: scratch.ensure_ghidra)
        raise ForkError("Ghidra isn't installed, or not all of it (its decompiler is missing, or the JDK it needs): "
                        "import the game again, and decompdecomp installs it (on a Mac that builds its decompiler, "
                        "with Xcode Command Line Tools)")
    said: list[str] = []
    rows = _run_script(program, "DecompWtfFunctions.java", timeout, said=said)
    why = next((ln.strip() for ln in said if any(s in ln for s in _NO_DECOMPILER)), None)
    found = [r for r in rows if not r.get("thunk")]
    if why is None and found and all(r.get("convention") in ("unknown", "null", None) for r in found):
        why = "no function it found got a calling convention"
    if why is not None:
        raise ForkError(f"Ghidra's decompiler didn't run ({why}), and without it Ghidra can't tell the functions' "
                        "calling conventions: decompdecomp would name them wrong. Delete Ghidra's folder "
                        f"({toolchains.path('ghidra')}) and import the game again: decompdecomp installs Ghidra again "
                        "(on a Mac that builds its decompiler, with Xcode Command Line Tools).")
    return rows
