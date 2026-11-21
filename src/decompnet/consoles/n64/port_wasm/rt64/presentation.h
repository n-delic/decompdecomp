#pragma once

#include <cstdint>

inline uint32_t port_rt64_vi_features(uint32_t status, uint32_t flags) {
    constexpr uint32_t bits[] = {0x8, 0x4, 0x10, 0x10000};
    for (unsigned i = 0; i < 4; ++i) {
        if (flags & (1u << (2 * i))) status |= bits[i];
        if (flags & (2u << (2 * i))) status &= ~bits[i];
    }
    return status;
}

inline bool port_rt64_incomplete_framebuffer(uint32_t width, uint32_t height, uint8_t size,
                                             uint32_t expected_width, uint32_t expected_height, uint8_t expected_size) {
    return width != expected_width || size != expected_size || height < expected_height;
}

struct PortRt64ViField {
    uint32_t origin, y_scale, v_start, v_burst, interrupt;
};

inline PortRt64ViField port_rt64_vi_field(const uint32_t *mode, unsigned field) {
    const auto *words = mode + 9 + (field & 1) * 5;
    return {words[0], words[1], words[2], words[3], words[4]};
}
