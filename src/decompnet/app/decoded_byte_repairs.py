"""Normalize a real packed-byte decode before widening into existing scalar carriers.

A visible unsigned-byte load bounds the subsequent shift. No reads, assignments,
storage or control are introduced, and no evaluation is moved.
"""
import re

from . import integer_abi, pointer_pair_repairs
from .integer_repair_support import DECL, IDENT, NUMBER, edit, integer
from .pattern_repairs import close

FAMILY = 'decoded_byte_normalization'


def address_escape(body, name, known):
    for hit in re.finditer(rf'(?<!&)&(?!&)\s*\(*\s*{re.escape(name)}\b', body):
        prefix = body[:hit.start()].rstrip()
        # A preceding ordinary identifier/literal is binary AND. Unknown
        # punctuation (including casts) stays a conservative address barrier.
        token = re.search(r'([A-Za-z_]\w*|[0-9][A-Za-z0-9_.]*)$', prefix)
        if not token or (token[1] not in known and not token[1][0].isdigit()):
            return True
    return False


def variants(source, name, header='', arch=None, *, language=None, int_bits=None):
    if language != 'c' or int_bits != 32:
        return []  # C++ calls may mutate scalar reference arguments without &
    part, _, macros = pointer_pair_repairs.context(source, name, header)
    if not part or set(re.findall(IDENT, part.body)) & macros.keys():
        return []
    if re.search(r'\b(?!(?:if|for|while|switch)\b)'+IDENT+r'\s*\([^;{}]*\)\s*{', part.body):
        return []  # nested functions can capture locals without an address escape
    if (re.search(r'\b(?:throw|co_await|co_return|new|delete)\b', part.body)
            or re.search(r'\[[^\[\];{}]*\]\s*(?:\([^;{}]*\)\s*)?(?:mutable\s*)?(?:->[^;{]+)?{', part.body)):
        return []  # C++ capture/exception syntax is outside this local proof
    context = source+'\n'+header
    declarations = {d['name']: d for d in DECL.finditer(part.clean, part.opening+1, part.end)
                    if not part.clean[part.opening+1:d.start()].rstrip()
                    or part.clean[part.opening+1:d.start()].rstrip()[-1] in ';{}'}
    types = {key: integer_abi.scalar(d['type'], context, arch) for key, d in declarations.items()}
    chain = re.compile(rf'(?<=[;{{}}])\s*({IDENT})\s*=\s*\(\s*({IDENT})\s*=\s*'
                       rf'(?P<value>{IDENT})\s*>>\s*({NUMBER})\s*\)\s*;')
    result = []
    for hit in chain.finditer(part.clean, part.opening+1, part.end):
        names = set(hit.group(1, 2, 'value'))
        if not names <= declarations.keys() or any(types[n] is None for n in names):
            continue
        if any(address_escape(part.body, n, types) for n in names):
            continue
        ambiguous = False
        for n in names:
            bindings = [m for m in re.finditer(rf'\b({IDENT})(?:\s+\**\s*|\s*\*+\s*){re.escape(n)}\s*(?=[;=,\[])', part.body)
                        if m[1] not in {'return', 'goto', 'case', 'else'}]
            if len(bindings) != 1:
                ambiguous = True
        if ambiguous:
            continue
        value = hit['value']
        bits, signed = types[value]
        if bits < 8 or (signed and bits == 8):
            continue
        shift = integer(hit[4])
        if not 1 <= shift <= 7:
            continue
        loads = list(re.compile(rf'(?<=[;{{}}])\s*{re.escape(value)}\s*=(?!=)\s*([^;{{}}]+);').finditer(
            part.clean, part.opening+1, hit.start()))
        if not loads:
            continue
        load = loads[-1]
        rhs = load[1].strip()
        byte = re.match(r'\*\s*\(\s*\(\s*(u8|unsigned\s+char)\s*\*\s*\)', rhs)
        if (not byte or close(rhs, rhs.index('(')) != len(rhs)
                or integer_abi.scalar(byte[1], context, arch) != (8, False)):
            continue
        operand = rhs[byte.end():-1].strip()
        # The cast must bind the complete operand of the dereference. An initial
        # byte cast in a comma/conditional expression says nothing about the
        # final pointer type: *((u8 *)p, wide_pointer) is a wide load.
        if not (re.fullmatch(IDENT, operand)
                or (operand.startswith('(') and close(operand, 0) == len(operand))):
            continue
        gap = part.clean[load.end():hit.start()]
        if re.search(r'[{}]|\b(?:if|else|switch|for|while|do|goto|return|break|continue)\b', gap):
            continue
        # A plain unescaped local cannot be changed by an intervening call, but
        # visible assignments and prefix/postfix updates invalidate its range.
        if re.search(rf'\b{re.escape(value)}\s*(?:\+\+|--|(?:<<|>>|[+*/%&|^\-])?=(?!=))|'
                     rf'(?:\+\+|--)\s*\b{re.escape(value)}\b', gap):
            continue
        # Nested/shadowed declarations require a full binding model. This family
        # only uses unique function-block declarations, not their textual suffixes.
        if any(part.clean[part.opening+1:declarations[n].start()].count('{')
               != part.clean[part.opening+1:declarations[n].start()].count('}') for n in names):
            continue
        start = hit.start('value')
        end = hit.end(4)
        result.append((FAMILY, edit(source, [(start, end, '('+byte[1]+')('+source[start:end]+')')])))
        if len(result) >= 4:
            break
    return result
