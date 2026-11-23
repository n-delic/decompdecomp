/*
 * The game's memory on a 32-bit computer (port/wasm/miyoo.py: the Miyoo Mini build), in place of wasm2c's own
 * allocator (wasm-rt-mem-impl.c, left out of that build).
 *
 * The WebAssembly build's memory is 4 GB, so every console address is in it (0x80000400 up). A 64-bit PC reserves
 * all of it; a 32-bit one can't. But the game only ever touches a few places in it: the module's own low memory (its
 * first stack and a few globals, at 1 MB), the threads' shadow stacks (host.c, from 0x10000000), the MIPS stacks
 * recompiled C and the interpreter run on (0x80800000 up, above the console's RAM), the console's RAM (0x80000000 up, 8 MB with the
 * Expansion Pak's) and its registers (0xA4000000 up). So the memory's base is address 0 and those are mapped at their own addresses: an address in the game is the same address here, every access
 * is one load as on the PC, and the host's `M + address` needs no translation. Anything else the game touches is a
 * fault, which the kernel reports where it happened, where the 64-bit build would have read zeros.
 *
 * The program itself is linked away from these (miyoo.py: --image-base), as the Miyoo64 ports link theirs above the
 * console's RAM for the same reason. Each mapping asks for its address and checks it got it: Linux 4.9 (the Miyoo
 * Mini's) has no MAP_FIXED_NOREPLACE, and MAP_FIXED would silently replace a library loaded there.
 */
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>

#include "wasm-rt.h"

static const struct { uint32_t start, size; const char *what; } regions[] = {
    {0x00001000u, 0x00400000u - 0x1000u, "the module's low memory"},
    {0x10000000u, 0x02000000u, "the threads' shadow stacks"},
    {0x80000000u, 0x00800000u, "the console's RAM"},
    {0x80800000u, 0x00880000u, "recompiled C's MIPS stacks (port/wasm/recomp.py: STACKS)"},
    {0x81100000u, 0x00840000u, "the interpreter's MIPS stacks (mips.c)"},
    /* the RCP's and the interfaces' registers the host doesn't answer itself (port_io_load returns their address):
       plain memory there, as in the 4 GB build */
    {0xA4000000u, 0x00A00000u, "the console's registers, and the rest of its I/O (host.c: IO_FAR)"},
};

static int map_region(uint32_t start, uint32_t size) {
    void *want = (void *)(uintptr_t)start;
    void *got = mmap(want, size, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE, -1, 0);
    if (got == want) return 1;
    if (got != MAP_FAILED) munmap(got, size);
    return 0;
}

void wasm_rt_allocate_memory(wasm_rt_memory_t *memory, uint64_t initial_pages, uint64_t max_pages, bool is64,
                             uint32_t page_size) {
    (void)is64;
    /* Page 0 too: the game reads through null pointers (a field of a struct not made yet), which reads zeros in the
       4 GB build and on the console. Linux keeps the lowest pages from programs that aren't root
       (vm.mmap_min_addr); the Miyoo's apps run as root. Asking for address 0 needs MAP_FIXED (0 means anywhere),
       safe here: nothing is ever loaded there. Refused, those reads fault. */
    int null_page = mmap(NULL, 0x1000, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED, -1, 0)
                    != MAP_FAILED;
    for (size_t i = 0; i < sizeof regions / sizeof *regions; i++) {
        uint32_t start = regions[i].start, size = regions[i].size;
        while (!map_region(start, size)) {
            if (i == 0 && start < 0x10000u) { start += 0x1000u; size -= 0x1000u; continue; }
            fprintf(stderr, "can't map %s at %08X (%u KB): %s\n", regions[i].what, regions[i].start,
                    regions[i].size >> 10, strerror(errno));
            exit(4);
        }
        if (start != regions[i].start) fprintf(stderr, "mapped %s from %08X\n", regions[i].what, start);
    }
    if (!null_page) fprintf(stderr, "page 0 isn't mapped (not root?): reading through a null pointer will fault\n");
    memory->data = (uint8_t *)(uintptr_t)0; /* every address is its own */
    memory->page_size = page_size;
    memory->pages = initial_pages;
    memory->max_pages = max_pages;
    memory->size = initial_pages * page_size; /* 4 GB: the bounds check passes every 32-bit address */
    memory->data_end = (uint8_t *)(uintptr_t)0xFFFFFFFFu;
    memory->is64 = false;
}

uint64_t wasm_rt_grow_memory(wasm_rt_memory_t *memory, uint64_t delta) {
    (void)memory; (void)delta;
    return (uint64_t)-1; /* the module never grows: its memory is already the most it can have */
}

void wasm_rt_free_memory(wasm_rt_memory_t *memory) { (void)memory; }
