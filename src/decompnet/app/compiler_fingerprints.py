"""Which installed compiler profile a function's code looks like it came from, read from its own bytes.

move_fingerprints separates IDO from the GCCs by how a register move is written. That can't tell one GCC build or
flag set from another, so a function built differently from its project (a library with its own flags, scheduling
turned off) was only ever probed for an exact match under profiles chosen by other evidence. Here a handful of
habits are read from the target, each also read from what every installed profile makes of a small probe source
(compiled once per profile and remembered), and the profiles are ranked by how many habits agree:

- where the stack frame is made: first, or after other work (a late prologue);
- the order the return address and saved registers are stored;
- branch delay slots left as nops, or filled;
- branch-likely instructions used or not;
- register moves as `or` or `addu`;
- frames rounded to 8 or to 16 bytes;
- how far a multiply or divide's result is read after it (`mflo`/`mfhi`).

A habit the target doesn't show (no call, no division) counts neither way. Ranking only orders the profiles the
deep pass measures (profile_climb); only the judge's linked verdict decides a match, and only the existing
profile-bundle recheck changes a function's settings.
"""
from __future__ import annotations

import io
import struct
import tempfile
import threading
from pathlib import Path

JR_RA = 0x03E00008
NOP = 0

# One small function per habit: calls with saved registers, a counting loop, a division and a product, a stack
# buffer, float arithmetic. Plain C every MIPS compiler of the era reads.
PROBE = """
extern int g(int, int);
int p_call(int a, int b) { int x = g(a, b); if (x) x = g(b, a); return x + a + b; }
int p_loop(int *p, int n) { int s = 0; while (n-- > 0) s += *p++; return s; }
int p_div(int a, int b) { return a / b + a * b; }
int p_frame(int a) { int buf[5]; buf[0] = a; buf[4] = a + 1; return g((int) buf, a); }
float p_float(float x, float y) { return x * y + x; }
"""

_seen: dict[tuple[str, tuple[str, ...], bool], dict | None] = {}
_lock = threading.Lock()


def _words(code: bytes, big_endian: bool) -> list[int]:
    return list(struct.unpack((">" if big_endian else "<") + f"{len(code) // 4}I", code[:len(code) // 4 * 4]))


def _is_branch(w: int) -> bool:
    op, rt = w >> 26, (w >> 16) & 31
    return op in (4, 5, 6, 7, 0x14, 0x15, 0x16, 0x17) or (op == 1 and rt in (0, 1, 2, 3, 0x10, 0x11, 0x12, 0x13)) \
        or (op == 0x11 and (w >> 21) & 31 == 8)


def _is_jump(w: int) -> bool:
    op, fn = w >> 26, w & 63
    return op in (2, 3) or (op == 0 and fn in (8, 9))


def traits(code: bytes, big_endian: bool = True) -> dict:
    """The habits one function's code shows; a habit it gives no evidence for is absent."""
    words = _words(code, big_endian)
    out: dict = {}
    frame_at = next((k for k, w in enumerate(words) if w >> 26 == 9 and (w >> 21) & 31 == 29 and (w >> 16) & 31 == 29
                     and (w & 0xFFFF) >= 0x8000), None)
    if frame_at is not None:
        out['frame first'] = frame_at <= 1
        size = 0x10000 - (words[frame_at] & 0xFFFF)
        out['16-byte frames'] = size % 16 == 0
        saves = []
        for w in words[frame_at:frame_at + 16]:
            if w >> 26 in (0x2B, 0x3F) and (w >> 21) & 31 == 29 and ((w >> 16) & 31 in range(16, 24) or (w >> 16) & 31 in (30, 31)):
                saves.append((w >> 16) & 31)
        if 31 in saves and len(saves) > 1:
            out['return address saved last'] = saves[-1] == 31
        s = [r for r in saves if 16 <= r <= 23]
        if len(s) > 1:
            out['saved registers ascending'] = s == sorted(s)
    slots = filled = 0
    for k, w in enumerate(words[:-1]):
        if (_is_branch(w) or _is_jump(w)) and words[k] != JR_RA:
            slots += 1
            filled += words[k + 1] != NOP
    if slots >= 2:
        out['delay slots filled'] = filled * 2 > slots
    if any(w >> 26 in (0x14, 0x15, 0x16, 0x17) or (w >> 26 == 1 and (w >> 16) & 31 in (2, 3, 0x12, 0x13)) for w in words):
        out['branch-likely'] = True
    elif slots >= 4:
        out['branch-likely'] = False
    moves = {fn for w in words if w >> 26 == 0 and (fn := w & 63) in (0x21, 0x25) and (w >> 11) & 31
             and (((w >> 21) & 31 == 0) != ((w >> 16) & 31 == 0))}
    if len(moves) == 1:
        out['moves with or'] = moves == {0x25}
    gaps = []
    for k, w in enumerate(words):
        if w >> 26 == 0 and (w & 63) in (0x18, 0x19, 0x1A, 0x1B):
            gap = next((j - k for j in range(k + 1, min(k + 8, len(words)))
                        if words[j] >> 26 == 0 and words[j] & 63 in (0x10, 0x12)), None)
            if gap is not None:
                gaps.append(gap)
    if gaps:
        out['result read right after'] = min(gaps) <= 2
    return out


def _functions(obj: bytes) -> dict[str, bytes]:
    """Each function's bytes in an object, split at its symbols."""
    from elftools.elf.elffile import ELFFile
    elf = ELFFile(io.BytesIO(obj))
    text = elf.get_section_by_name('.text')
    symtab = elf.get_section_by_name('.symtab')
    if text is None or symtab is None:
        return {}
    index = elf.get_section_index('.text')
    starts = sorted({(s['st_value'], s.name) for s in symtab.iter_symbols()
                     if s['st_shndx'] == index and s.name.startswith('p_')})
    data = text.data()
    out = {}
    for i, (start, name) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else len(data)
        out[name] = data[start:end]
    return out


def profile_traits(toolchain: str, flags, big_endian: bool = True) -> dict | None:
    """What this profile makes of PROBE: the habits all its probe functions agree on; None when it can't be told."""
    key = (toolchain, tuple(flags), big_endian)
    with _lock:
        if key in _seen:
            return _seen[key]
    found = None
    try:
        from ..compiler import compile_source
        from ..project import compiler_config
        with tempfile.TemporaryDirectory() as work:
            cfg = compiler_config(Path(work), {'compiler': {'toolchain': toolchain, 'flags': list(flags)}})
            result = compile_source(cfg, '', PROBE)
            if result.ok:
                found = {}
                for code in _functions(result.obj).values():
                    for habit, value in traits(code, big_endian).items():
                        found.setdefault(habit, set()).add(value)
                found = {habit: values.pop() for habit, values in found.items() if len(values) == 1}
    except Exception:  # noqa: BLE001 - a profile that can't be read is simply not ranked
        found = None
    with _lock:
        _seen[key] = found
    return found


def agreement(target: dict, profile: dict | None) -> int:
    """+1 for each habit the target and the profile share, -1 for each they disagree on; others count nothing."""
    if not profile:
        return 0
    return sum((1 if profile[h] == v else -1) for h, v in target.items() if h in profile)


def candidates(net, proj, cfg: dict, fn, own: dict) -> list[dict]:
    """Installed profiles worth measuring for this function, the function's own settings left out: each installed
    automatic compiler of the console at its default flags, and the flag variants of its own compiler."""
    from .. import consoles, toolchains
    own_flags = own['flags'].split() if isinstance(own['flags'], str) else list(own['flags'])
    key = lambda p: (p['toolchain'], tuple(p['flags']))  # noqa: E731
    seen = {key({'toolchain': own['toolchain'], 'flags': own_flags})}
    out = []
    for flags in getattr(net, 'flag_variants', lambda *a: [])(own['toolchain'], own_flags, consoles.of(cfg).flag_extras):
        profile = {'toolchain': own['toolchain'], 'flags': list(flags)}
        if key(profile) not in seen:
            seen.add(key(profile))
            out.append(profile)
    for compiler in consoles.of(cfg).compilers:
        if not compiler.automatic or not toolchains.installed(compiler.id):
            continue
        profile = {'toolchain': compiler.id, 'flags': list(consoles.default_flags(cfg, compiler.id))}
        if key(profile) not in seen:
            seen.add(key(profile))
            out.append(profile)
    return out


def ranked(net, proj, cfg: dict, fn, own: dict, limit: int = 6) -> list[tuple[int, dict]]:
    """(agreement, profile) for the candidates, best first; a profile agreeing less than the function's own settings
    is left out, and ties keep the candidates' order (own compiler's flag variants first)."""
    code = fn['target'] if 'target' in fn.keys() else None
    if not code:
        return []
    big_endian = bool(proj['big_endian']) if 'big_endian' in proj.keys() else True
    target = traits(bytes(code), big_endian)
    if not target:
        return []
    own_flags = own['flags'].split() if isinstance(own['flags'], str) else list(own['flags'])
    floor = agreement(target, profile_traits(own['toolchain'], own_flags, big_endian))
    scored = [(agreement(target, profile_traits(p['toolchain'], p['flags'], big_endian)), i, p)
              for i, p in enumerate(candidates(net, proj, cfg, fn, own))]
    scored = [(a, i, p) for a, i, p in scored if a >= floor]
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [(a, p) for a, _, p in scored[:limit]]
