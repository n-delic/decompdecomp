/*
 * The host side of an N64 game compiled to WebAssembly and translated to C by wasm2c (port/wasm, docs/PORTING.md).
 *
 * The game's memory is a wasm linear memory read big-endian (bemem.py), with its data at the N64's own addresses
 * (0x80000400 up), so pointers are 32-bit console addresses. Everything here reads and writes that memory with the
 * be* helpers. What libultra does on the console (threads and queues, DMA from the cartridge, the VI retrace, the
 * controllers) is done here, on the host.
 *
 * Threads: each game thread is a host thread; one runs at a time, the highest-priority ready one, as on the N64's
 * one CPU (the same scheduler as consoles/n64/port_glue/threads.c). Each has its own wasm shadow stack, in memory
 * below the game's data, swapped into the module's __stack_pointer when it gets the CPU.
 */
#include <pthread.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include "game.h"
#define SP_STATUS 0xA4040010u /* the RSP's status register: libultra waits for its halt bit */
#define IMAGE_END 0xA7FE0u /* where the game's zeroed memory starts, as a ROM offset: its code and data end */
#include "wasm-rt-exceptions.h"
#include "wasm-rt-impl.h"

struct w2c_env { int unused; };
static struct w2c_env env;
static w2c_game game;
static uint8_t *M; /* the game's memory */
static uint8_t *rom;
static size_t rom_size;
static int verbose = 1;

#define LOG(...) do { if (verbose) { fprintf(stderr, __VA_ARGS__); fputc('\n', stderr); } } while (0)

static uint32_t be32(uint32_t a) { uint32_t v; memcpy(&v, M + a, 4); return __builtin_bswap32(v); }
static uint16_t be16(uint32_t a) { uint16_t v; memcpy(&v, M + a, 2); return __builtin_bswap16(v); }
static void wbe32(uint32_t a, uint32_t v) { v = __builtin_bswap32(v); memcpy(M + a, &v, 4); }
static void wbe16(uint32_t a, uint16_t v) { v = __builtin_bswap16(v); memcpy(M + a, &v, 2); }
uint8_t *port_memory(void) { return M; } /* for the renderer (render.c) */
void port_render_task(uint32_t task); /* render.c: a graphics task's display list, drawn into the game's frame buffer */

/* ---------------------------------------------------------------- the game's functions by address */

typedef struct { unsigned address; void *func; } PortFunc;
extern const PortFunc port_funcs[];
extern const unsigned port_func_count;
void port_trace(void);

/* The function at a console address: a function pointer in the game is that address. */
static void port_preempt(void);
void *port_func(uint32_t address) {
    /* LLVM calls some functions through the table when the call's types differ from the function's only as
       pointer and integer: an index, never a console address (those are 0x80000000 and up) */
    port_preempt();
    wasm_rt_funcref_table_t *table = &game.w2c_0x5F_indirect_function_table;
    if (address < table->size && table->data[address].func) return (void *)table->data[address].func;
    unsigned lo = 0, hi = port_func_count;
    while (lo < hi) {
        unsigned mid = (lo + hi) / 2;
        if (port_funcs[mid].address < address) lo = mid + 1; else hi = mid;
    }
    if (lo < port_func_count && port_funcs[lo].address == address) return port_funcs[lo].func;
    fprintf(stderr, "a call through a function pointer to %08X, where no function of the game's C is\n", address);
    port_trace();
    wasm_rt_trap(WASM_RT_TRAP_CALL_INDIRECT);
}

/* ---------------------------------------------------------------- threads and message queues */

enum { CREATED, READY, WAITING, STOPPED, DONE };
enum { Q_MTQUEUE = 0, Q_FULLQUEUE = 4, Q_VALID = 8, Q_FIRST = 12, Q_COUNT = 16, Q_MSG = 20 };
#define OS_MESG_BLOCK 1
#define OS_NUM_EVENTS 15
#define SHADOW_STACKS 0x10000000u /* each thread's wasm shadow stack: 1 MB, below the game's data */
#define SHADOW_SIZE 0x100000u

typedef struct PortThread {
    uint32_t os, entry, arg, sp;
    int32_t id, pri;
    int state, started, number, waits, has_run;
    unsigned long long ready_at;
    pthread_t host;
    pthread_cond_t turn;
    uint32_t waiting_on;
    struct PortThread *next;
} PortThread;

static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
static PortThread *threads, *running;
static _Thread_local PortThread *self;
static unsigned long long ticks;
static int thread_count;
static int booted; /* the boot code runs on the host's main thread: no game thread runs until it returns */
static volatile int preempt; /* a thread woken from outside outranks the running one */
static struct { uint32_t queue, msg; } events[OS_NUM_EVENTS];

static PortThread *find(uint32_t t) {
    for (PortThread *p = threads; p; p = p->next)
        if (p->os == t) return p;
    return NULL;
}
static void make_ready(PortThread *p) { p->state = READY; p->waiting_on = 0; p->ready_at = ++ticks; }
static void schedule(void) {
    PortThread *best = NULL;
    for (PortThread *p = threads; p; p = p->next)
        if (p->state == READY && (!best || p->pri > best->pri || (p->pri == best->pri && p->ready_at < best->ready_at)))
            best = p;
    running = best;
    if (best) pthread_cond_signal(&best->turn);
}
/* Give the CPU up and wait (lock held) until it's this thread's again: its shadow stack goes with it. */
static void wait_turn(PortThread *me) {
    if (me->has_run) me->sp = game.w2c_0x5F_stack_pointer0x2E1; /* a new thread's is its fresh stack */
    me->has_run = 1;
    while (running != me || me->state != READY) pthread_cond_wait(&me->turn, &lock);
    game.w2c_0x5F_stack_pointer0x2E1 = me->sp;
}
static void after_wake(PortThread *woken) {
    if (self && woken && woken->pri > self->pri) {
        self->ready_at = ++ticks;
        schedule();
        wait_turn(self);
    } else if (!self && woken && !running && booted) {
        schedule();
    } else if (!self && woken && running && woken->pri > running->pri) {
        preempt = 1; /* the running thread yields at its next safe point (port_preempt) */
    }
}
/* A safe point (an indirect call): if a thread woken from outside outranks this one, it runs now, as the N64's
   interrupt would have switched to it. */
static void port_preempt(void) {
    if (self && running != self) {
        static int told;
        if (told++ < 3) {
            fprintf(stderr, "thread %d runs while thread %d has the CPU\n", self->number, running ? running->number : -1);
            port_trace();
        }
    }
    if (!preempt || !self) return;
    pthread_mutex_lock(&lock);
    preempt = 0;
    PortThread *best = NULL;
    for (PortThread *p = threads; p; p = p->next)
        if (p->state == READY && p != self && (!best || p->pri > best->pri)) best = p;
    if (best && best->pri > self->pri) {
        self->ready_at = ++ticks;
        schedule();
        wait_turn(self);
    }
    pthread_mutex_unlock(&lock);
}

static PortThread *wake_one(uint32_t mq) {
    PortThread *best = NULL;
    for (PortThread *p = threads; p; p = p->next)
        if (p->state == WAITING && p->waiting_on == mq && (!best || p->pri > best->pri)) best = p;
    if (best) make_ready(best);
    return best;
}

static void *host_entry(void *arg) {
    PortThread *me = arg;
    self = me;
    wasm_rt_init_thread();
    pthread_mutex_lock(&lock);
    wait_turn(me);
    pthread_mutex_unlock(&lock);
    LOG("thread %d (id %d, pri %d) starts at table entry %u", me->number, me->id, me->pri, me->entry);
    wasm_rt_trap_t trap = wasm_rt_impl_try();
    if (trap) {
        fprintf(stderr, "thread %d stopped: %s\n", me->number, wasm_rt_strerror(trap));
        _exit(3);
    }
    ((void (*)(void *, uint32_t))port_func(me->entry))(&game, me->arg);
    LOG("thread %d returned", me->number);
    pthread_mutex_lock(&lock);
    me->state = DONE;
    schedule();
    pthread_mutex_unlock(&lock);
    return NULL;
}

void w2c_env_osCreateThread(struct w2c_env *e, u32 t, u32 id, u32 entry, u32 arg, u32 sp, u32 pri) {
    (void)e; (void)sp; /* the game's stack is sized for MIPS frames: the thread gets a shadow stack of its own */
    pthread_mutex_lock(&lock);
    PortThread *p = find(t);
    if (p && p->started && p->state != DONE) {
        /* The game re-creates a thread that already ran (a racing game's loader does, for its two stream threads):
           the N64 starts it afresh at the new entry. Its old host thread stays parked for good, never READY again,
           and the OSThread gets a new one, else the old one would resume where it waited, on stale data. */
        PortThread **link = &threads;
        while (*link != p) link = &(*link)->next;
        *link = p->next;
        p->state = DONE;
        p = NULL;
    }
    if (!p || p->state == DONE) {
        if (p) { /* a thread that ended: unlink it, its host thread is gone */
            PortThread **link = &threads;
            while (*link != p) link = &(*link)->next;
            *link = p->next;
        }
        p = calloc(1, sizeof *p);
        pthread_cond_init(&p->turn, NULL);
        p->os = t;
        p->number = thread_count++;
        p->next = threads;
        threads = p;
    }
    p->id = (int32_t)id; p->pri = (int32_t)pri; p->entry = entry; p->arg = arg; p->state = CREATED;
    p->sp = SHADOW_STACKS + (uint32_t)(p->number + 1) * SHADOW_SIZE - 16;
    LOG("osCreateThread(%08X, id %d, entry %u, pri %d)", t, (int)id, entry, (int)pri);
    pthread_mutex_unlock(&lock);
    return;
}
void w2c_env_osStartThread(struct w2c_env *e, u32 t) {
    (void)e;
    pthread_mutex_lock(&lock);
    PortThread *p = find(t);
    if (p && (p->state == CREATED || p->state == STOPPED)) {
        make_ready(p);
        if (!p->started) {
            p->started = 1;
            pthread_attr_t attr;
            pthread_attr_init(&attr);
            pthread_attr_setstacksize(&attr, 64 << 20); /* wasm2c's C recurses as deep as the game */
            pthread_attr_setdetachstate(&attr, PTHREAD_CREATE_DETACHED);
            pthread_create(&p->host, &attr, host_entry, p);
            pthread_attr_destroy(&attr);
        }
        after_wake(p);
    }
    pthread_mutex_unlock(&lock);
}
void w2c_env_osDestroyThread(struct w2c_env *e, u32 t) {
    (void)e;
    pthread_mutex_lock(&lock);
    PortThread *p = t ? find(t) : self;
    if (p) {
        p->state = DONE;
        if (p == self) { schedule(); pthread_mutex_unlock(&lock); pthread_exit(NULL); }
        if (p == running) schedule();
    }
    pthread_mutex_unlock(&lock);
}
void w2c_env_osSetThreadPri(struct w2c_env *e, u32 t, u32 pri) {
    (void)e;
    pthread_mutex_lock(&lock);
    PortThread *p = t ? find(t) : self;
    if (p) {
        p->pri = (int32_t)pri;
        if (self && self->state == READY) { self->ready_at = ++ticks; schedule(); wait_turn(self); }
    }
    pthread_mutex_unlock(&lock);
}

void w2c_env_osCreateMesgQueue(struct w2c_env *e, u32 mq, u32 buf, u32 count) {
    (void)e;
    pthread_mutex_lock(&lock);
    wbe32(mq + Q_MTQUEUE, 0); wbe32(mq + Q_FULLQUEUE, 0); wbe32(mq + Q_VALID, 0); wbe32(mq + Q_FIRST, 0);
    wbe32(mq + Q_COUNT, count); wbe32(mq + Q_MSG, buf);
    pthread_mutex_unlock(&lock);
}
static int has_room(uint32_t mq) { return (int32_t)be32(mq + Q_VALID) < (int32_t)be32(mq + Q_COUNT); }
static int has_mesg(uint32_t mq) { return (int32_t)be32(mq + Q_VALID) > 0; }
static int block_on(uint32_t mq, uint32_t flag, int (*ready)(uint32_t)) {
    while (!ready(mq)) {
        if (flag != OS_MESG_BLOCK || !self) return -1;
        if (verbose > 1 || self->waits++ < 3) {
            LOG("thread %d waits on queue %08X", self->number, mq);
            if (verbose > 1) port_trace();
        }
        self->state = WAITING;
        self->waiting_on = mq;
        schedule();
        wait_turn(self);
    }
    return 0;
}
static int32_t put(uint32_t mq, uint32_t msg, uint32_t flag, int jam) {
    pthread_mutex_lock(&lock);
    if (block_on(mq, flag, has_room) < 0) { pthread_mutex_unlock(&lock); return -1; }
    uint32_t count = be32(mq + Q_COUNT), first = be32(mq + Q_FIRST), valid = be32(mq + Q_VALID), buf = be32(mq + Q_MSG);
    if (jam) {
        first = (first + count - 1) % count;
        wbe32(mq + Q_FIRST, first);
        wbe32(buf + 4 * first, msg);
    } else {
        wbe32(buf + 4 * ((first + valid) % count), msg);
    }
    wbe32(mq + Q_VALID, valid + 1);
    after_wake(wake_one(mq));
    pthread_mutex_unlock(&lock);
    return 0;
}
u32 w2c_env_osSendMesg(struct w2c_env *e, u32 mq, u32 msg, u32 flag) {
    (void)e;
    if (mq == 0x800C7938u && be32(msg + 4) < 0x80000000u) { /* a DMA request to outside RAM: who asks */
        fprintf(stderr, "DMA request ROM %08X -> %08X, %X bytes\n", be32(msg), be32(msg + 4), be32(msg + 8));
        port_trace();
    }
    return (u32)put(mq, msg, flag, 0);
}
u32 w2c_env_osJamMesg(struct w2c_env *e, u32 mq, u32 msg, u32 flag) { (void)e; return (u32)put(mq, msg, flag, 1); }
u32 w2c_env_osRecvMesg(struct w2c_env *e, u32 mq, u32 msg, u32 flag) {
    (void)e;
    pthread_mutex_lock(&lock);
    if (block_on(mq, flag, has_mesg) < 0) { pthread_mutex_unlock(&lock); return (u32)-1; }
    uint32_t count = be32(mq + Q_COUNT), first = be32(mq + Q_FIRST), buf = be32(mq + Q_MSG);
    if (msg) wbe32(msg, be32(buf + 4 * first));
    wbe32(mq + Q_FIRST, (first + 1) % count);
    wbe32(mq + Q_VALID, be32(mq + Q_VALID) - 1);
    after_wake(wake_one(mq));
    pthread_mutex_unlock(&lock);
    return 0;
}
void w2c_env_osSetEventMesg(struct w2c_env *e, u32 event, u32 mq, u32 msg) {
    (void)e;
    if (event >= OS_NUM_EVENTS) return;
    LOG("osSetEventMesg(%u, %08X)", event, mq);
    pthread_mutex_lock(&lock);
    events[event].queue = mq;
    events[event].msg = msg;
    pthread_mutex_unlock(&lock);
}
/* An event as the hardware would raise it: a message on the queue the game set for it, never waiting. */
static int post_event(int event) {
    pthread_mutex_lock(&lock);
    uint32_t mq = events[event].queue, msg = events[event].msg;
    pthread_mutex_unlock(&lock);
    if (!mq) return 0;
    put(mq, msg, 0, 0);
    return 1;
}
enum { EV_SP = 4, EV_SI = 5, EV_AI = 6, EV_VI = 7, EV_PI = 8, EV_DP = 9 };

/* ---------------------------------------------------------------- the console's uncached addresses */

#define IO_SCRATCH 0x000A0000u /* a word for reads the host answers and writes it drops (below the game's data) */
int port_ai_register(uint32_t addr, int write, uint32_t value, uint32_t *read); /* audio.c */
uint64_t port_io_load(uint64_t addr) {
    if (addr < 0xA0800000u) return addr - 0x20000000u; /* RAM, uncached: the same RAM */
    if (addr == SP_STATUS) { wbe32(IO_SCRATCH, 1); return IO_SCRATCH; } /* halted: a task finishes at once */
    uint32_t v;
    if (port_ai_register((uint32_t)addr, 0, 0, &v)) { wbe32(IO_SCRATCH, v); return IO_SCRATCH; }
    return addr;
}
uint64_t port_io_store(uint64_t addr, uint64_t value) {
    if (addr < 0xA0800000u) return addr - 0x20000000u;
    if (addr == SP_STATUS) return IO_SCRATCH + 4; /* a command (clear halt, set a signal): nothing to keep */
    if (port_ai_register((uint32_t)addr, 1, (uint32_t)value, NULL)) return IO_SCRATCH + 4;
    return addr;
}

/* ---------------------------------------------------------------- VI */

static struct { uint32_t queue, msg, every; } vi;
static uint32_t frame_buffer;
static unsigned long vi_count, swaps;

void w2c_env_osCreateViManager(struct w2c_env *e, u32 pri) { (void)e; (void)pri; }
void w2c_env_osViSetEvent(struct w2c_env *e, u32 mq, u32 msg, u32 every) {
    (void)e;
    LOG("osViSetEvent(%08X, every %u)", mq, every);
    vi.queue = mq; vi.msg = msg; vi.every = every ? every : 1;
}
void w2c_env_osViSetMode(struct w2c_env *e, u32 mode) { (void)e; LOG("osViSetMode(%08X)", mode); }
void w2c_env_osViSetYScale(struct w2c_env *e, f32 s) { (void)e; (void)s; }
void w2c_env_osViBlack(struct w2c_env *e, u32 on) { (void)e; (void)on; }
void w2c_env_osViSetSpecialFeatures(struct w2c_env *e, u32 f) { (void)e; (void)f; }
void port_frame_shown(uint32_t fb, unsigned long n); /* render.c: saves it when asked */
void w2c_env_viSetSwapBuffer(struct w2c_env *e, u32 fb) { (void)e; frame_buffer = fb; swaps++; port_frame_shown(fb, swaps); }

static void retrace(void) {
    vi_count++;
    if (vi.queue && vi_count % vi.every == 0) put(vi.queue, vi.msg, 0, 0);
}

/* ---------------------------------------------------------------- PI: the cartridge */

void w2c_env_osCreatePiManager(struct w2c_env *e, u32 pri, u32 q, u32 buf, u32 n) { (void)e; (void)pri; (void)q; (void)buf; (void)n; }
static unsigned long dmas;
u32 w2c_env_osPiStartDma(struct w2c_env *e, u32 mb, u32 pri, u32 dir, u32 dev, u32 dram, u32 size, u32 mq) {
    (void)e; (void)pri;
    dmas++;
    if (getenv("PORT_DMA")) fprintf(stderr, "osPiStartDma(mb %08X, pri %u, dir %u, dev %08X, dram %08X, size %X, mq %08X)\n", mb, pri, dir, dev, dram, size, mq);
    if (dram < 0x80000000u) { fprintf(stderr, "osPiStartDma to %08X, outside the console's RAM\n", dram); port_trace(); }
    if (dir != 0) LOG("osPiStartDma: a write to the cartridge (%08X), ignored", dev);
    else if (dev + (uint64_t)size > rom_size) LOG("osPiStartDma: %08X+%X is past the ROM's end", dev, size);
    else memcpy(M + dram, rom + dev, size);
    if (dmas <= 20 || dmas % 500 == 0) LOG("DMA %lu: ROM %06X -> %08X, %X bytes", dmas, dev, dram, size);
    if (mb) { wbe32(mb + 8, dram); wbe32(mb + 12, dev); wbe32(mb + 16, size); }
    if (mq) put(mq, mb, 0, 0);
    return 0;
}
u32 w2c_env_osVirtualToPhysical(struct w2c_env *e, u32 a) { (void)e; return a & 0x1FFFFFFF; }

/* ---------------------------------------------------------------- RSP tasks: graphics (render.c) and audio (audio.c) */

static unsigned long gfx_tasks, audio_tasks;
void port_audio_task(uint32_t task); /* audio.c */
void w2c_env_osSpTaskStartGo(struct w2c_env *e, u32 task) {
    (void)e;
    uint32_t type = be32(task);
    if (type == 1) gfx_tasks++; else audio_tasks++;
    if (type == 1) port_render_task(task);
    if (type == 2) port_audio_task(task);
    if (gfx_tasks + audio_tasks <= 4) LOG("RSP task type %u: data %08X, %X bytes", type, be32(task + 48), be32(task + 52));
    if (type == 1 && gfx_tasks == 2 && getenv("PORT_TASK")) {
        uint32_t ud = be32(task + 24) & 0x1FFFFFFF, uds = be32(task + 28), dl = be32(task + 48) & 0x1FFFFFFF;
        fprintf(stderr, "gfx task: ucode %08X (%X), ucode data %08X (%X), data %08X\n",
                be32(task + 16), be32(task + 20), be32(task + 24), uds, be32(task + 48));
        for (uint32_t i = 0; i + 3 < uds; i++)  /* the microcode's version string lives in its data */
            if (M[0x80000000u + ud + i] == 'R' && M[0x80000000u + ud + i + 1] == 'S' && M[0x80000000u + ud + i + 2] == 'P') {
                fprintf(stderr, "ucode text: %.80s\n", (char *)&M[0x80000000u + ud + i]); break; }
        for (int i = 0; i < 24; i++) fprintf(stderr, "%08X%c", be32(0x80000000u + dl + 4 * i), i % 2 ? '\n' : ' ');
    }
    wbe32(SP_STATUS, 1); /* done: halted again */
    post_event(EV_SP);
    if (type == 1) post_event(EV_DP);
}
u32 w2c_env_osSpTaskYielded(struct w2c_env *e, u32 task) { (void)e; (void)task; return 0; }

/* ---------------------------------------------------------------- the CPU's own registers */

/* The MIPS builtins the matched C reaches the R4300's coprocessor 0 and FPU with (the compiler's, for code that
   reads Status or Count): answered here, as the console's CPU would. */
static uint32_t cop0_status = 0x3400FF01u, cop0_compare, fcsr = 0x01000800u;
u32 w2c_env_0x5F_builtin_mips_read_status(struct w2c_env *e) { (void)e; return cop0_status; }
u32 w2c_env_0x5F_builtin_mips_write_status_return_spacing(struct w2c_env *e, u32 v) { (void)e; cop0_status = v; return 0; }
u32 w2c_env_0x5F_builtin_mips_write_compare(struct w2c_env *e, u32 v) { (void)e; cop0_compare = v; return 0; }
u32 w2c_env_0x5F_builtin_mips_read_count(struct w2c_env *e) { /* Count runs at half the CPU's 93.75 MHz */
    (void)e;
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (uint32_t)((uint64_t)t.tv_sec * 46875000u + (uint64_t)t.tv_nsec * 46875u / 1000000u);
}
u32 w2c_env_0x5F_builtin_mips_exchange_fcsr(struct w2c_env *e, u32 v) { (void)e; uint32_t old = fcsr; fcsr = v; return old; }
u32 w2c_env_0x5F_builtin_mips_live_return_address(struct w2c_env *e) { (void)e; return 0; } /* none in wasm */
u32 w2c_env_0x5F_builtin_mips_debug_break(struct w2c_env *e) { (void)e; return 0; } /* a debugger's breakpoint */

/* ---------------------------------------------------------------- controllers, saves */

static uint16_t buttons;
u32 w2c_env_osContInit(struct w2c_env *e, u32 mq, u32 bits, u32 status) {
    (void)e; (void)mq;
    M[bits] = 1;
    for (int i = 0; i < 4; i++) { wbe16(status + 4 * i, i == 0 ? 5 : 0); M[status + 4 * i + 2] = 0; M[status + 4 * i + 3] = i == 0 ? 0 : 8; }
    return 0;
}
static void si_done(uint32_t mq) { if (!post_event(EV_SI) && mq) put(mq, 0, 0, 0); }
u32 w2c_env_osContStartReadData(struct w2c_env *e, u32 mq) { (void)e; si_done(mq); return 0; }
/* PORT_INPUT: the first controller, scripted by frame shown: "300:START,306:-,400:A+UP,500:SX=60+SY=-20".
   Each entry sets the state from that frame on: buttons joined by +, "-" for none, SX/SY for the stick. */
static int8_t stick_x, stick_y;
int port_live_input(uint16_t *buttons, int8_t *x, int8_t *y); /* live.c: the keyboard, with PORT_LIVE */
void port_live_start(void);
static void script_input(void);
static void scripted_input(void) {
    uint16_t b; int8_t x, y;
    script_input();
    if (port_live_input(&b, &x, &y) && (b || x || y)) { buttons = b; stick_x = x; stick_y = y; } /* a key held wins */
}
static void script_input(void) {
    static const char *script;
    static int loaded;
    if (!loaded) { loaded = 1; script = getenv("PORT_INPUT"); }
    buttons = 0; stick_x = stick_y = 0;
    if (!script) return;
    static const struct { const char *name; uint16_t bit; } names[] = {
        {"A", 0x8000}, {"B", 0x4000}, {"Z", 0x2000}, {"START", 0x1000}, {"UP", 0x0800}, {"DOWN", 0x0400},
        {"LEFT", 0x0200}, {"RIGHT", 0x0100}, {"L", 0x0020}, {"R", 0x0010}, {"CU", 0x0008}, {"CD", 0x0004},
        {"CL", 0x0002}, {"CR", 0x0001}};
    const char *p = script;
    while (*p) { /* the last entry at or before this frame wins */
        char *end;
        unsigned long at = strtoul(p, &end, 10);
        if (*end != ':') break;
        p = end + 1;
        uint16_t b = 0; int sx = 0, sy = 0;
        while (*p && *p != ',') {
            size_t n = strcspn(p, "+,");
            if (!strncmp(p, "SX=", 3)) sx = atoi(p + 3);
            else if (!strncmp(p, "SY=", 3)) sy = atoi(p + 3);
            else for (size_t i = 0; i < sizeof names / sizeof *names; i++)
                if (strlen(names[i].name) == n && !strncmp(p, names[i].name, n)) b |= names[i].bit;
            p += n;
            if (*p == '+') p++;
        }
        if (at <= swaps) { buttons = b; stick_x = (int8_t)sx; stick_y = (int8_t)sy; }
        if (*p == ',') p++;
    }
}
void w2c_env_osContGetReadData(struct w2c_env *e, u32 pad) {
    (void)e;
    scripted_input();
    for (int i = 0; i < 4; i++) {
        wbe16(pad + 6 * i, i == 0 ? buttons : 0);
        M[pad + 6 * i + 2] = i == 0 ? (uint8_t)stick_x : 0; M[pad + 6 * i + 3] = i == 0 ? (uint8_t)stick_y : 0;
        M[pad + 6 * i + 4] = i == 0 ? 0 : 8;
    }
}
/* The cartridge's save EEPROM: 4 Kbit, 64 blocks of 8 bytes, kept in a file (PORT_SAVE=path, else game.eep in the
   folder the game runs from), written through as the game writes, so its saves outlive the program. */
static uint8_t eeprom[512];
static const char *eeprom_path(void) { return getenv("PORT_SAVE") ? getenv("PORT_SAVE") : "game.eep"; }
static void eeprom_load(void) {
    static int loaded;
    if (loaded) return;
    loaded = 1;
    memset(eeprom, 0xFF, sizeof eeprom); /* a blank chip */
    FILE *f = fopen(eeprom_path(), "rb");
    if (f) { if (fread(eeprom, 1, sizeof eeprom, f) != sizeof eeprom) LOG("%s: shorter than an EEPROM", eeprom_path()); fclose(f); }
}
static void eeprom_write(int block, const uint8_t *data) {
    memcpy(eeprom + block * 8, data, 8);
    FILE *f = fopen(eeprom_path(), "wb");
    if (f) { fwrite(eeprom, 1, sizeof eeprom, f); fclose(f); }
    else LOG("can't write the save to %s", eeprom_path());
}

/* The serial bus as the game drives it itself (its own __osSiRawStartDma: Controller Pak and EEPROM access): the
   64 bytes of PIF RAM, answered as the PIF would: the first controller, with no Controller Pak in it, the EEPROM on
   channel 4, and nothing else. Each transfer raises the SI event the game waits for: a racing game's save after its
   car selection waited for it for ever, and without the EEPROM it said its battery backup was damaged. */
static uint8_t pif[64];
static void pif_run(void) {
    int ch = 0;
    for (int i = 0; i < 63;) {
        uint8_t t = pif[i];
        if (t == 0xFE) break;
        if (t == 0xFF) { i++; continue; }
        if (t == 0) { ch++; i++; continue; }
        int tx = t & 0x3F, rx = pif[i + 1] & 0x3F, cmd = pif[i + 2];
        uint8_t *out = pif + i + 2 + tx;
        if (i + 2 + tx + rx > 63) break;
        if (ch == 0 && (cmd == 0x00 || cmd == 0xFF) && rx >= 3) { out[0] = 0x05; out[1] = 0x00; out[2] = 0x02; }
        else if (ch == 0 && cmd == 0x01 && rx >= 4) {
            scripted_input();
            out[0] = (uint8_t)(buttons >> 8); out[1] = (uint8_t)buttons; out[2] = (uint8_t)stick_x; out[3] = (uint8_t)stick_y;
        } else if (ch == 4 && (cmd == 0x00 || cmd == 0xFF) && rx >= 3) { out[0] = 0x00; out[1] = 0x80; out[2] = 0x00; }
        else if (ch == 4 && cmd == 0x04 && tx >= 2 && rx >= 8) { eeprom_load(); memcpy(out, eeprom + (pif[i + 3] & 63) * 8, 8); }
        else if (ch == 4 && cmd == 0x05 && tx >= 10) { eeprom_load(); eeprom_write(pif[i + 3] & 63, pif + i + 4); if (rx) out[0] = 0; }
        else pif[i + 1] |= 0x80; /* no device answers: no Controller Pak, no other controller */
        i += 2 + tx + rx;
        ch++;
    }
    pif[63] = 0;
}
u32 w2c_env_0x5F_osSiRawStartDma(struct w2c_env *e, u32 direction, u32 dram) {
    (void)e;
    if (direction == 1) { for (int i = 0; i < 64; i++) pif[i] = M[dram + i]; pif_run(); }
    else for (int i = 0; i < 64; i++) M[dram + i] = pif[i];
    post_event(EV_SI);
    return 0;
}
u32 w2c_env_osContStartQuery(struct w2c_env *e, u32 mq) { (void)e; si_done(mq); return 0; }
void w2c_env_osContGetQuery(struct w2c_env *e, u32 status) {
    (void)e;
    for (int i = 0; i < 4; i++) { wbe16(status + 4 * i, i == 0 ? 5 : 0); M[status + 4 * i + 2] = 0; M[status + 4 * i + 3] = i == 0 ? 0 : 8; }
    return;
}
u32 w2c_env_osEepromProbe(struct w2c_env *e, u32 mq) { (void)e; (void)mq; return 1; } /* EEPROM_TYPE_4K */
u32 w2c_env_osMotorInit(struct w2c_env *e, u32 mq, u32 pfs, u32 ch) { (void)e; (void)mq; (void)pfs; (void)ch; return 1; }
u32 w2c_env_0x5F_osMotorAccess(struct w2c_env *e, u32 pfs, u32 on) { (void)e; (void)pfs; (void)on; return 1; }
u32 w2c_env_osPfsIsPlug(struct w2c_env *e, u32 mq, u32 pattern) { (void)e; (void)mq; M[pattern] = 0; return 0; }
u32 w2c_env_osPfsInitPak(struct w2c_env *e, u32 mq, u32 pfs, u32 ch) { (void)e; (void)mq; (void)pfs; (void)ch; return 1; }

/* ---------------------------------------------------------------- the rest of libultra and libc */

void port_ai_frequency(uint32_t hz); /* audio.c */
u32 w2c_env_osAiSetFrequency(struct w2c_env *e, u32 f) { (void)e; LOG("osAiSetFrequency(%u)", f); port_ai_frequency(f); return f; }
void w2c_env_osInvalDCache(struct w2c_env *e, u32 a, u32 n) { (void)e; (void)a; (void)n; }
void w2c_env_osInvalICache(struct w2c_env *e, u32 a, u32 n) { (void)e; (void)a; (void)n; }
void w2c_env_osWritebackDCache(struct w2c_env *e, u32 a, u32 n) { (void)e; (void)a; (void)n; }
void w2c_env_osWritebackDCacheAll(struct w2c_env *e) { (void)e; }
u32 w2c_env_osSetIntState(struct w2c_env *e, u32 s) { (void)e; return s; }
void w2c_env_func_8008C4F0(struct w2c_env *e) { (void)e; } /* osMapTLBRdb */
static struct timespec start;
u64 w2c_env_osGetTime(struct w2c_env *e) {
    (void)e;
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    double s = (now.tv_sec - start.tv_sec) + (now.tv_nsec - start.tv_nsec) / 1e9;
    return (u64)(s * 46875000.0); /* the CPU's count rate */
}
u32 w2c_env_memcpy(struct w2c_env *e, u32 d, u32 s, u32 n) { (void)e; memmove(M + d, M + s, n); return d; }
void w2c_env_0x5F_osCopyBytes(struct w2c_env *e, u32 s, u32 d, u32 n) { (void)e; memmove(M + d, M + s, n); }
void w2c_env_func_80091920(struct w2c_env *e, u32 p, u32 n) { (void)e; memset(M + p, 0, n); } /* bzero */
u32 w2c_env_str_len(struct w2c_env *e, u32 s) { (void)e; return (u32)strlen((char *)M + s); }
u32 w2c_env_abs(struct w2c_env *e, u32 x) { (void)e; return (u32)abs((int32_t)x); }
u64 w2c_env_func_80099F78(struct w2c_env *e, u64 a, u64 b) { (void)e; return b ? a / b : 0; }
u64 w2c_env_func_8009A074(struct w2c_env *e, u64 a, u64 b) { (void)e; return b ? a / b : 0; }
u64 w2c_env_func_8009A2D0(struct w2c_env *e, u64 a, u64 b) { (void)e; return b ? a % b : 0; }
u64 w2c_env_func_8009A310(struct w2c_env *e, f64 x) { (void)e; return (u64)x; }
f64 w2c_env_func_8009A430(struct w2c_env *e, u64 x) { (void)e; return (f64)x; }
/* the game's old name for format_to_buffer: the same variadic call (its third argument points at the rest) */
u32 w2c_env_formatString(struct w2c_env *e, u32 a, u32 b, u32 c) { (void)e; w2c_game_format_to_buffer(&game, a, b, c); return 0; }
void w2c_env_D_8000A840(struct w2c_env *e, u32 a, u32 b, u32 c, u32 d, u32 f, u32 g) {
    (void)e; (void)a; (void)b; (void)c; (void)d; (void)f; (void)g;
    static int once;
    if (!once++) LOG("D_8000A840 (a function the split missed) called: skipped");
}

/* ---------------------------------------------------------------- start */

static uint8_t *read_file(const char *path, size_t *size) {
    FILE *f = fopen(path, "rb");
    if (!f) return NULL;
    fseek(f, 0, SEEK_END);
    *size = (size_t)ftell(f);
    fseek(f, 0, SEEK_SET);
    uint8_t *b = malloc(*size);
    if (fread(b, 1, *size, f) != *size) { fclose(f); free(b); return NULL; }
    fclose(f);
    return b;
}

#include <signal.h>
/* PORT_SAMPLE: once a second, where the running game thread is (a backtrace for atos) */
static void sample_handler(int sig) { (void)sig; port_trace(); }
static void sample(void) {
    pthread_mutex_lock(&lock);
    PortThread *r = running;
    pthread_t t = r ? r->host : 0;
    int n = r ? r->number : -1;
    pthread_mutex_unlock(&lock);
    if (r && !getenv("PORT_SAMPLE_THREAD")) { fprintf(stderr, "running: thread %d\n", n); pthread_kill(t, SIGUSR1); }
    if (getenv("PORT_SAMPLE_THREAD")) { /* PORT_SAMPLE_THREAD=n: thread n instead, running or waiting */
        pthread_mutex_lock(&lock);
        for (PortThread *p = threads; p; p = p->next)
            if (p->state != DONE && p->started && p->number == atoi(getenv("PORT_SAMPLE_THREAD"))) { fprintf(stderr, "thread %d:\n", p->number); pthread_kill(p->host, SIGUSR1); }
        pthread_mutex_unlock(&lock);
    }
    if (getenv("PORT_THREADS")) {
        static const char *names[] = { "created", "ready", "waiting", "stopped", "done" };
        pthread_mutex_lock(&lock);
        for (PortThread *p = threads; p; p = p->next)
            fprintf(stderr, "  thread %d id %d pri %d %s%s%08X entry %08X\n", p->number, p->id, p->pri, names[p->state],
                    p->state == WAITING ? " on " : " ", p->state == WAITING ? p->waiting_on : 0, p->entry);
        pthread_mutex_unlock(&lock);
    }
    const char *w = getenv("PORT_WATCH"); /* addr[,words]: memory to print, big-endian words */
    if (w) {
        uint32_t a = (uint32_t)strtoul(w, NULL, 16);
        int words = strchr(w, ',') ? atoi(strchr(w, ',') + 1) : 4;
        fprintf(stderr, "%08X:", a);
        for (int i = 0; i < words; i++) fprintf(stderr, " %08X", be32(a + 4 * i));
        fputc('\n', stderr);
    }
}

int main(int argc, char **argv) {
    signal(SIGUSR1, sample_handler);
    if (argc < 2) { fprintf(stderr, "usage: %s rom.z64 [seconds, 0: until stopped]\n", argv[0]); return 2; }
    rom = read_file(argv[1], &rom_size);
    if (!rom) { fprintf(stderr, "can't read %s\n", argv[1]); return 2; }
    double seconds = argc > 2 ? atof(argv[2]) : getenv("PORT_LIVE") ? 0 : 5; /* 0: until it's stopped */
    port_live_start();
    if (getenv("PORT_VERBOSE")) verbose = atoi(getenv("PORT_VERBOSE"));
    clock_gettime(CLOCK_MONOTONIC, &start);
    wasm_rt_init();
    wasm2c_game_instantiate(&game, &env);
    M = game.w2c_memory.data;
    LOG("memory: %llu MB, ROM %zu bytes", (unsigned long long)game.w2c_memory.size >> 20, rom_size);
    /* as the console's boot does: the game's code and data from the cartridge at their addresses, and the values
       the boot leaves in low memory for libultra */
    memcpy(M + 0x80000400u, rom + 0x1000, IMAGE_END - 0x1000);
    wbe32(SP_STATUS, 1);           /* the RSP is halted until a task starts it */
    wbe32(0x80000300, 0);          /* osTvType: PAL */
    wbe32(0x80000308, 0xB0000000); /* osRomBase */
    wbe32(0x8000030C, 0);          /* osResetType: cold */
    wbe32(0x80000318, 0x400000);   /* osMemSize */
    wasm_rt_trap_t trap = wasm_rt_impl_try();
    if (trap) { fprintf(stderr, "boot stopped: %s\n", wasm_rt_strerror(trap)); return 3; }
    LOG("boot_start");
    w2c_game_boot_start(&game);
    LOG("boot_start returned; the game's threads run");
    pthread_mutex_lock(&lock);
    booted = 1;
    schedule();
    pthread_mutex_unlock(&lock);
    /* the VI retrace, PAL (50 Hz) */
    for (int frame = 0; seconds <= 0 || frame < seconds * 50; frame++) {
        usleep(20000);
        retrace();
        if (getenv("PORT_SAMPLE") && frame % 10 == 5) sample();
    }
    if (getenv("PORT_DUMPBIN")) { /* addr,len,file: memory as bytes at the end, for comparing with the ROM's */
        char spec[512]; snprintf(spec, sizeof spec, "%s", getenv("PORT_DUMPBIN"));
        char *a = strtok(spec, ","), *n = strtok(NULL, ","), *path = strtok(NULL, ",");
        FILE *f = a && n && path ? fopen(path, "wb") : NULL;
        if (f) { fwrite(M + strtoul(a, NULL, 16), 1, strtoul(n, NULL, 0), f); fclose(f); }
    }
    if (getenv("PORT_DUMP")) { /* addr,len: memory as text at the end */
        uint32_t a = (uint32_t)strtoul(getenv("PORT_DUMP"), NULL, 16);
        int n = strchr(getenv("PORT_DUMP"), ',') ? atoi(strchr(getenv("PORT_DUMP"), ',') + 1) : 256;
        for (int i = 0; i < n; i++) { uint8_t c = M[a + i]; fputc(c >= 32 && c < 127 ? c : (c == 10 ? 10 : '.'), stderr); }
        fputc('\n', stderr);
    }
    fprintf(stderr, "after %.1f s: %lu retraces, %lu DMAs, %lu graphics and %lu audio tasks, %lu buffer swaps, "
            "frame buffer %08X\n", seconds, vi_count, dmas, gfx_tasks, audio_tasks, swaps, frame_buffer);
    return 0;
}

/* An indirect call to nothing: say which, before the trap stops the thread. */
#include <execinfo.h>
#ifdef __APPLE__
#include <mach-o/dyld.h>
#endif
void port_trace(void) {
    void *frames[24];
    int n = backtrace(frames, 24);
#ifdef __APPLE__
    fprintf(stderr, "atos -o game -l %p", (void *)_dyld_get_image_header(0));
    for (int i = 1; i < n; i++) fprintf(stderr, " %p", frames[i]);
    fputc('\n', stderr);
#else
    backtrace_symbols_fd(frames + 1, n - 1, 2);
#endif
}
void port_bad_call(uint32_t index) {
    fprintf(stderr, "a call through a function pointer to table entry %u, which holds no function\n", index);
    port_trace();
}
