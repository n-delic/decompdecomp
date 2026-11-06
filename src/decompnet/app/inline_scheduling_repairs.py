"""Bounded GCC scheduling hypotheses; all candidates still require the ordinary exact judge.

Inline identities expose compiler front-end choices without adding an out-of-line call.
The helpers are deliberately typed, private and limited to side-effect-free scalar operands.
"""
import re

from .integer_repair_support import IDENT, NUMBER, edit, locals_for, view
from .pattern_repairs import close

FAMILIES = ('inline_scalar_shape', 'expression_placement', 'stack_address_temporary',
            'structured_backedge', 'loop_invariant_placement', 'scalar_schedule_order', 'combined_scheduling')


def comparison_literal(text):
    text = text.strip().strip('()').replace(' ', '')
    if re.search(r'[uUlL]$',text):
        return False
    try:
        value = int(text, 16 if '0x' in text.lower() else 10)
    except ValueError:
        return False
    return -(2**31) <= value < 2**31


def singles(source, name):
    part = view(source, name)
    if not part or re.search(r'\b(?:volatile|asm|__asm__)\b', part.clean):
        return []
    declarations = {d['name']: d for d in locals_for(part)}
    result = []

    def helper(start, end, expression, typ, arguments, body):
        fresh = '__repair_inline'
        if fresh in source:
            return
        # Prepend outside the function; never nest a function definition or change other units.
        definition = f'static __inline__ {typ} {fresh}({arguments}) {{ return {body}; }}\n'
        result.append(('inline_scalar_shape', definition+edit(source, [(start,end,expression)])))

    for hit in list(re.finditer(rf'\b({IDENT})\s*(<|>)\s*(-?\s*{NUMBER})', part.body))[:4]:
        local = declarations.get(hit[1])
        if local is None or local['type'] not in {'int', 's32', 'signed int'} or not comparison_literal(hit[3]):
            continue
        typ = local['type']
        a = part.opening+1+hit.start()
        helper(a,part.opening+1+hit.end(), f'__repair_inline({hit[1]}, {hit[3]})',
               'int', f'{typ} a, {typ} b', f'a {hit[2]} b')
        helper(a,part.opening+1+hit.end(), f'__repair_inline({hit[3]}, {hit[1]})',
               'int', f'{typ} a, {typ} b', 'a '+('<' if hit[2] == '>' else '>')+' b')
    # Repeated axis/bounds tests often need the same front-end shape together.
    for op, reverse in (('<','>'), ('>','<')):
        hits = list(re.finditer(rf'\b({IDENT})\s*{op}\s*((?:\(\s*-?\s*{NUMBER}\s*\))|(?:-?\s*{NUMBER}))',part.body))
        for typ in dict.fromkeys(d['type'] for d in declarations.values()):
            if typ not in {'int', 's32', 'signed int'}:
                continue
            matching = [h for h in hits if h[1] in declarations and declarations[h[1]]['type'] == typ and comparison_literal(h[2])]
            if not 2 <= len(matching) <= 8 or '__repair_inline' in source:
                continue
            edits = [(part.opening+1+h.start(),part.opening+1+h.end(),f'__repair_inline({h[2]}, {h[1]})') for h in matching]
            definition = f'static __inline__ int __repair_inline({typ} a, {typ} b) {{ return a {reverse} b; }}\n'
            result.insert(0,('inline_scalar_shape',definition+edit(source,edits)))
    # A typed identity around a field load can alter register preference and scheduling.
    for hit in list(re.finditer(rf'\b({IDENT})\s*=\s*([^;{{}}]+);', part.body))[:64]:
        local = declarations.get(hit[1])
        expr = hit[2].strip()
        if local is None or not expr.startswith('*') or re.search(r'\+\+|--|=(?!=)|\b'+IDENT+r'\s*\(',expr) or not re.fullmatch(r'[\w\s*()+.>\[\]-]+',expr):
            continue
        a = part.opening+1+hit.start(2)
        helper(a,part.opening+1+hit.end(2),f'__repair_inline({expr})',
               local['type'],f'{local["type"]} a','a')
    # Keep floating operations floating; do not silently introduce integer conversion.
    floats = set(re.findall(rf'\b(?:float|f32)\s+({IDENT})\s*;', part.body))
    for hit in list(re.finditer(rf'\b({IDENT})\s*\*\s*(\d+\.\d+[fF])', part.body))[:4]:
        if hit[1] in floats:
            helper(part.opening+1+hit.start(),part.opening+1+hit.end(),
                   f'__repair_inline({hit[1]})','float','float a',f'a * {hit[2]}')
    # A fresh local evaluates an integer expression at precisely the same statement boundary.
    # Inserting a block also works for an unbraced if arm; no expression crosses a call/store.
    for hit in re.finditer(rf'(?<=[;{{}}])\s*({IDENT})\s*=\s*({IDENT})\s*([+*])\s*({IDENT}|{NUMBER})\s*;', part.body):
        if part.body[:hit.start()].count('(') != part.body[:hit.start()].count(')'):
            continue
        lhs, left, op, right = hit.groups()
        if lhs not in declarations or left not in declarations or (right not in declarations and not re.fullmatch(NUMBER,right)):
            continue
        typ = declarations[lhs]['type']
        if declarations[left]['type'] != typ or (right in declarations and declarations[right]['type'] != typ):
            continue
        fresh = '__repair_value'
        if fresh in source:
            continue
        a,b = part.opening+1+hit.start(),part.opening+1+hit.end()
        result.append(('expression_placement',edit(source,[(a,b,f'{{ {typ} {fresh}; {fresh} = {left} {op} {right}; {lhs} = {fresh}; }}')])) )
    # Preserve array extent: sizeof/aliasing rules must not be guessed to adjust a frame.
    arrays = {}
    for decl in re.finditer(rf'\b(s32|u32|int|float|f32)\s+({IDENT})\s*\[\s*\d+\s*\]\s*;',part.body):
        arrays[decl[2]] = decl[1]
    for hit in re.finditer(rf'(?<=[;{{}}])\s*({IDENT})\s*\(([^;{{}}]*)\)\s*;',part.body):
        if part.body[:hit.start()].count('(') != part.body[:hit.start()].count(')'):
            continue
        for address in re.finditer(rf'&\s*({IDENT})\s*\[\s*0\s*\]',hit[2]):
            array = address[1]
            if array not in arrays or '__repair_address' in source:
                continue
            call = source[part.opening+1+hit.start():part.opening+1+hit.end()].strip()
            old = address[0]
            if call.count(old) != 1:
                continue
            replacement = '{ '+arrays[array]+' *__repair_address; __repair_address = '+old+'; '+call.replace(old,'__repair_address')+' __repair_address = 0; }'
            result.append(('stack_address_temporary',edit(source,[(part.opening+1+hit.start(),part.opening+1+hit.end(),replacement)])))
    from .structured_loop_repairs import variants as structured_loops
    result.extend(structured_loops(source,name))
    # Swap only adjacent assignments of unescaped equal-type locals and pure scalar operands.
    scalar = rf'{IDENT}(?:\s*[+*]\s*(?:{IDENT}|{NUMBER}))?'
    pair = rf'(?<=[;{{}}])\s*({IDENT})\s*=\s*({scalar});\s*({IDENT})\s*=\s*({scalar});'
    for hit in re.finditer(pair,part.body):
        if part.body[:hit.start()].count('(') != part.body[:hit.start()].count(')'):
            continue
        a,x,b,y = hit.groups()
        refs = set(re.findall(IDENT,x+' '+y))
        if a == b or any(v not in declarations for v in refs | {a,b}) or a in refs or b in refs:
            continue
        if len({declarations[v]['type'] for v in refs | {a,b}}) != 1:
            continue
        result.append(('scalar_schedule_order',edit(source,[(part.opening+1+hit.start(),part.opening+1+hit.end(),f'\n{b} = {y}; {a} = {x};')])) )
    # Hoist a first, pure invariant assignment out of a do loop (the body always runs once).
    for loop in re.finditer(r'\bdo\s*{',part.body):
        opening = part.opening+1+loop.end()-1
        end = close(part.clean,opening,'{','}')
        if not end:
            continue
        first = re.match(rf'\s*({IDENT})\s*=\s*({scalar});',part.clean[opening+1:end-1])
        if not first:
            continue
        lhs,expr = first.groups()
        refs = set(re.findall(IDENT,expr))
        rest = part.clean[opening+1+first.end():end-1]
        if any(v not in declarations for v in refs | {lhs}) or lhs in refs or re.search(r'\b(?:goto|break|continue)\b',rest):
            continue
        if any(re.search(rf'\b{re.escape(v)}\s*(?:=(?!=)|[+*/%&|^-]=|\+\+|--)|(?:\+\+|--)\s*\b{re.escape(v)}\b',rest) for v in refs | {lhs}):
            continue
        a = part.opening+1+loop.start()
        result.append(('loop_invariant_placement',edit(source,[(a,a,f'{lhs} = {expr};\n'),(opening+1,opening+1+first.end(),'')])) )
    # Round-robin families so repeated scalar sites cannot crowd out stack/loop proposals.
    pools = {}
    for family, candidate in dict.fromkeys(result):
        pools.setdefault(family, []).append(candidate)
    selected = []
    while any(pools.values()) and len(selected) < 24:
        for family, pool in pools.items():
            if pool and len(selected) < 24:
                selected.append((family, pool.pop(0)))
    return selected


def variants(source, name):
    result = singles(source,name)
    # Include a few cross-family bridges even when either first step compiles identically
    # or temporarily worse. The existing beam judges and retains those alternatives safely.
    combined = []
    for family, first in result[:8]:
        for other, second in singles(first,name):
            if other != family:
                combined.append(('combined_scheduling',second))
                break
        if len(combined) == 4:
            break
    return list(dict.fromkeys(result+combined))
