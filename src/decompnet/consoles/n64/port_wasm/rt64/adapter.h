/* Native RT64 bridge, versioned because the adapter is built separately from the game. */
#pragma once

#include <stdint.h>

enum { PORT_RT64_ABI = 2 };

typedef struct {
    uint32_t abi;
    int (*open)(void *window, void *metal_layer);
    void (*task)(const uint8_t *rdram, uint32_t ucode, uint32_t ucode_data, uint32_t display_list);
    void (*screen)(const uint8_t *rdram, uint32_t framebuffer);
    void (*mode)(const uint32_t *words);
    void (*yscale)(float scale);
    void (*close)(void);
    void (*features)(uint32_t flags);
} PortRt64;

#ifndef PORT_WEB
void port_rt64_task(const uint8_t *rdram, uint32_t ucode, uint32_t ucode_data, uint32_t display_list);
void port_rt64_screen(const uint8_t *rdram, uint32_t framebuffer);
void port_rt64_mode(const uint32_t *words);
void port_rt64_yscale(float scale);
void port_rt64_features(uint32_t flags);
#endif
