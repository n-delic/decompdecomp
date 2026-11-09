"""Bounded MIPS return-register paths, including delay slots; hypotheses, never C contracts."""
import re
from collections import deque

from . import mips_regions as flow
from .mips_returns import instructions

LIMIT = 4096


def graph(asm):
    rows=instructions(asm)
    if not rows or len(rows)>8192:return None
    labels={};addresses={};index=0
    for line in asm.splitlines():
        label=re.fullmatch(r'\s*([.$\w]+):\s*',line)
        if label:labels[label[1]]=index
        address=re.search(r'/\*\s*[0-9A-Fa-f]+\s+([0-9A-Fa-f]{8})\s+[0-9A-Fa-f]{8}\s*\*/',line)
        if address:addresses[int(address[1],16)]=index;index+=1
    destinations={};delays=set()
    for i,row in enumerate(rows):
        op=row.split()[0]
        if op in {'eret','deret','syscall','teq','tne','tge','tlt'}:return None
        if op=='jr' and not re.fullmatch(r'jr\s+\$ra',row):return None
        if op in flow.CONDITIONAL|flow.CALLS|flow.JUMPS:
            if i+1>=len(rows):return None
            delays.add(i+1)
        if op in flow.CONDITIONAL|{'b','j'}:
            dest=row.replace(',',' ').split()[-1]
            target=labels.get(dest)
            if target is None:
                try:target=addresses.get(int(dest,0))
                except ValueError:return None
            if target is None or target>=len(rows):return None
            destinations[i]=target
        elif op.startswith(('b','j')) and op not in flow.CALLS|flow.JUMPS|{'break'}:return None
        if op in flow.CALLS-{'jal','jalr','bal'}:return None
    if set(destinations.values())&delays:return None
    if any(rows[i].split()[0] in flow.CONDITIONAL|flow.CALLS|flow.JUMPS|{'break'} for i in delays):return None
    return rows,destinations


def transfer(origin, row, index):
    op=row.split()[0];regs=re.findall(r'\$(\w+)',row)
    if op in flow.ALU|flow.LOADS|{'mfc1','cfc1'}:
        if regs and regs[0]=='v0':return index
    elif op not in flow.CONDITIONAL|flow.CALLS|flow.JUMPS|flow.STORES|{'nop','mtc1','mult','multu','div','divu','mthi','mtlo'}:
        return -1  # unknown implicit clobber
    return origin


def read(asm, caller='', name=''):
    decoded=graph(asm)
    if not decoded:return None
    rows,destinations=decoded
    queue=deque([(0,-1)]);seen=set();exits={};branch_values={}
    while queue:
        position,origin=queue.popleft()
        if (position,origin) in seen:continue
        seen.add((position,origin))
        if len(seen)>LIMIT or position>=len(rows):return None
        op=rows[position].split()[0]
        if op=='break':continue  # traps terminate; they are not C-return paths
        if op in flow.CONDITIONAL|flow.CALLS|flow.JUMPS:
            delayed=transfer(origin,rows[position+1],position+1)
            if op=='jr':exits.setdefault(position,set()).add(delayed)
            elif op in flow.CALLS:queue.append((position+2,-1))
            elif op in {'b','j'}:queue.append((destinations[position],delayed))
            else:
                branch_values.setdefault(position,set()).add(origin)
                queue.append((destinations[position],delayed))
                likely=op.endswith('l')
                queue.append((position+2,origin if likely else delayed))
        else:queue.append((position+1,transfer(origin,rows[position],position)))
    if not exits:return None
    guarded=[]
    for branch,target in destinations.items():
        match=re.fullmatch(r'(bgtz|blez)\s+\$v0,\s*(\S+)',rows[branch])
        if not match or target<=branch or not branch_values.get(branch) or -1 in branch_values[branch]:continue
        if transfer(branch,rows[branch+1],branch+1)!=branch:continue
        position=target;valid=False
        for _ in range(64):
            if position>=len(rows):break
            op=rows[position].split()[0]
            if op=='jr':
                valid=transfer(branch,rows[position+1],position+1)==branch
                break
            if op in flow.CONDITIONAL|flow.CALLS|flow.JUMPS|{'break'}:break
            if transfer(branch,rows[position],position)!=branch:break
            position+=1
        if valid:guarded.append(dict(instruction=branch,condition='> 0' if match[1]=='bgtz' else '<= 0',exit=position))
    return dict(register='v0',bits=32,truncated=len(exits)>8 or any(len(v)>8 for v in exits.values()),exits=[dict(instruction=i,definitions=sorted(v)[:8],ambiguous=len(v)>1) for i,v in sorted(exits.items())][:8],
                guarded_exits=guarded[:4],caller_uses=caller_use(caller,name) if caller and name else [],
                note='Reaching definitions and observed direct calls only; scratch results and indirect callers do not establish a defined C return on every path.')


def caller_use(asm,name):
    rows=instructions(asm);out=[]
    for i,row in enumerate(rows):
        if not re.fullmatch(r'jal\s+'+re.escape(name),row):continue
        status='unknown'
        for at,after in enumerate(rows[i+2:i+66],i+2):
            op=after.split()[0];regs=re.findall(r'\$(\w+)',after)
            if op in flow.CALLS:
                slot=rows[at+1] if at+1<len(rows) else ''
                parts=slot.split();registers=re.findall(r'\$(\w+)',slot)
                if not parts or parts[0] not in flow.ALU|flow.LOADS|flow.STORES|{'nop','mtc1'}:break
                reads=registers[1:] if parts[0] in flow.ALU|flow.LOADS else registers
                status='consumed' if 'v0' in reads else 'discarded'
                break
            if op in flow.CONDITIONAL|flow.STORES|{'mtc1'} and 'v0' in regs:status='consumed';break
            if op in flow.ALU|flow.LOADS:
                if 'v0' in regs[1:]:status='consumed';break
                if regs and regs[0]=='v0':status='discarded';break
            elif op in flow.JUMPS|flow.CONDITIONAL:break
            elif op not in flow.STORES|{'nop','mtc1'}:break
        out.append(dict(call=i,use=status))
    return out[:8]
