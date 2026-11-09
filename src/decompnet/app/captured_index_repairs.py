"""Bounded lifetime/index/loop hypotheses from comparable repair captures.

Never change shared declarations or compiler settings. The ordinary judge alone saves results.
"""
import re

from . import local_flow
from .call_lifetime_repairs import block_path
from .integer_repair_support import IDENT, edit, locals_for, view
from .pattern_repairs import close

FAMILIES = ('dead_index_scale', 'dead_pointer_clear', 'store_index_reuse',
            'constant_count_for', 'split_mask_assignment')
WIDE = {'u32', 'unsigned int', 's32', 'int', 'signed int'}


def statement(part, node):
    """Only standalone braced expression statements; never extend an unbraced arm."""
    return (part.clean[:node.start].rstrip()[-1:] in {';', '{', '}'}
            and part.clean[node.start:node.end].rstrip().endswith(';')
            and not re.match(r'\s*(?:return|break|continue)\b', part.clean[node.start:node.end]))


def outgoing_dead(flow, node, variable):
    return all(variable not in flow.nodes[n].live for n in node.successors)


def variants(source, name, header=''):
    part = view(source, name)
    if not part or re.search(r'\b(?:asm|__asm__|setjmp|longjmp|sigsetjmp|_setjmp)\b', part.clean):
        return []
    # A macro can hide aliases, repeated evaluation or control flow. This module only
    # handles ordinary C expressions, leaving macro-heavy drafts to their own repairs.
    macros = re.findall(r'^\s*#\s*define\s+('+IDENT+r')\b', part.clean+'\n'+header, re.M)
    if (any(re.search(rf'\b{macro}\b', part.body) for macro in macros)
            or re.search(r'\b[A-Z][A-Z_0-9]+\s*\(', part.body)):
        return []
    out = []
    flow = local_flow.analyze(source, name)
    if flow:
        wide = [d for d in flow.locals if d['type'] in WIDE]
        for node in flow.nodes:
            if not statement(part, node):
                continue
            text = part.clean[node.start:node.end]
            # Scale only a final unsigned index occurrence. An assignment inside a
            # macro argument may run twice, so require an ordinary array subscript.
            for hit in re.finditer(rf'\[\s*({IDENT})\s*\*\s*(2|4|8|16)\s*\]', text):
                var, scale = hit.groups()
                decl = next((d for d in wide if d['name'] == var and d['type'] in {'u32', 'unsigned int'}), None)
                if (not decl or decl.end() > node.start or not outgoing_dead(flow, node, var)
                        or len(re.findall(rf'\b{var}\b', text)) != 1):
                    continue
                a, b = node.start+hit.start(), node.start+hit.end()
                out.append((FAMILIES[0], edit(source, [(a, b, f'[({var} *= {scale})]')])))
                out.append((FAMILIES[0], edit(source, [(a, b, f'[{var}]'),
                    (node.start, node.start, f'{var} *= {scale};\n')])))
            # Stage a plain indexed-store index in an already dead 32-bit local.
            hit = re.fullmatch(rf'\s*({IDENT})\s*\[\s*({IDENT}\s*\[\s*\d+\s*\])\s*\]\s*=(?!=)([^;]+);\s*', text)
            if hit:
                base = re.match(rf'({IDENT})', hit[2])[1]
                types = re.findall(rf'\b(int|s32|u32|unsigned\s+int)\s*\*\s*{base}\b', part.clean)
                for decl in wide:
                    var = decl['name']
                    scope = block_path(part, decl.start())
                    if (block_path(part, node.start)[:len(scope)] == scope
                            and len(types) == 1 and decl['type'] == types[0]
                            and decl.end() <= node.start and flow.dead(var, node.start)
                            and not re.search(rf'\b{var}\b', text)):
                        out.append((FAMILIES[2], edit(source, [
                            (node.start+hit.start(2), node.start+hit.end(2), var),
                            (node.start, node.start, f'{var} = {hit[2]};\n') ])))
            # Preserve the exact operator association and types; introduce no extra
            # narrowing and never drop a volatile access or change a literal suffix.
            hit = re.fullmatch(rf'\s*({IDENT})\s*=\s*({IDENT})\s*&\s*(-?\([^;]+\)|{IDENT})\s*;\s*', text)
            if hit:
                var, first, rest = hit.groups()
                decl = next((d for d in wide if d['name'] == var), None)
                if (decl and any(d['name'] == first and d['type'] == decl['type'] for d in wide)
                        and decl.end() <= node.start and first != var
                        and not re.search(rf'\b{var}\b', rest)
                        and re.fullmatch(r'[\w\s()&|^~+\-0-9]+', rest)
                        and not re.search(rf'{IDENT}\s*\(', rest)):
                    out.append((FAMILIES[4], edit(source, [(node.start, node.end,
                        f'{var} = {first};\n{var} = {var} & {rest};')])))
    # Scoped pointer handling deliberately permits unrelated volatile reads. Only
    # an unescaped, unqualified local pointer is written, after a CFG-proven last use.
    pointers = []
    for decl in re.finditer(rf'(?<=[;{{}}])\s*(?P<type>(?:s32|u32|int|char|void)\s*\*)\s*(?P<name>{IDENT})\s*(?:=[^;]+)?;', part.clean[part.opening:part.end]):
        # Convert relative matches to source spans, as Flow expects declarations.
        absolute = re.compile(decl.re.pattern).match(part.clean, part.opening+decl.start())
        var = decl['name']
        if (absolute and not re.search(rf'&\s*(?:\(\s*)*{var}\b', part.body)
                and len(re.findall(rf'\b(?:{IDENT}\s+)+\*?\s*{var}\s*(?=[;=,\[])', part.body)) == 1):
            pointers.append(absolute)
    if pointers:
        try:
            pointer_flow = local_flow.Flow(part, pointers)
        except (ValueError, RecursionError):
            pointer_flow = None
        if pointer_flow:
            for node in pointer_flow.nodes:
                if not statement(part, node):
                    continue
                text = part.clean[node.start:node.end]
                for decl in pointers:
                    var = decl['name']
                    if (decl.end() <= node.start and var in node.uses
                            and outgoing_dead(pointer_flow, node, var)
                            and not re.search(rf'\b{var}\s*=\s*0\b', text)):
                        out.append((FAMILIES[1], edit(source, [(node.end, node.end, f'\n{var} = 0;')])))
    # Exact positive constant trip counts only. Moving a tail increment past a
    # continue or accepting an extra update would change behaviour.
    if flow:
        for hit in re.finditer(rf'(?<=[;{{}}])\s*({IDENT})\s*=\s*0\s*;\s*do\s*{{', part.clean[part.opening:part.end]):
            start = part.opening+hit.start()
            opening = part.opening+hit.end()-1
            end = close(part.clean, opening, '{', '}')
            if not end:
                continue
            var = hit[1]
            latch = re.match(rf'\s*while\s*\(\s*{var}\s*<\s*([1-9][0-9]*)\s*\)\s*;', part.clean[end:])
            body = part.clean[opening+1:end-1]
            step = re.search(rf'\b{var}\s*\+\+\s*;\s*$', body)
            decl = next((d for d in locals_for(part) if d['name'] == var and d['type'] in WIDE), None)
            if not latch or not step or not decl or int(latch[1]) > 32767:
                continue
            inner = body[:step.start()]
            if (re.search(r'\b(?:break|continue|goto|switch)\b|(?<!:)\b'+IDENT+r'\s*:(?!:)', inner)
                    or re.search(rf'(?:\+\+|--)\s*{var}\b|\b{var}\s*(?:\+\+|--|[-+*/%&|^]?=)', inner)):
                continue
            original_inner = source[opening+1:opening+1+step.start()]
            out.append((FAMILIES[3], edit(source, [(start, end+latch.end(),
                f'\nfor ({var} = 0; {var} < {latch[1]}; {var}++) {{{original_inner}}}')])))
    return list(dict.fromkeys(out))[:24]
