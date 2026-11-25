/* Native RT64 bridge, versioned because the adapter is built separately from the game. */
#pragma once

#include <stdint.h>

enum { PORT_RT64_ABI = 4, PORT_RT64_VI_MODE_WORDS = 19, PORT_RT64_VI_SERRATE = 0x40 };

/* The host's overlay painter (the settings menu), called on RT64's render thread once per presented frame with a
   cleared-by-you RGBA buffer of the drawable's width by height. Return 0 to draw nothing this frame; otherwise set
   `rect` to the part of the buffer to put over the game (x, y, width, height, in pixels): only that part is uploaded
   and it replaces the game's pixels there, so paint it opaque. */
typedef struct { uint32_t x, y, width, height; } PortRt64Rect;
typedef int (*PortRt64Paint)(uint8_t *rgba, uint32_t width, uint32_t height, PortRt64Rect *rect);

typedef struct {
    uint32_t abi;
    int (*open)(void *window, void *metal_layer);
    void (*task)(const uint8_t *rdram, uint32_t ucode, uint32_t ucode_data, uint32_t display_list);
    void (*screen)(const uint8_t *rdram, uint32_t framebuffer);
    void (*mode)(const uint32_t *words);
    void (*yscale)(float scale);
    void (*close)(void);
    void (*features)(uint32_t flags);
    void (*retrace)(unsigned field);
    void (*overlay)(PortRt64Paint paint); /* before open: the window's layer is then made copyable */
} PortRt64;

#ifndef PORT_WEB
void port_rt64_task(const uint8_t *rdram, uint32_t ucode, uint32_t ucode_data, uint32_t display_list);
void port_rt64_screen(const uint8_t *rdram, uint32_t framebuffer);
void port_rt64_mode(const uint32_t *words);
void port_rt64_yscale(float scale);
void port_rt64_features(uint32_t flags);
void port_rt64_retrace(unsigned field);
#endif
