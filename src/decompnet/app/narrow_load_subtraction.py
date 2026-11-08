"""Combine two halfword reads across pure local address calculations.

Narrowing A before subtracting B and narrowing A-B have the same 16-bit result.
Keep both reads, their order and the final assignment; never cross a memory effect.
"""
import re

from . import integer_abi, pointer_pair_repairs
from .call_lifetime_repairs import block_path
from .integer_repair_support import IDENT, edit, split_plain_declarations, view

FAMILY = 'narrow_load_subtraction'
FAMILIES = (FAMILY,)
DECL = re.compile(rf'\b(?P<type>(?:(?:unsigned|signed|const|volatile)\s+)?{IDENT}(?:\s*\*+\s*|\s+))(?P<name>{IDENT})\s*;')


def variants(source, name, header='', arch=None):
    source=split_plain_declarations(source,name,header)
    part = view(source,name)
    if not part or arch not in integer_abi.ARCHES:return []
    context=header+'\n'+source
    if re.search(r'\b(?:volatile|_Atomic|asm|__asm__|goto|setjmp|longjmp)\b',part.clean):return []
    qualified=pointer_pair_repairs.qualified_types(context.replace('_Atomic','volatile'))
    active=part.clean[max(part.clean.rfind(c,0,part.opening) for c in ';{}')+1:part.end]
    if qualified & set(re.findall(IDENT,active)):return []
    declarations=list(DECL.finditer(part.clean,part.opening+1,part.end))
    macros=set(re.findall(r'^\s*#\s*define\s+('+IDENT+')',context,re.M))
    macro_text='\n'.join(line for line in context.splitlines() if line.lstrip().startswith('#'))
    def hidden(variable):
        return re.search(r'\b'+re.escape(variable)+r'\b',macro_text)
    def binding(variable, at):
        path=block_path(part,at)
        matches=[d for d in declarations if d['name']==variable and d.start()<at
                 and path[:len(block_path(part,d.start()))]==block_path(part,d.start())]
        return max(matches,key=lambda d:(len(block_path(part,d.start())),d.start())) if matches else None
    def escaped(variable):
        return re.search(r'(?<!&)&(?!&)\s*\(*\s*'+re.escape(variable)+r'\b',part.body)
    load=rf'\*\s*(?P<pointer>{IDENT})'
    results=[]
    for first in re.compile(rf'\b(?P<dest>{IDENT})\s*=\s*{load}\s*;').finditer(part.clean,part.opening+1,part.end):
        if part.clean[part.opening+1:first.start()].rstrip()[-1:] not in ('',';','{','}'):continue
        dest,left=first['dest'],first['pointer']
        d= binding(dest,first.start());a=binding(left,first.start())
        if not d or not a or integer_abi.scalar(d['type'],context,arch)!=(16,True):continue
        if not a['type'].strip().endswith('*') or integer_abi.scalar(a['type'].strip()[:-1],context,arch)!=(16,False):continue
        update=re.search(r'\b'+re.escape(dest)+rf'\s*-=\s*{load}\s*;',part.clean[first.end():min(part.end,first.end()+512)])
        if not update:continue
        end=first.end()+update.end();start=first.end()+update.start();right=update['pointer']
        if part.clean[part.opening+1:start].rstrip()[-1:] not in ('',';','{','}'):continue
        b=binding(right,start)
        if not b or not b['type'].strip().endswith('*') or integer_abi.scalar(b['type'].strip()[:-1],context,arch)!=(16,False):continue
        if block_path(part,first.start())!=block_path(part,start):continue
        if any(escaped(v) or hidden(v) for v in (dest,left,right)) or {dest,left,right}&macros:continue
        gap=part.clean[first.end():start]
        safe=True
        for statement in gap.split(';'):
            if not statement.strip():continue
            assignment=re.fullmatch(rf'\s*({IDENT})\s*=(?!=)\s*(.+)',statement,re.S)
            if not assignment:safe=False;break
            lhs,rhs=assignment.groups()
            local=binding(lhs,start)
            if lhs in {dest,left} or not local or escaped(lhs) or hidden(lhs):safe=False;break
            if re.search(r'\b'+re.escape(dest)+r'\b',rhs):safe=False;break
            # Strip ordinary scalar/pointer casts, not parenthesized values. Remaining
            # names must be locals or parameters; unknown operands could be volatile.
            plain=re.sub(r'\(\s*(?:[su](?:8|16|32)|unsigned\s+(?:char|short|int)|char|short|int)\s*\*?\s*\)','',rhs)
            if (re.search(r'[=;{}?:\[\].]|->|\+\+|--|\*\s*(?:[A-Za-z_]|\()|(?<!&)&(?!&)\s*(?:[A-Za-z_]|\()|\b'+IDENT+r'\s*\(',plain)
                    or set(re.findall(IDENT,plain))&macros):safe=False;break
            for token in re.findall(IDENT,plain):
                if binding(token,first.start()):continue
                signature=part.clean[:part.opening].rsplit('(',1)[-1]
                if not re.search(r'\b'+re.escape(token)+r'\s*(?:,|\))',signature):safe=False;break
            if not safe:break
        if safe:
            results.append((FAMILY,edit(source,[(first.start(),first.end(),''),(start,end,f'{dest} = *{left} - *{right};')])))
        if len(results)==4:break
    return results
