"""Inline a real decrement/call/returned-count phase with its existing ANSI call contract."""
import re

from . import branch_call_support as support
from . import integer_abi
from .call_lifetime_repairs import block_path
from .csource import find
from .integer_repair_support import IDENT, edit

FAMILY = 'inline_call_count'
FAMILIES = (FAMILY,)
LIMIT = 4
MARKER = '__repair_call_count'


def variants(source, name, header='', arch=None):
    facts=support.facts(source,name,header,arch)
    if not facts or MARKER in source+header or re.search(r'\bp[0-7]\b',header+'\n'+source):return []
    part,flow,scalars=facts;context=header+'\n'+source;groups={}
    nodes=sorted(flow.nodes,key=lambda n:n.start)
    for node in nodes:
        call=support.call(part,node)
        if not call or call[0]:continue
        _,callee,args=call;proto=support.prototype(context,callee,arch)
        if not proto or proto[0]!='void' or len(proto[1])!=len(args):continue
        if any(integer_abi.scalar(t,context,arch) is None and not re.fullmatch(rf'{IDENT}\s*\*',t) for t in proto[1]):continue
        if not all(support.local_expression(a,part,scalars,context,arch) for a in args):continue
        for index,count in enumerate(args):
            if count not in scalars or integer_abi.scalar(scalars[count],context,arch)!=(32,True):continue
            if sum(bool(re.search(rf'\b{count}\b',a)) for a in args)!=1:continue
            preceding=[n for n in nodes if n.end<=node.start and block_path(part,n.start)==block_path(part,node.start)]
            for previous in reversed(preceding[-5:]):
                text=part.clean[previous.start:previous.end]
                if part.clean[:previous.start].rstrip()[-1:] not in ';{}':continue
                decrement=re.fullmatch(rf'\s*(?:{count}\s*--|--\s*{count}|{count}\s*-=\s*1)\s*;',text)
                assignment=re.fullmatch(rf'\s*{count}\s*=\s*({IDENT})\s*-\s*1\s*;',text)
                if not decrement and not assignment:continue
                original=assignment[1] if assignment else count
                if original not in scalars or integer_abi.scalar(scalars[original],context,arch)!=(32,True):continue
                between=part.clean[previous.end:node.start]
                # Move only across independent scalar updates. Never cross a call, control edge or memory access.
                if re.search(rf'\b(?:{count}|{original})\b',between):continue
                if between.strip():
                    statements=[s.strip() for s in between.split(';') if s.strip()]
                    if any(not re.fullmatch(rf'({IDENT})\s*(?:=|\+=|-=)\s*(.+)',s) for s in statements):continue
                    if any(not support.local_expression(s.split('=',1)[1],part,scalars,context,arch,arithmetic=True) for s in statements):continue
                    if any(s.split('=',1)[0].strip().rstrip('+-').strip() not in scalars for s in statements):continue
                types=list(proto[1]);types[index]=scalars[count]
                fixed=[bool(re.fullmatch(r'(?:0[xX][0-9a-fA-F]+|\d+)',a)) for a in args]
                parameters=[f'{t} p{i}' for i,t in enumerate(types) if not fixed[i]]
                actual=[original if i==index else a for i,a in enumerate(args) if not fixed[i]]
                inner=[a if fixed[i] else f'p{i}' for i,a in enumerate(args)]
                helper=(f'static __inline__ {scalars[count]} {MARKER}('+', '.join(parameters)+') { '
                        f'p{index}--; {callee}('+', '.join(inner)+f'); return p{index}; }}\n')
                replacement=f'{count} = {MARKER}('+', '.join(actual)+');'
                key=helper
                groups.setdefault(key,[]).append([(previous.start,previous.end,''),(node.start,node.end,replacement)])
                break
    result=[];start=find(source,name)[0]
    for helper,changes in groups.items():
        # The same helper can cover disjoint arms. No unbounded helper nesting on descendants.
        result.append((FAMILY,edit(source,[(start,start,helper)]+[e for group in changes for e in group])))
        if len(result)>=LIMIT:break
    return list(dict.fromkeys(result))
