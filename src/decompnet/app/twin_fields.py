"""What renaming a look-alike's names cannot say: another field of the same table, and a literal where it loads a global.

A matched donor reads `*(s16 *)(D_BASE + 0x210C + off)` and `D_BASE + 0x2108 + off`; its look-alike reads 0x210A and
the same 0x2108. Renaming `D_BASE` (the only thing an address difference can become in `rom_renames`) would move the
second read too, so the offset literal is changed instead and the name kept. And a donor that loads a global the
target never references, where the target builds a float constant with `lui`/`ori`, gets the constant written as a
literal (with the callee's parameter retyped when the donor passed the global as an integer). A 137-instruction
camera setup was exact with both and its matched neighbour's C (800 with the names alone).

ROM mode, MIPS: it reads the target's words. Project mode has no relocation addresses to compare (ROADMAP).
"""
import math
import re
import struct

from ..forks.csource import _blank

AUTO = re.compile(r'(?:D|func)_([0-9A-F]{8})\Z')
LITERAL = re.compile(r'(?<![\w.])0[xX]([0-9A-Fa-f]+)(?![\w.])')
EXTERN = r'^[ \t]*extern[ \t]+(?:(?:unsigned|signed|volatile|const)[ \t]+)*\w+[ \t]+{name}[ \t]*;[ \t]*\n'


def _address(symbols, name):
    hit = AUTO.match(name)
    if hit:
        return int(hit[1], 16)
    known = symbols.by_name.get(name) if symbols is not None else None
    return known.address if known else None


def moved_offsets(source, names, pairs, symbols):
    """(source, names) with the offset literals changed instead of a base renamed, or None. `pairs`: the donor's
    and the target's address for each aligned relocation."""
    clean = _blank(source)
    literals = {}
    for hit in LITERAL.finditer(clean):
        literals.setdefault(int(hit[1], 16), []).append(hit)
    edits, kept = {}, dict(names)
    for base, new in names.items():
        address = _address(symbols, base)
        if address is None or new == base or not new.startswith('D_'):
            continue
        # the base is also read where nothing moved: renaming it would move that read too
        if not any(x == y and (x - address in literals or x == address) for x, y in pairs):
            continue
        moved = {x - address: y - address for x, y in pairs if x != y and x - address in literals}
        if not moved or any(len(literals[old]) != 1 or value < 0 for old, value in moved.items()):
            continue
        for old, value in moved.items():
            hit = literals[old][0]
            edits[hit.start()] = (hit.end(), f'0x{value:X}')
        del kept[base]
    if not edits:
        return None
    for start, (end, value) in sorted(edits.items(), reverse=True):
        source = source[:start] + value + source[end:]
    return source, kept


def float_constants(code, big=True):
    """The float values a MIPS body builds with `lui` (and `ori`), as their 32-bit patterns."""
    words = [int.from_bytes(code[i:i + 4], 'big' if big else 'little') for i in range(0, len(code) - 3, 4)]
    found = []
    for index, word in enumerate(words):
        if word >> 26 != 0x0F:
            continue
        register, value = word >> 16 & 31, (word & 0xFFFF) << 16
        for later in words[index + 1:index + 4]:
            if later >> 26 == 0x0D and later >> 21 & 31 == register and later >> 16 & 31 == register:
                value |= later & 0xFFFF
                break
        number = struct.unpack('>f', value.to_bytes(4, 'big'))[0]
        if math.isfinite(number) and 1e-3 <= abs(number) <= 1e6 and value not in found:
            found.append(value)
    return found


def _spelled(bits):
    number = struct.unpack('>f', bits.to_bytes(4, 'big'))[0]
    for digits in range(1, 10):
        short = float(f'{number:.{digits}g}')
        if struct.pack('>f', short) == bits.to_bytes(4, 'big'):
            break
    return repr(short) + 'f'


def _retype(source, name):
    """Float parameters in the prototypes of the callees `name` is passed to."""
    clean = _blank(source)
    for call in re.finditer(r'\b([A-Za-z_]\w*)[ \t]*\(([^()]*(?:\([^()]*\)[^()]*)*)\)[ \t]*;', clean):
        arguments = [part.strip() for part in call[2].split(',')]
        if name not in arguments:
            continue
        position = arguments.index(name)
        prototype = re.search(rf'^[ \t]*(?:extern[ \t]+)?\w+[ \t*]+{call[1]}[ \t]*\(([^()]*)\)[ \t]*;', source, re.M)
        if not prototype:
            continue
        parameters = prototype[1].split(',')
        if position >= len(parameters) or parameters[position].strip() not in ('s32', 'int', 'u32'):
            continue
        parameters[position] = parameters[position].replace(parameters[position].strip(), 'f32')
        return source[:prototype.start(1)] + ','.join(parameters) + source[prototype.end(1):]
    return source


def constants_for_globals(source, donor_refs, target_refs, donor_code, target_code, symbols, big=True):
    """Sources with a global the donor loads and the target never references written as one of the float constants
    only the target builds."""
    known = set(float_constants(donor_code, big))
    constants = [bits for bits in float_constants(target_code, big) if bits not in known][:2]
    if not constants:
        return []
    out = []
    for name in sorted(set(re.findall(r'\b[A-Za-z_]\w*\b', _blank(source)))):
        address = _address(symbols, name)
        if address is None or address not in donor_refs or address in target_refs:
            continue
        declaration = re.search(EXTERN.format(name=name), source, re.M)
        if not declaration:
            continue
        without = source[:declaration.start()] + source[declaration.end():]
        for bits in constants:
            typed = _retype(without, name)
            out.append(re.sub(rf'\b{name}\b', _spelled(bits), typed))
    return out[:2]


def variants(donor_source, names, pairs, donor, target, symbols, apply, big=True):
    """Renamed donor sources with the offsets moved and the constants written, the combined child first."""
    from .similar_matches import relocations
    if not donor['target'] or not target['target']:
        return []
    moved = moved_offsets(donor_source, names, pairs, symbols)
    base = apply(*moved) if moved else apply(donor_source, names)
    donor_refs = {address for _, _, address in relocations(donor)}
    target_refs = {address for _, _, address in relocations(target)}
    out = constants_for_globals(base, donor_refs, target_refs, donor['target'], target['target'], symbols, big)
    if moved:
        out.append(base)
    return list(dict.fromkeys(out))
