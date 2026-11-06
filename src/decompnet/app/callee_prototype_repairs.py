"""Repair candidate-owned scalar callee prototypes without editing a project's shared declarations.

Wrong short formals can add conversions at every call. Masked results can also reveal a wrongly inferred
return width. Unknown, conditional, variadic and indirect declarations remain with the real compiler.
"""
import re

from ..forks.csource import find
from .integer_repair_support import INTEGER, calls, edit, split_args, view
from .repair_preflight import _PROTO

NARROW = {'s8', 'u8', 's16', 'u16', 'char', 'short', 'signed char', 'unsigned char', 'signed short', 'unsigned short'}


def variants(source, name):
    part = view(source, name)
    if not part or re.search(r'^\s*#', part.clean, re.M):
        return []
    prototypes = list(_PROTO.finditer(part.clean[:part.opening]))
    result = []
    for proto in prototypes:
        callee = proto[1]
        if callee == name or sum(p[1] == callee for p in prototypes) != 1 or find(source, callee):
            continue
        sites = list(calls(part, callee))
        if not sites or not proto[2].strip() or '...' in proto[2]:
            continue
        # A local object/parameter can shadow this function name.
        without_proto = part.clean[:proto.start()]+part.clean[proto.end():]
        if re.search(rf'\b{re.escape(callee)}\b(?!\s*\()', without_proto):
            continue
        params = split_args(proto[2])
        for start, end in params:
            param = proto[2][start:end]
            match = re.fullmatch(rf'\s*({INTEGER})(?:\s+[A-Za-z_]\w*)?\s*', param)
            if not match or ' '.join(match[1].split()) not in NARROW:
                continue
            offset = proto.start(2)+start
            result.append(('callee_parameter_width', edit(source, [(offset+match.start(1), offset+match.end(1), 'int')])))
            if len(result) >= 6:
                break
        # Infer return-width choices only from a captured direct result subsequently masked in this caller.
        returns = re.match(rf'\s*(?:extern\s+)?({INTEGER})\s+{re.escape(callee)}\s*\(', proto[0])
        if returns and ' '.join(returns[1].split()) in {'s32', 'u32', 'int', 'signed int', 'unsigned int'}:
            for start, _ in sites:
                # Call whitespace can vary; locate the assignment ending at the callee token instead.
                assignment = re.search(rf'\b([A-Za-z_]\w*)\s*=\s*{re.escape(callee)}\s*\($', part.clean[part.opening+1:start])
                if not assignment:
                    continue
                variable = re.escape(assignment[1])
                masks = re.findall(rf'\b{variable}\s*&\s*0x(FF|FFFF)\b', part.body, re.I)
                for mask in dict.fromkeys(masks):
                    for typ in (('unsigned char', 'signed char') if len(mask) == 2 else ('unsigned short', 'short')):
                        offset = proto.start()+returns.start(1)
                        result.append(('callee_return_width', edit(source, [(offset, proto.start()+returns.end(1), typ)])))
                if masks:
                    break
        if len(result) >= 10:
            break
    return list(dict.fromkeys(result))[:10]
