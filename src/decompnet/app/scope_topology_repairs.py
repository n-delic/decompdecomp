"""Move macro-scope boundaries, never the order of actual operations.

Lexical braces are retained when loops merge/unwrap. Control transfers, declarations
escaping a moved boundary, macros and volatile effects are not eligible.
"""
import re

from .bit_predicate_repairs import facts, wrap
from .integer_repair_support import IDENT, edit
from .pattern_repairs import close

FAMILIES = ('operation_scope_depth', 'operation_scope_merge', 'operation_scope_unwrap')


def variants(source, name, header='', arch=None, *, language=None, int_bits=None):
    parsed=facts(source,name,header,arch,language,int_bits)
    if not parsed:return []
    part,types,macros=parsed;results=[]
    # Actual shift/address arithmetic updates only; never a header clause or declaration.
    pattern=rf'(?<=[;{{}}])\s*(?P<dest>{IDENT})\s*=(?!=)(?P<rhs>[^;{{}}]+);'
    for hit in re.finditer(pattern,part.clean[part.opening:part.end]):
        if hit['dest'] not in types:continue
        rhs=hit['rhs'];names=set(re.findall(IDENT,rhs))
        if not names<=types.keys() or not re.search(r'<<|\*|\+',rhs):continue
        if re.search(r'[^\w\s+*<()\-0-9]|\b'+IDENT+r'\s*\(',rhs) or '++' in rhs or '--' in rhs:continue
        start,end=part.opening+hit.start(),part.opening+hit.end()
        for depth in (1,3,5):results.append((FAMILIES[0],edit(source,[(start,end,wrap(source[start:end],depth))])))
        if len(results)>=9:break
    loops=[]
    for hit in re.finditer(r'\bdo\s*{',part.clean[part.opening:part.end]):
        begin=part.opening+hit.start();opening=part.opening+hit.end()-1;end=close(part.clean,opening,'{','}')
        if not end:continue
        latch=re.match(r'\s*while\s*\(\s*0\s*\)\s*;',part.clean[end:])
        if not latch:continue
        body=part.clean[opening+1:end-1]
        if re.search(r'\b(?:break|continue|return|goto|case|default|volatile|asm|__asm__)\b|#|(?<!:):(?!:)',body):continue
        if macros & set(re.findall(IDENT,body)):continue
        loops.append((begin,opening,end,end+latch.end()))
    for begin,opening,end,stop in loops[:4]:
        results.append((FAMILIES[2],edit(source,[(begin,stop,source[opening:end])])))
        right=next((l for l in loops if l[0]>=stop and not part.clean[stop:l[0]].strip()),None)
        if right:
            # Preserve each original block's declaration scope and evaluation order.
            body=source[opening:end]+source[right[1]:right[2]]
            results.append((FAMILIES[1],edit(source,[(begin,right[3],wrap(body,1))])))
    return list(dict.fromkeys(results))[:17]
