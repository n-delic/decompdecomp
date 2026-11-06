"""A fixed standalone VR4300 Status write with return-branch spacing.

NEC VR4300 manual chapter 19, printed page 622 caution 2, warns of a
multi-instruction stall when a branch immediately follows setting Status ITS.
One intervening instruction avoids that immediate-branch arrangement. This
operation supplies that spacing, not Status completion, a CPU fence, interrupt
exclusion, or a guarantee for arbitrary inlined coprocessor users. CP0 may trap;
callers remain responsible for mode changes and every other Status hazard.
The compiler chooses the input GPR, return and delay slot. Historical SDK
intent cannot be inferred from the spacing or the actual caller setting CU1.
"""


def header():
    # Legacy GNU as has no push/pop; validated KMC #APP starts in reorder mode.
    # Otherwise the assembler can remove this performance spacing instruction.
    return ('#ifndef DECOMP_MIPS_WRITE_STATUS_RETURN_SPACING\n#define DECOMP_MIPS_WRITE_STATUS_RETURN_SPACING\n'
            '#define __builtin_mips_write_status_return_spacing(value) '
            '__asm__ volatile(".set noreorder\\n\\tmtc0 %0,$12\\n\\tnop\\n\\t.set reorder" : : "r"((unsigned int)(value)) : "memory")\n'
            '#endif\n')


def candidate(asm, name, toolchain_id):
    """A bounded architectural leaf hypothesis; the normal compiler/judge decides exactness."""
    import re

    from .. import toolchains

    if not re.fullmatch(r'[A-Za-z_]\w*',name or ''):
        return None
    try:
        tc = toolchains.get(toolchain_id or '')
    except toolchains.ToolchainError:
        return None
    if tc.id != 'gcc-2.7.2-kmc' or tc.intrinsics != 'decompnet.arch.mips_debug_break':
        return None
    # Encoded instructions, not printed aliases, identify the fixed Status return-spacing register and ABI
    # operands. Require the complete contiguous leaf, including its return slot.
    if any(line.strip() and not re.match(r'^(?:/\*\s*[0-9A-Fa-f]+\s+[0-9A-Fa-f]+\s+[0-9A-Fa-f]{8}\s*\*/[^\n]*|(?:glabel|endlabel)\s+[A-Za-z_]\w*)$',line.strip())
           for line in (asm or '').splitlines()):
        return None
    lines = re.findall(r'^/\*\s*[0-9A-Fa-f]+\s+([0-9A-Fa-f]+)\s+([0-9A-Fa-f]{8})\s*\*/[^\n]*$',asm or '',re.M)
    if len(lines) != 4:
        return None
    pcs = [int(pc,16) for pc,_ in lines]
    if pcs != list(range(pcs[0],pcs[0]+16,4)):
        return None
    if [int(word,16) for _,word in lines] != [0x40846000,0,0x03e00008,0]:
        return None
    return ('void '+name+'(int value) { '
            '__builtin_mips_write_status_return_spacing(value); }\n')
