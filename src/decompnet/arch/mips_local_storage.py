"""Small complete-row MIPS stack/register residuals; hints, never equivalence proofs."""
import re


def scalar_destination(instruction):
    """A saved-register definition, for source-line ranking rather than variable identity."""
    hit=re.match(r'(?:move|addiu|addu|subu|lui|ori|andi|sll|sra|srl|lw|lh|lhu|lb|lbu|sltu?|sltiu?)\s+(\$(?:s[0-7]|fp)),',instruction)
    return hit[1] if hit else None


def read(rows):
    if not rows or any(not isinstance(r,(tuple,list)) or len(r)!=3 or not all(isinstance(x,str) for x in r) for r in rows):return None
    differences=[r for r in rows if r[0]!=' ']
    if not 1<=len(differences)<=6:return None
    if all(mark=='s' for mark,_,_ in differences):
        frames=[]
        for side in [1,2]:
            values=[int(m[1],0) for r in rows if (m:=re.fullmatch(r'addiu \$sp, \$sp, (-0x[0-9a-f]+|-\d+)',r[side]))]
            if len(values)!=1:return None
            frames.append(-values[0])
        if frames[0]!=frames[1]:return None
        slots=set();ops=[]
        for _,a,b in differences:
            pattern=r'(lw|sw) \$(\w+), (0x[0-9a-f]+|\d+)\(\$sp\)'
            x=re.fullmatch(pattern,a);y=re.fullmatch(pattern,b)
            if not x or not y or (x[1],x[2])!=(y[1],y[2]) or x[2] in {'sp','ra','gp','zero'}:return None
            old,new=int(y[3],0),int(x[3],0)
            if old==new or any(v%4 or not 16<=v<frames[0] for v in [old,new]):return None
            slots.add((old,new,x[2]));ops.append(x[1])
        if len(slots)!=1 or sorted(ops)!=['lw','sw']:return None
        old,new,reg=slots.pop()
        return dict(kind='spill',old=old,new=new,register=reg,frame=frames[0])
    if all(mark=='r' for mark,_,_ in differences):
        pairs=set()
        for _,a,b in differences:
            left=re.findall(r'\$\w+',a);right=re.findall(r'\$\w+',b)
            if len(left)!=len(right) or re.sub(r'\$\w+','REG',a)!=re.sub(r'\$\w+','REG',b):return None
            pairs.update((x,y) for x,y in zip(left,right) if x!=y)
        if len(pairs)!=1:return None
        target,candidate=pairs.pop()
        if any(not re.fullmatch(r'\$(?:v[01]|t[0-9]|s[0-7])',r) for r in [target,candidate]):return None
        return dict(kind='register',target=target,candidate=candidate)
    return None
