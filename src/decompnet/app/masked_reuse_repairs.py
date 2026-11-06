"""Reuse a dead 32-bit local for a narrowed value, without choosing machine registers.

A cast/mask temporary can coalesce with a call-preserved register when a fresh temporary cannot.
Control-flow liveness must prove the destination's old value dead on every successor path.
"""
import re

from . import local_flow
from .integer_repair_support import IDENT, edit, refs

LIMIT = 24
WIDE = {'s32', 'u32', 'int', 'signed int', 'unsigned int'}
CAST = re.compile(rf'\(\s*(?:u16|unsigned\s+short|u8|unsigned\s+char)\s*\)\s*(?P<value>{IDENT})\b')
MASK = re.compile(rf'\b(?P<value>{IDENT})\s*&\s*(?:0[xX](?:[fF]{{2}}|[fF]{{4}})|255|65535)[uU]?\b')


def variants(source, name):
    """Materialize one complete mask/cast expression, preferring related and soon-reset locals."""
    flow = local_flow.analyze(source, name)
    if not flow:
        return []
    part = flow.part
    locals_ = [decl for decl in flow.locals if decl['type'] in WIDE
               and part.clean[part.opening:decl.start()].count('{') == part.clean[part.opening:decl.start()].count('}')+1]
    choices = []
    for pattern in (CAST, MASK):
        for match in pattern.finditer(part.clean, part.opening, part.end):
            before = part.clean[:match.start()].rstrip()
            after = part.clean[match.end():].lstrip()
            if pattern is MASK and not ((before.endswith('(') and after.startswith(')'))
                                        or (before.endswith('=') and after.startswith(';'))):
                continue  # Never split an operand out of a larger, differently associated expression.
            if pattern is CAST and re.match(r'\+\+|--|\[|\.|->|\(', after):
                continue
            node = flow.at(match.start())
            if not node:
                continue
            # Insert before a complete expression statement, never a condition, loop header, label,
            # declaration initializer, return, or an unbraced arm (which would change its extent).
            prefix = part.clean[:node.start].rstrip()
            statement = part.clean[node.start:node.end]
            if (prefix[-1:] not in {';', '{', '}', ':'} or not statement.rstrip().endswith(';')
                    or re.match(r'\s*(?:return|case|default)\b', statement)
                    or not re.match(rf'\s*{IDENT}\s*=(?!=)', statement)):
                continue
            source_decl = next((decl for decl in flow.locals if decl['name'] == match['value']), None)
            if source_decl is None:
                continue
            for dest in locals_:
                variable = dest['name']
                if (variable == match['value'] or dest.end() > node.start
                        or re.search(rf'\b{variable}\b', statement) or not flow.dead(variable, node.start)):
                    continue
                later = [hit for hit in refs(part, dest) if hit.start() > match.end()]
                same_stem = re.sub(r'_\d+$', '', variable) == re.sub(r'_\d+$', '', match['value'])
                distance = later[0].start()-match.start() if later else part.end
                expression = source[match.start():match.end()]
                candidate = edit(source, [(match.start(), match.end(), variable),
                                         (node.start, node.start, f'{variable} = {expression};\n    ')])
                choices.append((not same_stem, distance, candidate))
    return [('masked_value_reuse', candidate) for candidate in dict.fromkeys(
        text for _, _, text in sorted(choices, key=lambda c: c[:2]))][:LIMIT]
