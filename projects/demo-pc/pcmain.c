/* Demo Quest PC start-up and a few functions in the shapes PC games have: Win32-style __stdcall and __fastcall
 * functions, a dense switch (a jump table after the function's code) and string constants. Written for decompdecomp
 * (CC0). */
#include "game.h"

/* What the C runtime would bring: the floating-point marker and, on 32-bit x86, the float-to-int helper (hand-written
 * assembly; x64 converts with an instruction of its own, cvttss2si, and its compiler has no inline assembly) */
int _fltused = 0x9875;

#ifdef _M_IX86
__declspec(naked) void _ftol(void) {
    __asm {
        push ebp
        mov ebp, esp
        sub esp, 8
        fistp qword ptr [ebp-8]
        mov eax, [ebp-8]
        mov edx, [ebp-4]
        leave
        ret
    }
}
#endif

const char *g_level_names[4] = {"meadow", "cave", "castle", "keep"};
s32 g_settings[6];

s32 __stdcall settings_get(s32 index, s32 fallback) {
    if (index < 0 || index >= 6) {
        return fallback;
    }
    return g_settings[index];
}

s32 __fastcall mix_score(s32 a, s32 b) {
    return (a * 31) ^ (b >> 3);
}

s32 entity_points(s32 type) {
    switch (type) {
    case 0: return 0;
    case 1: return 10;
    case 2: return 25;
    case 3: return 50;
    case 4: return 500;
    case 5: return 1;
    case 6: return 75;
    default: return -1;
    }
}

s32 level_name_length(s32 level) {
    const char *p = g_level_names[level & 3];
    s32 n = 0;
    while (*p++) {
        n++;
    }
    return n;
}

s32 start(void) {
    s32 i, total = 0;
    for (i = 0; i < 6; i++) {
        g_settings[i] = i * 3;
    }
    for (i = 0; i < 8; i++) {
        entity_spawn((s16)(rng_next() % 6), i * 40, 0);
    }
    for (i = 0; i < 120; i++) {
        game_tick();
        total += entity_points(i % 8) + settings_get(i, 7);
    }
    total += count_alive_entities() + (s32)lerp(0.0f, 8.0f, 0.5f) + checksum_save((const u8 *)g_settings, 24);
    return mix_score(total, level_name_length(total)) + g_score;
}
