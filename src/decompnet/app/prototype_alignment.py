"""Repair one plain definition's return type to an unambiguous shared declaration.

The header stays untouched. Ambiguous/conditional declarations, old-style parameters and C++ need
human review; the ordinary compiler and exact judge decide whether this candidate may be kept.
"""
import hashlib
import re
import threading
from collections import OrderedDict

from decomprepair.integer_repair_support import IDENT, INTEGER, edit, split_args
from decomprepair.repair_preflight import _PROTO

from ..forks.csource import _blank, find

# A Deep Permuter status poll asks about every finished function, nearly all of them under the same few headers;
# parsing one again each time was about a seventh of a busy app's CPU. Both limits bound the memory: an entry holds the
# blanked header (as long as the header) and its prototype matches.
CACHE_ENTRIES = 64
CACHE_CHARACTERS = 16 * 1024 * 1024
_cache = OrderedDict()  # SHA-256 of a header -> what _prepare made of it
_cache_lock = threading.Lock()


def prepare(header):
    """The header blanked, its prototypes by name and whether it is conditional. Shared: callers only read it."""
    key = hashlib.sha256(header.encode('utf-8', 'surrogatepass')).digest()
    with _cache_lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    value = _prepare(header)
    if len(value[0]) <= CACHE_CHARACTERS:
        with _cache_lock:
            _cache[key] = value
            _cache.move_to_end(key)
            while len(_cache) > CACHE_ENTRIES or sum(len(v[0]) for v in _cache.values()) > CACHE_CHARACTERS:
                _cache.popitem(last=False)
    return value


def _prepare(header):
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
