"""Bounded lifetime experiments from exact matches; every result needs the ordinary judge."""
import re

from .call_lifetime_repairs import block_path
from .integer_repair_support import IDENT, NUMBER, edit, locals_for, view

FAMILIES = ('arithmetic_before_call', 'typed_element_store', 'narrow_value_reuse')
WORD = {'int', 'signed int', 'unsigned int', 's32', 'u32'}


def variants(source, name, header=''):
    part = view(source, name)
    if not part or re.search(r'\b(?:volatile|asm|__asm__|goto)\b', source):
        return []
    result = []
    declarations = locals_for(part)
    macros = set(re.findall(r'^\s*#\s*define\s+('+IDENT+r')', source+'\n'+header, re.M))
    # Restrict staging to arithmetic on a declared word local and a literal. The other
    # operand is an actual call, not a macro. This changes lifetime, not call order.
    pattern = rf'\(\s*({IDENT})\s*([+-])\s*({NUMBER})\s*\)\s*\+\s*({IDENT})\s*\(\s*\)'
    for hit in list(re.finditer(pattern, part.body))[:3]:
        origin = next((d for d in declarations if d['name']==hit[1] and d['type'] in WORD), None)
        start = part.opening+1+hit.start()
        if not origin or hit[4] in macros:
            continue
        # Only an ordinary standalone assignment; never hoist out of short-circuit
        # evaluation, a loop header, a condition, or an unbraced controlled statement.
        prefix = part.clean[:start]
        boundary = max(prefix.rfind(';'), prefix.rfind('{'), prefix.rfind('}'))+1
        lhs = prefix[boundary:]
        if not re.fullmatch(r'\s*'+IDENT+r'(?:\s*\[\s*'+NUMBER+r'\s*\])?\s*=\s*', lhs):
            continue
        path = block_path(part, start)
        origin_path = block_path(part, origin.start())
        if path[:len(origin_path)] != origin_path:
            continue
        fresh = '__repair_call_arithmetic'
        if fresh in source:
            continue
        expression = f'{hit[1]} {hit[2]} {hit[3]}'
        result.append((FAMILIES[0], edit(source, [
            (part.opening+1,part.opening+1,f'\n{origin["type"]} {fresh};'),
            (boundary,boundary,f'\n{fresh} = {expression};\n'),
            (start,start+hit.end()-hit.start(),f'{fresh} + {hit[4]}()')])) )
    # Stage an element address only when the original typedef states its type.
    context = source+'\n'+header
    structs = {}
    for m in re.finditer(r'\btypedef\s+struct(?:\s+'+IDENT+r')?\s*{([^{}]*)}\s*('+IDENT+r')\s*;',context,re.S):
        structs[m[2]] = m[1]
    store = rf'(?P<base>{IDENT})->(?P<array>{IDENT})\[(?P<index>{IDENT}|{NUMBER})\]\.(?P<field>{IDENT})\s*=\s*(?P<rhs>[^;{{}}]+);'
    for hit in list(re.finditer(store,part.body))[:3]:
        start = part.opening+1+hit.start()
        prefix=part.clean[:start].rstrip()
        if (prefix[-1:] not in ';{}' or macros.intersection(re.findall(IDENT,hit[0]))
                or re.search(r'\+\+|--|(?<![=!<>])=(?!=)|\b'+IDENT+r'\s*\(',hit['rhs'])):
            continue
        types = re.findall(r'\b('+IDENT+r')\s*\*\s*'+re.escape(hit['base'])+r'\b',context)
        if len(set(types))!=1 or types[0] not in structs:
            continue
        member = re.search(r'\b('+IDENT+r')\s+'+re.escape(hit['array'])+r'\s*\[[^]]+\]\s*;',structs[types[0]])
        if (not member or member[1] not in structs
                or re.search(r'\bvolatile\b',structs[types[0]]+' '+structs.get(member[1],''))
                or re.search(r'\bvolatile\b[^;{}\n]*\b'+re.escape(hit['base'])+r'\b',context)):
            continue
        fresh='__repair_element'
        if fresh in source:
            continue
        replacement = '{'+member[1]+' *'+fresh+'; '+fresh+' = &'+hit['base']+'->'+hit['array']+'['+hit['index']+']; '+fresh+'->'+hit['field']+' = '+hit['rhs']+';}'
        result.append((FAMILIES[1],edit(source,[(start,part.opening+1+hit.end(),replacement)])))
        # An existing correctly typed pointer can be overwritten when it is never
        # read again after this store. Do not extend this into a liveness guess
        # through later branches or loops.
        enclosing_loop = any(re.search(r'\b(?:for|while)\s*\([^{}]*\)\s*$|\bdo\s*$',part.clean[:brace])
                             for brace in block_path(part,start)[1:])
        if not enclosing_loop:
            for pointer in re.finditer(r'(?<=[;{}])\s*'+re.escape(member[1])+r'\s*\*\s*('+IDENT+r')\s*;',part.clean[part.opening:start]):
                variable=pointer[1]
                declaration=part.opening+pointer.start()
                path=block_path(part,declaration)
                if (len(re.findall(r'\b'+IDENT+r'\s+\**\s*'+re.escape(variable)+r'\s*[;=,)]',part.body))!=1
                        or variable in re.findall(IDENT,hit[0])
                        or block_path(part,start)[:len(path)]!=path
                        or any(not re.search(r'(?:\.|->)\s*$',part.clean[:m.start()])
                               for m in re.finditer(r'\b'+re.escape(variable)+r'\b',part.clean)
                               if part.opening+1+hit.end() <= m.start() < part.end)
                        or re.search(r'&\s*'+re.escape(variable)+r'\b',part.body)):
                    continue
                replacement=variable+' = &'+hit['base']+'->'+hit['array']+'['+hit['index']+']; '+variable+'->'+hit['field']+' = '+hit['rhs']+';'
                result.append((FAMILIES[1],edit(source,[(start,part.opening+1+hit.end(),replacement)])))
                break
    # Reuse explicit narrowing only for unescaped locals in straight-line code.
    narrow=rf'\(\s*(u16|unsigned short)\s*\)\s*\(\s*({IDENT})\s*\+\s*({NUMBER})\s*\)'
    for hit in list(re.finditer(narrow,part.body))[:2]:
        variable=hit[2]
        decl=next((d for d in declarations if d['name']==variable and d['type'] in WORD),None)
        if not decl:
            continue
        start=part.opening+1+hit.start()
        boundary=max(part.clean.rfind(';',part.opening,start),part.clean.rfind('{',part.opening,start))+1
        # Up to four adjacent statements in the same lexical block. The origin is
        # an unescaped local, so callees cannot change it; reject explicit writes.
        tail=part.clean[boundary:part.end-1]
        end=0
        for _ in range(4):
            stop=tail.find(';',end)
            if stop<0:
                break
            candidate=tail[:stop+1]
            if macros.intersection(re.findall(IDENT,candidate)):
                break
            if re.search(r'[{}?:]|\b(?:if|while|for|switch|return|break|continue)\b',candidate):
                break
            if re.search(r'\b'+re.escape(variable)+r'\s*(?:[+*/%&|^-]?=(?!=)|\+\+|--)|(?:\+\+|--)\s*\b'+re.escape(variable)+r'\b',candidate):
                break
            end=stop+1
            if candidate.count(hit[0])>=2:
                fresh='__repair_narrow'
                if fresh in source+header:
                    break
                # Preserve statement boundaries and all calls; only replace equal
                # explicit unsigned-short conversions with their once-computed value.
                replacement='\n{unsigned int '+fresh+'; '+fresh+' = '+hit[0]+'; '+source[boundary:boundary+end].replace(hit[0],fresh)+'}'
                result.append((FAMILIES[2],edit(source,[(boundary,boundary+end,replacement)])))
                break
    return list(dict.fromkeys(result))[:8]
