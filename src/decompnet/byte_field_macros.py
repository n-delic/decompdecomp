"""Expose simple byte-field accessors to the native parser without implementing cpp.

Only exact two-argument dereference templates in unconditional, unique definitions
are supported. Unknown preprocessing and multiline invocations stay untouched.
"""
import re
from collections import Counter

from .forks.csource import _blank

_IDENT = r'[A-Za-z_]\w*'
_DEFINE = re.compile(r'^[^\S\n]*#[^\S\n]*define[^\S\n]+('+_IDENT+r')\(([^()\n]*)\)[^\S\n]+([^\n]+)', re.M)
_TYPE = rf'(?:{_IDENT}(?:\s+{_IDENT})*)(?:\s*\*)*'
_BODY = re.compile(rf'^\s*\(\s*\*\s*\(\s*(?P<type>{_TYPE})\s*\*\s*\)\s*'
                   r'\(\s*\(\s*(?P<byte>char|s8|u8|unsigned\s+char)\s*\*\s*\)\s*'
                   r'\(\s*BASE\s*\)\s*\+\s*\(\s*OFFSET\s*\)\s*\)\s*\)\s*$')


def expand(source, header=''):
    """Expand visible byte-field lvalues, keeping directives, physical lines and evaluation counts."""
    if len(source)+len(header) > 2_000_000:
        return source
    combined = header+'\n'+source
    clean = _blank(combined)
    if (re.search(r'\\(?:\r\n?|\n)|\?\?|%:', combined)
            or re.search(r'\b(?:__LINE__|__FILE__|__COUNTER__|__BASE_FILE__|__INCLUDE_LEVEL__|__builtin_(?:LINE|COLUMN|FILE|source_location)|_Pragma|__pragma)\b', clean)
            or any(word != 'define' for word in re.findall(r'^[^\S\n]*#[^\S\n]*(\w+)', clean, re.M))):
        return source
    # Accessors inside stringified/pasted arguments must not be expanded early.
    if any('#' in line.split('#', 1)[1] for line in clean.splitlines()
           if re.match(r'\s*#\s*define\b', line)):
        return source
    names = re.findall(r'^[^\S\n]*#[^\S\n]*define[^\S\n]+('+_IDENT+r')\b', clean, re.M)
    counts = Counter(names)
    macro_names = set(names)
    definitions = {}
    for hit in _DEFINE.finditer(clean):
        name, parameters, body = hit.groups()
        args = [p.strip() for p in parameters.split(',')]
        if (counts[name] != 1 or len(args) != 2 or len(set(args)) != 2
                or not all(re.fullmatch(_IDENT, arg) for arg in args)):
            continue
        params = dict(zip(args, ('BASE', 'OFFSET')))
        normalized = re.sub(_IDENT, lambda m, params=params: params.get(m[0], m[0]), body)
        field = _BODY.fullmatch(normalized)
        if not field or set(re.findall(_IDENT, field['type']+' '+field['byte'])) & (macro_names | {'BASE', 'OFFSET'}):
            continue
        definitions[name] = (field['type'].strip(), field['byte'], hit.start() < len(header)+1)
    if not definitions:
        return source
    calls = re.compile(r'\b('+'|'.join(map(re.escape, definitions))+r')\s*\(')
    for _ in range(512):
        clean = _blank(source)
        # Source definitions can move when earlier calls expand. Recompute their positions.
        starts = {hit[1]: hit.end() for hit in _DEFINE.finditer(clean)}
        clean = re.sub(r'^[^\S\n]*#[^\n]*', lambda m: ' '*len(m[0]), clean, flags=re.M)
        changed = False
        for hit in reversed(list(calls.finditer(clean))):
            ctype, byte, in_header = definitions[hit[1]]
            if not in_header and hit.start() < starts.get(hit[1], len(source)):
                continue
            stop, parts = _arguments(clean, hit.end())
            if stop is None or '\n' in source[hit.start():stop] or '\r' in source[hit.start():stop]:
                continue
            base, offset = (source[a:b].strip() for a, b in parts)
            if not base or not offset:
                continue
            plain = f'(*({ctype} *)(({byte} *)({base}) + ({offset})))'
            source = source[:hit.start()]+plain+source[stop:]
            changed = True
            break
        if not changed:
            break
    return source


def _arguments(clean, start):
    """cpp separates arguments at parenthesis depth zero, even inside [] or {}."""
    depth, first, parts = 0, start, []
    for pos in range(start, len(clean)):
        char = clean[pos]
        if char == '(':
            depth += 1
        elif char == ')':
            if depth == 0:
                parts.append((first, pos))
                return (pos+1, parts) if len(parts) == 2 else (None, [])
            depth -= 1
        elif char == ',' and depth == 0:
            parts.append((first, pos))
            first = pos+1
    return None, []
