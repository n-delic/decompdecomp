/* decompdecomp's headless video plugin for the reversing emulator (consoles/n64/emulator): draws nothing, but
   finishes each display list as the RDP would, raising the DP interrupt, so a game that waits for its frame goes on
   (the core's own dummy never raises it: a game stops after its first graphics task).

   It counts the game's frames, the graphics tasks it hands the RSP (DecompFrames, which the input plugin reads to
   time its script by frames: EMU_INPUT_CLOCK=frames), and writes RDRAM as a task is handed over
   (EMU_FRAME_DUMP=n:path,...: at the game's nth frame, its display list finished, big-endian as the console holds
   it), so a frame of the console's compares with the WebAssembly port's same frame (its PORT_FRAME_DUMPS) whatever
   either took to get there. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "m64p_plugin.h"
#include "m64p_types.h"

static GFX_INFO gfx;
static int ready;
static unsigned int frames;
EXPORT unsigned int CALL DecompFrames(void) { return frames; }

static void frame_dumps(void) {
    for (const char *p = getenv("EMU_FRAME_DUMP"); p && *p;) {
        char *end;
        unsigned long at = strtoul(p, &end, 10);
        if (*end != ':') return;
        const char *path = end + 1;
        size_t n = strcspn(path, ",");
        if (at == frames && n < 4096) {
            char name[4096];
            memcpy(name, path, n); name[n] = 0;
            FILE *f = fopen(name, "wb");
            size_t words = (getenv("EMU_8MB") ? 8u << 20 : 4u << 20) / 4;
            if (f) {
                const uint32_t *rdram = (const uint32_t *)gfx.RDRAM; /* host-order words */
                for (size_t i = 0; i < words; i++) {
                    uint32_t w = rdram[i];
                    unsigned char b[4] = {w >> 24, w >> 16, w >> 8, w};
                    fwrite(b, 1, 4, f);
                }
                fclose(f);
                fprintf(stderr, "framedump %u %s\n", frames, name);
            } else fprintf(stderr, "error can't write RDRAM to %s\n", name);
        }
        p = path + n + (path[n] == ',');
    }
}

EXPORT m64p_error CALL PluginStartup(m64p_dynlib_handle core, void *context, void (*debug)(void *, int, const char *)) {
    (void)core; (void)context; (void)debug;
    return M64ERR_SUCCESS;
}
EXPORT m64p_error CALL PluginShutdown(void) { return M64ERR_SUCCESS; }
EXPORT m64p_error CALL PluginGetVersion(m64p_plugin_type *type, int *version, int *api, const char **name, int *caps) {
    if (type) *type = M64PLUGIN_GFX;
    if (version) *version = 0x010000;
    if (api) *api = 0x020200;
    if (name) *name = "decompdecomp headless video";
    if (caps) *caps = 0;
    return M64ERR_SUCCESS;
}
EXPORT int CALL InitiateGFX(GFX_INFO info) { gfx = info; ready = 1; return 1; }
EXPORT void CALL ProcessDList(void) {
    if (!ready) return;
    frames++;
    frame_dumps();
    *gfx.MI_INTR_REG |= 0x20; /* MI_INTR_DP: the frame is drawn */
    gfx.CheckInterrupts();
}
EXPORT void CALL ProcessRDPList(void) {
    if (!ready) return;
    *gfx.DPC_CURRENT_REG = *gfx.DPC_END_REG; /* the command list consumed */
    *gfx.MI_INTR_REG |= 0x20;
    gfx.CheckInterrupts();
}
EXPORT void CALL ChangeWindow(void) {}
EXPORT void CALL MoveScreen(int x, int y) { (void)x; (void)y; }
EXPORT void CALL RomClosed(void) {}
EXPORT int CALL RomOpen(void) { return 1; }
EXPORT void CALL ShowCFB(void) {}
EXPORT void CALL UpdateScreen(void) {}
EXPORT void CALL ViStatusChanged(void) {}
EXPORT void CALL ViWidthChanged(void) {}
EXPORT void CALL ReadScreen2(void *dest, int *width, int *height, int front) { (void)dest; (void)front; if (width) *width = 0; if (height) *height = 0; }
EXPORT void CALL SetRenderingCallback(void (*callback)(int)) { (void)callback; }
EXPORT void CALL FBRead(unsigned int addr) { (void)addr; }
EXPORT void CALL FBWrite(unsigned int addr, unsigned int size) { (void)addr; (void)size; }
EXPORT void CALL FBGetFrameBufferInfo(void *info) { (void)info; }
EXPORT void CALL ResizeVideoOutput(int width, int height) { (void)width; (void)height; }
