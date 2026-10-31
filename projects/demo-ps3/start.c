/* Demo Quest PS3 start-up: the entry point, a main loop, and the two things a PowerPC64 program does that a flat
 * one doesn't: it reaches a global through the TOC, and it calls through a function pointer (a descriptor).
 * Written for decompdecomp (CC0). */
#include "game.h"

typedef s32 (*score_fn)(s32);

s32 g_toc_probe = 7;  /* reached through the TOC */
static const char g_banner[] = "DEMOQUEST-PS3";
volatile s32 g_checksum;

static s32 bonus_for(s32 v) {
    return v * 3 + g_toc_probe;
}

s32 apply_score(score_fn fn, s32 value) {
    return fn(value) + g_toc_probe;
}

s32 banner_length(void) {
    s32 n = 0;
    while (g_banner[n] != 0) {
        n++;
    }
    return n;
}

void main_loop(void) {
    entity_spawn(ENTITY_SLIME, 32, 0);
    entity_spawn(ENTITY_BAT, 96, 64);
    for (;;) {
        game_tick();
        g_checksum = apply_score(bonus_for, g_score) + apply_score(abs_s32, -g_frame_count) + banner_length();
    }
}

void _start(void) {
    main_loop();
}
