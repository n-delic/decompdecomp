"""Named-buffer/literal hypotheses using an existing declaration and target address evidence."""
import re

from .integer_repair_support import edit, view
from .sdk_io_repairs import instructions

FAMILIES = ('proven_buffer_address',)


def addresses(asm):
    words=instructions(asm);result=set()
    for i,word in enumerate(words):
        if word>>26!=15:continue
        reg=(word>>16)&31;base=(word&65535)<<16
        for nxt in words[i+1:i+4]:
            op=nxt>>26;rs=(nxt>>21)&31;rt=(nxt>>16)&31
            if rs==reg and op in {9,13}:
                imm=nxt&65535
                value=(base+(imm-65536 if op==9 and imm>=32768 else imm))&0xffffffff
                if 0x80000000<=value<0xa0000000:result.add(value)
            if op in {0,1,2,3,4,5,6,7} or rt==reg:break
    return result


def variants(source,name,arch,asm,header=''):
    if arch!='mips':return []
    part=view(source,name)
    if not part:return []
    from ..forks.csource import _blank, find
    context=_blank(header+'\n'+source[:part.opening]);proof=addresses(asm);out=[]
    for decl in re.finditer(r'\bextern\s+(?:const\s+)?(?:u8|s8|char|unsigned\s+char|signed\s+char)\s+(D_([89][0-9a-fA-F]{7}))\s*\[\s*\d*\s*\]\s*;',context):
        symbol,encoded=decl.groups();address=int(encoded,16)
        if address not in proof:continue
        # Only explicit address casts, never an integer constant or a dereference/load width.
        pattern=r'\(\s*(?:void|u8|s8|char|unsigned\s+char|signed\s+char)\s*\*\s*\)\s*0[xX]0*'+f'{address:x}'+r'(?!\w)'
        for hit in list(re.finditer(pattern,part.clean[part.opening+1:part.end-1],re.I))[:2]:
            start=part.opening+1+hit.start()
            if part.clean[:start].rstrip().endswith('*'):continue
            if re.search(r'\b(?:int|char|u8|s8|void)\s*\**\s*'+symbol+r'\b',part.clean[find(source,name)[0]:part.end]):continue
            out.append((FAMILIES[0],edit(source,[(start,part.opening+1+hit.end(),symbol)])))
    return out[:4]
