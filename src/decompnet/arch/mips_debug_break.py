"""A fixed MIPS debugger breakpoint for legacy GNU C toolchains.

ISO C has no debugger-break operation. This compiler compatibility intrinsic provides the
fixed BREAK 9 used by assertion handlers; callers supply neither an opcode nor assembly.
Like the PS1 GTE templates, its implementation belongs to the pinned toolchain, not to
submitted source. A debugger may resume after the breakpoint, so this is not noreturn.
"""


def header():
    return ('#ifndef DECOMP_MIPS_DEBUG_BREAK\n#define DECOMP_MIPS_DEBUG_BREAK\n'
            '#define __builtin_mips_debug_break() __asm__ volatile("break 9" : : : "memory")\n'
            '#endif\n')
