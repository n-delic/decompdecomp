"""Fixed current link-register snapshots for a registered 32-bit GNU MIPS compiler.

This is not the function-entry caller address: a preceding jal/jalr changes $ra.
The empty volatile output tells GCC to capture the fixed hardware register here;
no submitted opcode, register selector or arbitrary instruction stream is accepted.
"""
import re

from .mips_addresses import resolved


def header():
    return ('#ifndef DECOMP_MIPS_LIVE_RETURN_ADDRESS\n#define DECOMP_MIPS_LIVE_RETURN_ADDRESS\n'
            '#define __builtin_mips_live_return_address() '
            '({ register void *decomp_live_ra __asm__("$31"); '
            '__asm__ volatile("" : "=r"(decomp_live_ra) : : "memory"); decomp_live_ra; })\n'
            '#endif\n')


def stores(asm, *, after_call=False):
    """Proven absolute word stores from $ra, excluding frame saves and unknown/gapped code.

    after_call additionally requires a call in the contiguous encoded prefix without
    a later RA assignment/restore. This is repair evidence, not a control-flow verdict.
    """
    words=re.findall(r'/\*\s*[0-9A-Fa-f]+\s+([0-9A-Fa-f]+)\s+([0-9A-Fa-f]{8})\s*\*/',asm or '')
    chunks=[];chunk=bytearray();previous=None
    for pc,word in words:
        pc=int(pc,16)
        if previous is not None and pc!=previous+4:
            chunks.append(bytes(chunk));chunk.clear()
        chunk.extend(bytes.fromhex(word));previous=pc
    chunks.append(bytes(chunk));result=set()
    for code in chunks:
        addresses=resolved(code)
        called=False
        for offset in range(0,len(code),4):
            word=int.from_bytes(code[offset:offset+4],'big')
            op=word>>26
            # A restored or explicitly assigned RA is not evidence of a current callback link.
            if op==3 or (op==0 and word&63==9 and (word>>11)&31==31):
                called=True
            elif (op==0 and (word>>11)&31==31 and word&63 not in (8,9)) or (op not in (0,2,3,4,5,6,7,40,41,42,43,46,47) and (word>>16)&31==31):
                called=False
            if offset in addresses and (called or not after_call) and word>>26==43 and (word>>16)&31==31 and (word>>21)&31!=29:
                result.add(addresses[offset])
    return result
