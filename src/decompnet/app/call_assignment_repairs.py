"""Try staged and embedded scalar call arguments while preserving dependencies and widths."""
import re

from . import branch_call_support as support
from .call_lifetime_repairs import block_path
from .integer_repair_support import IDENT, edit

FAMILIES = ('embedded_call_assignment','extracted_call_assignment')
LIMIT = 6


def variants(source, name, header='', arch=None):
    facts=support.facts(source,name,header,arch)
    if not facts:return []
    part,flow,scalars=facts;context=header+'\n'+source;result=[]
    nodes=sorted(flow.nodes,key=lambda n:n.start)
    for pos,node in enumerate(nodes):
        call=support.call(part,node)
        if not call:continue
        assigned,callee,args=call
        for index,var in enumerate(args):
            if var not in scalars or var==assigned:continue
            others=[a for i,a in enumerate(args) if i!=index]
            if any(re.search(rf'\b{var}\b',a) or not support.local_expression(a,part,scalars,context,arch) for a in others):continue
            prefix=[]
            for previous in reversed(nodes[max(0,pos-2):pos]):
                if block_path(part,previous.start)!=block_path(part,node.start):break
                match=re.fullmatch(rf'\s*{var}\s*(=|\+=|-=)\s*(.+);\s*',part.clean[previous.start:previous.end],re.S)
                if not match or not support.local_expression(match[2],part,scalars,context,arch,arithmetic=True):break
                prefix.insert(0,(previous,match))
                if match[1]=='=':break
            if not prefix or part.clean[:prefix[0][0].start].rstrip()[-1:] not in ';{}':continue
            if any(part.clean[a.end:b.start].strip() for (a,_),(b,_) in zip(prefix,prefix[1:])):continue
            if part.clean[prefix[-1][0].end:node.start].strip():continue
            expression=prefix[0][1][2].strip()
            if re.search(rf'\b{var}\b',expression):continue
            if prefix[0][1][1]!='=':expression=var+' '+prefix[0][1][1][0]+' ('+expression+')'
            for _,match in prefix[1:]:expression='(('+scalars[var]+')('+expression+')) '+match[1][0]+' ('+match[2].strip()+')'
            # All participating values have the same 32-bit scalar contract; no narrow intermediate is dropped.
            original_type=scalars[var]
            from .integer_abi import scalar
            operands=set(re.findall(r'\b'+IDENT+r'\b',expression)) & scalars.keys()
            if any(scalar(scalars[v],context,arch)!=scalar(original_type,context,arch) for v in operands):continue
            updated=list(args);updated[index]=f'({var} = {expression})'
            replacement=(f'{assigned} = ' if assigned else '')+callee+'('+', '.join(updated)+');'
            result.append((FAMILIES[0],edit(source,[(n.start,n.end,'') for n,_ in prefix]+[(node.start,node.end,replacement)])))
        for index,arg in enumerate(args):
            embedded=re.fullmatch(rf'\(\s*({IDENT})\s*=(?!=)\s*(.+)\)',arg,re.S)
            if not embedded or embedded[1] not in scalars or embedded[1]==assigned:continue
            var,expression=embedded.groups()
            if not support.local_expression(expression,part,scalars,context,arch,arithmetic=True):continue
            if any(re.search(rf'\b{var}\b',a) or not support.local_expression(a,part,scalars,context,arch) for i,a in enumerate(args) if i!=index):continue
            updated=list(args);updated[index]=var
            replacement=f'{var} = {expression};\n'+(f'{assigned} = ' if assigned else '')+callee+'('+', '.join(updated)+');'
            # Two statements require a braced scope, not an unbraced conditional's single body.
            if part.clean[:node.start].rstrip()[-1:] not in ';{}':continue
            result.append((FAMILIES[1],edit(source,[(node.start,node.end,replacement)])))
        if len(result)>=LIMIT:break
    return list(dict.fromkeys(result))[:LIMIT]
