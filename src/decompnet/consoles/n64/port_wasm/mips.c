/*
 * The functions the game has no C for yet, run from the player's own ROM (docs/PORTING.md, "Functions with no C
 * yet"): an interpreter of the console's CPU (the R4300's MIPS III, its FPU as libultra's games use it: 32-bit
 * registers in even and odd pairs for doubles) running each such function's own instructions, from the console's
 * memory, where the boot and the game's own loads put them from the ROM, as on the console. Nothing of the game's
 * code is in a build: what's interpreted comes from the ROM the player chose, at play time. (The native build may
 * also carry C recompiled from the user's ROM, port/wasm/recomp.py; this needs none.)
 *
 * Where it starts:
 * - the game's C calling a function it has no C for: the build's interp.c (port/wasm/interp.py) defines that
 *   import, which puts its arguments where o32 passes them and runs the function here (port_mips_run);
 * - a call through a function pointer, or a thread's entry, to one (host.c's port_func; in the browser, web/be.c's
 *   port_icall through port_mips_icall): its arguments with their types (port_mips_typed_call), floats included.
 *
 * Where it leaves: a jump to a function that has C (the game's or the host's: interp.c's table, by console address)
 * calls that C with o32's arguments and goes on at $ra with what it returned; the return to the caller's
 * sentinel address ends the run. Interpreted code calling interpreted code is only a jump.
 *
 * The registers' and stack's handover is the recompiled C's (recomp.py): each host thread interprets on a MIPS stack
 * of its own in the console's address space (MIPS_STACKS), below whatever interpreted frames it already has; the
 * game's C reads $a0-$a3 it doesn't declare, and leaves a void function's $v0, in the register words at REGS
 * (adapt.py).
 *
 * PORT_INTERP=0 turns it off (a call to a function with no C is then skipped, as before); PORT_INTERP_TRACE=1 says
 * each function it starts, 2 also each call it makes into C and where a long run is.
 */
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

uint8_t *port_memory(void);
uint64_t port_io_load(uint64_t addr);
uint64_t port_io_store(uint64_t addr, uint64_t value);
uint32_t port_stack_bus(uint32_t addr);
void port_safe_point(void);
#ifndef PORT_WEB
void port_boot_spin(void); /* host.c: a boot function that never returns, once its threads exist, is the idle thread */
#endif
/* host.c's: park the running thread for good (its code jumps to itself); a build without threads spins on */
__attribute__((weak)) void port_idle_forever(void) {}
int port_thread_number(void);
struct w2c_env;
uint32_t w2c_env_0x5F_builtin_mips_read_count(struct w2c_env *e);
#ifdef PORT_WEB
void port_web_yield(void);
#endif

/* the register words: $a0-$a3, then $v0, where the game's C passes and reads them (port/wasm/adapt.py's REGS, which
   interp.c says, port_mips_regs; this default is the same) */
__attribute__((weak)) const uint32_t port_mips_regs = 0x00090000u;
#define REGS port_mips_regs
#define MIPS_STACKS 0x81100000u /* each host thread's: 256 KB, above recompiled C's (recomp.py's STACKS), in KSEG0 like them */
#define MIPS_STACK 0x40000u
#define MIPS_THREADS 32
#define RETURN 0xFFFFFFF0u /* the sentinel return address: back to the C that started the run */

/* interp.c's: every function with C, by console address, and how to call it with o32's registers */
typedef void (*PortMipsCall)(uint64_t *r, uint32_t *f);
typedef struct { uint32_t vram; PortMipsCall call; } PortMipsEntry;
__attribute__((weak)) const PortMipsEntry port_mips_calls[1] = {{0, 0}};
__attribute__((weak)) const unsigned port_mips_call_count = 0;

/* Full-function, relocation-masked SDK fingerprints for modules the loader can move. */
typedef struct { const uint32_t *words, *masks; uint32_t count; PortMipsCall call; } PortMipsPattern;
__attribute__((weak)) const PortMipsPattern port_mips_patterns[1] = {{0, 0, 0, 0}};
__attribute__((weak)) const unsigned port_mips_pattern_count = 0;

/* The host's own libultra routines by name (interp.c's, from the names the host defines): the run-time structural
   test below names a routine in code the build never saw, and a call to it is this host function. */
typedef struct { const char *name; PortMipsCall call; } PortMipsShape;
__attribute__((weak)) const PortMipsShape port_mips_shapes[1] = {{0, 0}};
__attribute__((weak)) const unsigned port_mips_shape_count = 0;

/* PORT_ENTRIES=path: every address the game starts running here (a function's entry: a call, a pointer, a thread),
   each once, appended as it's first seen, so a game that's stopped keeps what it found. The importer reads the
   project's file as evidence of where functions start (consoles/n64/code.py, runtime_entries): a racing game's
   split left functions it only reaches through pointers as data. */
void port_note_entry(uint32_t address) {
#ifndef PORT_WEB
    static const char *path;
    static int ready;
    static uint32_t seen[16384];
    static unsigned count;
    if (!ready) { ready = 1; path = getenv("PORT_ENTRIES"); }
    if (!path || !address || count >= 12000) return;
    unsigned h = (address >> 2) * 2654435761u % 16384u;
    while (seen[h]) { if (seen[h] == address) return; h = (h + 1) % 16384u; }
    seen[h] = address;
    count++;
    FILE *f = fopen(path, "a");
    if (f) { fprintf(f, "%08X\n", address); fclose(f); }
#else
    (void)address;
#endif
}

/* ---------------------------------------------------------------- the console's memory, big-endian */

/* (always inlined: the native host is built with -fno-inline, and each instruction goes through these) */
#define HOT static inline __attribute__((always_inline))
static uint8_t *M;
/* The windows a game maps through the TLB (host.c): an address inside one is read and written at its KSEG0 spelling,
   as the split names it. */
extern unsigned port_tlb_n;
uint32_t port_tlb_canon(uint32_t a);
uint32_t port_tlb_alias(uint32_t a);
uint32_t port_tlb_jump(uint32_t slot, uint32_t imm); /* the target of a j/jal: top bits from the window's virtual base */
uint32_t port_tlb_virtual(uint32_t a); /* a KSEG0 spelling beyond the RAM of a high window's virtual address: that address */
uint32_t port_tlb_call(uint32_t a); /* a call spelled against KSEG0 that a window kept at its physical address maps */
uint32_t port_tlb_spelled(uint32_t a); /* the project's name (0x80000000 | virtual) for code such a window holds */
void port_tlb_write(uint32_t pm, uint32_t entry_hi, uint32_t lo0, uint32_t lo1);
void port_tlb_set(unsigned index, uint32_t pm, uint32_t entry_hi, uint32_t lo0, uint32_t lo1);
uint32_t port_tlb_probe(uint32_t entry_hi);
int port_tlb_read(unsigned index, uint32_t *pm, uint32_t *entry_hi, uint32_t *lo0, uint32_t *lo1);
uint32_t port_tlb_wired(int set, uint32_t value); /* the Wired register: the slots below it are the game's alone */
int port_tlb_invalid(uint32_t a); /* a page an entry covers with its valid bit clear: TLB Invalid, not a refill */
int port_tlb_fault(uint32_t epc, uint32_t badvaddr, uint32_t cause); /* host.c: a fault the game's pager answers; 1 once it restarted the thread */
/* (KSEG0 and KSEG1 are 0x80000000 to 0xBFFFFFFF: the one compare says "outside them", the mapped user, supervisor and
   KSEG2/KSEG3 space) */
HOT uint32_t mapped(uint32_t a) { return __builtin_expect(a - 0x80000000u >= 0x40000000u && port_tlb_n, 0) ? port_tlb_canon(a) : a; }
HOT uint8_t *at_load(uint32_t a) { a = mapped(a); return M + (a >= 0xA0000000u ? (uint32_t)port_io_load(a) : a); }
HOT uint8_t *at_store(uint32_t a, uint64_t v) { a = mapped(a); return M + (a >= 0xA0000000u ? (uint32_t)port_io_store(a, v) : a); }
HOT uint32_t lw(uint32_t a) { uint32_t v; memcpy(&v, at_load(a), 4); return __builtin_bswap32(v); }
/* An instruction: where a window's code was never written at its spelling (zero words), the bytes at its physical
   address (the boot chip's image, a decompressed program). A block of four zero words, so a nop alone is no hole. */
HOT uint32_t fetch(uint32_t a) {
    uint32_t w = lw(a);
    if (__builtin_expect(w == 0 && port_tlb_n && mapped(a) >= 0x80000000u, 0)) {
        /* (the block of zero words first: a nop is a zero word, and the alias is a search through the game's windows) */
        uint32_t blk = mapped(a) & ~15u;
        if (!lw(blk) && !lw(blk + 4) && !lw(blk + 8) && !lw(blk + 12)) {
            uint32_t b = port_tlb_alias(mapped(a));
            if (b) return lw(b);
        }
    }
    return w;
}
HOT uint64_t ld(uint32_t a) { return (uint64_t)lw(a) << 32 | lw(a + 4); }
HOT uint16_t lh(uint32_t a) { uint16_t v; memcpy(&v, at_load(a), 2); return __builtin_bswap16(v); }
HOT uint8_t lb(uint32_t a) { return *at_load(a); }
HOT void sw(uint32_t a, uint32_t v) { uint32_t b = __builtin_bswap32(v); memcpy(at_store(a, v), &b, 4); }
HOT void sd(uint32_t a, uint64_t v) { sw(a, (uint32_t)(v >> 32)); sw(a + 4, (uint32_t)v); }
HOT void sh(uint32_t a, uint16_t v) { uint16_t b = __builtin_bswap16(v); memcpy(at_store(a, v), &b, 2); }
HOT void sb(uint32_t a, uint8_t v) { *at_store(a, v) = v; }
/* for interp.c's bridges: a stack word */
uint32_t port_mips_lw(uint32_t a) { if (!M) M = port_memory(); return lw(a); }
void port_mips_sw(uint32_t a, uint32_t v) { if (!M) M = port_memory(); sw(a, v); }

/* ---------------------------------------------------------------- the functions that have C */

#define CACHE 1024
static struct { uint32_t vram; PortMipsCall call; } found[CACHE]; /* the last looked up, hit or miss */
static PortMipsCall with_pattern(uint32_t vram) {
    if ((vram & 3u) || vram < 0x80000000u || vram >= 0x80800000u) return 0;
    PortMipsCall match = 0;
    for (unsigned i = 0; i < port_mips_pattern_count; i++) {
        const PortMipsPattern *p = &port_mips_patterns[i];
        if (p->count > (0x80800000u - vram) / 4u) continue;
        unsigned n = 0;
        for (; n < p->count; n++) if ((fetch(vram + n * 4u) & p->masks[n]) != p->words[n]) break;
        if (n != p->count) continue;
        if (match && match != p->call) return 0; /* overlapping SDK identities are not a verdict */
        match = p->call;
    }
    return match;
}

/* ---------------------------------------------------------------- libultra routines told by structure, at run time

   A game that decompresses or loads its real code at run time carries its own libultra there, in code the build never
   saw, so no fingerprint could name it: its own osCreateThread, osRecvMesg and the rest ran against kernel structures
   the host doesn't keep, and the game's threads never ran (a loader whose libultra sat in the decompressed program).
   This is consoles/n64/sdk_shapes.py's test over the words in RAM when the interpreter is about to run a function it
   has no C for: the routine is told by the OSThread and OSMesgQueue fields it reads and writes and by its calls, and
   when it is one the host defines, the call is that host function. The size a test wants is where the function
   ends: its `jr $ra` that no branch of it jumps past. A verdict is kept by address with a hash of the function's
   first words, so another overlay loaded at the same address is looked at again. */
#define OPC(w) ((w) >> 26)
#define IMM(w) ((w) & 0xFFFFu)
#define RS(w) (((w) >> 21) & 31u)
#define RT(w) (((w) >> 16) & 31u)
#define SHAPE_MAX_WORDS 175 /* 700 bytes */

typedef struct {
    unsigned calls, divides, state_stores, other_halfword_stores, tlnext, valid_stores, first_stores, count_loads,
             first_loads, msg_loads;
    int reads_state, stores_priority, reads_priority, reads_queue, runnable, waiting, stopped, no_block, valid_down,
        valid_up, wide_load;
    unsigned stored; /* a bit for each queue field stored: 0, 4, 8, 0xC, 0x10, 0x14 (at offset / 4) */
} ShapeFeatures;

static void shape_features(const uint32_t *w, unsigned n, ShapeFeatures *f) {
    memset(f, 0, sizeof *f);
    for (unsigned i = 0; i < n; i++) {
        uint32_t op = OPC(w[i]), imm = IMM(w[i]);
        if (op == 3) f->calls++;
        if (op == 0 && ((w[i] & 63) == 0x1A || (w[i] & 63) == 0x1B)) f->divides++;
        if (op == 9 && RS(w[i]) == 0) {
            if (imm == 2) f->runnable = 1;
            if (imm == 8) f->waiting = 1;
            if (imm == 1) f->stopped = 1;
            if (imm == 0xFFFF) f->no_block = 1;
        }
        if (op == 9 && RS(w[i]) != 0) { if (imm == 0xFFFF) f->valid_down = 1; if (imm == 1) f->valid_up = 1; }
        if ((op == 0x21 || op == 0x25) && imm == 0x10) f->reads_state = 1;
        if (op == 0x21 || op == 0x23 || op == 0x25) f->wide_load = 1;
        if (op == 0x29) { if (imm == 0x10) f->state_stores++; else f->other_halfword_stores++; }
        if (op == 0x2B) {
            if (imm == 4) f->stores_priority = 1;
            if (imm == 8) f->valid_stores++;
            if (imm == 0xC) { f->first_stores++; f->tlnext++; }
            if (imm == 0 || imm == 4 || imm == 8 || imm == 0xC || imm == 0x10 || imm == 0x14) f->stored |= 1u << (imm >> 2);
        }
        if (op == 0x23) {
            if (imm == 4) f->reads_priority = 1;
            if (imm == 8) f->reads_queue = 1;
            if (imm == 0xC) { f->tlnext++; f->first_loads++; }
            if (imm == 0x10) f->count_loads++;
            if (imm == 0x14) f->msg_loads++;
        }
    }
}

static int shape_event_message(const uint32_t *w, unsigned n, unsigned size) {
    static const unsigned sizes[] = {88, 92, 96, 100, 104, 108, 168, 172, 176, 180, 184};
    int ok = 0, index = 0, table = 0, narrow = 0, back = 0, queue = 0, message = 0, structure = 0;
    unsigned calls = 0, loads = 0;
    for (unsigned i = 0; i < sizeof sizes / sizeof *sizes; i++) if (sizes[i] == size) ok = 1;
    if (!ok) return 0;
    for (unsigned i = 0; i < n; i++) {
        uint32_t op = OPC(w[i]), imm = IMM(w[i]);
        if (op == 3) calls++;
        if (op == 0 && (w[i] & 63) == 0 && ((w[i] >> 6) & 31) == 3 && ((w[i] >> 11) & 31) != 0) index = 1;
        if (op == 0 && (w[i] & 63) == 0x21) table = 1;
        if (op == 0x2B && RS(w[i]) != 29) {
            if (imm == 0) queue = 1;
            if (imm == 4) message = 1;
            if (imm >= 8 && imm <= 0xFF) structure = 1;
        }
        if (op == 0x23 && RS(w[i]) != 29) loads++;
        if (op == 0x28 || op == 0x29) narrow = 1;
        if ((op == 4 || op == 5 || op == 6 || op == 7 || op == 0x14 || op == 0x15) && (w[i] & 0x8000)) back = 1;
    }
    return calls >= 2 && calls <= 3 && index && table && queue && message && !structure && loads <= 4 && !narrow && !back;
}

/* Whether one of the function's calls goes to a routine that reads the CPU's Status register (`mfc0 $r, $12`): the
   interrupt disable the libultra routines that touch the kernel's lists begin with. A program's first function can
   set a thread up inline and have the same stores and as few calls; its callees are not that leaf. */
static int shape_calls_interrupt_leaf(const uint32_t *w, unsigned n, uint32_t vram, int every) {
    int found = 0;
    for (unsigned i = 0; i < n; i++) {
        if (OPC(w[i]) != 3) continue;
        uint32_t target = ((vram + 4u * i) & 0xF0000000u) | ((w[i] & 0x3FFFFFFu) << 2);
        int leaf = 0, status = 0;
        for (unsigned k = 0; k < 16 && !leaf; k++) {
            uint32_t x = fetch(target + 4u * k);
            if (((x & 0xFFE00000u) == 0x40000000u || (x & 0xFFE00000u) == 0x40800000u) && ((x >> 11) & 31) == 12)
                status = 1; /* mfc0 or mtc0 of Status */
            if (x == 0x03E00008u) leaf = 1; /* the leaf ends within sixteen words */
        }
        if (leaf && status) found = 1;
        else if (every) return 0;
    }
    return found;
}

/* The routines that touch the console's hardware, or the kernel's state for it (sdk_shapes.py's `_hardware_features`
   and the tests after it; change them together): told by the constants and field offsets the hardware fixes. `through`
   asks for an access through a register that is not $sp. */
static int hw_any(const uint32_t *w, unsigned n, unsigned op, unsigned imm, int through) {
    for (unsigned i = 0; i < n; i++)
        if (OPC(w[i]) == op && IMM(w[i]) == imm && (!through || RS(w[i]) != 29)) return 1;
    return 0;
}
static int hw_op(const uint32_t *w, unsigned n, unsigned op) {
    for (unsigned i = 0; i < n; i++) if (OPC(w[i]) == op) return 1;
    return 0;
}
static int hw_op_through(const uint32_t *w, unsigned n, unsigned op) {
    for (unsigned i = 0; i < n; i++) if (OPC(w[i]) == op && RS(w[i]) != 29) return 1;
    return 0;
}
static int hw_const(const uint32_t *w, unsigned n, unsigned imm) { /* addiu r, $zero, imm */
    for (unsigned i = 0; i < n; i++) if (OPC(w[i]) == 9 && RS(w[i]) == 0 && IMM(w[i]) == imm) return 1;
    return 0;
}
static int hw_sw_zero(const uint32_t *w, unsigned n, unsigned imm) { /* sw $zero, imm(r) */
    for (unsigned i = 0; i < n; i++)
        if (OPC(w[i]) == 0x2B && RT(w[i]) == 0 && RS(w[i]) != 29 && IMM(w[i]) == imm) return 1;
    return 0;
}
static int hw_back(const uint32_t *w, unsigned n) { /* a branch backwards: a loop */
    for (unsigned i = 0; i < n; i++) {
        unsigned op = OPC(w[i]);
        if ((op == 4 || op == 5 || op == 6 || op == 7 || op == 0x14 || op == 0x15) && (w[i] & 0x8000)) return 1;
    }
    return 0;
}
static unsigned hw_lui(const uint32_t *w, unsigned n, unsigned imm) {
    unsigned count = 0;
    for (unsigned i = 0; i < n; i++) if (OPC(w[i]) == 0x0F && IMM(w[i]) == imm) count++;
    return count;
}
static uint32_t hw_cache_ops(const uint32_t *w, unsigned n) { /* a bit for each cache operation code used */
    uint32_t ops = 0;
    for (unsigned i = 0; i < n; i++) if (OPC(w[i]) == 0x2F) ops |= 1u << RT(w[i]);
    return ops;
}
/* whether the function's stores through a register that is not $sp stay within the OSTimer's eight words, and no
   halfword or byte is stored through one */
static int hw_timer_stores_only(const uint32_t *w, unsigned n) {
    for (unsigned i = 0; i < n; i++) {
        unsigned op = OPC(w[i]);
        if (RS(w[i]) == 29) continue;
        if (op == 0x28 || op == 0x29) return 0;
        if (op == 0x2B && (IMM(w[i]) > 0x1C || (IMM(w[i]) & 3))) return 0;
    }
    return 1;
}
static int hw_stores_anything(const uint32_t *w, unsigned n) { /* sw, sh or sb through a register that is not $sp */
    for (unsigned i = 0; i < n; i++) {
        unsigned op = OPC(w[i]);
        if ((op == 0x2B || op == 0x29 || op == 0x28) && RS(w[i]) != 29) return 1;
    }
    return 0;
}

static const char *shape_hardware(const uint32_t *w, unsigned n, unsigned size, unsigned calls) {
    int back = hw_back(w, n);
    if (calls == 2) { /* the video manager's state, between an interrupt disable and restore */
        int ori = hw_op(w, n, 0x0D);
        if (size >= 56 && size <= 100 && hw_any(w, n, 0x2B, 4, 1) && hw_any(w, n, 0x29, 0, 1) && hw_any(w, n, 0x25, 0, 1)
            && hw_any(w, n, 0x0D, 0x10, 0))
            return "osViSwapBuffer";
        if (size >= 80 && size <= 130 && hw_any(w, n, 0x29, 0, 1) && hw_any(w, n, 0x25, 0, 1) && hw_any(w, n, 0x0D, 0x20, 0)
            && (hw_any(w, n, 0x0C, 0xFFDF, 0) || hw_const(w, n, 0xFFDF)))
            return "osViBlack";
        if (size >= 72 && size <= 120 && hw_any(w, n, 0x2B, 0x10, 1) && hw_any(w, n, 0x2B, 0x14, 1)
            && hw_any(w, n, 0x29, 2, 1) && !ori)
            return "osViSetEvent";
        if (size >= 64 && size <= 112 && hw_any(w, n, 0x2B, 8, 1) && hw_any(w, n, 0x2B, 0xC, 1) && hw_any(w, n, 0x23, 4, 1)
            && hw_any(w, n, 0x29, 0, 1) && hw_const(w, n, 1) && !ori && !hw_any(w, n, 0x2B, 0x10, 1))
            return "osViSetMode";
    }
    if (size >= 120 && size <= 320 && calls >= 2 && hw_const(w, n, 0xB) && hw_const(w, n, 0xC) && hw_any(w, n, 0x29, 0, 1)
        && hw_any(w, n, 0x28, 2, 1) && hw_sw_zero(w, n, 0x14) && hw_any(w, n, 0x2B, 4, 1) && hw_any(w, n, 0x2B, 8, 1)
        && hw_any(w, n, 0x2B, 0xC, 1) && hw_any(w, n, 0x2B, 0x10, 1))
        return "osPiStartDma";
    if (size >= 100 && size <= 260 && calls >= 2 && hw_const(w, n, 0xF) && hw_const(w, n, 0x10) && hw_any(w, n, 0x29, 0, 1)
        && hw_any(w, n, 0x24, 2, 1) && hw_any(w, n, 0x2B, 0x14, 1) && !hw_sw_zero(w, n, 0x14))
        return "osEPiStartDma";
    if (size >= 36 && size <= 80 && calls >= 2 && calls <= 3 && back) {
        for (unsigned i = 0; i < n; i++) /* addiu $a0, $zero, 0x125 */
            if (OPC(w[i]) == 9 && RS(w[i]) == 0 && RT(w[i]) == 4 && IMM(w[i]) == 0x125) return "osSpTaskStartGo";
    }
    if (size >= 110 && size <= 200 && hw_lui(w, n, 0xA450) && hw_any(w, n, 0x0C, 0x1FFF, 0) && hw_op_through(w, n, 0x28)
        && hw_any(w, n, 0x0D, 4, 0))
        return "osAiSetNextBuffer";
    if (size == 16 && OPC(w[0]) == 0x0F && IMM(w[0]) == 0xA450 && OPC(w[1]) == 0x0D && IMM(w[1]) == 4 && w[2] == 0x03E00008u
        && OPC(w[3]) == 0x23 && IMM(w[3]) == 0)
        return "osAiGetLength";
    if (size >= 200 && size <= 420 && hw_lui(w, n, 0xA450) >= 2 && hw_any(w, n, 0x0D, 0x10, 0) && hw_any(w, n, 0x0D, 0x14, 0))
        return "osAiSetFrequency";
    if (size == 12 && (w[0] & 0xFFE0FFFFu) == 0x40004800u && w[1] == 0x03E00008u) return "osGetCount";
    if (size == 132 && calls == 3 && !back && !hw_stores_anything(w, n)) { /* (132 bytes in every build seen) */
        for (unsigned i = 0; i < n; i++) /* sltu: the carry between the halves */
            if (OPC(w[i]) == 0 && (w[i] & 63) == 0x2B) return "osGetTime";
    }
    if (calls == 0 && size >= 32 && size <= 200) { /* a loop of cache operations: told by which */
        uint32_t ops = hw_cache_ops(w, n);
        if ((ops & (1u << 0x10)) && !(ops & ~((1u << 0x10) | 1u))) return "osInvalICache";
        if ((ops & (1u << 0x11)) && !(ops & ~((1u << 0x11) | (1u << 0x15) | (1u << 1)))) return "osInvalDCache";
        if ((ops & (1u << 0x19)) && !(ops & ~((1u << 0x19) | (1u << 1)))) return "osWritebackDCache";
        if (ops == (1u << 1) && size <= 48) return "osWritebackDCacheAll";
    }
    if (size >= 130 && size <= 330 && calls >= 2 && hw_sw_zero(w, n, 0) && hw_sw_zero(w, n, 4) && hw_timer_stores_only(w, n)) {
        static const unsigned fields[] = {8, 0xC, 0x10, 0x14, 0x18, 0x1C};
        int all = 1;
        for (unsigned k = 0; k < 6; k++) if (!hw_any(w, n, 0x2B, fields[k], 1)) all = 0;
        if (all) return "osSetTimer";
    }
    return 0;
}

/* sdk_shapes.py's thread_getter: osGetThreadId (the offset read is 0x14) and osGetThreadPri (0x04): a branch on $a0, the
   running thread loaded into $a0 from a global, one load into $v0 from $a0 in the return's delay slot, no store, no
   call. Returns the offset, or 0. */
static unsigned shape_thread_getter(const uint32_t *w, unsigned n) {
    int skip = 0, lui = 0, load = 0, ret = -1;
    if (n < 5 || n > 8) return 0;
    for (unsigned i = 0; i < n; i++) {
        uint32_t op = OPC(w[i]);
        if (op == 3 || (op >= 0x28 && op <= 0x2B)) return 0;
        if ((op == 4 || op == 5) && (RS(w[i]) == 4 || RT(w[i]) == 4)) skip = 1;
        if (op == 0x0F) lui = 1;
        if (op == 0x23 && RT(w[i]) == 4) load = 1;
        if (w[i] == 0x03E00008u) { if (ret >= 0) return 0; ret = (int)i; }
    }
    uint32_t last = w[n - 1];
    if (OPC(last) != 0x23 || RS(last) != 4 || RT(last) != 2 || ret != (int)n - 2) return 0;
    return skip && lui && load && (IMM(last) == 0x14 || IMM(last) == 4) ? IMM(last) : 0;
}

/* osCreateMesgQueue's registers (sdk_shapes.py queue_creation): every store goes through $a0, the valid count and first
   are $zero, the count argument ($a2) is stored at +0x10 and the ring's address ($a1) at +0x14, the two lists are a
   temporary's address (no argument register, not $zero). The offsets alone matched a game's heap initialiser. */
static int shape_queue_creation_registers(const uint32_t *w, unsigned n) {
    int reg[6] = {-1, -1, -1, -1, -1, -1};
    for (unsigned i = 0; i < n; i++) {
        if (OPC(w[i]) != 0x2B) continue;
        if (RS(w[i]) != 4) return 0;
        uint32_t off = IMM(w[i]);
        if (off > 0x14 || (off & 3)) return 0;
        reg[off / 4] = (int)RT(w[i]);
    }
    for (int k = 0; k < 6; k++) if (reg[k] < 0) return 0;
    if (reg[2] != 0 || reg[3] != 0 || reg[4] != 6 || reg[5] != 5) return 0;
    for (int k = 0; k < 2; k++) if (reg[k] == 0 || (reg[k] >= 4 && reg[k] <= 7)) return 0;
    return 1;
}

/* sdk_shapes.py's identify(), the same tests over the function's words: the library's name, or 0 */
static const char *shape_identify_words(const uint32_t *w, unsigned n) {
    unsigned size = n * 4;
    if (size < 12 || size > 700) return 0;
    unsigned got = shape_thread_getter(w, n);
    if (got) return got == 0x14 ? "osGetThreadId" : "osGetThreadPri";
    if (size < 32) { /* (the 12-byte osGetCount and the 16-byte osAiGetLength: the hardware tests below) */
        ShapeFeatures none;
        shape_features(w, n, &none);
        return shape_hardware(w, n, size, none.calls);
    }
    ShapeFeatures f;
    shape_features(w, n, &f);
    if (size >= 100) { /* osCreateThread: the id into +0x14, flags cleared at +0x12, a state halfword at +0x10 */
        int ids = 0, flags = 0, state = 0, pc = 0;
        for (unsigned i = 0; i < n; i++) {
            if (OPC(w[i]) == 0x2B && IMM(w[i]) == 0x14 && RS(w[i]) != 29) ids = 1; /* (from $a1, or a spilled copy of it) */
            if (OPC(w[i]) == 0x2B && IMM(w[i]) == 0x11C) pc = 1; /* the entry point into the saved context */
            if (OPC(w[i]) == 0x29 && RT(w[i]) == 0 && IMM(w[i]) == 0x12) flags = 1;
            if (OPC(w[i]) == 0x29 && IMM(w[i]) == 0x10 && RT(w[i]) != 0) state = 1;
        }
        if (ids && pc && flags && state && f.calls >= 2 && f.calls <= 3) return "osCreateThread";
    }
    if (n >= 8 && n <= 32 && f.calls == 3) { /* osYieldThread: three calls, no branch, one halfword store */
        int branch = 0, other = 0, runnable_reg[32] = {0};
        unsigned states = 0, state_reg = 0;
        for (unsigned i = 0; i < n; i++) {
            uint32_t op = OPC(w[i]);
            if (op == 1 || (op >= 4 && op <= 7) || (op >= 0x14 && op <= 0x17)) branch = 1;
            if (op == 9 && RS(w[i]) == 0 && IMM(w[i]) == 2) runnable_reg[RT(w[i])] = 1;
            if (op == 0x28 || op == 0x29 || op == 0x2A || op == 0x2B || op == 0x2E || op == 0x31 || op == 0x39 || op == 0x3D) {
                if (op == 0x29 && IMM(w[i]) == 0x10) { states++; state_reg = RT(w[i]); }
                else if (!(op == 0x2B && RS(w[i]) == 29)) other = 1;
            }
        }
        if (!branch && states == 1 && !other && runnable_reg[state_reg]) return "osYieldThread";
    }
    if (size >= 200 && size <= 450 && f.reads_state && f.state_stores >= 3 && !f.other_halfword_stores
        && !f.stores_priority && f.reads_priority && f.reads_queue && f.runnable && f.waiting && f.calls >= 6)
        return "osStartThread";
    if (size >= 150 && size <= 320 && f.reads_state && f.state_stores == 1 && !f.other_halfword_stores
        && f.stores_priority && f.reads_priority && f.reads_queue && f.runnable && f.calls >= 4)
        return "osSetThreadPri";
    if (size >= 150 && size <= 320 && f.reads_state && f.state_stores == 0 && !f.other_halfword_stores && f.tlnext >= 4
        && f.stopped && !f.runnable && !f.waiting && f.calls >= 3 && f.calls <= 5)
        return "osDestroyThread";
    if (size >= 250 && size <= 360 && f.calls == 6 && f.divides == 1 && f.state_stores == 1 && f.waiting && f.no_block
        && f.valid_stores == 1 && f.msg_loads >= 1) { /* a blocking ring buffer */
        if (f.first_stores == 1 && f.valid_down && f.count_loads <= 2 && f.first_loads == 2) return "osRecvMesg";
        if (f.first_stores == 0 && !f.valid_down && f.valid_up) return "osSendMesg";
        if (f.first_stores == 1 && f.valid_down && f.count_loads >= 3 && f.valid_up) return "osJamMesg";
    }
    if (size >= 32 && size <= 48 && f.calls == 0 && f.stored == 0x3Fu && !f.wide_load
        && shape_queue_creation_registers(w, n)) return "osCreateMesgQueue";
    if (shape_event_message(w, n, size)) return "osSetEventMesg";
    return shape_hardware(w, n, size, f.calls);
}

/* Whether one of the function's calls goes to a leaf that reads a coprocessor 0 register (`mfc0 $r, $reg`) and returns:
   the Count register (9) osGetTime reads between its interrupt disable and restore. */
static int shape_calls_cp0_leaf(const uint32_t *w, unsigned n, uint32_t vram, unsigned reg) {
    for (unsigned i = 0; i < n; i++) {
        if (OPC(w[i]) != 3) continue;
        uint32_t target = ((vram + 4u * i) & 0xF0000000u) | ((w[i] & 0x3FFFFFFu) << 2);
        int leaf = 0, read = 0;
        for (unsigned k = 0; k < 16 && !leaf; k++) {
            uint32_t x = fetch(target + 4u * k);
            if ((x & 0xFFE00000u) == 0x40000000u && ((x >> 11) & 31) == reg) read = 1; /* mfc0 */
            if (x == 0x03E00008u) leaf = 1;
        }
        if (leaf && read) return 1;
    }
    return 0;
}

static const char *shape_identify(const uint32_t *w, unsigned n, uint32_t vram) {
    const char *name = shape_identify_words(w, n);
    if (!name) return 0;
    /* the routines of the hardware: their constants are what tells them (osGetTime and the video manager's four are
       told by what they do to the kernel's state, so they must also call the interrupt disable and restore) */
    static const char *const bare[] = {"osPiStartDma", "osEPiStartDma", "osSpTaskStartGo", "osAiSetNextBuffer",
                                       "osAiGetLength", "osAiSetFrequency", "osGetCount", "osInvalICache",
                                       "osInvalDCache", "osWritebackDCache", "osWritebackDCacheAll", "osSetTimer"};
    for (unsigned i = 0; i < sizeof bare / sizeof *bare; i++) if (!strcmp(name, bare[i])) return name;
    if (!strcmp(name, "osGetTime"))
        return shape_calls_cp0_leaf(w, n, vram, 9) && shape_calls_interrupt_leaf(w, n, vram, 0) ? name : 0;
    if (!strncmp(name, "osVi", 4)) return shape_calls_interrupt_leaf(w, n, vram, 1) ? name : 0;
    /* every one of them but osCreateMesgQueue (six stores, no call) disables interrupts first */
    if (!strcmp(name, "osCreateMesgQueue") || !strcmp(name, "osGetThreadId") || !strcmp(name, "osGetThreadPri"))
        return name; /* (the getters and the queue's creation call nothing) */
    /* osCreateThread calls only the interrupt disable and restore; the others call more (the thread lists' routines) */
    return shape_calls_interrupt_leaf(w, n, vram, !strcmp(name, "osCreateThread")) ? name : 0;
}

/* Where the function at `vram` ends, in words: its `jr $ra` and the delay slot after it that no branch of the
   function jumps past; 0 when there is none within 700 bytes (not a function the tests know). */
static unsigned shape_extent(uint32_t vram, uint32_t *w) {
    uint32_t farthest = 0;
    for (unsigned i = 0; i < SHAPE_MAX_WORDS; i++) {
        uint32_t pc = vram + 4u * i;
        w[i] = fetch(pc);
        uint32_t op = OPC(w[i]);
        /* an indirect jump or call (`jalr`, `jr` through anything but $ra): none of the routines the tests know has one,
           and code that does is a stub or a bigger function that runs on into the routine after it (a program's
           entry: `jalr $a0` and then osCreateThread, six words later, judged as one function) */
        if (op == 0 && ((w[i] & 0xFC1F07FFu) == 9u || ((w[i] & 0xFC1FFFFFu) == 8u && RS(w[i]) != 31))) return 0;
        if (op == 1 || (op >= 4 && op <= 7) || (op >= 0x14 && op <= 0x17)) {
            int32_t off = (int16_t)(uint16_t)IMM(w[i]);
            uint32_t to = pc + 4u + (uint32_t)(off * 4);
            if (off > 0 && to > farthest) farthest = to;
        }
        if (w[i] == 0x03E00008u && pc + 8u >= farthest && i + 1 < SHAPE_MAX_WORDS) {
            w[i + 1] = fetch(pc + 4u);
            return i + 2;
        }
    }
    return 0;
}

#define SHAPE_CACHE 512
static struct { uint32_t vram, hash; int known; PortMipsCall call; } shaped[SHAPE_CACHE];
static PortMipsCall with_shape(uint32_t vram) {
    if (!port_mips_shape_count || (vram & 3u) || vram < 0x80000400u || vram >= 0x80800000u) return 0;
    uint32_t h = 2166136261u;
    for (unsigned i = 0; i < 8; i++) h = (h ^ fetch(vram + 4u * i)) * 16777619u;
    if (!h) return 0;
    unsigned slot = (vram >> 2) & (SHAPE_CACHE - 1);
    if (shaped[slot].known && shaped[slot].vram == vram && shaped[slot].hash == h) return shaped[slot].call;
    PortMipsCall call = 0;
    uint32_t w[SHAPE_MAX_WORDS + 2];
    unsigned n = shape_extent(vram, w);
    const char *name = n ? shape_identify(w, n, vram) : 0;
    if (name) {
        for (unsigned i = 0; i < port_mips_shape_count; i++)
            if (!strcmp(port_mips_shapes[i].name, name)) { call = port_mips_shapes[i].call; break; }
        if (call) {
            static uint32_t said[64];
            static unsigned count;
            unsigned k = 0;
            while (k < count && said[k] != vram) k++;
            if (k == count && count < 64) {
                said[count++] = vram;
                fprintf(stderr, "interpreter: the code at %08X is libultra's %s (told by its structure): the host's runs instead\n",
                        vram, name);
            }
        }
    }
    shaped[slot].vram = vram; shaped[slot].hash = h; shaped[slot].known = 1; shaped[slot].call = call;
    return call;
}
/* for the tests: the library's name for the function at `vram` in memory, or 0 */
const char *port_mips_shape_name(uint32_t vram) {
    uint32_t w[SHAPE_MAX_WORDS + 2];
    if (!M) M = port_memory();
    unsigned n = shape_extent(vram, w);
    return n ? shape_identify(w, n, vram) : 0;
}

/* `shapes`: also ask the structural test. Only a call's target is told by structure: the address a function returns
   to is no call, and a stub that returns into the start of a routine (a program's `jalr $a0`, whose return address is
   the routine after it) ran that routine again and again, each time returning to the same address. */
static PortMipsCall with_c_at(uint32_t vram, int shapes);
static PortMipsCall with_c(uint32_t vram, int shapes) {
    PortMipsCall c = with_c_at(vram, shapes);
    if (c) return c;
    /* code a window keeps at its physical address is known by the project's name for it (0x80000000 | virtual) */
    uint32_t name = port_tlb_spelled(vram);
    return name != vram ? with_c_at(name, 0) : 0;
}
static PortMipsCall with_c_at(uint32_t vram, int shapes) {
    uint32_t slot = (vram >> 2) & (CACHE - 1);
    if (found[slot].vram == vram && (found[slot].call || (!port_mips_pattern_count && !port_mips_shape_count)))
        return found[slot].call;
    unsigned lo = 0, hi = port_mips_call_count;
    while (lo < hi) { unsigned mid = (lo + hi) / 2; if (port_mips_calls[mid].vram < vram) lo = mid + 1; else hi = mid; }
    PortMipsCall c = lo < port_mips_call_count && port_mips_calls[lo].vram == vram ? port_mips_calls[lo].call : 0;
    found[slot].vram = vram;
    found[slot].call = c;
    /* An overlay can be replaced at the same address: neither a match nor a miss is durable. */
    if (c) return c;
    PortMipsCall p = with_pattern(vram);
    return p ? p : shapes ? with_shape(vram) : 0;
}

/* ---------------------------------------------------------------- each host thread's MIPS stack */

static uint32_t stack_top[MIPS_THREADS + 1]; /* where its next interpreted frame starts (0: its stack's top) */
static int thread_slot(void) {
    int n = port_thread_number() + 1; /* -1: the boot, before any thread */
    return n < 0 ? 0 : n > MIPS_THREADS ? MIPS_THREADS : n;
}
static uint32_t frame_base(int slot) {
    return stack_top[slot] ? stack_top[slot] : MIPS_STACKS + (uint32_t)(slot + 1) * MIPS_STACK - 0x100u;
}

/* ---------------------------------------------------------------- variadic functions' extra arguments */

/* o32 passes a variadic function's extra arguments as it passes any: the words after the fixed ones, in $a0-$a3
   and then on the stack. The game's C takes them as a pointer to a buffer of them (WebAssembly's convention),
   big-endian in both builds (the browser's byte-swaps each variadic call's extra arguments as the native build's
   memory is: web/webir.py; only the va_list's own pointer is little-endian). A racing game's allocator takes its
   block size that way: given the next register word as the pointer, it read the size from address 8 and never
   returned. */
#define VARARGS 32 /* the words a call passes on: more than any of a game's printf-like calls takes */
#define put_word sw
#define get_word lw
/* For a variadic function with C: the argument words from `first` on, as its buffer, below the caller's frame
   (call_c keeps interpreted frames below it); returns its address. */
uint32_t port_mips_varargs(const uint64_t *r, int first) {
    if (!M) M = port_memory();
    uint32_t sp = (uint32_t)r[29], buf = (sp - 16u - 4u * VARARGS) & ~7u;
    for (int i = 0; i < VARARGS; i++) {
        int n = first + i;
        put_word(buf + 4u * (uint32_t)i, n < 4 ? (uint32_t)r[4 + n] : lw(sp + 4u * (uint32_t)n));
    }
    return buf;
}
/* For a variadic function run here, called by the game's C with its buffer at `buf`: those words as o32 passes
   them, from argument word `first` on (the frame port_mips_enter made has room for them). */
void port_mips_spread(uint64_t *r, int first, uint32_t buf) {
    if (!M) M = port_memory();
    for (int i = 0; i < VARARGS / 2; i++) {
        int n = first + i;
        uint32_t v = get_word(buf + 4u * (uint32_t)i);
        if (n < 4) r[4 + n] = (uint64_t)(int64_t)(int32_t)v;
        else sw((uint32_t)r[29] + 4u * (uint32_t)n, v);
    }
}

/* A structure passed by value: o32 passes its words as argument words, from `n` on; the game's C takes a pointer to
   it (WebAssembly's byval). The argument words in memory: $a0-$a3 in the home slots the caller keeps for them,
   then the stack's, as a variadic MIPS function spills them, big-endian as the game's memory is. */
uint32_t port_mips_slot(const uint64_t *r, int n) {
    if (!M) M = port_memory();
    uint32_t sp = (uint32_t)r[29];
    for (int i = 0; i < 4; i++) sw(sp + 4u * (uint32_t)i, (uint32_t)r[4 + i]);
    return sp + 4u * (uint32_t)n;
}

/* ---------------------------------------------------------------- the interpreter */

typedef struct { uint64_t r[32], hi, lo; uint32_t f[32], fcr31, status, entry_hi, tlb_index, entry_lo[2], page_mask, cause, epc, context, badvaddr; } Cpu;
static int tracing = -1;

HOT int64_t sx32(uint32_t v) { return (int64_t)(int32_t)v; }
HOT float fs(const Cpu *c, int n) { float v; memcpy(&v, &c->f[n], 4); return v; }
HOT void set_fs(Cpu *c, int n, float v) { memcpy(&c->f[n], &v, 4); }
HOT double fd(const Cpu *c, int n) { n &= ~1; uint64_t b = (uint64_t)c->f[n + 1] << 32 | c->f[n]; double v; memcpy(&v, &b, 8); return v; }
HOT void set_fd(Cpu *c, int n, double v) { n &= ~1; uint64_t b; memcpy(&b, &v, 8); c->f[n] = (uint32_t)b; c->f[n + 1] = (uint32_t)(b >> 32); }
HOT uint64_t fl(const Cpu *c, int n) { n &= ~1; return (uint64_t)c->f[n + 1] << 32 | c->f[n]; }
HOT void set_fl(Cpu *c, int n, uint64_t v) { n &= ~1; c->f[n] = (uint32_t)v; c->f[n + 1] = (uint32_t)(v >> 32); }
/* rounding to an integer as FCR31's mode asks (round.w and the like name theirs) */
static double rounded(double v, int mode) {
    switch (mode & 3) { case 0: return nearbyint(v); case 1: return trunc(v); case 2: return ceil(v); default: return floor(v); }
}
/* 64 x 64 -> 128 bits, without a 128-bit type (a 32-bit ARM's GCC has none) */
static void mul64(uint64_t a, uint64_t b, uint64_t *hi, uint64_t *lo) {
    uint64_t al = (uint32_t)a, ah = a >> 32, bl = (uint32_t)b, bh = b >> 32;
    uint64_t ll = al * bl, lh = al * bh, hl = ah * bl, hh = ah * bh;
    uint64_t mid = (ll >> 32) + (uint32_t)lh + (uint32_t)hl;
    *lo = (mid << 32) | (uint32_t)ll;
    *hi = hh + (lh >> 32) + (hl >> 32) + (mid >> 32);
}

/* Builds without N64Recomp have no recompiled stack to hand over. */
__attribute__((weak)) uint32_t port_recomp_stack_enter(uint32_t sp) { (void)sp; return 0; }
__attribute__((weak)) void port_recomp_stack_leave(uint32_t saved) { (void)saved; }

static void call_c(Cpu *c, PortMipsCall fn) {
    int slot = thread_slot();
    uint32_t saved = stack_top[slot];
    /* interpreted code it calls goes below this frame, and below the buffer a variadic callee's extra arguments are
       copied into (port_mips_varargs) */
    stack_top[slot] = ((uint32_t)c->r[29] - 16u - 4u * VARARGS) & ~7u;
    uint32_t recomp_saved = port_recomp_stack_enter(stack_top[slot]);
    fn(c->r, c->f);
    port_recomp_stack_leave(recomp_saved);
    stack_top[slot] = saved;
    c->r[0] = 0;
}

/* PORT_INTERP_TRACE=3: the last jumps this thread took ({from, to, $ra}), printed when the interpreter meets
   something it can't run: a wild jump's first wrong step is in the lines before it. */
#define JUMP_HISTORY 40
static __thread struct { uint32_t from, to, ra, sp; } jumps[JUMP_HISTORY];
static __thread unsigned jump_n;
static void jump_history(const char *why, uint32_t pc) {
    static int said;
    if (tracing < 3 || said++ >= 4) return;
    fprintf(stderr, "interpreter: %s at %08X; the last jumps (from -> to, $ra, $sp):\n", why, pc);
    for (unsigned i = 0; i < JUMP_HISTORY && i < jump_n; i++) {
        unsigned k = (jump_n - 1 - i) % JUMP_HISTORY;
        fprintf(stderr, "  %08X -> %08X  ra %08X  sp %08X\n", jumps[k].from, jumps[k].to, jumps[k].ra, jumps[k].sp);
    }
}
static void unknown(uint32_t pc, uint32_t op) {
    static int said;
    if (said++ < 20) fprintf(stderr, "interpreter: instruction %08X at %08X isn't one it runs: skipped\n", op, pc);
    jump_history("an unknown instruction", pc);
}

/* One instruction at pc; a branch or jump says where it goes (*target, its delay slot still to run), or that a
   likely branch not taken skips its delay slot. Returns 1 for a jump the next instruction's run must check for C
   (a call or a jump to another function), 4 for a call through a pointer (the same, and a safe point), 2 for a
   branch, 3 for a likely branch not taken, 0 for neither. */
static int step(Cpu *c, uint32_t pc, uint32_t op, uint32_t *target) {
    uint64_t *r = c->r;
    int rs = op >> 21 & 31, rt = op >> 16 & 31, rd = op >> 11 & 31, sa = op >> 6 & 31;
    int16_t imm = (int16_t)op;
    uint32_t uimm = op & 0xFFFF, addr = (uint32_t)r[rs] + (uint32_t)(int32_t)imm;
    uint32_t br = pc + 4 + ((uint32_t)(int32_t)imm << 2);
#define BRANCH(cond) do { if (cond) { *target = br; return 2; } return 0; } while (0)
#define LIKELY(cond) do { if (cond) { *target = br; return 2; } return 3; } while (0)
    switch (op >> 26) {
    case 0: /* SPECIAL */
        switch (op & 63) {
        case 0: r[rd] = sx32((uint32_t)r[rt] << sa); break;
        case 2: r[rd] = sx32((uint32_t)r[rt] >> sa); break;
        case 3: r[rd] = sx32((uint32_t)((int32_t)r[rt] >> sa)); break;
        case 4: r[rd] = sx32((uint32_t)r[rt] << (r[rs] & 31)); break;
        case 6: r[rd] = sx32((uint32_t)r[rt] >> (r[rs] & 31)); break;
        case 7: r[rd] = sx32((uint32_t)((int32_t)r[rt] >> (r[rs] & 31))); break;
        case 8: *target = (uint32_t)r[rs]; return 1;                          /* jr */
        case 9: *target = (uint32_t)r[rs]; r[rd] = sx32(pc + 8); return 4;   /* jalr: a call through a pointer */
        case 12: fprintf(stderr, "interpreter: a syscall at %08X: skipped\n", pc); break;
        case 13: fprintf(stderr, "interpreter: a break at %08X: skipped\n", pc); break;
        case 15: break;                                                       /* sync */
        case 16: r[rd] = c->hi; break;
        case 17: c->hi = r[rs]; break;
        case 18: r[rd] = c->lo; break;
        case 19: c->lo = r[rs]; break;
        case 20: r[rd] = r[rt] << (r[rs] & 63); break;
        case 22: r[rd] = r[rt] >> (r[rs] & 63); break;
        case 23: r[rd] = (uint64_t)((int64_t)r[rt] >> (r[rs] & 63)); break;
        case 24: { int64_t p = (int64_t)(int32_t)r[rs] * (int32_t)r[rt]; c->lo = sx32((uint32_t)p); c->hi = sx32((uint32_t)((uint64_t)p >> 32)); break; }
        case 25: { uint64_t p = (uint64_t)(uint32_t)r[rs] * (uint32_t)r[rt]; c->lo = sx32((uint32_t)p); c->hi = sx32((uint32_t)(p >> 32)); break; }
        case 26: { int32_t a = (int32_t)r[rs], b = (int32_t)r[rt];
            if (b == 0) { c->lo = a < 0 ? 1 : (uint64_t)-1; c->hi = sx32((uint32_t)a); }
            else if (a == INT32_MIN && b == -1) { c->lo = sx32((uint32_t)a); c->hi = 0; }
            else { c->lo = sx32((uint32_t)(a / b)); c->hi = sx32((uint32_t)(a % b)); }
            break; }
        case 27: { uint32_t a = (uint32_t)r[rs], b = (uint32_t)r[rt];
            if (b == 0) { c->lo = (uint64_t)-1; c->hi = sx32(a); } else { c->lo = sx32(a / b); c->hi = sx32(a % b); }
            break; }
        case 28: { uint64_t a = r[rs], b = r[rt], hi, lo; mul64(a, b, &hi, &lo);
            if ((int64_t)a < 0) hi -= b;
            if ((int64_t)b < 0) hi -= a;
            c->hi = hi; c->lo = lo; break; }
        case 29: mul64(r[rs], r[rt], &c->hi, &c->lo); break;
        case 30: { int64_t a = (int64_t)r[rs], b = (int64_t)r[rt];
            if (b == 0) { c->lo = a < 0 ? 1 : (uint64_t)-1; c->hi = (uint64_t)a; }
            else if (a == INT64_MIN && b == -1) { c->lo = (uint64_t)a; c->hi = 0; }
            else { c->lo = (uint64_t)(a / b); c->hi = (uint64_t)(a % b); }
            break; }
        case 31: { uint64_t a = r[rs], b = r[rt];
            if (b == 0) { c->lo = (uint64_t)-1; c->hi = a; } else { c->lo = a / b; c->hi = a % b; }
            break; }
        case 32: case 33: r[rd] = sx32((uint32_t)r[rs] + (uint32_t)r[rt]); break;
        case 34: case 35: r[rd] = sx32((uint32_t)r[rs] - (uint32_t)r[rt]); break;
        case 36: r[rd] = r[rs] & r[rt]; break;
        case 37: r[rd] = r[rs] | r[rt]; break;
        case 38: r[rd] = r[rs] ^ r[rt]; break;
        case 39: r[rd] = ~(r[rs] | r[rt]); break;
        case 42: r[rd] = (int64_t)r[rs] < (int64_t)r[rt]; break;
        case 43: r[rd] = r[rs] < r[rt]; break;
        case 44: case 45: r[rd] = r[rs] + r[rt]; break;
        case 46: case 47: r[rd] = r[rs] - r[rt]; break;
        case 48: case 49: case 50: case 51: case 52: case 54: break;          /* traps: never taken here */
        case 56: r[rd] = r[rt] << sa; break;
        case 58: r[rd] = r[rt] >> sa; break;
        case 59: r[rd] = (uint64_t)((int64_t)r[rt] >> sa); break;
        case 60: r[rd] = r[rt] << (sa + 32); break;
        case 62: r[rd] = r[rt] >> (sa + 32); break;
        case 63: r[rd] = (uint64_t)((int64_t)r[rt] >> (sa + 32)); break;
        default: unknown(pc, op);
        }
        break;
    case 1: /* REGIMM */ {
        int64_t v = (int64_t)r[rs];
        switch (rt) {
        case 0: BRANCH(v < 0);
        case 1: BRANCH(v >= 0);
        case 2: LIKELY(v < 0);
        case 3: LIKELY(v >= 0);
        case 16: r[31] = sx32(pc + 8); BRANCH(v < 0);
        case 17: r[31] = sx32(pc + 8); BRANCH(v >= 0);
        case 18: r[31] = sx32(pc + 8); LIKELY(v < 0);
        case 19: r[31] = sx32(pc + 8); LIKELY(v >= 0);
        default: if (rt < 8 || rt > 14) unknown(pc, op); /* (8 to 14: traps, never taken here) */
        }
        break; }
    case 2: *target = port_tlb_jump(pc + 4, (op & 0x03FFFFFFu) << 2); return 1;
    case 3: *target = port_tlb_jump(pc + 4, (op & 0x03FFFFFFu) << 2); r[31] = sx32(pc + 8); return 1;
    case 4: BRANCH(r[rs] == r[rt]);
    case 5: BRANCH(r[rs] != r[rt]);
    case 6: BRANCH((int64_t)r[rs] <= 0);
    case 7: BRANCH((int64_t)r[rs] > 0);
    case 8: case 9: r[rt] = sx32((uint32_t)r[rs] + (uint32_t)(int32_t)imm); break;
    case 10: r[rt] = (int64_t)r[rs] < (int64_t)imm; break;
    case 11: r[rt] = r[rs] < (uint64_t)(int64_t)imm; break;
    case 12: r[rt] = r[rs] & uimm; break;
    case 13: r[rt] = r[rs] | uimm; break;
    case 14: r[rt] = r[rs] ^ uimm; break;
    case 15: r[rt] = sx32(uimm << 16); break;
    case 16: { /* COP0 */
        uint32_t *reg = rd == 12 ? &c->status : rd == 13 ? &c->cause : rd == 14 ? &c->epc : rd == 10 ? &c->entry_hi : rd == 0 ? &c->tlb_index :
                        rd == 5 ? &c->page_mask : rd == 4 ? &c->context : rd == 8 ? &c->badvaddr :
                        rd == 2 || rd == 3 ? &c->entry_lo[rd - 2] : NULL;
        if ((rs == 0 || rs == 1) && rd == 6) r[rt] = (int64_t)port_tlb_wired(0, 0); /* Wired: tlbwr only replaces the slots above it */
        else if ((rs == 4 || rs == 5) && rd == 6) port_tlb_wired(1, (uint32_t)r[rt]);
        else if (rs == 0 || rs == 1)
            r[rt] = rd == 9 ? sx32(w2c_env_0x5F_builtin_mips_read_count(0)) : reg ? sx32(*reg) : 0;
        else if ((rs == 4 || rs == 5) && reg) *reg = (uint32_t)r[rt];
        /* The raw PI routine probes the TLB if its buffer lives on a compatibility stack.
           Expose a pair of 4 KB pages on the compatibility host’s PI bus. */
        else if (op == 0x42000002u) port_tlb_set(c->tlb_index & 31u, c->page_mask, c->entry_hi, c->entry_lo[0], c->entry_lo[1]); /* tlbwi */
        else if (op == 0x42000006u) port_tlb_write(c->page_mask, c->entry_hi, c->entry_lo[0], c->entry_lo[1]);                   /* tlbwr */
        else if (op == 0x42000008u) { /* tlbp: an entry the game wrote answers first; the compatibility stack's pages after */
            uint32_t found = port_tlb_probe(c->entry_hi);
            c->tlb_index = found != 0x80000000u ? found : port_stack_bus(c->entry_hi & ~0x1FFFu) ? 0 : 0x80000000u;
        }
        else if (op == 0x42000001u && !(c->tlb_index & 0x80000000u) && port_tlb_read(c->tlb_index & 31u, &c->page_mask, &c->entry_hi,
                                                                                    &c->entry_lo[0], &c->entry_lo[1])) {
            /* tlbr of a slot the game wrote: its words back in the registers */
        }
        else if (op == 0x42000001u && c->tlb_index == 0) {
            uint32_t bus = port_stack_bus(c->entry_hi & ~0x1FFFu);
            c->page_mask = 0;
            c->entry_lo[0] = bus ? (bus >> 6) | 2 : 0;
            c->entry_lo[1] = bus ? c->entry_lo[0] + (0x1000u >> 6) : 0;
        }
        break;
    }
    case 17: { /* COP1 */
        int fmt = rs, ft = rt, fsr = rd, fdr = sa, fn = op & 63;
        switch (fmt) {
        case 0: r[rt] = sx32(c->f[fsr]); return 0;                                  /* mfc1 */
        case 1: r[rt] = fl(c, fsr); return 0;                                       /* dmfc1 */
        case 2: r[rt] = sx32(fsr == 31 ? c->fcr31 : fsr == 0 ? 0xA00u : 0); return 0; /* cfc1 */
        case 4: c->f[fsr] = (uint32_t)r[rt]; return 0;                              /* mtc1 */
        case 5: set_fl(c, fsr, r[rt]); return 0;                                    /* dmtc1 */
        case 6: if (fsr == 31) c->fcr31 = (uint32_t)r[rt]; return 0;                /* ctc1 */
        case 8: { int cond = (c->fcr31 >> 23) & 1;                                 /* bc1f, bc1t, bc1fl, bc1tl */
            switch (rt & 3) { case 0: BRANCH(!cond); case 1: BRANCH(cond); case 2: LIKELY(!cond); default: LIKELY(cond); } }
        }
        if (fn >= 48) { /* c.cond.fmt */
            double a = fmt == 17 ? fd(c, fsr) : fs(c, fsr), b = fmt == 17 ? fd(c, ft) : fs(c, ft);
            int un = isnan(a) || isnan(b), cond = (un && (fn & 1)) || (!un && (((fn & 2) && a == b) || ((fn & 4) && a < b)));
            c->fcr31 = cond ? c->fcr31 | 0x800000u : c->fcr31 & ~0x800000u;
            return 0;
        }
        if (fmt == 16 || fmt == 17) {
            int d = fmt == 17;
            double a = d ? fd(c, fsr) : fs(c, fsr), b = d ? fd(c, ft) : fs(c, ft), v;
#define PUT(x) do { if (d) set_fd(c, fdr, (x)); else set_fs(c, fdr, (float)(x)); } while (0)
            switch (fn) {
            case 0: if (d) set_fd(c, fdr, a + b); else set_fs(c, fdr, fs(c, fsr) + fs(c, ft)); return 0;
            case 1: if (d) set_fd(c, fdr, a - b); else set_fs(c, fdr, fs(c, fsr) - fs(c, ft)); return 0;
            case 2: if (d) set_fd(c, fdr, a * b); else set_fs(c, fdr, fs(c, fsr) * fs(c, ft)); return 0;
            case 3: if (d) set_fd(c, fdr, a / b); else set_fs(c, fdr, fs(c, fsr) / fs(c, ft)); return 0;
            case 4: if (d) set_fd(c, fdr, sqrt(a)); else set_fs(c, fdr, sqrtf(fs(c, fsr))); return 0;
            case 5: PUT(fabs(a)); return 0;
            case 6: if (d) set_fl(c, fdr, fl(c, fsr)); else c->f[fdr] = c->f[fsr]; return 0;
            case 7: PUT(-a); return 0;
            case 8: case 9: case 10: case 11: set_fl(c, fdr, (uint64_t)(int64_t)rounded(a, fn - 8)); return 0;
            case 12: case 13: case 14: case 15: v = rounded(a, fn - 12); c->f[fdr] = (uint32_t)(int32_t)v; return 0;
            case 32: set_fs(c, fdr, (float)a); return 0;
            case 33: set_fd(c, fdr, a); return 0;
            case 36: c->f[fdr] = (uint32_t)(int32_t)rounded(a, (int)(c->fcr31 & 3)); return 0;
            case 37: set_fl(c, fdr, (uint64_t)(int64_t)rounded(a, (int)(c->fcr31 & 3))); return 0;
            }
#undef PUT
        } else if (fmt == 20 || fmt == 21) { /* from a word or a long */
            double a = fmt == 20 ? (double)(int32_t)c->f[fsr] : (double)(int64_t)fl(c, fsr);
            if (fn == 32) { set_fs(c, fdr, fmt == 20 ? (float)(int32_t)c->f[fsr] : (float)(int64_t)fl(c, fsr)); return 0; }
            if (fn == 33) { set_fd(c, fdr, a); return 0; }
        }
        unknown(pc, op);
        return 0; }
    case 20: LIKELY(r[rs] == r[rt]);
    case 21: LIKELY(r[rs] != r[rt]);
    case 22: LIKELY((int64_t)r[rs] <= 0);
    case 23: LIKELY((int64_t)r[rs] > 0);
    case 24: case 25: r[rt] = r[rs] + (uint64_t)(int64_t)imm; break;
    case 26: { int sh = (addr & 7) * 8; uint64_t m = ld(addr & ~7u); r[rt] = sh ? (r[rt] & ((1ull << sh) - 1)) | (m << sh) : m; break; } /* ldl */
    case 27: { int sh = (7 - (addr & 7)) * 8; uint64_t m = ld(addr & ~7u); r[rt] = sh ? (r[rt] & ~(~0ull >> sh)) | (m >> sh) : m; break; } /* ldr */
    case 32: r[rt] = (uint64_t)(int64_t)(int8_t)lb(addr); break;
    case 33: r[rt] = (uint64_t)(int64_t)(int16_t)lh(addr); break;
    case 34: { int sh = (addr & 3) * 8; uint32_t m = lw(addr & ~3u), v = (uint32_t)r[rt];
        r[rt] = sx32(sh ? (v & ((1u << sh) - 1)) | (m << sh) : m); break; }                               /* lwl */
    case 35: case 48: r[rt] = sx32(lw(addr)); break;                                                     /* lw, ll */
    case 36: r[rt] = lb(addr); break;
    case 37: r[rt] = lh(addr); break;
    case 38: { int sh = (3 - (addr & 3)) * 8; uint32_t m = lw(addr & ~3u), v = (uint32_t)r[rt];
        r[rt] = sx32(sh ? (v & ~(0xFFFFFFFFu >> sh)) | (m >> sh) : m); break; }                         /* lwr */
    case 39: r[rt] = lw(addr); break;
    case 40: sb(addr, (uint8_t)r[rt]); break;
    case 41: sh(addr, (uint16_t)r[rt]); break;
    case 42: { int sh = (addr & 3) * 8; uint32_t m = lw(addr & ~3u), v = (uint32_t)r[rt];
        sw(addr & ~3u, sh ? (m & ~(0xFFFFFFFFu >> sh)) | (v >> sh) : v); break; }                        /* swl */
    case 43: sw(addr, (uint32_t)r[rt]); break;
    case 44: { int sh = (addr & 7) * 8; uint64_t m = ld(addr & ~7u);
        sd(addr & ~7u, sh ? (m & ~(~0ull >> sh)) | (r[rt] >> sh) : r[rt]); break; }                     /* sdl */
    case 45: { int sh = (7 - (addr & 7)) * 8; uint64_t m = ld(addr & ~7u);
        sd(addr & ~7u, sh ? (m & ~(~0ull << sh)) | (r[rt] << sh) : r[rt]); break; }                     /* sdr */
    case 46: { int sh = (3 - (addr & 3)) * 8; uint32_t m = lw(addr & ~3u), v = (uint32_t)r[rt];
        sw(addr & ~3u, sh ? (m & ~(0xFFFFFFFFu << sh)) | (v << sh) : v); break; }                       /* swr */
    case 47: break;                                                                                      /* cache */
    case 49: c->f[rt] = lw(addr); break;                                                                 /* lwc1 */
    case 52: case 55: r[rt] = ld(addr); break;                                                           /* lld, ld */
    case 53: set_fl(c, rt, ld(addr)); break;                                                             /* ldc1 */
    case 56: sw(addr, (uint32_t)r[rt]); r[rt] = 1; break;                                                /* sc */
    case 57: sw(addr, c->f[rt]); break;                                                                  /* swc1 */
    case 60: sd(addr, r[rt]); r[rt] = 1; break;                                                          /* scd */
    case 61: sd(addr, fl(c, rt)); break;                                                                 /* sdc1 */
    case 63: sd(addr, r[rt]); break;
    default: unknown(pc, op);
    }
    return 0;
#undef BRANCH
#undef LIKELY
}

/* PORT_PC_TRACE=addr,addr,...: a line with the registers each time the interpreter executes an instruction at one of the
   addresses (a function's entry, a call site): how a game's own control flow (its coroutines, its dispatchers) is followed. */
static uint32_t pc_trace_at[8];
static int pc_trace_n = -1;
static void pc_trace(const Cpu *c, uint32_t pc) {
    for (int i = 0; i < pc_trace_n; i++)
        if (pc_trace_at[i] == pc) {
            fprintf(stderr, "pc %08X: a0=%08X a1=%08X a2=%08X a3=%08X sp=%08X ra=%08X v0=%08X t0=%08X s0=%08X s1=%08X s2=%08X s3=%08X\n",
                    pc, (uint32_t)c->r[4], (uint32_t)c->r[5], (uint32_t)c->r[6], (uint32_t)c->r[7], (uint32_t)c->r[29],
                    (uint32_t)c->r[31], (uint32_t)c->r[2], (uint32_t)c->r[8], (uint32_t)c->r[16], (uint32_t)c->r[17],
                    (uint32_t)c->r[18], (uint32_t)c->r[19]);
            return;
        }
}
static void pc_trace_init(void) {
    pc_trace_n = 0;
    const char *e = getenv("PORT_PC_TRACE");
    while (e && *e && pc_trace_n < 8) {
        pc_trace_at[pc_trace_n++] = (uint32_t)strtoul(e, (char **)&e, 16);
        if (*e == ',') e++;
    }
}

/* PORT_INTERP_STATS=1: the instructions interpreted so far and their rate, printed every 16 M (the host ends with
   _exit, so there is no later moment to print at): how the interpreter's speed is measured. */
static void interp_stats(unsigned long n) {
    static int on = -1;
    static unsigned long total, next = 1ul << 24;
    static struct timespec t0;
    if (on < 0) { on = getenv("PORT_INTERP_STATS") != 0; if (on) clock_gettime(CLOCK_MONOTONIC, &t0); }
    if (!on) return;
    unsigned long t = __atomic_add_fetch(&total, n, __ATOMIC_RELAXED);
    if (t >= next) {
        next = t + (1ul << 24);
        struct timespec now;
        clock_gettime(CLOCK_MONOTONIC, &now);
        double secs = (double)(now.tv_sec - t0.tv_sec) + (double)(now.tv_nsec - t0.tv_nsec) / 1e9;
        fprintf(stderr, "interpreter stats: %lu M instructions in %.1f s: %.1f M/s\n", t >> 20, secs,
                secs > 0 ? (double)t / secs / 1e6 : 0.0);
    }
}

/* Run the function at `entry` with the registers `r` and float registers `f` as o32 left them ($sp, $ra: the
   caller's sentinel, set by port_mips_enter), until it returns. */
uint32_t port_interp_pc;
void port_mips_run(uint32_t entry, uint64_t *regs, uint32_t *fregs) {
    if (!M) M = port_memory();
    if (tracing < 0) tracing = getenv("PORT_INTERP_TRACE") ? atoi(getenv("PORT_INTERP_TRACE")) : 0;
    if (pc_trace_n < 0) pc_trace_init();
    entry = port_tlb_virtual(entry);
    /* an entry at a mapped virtual address (a callback pointer into a window): its virtual pc is kept from the first
       instruction, as for a jump into a window, so a branch lands on the next virtual page, not the next frame */
    uint32_t entry_virtual = (entry - 0x80000000u >= 0x40000000u && port_tlb_n && mapped(entry) != entry) ? entry : 0;
    entry = port_tlb_call(mapped(entry));
    port_note_entry(entry);
    if (tracing) fprintf(stderr, "interpreter: %08X (%08X, %08X, %08X, %08X)\n", entry, (uint32_t)regs[4],
                         (uint32_t)regs[5], (uint32_t)regs[6], (uint32_t)regs[7]);
    Cpu c = {0};
    memcpy(c.r, regs, sizeof c.r);
    memcpy(c.f, fregs, sizeof c.f);
    c.status = 0x2000FF01u;
    PortMipsCall relocated = with_pattern(entry);
    if (!relocated) relocated = with_shape(entry);
    if (relocated) { call_c(&c, relocated); goto done; }
    uint32_t pc = entry;
    /* The virtual address the code at `pc` has when it was reached through a TLB window (pc is its spelling in RAM,
       which is what the rest of the port finds functions by): the next instruction of a page is the next virtual
       page's, not the next frame in RAM (a game that pages its code in puts consecutive pages in any frames). */
    uint32_t vpc = entry_virtual;
    unsigned long steps = 0;
    int zeros = 0;
    unsigned fault_n = 0;
    uint32_t fault_pc = 1;
    for (;;) {
        /* A fetch from a virtual address no valid TLB entry covers is an exception on the console, and the game's own
           handler answers it. No entry for the page at all is a TLB refill (the vector 0x80000000): the handler reads
           Context, loads the page pair's EntryLo words from the game's table, writes them with tlbwr and returns with
           eret to retry the fetch (a shooter links its program at 0x7F000000 and brings 4 KB pages in as the code first
           runs: "no instructions at 8F000CD0"). An entry that covers the page with its valid bit clear is TLB Invalid
           (the general vector 0x80000180): the game's general handler pages the code in from the cartridge, writes the
           entry again, valid, and returns with eret. Context holds the faulting page's number in bits 4 to 22,
           EntryHi its address, Cause the code (2, TLBL), EPC the fetch itself. A page that stays faulting after 16
           refills (4 invalid exceptions: a libultra handler that hands the thread to a pager thread through kernel
           state the host does not keep never retries) has no handler that maps it, and the fetch goes on as before. */
        if (__builtin_expect(pc - 0x80000000u >= 0x40000000u && port_tlb_n && !(c.status & 2u), 0) && mapped(pc) == pc) {
            uint32_t vector = port_tlb_invalid(pc) ? 0x80000180u : 0x80000000u;
            if (fetch(vector)) {
                if (pc != fault_pc) { fault_pc = pc; fault_n = 0; }
                /* TLB Invalid on a game that registered a pager for OS_EVENT_FAULT: libultra's kernel would send the
                   fault to that thread and stop this one; the host does that (it keeps no kernel state for the game's
                   own handler to run on) and the fetch is retried once the pager has restarted the thread */
                if (vector == 0x80000180u && fault_n < 16 && port_tlb_fault(pc, pc, 2u << 2)) {
                    fault_n++;
                    if (tracing) fprintf(stderr, "interpreter: TLB invalid for a fetch at %08X: the game's pager answered\n", pc);
                    continue;
                }
                if (fault_n++ < (vector == 0x80000180u ? 4u : 16u)) {
                    if (tracing) fprintf(stderr, "interpreter: TLB %s for a fetch at %08X (from %08X)\n",
                                         vector == 0x80000180u ? "invalid" : "refill", pc, entry);
                    c.epc = pc;
                    c.badvaddr = pc;
                    c.entry_hi = pc & ~0x1FFFu;
                    c.context = (c.context & ~0x7FFFF0u) | (((pc >> 13) & 0x7FFFFu) << 4);
                    c.cause = 2u << 2; /* TLBL */
                    c.status |= 2u;
                    pc = vector; vpc = 0;
                    continue;
                }
            }
        }
        uint32_t op = fetch(pc), target = 0;
        if (op == 0 && ++zeros > 64) { /* a run of nothing: no code there (no ROM loaded where it was looked for) */
            fprintf(stderr, "interpreter: no instructions at %08X (from %08X): the function returns at once\n", pc, entry);
            jump_history("no instructions", pc);
            break;
        }
        if (op) zeros = 0;
        if (__builtin_expect(pc_trace_n, 0)) pc_trace(&c, pc);
        if ((op >> 26) == 0 && (op & 63) == 12 && fetch(0x80000180u)) {
            /* syscall: an exception, taken as the console takes it. The game installed its own handler at the general
               exception vector (libultra's osInitialize copies a jump to __osException there), and a game's handler
               can use SYSCALL on purpose: an overlay game's thunks are `syscall N` words, and its handler sends EPC to
               a dispatcher that reads N from the thunk, loads the overlay and calls it. Skipping the syscall left
               every call into an overlay doing nothing. EPC is the syscall's own address, Cause says
               "syscall", and Status.EXL is set until the handler's eret. */
            c.epc = pc;
            c.cause = 8u << 2;
            c.status |= 2u;
            pc = 0x80000180u;
            vpc = 0;
            continue;
        }
        if (op == 0x42000018u && (c.status & 2u)) {
            /* eret out of such an exception: back to where EPC says (the handler may have moved it), EXL cleared */
            c.status &= ~2u;
            pc = c.epc;
            vpc = 0;
            /* EPC is the faulting fetch's virtual address, which the handler has mapped by now: the code goes on at
               its frame with that virtual address kept (a refill handler that brought one page in left the rest of
               the program's pages to come the same way) */
            if (pc - 0x80000000u >= 0x40000000u && port_tlb_n && mapped(pc) != pc) { vpc = pc; pc = mapped(pc); }
            continue;
        }
        if (op == 0x42000018u) {
            /* eret: libultra's thread dispatch ends with it, and nothing returns from it: it resumes another thread.
               The interpreter has no exception model (the host runs the game's threads), so running on into the
               next function (the thread cleanup, which called osDestroyThread and came back for ever) kept a core
               busy at 100%. The routine is the game's own copy of osCreateThread/osStartThread/osSetThreadPri and the
               like that library detection did not name for this SDK build: say so, and park the thread. */
            static int said_eret;
            if (said_eret++ < 3)
                fprintf(stderr, "interpreter: an eret at %08X (from %08X): the game's own thread dispatch, its libultra routine "
                        "isn't recognised, so the host doesn't run its threads: the thread stops\n", pc, entry);
            port_idle_forever();
            continue; /* (only if it comes back: the same eret again) */
        }
        c.r[0] = 0;
        int kind = step(&c, pc, op, &target);
        c.r[0] = 0;
        if (kind == 3) { /* a likely branch not taken: its delay slot skipped */
            pc += 8;
            if (vpc) { vpc += 8; if ((vpc & 0xFFFu) < 8) pc = mapped(vpc); }
            continue;
        }
        if (!kind) {
            pc += 4;
            if (vpc) { vpc += 4; if (!(vpc & 0xFFFu)) pc = mapped(vpc); }
            goto counted;
        }
        /* the delay slot, then the jump */
        uint32_t slot = fetch(pc + 4), jop = op;
        {
            uint32_t second = 0;
            int skind = step(&c, pc + 4, slot, &second);
            c.r[0] = 0;
            if (skind == 1 || skind == 2 || skind == 4) {
                /* A taken branch or jump in the delay slot of a taken one (two `bnez` in a row, a loop's trick): the
                   console runs the first one's target once, as the second's delay slot, and then goes where the second
                   goes. Skipping it as "not an instruction" left the loop's cache operation unrun and the copy
                   routine started over for ever. */
                uint32_t once = fetch(target), after = 0;
                if (step(&c, target, once, &after)) unknown(target, once);
                c.r[0] = 0;
                target = second;
                kind = skind;
                jop = slot;
            } else if (skind == 3) {
                /* (a likely branch not taken in a slot: nothing to run) */
            }
        }
        /* A jump to itself with nothing in its delay slot (an idle thread's `for (;;);`, `j .`) can't get anywhere:
           on the console only an interrupt switches away from it. Spinning here took 41% of a racing game's
           interpreted instructions, and in the browser the page's time between retraces, so the host parks the
           thread instead; it comes back only if the game starts it again. */
        if ((kind == 1 || kind == 2) && target == pc && slot == 0) {
            port_idle_forever();
            continue; /* (only if it comes back: the same jump again) */
        }
        {
            /* a jump to a mapped virtual address runs the code at its spelling; the virtual address is kept for the
               instructions after it (a branch moves it by the same offset, a jump takes its target's) */
            int absolute = jop >> 26 == 2 || jop >> 26 == 3 || (jop >> 26 == 0 && ((jop & 63) == 8 || (jop & 63) == 9));
            uint32_t nv = absolute ? (target < 0x80000000u ? target : 0) : vpc ? vpc + (target - pc) : 0;
            /* j and jal keep the top four bits of the delay slot's address: the virtual one, not the frame's */
            if (vpc && (jop >> 26 == 2 || jop >> 26 == 3)) nv = ((vpc + 4) & 0xF0000000u) | ((jop & 0x03FFFFFFu) << 2);
            /* a call made from code reached through a window returns to the virtual address after it, as the
               console's $ra has it: the frame the code sits in may hold another page by the time it returns */
            if (vpc && (jop >> 26 == 3 || (jop >> 26 == 0 && (jop & 63) == 9 && ((jop >> 11) & 31) == 31)))
                c.r[31] = (uint64_t)(int64_t)(int32_t)(vpc + 8);
            vpc = nv;
            target = mapped(nv ? nv : target);
            if (nv && target == nv) vpc = 0; /* not mapped: the fetch faults, as before */
        }
        if (tracing >= 3) {
            unsigned k = jump_n++ % JUMP_HISTORY;
            jumps[k].from = pc; jumps[k].to = target; jumps[k].ra = (uint32_t)c.r[31]; jumps[k].sp = (uint32_t)c.r[29];
        }
        if (kind == 4 || jop >> 26 == 3) port_note_entry(target); /* a call: jal, jalr */
        pc = target;
        if (kind == 4) { /* a call through a pointer is a safe point, as host.c's port_func is for the game's C: only
                            those (a direct call never is), or a thread the C starts runs before its creator is done */
            port_safe_point();
            kind = 1;
        }
        if (kind == 1) {
            if (pc == RETURN) break;
            /* a call (or tail call) to a function with C: that C, then on at $ra, as its return would */
            /* (only a call's own target is told by structure: not where a routine returns to, by `jr $ra` or after the
               call, which is where a stub's `jalr` has its callee come back into the routine that follows it) */
            int first = !(jop >> 26 == 0 && (jop & 0xFC1FFFFFu) == 8u && ((jop >> 21) & 31) == 31);
            for (PortMipsCall fn; (fn = with_c(pc, first)) != 0; first = 0) {
                if (tracing > 1) fprintf(stderr, "interpreter: %08X calls C at %08X (%08X, %08X, %08X, %08X)\n", entry, pc,
                                         (uint32_t)c.r[4], (uint32_t)c.r[5], (uint32_t)c.r[6], (uint32_t)c.r[7]);
                call_c(&c, fn);
                pc = (uint32_t)c.r[31];
                vpc = 0; /* (the code it returns to: reached by its address as the caller kept it) */
                if (pc == RETURN) goto done;
            }
        }
    counted:
        if (!(++steps & 0xFFFF)) { /* a thread spinning here gives the others their turn */
            interp_stats(0x10000);
            port_interp_pc = pc; /* (PORT_SAMPLE prints it: where the interpreted game code is) */
            if (tracing > 1) fprintf(stderr, "interpreter: %08X still running, at %08X after %lu instructions\n", entry, pc, steps);
#ifndef PORT_WEB
            port_boot_spin();
#endif
            port_safe_point();
#ifdef PORT_WEB
            port_web_yield();
#endif
        }
    }
done:
    memcpy(regs, c.r, sizeof c.r);
    memcpy(fregs, c.f, sizeof c.f);
}

/* The registers a call into interpreted code starts with: $sp a frame of `words` argument words on this thread's
   MIPS stack (below its interpreted frames), $ra the sentinel, $a0-$a3 the register words the game's C leaves
   (adapt.py) for what its declared arguments don't cover. Returns 0 when the interpreter is turned off. */
int port_mips_enter(uint64_t *r, uint32_t *f, int words) {
    static int on = -1;
    if (on < 0) on = !getenv("PORT_INTERP") || atoi(getenv("PORT_INTERP")) != 0;
    if (!M) M = port_memory();
    memset(r, 0, 32 * sizeof *r);
    memset(f, 0, 32 * sizeof *f);
    r[29] = sx32((frame_base(thread_slot()) - 16u - 4u * (uint32_t)(words < 4 ? 4 : words)) & ~7u);
    r[31] = sx32(RETURN);
    for (int i = 0; i < 4; i++) r[4 + i] = sx32(lw(REGS + 4u * (uint32_t)i));
    return on;
}
/* After it: a void function's $v0 where the game's C reads it (adapt.py's register words). */
void port_mips_leave(const uint64_t *r) { sw(REGS + 16u, (uint32_t)r[2]); }

/* A call into interpreted code with its arguments' types: `types[i]` 0 a word, 1 64 bits, 2 a float, 3 a double, and
   `vals[i]` its bits; `ret` the result's type the same way (0 also for none). The arguments go where o32 passes
   them (as recomp.py's o32: the first two in $f12 and $f14 while they're floats, 64 bits in an aligned pair of
   words, high word first; a float after anything else in a word), the result comes back as its bits from $v0 (and
   $v1), or $f0 (and $f1). A float through a pointer once went in an integer word and the function read $f12. */
uint64_t port_mips_typed_call(uint32_t entry, const uint8_t *types, const uint64_t *vals, unsigned n, int ret) {
    int slot = 0, fpr = 12, floats = 1, where[16], kind[16]; /* kind: 0 a word, 1 a pair of words, 2 $f, 3 $f pair */
    if (n > 16) n = 16;
    for (unsigned i = 0; i < n; i++) {
        int t = types[i] & 3;
        if (floats && i < 2 && t >= 2) {
            if (t == 3) slot += slot % 2;
            kind[i] = t == 3 ? 3 : 2;
            where[i] = fpr;
            slot += t == 3 ? 2 : 1;
            fpr += 2;
            continue;
        }
        floats = 0;
        if (t == 1 || t == 3) { slot += slot % 2; kind[i] = 1; where[i] = slot; slot += 2; }
        else { kind[i] = 0; where[i] = slot; slot += 1; }
    }
    uint64_t r[32];
    uint32_t f[32];
    if (!port_mips_enter(r, f, slot > 4 ? slot : 4)) return 0;
    uint32_t sp = (uint32_t)r[29];
#define WORD(s, v) do { if ((s) < 4) r[4 + (s)] = sx32((uint32_t)(v)); else sw(sp + 4u * (uint32_t)(s), (uint32_t)(v)); } while (0)
    for (unsigned i = 0; i < n; i++) {
        uint64_t v = vals[i];
        switch (kind[i]) {
        case 0: WORD(where[i], v); break;
        case 1: WORD(where[i], v >> 32); WORD(where[i] + 1, v); break;
        case 2: f[where[i]] = (uint32_t)v; break;
        case 3: f[where[i]] = (uint32_t)v; f[where[i] + 1] = (uint32_t)(v >> 32); break;
        }
    }
#undef WORD
    port_mips_run(entry, r, f);
    port_mips_leave(r);
    switch (ret & 3) {
    case 1: return (uint64_t)(uint32_t)r[2] << 32 | (uint32_t)r[3];
    case 2: return f[0];
    case 3: return (uint64_t)f[1] << 32 | f[0];
    }
    return (uint32_t)r[2];
}
/* The same with integer arguments only: what a thread's entry or a pointer call of unknown types passes. */
uint64_t port_mips_pointer_call(uint32_t entry, const uint64_t *slots, unsigned count) {
    static const uint8_t words[16];
    return port_mips_typed_call(entry, words, slots, count, 0);
}

/* A structure the game's C passes by value to a function run here (as a pointer to it, WebAssembly's byval): its
   `words` copied into the argument words from `slot` on, where o32 passes it. */
void port_mips_copy_in(uint64_t *r, int slot, uint32_t from, int words) {
    if (!M) M = port_memory();
    for (int i = 0; i < words; i++) {
        uint32_t v = lw(from + 4u * (uint32_t)i);
        int s = slot + i;
        if (s < 4) r[4 + s] = sx32(v);
        else sw((uint32_t)r[29] + 4u * (uint32_t)s, v);
    }
}

/* Whether `address` has the game's code to interpret: in the console's RAM, an instruction there. The way the
   interpreter fetches it: a function in a TLB window (a game linked at 0x200000 and loaded at 0) is spelled
   0x80000000 | virtual, where RAM holds nothing, and its bytes are at the physical alias: asking RAM at the spelling
   alone said "no code" and skipped every such function the recompiler couldn't translate, so a game whose code all
   runs through a window never started its threads' work. */
int port_mips_has_code(uint32_t address) {
    if (!M) M = port_memory();
    address = mapped(address);
    if (address < 0x80000400u || address >= 0x80800000u || (address & 3)) return 0;
    return fetch(address) != 0 || fetch(address + 4) != 0;
}

#ifndef PORT_WEB
/* host.c's port_func, for a pointer to a function with no C. The game's C calls it as its own type says (wasm2c's
   CALL_INDIRECT casts to it: floats in float registers), so each type of call through a pointer the game makes has
   an entry of that type (interp.c's port_mips_pointer_types, by the type's text as bemem.py's CALL_INDIRECT names
   it), which hands its arguments to the interpreter with their types. The function it means is the one port_func
   was asked for, just before (port_mips_target). Without the type (a thread's entry): six integer arguments. */
typedef struct w2c_game w2c_game;
typedef struct { const char *type; void *entry; } PortMipsPointerType;
__attribute__((weak)) const PortMipsPointerType port_mips_pointer_types[1] = {{0, 0}};
__attribute__((weak)) const unsigned port_mips_pointer_type_count = 0;
static _Thread_local uint32_t pointer_target;
uint32_t port_mips_target(void) { return pointer_target; }
static uint64_t pointer_entry(w2c_game *g, uint64_t a0, uint64_t a1, uint64_t a2, uint64_t a3, uint64_t a4, uint64_t a5) {
    (void)g;
    const uint64_t slots[6] = {a0, a1, a2, a3, a4, a5};
    return port_mips_pointer_call(pointer_target, slots, 6);
}
static int same_type(const char *a, const char *b) { /* the types' texts, their spaces aside */
    for (;;) {
        while (*a == ' ') a++;
        while (*b == ' ') b++;
        if (*a != *b) return 0;
        if (!*a) return 1;
        a++, b++;
    }
}
static void *typed_entry(const char *type) {
    static struct { const char *type; void *entry; } seen[256]; /* by the call's own text (one per call site) */
    unsigned slot = (unsigned)(((uintptr_t)type >> 3) & 255);
    if (seen[slot].type == type) return seen[slot].entry;
    void *entry = 0;
    for (unsigned i = 0; i < port_mips_pointer_type_count && !entry; i++)
        if (port_mips_pointer_types[i].type && same_type(port_mips_pointer_types[i].type, type)) entry = port_mips_pointer_types[i].entry;
    if (!entry) {
        static int said;
        if (!said++) fprintf(stderr, "interpreter: no entry for a call through a pointer of type %s: its arguments as words\n", type);
        entry = (void *)pointer_entry;
    }
    seen[slot].type = type;
    seen[slot].entry = entry;
    return entry;
}
void *port_mips_pointer_as(uint32_t address, const char *type) {
    if (!port_mips_has_code(address)) return 0;
    pointer_target = address;
    return type ? typed_entry(type) : (void *)pointer_entry;
}
void *port_mips_pointer(uint32_t address) { return port_mips_pointer_as(address, 0); }
#else
/* web/be.c's port_icall, for a pointer to a function with no C: the call's slots ($a0-$a3, then the stack's) and
   its result in slots[16], as a universal entry leaves it. `count` carries the types above its low byte, as
   web/webir.py's calls put them: two bits an argument from bit 8 (0 a word, 1 64 bits, 2 a float, 3 a double; the
   first eight), and the result's at bit 24. 0: no code there either. */
__attribute__((export_name("port_mips_icall"))) uint32_t port_mips_icall(uint32_t pointer, uint32_t slots, uint32_t count) {
    if (!port_mips_has_code(pointer)) return 0;
    uint64_t *s = (uint64_t *)(uintptr_t)slots;
    unsigned n = count & 0xFF;
    uint8_t types[16] = {0};
    for (unsigned i = 0; i < n && i < 8; i++) types[i] = (uint8_t)((count >> (8 + 2 * i)) & 3);
    s[16] = port_mips_typed_call(pointer, types, s, n > 16 ? 16 : n, (int)((count >> 24) & 3));
    return 1;
}
#endif
