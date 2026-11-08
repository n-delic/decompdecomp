"""Small lifetime hypotheses observed in captured matches; every proposal uses the ordinary judge."""
import re

from ..forks.csource import _blank, find
from .pattern_repairs import close

ID = r'[A-Za-z_]\w*'


def variants(source, name):
    span = find(source, name)
    if not span:
        return []
    clean = _blank(source)
    opening = clean.find('{', *span)
    body = clean[opening+1:span[1]-1]
    if re.search(r'\b(?:volatile|asm|__asm__)\b|^\s*#', clean, re.M):
        return []
    out = []
    def emit(family, hit, replacement):
        a,b = opening+1+hit.start(), opening+1+hit.end()
        out.append((family, source[:a]+replacement+source[b:]))
    # A scalar cast is rounded before the bitwise operation. Keep the same assignment destination;
    # no duplicated reads, function calls, increments, or compound lvalues.
    pattern = rf'\b(?P<v>{ID})\s*=\s*(?P<cast>\(\s*(?:s32|u32|int|unsigned int)\s*\)\s*{ID})\s*(?P<op>[|&^])\s*(?P<rhs>(?:\(\s*(?:s32|u32|int|unsigned int)\s*\)\s*)?(?:0x[0-9A-Fa-f]+|\d+))\s*;'
    for hit in list(re.finditer(pattern, body))[:3]:
        emit('cast_bitwise_lifetime', hit, f"{hit['v']} = {hit['cast']};\n{hit['v']} = {hit['v']} {hit['op']} {hit['rhs']};")
    # Store a shifted local and end that local's old lifetime. Require a uniquely declared unsigned
    # scalar and no later references; changing a live value would corrupt subsequent computations.
    for hit in list(re.finditer(rf'(?P<dest>\*\s*\(\s*u(?:8|16|32)\s*\*\s*\)\s*{ID})\s*=\s*(?P<v>{ID})\s*>>\s*(?P<n>\d+)\s*;', body))[:3]:
        v = hit['v']
        declarations = re.findall(r'\b(?:u32|unsigned int)\s+'+re.escape(v)+r'\s*(?:;|=)', body[:hit.start()])
        if re.search(r'\b(?:for|while|do|goto)\b', body) or len(declarations) != 1 or int(hit['n']) > 31 or re.search(r'\b'+re.escape(v)+r'\b', body[hit.end():]) or re.search(r'&\s*'+re.escape(v)+r'\b', clean):
            continue
        emit('shift_store_lifetime', hit, f"{v} >>= {hit['n']};\n{hit['dest']} = {v};")
    # Split the leftmost explicit floating cast before multiplication, preserving factor order.
    # Parenthesized loads are allowed; calls, increments, assignments and volatile code are not.
    for hit in list(re.finditer(rf'\b(?P<v>{ID})\s*=\s*(?P<rhs>[^;{{}}]+);', body))[:20]:
        v = hit['v']
        typ = re.search(r'\b(f32|f64|float|double)\s+'+re.escape(v)+r'\s*;', body[:hit.start()])
        if not typ:
            continue
        rhs = hit['rhs'].strip()
        depth, product = 0, None
        for index, char in enumerate(rhs):
            if char in '([': depth += 1
            elif char in ')]': depth -= 1
            elif char == '*' and depth == 0:
                product = index
                break
        if product is None:
            continue
        left, tail = rhs[:product].strip(), rhs[product:]
        while left.startswith('(') and close(left, 0) == len(left):
            left = left[1:-1].strip()
        cast = re.fullmatch(r'\(\s*(f32|f64|float|double)\s*\)\s*(.+)', left)
        if not cast or cast[1] != typ[1]:
            continue
        operand = cast[2]
        if any(x in operand+tail for x in ('++','--','=', ';','{','}')) or re.search(r'\b'+ID+r'\s*\(', operand+tail):
            continue
        emit('float_cast_lifetime', hit, f"{v} = ({cast[1]}){operand};\n{v} = {v} {tail};")
    return list(dict.fromkeys(out))[:8]
