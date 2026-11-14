/*
 * The RSP and RDP, in software: each graphics task's display list drawn into the game's own frame buffer, as the
 * N64 does (port/wasm). A first renderer, to see the game: Nintendo's F3DEX2 (3D) and S2DEX (2D backgrounds and
 * sprites) microcodes, chosen per task by the microcode it loads, and the RDP commands both send. Textures filtered
 * as the RDP filters them (point, its three-texel bilinear, or average), one or two combiner cycles, the blender
 * with fog, a depth buffer, anti-aliasing (the RDP's coverage and the VI's filter), no dithering.
 *
 * Addresses in a display list are segmented (segment << 24 | offset) or physical; the segment table is the list's
 * own (G_MOVEWORD). The game's memory is big-endian at the console's addresses: physical p is 0x80000000 | p here.
 *
 * PORT_LIVE=port: played in a browser (live.c).
 * PORT_FRAMES=folder: every frame shown saved there as frame_NNNNN.png (PORT_FRAME_EVERY=n: every nth).
 * PORT_DLTRACE=n: the display lists of tasks n to n+5 printed.
 */
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

uint8_t *port_memory(void);
static uint8_t *R;
static uint32_t rd32(uint32_t a) { uint32_t v; memcpy(&v, R + a, 4); return __builtin_bswap32(v); }
static uint16_t rd16(uint32_t a) { uint16_t v; memcpy(&v, R + a, 2); return __builtin_bswap16(v); }
static void wr16(uint32_t a, uint16_t v) { v = __builtin_bswap16(v); memcpy(R + a, &v, 2); }

static uint32_t segments[16];
static uint32_t phys(uint32_t a) { /* a display-list address -> the game's memory */
    if (a >= 0x80000000u) return 0x80000000u | (a & 0x1FFFFFFF);
    return 0x80000000u | ((segments[(a >> 24) & 15] + (a & 0x00FFFFFF)) & 0x1FFFFFFF);
}

/* ---------------------------------------------------------------- RDP state */

typedef struct { float r, g, b, a; } Color;
typedef struct { int fmt, siz, line, tmem, palette, cms, cmt, masks, maskt, shifts, shiftt; float uls, ult, lrs, lrt; } Tile;

static uint8_t tmem[4096];
static uint16_t tlut[256];
static Tile tiles[8];
static uint32_t timg_addr; static int timg_siz, timg_width;
static uint32_t cimg_addr; static int cimg_width = 320;
static int cimg_siz = 2; /* G_SETCIMG's size: 1 8-bit, 2 16-bit, 3 32-bit */
static int cimg_siz_now(void) { return cimg_siz; }
static uint32_t zimg_addr;
static uint32_t fill_color;
static Color prim, env, blend, fog;
static int fog_mul, fog_off; /* G_MW_FOG: the RSP's fog alpha is z/w * fog_mul + fog_off, 0 to 255 */
static uint32_t combine0, combine1, mode_h, mode_l;
static int scissor[4] = {0, 0, 320, 240};
#define MAXW 640
#define MAXH 480
/* Each pixel's coverage, as the RDP keeps it in the frame buffer's hidden bits: how many of a pixel's 8 sample
   points are covered, kept as 8 minus that so that memory starts fully covered. By the pixel's address in RDRAM. */
static uint8_t cvg_hole[4u << 20];
static int cvg_get(uint32_t a) { return 8 - cvg_hole[(a & 0x7FFFFF) >> 1]; }
static void cvg_set(uint32_t a, int c) { cvg_hole[(a & 0x7FFFFF) >> 1] = (uint8_t)(8 - (c < 0 ? 0 : c > 8 ? 8 : c)); }
static uint32_t images[8][2]; /* colour images drawn to: address, width */
static int frame_height(void) { return cimg_width > 320 ? 480 : 240; } /* 640 wide: the high-resolution mode */

static int cycle_type(void) { return (mode_h >> 20) & 3; } /* 0 one, 1 two, 2 copy, 3 fill */

static Color unpack5551(uint16_t c) {
    return (Color){((c >> 11) & 31) / 31.0f, ((c >> 6) & 31) / 31.0f, ((c >> 1) & 31) / 31.0f, (c & 1) ? 1.0f : 0.0f};
}
static Color unpack8888(uint32_t c) {
    return (Color){(c >> 24) / 255.0f, ((c >> 16) & 255) / 255.0f, ((c >> 8) & 255) / 255.0f, (c & 255) / 255.0f};
}
static int clamp31(float v) { int i = (int)(v * 31.0f + 0.5f); return i < 0 ? 0 : i > 31 ? 31 : i; }
static uint16_t pack5551(Color c) { return (uint16_t)(clamp31(c.r) << 11 | clamp31(c.g) << 6 | clamp31(c.b) << 1 | 1); }

/* A texel coordinate as the RDP takes it: clamped to the tile first (when its clamp bit is set, or it has no mask),
   then wrapped or mirrored inside its mask. A clamped, mirrored tile wider than its mask shows the mirrored copy
   across its whole width; taking the clamp bit as "clamp only" read the second half raw from TMEM (a decal mirrored
   both ways came out as half a decal and garbage). */
static int wrap_coord(int s, int size, int cm, int mask) {
    if ((cm & 2) || !mask) s = s < 0 ? 0 : s >= size ? size - 1 : s;
    if (!mask) return s;
    int m = 1 << mask;
    if ((cm & 1) && ((s >> mask) & 1)) return m - 1 - (s & (m - 1));
    return s & (m - 1);
}

/* A tile's shift on texture coordinates: 1 to 10 divide by 2^shift, 11 to 15 multiply by 2^(16 - shift). */
static float shifted(float v, int shift) {
    if (!shift) return v;
    return shift > 10 ? v * (float)(1 << (16 - shift)) : v / (float)(1 << shift);
}

/* A palette entry as other mode H's TLUT type reads it: RGBA 5551, or IA16 (intensity, alpha). A game's text drawn
   with an IA16 palette came out as solid blocks when every palette was read as RGBA. */
static Color tlut_colour(uint16_t v) {
    if (((mode_h >> 14) & 3) == 3) { float i = (v >> 8) / 255.0f; return (Color){i, i, i, (v & 255) / 255.0f}; }
    return unpack5551(v);
}

/* A texel of a tile at integer coordinates (s, t), from TMEM as the loads left it. */
static Color texel(const Tile *tile, int s, int t) {
    int w = (int)(tile->lrs - tile->uls) + 1, h = (int)(tile->lrt - tile->ult) + 1;
    s -= (int)tile->uls; t -= (int)tile->ult;
    s = wrap_coord(s, w, tile->cms, tile->masks);
    t = wrap_coord(t, h, tile->cmt, tile->maskt);
    if (s < 0) s = 0;
    if (t < 0) t = 0;
    uint32_t row = (uint32_t)(tile->tmem * 8 + t * tile->line * 8);
    uint32_t odd = (t & 1) ? 4 : 0; /* TMEM keeps odd rows' 32-bit halves swapped: read them back */
    switch (tile->siz) {
    case 0: { /* 4-bit */
        uint8_t b = tmem[((row + s / 2) ^ odd) & 4095];
        int v = (s & 1) ? b & 15 : b >> 4;
        if (tile->fmt == 2) return tlut_colour(tlut[(tile->palette * 16 + v) & 255]);
        if (tile->fmt == 3) { float i = (v >> 1) / 7.0f; return (Color){i, i, i, (v & 1) ? 1.0f : 0.0f}; }
        float i = v / 15.0f;
        return (Color){i, i, i, i};
    }
    case 1: { /* 8-bit */
        uint8_t v = tmem[((row + s) ^ odd) & 4095];
        if (tile->fmt == 2) return tlut_colour(tlut[v]);
        if (tile->fmt == 3) { float i = (v >> 4) / 15.0f; return (Color){i, i, i, (v & 15) / 15.0f}; }
        float i = v / 255.0f;
        return (Color){i, i, i, i};
    }
    case 2: { /* 16-bit */
        uint32_t a = ((row + s * 2) ^ odd) & 4094;
        uint16_t v = (uint16_t)(tmem[a] << 8 | tmem[a + 1]);
        if (tile->fmt == 3) { float i = (v >> 8) / 255.0f; return (Color){i, i, i, (v & 255) / 255.0f}; }
        return unpack5551(v);
    }
    default: { /* 32-bit: red and green in TMEM's low half, blue and alpha at the same place in its high half */
        uint32_t a = ((row + s * 2) ^ odd) & 2046;
        return (Color){tmem[a] / 255.0f, tmem[a + 1] / 255.0f, tmem[a + 2048] / 255.0f, tmem[a + 2049] / 255.0f};
    }
    }
}

static uint32_t texel_raw(const Tile *tile, int s, int t) {
    int w = (int)(tile->lrs - tile->uls) + 1, h = (int)(tile->lrt - tile->ult) + 1;
    s = wrap_coord(s - (int)tile->uls, w, tile->cms, tile->masks);
    t = wrap_coord(t - (int)tile->ult, h, tile->cmt, tile->maskt);
    if (s < 0) s = 0;
    if (t < 0) t = 0;
    uint32_t row = (uint32_t)(tile->tmem * 8 + t * tile->line * 8), odd = (t & 1) ? 4 : 0;
    switch (tile->siz) {
    case 0: { uint8_t b = tmem[((row + s / 2) ^ odd) & 4095]; int v = (s & 1) ? b & 15 : b >> 4;
        return tile->fmt == 2 && cimg_siz_now() != 1 ? tlut[(tile->palette * 16 + v) & 255] : (uint32_t)v; }
    case 1: { uint8_t v = tmem[((row + s) ^ odd) & 4095];
        return tile->fmt == 2 && cimg_siz_now() != 1 ? tlut[v] : v; }
    case 2: { uint32_t a = ((row + s * 2) ^ odd) & 4094; return (uint32_t)(tmem[a] << 8 | tmem[a + 1]); }
    }
    Color c = texel(tile, s + (int)tile->uls, t + (int)tile->ult);
    return pack5551(c);
}

/* A texel of a tile at (s, t) in texels, filtered as other mode H asks (G_TF_POINT, G_TF_BILERP, G_TF_AVERAGE).
   The RDP's bilinear filter reads three texels, not four: the corner nearest the point and its two neighbours, from
   whichever triangle of the 2x2 square the point falls in. Copy mode never filters. A racing game asks for bilerp
   on its 3D and its menu text; drawn by point, its trees and roads were blocks of texels. */
static Color mix(Color a, Color b, Color c, float fb, float fc) { /* a + fb (b - a) + fc (c - a) */
    return (Color){a.r + fb * (b.r - a.r) + fc * (c.r - a.r), a.g + fb * (b.g - a.g) + fc * (c.g - a.g),
                   a.b + fb * (b.b - a.b) + fc * (c.b - a.b), a.a + fb * (b.a - a.a) + fc * (c.a - a.a)};
}
static Color sample(const Tile *tile, float s, float t) {
    int filter = (int)((mode_h >> 12) & 3), s0 = (int)floorf(s), t0 = (int)floorf(t);
    if (filter == 0 || cycle_type() >= 2) return texel(tile, s0, t0);
    float fs = s - (float)s0, ft = t - (float)t0;
    Color c00 = texel(tile, s0, t0), c10 = texel(tile, s0 + 1, t0), c01 = texel(tile, s0, t0 + 1);
    Color c11 = texel(tile, s0 + 1, t0 + 1);
    if (filter == 3) /* average: the four texels' mean */
        return (Color){(c00.r + c10.r + c01.r + c11.r) / 4, (c00.g + c10.g + c01.g + c11.g) / 4,
                       (c00.b + c10.b + c01.b + c11.b) / 4, (c00.a + c10.a + c01.a + c11.a) / 4};
    if (fs + ft < 1.0f) return mix(c00, c10, c01, fs, ft);
    return mix(c11, c01, c10, 1.0f - fs, 1.0f - ft);
}

/* ---------------------------------------------------------------- the colour combiner */

/* What one pixel's combiner sees: the texels of the two tiles (TEXEL0, TEXEL1), the level of detail's fraction,
   the shade, and the noise. */
typedef struct { Color t0, t1; float lod; } Texels;
static Texels one_texel(Color c) { return (Texels){c, c, 0}; }
static float prim_lod_frac, k4, k5;
static int prim_min_level;
static Color key_center, key_scale;
static uint32_t noise_seed = 0x12345678u;
static float noise(void) { noise_seed = noise_seed * 1664525u + 1013904223u; return (float)(noise_seed >> 24) / 255.0f; }

typedef struct { Color comb, t0, t1, shade; float lod, noise; } CombIn;
static Color grey(float v) { return (Color){v, v, v, v}; }
/* The RGB inputs: `which` is A (4 bits), B (4), C (5) or D (3), as G_SETCOMBINE lays them out. */
static Color cc_rgb(int which, int sel, const CombIn *in) {
    switch (sel) {
    case 0: return in->comb;
    case 1: return in->t0;
    case 2: return in->t1;
    case 3: return prim;
    case 4: return in->shade;
    case 5: return env;
    case 6: return which == 1 ? key_center : which == 2 ? key_scale : grey(1); /* 1, or the chroma key's */
    case 7: return which == 0 ? grey(in->noise) : which == 1 ? grey(k4) : which == 2 ? grey(in->comb.a) : grey(0);
    }
    if (which != 2) return grey(0);
    switch (sel) {
    case 8: return grey(in->t0.a);
    case 9: return grey(in->t1.a);
    case 10: return grey(prim.a);
    case 11: return grey(in->shade.a);
    case 12: return grey(env.a);
    case 13: return grey(in->lod);
    case 14: return grey(prim_lod_frac);
    case 15: return grey(k5);
    }
    return grey(0);
}
/* The alpha inputs: A, B and D share one list; C has the level of detail's fractions in place of the combined
   alpha and 1. */
static float cc_alpha(int which, int sel, const CombIn *in) {
    switch (sel) {
    case 0: return which == 2 ? in->lod : in->comb.a;
    case 1: return in->t0.a;
    case 2: return in->t1.a;
    case 3: return prim.a;
    case 4: return in->shade.a;
    case 5: return env.a;
    case 6: return which == 2 ? prim_lod_frac : 1.0f;
    }
    return 0;
}
static float clamp01(float v) { return v < 0 ? 0 : v > 1 ? 1 : v; }
static Color combine(int cycle, const CombIn *in) {
    int a, b, c, d, aa, ab, ac, ad;
    if (cycle == 0) {
        a = (combine0 >> 20) & 15; c = (combine0 >> 15) & 31; aa = (combine0 >> 12) & 7; ac = (combine0 >> 9) & 7;
        b = (combine1 >> 28) & 15; d = (combine1 >> 15) & 7; ab = (combine1 >> 12) & 7; ad = (combine1 >> 9) & 7;
    } else {
        a = (combine0 >> 5) & 15; c = combine0 & 31; aa = (combine1 >> 21) & 7; ac = (combine1 >> 18) & 7;
        b = (combine1 >> 24) & 15; d = (combine1 >> 6) & 7; ab = (combine1 >> 3) & 7; ad = combine1 & 7;
    }
    Color A = cc_rgb(0, a, in), B = cc_rgb(1, b, in), C = cc_rgb(2, c, in), D = cc_rgb(3, d, in);
    Color out = {(A.r - B.r) * C.r + D.r, (A.g - B.g) * C.g + D.g, (A.b - B.b) * C.b + D.b, 0};
    out.a = (cc_alpha(0, aa, in) - cc_alpha(1, ab, in)) * cc_alpha(2, ac, in) + cc_alpha(3, ad, in);
    return (Color){clamp01(out.r), clamp01(out.g), clamp01(out.b), clamp01(out.a)};
}
/* Both cycles as the RDP runs them. One cycle runs the first cycle's settings (libultra's one-cycle modes set both
   alike; which one the hardware reads is unsettled here, and the first is what the games tried so far draw with). In
   two cycles the second sees the texels swapped: its TEXEL0 is the first cycle's TEXEL1 (and its TEXEL1 the next
   pixel's TEXEL0, taken here as this one's). */
static Color combiner(const Texels *tx, Color shade) {
    CombIn in = {{0, 0, 0, 0}, tx->t0, tx->t1, shade, tx->lod, noise()};
    if (cycle_type() != 1) return combine(0, &in);
    in.comb = combine(0, &in);
    in.t0 = tx->t1; in.t1 = tx->t0;
    return combine(1, &in);
}

/* ---------------------------------------------------------------- the frame and depth images */

static int in_frame(int x, int y) {
    return x >= scissor[0] && y >= scissor[1] && x < scissor[2] && y < scissor[3] && x >= 0 && y >= 0 && x < cimg_width && y < frame_height();
}
static uint32_t pixel_addr(int x, int y) {
    uint32_t bytes = cimg_siz == 3 ? 4 : cimg_siz == 1 ? 1 : 2;
    return 0x80000000u | ((cimg_addr + (uint32_t)(y * cimg_width + x) * bytes) & 0x1FFFFFFF);
}
static Color read_pixel(uint32_t a) {
    if (cimg_siz == 3) return unpack8888(rd32(a));
    if (cimg_siz == 1) return grey(R[a] / 255.0f);
    return unpack5551(rd16(a));
}

/* The RDP's ordered dither, added to each 8-bit channel before it's cut to 5 bits: the magic square or Bayer
   matrix by the pixel's place, or noise, as other mode H's RGB dither asks (G_CD_*, bits 6 and 7). Without it,
   smooth gradients (skies, fades) showed bands the console doesn't. */
static const uint8_t magic_square[16] = {0, 6, 1, 7, 4, 2, 5, 3, 3, 5, 2, 4, 7, 1, 6, 0};
static const uint8_t bayer[16] = {0, 4, 1, 5, 4, 0, 5, 1, 3, 7, 2, 6, 7, 3, 6, 2};
static int dither_at(int x, int y) {
    switch ((mode_h >> 6) & 3) {
    case 0: return magic_square[(y & 3) * 4 + (x & 3)];
    case 1: return bayer[(y & 3) * 4 + (x & 3)];
    case 2: return (int)(noise() * 7.99f);
    }
    return 0;
}
static int to5(float v, int d) { int i = ((int)(v * 255.0f + 0.5f) + d) >> 3; return i < 0 ? 0 : i > 31 ? 31 : i; }
static void write_pixel(int x, int y, uint32_t a, Color c, int dither) {
    if (cimg_siz == 3) {
        uint32_t v = (uint32_t)(clamp01(c.r) * 255 + 0.5f) << 24 | (uint32_t)(clamp01(c.g) * 255 + 0.5f) << 16
                   | (uint32_t)(clamp01(c.b) * 255 + 0.5f) << 8 | (uint32_t)(clamp01(c.a) * 255 + 0.5f);
        R[a] = (uint8_t)(v >> 24); R[a + 1] = (uint8_t)(v >> 16); R[a + 2] = (uint8_t)(v >> 8); R[a + 3] = (uint8_t)v;
        return;
    }
    if (cimg_siz == 1) { R[a] = (uint8_t)(clamp01(c.r) * 255 + 0.5f); return; }
    int d = dither ? dither_at(x, y) : 0;
    wr16(a, (uint16_t)(to5(c.r, d) << 11 | to5(c.g, d) << 6 | to5(c.b, d) << 1 | 1));
}

/* Depth, kept where the game keeps it: the z image in RDRAM, in the RDP's own 16-bit format (an 18-bit depth as a
   3-bit exponent and an 11-bit mantissa, then the top 2 bits of its 4-bit delta z; the other 2 in hidden bits).
   So a game can clear it, draw into it (a lens's circle, a prerendered room's depth copied in) and read it (a
   flare's visibility test), as on the console. A private float buffer stood in for it before, which only fill-mode
   clears reached. Depths here are the RDP's: 0 to 0x3FFFF, the screen's 0 to 1023 times 256. Without a z image
   (none set yet) a private buffer is used. */
static uint8_t dz_hole[4u << 20];
static const struct { uint32_t base; int shift; } z_ranges[8] = {
    {0x00000, 6}, {0x20000, 5}, {0x30000, 4}, {0x38000, 3}, {0x3C000, 2}, {0x3E000, 1}, {0x3F000, 0}, {0x3F800, 0}};
static uint16_t z_encode(float z, float dz) {
    uint32_t zi = z <= 0 ? 0 : z >= 0x3FFFF ? 0x3FFFF : (uint32_t)z, e = 0;
    while (e < 7 && zi >= z_ranges[e + 1].base) e++;
    uint32_t m = ((zi - z_ranges[e].base) >> z_ranges[e].shift) & 0x7FF;
    int dzc = 0;
    while (dzc < 15 && (float)(1 << dzc) < dz) dzc++;
    return (uint16_t)(e << 13 | m << 2 | (uint32_t)(dzc >> 2));
}
static float z_decode(uint16_t v) {
    uint32_t e = v >> 13, m = (v >> 2) & 0x7FF;
    return (float)(z_ranges[e].base + (m << z_ranges[e].shift));
}
static float zbuf_private[MAXW * MAXH];
static int z_in_memory(void) { return zimg_addr != 0; }
static uint32_t z_addr(int x, int y) { return 0x80000000u | ((zimg_addr + (uint32_t)(y * cimg_width + x) * 2) & 0x1FFFFFFF); }
static float z_read(int x, int y, float *dz) {
    if (!z_in_memory()) { *dz = 0; return zbuf_private[y * MAXW + x]; }
    uint32_t a = z_addr(x, y);
    uint16_t v = rd16(a);
    *dz = (float)(1 << (((v & 3) << 2) | dz_hole[(a & 0x7FFFFF) >> 1]));
    return z_decode(v);
}
static void z_write(int x, int y, float z, float dz) {
    if (!z_in_memory()) { zbuf_private[y * MAXW + x] = z; return; }
    uint32_t a = z_addr(x, y);
    uint16_t v = z_encode(z, dz);
    wr16(a, v);
    int dzc = 0;
    while (dzc < 15 && (float)(1 << dzc) < dz) dzc++;
    dz_hole[(a & 0x7FFFFF) >> 1] = (uint8_t)(dzc & 3);
}
static float prim_z, prim_dz; /* G_SETPRIMDEPTH, for G_ZS_PRIM */

/* ---------------------------------------------------------------- the blender */

/* The blender, one cycle of it: P * A + M * B, each picked by other mode L (the first cycle's at bits 30, 26, 22 and
   18, the second's at 28, 24, 20 and 16). P and M: the pixel coming in, the frame buffer's, the blend colour or the
   fog colour; A: the incoming alpha, the fog colour's, the shade's (the RSP's fog alpha under G_FOG) or 0; B: 1 - A,
   the frame buffer's alpha, 1 or 0. Alpha passes through. A racing game fogs most of a race in the first cycle
   (fog * shade alpha + in * (1 - shade alpha)); without it, every race was drawn without its haze. */
static Color blender_cycle(int cycle, Color in, Color mem, Color shade) {
    int at = cycle == 0 ? 30 : 28;
    int p = (int)(mode_l >> at) & 3, a = (int)(mode_l >> (at - 4)) & 3;
    int m = (int)(mode_l >> (at - 8)) & 3, b = (int)(mode_l >> (at - 12)) & 3;
    Color pick[4] = {in, mem, blend, fog};
    float A = a == 0 ? in.a : a == 1 ? fog.a : a == 2 ? shade.a : 0;
    float B = b == 0 ? 1 - A : b == 1 ? mem.a : b == 2 ? 1 : 0;
    float sum = A + B > 1 ? A + B : 1;
    Color P = pick[p], M = pick[m];
    return (Color){(P.r * A + M.r * B) / sum, (P.g * A + M.g * B) / sum, (P.b * A + M.b * B) / sum, in.a};
}
/* Both cycles as the RDP runs them: in two-cycle mode the first always blends (fog); the last blends only with
   force blend (anti-aliasing, which would blend edges, isn't drawn here) and otherwise passes its P through. */
static Color blender(Color in, Color mem, Color shade) {
    int two = cycle_type() == 1;
    Color out = two ? blender_cycle(0, in, mem, shade) : in;
    int last = two ? 1 : 0;
    if (mode_l & 0x4000) return blender_cycle(last, out, mem, shade);
    Color pick[4] = {out, mem, blend, fog};
    Color passed = pick[(mode_l >> (last ? 28 : 30)) & 3];
    passed.a = in.a;
    return passed;
}

/* One pixel through the combiner and blender: shade and texels in, the frame buffer written. `cvg`: how many of
   its 8 sample points the primitive covers (8 but at a triangle's edge, with anti-aliasing on); `dz`: how far its
   depth may be from what's there and still count as the same surface (a mesh's neighbouring triangle).

   With anti-aliasing (AA_EN), as the RDP does: a partly covered pixel at the same depth as what's there (a mesh's
   internal edge) blends with it by the two coverages, which add up, so the mesh's edges join without a seam; one in
   front is written as it is with its partial coverage, a silhouette edge, which the VI's filter later blends with
   the background around it (port_frame_shown). Under force blend (translucent surfaces) the coverage scales the
   alpha instead. Without anti-aliasing every pixel is written whole.

   Alpha as coverage (a texture's cut-out edge: leaves, grass, fences): CVG_X_ALPHA scales the coverage by the
   alpha, ALPHA_CVG_SEL makes the coverage the blender's alpha; a pixel left with no coverage isn't drawn. The edge
   pixels keep their partial coverage, so the VI's filter softens a cut-out's outline as it does a silhouette's.

   Depth: Z_CMP compares (in decal mode, only a surface at the same depth passes: a shadow or a mark on a wall), and
   Z_UPD writes; G_ZS_PRIM takes the depth from G_SETPRIMDEPTH for every pixel (2D drawn at a depth). */
static void plot(int x, int y, float z, Color shade, const Texels *tx, int depth, int cvg, float dz) {
    if (!in_frame(x, y)) return;
    int zcmp = (mode_l & 0x10) != 0, zupd = (mode_l & 0x20) != 0, decal = ((mode_l >> 10) & 3) == 3;
    int aa = (mode_l & 0x8) != 0, force = (mode_l & 0x4000) != 0, same_surface = 1;
    if (mode_l & 4) { z = prim_z; dz = prim_dz; depth = 1; } /* G_ZS_PRIM */
    float mem_z = 0, mem_dz = 0;
    if (depth && (zcmp || zupd)) mem_z = z_read(x, y, &mem_dz);
    if (depth && zcmp) {
        float dzmax = dz > mem_dz ? dz : mem_dz;
        if (decal) { if (fabsf(z - mem_z) > dzmax) return; }
        else if (z > mem_z + dzmax) return;
        same_surface = fabsf(z - mem_z) <= dzmax;
    }
    Color c = combiner(tx, shade);
    int ac = (int)(mode_l & 3);
    if (ac == 1 && c.a <= blend.a) return;          /* alpha compare: above the blend colour's alpha */
    if (ac == 3 && c.a <= noise()) return;          /* or above a random threshold (G_AC_DITHER) */
    if (mode_l & 0x1000) cvg = (int)(cvg * c.a + 0.5f); /* CVG_X_ALPHA */
    if (mode_l & 0x2000) c.a = cvg / 8.0f;              /* ALPHA_CVG_SEL */
    if ((mode_l & 0x3000) && cvg <= 0) return;
    uint32_t a = pixel_addr(x, y);
    Color mem = read_pixel(a);
    int mem_cvg = cvg_get(a), stored = 8, dither = 1;
    if (aa && force) {
        if (!(mode_l & 0x2000)) c.a *= cvg / 8.0f;
        stored = mem_cvg;
    } else if (aa && cvg < 8 && same_surface && mem_cvg + cvg <= 8 && depth) {
        Color in = blender(c, mem, shade);
        float wi = (float)cvg, wm = (float)mem_cvg;
        c = (Color){(in.r * wi + mem.r * wm) / (wi + wm), (in.g * wi + mem.g * wm) / (wi + wm),
                    (in.b * wi + mem.b * wm) / (wi + wm), c.a};
        write_pixel(x, y, a, c, dither);
        cvg_set(a, mem_cvg + cvg);
        if (zupd && z < mem_z) z_write(x, y, z, dz);
        return;
    } else if (aa || (mode_l & 0x3000)) {
        stored = cvg;
    }
    c = blender(c, mem, shade);
    write_pixel(x, y, a, c, dither);
    cvg_set(a, stored);
    if (depth && zupd) z_write(x, y, z, dz);
}

/* Fill mode writes the fill colour as it is: two 16-bit pixels a word (four 8-bit, one 32-bit). */
static void fill_pixel(int x, int y) {
    uint32_t a = pixel_addr(x, y);
    if (cimg_siz == 3) { R[a] = (uint8_t)(fill_color >> 24); R[a + 1] = (uint8_t)(fill_color >> 16); R[a + 2] = (uint8_t)(fill_color >> 8); R[a + 3] = (uint8_t)fill_color; }
    else if (cimg_siz == 1) R[a] = (uint8_t)(fill_color >> (8 * (3 - (x & 3))));
    else wr16(a, (uint16_t)((x & 1) ? fill_color : fill_color >> 16));
    cvg_set(a, 8);
    if (cimg_addr == zimg_addr && !z_in_memory()) zbuf_private[y * MAXW + x] = 1e30f;
}
static void fill_rect(int x0, int y0, int x1, int y1) {
    if (cycle_type() == 3) { /* fill mode: edges inclusive */
        for (int y = y0; y <= y1; y++)
            for (int x = x0; x <= x1; x++) if (in_frame(x, y)) fill_pixel(x, y);
        return;
    }
    Texels none = one_texel((Color){0, 0, 0, 0});
    for (int y = y0; y < y1; y++)
        for (int x = x0; x < x1; x++) plot(x, y, 0, prim, &none, 0, 8, 0);
}

/* The texels a pixel sees (TEXEL0, TEXEL1 and the level of detail's fraction), from the tile G_TEXTURE or the
   rectangle names, at (s, t) in texels before each tile's shift; `lod` is how many texels one pixel steps. */
static Texels texels_at(int tile, float s, float t, float lod);

/* Copy mode moves texels as they are, without the combiner or blender: a 16-bit frame gets the texel's 16 bits
   (a palette's entry for colour-indexed ones), an 8-bit frame the texel's byte. */
static uint32_t texel_raw(const Tile *tile, int s, int t);

/* A textured rectangle from (x0, y0) to (x1, y1) in pixels, texture coordinates s, t at its corner and their steps
   per pixel. */
static void tex_rect(int tile, float x0, float y0, float x1, float y1, float s, float t, float dsdx, float dtdy, int flip) {
    int copy = cycle_type() == 2;
    if (copy) { dsdx /= 4.0f; x1 += 1; y1 += 1; }
    float lod = fmaxf(fabsf(dsdx), fabsf(dtdy));
    for (int y = (int)y0; y < (int)y1; y++)
        for (int x = (int)x0; x < (int)x1; x++) {
            float dx = x - x0, dy = y - y0;
            float ss = s + (flip ? dy : dx) * dsdx, tt = t + (flip ? dx : dy) * dtdy;
            if (copy) {
                if (!in_frame(x, y)) continue;
                const Tile *tl = &tiles[tile];
                int si = (int)floorf(shifted(ss, tl->shifts)), ti = (int)floorf(shifted(tt, tl->shiftt));
                if ((mode_l & 3) == 1 && texel(tl, si, ti).a <= blend.a) continue;
                uint32_t raw = texel_raw(tl, si, ti), a = pixel_addr(x, y);
                if (cimg_siz == 1) R[a] = (uint8_t)raw;
                else if (cimg_siz == 3) { Color c = texel(tl, si, ti); write_pixel(x, y, a, c, 0); }
                else wr16(a, (uint16_t)raw);
                cvg_set(a, 8);
                continue;
            }
            Texels tx = texels_at(tile, ss, tt, lod);
            plot(x, y, 0, (Color){1, 1, 1, 1}, &tx, 0, 8, 0);
        }
}

/* ---------------------------------------------------------------- texture loads */

/* A 64-bit word into TMEM; on an odd line its 32-bit halves are swapped, as the RDP stores them. */
static void tmem_word(uint32_t at, uint32_t src, int odd) {
    for (int k = 0; k < 8; k++) tmem[(at + (uint32_t)(odd ? k ^ 4 : k)) & 4095] = R[src + k];
}
/* A 32-bit texel into TMEM as the RDP keeps it: red and green at `at` in the low half, blue and alpha at the same
   place in the high half. A 32-bit tile's line and TMEM address therefore count two bytes a texel: each row read at
   its line's pitch is half a row of whole texels, which once made a racing game's soft car shadows (a 16x16 RGBA32
   texture) come out as dark stripes. */
static void tmem_texel32(uint32_t at, uint32_t src, int odd) {
    uint32_t a = (at ^ (odd ? 4u : 0u)) & 2046;
    tmem[a] = R[src]; tmem[a + 1] = R[src + 1]; tmem[a + 2048] = R[src + 2]; tmem[a + 2049] = R[src + 3];
}
static void load_block(int tile, int uls, int ult, int lrs, int dxt) {
    Tile *tl = &tiles[tile];
    if (timg_siz == 3) { /* each 64-bit word two texels; the line counter as below */
        uint32_t src = phys(timg_addr) + (uint32_t)((ult * timg_width + uls) * 4), line = 0;
        uint32_t words = (uint32_t)(lrs - uls + 2) / 2;
        for (uint32_t i = 0; i < words && tl->tmem * 8 + i * 4 < 2048; i++) {
            for (uint32_t k = 0; k < 2; k++) tmem_texel32(tl->tmem * 8 + i * 4 + k * 2, src + i * 8 + k * 4, (line >> 11) & 1);
            line += (uint32_t)dxt;
        }
        return;
    }
    int bytes = timg_siz == 0 ? 1 : 1 << (timg_siz - 1); /* 4-bit loads are made as 8 or 16-bit ones */
    uint32_t src = phys(timg_addr) + (uint32_t)((ult * timg_width + uls) * bytes);
    uint32_t words = ((uint32_t)(lrs - uls + 1) * bytes + 7) / 8;
    uint32_t line = 0; /* the line counter: dxt per word, the line is odd when bit 11 is set */
    for (uint32_t i = 0; i < words && tl->tmem * 8 + i * 8 < 4096; i++) {
        tmem_word(tl->tmem * 8 + i * 8, src + i * 8, (line >> 11) & 1);
        line += (uint32_t)dxt;
    }
}
static void load_tile(int tile, int uls, int ult, int lrs, int lrt) {
    Tile *tl = &tiles[tile];
    int bpp4 = timg_siz == 0, bytes = bpp4 ? 1 : 1 << (timg_siz - 1);
    if (timg_siz == 3) {
        for (int t = ult; t <= lrt; t++)
            for (int x = uls; x <= lrs; x++)
                tmem_texel32((uint32_t)(tl->tmem * 8 + (t - ult) * tl->line * 8 + (x - uls) * 2),
                             phys(timg_addr) + (uint32_t)((t * timg_width + x) * 4), (t - ult) & 1);
        return;
    }
    for (int t = ult; t <= lrt; t++) {
        uint32_t src = phys(timg_addr) + (uint32_t)(bpp4 ? (t * timg_width + uls) / 2 : (t * timg_width + uls) * bytes);
        uint32_t dst = (uint32_t)(tl->tmem * 8 + (t - ult) * tl->line * 8);
        int n = bpp4 ? (lrs - uls + 2) / 2 : (lrs - uls + 1) * bytes;
        for (int i = 0; i < n; i += 8) tmem_word(dst + (uint32_t)i, src + (uint32_t)i, (t - ult) & 1);
    }
}
static void load_tlut(int tile, int count) {
    int base = tiles[tile].tmem - 256;
    for (int i = 0; i < count; i++)
        if (base + i >= 0 && base + i < 256) tlut[base + i] = rd16(phys(timg_addr) + i * 2);
}

/* The RDP commands both microcodes send. */
static void rdp(uint8_t op, uint32_t w0, uint32_t w1) {
    switch (op) {
    case 0xFF: cimg_addr = w1 & 0x1FFFFFFF; cimg_width = (w0 & 0xFFF) + 1; if (cimg_width > MAXW) cimg_width = MAXW;
        cimg_siz = (int)(w0 >> 19) & 3; if (!cimg_siz) cimg_siz = 1;
        for (int i = 0; i < 8; i++) /* each colour image's width, for showing it later */
            if (!images[i][0] || images[i][0] == cimg_addr) { images[i][0] = cimg_addr; images[i][1] = (uint32_t)cimg_width; break; }
        break;
    case 0xEE: prim_z = (float)((w1 >> 16) & 0x7FFF) * 8.0f; prim_dz = (float)(w1 & 0xFFFF) * 8.0f; break; /* G_SETPRIMDEPTH */
    case 0xEC: k4 = (float)((int32_t)(w1 << 14) >> 23) / 255.0f; k5 = (float)((int32_t)(w1 << 23) >> 23) / 255.0f; break; /* G_SETCONVERT */
    case 0xEB: key_center.r = ((w1 >> 8) & 255) / 255.0f; key_scale.r = (w1 & 255) / 255.0f; break;   /* G_SETKEYR */
    case 0xEA: key_center.g = (w1 >> 24) / 255.0f; key_scale.g = ((w1 >> 16) & 255) / 255.0f;          /* G_SETKEYGB */
        key_center.b = ((w1 >> 8) & 255) / 255.0f; key_scale.b = (w1 & 255) / 255.0f; break;
    case 0xFE: zimg_addr = w1 & 0x1FFFFFFF; break;
    case 0xFD: timg_addr = w1; timg_siz = (w0 >> 19) & 3; timg_width = (w0 & 0xFFF) + 1; break;
    case 0xF7: fill_color = w1; break;
    case 0xFA: prim = unpack8888(w1); prim_min_level = (int)(w0 >> 8) & 0x1F; prim_lod_frac = (w0 & 0xFF) / 255.0f; break;
    case 0xFB: env = unpack8888(w1); break;
    case 0xF9: blend = unpack8888(w1); break;
    case 0xF8: fog = unpack8888(w1); break;
    case 0xFC: combine0 = w0 & 0xFFFFFF; combine1 = w1; break;
    case 0xED: scissor[0] = (int)((w0 >> 12) & 0xFFF) >> 2; scissor[1] = (int)(w0 & 0xFFF) >> 2;
               scissor[2] = (int)((w1 >> 12) & 0xFFF) >> 2; scissor[3] = (int)(w1 & 0xFFF) >> 2; break;
    case 0xF5: { Tile *t = &tiles[(w1 >> 24) & 7];
        t->fmt = (w0 >> 21) & 7; t->siz = (w0 >> 19) & 3; t->line = (w0 >> 9) & 0x1FF; t->tmem = w0 & 0x1FF;
        t->palette = (w1 >> 20) & 15; t->cmt = (w1 >> 18) & 3; t->maskt = (w1 >> 14) & 15; t->shiftt = (w1 >> 10) & 15;
        t->cms = (w1 >> 8) & 3; t->masks = (w1 >> 4) & 15; t->shifts = w1 & 15; break; }
    case 0xF2: { Tile *t = &tiles[(w1 >> 24) & 7];
        t->uls = ((w0 >> 12) & 0xFFF) / 4.0f; t->ult = (w0 & 0xFFF) / 4.0f;
        t->lrs = ((w1 >> 12) & 0xFFF) / 4.0f; t->lrt = (w1 & 0xFFF) / 4.0f; break; }
    case 0xF3: load_block((w1 >> 24) & 7, (w0 >> 12) & 0xFFF, w0 & 0xFFF, (w1 >> 12) & 0xFFF, w1 & 0xFFF); break;
    case 0xF4: { int tile = (w1 >> 24) & 7;
        load_tile(tile, ((w0 >> 12) & 0xFFF) >> 2, (w0 & 0xFFF) >> 2, ((w1 >> 12) & 0xFFF) >> 2, (w1 & 0xFFF) >> 2);
        Tile *t = &tiles[tile];
        t->uls = ((w0 >> 12) & 0xFFF) / 4.0f; t->ult = (w0 & 0xFFF) / 4.0f;
        t->lrs = ((w1 >> 12) & 0xFFF) / 4.0f; t->lrt = (w1 & 0xFFF) / 4.0f; break; }
    case 0xF0: load_tlut((w1 >> 24) & 7, (int)(((w1 >> 14) & 0x3FF) - ((w0 >> 14) & 0x3FF)) + 1); break;
    case 0xF6: fill_rect((int)((w1 >> 12) & 0xFFF) >> 2, (int)(w1 & 0xFFF) >> 2, (int)((w0 >> 12) & 0xFFF) >> 2, (int)(w0 & 0xFFF) >> 2); break;
    case 0xE2: case 0xE3: { /* set other mode, F3DEX2's form: length - 1 and 32 - shift - length */
        int len = (w0 & 0xFF) + 1, shift = 32 - (int)((w0 >> 8) & 0xFF) - len;
        if (shift < 0) shift = 0;
        uint32_t mask = (len >= 32 ? 0xFFFFFFFFu : ((1u << len) - 1)) << shift;
        if (op == 0xE2) mode_l = (mode_l & ~mask) | (w1 & mask); else mode_h = (mode_h & ~mask) | (w1 & mask);
        break; }
    case 0xEF: mode_h = w0 & 0xFFFFFF; mode_l = w1; break;
    }
}

/* ---------------------------------------------------------------- F3DEX2: vertices, matrices, triangles */

/* A vertex: clip space, screen space once projected, texture coordinates in texels, shade; `fixed_xy`/`fixed_z`
   when G_MODIFYVTX gave its screen place directly. */
typedef struct { float x, y, z, w, sx, sy, sz, s, t; Color shade; int clip, fixed_xy, fixed_z; float fx, fy, fz; } Vtx;
static Vtx vtx[64];
static float mv[10][16], proj[16], mvp[16];
static int mv_depth;
static float vp_scale[3] = {640, 480, 511}, vp_trans[3] = {640, 480, 511};
static uint32_t geometry;
static int tex_on, tex_tile, tex_levels; /* G_TEXTURE: on, its tile, and how many mip levels beyond the first */
static float tex_sscale = 1, tex_tscale = 1;
static Color lights[8];
static float light_dir[8][3];
static int num_lights = 1;
static float lookat[2][3] = {{1, 0, 0}, {0, 1, 0}}; /* G_TEXTURE_GEN's axes: X then Y */

/* A Light (or a LookAt entry, the same shape) from memory: its colour, then its direction at byte 8. `l` counts
   from LIGHT_1 at 0; -2 and -1 are the look-at X and Y. Every microcode's G_MOVEMEM puts LIGHT_1 after the two
   look-at entries: F3DEX2 at offset 48, GBI 1 at index G_MV_L0. Read from offset 24, as the renderer once did,
   every light landed one slot late: the loop's first light was the look-at, and the real light was taken for the
   ambient. */
static void set_light(int l, uint32_t a) {
    float x = (int8_t)R[a + 8], y = (int8_t)R[a + 9], z = (int8_t)R[a + 10], len = sqrtf(x * x + y * y + z * z);
    if (len == 0) len = 1;
    if (l == -2 || l == -1) { float *d = lookat[l + 2]; d[0] = x / len; d[1] = y / len; d[2] = z / len; return; }
    if (l < 0 || l >= 8) return;
    lights[l] = (Color){R[a] / 255.0f, R[a + 1] / 255.0f, R[a + 2] / 255.0f, 1};
    light_dir[l][0] = x / len; light_dir[l][1] = y / len; light_dir[l][2] = z / len;
}

static void matrix_read(uint32_t a, float *m) { /* s15.16: the integer halves, then the fractions */
    for (int i = 0; i < 16; i++) m[i] = (float)(int16_t)rd16(a + i * 2) + rd16(a + 32 + i * 2) / 65536.0f;
}
static void matrix_mul(const float *a, const float *b, float *out) { /* out = a * b, row vectors */
    float t[16];
    for (int i = 0; i < 4; i++)
        for (int j = 0; j < 4; j++)
            t[i * 4 + j] = a[i * 4] * b[j] + a[i * 4 + 1] * b[4 + j] + a[i * 4 + 2] * b[8 + j] + a[i * 4 + 3] * b[12 + j];
    memcpy(out, t, sizeof t);
}
static void update_mvp(void) { matrix_mul(mv[mv_depth], proj, mvp); }

static void load_vertices(uint32_t addr, int n, int v0) {
    for (int i = 0; i < n && v0 + i < 64; i++) {
        if (v0 + i < 0) continue;
        uint32_t a = addr + (uint32_t)i * 16;
        float x = (int16_t)rd16(a), y = (int16_t)rd16(a + 2), z = (int16_t)rd16(a + 4);
        Vtx *v = &vtx[v0 + i];
        v->x = x * mvp[0] + y * mvp[4] + z * mvp[8] + mvp[12];
        v->y = x * mvp[1] + y * mvp[5] + z * mvp[9] + mvp[13];
        v->z = x * mvp[2] + y * mvp[6] + z * mvp[10] + mvp[14];
        v->w = x * mvp[3] + y * mvp[7] + z * mvp[11] + mvp[15];
        v->s = (int16_t)rd16(a + 8) / 32.0f * tex_sscale;
        v->t = (int16_t)rd16(a + 10) / 32.0f * tex_tscale;
        uint8_t c0 = R[a + 12], c1 = R[a + 13], c2 = R[a + 14], c3 = R[a + 15];
        if (geometry & 0x20000) { /* lighting: the colour bytes are a normal */
            float nx = (int8_t)c0, ny = (int8_t)c1, nz = (int8_t)c2, *m = mv[mv_depth];
            float tx = nx * m[0] + ny * m[4] + nz * m[8], ty = nx * m[1] + ny * m[5] + nz * m[9];
            float tz = nx * m[2] + ny * m[6] + nz * m[10], len = sqrtf(tx * tx + ty * ty + tz * tz);
            if (len > 0) { tx /= len; ty /= len; tz /= len; }
            Color c = lights[num_lights < 8 ? num_lights : 7]; /* the ambient light comes after the directional ones */
            for (int l = 0; l < num_lights && l < 7; l++) {
                float d = tx * light_dir[l][0] + ty * light_dir[l][1] + tz * light_dir[l][2];
                if (d > 0) { c.r += lights[l].r * d; c.g += lights[l].g * d; c.b += lights[l].b * d; }
            }
            v->shade = (Color){c.r > 1 ? 1 : c.r, c.g > 1 ? 1 : c.g, c.b > 1 ? 1 : c.b, c3 / 255.0f};
            if (geometry & 0x40000) { /* G_TEXTURE_GEN: s and t from the normal along the look-at axes */
                float ds = tx * lookat[0][0] + ty * lookat[0][1] + tz * lookat[0][2];
                float dt = tx * lookat[1][0] + ty * lookat[1][1] + tz * lookat[1][2];
                if (geometry & 0x80000) { /* _LINEAR: by the angle, not its cosine */
                    ds = 1.0f - acosf(ds < -1 ? -1 : ds > 1 ? 1 : ds) / 3.14159265f;
                    dt = 1.0f - acosf(dt < -1 ? -1 : dt > 1 ? 1 : dt) / 3.14159265f;
                } else { ds = ds * 0.5f + 0.5f; dt = dt * 0.5f + 0.5f; }
                /* the RSP's 0 to 1 across 0x7FFF, by G_TEXTURE's scale: (scale >> 6) texels */
                v->s = ds * tex_sscale * 1024.0f;
                v->t = dt * tex_tscale * 1024.0f;
            }
        } else v->shade = (Color){c0 / 255.0f, c1 / 255.0f, c2 / 255.0f, c3 / 255.0f};
        if (geometry & 0x10000) { /* G_FOG: the shade's alpha becomes the fog's, from the vertex's depth */
            float f = (v->z / (v->w > 1e-4f ? v->w : 1e-4f)) * (float)fog_mul + (float)fog_off;
            v->shade.a = (f < 0 ? 0 : f > 255 ? 255 : f) / 255.0f;
        }
        v->clip = v->fixed_xy = v->fixed_z = 0;
    }
}

static void project(Vtx *v) {
    float iw = 1.0f / v->w;
    v->sx = vp_trans[0] / 4.0f + v->x * iw * vp_scale[0] / 4.0f;
    v->sy = vp_trans[1] / 4.0f - v->y * iw * vp_scale[1] / 4.0f;
    /* depth as the RDP takes it: the screen's 0 to 1023 (the viewport's), times 256: 18 bits */
    v->sz = (v->z * iw * vp_scale[2] + vp_trans[2]) * 256.0f;
    if (v->fixed_xy) { v->sx = v->fx; v->sy = v->fy; }
    if (v->fixed_z) v->sz = v->fz * 256.0f;
}

/* The texels a pixel sees. With G_TL_LOD (other mode H) the tile is picked by the level of detail, how many texels
   one pixel steps: level n is G_TEXTURE's tile + n, up to its level count, TEXEL1 the next level, and the LOD
   fraction how far between them (trilinear filtering through the combiner). Detail (G_TD_DETAIL) keeps the first
   tile for a fine texture blended in when magnified, the levels starting one tile later; sharpen (G_TD_SHARPEN)
   extrapolates past the first level. Without LOD, TEXEL0 is the tile and TEXEL1 the next one (two textures on one
   surface: scrolling water, flames, two-layer ground). */
static Texels texels_at(int tile, float s, float t, float lod) {
    Texels out = {{1, 1, 1, 1}, {1, 1, 1, 1}, 0};
    int t0 = tile, t1 = tile + 1;
    if (mode_h & 0x10000) { /* G_TL_LOD */
        int detail = (mode_h & 0x40000) != 0, sharpen = (mode_h & 0x20000) != 0, max_level = tex_levels;
        float min_lod = prim_min_level / 32.0f; /* the primitive's minimum level, u1.5 */
        if (lod < min_lod) lod = min_lod;
        if (lod < 1.0f) { /* magnified */
            out.lod = detail ? 1.0f - lod : sharpen ? lod - 1.0f : 0.0f;
            t0 = tile; t1 = tile + 1;
        } else {
            int level = 0;
            while (level < max_level && lod >= (float)(2 << level)) level++;
            if (level >= max_level) { level = max_level; out.lod = 1.0f; }
            else out.lod = lod / (float)(1 << level) - 1.0f;
            t0 = tile + level + detail; t1 = t0 + 1;
            if (t1 > tile + max_level + detail) t1 = t0;
        }
    }
    const Tile *a = &tiles[t0 & 7], *b = &tiles[t1 & 7];
    out.t0 = sample(a, shifted(s, a->shifts), shifted(t, a->shiftt));
    out.t1 = sample(b, shifted(s, b->shifts), shifted(t, b->shiftt));
    return out;
}

/* An edge's value at (px, py): the same two vertices give exactly opposite values in either order, so a pixel on an
   edge two triangles share is inside exactly one of them. */
static float edge_fn(const Vtx *p, const Vtx *q, float px, float py) {
    int swap = q->sy < p->sy || (q->sy == p->sy && q->sx < p->sx);
    const Vtx *u = swap ? q : p, *v = swap ? p : q;
    float e = (v->sx - u->sx) * (py - u->sy) - (v->sy - u->sy) * (px - u->sx);
    return swap ? -e : e;
}
/* Inside, by a weight `l` and its step (gx, gy) per pixel: a point exactly on the edge belongs to the triangle that
   edge is the top or left of (its inside below, or to the right). Every point on a quad's diagonal was drawn by both
   its triangles, so a see-through quad showed a bright seam along it. */
static int owns(float l, float gx, float gy) { return l > 0 || (l == 0 && (gx > 0 || (gx == 0 && gy > 0))); }

/* One triangle already in front of the eye, in screen space. */
static void raster(const Vtx *a, const Vtx *b, const Vtx *c) {
    float A0 = edge_fn(b, c, a->sx, a->sy), A1 = edge_fn(c, a, b->sx, b->sy), A2 = edge_fn(a, b, c->sx, c->sy);
    if (A0 == 0 || A1 == 0 || A2 == 0) return;
    int aa = (mode_l & 0x8) != 0 && cycle_type() < 2; /* anti-aliased: edge pixels by their coverage */
    int minx = (int)floorf(fminf(a->sx, fminf(b->sx, c->sx))), maxx = (int)ceilf(fmaxf(a->sx, fmaxf(b->sx, c->sx)));
    int miny = (int)floorf(fminf(a->sy, fminf(b->sy, c->sy))), maxy = (int)ceilf(fmaxf(a->sy, fmaxf(b->sy, c->sy)));
    if (aa) { minx--; miny--; maxx++; maxy++; }
    if (minx < scissor[0]) minx = scissor[0];
    if (miny < scissor[1]) miny = scissor[1];
    if (maxx > scissor[2]) maxx = scissor[2];
    if (maxy > scissor[3]) maxy = scissor[3];
    float area = (b->sx - a->sx) * (c->sy - a->sy) - (c->sx - a->sx) * (b->sy - a->sy);
    float wa = 1 / a->w, wb = 1 / b->w, wc = 1 / c->w, inv = 1 / area;
    /* the barycentric weights' steps per pixel, for the coverage samples, and the depth's, for "the same surface" */
    float d0x = -(c->sy - b->sy) * inv, d0y = (c->sx - b->sx) * inv, d1x = -(a->sy - c->sy) * inv, d1y = (a->sx - c->sx) * inv;
    float d2x = -(d0x + d1x), d2y = -(d0y + d1y);
    float dzx = d0x * a->sz + d1x * b->sz - (d0x + d1x) * c->sz, dzy = d0y * a->sz + d1y * b->sz - (d0y + d1y) * c->sz;
    float dz = fmaxf(fabsf(dzx), fabsf(dzy)) + 1e-6f;
    /* the RDP's 8 sample points in a pixel: four sub-scanlines, two staggered points each */
    static const float sx8[8] = {0.25f, 0.75f, 0.0f, 0.5f, 0.25f, 0.75f, 0.0f, 0.5f};
    static const float sy8[8] = {0.125f, 0.125f, 0.375f, 0.375f, 0.625f, 0.625f, 0.875f, 0.875f};
    /* how far each barycentric weight moves across half a pixel: a pixel at least that far inside every edge is
       covered whole, one that far outside any edge not at all; only the pixels between count their samples */
    float e0 = 0.5f * (fabsf(d0x) + fabsf(d0y)), e1 = 0.5f * (fabsf(d1x) + fabsf(d1y));
    float e2 = 0.5f * (fabsf(d0x + d1x) + fabsf(d0y + d1y));
    int smooth = (geometry & 0x200000) != 0, persp = (mode_h & 0x80000) != 0; /* G_TP_PERSP */
    int lod_on = (mode_h & 0x10000) != 0;
    for (int y = miny; y < maxy; y++)
        for (int x = minx; x < maxx; x++) {
            float px = x + 0.5f, py = y + 0.5f;
            float l0 = edge_fn(b, c, px, py) / A0, l1 = edge_fn(c, a, px, py) / A1, l2 = edge_fn(a, b, px, py) / A2;
            int cvg = 8;
            if (!aa) {
                if (!owns(l0, d0x, d0y) || !owns(l1, d1x, d1y) || !owns(l2, d2x, d2y)) continue;
            } else {
                if (l0 < -e0 || l1 < -e1 || l2 < -e2) continue; /* well outside */
                if (l0 < e0 || l1 < e1 || l2 < e2) { /* near an edge: count the samples */
                    cvg = 0;
                    for (int k = 0; k < 8; k++) {
                        float ox = sx8[k] - 0.5f, oy = sy8[k] - 0.5f;
                        float m0 = l0 + d0x * ox + d0y * oy, m1 = l1 + d1x * ox + d1y * oy, m2 = 1 - m0 - m1;
                        cvg += owns(m0, d0x, d0y) && owns(m1, d1x, d1y) && owns(m2, d2x, d2y);
                    }
                    if (!cvg) continue;
                }
            }
            float z = l0 * a->sz + l1 * b->sz + l2 * c->sz;
            Color sh = smooth ? (Color){l0 * a->shade.r + l1 * b->shade.r + l2 * c->shade.r,
                                        l0 * a->shade.g + l1 * b->shade.g + l2 * c->shade.g,
                                        l0 * a->shade.b + l1 * b->shade.b + l2 * c->shade.b,
                                        l0 * a->shade.a + l1 * b->shade.a + l2 * c->shade.a} : c->shade;
            Texels tx = one_texel((Color){1, 1, 1, 1});
            if (tex_on) {
                /* s and t at a point of the triangle by its weights; with perspective correction through 1/w.
                   Without it the RDP takes texture coordinates at half the scale it takes them with it: a racing
                   game's menu text (perspective off) gives each glyph twice the texel coordinates of its place in
                   its font page. This was once done for every 4-bit texture, which also halved the cars' 4-bit
                   skins (perspective on): each car showed the top-left quarter of its skin, stretched. */
                float st[3][2];
                for (int k = 0; k < (lod_on ? 3 : 1); k++) {
                    float m0 = l0 + (k == 1 ? d0x : k == 2 ? d0y : 0), m1 = l1 + (k == 1 ? d1x : k == 2 ? d1y : 0);
                    float m2 = 1 - m0 - m1, s, t;
                    if (persp) {
                        float q = m0 * wa + m1 * wb + m2 * wc;
                        s = (m0 * a->s * wa + m1 * b->s * wb + m2 * c->s * wc) / q;
                        t = (m0 * a->t * wa + m1 * b->t * wb + m2 * c->t * wc) / q;
                    } else {
                        s = (m0 * a->s + m1 * b->s + m2 * c->s) * 0.5f;
                        t = (m0 * a->t + m1 * b->t + m2 * c->t) * 0.5f;
                    }
                    st[k][0] = s; st[k][1] = t;
                }
                float lod = 0;
                if (lod_on) /* texels per pixel: the largest step across or down */
                    lod = fmaxf(fmaxf(fabsf(st[1][0] - st[0][0]), fabsf(st[1][1] - st[0][1])),
                                fmaxf(fabsf(st[2][0] - st[0][0]), fabsf(st[2][1] - st[0][1])));
                tx = texels_at(tex_tile, st[0][0], st[0][1], lod);
            }
            plot(x, y, z, sh, &tx, (geometry & 1) != 0, cvg, dz);
        }
}

static Vtx lerp(const Vtx *a, const Vtx *b, float f) {
    Vtx v;
    v.x = a->x + (b->x - a->x) * f; v.y = a->y + (b->y - a->y) * f; v.z = a->z + (b->z - a->z) * f;
    v.w = a->w + (b->w - a->w) * f; v.s = a->s + (b->s - a->s) * f; v.t = a->t + (b->t - a->t) * f;
    v.shade.r = a->shade.r + (b->shade.r - a->shade.r) * f; v.shade.g = a->shade.g + (b->shade.g - a->shade.g) * f;
    v.shade.b = a->shade.b + (b->shade.b - a->shade.b) * f; v.shade.a = a->shade.a + (b->shade.a - a->shade.a) * f;
    v.clip = v.fixed_xy = v.fixed_z = 0;
    return v;
}

/* A triangle: culled by its winding, clipped against the near plane (z = -w) in clip space, then drawn as a fan. */
static int debug_tris; /* PORT_TRIDEBUG: the state behind the first triangles of the traced tasks */
static void save_png(const char *path, const uint8_t *rgb, int w, int h);
static void dump_tile(const Tile *t) { /* PORT_TEXDUMP=folder: each traced triangle's texture as the sampler sees it */
    static int n;
    const char *dir = getenv("PORT_TEXDUMP");
    if (!dir || n >= 40) return;
    int w = (int)(t->lrs - t->uls) + 1, h = (int)(t->lrt - t->ult) + 1;
    if (w <= 0 || h <= 0 || w > 1024 || h > 1024) return;
    uint8_t *rgb = malloc((size_t)w * h * 3);
    for (int y = 0; y < h; y++)
        for (int x = 0; x < w; x++) {
            Color c = texel(t, (int)t->uls + x, (int)t->ult + y);
            uint8_t *p = rgb + ((size_t)y * w + x) * 3;
            p[0] = (uint8_t)(c.r * 255); p[1] = (uint8_t)(c.g * 255); p[2] = (uint8_t)(c.b * 255);
        }
    char path[1024];
    snprintf(path, sizeof path, "%s/tex_%02d_fmt%d_siz%d_%dx%d.png", dir, n++, t->fmt, t->siz, w, h);
    save_png(path, rgb, w, h);
    free(rgb);
}
static void triangle(int i0, int i1, int i2) {
    Vtx *in[3] = {&vtx[i0 & 63], &vtx[i1 & 63], &vtx[i2 & 63]};
    if (debug_tris > 0) {
        debug_tris--;
        Tile *t = &tiles[tex_tile];
        static Tile last;
        if (memcmp(&last, t, sizeof last)) { last = *t; dump_tile(t); }
        fprintf(stderr, "tri scale %.3f %.3f tex %d tile %d fmt %d siz %d line %d tmem %d pal %d size %.0f,%.0f-%.0f,%.0f shift %d,%d mask %d,%d cm %d,%d "
                "combine %06X %08X modes %08X %08X geom %08X prim %.2f,%.2f,%.2f,%.2f\n",
                tex_sscale, tex_tscale, tex_on, tex_tile, t->fmt, t->siz, t->line, t->tmem, t->palette, t->uls, t->ult, t->lrs, t->lrt,
                t->shifts, t->shiftt, t->masks, t->maskt, t->cms, t->cmt, combine0, combine1, mode_h, mode_l, geometry,
                prim.r, prim.g, prim.b, prim.a);
        for (int i = 0; i < 3; i++) fprintf(stderr, "   v clip %.1f %.1f %.1f %.1f st %.1f %.1f shade %.2f %.2f %.2f %.2f\n",
            in[i]->x, in[i]->y, in[i]->z, in[i]->w, in[i]->s, in[i]->t, in[i]->shade.r, in[i]->shade.g, in[i]->shade.b, in[i]->shade.a);
    }
    Vtx poly[8];
    int n = 0;
    for (int i = 0; i < 3; i++) {
        const Vtx *a = in[i], *b = in[(i + 1) % 3];
        float da = a->z + a->w, db = b->z + b->w;
        if (da >= 0) poly[n++] = *a;
        if ((da >= 0) != (db >= 0)) poly[n++] = lerp(a, b, da / (da - db));
    }
    if (n < 3) return;
    for (int i = 0; i < n; i++) { if (poly[i].w < 1e-4f) poly[i].w = 1e-4f; project(&poly[i]); }
    float area = 0;
    for (int i = 0; i < n; i++) {
        const Vtx *a = &poly[i], *b = &poly[(i + 1) % n];
        area += a->sx * b->sy - b->sx * a->sy;
    }
    if ((geometry & 0x400) && area > 0) return; /* cull back */
    if ((geometry & 0x200) && area < 0) return; /* cull front */
    for (int i = 1; i + 1 < n; i++) raster(&poly[0], &poly[i], &poly[i + 1]);
}

/* ---------------------------------------------------------------- what every microcode shares */

/* The microcode a task (or G_LOAD_UCODE) runs, from the signature in its data: "RSP Gfx ucode F3DEX  fifo 2.05" is
   GBI 2 (F3DEX2, F3DZEX, F3DLX2...), a 1.x version is GBI 1 (F3DEX, F3DLX, F3DLP); "S2DEX" the 2D microcode of
   either generation; "RSP SW Version" is Fast3D. Without a signature: F3DEX2, as before microcodes were told apart. */
enum { UC_F3DEX2, UC_S2DEX2, UC_F3D, UC_F3DEX, UC_S2DEX };
static int ucode;
static const char *const ucode_names[] = {"F3DEX2", "S2DEX2", "Fast3D", "F3DEX", "S2DEX"};
static uint32_t half1; /* G_RDPHALF_1's word: G_BRANCH_Z's list, G_LOAD_UCODE's data */
static int identify_ucode(uint32_t data) { /* data: a physical address */
    const uint8_t *d = R + 0x80000000u + (data & 0x1FFFFFFF);
    if ((data & 0x1FFFFFFF) + 0x800 > 0x800000) return UC_F3DEX2;
    for (int i = 0; i + 40 < 0x800; i++) {
        if (d[i] == 'R' && !memcmp(d + i, "RSP SW Version", 14)) return UC_F3D;
        if (d[i] != 'R' || memcmp(d + i, "RSP Gfx ucode ", 14)) continue;
        const uint8_t *p = d + i + 14;
        int two_d = !memcmp(p, "S2DEX", 5);
        while (*p && !(p[0] >= '0' && p[0] <= '9' && p[1] == '.')) p++; /* the version: after the name and "fifo" */
        if (*p != '1') return two_d ? UC_S2DEX2 : UC_F3DEX2;
        return two_d ? UC_S2DEX : UC_F3DEX;
    }
    return UC_F3DEX2;
}
static int tracing;
static void load_ucode(void) { /* G_LOAD_UCODE: the data's address came in G_RDPHALF_1 */
    ucode = identify_ucode(half1);
    if (tracing) fprintf(stderr, "  ucode -> %s\n", ucode_names[ucode]);
}

static void modify_vertex(int n, int where, uint32_t w1) { /* G_MODIFYVTX, and GBI 1's G_MW_POINTS */
    /* A racing game sets every road polygon's colours and texture coordinates this way: without it its roads were
       drawn with one texel each */
    if (n < 0 || n >= 64) return;
    Vtx *v = &vtx[n];
    if (where == 0x10) v->shade = unpack8888(w1);
    else if (where == 0x14) { v->s = (int16_t)(w1 >> 16) / 32.0f; v->t = (int16_t)w1 / 32.0f; }
    else if (where == 0x18) { v->fixed_xy = 1; v->fx = (int16_t)(w1 >> 16) / 4.0f; v->fy = (int16_t)w1 / 4.0f; }
    else if (where == 0x1C) { v->fixed_z = 1; v->fz = (float)w1 / 65536.0f; }
}

/* G_MW_MATRIX: one pair of the combined matrix's 16-bit halves rewritten, as the RSP keeps it (s15.16). */
static void matrix_word(int offset, uint32_t w1) {
    int frac = offset >= 32, at = (offset & 31) / 2; /* the first of the two elements the word holds */
    for (int k = 0; k < 2 && at + k < 16; k++) {
        float v = mvp[at + k];
        int32_t fixed = (int32_t)floorf(v * 65536.0f);
        uint16_t half = (uint16_t)(k == 0 ? w1 >> 16 : w1);
        fixed = frac ? (int32_t)(((uint32_t)fixed & 0xFFFF0000u) | half) : (int32_t)((uint32_t)half << 16 | ((uint32_t)fixed & 0xFFFF));
        mvp[at + k] = (float)fixed / 65536.0f;
    }
}
/* GBI 1's G_MV_MATRIX_1..4: the combined matrix loaded whole, a quarter at a time (gSPForceMatrix). */
static uint8_t forced[64];
static void force_matrix(int quarter, uint32_t a) {
    memcpy(forced + quarter * 16, R + a, 16);
    if (quarter != 3) return;
    for (int i = 0; i < 16; i++) {
        int16_t hi = (int16_t)(forced[i * 2] << 8 | forced[i * 2 + 1]);
        uint16_t lo = (uint16_t)(forced[32 + i * 2] << 8 | forced[33 + i * 2]);
        mvp[i] = (float)hi + lo / 65536.0f;
    }
}

/* G_MOVEWORD, both generations: `light_step` is how far apart G_MW_LIGHTCOL's lights are (F3DEX2 24, GBI 1 32). */
static void moveword(int index, uint32_t offset, uint32_t w1, int light_step) {
    switch (index) {
    case 0: matrix_word((int)offset, w1); break;                                       /* G_MW_MATRIX */
    case 2: num_lights = light_step == 24 ? (int)(w1 / 24) : (int)((w1 & 0x7FFFFFFF) / 32) - 1; /* G_MW_NUMLIGHT */
        if (num_lights < 0) num_lights = 0;
        break;
    case 8: fog_mul = (int16_t)(w1 >> 16); fog_off = (int16_t)(w1 & 0xFFFF); break;   /* G_MW_FOG */
    case 10: if (offset % (uint32_t)light_step == 0 && offset / (uint32_t)light_step < 8) /* G_MW_LIGHTCOL */
            lights[offset / (uint32_t)light_step] = (Color){(w1 >> 24) / 255.0f, ((w1 >> 16) & 255) / 255.0f,
                                                              ((w1 >> 8) & 255) / 255.0f, 1};
        break;
    case 12: modify_vertex((int)offset / 40, (int)offset % 40, w1); break;             /* G_MW_POINTS */
    }
}

/* G_BRANCH_Z (gSPBranchLessZ): the list in G_RDPHALF_1 replaces the rest of this one when the vertex is nearer than
   zval (the screen depth, 0 to 1023, in 16.16). A model's detail by distance: the near model is drawn when it's near,
   and the far one otherwise; always taking the far one drew every model at its lowest detail. */
static uint32_t branch_to;
static void branch_z(int n, uint32_t zval) {
    if (n < 0 || n >= 64) return;
    const Vtx *v = &vtx[n];
    float w = v->w > 1e-4f ? v->w : 1e-4f, z = v->z / w * vp_scale[2] + vp_trans[2];
    if (z * 65536.0f <= (float)zval || z > 1023.0f) branch_to = half1 ? half1 : 1;
}

static void f3dex2(uint8_t op, uint32_t w0, uint32_t w1) {
    switch (op) {
    case 0x01: { int n = (w0 >> 12) & 0xFF, end = (w0 >> 1) & 0x7F; load_vertices(phys(w1), n, end - n); break; }
    case 0x05: triangle(((w0 >> 16) & 0xFF) / 2, ((w0 >> 8) & 0xFF) / 2, (w0 & 0xFF) / 2); break;
    case 0x02: modify_vertex((int)(w0 & 0xFFFF) / 2, (int)(w0 >> 16) & 0xFF, w1); break; /* G_MODIFYVTX */
    case 0x06: case 0x07:
        triangle(((w0 >> 16) & 0xFF) / 2, ((w0 >> 8) & 0xFF) / 2, (w0 & 0xFF) / 2);
        triangle(((w1 >> 16) & 0xFF) / 2, ((w1 >> 8) & 0xFF) / 2, (w1 & 0xFF) / 2); break;
    case 0xDA: { /* G_MTX: F3DEX2 inverts the push bit */
        int p = (int)(w0 & 0xFF) ^ 1;
        float m[16];
        matrix_read(phys(w1), m);
        if (p & 4) { if (p & 2) memcpy(proj, m, sizeof m); else matrix_mul(m, proj, proj); }
        else {
            if ((p & 1) && mv_depth < 9) { memcpy(mv[mv_depth + 1], mv[mv_depth], sizeof m); mv_depth++; }
            if (p & 2) memcpy(mv[mv_depth], m, sizeof m); else matrix_mul(m, mv[mv_depth], mv[mv_depth]);
        }
        update_mvp(); break; }
    case 0xD8: { int n = (int)(w1 / 64); mv_depth = mv_depth > n ? mv_depth - n : 0; update_mvp(); break; }
    case 0xD9: geometry = (geometry & (w0 & 0xFFFFFF)) | w1; break;
    case 0xD7:
        tex_on = (w0 & 0xFF) != 0; tex_tile = (w0 >> 8) & 7; tex_levels = (w0 >> 11) & 7;
        tex_sscale = (w1 >> 16) == 0xFFFF ? 1.0f : (w1 >> 16) / 65536.0f;
        tex_tscale = (w1 & 0xFFFF) == 0xFFFF ? 1.0f : (w1 & 0xFFFF) / 65536.0f; break;
    case 0xDC: { int index = w0 & 0xFF, offset = (int)((w0 >> 8) & 0xFF) * 8; uint32_t a = phys(w1);
        if (index == 8)
            for (int i = 0; i < 3; i++) { vp_scale[i] = (int16_t)rd16(a + i * 2); vp_trans[i] = (int16_t)rd16(a + 8 + i * 2); }
        else if (index == 10) set_light(offset / 24 - 2, a); /* the look-at X and Y at 0 and 24, LIGHT_1 at 48 */
        break; }
    case 0xDB: moveword((w0 >> 16) & 0xFF, w0 & 0xFFFF, w1, 24); break;
    case 0x04: branch_z((int)(w0 & 0xFFF) / 2, w1); break;   /* G_BRANCH_Z: the far model only when it's far */
    case 0xE1: half1 = w1; break;                              /* G_RDPHALF_1 */
    case 0xDD: load_ucode(); break;                            /* G_LOAD_UCODE */
    case 0x00: case 0x03: case 0xE0: case 0xD3: case 0xD6: break; /* no-ops, G_CULLDL (never culls), DMA/special */
    default: rdp(op, w0, w1);
    }
}

/* GBI 1: Fast3D (Super Mario 64's generation, "RSP SW Version") and F3DEX 1.x with its lighter F3DLX and F3DLP.
   The same RDP commands, the same vertices and lights, other opcodes and encodings: matrices and lights through
   G_MOVEMEM's index, vertex and triangle indices counted in other units (Fast3D's triangles by ten), other-mode
   commands by shift and length, and its own geometry mode bits (translated here to F3DEX2's, which the rest of the
   renderer reads). Decoded as F3DEX2, such a list drew nothing right. */
static uint32_t gbi1_geometry(uint32_t g) {
    static const uint32_t from[] = {0x1, 0x4, 0x200, 0x1000, 0x2000, 0x10000, 0x20000, 0x40000, 0x80000};
    static const uint32_t to[] = {0x1, 0x4, 0x200000, 0x200, 0x400, 0x10000, 0x20000, 0x40000, 0x80000};
    uint32_t out = 0;
    for (int i = 0; i < 9; i++) if (g & from[i]) out |= to[i];
    return out;
}
static void gbi1(uint8_t op, uint32_t w0, uint32_t w1) {
    int fast3d = ucode == UC_F3D, scale = fast3d ? 10 : 2;
    switch (op) {
    case 0x01: { /* G_MTX: projection 1, load 2, push 4 */
        int p = (int)(w0 >> 16) & 0xFF;
        float m[16];
        matrix_read(phys(w1), m);
        if (p & 1) { if (p & 2) memcpy(proj, m, sizeof m); else matrix_mul(m, proj, proj); }
        else {
            if ((p & 4) && mv_depth < 9) { memcpy(mv[mv_depth + 1], mv[mv_depth], sizeof m); mv_depth++; }
            if (p & 2) memcpy(mv[mv_depth], m, sizeof m); else matrix_mul(m, mv[mv_depth], mv[mv_depth]);
        }
        update_mvp(); break; }
    case 0x03: { int index = (w0 >> 16) & 0xFF; uint32_t a = phys(w1); /* G_MOVEMEM by index */
        if (index == 0x80)
            for (int i = 0; i < 3; i++) { vp_scale[i] = (int16_t)rd16(a + i * 2); vp_trans[i] = (int16_t)rd16(a + 8 + i * 2); }
        else if (index >= 0x82 && index <= 0x94) set_light(index == 0x82 ? -1 : index == 0x84 ? -2 : (index - 0x86) / 2, a);
        else if (index >= 0x9E && index <= 0xA4) force_matrix((index - 0x9E) / 2, a);
        break; }
    case 0x04: /* G_VTX: Fast3D's count - 1 and first index in nibbles; F3DEX's first index doubled, count in 6 bits */
        if (fast3d) load_vertices(phys(w1), (int)((w0 >> 20) & 15) + 1, (int)(w0 >> 16) & 15);
        else load_vertices(phys(w1), (int)(w0 >> 10) & 0x3F, (int)((w0 >> 16) & 0xFF) / 2);
        break;
    case 0xBF: triangle((int)((w1 >> 16) & 0xFF) / scale, (int)((w1 >> 8) & 0xFF) / scale, (int)(w1 & 0xFF) / scale); break;
    case 0xB1: if (!fast3d) { /* G_TRI2 */
            triangle((int)((w0 >> 16) & 0xFF) / 2, (int)((w0 >> 8) & 0xFF) / 2, (int)(w0 & 0xFF) / 2);
            triangle((int)((w1 >> 16) & 0xFF) / 2, (int)((w1 >> 8) & 0xFF) / 2, (int)(w1 & 0xFF) / 2);
        } break;
    case 0xB5: if (!fast3d) { /* F3DEX's G_QUAD (Fast3D's G_LINE3D: lines aren't drawn) */
            int v0 = (int)(w1 >> 24) / 2, v1 = (int)((w1 >> 16) & 0xFF) / 2, v2 = (int)((w1 >> 8) & 0xFF) / 2, v3 = (int)(w1 & 0xFF) / 2;
            triangle(v0, v1, v2); triangle(v0, v2, v3);
        } break;
    case 0xB2: if (!fast3d) modify_vertex((int)(w0 & 0xFFFF) / 2, (int)(w0 >> 16) & 0xFF, w1); break; /* G_MODIFYVTX */
    case 0xB0: if (!fast3d) branch_z((int)(w0 & 0xFFF) / 2, w1); break;   /* G_BRANCH_Z */
    case 0xBD: mv_depth = mv_depth > 0 ? mv_depth - 1 : 0; update_mvp(); break; /* G_POPMTX */
    case 0xBC: moveword((int)(w0 & 0xFF), (w0 >> 8) & 0xFFFF, w1, 32); break;
    case 0xBB: /* G_TEXTURE */
        tex_on = (w0 & 0xFF) != 0; tex_tile = (w0 >> 8) & 7; tex_levels = (w0 >> 11) & 7;
        tex_sscale = (w1 >> 16) == 0xFFFF ? 1.0f : (w1 >> 16) / 65536.0f;
        tex_tscale = (w1 & 0xFFFF) == 0xFFFF ? 1.0f : (w1 & 0xFFFF) / 65536.0f; break;
    case 0xBA: case 0xB9: { /* G_SETOTHERMODE_H / _L: shift and length */
        int shift = (int)(w0 >> 8) & 0xFF, len = (int)w0 & 0xFF;
        uint32_t mask = (len >= 32 ? 0xFFFFFFFFu : ((1u << len) - 1)) << (shift & 31);
        if (op == 0xB9) mode_l = (mode_l & ~mask) | (w1 & mask); else mode_h = (mode_h & ~mask) | (w1 & mask);
        break; }
    case 0xB7: geometry |= gbi1_geometry(w1); break;
    case 0xB6: geometry &= ~gbi1_geometry(w1); break;
    case 0xB4: half1 = w1; break;          /* G_RDPHALF_1 */
    case 0xAF: load_ucode(); break;         /* G_LOAD_UCODE */
    case 0x00: case 0xBE: case 0xB3: case 0xB8: case 0x06: break; /* no-op, G_CULLDL, halves; lists in the task loop */
    default: if (op >= 0xC0) rdp(op, w0, w1);
    }
}

/* ---------------------------------------------------------------- S2DEX: backgrounds and sprites */

static void s2d_load(uint32_t txtr) { /* uObjTxtr: a block, a tile or a palette into TMEM */
    uint32_t type = rd32(txtr), src = phys(rd32(txtr + 4));
    int tm = rd16(txtr + 8), b = rd16(txtr + 10), c = rd16(txtr + 12);
    if (type == 0x00000030) { /* a palette: its first entry (phead) and its count minus one (pnum) */
        for (int i = 0; i <= b; i++) if (tm - 256 + i >= 0 && tm - 256 + i < 256) tlut[tm - 256 + i] = rd16(src + i * 2);
    } else if (type == 0x00001033) { /* a block: tsize words minus one, tline the line step (dxt) */
        uint32_t line = 0;
        for (int i = 0; i <= b && tm * 8 + i * 8 < 4096; i++) { tmem_word((uint32_t)(tm + i) * 8, src + (uint32_t)i * 8, (line >> 11) & 1); line += (uint32_t)c; }
    } else { /* a tile: its width in 8-byte words times four, minus one, and its height in lines times four, minus one */
        int words = (b + 1) >> 2, lines = (c + 1) >> 2;
        for (int l = 0; l < lines; l++)
            for (int i = 0; i < words && (tm + l * words + i) * 8 < 4096; i++)
                tmem_word((uint32_t)(tm + l * words + i) * 8, src + (uint32_t)(l * words + i) * 8, l & 1);
    }
}
/* A uObjSprite's place (objX, objY, s10.2), scale (u5.10) and image size in texels, its image set up as tile 7. */
typedef struct { float x, y, sw, sh; int iw, ih; } Sprite;
static Sprite s2d_sprite_tile(uint32_t sp) {
    Sprite o = {(int16_t)rd16(sp) / 4.0f, (int16_t)rd16(sp + 8) / 4.0f, rd16(sp + 2) / 1024.0f, rd16(sp + 10) / 1024.0f,
                rd16(sp + 4) >> 5, rd16(sp + 12) >> 5};
    Tile t = {.fmt = R[sp + 20], .siz = R[sp + 21], .line = rd16(sp + 16), .tmem = rd16(sp + 18), .palette = R[sp + 22],
              .cms = 2, .cmt = 2, .lrs = (float)(o.iw - 1), .lrt = (float)(o.ih - 1)};
    if (o.sw == 0) o.sw = 1;
    if (o.sh == 0) o.sh = 1;
    tiles[7] = t;
    return o;
}
static void s2d_sprite(uint32_t sp) { /* an unrotated rectangle from TMEM */
    Sprite o = s2d_sprite_tile(sp);
    tex_rect(7, o.x, o.y, o.x + o.iw / o.sw, o.y + o.ih / o.sh, 0, 0, 1.0f / o.sw, 1.0f / o.sh, 0);
}
/* The 2D matrix (uObjMtx, G_OBJ_MOVEMEM at index 0, 24 bytes): A, B, C, D (s15.16) and X, Y (s10.2). */
static float obj_mtx[6] = {1, 0, 0, 1, 0, 0};
static void s2d_matrix_load(uint32_t a, int sub) { /* the whole uObjMtx, or its uObjSubMtx (X, Y and the scales) */
    if (!sub) {
        for (int i = 0; i < 4; i++) obj_mtx[i] = (float)(int32_t)rd32(a + (uint32_t)i * 4) / 65536.0f;
        a += 16;
    }
    obj_mtx[4] = (int16_t)rd16(a) / 4.0f;
    obj_mtx[5] = (int16_t)rd16(a + 2) / 4.0f;
}
static void s2d_matrix(uint32_t w0, uint32_t a) { /* S2DEX2's G_OBJ_MOVEMEM: index 0 the matrix, 2 the sub-matrix */
    if ((w0 & 0xFF) == 0 && ((w0 >> 19) & 0x1F) == 2) s2d_matrix_load(a, 0);
    else if ((w0 & 0xFF) == 2) s2d_matrix_load(a, 1);
}
/* G_OBJ_SPRITE: the sprite through the 2D matrix, rotated and scaled: its corners in object space are mapped to
   the screen as (A x + B y + X, C x + D y + Y), and each pixel they cover back to the texel it shows. A racing
   game's rev counter needle turns this way; drawn unrotated, it was a grey streak above the gauge. */
static void s2d_sprite_rotated(uint32_t sp) {
    Sprite o = s2d_sprite_tile(sp);
    float A = obj_mtx[0], B = obj_mtx[1], C = obj_mtx[2], D = obj_mtx[3], X = obj_mtx[4], Y = obj_mtx[5];
    float det = A * D - B * C, w = o.iw / o.sw, h = o.ih / o.sh;
    if (fabsf(det) < 1e-6f || w <= 0 || h <= 0) return;
    float cx[4] = {o.x, o.x + w, o.x, o.x + w}, cy[4] = {o.y, o.y, o.y + h, o.y + h};
    float x0 = 1e9f, y0 = 1e9f, x1 = -1e9f, y1 = -1e9f;
    for (int i = 0; i < 4; i++) {
        float sx = A * cx[i] + B * cy[i] + X, sy = C * cx[i] + D * cy[i] + Y;
        x0 = fminf(x0, sx); x1 = fmaxf(x1, sx); y0 = fminf(y0, sy); y1 = fmaxf(y1, sy);
    }
    for (int py = (int)floorf(y0); py < (int)ceilf(y1); py++)
        for (int px = (int)floorf(x0); px < (int)ceilf(x1); px++) {
            float dx = px + 0.5f - X, dy = py + 0.5f - Y;
            float ox = (D * dx - B * dy) / det, oy = (-C * dx + A * dy) / det;
            if (ox < o.x || oy < o.y || ox >= o.x + w || oy >= o.y + h) continue;
            Texels tx = one_texel(sample(&tiles[7], (ox - o.x) * o.sw, (oy - o.y) * o.sh));
            plot(px, py, 0, (Color){1, 1, 1, 1}, &tx, 0, 8, 0);
        }
}
static void s2d_bg(uint32_t bg, int scaled) { /* uObjBg: a background straight from memory */
    float ix = rd16(bg) / 32.0f, iw = rd16(bg + 2) / 4.0f, fx = (int16_t)rd16(bg + 4) / 4.0f, fw = rd16(bg + 6) / 4.0f;
    float iy = rd16(bg + 8) / 32.0f, ih = rd16(bg + 10) / 4.0f, fy = (int16_t)rd16(bg + 12) / 4.0f, fh = rd16(bg + 14) / 4.0f;
    uint32_t image = phys(rd32(bg + 16));
    int fmt = R[bg + 22], siz = R[bg + 23], pal = rd16(bg + 24), width = (int)iw, height = (int)ih;
    float scw = scaled ? rd16(bg + 28) / 1024.0f : 1, sch = scaled ? rd16(bg + 30) / 1024.0f : 1;
    if (scw == 0) scw = 1;
    if (sch == 0) sch = 1;
    if (width <= 0 || height <= 0) return;
    for (int y = 0; y < (int)fh; y++)
        for (int x = 0; x < (int)fw; x++) {
            int s = (int)(ix + x * scw) % width, t = (int)(iy + y * sch) % height;
            Color c;
            switch (siz) {
            case 0: { uint8_t b = R[image + (uint32_t)(t * width + s) / 2]; int v = (s & 1) ? b & 15 : b >> 4;
                c = fmt == 2 ? unpack5551(tlut[(pal * 16 + v) & 255]) : (Color){v / 15.0f, v / 15.0f, v / 15.0f, 1}; break; }
            case 1: { uint8_t v = R[image + (uint32_t)(t * width + s)];
                c = fmt == 2 ? unpack5551(tlut[v]) : (Color){v / 255.0f, v / 255.0f, v / 255.0f, 1}; break; }
            case 2: c = unpack5551(rd16(image + (uint32_t)(t * width + s) * 2)); break;
            default: { uint32_t a = image + (uint32_t)(t * width + s) * 4;
                c = (Color){R[a] / 255.0f, R[a + 1] / 255.0f, R[a + 2] / 255.0f, R[a + 3] / 255.0f}; }
            }
            int px = (int)fx + x, py = (int)fy + y;
            if (cycle_type() == 2) {
                if (in_frame(px, py) && c.a > 0) { uint32_t a = pixel_addr(px, py); write_pixel(px, py, a, c, 0); cvg_set(a, 8); }
            } else { Texels tx = one_texel(c); plot(px, py, 0, (Color){1, 1, 1, 1}, &tx, 0, 8, 0); }
        }
}
/* S2DEX 1.x (GBI 1) numbers its object commands differently: each mapped to S2DEX2's; the rest are GBI 1's. */
static int s2dex1_op(uint8_t op) {
    switch (op) {
    case 0x01: return 0x09; case 0x02: return 0x0A; case 0x03: return 0x01; case 0x04: return 0x02;
    case 0x05: return 0xDC; case 0xB0: return 0x04; case 0xB1: return 0x0B; case 0xB2: return 0xDA;
    case 0xC1: return 0x05; case 0xC2: return 0x06; case 0xC3: return 0x07; case 0xC4: return 0x08;
    }
    return -1;
}
static void s2dex(uint8_t op, uint32_t w0, uint32_t w1) {
    if (ucode == UC_S2DEX) {
        int mapped = s2dex1_op(op);
        if (mapped < 0) { gbi1(op, w0, w1); return; }
        if (mapped == 0xDC) { /* gSPObjMatrix (24 bytes) or gSPObjSubMatrix (8, from X), by the length in w0 */
            s2d_matrix_load(phys(w1), ((w0 >> 16) & 0xFF) == 7 ? 1 : 0);
            return;
        }
        op = (uint8_t)mapped;
    }
    switch (op) {
    case 0x02: s2d_sprite_rotated(phys(w1)); break;
    case 0x01: case 0xDA: s2d_sprite(phys(w1)); break; /* the rectangles: no rotation (their scale is the sprite's) */
    case 0x05: s2d_load(phys(w1)); break;
    case 0x06: s2d_load(phys(w1)); s2d_sprite_rotated(phys(w1) + 24); break;
    case 0x07: case 0x08: s2d_load(phys(w1)); s2d_sprite(phys(w1) + 24); break;
    case 0xDC: s2d_matrix(w0, phys(w1)); break;
    case 0x09: s2d_bg(phys(w1), 1); break;
    case 0x0A: s2d_bg(phys(w1), 0); break;
    case 0xE1: half1 = w1; break;
    case 0xDD: load_ucode(); break;
    case 0x0B: case 0x04: case 0xD7: case 0xD9: case 0x00: break;
    default: rdp(op, w0, w1);
    }
}

/* ---------------------------------------------------------------- tasks */

static unsigned long render_tasks;

void port_render_task(uint32_t task) {
    R = port_memory();
    if (!render_tasks) for (int i = 0; i < MAXW * MAXH; i++) zbuf_private[i] = 0x3FFFF; /* the farthest depth */
    render_tasks++;
    ucode = identify_ucode(rd32(task + 24)); /* the microcode's data names it */
    static long trace = -1;
    if (trace < 0) trace = getenv("PORT_DLTRACE") ? atol(getenv("PORT_DLTRACE")) : 0;
    tracing = trace > 0 && (long)render_tasks >= trace && (long)render_tasks < trace + 6;
    if (tracing) fprintf(stderr, "== task %lu, %s\n", render_tasks, ucode_names[ucode]);
    if (tracing && getenv("PORT_TRIDEBUG")) debug_tris = atoi(getenv("PORT_TRIDEBUG"));
    if (tracing && getenv("PORT_DUMPAT")) { /* words at physical addresses, comma-separated, for debugging */
        char *list = strdup(getenv("PORT_DUMPAT")), *tok = strtok(list, ",");
        for (; tok; tok = strtok(NULL, ",")) {
            uint32_t a = 0x80000000u | (uint32_t)strtoul(tok, NULL, 16);
            fprintf(stderr, "  at %08X:", a);
            for (int i = 0; i < 64; i++) fprintf(stderr, "%s%08X", i % 4 ? " " : "\n   ", rd32(a + i * 4));
            fputc('\n', stderr);
        }
        free(list);
    }
    uint32_t stack[18];
    int depth = 0;
    uint32_t pc = phys(rd32(task + 48));
    for (long steps = 0; steps < 2000000; steps++) {
        uint32_t w0 = rd32(pc), w1 = rd32(pc + 4);
        uint8_t op = w0 >> 24;
        if (tracing) fprintf(stderr, "%*s%08X %08X %08X\n", depth * 2, "", pc, w0, w1);
        pc += 8;
        int one = ucode >= UC_F3D; /* GBI 1: G_DL 0x06, G_ENDDL 0xB8, G_MOVEWORD 0xBC */
        uint8_t op_dl = one ? 0x06 : 0xDE, op_end = one ? 0xB8 : 0xDF;
        if (!one && op == 0xDB && ((w0 >> 16) & 0xFF) == 0x06) segments[((w0 & 0xFFFF) / 4) & 15] = w1 & 0x1FFFFFFF;
        else if (one && op == 0xBC && (w0 & 0xFF) == 0x06) segments[(((w0 >> 8) & 0xFFFF) / 4) & 15] = w1 & 0x1FFFFFFF;
        else if (op == op_dl) { /* G_DL: a call, or a branch when the no-push bit is set */
            if ((phys(w1) & 0x1FFFFFFF) < 0x400) { /* the boot's vectors, never a display list: a null pointer */
                static int said;
                if (!said++) fprintf(stderr, "render: a display list calls address %08X (a null pointer); skipped\n", w1);
                if ((w0 >> 16) & 1) { if (!depth) break; pc = stack[--depth]; }
                continue;
            }
            if (!((w0 >> 16) & 1) && depth < 18) stack[depth++] = pc;
            pc = phys(w1);
        } else if (op == op_end) { /* G_ENDDL */
            if (!depth) break;
            pc = stack[--depth];
        } else if (op == 0xE4 || op == 0xE5) { /* a textured rectangle: two more commands carry s, t and steps */
            uint32_t h1 = rd32(pc + 4), h2 = rd32(pc + 12);
            pc += 16;
            float xh = ((w0 >> 12) & 0xFFF) / 4.0f, yh = (w0 & 0xFFF) / 4.0f;
            float xl = ((w1 >> 12) & 0xFFF) / 4.0f, yl = (w1 & 0xFFF) / 4.0f;
            tex_rect((w1 >> 24) & 7, xl, yl, xh, yh, (int16_t)(h1 >> 16) / 32.0f, (int16_t)h1 / 32.0f,
                     (int16_t)(h2 >> 16) / 1024.0f, (int16_t)h2 / 1024.0f, op == 0xE5);
        } else if (ucode == UC_S2DEX || ucode == UC_S2DEX2) s2dex(op, w0, w1);
        else if (one) gbi1(op, w0, w1);
        else f3dex2(op, w0, w1);
        if (branch_to) { pc = phys(branch_to); branch_to = 0; } /* G_BRANCH_Z taken: the rest of the list replaced */
    }
}

/* ---------------------------------------------------------------- frames saved as PNG */

static uint32_t crc_table[256];
static uint32_t crc(uint32_t c, const uint8_t *p, size_t n) {
    if (!crc_table[1])
        for (uint32_t i = 0; i < 256; i++) {
            uint32_t v = i;
            for (int k = 0; k < 8; k++) v = v & 1 ? 0xEDB88320u ^ (v >> 1) : v >> 1;
            crc_table[i] = v;
        }
    c = ~c;
    while (n--) c = crc_table[(c ^ *p++) & 255] ^ (c >> 8);
    return ~c;
}
static void chunk(FILE *f, const char *type, const uint8_t *data, uint32_t n) {
    uint8_t len[4] = {n >> 24, n >> 16, n >> 8, n};
    uint32_t c = crc(crc(0, (const uint8_t *)type, 4), data, n);
    uint8_t cb[4] = {c >> 24, c >> 16, c >> 8, c};
    fwrite(len, 1, 4, f); fwrite(type, 1, 4, f);
    if (n) fwrite(data, 1, n, f);
    fwrite(cb, 1, 4, f);
}
static void save_png(const char *path, const uint8_t *rgb, int w, int h) { /* uncompressed deflate blocks */
    FILE *f = fopen(path, "wb");
    if (!f) return;
    fwrite("\x89PNG\r\n\x1a\n", 1, 8, f);
    uint8_t ihdr[13] = {0, 0, w >> 8, w, 0, 0, h >> 8, h, 8, 2, 0, 0, 0};
    chunk(f, "IHDR", ihdr, 13);
    size_t raw = (size_t)h * (w * 3 + 1), blocks = (raw + 65534) / 65535, k = 0;
    uint8_t *line = malloc(raw), *z = malloc(raw + blocks * 5 + 6), *p = z;
    for (int y = 0; y < h; y++) { line[k++] = 0; memcpy(line + k, rgb + (size_t)y * w * 3, (size_t)w * 3); k += (size_t)w * 3; }
    uint32_t a = 1, b = 0;
    for (size_t i = 0; i < raw; i++) { a = (a + line[i]) % 65521; b = (b + a) % 65521; }
    *p++ = 0x78; *p++ = 1;
    for (size_t off = 0; off < raw; off += 65535) {
        size_t n = raw - off < 65535 ? raw - off : 65535;
        *p++ = off + n >= raw; *p++ = n & 255; *p++ = n >> 8; *p++ = ~n & 255; *p++ = (~n >> 8) & 255;
        memcpy(p, line + off, n); p += n;
    }
    uint32_t ad = b << 16 | a;
    *p++ = ad >> 24; *p++ = ad >> 16; *p++ = ad >> 8; *p++ = ad;
    chunk(f, "IDAT", z, (uint32_t)(p - z));
    chunk(f, "IEND", NULL, 0);
    fclose(f); free(z); free(line);
}

/* ---------------------------------------------------------------- the VI */

/* The VI's mode, as osViSetMode gives it (an OSViMode in the game's memory) and osViSetYScale and
   osViSetSpecialFeatures change it: the frame's width in memory, its pixel size, how many lines it shows, and the
   filters the VI runs as it shows them. Without a mode the frame's size is guessed from its width (320 x 240, or
   640 x 480). A game that draws at 400 x 300 was shown as 400 x 480, its frame read past its end. */
static struct { int known, width, bpp, lines; float yscale; int gamma, gamma_dither, divot, dedither; } vi;
void port_vi_mode(uint32_t mode) {
    R = port_memory();
    uint32_t a = 0x80000000u | (mode & 0x1FFFFFFF);
    uint32_t ctrl = rd32(a + 4), width = rd32(a + 8), xscale = rd32(a + 32) & 0xFFF, yscale = rd32(a + 44) & 0xFFF;
    uint32_t vstart = rd32(a + 48);
    int active = ((int)(vstart & 0x3FF) - (int)((vstart >> 16) & 0x3FF)) / 2; /* half-lines to lines: 237 */
    if (!width || !yscale || active <= 0) return;
    vi.known = 1;
    vi.width = (int)width;
    vi.bpp = (ctrl & 3) == 3 ? 32 : 16;
    /* the lines the frame holds: the active lines by the Y scale, 237 of them being a 240-line frame */
    vi.lines = (int)((float)active * (float)yscale / 1024.0f * 240.0f / 237.0f + 0.5f);
    vi.yscale = 1.0f;
    vi.gamma = (ctrl & 0x8) != 0; vi.gamma_dither = (ctrl & 0x4) != 0;
    vi.divot = (ctrl & 0x10) != 0; vi.dedither = (ctrl & 0x10000) != 0;
    (void)xscale;
}
void port_vi_yscale(float s) { if (s > 0) vi.yscale = s; }
void port_vi_features(uint32_t f) {
    if (f & 0x01) vi.gamma = 1;
    if (f & 0x02) vi.gamma = 0;
    if (f & 0x04) vi.gamma_dither = 1;
    if (f & 0x08) vi.gamma_dither = 0;
    if (f & 0x10) vi.divot = 1;
    if (f & 0x20) vi.divot = 0;
    if (f & 0x40) vi.dedither = 1;
    if (f & 0x80) vi.dedither = 0;
}

/* The VI's anti-aliasing filter, as the frame is shown (never written back): a pixel the RDP left partly covered,
   a silhouette's edge, is blended toward the background behind it, estimated from its fully covered neighbours
   as each channel's second-largest plus second-smallest minus the pixel's own, by how much of it is uncovered. */
static void vi_filter(uint8_t *rgb, int w, int h, uint32_t base, int bytes) {
    uint8_t *src = malloc((size_t)w * h * 3);
    memcpy(src, rgb, (size_t)w * h * 3);
    for (int y = 1; y < h - 1; y++)
        for (int x = 1; x < w - 1; x++) {
            int cvg = cvg_get(base + (uint32_t)(y * w + x) * (uint32_t)bytes);
            if (cvg >= 8) continue;
            for (int ch = 0; ch < 3; ch++) {
                int v[9], n = 0, centre = src[(y * w + x) * 3 + ch];
                v[n++] = centre;
                for (int dy = -1; dy <= 1; dy++)
                    for (int dx = -1; dx <= 1; dx++) {
                        int i = (y + dy) * w + x + dx;
                        if ((dx || dy) && cvg_get(base + (uint32_t)i * (uint32_t)bytes) >= 8) v[n++] = src[i * 3 + ch];
                    }
                if (n < 3) break; /* nothing around it to go by */
                for (int i = 1; i < n; i++) /* sorted, for the second-largest and second-smallest */
                    for (int j = i; j > 0 && v[j] < v[j - 1]; j--) { int t = v[j]; v[j] = v[j - 1]; v[j - 1] = t; }
                int bg = v[n - 2] + v[1] - centre;
                bg = bg < 0 ? 0 : bg > 255 ? 255 : bg;
                int out = centre + (bg - centre) * (8 - cvg) / 8;
                rgb[(y * w + x) * 3 + ch] = (uint8_t)out;
            }
        }
    free(src);
}

/* The VI's dedither filter (on fully covered pixels of a 16-bit frame): each 5-bit channel given back the 3 bits the
   RDP's dither cut, from how many of its eight neighbours are above or below it. */
static void vi_dedither(uint8_t *rgb, int w, int h, uint32_t base) {
    uint8_t *src = malloc((size_t)w * h * 3);
    memcpy(src, rgb, (size_t)w * h * 3);
    for (int y = 1; y < h - 1; y++)
        for (int x = 1; x < w - 1; x++) {
            if (cvg_get(base + (uint32_t)(y * w + x) * 2) < 8) continue;
            for (int ch = 0; ch < 3; ch++) {
                int c = src[(y * w + x) * 3 + ch] >> 3, sum = 0;
                for (int dy = -1; dy <= 1; dy++)
                    for (int dx = -1; dx <= 1; dx++) {
                        if (!dx && !dy) continue;
                        int n = src[((y + dy) * w + x + dx) * 3 + ch] >> 3;
                        sum += n > c ? 1 : n < c ? -1 : 0;
                    }
                int out = (c << 3) + (c << 3 >> 5) + sum;
                rgb[(y * w + x) * 3 + ch] = (uint8_t)(out < 0 ? 0 : out > 255 ? 255 : out);
            }
        }
    free(src);
}

/* The divot filter: a partly covered pixel takes the median of itself and its two neighbours on the line, which
   removes the one-pixel notches anti-aliasing leaves along silhouettes. */
static void vi_divot(uint8_t *rgb, int w, int h, uint32_t base, int bytes) {
    uint8_t *src = malloc((size_t)w * h * 3);
    memcpy(src, rgb, (size_t)w * h * 3);
    for (int y = 0; y < h; y++)
        for (int x = 1; x < w - 1; x++) {
            if (cvg_get(base + (uint32_t)(y * w + x) * (uint32_t)bytes) >= 8) continue;
            for (int ch = 0; ch < 3; ch++) {
                int a = src[(y * w + x - 1) * 3 + ch], b = src[(y * w + x) * 3 + ch], c = src[(y * w + x + 1) * 3 + ch];
                int lo = a < b ? a : b, hi = a < b ? b : a;
                rgb[(y * w + x) * 3 + ch] = (uint8_t)(c < lo ? lo : c > hi ? hi : c);
            }
        }
    free(src);
}

/* Gamma: the VI's square-root curve, with its dither (a random step of up to one level before it). Space and fades
   that games leave a dark grey on the console were black without it. */
static void vi_gamma(uint8_t *rgb, int w, int h) {
    for (int i = 0; i < w * h * 3; i++) {
        float v = sqrtf(rgb[i] / 255.0f) * 255.0f + (vi.gamma_dither ? noise() : 0.0f);
        rgb[i] = (uint8_t)(v > 255 ? 255 : v);
    }
}

int port_live_active(void); /* live.c */
void port_live_frame(const uint8_t *rgb, int w, int h);
/* The frame the VI shows next (osViSwapBuffer): saved when PORT_FRAMES names a folder, and shown live (PORT_LIVE). */
void port_frame_shown(uint32_t fb, unsigned long n) {
    static const char *dir;
    static long every = -1;
    if (every < 0) {
        dir = getenv("PORT_FRAMES");
        every = getenv("PORT_FRAME_EVERY") ? atol(getenv("PORT_FRAME_EVERY")) : 1;
        if (every < 1) every = 1;
    }
    int save = dir && n % (unsigned long)every == 0;
    if (!save && !port_live_active()) return;
    R = port_memory();
    uint32_t addr = fb & 0x1FFFFFFF;
    int w = 320, h, bytes = 2;
    for (int i = 0; i < 8; i++) if (images[i][0] == addr) w = (int)images[i][1];
    if (vi.known) {
        w = vi.width; bytes = vi.bpp / 8;
        h = (int)((float)vi.lines * vi.yscale + 0.5f);
    } else h = w > 320 ? 480 : 240;
    if (w > MAXW) w = MAXW;
    if (h > MAXH) h = MAXH;
    if (h < 1) h = 1;
    if ((size_t)addr + (size_t)w * h * bytes > 0x800000) h = (int)((0x800000 - addr) / ((uint32_t)w * bytes));
    uint8_t *rgb = malloc((size_t)w * h * 3);
    uint32_t base = 0x80000000u | addr;
    for (int i = 0; i < w * h; i++) {
        Color c = bytes == 4 ? unpack8888(rd32(base + (uint32_t)i * 4)) : unpack5551(rd16(base + (uint32_t)i * 2));
        rgb[i * 3] = (uint8_t)(c.r * 255); rgb[i * 3 + 1] = (uint8_t)(c.g * 255); rgb[i * 3 + 2] = (uint8_t)(c.b * 255);
    }
    if (vi.dedither && bytes == 2) vi_dedither(rgb, w, h, base);
    vi_filter(rgb, w, h, base, bytes);
    if (vi.divot) vi_divot(rgb, w, h, base, bytes);
    if (vi.gamma) vi_gamma(rgb, w, h);
    port_live_frame(rgb, w, h);
    if (save) {
        char path[1024];
        snprintf(path, sizeof path, "%s/frame_%05lu.png", dir, n);
        save_png(path, rgb, w, h);
    }
    free(rgb);
}
