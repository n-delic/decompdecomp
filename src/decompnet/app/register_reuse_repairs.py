"""Small def/use hypotheses for register-only near misses, always checked by the normal judge.

A nearly identical instruction stream can still read an undefined temporary or keep a redundant copy
live. These source candidates expose constant/copy reuse and an in-place final shift to old allocators.
They do not prescribe machine registers or edit the generated object.
"""
import re

from . import loop_liveness
from .integer_repair_support import IDENT, INTEGER, NUMBER, edit, refs, view

LIMIT = 16
DECL = re.compile(rf'\b(?P<type>{INTEGER})\s+(?P<name>{IDENT})\s*(?:=\s*(?P<value>{NUMBER}))?\s*;')


def variants(source, name):
    part = view(source, name)
    if not part or re.search(r'\b(?:volatile|asm|__asm__)\b', part.clean):
        return []
    locals_ = []
    for decl in DECL.finditer(part.clean, part.opening+1, part.end):
        prefix = part.clean[part.opening+1:decl.start()].rstrip()
        if prefix and prefix[-1] not in ';{}':
            continue
        variable = decl['name']
        shadows = re.findall(rf'\b(?:{INTEGER}|void|float|double)\s*\**\s*{variable}\s*(?=[;=,\[])', part.body)
        if len(shadows) != 1 or re.search(rf'&\s*(?:\(\s*)*{variable}\b', part.body):
            continue
        locals_.append(decl)
    result = []

    def add(family, edits):
        candidate = edit(source, edits)
        if candidate != source and (family, candidate) not in result and len(result) < LIMIT:
            result.append((family, candidate))

    for decl in locals_:
        hits = refs(part, decl)
        if not hits:
            continue
        variable = decl['name']
        writes = [hit for hit in hits if re.match(r'\s*(?:=(?!=)|[-+*/%&|^]?=|\+\+|--)', part.clean[hit.end():])
                  or re.search(r'(?:\+\+|--)\s*$', part.clean[:hit.start()])]
        # A read before any definition is a drafting mistake, not evidence that its arbitrary register
        # value is correct. Try an already initialized, same-type constant without changing the header.
        if decl['value'] is None:
            stop = writes[0].start() if writes else part.end
            early = [hit for hit in hits if hit.start() < stop]
            for constant in locals_:
                if not early or constant['value'] is None or constant['type'] != decl['type'] or constant.end() > early[0].start():
                    continue
                if any(re.match(r'\s*(?:[+\-*/%&|^]?=|\+\+|--)', part.clean[h.end():])
                       for h in refs(part, constant) if constant.end() < h.start() < early[-1].end()):
                    continue
                add('constant_register_reuse', [(h.start(), h.end(), constant['name']) for h in early])
        # Reuse a named copy only while its source remains unchanged. Keep the assignment and every
        # evaluation; one selected use at a time avoids a Cartesian product of aliases.
        for assignment in re.finditer(rf'\b{variable}\s*=\s*({IDENT})\s*;', part.body):
            end = part.opening+1+assignment.end()
            origin = next((d for d in locals_ if d['name'] == assignment[1] and d['type'] == decl['type']), None)
            if origin is None or origin['name'] == variable:
                continue
            selected = []
            for hit in hits:
                if hit.start() < end:
                    continue
                gap = part.clean[end:hit.start()]
                if re.search(rf'\b(?:{variable}|{origin["name"]})\s*(?:[+\-*/%&|^]?=|\+\+|--)|'
                             rf'(?:\+\+|--)\s*(?:{variable}|{origin["name"]})\b|\b(?:goto|for|while|do|case|default)\b', gap):
                    break
                if re.match(r'\s*(?:[+\-*/%&|^]?=|\+\+|--)', part.clean[hit.end():]):
                    break
                selected.append((hit.start(), hit.end(), origin['name']))
                add('copy_register_reuse', [(hit.start(), hit.end(), origin['name'])])
            if len(selected) > 1:
                add('copy_register_reuse', selected)
            # If this is the entire copy's lifetime, also try eliminating its now-dead declaration
            # and assignment. Early reads or another write keep those statements intact.
            assignment_start = part.opening+1+assignment.start()
            covered = {a for a, _, _ in selected} | {assignment_start}
            prefix = part.clean[part.opening+1:assignment_start].rstrip()
            if (selected and decl['value'] is None and prefix[-1:] in {';', '{', '}'}
                    and all(hit.start() in covered for hit in hits)):
                add('copy_register_reuse', selected+[(assignment_start, end, ''), (decl.start(), decl.end(), '')])
        # In a loop, a final textual use is reusable only when a direct reset dominates it on
        # every iteration and neither the header nor an earlier body read observes the mutation.
        # A final 32-bit shift can use its input's register directly. The shift is unchanged and
        # assigning its result adds no narrowing conversion; never do this for a byte/short local.
        if hits and loop_liveness.final_reuse(part,decl,hits[-1]) and decl['type'] in {'s32', 'u32', 'int', 'signed int', 'unsigned int'}:
            last = hits[-1]
            shift = re.match(r'\s*<<\s*([1-9]|[12][0-9]|3[01])\b', part.clean[last.end():])
            if shift and not re.match(r'\s*[+*/%.\-]', part.clean[last.end()+shift.end():]):
                add('last_shift_reuse', [(last.start(), last.end()+shift.end(), f'({variable} <<= {shift[1]})')])
            scale = re.match(r'\s*\*\s*(2|4|8|16)\b', part.clean[last.end():])
            if scale:
                add('last_shift_reuse', [(last.start(), last.end()+scale.end(), f'({variable} *= {scale[1]})')])
    return result
