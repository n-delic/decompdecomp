"""Offer unescaped local float-array storage only with an actual target call-delay store.

This is an explicit source hypothesis, never a general removal of volatile or a change to MMIO.
Only the ordinary exact judge may accept it. Unknown/aliased/qualified storage is left alone.
"""
import re

from .. import arch as architectures
from .integer_repair_support import IDENT, edit, view

FAMILY = 'plain_float_slots'


def variants(source,name,header='',arch=None,asm=''):
    reader=architectures.get(arch).source_stack_call_store
    part=view(source,name)
    if not part or not reader or not reader(asm):return []
    if re.search(r'\b(?:asm|__asm__|goto|union|struct)\b',part.body):return []
    if re.search(r'\bvolatile\s+[^;{}]*\*',part.body):return []
    decls=list(re.finditer(r'\bvolatile\s+(float|f32)\s+('+IDENT+r')\s*;',part.clean[part.opening+1:part.end-1]))
    if not 2<=len(decls)<=4 or len({d[1] for d in decls})!=1:return []
    typ=decls[0][1]
    if typ!='float' and re.findall(r'\btypedef\s+([^;{}]+)\s+f32\s*;',header+source)!=['float']:return []
    fresh='__repair_float_slots'
    if fresh in source+header:return []
    macros=set(re.findall(r'^\s*#\s*define\s+('+IDENT+')',header+'\n'+source,re.M))
    if macros&set(re.findall(IDENT,part.body)):return []
    changes=[]
    for index,d in enumerate(decls):
        start=part.opening+1+d.start();end=part.opening+1+d.end();name=d[2]
        prefix=part.clean[part.opening+1:start]
        if prefix.count('{')!=prefix.count('}') or prefix.rstrip()[-1:] not in {'',';','{','}'}:return []
        if len(re.findall(r'\b'+re.escape(name)+r'\b',part.body))<2:return []
        if re.search(r'(?<!&)&(?!&)\s*\(*\s*'+re.escape(name)+r'\b',part.body):return []
        if len(re.findall(r'\b(?:'+IDENT+r')(?:\s+|\s*\*+\s*)'+re.escape(name)+r'\s*(?=[;=,\[])',part.body))!=1:return []
        if re.search(r'^\s*#\s*define\s+'+re.escape(name)+r'\b',header+source,re.M):return []
        changes.append((start,end,typ+' '+fresh+'['+str(len(decls))+'];' if index==0 else ''))
        for hit in re.finditer(r'\b'+re.escape(name)+r'\b',part.clean):
            if part.opening<hit.start()<part.end and not start<=hit.start()<end:
                if re.search(r'(?:\.|->)\s*$',part.clean[:hit.start()]):return []
                changes.append((hit.start(),hit.end(),fresh+'['+str(index)+']'))
    return [(FAMILY,edit(source,changes))]
