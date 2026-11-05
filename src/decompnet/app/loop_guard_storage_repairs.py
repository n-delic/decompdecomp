"""Expose a byte loop guard's storage lifetime to old register allocators.

A named bound and comparison result can share storage while the promoted counter survives
into the body. All stores are sequenced C statements; generated sources still need the judge.
"""
import re

from .integer_repair_support import IDENT, INTEGER, edit, locals_for, refs, view
from .pattern_repairs import close

FAMILIES = ('loop_counter_copy', 'loop_guard_storage')


def variants(source, name, asm='', header=''):
    macros = source+'\n'+header
    part = view(source, name)
    if not part or re.search(r'\b(?:volatile|asm|__asm__)\b', part.clean):
        return []
    if re.search(r'(?m)^\s*#\s*(?:if|ifdef|ifndef|else|elif|endif)\b', source):
        return []
    if re.search(r'typedef[^;]*\bvolatile\b[^;]*\bu8\s*;', macros):
        return []
    local = {d['name']: d for d in locals_for(part)}
    guards = list(re.finditer(rf'\bif\s*\(\s*(?P<index>{IDENT})\s*<\s*(?P<bound>{IDENT})\s*\)\s*{{', part.clean[part.opening:part.end]))
    groups = {}
    for guard in guards:
        groups.setdefault((guard['index'], guard['bound']), []).append(guard)
    result = []
    for (index, bound), hits in groups.items():
        if len(hits) != 2 or index not in local or local[index]['type'] not in {'s8', 'signed char'}:
            continue
        if any(re.search(rf'(?m)^\s*#\s*define\s+{binding}\b', macros) for binding in (index, bound)):
            continue
        if re.search(rf'\b(?:{INTEGER}|void|float|double)\s*\**\s*{bound}\s*(?=[;=,\[])', part.body):
            continue
        if bound in local or not re.search(rf'\bextern\s+(?:u8|unsigned\s+char)\s+{bound}\s*;', part.clean[:part.opening]):
            continue
        spans = []
        for hit in hits:
            start = part.opening+hit.start()
            opening = part.opening+hit.end()-1
            if part.clean[part.opening+1:start].rstrip()[-1:] not in {';', '{', '}', ':'}:
                break
            end = close(part.clean, opening, '{', '}')
            if not end:
                break
            equality = re.search(rf'\(\s*(?P<dest>{IDENT})\s*=\s*{IDENT}\s*==\s*{index}\s*\)', part.clean[opening:end])
            spans.append((start, opening, end, equality))
        if len(spans) != 2:
            continue
        corrected = source
        # A stale copy in the first body is a drafting hypothesis, only offered when target
        # instructions independently show a changing byte counter supplying a call argument.
        copies = list(re.finditer(rf'\b(?P<alias>{IDENT})\s*=\s*{index}\s*;', part.clean[part.opening:spans[0][0]]))
        if copies:
            alias = copies[-1]['alias']
            body = part.clean[spans[0][1]:spans[0][2]]
            if alias not in local and re.search(rf'\b{alias}\b', body):
                continue
            if alias in local and re.search(rf'\b{alias}\b', body):
                evidence = re.search(r'addiu\s+(\$\w+),\s*\1,\s*(?:0x)?1\b', asm)
                counter = re.escape(evidence[1]) if evidence else ''
                mask = re.search(r'andi\s+(\$\w+),\s*'+counter+r',\s*0x[Ff][Ff]\b', asm) if evidence else None
                if not mask or not re.search(r'sll\s+\$\w+,\s*'+counter+r',\s*(?:24|0x18)\b', asm):
                    continue
                if not re.search(r'(?:addu|or|move)\s+\$a0,\s*'+re.escape(mask[1])+r'[^\n]*\n(?:[^\n]*\n){0,2}[^\n]*\bjal\b', asm):
                    continue
                if re.search(rf'\b{alias}\s*(?:[+\-*/%&|^]?=|\+\+|--|\()|&\s*{alias}\b|(?:\.|->)\s*{alias}\b', body):
                    continue
                if re.search(rf'(?m)^\s*#\s*define\s+{alias}\b', macros):
                    continue
                a, b = spans[0][1], spans[0][2]
                uses = [hit for hit in refs(part, local[alias]) if a <= hit.start() < b]
                corrected = edit(source, [(hit.start(), hit.end(), index) for hit in uses])
        # Reparse after correcting a potentially different length alias.
        if corrected != source:
            result.append(('loop_counter_copy', corrected))
        current = view(corrected, name)
        matches = list(re.finditer(rf'\bif\s*\(\s*{index}\s*<\s*{bound}\s*\)\s*{{', current.clean[current.opening:current.end]))
        if len(matches) != 2:
            continue
        storage = '__repair_guard'
        while re.search(rf'\b{storage}\b', current.clean):
            storage += '_'
        for selected in ((1,), ()):
            edits = [(local[index].start(), local[index].start(), f's32 {storage};\n  ')]
            for ordinal, hit in enumerate(matches):
                start = current.opening+hit.start()
                opening = current.opening+hit.end()-1
                end = close(current.clean, opening, '{', '}')
                edits.append((start, opening, f'{storage} = {bound};\n  if (({storage} = {index} < {storage}))\n  '))
                if ordinal in selected:
                    eq = re.search(rf'\(\s*(?P<dest>{IDENT})\s*=\s*{IDENT}\s*==\s*{index}\s*\)', current.clean[opening:end])
                    tail = current.clean[opening+eq.end():end] if eq else ''
                    # Only rewrite a scratch result whose following branch overwrites it
                    # unconditionally before any read (typically a captured call result).
                    overwritten = eq and re.match(r'[^;{}]*\{\s*'+re.escape(eq['dest'])+r'\s*=(?!=)', tail)
                    branch = opening+eq.end()+tail.index('{') if overwritten else None
                    branch_end = close(current.clean, branch, '{', '}') if branch is not None else None
                    if eq and (re.search(rf'\b{eq["dest"]}\b', tail[:tail.find('{')])
                               or not branch_end or re.search(rf'\b{eq["dest"]}\b', current.clean[branch_end:current.end])
                               or re.search(rf'(?m)^\s*#\s*define\s+{eq["dest"]}\b', macros)):
                        break
                    if not overwritten or eq['dest'] not in local:
                        break
                    if eq and eq['dest'] in local:
                        edits.append((opening+eq.start('dest'), opening+eq.end('dest'), storage))
            else:
                candidate = edit(corrected, edits)
                if candidate != source and ('loop_guard_storage', candidate) not in result:
                    result.append(('loop_guard_storage', candidate))
                continue
            continue
    return result[:10]
