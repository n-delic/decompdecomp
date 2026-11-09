"""Extract pure integer predicates and stage their masked bits without changing reads.

C32 evidence is supplied by the pinned invocation. No calls, fields, escaped locals,
unknown widths or macro operands are extracted. These are compiler hypotheses only.
"""
import re

from ..forks.csource import _blank, find
from . import integer_abi
from .integer_repair_support import IDENT, INTEGER, edit, view

FAMILIES = ('inline_bit_predicate', 'staged_bit_predicate')
DEPTHS = (0, 1, 3, 5, 2, 4, 6)
LIMIT = 14


def wrap(text, depth):
    return 'do{'*depth+text+'}while(0);'*depth


def facts(source, name, header, arch, language, int_bits):
    if language != 'c' or int_bits != 32 or len(source)+len(header)>150000:
        return None
    part=view(source,name)
    context=_blank(header+'\n'+source)
    if not part or re.search(r'\\\n|\?\?|%:|\b(?:volatile|_Atomic|asm|__asm__|typeof|__typeof__|_Generic|setjmp|longjmp|__LINE__|__COUNTER__)\b',part.clean):
        return None
    macros=set(re.findall(r'^\s*#\s*define\s+('+IDENT+')',context,re.M))
    if macros & {'unsigned','int','static','__inline__','return','do','while'}:
        return None
    # Unique ordinary scalar bindings only, including parameters and comma declarations.
    kinds={}
    pattern=rf'\b({INTEGER})\s+({IDENT}(?:\s*,\s*{IDENT})*)\s*(?=[;=,)])'
    for hit in re.finditer(pattern,part.clean[find(source,name)[0]:part.end]):
        kind=integer_abi.scalar(hit[1],context,arch)
        if kind is None or kind[0]>32:continue
        for variable in re.findall(IDENT,hit[2]):kinds.setdefault(variable,[]).append(kind)
    types={v:t[0] for v,t in kinds.items() if len(t)==1 and v not in macros}
    # Pointer/qualified shadows and escaped bindings require richer binding analysis.
    from .decoded_byte_repairs import address_escape
    for variable in list(types):
        declarations=[m for m in re.finditer(r'\b('+IDENT+r')\s*\**\s*'+re.escape(variable)+r'\s*(?=[;=,)\[])',part.clean)
                      if m[1] not in {'return','goto','else','case','break','continue'}]
        if len(declarations)!=1 or address_escape(part.body,variable,types):
            types.pop(variable)
    return part,types,macros


def predicates(part, types):
    # Identifiers, optionally a scalar cast, or the existing signed 1 << local expression.
    atom=rf'(?:\(\s*{INTEGER}\s*\)\s*)?{IDENT}'
    mask=rf'(?:\(\s*1\s*<<\s*{IDENT}\s*\)|{atom})'
    pattern=rf'\(\s*(?P<word>{IDENT})\s*&\s*(?P<mask>{mask})\s*\)\s*==\s*(?P=mask)(?!\w)'
    for hit in re.finditer(pattern,part.clean[part.opening:part.end]):
        names=set(re.findall(IDENT,hit['word']+' '+hit['mask']))-set(re.findall(IDENT,INTEGER))-{'unsigned','signed','short','char','int','u8','u16','u32','s8','s16','s32'}
        if names <= types.keys():
            yield part.opening+hit.start(),part.opening+hit.end(),hit['word'],hit['mask']


def fresh(context, stem):
    while re.search(r'\b'+re.escape(stem)+r'\b',context):stem+='_' 
    return stem


def helper_tunings(source, name, macros):
    result=[]
    focal=view(source,name)
    if not focal:return []
    pattern=r'static\s+__inline__\s+int\s+(__repair_bit_predicate_*)\s*\(unsigned int\s+('+IDENT+r'),unsigned int\s+('+IDENT+r')\)\s*{'
    for hit in re.finditer(pattern,_blank(source)):
        helper,a,b=hit.groups()
        span=find(source,helper)
        if not span or {helper,a,b}&macros or not re.search(r'\b'+re.escape(helper)+r'\s*\(',focal.body):continue
        body=_blank(source[span[0]:span[1]])
        body=body[body.index('{')+1:body.rfind('}')]
        predicate=f'return ({a}&{b})=={b};'
        compact=lambda text:re.sub(r'\s+','',text)
        if not any(compact(body)==compact(wrap(predicate,k))for k in range(7)):continue
        definition=source[span[0]:span[1]]
        for depth in DEPTHS:
            replacement=f'static __inline__ int {helper}(unsigned int {a},unsigned int {b}){{'+wrap(predicate,depth)+'}'
            replacement+=definition[definition.rfind('}')+1:]
            if compact(replacement)!=compact(definition):result.append((FAMILIES[0],edit(source,[(span[0],span[1],replacement)])))
        if result:break
    return result


def variants(source, name, header='', arch=None, *, language=None, int_bits=None):
    parsed=facts(source,name,header,arch,language,int_bits)
    if not parsed:return []
    part,types,macros=parsed
    if '__repair_bit_predicate' in source:return helper_tunings(source,name,macros)
    context=source+'\n'+header
    results=[]
    for start,end,word,mask in list(predicates(part,types))[:2]:
        helper=fresh(context,'__repair_bit_predicate')
        a=fresh(context,'word');b=fresh(context,'mask')
        for depth in DEPTHS:
            definition=f'static __inline__ int {helper}(unsigned int {a},unsigned int {b}){{'+wrap(f'return ({a}&{b})=={b};',depth)+'}\n'
            candidate=edit(source,[(start,end,f'{helper}({word},{mask})'),(find(source,name)[0],find(source,name)[0],definition)])
            results.append((FAMILIES[0],candidate))
    return results[:LIMIT]


def stages(source, name, header='', arch=None, *, language=None, int_bits=None):
    parsed=facts(source,name,header,arch,language,int_bits)
    if not parsed or '__repair_masked_bits' in source:return []
    part,types,_=parsed;results=[]
    for start,end,word,mask in predicates(part,types):
        prefix=part.clean[part.opening:start]
        assignment=re.search(r'(?<=[;{}])\s*('+IDENT+r')\s*=\s*$',prefix)
        declaration=re.search(r'(?<=[;{}])\s*('+INTEGER+r')\s+('+IDENT+r')\s*=\s*$',prefix)
        if (not assignment and not declaration) or part.clean[end:].lstrip()[:1]!=';':continue
        dest=assignment[1] if assignment else declaration[2]
        if dest not in types or dest in set(re.findall(IDENT,word+' '+mask)):continue
        stop=end+len(part.clean[end:])-len(part.clean[end:].lstrip())+1
        begin=part.opening+(assignment.start() if assignment else declaration.start())
        temp=fresh(source+'\n'+header,'__repair_masked_bits')
        # Equality of promoted <=32-bit integers is equality of their unsigned bit patterns.
        # Evaluate each original operand only as an unescaped, nonvolatile scalar expression.
        raw=f'unsigned int {temp}; {temp}=(unsigned int)({word})^(unsigned int)({mask}); {temp}&=(unsigned int)({mask});'
        final=f'{dest}=({temp}==0);'
        if declaration:
            # Keep an initializer in its original declaration position (GNU C89).
            # Converting it to a separate block would strand following declarations.
            value='({'+raw+f'({temp}==0);'+'})'
            results.append((FAMILIES[1],edit(source,[(start,end,value)])))
            if len(results)>=8:break
            continue
        results.append((FAMILIES[1],edit(source,[(begin,stop,'{'+raw+final+'}')])))
        # An adjacent pure integer address calculation may execute before the zero test.
        tail=re.match(r'\s*('+IDENT+r')\s*=\s*([^;{}]+);',part.clean[stop:part.end])
        if tail and tail[1] in types and types[tail[1]]==(32,False):
            operands=set(re.findall(IDENT,tail[2]))
            if operands<=types.keys() and not operands & {dest} and not re.search(r'[^\w\s+*()\-]|\b'+IDENT+r'\s*\(',tail[2]) and tail[1] not in {word,*re.findall(IDENT,mask)}:
                address=source[stop:stop+tail.end()]
                results.append((FAMILIES[1],edit(source,[(begin,stop+tail.end(),'{'+raw+address+final+'}')])))
        if len(results)>=8:break
    return results[:8]
