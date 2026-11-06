"""Keep narrow increment rounding inside expressions, as observed with older GCC.

Only adjacent statements and unescaped scalar locals are considered. These remain
compiler-judged hypotheses; they do not alter a shared header or compiler profile.
"""
import re

from .integer_repair_support import IDENT, edit, locals_for, view

FAMILIES = ('prefix_increment_result', 'postincrement_store')


def variants(source, name):
    part = view(source, name)
    if not part or re.search(r'\b(?:volatile|asm|__asm__)\b', part.clean):
        return []
    out = []
    for decl in locals_for(part):
        variable = decl['name']
        v = re.escape(variable)
        increment = rf'{v}\s*(?:\+=\s*1|=\s*{v}\s*\+\s*1)\s*;'
        # Require a statement boundary: rewriting the body of an unbraced if would
        # otherwise bring the second statement under its condition.
        boundary = r'(?<=[;{}])\s*'
        prefix = rf'{boundary}{increment}\s*(?P<dest>{IDENT})\s*=\s*{v}\s*;'
        postfix = rf'{boundary}(?P<dest>{IDENT}(?:\s*->\s*{IDENT})?)\s*=\s*{v}\s*;\s*{increment}'
        for family, pattern in zip(FAMILIES, (prefix, postfix)):
            for hit in re.compile(pattern).finditer(part.clean, part.opening, part.end-1):
                dest = hit['dest']
                if variable in re.findall(IDENT, dest):
                    continue
                # A plain destination must itself be an unescaped local. Member
                # stores are allowed only for postfix; no calls/index side effects.
                if '->' not in dest and dest not in {d['name'] for d in locals_for(part)}:
                    continue
                expr = f'++{variable}' if family == FAMILIES[0] else f'{variable}++'
                out.append((family, edit(source, [(hit.start(), hit.end(), f'\n{dest} = {expr};')])))
                if len(out) >= 8:
                    return out
    return list(dict.fromkeys(out))
