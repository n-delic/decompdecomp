"""Stage genuinely consumed integer-one operands inside a proven conditional phase."""
import re

from . import local_flow
from .allocator_call_guards import dominators
from .call_lifetime_repairs import block_path
from .integer_repair_support import IDENT, edit

FAMILY='branch_integer_constant'
FAMILIES=(FAMILY,)
LIMIT=6


def variants(source,name,header='',*,language=None,int_bits=None):
    if language!='c' or int_bits!=32:return []
    if re.search(r'\b(?:__LINE__|__COUNTER__|__FILE__|typeof|_Generic)\b|\\\n',source+'\n'+header):return []
    flow=local_flow.analyze(source,name)
    if not flow:return []
    part=flow.part;dom=dominators(flow)
    from .equal_value_flow import macros_safe
    if not macros_safe(source,header,part.body,{d['name']:d['type'] for d in flow.locals}):return []
    macros=set(re.findall(r'(?m)^\s*#\s*define\s+('+IDENT+r')',source+'\n'+header))
    if 'int' in macros:return []
    uses=[]
    for index,node in enumerate(flow.nodes):
        text=part.clean[node.start:node.end]
        if re.search(r'\b(?:sizeof|_Alignof|_Static_assert)\b',text):continue
        if not text.rstrip().endswith(';') or set(re.findall(IDENT,text)) & macros:continue
        for hit in re.finditer(r'(?<![\w.])1(?![\w.])',text):
            left=text[:hit.start()].rstrip();right=text[hit.end():].lstrip()
            # A number must be a whole argument or a plain member-store RHS,
            # never an array bound, index, condition, mask or constant expression.
            argument=left[-1:] in {'(',','} and right[:1] in {')',','}
            member=bool(re.fullmatch(r'\s*'+IDENT+r'\s*->\s*'+IDENT+r'\s*=\s*',left) and right==';')
            if argument or member:uses.append((node.start+hit.start(),node.start+hit.end(),index))
    groups={}
    for use in uses:
        for scope in block_path(part,use[0])[1:]:
            if re.search(r'(?:if\s*\([^{};]*\)|else)\s*$',part.clean[max(part.opening,scope-256):scope]):
                groups.setdefault(scope,[]).append(use)
    fresh='__repair_integer_one'
    while re.search(r'\b'+fresh+r'\b',source+'\n'+header):fresh+='_'
    result=[]
    for scope,hits in sorted(groups.items(),key=lambda item:-len(item[1])):
        if len(hits)<2:continue
        placements=[(i,n) for i,n in enumerate(flow.nodes) if scope<n.start<=min(h[0] for h in hits)
                    and block_path(part,n.start)[-1:]==[scope]
                    and all(i in dom.get(hit[2],set()) for hit in hits)]
        for _,node in sorted(placements,key=lambda item:item[1].start)[:3]:
            changes=[(part.opening+1,part.opening+1,'\n  int '+fresh+';'),
                     (node.start,node.start,fresh+' = 1;\n  ')]
            changes += [(start,end,fresh) for start,end,_ in hits]
            result.append((FAMILY,edit(source,changes)))
            if len(result)>=LIMIT:return list(dict.fromkeys(result))
    return list(dict.fromkeys(result))
