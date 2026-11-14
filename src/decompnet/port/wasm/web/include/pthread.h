/* The browser build's pthreads (port/wasm/web/co.c): each thread is a coroutine on its own wasm stack, switched with
 * JavaScript Promise Integration. The host runs one game thread at a time, as the N64's one CPU did, and hands the
 * CPU over with a condition variable, so a mutex has nothing to do and a wait is where a thread gives way. */
#ifndef PORT_WEB_PTHREAD_H
#define PORT_WEB_PTHREAD_H
typedef int pthread_t;
typedef struct { int unused; } pthread_mutex_t;
typedef struct { int unused; } pthread_attr_t;
typedef struct { volatile int signaled; } pthread_cond_t;
#define PTHREAD_MUTEX_INITIALIZER {0}
#define PTHREAD_CREATE_DETACHED 1
static inline int pthread_mutex_lock(pthread_mutex_t *m) { (void)m; return 0; }
static inline int pthread_mutex_unlock(pthread_mutex_t *m) { (void)m; return 0; }
static inline int pthread_cond_init(pthread_cond_t *c, const void *a) { (void)a; c->signaled = 0; return 0; }
static inline int pthread_cond_signal(pthread_cond_t *c) { c->signaled = 1; return 0; }
static inline int pthread_attr_init(pthread_attr_t *a) { (void)a; return 0; }
static inline int pthread_attr_setstacksize(pthread_attr_t *a, unsigned long n) { (void)a; (void)n; return 0; }
static inline int pthread_attr_setdetachstate(pthread_attr_t *a, int d) { (void)a; (void)d; return 0; }
static inline int pthread_attr_destroy(pthread_attr_t *a) { (void)a; return 0; }
static inline int pthread_detach(pthread_t t) { (void)t; return 0; }
int pthread_cond_wait(pthread_cond_t *c, pthread_mutex_t *m);
int pthread_create(pthread_t *t, const pthread_attr_t *a, void *(*fn)(void *), void *arg);
_Noreturn void pthread_exit(void *result);
void **port_web_self_slot(void);
void co_yield(void); /* give way to the page for its retrace, still ready to run */ /* the running coroutine's own `self` (the host's _Thread_local) */
#endif
