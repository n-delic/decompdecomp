"""A fixed floating-point control/status exchange for registered GNU MIPS compilers.

C cannot read or write FCSR. This operation reads its old 32-bit word and writes the
new word, including its rounding, exception and condition fields. It may trap.
The early-clobber output must not overwrite the input before CTC1 reads it.

Use this in standalone control accessors: a volatile memory clobber is not a
compiler barrier for unrelated register-only floating-point expressions. This
is neither an interrupt-atomic exchange nor a portable fenv implementation.
NEC VR4300 User's Manual U10504EJ7V0UMJ1, instruction descriptions pp. 571–572.
"""


def header():
    """One architectural operation, with no source-selected instruction or register."""
    return ('#ifndef DECOMP_MIPS_FCSR_EXCHANGE\n#define DECOMP_MIPS_FCSR_EXCHANGE\n'
            '#define __builtin_mips_exchange_fcsr(value) '
            '({ unsigned int __decomp_fcsr_old; unsigned int __decomp_fcsr_new = (value); '
            '__asm__ volatile("cfc1 %0,$31\\n\\tctc1 %1,$31" '
            ': "=&r"(__decomp_fcsr_old) : "r"(__decomp_fcsr_new) : "memory"); __decomp_fcsr_old; })\n'
            '#endif\n')


def candidate(asm, name, toolchain_id):
    """A bounded architectural leaf hypothesis; the normal compiler/judge decides exactness."""
    import re

    from .. import toolchains

    if not re.fullmatch(r'[A-Za-z_]\w*',name or ''):
        return None
    try:
        tc=toolchains.get(toolchain_id or '')
    except toolchains.ToolchainError:
        return None
    if tc.intrinsics!='decompnet.arch.mips_debug_break':
        return None
    # Encoded instructions, not printed aliases, identify the fixed FCSR and ABI
    # operands. Require the complete contiguous leaf, including its return slot.
    if any(line.strip() and not re.match(r'^(?:/\*\s*[0-9A-Fa-f]+\s+[0-9A-Fa-f]+\s+[0-9A-Fa-f]{8}\s*\*/[^\n]*|(?:glabel|endlabel)\s+[A-Za-z_]\w*)$',line.strip())
           for line in (asm or '').splitlines()):return None
    lines=re.findall(r'^/\*\s*[0-9A-Fa-f]+\s+([0-9A-Fa-f]+)\s+([0-9A-Fa-f]{8})\s*\*/[^\n]*$',asm or '',re.M)
    if len(lines)!=4:return None
    pcs=[int(pc,16) for pc,_ in lines]
    if pcs!=list(range(pcs[0],pcs[0]+16,4)):return None
    words=[int(word,16) for _,word in lines]
    read,write,ret,slot=words
    if not (read>>26==17 and (read>>21)&31==2 and (read>>16)&31==2
            and (read>>11)&31==31 and read&2047==0
            and write>>26==17 and (write>>21)&31==6 and (write>>16)&31==4
            and (write>>11)&31==31 and write&2047==0
            and ret==0x03e00008 and slot==0):return None
    return ('unsigned int '+name+'(unsigned int value) { '
            'return __builtin_mips_exchange_fcsr(value); }\n')
