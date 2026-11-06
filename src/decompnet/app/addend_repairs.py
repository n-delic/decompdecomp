"""Candidate-local MIPS address-addend corrections; the compiler decides pointer scaling and exact bytes."""

import re
from collections import defaultdict

from ..rom.symbols import AUTO_NAME

EXPR = re.compile(r'%(hi|lo)\(([A-Za-z_]\w*)(?:([+-])(0x[0-9a-fA-F]+|[0-9]+))?\)')


def variants(source, report):
    from .similar_matches import TOKENS

    def integer(text):
        return int(text, 16 if text.lower().startswith('0x') else 8 if len(text) > 1 and text.startswith('0') else 10)

    votes, halves = defaultdict(set), defaultdict(set)
    rows = list(report.get('rows', []))
    # Canonical target listings sometimes symbolize only LUI and leave ADDIU's low half numeric.
    # Reconstruct adjacent same-register pairs, including the signed low-half carry, before comparing addends.
    for i in range(len(rows) - 1):
        mark, wanted, compiled = rows[i]
        a, b = EXPR.search(wanted), EXPR.search(compiled)
        high = re.fullmatch(r'lui (\$\w+), .*', wanted)
        if not a or not b or a[1] != 'hi' or b[1] != 'hi' or not high:
            continue
        base = AUTO_NAME.fullmatch(a[2])
        low_mark, low_target, low_candidate = rows[i + 1]
        low = re.fullmatch(r'addiu ' + re.escape(high[1]) + ', ' + re.escape(high[1]) + r', (-?0x[0-9a-fA-F]+|-?[0-9]+)', low_target)
        c = EXPR.search(low_candidate)
        if not base or not low or not c or c[1] != 'lo' or (c[2], c[3], c[4]) != (b[2], b[3], b[4]):
            continue
        if EXPR.sub('%address', low_candidate) != low_target[:low.start(1)] + '%address':
            continue
        address = int(base[1], 16)
        extra = integer(a[4]) * (-1 if a[3] == '-' else 1) if a[4] else 0
        immediate = int(low[1], 16 if '0x' in low[1] else 10)
        if not -32768 <= immediate <= 65535:
            continue
        if immediate >= 32768:
            immediate -= 65536
        offset = (((address + extra + 0x8000) >> 16) << 16) + immediate - address
        expr = a[2] + (f'+{offset}' if offset >= 0 else str(offset))
        rows[i] = (mark, EXPR.sub('%hi(' + expr + ')', wanted), compiled)
        rows[i + 1] = (low_mark, low_target[:low.start(1)] + '%lo(' + expr + ')', low_candidate)
    for _, wanted, compiled in rows:
        a, b = EXPR.search(wanted), EXPR.search(compiled)
        if not a or not b or a[1] != b[1] or EXPR.sub('%address', wanted) != EXPR.sub('%address', compiled):
            continue
        def value(m):
            return integer(m[4]) * (-1 if m[3] == '-' else 1) if m[4] else 0
        old, new = (b[2], value(b)), (a[2], value(a))
        votes[old].add(new)
        halves[old, new].add(a[1])
    changes = [(old, next(iter(new))) for old, new in votes.items()
               if len(new) == 1 and old not in new and halves[old, next(iter(new))] == {'hi', 'lo'}]
    # Keep original token positions while hiding comments and strings from expression matching.
    masked = TOKENS.sub(lambda m: ' ' * len(m.group()) if m.group().startswith(('/*', '//', '"', "'")) else m.group(), source)
    out = []
    for (old, before), (new, after) in changes[:2]:
        if (before == after and old == new) or before < 0 or after < 0:
            continue
        # Match the full expression, never a numeric token elsewhere in the function or a declaration.
        pattern = re.compile(r'\b' + re.escape(old) + r'\s*(?:\[\s*(0x[0-9a-fA-F]+|[0-9]+)\s*\]|\+\s*(0x[0-9a-fA-F]+|[0-9]+)\b)')
        for m in pattern.finditer(masked):
            literal = integer(m[1] or m[2])
            if literal == 0 or before % literal:
                continue
            scale = before // literal
            # The old compiled address proves the stride. Candidate compilation checks the proposed new stride.
            if scale not in (1, 2, 4, 8, 16, 32, 64) or after % scale:
                continue
            replacement = f'{new}[{after // scale}]' if m[1] else f'{new} + {after // scale}'
            candidate = source[:m.start()] + replacement + source[m.end():]
            if old != new:
                candidate = TOKENS.sub(lambda t, old=old, new=new: new if t.group() == old else t.group(), candidate)
            out.append(candidate)
            if len(out) == 4:
                return out
    return list(dict.fromkeys(out))
