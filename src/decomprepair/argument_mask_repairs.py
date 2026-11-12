"""Capture a stable scalar parameter's mask before a single backward label.

Only a local parameter is moved, never a memory load. The original entry guard
and backedge remain intact; every proposal still needs the ordinary byte judge.
"""
import re

from .integer_repair_support import IDENT, edit, view

FAMILY = 'loop_argument_mask'


def variants(source, name, header=''):
    part = view(source, name)
    if not part or re.search(r'\b(?:volatile|const|restrict|asm|__asm__|setjmp|longjmp)\b|^\s*#', part.clean, re.M):
        return []
    signature = re.search(r'\b'+re.escape(name)+r'\s*\(([^()]*)\)\s*$', part.clean[:part.opening])
    if not signature:
        return []
    # Explicit 32-bit integer formals only; do not guess promotion or alias widths.
    parameter = re.compile(r'\s*(int|signed int|unsigned int|s32|u32)\s+('+IDENT+r')\s*')
    parameters = [parameter.fullmatch(p) for p in signature[1].split(',')]
    if not all(parameters):
        return []
    labels = list(re.finditer(r'\b('+IDENT+r')\s*:(?!:)', part.body))
    jumps = list(re.finditer(r'\bgoto\s+('+IDENT+r')\s*;', part.body))
    if len(labels) != 1 or len(jumps) != 1 or labels[0][1] != jumps[0][1] or labels[0].end() >= jumps[0].start():
        return []
    label, jump = labels[0], jumps[0]
    if label[1] in {'case', 'default'} or re.search(r'\b(?:case|default)\b', part.body):
        return []
    prefix = part.body[:label.start()].rstrip()
    if prefix and prefix[-1] not in ';{}':
        return []
    fresh = '__repair_argument_mask'
    if re.search(r'\b'+fresh+r'\b', part.clean):
        return []
    out = []
    for formal in parameters:
        typ, arg = formal.groups()
        from .permuter_macro_effects import DEFINE
        from .pointer_pair_repairs import qualified_types
        context = re.sub(r'\\\r?\n', ' ', header)
        if typ in {'s32', 'u32'}:
            expected = r'(?:signed\s+)?int' if typ == 's32' else r'unsigned\s+int'
            aliases = re.findall(r'\btypedef\s+([^;{}]+)\s+'+typ+r'\s*;', context+source[:part.opening])
            if len(aliases) != 1 or not re.fullmatch(expected, aliases[0].strip()):
                continue
        if qualified_types(re.sub(r'\bconst\b', 'volatile', context)) & set(typ.split()):
            continue
        used = set(re.findall(IDENT, part.body)) | set(typ.split()) | {arg}
        # A header macro can write a parameter without spelling that write here.
        # Only literal object macros (including the usual null pointer) are inert.
        literal_macro = r'\s*\(*\s*(?:\(\s*void\s*\*\s*\)\s*)?(?:0[xX][0-9a-fA-F]+|[0-9]+)[uUlL]*\s*\)*\s*'
        if any(m[1] in used and (m[2] is not None or not re.fullmatch(literal_macro, m[3]))
               for m in DEFINE.finditer(context)):
            continue
        # A call can only change an automatic parameter through its address.
        if re.search(r'&\s*(?:\(\s*)*'+re.escape(arg)+r'\b', part.body):
            continue
        if re.search(r'\b'+re.escape(arg)+r'\s*(?:\)\s*)*(?:=(?!=)|[+*/%&|^-]=|<<=|>>=|\+\+|--)|(?:\+\+|--)\s*(?:\(\s*)*\b'+re.escape(arg)+r'\b', part.body):
            continue
        # Reject shadows, including qualified, pointer and initialized declarations.
        if re.search(r'\b'+IDENT+r'\s+(?:\*\s*|\(\s*)*'+re.escape(arg)+r'\s*(?:\)\s*)*(?=[;=,\[])', part.body):
            continue
        mask = re.compile(r'\(\s*'+re.escape(arg)+r'\s*&\s*(0[xX][0-9a-fA-F]+|[0-9]+)\s*\)')
        hits = list(mask.finditer(part.body, label.end(), jump.start()))
        for literal in dict.fromkeys(h[1] for h in hits):
            if literal.startswith('0') and len(literal) > 1 and not literal.lower().startswith('0x'):
                continue
            if not 0 < int(literal, 16 if literal.lower().startswith('0x') else 10) <= 0x7fffffff:
                continue
            selected = [h for h in hits if h[1] == literal]
            offset = part.opening+1
            declaration = 'unsigned int' if typ in {'unsigned int', 'u32'} else 'int'
            changes = [(offset, offset, f'\n{declaration} {fresh};\n'),
                       (offset+label.start(), offset+label.start(), f'{fresh} = {arg} & {literal};\n')]
            changes += [(offset+h.start(), offset+h.end(), fresh) for h in selected]
            out.append((FAMILY, edit(source, changes)))
            if len(out) == 4:
                return out
    return out
