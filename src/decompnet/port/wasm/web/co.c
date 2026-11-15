/*
 * The browser build's threads (include/pthread.h): coroutines the page switches with JavaScript Promise Integration
 * (web/runtime.js). A thread waiting on a condition variable suspends its wasm stack (`suspend`); the page resumes
 * the one whose condition was signalled (`co_next`) once the running one waits. Only one runs at a time, so the
 * host's scheduler works unchanged: it decides who runs, this only carries it out.
 *
 * Each coroutine has a C stack of its own (the host's shadow stack, in the shared memory above the host's data),
 * which the page swaps into __stack_pointer whenever it switches; the game's own shadow stack the host swaps itself.
 */
#include <pthread.h>
#include <stdlib.h>

#define EXPORT(name) __attribute__((export_name(name)))
#define IMPORT(name) __attribute__((import_module("port_web"), import_name(name)))
IMPORT("suspend") void js_suspend(void);

#define MAX_CO 64
#define STACK (1 << 20)
static struct {
    void *(*fn)(void *);
    void *arg;
    int started, done;
    pthread_cond_t *waiting;
    unsigned stack_top;
} co[MAX_CO];
static int co_count, co_current = -1;
static void *selfs[MAX_CO + 1]; /* [0]: outside any thread (the page's own calls) */

void **port_web_self_slot(void) { return &selfs[co_current + 1]; }

int pthread_create(pthread_t *t, const pthread_attr_t *a, void *(*fn)(void *), void *arg) {
    (void)a;
    if (co_count == MAX_CO) return -1;
    int id = co_count++;
    co[id].fn = fn;
    co[id].arg = arg;
    char *stack = malloc(STACK);
    if (!stack) return -1;
    co[id].stack_top = ((unsigned)(unsigned long)stack + STACK) & ~15u;
    if (t) *t = id;
    return 0;
}

int pthread_cond_wait(pthread_cond_t *c, pthread_mutex_t *m) {
    (void)m;
    int me = co_current;
    if (me < 0) __builtin_trap(); /* the page's own calls never wait */
    co[me].waiting = c;
    js_suspend();
    co[me].waiting = 0;
    return 0;
}

_Noreturn void pthread_exit(void *result) {
    (void)result;
    co[co_current].done = 1;
    co[co_current].waiting = 0;
    js_suspend(); /* never resumed */
    __builtin_trap();
}

/* Give way to the page for a moment (its retrace), ready to go on: the page resumes it once the host says it's
   still the one to run. */
void co_yield(void) {
    static pthread_cond_t ready[MAX_CO];
    int me = co_current;
    if (me < 0) return;
    ready[me].signaled = 1;
    co[me].waiting = &ready[me];
    js_suspend();
    co[me].waiting = 0;
}

/* The page's side: which coroutine runs next (-1: none can), entering and leaving one, and starting one. */
EXPORT("co_next") int co_next(void) {
    for (int i = 0; i < co_count; i++)
        if (!co[i].started) return i;
    for (int i = 0; i < co_count; i++)
        if (!co[i].done && co[i].waiting && co[i].waiting->signaled) {
            co[i].waiting->signaled = 0;
            return i;
        }
    return -1;
}
EXPORT("co_started") int co_started(int id) { return co[id].started; }
EXPORT("co_stack_top") unsigned co_stack_top(int id) { return co[id].stack_top; }
EXPORT("co_enter") void co_enter(int id) { co_current = id; }
EXPORT("co_leave") void co_leave(void) { co_current = -1; }
EXPORT("co_main") void co_main(int id) {
    co[id].started = 1;
    co_current = id;
    co[id].fn(co[id].arg);
    co[id].done = 1;
}

/* The C stack pointer, for the page to give each coroutine its own. */
EXPORT("co_sp") unsigned co_sp(void) {
    unsigned sp;
    __asm__ volatile(".globaltype __stack_pointer, i32\n\tglobal.get __stack_pointer\n\tlocal.set %0" : "=r"(sp));
    return sp;
}
EXPORT("co_set_sp") void co_set_sp(unsigned sp) {
    __asm__ volatile(".globaltype __stack_pointer, i32\n\tlocal.get %0\n\tglobal.set __stack_pointer" : : "r"(sp));
}
