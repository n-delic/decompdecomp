"""Bounded MIPS search clues; displayed rows never authorize register or branch rewrites."""
import re

REG = re.compile(r'\$[A-Za-z0-9]+\b')
GPRS = {f'${p}{n}' for p,ns in [('v',range(2)),('a',range(4)),('t',range(10)),('s',range(8))] for n in ns}
BRANCHES = {'j','b','beq','bne','beqz','bnez','bgtz','blez','bltz','bgez',
            'beql','bnel','bgtzl','blezl','bltzl','bgezl','bc1t','bc1f','bc1tl','bc1fl'}

SLOTS = BRANCHES | {'jr','jal','jalr'}


def read(rows):
    empty=dict(register_chains=[],branch_target=False,delay_slot=False,return_copy_rotation=[])
    if not rows or len(rows)>16384 or any(not isinstance(r,(list,tuple)) or len(r)!=3
            or any(not isinstance(c,str) for c in r) for r in rows):return empty
    def opcode(text):return text.split()[0] if text.split() else ''
    # Legacy callers lack a processor argument. Require canonical MIPS register spelling
    # or its direct-jump opcode so ARM/PPC branch mnemonics do not invent delay slots.
    if not any(REG.search(text) or opcode(text)=='j' for r in rows for text in r[1:]):return empty
    target=False;slots=False
    for side in (1,2):
        previous=''
        for marker,left,right in rows:
            text=(left,right)[side-1]
            if not text:continue
            if marker!=' ':
                slots |= previous in SLOTS and marker in {'o','<','>','|'}
                if opcode(left) in BRANCHES and opcode(right) in BRANCHES:
                    target |= left.rsplit(',',1)[-1].split()[-1:] != right.rsplit(',',1)[-1].split()[-1:]
                elif opcode(text) in BRANCHES:target=True
            previous=opcode(text)
    chains=[];run=[]
    def flush():
        if not 2<=len(run)<=16:return
        edges={};reverse={}
        for _,left,right in run:
            if REG.sub('$reg',left)!=REG.sub('$reg',right):return
            for a,b in zip(REG.findall(left),REG.findall(right)):
                if a==b:continue
                if a not in GPRS or b not in GPRS or edges.get(a,b)!=b or reverse.get(b,a)!=a:return
                edges[a]=b;reverse[b]=a
        for start in sorted(edges.keys()-reverse.keys()):
            path=[start]
            while path[-1] in edges and len(path)<=8:
                path.append(edges[path[-1]])
            if 2<=len(path)<=8 and path[-1] not in edges and path not in chains:chains.append(path)
    for row in rows:
        if row[0]=='r':run.append(row)
        else:flush();run=[]
    flush()
    return dict(register_chains=chains[:4],branch_target=bool(target),delay_slot=bool(slots),return_copy_rotation=return_copy_rotation(rows))


def return_copy_rotation(rows):
    """A displayed three-operation rotation is a lead, never raw-byte equivalence proof."""
    from collections import Counter
    changed=[r for r in rows if len(r)==3 and r[0]!=' ']
    if not 2<=len(changed)<=6:return []
    # The diff aligns the unchanged address pair between a deleted and inserted copy.
    # Include that intervening context; unrelated symbolic operands remain unresolved.
    moved=[i for i,r in enumerate(rows) if len(r)==3 and r[0]=='o' and (not r[1] or not r[2])]
    window=changed
    if len(moved)==2 and moved[1]-moved[0]<=3:
        window=rows[moved[0]:moved[1]+1]
    left=[r[1].strip() for r in window if r[1]]
    right=[r[2].strip() for r in window if r[2]]
    if len(left)!=3 or left==right or Counter(left)!=Counter(right):return []
    copies=[re.fullmatch(r'move\s+(\$\w+),\s*\$v0',s) for s in left]
    copies=[m for m in copies if m]
    loads=[re.fullmatch(r'lui\s+(\$\w+),\s*.+',s) for s in left]
    loads=[m for m in loads if m]
    if len(copies)!=1 or len(loads)!=1 or copies[0][1]==loads[0][1]:return []
    pointer=loads[0][1]
    if not any(re.fullmatch(r'addiu\s+'+re.escape(pointer)+r',\s*'+re.escape(pointer)+r',\s*.+',s) for s in left):return []
    return [copies[0][1],pointer]
