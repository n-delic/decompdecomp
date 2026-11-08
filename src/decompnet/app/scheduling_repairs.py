"""Small scheduling shapes seen in captured matches, always checked by the ordinary judge."""
import re

from .integer_repair_support import IDENT, NUMBER, edit, locals_for, view
from .pattern_repairs import close


def variants(source, name):
    part = view(source, name)
    if not part or re.search(r'\b(?:asm|__asm__)\b', part.clean):
        return []
    part.end = len(part.clean[:part.end].rstrip())
    result = []
    # Reversing equal-type local stores keeps both values but changes register preference.
    declarations = {d['name']: d for d in locals_for(part)}
    for hit in re.finditer(rf'\b({IDENT})\s*=\s*\(\s*({IDENT})\s*=\s*({NUMBER})\s*\)\s*;', part.body):
        a, b, value = hit.groups()
        prefix = part.body[:hit.start()].rstrip()
        if a == b or a not in declarations or b not in declarations:
            continue
        if declarations[a]['type'] != declarations[b]['type'] or any(
                declarations[n].end() > part.opening+1+hit.start() for n in (a,b)):
            continue
        if prefix and prefix[-1] not in ';{}':
            continue
        result.append(('chained_local_order', edit(source, [(part.opening+1+hit.start(), part.opening+1+hit.end(),
                                                            f'{b} = ({a} = {value});')])))
    # Only a true void definition may return a discarded call. Leave unbraced guards alone.
    signature = part.clean[:part.opening]
    if not re.search(rf'\bvoid\s+{re.escape(name)}\s*\([^;{{}}]*\)\s*$', signature):
        return result[:8]
    hit = re.search(rf'\b({IDENT})\s*\([^;{{}}]*\)\s*;\s*$', part.body)
    if hit and hit[1] not in {'if', 'while', 'switch', 'return'}:
        prefix = part.body[:hit.start()].rstrip()
        if (not prefix or prefix[-1] in ';}') and prefix.count('{') == prefix.count('}'):
            start = part.opening+1+hit.start()
            result.append(('void_tail_call', edit(source, [(start, start, '\nreturn (void)')])))
    # Empty polling loops can use an explicit entry guard. The condition is evaluated once
    # before the first iteration and once per back edge in both forms, including volatile reads.
    tail = next((text for family,text in result if family == 'void_tail_call'), None)
    for wait in re.finditer(r'\bwhile\s*\(', part.body):
        start = part.opening+1+wait.start()
        cond_end = close(part.clean,part.opening+1+wait.end()-1)
        arm = re.match(r'\s*{\s*}',part.clean[cond_end:]) if cond_end else None
        prefix = part.clean[part.opening+1:start].rstrip()
        if not arm or (prefix and prefix[-1] not in ';{}'):
            continue
        end = cond_end+arm.end()
        condition = source[part.opening+1+wait.end():cond_end-1]
        replacement = f'if ({condition}) do {{}} while ({condition});'
        result.append(('empty_wait_guard',edit(source,[(start,end,replacement)])))
        if tail:
            result.append(('empty_wait_tail_call',edit(tail,[(start,end,replacement)])))
        break
    # A terminal conjunction can be spelled as ordered guards. Restrict conditions to scalar
    # comparisons so calls, volatile accesses and short-circuit side effects are never duplicated.
    for hit in re.finditer(r'\bif\s*\(', part.body):
        start = part.opening+1+hit.start()
        before = part.clean[part.opening+1:start]
        if before.count('{') != before.count('}') or (before.rstrip() and before.rstrip()[-1] not in ';}'):
            continue
        cond_end = close(part.clean, part.opening+1+hit.end()-1)
        arm = re.match(r'\s*{', part.clean[cond_end:]) if cond_end else None
        opening = cond_end+arm.end()-1 if arm else None
        end = close(part.clean, opening, '{', '}') if opening is not None else None
        if not end or part.clean[end:part.end-1].strip():
            continue
        condition = part.clean[part.opening+1+hit.end():cond_end-1].strip()
        while condition.startswith('(') and close(condition,0) == len(condition):
            condition = condition[1:-1].strip()
        terms = condition.split('&&')
        if not 2 <= len(terms) <= 6:
            continue
        valid = True
        for term in terms:
            term = term.strip()
            while term.startswith('(') and close(term,0) == len(term):
                term = term[1:-1].strip()
            if not re.fullmatch(rf'{IDENT}\s*(?:<=|>=|==|!=|<|>)\s*-?\s*{NUMBER}', term):
                valid = False
        if not valid or re.search(r'\b(?:goto|case|default|break|continue)\b|(?<!:):(?!:)', part.clean[opening+1:end-1]):
            continue
        guards = ''.join(f'if (!({term.strip()})) return;\n' for term in terms)
        result.append(('terminal_bounds_guards', edit(source, [(start,end,guards+source[opening:end])])) )
    return result[:8]
