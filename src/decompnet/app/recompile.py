"""Recompile: the game's own instructions as C, by the console's static recompiler (Console.recompiler; N64:
consoles/n64/recomp.py, N64Recomp), from the project's split and the user's ROM (docs/N64RECOMP.md, phase 0).

The Porting tab's Recompile button runs it as a job; Port readiness shows its last summary. The output is derived from
the ROM, so it goes in the project's build folder, which git ignores.
"""
from __future__ import annotations

import threading
from pathlib import Path

from .. import toolchains
from ..network import IN_BUILD, NetworkError


def run(net, slug: str, log=print) -> dict:
    with net.progress.view():
        proj = net.project_row(slug)
        console = net.console(proj)
        rows = net.project_store(proj["id"]).all(
            f"SELECT name, vram, rom_offset, size FROM functions WHERE project_id=? AND {IN_BUILD} "
            "AND vram IS NOT NULL AND rom_offset IS NOT NULL", proj["id"])
    if console.recompiler is None:
        raise NetworkError(f"{console.name} has no static recompiler in decompdecomp yet (ROADMAP.md): Recompile "
                           "works on N64 games for now")
    if not net.is_rom(proj):
        raise NetworkError("Recompile reads projects imported from a ROM: this one has no ROM to recompile")
    cfg = net.cfg(proj)
    root = Path(proj["root"])
    rom = root / cfg["rom"]["file"]
    if not rom.is_file():
        raise NetworkError(f"the game's ROM isn't at {rom} any more: import it again to recompile it")
    tool = console.recompiler.TOOL
    toolchains.ensure(tool, log=log)
    functions = [{"name": r["name"], "vram": r["vram"], "rom": r["rom_offset"], "size": r["size"] or 0} for r in rows]
    stop = getattr(log, "cancel_event", None) or threading.Event()
    try:
        return console.recompiler.recompile(toolchains.binary(tool), rom, cfg.get("segments") or [], functions,
                                            console.recompiler.folder(root), log, stop.is_set)
    except console.recompiler.RecompError as error:
        raise NetworkError(str(error)) from None


def summary(net, proj) -> dict | None:
    """The last Recompile's summary for Port readiness (it never runs one), or None."""
    recompiler = net.console(proj).recompiler
    return recompiler.load(Path(proj["root"])) if recompiler is not None else None
