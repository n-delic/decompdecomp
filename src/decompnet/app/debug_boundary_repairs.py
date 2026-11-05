"""Token-preserving statement boundaries for debug-sensitive legacy scheduling."""
import re

from .integer_repair_support import edit, view

FAMILY = 'debug_statement_boundary'
FAMILIES = (FAMILY,)
LIMIT = 12
MAX_BOUNDARIES = 512


def variants(source, name, flags=(), header='', *, offset=0, limit=LIMIT):
    if offset < 0 or (limit is not None and limit < 1):
        raise ValueError('Use a nonnegative offset and positive boundary page size.')
    if re.search(r'\b__LINE__\b|^\s*#\s*(?:line\b|\d)', header+'\n'+source,re.M):
        return []
    debug = [f for f in flags if re.fullmatch(r'-g(?:[0-3]|gdb[0-3]?)?', f)]
    if not debug or debug[-1] in {'-g0','-ggdb0'}:
        return []
    part = view(source, name)
    if not part:
        # Local object aliases are opaque, but their presence must not disable text-only probes.
        masked = re.sub(r'^[ \t]*#.*$', lambda m: ' '*len(m[0]), source, flags=re.M)
        part = view(masked, name)
    if not part:
        return []
    result = []
    boundaries = []
    # A boundary after a semicolon outside parentheses cannot be a for-header separator.
    depth = 0
    for pos in range(part.opening+1, part.end-1):
        char = part.clean[pos]
        depth += (char == '(')-(char == ')')
        if char != ';' or depth:
            continue
        gap = re.match(r'[ \t\r\n]*', source[pos+1:])
        stop = pos+1+gap.end()
        if stop >= part.end-1 or part.clean[stop:stop+1] in {'', '#'}:
            continue
        # Never join across comments, directives or labels by rebuilding token text.
        replacement = ' ' if '\n' in gap[0] else '\n'
        prefix = part.clean[max(part.opening+1, pos-100):pos]
        arithmetic = bool(re.search(r'/|>>|<<', prefix))
        boundaries.append((not arithmetic, pos+1, stop, replacement))
    pool = sorted(boundaries)[:MAX_BOUNDARIES]
    stop_at = len(pool) if limit is None else offset+limit
    for _, start, stop, replacement in pool[offset:stop_at]:
        result.append((FAMILY, edit(source, [(start, stop, replacement)])))
    return result
