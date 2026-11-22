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

uint8_t *port_memory(void);
uint64_t port_io_load(uint64_t addr);
uint64_t port_io_store(uint64_t addr, uint64_t value);
uint32_t port_stack_bus(uint32_t addr);
void port_safe_point(void);
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
#define MIPS_STACKS 0x14800000u /* each host thread's: 256 KB, above recompiled C's (recomp.py's STACKS) */
#define MIPS_STACK 0x40000u
#define MIPS_THREADS 32
#define RETURN 0xFFFFFFF0u /* the sentinel return address: back to the C that started the run */

/* interp.c's: every function with C, by console address, and how to call it with o32's registers */
typedef void (*PortMipsCall)(uint64_t *r, uint32_t *f);
typedef struct { uint32_t vram; PortMipsCall call; } PortMipsEntry;
__attribute__((weak)) const PortMipsEntry port_mips_calls[1] = {{0, 0}};
__attribute__((weak)) const unsigned port_mips_call_count = 0;

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
HOT uint8_t *at_load(uint32_t a) { return M + (a >= 0xA0000000u ? (uint32_t)port_io_load(a) : a); }
HOT uint8_t *at_store(uint32_t a, uint64_t v) { return M + (a >= 0xA0000000u ? (uint32_t)port_io_store(a, v) : a); }
HOT uint32_t lw(uint32_t a) { uint32_t v; memcpy(&v, at_load(a), 4); return __builtin_bswap32(v); }
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
static PortMipsCall with_c(uint32_t vram) {
    uint32_t slot = (vram >> 2) & (CACHE - 1);
    if (found[slot].vram == vram) return found[slot].call;
    unsigned lo = 0, hi = port_mips_call_count;
    while (lo < hi) { unsigned mid = (lo + hi) / 2; if (port_mips_calls[mid].vram < vram) lo = mid + 1; else hi = mid; }
    PortMipsCall c = lo < port_mips_call_count && port_mips_calls[lo].vram == vram ? port_mips_calls[lo].call : 0;
    found[slot].vram = vram;
    found[slot].call = c;
    return c;
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

typedef struct { uint64_t r[32], hi, lo; uint32_t f[32], fcr31, status, entry_hi, tlb_index, entry_lo[2], page_mask; } Cpu;
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

static void unknown(uint32_t pc, uint32_t op) {
    static int said;
    if (said++ < 20) fprintf(stderr, "interpreter: instruction %08X at %08X isn't one it runs: skipped\n", op, pc);
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
    case 2: *target = ((pc + 4) & 0xF0000000u) | (op & 0x03FFFFFFu) << 2; return 1;
    case 3: *target = ((pc + 4) & 0xF0000000u) | (op & 0x03FFFFFFu) << 2; r[31] = sx32(pc + 8); return 1;
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
        uint32_t *reg = rd == 12 ? &c->status : rd == 10 ? &c->entry_hi : rd == 0 ? &c->tlb_index :
                        rd == 5 ? &c->page_mask : rd == 2 || rd == 3 ? &c->entry_lo[rd - 2] : NULL;
        if (rs == 0 || rs == 1)
            r[rt] = rd == 9 ? sx32(w2c_env_0x5F_builtin_mips_read_count(0)) : reg ? sx32(*reg) : 0;
        else if ((rs == 4 || rs == 5) && reg) *reg = (uint32_t)r[rt];
        /* The raw PI routine probes the TLB if its buffer lives on a compatibility stack.
           Expose a pair of 4 KB pages on the compatibility host’s PI bus. */
        else if (op == 0x42000008u) c->tlb_index = port_stack_bus(c->entry_hi & ~0x1FFFu) ? 0 : 0x80000000u;
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

/* Run the function at `entry` with the registers `r` and float registers `f` as o32 left them ($sp, $ra: the
   caller's sentinel, set by port_mips_enter), until it returns. */
void port_mips_run(uint32_t entry, uint64_t *regs, uint32_t *fregs) {
    if (!M) M = port_memory();
    if (tracing < 0) tracing = getenv("PORT_INTERP_TRACE") ? atoi(getenv("PORT_INTERP_TRACE")) : 0;
    port_note_entry(entry);
    if (tracing) fprintf(stderr, "interpreter: %08X (%08X, %08X, %08X, %08X)\n", entry, (uint32_t)regs[4],
                         (uint32_t)regs[5], (uint32_t)regs[6], (uint32_t)regs[7]);
    Cpu c = {0};
    memcpy(c.r, regs, sizeof c.r);
    memcpy(c.f, fregs, sizeof c.f);
    c.status = 0x2000FF01u;
    uint32_t pc = entry;
    unsigned long steps = 0;
    int zeros = 0;
    for (;;) {
        uint32_t op = lw(pc), target = 0;
        if (op == 0 && ++zeros > 64) { /* a run of nothing: no code there (no ROM loaded where it was looked for) */
            fprintf(stderr, "interpreter: no instructions at %08X (from %08X): the function returns at once\n", pc, entry);
            break;
        }
        if (op) zeros = 0;
        c.r[0] = 0;
        int kind = step(&c, pc, op, &target);
        c.r[0] = 0;
        if (kind == 3) { pc += 8; continue; }    /* a likely branch not taken: its delay slot skipped */
        if (!kind) { pc += 4; goto counted; }
        /* the delay slot, then the jump */
        uint32_t slot = lw(pc + 4);
        { uint32_t ignored; if (step(&c, pc + 4, slot, &ignored)) unknown(pc + 4, slot); c.r[0] = 0; }
        /* A jump to itself with nothing in its delay slot (an idle thread's `for (;;);`, `j .`) can't get anywhere:
           on the console only an interrupt switches away from it. Spinning here took 41% of a racing game's
           interpreted instructions, and in the browser the page's time between retraces, so the host parks the
           thread instead; it comes back only if the game starts it again. */
        if ((kind == 1 || kind == 2) && target == pc && slot == 0) {
            port_idle_forever();
            continue; /* (only if it comes back: the same jump again) */
        }
        if (kind == 4 || op >> 26 == 3) port_note_entry(target); /* a call: jal, jalr */
        pc = target;
        if (kind == 4) { /* a call through a pointer is a safe point, as host.c's port_func is for the game's C: only
                            those (a direct call never is), or a thread the C starts runs before its creator is done */
            port_safe_point();
            kind = 1;
        }
        if (kind == 1) {
            if (pc == RETURN) break;
            /* a call (or tail call) to a function with C: that C, then on at $ra, as its return would */
            for (PortMipsCall fn; (fn = with_c(pc)) != 0;) {
                if (tracing > 1) fprintf(stderr, "interpreter: %08X calls C at %08X (%08X, %08X, %08X, %08X)\n", entry, pc,
                                         (uint32_t)c.r[4], (uint32_t)c.r[5], (uint32_t)c.r[6], (uint32_t)c.r[7]);
                call_c(&c, fn);
                pc = (uint32_t)c.r[31];
                if (pc == RETURN) goto done;
            }
        }
    counted:
        if (!(++steps & 0xFFFF)) { /* a thread spinning here gives the others their turn */
            if (tracing > 1) fprintf(stderr, "interpreter: %08X still running, at %08X after %lu instructions\n", entry, pc, steps);
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

/* Whether `address` has the game's code to interpret: in the console's RAM, an instruction there. */
int port_mips_has_code(uint32_t address) {
    if (!M) M = port_memory();
    if (address < 0x80000400u || address >= 0x80800000u || (address & 3)) return 0;
    return lw(address) != 0 || lw(address + 4) != 0;
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
