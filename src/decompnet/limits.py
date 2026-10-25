"""Keep heavy tools from taking the user's computer down.

decompdecomp runs other people's tools on big inputs: project builds, decomp-toolkit's analysis, Ghidra. A runaway
one can eat all memory and swap until the system's out-of-memory killer takes the whole desktop session with it.
On Linux each heavy command runs in its own systemd scope with a memory cap and no swap, so only that command
is stopped.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from functools import lru_cache


def _total_memory() -> int:
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        return 8 << 30


def default_cap() -> int:
    """Half the machine's memory, between 2 and 8 GB."""
    return max(2 << 30, min(8 << 30, _total_memory() // 2))


@lru_cache(maxsize=1)
def _systemd_run() -> str | None:
    exe = shutil.which("systemd-run")
    if not exe or not os.environ.get("XDG_RUNTIME_DIR"):
        return None
    try:  # the user manager has to be reachable (not the case in some containers or SSH sessions)
        ok = subprocess.run([exe, "--user", "--scope", "--quiet", "-p", "MemoryMax=64M", "--", "true"],
                            capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        ok = False
    return exe if ok else None


def capped(argv: list[str], memory: int | None = None, stop_all: bool = True) -> list[str]:
    """`argv`, wrapped so it's stopped when it uses more than `memory` bytes (default: default_cap()). Over the cap
    the kernel kills the command's biggest process, and systemd then stops the rest of it; with `stop_all` False the
    rest carries on without that process, for a tool that copes: Ghidra's analysis of a 6 MB game ran one decompiler
    process up to 6 GB, and when the kernel killed it at the cap systemd stopped Ghidra too, which then said nothing
    about why. Ghidra starts another decompiler for the next function."""
    exe = _systemd_run()
    if exe is None:
        return list(argv)
    mb = (memory or default_cap()) >> 20
    keep = [] if stop_all else ["-p", "OOMPolicy=continue"]
    return [exe, "--user", "--scope", "--quiet", "-p", f"MemoryMax={mb}M", "-p", "MemorySwapMax=0", *keep, "--", *argv]


def no_core_dumps() -> None:
    """No core dumps from this process or anything it starts: a compiler run through wibo aborts on a Windows
    function wibo doesn't have, and a period compiler can crash on odd C, so the desktop's crash reporter put up a
    dialog for each one an agent's attempt hit. Children inherit the limit, and systemd-coredump honours it."""
    try:
        import resource
    except ImportError:  # Windows: no core dumps to stop
        return
    try:
        resource.setrlimit(resource.RLIMIT_CORE, (0, resource.getrlimit(resource.RLIMIT_CORE)[1]))
    except (ValueError, OSError):
        pass


def killed_for_memory(returncode: int) -> bool:
    return returncode in (137, -9)
