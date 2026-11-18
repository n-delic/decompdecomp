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
 *
 * PORT_WEB: the browser build (port/wasm/web.py). This file is compiled to WebAssembly beside the game's module,
 * sharing its memory (based at 0); its threads are coroutines (port/wasm/web/co.c), the game's functions it calls
 * go through the page's table (the generated game.h), and the page drives the retrace instead of main().
 */
#include <math.h>
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
#include "wasm-rt-exceptions.h"
#include "wasm-rt-impl.h"

struct w2c_env { int unused; };
static struct w2c_env env;
static w2c_game game;
w2c_game *port_game(void) { return &game; } /* for a game's own host file (games/<its ROM's SHA-1>.c) */
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
/* recompiled C for functions with no C yet (port/wasm/recomp.py's bridges.c, when the build has it): a function
   pointer's bridge, and each host thread's MIPS stack for it */
__attribute__((weak)) void *port_recomp_pointer(uint32_t address) { (void)address; return 0; }
/* a call through a pointer in recompiled code is a safe point too, as port_func is for the game's C: a racing game's
   boot thread loops forever calling its frame hook through a pointer, and recompiled, it never let the threads the
   retrace woke run (nothing was drawn) */
void port_safe_point(void) { port_preempt(); }
__attribute__((weak)) void port_recomp_thread(int number) { (void)number; }
void *port_mips_pointer_as(uint32_t address, const char *type); /* mips.c: run from the ROM by the interpreter */
/* The function at `address`, called as `type` says (CALL_INDIRECT's C type, as text: bemem.py), which a function
   with no C, interpreted, needs to take its float arguments where the call puts them. */
void *port_func_as(uint32_t address, const char *type) {
    /* LLVM calls some functions through the table when the call's types differ from the function's only as
       pointer and integer: an index, never a console address (those are 0x80000000 and up) */
    port_preempt();
#ifdef PORT_WEB
    return (void *)(uintptr_t)address; /* not used: the game's own port_icall finds its functions (web/be.c) */
#endif
    wasm_rt_funcref_table_t *table = &game.w2c_0x5F_indirect_function_table;
    if (address < table->size && table->data[address].func) return (void *)table->data[address].func;
    unsigned lo = 0, hi = port_func_count;
    while (lo < hi) {
        unsigned mid = (lo + hi) / 2;
        if (port_funcs[mid].address < address) lo = mid + 1; else hi = mid;
    }
    if (lo < port_func_count && port_funcs[lo].address == address) return port_funcs[lo].func;
    void *recompiled = port_recomp_pointer(address); /* a function with no C yet, run as recompiled C */
    if (recompiled) return recompiled;
    void *interpreted = port_mips_pointer_as(address, type); /* or its own instructions, interpreted */
    if (interpreted) return interpreted;
    fprintf(stderr, "a call through a function pointer to %08X, where no function of the game's C is\n", address);
    port_trace();
    wasm_rt_trap(WASM_RT_TRAP_CALL_INDIRECT);
}
void *port_func(uint32_t address) { return port_func_as(address, 0); }

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
#ifdef PORT_WEB
#define self (*(PortThread **)port_web_self_slot()) /* each coroutine's own */
uint32_t port_web_game_sp(void);
void port_web_set_game_sp(uint32_t sp);
#define GAME_SP port_web_game_sp()
#define SET_GAME_SP(v) port_web_set_game_sp(v)
#else
static _Thread_local PortThread *self;
#define GAME_SP game.w2c_0x5F_stack_pointer0x2E1
#define SET_GAME_SP(v) (game.w2c_0x5F_stack_pointer0x2E1 = (v))
#endif
/* the running game thread's number (-1 before any: the boot), for the interpreter's stack of its own (mips.c) */
int port_thread_number(void) { return self ? self->number : -1; }
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
    if (me->has_run) me->sp = GAME_SP; /* a new thread's is its fresh stack */
    me->has_run = 1;
    while (running != me || me->state != READY) pthread_cond_wait(&me->turn, &lock);
    SET_GAME_SP(me->sp);
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

/* The running thread's code jumps to itself (mips.c): it can't do anything again, so it gives the CPU up for good
   instead of spinning, as the console's idle thread does until an interrupt switches away. Stopped, not waiting:
   no message wakes it, only osStartThread, which would send it back to the same jump. The boot code has no thread
   to give up and returns. */
void port_idle_forever(void) {
    if (!self) return;
    pthread_mutex_lock(&lock);
    LOG("thread %d idles for good (its code jumps to itself): parked", self->number);
    self->state = STOPPED;
    schedule();
    wait_turn(self);
    pthread_mutex_unlock(&lock);
}

/* the game's own C's endless empty loop, rewritten to call this (build.py, EMPTY_FOREVER) */
void w2c_env_port_idle_forever(struct w2c_env *e) { (void)e; port_idle_forever(); }

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
    port_recomp_thread(me->number); /* its MIPS stack, for recompiled code it runs */
    wasm_rt_trap_t trap = wasm_rt_impl_try();
    if (trap) {
        fprintf(stderr, "thread %d stopped: %s\n", me->number, wasm_rt_strerror(trap));
        _exit(3);
    }
#ifdef PORT_WEB
    w2c_game_port_call1(&game, me->entry, me->arg);
#else
    ((void (*)(void *, uint32_t))port_func(me->entry))(&game, me->arg);
#endif
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
/* The rest of libultra's thread calls the port kit says the host provides (consoles/n64/port.py): the game's own
   copies read libultra's thread list, which the host doesn't keep, so they'd answer for no thread. */
u32 w2c_env_osGetThreadId(struct w2c_env *e, u32 t) {
    (void)e;
    pthread_mutex_lock(&lock);
    PortThread *p = t ? find(t) : self;
    u32 id = p ? (u32)p->id : t ? be32(t + 20) : 0; /* (OSThread's id, for one the host never started) */
    pthread_mutex_unlock(&lock);
    return id;
}
u32 w2c_env_osGetThreadPri(struct w2c_env *e, u32 t) {
    (void)e;
    pthread_mutex_lock(&lock);
    PortThread *p = t ? find(t) : self;
    u32 pri = p ? (u32)p->pri : t ? be32(t + 4) : 0;
    pthread_mutex_unlock(&lock);
    return pri;
}
void w2c_env_osYieldThread(struct w2c_env *e) {
    (void)e;
    pthread_mutex_lock(&lock);
    if (self) { self->ready_at = ++ticks; schedule(); wait_turn(self); }
    pthread_mutex_unlock(&lock);
}
void w2c_env_osStopThread(struct w2c_env *e, u32 t) {
    (void)e;
    pthread_mutex_lock(&lock);
    PortThread *p = t ? find(t) : self;
    if (p && p->state != DONE) {
        p->state = STOPPED; /* osStartThread makes it ready again */
        if (p == self) { schedule(); wait_turn(self); }
        else if (p == running) schedule();
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
/* a game's DMA request queue, watched for requests to outside RAM (a game's own host file sets it; none by default) */
__attribute__((weak)) uint32_t port_dma_watch_queue = 0;
u32 w2c_env_osSendMesg(struct w2c_env *e, u32 mq, u32 msg, u32 flag) {
    (void)e;
    if (mq && mq == port_dma_watch_queue && be32(msg + 4) < 0x80000000u) { /* a DMA request to outside RAM: who asks */
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
#ifdef PORT_MEM32
/* A 32-bit computer maps only the registers' window (miyoo/mem32.c): the rest of the console's I/O space the game
   touches (the PIF's RAM at 0xBFC007C0, the cartridge's domain at 0xB0000000) is beyond what a 32-bit program may
   map, so it lands in the 1 MB after the window, by its low 20 bits. As in the 4 GB build, it's memory no one else
   reads. */
#define IO_FAR(addr) ((addr) >= 0xA4900000u ? 0xA4900000u + ((uint32_t)(addr) & 0xFFFFFu) : (addr))
#else
#define IO_FAR(addr) (addr)
#endif
int port_ai_register(uint32_t addr, int write, uint32_t value, uint32_t *read); /* audio.c */
static int si_register(uint32_t addr, int write, uint32_t value, uint32_t *read); /* below, with the PIF */
static uint32_t rom32(size_t o); /* below, with the boot */
uint64_t port_io_load(uint64_t addr) {
    if (addr < 0xA0800000u) return addr - 0x20000000u; /* RAM, uncached: the same RAM */
    if (addr == SP_STATUS) { wbe32(IO_SCRATCH, 1); return IO_SCRATCH; } /* halted: a task finishes at once */
    uint32_t v;
    if (port_ai_register((uint32_t)addr, 0, 0, &v) || si_register((uint32_t)addr, 0, 0, &v)) { wbe32(IO_SCRATCH, v); return IO_SCRATCH; }
    if (addr >= 0xB0000000u && addr < 0xBFC00000u) { /* the cartridge, read directly (a racing game checks its own word) */
        wbe32(IO_SCRATCH, rom32((size_t)((addr - 0xB0000000u) & ~3u)));
        return IO_SCRATCH + (addr & 3);
    }
    return IO_FAR(addr);
}
uint64_t port_io_store(uint64_t addr, uint64_t value) {
    if (addr < 0xA0800000u) return addr - 0x20000000u;
    if (addr == SP_STATUS) return IO_SCRATCH + 4; /* a command (clear halt, set a signal): nothing to keep */
    if (port_ai_register((uint32_t)addr, 1, (uint32_t)value, NULL) || si_register((uint32_t)addr, 1, (uint32_t)value, NULL))
        return IO_SCRATCH + 4;
    return IO_FAR(addr);
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
/* The VI's mode, Y scale and filters go to the renderer (render.c), which shows each frame at its size. */
void port_vi_mode(uint32_t mode);
void port_vi_yscale(float s);
void port_vi_features(uint32_t f);
void w2c_env_osViSetMode(struct w2c_env *e, u32 mode) { (void)e; LOG("osViSetMode(%08X)", mode); port_vi_mode(mode); }
void w2c_env_osViSetYScale(struct w2c_env *e, f32 s) { (void)e; port_vi_yscale(s); }
void w2c_env_osViBlack(struct w2c_env *e, u32 on) { (void)e; (void)on; }
void w2c_env_osViSetSpecialFeatures(struct w2c_env *e, u32 f) { (void)e; port_vi_features(f); }
void port_frame_shown(uint32_t fb, unsigned long n); /* render.c: saves it when asked */
void port_vi_swap(uint32_t fb) { frame_buffer = fb; swaps++; port_frame_shown(fb, swaps); } /* (games/: a game's own name) */
void w2c_env_osViSwapBuffer(struct w2c_env *e, u32 fb) { (void)e; port_vi_swap(fb); }
u32 w2c_env_osViGetCurrentFramebuffer(struct w2c_env *e) { (void)e; return frame_buffer; }
u32 w2c_env_osViGetNextFramebuffer(struct w2c_env *e) { (void)e; return frame_buffer; }

static void port_timers(void);
/* PORT_RAM_DUMPS="vi:file,vi:file": the game's RAM (8 MB from 0x80000000, big-endian) written at those retraces,
   laid out as the reversing emulator writes its dumps at the same retraces (consoles/n64/emulator, EMU_DUMP), so the
   two compare word by word and tools/n64_frame_compare.py draws each one's frame. PORT_FRAME_DUMPS="n:file,...":
   the same as the game hands over its nth graphics task (its nth frame, its display list finished), as the
   emulator's video plugin writes its own (EMU_FRAME_DUMP): the console's frame and the port's at the same point in
   the game, however long each took to get there. */
static void ram_dumps(const char *var, unsigned long now) {
    for (const char *p = getenv(var); p && *p;) {
        char *end;
        unsigned long at = strtoul(p, &end, 10);
        if (*end != ':') return;
        const char *path = end + 1, *next = strchr(path, ',');
        size_t n = next ? (size_t)(next - path) : strlen(path);
        if (at == now && n < 1024) {
            char name[1024];
            memcpy(name, path, n); name[n] = 0;
            FILE *f = fopen(name, "wb");
            if (f) { fwrite(M + 0x80000000u, 1, 0x800000, f); fclose(f); }
            else fprintf(stderr, "%s: can't write %s\n", var, name);
        }
        p = next ? next + 1 : path + n;
    }
}
static void retrace(void) {
    vi_count++;
    ram_dumps("PORT_RAM_DUMPS", vi_count);
    port_timers();
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
/* libultra's newer cartridge API (2.0F on): a game asks osCartRomInit for the cartridge's handle, then sends each
   transfer as an OSIoMesg (its dramAddr at +8, devAddr at +12, size at +16, its queue at +4) to osEPiStartDma. A
   racing game's main thread waited for ever on the reply the stub never sent. The handle is the host's, in low
   memory below the game's data: its domain 0 and base 0xB0000000, as libultra's own handle has them. */
#define CART_HANDLE 0x000A1000u
u32 w2c_env_osCartRomInit(struct w2c_env *e) {
    (void)e;
    if (!be32(CART_HANDLE + 12)) {
        memset(M + CART_HANDLE, 0, 0x74);
        M[CART_HANDLE + 4] = 2;            /* type: DEVICE_TYPE_INIT, the cartridge */
        wbe32(CART_HANDLE + 12, 0xB0000000u); /* baseAddress */
    }
    return CART_HANDLE;
}
u32 w2c_env_osEPiStartDma(struct w2c_env *e, u32 handle, u32 mb, u32 dir) {
    (void)handle;
    return w2c_env_osPiStartDma(e, mb, 0, dir, be32(mb + 12) & 0x0FFFFFFFu, be32(mb + 8), be32(mb + 16), be32(mb + 4));
}
u32 w2c_env_osEPiReadIo(struct w2c_env *e, u32 handle, u32 dev, u32 data) {
    (void)e; (void)handle;
    uint32_t at = dev & 0x0FFFFFFCu;
    wbe32(data, at + 4 <= rom_size ? (uint32_t)rom[at] << 24 | rom[at + 1] << 16 | rom[at + 2] << 8 | rom[at + 3] : 0);
    return 0;
}
u32 w2c_env_osEPiWriteIo(struct w2c_env *e, u32 handle, u32 dev, u32 data) { (void)e; (void)handle; (void)dev; (void)data; return 0; }
u32 w2c_env_osVirtualToPhysical(struct w2c_env *e, u32 a) { (void)e; return a & 0x1FFFFFFF; }

/* ---------------------------------------------------------------- RSP tasks: graphics (render.c) and audio (audio.c) */

static unsigned long gfx_tasks, audio_tasks;
void port_audio_task(uint32_t task); /* audio.c */
void w2c_env_osSpTaskStartGo(struct w2c_env *e, u32 task) {
    (void)e;
    uint32_t type = be32(task);
    if (type == 1) gfx_tasks++; else audio_tasks++;
    if (type == 1) ram_dumps("PORT_FRAME_DUMPS", gfx_tasks);
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
/* osSpTaskStart is osSpTaskLoad then osSpTaskStartGo: the task runs whole at StartGo, so loading it is nothing */
void w2c_env_osSpTaskLoad(struct w2c_env *e, u32 task) { (void)e; (void)task; }

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
   Each entry sets the state from that frame on: buttons joined by +, "-" for none, SX/SY for the stick. With
   PORT_INPUT_CLOCK=vi the times are retraces instead, as the reversing emulator's scripts count them (EMU_INPUT), and
   with PORT_INPUT_CLOCK=frames the game's frames (the graphics tasks it has handed over, EMU_INPUT_CLOCK=frames), so
   one script drives both the same way. */
static int8_t stick_x, stick_y;
int port_live_input(uint16_t *buttons, int8_t *x, int8_t *y); /* live.c: the keyboard, with PORT_LIVE */
void port_live_start(void);
void port_live_pump(void); /* live.c: the main thread's turn at the window (PORT_WINDOW), every retrace */
void port_live_app(const char *argv0); /* live.c: an exported app's settings, when no ROM is named */
const char *port_live_rom(void);        /* live.c: the ROM the player chooses in the window */
static void script_input(void);
static void scripted_input(void) {
    uint16_t b; int8_t x, y;
    script_input();
    if (port_live_input(&b, &x, &y) && (b || x || y)) { buttons = b; stick_x = x; stick_y = y; } /* a key held wins */
}
static void script_input(void) {
    static const char *script;
    static int loaded;
    static int by_vi; /* the clock: 0 frames shown, 1 retraces, 2 graphics tasks */
    if (!loaded) {
        loaded = 1; script = getenv("PORT_INPUT");
        const char *clock = getenv("PORT_INPUT_CLOCK");
        by_vi = !clock ? 0 : !strcmp(clock, "vi") ? 1 : !strcmp(clock, "frames") ? 2 : 0;
    }
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
        if (at <= (by_vi == 1 ? vi_count : by_vi == 2 ? gfx_tasks : swaps)) { buttons = b; stick_x = (int8_t)sx; stick_y = (int8_t)sy; }
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
#ifdef PORT_WEB /* the page keeps it (the browser's storage) */
__attribute__((import_module("port_web"), import_name("eeprom_load"))) void port_web_eeprom_load(uint8_t *chip, uint32_t size);
__attribute__((import_module("port_web"), import_name("eeprom_save"))) void port_web_eeprom_save(const uint8_t *chip, uint32_t size);
static void eeprom_load(void) {
    static int loaded;
    if (loaded) return;
    loaded = 1;
    memset(eeprom, 0xFF, sizeof eeprom);
    port_web_eeprom_load(eeprom, sizeof eeprom);
}
static void eeprom_write(int block, const uint8_t *data) {
    memcpy(eeprom + block * 8, data, 8);
    port_web_eeprom_save(eeprom, sizeof eeprom);
}
#else
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
#endif

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
/* The serial interface's registers, for a game whose own SI code runs (recompiled or interpreted: library detection
   didn't name its __osSiRawStartDma, as in a racing game whose main thread then waited for the SI event for ever):
   SI_DRAM_ADDR, then a write to SI_PIF_ADDR_RD64B or _WR64B moves the PIF's 64 bytes and raises the event, as the
   host's own __osSiRawStartDma does. SI_STATUS is never busy. */
static uint32_t si_dram;
static int si_register(uint32_t addr, int write, uint32_t value, uint32_t *read) {
    if (addr < 0xA4800000u || addr > 0xA4800018u) return 0;
    switch (addr & 0x1F) {
    case 0x00: if (write) si_dram = value & 0x7FFFFF; else *read = si_dram; break;
    case 0x04: if (write) w2c_env_0x5F_osSiRawStartDma(NULL, 0, 0x80000000u | si_dram); else *read = 0; break; /* PIF to RAM */
    case 0x10: if (write) w2c_env_0x5F_osSiRawStartDma(NULL, 1, 0x80000000u | si_dram); else *read = 0; break; /* RAM to PIF */
    default: if (read) *read = 0; /* SI_STATUS: not busy, no error; a write clears the interrupt */
    }
    return 1;
}
u32 w2c_env_osContStartQuery(struct w2c_env *e, u32 mq) { (void)e; si_done(mq); return 0; }
void w2c_env_osContGetQuery(struct w2c_env *e, u32 status) {
    (void)e;
    for (int i = 0; i < 4; i++) { wbe16(status + 4 * i, i == 0 ? 5 : 0); M[status + 4 * i + 2] = 0; M[status + 4 * i + 3] = i == 0 ? 0 : 8; }
    return;
}
u32 w2c_env_osEepromProbe(struct w2c_env *e, u32 mq) { (void)e; (void)mq; return 1; } /* EEPROM_TYPE_4K */
/* libultra's EEPROM calls, which the host provides as it provides osEepromProbe: the game's own copies go out of the
   build with the rest of what the host replaces (port/wasm/play.py), so a racing game's osEepromLongRead, whose C
   had reached the chip through the serial bus above, became a stub that read nothing: its menus said its default
   car broke its class's rules, and its car selection never let it race. `address` is the 8-byte block. */
static u32 eeprom_read(u32 address, u32 buffer, u32 bytes) {
    eeprom_load();
    for (u32 i = 0; i < bytes && address * 8 + i < sizeof eeprom; i++) M[buffer + i] = eeprom[address * 8 + i];
    return 0;
}
static u32 eeprom_write_bytes(u32 address, u32 buffer, u32 bytes) {
    eeprom_load();
    for (u32 b = 0; b * 8 < bytes && address + b < sizeof eeprom / 8; b++) {
        uint8_t block[8];
        memcpy(block, eeprom + (address + b) * 8, 8);
        for (u32 i = 0; i < 8 && b * 8 + i < bytes; i++) block[i] = M[buffer + b * 8 + i];
        eeprom_write((int)(address + b), block);
    }
    return 0;
}
u32 w2c_env_osEepromRead(struct w2c_env *e, u32 mq, u32 address, u32 buffer) {
    (void)e; (void)mq; return eeprom_read(address & 0xFF, buffer, 8);
}
u32 w2c_env_osEepromWrite(struct w2c_env *e, u32 mq, u32 address, u32 buffer) {
    (void)e; (void)mq; return eeprom_write_bytes(address & 0xFF, buffer, 8);
}
u32 w2c_env_osEepromLongRead(struct w2c_env *e, u32 mq, u32 address, u32 buffer, u32 bytes) {
    (void)e; (void)mq; return eeprom_read(address & 0xFF, buffer, bytes);
}
u32 w2c_env_osEepromLongWrite(struct w2c_env *e, u32 mq, u32 address, u32 buffer, u32 bytes) {
    (void)e; (void)mq; return eeprom_write_bytes(address & 0xFF, buffer, bytes);
}
u32 w2c_env_osMotorInit(struct w2c_env *e, u32 mq, u32 pfs, u32 ch) { (void)e; (void)mq; (void)pfs; (void)ch; return 1; }
u32 w2c_env_0x5F_osMotorAccess(struct w2c_env *e, u32 pfs, u32 on) { (void)e; (void)pfs; (void)on; return 1; }
u32 w2c_env_osPfsIsPlug(struct w2c_env *e, u32 mq, u32 pattern) { (void)e; (void)mq; M[pattern] = 0; return 0; }
u32 w2c_env_osPfsInitPak(struct w2c_env *e, u32 mq, u32 pfs, u32 ch) { (void)e; (void)mq; (void)pfs; (void)ch; return 1; }
/* no Controller Pak (osPfsInitPak said so): PFS_ERR_NOPACK, as a console with none answers */
u32 w2c_env_osPfsNumFiles(struct w2c_env *e, u32 pfs, u32 max, u32 used) { (void)e; (void)pfs; (void)max; (void)used; return 1; }
u32 w2c_env_osPfsFreeBlocks(struct w2c_env *e, u32 pfs, u32 left) { (void)e; (void)pfs; (void)left; return 1; }

/* ---------------------------------------------------------------- the rest of libultra and libc */

void port_ai_frequency(uint32_t hz); /* audio.c */
u32 w2c_env_osAiSetFrequency(struct w2c_env *e, u32 f) { (void)e; LOG("osAiSetFrequency(%u)", f); port_ai_frequency(f); return f; }
void w2c_env_osInvalDCache(struct w2c_env *e, u32 a, u32 n) { (void)e; (void)a; (void)n; }
void w2c_env_osInvalICache(struct w2c_env *e, u32 a, u32 n) { (void)e; (void)a; (void)n; }
void w2c_env_osWritebackDCache(struct w2c_env *e, u32 a, u32 n) { (void)e; (void)a; (void)n; }
void w2c_env_osWritebackDCacheAll(struct w2c_env *e) { (void)e; }
u32 w2c_env_osSetIntState(struct w2c_env *e, u32 s) { (void)e; return s; }
static struct timespec start;
static int64_t time_offset; /* osSetTime's */
u64 w2c_env_osGetTime(struct w2c_env *e) {
    (void)e;
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    double s = (now.tv_sec - start.tv_sec) + (now.tv_nsec - start.tv_nsec) / 1e9;
    return (u64)((int64_t)(s * 46875000.0) + time_offset); /* the CPU's count rate */
}
void w2c_env_osSetTime(struct w2c_env *e, u64 t) { time_offset = 0; time_offset = (int64_t)t - (int64_t)w2c_env_osGetTime(e); }
/* osSetTimer: its message when the countdown (in the CPU's count, as osGetTime) has run, then every interval if it
   has one; checked at each retrace (port_timers), so a timer fires to the nearest 1/60 s. */
static struct { u32 timer, mq, msg; u64 due, interval; } timers[32];
u32 w2c_env_osSetTimer(struct w2c_env *e, u32 t, u64 countdown, u64 interval, u32 mq, u32 msg) {
    u64 now = w2c_env_osGetTime(e);
    pthread_mutex_lock(&lock);
    int slot = -1;
    for (int i = 0; i < 32; i++) if (timers[i].timer == t || (slot < 0 && !timers[i].timer)) { slot = i; if (timers[i].timer == t) break; }
    if (slot >= 0) {
        timers[slot].timer = t; timers[slot].mq = mq; timers[slot].msg = msg;
        timers[slot].due = now + (countdown ? countdown : interval); timers[slot].interval = interval;
    } else LOG("osSetTimer: more than 32 timers at once, %08X ignored", t);
    pthread_mutex_unlock(&lock);
    return 0;
}
u32 w2c_env_osStopTimer(struct w2c_env *e, u32 t) {
    (void)e;
    pthread_mutex_lock(&lock);
    int found = -1;
    for (int i = 0; i < 32; i++) if (timers[i].timer == t) { timers[i].timer = 0; found = 0; }
    pthread_mutex_unlock(&lock);
    return (u32)found;
}
static void port_timers(void) {
    u64 now = w2c_env_osGetTime(NULL);
    struct { u32 mq, msg; } due[32];
    int n = 0;
    pthread_mutex_lock(&lock);
    for (int i = 0; i < 32; i++) {
        if (!timers[i].timer || now < timers[i].due) continue;
        due[n].mq = timers[i].mq; due[n++].msg = timers[i].msg;
        if (timers[i].interval) timers[i].due += timers[i].interval; else timers[i].timer = 0;
    }
    pthread_mutex_unlock(&lock);
    for (int i = 0; i < n; i++) if (due[i].mq) put(due[i].mq, due[i].msg, 0, 0); /* (put takes the lock itself) */
}
/* The audio interface as libultra drives it, through the registers audio.c answers: what's left of the buffer
   playing, and the next buffer (with libultra's own step back of 8 KB after a buffer that ends on an 8 KB boundary,
   which audio.c undoes as the hardware does) */
int port_ai_register(uint32_t addr, int write, uint32_t value, uint32_t *read); /* audio.c */
u32 w2c_env_osAiGetLength(struct w2c_env *e) { (void)e; u32 v = 0; port_ai_register(0xA4500004u, 0, 0, &v); return v; }
u32 w2c_env_osAiSetNextBuffer(struct w2c_env *e, u32 buf, u32 size) {
    (void)e;
    static int bug;
    u32 status = 0;
    port_ai_register(0xA450000Cu, 0, 0, &status);
    if (status & 0x80000000u) return (u32)-1; /* full: a buffer already waits */
    u32 at = buf - (bug ? 0x2000u : 0);
    bug = ((buf + size) & 0x1FFF) == 0;
    port_ai_register(0xA4500000u, 1, at & 0x1FFFFFFFu, NULL);
    port_ai_register(0xA4500004u, 1, size, NULL);
    return 0;
}
/* the older API's word of the cartridge, as osEPiReadIo reads it */
u32 w2c_env_osPiReadIo(struct w2c_env *e, u32 dev, u32 data) { return w2c_env_osEPiReadIo(e, 0, dev, data); }
u32 w2c_env_osPiWriteIo(struct w2c_env *e, u32 dev, u32 value) { return w2c_env_osEPiWriteIo(e, 0, dev, value); }
void w2c_env_osViSetXScale(struct w2c_env *e, f32 x) { (void)e; (void)x; } /* (the frame is shown whole) */
void w2c_env_osMapTLB(struct w2c_env *e, u32 index, u32 pm, u32 va, u32 even, u32 odd, u32 asid) {
    (void)e; (void)pm; (void)asid;
    static int said;
    if (!said++) fprintf(stderr, "osMapTLB(%u, %08X -> %08X/%08X): the port has no TLB, so mapped addresses aren't translated\n", index, va, even, odd);
}
u32 w2c_env_memcpy(struct w2c_env *e, u32 d, u32 s, u32 n) { (void)e; memmove(M + d, M + s, n); return d; }
u32 w2c_env_memset(struct w2c_env *e, u32 d, u32 c, u32 n) { (void)e; memset(M + d, (int)c, n); return d; }
u32 w2c_env_strlen(struct w2c_env *e, u32 p) { (void)e; return (u32)strlen((char *)M + p); }
void w2c_env_0x5F_osCopyBytes(struct w2c_env *e, u32 s, u32 d, u32 n) { (void)e; memmove(M + d, M + s, n); }
u32 w2c_env_abs(struct w2c_env *e, u32 x) { (void)e; return (u32)abs((int32_t)x); }
/* What stays hand-written assembly in most games, answered by its library name: a game's C calls it by its automatic
   name, and the build renames the call to the name library detection found (play.library_names). Without these a
   racing game's bzero was stubbed to nothing. The TLB, the status register and interrupts are the host's own. */
void w2c_env_bzero(struct w2c_env *e, u32 p, u32 n) { (void)e; memset(M + p, 0, n); }
u32 w2c_env_bcmp(struct w2c_env *e, u32 a, u32 b, u32 n) { (void)e; return memcmp(M + a, M + b, n) != 0; }
void w2c_env_bcopy(struct w2c_env *e, u32 s, u32 d, u32 n) { (void)e; memmove(M + d, M + s, n); }
/* the same three by the labels libultra's assembly also gives them: a packed shooter's C called _bzero, so its
   clears were stubbed to nothing */
void w2c_env_0x5Fbzero(struct w2c_env *e, u32 p, u32 n) { w2c_env_bzero(e, p, n); }
void w2c_env_0x5Fbcopy(struct w2c_env *e, u32 s, u32 d, u32 n) { w2c_env_bcopy(e, s, d, n); }
u32 w2c_env_0x5Fbcmp(struct w2c_env *e, u32 a, u32 b, u32 n) { return w2c_env_bcmp(e, a, b, n); }
/* the handler libultra installs for an RCP interrupt: the host raises the console's interrupts itself. Two
   arguments, as the older libultra a packed shooter's C calls it with (the stack argument came later; a build's
   declarations must agree with the game's) */
void w2c_env_0x5F_osSetHWIntrRoutine(struct w2c_env *e, u32 irq, u32 handler) { (void)e; (void)irq; (void)handler; }
f32 w2c_env_sqrtf(struct w2c_env *e, f32 x) { (void)e; return sqrtf(x); }
u32 w2c_env_osGetCount(struct w2c_env *e) { return w2c_env_0x5F_builtin_mips_read_count(e); }
void w2c_env_osMapTLBRdb(struct w2c_env *e) { (void)e; }
void w2c_env_osUnmapTLBAll(struct w2c_env *e) { (void)e; }
void w2c_env_osSetTLBASID(struct w2c_env *e, u32 asid) { (void)e; (void)asid; }
u32 w2c_env_0x5F_osGetSR(struct w2c_env *e) { (void)e; return cop0_status; } /* as __builtin_mips_read_status */
void w2c_env_0x5F_osSetSR(struct w2c_env *e, u32 v) { (void)e; cop0_status = v; }
u32 w2c_env_0x5F_osGetCause(struct w2c_env *e) { (void)e; return 0; }
/* the RCP interrupts a game masks: the host raises them itself, so the mask is only kept to be returned (a packed
   shooter's osSetIntMask is hand-written assembly, and was skipped) */
u32 w2c_env_osSetIntMask(struct w2c_env *e, u32 mask) { (void)e; static u32 now = 0x3FFFFFu; u32 was = now; now = mask; return was; }
u32 w2c_env_0x5F_osDisableInt(struct w2c_env *e) { (void)e; return 1; }
void w2c_env_0x5F_osRestoreInt(struct w2c_env *e, u32 v) { (void)e; (void)v; }
u32 w2c_env_0x5F_osGetFpcCsr(struct w2c_env *e) { (void)e; return fcsr; }
u32 w2c_env_0x5F_osSetFpcCsr(struct w2c_env *e, u32 v) { return w2c_env_0x5F_builtin_mips_exchange_fcsr(e, v); }
/* What only one game needs (functions it names by its own addresses, its old names): games/<its ROM's SHA-1, 12
   hex digits>.c, which the build compiles for that game alone (port/wasm/build.py). */

/* ---------------------------------------------------------------- start */

/* The boot, as the console does it, read from the ROM: so any game starts, not only the one the host was written
   for (it named that game's first C function and where its image ends). The boot chip copies the 1 MB after the
   header to the entry address the header names; the game's entry routine there zeroes its BSS, sets its stack and
   jumps to its first C function. The routine is read, not run: its lui/addiu/ori constants give the BSS (the
   address its first `sw $zero` stores at, the size its loop counts down or the end it counts up to) and the jump's
   target. A racing game loads at 0x80000400 and is PAL; another at 0x80025C00, NTSC. */
static struct { uint32_t entry, target, bss, bss_size; int tv; } boot;
static uint32_t rom32(size_t o) { return o + 4 <= rom_size ? (uint32_t)rom[o] << 24 | rom[o + 1] << 16 | rom[o + 2] << 8 | rom[o + 3] : 0; }
/* The boot chip (CIC) a cartridge was made for, by its boot code's CRC-32 (ROM 0x40 to 0x1000): CIC-6103 and
   CIC-6106 start the game 0x100000 and 0x200000 below the header's entry address, so the header of a racing game
   made for the 6103 says 0x80100400 where its code runs at 0x80000400. Taken at its word, the code was loaded a
   megabyte off and the functions with no C had no instructions to run. */
static uint32_t boot_crc(void) {
    uint32_t crc = 0xFFFFFFFFu;
    for (size_t i = 0x40; i < 0x1000 && i < rom_size; i++) {
        crc ^= rom[i];
        for (int k = 0; k < 8; k++) crc = (crc >> 1) ^ (0xEDB88320u & -(crc & 1));
    }
    return ~crc;
}
/* What each boot chip's boot code leaves behind, read from its instructions (the PAL chips' are the same code):
   where it starts the game; the part of itself it copies into RAM to run from there (ROM offsets, and where in RAM);
   the word it fills the RSP's two memories with; the ID it leaves in the first word of the RSP's instruction memory
   and in osCicId (0x80000310), 0 for none. Games check these as the console leaves them: a racing game made for the
   6103 turns its screen upside down when the cartridge's word at 0x578 isn't its own, mirrors its tracks when RAM
   0x200 isn't the boot code's, and pauses in its world when the instruction memory doesn't start with 6103. The
   6106's copy is scrambled as it goes, so it has none here yet. */
static const struct boot_chip {
    uint32_t crc, entry_less, copy_from, copy_to, copy_at, sp_fill, id;
} boot_chips[] = {
    {0x90BB6CB5u, 0, 0x4C0, 0x774, 0, 0, 0},                  /* CIC-6102 (7101) */
    {0x009E9EA3u, 0, 0x4C0, 0x750, 0, 0, 0},                  /* CIC-7102 */
    {0x0B050EE0u, 0x100000u, 0x4C0, 0x768, 0, 0xFFFFFFFFu, 6103}, /* CIC-6103 (7103) */
    {0x98BC2C86u, 0, 0x554, 0x888, 4, 0xA4002000u, 6105},       /* CIC-6105 (7105) */
    {0xACC8580Au, 0x200000u, 0, 0, 0, 0, 0},                  /* CIC-6106 (7106) */
};
static const struct boot_chip *boot_chip(void) {
    uint32_t crc = boot_crc();
    for (size_t i = 0; i < sizeof boot_chips / sizeof *boot_chips; i++)
        if (boot_chips[i].crc == crc) return &boot_chips[i];
    return NULL;
}
static uint32_t boot_entry(void) {
    const struct boot_chip *chip = boot_chip();
    return rom32(8) - (chip ? chip->entry_less : 0);
}
/* RAM and the RSP's memories as the boot chip's code leaves them (above) */
static void boot_leftovers(void) {
    const struct boot_chip *chip = boot_chip();
    if (!chip) return;
    for (uint32_t o = chip->copy_from; o < chip->copy_to; o += 4) wbe32(0x80000000u + chip->copy_at + o - chip->copy_from, rom32(o));
    for (uint32_t a = 0xA4000000u; a < 0xA4002000u; a += 4) wbe32(a, chip->sp_fill); /* DMEM, then IMEM */
    if (chip->id) { wbe32(0xA4001000u, chip->id); wbe32(0x80000310u, chip->id); } /* osCicId */
}
static int read_boot(void) {
    uint32_t r[32] = {0}, pre[32] = {0};
    int base = -1;
    boot.entry = boot_entry();
    for (int i = 0; i < 64; i++) {
        uint32_t w = rom32(0x1000 + 4 * (size_t)i), op = w >> 26, rs = (w >> 21) & 31, rt = (w >> 16) & 31;
        int32_t imm = (int16_t)(w & 0xFFFF);
        if (op == 0x0F) r[rt] = (w & 0xFFFF) << 16;                                     /* lui */
        else if (op == 0x08 || op == 0x09) r[rt] = r[rs] + (uint32_t)imm;               /* addi, addiu */
        else if (op == 0x0D) r[rt] = r[rs] | (w & 0xFFFF);                              /* ori */
        else if (op == 0x2B && rt == 0 && base < 0) {                                    /* sw $zero: the BSS */
            boot.bss = r[rs] + (uint32_t)imm; base = (int)rs; memcpy(pre, r, sizeof r);
        } else if (op == 0x05 && base >= 0 && !boot.bss_size) {                         /* bne: the loop, back */
            if (rt == 0 && (int)rs != base) boot.bss_size = pre[rs];                     /* a count down */
            else if (rt) boot.bss_size = (rs == (uint32_t)base ? pre[rt] : pre[rs]) - boot.bss; /* up to an end */
        } else if (op == 0x02 || op == 0x03) {                                           /* j, jal */
            boot.target = (boot.entry & 0xF0000000u) | (w & 0x03FFFFFFu) << 2; break;
        } else if (op == 0 && (w & 0x3F) == 8 && rs != 31) { boot.target = r[rs]; break; } /* jr */
        if (op == 0x0F || op == 0x08 || op == 0x09 || op == 0x0D) r[0] = 0;
    }
    /* the header's region: Europe and Australia are PAL, Brazil MPAL, the rest NTSC (osTvType 0, 2, 1) */
    char region = rom_size > 0x3E ? (char)rom[0x3E] : 'E';
    boot.tv = strchr("DFHIPSUWXY", region) ? 0 : region == 'B' ? 2 : 1;
    return boot.entry >= 0x80000000u && boot.target >= 0x80000000u;
}
/* the console's RAM as the boot leaves it: the game's image copied in, its BSS zeroed, the values libultra reads */
static int boot_memory(void) {
    if (!read_boot()) {
        fprintf(stderr, "the ROM's entry routine (at 0x1000) didn't say where the game's C starts: entry %08X\n", boot.entry);
        return 0;
    }
    size_t image = rom_size > 0x1000 ? rom_size - 0x1000 : 0;
    if (image > 0x100000) image = 0x100000; /* the boot chip copies 1 MB */
    if ((uint64_t)boot.entry + image > 0x80800000u) image = 0x80800000u - boot.entry;
    memcpy(M + boot.entry, rom + 0x1000, image);
    if (boot.bss_size && boot.bss >= 0x80000000u && (uint64_t)boot.bss + boot.bss_size <= 0x80800000u)
        memset(M + boot.bss, 0, boot.bss_size);
    else LOG("the entry routine's BSS wasn't read: memory after the image keeps the ROM's bytes");
    boot_leftovers();
    wbe32(SP_STATUS, 1);              /* the RSP is halted until a task starts it */
    wbe32(0x80000300, (uint32_t)boot.tv); /* osTvType */
    wbe32(0x80000308, 0xB0000000);    /* osRomBase */
    wbe32(0x8000030C, 0);             /* osResetType: cold */
    wbe32(0x80000318, 0x400000);      /* osMemSize */
    LOG("boot: image at %08X, BSS %08X + %X, first C function %08X, %s", boot.entry, boot.bss, boot.bss_size,
        boot.target, boot.tv == 0 ? "PAL" : boot.tv == 2 ? "MPAL" : "NTSC");
    return 1;
}
/* the game's first C function, by its console address (the table every function pointer goes through) */
static void boot_call(void) {
    port_recomp_thread(-1);
#ifdef PORT_WEB
    w2c_game_port_call1(&game, boot.target, 0); /* as a thread's entry: through the page's table */
#else
    void (*first)(w2c_game *) = (void (*)(w2c_game *))port_func(boot.target);
    if (!first) { fprintf(stderr, "no C for the game's first function, %08X: it needs matched C to boot\n", boot.target); exit(3); }
    first(&game);
#endif
}
/* the VI retrace's period: 50 a second for PAL, 60 otherwise */
static int retrace_us(void) { return boot.tv == 0 ? 20000 : 16667; }

#ifdef PORT_WEB
/* The page's start: the ROM the player chose (in memory the page got from port_web_alloc), then the boot, as main()
   does; then the page calls port_web_retrace 50 times a second and runs whichever thread is to run between. */
__attribute__((import_module("port_web"), import_name("memory_base"))) uint8_t *port_web_memory_base(void);
__attribute__((export_name("port_web_alloc"))) void *port_web_alloc(uint32_t size) { return malloc(size); }
__attribute__((export_name("port_preempt"))) void port_web_preempt(void) { port_preempt(); }
/* Where `preempt` is, which the game's calls through pointers read themselves (web/be.c), calling port_preempt only
   when it's set */
__attribute__((export_name("port_preempt_flag"))) uint32_t port_web_preempt_flag(void) {
    return (uint32_t)(uintptr_t)&preempt;
}
/* When the page's next retrace is due (performance.now(), in ms: the host's monotonic clock, runtime.js's WASI),
   which the page sets before it runs the threads: a thread still running then gives way (port_yield). */
static double page_deadline;
__attribute__((export_name("port_web_deadline"))) void port_web_deadline(double ms) { page_deadline = ms / 1000; }
/* A game thread that spins on memory asks this every so often (web/be.c): once the page's next retrace is due (or,
   without a deadline, once it has had 10 ms), the page gets its turn, and the thread goes on where it was. Only
   that: a thread the retrace wakes that outranks it still takes over at the next safe point (port_preempt, a call
   through a pointer), as natively, never in the middle of whatever the running thread was doing between two of
   them. A fixed slice alone overran the retrace: a game whose low-priority thread never waits held the page for
   22 ms of every 20, so a PAL racing game had 44 retraces a second, and drew 22 frames where it draws 25. */
__attribute__((export_name("port_yield"))) void port_web_yield(void) {
    static double slice;
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    double now = t.tv_sec + t.tv_nsec / 1e9;
    if (page_deadline ? now < page_deadline : now - slice < 0.010) return;
    slice = now;
    if (self && running == self) {
        self->sp = GAME_SP; /* (the page doesn't keep the game's stack pointer: the host does, as in wait_turn) */
        co_yield();
        SET_GAME_SP(self->sp);
    }
}
__attribute__((export_name("port_web_boot"))) int port_web_boot(uint8_t *image, uint32_t size) {
    rom = image;
    rom_size = size;
    if (getenv("PORT_VERBOSE")) verbose = atoi(getenv("PORT_VERBOSE"));
    clock_gettime(CLOCK_MONOTONIC, &start);
    M = port_web_memory_base(); /* 0, from the page: never a constant the compiler could fold */
    port_live_start();
    if (!boot_memory()) return 1;
    boot_call();
    LOG("the game's first function returned; its threads run");
    booted = 1;
    schedule();
    return 0;
}
__attribute__((export_name("port_web_retrace"))) void port_web_retrace(void) { retrace(); }
/* the retrace's period in ms for the page (web/runtime.js), once booted: 20 for PAL, 16.67 for NTSC */
__attribute__((export_name("port_web_retrace_ms"))) double port_web_retrace_ms(void) { return retrace_us() / 1000.0; }
__attribute__((import_module("port_web"), import_name("trace"))) void port_web_trace(void);
void port_trace(void) { port_web_trace(); }
#else
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
/* A ROM in another dump's byte order (.v64: 16-bit words swapped, .n64: 32-bit words little-endian), turned into the
   cartridge's own (.z64), told by its first word: a Miyoo's card can hold any of them. */
static void rom_byte_order(uint8_t *b, size_t n) {
    if (n < 4) return;
    if (b[0] == 0x37 && b[1] == 0x80)
        for (size_t i = 0; i + 1 < n; i += 2) { uint8_t t = b[i]; b[i] = b[i + 1]; b[i + 1] = t; }
    else if (b[0] == 0x40 && b[1] == 0x12)
        for (size_t i = 0; i + 3 < n; i += 4) {
            uint8_t t = b[i]; b[i] = b[i + 3]; b[i + 3] = t; t = b[i + 1]; b[i + 1] = b[i + 2]; b[i + 2] = t;
        }
}

#include <signal.h>
#ifndef SIGUSR1 /* Windows: no signal to interrupt a thread with, so no backtraces from PORT_SAMPLE there */
#define SIGUSR1 0
#define pthread_kill(t, sig) ((void)(t), 0)
#endif
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

/* PORT_CALL=vram: one call of the game's function at vram, from a state the reversing emulator recorded
   (app/behaviour.py), instead of the game: PORT_CALL_STATE (its registers, one line as emulator/frontend.c writes
   it), PORT_CALL_RDRAM (memory, big-endian, loaded at 0x80000000), PORT_CALL_OUT (where to write <out>.rdram, the
   memory after the call, and <out>.result, "v0 v1 f0 f1 kind": what the function returns, as the invoker says). Arguments are taken as MIPS passed them: $a0-$a3, the
   stack's words after them, $f12 and $f14 (funcs.c's port_calls, port/wasm/build.py's invoker). */
typedef void (*PortInvoke)(w2c_game *, const u32 *, const u32 *, u32 *);
typedef struct { unsigned address; PortInvoke invoke; } PortCall;
extern const PortCall port_calls[];
extern const unsigned port_call_count;
static int port_call(uint32_t vram) {
    const char *state = getenv("PORT_CALL_STATE"), *in = getenv("PORT_CALL_RDRAM"), *out = getenv("PORT_CALL_OUT");
    if (!state || !in || !out) { fprintf(stderr, "PORT_CALL needs PORT_CALL_STATE, PORT_CALL_RDRAM and PORT_CALL_OUT\n"); return 2; }
    FILE *f = fopen(state, "r");
    if (!f) { perror(state); return 2; }
    unsigned pc;
    unsigned long long g[34];
    unsigned fr[32];
    if (fscanf(f, "%x", &pc) != 1) return 2;
    for (int i = 0; i < 34; i++) if (fscanf(f, "%llx", &g[i]) != 1) return 2;
    for (int i = 0; i < 32; i++) if (fscanf(f, "%x", &fr[i]) != 1) return 2;
    fclose(f);
    size_t size;
    uint8_t *bytes = read_file(in, &size);
    if (!bytes || size > 0x800000u) { fprintf(stderr, "can't read %s\n", in); return 2; }
    memcpy(M + 0x80000000u, bytes, size);
    u32 w[32], regs[32], result[5] = {0, 0, 0, 0, 0};
    for (int i = 0; i < 4; i++) w[i] = (u32)g[4 + i];
    for (int i = 4; i < 32; i++) w[i] = be32((u32)g[29] + 4u * (unsigned)i);
    for (int i = 0; i < 32; i++) regs[i] = fr[i];
    unsigned lo = 0, hi = port_call_count;
    while (lo < hi) { unsigned mid = (lo + hi) / 2; if (port_calls[mid].address < vram) lo = mid + 1; else hi = mid; }
    if (lo >= port_call_count || port_calls[lo].address != vram) {
        fprintf(stderr, "no function of the game's C at %08X\n", vram);
        return 4;
    }
    wasm_rt_trap_t trap = wasm_rt_impl_try();
    if (trap) { fprintf(stderr, "the call stopped: %s\n", wasm_rt_strerror(trap)); return 3; }
    port_calls[lo].invoke(&game, w, regs, result);
    char path[4096];
    snprintf(path, sizeof path, "%s.result", out);
    f = fopen(path, "w");
    if (!f) { perror(path); return 2; }
    fprintf(f, "%08X %08X %08X %08X %u\n", result[0], result[1], result[2], result[3], result[4]);
    fclose(f);
    snprintf(path, sizeof path, "%s.rdram", out);
    f = fopen(path, "wb");
    if (!f) { perror(path); return 2; }
    fwrite(M + 0x80000000u, 1, size, f);
    fclose(f);
    return 0;
}

int main(int argc, char **argv) {
    if (SIGUSR1) signal(SIGUSR1, sample_handler);
    if (argc < 2) port_live_app(argv[0]); /* an exported app, opened from the Finder: its settings beside it */
    const char *rom_path = argc > 1 ? argv[1] : port_live_rom();
    if (!rom_path) { fprintf(stderr, "usage: %s rom.z64 [seconds, 0: until stopped]\n", argv[0]); return 2; }
    rom = read_file(rom_path, &rom_size);
    if (!rom) { fprintf(stderr, "can't read %s\n", rom_path); return 2; }
    rom_byte_order(rom, rom_size);
    double seconds = argc > 2 ? atof(argv[2]) : getenv("PORT_LIVE") || getenv("PORT_WINDOW") ? 0 : 5; /* 0: until stopped */
    port_live_start();
    if (getenv("PORT_VERBOSE")) verbose = atoi(getenv("PORT_VERBOSE"));
    clock_gettime(CLOCK_MONOTONIC, &start);
    wasm_rt_init();
    wasm2c_game_instantiate(&game, &env);
    M = game.w2c_memory.data;
    LOG("memory: %llu MB, ROM %zu bytes", (unsigned long long)game.w2c_memory.size >> 20, rom_size);
    /* as the console's boot does: the game's code and data from the cartridge at their addresses, and the values
       the boot leaves in low memory for libultra */
    if (!boot_memory()) return 3;
    if (getenv("PORT_CALL")) return port_call((uint32_t)strtoul(getenv("PORT_CALL"), NULL, 16));
    wasm_rt_trap_t trap = wasm_rt_impl_try();
    if (trap) { fprintf(stderr, "boot stopped: %s\n", wasm_rt_strerror(trap)); return 3; }
    boot_call();
    LOG("the game's first function returned; its threads run");
    pthread_mutex_lock(&lock);
    booted = 1;
    schedule();
    pthread_mutex_unlock(&lock);
    /* the VI retrace: 50 a second for PAL, 60 for NTSC */
    for (int frame = 0; seconds <= 0 || frame < seconds * 1000000 / retrace_us(); frame++) {
        usleep((useconds_t)retrace_us());
        retrace();
        port_live_pump();
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

#endif /* PORT_WEB */
/* An indirect call to nothing: say which, before the trap stops the thread. */
#ifdef PORT_WEB
#elif defined(_WIN32)
void port_trace(void) { fprintf(stderr, "(no backtrace on Windows)\n"); }
#else
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
#endif
void port_bad_call(uint32_t index) {
    fprintf(stderr, "a call through a function pointer to table entry %u, which holds no function\n", index);
    port_trace();
}
