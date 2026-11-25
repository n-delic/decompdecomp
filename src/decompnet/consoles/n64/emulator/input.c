/* decompdecomp's scripted input plugin for the reversing emulator (consoles/n64/emulator): the first controller's
   buttons and stick from EMU_INPUT, "vi:BUTTON+BUTTON+SX=n+SY=n,vi:..." (the port's PORT_INPUT format, timed in
   vertical interrupts since boot as the core counts them, DecompGetViCount; with EMU_INPUT_CLOCK=frames, in the
   game's frames, the graphics tasks the video plugin has seen, DecompFrames). The last entry at or before the
   current time wins. */
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "m64p_plugin.h"
#include "m64p_types.h"
#include "live.h"

static unsigned int (*vi_count)(void);
static CONTROL *controls;

EXPORT m64p_error CALL PluginStartup(m64p_dynlib_handle core, void *context, void (*debug)(void *, int, const char *)) {
    (void)context; (void)debug;
    vi_count = (unsigned int (*)(void))dlsym(core, "DecompGetViCount");
    return vi_count ? M64ERR_SUCCESS : M64ERR_INCOMPATIBLE;
}
EXPORT m64p_error CALL PluginShutdown(void) { return M64ERR_SUCCESS; }
EXPORT m64p_error CALL PluginGetVersion(m64p_plugin_type *type, int *version, int *api, const char **name, int *caps) {
    if (type) *type = M64PLUGIN_INPUT;
    if (version) *version = 0x010000;
    if (api) *api = 0x020100;
    if (name) *name = "decompdecomp scripted input";
    if (caps) *caps = 0;
    return M64ERR_SUCCESS;
}
EXPORT void CALL InitiateControllers(CONTROL_INFO info) {
    controls = info.Controls;
    for (int i = 0; i < 4; i++) { controls[i].Present = i == 0; controls[i].RawData = 0; controls[i].Plugin = PLUGIN_NONE; }
}
EXPORT void CALL GetKeys(int control, BUTTONS *keys) {
    keys->Value = 0;
    static volatile uint32_t *live; static int live_tried; /* EMU_LIVE: the viewer's pad (live.h), not a script */
    if (!live_tried) { live_tried = 1; live = live_map(); }
    if (live) { if (control == 0) keys->Value = live[LIVE_INPUT / 4]; return; }
    const char *p = getenv("EMU_INPUT");
    if (control != 0 || !p) return;
    static const struct { const char *name; int bit; } names[] = {
        {"RIGHT", 0}, {"LEFT", 1}, {"DOWN", 2}, {"UP", 3}, {"START", 4}, {"Z", 5}, {"B", 6}, {"A", 7},
        {"CR", 8}, {"CL", 9}, {"CD", 10}, {"CU", 11}, {"R", 12}, {"L", 13}};
    static unsigned int (*frames)(void);
    static int clock_read;
    if (!clock_read) { /* EMU_INPUT_CLOCK=frames: the game's frames (the video plugin's count), not retraces */
        clock_read = 1;
        const char *clock = getenv("EMU_INPUT_CLOCK"), *video = getenv("EMU_VIDEO_PLUGIN");
        void *h = clock && !strcmp(clock, "frames") && video ? dlopen(video, RTLD_NOW | RTLD_NOLOAD) : NULL;
        if (h) frames = (unsigned int (*)(void))dlsym(h, "DecompFrames");
    }
    unsigned int now = frames ? frames() : vi_count();
    static unsigned int last_bits = ~0u;
    unsigned int shown = 0;
    while (*p) {
        char *end;
        unsigned long at = strtoul(p, &end, 10);
        if (*end != ':') break;
        p = end + 1;
        unsigned int bits = 0; int sx = 0, sy = 0;
        while (*p && *p != ',') {
            size_t n = strcspn(p, "+,");
            if (!strncmp(p, "SX=", 3)) sx = atoi(p + 3);
            else if (!strncmp(p, "SY=", 3)) sy = atoi(p + 3);
            else for (size_t i = 0; i < sizeof names / sizeof *names; i++)
                if (strlen(names[i].name) == n && !strncmp(p, names[i].name, n)) bits |= 1u << names[i].bit;
            p += n;
            if (*p == '+') p++;
        }
        if (at <= now) keys->Value = bits | ((unsigned)(sx & 0xFF) << 16) | ((unsigned)(sy & 0xFF) << 24);
        if (*p == ',') p++;
    }
    /* EMU_PAD_LOG=file: "PAD <time> <buttons>" at the first poll that shows each change of the pad (docs/PORTING.md: the task the
       game first sees an edge at, which the port's PORT_PAD_LOG gives for the same script) */
    shown = keys->Value & 0xFFFF;
    if (shown != last_bits) {
        last_bits = shown;
        const char *path = getenv("EMU_PAD_LOG");
        FILE *log = path ? fopen(path, "a") : NULL;
        if (log) { fprintf(log, "PAD %u %04X\n", now, shown); fclose(log); }
    }
}
EXPORT void CALL ControllerCommand(int control, unsigned char *command) { (void)control; (void)command; }
EXPORT void CALL ReadController(int control, unsigned char *command) { (void)control; (void)command; }
EXPORT int CALL RomOpen(void) { return 1; }
EXPORT void CALL RomClosed(void) {}
EXPORT void CALL SDL_KeyDown(int keymod, int keysym) { (void)keymod; (void)keysym; }
EXPORT void CALL SDL_KeyUp(int keymod, int keysym) { (void)keymod; (void)keysym; }
