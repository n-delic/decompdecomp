"""Inline a repeated pointer-to-integer cast through a small helper for old register allocators.

The source must declare the operand as a pointer. This preserves evaluations and keeps the compiler's
inlining choice provisional: only the ordinary exact judge can accept the resulting object.
"""
import re

from ..forks.csource import _blank, find

IDENT = r'[A-Za-z_]\w*'


def variants(source, name, arch):
    if arch not in {'mips', 'ppc', 'x86', 'arm', 'thumb'}:
        return []
    span = find(source, name)
    if not span:
        return []
    clean = _blank(source)
    opening = clean.find('{', *span)
    if opening < 0 or '#' in clean[opening:span[1]] or re.search(r'\bvolatile\b', clean):
        return []
    helper = '__repair_pointer_bits'
    if re.search(r'\b'+helper+r'\b', clean):
        return []
    pointers = re.findall(r'\b(?:void|char|[su](?:8|16|32)|unsigned\s+(?:char|short|int))\s*\*\s*('+IDENT+r')\s*[;,)]', clean)
    body = clean[opening:span[1]]
    cast = re.compile(r'\(\s*(?P<type>s32|u32|int|unsigned\s+int)\s*\)\s*(?P<expr>'+IDENT+r'(?:\s*->\s*'+IDENT+r')*)(?!\w|\s*\()')
    groups = {}
    for hit in cast.finditer(body):
        expr = re.sub(r'\s+', '', hit['expr'])
        pointer = expr.split('->')[-1]
        if pointers.count(pointer) != 1:
            continue
        # Don't take a prefix out of indexed/longer expressions or post-increment a pointer.
        if re.match(r'\s*(?:\[|\.|->|\+\+|--)', body[hit.end():]):
            continue
        groups.setdefault((hit['type'], expr), []).append(hit)
    result = []
    for (typ, expr), hits in groups.items():
        if len(hits) < 2:
            continue
        text = source
        for hit in reversed(hits):
            a, b = opening+hit.start(), opening+hit.end()
            operand = source[opening+hit.start('expr'):opening+hit.end('expr')]
            text = text[:a]+helper+'('+operand+')'+text[b:]
        # Built-in return types make the helper independent of typedef placement in the candidate.
        returned = 'unsigned int' if typ in {'u32', 'unsigned int'} else 'int'
        prefix = 'static inline '+returned+' '+helper+'(void *value)\n{\n  return ('+returned+')value;\n}\n\n'
        result.append(prefix+text)
        if len(result) >= 2:
            break
    return result
