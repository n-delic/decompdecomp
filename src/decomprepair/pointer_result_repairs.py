"""Share compatible constructor-result locals only across CFG-disjoint initialized lifetimes."""
import re

from . import local_flow, narrow_store_repairs, pointer_pair_repairs
from .call_lifetime_repairs import block_path
from .integer_repair_support import IDENT, edit, refs

FAMILIES=('pointer_result_reuse',)


def variants(source,name,header=''):
    part,ds,macros=pointer_pair_repairs.context(source,name,header,nested=True)
    if not part:return []
    macros = set(macros) | set(re.findall(r'(?m)^\s*#\s*define\s+('+IDENT+r')\b',header+'\n'+source))
    flow=local_flow.analyze(source,name,declarations=ds)
    if not flow:return []
    eligible=[]
    for d in ds:
        var=d['name']
        if var in macros:continue
        if narrow_store_repairs.opaque_use(part,var,header) or not pointer_pair_repairs.initialized(flow,var):continue
        definitions=[n for n in flow.nodes if var in n.defines and not n.start<=d.start()<n.end]
        if not definitions or not refs(part,d):continue
        # Ordinary direct call results only. No initializer, scalar/pointer cast or implicit read is invented.
        if any(not re.fullmatch(rf'\s*{re.escape(var)}\s*=\s*{IDENT}\s*\([^;{{}}]*\)\s*;',part.clean[n.start:n.end]) for n in definitions):continue
        eligible.append(d)
    result=[]
    for i,left in enumerate(eligible):
        for right in eligible[i+1:]:
            if ''.join(left['type'].split())!=''.join(right['type'].split()) or not local_flow.disjoint(flow,left,right):continue
            a,b=block_path(part,left.start()),block_path(part,right.start())
            keep,remove=(left,right) if b[:len(a)]==a else ((right,left) if a[:len(b)]==b else (None,None))
            if keep is None or keep.start()>remove.start():continue
            # Rebinding must cover every renamed use; a sibling declaration never provides storage.
            scope=block_path(part,keep.start())
            uses=refs(part,remove)
            if any(block_path(part,h.start())[:len(scope)]!=scope for h in uses):continue
            result.append((FAMILIES[0],edit(source,[(remove.start(),remove.end(),'')]+[(h.start(),h.end(),keep['name']) for h in uses])))
            if len(result)>=3:return result
    return result
