"""Measured line and scalar-conversion lifetime candidates; ordinary judges decide."""
import re

from ..arch import get
from .integer_repair_support import IDENT, edit, locals_for
from .repair_source_view import read

FAMILY='hazard_statement_lines'


def variants(source,name,header='',arch=None,rows=()):
    reader=get(arch).source_hazard_rows if arch else None
    if not reader or not reader(rows):return []
    # Line macros and directives can make whitespace observable. Do not alter their context.
    if re.search(r'\b(?:__LINE__|__FILE__|__COUNTER__)\b',source+'\n'+header):return []
    part=read(source,name)
    if not part or re.search(r'\b(?:volatile|asm|__asm__)\b',part.body):return []
    from .measured_source_repairs import statement_lines
    result=[(FAMILY,s) for _,s,h in statement_lines(source,name,header,rows,measured=True) if h==header]
    # Preserve assignment conversion through a same-type unescaped integer temporary.
    # This changes lifetime/line markers, never the evaluation count or order of the RHS.
    macros=set(re.findall(r'^\s*#\s*define\s+('+IDENT+')',source+'\n'+header,re.M))
    token='__repair_hazard_value'
    while re.search(r'\b'+token+r'\b',source+'\n'+header):token+='_' 
    staged=[]
    for d in locals_for(part):
        if re.search(r'(?<!&)&(?!&)\s*\(*\s*'+re.escape(d["name"])+r'\b',part.body):continue
        for m in re.finditer(r'\b'+re.escape(d['name'])+r'\s*=(?!=)\s*([^;{}]+);',part.clean[d.end():part.end]):
            a=d.end()+m.start();b=d.end()+m.end();rhs=source[d.end()+m.start(1):d.end()+m.end(1)]
            if part.clean[:a].rstrip()[-1:] not in ';{}':continue
            if re.search(r'\b(?:return|goto|if|for|while)\b',rhs) or macros & set(re.findall(IDENT,rhs)):continue
            staged.append((a,(FAMILY,edit(source,[(a,b,'{ '+d['type']+' '+token+';\n'+token+' = '+rhs+';\n'+d['name']+' = '+token+';\n}')]))))
    # Branch tails are a useful first hypothesis; keep a bounded forward sample as well.
    staged=[item for _,item in sorted(staged,reverse=True)]
    return list(dict.fromkeys(staged[:2]+result+staged[2:4]))[:6]
