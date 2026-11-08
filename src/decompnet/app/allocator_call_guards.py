"""Equivalent call branches for an initialized, unescaped pointer; CFG proves every added read."""
import re
from collections import Counter

from . import local_flow, pointer_pair_repairs
from .integer_repair_support import IDENT, edit
from .pattern_repairs import close

LIMIT = 24
MARKER = '__repair_pointer_balance'


def context(source,name,header):
    part,pointers,macros = pointer_pair_repairs.context(source,name,header)
    if part:
        invoked = set(re.findall(IDENT,part.body))
        if any(key in invoked and ('\\' in body or '#' in body) for key,(_,body) in macros.items()):
            return None,[],{}
    return part,pointers,macros


def declarations(part):
    pointers = pointer_pair_repairs.declarations(part)
    # Pointer-to-array locals occur in formatter drafts too. Do not treat an array
    # object, parameter, escaped pointer or shadow as initialized pointer storage.
    pattern = rf'\b(?P<type>char|[su]8)\s*\(\s*\*\s*(?P<name>{IDENT})\s*\)\s*\[\s*\d+\s*\]\s*;'
    for decl in re.compile(pattern).finditer(part.clean, part.opening+1, part.end):
        name = decl['name']
        signature = part.clean[max(part.clean.rfind(ch,0,part.opening) for ch in ";{}")+1:part.opening]
        if re.search(rf'\b{name}\b', signature):
            continue
        if re.search(rf'\b(?:{IDENT})\s+\**\s*{name}\s*(?=[;=,\[])',part.body):
            continue
        if re.search(rf'&\s*\(*\s*{name}\b', part.body):
            continue
        if len([m for m in re.finditer(pattern,part.body) if m['name']==name]) != 1:
            continue
        prefix = part.clean[part.opening+1:decl.start()].rstrip()
        if not prefix or prefix[-1] in ';{}':
            pointers.append(decl)
    return [decl for decl in pointers
            if sum(m['name']==decl['name'] for m in pointer_pair_repairs.POINTER.finditer(part.body))+
               sum(m['name']==decl['name'] for m in re.finditer(pattern,part.body))==1]


def dominators(flow):
    """Unknown/unreachable blocks cannot prove an initialization, including forward-jump entries."""
    if flow.entry is None:
        return {}
    reached, pending = set(), [flow.entry]
    while pending:
        index = pending.pop()
        if index not in reached:
            reached.add(index)
            pending.extend(flow.nodes[index].successors)
    parents = {index: set() for index in reached}
    for index in reached:
        for child in flow.nodes[index].successors & reached:
            parents[child].add(index)
    result = {index: ({index} if index == flow.entry else set(reached)) for index in reached}
    changed = True
    while changed:
        changed = False
        for index in reached - {flow.entry}:
            value = {index} | set.intersection(*(result[parent] for parent in parents[index]))
            if value != result[index]:
                result[index] = value
                changed = True
    return result


def variants(source, name, header=''):
    part, _, macros = context(source, name, header)
    if not part or MARKER in source+header:
        return []
    pointers = declarations(part)
    names = {d['name'] for d in pointers}
    if any(names & (set(re.findall(IDENT,body))-set(re.findall(IDENT,params or '')))
           for params,body in macros.values()):
        return []
    flow = local_flow.analyze(source, name, declarations=pointers)
    if not flow:
        return []
    dom = dominators(flow)
    guards = []
    for decl in pointers:
        variable = decl['name']
        writes_text = re.findall(rf'\b{variable}\s*(?:=(?!=)|[+\-*/%&|^]=|\+\+|--)|(?:\+\+|--)\s*\b{variable}\b',part.body)
        if len(writes_text)!=1:
            continue
        writes = [i for i, node in enumerate(flow.nodes) if variable in node.defines
                  and not node.start <= decl.start() < node.end]
        if len(writes) != 1:
            continue
        index = writes[0]
        text = part.clean[flow.nodes[index].start:flow.nodes[index].end]
        # Taking an ordinary object's address reads no uninitialized object content.
        # Alias/macros and pointer updates are deliberately not inferred here.
        if re.fullmatch(rf'\s*{variable}\s*=\s*&\s*{IDENT}\s*;\s*', text):
            guards.append((variable, index))
    calls = []
    for index, node in enumerate(flow.nodes):
        text = part.clean[node.start:node.end]
        hit = re.match(rf'\s*({IDENT})\s*\(', text)
        if not hit or hit[1] in macros or hit[1] in {'return', 'if', 'while', 'for', 'switch', 'sizeof'}:
            continue
        opening = node.start+hit.end()-1
        end = close(part.clean, opening)
        if end and re.fullmatch(r'\s*;', part.clean[end:node.end]):
            calls.append((hit[1], index, node.start, node.end))
    frequency = Counter(c[0] for c in calls)
    calls = sorted(reversed(calls), key=lambda c: -frequency[c[0]])
    pools = []
    for variable, initialized in guards[:2]:
        pool = []
        eligible = [c for c in calls if initialized in dom.get(c[1], set())][:6]
        for _, _, start, end in eligible:
            statement = source[start:end]
            for nested in (False, True):
                branch = f'if ((unsigned long){variable} & 0x100) {{\n{statement}\n}} else {{\n{statement}\n}}'
                if nested:
                    branch = f'if ((unsigned long){variable} & 0x10000) {{\n{branch}\n}} else {{\n{branch}\n}}'
                pool.append(('balanced_pointer_calls', edit(source, [(start, end, f'/*{MARKER}*/'+branch)])))
        pools.append(pool)
    from itertools import zip_longest
    return [item for row in zip_longest(*pools) for item in row if item is not None][:LIMIT]
