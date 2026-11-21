"""Cheap local ordering hypotheses before random drift; only the normal judge can accept one."""
import re

from ..forks.csource import _blank, find


def variants(source, name):
    span = find(source, name)
    if not span:
        return []
    clean = _blank(source)
    opening = clean.find('{', *span)
    if opening < 0 or re.search(r'\bvolatile\b|^\s*#', clean[span[0]:span[1]], re.M):
        return []
    result = []
    # Adjacent constant stores through the same pointer often differ only in scheduling. Do not
    # cross statements, branches, indentation levels or a call; preserve the original lines verbatim.
    store = re.compile(r'(?m)^([ \t]+)([A-Za-z_]\w*)->([A-Za-z_]\w*)\s*=\s*'
                       r'(-?(?:0[xX][0-9a-fA-F]+|[0-9]+(?:\.[0-9]+)?)[uUlLfF]*)\s*;[ \t]*$')
    matches = list(store.finditer(clean, opening, span[1]))
    for left, right in zip(matches, matches[1:]):
        if (clean[left.end():right.start()] != '\n' or (left[1], left[2]) != (right[1], right[2])
                or left[3] == right[3] or left[4] != right[4]):
            continue
        result.append(('adjacent_constant_stores', source[:left.start()] + source[right.start():right.end()] + '\n'
                       + source[left.start():left.end()] + source[right.end():]))
    # A common integer multiply-by-33 idiom: operand order can choose which value occupies the
    # destination register. Both operands remain once each; the compiler/link judge remains mandatory.
    for match in re.finditer(r'\b([A-Za-z_]\w*)\s*\+\s*\(\s*\1\s*<<\s*([0-9]+)\s*\)', clean[opening:span[1]]):
        a, b = opening + match.start(), opening + match.end()
        result.append(('shift_add_order', source[:a] + f'({match[1]} << {match[2]}) + {match[1]}' + source[b:]))
    return result[:8]
