/* Demo Quest PSP: one segment based at 0, as a PSP module (PRX) is before its loader places it (build.py links it
   with its relocations kept, and turns those into the PSP's). */
OUTPUT_ARCH(mips)
ENTRY(_start)
SECTIONS
{
    . = 0;
    .text : { crt0.o(.text) *(.text) }
    .rodata : { *(.rodata) *(.rodata.*) }
    .data : { *(.data) *(.data.*) }
    _gp = ALIGN(16) + 0x7FF0;
    .sdata : { *(.sdata) *(.lit4) *(.lit8) }
    /* small and common globals first, so they stay within $gp's reach; big static arrays after */
    .bss (NOLOAD) : { _bss_start = .; *(.sbss) *(.scommon) *(COMMON) *(.bss) . = ALIGN(4); _bss_end = .; }
    /DISCARD/ : { *(.reginfo) *(.mdebug*) *(.comment) *(.pdr) *(.note*) *(.gnu.attributes) *(.MIPS.abiflags) }
}
