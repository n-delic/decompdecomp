"""Bounded MIPS search clues; displayed rows never authorize register or branch rewrites."""
import re

REG = re.compile(r'\$[A-Za-z0-9]+\b')
GPRS = {f'${p}{n}' for p,ns in [('v',range(2)),('a',range(4)),('t',range(10)),('s',range(8))] for n in ns}
BRANCHES = {'j','b','beq','bne','beqz','bnez','bgtz','blez','bltz','bgez',
            'beql','bnel','bgtzl','blezl','bltzl','bgezl','bc1t','bc1f','bc1tl','bc1fl'}

SLOTS = BRANCHES | {'jr','jal','jalr'}


def read(rows):
    empty=dict(register_chains=[],branch_target=False,delay_slot=False)
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
    return dict(register_chains=chains[:4],branch_target=bool(target),delay_slot=bool(slots))
