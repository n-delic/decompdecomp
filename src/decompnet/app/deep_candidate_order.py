"""Order a bounded batch of already generated candidates before compiler admission."""
import json
import re
from functools import lru_cache

from decomprepair.repair_tokens import tokens

from .. import arch

_LITERAL = re.compile(r'(0[xX][0-9a-fA-F]+|0[0-7]*|[1-9][0-9]*)[uUlL]*')


def _literals(source):
    values = set()
    for token in tokens(source):
        match = _LITERAL.fullmatch(token)
        if match is None or len(match[1]) > 12:
            continue
        digits = match[1]
        base = 16 if digits.lower().startswith('0x') else 8 if digits.startswith('0') else 10
        value = int(digits, base)
        if 0x10000 <= value <= 0xffffffff:
            values.add(value)
    return values


def for_function(project, function, log):
    reader = arch.get(project['arch']).split_immediates
    if reader is None or not function['target']:
        return None
    relocated = {reloc[0] & ~3 for reloc in json.loads(function['relocs'] or '[]')}
    target = reader(function['target'], bool(project['big_endian']), relocated)
    if not target:
        return None
    parent_literals = lru_cache(maxsize=1)(_literals)
    reported = False

    def order(parent, candidates):
        nonlocal reported
        if parent is None or len(candidates) < 2:
            return candidates
        base = parent_literals(parent)

        def distance(candidate):
            return min(((value ^ expected).bit_count()
                        for value in _literals(candidate) - base for expected in target), default=33)

        ranked = sorted(candidates, key=distance)
        if not reported and ranked != candidates:
            log('CPU constant ranking reordered a random candidate batch.')
            reported = True
        return ranked

    return order
