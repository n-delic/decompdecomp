"""Small evaluation/lifetime alternatives; no assumed registers or forced machine code."""
import re

from .integer_repair_support import IDENT, NUMBER, edit, integer, locals_for, view

FAMILIES = ('store_value_lifetime', 'assignment_chain_lifetime', 'pointer_index_order')
LIMIT = 12


def variants(source, name):
    part = view(source, name)
    if not part or re.search(r'\b(?:volatile|asm|__asm__)\b', part.clean):
        return []
    result = []
    def add(family, start, end, replacement):
        candidate = edit(source, [(start, end, replacement)])
        if len(result) < LIMIT and (family, candidate) not in result:
            result.append((family, candidate))
    # A typed, block-local constant exposes its lifetime without touching pointer evaluation.
    # Bound the value to signed int range: both ordinary int and unsigned stores see the same value.
    scratch = 'repair_store_value'
    while re.search(rf'\b{scratch}\b', part.clean):
        scratch += '_'
    for hit in re.finditer(rf'\b{IDENT}\s*->\s*{IDENT}\s*=\s*({NUMBER})\s*;', part.body):
        start, end = part.opening+1+hit.start(), part.opening+1+hit.end()
        if not 0 < integer(hit[1]) <= 0x7fffffff:
            continue
        prefix = part.clean[part.opening+1:start].rstrip()
        if prefix and prefix[-1:] not in {';', '{', '}'}:
            continue  # don't replace an unbraced conditional/loop's body or expression
        statement = source[start:end]
        value_start = hit.start(1)-hit.start()
        value_end = hit.end(1)-hit.start()
        statement = statement[:value_start]+scratch+statement[value_end:]
        add('store_value_lifetime', start, end,
            '{ int '+scratch+' = '+hit[1]+'; '+statement+' }')
    locals_ = {decl['name']:decl for decl in locals_for(part)}
    # A comma expression retains the chain's value and both conversions. Restrict to identical
    # unescaped local types so reading the inner assignment cannot narrow or change precision.
    for hit in re.finditer(rf'\b({IDENT})\s*=\s*({IDENT})\s*=\s*([^;{{}}]+?)(?=;|\)\s*(?:[<>!=]|\)))', part.body):
        a, b = locals_.get(hit[1]), locals_.get(hit[2])
        if a is None or b is None or a['type'] != b['type'] or hit[1] == hit[2]:
            continue
        # Leave expression parsing, calls, and chained assignments outside this bounded shape.
        expr = hit[3].strip()
        if not re.fullmatch(rf'{IDENT}(?:\[\s*{NUMBER}\s*\])?\s*<<\s*(?:[1-9]|[12][0-9]|3[01])', expr):
            continue
        add('assignment_chain_lifetime', part.opening+1+hit.start(), part.opening+1+hit.end(),
            f'({hit[2]} = {expr}, {hit[1]} = {hit[2]})')
    # A pure member-pointer addition has the same evaluation count in either operand order.
    # Valid original C proves one operand is a pointer; neither operand has side effects.
    for hit in re.finditer(rf'\b({IDENT}\s*->\s*{IDENT})\s*\+\s*({IDENT})\b(?=\s*[;,)])', part.body):
        prefix = part.clean[part.opening+1:part.opening+1+hit.start()].rstrip()
        if prefix and prefix[-1:] not in {'=', '(', ',', ';', '{', '}'}:
            continue
        add('pointer_index_order', part.opening+1+hit.start(), part.opening+1+hit.end(),
            f'({hit[2]} + {hit[1]})')
    return result
