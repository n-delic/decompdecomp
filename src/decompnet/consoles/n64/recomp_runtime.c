/* decompdecomp's runtime for running one recompiled function (consoles/n64/recomp.py, `harness`): what N64Recomp's
   output needs from a runtime (recomp.h), and a main that runs one call from a recorded state.

   usage: harness <vram> <state> <rdram in> <rdram out> <result out>
   state: one line as the reversing emulator records it (emulator/frontend.c, EMU_RECORD): pc, the 32 GPRs, hi, lo
   (64-bit hex), the 32 FPRs' single-precision words. rdram: big-endian, as the console holds it, both ways.
   result: "v0 v1 f0 f1" in hex, and a line "runtime <name>" for each runtime function the call reached (decompdecomp
   provides them as stubs that do nothing: what differs after one is not the function's own doing).

   The generated harness_table.c gives harness_functions[] (vram, function) sorted, and the runtime stubs. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "recomp.h"

typedef struct { uint32_t vram; recomp_func_t *func; } HarnessFunction;
extern const HarnessFunction harness_functions[];
extern const unsigned harness_function_count;
int32_t *section_addresses;
static int32_t no_sections[1];
static FILE *result;

void harness_runtime(const char *name) {
    if (result) fprintf(result, "runtime %s\n", name);
}

recomp_func_t *get_function(int32_t vram) {
    unsigned lo = 0, hi = harness_function_count;
    while (lo < hi) {
        unsigned mid = (lo + hi) / 2;
        if (harness_functions[mid].vram < (uint32_t)vram) lo = mid + 1; else hi = mid;
    }
    if (lo < harness_function_count && harness_functions[lo].vram == (uint32_t)vram) return harness_functions[lo].func;
    fprintf(stderr, "a call to %08X, where no recompiled function is\n", (uint32_t)vram);
    exit(4);
}

void cop0_status_write(recomp_context *ctx, gpr value) { ctx->status_reg = (uint32_t)value; }
gpr cop0_status_read(recomp_context *ctx) { return (gpr)(int32_t)ctx->status_reg; }
void switch_error(const char *func, uint32_t vram, uint32_t jtbl) {
    fprintf(stderr, "switch out of its table in %s at %08X (table %08X)\n", func, vram, jtbl);
    exit(5);
}
void do_break(uint32_t vram) { fprintf(stderr, "break at %08X\n", vram); exit(6); }
void recomp_syscall_handler(uint8_t *rdram, recomp_context *ctx, int32_t vram) {
    (void)rdram; (void)ctx;
    fprintf(stderr, "syscall at %08X\n", (uint32_t)vram);
    exit(7);
}
void pause_self(uint8_t *rdram) { (void)rdram; fprintf(stderr, "the call paused its thread\n"); exit(8); }

static uint8_t *read_rdram(const char *path, size_t *size) {
    FILE *f = fopen(path, "rb");
    if (!f) { perror(path); exit(2); }
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fseek(f, 0, SEEK_SET);
    size_t room = 8u << 20; /* the Expansion Pak's 8 MB, whatever was recorded */
    uint8_t *bytes = calloc(1, room > (size_t)n ? room : (size_t)n);
    if (!bytes || fread(bytes, 1, (size_t)n, f) != (size_t)n) { fprintf(stderr, "can't read %s\n", path); exit(2); }
    fclose(f);
    for (long i = 0; i + 3 < n; i += 4) { /* N64Recomp keeps RDRAM as host-order words */
        uint32_t w = (uint32_t)bytes[i] << 24 | (uint32_t)bytes[i + 1] << 16 | (uint32_t)bytes[i + 2] << 8 | bytes[i + 3];
        memcpy(bytes + i, &w, 4);
    }
    *size = (size_t)n;
    return bytes;
}

int main(int argc, char **argv) {
    if (argc != 6) { fprintf(stderr, "usage: harness vram state rdram-in rdram-out result-out\n"); return 2; }
    uint32_t vram = (uint32_t)strtoul(argv[1], NULL, 16);
    recomp_context ctx;
    memset(&ctx, 0, sizeof ctx);
    FILE *s = fopen(argv[2], "r");
    if (!s) { perror(argv[2]); return 2; }
    unsigned pc;
    unsigned long long g[34];
    unsigned f[32];
    if (fscanf(s, "%x", &pc) != 1) return 2;
    for (int i = 0; i < 34; i++) if (fscanf(s, "%llx", &g[i]) != 1) return 2;
    for (int i = 0; i < 32; i++) if (fscanf(s, "%x", &f[i]) != 1) return 2;
    fclose(s);
    gpr *r = &ctx.r0;
    for (int i = 0; i < 32; i++) r[i] = g[i];
    ctx.hi = g[32];
    ctx.lo = g[33];
    fpr *fr = &ctx.f0;
    for (int i = 0; i < 32; i += 2) { fr[i].u32l = f[i]; fr[i].u32h = f[i + 1]; } /* FR=0: pairs */
    ctx.f_odd = &ctx.f0.u32h;
    ctx.mips3_float_mode = 0;
    section_addresses = no_sections;
    size_t size;
    uint8_t *bytes = read_rdram(argv[3], &size);
    uint8_t *rdram = bytes;
    result = fopen(argv[5], "w");
    if (!result) { perror(argv[5]); return 2; }
    get_function((int32_t)vram)(rdram, &ctx);
    fprintf(result, "%08X %08X %08X %08X\n", (uint32_t)ctx.r2, (uint32_t)ctx.r3, ctx.f0.u32l, ctx.f0.u32h);
    fclose(result);
    FILE *o = fopen(argv[4], "wb");
    if (!o) { perror(argv[4]); return 2; }
    for (size_t i = 0; i + 3 < size; i += 4) {
        uint32_t w;
        memcpy(&w, bytes + i, 4);
        uint8_t b[4] = {w >> 24, w >> 16, w >> 8, w};
        fwrite(b, 1, 4, o);
    }
    fclose(o);
    return 0;
}
