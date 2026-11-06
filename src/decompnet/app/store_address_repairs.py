"""Stage a scalar store's byte address without moving its loads across other statements.

Older compilers can choose a different operand order for an address kept in a pointer temporary.
These bounded source hypotheses still need the ordinary linked/object judge to accept them.
"""
import re

from ..forks.csource import _blank, find
from .register_repairs import unwrap

IDENT = r'[A-Za-z_]\w*'
SCALAR = r'(?:[su](?:8|16|32)|char|short|int|unsigned\s+(?:char|short|int))'
NUMBER = r'(?:0x[0-9a-fA-F]+|[0-9]+)'


def split_sum(text):
    """Find a binary addition outside casts and nested address expressions."""
    depth = 0
    for pos, token in enumerate(text):
        depth += (token in '([') - (token in ')]')
        if token == '+' and depth == 0:
            return text[:pos].strip(), text[pos+1:].strip()
    return None


def variants(source, name, arch):
    if arch not in {'mips', 'ppc', 'x86', 'arm', 'thumb'}:
        return []
    span = find(source, name)
    if not span:
        return []
    clean = _blank(source)
    opening = clean.find('{', *span)
    if opening < 0 or re.search(r'\bvolatile\b', clean) or '#' in clean[opening:span[1]]:
        return []
    body = clean[opening+1:span[1]-1]
    result = []
    fresh = '__repair_store_address'
    if re.search(r'\b'+fresh+r'\b', clean):
        return []
    # The RHS is a simple value: do not reorder an address load around a call or another store.
    store = re.compile(r'(?:^|(?<=[;{}]))\s*(?P<lhs>\*[^;{}]+?)\s*=(?!=)\s*(?P<rhs>'+IDENT+'|'+NUMBER+r')\s*;')
    for hit in store.finditer(body):
        lhs = unwrap(hit['lhs'][1:])
        cast = re.match(r'\(\s*('+SCALAR+r')\s*\*\s*\)', lhs)
        if not cast:
            continue
        address = unwrap(lhs[cast.end():])
        outer = split_sum(address)
        if not outer or not re.fullmatch(NUMBER, outer[1]):
            continue
        bytecast = re.match(r'\(\s*(?:char|u8|s8|unsigned\s+char)\s*\*\s*\)', unwrap(outer[0]))
        if not bytecast:
            continue
        term = unwrap(outer[0])
        summed = split_sum(unwrap(term[bytecast.end():]))
        if not summed:
            continue
        left, right = summed
        # One base identifier and one nontrivial offset. No calls, increments, assignments or comma
        # operators, and no unknown character tokens. Every original load is evaluated exactly once.
        if re.fullmatch(IDENT, right):
            base, index = right, left
        elif re.fullmatch(IDENT, left):
            base, index = left, right
        else:
            continue
        if not re.search(r'[\[\]*]', index) or re.search(r'\+\+|--|[=,?:;{}]|\b'+IDENT+r'\s*\(', index):
            continue
        replacement = ('{ char *'+fresh+' = (char *)'+base+'; '+fresh+' += '+index+'; '
                       '*('+cast[1]+' *)('+fresh+' + '+outer[1]+') = '+hit['rhs']+'; }')
        start = opening+1+hit.start('lhs')
        end = opening+1+hit.end()
        candidate = source[:start]+replacement+source[end:]
        if candidate != source and candidate not in result:
            result.append(candidate)
        if len(result) >= 4:
            break
    return result
