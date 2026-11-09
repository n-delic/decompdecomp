"""Explicit lvalues for named volatile hardware registers, never scalar-value-to-address rewrites."""
import re

from ..forks.csource import _blank, find


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
    return out
