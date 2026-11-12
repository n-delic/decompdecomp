"""Stage ordinary byte-array compound updates through a same-type scalar.

The load and final store stay at the same statement, without moving another memory
operation. Compound updating the temporary can give old GCC a different live range
from assigning a binary expression straight back to the array. The judge decides.
"""
import itertools
import re

from . import integer_abi
from .csource import _blank
from .integer_repair_support import IDENT, edit, locals_for, view

FAMILY = 'loaded_byte_compound'
FAMILIES = (FAMILY,)
BYTE = r'(?:u8|unsigned\s+char)'
CELL = rf'{IDENT}\s*\[\s*(?:{IDENT}|[0-9]+)\s*\]'


def declarators(text, name):
    """Notice unknown/qualified type spellings as well as supported declarations."""
    return list(re.finditer(rf'\b{IDENT}(?:\s+\**\s*|\s*\*+\s*)'
                            rf'(?P<name>{re.escape(name)})\s*(?=[;=,)\[])', text))


def complex_declarator(text, name):
    # Reject parentheses and later comma declarators conservatively, including
    # pointers to arrays and typedef-qualified shadows that the scalar view omits.
    return re.search(rf'\(\s*\**\s*(?:(?:const|volatile|restrict)\s+)*{re.escape(name)}\s*\)\s*(?=[;=(,\[])|'
                     rf',\s*\**\s*{re.escape(name)}\s*(?=[;=)\[])', text)


def variants(source, name, header='', arch=None):
    part = view(source, name)
    if not part or '#' in part.body or re.search(r'\b(?:volatile|_Atomic|asm|__asm__|typeof|__typeof__)\b', part.body):
        return []
    context = _blank(source[:part.opening] + '\n' + header)
    macros = set(re.findall(r'^\s*#\s*define\s+('+IDENT+')', context, re.M))
    # Only explicit unqualified global byte arrays. Pointer aliases, members,
    # unknown typedefs and qualified/shadowed declarations are not inferred.
    arrays = {}
    for d in re.finditer(rf'\bextern\s+({BYTE})\s+({IDENT})\s*\[\s*[0-9]*\s*\]\s*;', context):
        root = d[2]
        if integer_abi.scalar(d[1], context, arch) != (8, False):
            continue
        declarations = declarators(context, root)
        if (len(declarations) != 1 or declarators(part.body, root)
                or complex_declarator(context+'\n'+part.body, root)):
            continue
        arrays[root] = d[1]
    indices = set()
    for d in locals_for(part):
        index = d['name']
        declarations = declarators(part.clean[part.opening+1:part.end], index)
        if (integer_abi.scalar(d['type'], context, arch) and len(declarations) == 1
                and declarations[0].start('name')+part.opening+1 == d.start('name')
                and not declarators(context, index)
                and not complex_declarator(context+'\n'+part.body, index)
                and part.clean[part.opening+1:d.start()].count('{')
                    == part.clean[part.opening+1:d.start()].count('}')):
            indices.add(index)
    token = '__repair_loaded_byte'
    while re.search(r'\b'+token+r'\b', source+'\n'+header):
        token += '_'
    hits = []
    pattern = rf'(?P<lhs>(?P<root>{IDENT})\s*\[\s*(?P<index>{IDENT})\s*\])\s*\+=\s*(?P<rhs>[^;{{}}]+);'
    for m in re.compile(pattern).finditer(part.clean, part.opening+1, part.end):
        if m['root'] not in arrays or m['index'] not in indices:
            continue
        if part.clean[:m.start()].rstrip()[-1:] not in ';{}':
            continue
        lhs = re.sub(r'\s+', '', m['lhs'])
        rhs = re.sub(r'\s+', '', m['rhs'])
        if macros & set(re.findall(IDENT, m[0])):
            continue
        # A right shift, or a bounded byte product divided by a nonzero positive
        # literal: promoted arithmetic cannot overflow a 32-bit int. The cell
        # appears once in the RHS and once as the compound assignment's load.
        shift = re.fullmatch(re.escape(lhs)+r'>>(\d+)', rhs)
        product = re.fullmatch(r'\('+re.escape(lhs)+r'\*('+CELL+r')\)/(\d+)', rhs)
        if shift:
            if not 0 < int(shift[1]) < 8:
                continue
        elif product:
            factor = re.match(IDENT, product[1])[0]
            if factor not in arrays or not 0 < int(product[2]) <= 2147483647:
                continue
            factor_index = re.search(r'\[(.*)\]', product[1])[1]
            if not factor_index.isdigit() and factor_index not in indices:
                continue
        else:
            continue
        original_lhs = source[m.start('lhs'):m.end('lhs')]
        original_rhs = source[m.start('rhs'):m.end('rhs')]
        cell = re.escape(m['root'])+r'\s*\[\s*'+re.escape(m['index'])+r'\s*\]'
        load = re.search(cell, m['rhs'])
        expression = original_rhs[:load.start()]+token+original_rhs[load.end():]
        replacement = f'{token} = {original_lhs}; {token} += {expression}; {original_lhs} = {token};'
        hits.append((m.start(), m.end(), replacement, arrays[m['root']]))
    hits = hits[:4]
    result = []
    # Independent sites may need the same allocator change together. Pairs come
    # before singles so a small local budget does not discard a joint solution.
    groups = list(itertools.combinations(hits, 2)) + [(h,) for h in hits]
    for group in groups[:10]:
        if len({h[3] for h in group}) != 1:
            continue
        edits = [(a,b,value) for a,b,value,_ in group]
        edits.append((part.opening+1, part.opening+1, '\n'+group[0][3]+' '+token+';\n'))
        result.append((FAMILY, edit(source, edits)))
    return result
