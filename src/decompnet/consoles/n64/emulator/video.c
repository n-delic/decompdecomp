/* decompdecomp's headless video plugin for the reversing emulator (consoles/n64/emulator): draws nothing, but
   finishes each display list as the RDP would, raising the DP interrupt, so a game that waits for its frame goes on
   (the core's own dummy never raises it: a game stops after its first graphics task). */
#include "m64p_plugin.h"
#include "m64p_types.h"

static GFX_INFO gfx;
static int ready;

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
