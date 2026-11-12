"""Split dead scalar phases inside braced arms, even in functions with switches and loops."""
import re

from . import branch_call_support as support
from .call_lifetime_repairs import block_path
from .integer_repair_support import edit, refs
from .pattern_repairs import close

FAMILY = 'branch_scalar_lifetime'
FAMILIES = (FAMILY,)
LIMIT = 6


def variants(source, name, header='', arch=None):
    facts = support.facts(source,name,header,arch)
    if not facts or '__repair_branch_' in source+header:return []
    part,flow,_ = facts
    arms=[]
    for hit in re.finditer(r'\b(?:if\s*\(|else\s*{)',part.body):
        start=part.opening+1+hit.start()
        if hit[0].startswith('if'):
            opening=part.clean.find('(',start);stop=close(part.clean,opening)
            arm=re.match(r'\s*{',part.clean[stop:]) if stop else None
            if not arm:continue
            opening=stop+arm.end()-1
        else:opening=part.opening+hit.end()
        stop=close(part.clean,opening,'{','}')
        if not stop:continue
        body=part.clean[opening+1:stop-1]
        # The surrounding CFG can be complex; the selected phase must be one straight-line arm.
        if re.search(r'[{}]|\b(?:if|for|while|do|switch|goto|return|break|continue)\b|(?<!:):(?!:)',body):continue
        arms.append((opening,stop))
    result=[]
    for decl in flow.locals:
        variable=decl['name'];groups=[]
        if sum(d['name']==variable for d in flow.locals)!=1:continue
        for opening,stop in arms:
            if decl.end()>=opening:continue
            nodes=[n for n in flow.nodes if opening<n.start and n.end<stop]
            if not nodes:continue
            if any(variable in flow.nodes[child].live for n in nodes for child in n.successors
                   if not opening<flow.nodes[child].start<stop):continue
            uses=[r for r in refs(part,decl) if opening<r.start()<stop]
            if not uses or not re.search(rf'\b{variable}\s*(?:=(?!=)|[+\-]=)',part.clean[opening+1:stop-1]):continue
            if any(re.search(rf'(?:\.|->)\s*{variable}\b',part.clean[n.start:n.end]) for n in nodes):continue
            first=uses[0]
            kill=re.match(r'\s*=(?!=)\s*([^;]+);',part.clean[first.end():stop])
            incoming=not kill or re.search(rf'\b{variable}\b',kill[1])
            fresh='__repair_branch_'+variable+'_'+str(len(groups))
            if fresh in source+header:continue
            prefix=f'\n{decl["type"]} {fresh}'+(f' = {variable}' if incoming else '')+';\n'
            changes=[(r.start(),r.end(),fresh) for r in uses]+[(opening+1,opening+1,prefix)]
            path=block_path(part,decl.start())
            if block_path(part,opening)[:len(path)]!=path:continue
            groups.append(changes)
        if len(groups)>1:result.append((FAMILY,edit(source,[e for group in groups for e in group])))
        result.extend((FAMILY,edit(source,group)) for group in groups)
        if len(result)>=LIMIT:break
    return list(dict.fromkeys(result))[:LIMIT]
