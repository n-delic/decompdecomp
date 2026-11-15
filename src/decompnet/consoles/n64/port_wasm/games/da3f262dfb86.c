/*
 * V-Rally's own host functions (ROM SHA-1 da3f262dfb86...): the N64 host (host.c) was written on it, and these name
 * its own addresses and names, so they would be wrong for any other game (another game's 0x80091920 isn't bzero).
 * port/wasm/build.py compiles a file here only for the game whose ROM's SHA-1 starts with its name.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "game.h"

struct w2c_env;
uint8_t *port_memory(void);
w2c_game *port_game(void);
#define M port_memory()

/* its DMA request queue: a request to outside RAM is reported with who asked (host.c, osSendMesg) */
uint32_t port_dma_watch_queue = 0x800C7938u;

void w2c_env_func_8008C4F0(struct w2c_env *e) { (void)e; } /* osMapTLBRdb */
void w2c_env_func_80091920(struct w2c_env *e, u32 p, u32 n) { (void)e; memset(M + p, 0, n); } /* bzero */
u32 w2c_env_str_len(struct w2c_env *e, u32 s) { (void)e; return (u32)strlen((char *)M + s); }
void port_vi_swap(uint32_t fb);
/* its own name for osViSwapBuffer */
void w2c_env_viSetSwapBuffer(struct w2c_env *e, u32 fb) { (void)e; port_vi_swap(fb); }
/* A racing game's 64-bit helpers (its own hand-written MIPS, read from its ROM): 0x80099F78 unsigned 64 / 64,
   0x8009A074 unsigned 64 / 32, 0x8009A2D0 the unsigned remainder, 0x8009A310 a double to a signed 64-bit integer
   (__fixdfdi: toward zero, INT64_MIN when out of range) and 0x8009A430 a signed 64-bit integer to a double
   (__floatdidf). The last two were once unsigned here: a negative double became 0 (or trapped, compiled to
   WebAssembly) and a negative integer 1.8e19. */
u64 w2c_env_func_80099F78(struct w2c_env *e, u64 a, u64 b) { (void)e; return b ? a / b : 0; }
u64 w2c_env_func_8009A074(struct w2c_env *e, u64 a, u64 b) { (void)e; return b ? a / b : 0; }
u64 w2c_env_func_8009A2D0(struct w2c_env *e, u64 a, u64 b) { (void)e; return b ? a % b : 0; }
u64 w2c_env_func_8009A310(struct w2c_env *e, f64 x) {
    (void)e;
    if (!(x > -9223372036854775808.0 && x < 9223372036854775808.0)) return 0x8000000000000000ull;
    return (u64)(int64_t)x;
}
f64 w2c_env_func_8009A430(struct w2c_env *e, u64 x) { (void)e; return (f64)(int64_t)x; }
/* the game's old name for format_to_buffer: the same variadic call (its third argument points at the rest) */
void *port_func(uint32_t address);
/* by its address (0x80099DF8), so the game's C for it or, with none, its recompiled C (port/wasm/recomp.py) */
u32 w2c_env_formatString(struct w2c_env *e, u32 a, u32 b, u32 c) {
    (void)e;
    ((void (*)(w2c_game *, u32, u32, u32))port_func(0x80099DF8u))(port_game(), a, b, c);
    return 0;
}
void w2c_env_D_8000A840(struct w2c_env *e, u32 a, u32 b, u32 c, u32 d, u32 f, u32 g) {
    (void)e; (void)a; (void)b; (void)c; (void)d; (void)f; (void)g;
    static int once;
    if (!once++) fprintf(stderr, "D_8000A840 (a function the split missed) called: skipped\n");
}
