"""Small source hypotheses isolated from captured repairs; only the exact judge may keep them."""

import re

from ..forks.csource import _blank, find
from . import narrow_chains

IDENT = r'[A-Za-z_]\w*'
LIMIT = 6


def variants(source: str, name: str, arch: str) -> list[str]:
    span = find(source, name)
    if not span:
        return []
    clean = _blank(source)
    definition = re.search(r'\b' + re.escape(name) + r'\s*\([^{};]*\)\s*{', clean[span[0]:span[1]])
    if not definition:
        return []
    opening = span[0] + definition.end() - 1
    if '#' in clean[opening:span[1]]:
        return []
    body = clean[opening:span[1]]
    result = narrow_chains.variants(source, name)

    def edit(start, end, replacement):
        candidate = source[:start] + replacement + source[end:]
        if candidate not in result:
            result.append(candidate)

    # A narrowing assignment can produce different scheduling from an explicit mask. These are hypotheses,
    # not claims of C equivalence (promotion/signedness can differ). Leave nested/side-effecting expressions out.
    mask = re.compile(rf'\(\s*(?P<x>{IDENT}(?:\s*(?:->|\.)\s*{IDENT})*)\s*(?P<op>[+-])\s*(?P<n>{IDENT}|0[xX][0-9a-fA-F]+|[0-9]+)\s*\)'
                      r'\s*&\s*(?P<mask>0[xX](?:[fF]{4}|[fF]{2}))(?![\w.])')
    for m in mask.finditer(body):
        typ = 'unsigned short' if len(m['mask']) == 6 else 'unsigned char'
        edit(opening + m.start(), opening + m.end(), f'({typ})({m["x"]} {m["op"]} {m["n"]})')
        if len(result) >= 2:
            break

    # Coalesce a narrow copy temporary with its wider source, then try the final masked
    # subtraction as a cast. Require one simple copy assignment, no other writes to the copy.
    narrow = re.compile(rf'\b(?:s32|int)\s+(?P<wide>{IDENT})\s*;\s*(?P<t>u16|u8)\s+(?P<small>{IDENT})\s*;')
    for decl in narrow.finditer(body):
        assignment = list(re.finditer(r'\b'+re.escape(decl['small'])+r'\s*=\s*'+re.escape(decl['wide'])+r'\s*;',body))
        writes = re.findall(r'\b'+re.escape(decl['small'])+r'\s*=(?!=)',body)
        if len(assignment)!=1 or len(writes)!=1:
            continue
        copy = assignment[0]
        if copy.start() < decl.end():
            continue
        changed = source[:opening+copy.start()]+source[opening+copy.end():]
        changed = changed[:opening+decl.start()]+f"{decl['t']} {decl['wide']};"+changed[opening+decl.end():]
        # Keep declarations and edits restricted to this definition, not neighbouring functions.
        stop = span[1]-(copy.end()-copy.start())-(decl.end()-decl.start())+len(f"{decl['t']} {decl['wide']};")
        region = changed[opening:stop]
        region = re.sub(r'\b'+re.escape(decl['small'])+r'\b',decl['wide'],region)
        region = re.sub(r'\(([^;{}()]+-[^;{}()]+)\)\s*&\s*0x[Ff]{4}\b',r'(u16)(\1)',region) if decl['t']=='u16' else region
        candidate = changed[:opening]+region+changed[stop:]
        if candidate not in result:
            result.append(candidate)
        break

    if arch in {'mips', 'ppc', 'x86', 'arm', 'thumb'}:
        # A scaled typed-pointer addition can schedule differently when written as integer address
        # arithmetic. Only fixed-width element types and simple operands qualify, on 32-bit targets.
        # Keep each operand evaluated once; the linked/objdiff judge must verify every hypothesis.
        scaled = re.compile(r'\(\s*(?P<type>[su](?:8|16|32))\s*\*\s*\)\s*'
                            rf'(?P<base>{IDENT})\s*\+\s*(?P<index>{IDENT})(?!\w|\s*\()')
        for m in scaled.finditer(body):
            tail = body[m.end():].lstrip()
            if not tail.startswith((';', ')', ',', ']')) or tail.startswith(('++', '--')):
                continue
            scale = int(m['type'][1:]) // 8
            edit(opening + m.start(), opening + m.end(),
                 f'({m["type"]} *)({m["index"]} * {scale} + (int){m["base"]})')
            if len(result) >= 4:
                break

        # A symbol's address is different from reading its value. Only explicit &symbol or a declared
        # array decay qualifies. Never convert a scalar/pointer global load to its label's numeric address.
        arrays = set(re.findall(r'\bextern\s+(?:volatile\s+|const\s+)?(?:unsigned\s+)?\w+\s+'
                                r'(D_[0-9A-Fa-f]{8})\s*\[\s*\]\s*;', clean[:opening]))
        address = re.compile(r'\(\s*(?:u32|s32|unsigned\s+int|int)\s*\)\s*'
                             r'(?P<wrap>\()?\s*(?P<amp>&\s*)?(?P<sym>D_[0-9A-Fa-f]{8})(?!\w)\s*(?(wrap)\))')
        for m in address.finditer(body):
            if m['amp'] or m['sym'] in arrays:
                edit(opening + m.start(), opening + m.end(), '0x' + m['sym'][2:])
                if len(result) >= 4:
                    break

    # Cache a pointer-valued global at the start of a local block, before an intervening store can alias
    # it. This intentionally explores load order; exact verification, not a heuristic, decides correctness.
    globals_ = list(re.finditer(r'\bextern\s+(?P<type>(?:void|char|u8|s8|s32|u32)\s*\*)\s*'
                               rf'(?P<name>{IDENT})\s*;', clean[:opening]))
    for declaration in globals_:
        global_name = declaration['name']
        call = re.compile(rf'\b{IDENT}\s*\(\s*(?P<arg>{re.escape(global_name)})\s*,')
        for m in call.finditer(body):
            stack = []
            for pos, char in enumerate(body[:m.start()]):
                if char == '{':
                    stack.append(pos)
                elif char == '}' and stack:
                    stack.pop()
            if not stack or '=' not in body[stack[-1] + 1:m.start()]:
                continue
            variable = '__repair_cached_pointer'
            if re.search(r'\b' + variable + r'\b', clean):
                continue
            at = opening + stack[-1] + 1
            candidate = source[:opening + m.start('arg')] + variable + source[opening + m.end('arg'):]
            candidate = candidate[:at] + f'\n{declaration["type"]}{variable} = {global_name};\n' + candidate[at:]
            if candidate not in result:
                result.append(candidate)
            break
        if len(result) >= LIMIT:
            break
    # Inlining even a simple sum can change old compilers' register allocation. Only ordinary
    # signed integer locals qualify: no loads, calls, pointer arithmetic or floating expressions.
    # A compiler which leaves a call instead will fail the normal exact judge.
    helper = '__repair_inline_sum'
    if arch in {'mips', 'ppc', 'x86', 'arm', 'thumb'} and not re.search(r'\b' + helper + r'\b', clean):
        locals_ = {m[1] for m in re.finditer(r'(?:^|(?<=[;{}]))\s*(?:s32|int)\s+(' + IDENT + r')\s*;', body)}
        for m in re.finditer(r'\b(?P<lhs>' + IDENT + r')\s*=\s*(?P<a>' + IDENT + r')\s*\+\s*'
                             r'(?P<b>' + IDENT + r')\s*;', body):
            if not {m['lhs'], m['a'], m['b']} <= locals_:
                continue
            prefix = f'static inline int {helper}(int left, int right)\n{{\n  return left + right;\n}}\n\n'
            candidate = (prefix + source[:opening+m.start()] +
                         f'{m["lhs"]} = {helper}({m["a"]}, {m["b"]});' + source[opening+m.end():])
            if candidate not in result:
                result.append(candidate)
            if len(result) >= LIMIT:
                break
    return result[:LIMIT]
