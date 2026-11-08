"""Explicit loader arguments can separate code banks a statistical placement merged.

Require two different callees to receive the same ROM/RAM/length triples, and
three adjacent transfers. A call-address histogram alone cannot prove these cuts.
"""
from collections import defaultdict
from itertools import permutations

from ...arch.mips import _mips_uses, is_frame


def transfers(image, taken):
    """Read only constants in straight-line known code, including call delay slots."""
    calls=[]
    for start,end,vram in taken:
        joins=set()
        for at in range(start,min(end,len(image))-3,4):
            w=int.from_bytes(image[at:at+4],'big');op=w>>26
            if op in (1,4,5,6,7,20,21,22,23) or op==17 and (w>>21)&31==8:
                imm=((w&0xffff)^0x8000)-0x8000
                joins.add(at+4+imm*4)
            elif op in (2,3):
                target=((vram+at-start+4)&0xf0000000)|((w&0x3ffffff)<<2)
                joins.add(start+target-vram)
        values={0:0};pending=None;reset=False
        for at in range(start,min(end,len(image))-3,4):
            w=int.from_bytes(image[at:at+4],'big')
            op=w>>26;rs=(w>>21)&31;rt=(w>>16)&31;rd=(w>>11)&31;fun=w&63
            imm=(w&0xffff)^0x8000;imm-=0x8000
            if is_frame(w) or at in joins:values={0:0}
            value=None;dest=None
            if op==15:dest=rt;value=(w&0xffff)<<16
            elif op in (9,13):
                dest=rt
                if rs in values:value=values[rs]+imm if op==9 else values[rs]|(w&0xffff)
            elif op==0 and fun in (33,35,37):
                dest=rd
                if rs in values and rt in values:
                    a,b=values[rs],values[rt]
                    value=a+b if fun==33 else a-b if fun==35 else a|b
            uses=_mips_uses(w,'')
            if uses is None:values={0:0}
            else:
                for reg in uses[1]-{0}:values.pop(reg,None)
            if dest and value is not None:values[dest]=value&0xffffffff
            if pending is not None:
                calls.append((pending,tuple(values.get(r) for r in range(4,8))))
                values={r:v for r,v in values.items() if r==0 or 16<=r<=23 or r in (28,29,30)}
                pending=None
            elif reset:values={0:0};reset=False
            if op==3:pending=((vram+at-start+4)&0xf0000000)|((w&0x3ffffff)<<2)
            elif op in (1,2,4,5,6,7,20,21,22,23) or op==0 and fun in (8,9) or op==17 and rs==8:reset=True
    evidence=defaultdict(set)
    for callee,args in calls:
        for source,destination,length in permutations(range(4),3):
            lo,ram,size=args[source],args[destination],args[length]
            if (lo is None or ram is None or size is None or lo%4 or ram%4 or size%4
                    or not 0x1000<=lo<lo+size<=len(image) or size<64
                    or not 0x80000000<=ram<ram+size<=0x80800000
                    or any(a<lo+size and lo<b for a,b,_ in taken)):continue
            evidence[(lo,lo+size,ram)].add((callee,source,destination,length))
    # Two independently called wrappers must agree; repeated calls to one helper
    # or conflicting argument layouts are insufficient.
    agreed={t for t,sites in evidence.items() if len({s[0] for s in sites})>=2}
    result=set()
    for signature in {s for t in agreed for s in evidence[t]}:
        chain=[]
        for t in sorted(t for t in agreed if signature in evidence[t]):
            if chain and chain[-1][1]!=t[0]:
                if len(chain)>=3:result.update(chain)
                chain=[]
            chain.append(t)
        if len(chain)>=3:result.update(chain)
    return sorted(result)


def refine(image, segments, taken=(), log=print):
    """Split only discovered code covered by unambiguous explicit loader ranges."""
    ranges=transfers(image,taken)
    if not ranges:return segments
    # Overlapping copies can be overlays or alternate destinations. They require
    # explicit support, rather than silently choosing one in an address-keyed project.
    safe=[t for t in ranges if not any(t!=s and (s[0]<t[1] and t[0]<s[1]
          or s[2]<t[2]+t[1]-t[0] and t[2]<s[2]+s[1]-s[0]) for s in ranges)
          and not any(v<t[2]+t[1]-t[0] and t[2]<v+b-a for a,b,v in taken)]
    result=[];changed=0
    for lo,hi,vram in segments:
        cuts=sorted({lo,hi}|{p for a,b,_ in safe for p in (a,b) if lo<p<hi})
        for a,b in zip(cuts,cuts[1:]):
            owners=[r for r in safe if r[0]<=a<b<=r[1]]
            base=owners[0][2]+a-owners[0][0] if len(owners)==1 else vram+a-lo
            changed+=base!=vram+a-lo
            result.append((a,b,base))
    # Do not let a retained statistical mapping overlap an explicit destination.
    result=[t for t in result if any(a<=t[0]<t[1]<=b and t[2]-t[0]==v-a for a,b,v in safe)
            or not any(v<t[2]+t[1]-t[0] and t[2]<v+b-a for a,b,v in safe)]
    if changed:log(f'Recovered explicit load mappings for {changed} code ranges from corroborated loader calls.')
    return result
