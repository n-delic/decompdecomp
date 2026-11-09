"""Explicit lvalues for named volatile hardware registers, never scalar-value-to-address rewrites."""
import re

from ..forks.csource import _blank, find

# m2c writes a register access as `*(s32 *)0xA4040010`. The original source read and wrote it through a
# volatile type, often the unsigned one; without volatile the compiler may drop, merge or reorder the access.
_LITERAL = re.compile(r'\*\s*\(\s*(?P<type>(?:unsigned\s+)?(?:char|short|int)|[su](?:8|16|32))\s*\*\s*\)\s*(?P<addr>0x[ABab][0-9A-Fa-f]{7})\b')
_FLIPPED = {'s8':'u8','u8':'s8','s16':'u16','u16':'s16','s32':'u32','u32':'s32'}


def _literal_variants(source, clean, opening, end):
    """Whole-function rewrites of KSEG1 literal accesses to volatile ones: the same type, then the other signedness."""
    hits = [h for h in _LITERAL.finditer(clean, opening, end) if 0xA0000000 <= int(h['addr'], 16) < 0xC0000000]
    out = []
    for flip in (False, True):
        text = source
        for hit in reversed(hits):
            typ = ' '.join(hit['type'].split())
            typ = _FLIPPED.get(typ, typ) if flip else typ
            text = text[:hit.start()] + f'*(volatile {typ} *){hit["addr"]}' + text[hit.end():]
        if hits and text not in out:
            out.append(text)
    return out


def variants(source, name, arch):
    if arch != 'mips':
        return []
    span = find(source, name)
    if not span:
        return []
    clean = _blank(source)
    opening = clean.find('{', *span)
    if opening < 0 or '#' in clean[opening:span[1]]:
        return []
    literal = _literal_variants(source, clean, opening, span[1])
    # These names encode their absolute address. Require an explicit volatile-width declaration,
    # and the MIPS uncached KSEG1 region; never reinterpret RAM globals or pointer variables.
    declarations = re.finditer(r'\bextern\s+(?P<type>vu(?:8|16|32)|volatile\s+(?:unsigned\s+)?(?:char|short|int))\s+(?P<name>D_(?P<addr>[ABab][0-9A-Fa-f]{7}))\s*;',clean[:opening])
    out = []
    for m in declarations:
        address = int(m['addr'],16)
        if not 0xA0000000 <= address < 0xC0000000:
            continue
        typ = {'vu8':'volatile unsigned char','vu16':'volatile unsigned short','vu32':'volatile unsigned int'}.get(m['type'],m['type'])
        body = source[opening:span[1]]
        # Assignments only: keep RHS calls and evaluation order; don't touch comments/strings or &name.
        matches = list(re.finditer(r'(?<![\w&])\b'+re.escape(m['name'])+r'\b(?=\s*=(?!=))',clean[opening:span[1]]))
        if not matches:
            continue
        for hit in reversed(matches):
            body = body[:hit.start()]+f'*({typ} *)0x{address:08X}'+body[hit.end():]
        out.append(source[:opening]+body+source[span[1]:])
        if len(out) >= 4:
            break
    return out + literal
