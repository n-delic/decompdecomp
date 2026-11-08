"""Duplicate a small unsigned arithmetic/store tail across exhaustive, braced selecting arms.

This changes source layout, not execution count. Loop updates remain outside the tail.
Unknown types, shadowing, transfers and opaque macro effects yield no proposal.
"""
import re

from . import integer_abi, narrow_store_repairs
from .call_lifetime_repairs import block_path
from .integer_repair_support import IDENT, edit, view
from .pattern_repairs import close

FAMILIES = ('branch_packing_tail',)
LIMIT = 4


def chain(part, start):
    """Return arm ends only for a complete if / else-if / else chain."""
    arms=[]
    pos=start
    for _ in range(4):
        hit=re.match(r'if\s*\(',part.clean[pos:])
        if not hit:return None
        cond=close(part.clean,pos+hit.end()-1)
        arm=re.match(r'\s*{',part.clean[cond:]) if cond else None
        if not arm:return None
        a=cond+arm.end()-1;b=close(part.clean,a,'{','}')
        if not b:return None
        arms.append((a,b))
        other=re.match(r'\s*else\s*',part.clean[b:])
        if not other:return None
        pos=b+other.end()
        if part.clean[pos:pos+1]=='{':
            end=close(part.clean,pos,'{','}')
            return (arms+[(pos,end)],end) if end else None
        if not re.match(r'if\b',part.clean[pos:]):return None
    return None


def unsigned_local(part, var, pos, header, arch):
    kind=narrow_store_repairs.scalar_type(part,var,pos,header,arch)
    if kind is None:
        # Comma declarations are analyzed without rewriting their physical lines.
        choices=[];path=block_path(part,pos)
        for hit in re.finditer(rf'\b({IDENT}(?:[ \t]+{IDENT})*?)[ \t]+({IDENT}(?:\s*,\s*{IDENT})+)\s*;',part.clean[part.opening+1:pos]):
            at=part.opening+1+hit.start();scope=block_path(part,at)
            if var in re.findall(IDENT,hit[2]) and path[:len(scope)]==scope:
                choices.append((len(scope),integer_abi.scalar(hit[1],header+'\n'+part.source,arch)))
        if choices:
            depth=max(d for d,_ in choices);kinds=[k for d,k in choices if d==depth]
            if len(kinds)==1:kind=kinds[0]
    return kind==(32,False) and not narrow_store_repairs.opaque_use(part,var,header) and not re.search(
        rf'(?<!&)&(?!&)\s*\(*\s*{re.escape(var)}\b',part.body)


def store_type(part, obj, member, pos, header, arch):
    declared=narrow_store_repairs.declared(part,obj,pos,header)
    if not declared:return None
    context=header+'\n'+part.source[:part.opening]
    records=re.findall(r'\btypedef\s+struct(?:\s+'+IDENT+r')?\s*{([^{}]*)}\s*'+re.escape(declared[0])+r'\s*;',context)
    if len(records)!=1 or re.search(r'\b(?:volatile|union|__attribute__)\b|[:#]',records[0]):return None
    members={}
    for declaration in filter(str.strip,records[0].split(';')):
        hit=re.fullmatch(r'\s*('+IDENT+r'(?:[ \t]+'+IDENT+r')*?)[ \t]+(.+)',declaration)
        if not hit or integer_abi.scalar(hit[1],context,arch) is None:return None
        for item in hit[2].split(','):
            field=re.fullmatch(r'\s*('+IDENT+r')\s*(\[\s*\d+\s*\])?\s*',item)
            if not field or field[1] in members:return None
            members[field[1]]=None if field[2] else integer_abi.scalar(hit[1],context,arch)
    return members.get(member)


def variants(source, name, header='', arch=None):
    part=view(source,name)
    if not part or re.search(r'\b(?:volatile|asm|__asm__|setjmp|longjmp|__LINE__)\b',part.body) or '__LINE__' in source+'\n'+header:return []
    macros=set(re.findall(r'(?m)^\s*#\s*define\s+('+IDENT+r')\b',header+'\n'+source))
    result=[]
    for hit in reversed(list(re.finditer(r'\bif\s*\(',part.body))):
        start=part.opening+1+hit.start()
        if re.search(r'\belse\s*$',part.clean[:start]):continue
        found=chain(part,start)
        if not found:continue
        arms,end=found
        bodies='\n'.join(part.clean[a+1:b-1] for a,b in arms)
        if re.search(r'\b(?:goto|return|break|continue|case|default|for|while|do|switch)\b|(?<![?:]):(?!:)',bodies):continue
        cursor=end;scalars=[];changes=[]
        for _ in range(4):
            op=re.match(rf'\s*({IDENT})\s*(\+=|\|=|\^=|&=|=(?!=))\s*({IDENT})(?:\s*<<\s*(\d+))?\s*;',part.clean[cursor:])
            if not op:break
            if op[4] and not 0<=int(op[4])<32:break
            if not all(unsigned_local(part,v,cursor,header,arch) for v in (op[1],op[3])):break
            scalars.extend((op[1],op[3]));cursor+=op.end()
        store=re.match(rf'\s*({IDENT})(->|\.)({IDENT})\s*=(?!=)\s*({IDENT})\s*;',part.clean[cursor:])
        if not store or not unsigned_local(part,store[4],cursor,header,arch):continue
        declared=narrow_store_repairs.declared(part,store[1],cursor,header)
        if not declared or bool(declared[1]) != (store[2]=='->'):continue
        if declared[0] in macros or store_type(part,store[1],store[3],cursor,header,arch) is None:continue
        stop=cursor+store.end();names=set(scalars+[store[1],store[3],store[4]])
        if (set(re.findall(IDENT,part.clean[start:stop])) | names) & macros or narrow_store_repairs.opaque_use(part,store[1],header):continue
        # Declarations in any arm could rebind tail locals or types at its new location.
        if re.search(rf'(?:^|[;{{}}])\s*(?:struct\s+)?{IDENT}(?:\s+\**\s*|\s*\*+\s*){IDENT}\s*(?=[=;,\[])',bodies):continue
        if re.search(rf'\b(?:{"|".join(map(re.escape,names))})\s*:(?!:)',bodies):continue
        if re.search(rf'\b{IDENT}\s*\**\s*\(\s*\**\s*(?:{"|".join(map(re.escape,names))})\s*\)\s*(?=[=;,])',bodies):continue
        text=source[end:stop].strip()
        changes=[(b-1,b-1,'\n'+text+'\n') for _,b in arms]+[(end,stop,'')]
        result.append((FAMILIES[0],edit(source,changes)))
        if len(result)>=LIMIT:break
    return list(dict.fromkeys(result))
