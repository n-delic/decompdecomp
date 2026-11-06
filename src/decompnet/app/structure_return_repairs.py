"""Recover a candidate's explicit result-pointer lowering using its existing record type.

A C structure return lets the period compiler choose its hidden result-pointer ABI and copy schedule.
No shared declaration or record layout is invented, and the ordinary byte judge remains mandatory.
"""
import re

from ..forks.csource import _blank, find
from .integer_repair_support import IDENT, edit, view
from .pattern_repairs import close


def variants(source, name, header=''):
    part = view(source, name)
    if part:
        part.end = close(part.clean, part.opening, '{', '}') or part.end
    if not part or re.search(r'\b(?:volatile|asm|__asm__)\b', part.body):
        return []
    start, _ = find(source, name)
    signature = re.fullmatch(rf'\s*void\s+{re.escape(name)}\s*\(\s*({IDENT})\s*\*\s*({IDENT})\s*,([^()]*)\)\s*',
                             part.clean[start:part.opening])
    if not signature:
        return []
    record, output, args = signature.groups()
    context = _blank(header+'\n'+source[:start])
    if not re.search(rf'\btypedef\s+struct\s*(?:{IDENT}\s*)?{{[^{{}}]+}}\s*{re.escape(record)}\s*;', context):
        return []
    if re.search(rf'\b{re.escape(name)}\s*\(', context) or re.search(rf'\b{re.escape(name)}\s*\(', part.body):
        return []  # Updating a shared prototype or recursive call needs a project-wide ABI review.
    if re.search(rf'^\s*#\s*define\s+(?:{re.escape(record)}|{re.escape(output)})\b', header+'\n'+source, re.M):
        return []
    tail = re.search(rf'\*\s*{re.escape(output)}\s*=\s*({IDENT})\s*;\s*$', part.body)
    if not tail or len(re.findall(rf'\b{re.escape(output)}\b', part.body)) != 1:
        return []
    value = tail[1]
    if not re.search(rf'\b{re.escape(record)}\s+{re.escape(value)}\s*;', part.body):
        return []
    if re.search(r'\breturn\b', part.body[:tail.start()]):
        return []
    return [('structure_return', edit(source, [(start, part.opening, f'{record} {name}({args.strip()})\n'),
              (part.opening+1+tail.start(), part.opening+1+tail.end(), f'return {value};\n')]))]
