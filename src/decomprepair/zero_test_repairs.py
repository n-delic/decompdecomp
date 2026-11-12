"""Reuse a dead word local for zero-only tests, retaining declarations used by debug-era allocation."""
import re

from . import local_flow
from .integer_repair_support import edit, refs
from .pointer_pair_repairs import context, initialized

FAMILIES=('word_zero_test_reuse',)
WORD={'s32','u32','int','unsigned int','signed int'}


def variants(source,name,arch,header=''):
    if arch not in {'mips','ppc','x86','arm','thumb'}:return []
    part,_,_=context(source,name,header)
    if part is None:return []
    flow=local_flow.analyze(source,name)
    if not flow:return []
    out=[]
    # Reaching a use through a different predecessor would need a separate assignment.
    predecessors={i:set() for i in range(len(flow.nodes))}
    for i,node in enumerate(flow.nodes):
        for successor in node.successors:predecessors[successor].add(i)
    for old in flow.locals:
        if old['type'] not in WORD or not initialized(flow,old['name']):continue
        for index,node in enumerate(flow.nodes):
            text=flow.part.clean[node.start:node.end]
            assignment=re.fullmatch(rf'\s*({re.escape(old["name"])})\s*=(?!=).*;\s*',text,re.S)
            if not assignment:continue
            region=set();pending=list(node.successors);uses=[];use_nodes=set();valid=True
            while pending and len(region)<128:
                current=pending.pop()
                if current in region:continue
                following=flow.nodes[current]
                if old['name'] in following.defines:continue
                region.add(current)
                if old['name'] in following.uses:
                    condition=flow.part.clean[following.start:following.end]
                    if not re.fullmatch(rf'\s*{re.escape(old["name"])}\s*(?:==|!=)\s*0\s*',condition):valid=False;break
                    use_nodes.add(current)
                    uses.extend(h for h in refs(flow.part,old) if following.start<=h.start()<following.end)
                pending.extend(following.successors)
            if pending or not valid or not uses:continue
            # Retain only paths leading to an old-value read. Back edges after its next
            # definition do not belong to this phase; external entries to a selected use do.
            active=set();pending=list(use_nodes)
            while pending:
                current=pending.pop()
                if current in active:continue
                active.add(current);pending.extend(predecessors[current]&region-active)
            if any(predecessors[i]-active-{index} for i in active):continue
            region=active
            for target in flow.locals:
                if target['name']==old['name'] or target['type'] not in WORD:continue
                if target.end()>node.start or any(target['name'] in flow.nodes[i].live|flow.nodes[i].defines|flow.nodes[i].uses for i in region|{index}):continue
                edits=[(node.start+assignment.start(1),node.start+assignment.end(1),target['name'])]
                edits += [(h.start(),h.end(),target['name']) for h in uses]
                out.append((FAMILIES[0],edit(source,edits)))
                if len(out)==6:return out
    return out
