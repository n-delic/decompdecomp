"""Bounded legacy-GCC allocation hypotheses; the ordinary judge remains authoritative.

One-shot scopes can change loop-weighted pseudo references without adding a runtime
iteration. Only plain scalar updates are wrapped, so control transfers cannot change target.
"""
import re

from .equal_value_flow import macros_safe
from .integer_repair_support import IDENT, edit, locals_for, view

FAMILIES = ('loop_weight_scope', 'equal_value_increment')
LIMIT = 12


def variants(source, name, header=''):
    part = view(source, name)
    if not part or '__repair_loop_weight' in source or '__repair_equal_value' in source or re.search(r'\b(?:volatile|asm|__asm__)\b', part.clean):
        return []
    # Unknown macros can hide control transfers, writes or address escapes.
    declarations = {d['name']:d['type'] for d in locals_for(part)}
    if not macros_safe(source,header,part.body,declarations):return []
    updates = []
    pattern = rf'(?<=[;{{}}])\s*(?:\+\+(?P<pre>{IDENT})|(?P<post>{IDENT})\+\+|(?P<add>{IDENT})\s*\+=\s*(?P<rhs>{IDENT}|1)|(?P<self>{IDENT})\s*=\s*(?P=self)\s*\+\s*(?P<value>{IDENT}|1))\s*;'
    for hit in re.compile(pattern).finditer(part.clean,part.opening,part.end-1):
        variable = hit['pre'] or hit['post'] or hit['add'] or hit['self']
        if variable not in declarations:
            continue
        updates.append(hit)
    out = []
    for hit in updates[-3:]:
        groups = [(hit.start(),hit.end())]
        previous = next((p for p in updates if p.end()==hit.start()),None)
        if previous:
            groups.append((previous.start(),hit.end()))
        for start,end in groups:
            statement = source[start:end]
            for depth in (1,3):
                wrapper = '/*__repair_loop_weight*/'+'do {'*depth+statement+'} while (0);'*depth
                out.append((FAMILIES[0],edit(source,[(start,end,wrapper)])))
    # A straight-line equal-value copy followed by increment: keep its earlier
    # pseudo live. No call, branch, intervening assignment or narrowing allowed.
    pattern = rf'(?<=[;{{}}])\s*({IDENT})\s*=\s*({IDENT})\s*;\s*(?:\+\+\s*\2|\2\s*\+\+|\2\s*\+=\s*1)\s*;'
    for hit in re.compile(pattern).finditer(part.clean,part.opening,part.end-1):
        earlier,current = hit[1],hit[2]
        if earlier==current or earlier not in declarations or declarations.get(current)!=declarations[earlier]:
            continue
        replacement = f'/*__repair_equal_value*/\n{earlier} = {current}; {current} = {earlier} + 1;'
        out.insert(0,(FAMILIES[1],edit(source,[(hit.start(),hit.end(),replacement)])))
    return list(dict.fromkeys(out))[:LIMIT]
