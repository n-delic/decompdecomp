/* The browser build's live view (live.c's part, port/wasm/web.py): the frames, the sound and the controller go to
 * and from the page directly (consoles/n64/port_wasm/web/play.js) instead of over HTTP. */
#include <stdint.h>

#define IMPORT(name) __attribute__((import_module("port_web"), import_name(name)))
IMPORT("frame") void js_frame(const uint8_t *rgb, int w, int h);
IMPORT("audio") void js_audio(const int16_t *lr, int frames, int hz);
IMPORT("input") uint32_t js_input(void); /* buttons, then the stick's x and y, a byte each */

void port_live_start(void) {}
int port_live_active(void) { return 1; }
void port_live_frame(const uint8_t *rgb, int w, int h) { js_frame(rgb, w, h); }
void port_live_audio(const int16_t *lr, int frames, int hz) { js_audio(lr, frames, hz); }
int port_live_input(uint16_t *buttons, int8_t *x, int8_t *y) {
    uint32_t v = js_input();
    *buttons = (uint16_t)(v >> 16);
    *x = (int8_t)(v >> 8);
    *y = (int8_t)v;
    return 1;
}
