"""Target-text MIPS control-flow partitions and explicit within-block register dependencies.

Delay instructions stay with their transfer. Unknown direct destinations/transfer forms decline
analysis; this reader supplies search hints, never instruction decoding or acceptance evidence.
"""
import re

CONDITIONAL=set('beq bne beqz bnez bgtz blez bltz bgez beql bnel beqzl bnezl bgtzl blezl bltzl bgezl bc1t bc1f bc1tl bc1fl'.split())
CALLS={'jal','jalr','bal','bgezal','bltzal','bgezall','bltzall'}
JUMPS={'b','j','jr'}
LOADS=set('lb lbu lh lhu lw lwu ld lwc1 ldc1 lui li la'.split())
STORES=set('sb sh sw sd swc1 sdc1'.split())
ALU=set('move addu addiu add subu sub slt sltu slti sltiu and andi or ori xor xori nor sll sllv srl srlv sra srav dsll dsrl dsra mfhi mflo negu neg not'.split())


def read(targets):
    if not targets or len(targets)>8192:return None
    instructions=[];leaders={0};transfers={};delay=set()
    for i,text in enumerate(targets):
        parts=text.strip().split(None,1)
        if not parts:return None
        op=parts[0];args=parts[1] if len(parts)>1 else ''
        instructions.append((op,args))
        if ((op.startswith(('b','j')) and op not in CONDITIONAL|CALLS|JUMPS)
                or op.startswith('.') or op in {'eret','deret','syscall','break','invalid','teq','tne','tge','tlt'}):return None
        if op not in CONDITIONAL|CALLS|JUMPS:continue
        if i+1>=len(targets) or i in delay:return None
        delay.add(i+1)
        if i+2<len(targets):leaders.add(i+2)
        destination=None
        if op!='jr' and op not in CALLS:
            match=re.search(r'(?:^|,)\s*(0x[0-9a-fA-F]+|\d+)\s*$',args)
            if not match:return None
            address=int(match[1],0 if match[1].startswith('0x') else 10)
            if address%4 or not 0<=address//4<len(targets):return None
            destination=address//4;leaders.add(destination)
        transfers[i]=(op,destination)
    if leaders & delay:return None  # an incoming edge into a delay slot needs a richer graph
    starts=sorted(leaders);block_for=[]
    for index,start in enumerate(starts):block_for += [start]*((starts[index+1] if index+1<len(starts) else len(targets))-start)
    successors={};dependencies=[]
    for index,start in enumerate(starts):
        end=starts[index+1] if index+1<len(starts) else len(targets)
        transfer=transfers.get(end-2);out=[]
        if transfer:
            op,dest=transfer
            if dest is not None:out.append(dest)
            if op in CONDITIONAL|CALLS and end<len(targets):out.append(end)
        elif end<len(targets):out.append(end)
        successors[str(start)]=sorted(set(out))
        definitions={}
        for i in range(start,end):
            op,args=instructions[i];regs=re.findall(r'\$([A-Za-z0-9]+)',args)
            if op in CALLS:definitions.clear();continue
            if op in LOADS|ALU:
                if not regs:definitions.clear();continue
                written=regs[:1];used=regs[1:]
            elif op in STORES|CONDITIONAL|JUMPS:written=[];used=regs
            elif op=='nop':continue
            else:definitions.clear();continue
            for register in used:
                if register in definitions:dependencies.append((definitions[register],i,register))
            for register in written:
                if register!='zero':definitions[register]=i
    return dict(block_for=block_for,successors=successors,dependencies=dependencies)
