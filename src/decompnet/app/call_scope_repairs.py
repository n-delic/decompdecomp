"""Single-call one-shot scopes: no duplicated calls, moved effects or swallowed loop control."""
import re

from . import local_flow
from .integer_repair_support import IDENT, edit, view
from .pattern_repairs import close

FAMILIES = ('call_one_shot_scope',)
LIMIT = 24


def variants(source, name, header=''):
    part = view(source,name)
    flow = local_flow.analyze(source,name)
    if not part or not flow:
        return []
    macros = set(re.findall(r'^\s*#\s*define\s+(\w+)',source+'\n'+header,re.M))
    if '__LINE__' in source+'\n'+header:
        return []
    floats = set(re.findall(r'\b(?:f32|f64|float|double)\s+('+IDENT+r')\s*;',part.body))
    candidates = []
    for node in flow.nodes:
        text = part.clean[node.start:node.end]
        hit = re.match(rf'\s*({IDENT})\s*\(',text)
        if not hit or hit[1] in macros | {'if','while','for','switch','sizeof','return','do'}:
            continue
        opening = node.start+hit.end()-1
        end = close(part.clean,opening)
        if not end or not re.fullmatch(r'\s*;',part.clean[end:node.end]):
            continue
        if set(re.findall(IDENT,text)) & macros or re.search(r'[{}]|\b(?:break|continue|goto)\b',text):
            continue
        # Do not nest another synthetic loop around an already single-call wrapper.
        prefix = part.clean[:node.start]
        if re.search(r'\bdo\s*{\s*$',prefix):
            tail = part.clean[node.end:]
            if re.match(r'\s*}\s*while\s*\(\s*0\s*\)',tail):
                continue
        original = source[node.start:node.end]
        candidate = edit(source,[(node.start,node.end,'do { '+original+' } while (0);')])
        candidates.append((not bool(floats & set(re.findall(IDENT,text))),node.start,candidate))
    return [(FAMILIES[0],c) for _,_,c in sorted(candidates)[:LIMIT]]
