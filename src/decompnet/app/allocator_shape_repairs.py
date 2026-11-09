"""Small allocator-facing C shapes. Every candidate still needs the ordinary judge."""
import re

from .integer_repair_support import IDENT, NUMBER, edit, locals_for, view
from .pattern_repairs import close

FAMILIES = ('constant_index_temporary', 'balanced_call_references', 'balanced_pointer_calls')


def indices(source, name):
    part = view(source, name)
    fresh = '__repair_constant_index'
    if not part or fresh in source or re.search(r'\b(?:asm|__asm__)\b',part.body):
        return []
    # Pointer stores only; keep the literal, RHS, pointer evaluation and array bounds.
    pointers = set(re.findall(rf'\b(?:[su]32|int|unsigned int)\s*\*\s*({IDENT})\b',part.body))
    hits = [h for h in re.finditer(rf'(?<![\w.>])({IDENT})\s*\[\s*0\s*\]\s*=\s*({NUMBER})\s*;',part.body)
            if h[1] in pointers and part.body[:h.start()].count('(')==part.body[:h.start()].count(')')]
    result = []
    for hit in reversed(hits[-4:]):
        a,b = part.opening+1+hit.start(),part.opening+1+hit.end()
        statement = source[a:b]
        indexed = re.sub(r'\[\s*0\s*\]', '['+fresh+']',statement,count=1)
        for local in (False, True):
            replacement = '{'+('int '+fresh+';' if local else '')+fresh+'=0;'+indexed+'}'
            edits = [(a,b,replacement)]
            if not local:
                edits.append((part.opening+1,part.opening+1,'\nint '+fresh+';'))
            result.append(edit(source,edits))
    # A grouped pair can cross an allocation plateau without a long beam.
    for pair in (hits[-2:],hits[-4:-2]):
        if len(pair) != 2:
            continue
        edits = [(part.opening+1,part.opening+1,'\nint '+fresh+';')]
        for hit in pair:
            a,b = part.opening+1+hit.start(),part.opening+1+hit.end()
            statement = re.sub(r'\[\s*0\s*\]', '['+fresh+']',source[a:b],count=1)
            edits.append((a,b,'{'+fresh+'=0;'+statement+'}'))
        result.append(edit(source,edits))
    return list(dict.fromkeys(result))[:10]


def balanced_calls(source, name, header=''):
    macros = set(re.findall(r'^\s*#\s*define\s+(\w+)',source+'\n'+header,re.M))
    part = view(source, name)
    marker = '__repair_call_balance'
    if not part or marker in source or re.search(r'\b(?:asm|__asm__|goto|case|default)\b',part.body):
        return []
    declarations = {d['name']:d for d in locals_for(part)}
    # A literal initialization in the outer scope establishes the added guard
    # read on every path. Never add a read of a volatile/escaped/unknown value.
    guards = []
    for hit in re.finditer(rf'\b({IDENT})\s*=\s*{NUMBER}\s*;',part.body):
        prefix = part.body[:hit.start()].rstrip()
        if (hit[1] in declarations and declarations[hit[1]]['type'] in {'int','s32','u32','unsigned int'} and prefix.count('{')==prefix.count('}')
                and (not prefix or prefix[-1] in ';{}')):
            if not any(key==hit[1] for key,_ in guards):
                guards.append((hit[1],hit.end()))
    calls = []
    for hit in re.finditer(rf'(?<=[;{{}}])\s*({IDENT})\s*\(',part.body):
        if hit[1] in {'if','while','for','switch','sizeof'} | macros or hit[1] in declarations:
            continue
        prefix = part.body[:hit.start()]
        if prefix.count('(')!=prefix.count(')'):
            continue
        opening = part.opening+hit.end()
        end = close(part.clean,opening)
        suffix = re.match(r'\s*;',part.clean[end:]) if end else None
        if suffix:
            calls.append((hit[1],part.opening+1+hit.start(),end+suffix.end()))
    from collections import Counter
    counts = Counter(c[0] for c in calls)
    pools = []
    for guard,initialized in guards[:2]:
        pool = []
        available = sorted([c for c in calls if c[1] >= part.opening+1+initialized], key=lambda c:-counts[c[0]])[:6]
        groups = [[c] for c in available]
        groups += [[a,b] for a,b in zip(available,available[1:]) if a[0]==b[0]]
        for group in groups:
            edits = []
            for _,a,b in group:
                statement = source[a:b]
                edits.append((a,b,f'/*{marker}*/if({guard}){{{statement}}}else{{{statement}}}'))
            pool.append(edit(source,edits))
        pools.append(pool[:8])
    from itertools import zip_longest
    return [candidate for row in zip_longest(*pools) for candidate in row if candidate is not None][:16]


def variants(source, name, header=''):
    from .allocator_call_guards import variants as pointer_calls
    return [('constant_index_temporary',s) for s in indices(source,name)]+[
        ('balanced_call_references',s) for s in balanced_calls(source,name,header)]+pointer_calls(source,name,header)
