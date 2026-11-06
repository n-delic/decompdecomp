"""Instantiate small verified MIPS wrappers whose only varying operand is one global address.

Grouping is a shortlist, never a match. Every candidate uses the destination's ordinary byte judge;
the address substitution is private to this function, not a learned project-wide symbol mapping.
"""
import json
import re

from ..forks.csource import _blank, find
from .similar_matches import TOKENS

MAX_BYTES = 96
MAX_DONORS = 3


def shape(fn, big):
    code=fn['target'] or b''
    if not 16<=len(code)<=MAX_BYTES or len(code)%4:return None
    relocs=json.loads(fn['relocs'] or '[]')
    if len(relocs)!=3:return None
    high=[r for r in relocs if r[1]==5];low=[r for r in relocs if r[1]==6];call=[r for r in relocs if r[1]==4]
    if len(high)!=1 or len(low)!=1 or len(call)!=1:return None
    hi,lo,jump=high[0],low[0],call[0]
    if hi[2]!=lo[2] or lo[0]!=hi[0]+4 or any(r[0]%4 or not 0<=r[0]<=len(code)-4 for r in relocs):return None
    words=[int.from_bytes(code[i:i+4],'big' if big else 'little') for i in range(0,len(code),4)]
    h,l,j=words[hi[0]//4],words[lo[0]//4],words[jump[0]//4]
    register=(h>>16)&31
    if h>>26!=15 or l>>26 not in (9,13) or (l>>21)&31!=register or (l>>16)&31!=register or j>>26!=3:return None
    # One direct call, with no other branches, indirect calls or jumps except the final return.
    for word in words:
        op=word>>26
        if op in (1,2,4,5,6,7,20,21,22,23) or (op==0 and word&63 in (8,9) and word!=0x03e00008):return None
    if sum(w>>26==3 for w in words)!=1:return None
    for offset in (hi[0],lo[0]):words[offset//4]&=0xffff0000
    return (tuple(words),tuple(r[0] for r in (hi,lo,jump)),jump[2]),hi[2]


def index(net,proj):
    if proj['arch']!='mips':return {}
    groups={}
    for row in net.store.all("SELECT * FROM functions WHERE project_id=? AND status='matched' AND matched_source IS NOT NULL AND size<=? ORDER BY id",proj['id'],MAX_BYTES):
        key=shape(row,bool(proj['big_endian']))
        if key:
            group=groups.setdefault(key[0],[])
            if len(group)<MAX_DONORS:group.append(row)
    return groups


def candidates(net,proj,fn,groups):
    key=shape(fn,bool(proj['big_endian']))
    if not key:return []
    symbols=net.symbols(proj['id']);out=[]
    for donor in groups.get(key[0],[]):
        if donor['id']==fn['id']:continue
        source=donor['matched_source'];part=find(source,donor['name'])
        if not part or len(source)>65536:continue
        # A multi-function source is not a reusable wrapper template.
        if re.search(r'\)\s*{',_blank(source[:part[0]]+source[part[1]:])):continue
        old_address=shape(donor,bool(proj['big_endian']))[1]
        if old_address==key[1]:continue
        clean=_blank(source)
        names={m[0] for m in re.finditer(r'\b[A-Za-z_]\w*\b',clean) if symbols.resolve(m[0])==old_address}
        if len(names)!=1:continue
        old=next(iter(names))
        # Only declared address values qualify; a scalar read is a different source hypothesis.
        declared=re.search(rf'\bextern\s+[^;{{}}]+\b{re.escape(old)}\s*(?P<array>\[[^;]*\])?\s*;',clean)
        if not declared or (not declared['array'] and len(re.findall(rf'&\s*{re.escape(old)}\b',clean))!=1):continue
        destination=symbols.label(key[1])
        if not re.fullmatch(r'[A-Za-z_]\w*',destination):continue
        mapping={donor['name']:fn['name'],old:destination}
        candidate=TOKENS.sub(lambda m,mapping=mapping:mapping.get(m[0],m[0]),source)
        out.append(('wrapper-family','verified wrapper '+donor['name'],candidate))
    return out
