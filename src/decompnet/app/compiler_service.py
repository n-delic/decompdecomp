"""What Settings shows about compiler services (docs/HOSTED.md): this computer's part in hosted projects.

A compiler service (a verifier) compiles the submissions agents make on a hosted server, so their matches count. Any
computer with decompdecomp and the games' compilers can be one, this one included; with verification packs it needs
no game. Settings explains how and fills the commands in for this computer: where its gateway program and `decomp`
are, which compilers it has. It also shows the projects here that take a hosted server's matches in
(hosted_sync.py). Tokens are never shown or kept here: the commands read them from the environment.
"""
from __future__ import annotations

import os
import shutil
import socket
import sys
from pathlib import Path


def decomp_command() -> str:
    """How this computer runs decompdecomp's command line: beside this Python (a checkout's or package's), else PATH."""
    here = Path(sys.executable).with_name("decomp" + (".exe" if os.name == "nt" else ""))
    if here.is_file():
        return str(here)
    return shutil.which("decomp") or "decomp"


def installed_compilers() -> list[str]:
    from .. import toolchains
    found = []
    for tid in sorted(toolchains.registry()):
        try:
            if toolchains.installed(tid) is not None:
                found.append(tid)
        except Exception:
            continue
    return found


def info(net, internal: Path) -> dict:
    from . import hosted_sync, mcp_gateway
    gateway = mcp_gateway.built()
    cores = os.cpu_count() or 1
    last = getattr(getattr(net, "hosted_sync", None), "last", {}) or {}
    links = [{"slug": item["slug"], "server": item["server"], "remote": item.get("remote") or item["slug"],
              "since": item.get("since") or 0, "last": last.get(item["slug"])}
             for item in hosted_sync.read_links(internal)]
    return {
        "gateway": str(gateway) if gateway else None,
        "decomp": decomp_command(),
        "compilers": installed_compilers(),
        "cores": cores,
        "slots": max(1, cores - 2),  # (workers.RESERVED_CORES: two left for the app and its agents)
        "name": socket.gethostname().split(".")[0][:60] or "compiler-service",
        "packs": str(Path.home() / "decomp-packs"),
        "links": links,
        "docs": "https://github.com/RetroReversing/decompdecomp/blob/main/docs/HOSTED.md",
    }
