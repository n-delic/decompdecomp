/*
 * libultra's threads and message queues for an N64 port on libultraship (decompdecomp, docs/PORTING.md phase 3).
 *
 * libultraship's message queues never wait (osRecvMesg on an empty queue returns -1), because the ports built on it
 * rewrite the game's threads into one main loop. This file lets a game keep its threads as written instead: each
 * game thread is a host thread, and, as on the N64's one CPU, only one of them runs at a time, the highest priority
 * one that can. A thread gives the CPU up when it waits on a message queue, yields, finishes, or wakes a thread of
 * higher priority; events from outside (libultraship's VI timer, the renderer finishing a task) are posted from host
 * threads and wake the game thread waiting for them.
 *
 * It defines every symbol of libultraship's os_mesg.cpp (osCreateMesgQueue, osSendMesg, osJamMesg, osRecvMesg,
 * osSetEventMesg, __osEventStateTab), so the linker takes these and never pulls that file in; libultraship's own
 * callers (its VI timer) then send through these, from their own threads, which is allowed: a call from a thread
 * that isn't a game thread never waits.
 *
 * Not done: preempting a game thread that runs without ever waiting (the N64 would switch to a higher-priority
 * thread an interrupt woke). A game that spins on a flag another thread sets would stop here; port_thread_stalls()
 * counts how often a woken higher-priority thread had to wait for a running one, for a port to report.
 *
 * Plain C11 and POSIX threads. The structures follow libultra's layout (OSMesgQueue's fields, OSMesg a pointer-sized
 * value); OSThread is only an address here, so the game's own OSThread (any size) works. A thread's state is kept
 * apart from it, by that address.
 */

#include <pthread.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

typedef union {
    uint8_t data8;
    uint16_t data16;
    uint32_t data32;
    void *ptr;
} OSMesg;

typedef struct OSThread OSThread; /* the game's own; only its address is used */

typedef struct OSMesgQueue {
    OSThread *mtqueue;
    OSThread *fullqueue;
    int32_t validCount;
    int32_t first;
    int32_t msgCount;
    OSMesg *msg;
} OSMesgQueue;

typedef struct {
    OSMesgQueue *queue;
    OSMesg msg;
} __OSEventState;

typedef uint32_t OSEvent;
typedef int32_t OSPri;
typedef int32_t OSId;

#define OS_MESG_NOBLOCK 0
#define OS_MESG_BLOCK 1
#define OS_NUM_EVENTS 15

/* initialised, so it's a definition and not a tentative one: a linker would otherwise take libultraship's, and the
   rest of its os_mesg with it (duplicate symbols) */
__OSEventState __osEventStateTab[OS_NUM_EVENTS] = { { 0 } };

enum { CREATED, READY, WAITING, STOPPED, DONE };

typedef struct PortThread {
    OSThread *os;
    OSId id;
    OSPri pri;
    int state;
    unsigned long long ready_at; /* among equal priorities, the one ready longest runs first */
    void (*entry)(void *);
    void *arg;
    pthread_t host;
    int started;
    pthread_cond_t turn;
    OSMesgQueue *waiting_on; /* while WAITING */
    struct PortThread *next;
} PortThread;

static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
static PortThread *threads;              /* every game thread */
static PortThread *running;              /* the one allowed to run, or NULL */
static _Thread_local PortThread *self;   /* the game thread this host thread is, or NULL */
static unsigned long long ticks;
static unsigned long stalls;

static PortThread *find(OSThread *t) {
    for (PortThread *p = threads; p; p = p->next)
        if (p->os == t) return p;
    return NULL;
}

static void make_ready(PortThread *p) {
    p->state = READY;
    p->waiting_on = NULL;
    p->ready_at = ++ticks;
}

/* Hand the CPU to the highest-priority ready thread (lock held). */
static void schedule(void) {
    PortThread *best = NULL;
    for (PortThread *p = threads; p; p = p->next)
        if (p->state == READY && (!best || p->pri > best->pri || (p->pri == best->pri && p->ready_at < best->ready_at)))
            best = p;
    running = best;
    if (best) pthread_cond_signal(&best->turn);
}

/* Wait (lock held) until this game thread may run again. */
static void wait_turn(PortThread *me) {
    while (running != me || me->state != READY) pthread_cond_wait(&me->turn, &lock);
}

/* A thread `woken` became ready: give it the CPU now if it outranks the caller (lock held). */
static void after_wake(PortThread *woken) {
    if (self && woken && woken->pri > self->pri) {
        self->ready_at = ++ticks;
        schedule();
        wait_turn(self);
    } else if (!self && woken) {
        if (!running) schedule();
        else if (woken->pri > running->pri) stalls++; /* it waits until the running thread waits or yields */
    }
}

static PortThread *wake_one(OSMesgQueue *mq) {
    PortThread *best = NULL;
    for (PortThread *p = threads; p; p = p->next)
        if (p->state == WAITING && p->waiting_on == mq && (!best || p->pri > best->pri))
            best = p;
    if (best) make_ready(best);
    return best;
}

static void *host_entry(void *arg) {
    PortThread *me = arg;
    self = me;
    pthread_mutex_lock(&lock);
    wait_turn(me);
    pthread_mutex_unlock(&lock);
    me->entry(me->arg);
    pthread_mutex_lock(&lock);
    me->state = DONE;
    schedule();
    pthread_mutex_unlock(&lock);
    return NULL;
}

void osCreateThread(OSThread *t, OSId id, void (*entry)(void *), void *arg, void *sp, OSPri pri) {
    (void)sp; /* a host thread has its own stack */
    pthread_mutex_lock(&lock);
    PortThread *p = find(t);
    if (!p) {
        p = calloc(1, sizeof *p);
        pthread_cond_init(&p->turn, NULL);
        p->os = t;
        p->next = threads;
        threads = p;
    }
    p->id = id;
    p->pri = pri;
    p->entry = entry;
    p->arg = arg;
    p->state = CREATED;
    pthread_mutex_unlock(&lock);
}

void osStartThread(OSThread *t) {
    pthread_mutex_lock(&lock);
    PortThread *p = find(t);
    if (p && (p->state == CREATED || p->state == STOPPED)) {
        make_ready(p);
        if (!p->started) {
            p->started = 1;
            pthread_attr_t attr;
            pthread_attr_init(&attr);
            pthread_attr_setstacksize(&attr, 1 << 20);
            pthread_attr_setdetachstate(&attr, PTHREAD_CREATE_DETACHED); /* never joined: a game thread ends with the game */
            pthread_create(&p->host, &attr, host_entry, p);
            pthread_attr_destroy(&attr);
        }
        after_wake(p);
    }
    pthread_mutex_unlock(&lock);
}

void osStopThread(OSThread *t) {
    pthread_mutex_lock(&lock);
    PortThread *p = t ? find(t) : self;
    if (p) {
        p->state = STOPPED;
        if (p == self) {
            schedule();
            wait_turn(p); /* until someone starts it again */
        } else if (p == running) {
            schedule();
        }
    }
    pthread_mutex_unlock(&lock);
}

void osDestroyThread(OSThread *t) {
    pthread_mutex_lock(&lock);
    PortThread *p = t ? find(t) : self;
    if (p) {
        p->state = DONE;
        if (p == self) {
            schedule();
            pthread_mutex_unlock(&lock);
            pthread_exit(NULL);
        }
        if (p == running) schedule();
    }
    pthread_mutex_unlock(&lock);
}

void osSetThreadPri(OSThread *t, OSPri pri) {
    pthread_mutex_lock(&lock);
    PortThread *p = t ? find(t) : self;
    if (p) {
        p->pri = pri;
        if (self && self->state == READY) {
            self->ready_at = ++ticks;
            schedule();
            wait_turn(self);
        }
    }
    pthread_mutex_unlock(&lock);
}

OSPri osGetThreadPri(OSThread *t) {
    pthread_mutex_lock(&lock);
    PortThread *p = t ? find(t) : self;
    OSPri pri = p ? p->pri : 0;
    pthread_mutex_unlock(&lock);
    return pri;
}

OSId osGetThreadId(OSThread *t) {
    pthread_mutex_lock(&lock);
    PortThread *p = t ? find(t) : self;
    OSId id = p ? p->id : 0;
    pthread_mutex_unlock(&lock);
    return id;
}

void osYieldThread(void) {
    pthread_mutex_lock(&lock);
    if (self) {
        self->ready_at = ++ticks;
        schedule();
        wait_turn(self);
    }
    pthread_mutex_unlock(&lock);
}

void osCreateMesgQueue(OSMesgQueue *mq, OSMesg *buf, int32_t count) {
    pthread_mutex_lock(&lock);
    mq->mtqueue = NULL;
    mq->fullqueue = NULL;
    mq->validCount = 0;
    mq->first = 0;
    mq->msgCount = count;
    mq->msg = buf;
    pthread_mutex_unlock(&lock);
}

/* Wait (lock held) on `mq` until `ready()` holds; 0, or -1 when the caller may not wait. */
static int block_on(OSMesgQueue *mq, int32_t flag, int (*ready)(OSMesgQueue *)) {
    while (!ready(mq)) {
        if (flag != OS_MESG_BLOCK || !self) return -1; /* a host thread never waits for a game thread */
        self->state = WAITING;
        self->waiting_on = mq;
        schedule();
        wait_turn(self);
    }
    return 0;
}

static int has_room(OSMesgQueue *mq) { return mq->validCount < mq->msgCount; }
static int has_mesg(OSMesgQueue *mq) { return mq->validCount > 0; }

static int32_t put(OSMesgQueue *mq, OSMesg msg, int32_t flag, int jam) {
    pthread_mutex_lock(&lock);
    if (block_on(mq, flag, has_room) < 0) {
        pthread_mutex_unlock(&lock);
        return -1;
    }
    if (jam) {
        mq->first = (mq->first + mq->msgCount - 1) % mq->msgCount;
        mq->msg[mq->first] = msg;
    } else {
        mq->msg[(mq->first + mq->validCount) % mq->msgCount] = msg;
    }
    mq->validCount++;
    after_wake(wake_one(mq));
    pthread_mutex_unlock(&lock);
    return 0;
}

int32_t osSendMesg(OSMesgQueue *mq, OSMesg msg, int32_t flag) { return put(mq, msg, flag, 0); }
int32_t osJamMesg(OSMesgQueue *mq, OSMesg msg, int32_t flag) { return put(mq, msg, flag, 1); }

int32_t osRecvMesg(OSMesgQueue *mq, OSMesg *msg, int32_t flag) {
    pthread_mutex_lock(&lock);
    if (block_on(mq, flag, has_mesg) < 0) {
        pthread_mutex_unlock(&lock);
        return -1;
    }
    if (msg) *msg = mq->msg[mq->first];
    mq->first = (mq->first + 1) % mq->msgCount;
    mq->validCount--;
    after_wake(wake_one(mq));
    pthread_mutex_unlock(&lock);
    return 0;
}

void osSetEventMesg(OSEvent event, OSMesgQueue *mq, OSMesg msg) {
    if (event >= OS_NUM_EVENTS) return;
    pthread_mutex_lock(&lock);
    __osEventStateTab[event].queue = mq;
    __osEventStateTab[event].msg = msg;
    pthread_mutex_unlock(&lock);
}

/* For the port's host side (its renderer finishing a task, a timer): post a libultra event as the hardware would. */
void port_post_event(OSEvent event) {
    if (event >= OS_NUM_EVENTS) return;
    pthread_mutex_lock(&lock);
    OSMesgQueue *mq = __osEventStateTab[event].queue;
    OSMesg msg = __osEventStateTab[event].msg;
    pthread_mutex_unlock(&lock);
    if (mq) osSendMesg(mq, msg, OS_MESG_NOBLOCK);
}

/* How many times a higher-priority thread, woken from outside, had to wait for a running one to wait or yield. */
unsigned long port_thread_stalls(void) {
    pthread_mutex_lock(&lock);
    unsigned long n = stalls;
    pthread_mutex_unlock(&lock);
    return n;
}
