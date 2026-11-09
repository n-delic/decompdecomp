/* Freestanding entry for Demo Quest built with the user's Watcom 10.0a C. */
#include "game.h"

s32 g_settings[6];

s32 settings_get(s32 index, s32 fallback) {
    return (index < 0 || index >= 6) ? fallback : g_settings[index];
}

s32 mix_score(s32 a, s32 b) { return (a * 31) ^ (b >> 3); }

s32 entity_points(s32 type) {
    switch (type) {
    case 1: return 10;
    case 2: return 25;
    case 3: return 50;
    case 4: return 500;
    default: return 0;
    }
}

s32 start(void) {
    g_settings[0] = 3;
    return mix_score(settings_get(0, 0) + entity_points(2), abs_s32(-3));
}

/* Watcom's floating-point object references these runtime symbols. */
int _8087 = 1;
void _init_387_emulator(void) {}
