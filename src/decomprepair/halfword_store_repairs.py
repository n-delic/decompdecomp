"""Explicit narrowing at proven unsigned-halfword stores; no invented objects or value uses."""
import re

from . import integer_abi, local_flow, macro_address_repairs, narrow_store_repairs
from .integer_repair_support import IDENT, edit, locals_for, view

FAMILY='halfword_store_normalization'
FAMILIES=(FAMILY,)
LIMIT=8
HELPER='__repair_store_halfword'


def sites(source,name,header='',arch=None):
    part=view(source,name)
    context=header+'\n'+source
    if not part or arch not in integer_abi.ARCHES or re.search(r'\b(?:volatile|asm|__asm__|restrict|_Atomic|__LINE__|__COUNTER__)\b',source):return []
    if HELPER in context or integer_abi.scalar('int',context,arch)!=(32,True):return []
    if integer_abi.scalar('short',context,arch)!=(16,True):return []
    keywords=set(re.findall(r'(?m)^\s*#\s*define\s+('+IDENT+r')\b',context))
    if keywords & {'int','short','unsigned','signed','static','inline'}:return []
    declarations={d['name']:d for d in locals_for(part)}
    macros=macro_address_repairs.definitions(source,header,part.opening)
    opaque=set(re.findall(r'(?m)^\s*#\s*define\s+('+IDENT+r')\b',context))-macros.keys()
    try:flow=local_flow.analyze(source,name)
    except (ValueError,RecursionError):return []
    if not flow:return []
    result=[]
    for node in flow.nodes:
        text=part.clean[node.start:node.end]
        hit=re.fullmatch(r'\s*(.+?)\s*=(?!=)\s*('+IDENT+r')\s*;\s*',text,re.S)
        if not hit or hit[2] not in declarations:continue
        decl=declarations[hit[2]]
        # Preserve the parameter's int conversion for every admitted input.
        if integer_abi.scalar(decl['type'],context,arch) not in {(16,False),(16,True),(32,True)}:continue
        # Resolve the actual lexical declaration, not a same-named sibling or parameter.
        if narrow_store_repairs.declared(part,hit[2],node.start,header)!=(decl['type'],''):continue
        if re.search(r'(?<!&)&(?!&)\s*\(*\s*'+re.escape(hit[2])+r'\b',part.body):continue
        lhs=hit[1].strip()
        if set(re.findall(IDENT,lhs+' '+hit[2]))&opaque:continue
        typ=narrow_store_repairs.field_type(part,lhs,node.start,header,arch)
        macro=macro_address_repairs.invocation(lhs,macros)
        if macro:
            kind=macros[macro[0]][0]
            if kind.endswith('*') and not kind.endswith('**') and all(re.fullmatch(r'[\w\s()+\-*/&|^~<>]+',a) and not re.search(r'\+\+|--|\b'+IDENT+r'\s*\(',a) for a in macro[1]):
                typ=kind[:-1]
        # Direct cast/dereference stores; qualifiers and compound/effectful addresses decline.
        direct=re.fullmatch(r'\(?\s*\*\s*\(\s*('+IDENT+r'(?:\s+'+IDENT+r')*)\s*\*\s*\)\s*([\w\s()+\-*/&|^~<>]+?)\s*\)?',lhs)
        if direct:typ=direct[1]
        if integer_abi.scalar(typ or '',context,arch)!=(16,False):continue
        if re.search(r'\+\+|--|\b'+IDENT+r'\s*\(',lhs) and not macro:continue
        start=node.start+hit.start(2);end=node.start+hit.end(2)
        result.append(dict(start=start,end=end,value=hit[2],line=source.count('\n',0,start)+1,lhs=lhs[:100]))
    return result[:LIMIT]


def variants(source,name,header='',arch=None):
    points=sites(source,name,header,arch)
    if not points:return []
    from .normalization_repairs import definition_before
    definition=f'static inline short {HELPER}(int x){{x=(unsigned int)x<<16;x=(int)x>>16;return x;}}\n'
    return [(FAMILY,definition_before(edit(source,[(p['start'],p['end'],HELPER+'('+p['value']+')')]),name,definition)) for p in points]
