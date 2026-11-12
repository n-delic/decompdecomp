"""Bounded floating product lifetime hypotheses; casts and multiplication order stay explicit.

An intermediate store may change rounding as well as allocation. These are proposals for the ordinary
judge, never an equivalence proof or permission to reassociate floating arithmetic.
"""
import re

from .integer_repair_support import IDENT, edit, refs, view

LIMIT = 6
FLOAT = r'(?:float|double|f32|f64)'
ATOM = rf'{IDENT}(?:\s*(?:->|\.)\s*{IDENT})*'
DECL = re.compile(rf'\b(?P<type>{FLOAT})\s+(?P<name>{IDENT})\s*;')


def variants(source, name):
    part = view(source, name)
    if not part or re.search(r'\b(?:volatile|asm|__asm__|goto|for|while|do)\b', part.clean):
        return []
    declarations = [d for d in DECL.finditer(part.clean, part.opening+1, part.end)
                    if part.clean[part.opening+1:d.start()].rstrip()[-1:] in {'', ';', '{', '}'}]
    result = []
    for decl in declarations:
        variable = decl['name']
        if len(re.findall(rf'\b{FLOAT}\s+{variable}\b', part.body)) != 1 or re.search(rf'&\s*{variable}\b', part.body):
            continue
        # Adjacent assignments only. The first is a cast of one scalar; the second consumes it
        # once as the RHS of the first product. No calls, increments, indexing or control flow.
        pattern = re.compile(rf'(?<![\w.>]){variable}\s*=\s*(?P<cast>\(\s*{FLOAT}\s*\)\s*{ATOM})\s*;'
                             rf'(?P<gap>\s*)(?P<assignment>{ATOM}\s*=\s*[^;{{}}]+);')
        for assignment in pattern.finditer(part.clean, decl.end(), part.end):
            if part.clean[part.opening+1:assignment.start()].rstrip()[-1:] not in {';', '{', '}'}:
                continue
            expression = assignment['assignment']
            if re.search(r'\+\+|--|\?|,|\b(?:sizeof|return)\b|\b\w+\s*\(', expression):
                continue
            product = re.search(rf'\(\s*(?P<factor>{ATOM})\s*\*\s*{variable}\s*\)', expression)
            if not product or len(re.findall(rf'\b{variable}\b', expression)) != 1:
                continue
            hits = refs(part, decl)
            covered = [h for h in hits if assignment.start() <= h.start() < assignment.end()]
            # Earlier branch-local lifetimes may use this local too; its new value must be dead
            # after this pair. Do not guess dominance across a later write/loop or goto.
            if len(covered) != 2 or any(h.start() >= assignment.end() for h in hits):
                continue
            cast = source[assignment.start('cast'):assignment.end('cast')]
            factor = source[assignment.start('assignment')+product.start('factor'):
                            assignment.start('assignment')+product.end('factor')]
            if re.search(rf'\b{variable}\b', cast+factor):
                continue
            # Same-type locals whose earlier lifetime has ended can be the product temporary.
            choices = [variable]+[d['name'] for d in declarations if d['name']!=variable and d['type']==decl['type']
                                  and d.end()<assignment.start() and not re.search(rf'&\s*{d["name"]}\b',part.body)
                                  and len(re.findall(rf'\b{FLOAT}\s+{d["name"]}\b',part.body))==1
                                  and not any(h.start() >= assignment.start() for h in refs(part,d))]
            for temporary in choices[:3]:
                original = source[assignment.start('assignment'):assignment.end('assignment')]
                rewritten = original[:product.start()]+temporary+original[product.end():]
                replacement = f'{temporary} = {factor} * {cast};'+assignment['gap']+rewritten+';'
                candidate = edit(source, [(assignment.start(),assignment.end(),replacement)])
                if ('float_product_intermediate',candidate) not in result:
                    result.append(('float_product_intermediate',candidate))
                if len(result) >= LIMIT:
                    return result
    return result
