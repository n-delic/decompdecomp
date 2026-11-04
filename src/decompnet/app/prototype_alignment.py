"""Repair one plain definition's return type to an unambiguous shared declaration.

The header stays untouched. Ambiguous/conditional declarations, old-style parameters and C++ need
human review; the ordinary compiler and exact judge decide whether this candidate may be kept.
"""
import re

from ..forks.csource import _blank, find
from .integer_repair_support import IDENT, INTEGER, edit, split_args
from .repair_preflight import _PROTO


def prepare(header):
    shared = _blank(header)
    prototypes = {}
    conditional = bool(re.search(r'^\s*#\s*(?:if|ifdef|ifndef|include)\b|::', shared, re.M))
    if not conditional:
        for proto in _PROTO.finditer(shared):
            prototypes.setdefault(proto[1], []).append(proto)
    return shared, prototypes, conditional


def variants(source, name, header, prepared=None):
    span = find(source, name)
    if not span:
        return []
    clean = _blank(source)
    shared, prototypes, conditional = prepared if prepared is not None else prepare(header)
    if conditional:
        return []
    protos = prototypes.get(name, [])
    if len(protos) != 1 or not protos[0][2].strip() or '...' in protos[0][2]:
        return []
    proto = protos[0]
    result = re.fullmatch(rf'\s*(?:extern\s+)?({INTEGER}|void)\s*', proto[0][:proto.start(1)-proto.start()])
    own = re.match(rf'\s*(?P<type>{INTEGER}|void)\s+{re.escape(name)}\s*\((?P<args>[^(){{}};]*)\)\s*{{', clean[span[0]:span[1]])
    if not result or not own or result[1] == own['type']:
        return []
    words = set(re.findall(IDENT, result[1]+' '+own['type']+' '+name+' '+proto[2]+' '+own['args']))
    if any(m[1] in words for m in re.finditer(r'^\s*#\s*define\s+('+IDENT+r')\b', shared, re.M)):
        return []

    def types(args):
        if args.strip() == 'void':
            return []
        out = []
        for start, end in split_args(args):
            match = re.fullmatch(rf'\s*({INTEGER})(?:\s+{IDENT})?\s*', args[start:end])
            if not match:
                return None
            out.append(' '.join(match[1].split()))
        return out

    expected, actual = types(proto[2]), types(own['args'])
    if expected is None or expected != actual:
        return []
    return [edit(source, [(span[0]+own.start('type'), span[0]+own.end('type'), result[1])])]
