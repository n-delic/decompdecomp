/* The browser build's live view (live.c's part, port/wasm/web.py): the frames, the sound and the controller go to
 * and from the page directly (consoles/n64/port_wasm/web/play.js) instead of over HTTP. */
#include <stdint.h>

#define IMPORT(name) __attribute__((import_module("port_web"), import_name(name)))
IMPORT("frame") void js_frame(const uint8_t *rgb, int w, int h);
IMPORT("audio") void js_audio(const int16_t *lr, int frames, int hz);
IMPORT("input") uint32_t js_input(void); /* buttons, then the stick's x and y, a byte each */
/* The GPU renderer (gl.js), when the player chose it: render.c's GPU path sends it its commands and frames */
IMPORT("gpu_ready") int js_gpu_ready(void);
IMPORT("gpu_run") void js_gpu_run(const uint32_t *commands, uint32_t words, const void *draws, const float *vertices,
                                   const uint32_t *pixels);
IMPORT("gpu_present") int js_gpu_present(uint32_t addr, int w, int h, int flags);

void port_live_start(void) {}
int port_live_active(void) { return 1; }
void port_live_frame(const uint8_t *rgb, int w, int h) { js_frame(rgb, w, h); }
void port_live_audio(const int16_t *lr, int frames, int hz) { js_audio(lr, frames, hz); }
int port_gpu_ready(void) { return js_gpu_ready(); }
void port_gpu_run(const uint32_t *commands, uint32_t words, const void *draws, const float *vertices, const uint32_t *pixels) {
    js_gpu_run(commands, words, draws, vertices, pixels);
}
int port_gpu_present(uint32_t addr, int w, int h, int flags) { return js_gpu_present(addr, w, h, flags); }
int port_live_input(uint16_t *buttons, int8_t *x, int8_t *y) {
    uint32_t v = js_input();
    *buttons = (uint16_t)(v >> 16);
    *x = (int8_t)(v >> 8);
    *y = (int8_t)v;
    return 1;
}
