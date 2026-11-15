"""Recover closed CPU bodies throughout corroborated loader banks, including quiet gaps.

A loaded bank can also contain assets. Require calls, materialized callbacks,
corroborated callees or contiguous accepted code; unrelated patterns stay raw.
"""
from bisect import bisect_right
from collections import defaultdict

from ...arch.mips import is_frame
from ...arch.mips_code_recovery import called_extent, leaf_extent
from ...arch.mips_references import references


def complete(image, segments, banks, taken=(), log=print):
    """Scan every unowned bank interval, then follow newly exposed calls to a fixed point."""
    banks=sorted(banks)
    spans=sorted(list(segments)+list(taken))
    covered=sorted((a,b) for a,b,_ in spans)
    gaps=[]
    for lo,hi,vram in banks:
        cursor=lo
        for a,b in covered:
            if b<=cursor or a>=hi:continue
            if cursor<a:gaps.append((cursor,min(a,hi),vram+cursor-lo))
            cursor=max(cursor,b)
        if cursor<hi:gaps.append((cursor,hi,vram+cursor-lo))
    if not gaps:return sorted(segments)
    starts=[a for a,_,_ in gaps]
    check=getattr(log,'check_cancel',lambda:None)
    calls=set();entries=set();incoming=defaultdict(set)
    def read_calls(lo,hi,vram):
        for target,at,kind in references(image,lo,hi,vram):
            incoming[target].add(at)
            if kind=='call':calls.add(target)
            if kind in ('call','address'):entries.add(target)
    for a,b,v in spans:read_calls(a,b,v)
    def independent(a,b,v):
        return any(v<t<v+b-a and any(not a<=at<b for at in sites) for t,sites in incoming.items())
    # Validate all frames, not just the first N hits or most common mappings.
    bodies={}
    for lo,hi,vram in gaps:
        check()
        for at in range(lo,hi-3,4):
            if at%0x1000==0:check()
            if not is_frame(int.from_bytes(image[at:at+4],'big')):continue
            size=called_extent(image,at,hi,vram+at-lo)
            if size:bodies[at]=(at,at+size,vram+at-lo)
    # A quiet framed function can be dead code or a callback. Its own calls to
    # independently established CPU callees corroborate it even without an incoming
    # edge. Require an RA spill/restore too; an asset's frame-like word is insufficient.
    callee_cache={}
    def closed_callee(target):
        if target not in callee_cache:
            owners=[(lo,hi,ram) for lo,hi,ram in list(banks)+list(taken) if ram<=target<ram+hi-lo]
            valid=False
            if len(owners)==1:
                lo,hi,ram=owners[0];at=lo+target-ram
                valid=bool(called_extent(image,at,hi,target) or leaf_extent(image,at,hi,target))
            callee_cache[target]=valid
        return callee_cache[target]
    def linked_frame(a,b,v):
        words=[int.from_bytes(image[at:at+4],'big') for at in range(a,b,4)]
        amount=0x10000-(words[0]&0xffff)
        spills={w&0xffff for w in words if w&0xffff0000==0xafbf0000 and (w&0xffff)<amount}
        restores={w&0xffff for w in words if w&0xffff0000==0x8fbf0000}
        return bool(spills&restores) and any(kind=='call' and (target in calls or closed_callee(target))
            and not v<=target<v+b-a for target,_,kind in references(image,a,b,v))
    accepted={};examined=set()
    while True:
        check();before=len(accepted)
        # Only the actual bank owning a destination can supply its ROM offset.
        for target in sorted(entries-examined):
            examined.add(target)
            owners=[(lo,hi,v) for lo,hi,v in banks if v<=target<v+hi-lo]
            if len(owners)!=1:continue
            lo,_,v=owners[0];at=lo+target-v;i=bisect_right(starts,at)-1
            if i<0 or not gaps[i][0]<=at<gaps[i][1]:continue
            if at not in bodies:
                size=leaf_extent(image,at,gaps[i][1],target)
                if size:bodies[at]=(at,at+size,target)
        # Grow anchored runs in either direction. Padding is allowed only when
        # it is zero and no direct call names an interior instruction.
        anchors=list(spans)+list(accepted.values())
        pending={at:body for at,body in bodies.items() if at not in accepted}
        while pending:
            progress=False
            for at,body in list(pending.items()):
                a,b,v=body;mapping=v-a
                if independent(a,b,v):continue
                anchored=v in entries or linked_frame(a,b,v) or any(base-start==mapping and
                    (0<=a-end<=12 and not any(image[end:a]) or
                     0<=start-b<=12 and not any(image[b:start])) for start,end,base in anchors)
                if not anchored:continue
                if any(x<b and a<y and (x,y,base)!=body for x,y,base in accepted.values()):continue
                accepted[at]=body;anchors.append(body);del pending[at];progress=True
                read_calls(*body)
            if not progress:break
        if len(accepted)==before:break
    # Later callees may expose an independent interior entry. Never let iteration
    # order select one overlapping body or swallow such a destination.
    result=[r for r in accepted.values() if not independent(*r)
            and not any(r!=s and r[0]<s[1] and s[0]<r[1] for s in accepted.values())]
    if result:log(f'Recovered {len(result)} closed CPU bodies across full verified loader ranges; other loaded bytes remain raw.')
    return sorted(list(segments)+result)
