/* A tiny PNG writer for the adapter's frame readback (PORT_RT64_FRAMES): 8-bit RGB from BGRA pixels, stored
 * (uncompressed) deflate blocks. Standard library only, so a test can build it without Metal or RT64. */
#pragma once

#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <string>
#include <vector>

namespace png_dump {
inline uint32_t crc32_of(const uint8_t *p, size_t n) {
    static uint32_t table[256];
    if (!table[1])
        for (uint32_t i = 0; i < 256; ++i) {
            uint32_t c = i;
            for (int k = 0; k < 8; ++k) c = c & 1 ? 0xEDB88320u ^ (c >> 1) : c >> 1;
            table[i] = c;
        }
    uint32_t crc = ~0u;
    for (size_t i = 0; i < n; ++i) crc = table[(crc ^ p[i]) & 0xFF] ^ (crc >> 8);
    return ~crc;
}

inline void put32(std::vector<uint8_t> &out, uint32_t v) {
    for (int s = 24; s >= 0; s -= 8) out.push_back(uint8_t(v >> s));
}

inline void chunk(std::vector<uint8_t> &out, const char *type, const std::vector<uint8_t> &data) {
    put32(out, uint32_t(data.size()));
    std::vector<uint8_t> body(type, type + 4);
    body.insert(body.end(), data.begin(), data.end());
    out.insert(out.end(), body.begin(), body.end());
    put32(out, crc32_of(body.data(), body.size()));
}

inline std::vector<uint8_t> encode(const uint8_t *bgra, uint32_t w, uint32_t h, size_t row_bytes) {
    std::vector<uint8_t> raw;
    raw.reserve(size_t(h) * (w * 3 + 1));
    for (uint32_t y = 0; y < h; ++y) {
        raw.push_back(0);
        const uint8_t *row = bgra + y * row_bytes;
        for (uint32_t x = 0; x < w; ++x) {
            raw.push_back(row[x * 4 + 2]);
            raw.push_back(row[x * 4 + 1]);
            raw.push_back(row[x * 4 + 0]);
        }
    }
    std::vector<uint8_t> z = {0x78, 0x01};
    uint32_t a = 1, b = 0;
    for (uint8_t v : raw) {
        a = (a + v) % 65521;
        b = (b + a) % 65521;
    }
    for (size_t at = 0; at < raw.size(); at += 65535) {
        const size_t n = std::min<size_t>(65535, raw.size() - at);
        z.push_back(at + n >= raw.size());
        z.push_back(uint8_t(n)); z.push_back(uint8_t(n >> 8));
        z.push_back(uint8_t(~n)); z.push_back(uint8_t((~n) >> 8));
        z.insert(z.end(), raw.begin() + at, raw.begin() + at + n);
    }
    put32(z, (b << 16) | a);
    std::vector<uint8_t> png = {0x89, 'P', 'N', 'G', 0x0D, 0x0A, 0x1A, 0x0A}, ihdr;
    put32(ihdr, w);
    put32(ihdr, h);
    ihdr.insert(ihdr.end(), {8, 2, 0, 0, 0});
    chunk(png, "IHDR", ihdr);
    chunk(png, "IDAT", z);
    chunk(png, "IEND", {});
    return png;
}

inline bool write(const std::string &path, const uint8_t *bgra, uint32_t w, uint32_t h, size_t row_bytes) {
    const auto png = encode(bgra, w, h, row_bytes);
    FILE *f = std::fopen(path.c_str(), "wb");
    if (!f) return false;
    std::fwrite(png.data(), 1, png.size(), f);
    std::fclose(f);
    return true;
}
}
