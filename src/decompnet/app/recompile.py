"""Recompile: the game's own instructions as C, by the console's static recompiler (Console.recompiler; N64:
consoles/n64/recomp.py, N64Recomp), from the project's split and the user's ROM (docs/N64RECOMP.md, phase 0).

The Porting tab's Recompile button runs it as a job; Port readiness shows its last summary. The output is derived from
the ROM, so it goes in the project's build folder, which git ignores.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from pathlib import Path

from .. import toolchains
from ..network import IN_BUILD, NetworkError
from ..writer_gate import WriterGate


@contextmanager
def build_turn(folder, log, stop=lambda: False, *, resource="N64Recomp output", slug=""):
    """Keep the generated directory stable until its writer or reader is done."""
    folder = Path(folder).resolve()
    folder.parent.mkdir(parents=True, exist_ok=True)
    cancel_event = getattr(log, "cancel_event", None)
    check_cancel = getattr(log, "check_cancel", lambda: None)
    waiting = False
    progress = getattr(log, "progress", lambda value: None)

    def check():
        check_cancel()
        if stop() or (cancel_event is not None and cancel_event.is_set()):
            raise NetworkError("Stopped.")

    def wait():
        nonlocal waiting
        check()
        if not waiting:
            waiting = True
            phase = f"Waiting for another job using this game's {resource}"
            log(phase)
            progress(dict(phase=phase, slug=slug))

    check()
    with WriterGate(folder).hold(wait=wait):
        check()
        if waiting:
            progress(dict(phase=f"Preparing {resource}", slug=slug))
        yield


def run(net, slug: str, log=print) -> dict:
    proj = net.project_row(slug, sync=False)
    recompiler = net.console(proj).recompiler
    if recompiler is None:
        return _run(net, slug, log)
    if not net.is_rom(proj):
        return _run(net, slug, log)
    with build_turn(recompiler.folder(Path(proj["root"])), log, slug=slug):
        return _run(net, slug, log)


@contextmanager
def ready(net, slug, proj, recompiler, log, stop=lambda: False):
    """Build once on demand, then keep the result stable while the caller reads it."""
    root = Path(proj["root"])
    folder = recompiler.folder(root)
    with build_turn(folder, log, stop, slug=slug):
        saved = recompiler.load(root)
        needs_recompile = getattr(recompiler, "needs_recompile", None)
        if saved is None or (needs_recompile and needs_recompile(saved, net.cfg(proj), root)):
            log("Recompiling the game with N64Recomp for the functions with no C yet")
            _run(net, slug, log, stop)
        yield folder


def _run(net, slug: str, log, stop=lambda: False) -> dict:
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
    cancel = getattr(log, "cancel_event", None) or threading.Event()
    try:
        return console.recompiler.recompile(toolchains.binary(tool), rom, cfg.get("segments") or [], functions,
                                            console.recompiler.folder(root), log, lambda: stop() or cancel.is_set())
    except console.recompiler.RecompError as error:
        raise NetworkError(str(error)) from None


def summary(net, proj) -> dict | None:
    """The last Recompile's summary for Port readiness (it never runs one), or None."""
    recompiler = net.console(proj).recompiler
    return recompiler.load(Path(proj["root"])) if recompiler is not None else None
