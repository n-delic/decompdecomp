"""Conservative byte-field offset hypotheses learned from independently verified regional pairs.

Two functions must support a mapping; contradictory evidence removes it. This never edits shared struct types.
Only explicit pointer-plus-byte-offset C is adapted, then the ordinary judge checks every byte.
"""

import json
import re
from collections import defaultdict

MEMORY = {32, 33, 35, 36, 37, 40, 41, 43}
ARGUMENTS = {4, 5, 6, 7}


def fields(fn, big):
    relocated = {r[0] for r in json.loads(fn['relocs'] or '[]')}
    code = fn['target'] or b''
    out = {}
    for offset in range(0, len(code) - 3, 4):
        word = int.from_bytes(code[offset:offset + 4], 'big' if big else 'little')
        op, base, immediate = word >> 26, (word >> 21) & 31, word & 65535
        if offset not in relocated and op in MEMORY and base in ARGUMENTS and immediate < 32768:
            out[offset] = (op, base, immediate)
    return out


def learn(net, destination, donor):
    """Maps (memory opcode, argument register, old offset) to an agreed new byte offset."""
    votes = defaultdict(lambda: defaultdict(set))
    pairs = net.store.all("SELECT DISTINCT source_function_id,function_id FROM cross_game_attempts "
                          "WHERE project_id=? AND source_project_id=? AND outcome='match'",
                          destination['id'], donor['id'])
    for pair in pairs:
        a = net.store.one("SELECT * FROM functions WHERE id=? AND project_id=? AND status='matched'",
                         pair['source_function_id'], donor['id'])
        b = net.store.one("SELECT * FROM functions WHERE id=? AND project_id=? AND status='matched'",
                         pair['function_id'], destination['id'])
        if not a or not b or len(a['target'] or b'') != len(b['target'] or b''):
            continue
        left, right = fields(a, donor['big_endian']), fields(b, destination['big_endian'])
        for offset, key in left.items():
            other = right.get(offset)
            if other and key[:2] == other[:2]:
                votes[key][other[2]].add((a['id'], b['id']))
    return {key: next(iter(values)) for key, values in votes.items()
            if len(values) == 1 and len({a for a, _ in next(iter(values.values()))}) >= 2
            and len({b for _, b in next(iter(values.values()))}) >= 2}


def variants(source, a, b, mapping, big):
    from .similar_matches import INTEGER, TOKENS

    old_fields, new_fields = set(fields(a, big).values()), set(fields(b, big).values())
    changes = defaultdict(set)
    for key in old_fields:
        new = mapping.get(key)
        if new is not None and new != key[2] and (*key[:2], new) in new_fields:
            changes[key[2]].add(new)
    out = source
    edits = []
    for token in TOKENS.finditer(source):
        number = INTEGER.fullmatch(token.group())
        if not number:
            continue
        old = int(number[1], 16 if number[1].lower().startswith('0x') else 10)
        # Only explicit pointer/byte offset syntax; named fields need type-specific reconstruction, planned work.
        if old in changes and len(changes[old]) == 1 and re.search(r'\b\w+\s*\+\s*$', source[:token.start()]):
            edits.append((token.start(), token.end(), str(next(iter(changes[old]))) + number[2]))
    for start, end, replacement in reversed(edits):
        out = out[:start] + replacement + out[end:]
    return [out] if edits and out != source else []
