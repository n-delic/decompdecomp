"""Small line repairs for debug builds with a residual MIPS NOP.

Only whitespace changes. Without a map, try adjacent dependent source statements;
never invent an assembly-to-source location.
"""
import re

from . import layout_frontiers, local_flow, repair_localisation
from .call_lifetime_repairs import block_path
from .integer_repair_support import edit, view

FAMILY = 'targeted_debug_layout'


def variants(source, name, header, arch, flags, report):
    debug=[f for f in flags if re.fullmatch(r'-g[0-3]?',f)]
    if arch != 'mips' or not debug or debug[-1]=='-g0' or not layout_frontiers.safe(source,header):
        return []
    rows=report.get('rows') or []
    if not any(len(r)==3 and r[0]!=' ' and any(re.search(r'\bnop\b',s) for s in r[1:] if isinstance(s,str)) for r in rows):
        return []
    relevant=repair_localisation.lines(source,report.get('repair_locations'))
    part=view(source,name)
    if not part:return []
    if not relevant:return staged_boundaries(source,name,header)
    starts=[0]+[m.end() for m in re.finditer('\n',source)]
    result=[]
    # Join only line boundaries in the mapped statement's small neighbourhood.
    # Each proposal changes one boundary; one combined proposal covers the region.
    edits=[]
    for line in sorted({n for line in relevant for n in range(max(2,line-1),line+2)}):
        if line>len(starts):continue
        pos=starts[line-1]-1
        if not part.opening<pos<part.end-1:continue
        previous=source[starts[line-2]:pos]
        following=source[pos+1:starts[line] if line<len(starts) else len(source)]
        if '#' in previous+following or '//' in previous or re.search(r'/\*|\*/',previous+following):continue
        change=(pos,pos+1,' ')
        candidate=edit(source,[change])
        if layout_frontiers.equivalent(source,candidate,header):
            edits.append(change);result.append((FAMILY,candidate))
        if len(edits)>=3:break
    if len(edits)>1:result.insert(0,(FAMILY,edit(source,edits)))
    return list(dict.fromkeys(result))[:4]


def staged_boundaries(source,name,header=''):
    """Without a compiler line map, use real adjacent dependent statements only.

    This is a bounded source hypothesis, not an inferred assembly/source location.
    Joining whitespace preserves tokens and never crosses a macro or comment.
    """
    flow=local_flow.analyze(source,name)
    if not flow:return []
    part=flow.part
    macros=set(re.findall(r'^\s*#\s*define\s+(\w+)',source+'\n'+header,re.M))
    edits=[]
    nodes=sorted(flow.nodes,key=lambda n:n.start)
    for left,right in zip(nodes,nodes[1:]):
        gap=source[left.end:right.start]
        a,b=part.clean[left.start:left.end],part.clean[right.start:right.end]
        hit=re.fullmatch(r'\s*([A-Za-z_]\w*)\s*=(?!=)[^;{}]*[*/|][^;{}]*;\s*',a)
        if (not hit or not a.rstrip().endswith(';') or not b.rstrip().endswith(';')
                or not re.search(r'\b'+re.escape(hit[1])+r'\b',b)
                or not gap.isspace() or '\n' not in gap
                or block_path(part,left.start)!=block_path(part,right.start)
                or macros & set(re.findall(r'[A-Za-z_]\w*',a+b))):continue
        candidate=edit(source,[(left.end,right.start,' ')])
        if layout_frontiers.equivalent(source,candidate,header):edits.append((left.end,right.start,' '))
    # Tail arithmetic must not be starved by the first four initialization lines.
    edits=edits[-4:]
    result=[(FAMILY,edit(source,[e])) for e in edits]
    if len(edits)>1:result.insert(0,(FAMILY,edit(source,edits)))
    return list(dict.fromkeys(result))[:5]
