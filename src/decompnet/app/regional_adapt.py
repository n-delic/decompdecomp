"""The other release's C, fitted to this release's numbers: its struct fields at this release's offsets, and the
constants that changed between them.

Two releases of a game often build a function to the same instructions with different numbers in them: a struct
grew a field (`lbu s0, 0x4c(s2)` in one release, `0x42(s2)` in the other), a frame rate or a screen height changed
(50 and 60, 288 and 240), an array of structs walks by a different stride. The other release's C then misses by a
few operands. A pass over a game and its other release found 201 of 242 such functions with moved offsets and 88
with changed constants.

Each pair's instructions say what moved: an operand that is the same in both but for one number. From those:
- a struct the C defines itself (agents write `char pad2C[0x4C - 0x2C]; u8 nsub;`) is laid out again: each of its
  fields at the offset this release reads it at, the fields it doesn't read moved along with the one before them,
  and filler between them. The compiler says where each field was (a probe compiled with the C) and checks the new
  layout before it is tried;
- a number written in the C (`(u8 *)p + 0x4C`, `* 56`, `< 288`) becomes this release's when the instructions map it
  to exactly one other number.
The judge decides, as for any other candidate: a variant that doesn't build these bytes is a miss like any other.
Structs from the shared header aren't changed here: other functions use them (ROADMAP.md).
"""

from __future__ import annotations

import re
import struct
from collections import defaultdict
from decimal import Decimal
from difflib import SequenceMatcher
from io import BytesIO

from .. import arch as processors

NUMBER = re.compile(r'(?<![\w.])-?(?:0[xX][0-9a-fA-F]+|\d+)(?![\w.])')
# a literal in C: decimal or hex, with an optional integer suffix, not part of a name or a float
C_INTEGER = re.compile(r'(?<![\w.])(0[xX][0-9a-fA-F]+|\d+)([uUlL]*)(?![\w.])')
FRAME = {'$sp', '$fp', 'sp', 'fp', '$s8', 'esp', 'ebp', 'rsp', 'rbp'}  # a stack slot moves with the code, not a struct
ZERO = re.compile(r'(?:^|[\s,])\((\$?\w+)\)')
STRUCT = re.compile(r'(\btypedef\s+)?\bstruct\s+(\w+)?\s*\{([^{}]*)\}\s*(\w+)?\s*;')
FILLER = re.compile(r'^(?:_*pad|unk_?pad|filler|_*unused)', re.I)
PROBE = '__release_layout'
MAX_VARIANTS = 3


def align(old, new) -> list[tuple[int, int]]:
    """(row in old, row in new) for the instructions two releases have in common, in order: all of them when the
    instructions are the same, the unchanged stretches around what changed when some were added or removed."""
    if [i.mnemonic for i in old] == [i.mnemonic for i in new]:
        return list(zip(range(len(old)), range(len(new))))
    matcher = SequenceMatcher(None, [i.mnemonic for i in old], [i.mnemonic for i in new], autojunk=False)
    return [(i + k, j + k) for i, j, n in matcher.get_matching_blocks() for k in range(n)]


def differences(old, new, arch: str | None = None):
    """(memory offsets that moved: old -> new, offsets read at the same place somewhere, other numbers that changed:
    old -> new, floats that changed: old bits -> new bits) from two releases' instructions (scoring.Insn), lined up
    by align(). A number that moves two ways says nothing; one that moved somewhere and stayed elsewhere is in both
    (two structs with a field at that offset, one of them moved: moved_layout tells them apart). Branch and jump
    targets move with the code around them, not with anything the C says: they're left out."""
    moved, constant, floats = defaultdict(set), defaultdict(set), defaultdict(set)
    kept = set()
    rows = align(old, new)
    built = getattr(processors.get(arch), 'built_floats', None) if arch else None
    if built:
        # in order, when both build as many floats (a float built with an ori in one release and without in the
        # other shifts the instructions around it); else where the instructions line up
        theirs, mine = built(old), built(new)
        lined = list(zip(sorted(theirs), sorted(mine))) if len(theirs) == len(mine) else \
            [(i, j) for i, j in rows if i in theirs and j in mine]
        for i, j in lined:
            if theirs[i] != mine[j]:
                floats[theirs[i]].add(mine[j])
    for i, j in rows:
        a, b = old[i], new[j]
        if a.mnemonic != b.mnemonic or a.mnemonic.startswith(('b', 'j')):
            continue
        xs, ys = list(NUMBER.finditer(a.operands)), list(NUMBER.finditer(b.operands))
        if len(xs) != len(ys) or NUMBER.sub('#', a.operands) != NUMBER.sub('#', b.operands):
            continue  # other registers or symbols: not a number that changed
        zero = ZERO.search(a.operands)  # `lw $v0, ($s2)`: offset 0, read the same in both
        if zero and zero.group(1) not in FRAME:
            kept.add(0)
        for x, y in zip(xs, ys):
            u, v = int(x.group(), 0), int(y.group(), 0)
            after = a.operands[x.end():].lstrip()
            memory = after.startswith('(') or _bracketed(a.operands, x.start())
            if memory:
                base = after[1:].split(')', 1)[0].strip() if after.startswith('(') else ''
                if base in FRAME:
                    continue
                (kept.add(u) if u == v else moved[u].add(v))
            elif u != v:
                constant[u].add(v)
    moves = {u: next(iter(v)) for u, v in moved.items() if len(v) == 1}
    constants = {u: next(iter(v)) for u, v in constant.items() if len(v) == 1}
    return moves, kept, constants, {u: next(iter(v)) for u, v in floats.items() if len(v) == 1}


def _bracketed(text: str, at: int) -> bool:
    return text.rfind('[', 0, at) > text.rfind(']', 0, at)


# ------------------------------------------------------------------ structs the C defines itself

class Struct:
    def __init__(self, match):
        self.match = match
        self.tag, self.body, self.alias = match.group(2), match.group(3), match.group(4)
        self.type = f'struct {self.tag}' if self.tag else self.alias
        self.members = _members(self.body)


def _members(body: str) -> list[tuple[str, str]] | None:
    """(declaration, member name) in order, or None when the body is more than one plain declaration each (bit
    fields, several names in one declaration)."""
    out = []
    for decl in (d.strip() for d in body.split(';')):
        if not decl:
            continue
        if ':' in decl or ',' in decl.split('(')[0]:
            return None
        pointer = re.search(r'\(\s*\*\s*(\w+)\s*\)', decl)
        name = pointer.group(1) if pointer else re.search(r'(\w+)\s*((?:\[[^\]]*\]\s*)*)$', decl)
        if name is None:
            return None
        out.append((decl, name if isinstance(name, str) else name.group(1)))
    return out or None


def structs(source: str) -> list[Struct]:
    return [s for s in map(Struct, STRUCT.finditer(source)) if s.type and s.members]


def probe(source: str, items: list[Struct]) -> str:
    """The C with an array of each member's offset and size and each struct's size, for the compiler to fill in."""
    values = []
    for s in items:
        for _, name in s.members:
            values += [f'(int)&(({s.type} *)0)->{name}', f'(int)sizeof((({s.type} *)0)->{name})']
        values.append(f'(int)sizeof({s.type})')
    return f'{source}\nint {PROBE}[] = {{{", ".join(values)}}};\n'


def read_probe(obj: bytes, count: int) -> list[int] | None:
    """The probe's numbers from the compiled object, or None (another format, or no such array)."""
    from elftools.common.exceptions import ELFError
    from elftools.elf.elffile import ELFFile
    try:
        elf = ELFFile(BytesIO(obj))
        table = elf.get_section_by_name('.symtab')
        symbol = next((s for s in table.iter_symbols() if s.name == PROBE), None) if table else None
        if symbol is None or not isinstance(symbol['st_shndx'], int):
            return None
        data = elf.get_section(symbol['st_shndx']).data()
    except (ELFError, ValueError, AttributeError, StopIteration):
        return None
    order = 'little' if elf.little_endian else 'big'
    at = symbol['st_value']
    if at + 4 * count > len(data):
        return None
    return [int.from_bytes(data[at + 4 * i:at + 4 * i + 4], order, signed=True) for i in range(count)]


def layouts(values: list[int], items: list[Struct]) -> list[tuple[list[tuple[int, int]], int]]:
    """Each struct's [(offset, size)] per member and its size, from the probe's numbers."""
    out, at = [], 0
    for s in items:
        fields = [(values[at + 2 * i], values[at + 2 * i + 1]) for i in range(len(s.members))]
        at += 2 * len(s.members)
        out.append((fields, values[at]))
        at += 1
    return out


def anchors(s: Struct, fields, moves, kept) -> dict[int, int] | None:
    """{member index: this release's offset} for the struct's fields this release reads, or None when none of them
    moved. A struct moves when one of its fields moved and was nowhere read in place; its fields read both ways
    (another struct's field shares the offset) then move too."""
    real = [(i, offset) for i, ((_, name), (offset, _)) in enumerate(zip(s.members, fields)) if not FILLER.match(name)]
    if not any(offset in moves and offset not in kept for _, offset in real):
        return None
    return {i: moves.get(offset, offset) for i, offset in real if offset in moves or offset in kept}


def place(s: Struct, fields, size, found: dict[int, int], constants):
    """[(declaration, member, new offset, width)] for the struct's fields (filler dropped) and its new size, with
    the fields in `found` where it says and the others moved with the field before them; or None when they don't
    fit."""
    placed, delta, end = [], 0, 0  # what comes before the first field read stays where it is
    for i, ((decl, name), (offset, width)) in enumerate(zip(s.members, fields)):
        if FILLER.match(name):
            continue
        if i in found:
            delta = found[i] - offset
        new = offset + delta
        if new < end:
            return None  # two fields would overlap: the guess is wrong
        placed.append((decl, name, new, width))
        end = new + width
    # its size: as this release's code says (a stride, a sizeof), or moved with the last field (the compiler rounds
    # it up to the struct's alignment)
    said = constants.get(size)
    new_size = said if said is not None else size + delta
    return (placed, new_size, said is not None) if new_size >= end else None


def moved_layout(s: Struct, fields, size, moves, kept, constants):
    """place() with the fields this release reads where it reads them, or None when nothing of it moved."""
    found = anchors(s, fields, moves, kept)
    return None if found is None else place(s, fields, size, found, constants)


def relaid(s: Struct, placed, size) -> str:
    """The struct's definition with its fields at their new offsets, filler between them."""
    lines, cursor = [], 0
    for decl, _, offset, width in placed:
        if offset > cursor:
            lines.append(f'char pad_{cursor:X}[0x{offset - cursor:X}];')
        lines.append(decl + ';')
        cursor = offset + width
    if size > cursor:
        lines.append(f'char pad_{cursor:X}[0x{size - cursor:X}];')
    text = s.match.group()
    body = s.match.group(3)
    start = text.index('{') + 1
    return text[:start] + '\n  ' + '\n  '.join(lines) + '\n' + text[start + len(body):]


def adapt_structs(net, proj, header, source, fn, moves, kept, constants) -> str | None:
    """The C with its own structs laid out as this release reads them, checked by the compiler, or None."""
    items = structs(source)
    if not items or not (moves or constants):
        return None
    count = sum(2 * len(s.members) + 1 for s in items)
    values = _probe(net, proj, header, source, fn, items, count)
    if values is None:
        return None
    out, wanted = source, []
    for s, (fields, size) in reversed(list(zip(items, layouts(values, items)))):
        found = moved_layout(s, fields, size, moves, kept, constants)
        if found is None:
            continue
        placed, new_size, said = found
        out = out[:s.match.start()] + relaid(s, placed, new_size) + out[s.match.end():]
        wanted.append((s.type, [(name, at) for _, name, at, _ in placed], new_size if said else None))
    if out == source:
        return None
    # the compiler checks the new layout: alignment it adds would put a field somewhere else
    check = structs(out)
    values = _probe(net, proj, header, out, fn, check, sum(2 * len(s.members) + 1 for s in check))
    if values is None:
        return None
    got = {s.type: (dict(zip((n for _, n in s.members), (o for o, _ in f))), size)
           for s, (f, size) in zip(check, layouts(values, check))}
    for kind, fields, size in wanted:
        offsets, actual = got.get(kind, ({}, None))
        if actual is None or (size is not None and actual != size) or any(offsets.get(name) != at for name, at in fields):
            return None
    return out


def _probe(net, proj, header, source, fn, items, count):
    from ..network import NetworkError
    from ..toolchains import ToolchainError
    try:
        result = net._compile(proj, header, probe(source, items), fn)
    except (NetworkError, ToolchainError, OSError):
        return None
    if not result.ok or not result.obj:
        return None
    return read_probe(result.obj, count)


# ------------------------------------------------------------------ numbers written in the C

C_FLOAT = re.compile(r'(?<![\w.])((?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?)([fF]?)(?![\w.])')


def _bits(value: float) -> int:
    return struct.unpack('>I', struct.pack('>f', value))[0]


def _float_text(bits: int) -> str:
    """The shortest decimal that is this float (single precision)."""
    value = struct.unpack('>f', struct.pack('>I', bits))[0]
    for digits in range(1, 10):
        text = f'{value:.{digits}g}'
        if _bits(float(text)) == bits:
            break
    if 'e' in text and abs(value) < 1e9:
        text = format(Decimal(text), 'f')  # 13000, not 1.3e+04: as people write it
    if 'e' not in text and '.' not in text:
        text += '.0'
    return text


def adapt_floats(source: str, floats: dict[int, int]) -> str | None:
    """The C with each float literal that is one of the other release's floats changed to this release's."""
    if not floats:
        return None
    code = _code_only(source)
    edits = []
    for m in C_FLOAT.finditer(code):
        if '.' not in m.group(1) and 'e' not in m.group(1).lower() and not m.group(2):
            continue  # an integer
        try:
            bits = _bits(float(m.group(1)))
        except (OverflowError, ValueError):
            continue
        if bits in floats:
            edits.append((m.start(), m.end(), _float_text(floats[bits]) + (m.group(2) or 'f')))
    out = source
    for start, end, text in reversed(edits):
        out = out[:start] + text + out[end:]
    return out if out != source else None


def adapt_numbers(source: str, moves: dict[int, int], constants: dict[int, int]) -> str | None:
    """The C with each number the instructions map to exactly one other number changed to it (as written: hex
    stays hex). Offsets change only where the C adds them to a pointer or indexes with them."""
    edits = []
    code = _code_only(source)
    for m in C_INTEGER.finditer(code):
        value = int(m.group(1), 0)
        new = constants.get(value)
        if new is None and value in moves and re.search(r'(?:\+|\[)\s*$', code[:m.start()]):
            new = moves[value]
        if new is None or new < 0:
            continue
        text = (f'0x{new:X}' if m.group(1).lower().startswith('0x') else str(new)) + m.group(2)
        edits.append((m.start(), m.end(), text))
    out = source
    for start, end, text in reversed(edits):
        out = out[:start] + text + out[end:]
    return out if out != source else None


def _code_only(source: str) -> str:
    """The C with comments, strings and preprocessor lines blanked (same length), so numbers in them stay."""
    def blank(m):
        return re.sub(r'[^\n]', ' ', m.group())
    return re.sub(r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|^\s*#[^\n]*', blank, source,
                  flags=re.S | re.M)


def variants(net, proj, header, source, fn, old, new) -> list[str]:
    """Up to MAX_VARIANTS versions of the other release's C fitted to this release's numbers, most changed first."""
    moves, kept, constants, floats = differences(old, new, proj['arch'])
    if not moves and not constants and not floats:
        return []
    floated = adapt_floats(source, floats)  # a float is the same float whatever else changed
    source = floated or source
    laid = adapt_structs(net, proj, header, source, fn, moves, kept, constants)
    numbered = adapt_numbers(source, moves, constants)
    both = adapt_numbers(laid, moves, constants) if laid else None
    out = []
    for v in (both, laid, numbered, floated):
        if v and v not in out:
            out.append(v)
    return out[:MAX_VARIANTS]
