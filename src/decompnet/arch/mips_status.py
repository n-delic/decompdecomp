"""A fixed CP0 Status read for registered GNU MIPS compilers.

Status is a 32-bit CP0 control word, not an ordinary memory object.
Reading CP0 may trap outside kernel mode or when coprocessor access is disabled.
The volatile operation must remain even if its result is discarded. A memory
clobber does not serialize the processor or order unrelated register-only work.
Validated as a standalone 32-bit accessor, not an arbitrary inlined timing fence.
This accessor supplies no write, arbitrary CP0 selector, or instruction hazard
sequence: the compiler chooses the result register, return, and delay slot.
NEC VR4300 User's Manual U10504EJ7V0UMJ1, section 6.3.5 (Status) and MFC0 p. 469.
"""


def header():
    """A single architectural read; source cannot select an opcode or register."""
    return ('#ifndef DECOMP_MIPS_READ_STATUS\n#define DECOMP_MIPS_READ_STATUS\n'
            '#define __builtin_mips_read_status() '
            '({ unsigned int __decomp_status; '
            '__asm__ volatile("mfc0 %0,$12" : "=r"(__decomp_status) : : "memory"); '
            '__decomp_status; })\n#endif\n')


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
    # Encoded instructions, not printed aliases, identify the fixed Status register and ABI
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
    if [int(word,16) for _,word in lines] != [0x40026000,0x03e00008,0]:
        return None
    return ('unsigned int '+name+'(void) { '
            'return __builtin_mips_read_status(); }\n')
