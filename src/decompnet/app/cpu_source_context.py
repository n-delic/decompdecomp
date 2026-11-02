"""Bounded actual reference declarations and call forms, never fabricated ABI types."""
import re

from ..forks.csource import _blank
from .source_match_context import dependencies, macros
from .source_next_steps import called, signatures

ID = r'[A-Za-z_]\w*'


def declarations(text, names):
    """Only unambiguous ANSI prototypes/definition heads; retain aggregate return/argument types."""
    result = {}
    for name in names:
        pattern = r'(?:^|(?<=[;{}\n]))[ \t]*((?:(?:extern|static|inline)[ \t]+)?(?:'+ID+r'[ \t*]+)+)'+re.escape(name)+r'\s*\(([^(){};]*)\)\s*(?:;|\{)'
        hits = [m for m in re.finditer(pattern, _blank(text)) if not re.search(r'\b(?:return|if|else|while|for|switch)\b',m[1])]
        sigs = signatures(text, {name})
        if hits and len({(' '.join(m[1].split()), ' '.join(m[2].split())) for m in hits}) == 1:
            m = hits[0]
            if '...' not in m[2] and name in sigs:
                result[name] = re.sub(r'\b(?:extern|static|inline)\s+', '', m[1])+name+'('+m[2]+');'
    return result


def variants(source, header, reference, target_calls=()):
    from ..forks.csource import find
    used = {name for name in called(source) if not find(source,name)}
    originals = declarations(reference, used)
    current = signatures(header, used)
    changes = []
    updated = header
    for name, declaration in sorted(originals.items()):
        if name not in current:
            # A call without a declaration promotes floats to double. Use only the actual
            # selected reference declaration; never infer parameter types from a call.
            if name not in macros(header) and not re.search(r'\(\s*\*\s*'+re.escape(name)+r'\b',_blank(header)):
                updated += '\n'+declaration+'\n'
            continue
        if current[name] == signatures(declaration, {name}).get(name):
            continue
        # Replace a prototype only, never a function body, function pointer or macro.
        pattern = r'(?m)^[ \t]*(?:(?:extern|static|inline)[ \t]+)?(?:'+ID+r'[ \t*]+)+'+re.escape(name)+r'\s*\([^(){};]*\)\s*;'
        hits = list(re.finditer(pattern, _blank(updated)))
        if len(hits) != 1:
            continue
        m = hits[0]
        updated = updated[:m.start()]+declaration+'\n'*updated[m.start():m.end()].count('\n')+updated[m.end():]
    if updated != header:
        changes.append(('cpu-reference-abi', source, updated))
    actual_records=merge_records(source,updated,reference)
    if actual_records and actual_records!=updated:
        changes.append(('cpu-reference-records',source,actual_records))
    # A direct target call is evidence to try the actual prototype instead of a macro.
    callbase=actual_records or updated
    available = {**declarations(callbase, used), **originals}
    for name in sorted(set(target_calls) & used & available.keys()):
        macro = macros(callbase).get(name)
        if not macro:
            continue
        text = macro[0]
        if len(text) > 4096:
            continue
        h = callbase.replace(text, '\n'*text.count('\n'), 1)
        if name not in declarations(h, {name}):h += '\n'+available[name]+'\n'
        changes.append(('cpu-preserve-target-call', source, h))
    from .measured_source_repairs import externs
    return [*externs(source,header,reference),*changes][:4]


def reachable_types(source, header):
    """Include actual types of macro-reached extern objects, not only explicit local type names."""
    from .source_context import typedefs
    used=dependencies(source,header)
    for declaration in declarations(header,called(source)).values():used.update(re.findall(ID,declaration))
    for _ in range(8):
        before=set(used)
        for m in re.finditer(r'\bextern\s+([^;{}()]+);',_blank(header)):
            names=set(re.findall(r'\b('+ID+r')\s*(?=,|\[|$)',m[1]))
            if names & used:used.update(re.findall(ID,m[1]))
        for name,declaration in typedefs(header).items():
            if name in used:used.update(re.findall(ID,declaration))
        if used==before:break
    return used


def merge_records(source, header, verified):
    """Transfer actual named record declarations as one private hypothesis, retaining dependencies."""
    from .source_context import typedefs
    own, other = typedefs(header), typedefs(verified)
    used = reachable_types(source, header)
    names = {n for n in used & own.keys() & other.keys() if '{' in own[n] and own[n] != other[n]}
    if not names:return None
    # Dependency closure must already exist with identical non-record aliases. Avoid mixing SDKs.
    for _ in range(8):
        old = set(names)
        for name in list(names):
            for dep in set(re.findall(ID, other[name])) & other.keys():
                if dep == name:continue
                if dep not in own:return None
                if own[dep] != other[dep]:
                    if '{' not in other[dep]:return None
                    names.add(dep)
        if names == old:break
    else:return None
    if len(names)>8:return None
    result = header
    # Locate whole typedefs using their token-normalized spelling, without reformatting others.
    for name in sorted(names):
        from .pattern_repairs import close
        clean=_blank(result);hits=[]
        for match in re.finditer(r'\btypedef\s+(?:struct|union)(?:\s+'+ID+r')?\s*\{',clean):
            end=close(clean,match.end()-1,'{','}')
            if not end:continue
            alias=re.match(r'\s*'+re.escape(name)+r'\s*;',clean[end:])
            if alias and ' '.join(clean[match.start():end+alias.end()].split())==own[name]:
                hits.append((match.start(),end+alias.end()))
        if len(hits)!=1:return None
        a,b=hits[0];result=result[:a]+other[name]+result[b:]
    return result
