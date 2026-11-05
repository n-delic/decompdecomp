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


def stores(asm):
    """Proven absolute word stores from $ra, excluding frame saves and unknown/gapped code."""
    words=re.findall(r'/\*\s*[0-9A-Fa-f]+\s+([0-9A-Fa-f]+)\s+([0-9A-Fa-f]{8})\s*\*/',asm or '')
    chunks=[];chunk=bytearray();previous=None
    for pc,word in words:
        pc=int(pc,16)
        if previous is not None and pc!=previous+4:
            chunks.append(bytes(chunk));chunk.clear()
        chunk.extend(bytes.fromhex(word));previous=pc
    chunks.append(bytes(chunk));result=set()
    for code in chunks:
        for offset,address in resolved(code).items():
            word=int.from_bytes(code[offset:offset+4],'big')
            if word>>26==43 and (word>>16)&31==31 and (word>>21)&31!=29:
                result.add(address)
    return result
