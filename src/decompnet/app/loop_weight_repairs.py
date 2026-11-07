"""Bounded legacy-GCC allocation hypotheses; the ordinary judge remains authoritative.

One-shot scopes can change loop-weighted pseudo references without adding a runtime
iteration. Only complete scalar updates, pointer loads or quotient assignments are wrapped,
so control transfers cannot change target.
"""
import re

from .equal_value_flow import macros_safe
from .integer_repair_support import IDENT, INTEGER, edit, locals_for, view

FAMILIES = ('loop_weight_scope', 'equal_value_increment')
UPDATE_LIMIT = 12
LIMIT = 18
QUOTIENT_LIMIT = 6
QUOTIENT_UPDATE_LIMIT = 8
QUOTIENT_LOAD_LIMIT = 4


def variants(source, name, header=''):
    part = view(source, name)
    if not part or '__repair_loop_weight' in source or '__repair_equal_value' in source or re.search(r'\b(?:volatile|asm|__asm__)\b', part.clean):
        return []
    # Unknown macros can hide control transfers, writes or address escapes.
    declarations = {d['name']:d['type'] for d in locals_for(part)}
    load_declarations = set(declarations)
    for decl in re.finditer(rf'(?<=[;{{}}])\s*{INTEGER}\s+(?P<name>{IDENT})\s*=',part.clean[part.opening:part.end]):
        load_declarations.add(decl['name'])
    if not macros_safe(source,header,part.body,load_declarations):return []
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
    # A load into a real scalar can change reference weights independently of
    # the following pointer chain. Keep it isolated: no calls, macros, labels,
    # indexing, compound expressions or declarations inside the new scope.
    loads = []
    cast = rf'(?:\(\s*(?:{IDENT}\s+)*{IDENT}\s*\*\s*\)\s*)?'
    pattern = rf'(?<=[;{{}}])\s*(?P<dest>{IDENT})\s*=\s*\*\s*{cast}(?P<pointer>{IDENT})\s*;'
    for hit in re.compile(pattern).finditer(part.clean,part.opening,part.end-1):
        prefix = part.clean[part.opening:hit.start()]
        if hit['dest'] not in load_declarations or prefix.count('(')!=prefix.count(')') or prefix.count('[')!=prefix.count(']'):
            continue
        for depth in (1,2,3):
            wrapper = '/*__repair_loop_weight*/'+'do {'*depth+source[hit.start():hit.end()]+'} while (0);'*depth
            loads.append((FAMILIES[0],edit(source,[(hit.start(),hit.end(),wrapper)])))
        if len(loads) >= 6:
            break
    divisions = quotient_scopes(source,part,load_declarations)
    if divisions:
        # Reserve room for the new operation even in update-heavy functions.
        return (divisions[:QUOTIENT_LIMIT]+list(dict.fromkeys(out))[:QUOTIENT_UPDATE_LIMIT]
                +list(dict.fromkeys(loads))[:QUOTIENT_LOAD_LIMIT])
    return list(dict.fromkeys(out))[:UPDATE_LIMIT]+list(dict.fromkeys(loads))[:LIMIT-UPDATE_LIMIT]


def quotient_scopes(source, part, declarations):
    """Wrap an actual quotient statement, retaining every operand and its evaluation.

    A one-shot scope can change legacy GCC's loop-weighted reference counts. It
    must not capture a loop-header clause, an unbraced body or hidden control.
    """
    if re.search(r'\b(?:goto|__attribute__|__declspec|typeof|__typeof__|__extension__)\b|\(\s*{',part.body):
        return []
    assignments=[]
    pattern=rf'(?<=[;{{}}])\s*(?P<dest>{IDENT})\s*=(?!=)(?P<rhs>[^;{{}}]+);'
    for hit in re.compile(pattern).finditer(part.clean,part.opening,part.end-1):
        rhs=hit['rhs']
        if hit['dest'] not in declarations or '/' not in rhs:
            continue
        # No calls, updates, secondary assignments, labels or extensions. Casts,
        # ordinary arithmetic and array/member reads retain their original text.
        if (re.search(r'[=?:,#\\]|\+\+|--|\b'+IDENT+r'\s*\(',rhs)
                or re.search(r'[^\w\s()+*/%<>&|^~!\[\].-]',rhs)):
            continue
        prefix=part.clean[part.opening:hit.start()]
        if prefix.count('(')!=prefix.count(')') or prefix.count('[')!=prefix.count(']'):
            continue
        if rhs.count('(')!=rhs.count(')') or rhs.count('[')!=rhs.count(']'):
            continue
        assignments.append(hit)
    # First and last eligible sites give diversity without an unbounded scan's
    # worth of candidates crowding out the existing update and load hypotheses.
    selected=assignments[:1]+assignments[-1:] if len(assignments)>1 else assignments
    result=[]
    for hit in selected:
        for depth in (1,2,3):
            statement=source[hit.start('dest'):hit.end()]
            leading=source[hit.start():hit.start('dest')]
            if '\n' in leading and not leading.strip():
                indent=leading.rsplit('\n',1)[1]
                wrapper=leading+'/*__repair_loop_weight*/'+'do {\n'+indent
                wrapper+=('do {\n'+indent)*(depth-1)+statement+('\n'+indent+'} while (0);')*depth
            else:
                wrapper='/*__repair_loop_weight*/'+'do {'*depth+source[hit.start():hit.end()]+'} while (0);'*depth
            result.append((FAMILIES[0],edit(source,[(hit.start(),hit.end(),wrapper)])))
    return result
