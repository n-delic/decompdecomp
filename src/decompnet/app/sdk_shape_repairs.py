"""Bounded SDK-style word setters using the candidate's declared record and payload types."""
import re

from ..forks.csource import _blank, find
from .integer_repair_support import IDENT, edit, view


def variants(source, name, header=''):
    """Restore inline setters' typed pointer locals without guessing a new shared layout.

These small locals can affect inlining and stack allocation even when the stores look equivalent.
Only plain word-array stores through one existing record parameter are supported here.
"""
    part = view(source, name)
    if not part or re.search(r'\b(?:volatile|asm|__asm__)\b', part.body):
        return []
    start, _ = find(source, name)
    signature = part.clean[start:part.opening]
    pointer = re.search(rf'\b({IDENT})\s*\*\s*({IDENT})\s*(?=[,)])', signature)
    if not pointer:
        return []
    record, parameter = pointer.groups()
    context = _blank(header+'\n'+source[:start])
    # A real declaration must supply the field; raw inferred offsets are not layout proof.
    declaration = re.search(rf'\btypedef\s+struct\s*(?:{IDENT}\s*)?{{([^{{}}]*)}}\s*{record}\s*;', context)
    if not declaration or re.search(r'\bvolatile\b', declaration[1]):
        return []
    pattern = re.compile(rf'\(\s*\(\s*(u32|unsigned\s+int)\s*\*\s*\)\s*{parameter}\s*->\s*({IDENT})\s*\)\s*\[\s*([01])\s*\]\s*=\s*({IDENT}|[0-9]+)\s*;')
    hits = list(pattern.finditer(part.body))[:2]
    if not hits or len({h[2] for h in hits}) != 1:
        return []
    field = hits[0][2]
    if not re.search(rf'\b(?:void|u32|unsigned\s+int)\s*\*\s*{field}\s*;', declaration[1]):
        return []
    if re.search(rf'^\s*#\s*define\s+(?:{record}|{parameter}|{field})\b', header+'\n'+source, re.M):
        return []
    helpers, changes = [], []
    for i, hit in enumerate(hits):
        helper = f'decomp_word_setter_{i}'
        if re.search(rf'\b{helper}\b', context+'\n'+part.clean):
            return []
        typ, _, index, value = hit.groups()
        helpers.append(f'static __inline void {helper}({record} *object, {typ} value)\n'
                       '{\n '+typ+' *words;\n words=('+typ+'*)object->'+field+';\n words['+index+']=value;\n}\n')
        changes.append((part.opening+1+hit.start(), part.opening+1+hit.end(), f'{helper}({parameter}, {value});'))
    # Keep the requested body and its line number; helpers are candidate-local, never shared-header edits.
    line = source[:start].count('\n')+1
    changes.append((start, start, ''.join(helpers)+f'#line {line} "func.c"\n'))
    return [('sdk_inline_word_setters', edit(source, changes))]
