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
