/* Demo Quest Xbox start-up and a few functions in the shapes Xbox games have: __stdcall and __fastcall functions, a
 * dense switch (a jump table after the function's code), string constants, a float constant and a kernel call through
 * the kernel thunk table. No XDK headers. Written for decompdecomp (CC0). */
#include "game.h"

/* What the C runtime would bring: the floating-point marker and the float-to-int helper, _ftol2 in Visual C++ 7.1 (hand-written assembly) */
int _fltused = 0x9875;

__declspec(naked) void _ftol2(void) {
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

/* The kernel thunk table: the Xbox's loader writes each kernel function's address over its 0x80000000 | ordinal, so a
 * kernel call is `call [slot]`, as a Windows program calls an import (ordinal 187: NtClose). build.py points the XBE's
 * header at it. */
unsigned long xboxkrnl_thunks[2] = {0x80000000 | 187, 0};

__declspec(noinline) s32 close_handle(void *handle) {  /* (else /O2 puts it in start) */
    return ((s32 (__stdcall *)(void *))xboxkrnl_thunks[0])(handle);
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
    close_handle((void *)0);
    return mix_score(total, level_name_length(total)) + g_score;
}
