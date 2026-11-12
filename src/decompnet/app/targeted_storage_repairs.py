"""Compiler-mapped local storage/lifetime alternatives; never prescribe physical registers/slots."""
import re

from decomprepair import integer_abi, repair_localisation
from decomprepair.call_lifetime_repairs import block_path
from decomprepair.integer_repair_support import IDENT, edit
from decomprepair.repair_source_view import read

from ..arch import get

FAMILIES=('mapped_spill_storage','mapped_register_lifetime')
LIMIT=10
DECL=re.compile(r'(?m)^[ \t]*(?P<type>(?:(?:unsigned|signed)\s+)?'+IDENT+r'(?:\s+int)?[ \t]*\*?)[ \t]*(?P<name>'+IDENT+r')[ \t]*(?P<init>=[^;{}]+)?;[ \t]*$')


def variants(source,name,header='',arch=None,rows=(),locations=None):
    reader=get(arch).source_local_storage if arch else None
    witness=reader(rows) if reader else None
    relevant=repair_localisation.lines(source,locations)
    part=read(source,name)
    if not witness or not relevant or not part:return []
    if re.search(r'\b(?:volatile|asm|__asm__|setjmp|longjmp)\b',part.body):return []
    # Moving a declaration must not change line-dependent C or macro expansion.
    if re.search(r'\b(?:__LINE__|__COUNTER__|__FILE__)\b',source+'\n'+header):return []
    macros=set(re.findall(r'^\s*#\s*define\s+('+IDENT+')',source+'\n'+header,re.M))
    declarations=[d for d in DECL.finditer(part.clean) if part.opening<d.start()<part.end and block_path(part,d.start())==[part.opening]]
    # Only the initial declaration run may move. Never cross an evaluated statement.
    initial=[];cursor=part.opening+1
    for d in declarations:
        if part.clean[cursor:d.start()].strip() or d['init']:break
        if source[d.start():d.end()].strip()!=part.clean[d.start():d.end()].strip():break
        initial.append(d);cursor=d.end()
    mapped=set()
    for line in relevant:
        text=source.splitlines()[line-1]
        hit=re.match(r'\s*('+IDENT+r')\s*=(?!=)',text)
        if hit:mapped.add(hit[1])
    result=[]
    for d in (initial if witness['kind']=='spill' else declarations):
        var=d['name'];type_=d['type'].strip()
        if var not in mapped or var in macros:continue
        pointer=type_.endswith('*')
        if not pointer and integer_abi.scalar(type_,header+'\n'+source,arch) not in {(32,True),(32,False)}:continue
        if len(re.findall(r'\b'+re.escape(type_.rstrip('*').strip())+r'\s*\*?\s*'+re.escape(var)+r'\b',part.body))!=1:continue
        if re.search(r'(?<!&)&(?!&)\s*\(*\s*'+re.escape(var)+r'\b',part.body):continue
        definitions=re.findall(r'(?m)^[ \t]*#(?:[^\n\\]|\\[^\n]|\\\n)*',source+'\n'+header)
        if any(re.search(r'\b'+re.escape(var)+r'\b',text) for text in definitions):continue
        family=FAMILIES[0] if witness['kind']=='spill' else FAMILIES[1]
        index=initial.index(d) if d in initial else -1
        # Move only this declaration; balanced nearby placements precede distant ones.
        positions=sorted((i for i in range(len(initial)) if i!=index and not d['init']),key=lambda i:(abs(i-index),i))[:6]
        for i in positions:
            at=initial[i].start()
            result.append((family,edit(source,[(d.start(),d.end(),''),(at,at,source[d.start():d.end()]+'\n')])))
        if witness['kind']=='spill':
            fresh='__repair_spill_slot'
            while re.search(r'\b'+fresh+r'\b',source+'\n'+header):fresh+='_'
            # A one-element local array exposes addressable storage without claiming a
            # particular offset. All value uses move with it; aliases/shadows were rejected.
            edits=[(d.start(),d.end(),type_+' '+fresh+'[1];')]
            edits += [(m.start(),m.end(),fresh+'[0]') for m in re.finditer(r'\b'+re.escape(var)+r'\b',part.clean)
                      if d.end()<=m.start()<part.end and not re.search(r'(?:\.|->|\bgoto)\s*$',part.clean[:m.start()]) and not re.match(r'\s*:(?!:)',part.clean[m.end():])]
            result.append((family,edit(source,edits)))
        else:
            # Keep the mapped assignment's conversion in a short lexical lifetime.
            # No RHS duplication or new pointer/type inference is required.
            fresh='__repair_chain_value'
            while re.search(r'\b'+fresh+r'\b',source+'\n'+header):fresh+='_'
            for line in relevant:
                start=sum(len(s) for s in source.splitlines(keepends=True)[:line-1])
                hit=re.match(r'[ \t]*'+re.escape(var)+r'\s*=(?!=)\s*([^;{}]+);',part.clean[start:])
                if not hit or macros & set(re.findall(IDENT,hit[1])):continue
                rhs=source[start+hit.start(1):start+hit.end(1)]
                text='{ '+type_+' '+fresh+';\n'+fresh+' = '+rhs+';\n'+var+' = '+fresh+';\n}'
                result.insert(0,(family,edit(source,[(start,start+hit.end(),text)])))
    return list(dict.fromkeys(result))[:LIMIT]
