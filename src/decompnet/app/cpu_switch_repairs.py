"""Retail table labels and structurally supported arm clones, private until ordinary byte judging."""
import re

from .. import arch as arches
from ..forks.csource import _blank, find
from .pattern_repairs import close
from .source_next_steps import called


def variants(source, name, header, tables, blocks):
    span=find(source,name)
    if not span or len(tables)!=1 or not blocks:return []
    low,destinations=tables[0];clean=_blank(source);result=[]
    constants={m[1]:int(m[2],0) for m in re.finditer(r'(?m)^\s*#define\s+(\w+)\s+(-?(?:0x[0-9a-fA-F]+|\d+))\s*$',_blank(header))}
    for switch in re.finditer(r'\bswitch\s*\(',clean[span[0]:span[1]]):
        c=close(clean,span[0]+switch.end()-1)
        if not c:continue
        brace=c+len(clean[c:])-len(clean[c:].lstrip());end=close(clean,brace,'{','}') if clean[brace:brace+1]=='{' else None
        if not end:continue
        labels=[m for m in re.finditer(r'\bcase\s+([^:;{}]+)\s*:|\bdefault\s*:',clean[brace+1:end-1])
                if clean[brace+1:brace+1+m.start()].count('{')==clean[brace+1:brace+1+m.start()].count('}')]
        groups=[];pending=[];start=None
        for i,label in enumerate(labels):
            if start is None:start=brace+1+label.start()
            value=label[1].strip() if label[1] else None
            if value is not None:
                try:pending.append(constants[value] if value in constants else int(value,0))
                except ValueError:break
            stop=brace+1+(labels[i+1].start() if i+1<len(labels) else end-brace-2)
            body_start=brace+1+label.end();body=source[body_start:stop]
            if not _blank(body).strip():continue
            # No fallthrough, local scope, or user labels: clone only a straight-line terminating arm.
            if value is None or re.search(r'[{}]|\bgoto\b',_blank(body)) or not re.search(r'\bbreak\s*;\s*$',_blank(body)):
                groups.append((start,stop,None,body,body_start));pending=[];start=None;continue
            symbols=set(re.findall(r'\bD_[0-9A-Fa-f]{8}\b',_blank(body)))
            from .source_match_context import macros
            calls=called(body)-macros(header).keys()
            matches=[address for address,b in blocks.items() if symbols and symbols<=set(b['symbols']) and calls==set(b['calls'])]
            if len(matches)!=1:break
            groups.append((start,stop,matches[0],body,body_start));pending=[];start=None
        else:
            bound={address for _,_,address,_,_ in groups if address is not None}
            edits=[]
            for a,b,address,body,body_start in groups:
                if address is None:continue
                numbers=[low+i for i,v in enumerate(destinations) if v==address]
                if numbers:edits.append((a,b,''.join('case '+str(n)+':\n' for n in numbers)+body))
            # A missing arm can reuse only a target arm with exactly the same instruction shape.
            # Remap actual data symbols bijectively; never invent arbitrary stores/calls/control flow.
            extras=[]
            from collections import Counter
            common=Counter(destinations).most_common(1)[0]
            default_address=common[0] if common[1]>1 else None
            for address in sorted(set(destinations)-bound-{default_address}):
                if address not in blocks:continue
                donors=[g for g in groups if g[2] in blocks and blocks[g[2]]['signature']==blocks[address]['signature']]
                from ..repair_changes import TOKEN, without_comments
                clones={}
                for donor in donors[:8]:
                    old=blocks[donor[2]]['symbols'];new=blocks[address]['symbols']
                    if len(old)!=len(new) or len(old)>8 or old==new:continue
                    aliases=dict(zip(old,new))
                    body=re.sub(r'\bD_[0-9A-Fa-f]{8}\b',lambda m,aliases=aliases:aliases.get(m[0],m[0]),donor[3])
                    clones.setdefault(tuple(TOKEN.findall(without_comments(body))),body)
                if len(clones)!=1:continue
                body=next(iter(clones.values()))
                extras.append(''.join('case '+str(low+i)+':\n' for i,v in enumerate(destinations) if v==address)+body)
            if edits:
                text=source
                # Keep default in place and insert supported extra arms immediately before it.
                default=next((g[0] for g in groups if g[2] is None),end-1)
                if extras:edits.append((default,default,''.join(extras[:2])))
                for a,b,new in sorted(edits,key=lambda e:(e[0],e[1]),reverse=True):text=text[:a]+new+text[b:]
                if text!=source:result.append(('cpu-retail-switch',text,header))
    return result[:1]


def read_blocks(net, proj, fn, tables):
    reader=arches.get(proj['arch']).source_switch_blocks
    if not reader or not tables:return {}
    return reader(fn['asm'],{a for _,values in tables for a in values},fn['vram']+fn['size'])
