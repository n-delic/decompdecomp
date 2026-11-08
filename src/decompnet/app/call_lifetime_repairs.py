"""Bounded lexical lifetimes and scalar call staging; every candidate needs the exact judge.

No inline helpers, inferred pointer types or moved side effects. Unsupported C stays untouched.
"""
import re

from ..forks.csource import _blank
from .integer_repair_support import IDENT, NUMBER, edit, locals_for, split_args, view
from .pattern_repairs import close

FAMILIES = ('branch_tail_lifetimes', 'staged_call_arguments', 'combined_call_lifetimes')
LIMIT = 8


def block_path(part, position):
    stack = []
    for offset in range(part.opening,position):
        if part.clean[offset] == '{':
            stack.append(offset)
        elif part.clean[offset] == '}' and stack:
            stack.pop()
    return stack


def tails(source, name, header=''):
    macros = set(re.findall(r'^\s*#\s*define\s+(\w+)',source+'\n'+header,re.M))
    part = view(source, name)
    if not part or re.search(r'\b(?:volatile|asm|__asm__|goto|case|default|for|while|do)\b|(?<!:):(?!:)', part.body):
        return []
    result = []
    for hit in re.finditer(r'\bif\s*\(', part.body):
        start = part.opening+1+hit.start()
        test_end = close(part.clean, part.opening+1+hit.end()-1)
        if not test_end:
            continue
        arm = re.match(r'\s*{', part.clean[test_end:])
        if not arm:
            continue
        a = test_end+arm.end()-1
        b = close(part.clean, a, '{', '}')
        other = re.match(r'\s*else\s*{', part.clean[b:]) if b else None
        if not other:
            continue
        c = b+other.end()-1
        d = close(part.clean, c, '{', '}')
        if not d or d >= part.end-1:
            continue
        tail = part.clean[d:part.end-1]
        if macros & set(re.findall(IDENT,part.clean[a:d]+tail)):
            continue
        # A terminal straight-line call pair, optionally ending with a return. Never duplicate
        # nested blocks, declarations, labels, loops or control that can escape its new scope.
        if re.search(r'[{}]|\b(?:if|break|continue)\b', tail):
            continue
        statements = re.findall(r'[^;]+;', tail)
        if ''.join(statements).strip() != tail.strip() or not 2 <= len(statements) <= 4:
            continue
        if not all(re.fullmatch(rf'\s*(?:{IDENT}\s*\([^;]*\)|return(?:\s+[^;]+)?);', s) for s in statements):
            continue
        if sum(bool(re.match(rf'\s*{IDENT}\s*\(', s)) for s in statements) != 2:
            continue
        for decl in locals_for(part):
            var = decl['name']
            if decl.start() >= start or block_path(part,decl.start()) != [part.opening]:
                continue
            arms = [part.clean[a+1:b-1], part.clean[c+1:d-1]]
            firsts = [re.match(rf'\s*{re.escape(var)}\s*=(?!=)([^;{{}}]+);', body) for body in arms]
            if not all(firsts) or any(re.search(rf'\b{re.escape(var)}\b', first[1]) for first in firsts):
                continue
            # Every use must move with the arms/tail; keep parameter/global/member names intact.
            outside = part.clean[part.opening+1:decl.start()]+part.clean[decl.end():start]
            condition = part.clean[start:a]
            if re.search(rf'\b{re.escape(var)}\b', outside+condition):
                continue
            if not re.search(rf'\b{re.escape(var)}\b', tail):
                continue
            fresh = '__repair_branch_value'
            if fresh in part.clean:
                continue
            def block(left, right, fresh, d=d, var=var, typ=decl['type']):
                text = source[left+1:right-1]+source[d:part.end-1]
                # Reject member-name collisions instead of treating member tokens as locals.
                if re.search(rf'(?:\.|->)\s*{re.escape(var)}\b', text):
                    return None
                text = edit(text, [(m.start(),m.end(),fresh) for m in re.finditer(rf'\b{re.escape(var)}\b', _blank(text))])
                return '{\n'+typ+' '+fresh+';\n'+text+'\n}'
            left, right = block(a,b,fresh+'_0'), block(c,d,fresh+'_1')
            if left and right:
                replacement = source[start:a]+left+source[b:c]+right
                result.append(('branch_tail_lifetimes', edit(source, [(decl.start(),decl.end(),''), (start,part.end-1,replacement)])))
            if len(result) >= 2:
                return result
    return result


def staged(source, name, header=''):
    macros = set(re.findall(r'^\s*#\s*define\s+(\w+)',source+'\n'+header,re.M))
    part = view(source, name)
    if not part or re.search(r'\b(?:volatile|asm|__asm__|goto)\b', part.clean):
        return []
    declarations = {d['name']: d for d in locals_for(part)
                    if not re.search(rf'\b{re.escape(d["name"])}\b',part.clean[:part.opening])}
    result = []
    for hit in re.finditer(rf'(?<=[;{{}}])\s*({IDENT})\s*\(', part.body):
        if hit[1] in {'if','while','for','switch','sizeof'} | macros or hit[1] in declarations:
            continue
        start = part.opening+1+hit.start()
        opening = part.opening+hit.end()
        end = close(part.clean, opening)
        if not end or not re.match(r'\s*;', part.clean[end:]):
            continue
        text = part.clean[opening+1:end-1]
        args = [text[a:b].strip() for a,b in split_args(text)]
        # Only unescaped integer locals and signed int literals. This preserves evaluation values
        # and types without choosing an order for calls, loads or other side effects.
        types = []
        for arg in args:
            if arg in declarations:
                decl = declarations[arg]
                path = block_path(part,decl.start())
                if decl.start() >= start or block_path(part,start)[:len(path)] != path:
                    break
                types.append(decl['type'])
            elif re.fullmatch(NUMBER, arg) and not re.search(r'[uUlL]$',arg) and int(arg,0 if arg.startswith(('0x','0X')) else 10) < 2**31:
                types.append('int')
            else:
                break
        if len(types) != len(args) or not 2 <= len(args) <= 8 or not any(arg in declarations for arg in args):
            continue
        if '__repair_call_arg' in part.clean:
            continue
        # Avoid for-header semicolons and calls nested in conditions or argument expressions.
        prefix = part.body[:hit.start()]
        if prefix.count('(') != prefix.count(')'):
            continue
        suffix = re.match(r'\s*;', part.clean[end:])
        names = [f'__repair_call_arg{i}' for i in range(len(args))]
        block = '{\n'+''.join(f'{typ} {var};\n' for typ,var in zip(types,names))
        block += ''.join(f'{var} = {arg};\n' for var,arg in zip(names,args))
        block += hit[1]+'('+', '.join(names)+');\n}'
        result.append(('staged_call_arguments', edit(source,[(start,end+suffix.end(),block)])))
        if len(result) >= 3:
            break
    return result


def variants(source, name, header=''):
    branches = tails(source,name,header)
    result = branches+staged(source,name,header)
    for _, first in branches:
        for _, second in staged(first,name,header):
            result.append(('combined_call_lifetimes',second))
            if len(result) >= LIMIT:
                return result
    return result
