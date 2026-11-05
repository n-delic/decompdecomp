"""Check a missing scalar return before searching allocation; shared prototypes stay untouched."""
import re

from .. import arch as architectures
from ..forks.csource import _blank, find
from . import integer_abi
from .integer_repair_support import IDENT, edit, locals_for, view
from .pattern_repairs import close

FAMILY = 'return_contract'
LIMIT = 4


def witness(asm, arch, caller='', name=''):
    reader=architectures.get(arch).source_return_contract
    return reader(asm,caller,name) if reader else None


def variants(source, name, header='', arch=None, asm=''):
    part=view(source,name);proof=witness(asm,arch)
    if not part or not proof or proof['bits']!=32:return []
    part.end=close(part.clean,part.opening,'{','}') or part.end
    start,_=find(source,name)
    signature=re.fullmatch(r'\s*(?:static\s+)?(?P<type>void)\s+'+re.escape(name)+r'\s*\([^;{}]*\)\s*',part.clean[start:part.opening])
    if not signature or re.search(r'\b(?:return|goto|if|switch|for|while|do|asm|__asm__)\b',part.body):return []
    # Updating recursive calls or any external prototype requires a coordinated ABI review.
    if re.search(r'\b'+re.escape(name)+r'\s*\(',_blank(header+'\n'+source[:start]+source[part.end:]+part.body)):return []
    macros=set(re.findall(r'^\s*#\s*define\s+('+IDENT+')',header+'\n'+source,re.M))
    if macros&set(re.findall(IDENT,part.body)):return []
    declarations=locals_for(part);choices=[]
    for d in declarations:
        if integer_abi.scalar(d['type'],header+'\n'+source,arch) not in {(32,True),(32,False)}:continue
        var=d['name'];uses=list(re.finditer(r'(?<![\w.>])'+re.escape(var)+r'\b',part.clean[d.end():part.end-1]))
        # A standalone assignment initializes this top-level scalar on the only path.
        assignment=re.search(r'(?<=[;{}])\s*'+re.escape(var)+r'\s*=(?!=)\s*([^;{}]+);',part.clean[d.end():part.end-1])
        if not assignment or not uses or re.search(r"\b"+re.escape(var)+r"\b",assignment[1]):continue
        lhs=assignment.start()+re.search(r"\b"+re.escape(var)+r"\b",assignment[0]).start()
        if uses[0].start()!=lhs:continue
        prefix=part.clean[part.opening+1:d.start()]
        if prefix.count('{')!=prefix.count('}'):continue
        call=bool(re.match(IDENT+r'\s*\(',assignment[1]))
        choices.append((not call,-len(uses),var,d['type']))
    out=[]
    for _,_,var,typ in sorted(choices)[:LIMIT]:
        out.append((FAMILY,edit(source,[(start+signature.start('type'),start+signature.end('type'),typ),
                                       (part.end-1,part.end-1,'\nreturn '+var+';\n')])))
    return out


def hint(source, name, asm, arch, caller=''):
    proof=witness(asm,arch,caller,name)
    if proof and re.search(r'\bvoid\s+'+re.escape(name)+r'\s*\(',_blank(source)):
        return ('Return-contract hypothesis: target preserves a word result in its return register'
                +(' and a caller consumes it' if proof['caller_consumes'] else '')
                +'. The draft says void. Test returning an existing initialized scalar before allocation permutations; ordinary judging is required.')
    return ''
