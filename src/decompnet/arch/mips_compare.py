"""A fixed CP0 Compare write for registered GNU MIPS compilers.

Writing Compare sets the 32-bit counter comparison and clears the timer interrupt.
CP0 access may trap outside kernel mode when coprocessor access is disabled.
This is one architectural write, not a general
execution hazard barrier, interrupt atomic section, or arbitrary CP0 selector.
The compiler chooses the input GPR, return and delay slot. A memory clobber
orders memory accesses, not unrelated register-only work or CPU execution.
NEC VR4300 User's Manual U10504EJ7V0UMJ1, section 6.3.4 and chapter 19.
"""


def header():
    return ('#ifndef DECOMP_MIPS_WRITE_COMPARE\n#define DECOMP_MIPS_WRITE_COMPARE\n'
            '#define __builtin_mips_write_compare(value) '
            '__asm__ volatile("mtc0 %0,$11" : : "r"((unsigned int)(value)) : "memory")\n'
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
    if tc.intrinsics != 'decompnet.arch.mips_debug_break':
        return None
    # Encoded instructions, not printed aliases, identify the fixed Compare register and ABI
    # operands. Require the complete contiguous leaf, including its return slot.
    if any(line.strip() and not re.match(r'^(?:/\*\s*[0-9A-Fa-f]+\s+[0-9A-Fa-f]+\s+[0-9A-Fa-f]{8}\s*\*/[^\n]*|(?:glabel|endlabel)\s+[A-Za-z_]\w*)$',line.strip())
           for line in (asm or '').splitlines()):
        return None
    lines = re.findall(r'^/\*\s*[0-9A-Fa-f]+\s+([0-9A-Fa-f]+)\s+([0-9A-Fa-f]{8})\s*\*/[^\n]*$',asm or '',re.M)
    if len(lines) != 3:
        return None
    pcs = [int(pc,16) for pc,_ in lines]
    if pcs != list(range(pcs[0],pcs[0]+12,4)):
        return None
    if [int(word,16) for _,word in lines] != [0x40845800,0x03e00008,0]:
        return None
    return ('void '+name+'(unsigned int value) { '
            '__builtin_mips_write_compare(value); }\n')
