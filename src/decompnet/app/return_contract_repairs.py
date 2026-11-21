"""Check a missing scalar return before searching allocation; shared prototypes stay untouched."""
import re

from decomprepair import integer_abi
from decomprepair.integer_repair_support import IDENT, edit, locals_for, view
from decomprepair.pattern_repairs import close

from .. import arch as architectures
from ..forks.csource import _blank, find

FAMILY = 'return_contract'
LIMIT = 4
WORDS = {'s32', 'u32', 'int', 'unsigned', 'unsigned int', 'long', 'unsigned long'}  # (m2c's names, before any typedef is read)


def witness(asm, arch, caller='', name=''):
    reader=architectures.get(arch).source_return_contract
    return reader(asm,caller,name) if reader else None


def variants(source, name, header='', arch=None, asm=''):
    part=view(source,name);proof=witness(asm,arch)
    if not part or not proof or proof['bits']!=32 or proof.get('requires_caller'):
        return flag_variants(source,name,header,arch,asm)
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


def flag_variants(source, name, header='', arch=None, asm=''):
    """A `void` function whose last act is `if (flag == 0) { callee(); }` and whose target leaves the flag in `$v0`.

    The target copies a status into the return register once, at the final test, and the callee's result takes its
    place (an N64 sound-request guard, gcc 2.7.2: score 15 to exact, docs/COMPILER_MATCHING_LEARNINGS.md):

        flag = x; if (flag == 0) { flag = callee(); } return flag;

    m2c wrote the same function as `void`, with `flag = x;` copied on every path before the test (and the early copy
    constant-folded into the wrong register). Here the copies are sunk into the one at the test, the callee's result
    is kept and returned. A function with gotos or nested guards is fine: only the copies and the tail change.
    """
    part=view(source,name)
    if not part:return []
    part.end=close(part.clean,part.opening,'{','}') or part.end
    start,_=find(source,name)
    signature=re.fullmatch(r'\s*(?:static\s+)?(?P<type>void)\s+'+re.escape(name)+r'\s*\([^;{}]*\)\s*',part.clean[start:part.opening])
    if not signature or re.search(r'\b(?:return|switch|asm|__asm__)\b',part.body):return []
    if re.search(r'\b'+re.escape(name)+r'\s*\(',_blank(header+'\n'+source[:start]+source[part.end:]+part.body)):return []
    clean=part.clean;body_end=part.end-1
    tail=re.search(r'(?P<indent>[ \t]*)if\s*\(\s*(?:(?P<a>'+IDENT+r')\s*==\s*0|!\s*(?P<b>'+IDENT+r'))\s*\)\s*\{\s*(?P<callee>'+IDENT+r')\s*\((?P<args>[^;{}]*)\)\s*;\s*\}\s*$',clean[part.opening+1:body_end])
    if not tail:return []
    base=part.opening+1;flag=tail['a'] or tail['b'];callee=tail['callee']
    if callee==name or re.search(r'\bvoid\s+'+re.escape(callee)+r'\s*\(',_blank(header+'\n'+source)):return []
    decl=[d for d in locals_for(part) if d['name']==flag]
    if not decl or (decl[0]['type'].strip() not in WORDS and integer_abi.scalar(decl[0]['type'],header+'\n'+source,arch) not in {(32,True),(32,False)}):return []
    before=clean[part.opening+1:base+tail.start()]
    copies=list(re.finditer(r'(?m)^[ \t]*'+re.escape(flag)+r'\s*=(?!=)\s*('+IDENT+r'|0)\s*;[ \t]*\n?',before))
    # a copy of a variable gives the value; a copy of 0 (m2c folded the variable where it was known to be zero) is dropped
    sources={m[1] for m in copies if m[1]!='0'}
    if not copies or len(sources)!=1:return []
    value=sources.pop()
    # the flag is read only by the final test, and is assigned only by those copies
    if len(re.findall(r'(?<![\w.>])'+re.escape(flag)+r'\b',before))!=len(copies)+1:return []
    if value not in {d['name'] for d in locals_for(part)}:return []
    edits=[(start+signature.start('type'),start+signature.end('type'),decl[0]['type'])]
    for m in copies:
        at=base+m.start();label=re.search(r'\w+:\s*$',clean[:at])
        edits.append((at,base+m.end(),(re.match(r'[ \t]*',m[0])[0]+';\n') if label else ''))
    at=base+tail.start()
    indent=tail['indent']
    edits.append((at,body_end,f"{indent}{flag} = {value};\n{indent}if ({flag} == 0) {{\n{indent}    {flag} = {callee}({tail['args']});\n{indent}}}\n{indent}return {flag};\n"))
    return [(FAMILY,edit(source,edits))]


def hint(source, name, asm, arch, caller=''):
    proof=witness(asm,arch,caller,name)
    if proof and proof.get('requires_caller'):
        if not proof['caller_consumes']:return ''
        if re.search(r'\bvoid\s+'+re.escape(name)+r'\s*\(',_blank(source)):
            return ('Return-contract hypothesis: target leaves a loaded word in its return register and a caller consumes it. '
                    'The draft says void. Review the loaded value\'s scalar or pointer type and return contract before '
                    'allocation permutations; shared prototypes were not changed.')
    if proof and re.search(r'\bvoid\s+'+re.escape(name)+r'\s*\(',_blank(source)):
        return ('Return-contract hypothesis: target preserves a word result in its return register'
                +(' and a caller consumes it' if proof['caller_consumes'] else '')
                +'. The draft says void. Test returning an existing initialized scalar before allocation permutations; ordinary judging is required.')
    return ''
