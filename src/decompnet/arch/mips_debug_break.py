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
    from .mips_compare import header as compare_header
    from .mips_count import header as count_header
    from .mips_fcsr import header as fcsr_header
    from .mips_live_return_address import header as live_header
    from .mips_status import header as status_header
    from .mips_status_return_spacing import header as status_return_header
    return status_return_header()+_break_header()+live_header()+fcsr_header()+count_header()+status_header()+compare_header()


def header_for(source):
    """Keep existing portable breakpoint headers unchanged when a new intrinsic is unused."""
    import re

    from .mips_compare import header as compare_header
    from .mips_count import header as count_header
    from .mips_fcsr import header as fcsr_header
    from .mips_live_return_address import header as live_header
    from .mips_status import header as status_header
    from .mips_status_return_spacing import header as status_return_header
    used=set(re.findall(r'[A-Za-z_]\w*',source))
    return ((status_return_header() if '__builtin_mips_write_status_return_spacing' in used else '')+(_break_header() if '__builtin_mips_debug_break' in used else '')
            +(live_header() if '__builtin_mips_live_return_address' in used else '')
            +(fcsr_header() if '__builtin_mips_exchange_fcsr' in used else '')
            +(count_header() if '__builtin_mips_read_count' in used else '')
            +(status_header() if '__builtin_mips_read_status' in used else '')
            +(compare_header() if '__builtin_mips_write_compare' in used else ''))
