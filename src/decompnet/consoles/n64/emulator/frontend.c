/* decompdecomp's headless frontend for the reversing emulator (RetroReversing's mupen64plus core, built by
   consoles/n64/emulator/__init__.py): runs a ROM with the HLE RSP, the scripted input plugin and the headless video
   plugin, as fast as the host allows, and reports what the caller asked for on stderr, one line each:

     dumped <vi> <path>                      RDRAM written at that VI, big-endian as the console holds it
     watch <vi> <pc> <ra> <a0> <a1> <a2> <a3> <v0>        a write breakpoint hit (before the store)
     break <vi> <pc> <ra> <a0> <a1> <a2> <a3> <v0> <w0> <w1> <w2> <w3>   an execution breakpoint hit, with the four
                                             words at $a0
     recorded <vi> <n> <path>                a call recorded (EMU_RECORD): <path>.state and <path>.rdram, its
                                             path dir/<address>_<n>
     stopped <vi>                            the run ended

   usage: frontend core rsp input video rom
   EMU_VIS=n           stop after n vertical interrupts
   EMU_DUMP=vi:path,…  RDRAM dumps, each at exactly that VI (from the core's VI hook, in the emulation thread)
   EMU_WATCH=addr:len,…  write breakpoints (physical addresses, hex)
   EMU_BREAK=addr,…    execution breakpoints (virtual addresses, hex)
   EMU_FROM=vi         report breakpoint hits only from this VI on
   EMU_MAX=n           report at most n hits (200)
   EMU_8MB=1           the Expansion Pak's 8 MB (default 4 MB, what the WebAssembly port's host gives a game)
   EMU_CONFIG=folder   the core's configuration folder (a scratch one: nothing is saved between runs)
   EMU_INPUT           the input plugin's script (input.c); EMU_INPUT_CLOCK=frames times it in the game's frames
   EMU_FRAME_DUMP=n:path,…  RDRAM dumps, each as the game hands over its nth graphics task (the video plugin, video.c;
                       reported as "framedump <n> <path>")
   EMU_RECORD=dir:n    at the first n hits of each execution breakpoint (from EMU_FROM), the machine's state:
                       dir/<address>_<i>.state (one line: pc, the 32 GPRs, hi and lo as 64-bit hex, then the 32 FPRs'
                       single-precision words) and dir/<address>_<i>.rdram (RDRAM, big-endian). A breakpoint goes once
                       its n are recorded; the run stops when none is left
   EMU_RECORD_PENDING=n  wait before recording while n recordings (.rdram files) are in the folder: the caller
                       compares and deletes each as it comes, so a run recording thousands of calls keeps a few MB */
#include <dirent.h>
#include <dlfcn.h>
#include <unistd.h>
#include <stdint.h>
#include "live.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "m64p_types.h"
#include "m64p_frontend.h"
#include "m64p_config.h"
#include "m64p_debugger.h"
#include "m64p_common.h"

static ptr_CoreDoCommand do_command;
static ptr_DebugMemGetPointer mem_pointer;
static ptr_DebugGetCPUDataPtr cpu_data;
static ptr_DebugSetRunState set_run_state;
static ptr_DebugStep step;
static ptr_DebugBreakpointCommand bkp_command;
static unsigned int (*vi_count)(void);
static unsigned int stop_at, report_from;
static unsigned long hits, max_hits = 200;
static int expansion;
static unsigned int breaks[4096], nbreaks;
static unsigned long recorded_at[4096], recording_left;

static void debug_cb(void *context, int level, const char *message) {
    (void)context;
    if (level <= M64MSG_ERROR) fprintf(stderr, "core: %s\n", message);
}

static uint32_t rdram_word(uint32_t addr) {
    uint32_t *rdram = mem_pointer(M64P_DBG_PTR_RDRAM);
    uint32_t size = expansion ? 8u << 20 : 4u << 20;
    addr &= 0x1FFFFFFC;
    return rdram && addr < size ? rdram[addr >> 2] : 0;
}

static void dump(const char *path, unsigned int vi) {
    uint32_t *rdram = mem_pointer(M64P_DBG_PTR_RDRAM);
    FILE *f = fopen(path, "wb");
    if (!rdram || !f) { fprintf(stderr, "error can't write RDRAM to %s\n", path); if (f) fclose(f); return; }
    size_t words = (expansion ? 8u << 20 : 4u << 20) / 4;
    for (size_t i = 0; i < words; i++) {
        uint32_t w = rdram[i]; /* the core keeps RDRAM as host-order words */
        unsigned char b[4] = {w >> 24, w >> 16, w >> 8, w};
        fwrite(b, 1, 4, f);
    }
    fclose(f);
    fprintf(stderr, "dumped %u %s\n", vi, path);
}

/* The core calls this at every vertical interrupt (its DecompSetViHook): dumps land on the VI asked for, and the run
   stops on the one it should, whatever the host's speed. */
static volatile uint32_t *live;

/* EMU_LIVE with a gate (lockstep): the RAM as of this retrace goes in the file's second block (live.h), then the
   emulation waits here until the viewer allows the retrace, so the port and the console go on one retrace at a time. */
static void live_gate(unsigned int vi) {
    if (!live) return;
    uint32_t *rdram = mem_pointer(M64P_DBG_PTR_RDRAM);
    if (live[LIVE_GATE / 4] != 0xFFFFFFFFu && rdram) {
        size_t words = (expansion ? 8u << 20 : 4u << 20) / 4;
        volatile uint32_t *out = live + LIVE_VIRAM / 4;
        uint32_t seq = live[LIVE_VISEQ / 4];
        __atomic_store_n(&live[LIVE_VISEQ / 4], seq | 1, __ATOMIC_RELEASE);
        live[LIVE_VIVI / 4] = vi;
        for (size_t i = 0; i < words; i++) out[i] = __builtin_bswap32(rdram[i]);
        __atomic_store_n(&live[LIVE_VISEQ / 4], (seq | 1) + 1, __ATOMIC_RELEASE);
    }
    while (vi > __atomic_load_n(&live[LIVE_GATE / 4], __ATOMIC_ACQUIRE)) usleep(200);
}

static void on_vi(unsigned int vi) {
    live_gate(vi);
    const char *p = getenv("EMU_DUMP");
    while (p && *p) {
        char *end;
        unsigned long at = strtoul(p, &end, 10);
        if (*end != ':') break;
        p = end + 1;
        size_t n = strcspn(p, ",");
        if (at == vi) {
            char path[4096];
            snprintf(path, sizeof path, "%.*s", (int)n, p);
            dump(path, vi);
        }
        p += n;
        if (*p == ',') p++;
    }
    if (stop_at && vi >= stop_at) do_command(M64CMD_STOP, 0, NULL);
}

static void add_breakpoints(void) {
    const char *p = getenv("EMU_WATCH");
    while (p && *p) {
        char *end;
        unsigned long addr = strtoul(p, &end, 16), len = 4;
        if (end == p) break;
        if (*end == ':') len = strtoul(end + 1, &end, 16);
        m64p_breakpoint b = {(unsigned int)addr, (unsigned int)(addr + (len ? len : 1) - 1),
                             M64P_BKP_FLAG_ENABLED | M64P_BKP_FLAG_WRITE};
        bkp_command(M64P_BKP_CMD_ADD_STRUCT, 0, &b);
        p = *end == ',' ? end + 1 : end;
    }
    p = getenv("EMU_BREAK");
    while (p && *p && nbreaks < sizeof breaks / sizeof *breaks) {
        char *end;
        unsigned long addr = strtoul(p, &end, 16);
        if (end == p) break;
        breaks[nbreaks++] = (unsigned int)addr;
        recording_left++;
        m64p_breakpoint b = {(unsigned int)addr, (unsigned int)addr, M64P_BKP_FLAG_ENABLED | M64P_BKP_FLAG_EXEC};
        bkp_command(M64P_BKP_CMD_ADD_STRUCT, 0, &b);
        p = *end == ',' ? end + 1 : end;
    }
}

static void go_on(void) { set_run_state(M64P_DBG_RUNSTATE_RUNNING); step(); }
/* breakpoints go in once the core has set up its memory map (adding a memory breakpoint changes it) */
static void dbg_init(void) { add_breakpoints(); go_on(); }
static void dbg_vi(void) {}
/* EMU_RECORD: the state a call starts from, for running the same call elsewhere (app/behaviour.py) */
static void record(unsigned int which, unsigned int pc, unsigned int vi, int64_t *r) {
    char dir[3072];
    const char *spec = getenv("EMU_RECORD"), *colon = strrchr(spec, ':');
    unsigned long most = colon ? strtoul(colon + 1, NULL, 10) : 1;
    if (recorded_at[which] >= most) return;
    unsigned long n = recorded_at[which];
    snprintf(dir, sizeof dir, "%.*s", colon ? (int)(colon - spec) : (int)strlen(spec), spec);
    long pending = getenv("EMU_RECORD_PENDING") ? atol(getenv("EMU_RECORD_PENDING")) : 0;
    while (pending > 0) { /* the emulation waits for the caller to take what it recorded */
        long waiting = 0;
        DIR *d = opendir(dir);
        struct dirent *e;
        while (d && (e = readdir(d)))
            if (strstr(e->d_name, ".rdram")) waiting++;
        if (d) closedir(d);
        if (waiting < pending) break;
        usleep(20000);
    }
    char path[4096];
    snprintf(path, sizeof path, "%s/%08X_%lu.state", dir, pc, n);
    FILE *f = fopen(path, "w");
    if (!f) { fprintf(stderr, "error can't write %s\n", path); return; }
    int64_t *hi = cpu_data(M64P_CPU_REG_HI), *lo = cpu_data(M64P_CPU_REG_LO);
    float **fpr = cpu_data(M64P_CPU_REG_COP1_SIMPLE_PTR);
    fprintf(f, "%08X", pc);
    for (int i = 0; i < 32; i++) fprintf(f, " %016llX", (unsigned long long)r[i]);
    fprintf(f, " %016llX %016llX", (unsigned long long)(hi ? *hi : 0), (unsigned long long)(lo ? *lo : 0));
    for (int i = 0; i < 32; i++) {
        uint32_t bits = 0;
        if (fpr && fpr[i]) memcpy(&bits, fpr[i], 4);
        fprintf(f, " %08X", bits);
    }
    fputc('\n', f);
    fclose(f);
    snprintf(path, sizeof path, "%s/%08X_%lu.rdram", dir, pc, n);
    dump(path, vi);
    snprintf(path, sizeof path, "%s/%08X_%lu", dir, pc, n);
    fprintf(stderr, "recorded %u %lu %s\n", vi, n, path);
    if (++recorded_at[which] >= most) {
        bkp_command(M64P_BKP_CMD_REMOVE_ADDR, pc, NULL);
        if (--recording_left == 0) do_command(M64CMD_STOP, 0, NULL);
    }
}

static void dbg_update(unsigned int pc) {
    int64_t *r = cpu_data(M64P_CPU_REG_REG);
    unsigned int vi = vi_count();
    if (r && vi >= report_from && getenv("EMU_RECORD"))
        for (unsigned int i = 0; i < nbreaks; i++)
            if (breaks[i] == pc) record(i, pc, vi, r);
    if (r && vi >= report_from && hits < max_hits) {
        int exec = 0;
        for (unsigned int i = 0; i < nbreaks; i++) exec |= breaks[i] == pc;
        hits++;
        fprintf(stderr, "%s %u %08X %08X %08X %08X %08X %08X %08X", exec ? "break" : "watch", vi, pc,
                (uint32_t)r[31], (uint32_t)r[4], (uint32_t)r[5], (uint32_t)r[6], (uint32_t)r[7], (uint32_t)r[2]);
        if (exec)
            for (int i = 0; i < 4; i++) fprintf(stderr, " %08X", rdram_word((uint32_t)r[4] + 4 * i));
        fputc('\n', stderr);
    }
    go_on();
}

static void plugin(const char *path, m64p_dynlib_handle core, m64p_plugin_type type) {
    void *h = dlopen(path, RTLD_NOW);
    if (!h) { fprintf(stderr, "error %s\n", dlerror()); exit(1); }
    ptr_PluginStartup start = (ptr_PluginStartup)dlsym(h, "PluginStartup");
    if (!start || start(core, NULL, debug_cb) != M64ERR_SUCCESS) { fprintf(stderr, "error %s didn't start\n", path); exit(1); }
    ptr_CoreAttachPlugin attach = (ptr_CoreAttachPlugin)dlsym(core, "CoreAttachPlugin");
    if (attach(type, h) != M64ERR_SUCCESS) { fprintf(stderr, "error the core refused %s\n", path); exit(1); }
}

int main(int argc, char **argv) {
    if (argc != 6) { fprintf(stderr, "usage: frontend core rsp input video rom\n"); return 2; }
    void *core = dlopen(argv[1], RTLD_NOW | RTLD_GLOBAL);
    if (!core) { fprintf(stderr, "error %s\n", dlerror()); return 1; }
    ptr_CoreStartup startup = (ptr_CoreStartup)dlsym(core, "CoreStartup");
    do_command = (ptr_CoreDoCommand)dlsym(core, "CoreDoCommand");
    mem_pointer = (ptr_DebugMemGetPointer)dlsym(core, "DebugMemGetPointer");
    cpu_data = (ptr_DebugGetCPUDataPtr)dlsym(core, "DebugGetCPUDataPtr");
    vi_count = (unsigned int (*)(void))dlsym(core, "DecompGetViCount");
    void (*set_vi_hook)(void (*)(unsigned int)) = (void (*)(void (*)(unsigned int)))dlsym(core, "DecompSetViHook");
    if (!startup || !do_command || !mem_pointer || !cpu_data || !vi_count || !set_vi_hook) {
        fprintf(stderr, "error the core lacks decompdecomp's functions (DecompGetViCount, DecompSetViHook)\n");
        return 1;
    }
    stop_at = getenv("EMU_VIS") ? (unsigned)atoi(getenv("EMU_VIS")) : 0;
    report_from = getenv("EMU_FROM") ? (unsigned)atoi(getenv("EMU_FROM")) : 0;
    if (getenv("EMU_MAX")) max_hits = strtoul(getenv("EMU_MAX"), NULL, 10);
    expansion = getenv("EMU_8MB") != NULL;
    const char *config = getenv("EMU_CONFIG");
    if (startup(0x020001, config, config, NULL, debug_cb, NULL, NULL) != M64ERR_SUCCESS) {
        fprintf(stderr, "error the core didn't start\n");
        return 1;
    }
    int debugger = getenv("EMU_WATCH") != NULL || getenv("EMU_BREAK") != NULL;
    ptr_ConfigOpenSection open_section = (ptr_ConfigOpenSection)dlsym(core, "ConfigOpenSection");
    ptr_ConfigSetParameter set_param = (ptr_ConfigSetParameter)dlsym(core, "ConfigSetParameter");
    m64p_handle section;
    if (open_section && set_param && open_section("Core", &section) == M64ERR_SUCCESS) {
        int emulator = debugger ? 0 : 1; /* breakpoints need the pure interpreter; otherwise the cached one */
        set_param(section, "R4300Emulator", M64TYPE_INT, &emulator);
        set_param(section, "EnableDebugger", M64TYPE_BOOL, &debugger);
        int no_extra = !expansion;
        set_param(section, "DisableExtraMem", M64TYPE_BOOL, &no_extra);
        int off = 0;
        set_param(section, "OnScreenDisplay", M64TYPE_BOOL, &off);
        /* the same run every time: the core randomises interrupt timing by default */
        set_param(section, "RandomizeInterrupt", M64TYPE_BOOL, &off);
    }
    FILE *f = fopen(argv[5], "rb");
    if (!f) { fprintf(stderr, "error can't open %s\n", argv[5]); return 1; }
    fseek(f, 0, SEEK_END);
    long size = ftell(f);
    fseek(f, 0, SEEK_SET);
    unsigned char *rom = malloc((size_t)size);
    if (!rom || fread(rom, 1, (size_t)size, f) != (size_t)size) { fprintf(stderr, "error can't read the ROM\n"); return 1; }
    fclose(f);
    if (do_command(M64CMD_ROM_OPEN, (int)size, rom) != M64ERR_SUCCESS) { fprintf(stderr, "error the core refused the ROM\n"); return 1; }
    setenv("EMU_VIDEO_PLUGIN", argv[4], 1); /* where the input plugin finds the frame count (EMU_INPUT_CLOCK) */
    plugin(argv[4], core, M64PLUGIN_GFX); /* the core wants video, audio, input, then the RSP */
    plugin(argv[3], core, M64PLUGIN_INPUT);
    plugin(argv[2], core, M64PLUGIN_RSP);
    if (debugger) {
        set_run_state = (ptr_DebugSetRunState)dlsym(core, "DebugSetRunState");
        step = (ptr_DebugStep)dlsym(core, "DebugStep");
        bkp_command = (ptr_DebugBreakpointCommand)dlsym(core, "DebugBreakpointCommand");
        ptr_DebugSetCallbacks set_callbacks = (ptr_DebugSetCallbacks)dlsym(core, "DebugSetCallbacks");
        set_callbacks(dbg_init, dbg_update, dbg_vi);
    }
    live = live_map();
    set_vi_hook(on_vi);
    int limiter = getenv("EMU_LIVE") != NULL; /* live (a person plays it beside the port): real time; otherwise as fast as the host allows */
    do_command(M64CMD_CORE_STATE_SET, M64CORE_SPEED_LIMITER, &limiter);
    m64p_error err = do_command(M64CMD_EXECUTE, 0, NULL);
    fprintf(stderr, "stopped %u\n", vi_count());
    do_command(M64CMD_ROM_CLOSE, 0, NULL);
    return err == M64ERR_SUCCESS ? 0 : 1;
}
