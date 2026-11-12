"""Separate declaration order from evaluation order for adjacent scalar literal initializers."""
import re

from . import integer_abi
from .integer_repair_support import IDENT, INTEGER, NUMBER, edit, refs, view

LIMIT = 4
DECL = re.compile(r'\b(?P<type>'+INTEGER+r')\s+(?P<name>'+IDENT+r')\s*=\s*(?P<value>'+NUMBER+r')\s*;')


def staged(source,name,header='',arch=''):
    part=view(source,name)
    if not part or re.search(r'\b(?:__LINE__|__COUNTER__)\b',header+'\n'+source):return []
    declarations=list(DECL.finditer(part.clean,part.opening+1,part.end-1));result=[]
    for first,second in zip(declarations,declarations[1:]):
        if part.clean[first.end():second.start()].strip():continue
        if part.clean[:first.start()].rstrip()[-1:] not in ';{}':continue
        if first['name']==second['name']:continue
        if any(integer_abi.scalar(d['type'],header+'\n'+source,arch) not in {(32,True),(32,False)} for d in (first,second)):continue
        # Hoist only the declaration, never the later initializer. No intervening evaluation
        # may observe its newly extended scope; both existing initializers remain in order.
        if any(re.search(r'(?<!&)&(?!&)\s*\(*\s*'+d['name']+r'\b',part.body) for d in (first,second)):continue
        if any(len(re.findall(r'\b'+INTEGER+r'\s+\**\s*'+d['name']+r'\s*(?=[;=,\[])',part.body))!=1 for d in (first,second)):continue
        if any(re.search(r'^\s*#\s*define[^\n]*\b'+d['name']+r'\b',header+'\n'+source,re.M) for d in (first,second)):continue
        result.append(edit(source,[(first.start(),first.start(),second['type']+' '+second['name']+';\n '),
                                   (second.start(),second.end(),second['name']+' = '+second['value']+';')]))
        if len(result)>=LIMIT:break
    return result


def inlined(source,name,header='',arch=''):
    """Inline a proven unchanged literal local, retaining its declared scalar type and source lines."""
    part=view(source,name)
    if not part:return []
    result=[]
    for decl in DECL.finditer(part.clean,part.opening+1,part.end-1):
        variable=decl['name']
        if part.clean[:decl.start()].rstrip()[-1:] not in ';{}':continue
        if integer_abi.scalar(decl['type'],header+'\n'+source,arch) not in {(32,True),(32,False)}:continue
        if len(re.findall(r'\b'+INTEGER+r'\s+\**\s*'+variable+r'\s*(?=[;=,\[])',part.body))!=1:continue
        if re.search(r'^\s*#\s*define[^\n]*\b'+variable+r'\b',header+'\n'+source,re.M):continue
        uses=refs(part,decl)
        if not uses or len(uses)>8 or any(u.start()<decl.end() for u in uses):continue
        if re.search(r'(?<!&)&(?!&)\s*\(*\s*'+variable+r'\b|\b'+variable+r'\s*\)*\s*(?:\+\+|--|(?:<<|>>|[+*/%&|^\-])?=(?!=))|(?:\+\+|--)\s*\(*\s*\b'+variable+r'\b',part.clean[decl.end():part.end-1]):continue
        replacement='(('+decl['type']+')('+decl['value']+'))'
        # Keep physical newlines: removing a declaration must not change __LINE__ elsewhere.
        blank=re.sub(r'[^\n]',' ',source[decl.start():decl.end()])
        result.append(edit(source,[(decl.start(),decl.end(),blank)]+[(u.start(),u.end(),replacement) for u in uses]))
        if len(result)>=LIMIT:break
    return result


def variants(source,name,header='',arch=''):
    return (inlined(source,name,header,arch)+staged(source,name,header,arch))[:LIMIT]
