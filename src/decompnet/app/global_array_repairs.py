"""Global tables as the source declared them, where m2c saw only a word and an address.

- `extern M2C_UNK T; ... *(&T + (i * 4))` is `extern f32 T[]; ... T[i]`: m2c scales the index by hand and
  gives the table no element type. The element is tried as float, signed and unsigned word, the three
  loads a 4-byte stride can be.
- `extern char T[];` is the same table declared `u8` or `s8`: plain `char` is signed on this compiler, so
  the choice changes every widening load.
- `extern M2C_UNK T;` used only as `&T` is a table: `extern u8 T[];` with `T` in place of `&T` (an array name
  already is the address, and `&T` would scale arithmetic by the whole array).
- `*(T + i)` on a byte table is a halfword read, `*(u16 *)(T + i)`: m2c typed the table by its first use,
  and a record's 16-bit field read through it comes out as a byte load.
- `extern s32 G;` is a narrower or unsigned scalar in the source: the access width and signedness decide the
  load, and m2c only knows the word.
These are the rewrites agents made by hand for float, byte and scalar globals in an N64 game (gcc 2.7.2).
"""
import re

from ..forks.csource import _blank, find
from .integer_repair_support import edit
from .pattern_repairs import close

FAMILIES = ('global_table_index', 'byte_table_signedness', 'global_address_table', 'global_scalar_type', 'global_value_address', 'table_read_width')
_WORD = ('f32', 's32', 'u32')
_SCALARS = {'M2C_UNK': ('u32', 's32', 'f32'), 's32': ('u32', 's8', 'u8', 's16', 'u16', 'f32'), 'u32': ('s32', 'u8', 's8', 'u16', 's16'),
            's16': ('u16', 's8', 'u8'), 'u16': ('s16', 'u8'), 's8': ('u8',), 'u8': ('s8',), 'char': ('u8',)}
_BYTE = {'char': ('u8', 's8'), 's8': ('u8',), 'u8': ('s8',)}


def _table_index(source, clean, span):
    result = []
    for decl in re.finditer(r'^[ \t]*extern\s+(?:M2C_UNK|s32|u32|f32)\s+(D_[0-9A-Fa-f]{6,8})\s*;', clean[:span[0]], re.M):
        name = decl[1]
        uses = []
        for use in re.finditer(r'\*\s*\(\s*&\s*' + name + r'\s*\+\s*(?=\()', clean[span[0]:span[1]]):
            start, index = span[0] + use.start(), span[0] + use.end()
            outer_end = close(clean, clean.index('(', start))
            index_end = close(clean, index)
            scaled = re.fullmatch(r'\((.+?)\s*\*\s*4\s*\)', clean[index:index_end], re.S) if index_end else None
            if not scaled or index_end + 1 != outer_end or clean[index_end:outer_end].strip() != ')':
                uses = []
                break
            uses.append((start, outer_end, name + '[' + source[index+1:index+1+len(scaled[1])] + ']'))
        # Every use of the name must be one of these, or the declaration change would break the others.
        if uses and len(re.findall(r'\b' + name + r'\b', clean[span[0]:span[1]])) == len(uses):
            for typ in _WORD:
                text = edit(source, uses + [(decl.start(), decl.end(), f'extern {typ} {name}[];')])
                result.append(('global_table_index', text))
    return result


def _byte_tables(source, clean):
    result = []
    for decl in re.finditer(r'^([ \t]*extern\s+)(char|s8|u8)(\s+D_[0-9A-Fa-f]{6,8}\s*\[\s*\]\s*;)', clean, re.M):
        for typ in _BYTE[decl[2]]:
            result.append(('byte_table_signedness', edit(source, [(decl.start(2), decl.end(2), typ)])))
    return result


def _address_tables(source, clean, span):
    result = []
    for decl in re.finditer(r'^[ \t]*extern\s+(?:M2C_UNK|s32|u8|s8|char)\s+(D_[0-9A-Fa-f]{6,8})\s*;', clean[:span[0]], re.M):
        name = decl[1]
        body = clean[span[0]:span[1]]
        hits = list(re.finditer(r'(?<![\w&])&\s*' + name + r'\b', body))
        if not hits or len(hits) != len(re.findall(r'\b' + name + r'\b', body)):
            continue
        uses = [(span[0] + h.start(), span[0] + h.end(), name) for h in hits]
        for typ in ('u8', 's8'):
            result.append(('global_address_table', edit(source, uses + [(decl.start(), decl.end(), f'extern {typ} {name}[];')])))
    return result



def _scalar_types(source, clean, span):
    """One global at a time, then every global of that declared type together (a group of registers or flags)."""
    found = [d for d in re.finditer(r'^[ \t]*extern\s+(M2C_UNK|s32|u32|s16|u16|s8|u8|char)\s+(D_[0-9A-Fa-f]{6,8})\s*;', clean[:span[0]], re.M)
             if re.search(r'\b' + d[2] + r'\b', clean[span[0]:span[1]])]
    result = []
    for decl in found:
        for typ in _SCALARS[decl[1]]:
            result.append(('global_scalar_type', edit(source, [(decl.start(1), decl.end(1), typ)])))
    for declared in dict.fromkeys(d[1] for d in found):
        group = [d for d in found if d[1] == declared]
        if len(group) > 1:
            for typ in _SCALARS[declared]:
                result.append(('global_scalar_type', edit(source, [(d.start(1), d.end(1), typ) for d in group])))
    return result


def _value_addresses(source, clean, span):
    """`x = D_8010;` where D_8010 is a word global: m2c read the value where the code took the address."""
    result = []
    for decl in re.finditer(r'^[ \t]*extern\s+(?:M2C_UNK|s32|u32)\s+(D_[0-9A-Fa-f]{6,8})\s*;', clean[:span[0]], re.M):
        hits = list(re.finditer(r'(?<![=!<>+\-*/%&|^])=\s*(' + decl[1] + r')\s*;', clean[span[0]:span[1]]))
        for hit in hits[:2]:
            result.append(('global_value_address', edit(source, [(span[0] + hit.start(1), span[0] + hit.end(1), f'(s32)&{decl[1]}')])))
    return result


def _read_widths(source, clean, span):
    """Dereferences `*(T + e)` of byte tables as u16/s16 reads: every table at once, then each table, then each site."""
    per_table = []
    for decl in re.finditer(r'^[ \t]*extern\s+(?:volatile\s+)?(?:char|u8|s8)\s+(D_[0-9A-Fa-f]{6,8})\s*\[\s*\]\s*;', clean[:span[0]], re.M):
        sites = []
        for hit in re.finditer(r'\*\s*\(\s*' + decl[1] + r'\s*\+', clean[span[0]:span[1]]):
            start = span[0] + hit.start()
            # a dereference, not a product: the previous token is not an operand
            if not re.search(r'[\w)\]]\s*$', clean[:start]):
                sites.append(start)
        if sites:
            per_table.append(sites)
    def edits(starts, typ):
        # (the `*` and its `(` are replaced together: `*(u16 *)(`)
        return [(a, a + clean[a:].index('(') + 1, f'*({typ} *)(') for a in starts]
    result = []
    for typ in ('u16', 's16'):
        if len(per_table) > 1:
            result.append(('table_read_width', edit(source, edits(sum(per_table, []), typ))))
        result += [('table_read_width', edit(source, edits(sites, typ))) for sites in per_table]
    for sites in per_table:
        if len(sites) > 1:
            result += [('table_read_width', edit(source, edits([site], 'u16'))) for site in sites]
    return result


def variants(source, name):
    span = find(source, name)
    if not span:
        return []
    clean = _blank(source)
    tables = _address_tables(source, clean, span)
    # Agents retyped the table and wrote its pointer temporary out at each use in one rewrite.
    from .local_inline_repairs import variants as inlined
    tables += [('global_address_table', text) for _, table in tables[:2] for _, text in inlined(table, name, limit=4)]
    return (_table_index(source, clean, span) + _read_widths(source, clean, span) + _byte_tables(source, clean) + tables
            + _value_addresses(source, clean, span)
            + _scalar_types(source, clean, span))[:20]
