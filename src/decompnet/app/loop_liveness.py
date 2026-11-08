"""Prove a limited local's value is dead after its final textual use, including reset loops.

This intentionally handles structured, braced loops only. A reset must dominate the selected use on
every iteration, with no earlier read in the loop body or header. Unknown jumps/scopes are rejected.
"""
import re

from .integer_repair_support import IDENT, refs
from .pattern_repairs import close


def final_reuse(part, decl, last):
    clean = part.clean
    if re.search(r'\b(?:goto|setjmp|_setjmp|sigsetjmp|longjmp)\b',part.body):
        return False
    loops = []
    headers = []
    for hit in re.finditer(r'\bdo\b',part.body):
        start = part.opening+1+hit.start()
        arm = re.match(r'\s*{',clean[start+len(hit[0]):])
        if not arm:
            return False
        opening = start+len(hit[0])+arm.end()-1
        end = close(clean,opening,'{','}')
        if not end:
            return False
        latch = re.match(r'\s*while\s*\(',clean[end:])
        if not latch:
            return False
        tail = close(clean,end+latch.end()-1)
        if not tail:
            return False
        loops.append((start,opening,end,clean[end:tail]))
        headers.append((end,tail))
    do_latches = {end+re.match(r'\s*',clean[end:]).end() for _,_,end,_ in loops}
    for hit in re.finditer(r'\b(?:for|while)\s*\(',part.body):
        start = part.opening+1+hit.start()
        if start in do_latches:
            continue
        condition = close(clean,part.opening+1+hit.end()-1)
        arm = re.match(r'\s*{',clean[condition:]) if condition else None
        if not arm:
            return False
        opening = condition+arm.end()-1
        end = close(clean,opening,'{','}')
        if not end:
            return False
        loops.append((start,opening,end,clean[start:condition]))
        headers.append((start,condition))
    if any(start <= last.start() < end for start,end in headers):
        return False
    enclosing = [loop for loop in loops if loop[1]<last.start()<loop[2]]
    if not enclosing:
        return True
    if len(enclosing)!=1:
        return False  # nested back edges need a full CFG rather than a textual dominance proof
    _,opening,end,header = enclosing[0]
    name = decl['name']
    if re.search(rf'\b{re.escape(name)}\b',header):
        return False
    body = clean[opening+1:end-1]
    definition = re.search(rf'(?:^|(?<=[;{{}}]))\s*{re.escape(name)}\s*=(?!=)([^;{{}}]+);',body)
    if definition:
        start,stop = opening+1+definition.start(),opening+1+definition.end()
        if re.search(rf'\b{re.escape(name)}\b',definition[1]):
            return False
    elif decl['value'] is not None and opening<decl.start()<last.start():
        start,stop = decl.start(),decl.end()
    else:
        return False
    prefix = clean[opening+1:start]
    if stop>last.start() or prefix.count('{')!=prefix.count('}') or re.search(
            rf'\b(?:if|else|for|while|do|switch|case|default|continue|break|return)\b|\b{IDENT}\s*:',prefix):
        return False
    # Even a read before a later unconditional reset observes the preceding iteration's mutation.
    if any(opening<hit.start()<start for hit in refs(part,decl)):
        return False
    return True
