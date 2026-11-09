"""Bounded same-width zero-test and declared pointer-call lifetime hypotheses; exact judge required."""
import re

from ..m2cfix import _split_top_level
from .call_lifetime_repairs import block_path
from .integer_repair_support import IDENT, edit, locals_for, refs, view
from .pattern_repairs import close

WORD = {'int', 'unsigned int', 'signed int', 's32', 'u32'}


def variants(source, name, header=''):
    part = view(source, name)
    if not part or re.search(r'\b(?:volatile|asm|__asm__)\b', part.clean):
        return []
    result = []
    locals_ = [d for d in locals_for(part) if d['type'] in WORD]
    for hit in list(re.finditer(rf'\b(?P<copy>{IDENT})\s*=\s*(?P<origin>{IDENT})\s*;\s*'
                               rf'(?P=origin)--\s*;\s*if\s*\(\s*(?P=copy)\s*!=\s*0\s*\)', part.body))[:3]:
        copied = next((d for d in locals_ if d['name'] == hit['copy']), None)
        origin = next((d for d in locals_ if d['name'] == hit['origin']), None)
        if not copied or not origin:
            continue
        for spare in locals_:
            if spare['name'] in {hit['copy'],hit['origin']} or refs(part,spare) or spare.end() >= part.opening+1+hit.start():
                continue
            path = block_path(part, spare.start())
            if block_path(part, part.opening+1+hit.start())[:len(path)] != path:
                continue
            replacement = re.sub(r'\b'+re.escape(hit['copy'])+r'\b',spare['name'],hit[0])
            result.append(('word_zero_test_reuse',edit(source,[(part.opening+1+hit.start(),part.opening+1+hit.end(),replacement)])))
            if len(result) >= 3:
                break
    # No prototype inference: accept only an explicit no-argument pointer-return declaration.
    context = part.clean[:part.opening]+'\n'+header
    macro_names = set(re.findall(r'^\s*#\s*define\s+('+IDENT+r')\b', source+'\n'+header, re.M))
    prototypes = list(re.finditer(rf'\b(?P<type>(?:const\s+)?(?:struct\s+)?{IDENT}\s*\*)\s*(?P<name>{IDENT})\s*\(\s*(?:void)?\s*\)\s*;',context))
    for proto in prototypes[:16]:
        callee = proto['name']
        if callee in macro_names or len({' '.join(p['type'].split()) for p in prototypes if p['name']==callee})!=1 or find_definition(source,callee):
            continue
        pointer = re.search(re.escape(proto['type'])+r'\s*('+IDENT+r')\s*;',part.clean[part.opening:part.end])
        if not pointer:
            continue
        variable = pointer[1]
        # Only a currently unused candidate-local pointer; no new padding or guessed stack object.
        if len(re.findall(r'\b'+re.escape(variable)+r'\b',part.body)) != 1:
            continue
        for call in re.finditer(rf'(?<=[;{{}}])\s*({IDENT})\s*\(',part.body):
            if call[1] in {'if','while','for','switch','sizeof'} | macro_names:
                continue
            opening = part.opening+call.end()
            end = close(part.clean, opening)
            if not end or not re.match(r'\s*;',part.clean[end:]):
                continue
            args = _split_top_level(part.clean[opening+1:end-1])
            nested = [i for i,arg in enumerate(args) if re.fullmatch(re.escape(callee)+r'\s*\(\s*\)',arg.strip())]
            if len(nested)!=1:
                continue
            remaining = ','.join(arg for i,arg in enumerate(args) if i!=nested[0])
            if re.search(r'\+\+|--|(?<![=!<>])=(?!=)|\b'+IDENT+r'\s*\(',remaining):
                continue
            # Do not stage calls in loop headers or across labels/scopes; the assignment remains
            # immediately adjacent, under the exact same control as the original statement.
            prefix=part.body[:call.start()]
            if prefix.count('(')!=prefix.count(')'):
                continue
            start=part.opening+1+call.start()
            declaration = part.opening+pointer.start(1)
            path = block_path(part, declaration)
            if declaration >= start or block_path(part, start)[:len(path)] != path:
                continue
            args[nested[0]]=variable
            suffix=re.match(r'\s*;',part.clean[end:])
            original=source[start:end+suffix.end()]
            opening_relative=opening-start
            rewritten=original[:opening_relative+1]+', '.join(args)+original[end-start-1:]
            result.append(('pointer_call_staging',edit(source,[(start,end+suffix.end(),f'\n{variable} = {callee}();\n'+rewritten)])))
            if sum(f=='pointer_call_staging' for f,_ in result)>=3:
                return result
    return result


def find_definition(source, name):
    from ..forks.csource import find
    return find(source,name) is not None
