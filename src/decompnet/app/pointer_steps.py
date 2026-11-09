"""Propose explicit pointer steps when aligned MIPS increments prove a different array stride."""

import json
import re

from ..forks.csource import _blank


def variants(source, report, target, big):
    """Keep types and headers intact; the ordinary compiler/link judge must accept every proposal."""
    if not report.get('candidate_target'):
        return []
    left, right = bytes.fromhex(report['candidate_target']), target['target'] or b''
    if len(left) != len(right) or len(left) % 4:
        return []
    relocated = {r[0] for r in json.loads(report.get('candidate_relocs', '[]'))}
    relocated.update(r[0] for r in json.loads(target['relocs'] or '[]'))
    votes, signs = set(), set()
    order = 'big' if big else 'little'
    for pos in range(0, len(left), 4):
        a, b = int.from_bytes(left[pos:pos+4], order), int.from_bytes(right[pos:pos+4], order)
        if a == b or pos in relocated:
            continue
        if a >> 16 != b >> 16 or a >> 26 != 9 or (a >> 21) & 31 in (0, 28, 29, 30):
            return []
        old, new = a & 65535, b & 65535
        old = old - 65536 if old >= 32768 else old
        new = new - 65536 if new >= 32768 else new
        if not 0 < abs(old) <= 4096 or not 0 < abs(new) <= 4096 or (old < 0) != (new < 0):
            return []
        votes.add((abs(old), abs(new)))
        signs.add(old < 0)
    if len(votes) != 1 or signs != {True, False}:
        return []
    _, stride = next(iter(votes))
    clean = _blank(source)
    if re.search(r'\b(?:packed|pragma|union)\b|__attribute__', clean):
        return []
    types = re.findall(r'\btypedef\s+struct(?:\s+\w+)?\s*\{[^{}]*\}\s*(\w+)\s*;', clean)
    result = []
    for typ in types:
        pointers = sorted(set(re.findall(r'\b' + re.escape(typ) + r'\s*\*\s*(\w+)\b', clean)))
        for name in pointers:
            ident = re.escape(name)
            increments = list(re.finditer(r'(?m)^[ \t]*' + ident + r'[ \t]*\+\+[ \t]*;', clean))
            additions = list(re.finditer(r'(?<![\w.>])\b' + ident + r'\s*\+\s*1\b', clean))
            if not increments or not additions:
                continue
            edits = []
            for match in increments + additions:
                if match in increments and clean[:match.start()].rstrip()[-1:] not in ('{', ';', '}', ')'):
                    continue
                if source[match.start():match.end()] != clean[match.start():match.end()]:
                    continue  # Never discard a comment embedded in the expression.
                expression = f'(({typ} *)((char *){name} + {stride}))'
                value = f'{name} = {expression};' if match in increments else expression
                edits.append((match.start(), match.end(), value))
            if len(edits) != len(increments) + len(additions):
                continue
            proposal = source
            for start, end, value in sorted(edits, reverse=True):
                proposal = proposal[:start] + value + proposal[end:]
            result.append(proposal)
            if len(result) >= 2:
                return list(dict.fromkeys(result))
    return list(dict.fromkeys(result))
