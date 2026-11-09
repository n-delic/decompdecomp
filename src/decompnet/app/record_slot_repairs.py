"""A fixed-stride table of records, as the source declared it: `Slot D[]` and `D[i].field`.

m2c sees only a base address and `index * stride`: `*(u16 *)(D + (i & 0xFFFF) * 100) |= 0x10`, or a local
`off = i * 0x64` used as `*(&D + off)`. The original declared a record of that size and indexed the table.
Only the first field (offset 0) is recovered: its type comes from the cast, or is tried as u16, u8 and u32
when the access is uncast. Every use of the table in the function must be such an access with one stride,
and the index must be a plain expression (no calls or steps). Agents wrote the same typedef in six separate batches of repairs
in one N64 game's object tables.
"""
import re

from ..forks.csource import _blank, find
from .integer_repair_support import edit
from .pattern_repairs import close

FAMILY = 'record_slot_table'
FAMILIES = (FAMILY,)
_SIZES = {'u8': 1, 's8': 1, 'char': 1, 'u16': 2, 's16': 2, 'short': 2, 'u32': 4, 's32': 4, 'f32': 4}
_PLAIN = re.compile(r'[\w\s()+\-*/%&|^~<>.\[\]]+\Z')
_STRIDE = re.compile(r'(.+?)\s*\*\s*(0x[0-9A-Fa-f]+|\d+)\s*\Z', re.S)


def _split(operand):
    """`(e) * 100` or `((e) * 100)` as (e, 100); None unless the stride is the last top-level factor."""
    text = operand.strip()
    while text.startswith('(') and close(text, 0) == len(text):
        text = text[1:-1].strip()
    hit = _STRIDE.match(text)
    if not hit:
        return None
    left, stride = hit[1].strip(), int(hit[2], 0)
    while left.startswith('(') and close(left, 0) == len(left):
        left = left[1:-1].strip()
    depth_ok = left.count('(') == left.count(')')
    plain = _PLAIN.match(left) and not re.search(r'\w\s*\(|\+\+|--|(?:^|[(+\-*/%&|^~<>!\[])\s*&|=', left)
    return (left, stride) if depth_ok and plain and stride >= 8 else None


def _accesses(source, clean, span, table, temps):
    """Every `*(T *)(D + e)` / `*(&D + e)` in the function as (start, stop, index, stride, cast); None if one is not."""
    body = clean[span[0]:span[1]]
    found = []
    for hit in re.finditer(r'\*\s*(?:\(\s*(\w+)\s*\*\s*\)\s*)?\(\s*&?\s*' + table + r'\s*\+', body):
        paren = span[0] + hit.start() + hit[0].rfind('(', 0, hit[0].index(table))
        stop = close(clean, paren)
        operand = source[span[0] + hit.end():stop - 1] if stop else ''
        split = temps.get(operand.strip()) or _split(operand)
        if not split:
            return None
        found.append((span[0] + hit.start(), stop, split[0], split[1], hit[1]))
    # every mention of the table must be one of these accesses
    return found if found and len(found) == len(re.findall(r'\b' + table + r'\b', body)) else None


def variants(source, name):
    span = find(source, name)
    if not span:
        return []
    clean = _blank(source)
    body = clean[span[0]:span[1]]
    # temporaries: `off = (e) * 100;` later used as `*(&D + off)`
    temps = {t[1]: split for t in re.finditer(r'(?m)^[ \t]*(\w+)\s*=\s*([^;{}]+);', body) if (split := _split(t[2]))}
    result = []
    for decl in re.finditer(r'^[ \t]*extern\s+(?:volatile\s+)?(?:M2C_UNK|s32|u8|s8|char)\s+(D_[0-9A-Fa-f]{6,8})\s*(?:\[\s*\])?\s*;', clean[:span[0]], re.M):
        table = decl[1]
        uses = _accesses(source, clean, span, table, temps)
        if not uses or len({use[3] for use in uses}) != 1:
            continue
        stride, casts = uses[0][3], {use[4] for use in uses}
        for typ in (list(casts) if len(casts) == 1 and None not in casts else ['u16', 'u8', 'u32']):
            if typ not in _SIZES or _SIZES[typ] >= stride:
                continue
            slot = 'Slot_' + table[2:]
            record = f'typedef struct {{ {typ} f0; u8 pad[{stride - _SIZES[typ]}]; }} {slot};\nextern {slot} {table}[];'
            edits = [(a, b, f'{table}[{index}].f0') for a, b, index, _, _ in uses] + [(decl.start(), decl.end(), record)]
            result.append((FAMILY, edit(source, edits)))
    return result[:6]
