/* Drives consoles/n64/port_glue/threads.c as a game would (tests/test_port_glue.py builds and runs it). */
#include <pthread.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

typedef union { unsigned char data8; unsigned short data16; unsigned int data32; void *ptr; } OSMesg;
typedef struct OSThread { char opaque[0x1B0]; } OSThread; /* the game's own size: the glue only uses its address */
typedef struct OSMesgQueue { OSThread *mtqueue, *fullqueue; int validCount, first, msgCount; OSMesg *msg; } OSMesgQueue;
void osCreateThread(OSThread *, int, void (*)(void *), void *, void *, int);
void osStartThread(OSThread *);
void osSetThreadPri(OSThread *, int);
int osGetThreadPri(OSThread *);
void osYieldThread(void);
void osCreateMesgQueue(OSMesgQueue *, OSMesg *, int);
int osSendMesg(OSMesgQueue *, OSMesg, int);
int osJamMesg(OSMesgQueue *, OSMesg, int);
int osRecvMesg(OSMesgQueue *, OSMesg *, int);
void osSetEventMesg(unsigned, OSMesgQueue *, OSMesg);
void port_post_event(unsigned);
unsigned long port_thread_stalls(void);
#define BLOCK 1
#define NOBLOCK 0
#define M(x) ((OSMesg){ .data32 = (x) })

static char log_[512];
static void say(const char *s) { strcat(log_, s); }
static int failures;
static void expect(const char *what, const char *want) {
    if (strcmp(log_, want)) { printf("FAIL %s: got '%s', want '%s'\n", what, log_, want); failures++; }
    log_[0] = 0;
}

static OSThread boot, hi, prod, cons, eq1, eq2, vi;
static OSMesgQueue q, one, vq;
static OSMesg qbuf[4], onebuf[1], vbuf[8];

static void hi_main(void *a) { OSMesg m; osRecvMesg(&q, &m, BLOCK); say("H"); }
static void prod_main(void *a) { for (unsigned i = 1; i <= 3; i++) { osSendMesg(&one, M(i), BLOCK); say("p"); } }
static void cons_main(void *a) { OSMesg m; char s[4]; for (int i = 0; i < 3; i++) { osRecvMesg(&one, &m, BLOCK); snprintf(s, 4, "c%u", m.data32); say(s); } }
static void eq_main(void *a) { for (int i = 0; i < 2; i++) { say((char *)a); osYieldThread(); } }
static void vi_main(void *a) { OSMesg m; for (int i = 0; i < 3; i++) { osRecvMesg(&vq, &m, BLOCK); say("v"); } }

static pthread_mutex_t done_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t done_cond = PTHREAD_COND_INITIALIZER;
static int done;

static void *timer(void *a) { for (int i = 0; i < 3; i++) { usleep(20000); port_post_event(7); } return NULL; }

static void boot_main(void *a) {
    /* a higher-priority thread runs as soon as the message it waits for is sent */
    osCreateMesgQueue(&q, qbuf, 4);
    osCreateThread(&hi, 2, hi_main, NULL, NULL, 20);
    osStartThread(&hi);  /* runs, blocks on q */
    say("b");
    osSendMesg(&q, M(1), BLOCK);  /* wakes hi, which outranks boot */
    say("b");
    expect("preemption on send", "bHb");

    /* a one-slot queue: the producer waits for room, the consumer for messages */
    osCreateMesgQueue(&one, onebuf, 1);
    osCreateThread(&prod, 3, prod_main, NULL, NULL, 5);
    osCreateThread(&cons, 4, cons_main, NULL, NULL, 6);
    osStartThread(&prod);
    osStartThread(&cons);
    osSetThreadPri(NULL, 1);  /* let them run */
    osSetThreadPri(NULL, 10);
    /* the consumer outranks the producer, so each send switches to it at once, as libultra's does */
    expect("blocking send and receive", "c1pc2pc3p");

    /* jammed messages come first */
    OSMesg m;
    osSendMesg(&q, M(2), BLOCK);
    osJamMesg(&q, M(3), BLOCK);
    osRecvMesg(&q, &m, BLOCK); if (m.data32 == 3) say("j");
    osRecvMesg(&q, &m, BLOCK); if (m.data32 == 2) say("s");
    if (osRecvMesg(&q, &m, NOBLOCK) == -1) say("e");
    expect("jam and empty", "jse");

    /* equal priorities take turns at each yield */
    osCreateThread(&eq1, 5, eq_main, "1", NULL, 7);
    osCreateThread(&eq2, 6, eq_main, "2", NULL, 7);
    osStartThread(&eq1);
    osStartThread(&eq2);
    osSetThreadPri(NULL, 1);
    osSetThreadPri(NULL, 10);
    expect("yield among equals", "1212");

    /* an event posted from a host thread (libultraship's VI timer) wakes the game thread waiting for it */
    osCreateMesgQueue(&vq, vbuf, 8);
    osSetEventMesg(7, &vq, M(0));
    osCreateThread(&vi, 7, vi_main, NULL, NULL, 30);
    osStartThread(&vi);
    pthread_t t;
    pthread_create(&t, NULL, timer, NULL);
    pthread_join(t, NULL);  /* boot holds the CPU, so the woken vi waits for it (a stall), then runs */
    if (port_thread_stalls() >= 1) say("w");
    osSetThreadPri(NULL, 1);
    osSetThreadPri(NULL, 10);
    if (osRecvMesg(&vq, &m, NOBLOCK) == -1) say("e");
    expect("events from a host thread", "wvvve");
    pthread_mutex_lock(&done_lock); done = 1; pthread_cond_signal(&done_cond); pthread_mutex_unlock(&done_lock);
}

int main(void) {
    osCreateThread(&boot, 1, boot_main, NULL, NULL, 10);
    osStartThread(&boot);
    pthread_mutex_lock(&done_lock);
    while (!done) pthread_cond_wait(&done_cond, &done_lock);
    pthread_mutex_unlock(&done_lock);
    printf(failures ? "%d failed\n" : "ok\n", failures);
    return failures != 0;
}
