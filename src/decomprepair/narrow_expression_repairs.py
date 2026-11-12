"""Inline repeated pure narrow scalar expressions, retaining assignment conversion at every use."""
import re

from . import branch_call_support as support
from . import integer_abi
from .call_lifetime_repairs import block_path
from .integer_repair_support import IDENT, edit, locals_for, refs, split_args, view

FAMILY = 'narrow_expression_inline'
LIMIT = 4


def variants(source, name, header='', arch=None):
    part=view(source,name)
    if not part or re.search(support.FORBIDDEN,part.body):return []
    context=header+'\n'+source;declarations=locals_for(part)
    types={d['name']:integer_abi.scalar(d['type'],context,arch) for d in declarations}
    signature=re.search(r'\b'+re.escape(name)+r'\s*\(([^{};]*)\)\s*$',part.clean[:part.opening])
    if signature:
        for a,b in split_args(signature[1]):
            p=re.fullmatch(r'\s*(.+?)\s+('+IDENT+r')\s*',signature[1][a:b])
            if p:
                shadow=re.search(r'\b'+IDENT+r'\s+\**\s*'+re.escape(p[2])+r'\s*(?=[;=,\[])',part.body)
                types[p[2]]=None if shadow else integer_abi.scalar(p[1],context,arch)
    macros=set(re.findall(r'^\s*#\s*define\s+('+IDENT+')',context,re.M))
    result=[]
    for d in declarations:
        bits=types[d['name']]
        if not bits or bits[0] not in (8,16):continue
        hits=refs(part,d)
        if not 3<=len(hits)<=7:continue
        if any(block_path(part,h.start())!=[part.opening] for h in hits):continue
        assigned=hits[0];assignment=re.match(r'\s*=(?!=)\s*([^;{}]+);',part.clean[assigned.end():])
        if not assignment or part.clean[:assigned.start()].rstrip()[-1:] not in ';{}':continue
        rhs=assignment[1].strip();stop=assigned.end()+assignment.end()
        if not support.pure(rhs) or re.search(r'(?:^|[(+\-*/%|^~<>])\s*[*&]|\([^)]*\*\s*\)',rhs):continue
        words=set(re.findall(r'\b'+IDENT+r'\b',rhs))
        # Cast type words are allowed; data operands must be declared integer scalars.
        operands={w for w in words if w in types}
        other=words-operands
        if (d['name'] in words or macros&words or not operands
                or any(types[w] is None for w in operands)
                or any(integer_abi.scalar(w,context,arch) is None and w not in {'unsigned','signed','int','short','char'} for w in other)):
            continue
        span=part.clean[stop:hits[-1].end()]
        if re.search(r'\b(?:if|else|for|while|do|switch|goto|return)\b',span) or macros&set(re.findall(IDENT,span)):continue
        names=operands|{d['name']}
        if any(re.search(r'(?<!&)&(?!&)\s*(?:\(\s*)*'+re.escape(v)+r'\b',part.body) for v in names):continue
        # Any assignment/update to an input or the destination invalidates duplication.
        if any(re.search(r'(?<![\w.>])'+re.escape(v)+r'\s*(?:\+\+|--|(?:<<|>>|[+*/%&|^\-])?=(?!=))|(?:\+\+|--)\s*\b'+re.escape(v)+r'\b',span) for v in names):continue
        # Ambiguous shadowing and pointer/global reads never reach this path.
        replacement='(('+d['type']+')('+rhs+'))'
        changes=[(assigned.start(),stop,'')]+[(h.start(),h.end(),replacement) for h in hits[1:]]
        result.append((FAMILY,edit(source,changes)))
        if len(result)>=LIMIT:break
    return result
