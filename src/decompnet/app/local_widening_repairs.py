"""Try wider locals when arithmetic or a later conversion contradicts a byte/halfword declaration.

A high similarity score can hide a truncated screen coordinate. These bounded hypotheses use source
evidence; they never change a matched function or replace the ordinary compile/link judge.
"""
import re

from .integer_repair_support import NUMBER, edit, integer, locals_for, refs, view
from .pattern_repairs import close

WIDTHS = {'s8': 8, 'u8': 8, 'char': 8, 'signed char': 8, 'unsigned char': 8,
          's16': 16, 'u16': 16, 'short': 16, 'signed short': 16, 'unsigned short': 16}


def large_arithmetic(rhs, maximum):
    """A large coefficient/value at expression depth zero, not a pointer's field offset or cast type."""
    rhs = rhs.strip()
    while rhs.startswith('(') and close(rhs, 0) == len(rhs):
        rhs = rhs[1:-1].strip()
    if re.search(r'\bsizeof\b', rhs):
        return False
    depth = 0
    for token in re.finditer(rf'(?<![\w.]){NUMBER}(?![\w.])|[()]', rhs):
        if token[0] == '(':
            depth += 1
        elif token[0] == ')':
            depth -= 1
        elif depth == 0 and integer(token[0]) > maximum:
            return bool(re.fullmatch(NUMBER, rhs) or re.search(r'[+*\-]|<<', rhs))
    return False


def variants(source, name):
    part = view(source, name)
    if not part:
        return []
    result = []
    for decl in locals_for(part):
        typ = ' '.join(decl['type'].split())
        bits = WIDTHS.get(typ)
        if not bits:
            continue
        unsigned = typ.startswith('u') or typ.startswith('unsigned')
        maximum = (1 << (bits if unsigned else bits-1))-1
        arithmetic = conversion = False
        for hit in refs(part, decl):
            tail = part.clean[hit.end():]
            assignment = re.match(r'\s*=(?!=)([^;{}]+);', tail)
            if assignment:
                arithmetic |= large_arithmetic(assignment[1], maximum)
            if bits == 8:
                conversion |= bool(re.search(r'\(\s*(?:[su]16|(?:unsigned\s+)?short)\s*\)\s*$', part.clean[:hit.start()]))
            shift = re.match(rf'\s*<<\s*({NUMBER})', tail)
            conversion |= bool(shift and integer(shift[1]) >= bits)
        if not arithmetic and not conversion:
            continue
        choices = ('short', 'unsigned short', 'int') if bits == 8 else ('int', 'unsigned int')
        for choice in choices:
            result.append(edit(source, [(decl.start('type'), decl.end('type'), choice)]))
        if len(result) >= 8:
            break
    return result[:8]
