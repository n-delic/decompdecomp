"""Bounded operand-order hypotheses; every candidate still needs the ordinary exact judge."""
import re

from ..forks.csource import find
from .integer_repair_support import IDENT, edit, view

# Only simple reads: no calls, increments, dereferences of computed expressions or nested indices.
READ = rf'{IDENT}(?:(?:->|\.){IDENT})*(?:\[\s*{IDENT}\s*\])?'


def variants(source, name, header=''):
    part = view(source, name)
    if not part or re.search(r'\b(?:volatile|asm|__asm__)\b', part.clean+'\n'+header):
        return []
    # Macro expansion can add side effects even when its spelling looks like an ordinary read.
    macros = set(re.findall(r'^\s*#\s*define\s+('+IDENT+r')\b', part.clean+'\n'+header, re.M))
    result = []
    pattern = re.compile(rf'(?<![\w.>])(?P<a>{READ})\s*(?P<op>==|!=)\s*(?P<b>{READ})(?![\w.\[>])')
    for m in pattern.finditer(part.body):
        a, b = m['a'], m['b']
        if a == b or macros.intersection(re.findall(IDENT, a+' '+b)):
            continue
        # Do not partially capture a call, ++/--, pointer expression or arithmetic operand.
        before, after = part.body[:m.start()].rstrip(), part.body[m.end():].lstrip()
        if (before and before[-1] in '+-*/&|!<>=' or after.startswith(('(', '++', '--', '->', '.', '[', '+', '-', '*', '/', '&', '|', '<', '>', '='))):
            continue
        start = part.opening+1+m.start()
        result.append(('comparison_operand_order', edit(source, [(start, part.opening+1+m.end(), b+' '+m['op']+' '+a)])))
        if len(result) == 4:
            break
    # Unsigned scalar products are associative modulo their width; do not reassociate floating
    # point or signed-overflow expressions. Try original-looking orders without adding casts.
    unsigned = set(re.findall(r'\b(?:u32|unsigned\s+int)\s+('+IDENT+r')\b', part.clean[find(source, name)[0]:part.end]))
    product = re.compile(rf'(?<![\w.>])({IDENT})\s*\*\s*({IDENT})\s*\*\s*({IDENT})(?!\w)')
    for m in product.finditer(part.body):
        a, b, c = m.groups()
        if not {a,b,c} <= unsigned or {a,b,c} & macros:
            continue
        before, after = part.body[:m.start()].rstrip(), part.body[m.end():].lstrip()
        if (before and before[-1] in '*/.>' or after.startswith(('(', '[', '.', '->', '*', '/', '++', '--'))):
            continue
        start, end = part.opening+1+m.start(), part.opening+1+m.end()
        for replacement in (f'{c} * {b} * {a}', f'{a} * ({b} * {c})'):
            result.append(('integer_product_order', edit(source, [(start,end,replacement)])))
        break
    return result[:6]
