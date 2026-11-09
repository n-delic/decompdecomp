"""Where a local's first value is set: `s32 i = 0;` at the top, or the assignment a few statements later.

m2c puts every initializer in the declaration. The original assigned it after some of the leading
calls, and where the `li` lands decides the register schedule. An agent matched a function by moving
`var_s0 = 0;` below the fifth call. Only plain leading statements are crossed (no control flow), and
never past the first statement that reads the variable.
"""
import re

from .integer_repair_support import edit, view

FAMILY = 'initializer_placement'
FAMILIES = (FAMILY,)
_CONTROL = re.compile(r'\s*(?:if|else|do|while|for|switch|case|default|goto|return|break|continue)\b|\s*\w+\s*:(?!:)')


def _statements(clean, position, end, limit):
    """Ends of the plain statements that follow `position`, up to the first control statement."""
    stops = []
    while len(stops) < limit:
        if _CONTROL.match(clean, position) or clean[position:end].strip().startswith(('{', '}')):
            break
        depth, stop = 0, position
        while stop < end:
            depth += (clean[stop] in '([{') - (clean[stop] in ')]}')
            stop += 1
            if depth == 0 and clean[stop-1] == ';':
                break
        else:
            break
        stops.append(stop)
        position = stop
    return stops


def variants(source, name, limit=6):
    part = view(source, name)
    if not part:
        return []
    clean, base = part.clean, part.opening + 1
    result = []
    for decl in re.finditer(r'(?m)^([ \t]*)((?:unsigned\s+)?(?:s|u)?(?:8|16|32|int|short|char)?\s*\w*?)\b(\w+)\s*=\s*(-?\w+)\s*;[ \t]*\n',
                            clean[base:part.end]):
        # `T name = value;` among the leading declarations, with a bare constant or name as value
        text = decl[0]
        typ = re.match(r'[ \t]*((?:unsigned\s+)?[su]?(?:8|16|32)|int|short|char|unsigned\s+\w+)\s+(\w+)\s*=\s*(-?\w+)\s*;', text)
        if not typ or clean[base:base+decl.start()].count('{') != clean[base:base+decl.start()].count('}'):
            continue
        kind, variable, value = typ.groups()
        start, end = base + decl.start(), base + decl.end()
        if re.fullmatch(r'[A-Za-z_]\w*', value) and re.search(rf'\b{value}\b', clean[start:end].split('=')[0]):
            continue
        indent = decl[1]
        for stop in _statements(clean, end, part.end, 8):
            if re.search(rf'\b{variable}\b', clean[end:stop]):
                break
            moved = edit(source, [(start, end, f'{indent}{kind} {variable};\n')])
            offset = len(moved) - len(source)
            position = stop + offset
            moved = moved[:position] + f'\n{indent}{variable} = {value};' + moved[position:]
            result.append((FAMILY, moved))
            if len(result) >= limit:
                return result
    return result
