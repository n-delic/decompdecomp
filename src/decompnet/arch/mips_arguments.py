"""Conservative register-position evidence for ordinary MIPS integer argument repairs."""

ARGS = range(4,8)
IMMEDIATE = {8,9,10,11,12,13,14,32,33,35,36,37,40,41,43}


def evidence(candidate, target, relocated, big):
    if not candidate or len(candidate)!=len(target) or len(candidate)%4:
        return None
    order='big' if big else 'little'
    left=[int.from_bytes(candidate[i:i+4],order) for i in range(0,len(candidate),4)]
    right=[int.from_bytes(target[i:i+4],order) for i in range(0,len(target),4)]
    calls=[i for i,w in enumerate(left) if w>>26 in (2,3)]
    differences=[]
    for i,(x,y) in enumerate(zip(left,right)):
        if x==y:
            continue
        if i*4 in relocated:
            if x>>26 != y>>26:
                return None
            continue
        op=x>>26
        kind=old=new=None
        if op in IMMEDIATE and x & ~0x1f0000 == y & ~0x1f0000 and op not in {40,41,43}:
            old,new=x>>16&31,y>>16&31
            kind='outgoing'
        elif op==0 and x&63 in (0x21,0x25) and x & ~0xf800 == y & ~0xf800:
            old,new=x>>11&31,y>>11&31
            kind='outgoing'
        elif op in IMMEDIATE and x & ~0x3e00000 == y & ~0x3e00000:
            old,new=x>>21&31,y>>21&31
            kind='incoming'
        elif op==0 and x&63 in (0x21,0x25) and not (x>>16&31) and x & ~0x3e00000 == y & ~0x3e00000:
            old,new=x>>21&31,y>>21&31
            kind='incoming'
        elif op==0 and x&63 in (0x21,0x25) and not (x>>21&31) and x & ~0x1f0000 == y & ~0x1f0000:
            old,new=x>>16&31,y>>16&31
            kind='incoming'
        if old not in ARGS or new not in ARGS or new<=old:
            return None
        if kind=='incoming':
            # Only reads of live incoming values, before calls and before either register is overwritten.
            for w in left[:i]:
                opcode=w>>26
                if opcode in (1,2,3,4,5,6,7) or opcode==0 and w&63 in (8,9):
                    return None
                if opcode not in ({0,15,49,53,57,61}|IMMEDIATE) or opcode==0 and w&63 in (12,13):
                    return None
                dest=(w>>11&31) if opcode==0 else (w>>16&31) if opcode in (IMMEDIATE-{40,41,43})|{15} else None
                if dest in (old,new):
                    return None
        else:
            # Single-call wrappers only, with argument setup immediately before/in the delay slot.
            if len(left)>64 or len(calls)!=1 or not calls[0]-8<=i<=calls[0]+1:
                return None
            if any(w>>26 in (1,4,5,6,7) for w in left):
                return None
        differences.append((kind,new-old,old-4))
    if not differences or len(differences)>4 or len({(k,d) for k,d,_ in differences})!=1:
        return None
    kind,delta,_=differences[0]
    return kind,delta,{old for _,_,old in differences}
