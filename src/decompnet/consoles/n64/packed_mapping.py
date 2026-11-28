"""Recover linked addresses inside an independently compressed code block."""
import struct
from collections import Counter

from ...arch.mips import JR_RA, is_frame
from ...arch.mips_code_recovery import called_extent, leaf_extent

_MIN_CLOSED=8
_MAX_PAIRS=1_000_000


def _mapping(image,lo,hi,check):
    """Require a dominant address hypothesis and several separately closed called bodies."""
    words=struct.unpack_from(f'>{(hi-lo)//4}I',image,lo)
    targets={0x80000000|((word&0x3ffffff)<<2) for word in words if word>>26==3}
    entries=[lo+i*4 for i,word in enumerate(words) if is_frame(word) or i>=2 and words[i-2]==JR_RA]
    if len(targets)<_MIN_CLOSED or len(entries)<_MIN_CLOSED or len(targets)*len(entries)>_MAX_PAIRS:return None
    scores=Counter()
    for target in targets:
        check()
        for at in entries:
            delta=target-at;base=lo+delta
            if delta%16==0 and 0x80000400<=base<base+hi-lo<=0x90000000:scores[delta]+=1
    ranked=scores.most_common(2)
    if not ranked or ranked[0][1]<_MIN_CLOSED or len(ranked)>1 and ranked[0][1]<4*ranked[1][1]:return None
    delta=ranked[0][0];closed={}
    for target in targets:
        check()
        at=target-delta
        if lo<=at<hi-8:
            size=called_extent(image,at,hi,target,cpu='vr4300') or leaf_extent(image,at,hi,target,cpu='vr4300')
            if size:closed[at]=size
    return (delta,closed) if len(closed)>=_MIN_CLOSED else None


def recover(image,found,blocks,taken=(),check=lambda:None):
    """Correct only fragments with their own closed call target, never a declared bank."""
    result=list(found);changed=set()
    for lo,hi in blocks:
        check()
        if not 0<=lo<hi<=len(image) or (lo|hi)%4:continue
        if not (mapping:=_mapping(image,lo,hi,check)):continue
        delta,closed=mapping
        for index,(start,end,old) in enumerate(result):
            check()
            if not lo<=start<end<=hi or old-start==delta:continue
            if not any(start<=at and at+size<=end for at,size in closed.items()):continue
            words=struct.unpack_from(f'>{(end-start)//4}I',image,start)
            calls={0x80000000|((word&0x3ffffff)<<2) for word in words if word>>26==3}
            conflict=False
            for target in calls:
                check()
                if old<=target<old+end-start and (called_extent(image,start+target-old,end,target,cpu='vr4300') or
                                                   leaf_extent(image,start+target-old,end,target,cpu='vr4300')):
                    conflict=True;break
            if conflict:continue
            base=start+delta
            if any(v<base+end-start and base<v+b-a for a,b,v in taken):continue
            result[index]=(start,end,base);changed.add(start)
    return result,changed
