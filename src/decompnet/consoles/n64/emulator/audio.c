/* decompdecomp's audio plugin for the live emulator (consoles/n64/emulator/live.py): plays nothing itself. Each buffer
   the game hands the audio interface (AiLenChanged) is copied, as 16-bit little-endian stereo, into the ring in the file
   the viewer shares (live.h), with the rate the game set, and the viewer sends it to the page, which plays it beside
   the port's (port/wasm/side_by_side.py). Attached only when EMU_LIVE is set; a headless run keeps the core's own
   dummy, which does nothing. */
#include <stdint.h>
#include <stdlib.h>
#include "m64p_plugin.h"
#include "m64p_types.h"
#include "live.h"

static AUDIO_INFO ai;
static volatile uint32_t *live;
static unsigned int clock_hz = 48681812; /* the video clock the DAC rate divides: NTSC */

EXPORT m64p_error CALL PluginStartup(m64p_dynlib_handle core, void *context, void (*debug)(void *, int, const char *)) {
    (void)core; (void)context; (void)debug;
    live = live_map();
    return live ? M64ERR_SUCCESS : M64ERR_INCOMPATIBLE;
}
EXPORT m64p_error CALL PluginShutdown(void) { return M64ERR_SUCCESS; }
EXPORT m64p_error CALL PluginGetVersion(m64p_plugin_type *type, int *version, int *api, const char **name, int *caps) {
    if (type) *type = M64PLUGIN_AUDIO;
    if (version) *version = 0x010000;
    if (api) *api = 0x020000;
    if (name) *name = "decompdecomp live audio";
    if (caps) *caps = 0;
    return M64ERR_SUCCESS;
}
EXPORT int CALL InitiateAudio(AUDIO_INFO info) { ai = info; return 1; }
EXPORT void CALL AiDacrateChanged(int system) {
    clock_hz = system == 1 ? 49656530u : system == 2 ? 48628316u : 48681812u; /* NTSC, PAL, MPAL */
    if (live && ai.AI_DACRATE_REG) live[LIVE_AUDIO_HZ / 4] = clock_hz / (*ai.AI_DACRATE_REG + 1);
}
EXPORT void CALL AiLenChanged(void) {
    if (!live || !ai.RDRAM) return;
    uint32_t at = *ai.AI_DRAM_ADDR_REG & 0xFFFFF8u, bytes = *ai.AI_LEN_REG & 0x3FFF8u;
    const uint32_t *rdram = (const uint32_t *)ai.RDRAM; /* host-order words: left sample in the top half */
    volatile uint32_t *ring = live + LIVE_AUDIO / 4;
    uint32_t end = live[LIVE_AUDIO_END / 4];
    for (uint32_t i = 0; i < bytes / 4; i++, end++) {
        uint32_t w = rdram[(at >> 2) + i];
        ring[end % LIVE_RING] = (w >> 16 & 0xFFFF) | (w & 0xFFFF) << 16; /* left in the low half, as the page reads it */
    }
    __atomic_store_n(&live[LIVE_AUDIO_END / 4], end, __ATOMIC_RELEASE);
}
EXPORT void CALL ProcessAList(void) {}
EXPORT void CALL SetSpeedFactor(int percent) { (void)percent; }
EXPORT void CALL VolumeUp(void) {}
EXPORT void CALL VolumeDown(void) {}
EXPORT int CALL VolumeGetLevel(void) { return 100; }
EXPORT void CALL VolumeSetLevel(int level) { (void)level; }
EXPORT void CALL VolumeMute(void) {}
EXPORT const char *CALL VolumeGetString(void) { return "100%"; }
EXPORT int CALL RomOpen(void) { return 1; }
EXPORT void CALL RomClosed(void) {}
