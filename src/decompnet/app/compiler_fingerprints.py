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
- how far a multiply or divide's result is read after it (`mflo`/`mfhi`);
- a load whose result the very next instruction waits for while the instruction before it could have filled the
  gap: a build with scheduling off (`-fno-schedule-insns -fno-schedule-insns2`, as libraries were often built)
  keeps the C's order, where a scheduled build never leaves that. A leaf with no frame shows no other habit, so this alone told a library strcmp built that way from its game's
  scheduled code (its saved C was 130 off under the game's settings and exact, as plain C, with scheduling off);
- three that tell one GCC of a game from another (an N64 game built with KMC GCC 2.7.2 had eight near-matches stuck
  at score 10 that were SN GCC 2.8.1 code; under it their saved C was exact or one edit away):
  - a zero argument copied from a register just zeroed (`move $a1,$zero; move $a2,$a1`) or each set from `$zero`;
  - a float literal loaded from memory (`lui $at; lwc1`) or built in a register (`lui $at; mtc1 $at`);
  - a global's word loaded through its own address register (`lui $v1; lw $v1,%lo($v1)`) or into another one
    (`lui $v0; lw $v1,%lo($v0)`).
  Each is read from a probe function of its own, so the habits above keep the probes they always had.

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
# buffer, float arithmetic, arithmetic before a single call, and a byte-compare loop (where scheduling shows) (the shape where a compiler may make the frame late).
# Plain C every MIPS compiler of the era reads.
PROBE = """
extern int g(int, int);
int p_call(int a, int b) { int x = g(a, b); if (x) x = g(b, a); return x + a + b; }
int p_loop(int *p, int n) { int s = 0; while (n-- > 0) s += *p++; return s; }
int p_div(int a, int b) { return a / b + a * b; }
int p_frame(int a) { int buf[5]; buf[0] = a; buf[4] = a + 1; return g((int) buf, a); }
float p_float(float x, float y) { return x * y + x; }
extern void h(float);
void p_late(float *a, float *b) { float x = a[0] - b[0], y = a[1] - b[1]; h(x * x + y * y); }
int p_cmp(const unsigned char *a, const unsigned char *b) { int c, d; do { c = *a++; d = *b++; } while (c == d && c); return c - d; }
extern int q_word; extern int q_table[4];
int s_global(void) { return q_table[1] + q_word; }
extern void k(int, int, int, int);
void s_zero(int a) { k(a, 0, 0, 0); }
float s_literal(float x) { return x * 1.5f + 0.25f; }
"""
# the probe function each of the three GCC-telling habits is read from (the other habits never read these)
SIGNAL_PROBES = {'s_global': 'address register kept for the load', 's_zero': 'zero copied between registers',
                 's_literal': 'float literals loaded from memory'}

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
    loads, idle = load_evidence(words)
    if idle:
        out['unscheduled loads'] = True
    elif loads >= 3:
        out['unscheduled loads'] = False
    out.update(gcc_signals(words))
    return out


def _zeroes(w: int) -> int:
    """The register an instruction sets to zero from `$zero` (`move rd,$zero`, `li rd,0`), else 0."""
    op, rs, rt, rd = w >> 26, (w >> 21) & 31, (w >> 16) & 31, (w >> 11) & 31
    if op == 0 and w & 63 in (0x21, 0x25, 0x2D) and rs == 0 and rt == 0:
        return rd
    if op in (9, 0xD) and rs == 0 and w & 0xFFFF == 0:
        return rt
    return 0


def gcc_signals(words: list[int]) -> dict:
    """The three habits that tell one GCC of a game from another (the module's docstring); one absent when the code
    shows it neither way, or both ways."""
    out: dict = {}
    # a zero copied from a register just zeroed, or several registers each set from $zero
    copies = direct = 0
    zeroed: dict[int, int] = {}  # register -> where it was zeroed
    for k, w in enumerate(words):
        op, rs, rt, rd = w >> 26, (w >> 21) & 31, (w >> 16) & 31, (w >> 11) & 31
        source = rs or rt
        if op == 0 and w & 63 in (0x21, 0x25, 0x2D) and rd and (rs == 0) != (rt == 0) \
                and k - zeroed.get(source, -99) <= 4:
            copies += 1
        if reg := _zeroes(w):
            direct += any(k - at <= 4 for r, at in zeroed.items() if r != reg)
        for r in _writes(w):
            zeroed.pop(r, None)
        if reg := _zeroes(w):
            zeroed[reg] = k
    if copies:
        out['zero copied between registers'] = True
    elif direct:
        out['zero copied between registers'] = False
    # a float literal loaded from memory through $at, or built in $at and moved over
    memory = register = 0
    for w, nxt in zip(words, words[1:]):
        if w >> 26 == 0xF and (w >> 16) & 31 == 1:  # lui $at
            if nxt >> 26 == 0x31 and (nxt >> 21) & 31 == 1:  # lwc1 $fN, lo($at)
                memory += 1
            elif nxt >> 26 == 0x11 and (nxt >> 21) & 31 == 4 and (nxt >> 16) & 31 == 1:  # mtc1 $at, $fN
                register += 1
    if memory and not register:
        out['float literals loaded from memory'] = True
    elif register and not memory:
        out['float literals loaded from memory'] = False
    # a global's word loaded through its own address register, or into another one that the address then dies in
    same = other = 0
    for k, w in enumerate(words):
        if w >> 26 != 0xF or (base := (w >> 16) & 31) in (0, 1):  # lui, not $at (the assembler's own)
            continue
        for j in range(k + 1, min(k + 5, len(words))):
            u = words[j]
            if u >> 26 in LOADS and (u >> 21) & 31 == base:
                if (u >> 16) & 31 == base:
                    same += 1
                elif not any(base in _reads(v) for v in _until_written(words[j + 1:j + 9], base)):
                    other += 1
                break
            if base in _writes(u) or base in _reads(u) or _slot(u):
                break
    if other and not same:
        out['address register kept for the load'] = False
    elif same >= 2 and not other:
        out['address register kept for the load'] = True
    return out


def _until_written(words: list[int], reg: int) -> list[int]:
    """The instructions up to and including the first that writes `reg` (or that ends the straight line)."""
    out = []
    for w in words:
        out.append(w)
        if reg in _writes(w) or _slot(w):
            break
    return out


def load_evidence(words: list[int]) -> tuple[int, int]:
    """(loads that could show scheduling, those left waiting while the instruction before could have filled the
    gap)."""
    loads = idle = 0
    for k, w in enumerate(words[:-1]):
        # (loads through the stack pointer are the epilogue's restores, ordered by the frame, not the scheduler)
        if w >> 26 not in LOADS or (w >> 21) & 31 == 29 or not (rt := (w >> 16) & 31):
            continue
        before, after = (words[k - 1] if k else NOP), words[k + 1]
        if (k > 1 and _slot(words[k - 2])) or _slot(before):
            continue  # (a delay slot's contents are the filler's choice)
        loads += 1
        written = _writes(before)
        # the next instruction waits on the load while the one before it could have filled the gap: nothing it
        # writes is read by the load or its use, and it reads nothing they write. A scheduler never leaves that.
        if rt in _reads(after) and written and not (written & (_reads(w) | _reads(after) | {rt})) \
                and not (_reads(before) & ({rt} | _writes(after))) and before >> 26 not in LOADS and not _store(before):
            idle += 1
    return loads, idle


LOADS = (0x20, 0x21, 0x23, 0x24, 0x25, 0x27)  # lb lh lw lbu lhu lwu (integer loads; their result is rt)


def _slot(w: int) -> bool:
    """A branch or jump: the next instruction is its delay slot, placed by the delay-slot filler."""
    return _is_branch(w) or _is_jump(w)


def _store(w: int) -> bool:
    return 0x28 <= w >> 26 <= 0x3F and w >> 26 not in (0x30, 0x31, 0x34, 0x35, 0x37)


def _writes(w: int) -> set[int]:
    """The general register an ALU instruction or load writes (empty for anything else: branches, stores, moves to
    and from the coprocessors, which this reading doesn't follow)."""
    op = w >> 26
    if op == 0:
        fn = w & 63
        return {(w >> 11) & 31} - {0} if fn not in (8, 9, 0x18, 0x19, 0x1A, 0x1B, 0x1C, 0x1D, 0x1E, 0x1F, 0xC, 0xD) else set()
    if 8 <= op <= 0xF or op in LOADS or op in (0x18, 0x19):
        return {(w >> 16) & 31} - {0}
    return set()


def _reads(w: int) -> set[int]:
    """The general registers an instruction reads (rs, and rt where the instruction reads it)."""
    op, rs, rt = w >> 26, (w >> 21) & 31, (w >> 16) & 31
    if op == 0:
        return {rs, rt} - {0}
    if op in (4, 5, 0x14, 0x15) or op >= 0x28:  # beq/bne(l) compare rt; stores read the value stored
        return {rs, rt} - {0}
    return {rs} - {0}


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
                     if s['st_shndx'] == index and s.name.startswith(('p_', 's_'))})
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
                every = _functions(result.obj)
                functions = {name: code for name, code in every.items() if name not in SIGNAL_PROBES}
                signals = set(SIGNAL_PROBES.values())
                for name, code in functions.items():
                    for habit, value in traits(code, big_endian).items():
                        if habit not in ('frame first', 'unscheduled loads') and habit not in signals:
                            found.setdefault(habit, set()).add(value)
                found = {habit: values.pop() for habit, values in found.items() if len(values) == 1}
                # (one unscheduled load anywhere shows the scheduler was off: the other probes may simply have none)
                # (summed over the probes: each alone may have too few loads to say)
                loads = idle = 0
                for code in functions.values():
                    n, i = load_evidence(_words(code, big_endian))
                    loads, idle = loads + n, idle + i
                if idle or loads >= 2:
                    found['unscheduled loads'] = bool(idle)
                # (where the frame is made shows only where work comes before the first call: p_late alone)
                late = traits(functions['p_late'], big_endian) if 'p_late' in functions else {}
                if 'frame first' in late:
                    found['frame first'] = late['frame first']
                for name, habit in SIGNAL_PROBES.items():
                    shown = gcc_signals(_words(every[name], big_endian)) if name in every else {}
                    if habit in shown:
                        found[habit] = shown[habit]
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


def own_agreement(proj, fn, own: dict) -> int | None:
    """How well the function's own settings agree with its code's habits (None when its code shows none)."""
    code = fn['target'] if 'target' in fn.keys() else None
    if not code:
        return None
    big_endian = bool(proj['big_endian']) if 'big_endian' in proj.keys() else True
    target = traits(bytes(code), big_endian)
    if not target:
        return None
    own_flags = own['flags'].split() if isinstance(own['flags'], str) else list(own['flags'])
    return agreement(target, profile_traits(own['toolchain'], own_flags, big_endian))


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
