"""Try a narrower local when the candidate already casts that value to a narrow type.

A byte store's cast can be too late to reproduce the original allocator. These hypotheses change only
an ordinary scalar declaration; the exact judge, not the cast alone, decides whether to keep them.
"""
import re

from ..forks.csource import _blank, find

IDENT = r'[A-Za-z_]\w*'


def variants(source, name):
    span = find(source, name)
    if not span:
        return []
    clean = _blank(source)
    opening = clean.find('{', *span)
    if opening < 0 or '#' in clean[opening:span[1]] or re.search(r'\bvolatile\b', clean):
        return []
    body = clean[opening:span[1]]
    result = []
    declaration = re.compile(r'(?:^|(?<=[;{}]))\s*(?P<type>s32|u32|int|unsigned\s+int)\s+(?P<name>'+IDENT+r')\s*;')
    for hit in declaration.finditer(body):
        var = hit['name']
        if re.search(r'&\s*'+re.escape(var)+r'\b', body):
            continue
        cast = re.search(r'\(\s*(s8|u8|char|signed\s+char|unsigned\s+char|s16|u16|short|unsigned\s+short)\s*\)\s*'+re.escape(var)+r'\b', body[hit.end():])
        if not cast:
            continue
        # A name reused in an inner scope is ambiguous; don't retype a different declaration.
        if len(re.findall(r'\b(?:s32|u32|int|unsigned\s+int|char|short|[su](?:8|16))\s+'+re.escape(var)+r'\s*[;=,]', body)) != 1:
            continue
        choices = ('char', 'signed char', 'unsigned char') if cast[1] in {'s8','u8','char','signed char','unsigned char'} else ('short','unsigned short')
        for typ in choices:
            a, b = opening+hit.start('type'), opening+hit.end('type')
            result.append(source[:a]+typ+source[b:])
        if len(result) >= 6:
            break
    return result[:6]
