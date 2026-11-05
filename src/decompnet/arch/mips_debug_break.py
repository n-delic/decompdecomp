"""A fixed MIPS debugger breakpoint for legacy GNU C toolchains.

ISO C has no debugger-break operation. This compiler compatibility intrinsic provides the
fixed BREAK 9 used by assertion handlers; callers supply neither an opcode nor assembly.
Like the PS1 GTE templates, its implementation belongs to the pinned toolchain, not to
submitted source. A debugger may resume after the breakpoint, so this is not noreturn.
"""


def _break_header():
    return ('#ifndef DECOMP_MIPS_DEBUG_BREAK\n#define DECOMP_MIPS_DEBUG_BREAK\n'
            '#define __builtin_mips_debug_break() __asm__ volatile("break 9" : : : "memory")\n'
            '#endif\n')


def header():
    """Registered fixed GNU MIPS operations; unused macros emit no machine code."""
    from .mips_live_return_address import header as live_header
    return _break_header()+live_header()


def header_for(source):
    """Keep existing portable breakpoint headers unchanged when a new intrinsic is unused."""
    import re

    from .mips_live_return_address import header as live_header
    used=set(re.findall(r'[A-Za-z_]\w*',source))
    return ((_break_header() if '__builtin_mips_debug_break' in used else '')
            +(live_header() if '__builtin_mips_live_return_address' in used else ''))
