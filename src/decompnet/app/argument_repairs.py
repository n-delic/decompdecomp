"""Candidate-local ordinary integer/pointer signatures; byte verification remains mandatory."""

import re

from ..arch.mips_arguments import evidence

SCALARS={'int','char','short','signed','unsigned','s8','u8','s16','u16','s32','u32',
         'int8_t','uint8_t','int16_t','uint16_t','int32_t','uint32_t','size_t','intptr_t','uintptr_t','M2C_UNK'}
QUALIFIERS={'const','volatile','register','restrict','__restrict'}


def ordinary(parameters, named=False):
    if parameters.strip() in ('','void'):
        return []
    result=parameters.split(',')
    for parameter in result:
        if not re.fullmatch(r'[\w\s*]+',parameter) or not parameter.strip():
            return None
        words=re.findall(r'\w+',parameter)
        if named:
            if len(words)<2 or words[-1] in SCALARS|QUALIFIERS:
                return None
            words=words[:-1]
        words=[w for w in words if w not in QUALIFIERS]
        if not words or '*' not in parameter and any(w not in SCALARS for w in words):
            return None
    return [p.strip() for p in result] if len(result)<=4 else None


def variants(source, report, target, big):
    from .similar_matches import TOKENS, relocations

    if not report.get('candidate_target'):
        return []
    candidate={'target':bytes.fromhex(report['candidate_target']),'relocs':report.get('candidate_relocs','[]')}
    relocated={off for off,_,_ in relocations(candidate)}|{off for off,_,_ in relocations(target)}
    proof=evidence(candidate['target'],target['target'] or b'',relocated,big)
    if not proof:
        return []
    kind,delta,indices=proof
    masked=TOKENS.sub(lambda t:' '*len(t.group()) if t.group().startswith(('/*','//','"',"'")) else t.group(),source)
    definition=re.search(r'\b'+re.escape(target['name'])+r'\s*\(([^()]*)\)\s*{',masked)
    if not definition:
        return []
    parameters=ordinary(definition[1],named=True)
    if parameters is None or len(parameters)+delta>4:
        return []
    names=[f'__reuse_arg{i}' for i in range(delta)]
    if any(re.search(r'\b'+name+r'\b',masked) for name in names):
        return []
    position=min(indices) if kind=='incoming' else 0
    new_parameters=parameters[:position]+[f'int {name}' for name in names]+parameters[position:]
    edits=[(definition.start(1),definition.end(1),', '.join(new_parameters))]
    if kind=='incoming':
        if not indices or max(indices)>=len(parameters):
            return []
    else:
        # Forward untouched argument registers through a tiny wrapper instead of inventing zero values.
        # Existing nonempty signatures, nested calls and multiple statements need a fuller C reader.
        if parameters:
            return []
        end=masked.find('}',definition.end())
        if end<0:
            return []
        body=masked[definition.end():end]
        call=re.fullmatch(r'\s*(?:return\s+)?([A-Za-z_]\w*)\s*\(([^();{}]*)\)\s*;\s*',body)
        if not call or call[1]==target['name']:
            return []
        arguments=[a.strip() for a in call[2].split(',')] if call[2].strip() else []
        if not arguments or max(indices)>=len(arguments) or len(arguments)+delta>4:
            return []
        if not all(re.fullmatch(r'(?:[A-Za-z_]\w*|-?(?:0x[0-9a-fA-F]+|[0-9]+)[uUlL]*)',a) for a in arguments):
            return []
        start=definition.end()+call.start(2);stop=definition.end()+call.end(2)
        edits.append((start,stop,', '.join(names+arguments)))
        # Only candidate-local declarations are changed. A conflicting shared prototype fails compilation.
        declaration=re.compile(r'(?m)^[ \t]*([A-Za-z_]\w*(?:[ \t]+[A-Za-z_]\w*)*[ \t*]+)'+re.escape(call[1])+r'\s*\(([^()]*)\)\s*;')
        for decl in declaration.finditer(masked):
            if 'return' in decl[1].split():
                continue
            types=ordinary(decl[2])
            if types is None:
                types=ordinary(decl[2],named=True)
            if types is None or types and len(types)!=len(arguments):
                return []
            if types:edits.append((decl.start(2),decl.end(2),', '.join(['int']*delta+types)))
    text=source
    for start,stop,replacement in sorted(edits,reverse=True):
        text=text[:start]+replacement+text[stop:]
    return [text]
