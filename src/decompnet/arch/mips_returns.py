"""Conservative straight-line MIPS result and stack delay-slot witnesses, never verdicts."""
import re

from . import mips_regions as flow


def instructions(asm):
    if not isinstance(asm,str):return []
    return [line.split('*/',1)[-1].strip().split('#',1)[0].strip() for line in asm.splitlines()
            if re.search(r'/\*\s*[0-9A-Fa-f]+\s+[0-9A-Fa-f]{8}\s+[0-9A-Fa-f]{8}\s*\*/',line)]


def result(asm, caller='', name=''):
    text=instructions(asm)
    if not text or len(text)>8192:return None
    last=None;returns=0
    for i,line in enumerate(text):
        parts=line.split(None,1)
        if not parts:return None
        op=parts[0];args=parts[1] if len(parts)>1 else ''
        regs=re.findall(r'\$(\w+)',args)
        if op in flow.CONDITIONAL or op in {'j','b','jalr'}:return None
        if op=='jr':
            if regs!=['ra'] or i!=len(text)-2:return None
            returns+=1
        if op in flow.CALLS:last=None
        elif op in flow.ALU|flow.LOADS|{'mfc1','dmfc1','cfc1'}:
            if regs and regs[0]=='v0':
                previous=text[i-1].split(None,1)[0] if i else ''
                last=None if previous in flow.CALLS else (i,op,regs)
        elif op not in flow.STORES|{'jr','nop','mtc1','dmtc1','ctc1'}:
            last=None  # an unknown instruction may clobber an implicit result register
    # A load can also supply the result, but may merely leave a scratch in v0.
    # Expose it for caller lookup, never as a caller-free source rewrite witness.
    if returns!=1 or not last:return None
    loaded=last[1]=='lw'
    if not loaded:
        if last[1] not in {'move','addu','or'}:return None
        regs=last[2]
        if len(regs)<2 or regs[1] in {'zero','ra','sp','v0'} or (len(regs)==3 and regs[2]!='zero'):return None
    consumed=False
    if caller and name:
        rows=instructions(caller)
        for i,line in enumerate(rows):
            if not re.fullmatch(r'jal\s+'+re.escape(name),line):continue
            # The delay slot precedes the result. Stop at the first control transfer or overwrite.
            for use in rows[i+2:i+10]:
                parts=use.split(None,1);op=parts[0];r=re.findall(r'\$(\w+)',parts[1] if len(parts)>1 else '')
                if op in flow.CALLS|flow.CONDITIONAL|flow.JUMPS:break
                if op in flow.ALU|flow.LOADS|{'mfc1'}:
                    if 'v0' in r[1:]:consumed=True;break
                    if r and r[0]=='v0':break
                elif op in flow.STORES|{'mtc1'}:
                    if 'v0' in r:consumed=True;break
                elif op!='nop':break
    proof=dict(bits=32,register='v0',caller_consumes=consumed)
    if loaded:proof['requires_caller']=True
    return proof


def stack_call_store(asm):
    rows=instructions(asm)
    return any(re.match(r'(?:jal|jalr|bal)\b',a) and re.fullmatch(r'swc1\s+\$\w+,\s*(?:0x[0-9a-fA-F]+|\d+)\(\$sp\)',b)
               for a,b in zip(rows,rows[1:]))
