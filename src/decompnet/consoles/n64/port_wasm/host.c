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
#ifndef PORT_WEB
#include <setjmp.h> /* the boot code's hand-over to the game's threads (boot_idle): the browser build's boot is the page's */
#endif
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include "game.h"
#include "rt64/adapter.h"
#define DPC_BASE 0xA4100000u /* the RDP's command registers: start, end, current, status, clock, busy counters */
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

/* A game pointer in KSEG2/KSEG3 (0xC0000000 up: a game that maps its RAM at 0xE0000000 and keeps its queues, threads and
   buffers there) is read and written where the interpreter and the compiled C find it, at the KSEG0 spelling of the
   physical RAM it maps (port_tlb_canon); everything below is the console's own unmapped or low memory as before. */
uint32_t port_tlb_canon(uint32_t a);
static inline uint32_t gv(uint32_t a) { return __builtin_expect(a >= 0xC0000000u, 0) ? port_tlb_canon(a) : a; }
static uint32_t be32(uint32_t a) { uint32_t v; memcpy(&v, M + gv(a), 4); return __builtin_bswap32(v); }
static uint16_t be16(uint32_t a) { uint16_t v; memcpy(&v, M + gv(a), 2); return __builtin_bswap16(v); }
static void wbe32(uint32_t a, uint32_t v) { v = __builtin_bswap32(v); memcpy(M + gv(a), &v, 4); }
static void wbe16(uint32_t a, uint16_t v) { v = __builtin_bswap16(v); memcpy(M + gv(a), &v, 2); }
uint8_t *port_memory(void) { return M; } /* for the renderer (render.c) */
void port_render_task(uint32_t task); /* render.c: a graphics task's display list, drawn into the game's frame buffer */
void port_render_rdp_list(const uint8_t *cmds, size_t bytes); /* render.c: a list the game gives the RDP itself */

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
uint32_t port_tlb_canon(uint32_t a); /* below, with the TLB windows */
uint32_t port_tlb_call(uint32_t a);
uint32_t port_tlb_spelled(uint32_t a);
extern unsigned port_tlb_n;
void *port_func_as(uint32_t address, const char *type) {
    /* LLVM calls some functions through the table when the call's types differ from the function's only as
       pointer and integer: an index, never a console address (those are 0x80000000 and up) */
    port_preempt();
#ifdef PORT_WEB
    return (void *)(uintptr_t)address; /* not used: the game's own port_icall finds its functions (web/be.c) */
#endif
    wasm_rt_funcref_table_t *table = &game.w2c_0x5F_indirect_function_table;
    if (address < table->size && table->data[address].func) return (void *)table->data[address].func;
    /* a function in a TLB window (a thread entry, a callback: 0x100010F8) is named by the physical address it maps */
    if (port_tlb_n && (address < 0x80000000u || address >= 0xC0000000u)) address = port_tlb_canon(address);
    address = port_tlb_call(address);
    /* a function the game has C for, known by the project's name for its window address */
    uint32_t names[2] = {address, port_tlb_spelled(address)};
    for (int k = 0; k < 2 && (k == 0 || names[1] != names[0]); k++) {
        unsigned lo = 0, hi = port_func_count;
        while (lo < hi) {
            unsigned mid = (lo + hi) / 2;
            if (port_funcs[mid].address < names[k]) lo = mid + 1; else hi = mid;
        }
        if (lo < port_func_count && port_funcs[lo].address == names[k]) return port_funcs[lo].func;
    }
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
#define SHADOW_STACKS 0x82000000u /* each thread's wasm shadow stack: 1 MB each, in KSEG0 above the MIPS stacks (recomp.py: STACKS,
                                     mips.c: MIPS_STACKS). Not below 0x20000000: a game maps its code through TLB windows there
                                     (a 3-D platformer's two 4 MB pages at 0x10000000), which the game's C reads as RAM aliases. */
#define SHADOW_SIZE 0x100000u

typedef struct PortThread {
    uint32_t os, entry, arg, sp;
    int32_t id, pri;
    int state, started, number, waits, has_run;
    unsigned long long ready_at;
    pthread_t host;
    pthread_cond_t turn;
    uint32_t waiting_on;
    int faulted; /* stopped by a TLB fault until the game's pager clears the fault flag of its OSThread (port_tlb_fault) */
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
    /* a thread the game's pager has restarted (it cleared the fault flag in the OSThread, as libultra's pager does
       before it puts the thread back on the run queue): ready again */
    for (PortThread *p = threads; p; p = p->next)
        if (p->faulted && p->state == STOPPED && !(be16(p->os + 18) & 2)) {
            static int said;
            if (said++ < 3 || verbose > 1) LOG("thread %d: the game's pager has restarted it", p->number);
            p->faulted = 0;
            /* The pager put the thread back by __osEnqueueThread on the game's own run queue, and libultra's dispatcher
               would pop it from there as it ran it: nothing here does, so the thread would stay linked and the next
               enqueue of it (the next fault) would close a cycle. Take it off the queue (__osDequeueThread). */
            uint32_t queue = be32(p->os + 8);
            if (queue >= 0x80000000u && queue < 0x80800000u) {
                uint32_t pred = queue, succ = be32(pred);
                for (int steps = 0; succ && steps < 256 && succ >= 0x80000000u && succ < 0x80800000u; steps++) {
                    if (succ == p->os) { wbe32(pred, be32(p->os)); break; }
                    pred = succ;
                    succ = be32(pred);
                }
                wbe32(p->os + 8, 0);
            }
            make_ready(p);
        }
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
#ifndef PORT_WEB
/* A game whose first function never returns but ends in an idle loop (`for (;;) osSetThreadPri(NULL, 0)`, or an
   empty `for (;;)` that becomes port_idle_forever): that is the console's idle thread, the boot thread giving the
   threads it started the CPU, and the boot code is it. The host has no thread for the boot code to switch away
   from, so the loop spun for ever and no game thread ever ran. Many such calls in a row from the boot code end
   the boot as a return would. */
static jmp_buf boot_jmp;
static int boot_active;
static void boot_idle(unsigned *calls, const char *what) {
    if (boot_active && ++*calls >= 1000) {
        LOG("the boot code became the idle thread (%s in a loop): its threads run", what);
        boot_active = 0;
        longjmp(boot_jmp, 1);
    }
}
/* The interpreter's periodic safe point (every 65536 instructions) in the boot code, once it has started threads: a
   first function that never returns and whose idle loop is neither of the two above (a loop around a call to the
   game's own code) is the idle thread too. A thousand such safe points are 65 million instructions in one first
   function after its threads exist, more than any loader's work. */
void port_boot_spin(void) {
    static unsigned spins;
    if (!self && !booted && thread_count > 0) boot_idle(&spins, "a loop of its own code");
}
#endif
void port_idle_forever(void) {
    if (!self) {
#ifndef PORT_WEB
        static unsigned idle_calls;
        if (!booted) boot_idle(&idle_calls, "an empty loop");
#endif
        return;
    }
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
    LOG("osDestroyThread(%08X)%s", t, p ? "" : ": no such thread");
    if (p) {
        p->state = DONE;
        if (p == self) { schedule(); pthread_mutex_unlock(&lock); pthread_exit(NULL); }
        if (p == running) schedule();
    }
    pthread_mutex_unlock(&lock);
}
void w2c_env_osSetThreadPri(struct w2c_env *e, u32 t, u32 pri) {
    (void)e;
#ifndef PORT_WEB
    if (!self && !booted && !t) {
        static unsigned boot_calls;
        boot_idle(&boot_calls, "osSetThreadPri(NULL)");
    }
#endif
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
/* PORT_MSGLOG=1: a line for every message sent ("MSG t<thread> send <queue> msg <word> flag <block> valid <n>/<size>", t-1 the
   host itself: a retrace, a completed task) and every receive started, to see what a game's threads say to each other
   when none of them draws (a handshake that spins, a queue nobody posts to) */
static int msg_log(void) { static int on = -1; if (on < 0) on = getenv("PORT_MSGLOG") != NULL; return on; }
static int32_t put(uint32_t mq, uint32_t msg, uint32_t flag, int jam) {
    pthread_mutex_lock(&lock);
    if (msg_log()) fprintf(stderr, "MSG t%d send %08X msg %08X flag %u valid %d/%d\n", self ? self->number : -1, mq, msg, flag, (int)be32(mq + Q_VALID), (int)be32(mq + Q_COUNT));
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
    if (msg_log()) fprintf(stderr, "MSG t%d recv %08X flag %u valid %d\n", self ? self->number : -1, mq, flag, (int)be32(mq + Q_VALID));
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
/* A CPU write to cartridge ROM holds its bus value while PI I/O is busy. Until instruction-level PI timing is
   available, release it on the next retrace; the ROM itself remains read-only. */
static uint32_t cart_io_last;
static int cart_io_busy;
/* Raw PI transfers bypass the libultra host imports. The bootstrap wasm stack is in the
   first 64 KiB (build.py's stack-first), so give it a bus address outside console RAM. */
#define BOOT_STACK_SIZE 0x10000u
#define BOOT_STACK_BUS 0x15000000u
static uint32_t stack_end(uint32_t addr) {
    if (addr >= 0x1000u && addr < BOOT_STACK_SIZE) return BOOT_STACK_SIZE;
    if (addr >= SHADOW_STACKS && addr < SHADOW_STACKS + 32 * SHADOW_SIZE) return SHADOW_STACKS + 32 * SHADOW_SIZE;
    /* the MIPS stacks recompiled C and the interpreter run on (recomp.py: STACKS, mips.c: MIPS_STACKS): KSEG0 above RAM */
    if (addr >= 0x80800000u && addr < 0x82000000u) return 0x82000000u;
    return 0;
}
uint32_t port_stack_bus(uint32_t addr) {
    if (!stack_end(addr)) return 0;
    return addr < BOOT_STACK_SIZE ? BOOT_STACK_BUS + addr : addr;
}
/* The cartridge's save window (domain 2: bus 0x08000000-0x0FFFFFFF, KSEG1 0xA8000000-0xAFFFFFFF) holds SRAM or a
   FlashRAM chip. A game's own libultra reaches it without the host's osEPi* routines: stores and loads through the
   uncached address (the flash command register at +0x10000, the status words) and raw PI transfers. All of them were
   dropped (a strategy game's flash routines wrote the page-write commands, never saw the chip leave its id mode, and
   polled the status for ever). A game that has sent the chip a command talks to FlashRAM; otherwise it is SRAM. */
int port_sram_dma(uint32_t dram, uint32_t offset, uint32_t size, uint32_t write);
int port_flash_dma(uint32_t dram, uint32_t offset, uint32_t size, uint32_t write);
void port_flash_command(uint32_t command);
uint32_t port_flash_read(uint32_t offset);
static int flash_in_use;
static int save_window(uint32_t bus) { bus &= 0x1FFFFFFFu; return bus >= 0x08000000u && bus < 0x10000000u; }
static uint32_t save_offset(uint32_t bus) { return (bus & 0x1FFFFFFFu) - 0x08000000u; }
static int save_dma(uint32_t dest, uint32_t cart, uint32_t size, uint32_t write) {
    uint32_t end = 0x80800000u;
    if (dest < 0x80000000u || size > end - dest) return -1;
    return flash_in_use ? port_flash_dma(dest, save_offset(cart), size, write) : port_sram_dma(dest, save_offset(cart), size, write);
}
static unsigned long dmas; /* (the count below, also of a game's own PI manager's transfers through the registers) */
static unsigned long vi_count;
static int pi_register(uint32_t addr, int write, uint32_t value, uint32_t *read) {
    static uint32_t dram, cart;
    switch (addr) {
    case 0xA4600000u: if (write) dram = value; else *read = dram; return 1;
    case 0xA4600004u: if (write) cart = value; else *read = cart; return 1;
    case 0xA4600010u: if (!write) *read = cart_io_busy ? 2u : 0u; return 1;
    case 0xA4600008u: {
        if (!write) { *read = 0; return 1; }
        /* DRAM to the cartridge: only the save window takes a write (the ROM ignores it) */
        if (save_window(cart)) {
            uint32_t size = (value & 0x00FFFFFFu) + 1, bus = dram >= 0x00800000u && dram < 0x04000000u ? dram | 0x80000000u : dram;
            uint32_t dest = bus < 0x00800000u ? 0x80000000u | (bus & 0x007FFFFEu) : bus;
            save_dma(dest, cart, size, 1);
            post_event(EV_PI);
        }
        return 1;
    }
    case 0xA460000Cu: {
        if (!write) { *read = 0; return 1; }
        uint32_t size = (value & 0x00FFFFFFu) + 1;
        /* a stack's physical address (osVirtualToPhysical of its KSEG0 one) is 0x00800000 up: back to where it is */
        uint32_t bus = dram >= 0x00800000u && dram < 0x04000000u ? dram | 0x80000000u : dram;
        int boot = bus >= BOOT_STACK_BUS && bus < 0x80000000u;
        uint32_t dest = boot ? bus - BOOT_STACK_BUS : bus;
        uint32_t end = bus >= 0x00800000u ? stack_end(dest) : 0x80800000u;
        if (bus < 0x00800000u) dest = 0x80000000u | (bus & 0x007FFFFEu);
        if (boot && dest >= BOOT_STACK_SIZE) end = 0;
        if (save_window(cart)) { save_dma(dest, cart, size, 0); post_event(EV_PI); return 1; }
        uint32_t offset = cart & 0x0FFFFFFEu;
        int copied = cart >= 0x10000000u && cart < 0x1FC00000u && dest < end
                && size <= end - dest && (uint64_t)offset + size <= rom_size;
        if (copied) memcpy(M + dest, rom + offset, size);
        /* a game's own PI manager reads the cartridge through these registers: counted and logged as the host's own
           osPiStartDma is (the summary said "0 DMAs" of a game that read its files this way), and a transfer the
           host could not do says so */
        dmas++;
        if (dmas <= 20 || dmas % 500 == 0 || getenv("PORT_DMA_LOG"))
            LOG("DMA %lu (retrace %lu): ROM %06X -> %08X, %X bytes%s", dmas, vi_count, offset, dest, size, copied ? "" : " (NOT DONE)");
        post_event(EV_PI);
        return 1;
    }
    default: return 0;
    }
}
/* The 64DD's registers and IPL (physical 0x05000000-0x07FFFFFF) on a console with no 64DD: nothing answers, and the
   cartridge bus floats, giving back the low 16 bits of the address in both halves of the word. A game's libleo
   (the 64DD support some cartridges carry) reads the status register at 0x05000508 and, finding a nonzero word,
   returns at once with "no 64DD"; answered with 0 it built its disk manager and waited for it for ever (a racing
   game's main thread spun on the manager's reply). */
/* The RDP's own command list (osDpSetNextBuffer, a scheduler that draws without an RSP task): DPC_START names where the
   list begins, and writing DPC_END starts the RDP, which draws [current, end) and, at a full sync (command 0x29), raises
   the DP interrupt the game's scheduler waits for. With these registers plain memory the list was never drawn and the
   DP event never came: a racing-style game's scheduler waited for ever after its second list. */
static uint32_t dpc_start, dpc_end, dpc_cur;
static uint32_t dpc_start_word, dpc_end_word; /* the words as written: what a self-test of the registers reads back */
static int dpc_start_valid;
static void rcp_raise(int event);
static void dpc_run(void) {
    if (dpc_start_valid) { dpc_cur = dpc_start; dpc_start_valid = 0; }
    uint32_t from = dpc_cur & 0x00FFFFF8u, to = dpc_end;
    if (to <= from || to - from > 0x100000u || to > 0x00800000u) { dpc_cur = to > from ? from : dpc_cur; return; }
    const uint8_t *cmds = M + gv(0x80000000u + from);
    size_t bytes = to - from;
    int sync = 0;
    for (size_t at = 0; at + 8 <= bytes;) { /* the list's commands: length by opcode, a full sync raises the interrupt */
        uint32_t id = cmds[at] & 0x3Fu, words = 2;
        if (id >= 0x08 && id <= 0x0F) words = 8 + (id & 4 ? 16 : 0) + (id & 2 ? 16 : 0) + (id & 1 ? 4 : 0);
        else if (id == 0x24 || id == 0x25) words = 4;
        if (id == 0x29) sync = 1;
        at += words * 4;
    }
    static int said;
    if (said++ < 3) LOG("the game gave the RDP a command list: %06X to %06X (%s)", from, to, sync ? "ends in a full sync" : "no full sync");
    port_render_rdp_list(cmds, bytes);
    dpc_cur = to;
    if (sync) rcp_raise(EV_DP);
}
static int dd_range(uint32_t bus) { bus &= 0x1FFFFFFFu; return bus >= 0x05000000u && bus < 0x08000000u; }
static uint32_t open_bus(uint32_t bus) { return (bus & 0xFFFFu) * 0x10001u; }
uint64_t port_io_load(uint64_t addr) {
    if (addr < 0xA0800000u) return addr - 0x20000000u; /* RAM, uncached: the same RAM */
    if (addr >= 0xA5000000u && addr < 0xA8000000u) { wbe32(IO_SCRATCH, open_bus((uint32_t)addr & ~3u)); return IO_SCRATCH + (addr & 3); }
    if (addr >= 0xA8000000u && addr < 0xB0000000u) { /* the save window: FlashRAM's status or array words (SRAM is read by DMA) */
        wbe32(IO_SCRATCH, flash_in_use ? port_flash_read(save_offset((uint32_t)addr) & ~3u) : 0);
        return IO_SCRATCH + (addr & 3);
    }
    if (addr == SP_STATUS) { wbe32(IO_SCRATCH, 1); return IO_SCRATCH; } /* halted: a task finishes at once */
    if (addr >= DPC_BASE && addr < DPC_BASE + 0x0Cu) { /* the list the RDP was given: start, end, and where it is (done) */
        wbe32(IO_SCRATCH, addr == DPC_BASE ? dpc_start_word : addr == DPC_BASE + 4u ? dpc_end_word : dpc_cur);
        return IO_SCRATCH;
    }
    if (addr >= DPC_BASE + 0x0Cu && addr < DPC_BASE + 0x20u) {
        /* The RDP's command registers: the host draws a task when it starts, so the RDP is always idle. A game that
           writes the status (clear XBUS DMEM DMA) and polls bit 0 until it drops (libultra's osDpSetStatus users)
           spun for ever on a plain memory word that kept the bit it had set. Status: only CBUF_READY; the clock
           and busy counters read 0. */
        wbe32(IO_SCRATCH, addr == DPC_BASE + 0x0Cu ? 0x80u : 0u);
        return IO_SCRATCH;
    }
    uint32_t v;
    if (port_ai_register((uint32_t)addr, 0, 0, &v) || si_register((uint32_t)addr, 0, 0, &v) || pi_register((uint32_t)addr, 0, 0, &v)) { wbe32(IO_SCRATCH, v); return IO_SCRATCH; }
    if (addr >= 0xB0000000u && addr < 0xBFC00000u) { /* the cartridge, read directly (a racing game checks its own word) */
        wbe32(IO_SCRATCH, cart_io_busy ? cart_io_last : rom32((size_t)((addr - 0xB0000000u) & ~3u)));
        return IO_SCRATCH + (addr & 3);
    }
    return IO_FAR(addr);
}
uint64_t port_io_store(uint64_t addr, uint64_t value) {
    if (addr < 0xA0800000u) return addr - 0x20000000u;
    if (addr >= 0xA8000000u && addr < 0xB0000000u) { /* the save window: the flash chip's command register */
        if (save_offset((uint32_t)addr) == 0x10000u) { flash_in_use = 1; port_flash_command((uint32_t)value); }
        return IO_SCRATCH + 4;
    }
    if (addr >= 0xB0000000u && addr < 0xBFC00000u) {
        cart_io_last = (uint32_t)value;
        cart_io_busy = 1;
        return IO_SCRATCH + 4;
    }
    if (addr == SP_STATUS) return IO_SCRATCH + 4; /* a command (clear halt, set a signal): nothing to keep */
    if (addr == DPC_BASE) { dpc_start_word = (uint32_t)value; dpc_start = (uint32_t)value & 0x00FFFFF8u; dpc_start_valid = 1; return IO_SCRATCH + 4; }
    if (addr == DPC_BASE + 4u) { dpc_end_word = (uint32_t)value; dpc_end = (uint32_t)value & 0x00FFFFF8u; dpc_run(); return IO_SCRATCH + 4; }
    if (addr == DPC_BASE + 0x0Cu) return IO_SCRATCH + 4; /* a command to the RDP's status: nothing to keep (see port_io_load) */
    if (port_ai_register((uint32_t)addr, 1, (uint32_t)value, NULL) || si_register((uint32_t)addr, 1, (uint32_t)value, NULL)
            || pi_register((uint32_t)addr, 1, (uint32_t)value, NULL))
        return IO_SCRATCH + 4;
    return IO_FAR(addr);
}

/* ---------------------------------------------------------------- VI */

static struct { uint32_t queue, msg, every; } vi;
static uint32_t frame_buffer;
static unsigned long vi_count, swaps;

void port_vi_black(int on); /* render.c */
void w2c_env_osCreateViManager(struct w2c_env *e, u32 pri) { (void)e; (void)pri; port_vi_black(1); /* osViInit: blanked */ }
void w2c_env_osViSetEvent(struct w2c_env *e, u32 mq, u32 msg, u32 every) {
    (void)e;
    LOG("osViSetEvent(%08X, every %u)", mq, every);
    vi.queue = mq; vi.msg = msg; vi.every = every ? every : 1;
}
/* The VI's mode, Y scale and filters go to the renderer (render.c), which shows each frame at its size. */
void port_vi_mode(uint32_t mode);
void port_vi_yscale(float s);
void port_vi_features(uint32_t f);
void w2c_env_osViSetMode(struct w2c_env *e, u32 mode) {
    (void)e; LOG("osViSetMode(%08X)", mode); port_vi_mode(mode);
#ifndef PORT_WEB
    if (getenv("PORT_RT64") && (mode & 0x1FFFFFFFu) <= 0x800000u - 4 - 4 * PORT_RT64_VI_MODE_WORDS) {
        uint32_t words[PORT_RT64_VI_MODE_WORDS], address = 0x80000000u | (mode & 0x1FFFFFFFu);
        for (unsigned i = 0; i < PORT_RT64_VI_MODE_WORDS; i++) words[i] = be32(address + 4 + i * 4);
        port_rt64_mode(words);
    }
#endif
}
void w2c_env_osViSetYScale(struct w2c_env *e, f32 s) {
    (void)e; port_vi_yscale(s);
#ifndef PORT_WEB
    if (getenv("PORT_RT64")) port_rt64_yscale(s);
#endif
}
void w2c_env_osViBlack(struct w2c_env *e, u32 on) { (void)e; LOG("osViBlack(%u)", on); port_vi_black(on != 0); }
void w2c_env_osViSetSpecialFeatures(struct w2c_env *e, u32 f) {
    (void)e; port_vi_features(f);
#ifndef PORT_WEB
    if (getenv("PORT_RT64")) port_rt64_features(f);
#endif
}
void port_frame_shown(uint32_t fb, unsigned long n); /* render.c: saves it when asked */
#ifndef PORT_WEB
static int vtime; /* PORT_VTIME (the clock is below) */
static uint64_t vclock_us;
static unsigned *vtrace; /* PORT_VTIME_TRACE: the console's retrace at each graphics task, by task number */
static unsigned vtrace_n;
static unsigned long vi_target; /* the retrace the console was at when it started the latest graphics task */
static int retrace_us(void);
#endif
static unsigned long gfx_tasks, audio_tasks;
/* PORT_SWAP_LOG=file (or - for stderr, among the host's other lines): a line for each buffer the game hands the VI, "frame NUMBER task GRAPHICS-TASKS-SO-FAR retrace
   VI-COUNT buffer ADDRESS black 0|1" (black: the VI blanked, osViBlack): which graphics task (the unit of PORT_FRAME_DUMPS and tools/n64_frame_compare.py --frames) a
   frame shown (PORT_FRAMES, frame_NNNNN.png) came after, to look at the console's task there. */
int port_vi_blanked(void); /* render.c */
static void port_swap_log(unsigned long n, uint32_t fb) {
    static FILE *log; static int tried;
    if (!tried) { const char *path = getenv("PORT_SWAP_LOG"); tried = 1; if (path) log = strcmp(path, "-") ? fopen(path, "w") : stderr; }
    if (log) { fprintf(log, "frame %lu task %lu retrace %lu buffer %08X black %d\n", n, gfx_tasks, vi_count, fb, port_vi_blanked()); fflush(log); }
}
void port_vi_swap(uint32_t fb) { /* (games/: a game's own name) */
    /* The VI's origin register holds 24 bits: a game that hands over 0x847B4F80 (a flag bit above the address, as
       one shooter's buffers carry) shows the buffer at 0x7B4F80, as the console does. What the game reads back
       (osViGetCurrentFramebuffer) stays as it gave it. */
    uint32_t shown = 0x80000000u | (fb & 0x00FFFFFFu);
    frame_buffer = fb; swaps++; port_frame_shown(shown, swaps);
    port_swap_log(swaps, fb);
#ifndef PORT_WEB
    if (getenv("PORT_RT64")) port_rt64_screen(M + 0x80000000u, shown);
#endif
}
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
static void ram_dumps(const char *var, unsigned long now, uint32_t task) {
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
            if (f) {
                fwrite(M + 0x80000000u, 1, 0x800000, f); fclose(f);
                if (task && n + 6 < sizeof name) { /* the task beside it, as the emulator writes NAME.task */
                    strcat(name, ".task");
                    FILE *t = fopen(name, "wb");
                    if (t) { fwrite(M + task, 1, 64, t); fclose(t); }
                }
            } else fprintf(stderr, "%s: can't write %s\n", var, name);
        }
        p = next ? next + 1 : path + n;
    }
}
unsigned port_ai_completions(void); /* audio.c */
static void port_audio_interrupts(void) {
    for (unsigned n = port_ai_completions(); n; n--) post_event(EV_AI);
}
static void dp_deliver(int force); /* the RSP's and RDP's held-back interrupts (osSpTaskStartGo) */
static void retrace(void) {
    dp_deliver(1); /* a task's SP and DP interrupts are raised before the next retrace at the latest */
    vi_count++;
    cart_io_busy = 0;
#ifndef PORT_WEB
    if (getenv("PORT_RT64")) port_rt64_retrace(vi_count & 1);
#endif
    ram_dumps("PORT_RAM_DUMPS", vi_count, 0);
    port_timers();
    port_audio_interrupts();
    if (vi.queue && vi_count % vi.every == 0) put(vi.queue, vi.msg, 0, 0);
}

/* ---------------------------------------------------------------- PI: the cartridge */

void w2c_env_osCreatePiManager(struct w2c_env *e, u32 pri, u32 q, u32 buf, u32 n) { (void)e; (void)pri; (void)q; (void)buf; (void)n; }
static unsigned long dmas;
/* A DMA's RAM address given as a virtual address a TLB window maps (a game that brings its program into a window:
   libultra's osVirtualToPhysical asks the TLB): the bytes go where the window keeps that address. Left as it was, the
   transfer wrote the wasm module's own low memory at the virtual address, and the code never reached the window. */
static u32 dma_ram(u32 dram) {
    if (port_tlb_n && (dram < 0x80000000u || dram >= 0xC0000000u)) return port_tlb_canon(dram);
    return dram;
}
void port_tlb_physical_use(uint32_t a, uint32_t size); /* below, with the windows */
u32 w2c_env_osPiStartDma(struct w2c_env *e, u32 mb, u32 pri, u32 dir, u32 dev, u32 dram_given, u32 size, u32 mq) {
    (void)e; (void)pri;
    u32 dram = dma_ram(dram_given);
    if (dram_given >= 0x80000000u && port_tlb_n) port_tlb_physical_use(dram_given, size); /* (a transfer of the game's own data) */
    dmas++;
    if (getenv("PORT_DMA")) fprintf(stderr, "osPiStartDma(mb %08X, pri %u, dir %u, dev %08X, dram %08X, size %X, mq %08X)\n", mb, pri, dir, dev, dram, size, mq);
    u32 end = stack_end(dram);
    if ((dram < 0x80000000u || (dram >= 0x80800000u && dram < 0xA0000000u)) && (!end || size > end - dram)) {
        fprintf(stderr, "osPiStartDma to %08X, outside the console's RAM\n", dram); port_trace();
    }
    /* The PI uses a cartridge bus address. Some callers pass the full address returned by osRomBase,
       including the KSEG bits, while others pass a byte offset into the ROM. Keep dev intact in OSIoMesg. */
    u32 offset = dev & 0x0FFFFFFFu;
    if (dir != 0) LOG("osPiStartDma: a write to the cartridge (%08X), ignored", dev);
    else if (offset + (uint64_t)size > rom_size) LOG("osPiStartDma: %08X+%X is past the ROM's end", dev, size);
    else memcpy(M + dram, rom + offset, size);
    if (dmas <= 20 || dmas % 500 == 0 || getenv("PORT_DMA_LOG")) LOG("DMA %lu (retrace %lu): ROM %06X -> %08X, %X bytes", dmas, vi_count, offset, dram, size);
    if (mb) { wbe32(mb + 8, dram_given); wbe32(mb + 12, dev); wbe32(mb + 16, size); }
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
/* FlashRAM (flash.c): a PI handle of type 8, libultra's DEVICE_TYPE_FLASH */
#define PI_TYPE_FLASH 8
static int flash_handle(u32 handle) { return handle && M[handle + 4] == PI_TYPE_FLASH; }
static uint32_t flash_offset(u32 dev) { uint32_t bus = dev & 0x1FFFFFFFu; return bus >= 0x08000000u ? bus - 0x08000000u : bus; }
u32 w2c_env_osEPiStartDma(struct w2c_env *e, u32 handle, u32 mb, u32 dir) {
    u32 dev = be32(mb + 12), dram = be32(mb + 8), size = be32(mb + 16), mq = be32(mb + 4);
    /* A type-3 PI handle is SRAM; FlashRAM shares its bus window but uses a different command protocol. */
    if (handle && M[handle + 4] == 3) {
        u32 bus = dev & 0x1FFFFFFFu;
        u32 offset = bus >= 0x08000000u ? bus - 0x08000000u : bus;
        u32 end = stack_end(dram);
        if (dram >= 0x80000000u && dram < 0x80800000u) end = 0x80800000u;
        if (!end || size > end - dram || port_sram_dma(dram, offset, size, dir)) return (u32)-1;
        if (mq) put(mq, mb, 0, 0);
        return 0;
    }
    if (flash_handle(handle)) {
        u32 end = stack_end(dram);
        if (dram >= 0x80000000u && dram < 0x80800000u) end = 0x80800000u;
        if (!end || size > end - dram || port_flash_dma(dram, flash_offset(dev), size, dir)) return (u32)-1;
        if (mq) put(mq, mb, 0, 0);
        return 0;
    }
    return w2c_env_osPiStartDma(e, mb, 0, dir, dev, dram, size, mq);
}
u32 w2c_env_osEPiReadIo(struct w2c_env *e, u32 handle, u32 dev, u32 data) {
    (void)e;
    if (flash_handle(handle)) { wbe32(data, port_flash_read(flash_offset(dev))); return 0; }
    if (dd_range(dev)) { wbe32(data, open_bus(dev & ~3u)); return 0; }
    uint32_t at = dev & 0x0FFFFFFCu;
    wbe32(data, at + 4 <= rom_size ? (uint32_t)rom[at] << 24 | rom[at + 1] << 16 | rom[at + 2] << 8 | rom[at + 3] : 0);
    return 0;
}
u32 w2c_env_osEPiWriteIo(struct w2c_env *e, u32 handle, u32 dev, u32 data) {
    (void)e;
    if (flash_handle(handle) && flash_offset(dev) == 0x10000u) { flash_in_use = 1; port_flash_command(data); } /* the command register */
    return 0;
}
/* A local of the bootstrap wasm stack (the first 64 KiB, below console RAM's low memory) has no KSEG address: its
   physical address is its bus address (BOOT_STACK_BUS), which the raw PI transfer reads back to the stack. Masked
   as KSEG, it read as console RAM at the same offset and a DMA overwrote the game's own low memory (a snowboarding
   game's file reader, whose buffer is such a local, read headers for ever once the host answered this by name). */
u32 w2c_env_osVirtualToPhysical(struct w2c_env *e, u32 a) {
    (void)e;
    if (a >= 0x1000u && a < BOOT_STACK_SIZE) return BOOT_STACK_BUS + a;
    if (a < 0x80000000u && port_tlb_n) return dma_ram(a) & 0x1FFFFFFF; /* a TLB-mapped address: where its window keeps it */
    return a & 0x1FFFFFFF;
}

/* ---------------------------------------------------------------- RSP tasks: graphics (render.c) and audio (audio.c) */

void port_audio_task(uint32_t task); /* audio.c */
/* The RSP and RDP interrupts of a task (SP: the ucode ran out; DP: the RDP's full sync) arrive after osSpTaskStartGo
   has returned, as on the console, where the RSP takes far longer than the call that started it and the scheduler
   finishes its own bookkeeping first. A game's scheduler whose SP handler is a thread of higher priority than the
   caller found, in the same instant, a task the caller had not finished recording, and its DP handler then found no
   RDP task and stopped the game ("NULL RDP Task!" in a fighting game). The events are held (SP, then DP, in order) and
   raised from the host's tick, when every game thread is waiting in virtual time, and at the retrace at the latest.
   The web build, which has no tick, raises them at once as before. */
#ifndef PORT_WEB
static uint64_t monotonic_us(void);
static pthread_mutex_t rcp_lock = PTHREAD_MUTEX_INITIALIZER;
static unsigned char rcp_events[64];
static unsigned long rcp_due_vi[64]; /* the retrace each held event may be raised at (PORT_RCP_RETRACES) */
static unsigned rcp_count;
static uint64_t rcp_due_us;
#define RCP_DELAY_US 400
#endif
/* PORT_RCP_RETRACES=n: hold each task's SP and DP interrupts n retraces after the task was started, as a console whose RSP and RDP take
   that long to finish a heavy frame does (the emulator's logic ran a frame behind the port's at the same graphics task when they came at once,
   docs/PORTING.md). 0, the default, raises them when every game thread waits, as before. */
static unsigned long rcp_delay_vi(void) {
    static long n = -1;
    if (n < 0) n = getenv("PORT_RCP_RETRACES") ? atol(getenv("PORT_RCP_RETRACES")) : 0;
    return (unsigned long)n;
}
static void rcp_raise(int event) {
#ifdef PORT_WEB
    post_event(event);
#else
    pthread_mutex_lock(&rcp_lock);
    if (rcp_count < sizeof rcp_events) { rcp_due_vi[rcp_count] = vi_count + rcp_delay_vi(); rcp_events[rcp_count++] = (unsigned char)event; }
    rcp_due_us = monotonic_us() + RCP_DELAY_US;
    pthread_mutex_unlock(&rcp_lock);
#endif
}
static void dp_deliver(int force) {
#ifndef PORT_WEB
    unsigned char out[sizeof rcp_events];
    unsigned n = 0, keep = 0;
    pthread_mutex_lock(&rcp_lock);
    if (!rcp_count || (!force && monotonic_us() < rcp_due_us)) { pthread_mutex_unlock(&rcp_lock); return; }
    for (unsigned i = 0; i < rcp_count; i++) { /* in order: an event waits for the ones before it */
        if (!keep && rcp_due_vi[i] <= vi_count) out[n++] = rcp_events[i];
        else { rcp_events[keep] = rcp_events[i]; rcp_due_vi[keep++] = rcp_due_vi[i]; }
    }
    rcp_count = keep;
    pthread_mutex_unlock(&rcp_lock);
    for (unsigned i = 0; i < n; i++) post_event(out[i]);
#else
    (void)force;
#endif
}
void w2c_env_osSpTaskStartGo(struct w2c_env *e, u32 task) {
    (void)e;
    uint32_t type = be32(task);
    if (type == 1) gfx_tasks++; else audio_tasks++;
#ifndef PORT_WEB
    if (type == 1 && vtime && vtrace && gfx_tasks < vtrace_n) { /* the console started this task at this retrace */
        uint64_t want = (uint64_t)vtrace[gfx_tasks] * (uint64_t)retrace_us();
        if (vclock_us < want) vclock_us = want;
        if (vtrace[gfx_tasks] > vi_target) vi_target = vtrace[gfx_tasks];
        /* the console had had those retraces before it started the task, so the threads they wake (the game's logic, counted in
           retraces) have run before the task's RAM is taken or its list drawn: raised here, from the thread that starts the task, as an
           interrupt would be. Left to the host's loop they came after the task started, and the port's logic, taken task by task, ran
           a frame ahead of the console's (the track's line points changed at tasks 604, 609, 614 and, on the console, 603, 608, 613).
           PORT_VTIME_EARLY=0 restores the later raising. */
        static int early = -1;
        if (early < 0) early = !getenv("PORT_VTIME_EARLY") || atoi(getenv("PORT_VTIME_EARLY")) != 0;
        while (early && vi_count < vtrace[gfx_tasks]) retrace();
    }
#endif
    if (type == 1) ram_dumps("PORT_FRAME_DUMPS", gfx_tasks, task);
    if (type == 1) {
#ifndef PORT_WEB
        if (getenv("PORT_RT64"))
            port_rt64_task(M + 0x80000000u, be32(task + 16), be32(task + 24), be32(task + 48));
#endif
        port_render_task(task);
    }
    if (type == 2) port_audio_task(task);
    { /* the first task of each type, whatever the count: a game's own task types (not 1 or 2) count as audio above */
        static unsigned seen_types;
        if (type < 32 && !(seen_types & (1u << type))) {
            seen_types |= 1u << type;
            LOG("first RSP task of type %u: flags %X, ucode %08X, data %08X (%X bytes)", type, be32(task + 4), be32(task + 16),
                be32(task + 48), be32(task + 52));
        }
    }
    if (gfx_tasks + audio_tasks <= 4) LOG("RSP task type %u flags %X: data %08X, %X bytes", type, be32(task + 4), be32(task + 48), be32(task + 52));    if (type == 1 && gfx_tasks == 2 && getenv("PORT_TASK")) {
        uint32_t ud = be32(task + 24) & 0x1FFFFFFF, uds = be32(task + 28), dl = be32(task + 48) & 0x1FFFFFFF;
        fprintf(stderr, "gfx task: ucode %08X (%X), ucode data %08X (%X), data %08X\n",
                be32(task + 16), be32(task + 20), be32(task + 24), uds, be32(task + 48));
        for (uint32_t i = 0; i + 3 < uds; i++)  /* the microcode's version string lives in its data */
            if (M[0x80000000u + ud + i] == 'R' && M[0x80000000u + ud + i + 1] == 'S' && M[0x80000000u + ud + i + 2] == 'P') {
                fprintf(stderr, "ucode text: %.80s\n", (char *)&M[0x80000000u + ud + i]); break; }
        for (int i = 0; i < 24; i++) fprintf(stderr, "%08X%c", be32(0x80000000u + dl + 4 * i), i % 2 ? '\n' : ' ');
    }
    wbe32(SP_STATUS, 1); /* done: halted again */
    rcp_raise(EV_SP);
    if (type == 1) rcp_raise(EV_DP);
}
u32 w2c_env_osSpTaskYielded(struct w2c_env *e, u32 task) { (void)e; (void)task; return 0; }
/* a scheduler asks the RSP to yield its task: the host runs a task to its end at once, so there is nothing to ask */
void w2c_env_osSpTaskYield(struct w2c_env *e) { (void)e; }
/* osSpTaskStart is osSpTaskLoad then osSpTaskStartGo: the task runs whole at StartGo, so loading it is nothing */
void w2c_env_osSpTaskLoad(struct w2c_env *e, u32 task) { (void)e; (void)task; }

/* ---------------------------------------------------------------- the CPU's own registers */

/* The MIPS builtins the matched C reaches the R4300's coprocessor 0 and FPU with (the compiler's, for code that
   reads Status or Count): answered here, as the console's CPU would. */
static uint32_t cop0_status = 0x3000FF01u, cop0_compare, fcsr = 0x01000800u;
u32 w2c_env_0x5F_builtin_mips_read_status(struct w2c_env *e) { (void)e; return cop0_status; }
uint32_t port_cop0_status(void) { return cop0_status; } /* recompiled code's frames start from it (recomp.py) */
void port_cop0_status_set(uint32_t v) { cop0_status = v; }
u32 w2c_env_0x5F_builtin_mips_write_status_return_spacing(struct w2c_env *e, u32 v) { (void)e; cop0_status = v; return 0; }
u32 w2c_env_0x5F_builtin_mips_write_compare(struct w2c_env *e, u32 v) { (void)e; cop0_compare = v; return 0; }
#ifndef PORT_WEB
/* PORT_VTIME=1: virtual time. The retrace no longer follows the wall clock: it fires when every game thread is waiting
   (the CPU of a console with no speed limit has nothing left to do before the next retrace) and the clock the game reads
   (osGetTime, the count register, the audio interface's drain) advances one retrace's worth each time. A run is then the
   same whatever the machine's load, and as fast as the machine can go, so two runs of one input script (timed in
   retraces) see the game at the same point at the same retrace, and a game's own timing (race clocks, animations)
   can be compared with the console's retrace for retrace. Reads of the clock within a retrace advance it a microsecond
   each, so a loop waiting for the time to pass still ends. */
static uint64_t vnext_us; /* when the next retrace is due */
static unsigned vsub;
static unsigned long vtime_forced; /* retraces fired with a thread still running, after the wait's limit */
static int retrace_us(void);
double port_clock_seconds(void) {
    if (vtime) {
        unsigned room = (unsigned)retrace_us() - 1;
        if (vsub < room) vsub++;
        return (double)(vclock_us + vsub) / 1e6;
    }
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (double)t.tv_sec + t.tv_nsec / 1e9;
}
#endif
u32 w2c_env_0x5F_builtin_mips_read_count(struct w2c_env *e) { /* Count runs at half the CPU's 93.75 MHz */
    (void)e;
#ifndef PORT_WEB
    if (vtime) return (uint32_t)(port_clock_seconds() * 46875000.0);
#endif
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
    /* The transfer ends after it has started, as on the console, where the controller read takes a moment: the SI event
       arrives from the host's tick like the RSP's, not inside the call. A racing game's controller thread (priority 90)
       sent itself the SI message in the same instant and received it at once, so it never blocked: it kept the CPU from
       every lower-priority thread, one of which posted the queue it polled, and nothing else ran. (osContStartReadData
       posts at once: a bowling game's startup, with its controller thread blocking there, called a filter method
       before its audio setup had run.) */
    rcp_raise(EV_SI);
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
/* The rest of libultra's controller, Controller Pak, Game Boy Pak and Voice Recognition API, answered as a console with
   no pak in any controller answers (PFS_ERR_NOPACK = 1; the accessories' own checks: PFS_ERR_DEVICE = 11). A game whose
   own C for these was left out as the host's ran a stub that returned 0 ("success") with nothing filled in
   (osContSetCh, osPfsInit and others were reported "not implemented, skipped"). */
u32 w2c_env_osContSetCh(struct w2c_env *e, u32 max) { (void)e; (void)max; return 0; } /* how many controllers to scan */
u32 w2c_env_osContReset(struct w2c_env *e, u32 mq, u32 status) { (void)e; (void)mq; (void)status; return 0; }
u32 w2c_env_osPfsInit(struct w2c_env *e, u32 mq, u32 pfs, u32 ch) { (void)e; (void)mq; (void)pfs; (void)ch; return 1; }
u32 w2c_env_osPfsReFormat(struct w2c_env *e, u32 pfs, u32 mq, u32 ch) { (void)e; (void)pfs; (void)mq; (void)ch; return 1; }
u32 w2c_env_osPfsChecker(struct w2c_env *e, u32 pfs) { (void)e; (void)pfs; return 1; }
u32 w2c_env_osPfsRepairId(struct w2c_env *e, u32 pfs) { (void)e; (void)pfs; return 1; }
u32 w2c_env_osPfsFileState(struct w2c_env *e, u32 pfs, u32 file, u32 state) { (void)e; (void)pfs; (void)file; (void)state; return 1; }
u32 w2c_env_osPfsFindFile(struct w2c_env *e, u32 pfs, u32 company, u32 game, u32 name, u32 ext, u32 file) {
    (void)e; (void)pfs; (void)company; (void)game; (void)name; (void)ext; (void)file; return 1;
}
u32 w2c_env_osPfsAllocateFile(struct w2c_env *e, u32 pfs, u32 company, u32 game, u32 name, u32 ext, u32 size, u32 file) {
    (void)e; (void)pfs; (void)company; (void)game; (void)name; (void)ext; (void)size; (void)file; return 1;
}
u32 w2c_env_osPfsDeleteFile(struct w2c_env *e, u32 pfs, u32 company, u32 game, u32 name, u32 ext) {
    (void)e; (void)pfs; (void)company; (void)game; (void)name; (void)ext; return 1;
}
u32 w2c_env_osPfsReadWriteFile(struct w2c_env *e, u32 pfs, u32 file, u32 flag, u32 offset, u32 size, u32 data) {
    (void)e; (void)pfs; (void)file; (void)flag; (void)offset; (void)size; (void)data; return 1;
}
u32 w2c_env_osPfsGetLabel(struct w2c_env *e, u32 pfs, u32 label, u32 len) { (void)e; (void)pfs; (void)label; (void)len; return 1; }
u32 w2c_env_osPfsSetLabel(struct w2c_env *e, u32 pfs, u32 label) { (void)e; (void)pfs; (void)label; return 1; }
u32 w2c_env_0x5F_osPfsGetStatus(struct w2c_env *e, u32 mq, u32 ch) { (void)e; (void)mq; (void)ch; return 1; }
void w2c_env_0x5F_osSetWatchLo(struct w2c_env *e, u32 value) { (void)e; (void)value; } /* a debugger's watchpoint: none here */
u32 w2c_env_osGbpakInit(struct w2c_env *e, u32 mq, u32 pfs, u32 ch) { (void)e; (void)mq; (void)pfs; (void)ch; return 11; }
u32 w2c_env_osGbpakPower(struct w2c_env *e, u32 pfs, u32 on) { (void)e; (void)pfs; (void)on; return 11; }
u32 w2c_env_osGbpakCheckConnector(struct w2c_env *e, u32 pfs, u32 status) { (void)e; (void)pfs; (void)status; return 11; }
u32 w2c_env_osGbpakGetStatus(struct w2c_env *e, u32 pfs, u32 status) { (void)e; (void)pfs; (void)status; return 11; }
u32 w2c_env_osGbpakReadId(struct w2c_env *e, u32 pfs, u32 id, u32 status) { (void)e; (void)pfs; (void)id; (void)status; return 11; }
u32 w2c_env_osGbpakReadWrite(struct w2c_env *e, u32 pfs, u32 flag, u32 address, u32 buffer, u32 size) {
    (void)e; (void)pfs; (void)flag; (void)address; (void)buffer; (void)size; return 11;
}
u32 w2c_env_osVoiceInit(struct w2c_env *e, u32 mq, u32 handle, u32 ch) { (void)e; (void)mq; (void)handle; (void)ch; return 11; }
u32 w2c_env_osVoiceStartReadData(struct w2c_env *e, u32 handle) { (void)e; (void)handle; return 11; }
u32 w2c_env_osVoiceStopReadData(struct w2c_env *e, u32 handle) { (void)e; (void)handle; return 11; }
u32 w2c_env_osVoiceGetReadData(struct w2c_env *e, u32 handle, u32 data) { (void)e; (void)handle; (void)data; return 11; }
u32 w2c_env_osVoiceSetWord(struct w2c_env *e, u32 handle, u32 word) { (void)e; (void)handle; (void)word; return 11; }
u32 w2c_env_osVoiceClearDictionary(struct w2c_env *e, u32 handle, u32 words) { (void)e; (void)handle; (void)words; return 11; }
u32 w2c_env_osVoiceMaskDictionary(struct w2c_env *e, u32 handle, u32 mask, u32 size) { (void)e; (void)handle; (void)mask; (void)size; return 11; }
u32 w2c_env_osVoiceControlGain(struct w2c_env *e, u32 handle, u32 analog, u32 digital) { (void)e; (void)handle; (void)analog; (void)digital; return 11; }

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
#ifndef PORT_WEB
    if (vtime) return (u64)((int64_t)(port_clock_seconds() * 46875000.0) + time_offset);
#endif
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
static void tlb_map(uint32_t pm, uint32_t va, uint32_t even, uint32_t odd); /* below, with the boot */
void w2c_env_osMapTLB(struct w2c_env *e, u32 index, u32 pm, u32 va, u32 even, u32 odd, u32 asid) {
    (void)e; (void)asid;
    static int said;
    if (!said++) fprintf(stderr, "osMapTLB(%u, %08X -> %08X/%08X): the port has no TLB, so a mapped window keeps its KSEG0 spelling\n", index, va, even, odd);
    tlb_map(pm, va, even, odd);
}
u32 w2c_env_memcpy(struct w2c_env *e, u32 d, u32 s, u32 n) { (void)e; memmove(M + d, M + s, n); return d; }
u32 w2c_env_memset(struct w2c_env *e, u32 d, u32 c, u32 n) { (void)e; memset(M + d, (int)c, n); return d; }
/* PORT_SEED_REPLAY=file: the seeds a game's random-number generator was given on the console, one number a line, in the order its
   seeding function was called (the emulator's break on that function reports its argument). A port-only override of that function
   (port/wasm/src/) passes the seed it was given through port_seed_replay: the next number from the file while there are any, the
   seed itself after them. A game that seeds from the clock (osGetTime) gets the console's draws, where the port's clock, which
   isn't the console's cycle count, would send each one elsewhere. */
u32 w2c_env_port_seed_replay(struct w2c_env *e, u32 fallback) {
    (void)e;
#ifndef PORT_WEB
    static FILE *file; static int tried;
    if (!tried) { const char *path = getenv("PORT_SEED_REPLAY"); tried = 1; if (path) file = fopen(path, "r"); }
    unsigned long seed;
    if (file && fscanf(file, "%lu", &seed) == 1) return (u32)seed;
#endif
    return fallback;
}
u32 w2c_env_strlen(struct w2c_env *e, u32 p) { (void)e; return (u32)strlen((char *)M + p); }
/* The rest of the C library's string and memory functions the kit counts as the host's (port.py _LIBC): left out of the build
   as the game's own copies, so one the host doesn't define was a stub returning 0: every strncmp said "equal" and every
   strcpy copied nothing. Their results are the C library's (a comparison's sign, the destination). */
u32 w2c_env_memmove(struct w2c_env *e, u32 d, u32 s, u32 n) { (void)e; memmove(M + d, M + s, n); return d; }
u32 w2c_env_memcmp(struct w2c_env *e, u32 a, u32 b, u32 n) { (void)e; return (u32)(int32_t)memcmp(M + a, M + b, n); }
u32 w2c_env_strcmp(struct w2c_env *e, u32 a, u32 b) { (void)e; return (u32)(int32_t)strcmp((char *)M + a, (char *)M + b); }
u32 w2c_env_strncmp(struct w2c_env *e, u32 a, u32 b, u32 n) { (void)e; return (u32)(int32_t)strncmp((char *)M + a, (char *)M + b, n); }
u32 w2c_env_strcpy(struct w2c_env *e, u32 d, u32 s) { (void)e; strcpy((char *)M + d, (char *)M + s); return d; }
u32 w2c_env_strncpy(struct w2c_env *e, u32 d, u32 s, u32 n) { (void)e; strncpy((char *)M + d, (char *)M + s, n); return d; }
u32 w2c_env_strcat(struct w2c_env *e, u32 d, u32 s) { (void)e; strcat((char *)M + d, (char *)M + s); return d; }
u32 w2c_env_strchr(struct w2c_env *e, u32 s, u32 c) { (void)e; char *p = strchr((char *)M + s, (int)c); return p ? (u32)(p - (char *)M) : 0; }
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
/* libm, which port.py's _LIBC says the host provides: without these an import of sinf/cosf was built as an empty
   stub ("sinf: not implemented, skipped"), so a game's rotation matrices came out as NaN (as did its models' and
   cameras'), and a display list built from them could be wrong or never finish. */
f32 w2c_env_sinf(struct w2c_env *e, f32 x) { (void)e; return sinf(x); }
f32 w2c_env_cosf(struct w2c_env *e, f32 x) { (void)e; return cosf(x); }
f32 w2c_env_ldexpf(struct w2c_env *e, f32 x, u32 n) { (void)e; return ldexpf(x, (int)n); }
/* the exponent goes to the game's int, big-endian; port.py lists it with the host's C library (a game's own call to it
   was "not implemented, skipped" and the float it returned was 0) */
f32 w2c_env_frexpf(struct w2c_env *e, f32 x, u32 p) { (void)e; int ex = 0; f32 r = frexpf(x, &ex); if (p) wbe32(p, (u32)ex); return r; }
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
/* Words of a boot chip's decoded boot code that stay in low RAM, where the ROM keeps them scrambled (so no copy from
   the ROM gives them): {physical address, word}, as a game's self-test reads them back */
static const uint32_t cic6106_words[][2] = {{0x164, 0x01EC6021u}, {0x1BC, 0x8FBF001Cu}, {0x234, 0xAD130004u}, {0x2B8, 0x8941680Cu}};
static const struct boot_chip {
    uint32_t crc, entry_less, copy_from, copy_to, copy_at, sp_fill, id;
    uint32_t mark_at, mark_from; /* a word the boot code stores in RAM (its address, and where in the ROM's boot code it
                                    is kept), 0 for none */
    const uint32_t (*words)[2]; unsigned word_count; /* decoded words it leaves in RAM (above) */
} boot_chips[] = {
    {0x90BB6CB5u, 0, 0x4C0, 0x774, 0, 0, 0, 0, 0},                  /* CIC-6102 (7101) */
    {0x009E9EA3u, 0, 0x4C0, 0x750, 0, 0, 0, 0, 0},                  /* CIC-7102 */
    {0x0B050EE0u, 0x100000u, 0x4C0, 0x768, 0, 0xFFFFFFFFu, 6103, 0, 0}, /* CIC-6103 (7103) */
    {0x98BC2C86u, 0, 0x554, 0x888, 4, 0xA4002000u, 6105, 0x002FE1C0u, 0x750},       /* CIC-6105 (7105) */
    /* CIC-6106 (7106): its copy of itself is scrambled as it goes, so the copy isn't made; the RSP's memories are
       filled and tagged the way the 6103's are, with the ID 6104, and the four decoded words a game reads back are
       set (cic6106_words). A racing game made for it runs seven self-tests at boot (DMEM[0] must be -1, IMEM[0]
       0x17D8, and four words of RAM below 0x300 the boot code's) and parks its main thread in a `b .` on purpose
       when one fails. */
    {0xACC8580Au, 0x200000u, 0, 0, 0, 0xFFFFFFFFu, 6104, 0, 0, cic6106_words, 4},
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
    /* The 6105's boot code leaves its own constant (0xAD170014, a word of its data at ROM 0x750) at physical
       0x2FE1C0 in RAM. A game made for it reads the word back and hangs for ever when it isn't there (an
       anti-piracy check at the very start of a platformer's first function: it waited on an uncached read that
       never changed, 0 DMAs, a black window). */
    if (chip->mark_at) wbe32(0x80000000u | chip->mark_at, rom32(chip->mark_from));
    for (unsigned i = 0; i < chip->word_count; i++) wbe32(0x80000000u | chip->words[i][0], chip->words[i][1]);
}
/* The TLB windows a game maps its code through (its entry routine's setup, or osMapTLB later): a 1 MB page pair at
   the virtual address 0x00200000 puts the game's code at what the project spells 0x80200000 (the split names every
   address `0x80000000 | its virtual address`), while the bytes sit at their physical address in RAM (a decompressed
   program, the boot chip's image). The port has no TLB: a low address inside a window is read at its KSEG0 spelling,
   and the interpreter fetches code from the physical alias where nothing was ever written at the spelling. */
#define TLB_WINDOWS 512 /* 4 KB pages a refill handler brings in, merged with the neighbour they continue */
/* real: the window is kept at the physical address it maps. A window whose spelling (0x80000000 | virtual) would sit
   on memory already in use (the game's boot loader and its compressed program at 0x80200000 and up, under a window at
   virtual 0x00200000 that maps physical 0x600000) can't be read at the spelling: the code the game brings in lands at
   the physical address, and a copy at the spelling collided with the loader's own bytes. */
static struct { uint32_t lo, size, phys; uint8_t real; } tlb_win[TLB_WINDOWS];
/* whether the console's memory at a window's spelling holds anything yet: a window may be spelled when it holds nothing */
static int tlb_spelling_in_use(uint32_t lo, uint32_t size) {
    if (lo >= 0x00800000u || size > 0x01000000u || lo + size > 0x00800000u) return 0; /* (higher windows are named by their physical address anyway) */
    for (uint32_t off = 0; off < size; off += 4)
        if (be32(0x80000000u | (lo + off))) return 1;
    return 0;
}
unsigned port_tlb_n;
unsigned port_tlb_hi; /* windows the game's compiled C reads as aliases of RAM (port_tlb_wasm): at 0x00800000 up, and low ones above the module's own data */
/* The module's own low memory: its stack and data sit below `--global-base` plus its data (build.py LINK_FLAGS: 1 MB, and
   the data after it, `__data_end`). A game's low TLB window above that (a code and data page pair at virtual 0x200000)
   names RAM the compiled C would otherwise read as zeros: a class table's function pointers, behind a method slot
   the game calls, read as null. main() raises the floor to the module's data end. */
uint32_t port_wasm_floor = 0x00100000u;
static uint32_t wasm_lo = 0xFFFFFFFFu, wasm_hi; /* the span of the windows port_tlb_wasm answers for */
/* A low window shows the same RAM at two names: the console reads a table at virtual 0x00102330 from physical 0x2330
   (a jump table of a function the project has no C for: the bytes were copied to the boot image at the physical
   address, and nothing wrote them where the port spells the virtual address, 0x80102330). The words of the
   physical alias come to the spelling where it holds nothing yet, once, as the window is made: a jump table read as
   zeros sent a function of such a game to address 0x100. Later writes go to whichever name the code uses. */
static void tlb_mirror(uint32_t lo, uint32_t size, uint32_t phys) {
    if (lo >= 0x00800000u || size > 0x01000000u || lo < 0x00001000u) return; /* (a page at 0 is read at its alias: port_tlb_canon) */
    uint32_t spelled = 0x80000000u | lo, alias = 0x80000000u | phys;
    if (spelled == alias) return;
    for (uint32_t off = 0; off + 4 <= size && spelled + off + 4 <= 0x80800000u && alias + off + 4 <= 0x80800000u; off += 4) {
        uint32_t w = be32(alias + off);
        if (w && !be32(spelled + off)) wbe32(spelled + off, w);
    }
}
/* pm: the page mask register (PM_1M is 0x1FE000), va the pair's virtual address, even/odd the physical addresses of
   its two pages (-1: not mapped) */
static void tlb_spell_span(void);
static void tlb_map(uint32_t pm, uint32_t va, uint32_t even, uint32_t odd) {
    uint32_t size = ((pm >> 13) + 1) << 12;
    if ((pm & ~0x01FFE000u) || size > 0x1000000u) return;
    va &= ~(2 * size - 1);
    for (int half = 0; half < 2; half++) {
        uint32_t phys = half ? odd : even, lo = va + (uint32_t)half * size;
        /* (KSEG0 and KSEG1, 0x80000000 to 0xBFFFFFFF, are not mapped by the TLB; KSEG2 and KSEG3, 0xC0000000 up, are: a
           game that maps its 8 MB of RAM at 0xE0000000, where its loader decompresses to, needs them) */
        if (phys == 0xFFFFFFFFu || (lo >= 0x80000000u && lo < 0xC0000000u) || phys >= 0x00800000u) continue;
        unsigned i = 0;
        while (i < port_tlb_n && tlb_win[i].lo != lo) i++;
        /* a window made now is spelled (0x80000000 | virtual) only where the spelling holds nothing: a page of the boot
           image or the loader's data there is the loader's own, not the program's. A spelled window is kept at the
           physical address it maps once the game puts data of its own at its spelling (port_tlb_physical_use). */
        uint8_t real = i < port_tlb_n ? tlb_win[i].real : (uint8_t)(lo != phys && tlb_spelling_in_use(lo, size));
        if (getenv("PORT_TLB_TRACE")) fprintf(stderr, "window %08X + %X -> physical %08X%s\n", lo, size, phys, real ? " (kept there)" : "");
        if (!real) tlb_mirror(lo, size, phys);
        if (i == port_tlb_n) {
            /* a page that continues a window (the next virtual page onto the next physical one) extends it: a program
               brought in page by page is one window, not hundreds */
            for (unsigned j = 0; j < port_tlb_n; j++)
                if (tlb_win[j].lo + tlb_win[j].size == lo && tlb_win[j].phys + tlb_win[j].size == phys && tlb_win[j].size < 0x01000000u && tlb_win[j].real == real) {
                    tlb_win[j].size += size;
                    if (tlb_win[j].lo + tlb_win[j].size > wasm_hi && (tlb_win[j].lo >= 0x00800000u || tlb_win[j].lo >= port_wasm_floor)) wasm_hi = tlb_win[j].lo + tlb_win[j].size;
                    phys = 0xFFFFFFFFu;
                    break;
                }
            if (phys == 0xFFFFFFFFu) continue;
        }
        if (i == port_tlb_n && port_tlb_n == TLB_WINDOWS) return;
        tlb_win[i].lo = lo; tlb_win[i].size = size; tlb_win[i].phys = phys; tlb_win[i].real = real;
        if (i == port_tlb_n) port_tlb_n++;
        if (lo >= 0x00800000u || lo >= port_wasm_floor) {
            port_tlb_hi = 1;
            if (lo < wasm_lo) wasm_lo = lo;
            if (lo + size > wasm_hi) wasm_hi = lo + size;
        }
    }
    tlb_spell_span();
}
/* The interpreter's `tlbwi`/`tlbwr`: the registers the game's own code wrote (a game that sets its TLB up in its
   first C function, with mtc0 and a tlbwi, as the entry routine's osMapTLB-style call does for others). An EntryLo
   word's page frame is bits 6 and up, valid in bit 1. */
/* The 32 entries of the console's TLB as the game's own code wrote them (tlbwi at the Index register, tlbwr at the
   next free slot): tlbp and tlbr read them back, and an entry whose half has the valid bit clear is remembered, so a
   fetch from that page raises the TLB Invalid exception (the general vector) rather than the refill one. A game that
   pages its program in as it runs (a shooter whose general handler loads the page from the cartridge and writes
   the entry again, valid) needs both. */
#define TLB_ENTRIES 32
static struct { uint32_t pm, hi, lo0, lo1; int used; } tlb_ent[TLB_ENTRIES];
/* a page [lo, lo + size) leaves the windows (the entry that mapped it was overwritten): a window it was part of is
   cut into what remains on either side */
static void tlb_unmap_page(uint32_t lo, uint32_t size) {
    for (unsigned j = 0; j < port_tlb_n; j++) {
        uint32_t wl = tlb_win[j].lo, ws = tlb_win[j].size, wp = tlb_win[j].phys;
        if (lo < wl || lo + size > wl + ws) continue;
        uint32_t before = lo - wl, after = wl + ws - (lo + size);
        if (before) {
            tlb_win[j].size = before;
            if (after && port_tlb_n < TLB_WINDOWS) {
                tlb_win[port_tlb_n].lo = lo + size; tlb_win[port_tlb_n].size = after;
                tlb_win[port_tlb_n].phys = wp + before + size;
                tlb_win[port_tlb_n].real = tlb_win[j].real;
                port_tlb_n++;
            }
        } else if (after) {
            tlb_win[j].lo = lo + size; tlb_win[j].size = after; tlb_win[j].phys = wp + size;
        } else {
            tlb_win[j] = tlb_win[--port_tlb_n];
        }
        return;
    }
}
static void tlb_entry_pages(uint32_t pm, uint32_t hi, uint32_t *va, uint32_t *size) {
    *size = ((pm >> 13) + 1) << 12;
    *va = hi & ~0x1FFFu & ~(2 * *size - 1);
}
static void tlb_set_entry(unsigned index, uint32_t pm, uint32_t entry_hi, uint32_t lo0, uint32_t lo1, int unmap_old) {
    index &= TLB_ENTRIES - 1;
    if (getenv("PORT_TLB_TRACE"))
        fprintf(stderr, "tlb[%u] = mask %08X hi %08X lo0 %08X lo1 %08X\n", index, pm, entry_hi, lo0, lo1);
    if (unmap_old && tlb_ent[index].used && !(tlb_ent[index].pm & ~0x01FFE000u)) {
        uint32_t va, size;
        tlb_entry_pages(tlb_ent[index].pm, tlb_ent[index].hi, &va, &size);
        for (int half = 0; half < 2; half++)
            if ((half ? tlb_ent[index].lo1 : tlb_ent[index].lo0) & 2) tlb_unmap_page(va + (uint32_t)half * size, size);
    }
    tlb_ent[index].pm = pm; tlb_ent[index].hi = entry_hi; tlb_ent[index].lo0 = lo0; tlb_ent[index].lo1 = lo1;
    tlb_ent[index].used = 1;
    tlb_map(pm, entry_hi & ~0x1FFFu, (lo0 & 2) ? (lo0 >> 6) << 12 : 0xFFFFFFFFu, (lo1 & 2) ? (lo1 >> 6) << 12 : 0xFFFFFFFFu);
}
/* tlbwi: the slot the Index register names; what that slot mapped before is gone */
void port_tlb_set(unsigned index, uint32_t pm, uint32_t entry_hi, uint32_t lo0, uint32_t lo1) {
    tlb_set_entry(index, pm, entry_hi, lo0, lo1, 1);
}
/* tlbwr: the console picks a slot at random above the wired ones (the Wired register: a game keeps its permanent
   mappings below it); a free one here, the oldest once all are taken. What a reused slot mapped before is gone, as on the console: a program of a megabyte brought in page by page is
   more pages than the console holds at once, and it faults on the evicted ones and refills them. A paging game depends
   on that: it reuses the page's frame for another page and trusts the stale entry to be gone (keeping it mapped, as the
   port first did to spare the refills, ran the old page's address into the new page's code). */
static unsigned tlb_wired_slots;
uint32_t port_tlb_wired(int set, uint32_t value) {
    if (set) tlb_wired_slots = value > TLB_ENTRIES ? TLB_ENTRIES : value;
    return tlb_wired_slots;
}
void port_tlb_write(uint32_t pm, uint32_t entry_hi, uint32_t lo0, uint32_t lo1) {
    static unsigned next;
    unsigned wired = tlb_wired_slots < TLB_ENTRIES ? tlb_wired_slots : TLB_ENTRIES - 1; /* (all 32 wired: the last takes it) */
    unsigned index = wired;
    while (index < TLB_ENTRIES && tlb_ent[index].used) index++;
    if (index == TLB_ENTRIES) index = wired + next++ % (TLB_ENTRIES - wired);
    tlb_set_entry(index, pm, entry_hi, lo0, lo1, tlb_ent[index].used);
}
/* tlbp: the slot whose virtual page pair holds EntryHi's address, or 0x80000000 */
uint32_t port_tlb_probe(uint32_t entry_hi) {
    for (unsigned i = 0; i < TLB_ENTRIES; i++) {
        if (!tlb_ent[i].used || (tlb_ent[i].pm & ~0x01FFE000u)) continue;
        uint32_t va, size;
        tlb_entry_pages(tlb_ent[i].pm, tlb_ent[i].hi, &va, &size);
        if ((entry_hi & ~0x1FFFu & ~(2 * size - 1)) == va) return i;
    }
    return 0x80000000u;
}
/* libultra's TLB routines the game's own pager uses (hand-written assembly the port does not run: the entries live in this
   table): __osGetTLBHi(i) and its siblings are `mtc0 i, Index; tlbr; mfc0 EntryHi / EntryLo0 / EntryLo1 / PageMask`, and
   osUnmapTLB(i) writes the slot back as an unmapped page. A paging game finds the slot that holds the page it is
   replacing by reading every slot's EntryHi, and unmaps it, so the next access takes a refill and its handler loads
   the new page. A slot nothing wrote reads as an address no program maps (KSEG0). */
uint32_t w2c_env_0x5F_osGetTLBHi(struct w2c_env *e, uint32_t index) {
    (void)e;
    index &= TLB_ENTRIES - 1;
    return tlb_ent[index].used ? tlb_ent[index].hi : 0x80000000u;
}
uint32_t w2c_env_0x5F_osGetTLBLo0(struct w2c_env *e, uint32_t index) { (void)e; index &= TLB_ENTRIES - 1; return tlb_ent[index].used ? tlb_ent[index].lo0 : 0; }
uint32_t w2c_env_0x5F_osGetTLBLo1(struct w2c_env *e, uint32_t index) { (void)e; index &= TLB_ENTRIES - 1; return tlb_ent[index].used ? tlb_ent[index].lo1 : 0; }
uint32_t w2c_env_0x5F_osGetTLBPageMask(struct w2c_env *e, uint32_t index) { (void)e; index &= TLB_ENTRIES - 1; return tlb_ent[index].used ? tlb_ent[index].pm : 0; }
void w2c_env_osUnmapTLB(struct w2c_env *e, uint32_t index) {
    (void)e;
    index &= TLB_ENTRIES - 1;
    if (getenv("PORT_TLB_TRACE")) fprintf(stderr, "tlb[%u] unmapped\n", index);
    if (tlb_ent[index].used && !(tlb_ent[index].pm & ~0x01FFE000u)) {
        uint32_t va, size;
        tlb_entry_pages(tlb_ent[index].pm, tlb_ent[index].hi, &va, &size);
        for (int half = 0; half < 2; half++)
            if ((half ? tlb_ent[index].lo1 : tlb_ent[index].lo0) & 2) tlb_unmap_page(va + (uint32_t)half * size, size);
    }
    memset(&tlb_ent[index], 0, sizeof tlb_ent[index]);
}
/* tlbr */
int port_tlb_read(unsigned index, uint32_t *pm, uint32_t *entry_hi, uint32_t *lo0, uint32_t *lo1) {
    index &= TLB_ENTRIES - 1;
    if (!tlb_ent[index].used) return 0;
    *pm = tlb_ent[index].pm; *entry_hi = tlb_ent[index].hi; *lo0 = tlb_ent[index].lo0; *lo1 = tlb_ent[index].lo1;
    return 1;
}
/* an address whose page an entry covers with the valid bit clear: a fetch or access there is a TLB Invalid exception */
int port_tlb_invalid(uint32_t a) {
    int invalid = 0;
    for (unsigned i = 0; i < TLB_ENTRIES; i++) {
        if (!tlb_ent[i].used || (tlb_ent[i].pm & ~0x01FFE000u)) continue;
        uint32_t va, size;
        tlb_entry_pages(tlb_ent[i].pm, tlb_ent[i].hi, &va, &size);
        if (a < va || a - va >= 2 * size) continue;
        if ((a - va >= size ? tlb_ent[i].lo1 : tlb_ent[i].lo0) & 2) return 0; /* another entry maps it, valid */
        invalid = 1;
    }
    return invalid;
}
/* an address inside a window: the spelling the port keeps it at. A window in the low 8 MB of virtual addresses is
   what the split spells `0x80000000 | virtual` (the bytes at their physical alias); a window higher up (a game that
   maps its code at 0x10000000) can only be named by the physical address it maps, in KSEG0, as the split does. */
uint32_t port_tlb_canon(uint32_t a) {
    for (unsigned i = 0; (a < 0x80000000u || a >= 0xC0000000u) && i < port_tlb_n; i++)
        if (a >= tlb_win[i].lo && a - tlb_win[i].lo < tlb_win[i].size) {
            /* a page at virtual 0 spelled `0x80000000 | virtual` is where libultra's osInitialize writes the exception
               vectors (0x80000000, 0x80000080, 0x80000100, 0x80000180): a game that keeps its own call dispatcher
               there (`jr $zero` thunks, a table-driven overlay linker) had it overwritten by the vector stubs.
               Such a page is kept at the physical address its bytes are at. */
            if (tlb_win[i].lo < 0x00001000u || tlb_win[i].real) return 0x80000000u | (tlb_win[i].phys + (a - tlb_win[i].lo));
            return tlb_win[i].lo < 0x00800000u ? a | 0x80000000u : 0x80000000u | (tlb_win[i].phys + (a - tlb_win[i].lo));
        }
    return a;
}
/* A call the project's analysis spelled against KSEG0 (`jal 0x00347080` resolved with the top bits 8: func_80347080)
   that lies in the virtual range of a window kept at its physical address. The game's second stage meant the code
   its window maps; the spelling is where the boot loader's own bytes are, and entering there ran the loader's
   decompressor on garbage. Once such a window exists the loader is long done: a call there goes to the window's
   physical address. */
/* the name the project gives code a window kept at its physical address holds: `0x80000000 | virtual` (the split
   spells func_80100418 where the console runs it at virtual 0x00100418, and it is at physical 0x418); a function
   with C or a library identity is known by that name */
/* The game puts its own data at a physical address a window spells its virtual range at (a transfer into KSEG0
   0x80120680 under a window at virtual 0x00100000 that maps physical 0): the spelling is memory in use, and a table
   the code reads through the window (a jump table at virtual 0x00122B60, physical 0x22B60) read the strings
   transferred there instead. From now on the window is kept at the physical address it maps. */
void port_tlb_physical_use(uint32_t a, uint32_t size) {
    if (a >= 0x80800000u) return;
    for (unsigned i = 0; i < port_tlb_n; i++) {
        if (tlb_win[i].real || tlb_win[i].lo >= 0x00800000u || tlb_win[i].lo == tlb_win[i].phys) continue;
        uint32_t s = 0x80000000u | tlb_win[i].lo;
        if (a < s + tlb_win[i].size && a + size > s) {
            tlb_win[i].real = 1;
            if (getenv("PORT_TLB_TRACE")) fprintf(stderr, "window %08X + %X: the game's own data is at its spelling (%08X): kept at physical %08X from now on\n", tlb_win[i].lo, tlb_win[i].size, a, tlb_win[i].phys);
        }
    }
    tlb_spell_span();
}
/* The span of the console's memory the windows not kept at their physical address are spelled at (0x80000000 | virtual).
   A store of the game's own into it, through a KSEG0 name and not the window's virtual one, is data of its own at the
   spelling, as a transfer there is (port_tlb_physical_use): a game whose loader copies its tables with stores, under a
   window at virtual 0x200000 onto physical 0, had its code (at the physical alias) read as those tables. */
uint32_t port_spell_lo = 0xFFFFFFFFu, port_spell_len;
/* and the span of the spellings of the windows kept at their physical address: a jump there goes to the physical alias
   (port_tlb_call), as the console runs the window's code, not what sits at the spelling */
uint32_t port_real_lo = 0xFFFFFFFFu, port_real_len;
static void tlb_spell_span(void) {
    uint32_t lo = 0xFFFFFFFFu, hi = 0, rlo = 0xFFFFFFFFu, rhi = 0;
    for (unsigned i = 0; i < port_tlb_n; i++) {
        if (tlb_win[i].lo >= 0x00800000u || tlb_win[i].lo == tlb_win[i].phys || tlb_win[i].lo < 0x00001000u) continue;
        uint32_t s = 0x80000000u | tlb_win[i].lo;
        if (tlb_win[i].real) {
            if (s < rlo) rlo = s;
            if (s + tlb_win[i].size > rhi) rhi = s + tlb_win[i].size;
        } else {
            if (s < lo) lo = s;
            if (s + tlb_win[i].size > hi) hi = s + tlb_win[i].size;
        }
    }
    port_spell_lo = lo;
    port_spell_len = hi > lo ? hi - lo : 0;
    port_real_lo = rlo;
    port_real_len = rhi > rlo ? rhi - rlo : 0;
}
void port_tlb_spell_store(uint32_t a) {
    port_tlb_physical_use(a, 4);
    tlb_spell_span();
}
uint32_t port_tlb_spelled(uint32_t a) {
    if (!port_tlb_n || a < 0x80000000u || a >= 0x80800000u) return a;
    uint32_t p = a & 0x1FFFFFFFu;
    for (unsigned i = 0; i < port_tlb_n; i++)
        if (tlb_win[i].real && tlb_win[i].lo < 0x00800000u && p >= tlb_win[i].phys && p - tlb_win[i].phys < tlb_win[i].size)
            return 0x80000000u | (tlb_win[i].lo + (p - tlb_win[i].phys));
    return a;
}
/* A call target the project spelled in KSEG0 that no console has RAM at (0x85016170) and that names code a high window
   maps: a `jal 0x05016170` made from code the project places in KSEG0 takes its top four bits from there (8), though
   the game ran it at the window's virtual address (0x15016170). The window's own top bits are the ones the game's
   TLB entries use: a game that maps its code at one base (0x15xxxxxx) has exactly one top nibble below 8 among its
   windows. Returns the virtual address (so a mapped page translates, and an evicted one takes the game's refill
   handler, as the console would), or the address itself when no single base is known. */
uint32_t port_tlb_virtual(uint32_t a) {
    if (!port_tlb_n || a < 0x80800000u || a >= 0xA0000000u) return a;
    uint32_t v = a & 0x0FFFFFFFu, nibble = 0;
    int found = 0;
    for (unsigned i = 0; i < port_tlb_n; i++) {
        if (tlb_win[i].lo < 0x00800000u || tlb_win[i].lo >= 0x80000000u) continue;
        uint32_t n = tlb_win[i].lo >> 28;
        if (found && n != nibble) return a; /* two bases: ambiguous */
        nibble = n;
        found = 1;
    }
    return found ? (nibble << 28) | v : a;
}
/* The virtual address the code in a physical frame has, for code the interpreter reached by the frame's KSEG0 name (a
   `jal 0x802FA000` the project spelled from the physical address: the name a high window's code has, whatever page
   of it the game brought there). The next page of that code is the next VIRTUAL page, which the game's TLB maps to
   any frame (a paging game puts consecutive pages in frames far apart): running on into the next frame ran another
   function's bytes, and a loop around the call spun for ever. Only a high window's frame is told (lo at 8 MB or
   more: the split names those by the physical address). A frame the game maps twice, as a page of its code and
   inside a large window that mirrors all of RAM (virtual 0x10000000 onto physical 0), is told by the page: the large
   window is contiguous, so the code it runs sees no difference, and the page is the one the pager set up. Two
   pages of the same size mapping it are ambiguous. */
uint32_t port_tlb_virtual_of(uint32_t a) {
    if (!port_tlb_n || a < 0x80000000u || a >= 0x80800000u) return 0;
    uint32_t p = a & 0x1FFFFFFFu, v = 0, best = 0xFFFFFFFFu;
    for (unsigned i = 0; i < port_tlb_n; i++) {
        if (tlb_win[i].lo < 0x00800000u || tlb_win[i].lo >= 0x80000000u) continue;
        if (p >= tlb_win[i].phys && p - tlb_win[i].phys < tlb_win[i].size) {
            uint32_t c = tlb_win[i].lo + (p - tlb_win[i].phys);
            if (tlb_win[i].size < best) { best = tlb_win[i].size; v = c; }
            else if (tlb_win[i].size == best && v != c) v = 0; /* two equal windows: which one is unknown */
        }
    }
    return v;
}
uint32_t port_tlb_call(uint32_t a) {
    if (!port_tlb_n || a < 0x80000000u || a >= 0x80800000u) return a;
    uint32_t v = a & 0x7FFFFFFFu;
    for (unsigned i = 0; i < port_tlb_n; i++)
        if (tlb_win[i].real && v >= tlb_win[i].lo && v - tlb_win[i].lo < tlb_win[i].size)
            return 0x80000000u | (tlb_win[i].phys + (v - tlb_win[i].lo));
    return a;
}
/* The game's compiled C (WebAssembly) reaches a window through an address like 0x100010F8: the loads and stores the
   build writes (port/wasm/bemem.py) ask here once a window high in the virtual space exists, and read the same RAM at
   its KSEG0 spelling. Windows in the low 8 MB are left alone: the module's own low memory lives there. */
uint32_t port_tlb_wasm(uint32_t a) {
    if (a < wasm_lo || a >= wasm_hi) return a; /* (the stack and the module's data are below every window) */
    for (unsigned i = 0; i < port_tlb_n; i++) {
        if (a < tlb_win[i].lo || a - tlb_win[i].lo >= tlb_win[i].size) continue;
        if (tlb_win[i].lo >= 0x00800000u) return 0x80000000u | (tlb_win[i].phys + (a - tlb_win[i].lo));
        /* a low window above the module's data is named as the interpreter and the DMA name it (port_tlb_canon): its
           spelling 0x80000000 | virtual, or the physical address where the window was kept there */
        if (tlb_win[i].lo >= port_wasm_floor) return port_tlb_canon(a);
    }
    return a;
}
/* The address a `j`/`jal` at the (delay slot) address `slot` goes to, given its 26-bit field `imm` (already shifted).
   The console takes the jump's top four bits from the virtual address the code runs at. Code a game links at a
   window's virtual base (a program at 0x70000400 mapped onto physical 0) is kept by the port at its KSEG0
   spelling, so the top bits came out 8 and a `jal 0x7F000CD0` went to 0x8F000CD0: "no instructions". When the
   code sits in a high window, the window's top bits are tried, and used when that target is itself mapped or the
   plain one lies outside the console's RAM. */
int port_tlb_covers(uint32_t v) {
    for (unsigned i = 0; i < port_tlb_n; i++)
        if (v >= tlb_win[i].lo && v - tlb_win[i].lo < tlb_win[i].size) return 1;
    return 0;
}
uint32_t port_tlb_jump(uint32_t slot, uint32_t imm) {
    uint32_t plain = (slot & 0xF0000000u) | imm;
    if (!port_tlb_n || slot < 0x80000000u) return plain;
    /* Code in a low window (a program the port keeps at its spelling 0x80000000 | virtual) runs at a virtual address
       whose top four bits are 0, so its `jal 0x00340000` goes to the window's virtual 0x340000, not to the
       KSEG0 0x80340000 the spelled slot gives (where a boot loader's own function lived: a game's second stage called
       the loader's decompressor instead of the library it had brought in). A target a window maps is named as the
       window keeps it. */
    for (unsigned i = 0; i < port_tlb_n && slot < 0x80800000u; i++) {
        if (tlb_win[i].lo >= 0x00800000u) continue;
        uint32_t v = slot & 0x7FFFFFFFu, p = slot & 0x1FFFFFFFu;
        /* (code of a window kept at its physical address that began running at the spelling, before the window was
           switched, is still at the spelling) */
        int inside = (v >= tlb_win[i].lo && v - tlb_win[i].lo < tlb_win[i].size) ||
                     (tlb_win[i].real && p >= tlb_win[i].phys && p - tlb_win[i].phys < tlb_win[i].size);
        if (!inside) continue;
        uint32_t target = (tlb_win[i].lo & 0xF0000000u) | imm;
        if (port_tlb_covers(target)) return port_tlb_canon(target);
        break;
    }
    uint32_t phys = slot & 0x1FFFFFFFu;
    for (unsigned i = 0; i < port_tlb_n; i++) {
        if (tlb_win[i].lo < 0x00800000u || phys < tlb_win[i].phys || phys - tlb_win[i].phys >= tlb_win[i].size) continue;
        uint32_t v = (tlb_win[i].lo & 0xF0000000u) | imm;
        /* mapped now, or a plain target no RAM could hold (a page the game's refill handler brings in when it is first
           run: the interpreter's fetch takes that exception) */
        if (v != plain && (port_tlb_covers(v) || plain >= 0x80800000u)) return v;
    }
    return plain;
}
/* where the bytes of code at a spelling live in RAM, 0 when no window covers it */
uint32_t port_tlb_alias(uint32_t a) {
    uint32_t v = a & 0x7FFFFFFFu;
    for (unsigned i = 0; (a & 0x80000000u) && i < port_tlb_n; i++)
        if (!tlb_win[i].real && v >= tlb_win[i].lo && v - tlb_win[i].lo < tlb_win[i].size) return 0x80000000u | (tlb_win[i].phys + (v - tlb_win[i].lo));
    return 0;
}
static uint32_t boot_stk[16]; /* the words the entry routine stored at 0($sp)...: osMapTLB's fifth and sixth arguments */
/* The effect of one instruction of the entry routine on the registers it builds addresses in (not a branch). */
static void boot_step(uint32_t *r, uint32_t w) {
    uint32_t op = w >> 26, rs = (w >> 21) & 31, rt = (w >> 16) & 31, rd = (w >> 11) & 31, fn = w & 0x3F;
    int32_t imm = (int16_t)(w & 0xFFFF);
    if (op == 0x2B && rs == 29 && imm >= 0 && imm < 64 && !(imm & 3)) boot_stk[imm / 4] = r[rt]; /* sw to the stack */
    if (op == 0x0F) r[rt] = (w & 0xFFFF) << 16;                                          /* lui */
    else if (op == 0x08 || op == 0x09) r[rt] = r[rs] + (uint32_t)imm;                    /* addi, addiu */
    else if (op == 0x0D) r[rt] = r[rs] | (w & 0xFFFF);                                   /* ori */
    else if (op == 0 && (fn == 0x20 || fn == 0x21)) r[rd] = r[rs] + r[rt];               /* add, addu (move) */
    else if (op == 0 && fn == 0x25) r[rd] = r[rs] | r[rt];                               /* or */
    r[0] = 0;
}
/* Whether a call in the entry routine reaches a loop that stores $a2 through $a0 a word at a time: the helper some
   entry routines fill a stack with a pattern and clear their BSS with (`jal fill` with $a0 = start, $a1 = end,
   $a2 = the word), which is not the game's first function. */
static int boot_is_sw_zero(uint32_t w) { return (w >> 26) == 0x2B && ((w >> 16) & 31) == 0; }
static int boot_is_fill(uint32_t at) {
    if (at < boot.entry || at - boot.entry >= 0x100000u) return 0;
    size_t off = 0x1000 + (at - boot.entry);
    int stores = 0, loops = 0;
    for (int k = 0; k < 10 && off + 4 * (size_t)k + 4 <= rom_size; k++) {
        uint32_t w = rom32(off + 4 * (size_t)k), op = w >> 26;
        if (op == 0x2B && ((w >> 21) & 31) == 4 && ((w >> 16) & 31) == 6 && !(w & 0xFFFF)) stores = 1; /* sw $a2, 0($a0) */
        if ((op == 0x04 || op == 0x05) && (int16_t)(w & 0xFFFF) < 0) loops = 1;                      /* a branch back */
    }
    return stores && loops;
}
static int read_boot(void) {
    uint32_t r[32] = {0}, pre[32] = {0}, sltu_end = 0;
    int base = -1, sltu_rd = -1;
    memset(boot_stk, 0, sizeof boot_stk);
    port_tlb_n = 0;
    memset(tlb_ent, 0, sizeof tlb_ent);
    port_tlb_hi = 0;
    wasm_lo = 0xFFFFFFFFu;
    wasm_hi = 0;
    boot.entry = boot_entry();
    /* a call is the game's first function when only a `break` follows it; one with more code after it is a helper
       (a fill, a TLB setup) the routine returns from */
    int last_jump = -1;
    for (int i = 0; i < 64; i++) {
        uint32_t w = rom32(0x1000 + 4 * (size_t)i), op = w >> 26;
        if (op == 2 && ((boot.entry & 0xF0000000u) | (w & 0x03FFFFFFu) << 2) == boot.entry + 4 * (uint32_t)i) continue; /* `j .` */
        if (op == 2 || op == 3 || (op == 0 && ((w & 0x3F) == 8 || (w & 0x3F) == 9) && ((w >> 21) & 31) != 31)) last_jump = i;
    }
    for (int i = 0; i < 64; i++) {
        uint32_t w = rom32(0x1000 + 4 * (size_t)i), op = w >> 26, rs = (w >> 21) & 31, rt = (w >> 16) & 31;
        int32_t imm = (int16_t)(w & 0xFFFF);
        int call = op == 0x03 || (op == 0 && (w & 0x3F) == 9), jump = op == 0x02 || (op == 0 && (w & 0x3F) == 8 && rs != 31);
        if (call || jump) {
            uint32_t to = op == 0 ? r[rs] : (boot.entry & 0xF0000000u) | (w & 0x03FFFFFFu) << 2;
            uint32_t after = rom32(0x1000 + 4 * (size_t)i + 8);
            int ends = jump || (after & 0xFC00003Fu) == 0x0D || i >= last_jump;           /* break, or no jump follows */
            boot_step(r, rom32(0x1000 + 4 * (size_t)i + 4));                              /* the delay slot runs first */
            if (ends) { boot.target = to; break; }
            if (op == 0x03 && boot_is_fill(to) && !r[6] && r[5] > r[4] && !boot.bss_size) { /* the zero fill: the BSS */
                boot.bss = r[4]; boot.bss_size = r[5] - r[4];
            } else if (op == 0x03 && r[4] < 32 && r[5] && !(r[5] & ~0x01FFE000u)) { /* the TLB setup: osMapTLB(index, mask, va, even, odd) */
                tlb_map(r[5], r[6], r[7], boot_stk[4]);
            }
            i++;
            continue;
        }
        if (op == 0x05 && base < 0 && rt == 0 && imm < 0 && boot_is_sw_zero(rom32(0x1000 + 4 * (size_t)i + 4)) && sltu_rd == (int)rs) {
            /* the store is the branch's delay slot (`sltu $at, $t0, $t1; bnez $at, loop; sw $zero, -4($t0)`): the
               loop clears from the store's address up to the register it compares with */
            uint32_t slot = rom32(0x1000 + 4 * (size_t)i + 4);
            boot.bss = r[(slot >> 21) & 31] + (uint32_t)(int16_t)(slot & 0xFFFF); base = (int)((slot >> 21) & 31);
            memcpy(pre, r, sizeof r);
            if (r[sltu_end] > boot.bss) boot.bss_size = r[sltu_end] - boot.bss;
            i++;                                                                         /* (the slot is read) */
        } else if (op == 0x2B && rt == 0 && base < 0) {                                  /* sw $zero: the BSS */
            boot.bss = r[rs] + (uint32_t)imm; base = (int)rs; memcpy(pre, r, sizeof r);
        } else if (op == 0x05 && base >= 0 && !boot.bss_size) {                         /* bne: the loop, back */
            if (rt == 0 && (int)rs != base) boot.bss_size = pre[rs];                     /* a count down */
            else if (rt) boot.bss_size = (rs == (uint32_t)base ? pre[rt] : pre[rs]) - boot.bss; /* up to an end */
        } else {
            if (op == 0 && (w & 0x3F) == 0x2B) { sltu_rd = (int)((w >> 11) & 31); sltu_end = (w >> 16) & 31; } /* sltu */
            boot_step(r, w);
        }
    }
    /* a game that maps its code through the TLB at the address it has in RAM starts at that virtual address (a
       `jalr` to 0x00292E00): the same code runs at its KSEG0 address */
    if (boot.target && boot.target < 0x00800000u) boot.target |= 0x80000000u;
    /* the header's region: Europe and Australia are PAL, Brazil MPAL, the rest NTSC (osTvType 0, 2, 1) */
    char region = rom_size > 0x3E ? (char)rom[0x3E] : 'E';
    boot.tv = strchr("DFHIPSUWXY", region) ? 0 : region == 'B' ? 2 : 1;
    return boot.entry >= 0x80000000u && boot.target >= 0x80000000u;
}
/* osMemSize: what the boot chip's memory test leaves for libultra. The host maps 8 MB of RAM for every game, and a
   console with the Expansion Pak is what a player has: a game that needs the pak refuses to run on 4 MB (it shows
   "N64 Expansion Pak not installed" and goes no further), while one that works on 4 MB reads the larger size as an
   option (hi-res modes, a bigger heap). So 8 MB is the default; PORT_MEMSIZE ("4" or "8" in MB, or a byte count)
   gives a game the console it was meant for (Play passes the project's `rom.memory_size` setting that way). */
static uint32_t console_memory(void) {
#ifdef PORT_WEB
    return 0x800000u;
#else
    const char *v = getenv("PORT_MEMSIZE");
    unsigned long n = v ? strtoul(v, NULL, 0) : 0;
    if (n >= 1 && n <= 8) n <<= 20;
    return n == 0x400000ul ? 0x400000u : 0x800000u;
#endif
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
    for (unsigned i = 0; i < port_tlb_n; i++) if (!tlb_win[i].real) tlb_mirror(tlb_win[i].lo, tlb_win[i].size, tlb_win[i].phys); /* (the image is in) */
    wbe32(SP_STATUS, 1);              /* the RSP is halted until a task starts it */
    wbe32(0x80000300, (uint32_t)boot.tv); /* osTvType */
    wbe32(0x80000308, 0xB0000000);    /* osRomBase */
    wbe32(0x8000030C, 0);             /* osResetType: cold */
    wbe32(0x80000318, console_memory()); /* osMemSize */
    wbe32(0x800003F0, console_memory()); /* the boot chip's RAM test also leaves the size here (the console's RDRAM total) */
    for (unsigned i = 0; i < port_tlb_n; i++) LOG("TLB: virtual %08X + %X is physical %08X", tlb_win[i].lo, tlb_win[i].size, tlb_win[i].phys);
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
static uint64_t monotonic_us(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (uint64_t)t.tv_sec * 1000000 + (uint64_t)t.tv_nsec / 1000;
}
#ifndef PORT_WEB
/* PORT_VTIME_TRACE=file: "TASK VI" lines (the emulator's EMU_TASK_LOG): the retrace the console was at when it started each graphics
   task, kept by task number in `vtrace`. */
static void vtrace_load(const char *path) {
    FILE *tf = fopen(path, "r");
    unsigned t, v;
    while (tf && fscanf(tf, "%u %u", &t, &v) == 2) {
        if (t >= vtrace_n) {
            unsigned n = t + 4096;
            vtrace = realloc(vtrace, (size_t)n * sizeof *vtrace);
            for (unsigned k = vtrace_n; k < n; k++) vtrace[k] = 0;
            vtrace_n = n;
        }
        vtrace[t] = v;
    }
    if (tf) fclose(tf);
}
#endif
/* The audio interface can drain several buffers between VI interrupts. Give the game a chance to refill them. */
static void wait_retrace(void) {
    if (vtime) { /* PORT_VTIME: when every game thread is waiting (or after 3 s of wall time, for one that spins) */
        uint64_t limit = monotonic_us() + 3000000;
        int idle = 0;
        while (idle < 3 && monotonic_us() < limit) {
            pthread_mutex_lock(&lock);
            /* no thread running, or only the idle thread: a console spins in its lowest-priority thread until an
               interrupt switches away, so a lone ready thread no other thread ranks below is the CPU with nothing to do */
            int none = booted && running == NULL;
            if (booted && running) {
                unsigned ready = 0;
                int lowest = 1;
                for (PortThread *p = threads; p; p = p->next) {
                    ready += p->state == READY;
                    if (p != running && p->pri < running->pri) lowest = 0;
                }
                none = ready == 1 && lowest;
            }
            pthread_mutex_unlock(&lock);
            idle = none ? idle + 1 : 0;
            if (none) dp_deliver(1); /* the RCP finishes while the CPU has nothing to do */
            usleep(none ? 100 : 50);
        }
        if (idle < 3) {
            vtime_forced++;
            if (vtime_forced <= 3 || vtime_forced % 100 == 0) {
                pthread_mutex_lock(&lock);
                int n = running ? running->number : -1, pri = running ? running->pri : 0;
                unsigned ready = 0;
                for (PortThread *p = threads; p; p = p->next) ready += p->state == READY;
                pthread_mutex_unlock(&lock);
                LOG("virtual time: retrace %lu fired with thread %d (priority %d) running, %u ready", vi_count, n, pri, ready);
            }
        }
        /* idle: the next retrace is due, or late by the time the console's trace put the clock ahead of it */
        if (vclock_us < vnext_us) vclock_us = vnext_us;
        vnext_us += (unsigned)retrace_us();
        vsub = 0;
        return;
    }
    uint64_t deadline = monotonic_us() + (unsigned)retrace_us();
    uint64_t time;
    while ((time = monotonic_us()) < deadline) {
        uint64_t left = deadline - time;
        usleep((useconds_t)(left < 1000 ? left : 1000));
        dp_deliver(0);
        port_audio_interrupts();
    }
}
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
extern uint32_t port_interp_pc;
static void sample(void) {
    fprintf(stderr, "interpreter pc: %08X\n", port_interp_pc);
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
        fprintf(stderr, "  running: %d\n", running ? running->number : -1);
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

/* The time limit (the second argument), kept even when game code never reaches a safe point: the retrace loop below
   needs the scheduler's lock, so a first function or thread that loops without one (a loader, an idle loop) kept the
   host running, and a core busy, for an hour after its time was up. */
static double watchdog_seconds;
static void *watchdog(void *arg) {
    (void)arg;
    usleep((useconds_t)((watchdog_seconds + 5) * 1000000));
    fprintf(stderr, "after %.1f s: %lu retraces, %lu DMAs, %lu graphics and %lu audio tasks, %lu buffer swaps, "
            "frame buffer %08X (the time limit ended a game stuck in a loop)\n", watchdog_seconds, vi_count, dmas,
            gfx_tasks, audio_tasks, swaps, frame_buffer);
    _exit(0);
    return NULL;
}
/* PORT_DUMPBIN=addr,len,file: memory as bytes at the end of the run, for comparing with the ROM's or with the
   reference emulator's dump. It also runs when the game stops on a call to nothing (port_trace), so a run that
   dies has its memory to read. */
static void dump_memory_if_asked(void) {
    const char *spec_env = getenv("PORT_DUMPBIN");
    if (!spec_env) return;
    char spec[512]; snprintf(spec, sizeof spec, "%s", spec_env);
    char *a = strtok(spec, ","), *n = strtok(NULL, ","), *path = strtok(NULL, ",");
    FILE *f = a && n && path ? fopen(path, "wb") : NULL;
    if (f) { fwrite(M + strtoul(a, NULL, 16), 1, strtoul(n, NULL, 0), f); fclose(f); }
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
    vtime = getenv("PORT_VTIME") && atoi(getenv("PORT_VTIME")) != 0;
    vnext_us = (unsigned)retrace_us();
    if (vtime && getenv("PORT_VTIME_TRACE")) vtrace_load(getenv("PORT_VTIME_TRACE"));
    wasm_rt_init();
#ifdef PORT_WASM_NO_IMPORTS /* a module with no host imports takes no import state (build.py sets this) */
    wasm2c_game_instantiate(&game);
#else
    wasm2c_game_instantiate(&game, &env);
#endif
    M = game.w2c_memory.data;
    if (game.w2c_0x5F_data_end > port_wasm_floor) port_wasm_floor = (game.w2c_0x5F_data_end + 0xFFFu) & ~0xFFFu;
    LOG("memory: %llu MB, ROM %zu bytes", (unsigned long long)game.w2c_memory.size >> 20, rom_size);
    /* as the console's boot does: the game's code and data from the cartridge at their addresses, and the values
       the boot leaves in low memory for libultra */
    if (!boot_memory()) return 3;
    if (getenv("PORT_CALL")) return port_call((uint32_t)strtoul(getenv("PORT_CALL"), NULL, 16));
    wasm_rt_trap_t trap = wasm_rt_impl_try();
    if (trap) { fprintf(stderr, "boot stopped: %s\n", wasm_rt_strerror(trap)); return 3; }
    if (seconds > 0) {
        pthread_t guard;
        watchdog_seconds = seconds;
        if (pthread_create(&guard, NULL, watchdog, NULL) == 0) pthread_detach(guard);
    }
    boot_active = 1;
    if (!setjmp(boot_jmp)) boot_call();
    boot_active = 0;
    LOG("the game's first function returned; its threads run");
    pthread_mutex_lock(&lock);
    booted = 1;
    schedule();
    pthread_mutex_unlock(&lock);
    /* the VI retrace: 50 a second for PAL, 60 for NTSC */
    for (int frame = 0; seconds <= 0 || (vtime ? (int)(vi_count) : frame) < seconds * 1000000 / retrace_us(); frame++) {
        /* PORT_VTIME_TRACE: the console was at retrace vtrace[n] when it started graphics task n, and no later until the game had: a game
           that works for a few milliseconds between two tasks sees no retrace in them. The port's threads, once idle, would fire
           retraces at once (virtual time) and the game's logic, counted in retraces, ran a frame ahead of the console's at the same task
           (the track's line points changed at tasks 604, 609, 614 in the port and 603, 608, 613 on the console). Once the retrace the
           next task started at has come, none follows until the game has started it (200 ms of wall time at most; PORT_VTIME_HOLD=0: not at all). */
        static int hold = -1;
        if (hold < 0) hold = !getenv("PORT_VTIME_HOLD") || atoi(getenv("PORT_VTIME_HOLD")) != 0;
        if (hold && vtime && vtrace) {
            volatile unsigned long *started = &gfx_tasks;
            unsigned long was = *started;
            uint64_t from = monotonic_us();
            while (was + 1 < vtrace_n && vi_count >= vtrace[was + 1] && vtrace[was + 1] && *started == was && monotonic_us() - from < 200000)
                usleep(100);
        }
        wait_retrace();
        retrace();
        /* the retraces the console had while it worked on a frame (PORT_VTIME_TRACE), fired as the game goes idle:
           they queue for it as they did there */
        while (vtime && vi_count < vi_target) retrace();
        if (vtime && vi_target) vnext_us = ((uint64_t)vi_count + 1) * (unsigned)retrace_us();
        port_live_pump();
        if (getenv("PORT_SAMPLE") && frame % 10 == 5) sample();
    }
    dump_memory_if_asked();
    if (getenv("PORT_DUMP")) { /* addr,len: memory as text at the end */
        uint32_t a = (uint32_t)strtoul(getenv("PORT_DUMP"), NULL, 16);
        int n = strchr(getenv("PORT_DUMP"), ',') ? atoi(strchr(getenv("PORT_DUMP"), ',') + 1) : 256;
        for (int i = 0; i < n; i++) { uint8_t c = M[a + i]; fputc(c >= 32 && c < 127 ? c : (c == 10 ? 10 : '.'), stderr); }
        fputc('\n', stderr);
    }
    fprintf(stderr, "after %.1f s: %lu retraces, %lu DMAs, %lu graphics and %lu audio tasks, %lu buffer swaps, "
            "frame buffer %08X\n", seconds, vi_count, dmas, gfx_tasks, audio_tasks, swaps, frame_buffer);
    if (vtime && vtime_forced) fprintf(stderr, "virtual time: %lu retraces fired with a thread still running (a game that spins instead of waiting)\n", vtime_forced);
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
    dump_memory_if_asked();
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

uint32_t port_tlb_canon(uint32_t a);
/* Where libultra's exception handler keeps __osFaultedThread: the game installed a preamble at the general vector
   (`lui k0; addiu k0,k0,lo; jr k0`, or a `j`) that reaches __osException, whose `panic` block stores the thread that
   faulted (`sw k0, __osFaultedThread`) and then marks it stopped and faulted (`sh x, 18(k0)`: OSThread's flags).
   Found in the game's own code, so it holds for any libultra build; 0 when the handler is not libultra's. */
static uint32_t fault_variable(void) {
    static uint32_t found, tried;
    if (tried) return found;
    tried = 1;
    uint32_t w0 = be32(0x80000180u), w1 = be32(0x80000184u), target = 0;
    if ((w0 >> 16) == 0x3C1Au && ((w1 >> 16) == 0x275Au || (w1 >> 16) == 0x375Au) && be32(0x80000188u) == 0x03400008u)
        target = (w1 >> 26) == 9 ? (w0 << 16) + (uint32_t)(int32_t)(int16_t)w1 : (w0 << 16) | (w1 & 0xFFFFu);
    else if ((w0 >> 26) == 2)
        target = 0x80000000u | ((w0 & 0x3FFFFFFu) << 2);
    if (!target) return 0;
    uint32_t base = target < 0x80000000u ? port_tlb_canon(target) : target; /* (a handler linked in a TLB window) */
    if (base < 0x80000000u || base > 0x807FF000u) return 0;
    for (uint32_t i = 1; i < 1024; i++) {
        uint32_t op = be32(base + 4 * i), prev = be32(base + 4 * i - 4);
        /* sw k0, lo(rs) right after lui rs, hi */
        if ((op >> 26) != 0x2B || ((op >> 16) & 31) != 26 || (prev >> 26) != 0x0F) continue;
        uint32_t rs = (op >> 21) & 31;
        if (rs == 26 || ((prev >> 16) & 31) != rs) continue;
        for (uint32_t j = 1; j <= 12; j++) {
            uint32_t f = be32(base + 4 * (i + j));
            if ((f >> 26) == 0x29 && ((f >> 21) & 31) == 26 && (f & 0xFFFFu) == 18) { /* sh x, 18(k0) */
                found = (prev << 16) + (uint32_t)(int32_t)(int16_t)op;
                LOG("the game's __osFaultedThread is at %08X", found);
                return found;
            }
        }
    }
    return 0;
}
/* A TLB fault the game's own pager answers. A game that pages its code in as it runs (a shooter links its program at
   a virtual base and brings 4 KB pages in) takes TLB Invalid exceptions that libultra's kernel turns into a message on
   the queue the game registered for OS_EVENT_FAULT: the faulting thread stops, __osFaultedThread names it, and a
   pager thread reads its cause and BadVAddr from the OSThread, maps the page and puts the thread back. The host runs
   the game's threads itself and keeps no kernel state for libultra's handler to work on, so the fault is delivered
   here instead: fill the faulting OSThread as __osException does, point __osFaultedThread at it, post the fault
   event, and park the host thread until the pager clears the fault flag. Returns 1 when the thread has been
   restarted (the caller retries the access), 0 when nothing here can deliver it (no pager registered, not a game
   thread, a handler that is not libultra's). */
int port_tlb_fault(uint32_t epc, uint32_t badvaddr, uint32_t cause) {
    if (!self || !self->os || !events[12].queue) return 0;
    uint32_t var = fault_variable();
    if (var < 0x80000000u || var > 0x807FFFFFu) return 0;
    uint32_t os = self->os;
    pthread_mutex_lock(&lock);
    wbe16(os + 16, 1); /* OS_STATE_STOPPED */
    wbe16(os + 18, 2); /* OS_FLAG_FAULT */
    wbe32(os + 284, epc); /* THREAD_PC */
    wbe32(os + 288, cause); /* THREAD_CAUSE */
    wbe32(os + 292, badvaddr); /* THREAD_BADVADDR */
    wbe32(var, os);
    self->state = STOPPED;
    self->faulted = 1;
    static int said;
    if (said++ < 3 || verbose > 1) LOG("thread %d takes a TLB fault at %08X (EPC %08X): the game's pager is told", self->number, badvaddr, epc);
    pthread_mutex_unlock(&lock);
    post_event(12); /* a pager that outranks this thread runs now, and the thread waits for it */
    pthread_mutex_lock(&lock);
    if (self->state != READY) { schedule(); wait_turn(self); }
    pthread_mutex_unlock(&lock);
    return 1;
}
