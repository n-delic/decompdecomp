"""Advisory one-word branch-label movement around a NOP; never control-flow equivalence."""
import re
from collections import Counter


def rows_witness(rows):
    if not rows or any(not isinstance(r,(list,tuple)) or len(r)!=3 or not all(isinstance(x,str) for x in r) for r in rows):return False
    changed=[(a,b) for m,a,b in rows if m!=' ']
    if not 1<=len(changed)<=8 or not any('nop' in (a.strip(),b.strip()) for _,a,b in rows):return False
    branches=0
    def normalize(s):return re.sub(r'\s+','',s)
    left=[];right=[]
    for a,b in changed:
        # Only rendered local numeric destinations, with identical opcode and test operands.
        pattern=r'(.+?\s+)(0x[0-9a-fA-F]+|[A-Za-z_]\w*\+0x[0-9a-fA-F]+|\.L[0-9a-fA-F]+)$'
        x=re.fullmatch(pattern,a.strip());y=re.fullmatch(pattern,b.strip())
        if x and y and x[1].strip().split()[0] in {'b','j','beq','bne','beqz','bnez','bgez','bgtz','blez','bltz','bc1t','bc1f'}:
            prefix=lambda v:'.L' if v.startswith('.L') else v.rsplit('0x',1)[0]
            value=lambda v:int(v[2:] if v.startswith('.L') else v.rsplit('0x',1)[1],16)
            if normalize(x[1])!=normalize(y[1]) or prefix(x[2])!=prefix(y[2]):return False
            if abs(value(x[2])-value(y[2]))!=4:return False
            branches+=1;left.append(normalize(x[1])+'LABEL');right.append(normalize(y[1])+'LABEL')
        else:
            left.append(normalize(a));right.append(normalize(b))
    return bool(branches) and Counter(filter(None,left))==Counter(filter(None,right))
