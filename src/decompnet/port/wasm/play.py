"""Play: the app's button that builds a game's WebAssembly build and runs it in the browser (docs/PORTING.md).

The game opens in a window of its own, made by the app window's program (app/window.py), and the browser where no
window can open (a browser session, tests); closing the window stops the game. The build comes from the project's
save repository (the folder Save writes, its progress/ inside), as
`python -m decompnet.port.wasm` builds it; only what changed is rebuilt. The game then runs with its live view
(the host's PORT_LIVE, on 127.0.0.1 only, on a free port), its saves in the build folder (game.eep, beside the
program: game data, kept out of git), until it's stopped or the app quits. One game runs per project: Play again
returns the one running.
"""

from __future__ import annotations

import atexit
import json
import os
import re
import socket
import subprocess
import threading
import time
from pathlib import Path

from ...network import NetworkError

STARTING = "Starting the game"  # the progress bar's last step, after the build's
_running: dict[str, tuple[subprocess.Popen, str]] = {}
_native: set[str] = set()
_windows: dict[str, subprocess.Popen] = {}  # each game's own window, closed when the game stops
_sessions: dict = {}  # each game played beside the console: its side-by-side viewer (side_by_side.Session)
_lock = threading.Lock()


def save_repository(folder: Path) -> Path:
    """The save repository holding `folder`, the project's progress folder: the build reads its progress/ and port/."""
    folder = Path(folder)
    for repo in (folder.parent, folder):
        # (a save of a game with nothing matched yet has no progress/src: it still runs, from the recompiled code
        # and the ROM interpreter)
        if (repo / "progress" / "decompdecomp.json").is_file():
            return repo
    raise NetworkError(f"{folder} isn't a saved project yet: Save the project first, then Play builds from the save")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _answers(url_port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", url_port), timeout=0.5):
            return True
    except OSError:
        return False


def running_result(slug: str) -> dict | None:
    """The live view and window type of a running game."""
    with _lock:
        entry = _running.get(slug)
        if entry and entry[0].poll() is None:
            view = {"url": entry[1], "native": True} if slug in _native else {"url": entry[1]}
            return {**view, "compare": True} if slug in _sessions else view
        _running.pop(slug, None)
        _native.discard(slug)
        _end_session(slug)
        return None


def _end_session(slug: str) -> None:
    session = _sessions.pop(slug, None)
    if session is not None:
        session.close()


def running(slug: str) -> str | None:
    result = running_result(slug)
    return result["url"] if result else None


def native_adapter(net, slug: str, runtime_id: str) -> Path:
    project = net.project_row(slug, sync=False)
    kit = net.console(project).port_kit
    if kit and net.is_rom(project):
        for runtime in kit.runtimes:
            if runtime.id == runtime_id and "compatibility" in runtime.modes and runtime.native_adapter:
                if path := runtime.native_adapter():
                    return path
                raise NetworkError("Configure the macOS RT64 adapter before playing with N64ModernRuntime.")
    raise NetworkError("Choose N64 compatibility and the decompdecomp runtime to play or export today.")


def end(proc: subprocess.Popen, grace: float = 3.0) -> None:
    """Ends a game's process: asked to stop, then killed if it doesn't within `grace` seconds. A game stuck in a
    loop of its own code (a boot that never leaves its loader) ignored the request and kept a core busy for an
    hour after Stop."""
    proc.terminate()
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=grace)


def stop(slug: str) -> bool:
    with _lock:
        entry = _running.pop(slug, None)
        _native.discard(slug)
        win = _windows.pop(slug, None)
    _end_session(slug)
    if win is not None and win.poll() is None:
        win.terminate()
    if entry and entry[0].poll() is None:
        end(entry[0])
        return True
    return False


@atexit.register
def _stop_all() -> None:
    for slug in list(_running):
        stop(slug)


def game(net, slug: str) -> tuple[Path, dict, Path]:
    """The project's save repository, its config and its ROM, for Play and Export."""
    from ...app import saves

    # Without synchronizing the save folder: the build reads the save as it is on disk, and a sync waits behind the
    # helpers' upkeep (minutes while they work, with nothing in Play's log)
    with net.progress.view():
        proj = net._project(slug)
    if not net.is_rom(proj):
        raise NetworkError("Play builds games imported from a ROM; this project isn't one")
    cfg = net.cfg(proj)
    rom = Path(proj["root"]) / cfg["rom"]["file"]
    if not rom.is_file():
        raise NetworkError(f"the game's ROM isn't at {rom} any more: import it again to play")
    return save_repository(saves.default_folder(net, slug)), cfg, rom


def library(net, slug: str, log=print) -> set[str]:
    """The save's files of library code the host provides, proven by the project's library detection (the SDK
    folders the user indexed, the console's built-in signatures, identities the user confirmed): the files the
    build leaves out besides the save's own exclude.txt.

    A save made on another computer had no exclude.txt and named libultra func_<address>, so its WebAssembly
    build compiled the Controller Pak's internals (`OSPifRam` undeclared) and failed. What the port kit says the
    host or the port's glue replaces goes (`sdk_group`: "replaced", "glue"); what runs as the game's own C stays
    (the matrix helpers, the synthesizer: "native")."""
    from ...app.saves import _source_file
    from ...app.sdk_identity import display_name, identify
    from ..identities import confirmed

    with net.progress.view():
        proj = net._project(slug)
    kit = net.console(proj).port_kit
    if kit is None:
        return set()
    sdk = identify(net, proj, net.sdk_functions(proj), include_asm=True, builtin=True) | confirmed(net, proj)
    # Every function, not only the matched: a save also carries C that isn't proven yet (a twin from another game,
    # a draft), and a racing game's twin of osEPiStartDma, still open, was compiled over the host's: it posted to
    # libultra's PI manager, which the host doesn't run, and the boot thread waited for its first DMA for ever.
    rows = net.project_store(proj["id"]).all("SELECT id, name FROM functions WHERE project_id=?", proj["id"])
    out = set()
    for r in rows:
        hint = sdk.get(r["id"])
        names = (hint.get("names") or [display_name(hint, r["name"])]) if hint else [r["name"]]
        # a function already named as the library's (an N64 game's _Printf, its IDO va_arg C beyond clang) needs
        # no detection to say so
        if {"replaced", "glue"} & {kit.sdk_group(n) for n in names}:
            out.add(_source_file(r["name"]))
    if out:
        log(f"leaving out {len(out)} functions of libultra the host provides (found by library detection)")
    return out


def unverified(net, slug: str, log=print) -> set[str]:
    """The save's files of C for functions the project doesn't count as matched: the build leaves them out, and
    those functions run as the game's own code instead (recompiled, or interpreted from the player's ROM). A save
    copied while it was being rewritten after a recheck still had the C of 296 functions the recheck had found
    wrong: a racing game's switch matched another game's jump table, not its own, and its car selection said every
    car broke its class's rules, so it never raced. A save made elsewhere (a contributor's, an older one) can carry
    the same. The project's own matches are what the save holds when it's whole, so this leaves nothing out then."""
    from ...app.saves import _source_file

    with net.progress.view():
        proj = net._project(slug)
    root = default_save(net, slug)
    rows = net.project_store(proj["id"]).all("SELECT name FROM functions WHERE project_id=? AND status!='matched'",
                                             proj["id"])
    out = {_source_file(r["name"]) for r in rows}
    if root is not None:
        out = {name for name in out if (root / "progress" / "src" / name).is_file()}
    if out:
        log(f"leaving out the save's C for {len(out)} functions the project doesn't count as matched: they run as "
            "the game's own code")
    return out


def default_save(net, slug: str) -> Path | None:
    """The save repository the build reads (game), or None if there's none to look in."""
    from ...app import saves
    try:
        return save_repository(saves.default_folder(net, slug))
    except Exception:  # noqa: BLE001  (no save: nothing of it to leave out)
        return None


def host_defined(kit) -> set[str]:
    """The library routines the host's own C defines (`w2c_env_<name>`, leading underscores dropped): the names a call
    can be sent to and be answered."""
    from .recomp import _demangle

    out = set()
    folder = Path(kit.wasm) if kit and getattr(kit, "wasm", None) else None
    for path in sorted(folder.glob("*.c")) if folder and folder.is_dir() else ():
        for m in re.finditer(r"\bw2c_env_(\w+)\(", path.read_text(encoding="utf-8", errors="replace")):
            out.add(_demangle(m.group(1)).lstrip("_"))
    return out


def host_provides(kit, names) -> bool:
    """Whether the port's host answers a function known by these library names: the port says it replaces or
    supplies it (what `library` decides leaves a matched function's C out) AND the host defines the routine. The
    port's "glue" group is every other os* name, though the host defines only some: a game's matched
    osPiRawStartDma / osPiRawReadIo, sent to a name the host lacks, ran as a stub, and the game's file reader
    looped on headers for ever."""
    if not kit:
        return False
    names = [n for n in names or () if kit.sdk_group(n) in ("replaced", "glue")]
    return bool(names) and bool({n.lstrip("_") for n in names} & (host_defined(kit)))


def library_names(net, slug: str) -> dict[str, str]:
    """{a function's name: its library name} for library code that doesn't run as the game's own C (no matched C,
    or the host provides it), found by library detection: its
    calls go to the library's name, which the host answers by name in any game (a racing game's bzero kept its
    automatic name, `func_8003DDC0`, and the host stubbed it to nothing). One name each; names already the
    library's, and the user's confirmed identities, as they are."""
    from ...app.sdk_identity import ambiguous, identify
    from ..identities import confirmed

    with net.progress.view():
        proj = net._project(slug)
    sdk = identify(net, proj, net.sdk_functions(proj), include_asm=True, builtin=True) | confirmed(net, proj)
    shared = ambiguous(net, proj, builtin=True)  # (several library routines with one body: see below)
    kit = net.console(proj).port_kit
    out = {}
    for r in net.project_store(proj["id"]).all("SELECT id, name, status, meta FROM functions WHERE project_id=?",
                                               proj["id"]):
        # detection's identity, else the name it showed (`sdk_display`): an exact-bytes proof of hand-written assembly
        # goes stale with the project's compiler setting (its fingerprint holds it), not with the bytes
        names = (sdk.get(r["id"]) or {}).get("names") or (shared.get(r["id"]) or {}).get("names") or []
        # A matched function keeps its automatic name too when polishing hasn't renamed it. When the host provides it
        # (`library` leaves its C out), recompiled code must call the host's: a battle game's osRecvMesg
        # (func_8004DE70, matched) ran as a recompiled copy against kernel routines the host doesn't have, and every
        # worker thread ended at once.
        if r["status"] == "matched" and not host_provides(kit, names):
            continue
        if not names and '"sdk_display"' in (r["meta"] or ""):
            shown = (json.loads(r["meta"]).get("sdk_display") or {}).get("name") or ""
            names = [shown] if shown and " / " not in shown else []
        # Routines whose bodies are identical share one fingerprint, so detection names them all (osViGetCurrent-
        # and osViGetNextFramebuffer are the same 64 bytes in libultra 2.0I-2.0L). When the host answers some of
        # them, any of those is the right call: a fighting game's main thread waited in its own copy of
        # osViGetCurrentFramebuffer for the buffer it had handed the VI, which only the host's VI ever shows.
        if len(names) > 1:
            hosted = sorted(n for n in names if host_provides(kit, [n]))
            names = hosted[:1] or names
        if len(names) == 1 and names[0] != r["name"] and re.fullmatch(r"[A-Za-z_]\w*", names[0]):
            out[r["name"]] = names[0]
    return out


def relative_bank_code(net, slug: str, log=print) -> set[str]:
    """Do not compile analysis-only functions as if their bank-relative offsets were runtime addresses."""
    from ...app.saves import _source_file

    with net.progress.view():
        proj = net._project(slug)
    recompiler = net.console(proj).recompiler
    classify = getattr(recompiler, "banked_functions", None)
    if classify is None:
        return set()
    rows = net.project_store(proj["id"]).all(
        "SELECT name, rom_offset, size FROM functions WHERE project_id=? AND rom_offset IS NOT NULL", proj["id"])
    functions = [{"name": r["name"], "rom": r["rom_offset"], "size": r["size"]} for r in rows]
    cfg = net.cfg(proj)
    rom = cfg.get("rom") or {}
    image_name = rom.get("donor_analysis_base") or rom.get("file")
    image_path = Path(proj["root"]) / image_name if image_name else None
    if image_path and not image_path.is_file() and rom.get("file"):
        image_path = Path(proj["root"]) / rom["file"]
    image = image_path.read_bytes() if image_path and image_path.is_file() else None
    names = {_source_file(f["name"]) for f in classify(cfg.get("segments") or [], functions, image)}
    if names:
        log(f"leaving out C for {len(names)} relocatable overlay functions until their runtime addresses are known")
    return names


def code_offsets(net, slug: str) -> dict | None:
    """Where each function's machine code is in the image the project analysed ({"image": path, "offsets": {function:
    ROM offset}}). The cartridge itself holds a compressed game's code compressed (a game that unpacks its code into
    RAM at boot): the project's analysis image, with the code unpacked, is where a function's bytes can be read for
    what the build works out from them (port/wasm/library_data.py)."""
    with net.progress.view():
        proj = net._project(slug)
    cfg = net.cfg(proj)
    rom = cfg.get("rom") or {}
    name = rom.get("donor_analysis_base") or rom.get("file")
    image = Path(proj["root"]) / name if name else None
    if image and not image.is_file() and rom.get("file"):
        image = Path(proj["root"]) / rom["file"]
    if not image or not image.is_file():
        return None
    rows = net.project_store(proj["id"]).all(
        "SELECT name, rom_offset FROM functions WHERE project_id=? AND rom_offset IS NOT NULL", proj["id"])
    return {"image": str(image), "offsets": {r["name"]: r["rom_offset"] for r in rows}}


def inputs(net, slug: str, log=print) -> dict:
    """What the build takes from the project besides its save: the library the host provides (`leave_out`), library
    code built from ultralib's C (`library_c`), library code's own names (`library_names`), and the recompiled C for
    functions with no C yet (`recomp`)."""
    return {"leave_out": library(net, slug, log) | unverified(net, slug, log) | relative_bank_code(net, slug, log), "library_c": library_c(net, slug),
            "library_names": library_names(net, slug), "recomp": recompiled(net, slug, log),
            "function_headers": function_headers(net, slug), "code_offsets": code_offsets(net, slug)}


def function_headers(net, slug: str) -> dict[str, str]:
    """Which part's own header each function compiles with ({function: part}), from the project as it is now, as a
    save names them (saves.py). A save written before saves named them (1da025c3) has none, so its WebAssembly build
    gave every function the shared header and 268 files of an N64 game stopped at `unknown type name`."""
    from ...app.saves import PRIVATE_HEADERS
    with net.progress.view():
        proj = net._project(slug)
    units = net._unit_contexts(proj["id"])
    rows = net.project_store(proj["id"]).all("SELECT name,unit FROM functions WHERE project_id=?", proj["id"])
    return {r["name"]: r["unit"] for r in rows if r["unit"].startswith(PRIVATE_HEADERS) and r["unit"] in units}


def recompiled(net, slug: str, log=print) -> dict | None:
    """The console's recompiler's output for the port ({"folder", "header"}), run first when there's none yet: the
    game's every function as C (Porting → Recompile), so a function with no matched C runs as its recompiled C
    instead of stopping the game. None when the console has no recompiler or it can't run here (the port then has
    the game's C alone, as before)."""
    with net.progress.view():
        proj = net._project(slug)
    recompiler = net.console(proj).recompiler
    if recompiler is None or not net.is_rom(proj):
        return None
    from ...app import recompile
    try:
        with recompile.ready(net, slug, proj, recompiler, log) as folder:
            header = recompiler.toolchain_folder() / "include" / "recomp.h"
    except Exception as error:  # noqa: BLE001  the interpreter can still run functions missing C
        getattr(log, "check_cancel", lambda: None)()
        log(f"Could not prepare recompiled functions: {error}. The port will use the ROM interpreter for them.")
        return None
    return {"folder": str(folder), "header": str(header)} if header.is_file() else None


def library_c(net, slug: str) -> dict:
    """Library functions set aside as SDK code that the port builds from the user's ultralib (port/ultralib.units)."""
    from .. import ultralib
    return ultralib.units(net, slug)


def project_symbols(net, slug: str):
    """The project's names for addresses (its symbols and functions), for the side-by-side viewer's difference list."""
    from .side_by_side import Symbols

    with net.progress.view():
        proj = net._project(slug)
    store = net.project_store(proj["id"])
    rows = [(r["address"], r["size"], r["name"]) for r in
            store.all("SELECT name, address, size FROM symbols WHERE project_id=? AND address IS NOT NULL", proj["id"])]
    rows += [(r["vram"], r["size"], r["name"]) for r in
             store.all("SELECT name, vram, size FROM functions WHERE project_id=? AND vram IS NOT NULL", proj["id"])]
    return Symbols(rows)


def play(net, slug: str, log=print, *, adapter: Path | None = None, compare: bool = False) -> dict:
    """Build the project's game for the PC if anything changed, start it with its live view, and return its address
    once it answers: {"url": ...}. `compare`: play it beside the console (side_by_side.py): the game and the reversing
    emulator run in lockstep in one page, one pad for both, and the RAM that differs is listed; the address is that page's."""
    from . import build

    if view := running_result(slug):
        return view
    if compare:
        from ...consoles.n64 import emulator
        with net.progress.view():
            console = net.console(net._project(slug)).id
        if console != "n64" or adapter:
            raise NetworkError("Playing beside the console works for N64 games in the decompdecomp runtime today.")
        if emulator.ensure(log) is None:
            raise NetworkError("The reversing emulator (mupenRE) isn't available: install it under Compilers & tools → Tools.")
    repo, cfg, rom = game(net, slug)
    steps = (*build.STEPS, STARTING)
    program = build.build(repo, console=cfg.get("platform", "n64"), log=log, steps=steps, runtime_image=rom,
                          **inputs(net, slug, log))
    build.Progress(log, steps).start(STARTING)
    port = _free_port()
    out = program.parent
    # what the game runs that no function starts, kept beside the project for its next import (runtime_entries)
    from ...consoles.n64.code import RUNTIME_ENTRIES
    env = {**os.environ, "PORT_LIVE": str(port), "PORT_SAVE": str(out / "game.eep"), "PORT_SRAM": str(out / "game.sra"),
           "PORT_FLASH": str(out / "game.fla"), "PORT_ENTRIES": str(rom.parent / RUNTIME_ENTRIES)}
    if memory := (cfg.get("rom") or {}).get("memory_size"):  # the console the game was meant for (host.c console_memory)
        env["PORT_MEMSIZE"] = str(memory)
    if compare:  # parked before each retrace until the viewer lets it go on, in virtual time so the game is idle there
        env.update(PORT_LOCKSTEP="1", PORT_VTIME="1")
    env.pop("PORT_RT64", None)
    env.pop("PORT_RECOMP_UI", None)
    if adapter:
        from ... import toolchains

        toolchains.ensure("sdl3", log=log)
        env.update(PORT_RT64=str(adapter), PORT_WINDOW="1", PORT_SDL=str(toolchains.binary("sdl3")),
                   PORT_TITLE=cfg["rom"].get("title") or slug)
        from ...consoles.n64.port import recomp_ui_library

        if menu := recomp_ui_library():  # recomp-ui's menu model, if it was built (tools/build_recomp_ui.py)
            env["PORT_RECOMP_UI"] = str(menu)
    if (unlisted := unlisted_entries(net, slug)):
        log(f"playing has found {len(unlisted)} addresses the game runs that no function starts "
            f"({', '.join(f'{a:08X}' for a in unlisted[:4])}{'…' if len(unlisted) > 4 else ''}): Add missed functions "
            "(the project's Code coverage review) adds them as functions, without a reimport")
    log_path = out / "play.log"
    with log_path.open("w", encoding="utf-8") as log_file:
        proc = subprocess.Popen([str(program), str(rom), "0"], cwd=out, env=env, stdout=log_file,
                                stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}/"
    try:
        with log_path.open(encoding="utf-8", errors="replace") as startup:
            ready = not adapter
            marker = "RT64 compatibility frame submitted"
            tail = ""
            for _ in range(250 if adapter else 100):
                getattr(log, "check_cancel", lambda: None)()
                if proc.poll() is not None:
                    raise NetworkError(f"the game stopped as it started: see {log_path}")
                if not ready:
                    chunk = tail + startup.read()
                    ready = marker in chunk
                    tail = chunk[-len(marker):]
                if ready and _answers(port):
                    break
                time.sleep(0.1)
            else:
                explanation = "RT64 did not receive a frame from the game" if adapter else "the game didn't start"
                raise NetworkError(f"{explanation}: see {log_path}")
    except BaseException:
        if proc.poll() is None:
            end(proc)
        raise
    title = f"{cfg['rom'].get('title') or slug} (PC port)"
    if compare:
        from . import side_by_side
        megabytes = int(memory or 8)  # "4" or "8" or a byte count, as the host reads it (host.c console_memory)
        megabytes = megabytes >> 20 if megabytes > 8 else megabytes
        try:  # the emulator has the Expansion Pak when the port's console does (the port defaults to it)
            session = side_by_side.Session(rom, url, out / "side-by-side", expansion_pak=megabytes != 4,
                                           lockstep=True, symbols=project_symbols(net, slug), title=slug)
        except BaseException:
            end(proc)
            raise
        _sessions[slug] = session
        url, title = session.url, title.replace("(PC port)", "(PC port beside the console)")
        log("Playing beside the console: the port and the reversing emulator run in lockstep, with one pad for both.")
    with _lock:
        _running[slug] = (proc, url)
        if adapter:
            _native.add(slug)
    if adapter:
        log("Playing in the RT64 Metal window.")
        return {"url": url, "native": True}
    log(f"playing at {url}")
    return {"url": url, "window": _open_window(slug, url, proc.pid, title), **({"compare": True} if compare else {})}


def unlisted_entries(net, slug: str) -> list[int]:
    """The addresses the game was seen starting to run (the project's runtime-entries.txt, which Play's host
    writes) that no function of the project starts: functions the split missed, in its data or run together with
    the function before them, which the next import takes as evidence (consoles/n64/code.py)."""
    from ...consoles.n64.code import runtime_entries

    with net.progress.view():
        proj = net._project(slug)
    seen = runtime_entries(Path(proj["root"]))
    if not seen:
        return []
    starts = {r["vram"] for r in net.project_store(proj["id"]).all("SELECT vram FROM functions WHERE project_id=?",
                                                                   proj["id"])}
    return sorted(a for a in seen if a not in starts)


def _open_window(slug: str, url: str, pid: int, title: str) -> bool:
    """The game's own window; closing it stops the game. False when none opened (the page then goes to the browser)."""
    from ...app import window
    win = window.open_page(url, title, (960, 800))
    if win is None:
        return False
    with _lock:
        _windows[slug] = win

    def watch():
        win.wait()
        with _lock:
            entry = _running.get(slug)
        if entry and entry[0].pid == pid:  # (not a game started again since)
            stop(slug)
    threading.Thread(target=watch, daemon=True, name=f"play-{slug}").start()
    return True
