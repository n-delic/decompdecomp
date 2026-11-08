"""Coupled byte-value lifetime hypotheses, accepted only by the ordinary exact judge."""
import re

from ..forks.csource import _blank, find

IDENT = r'[A-Za-z_]\w*'


def variants(source, name):
    span = find(source, name)
    if not span:
        return []
    clean = _blank(source)
    definition = re.search(r'\b' + re.escape(name) + r'\s*\([^{};]*\)\s*{', clean[span[0]:span[1]])
    if not definition:
        return []
    opening = span[0] + definition.end() - 1
    body = clean[opening:span[1]]
    if '#' in body:
        return []
    result = []

    def ordinary(variable, typ):
        # Do not widen volatile/qualified objects, arrays, arguments or shared globals.
        declarations = list(re.finditer(r'(?:[;{}]|^)\s*(?:' + typ + r')\s+' + re.escape(variable) + r'\s*;', body))
        return declarations[0] if len(declarations) == 1 else None

    def writes(variable, text):
        return re.search(r'(?:\+\+|--)\s*\b' + re.escape(variable) + r'\b|\b' + re.escape(variable) +
                         r'\s*(?:\+\+|--|(?:<<|>>|[+*/%&|^\-])?=(?!=))', text)

    for assignment in re.finditer(r'\b(?P<narrow>' + IDENT + r')\s*=\s*(?P<wide>' + IDENT +
                                  r')\s*&\s*0x[Ff]{2}\s*;', body):
        narrow, wide = assignment['narrow'], assignment['wide']
        declaration = ordinary(narrow, 'u8')
        if not declaration or not ordinary(wide, r's32|u32|int|unsigned\s+int'):
            continue
        if declaration.end() > assignment.start() or writes(narrow, body[declaration.end():assignment.start()]):
            continue
        later = body[assignment.end():]
        if writes(narrow, later) or writes(wide, later) or re.search(r'&\s*\b(?:' + re.escape(narrow) +
                                                                  '|' + re.escape(wide) + r')\b', body):
            continue
        masks = list(re.finditer(r'\b' + re.escape(wide) + r'\s*&\s*0x[Ff]{2}\b', later))
        if len(masks) != 1:
            continue
        use = masks[0]
        edits = [(declaration.start() + declaration[0].index('u8'),
                  declaration.start() + declaration[0].index('u8') + 2, 'u16'),
                 (assignment.start(), assignment.end(), f'{narrow} = (u8)(s8){wide};'),
                 (assignment.end() + use.start(), assignment.end() + use.start() + len(wide), narrow)]
        # An earlier copy-and-mask of the same value can keep another live range alive. Try its
        # collapse together with the width/reuse changes: individually worse steps must not prune it.
        copy = re.compile(r'\b(?P<copy>' + IDENT + r')\s*=\s*' + re.escape(wide) + r'\s*;\s*'
                          r'(?P=copy)\s*=\s*\(\s*u16\s*\)\s*(?P=copy)\s*&\s*0x[Ff]{2}\s*;')
        coupled = [m for m in copy.finditer(body[:assignment.start()])
                   if m['copy'] not in (narrow, wide) and ordinary(m['copy'], r's32|u32|int|unsigned\s+int')]
        bundles = [edits + [(m.start(), m.end(), f'{m["copy"]} = {wide} & 0xFF;')]
                   for m in coupled[:2]] + [edits]
        for bundle in bundles:
            candidate = source
            for a, b, value in sorted(bundle, reverse=True):
                candidate = candidate[:opening+a] + value + candidate[opening+b:]
            if candidate not in result:
                result.append(candidate)
        if len(result) >= 4:
            break
    return result[:4]
