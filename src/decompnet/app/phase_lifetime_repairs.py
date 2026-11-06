"""Split a fully dominated value phase, then try an existing disjoint-lifetime merge."""
import re

from . import integer_lifetime_repairs, local_flow
from .allocator_call_guards import context, dominators
from .call_lifetime_repairs import block_path
from .integer_repair_support import edit, refs

FAMILY = 'paired_integer_lifetimes'
LIMIT = 16


def reaching(flow, variable, reachable):
    parents = {i:set() for i in reachable}
    for i in reachable:
        for child in flow.nodes[i].successors & reachable:
            parents[child].add(i)
    incoming, outgoing = {i:set() for i in reachable}, {i:set() for i in reachable}
    changed = True
    while changed:
        changed = False
        for i in reachable:
            value = set().union(*(outgoing[p] for p in parents[i]))
            if i==flow.entry:
                value.add(-1)
            after = {i} if variable in flow.nodes[i].defines else value
            if value!=incoming[i] or after!=outgoing[i]:
                incoming[i],outgoing[i] = value,after
                changed = True
    return incoming


def splits(source, name, header=''):
    if not context(source,name,header)[0]:
        return []
    flow = local_flow.analyze(source,name)
    if not flow:
        return []
    dom = dominators(flow)
    result = []
    for decl in flow.locals:
        if block_path(flow.part,decl.start())!=[flow.part.opening]:
            continue
        variable = decl['name']
        fresh = '__repair_phase_'+variable
        if fresh in source or decl['type'] not in {'s32','u32','int','unsigned int','signed int'}:
            continue
        definitions = [i for i,node in enumerate(flow.nodes) if variable in node.defines
                       and not node.start <= decl.start() < node.end]
        if len(definitions)<2:
            continue
        incoming = reaching(flow,variable,set(dom))
        for initial in definitions:
            node = flow.nodes[initial]
            if not re.fullmatch(rf'\s*{variable}\s*=(?!=)[^;]*;\s*',flow.part.clean[node.start:node.end]):
                continue
            if variable in node.uses:
                continue
            reached, pending, valid = {initial}, list(node.successors), True
            while pending:
                index = pending.pop()
                if index in reached:
                    continue
                current = flow.nodes[index]
                if variable in current.defines:
                    # A self-update consumes this phase; leave it to the general CFG families.
                    if variable in current.uses:
                        valid = False
                    continue
                reached.add(index)
                if variable in current.uses and incoming.get(index)!={initial}:
                    valid = False
                pending.extend(current.successors)
            if not valid:
                continue
            uses = [hit for hit in refs(flow.part,decl)
                    if any(flow.nodes[i].start<=hit.start()<flow.nodes[i].end for i in reached)]
            if len(uses)<2 or len(uses)==len(refs(flow.part,decl)):
                continue
            edits = [(decl.end(),decl.end(),f'\n{decl["type"]} {fresh};')]
            edits += [(hit.start(),hit.end(),fresh) for hit in uses]
            # A scalar later reused for cast address arithmetic is a useful phase
            # boundary. This only prioritizes already-proven splits, never infers a type.
            address_shape = bool(re.search(r'\(\s*(?:[su]32|unsigned\s+(?:int|long))\s*\).*\+',
                                           flow.part.clean[node.start:node.end]))
            result.append((not address_shape,variable,edit(source,edits)))
            if len(result)>=24:
                break
        if len(result)>=24:
            break
    ranked = sorted(result,key=lambda item:item[0])
    selected,variables = [],set()
    for _,variable,candidate in ranked:
        if variable not in variables:
            selected.append(candidate)
            variables.add(variable)
    selected += [candidate for _,_,candidate in ranked if candidate not in selected]
    return selected[:4]


def variants(source,name,header=''):
    result = []
    for first in splits(source,name,header):
        result.append(('split_integer_phase',first))
        merges = [(family,text) for family,text in integer_lifetime_repairs.variants(first,name)
                  if family in {'merge_integer_lifetimes','cfg_integer_lifetimes'}]
        flow = local_flow.analyze(first,name)
        preferred = []
        if flow:
            phase = re.search(r'__repair_phase_(\w+)',first)
            stem = re.sub(r'_\d+$','',phase[1]) if phase else ''
            candidates = [d for d in flow.locals if not d['name'].startswith('__repair_phase_')
                          and block_path(flow.part,d.start())==[flow.part.opening]]
            for index,left in enumerate(candidates):
                for right in candidates[index+1:]:
                    if left['type']!=right['type'] or not local_flow.disjoint(flow,left,right):
                        continue
                    names = [re.sub(r'_\d+$','',d['name']) for d in (left,right)]
                    if names!=[stem,stem]:
                        continue
                    edits = [(right.start(),right.end(),'')]
                    edits += [(hit.start(),hit.end(),left['name']) for hit in refs(flow.part,right)]
                    preferred.append(edit(first,edits))
        for second in list(dict.fromkeys(preferred+[text for _,text in merges]))[:3]:
            result.append((FAMILY,second))
    return result[:LIMIT]
