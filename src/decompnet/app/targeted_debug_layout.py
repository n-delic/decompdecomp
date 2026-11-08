"""Small source-mapped line repairs for debug builds with a residual MIPS NOP.

Only whitespace changes. No map means no guessed assembly-to-source location.
"""
import re

from . import layout_frontiers, repair_localisation
from .integer_repair_support import edit, view

FAMILY = 'targeted_debug_layout'


def variants(source, name, header, arch, flags, report):
    if arch != 'mips' or not any(re.fullmatch(r'-g[123]?',f) for f in flags) or not layout_frontiers.safe(source,header):
        return []
    rows=report.get('rows') or []
    if not any(len(r)==3 and r[0]!=' ' and any(re.search(r'\bnop\b',s) for s in r[1:] if isinstance(s,str)) for r in rows):
        return []
    relevant=repair_localisation.lines(source,report.get('repair_locations'))
    part=view(source,name)
    if not part or not relevant:return []
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
