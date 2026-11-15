"""Couple typed pointer/index lifetimes with a one-shot compiler loop scope.

The split and scope can be complementary: either edit alone may worsen allocation.
Only a complete, side-effect-free slot block is rewritten; ordinary judging applies.
"""
import re

from decomprepair.integer_repair_support import IDENT, INTEGER, edit, locals_for, view

FAMILY = 'pointer_index_scope'
LIMIT = 2


def variants(source, name, header=''):
    part = view(source, name)
    if not part or '__repair_index_scope' in source or re.search(r'\b(?:volatile|asm|__asm__)\b',part.clean):
        return []
    macros=set(re.findall(r'^\s*#\s*define\s+('+IDENT+r')\b',source+'\n'+header,re.M))
    locals_={d['name']:d['type'] for d in locals_for(part)}
    # The slot is declared, written through once and then leaves scope. No labels,
    # control transfers, calls, updates or escaping address can hide in this grammar.
    pattern=re.compile(rf'{{\s*(?P<type>{INTEGER}|void)\s*(?P<stars>\*\s*(?:\*\s*)*)'
        rf'(?P<slot>{IDENT})\s*=\s*(?P<base>{IDENT}\s*->\s*{IDENT})\s*\+\s*(?P<index>{IDENT})\s*;'
        rf'\s*\*\s*(?P=slot)\s*=\s*(?P<value>(?:\(\s*(?:{INTEGER}|void)\s*\*\s*\)\s*)?{IDENT})\s*;\s*}}')
    result=[]
    for hit in pattern.finditer(part.clean,part.opening+1,part.end-1):
        index=hit['index']
        if hit['slot'] in re.findall(IDENT,hit['base']+' '+index+' '+hit['value']):continue
        if index not in locals_ or macros.intersection(re.findall(IDENT,hit[0])):
            continue
        # Reject a nested shadow whose type differs from the known integer local.
        if len(re.findall(rf'\b{IDENT}\s+\**\s*{re.escape(index)}\s*(?=[=;,\[])',part.body))!=1:
            continue
        base='__repair_index_scope_base';offset='__repair_index_scope_offset'
        if re.search(rf'\b(?:{base}|{offset})\b',source+'\n'+header):continue
        pointer=hit['type']+' '+hit['stars']
        replacement=(f'{{ do {{ {pointer}{base} = {hit["base"]}; '
                     f'{locals_[index]} {offset} = {index}; '
                     f'*({base} + {offset}) = {hit["value"]}; }} while (0); }}')
        result.append((FAMILY,edit(source,[(hit.start(),hit.end(),replacement)])))
        if len(result)==LIMIT:break
    return result
