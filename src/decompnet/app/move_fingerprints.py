"""Compilers a function's own code points to, from how it encodes a register move and how it shifts a loaded byte.

A MIPS `move` is a pseudo-instruction: GCC's assemblers of the 1990s write it as `addu rd, rs, $zero`, SGI's (IDO)
as `or rd, rs, $zero`. A function whose moves are all `or` was not built by a compiler that writes them with
`addu`, whatever the project says. An N64 game built with KMC GCC linked a sound library built with IDO: nine
functions whose deep passes, scoring with GCC, could never improve; built with IDO 5.3, three were exact as their C
stood. Which way each compiler writes a move is read from its own output for a small function (compiled once per
profile), so no compiler is named here; the deep pass probes the profiles that agree with the target for an exact
match (deep_profiles.selected), and only the judge's exact match changes a function's settings.
"""
from __future__ import annotations

import io
import tempfile
import threading
from pathlib import Path

from .. import compilermap

PROBE = 'extern int g(int, int);\nint f(int a, int b) { return g(b, a) + a; }\n'
EXTRACT_PROBE = 'int f(unsigned char *p) { return (p[1] >> 3) & 0x1F; }\n'
_seen: dict[tuple[str, tuple[str, ...]], str | None] = {}
_extract_seen: dict[tuple[str, tuple[str, ...]], bool | None] = {}
WINDOW = 4  # instructions between the load and its shift that still count (a scheduler moves work between them)
_lock = threading.Lock()


def moves(code: bytes, big_endian: bool) -> str | None:
    """'or' or 'addu' (64-bit 'daddu' counts as 'addu') when all of a function's register moves are written one
    way, else None (none, or both)."""
    habits = compilermap.quirks(code, big_endian)
    by_or, by_add = habits.get('or move', 0), habits.get('addu move', 0) + habits.get('daddu move', 0)
    if by_or and not by_add:
        return 'or'
    if by_add and not by_or:
        return 'addu'
    return None


def shifted_loads(code: bytes, big_endian: bool) -> int:
    """How many times the code shifts a just-loaded byte or halfword (`lbu`/`lhu`) arithmetically (`sra`).

    A compiler that proves the load zero-extended shifts it with `srl`; one that doesn't prove it writes `sra`. The
    shift must read the loaded register itself, with that register untouched in between (up to WINDOW instructions)."""
    import struct
    words = struct.unpack((">" if big_endian else "<") + f"{len(code) // 4}I", code[:len(code) // 4 * 4])
    found = 0
    for k, w in enumerate(words):
        if w >> 26 not in (0x24, 0x25):  # lbu, lhu
            continue
        loaded = (w >> 16) & 31
        if not loaded:
            continue
        widest = 7 if w >> 26 == 0x24 else 15  # a shift by the whole width is no extraction
        for nxt in words[k + 1:k + 1 + WINDOW]:
            op, rt, rd, shamt, fn = nxt >> 26, (nxt >> 16) & 31, (nxt >> 11) & 31, (nxt >> 6) & 31, nxt & 63
            if op == 0 and fn == 3 and rt == loaded and 1 <= shamt <= widest:  # sra rd, loaded, shamt
                found += 1
                break
            if op in (0x08, 0x09, 0x0C, 0x0D, 0x0E, 0x0F, 0x20, 0x21, 0x23, 0x24, 0x25) and rt == loaded:
                break  # loaded again or overwritten by an immediate op or load
            if op == 0 and fn not in (0x08, 0x0D) and rd == loaded:
                break  # overwritten by a register op
    return found


def compiler_extracts(toolchain: str, flags: list[str]) -> bool | None:
    """Whether this compiler shifts a loaded byte with `sra`, from its code for EXTRACT_PROBE; None when it can't be
    told (or doesn't build)."""
    key = (toolchain, tuple(flags))
    with _lock:
        if key in _extract_seen:
            return _extract_seen[key]
    found = None
    try:
        from elftools.elf.elffile import ELFFile

        from ..compiler import compile_source
        from ..project import compiler_config
        with tempfile.TemporaryDirectory() as work:
            cfg = compiler_config(Path(work), {'compiler': {'toolchain': toolchain, 'flags': list(flags)}})
            result = compile_source(cfg, '', EXTRACT_PROBE)
            if result.ok:
                text = ELFFile(io.BytesIO(result.obj)).get_section_by_name('.text')
                found = shifted_loads(text.data(), True) > 0 if text is not None else None
    except Exception:  # noqa: BLE001 - a profile that can't be read is simply not suggested
        found = None
    with _lock:
        _extract_seen[key] = found
    return found


def compiler_moves(toolchain: str, flags: list[str], big_endian: bool) -> str | None:
    """How this compiler writes a move, from its code for PROBE; None when it can't be told (or doesn't build)."""
    key = (toolchain, tuple(flags))
    with _lock:
        if key in _seen:
            return _seen[key]
    found = None
    try:
        from elftools.elf.elffile import ELFFile

        from ..compiler import compile_source
        from ..project import compiler_config
        with tempfile.TemporaryDirectory() as work:
            cfg = compiler_config(Path(work), {'compiler': {'toolchain': toolchain, 'flags': list(flags)}})
            result = compile_source(cfg, '', PROBE)
            if result.ok:
                text = ELFFile(io.BytesIO(result.obj)).get_section_by_name('.text')
                found = moves(text.data(), big_endian) if text is not None else None
    except Exception:  # noqa: BLE001 - a profile that can't be read is simply not suggested
        found = None
    with _lock:
        _seen[key] = found
    return found


def _neighbours(net, proj, fn, big_endian: bool, reach: int = 8) -> str | None:
    """A function too small to move a register takes its neighbours' way, when the nearest function with moves on
    each side agree (the linker lays out an object file whole, and an object file is one compiler's)."""
    if net is None or 'id' not in proj.keys() or 'vram' not in fn.keys() or fn['vram'] is None:
        return None
    store = net.project_store(proj['id'])
    sides = []
    for query in ('SELECT target FROM functions WHERE project_id=? AND vram<? ORDER BY vram DESC LIMIT ?',
                  'SELECT target FROM functions WHERE project_id=? AND vram>? ORDER BY vram LIMIT ?'):
        side = None
        for row in store.all(query, proj['id'], fn['vram'], reach):
            if row['target'] and (side := moves(bytes(row['target']), big_endian)) is not None:
                break
        sides.append(side)
    return sides[0] if sides[0] is not None and sides[0] == sides[1] else None


def profiles(net, proj, cfg: dict, fn, own: dict, limit: int = 2) -> list[dict]:
    """Installed profiles of this console that agree with the function's code where its own compiler doesn't: in how
    it writes a move, and in whether it shifts a loaded byte with `sra`."""
    code = fn['target'] if 'target' in fn.keys() else None
    if not code:
        return []
    big_endian = bool(proj['big_endian']) if 'big_endian' in proj.keys() else True
    mine = moves(bytes(code), big_endian)
    if mine is None:
        mine = _neighbours(net, proj, fn, big_endian)
    sra = shifted_loads(bytes(code), big_endian) > 0
    # (a signal only counts against the function's own compiler when that compiler can be read: unreadable is silent)
    move_clash = mine is not None and compiler_moves(own['toolchain'], own['flags'], big_endian) not in (None, mine)
    sra_clash = sra and compiler_extracts(own['toolchain'], own['flags']) is False
    if not (move_clash or sra_clash):
        return []
    from .. import consoles, toolchains
    found = []
    for compiler in consoles.of(cfg).compilers:
        if not compiler.automatic or compiler.id == own['toolchain'] or not toolchains.installed(compiler.id):
            continue
        flags = list(consoles.default_flags(cfg, compiler.id))
        if mine is not None and compiler_moves(compiler.id, flags, big_endian) != mine:
            continue
        if sra and compiler_extracts(compiler.id, flags) is not True:
            continue
        found.append({'toolchain': compiler.id, 'flags': flags})
        if len(found) >= limit:
            break
    return found
