"""Bounded hypotheses from verified repair examples; every candidate still needs the ordinary byte judge."""
import re

from ..arch.mips import prepared_call_arguments
from ..forks.csource import _blank, find
from ..m2cfix import _split_top_level
from .pattern_repairs import close

ID = r'[A-Za-z_]\w*'


def variants(source, name, assembly=''):
    span = find(source, name)
    if not span:
        return []
    clean = _blank(source)
    opening = clean.find('{', *span)
    body = clean[opening:span[1]]
    if re.search(r'\b(?:volatile|vu8|vu16|vu32)\b', clean):
        return []
    out = []
    # Exchange a top-level sum in an assignment. Calls/increments and nested assignments are excluded.
    # This is a compiler experiment, not a claim that floating point is associative/commutative.
    for assignment in re.finditer(r'(?<![=!<>])=(?!=)([^;{}]+);', body):
        rhs = assignment[1].strip()
        if re.search(r'\+\+|--|(?<![=!<>])=(?!=)|\b'+ID+r'\s*\(', rhs):
            continue
        while rhs.startswith('(') and close(rhs, 0) == len(rhs):
            rhs = rhs[1:-1].strip()
        depth = 0
        for i, char in enumerate(rhs):
            if char in '([': depth += 1
            elif char in ')]': depth -= 1
            elif char == '+' and depth == 0 and i and rhs[i-1] not in '+eE' and rhs[i+1:i+2] != '+':
                left, right = rhs[:i].strip(), rhs[i+1:].strip()
                if left and right:
                    a, b = opening+assignment.start(1), opening+assignment.end(1)
                    out.append(('arithmetic_operand_order', source[:a]+f' ({right}) + ({left})'+source[b:]))
                break
        if len(out) >= 4:
            break
    # An incoming parameter may have been omitted from a locally declared call. Only try forwarding
    # declared parameters, only with direct target-call/register evidence, and update the local prototype too.
    signature = re.search(r'\b'+re.escape(name)+r'\s*\(([^()]*)\)', clean[span[0]:opening])
    if not signature:
        return out
    declarations = [p.strip() for p in _split_top_level(signature[1])]
    params = [re.search(r'('+ID+r')\s*$', p) for p in declarations]
    if not all(p and p.start(1) > 0 for p in params) or any(p.strip() == 'void' for p in declarations):
        return out
    for call in re.finditer(r'\b('+ID+r')\s*\(', body):
        callee = call[1]
        if callee in {'if','while','for','switch','sizeof'}:
            continue
        prepared = prepared_call_arguments(assembly, callee)
        if not prepared:
            continue
        end = close(body, call.end()-1)
        if not end or len(re.findall(r'\b'+re.escape(callee)+r'\s*\(', body)) != 1:
            continue
        args = [p.strip() for p in _split_top_level(body[call.end():end-1]) if p.strip()]
        prototypes = list(re.finditer(r'\b(?:extern\s+)?(?:void|s32|u32|int)\s+'+re.escape(callee)+r'\s*\(([^;{}]*)\)\s*;', clean[:opening]))
        if len(prototypes) != 1 or not 0 <= len(args) < 4:
            continue
        proto = prototypes[0]
        types = [p.strip() for p in _split_top_level(proto[1]) if p.strip()]
        if types == ['void']:
            types = []
        if len(types) != len(args) or '...' in proto[1]:
            continue
        for count in range(1, min(2, len(params), 4-len(args))+1):
            if prepared < len(args)+count:
                continue
            edits = [(opening+call.end(),opening+end-1, ', '.join([p[1] for p in params[:count]]+args)),
                     (proto.start(1),proto.end(1), ', '.join(declarations[:count]+types))]
            text = source
            for a,b,replacement in sorted(edits, reverse=True): text = text[:a]+replacement+text[b:]
            out.append(('call_forwarded_arguments',text))
        if sum(f == 'call_forwarded_arguments' for f,_ in out) >= 4:
            break
    return out
