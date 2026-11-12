/*
 * The RSP and RDP, in software: each graphics task's display list drawn into the game's own frame buffer, as the
 * N64 does (port/wasm). A first renderer, to see the game: Nintendo's F3DEX2 (3D) and S2DEX (2D backgrounds and
 * sprites) microcodes, chosen per task by the microcode it loads, and the RDP commands both send. Point sampling,
 * one or two combiner cycles, a depth buffer, no anti-aliasing or dithering.
 *
 * Addresses in a display list are segmented (segment << 24 | offset) or physical; the segment table is the list's
 * own (G_MOVEWORD). The game's memory is big-endian at the console's addresses: physical p is 0x80000000 | p here.
 *
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
static uint32_t zimg_addr;
static uint32_t fill_color;
static Color prim, env, blend;
static uint32_t combine0, combine1, mode_h, mode_l;
static int scissor[4] = {0, 0, 320, 240};
#define MAXW 640
#define MAXH 480
static float zbuf[MAXW * MAXH];
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

/* A texel of a tile at integer coordinates (s, t), from TMEM as the loads left it. */
static Color texel(const Tile *tile, int s, int t) {
    int w = (int)(tile->lrs - tile->uls) + 1, h = (int)(tile->lrt - tile->ult) + 1;
    s -= (int)tile->uls; t -= (int)tile->ult;
    int ms = tile->masks ? 1 << tile->masks : 0, mt = tile->maskt ? 1 << tile->maskt : 0;
    if ((tile->cms & 2) || !ms) s = s < 0 ? 0 : s >= w ? w - 1 : s;
    else if ((tile->cms & 1) && ((s >> tile->masks) & 1)) s = ms - 1 - (s & (ms - 1));
    else s &= ms - 1;
    if ((tile->cmt & 2) || !mt) t = t < 0 ? 0 : t >= h ? h - 1 : t;
    else if ((tile->cmt & 1) && ((t >> tile->maskt) & 1)) t = mt - 1 - (t & (mt - 1));
    else t &= mt - 1;
    if (s < 0) s = 0;
    if (t < 0) t = 0;
    uint32_t row = (uint32_t)(tile->tmem * 8 + t * tile->line * 8);
    uint32_t odd = (t & 1) && tile->siz < 3 ? 4 : 0; /* TMEM keeps odd rows' 32-bit halves swapped: read them back */
    switch (tile->siz) {
    case 0: { /* 4-bit */
        uint8_t b = tmem[((row + s / 2) ^ odd) & 4095];
        int v = (s & 1) ? b & 15 : b >> 4;
        if (tile->fmt == 2) return unpack5551(tlut[(tile->palette * 16 + v) & 255]);
        if (tile->fmt == 3) { float i = (v >> 1) / 7.0f; return (Color){i, i, i, (v & 1) ? 1.0f : 0.0f}; }
        float i = v / 15.0f;
        return (Color){i, i, i, i};
    }
    case 1: { /* 8-bit */
        uint8_t v = tmem[((row + s) ^ odd) & 4095];
        if (tile->fmt == 2) return unpack5551(tlut[v]);
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
    default: { /* 32-bit: split across TMEM's halves on the console; kept whole here */
        uint32_t a = (row + s * 4) & 4092;
        return (Color){tmem[a] / 255.0f, tmem[a + 1] / 255.0f, tmem[a + 2] / 255.0f, tmem[a + 3] / 255.0f};
    }
    }
}

/* The colour combiner, (a - b) * c + d per channel, for one cycle. */
static float pick(Color v) { return v.r; }
static Color input(int which, int sel, Color comb, Color t0, Color shade) {
    Color zero = {0, 0, 0, 0}, one = {1, 1, 1, 1};
    switch (sel) {
    case 0: return comb;
    case 1: case 2: return t0;
    case 3: return prim;
    case 4: return shade;
    case 5: return env;
    case 6: return which == 1 ? zero : one; /* b's 6 is the key centre: none here */
    }
    if (which == 2) { /* c: alphas as colours */
        float a = sel == 7 ? comb.a : sel == 8 || sel == 9 ? t0.a : sel == 10 ? prim.a : sel == 11 ? shade.a
                : sel == 12 ? env.a : 0;
        return (Color){a, a, a, a};
    }
    return zero;
}
static float alpha_input(int which, int sel, Color comb, Color t0, Color shade) {
    switch (sel) {
    case 0: return which == 2 ? 1.0f : comb.a; /* c's 0 is the LOD fraction */
    case 1: case 2: return t0.a;
    case 3: return prim.a;
    case 4: return shade.a;
    case 5: return env.a;
    case 6: return which == 2 ? 1.0f : 1.0f;
    }
    return 0;
}
static Color combine(int cycle, Color comb, Color t0, Color shade) {
    int a, b, c, d, aa, ab, ac, ad;
    if (cycle == 0) {
        a = (combine0 >> 20) & 15; c = (combine0 >> 15) & 31; aa = (combine0 >> 12) & 7; ac = (combine0 >> 9) & 7;
        b = (combine1 >> 28) & 15; d = (combine1 >> 15) & 7; ab = (combine1 >> 12) & 7; ad = (combine1 >> 9) & 7;
    } else {
        a = (combine0 >> 5) & 15; c = combine0 & 31; aa = (combine1 >> 21) & 7; ac = (combine1 >> 18) & 7;
        b = (combine1 >> 24) & 15; d = (combine1 >> 6) & 7; ab = (combine1 >> 3) & 7; ad = combine1 & 7;
    }
    Color A = input(0, a, comb, t0, shade), B = input(1, b, comb, t0, shade), C = input(2, c, comb, t0, shade);
    Color D = input(3, d, comb, t0, shade);
    if (d == 6) D = (Color){1, 1, 1, 1};
    if (d == 7) D = (Color){0, 0, 0, 0};
    (void)pick;
    Color out = {(A.r - B.r) * C.r + D.r, (A.g - B.g) * C.g + D.g, (A.b - B.b) * C.b + D.b, 0};
    out.a = (alpha_input(0, aa, comb, t0, shade) - alpha_input(1, ab, comb, t0, shade)) * alpha_input(2, ac, comb, t0, shade)
            + alpha_input(3, ad, comb, t0, shade);
    if (ad == 7) out.a -= alpha_input(3, ad, comb, t0, shade);
    out.r = out.r < 0 ? 0 : out.r > 1 ? 1 : out.r;
    out.g = out.g < 0 ? 0 : out.g > 1 ? 1 : out.g;
    out.b = out.b < 0 ? 0 : out.b > 1 ? 1 : out.b;
    out.a = out.a < 0 ? 0 : out.a > 1 ? 1 : out.a;
    return out;
}

static int in_frame(int x, int y) {
    return x >= scissor[0] && y >= scissor[1] && x < scissor[2] && y < scissor[3] && x >= 0 && y >= 0 && x < cimg_width && y < frame_height();
}
static uint32_t pixel_addr(int x, int y) { return 0x80000000u | ((cimg_addr + (uint32_t)(y * cimg_width + x) * 2) & 0x1FFFFFFF); }

/* One pixel through the combiner and blender: shade and texel colours in, the frame buffer written. */
static void plot(int x, int y, float z, Color shade, Color t0, int depth) {
    if (!in_frame(x, y)) return;
    int zcmp = (mode_l & 0x10) != 0, zupd = (mode_l & 0x20) != 0, zi = y * MAXW + x;
    if (depth && zcmp && z > zbuf[zi]) return;
    Color c = combine(0, (Color){0, 0, 0, 0}, t0, shade);
    if (cycle_type() == 1) c = combine(1, c, t0, shade);
    if ((mode_l & 3) == 1 && c.a <= blend.a) return; /* alpha compare: above the blend colour's alpha */
    if ((mode_l & 0x2000) && c.a <= 0.0f) return; /* coverage times alpha: nothing covered */
    uint32_t a = pixel_addr(x, y);
    if ((mode_l & 0x4000) && c.a < 1.0f) { /* force blend: in * alpha + memory * (1 - alpha) */
        Color m = unpack5551(rd16(a));
        c.r = c.r * c.a + m.r * (1 - c.a); c.g = c.g * c.a + m.g * (1 - c.a); c.b = c.b * c.a + m.b * (1 - c.a);
    }
    wr16(a, pack5551(c));
    if (depth && zupd) zbuf[zi] = z;
}

static void fill_rect(int x0, int y0, int x1, int y1) {
    if (cycle_type() == 3) { /* fill mode: the fill colour, two pixels a word, edges inclusive */
        int zclear = cimg_addr == zimg_addr;
        for (int y = y0; y <= y1; y++)
            for (int x = x0; x <= x1; x++) {
                if (!in_frame(x, y)) continue;
                wr16(pixel_addr(x, y), (uint16_t)((x & 1) ? fill_color : fill_color >> 16));
                if (zclear) zbuf[y * MAXW + x] = 1e30f;
            }
        return;
    }
    for (int y = y0; y < y1; y++)
        for (int x = x0; x < x1; x++) plot(x, y, 0, prim, (Color){0, 0, 0, 0}, 0);
}

/* A textured rectangle from (x0, y0) to (x1, y1) in pixels, texture coordinates s, t at its corner and their steps
   per pixel. */
static void tex_rect(int tile, float x0, float y0, float x1, float y1, float s, float t, float dsdx, float dtdy, int flip) {
    int copy = cycle_type() == 2;
    if (copy) { dsdx /= 4.0f; x1 += 1; y1 += 1; }
    for (int y = (int)y0; y < (int)y1; y++)
        for (int x = (int)x0; x < (int)x1; x++) {
            float dx = x - x0, dy = y - y0;
            float ss = s + (flip ? dy : dx) * dsdx, tt = t + (flip ? dx : dy) * dtdy;
            Color c = texel(&tiles[tile], (int)floorf(ss), (int)floorf(tt));
            if (copy) {
                if ((mode_l & 3) == 1 && c.a <= blend.a) continue;
                if (in_frame(x, y)) wr16(pixel_addr(x, y), pack5551(c));
            } else plot(x, y, 0, (Color){1, 1, 1, 1}, c, 0);
        }
}

/* ---------------------------------------------------------------- texture loads */

/* A 64-bit word into TMEM; on an odd line its 32-bit halves are swapped, as the RDP stores them. */
static void tmem_word(uint32_t at, uint32_t src, int odd) {
    for (int k = 0; k < 8; k++) tmem[(at + (uint32_t)(odd ? k ^ 4 : k)) & 4095] = R[src + k];
}
static void load_block(int tile, int uls, int ult, int lrs, int dxt) {
    Tile *tl = &tiles[tile];
    int bytes = timg_siz == 0 ? 1 : 1 << (timg_siz - 1); /* 4-bit loads are made as 8 or 16-bit ones */
    uint32_t src = phys(timg_addr) + (uint32_t)((ult * timg_width + uls) * bytes);
    uint32_t words = ((uint32_t)(lrs - uls + 1) * bytes + 7) / 8;
    uint32_t line = 0; /* the line counter: dxt per word, the line is odd when bit 11 is set */
    for (uint32_t i = 0; i < words && tl->tmem * 8 + i * 8 < 4096; i++) {
        tmem_word(tl->tmem * 8 + i * 8, src + i * 8, timg_siz < 3 && ((line >> 11) & 1));
        line += (uint32_t)dxt;
    }
}
static void load_tile(int tile, int uls, int ult, int lrs, int lrt) {
    Tile *tl = &tiles[tile];
    int bpp4 = timg_siz == 0, bytes = bpp4 ? 1 : 1 << (timg_siz - 1);
    for (int t = ult; t <= lrt; t++) {
        uint32_t src = phys(timg_addr) + (uint32_t)(bpp4 ? (t * timg_width + uls) / 2 : (t * timg_width + uls) * bytes);
        uint32_t dst = (uint32_t)(tl->tmem * 8 + (t - ult) * tl->line * 8);
        int n = bpp4 ? (lrs - uls + 2) / 2 : (lrs - uls + 1) * bytes;
        for (int i = 0; i < n; i += 8) tmem_word(dst + (uint32_t)i, src + (uint32_t)i, ((t - ult) & 1) && timg_siz < 3);
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
        for (int i = 0; i < 8; i++) /* each colour image's width, for showing it later */
            if (!images[i][0] || images[i][0] == cimg_addr) { images[i][0] = cimg_addr; images[i][1] = (uint32_t)cimg_width; break; }
        break;
    case 0xFE: zimg_addr = w1 & 0x1FFFFFFF; break;
    case 0xFD: timg_addr = w1; timg_siz = (w0 >> 19) & 3; timg_width = (w0 & 0xFFF) + 1; break;
    case 0xF7: fill_color = w1; break;
    case 0xFA: prim = unpack8888(w1); break;
    case 0xFB: env = unpack8888(w1); break;
    case 0xF9: blend = unpack8888(w1); break;
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

typedef struct { float x, y, z, w, sx, sy, sz, s, t; Color shade; int clip; } Vtx;
static Vtx vtx[64];
static float mv[10][16], proj[16], mvp[16];
static int mv_depth;
static float vp_scale[3] = {640, 480, 511}, vp_trans[3] = {640, 480, 511};
static uint32_t geometry;
static int tex_on, tex_tile;
static float tex_sscale = 1, tex_tscale = 1;
static Color lights[8];
static float light_dir[8][3];
static int num_lights = 1;

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
        } else v->shade = (Color){c0 / 255.0f, c1 / 255.0f, c2 / 255.0f, c3 / 255.0f};
        v->clip = 0;
    }
}

static void project(Vtx *v) {
    float iw = 1.0f / v->w;
    v->sx = vp_trans[0] / 4.0f + v->x * iw * vp_scale[0] / 4.0f;
    v->sy = vp_trans[1] / 4.0f - v->y * iw * vp_scale[1] / 4.0f;
    v->sz = v->z * iw;
}

/* One triangle already in front of the eye, in screen space. */
static void raster(const Vtx *a, const Vtx *b, const Vtx *c) {
    float area = (b->sx - a->sx) * (c->sy - a->sy) - (c->sx - a->sx) * (b->sy - a->sy);
    if (area == 0) return;
    int minx = (int)floorf(fminf(a->sx, fminf(b->sx, c->sx))), maxx = (int)ceilf(fmaxf(a->sx, fmaxf(b->sx, c->sx)));
    int miny = (int)floorf(fminf(a->sy, fminf(b->sy, c->sy))), maxy = (int)ceilf(fmaxf(a->sy, fmaxf(b->sy, c->sy)));
    if (minx < scissor[0]) minx = scissor[0];
    if (miny < scissor[1]) miny = scissor[1];
    if (maxx > scissor[2]) maxx = scissor[2];
    if (maxy > scissor[3]) maxy = scissor[3];
    float wa = 1 / a->w, wb = 1 / b->w, wc = 1 / c->w, inv = 1 / area;
    int smooth = (geometry & 0x200000) != 0;
    Tile *tl = &tiles[tex_tile];
    for (int y = miny; y < maxy; y++)
        for (int x = minx; x < maxx; x++) {
            float px = x + 0.5f, py = y + 0.5f;
            float l0 = ((b->sx - px) * (c->sy - py) - (c->sx - px) * (b->sy - py)) * inv;
            float l1 = ((c->sx - px) * (a->sy - py) - (a->sx - px) * (c->sy - py)) * inv;
            float l2 = 1 - l0 - l1;
            if (l0 < 0 || l1 < 0 || l2 < 0) continue;
            float z = l0 * a->sz + l1 * b->sz + l2 * c->sz;
            Color sh = smooth ? (Color){l0 * a->shade.r + l1 * b->shade.r + l2 * c->shade.r,
                                        l0 * a->shade.g + l1 * b->shade.g + l2 * c->shade.g,
                                        l0 * a->shade.b + l1 * b->shade.b + l2 * c->shade.b,
                                        l0 * a->shade.a + l1 * b->shade.a + l2 * c->shade.a} : c->shade;
            Color t0 = {1, 1, 1, 1};
            if (tex_on) {
                float q = l0 * wa + l1 * wb + l2 * wc;
                float s = (l0 * a->s * wa + l1 * b->s * wb + l2 * c->s * wc) / q;
                float t = (l0 * a->t * wa + l1 * b->t * wb + l2 * c->t * wc) / q;
                if (tl->shifts) s = tl->shifts > 10 ? s * (float)(1 << (16 - tl->shifts)) : s / (float)(1 << tl->shifts);
                if (tl->shiftt) t = tl->shiftt > 10 ? t * (float)(1 << (16 - tl->shiftt)) : t / (float)(1 << tl->shiftt);
                /* 4-bit textures: a racing game's menu text gives each glyph twice the texel coordinates of its
                   place in a 128-texel font page (the matched C doubles its table's values); halved, the text reads
                   as on the console. Found by trying it: the RDP rule behind it is still to be confirmed. */
                if (tl->siz == 0) { s *= 0.5f; t *= 0.5f; }
                t0 = texel(tl, (int)floorf(s), (int)floorf(t));
            }
            plot(x, y, z, sh, t0, (geometry & 1) != 0);
        }
}

static Vtx lerp(const Vtx *a, const Vtx *b, float f) {
    Vtx v;
    v.x = a->x + (b->x - a->x) * f; v.y = a->y + (b->y - a->y) * f; v.z = a->z + (b->z - a->z) * f;
    v.w = a->w + (b->w - a->w) * f; v.s = a->s + (b->s - a->s) * f; v.t = a->t + (b->t - a->t) * f;
    v.shade.r = a->shade.r + (b->shade.r - a->shade.r) * f; v.shade.g = a->shade.g + (b->shade.g - a->shade.g) * f;
    v.shade.b = a->shade.b + (b->shade.b - a->shade.b) * f; v.shade.a = a->shade.a + (b->shade.a - a->shade.a) * f;
    v.clip = 0;
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

static void f3dex2(uint8_t op, uint32_t w0, uint32_t w1) {
    switch (op) {
    case 0x01: { int n = (w0 >> 12) & 0xFF, end = (w0 >> 1) & 0x7F; load_vertices(phys(w1), n, end - n); break; }
    case 0x05: triangle(((w0 >> 16) & 0xFF) / 2, ((w0 >> 8) & 0xFF) / 2, (w0 & 0xFF) / 2); break;
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
        tex_on = (w0 & 0xFF) != 0; tex_tile = (w0 >> 8) & 7;
        tex_sscale = (w1 >> 16) == 0xFFFF ? 1.0f : (w1 >> 16) / 65536.0f;
        tex_tscale = (w1 & 0xFFFF) == 0xFFFF ? 1.0f : (w1 & 0xFFFF) / 65536.0f; break;
    case 0xDC: { int index = w0 & 0xFF, offset = (int)((w0 >> 8) & 0xFF) * 8; uint32_t a = phys(w1);
        if (index == 8)
            for (int i = 0; i < 3; i++) { vp_scale[i] = (int16_t)rd16(a + i * 2); vp_trans[i] = (int16_t)rd16(a + 8 + i * 2); }
        else if (index == 10) { int l = (offset - 0x18) / 24; /* lights come after the two look-at entries */
            if (l >= 0 && l < 8) {
                lights[l] = (Color){R[a] / 255.0f, R[a + 1] / 255.0f, R[a + 2] / 255.0f, 1};
                float x = (int8_t)R[a + 8], y = (int8_t)R[a + 9], z = (int8_t)R[a + 10], len = sqrtf(x * x + y * y + z * z);
                if (len == 0) len = 1;
                light_dir[l][0] = x / len; light_dir[l][1] = y / len; light_dir[l][2] = z / len;
            } }
        break; }
    case 0xDB: if (((w0 >> 16) & 0xFF) == 2) num_lights = (int)(w1 / 24); break;
    default: rdp(op, w0, w1);
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
static void s2d_sprite(uint32_t sp) { /* uObjSprite: an unrotated rectangle from TMEM */
    float x = (int16_t)rd16(sp) / 4.0f, y = (int16_t)rd16(sp + 8) / 4.0f;
    float sw = rd16(sp + 2) / 1024.0f, sh = rd16(sp + 10) / 1024.0f;
    int iw = rd16(sp + 4) >> 5, ih = rd16(sp + 12) >> 5;
    Tile t = {.fmt = R[sp + 20], .siz = R[sp + 21], .line = rd16(sp + 16), .tmem = rd16(sp + 18), .palette = R[sp + 22],
              .cms = 2, .cmt = 2, .lrs = (float)(iw - 1), .lrt = (float)(ih - 1)};
    if (sw == 0) sw = 1;
    if (sh == 0) sh = 1;
    tiles[7] = t;
    tex_rect(7, x, y, x + iw / sw, y + ih / sh, 0, 0, 1.0f / sw, 1.0f / sh, 0);
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
            if (cycle_type() == 2) { if (in_frame(px, py) && c.a > 0) wr16(pixel_addr(px, py), pack5551(c)); }
            else plot(px, py, 0, (Color){1, 1, 1, 1}, c, 0);
        }
}
static void s2dex(uint8_t op, uint32_t w0, uint32_t w1) {
    switch (op) {
    case 0x01: case 0x02: case 0xDA: s2d_sprite(phys(w1)); break; /* rotated sprites drawn unrotated for now */
    case 0x05: s2d_load(phys(w1)); break;
    case 0x06: case 0x07: case 0x08: s2d_load(phys(w1)); s2d_sprite(phys(w1) + 24); break;
    case 0x09: s2d_bg(phys(w1), 1); break;
    case 0x0A: s2d_bg(phys(w1), 0); break;
    case 0x0B: case 0x04: case 0xDC: case 0xD7: case 0xD9: break;
    default: rdp(op, w0, w1);
    }
}

/* ---------------------------------------------------------------- tasks */

static unsigned long render_tasks;

void port_render_task(uint32_t task) {
    R = port_memory();
    render_tasks++;
    uint32_t ud = rd32(task + 24) & 0x1FFFFFFF;
    int is_2d = 0; /* the microcode's data names it: "RSP Gfx ucode S2DEX ..." */
    for (uint32_t i = 0; i + 19 < 0x800; i++)
        if (R[0x80000000u + ud + i] == 'R' && !memcmp(R + 0x80000000u + ud + i, "RSP Gfx ucode ", 14)) {
            is_2d = !memcmp(R + 0x80000000u + ud + i + 14, "S2DEX", 5); break; }
    static long trace = -1;
    if (trace < 0) trace = getenv("PORT_DLTRACE") ? atol(getenv("PORT_DLTRACE")) : 0;
    int tracing = trace > 0 && (long)render_tasks >= trace && (long)render_tasks < trace + 6;
    if (tracing) fprintf(stderr, "== task %lu, %s\n", render_tasks, is_2d ? "S2DEX" : "F3DEX2");
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
        if (op == 0xDB && ((w0 >> 16) & 0xFF) == 0x06) segments[((w0 & 0xFFFF) / 4) & 15] = w1 & 0x1FFFFFFF;
        else if (op == 0xDE) { /* G_DL: a call, or a branch when the no-push bit is set */
            if ((phys(w1) & 0x1FFFFFFF) < 0x400) { /* the boot's vectors, never a display list: a null pointer */
                static int said;
                if (!said++) fprintf(stderr, "render: a display list calls address %08X (a null pointer); skipped\n", w1);
                if ((w0 >> 16) & 1) { if (!depth) break; pc = stack[--depth]; }
                continue;
            }
            if (!((w0 >> 16) & 1) && depth < 18) stack[depth++] = pc;
            pc = phys(w1);
        } else if (op == 0xDF) { /* G_ENDDL */
            if (!depth) break;
            pc = stack[--depth];
        } else if (op == 0xE4 || op == 0xE5) { /* a textured rectangle: two more commands carry s, t and steps */
            uint32_t h1 = rd32(pc + 4), h2 = rd32(pc + 12);
            pc += 16;
            float xh = ((w0 >> 12) & 0xFFF) / 4.0f, yh = (w0 & 0xFFF) / 4.0f;
            float xl = ((w1 >> 12) & 0xFFF) / 4.0f, yl = (w1 & 0xFFF) / 4.0f;
            tex_rect((w1 >> 24) & 7, xl, yl, xh, yh, (int16_t)(h1 >> 16) / 32.0f, (int16_t)h1 / 32.0f,
                     (int16_t)(h2 >> 16) / 1024.0f, (int16_t)h2 / 1024.0f, op == 0xE5);
        } else if (is_2d) s2dex(op, w0, w1);
        else f3dex2(op, w0, w1);
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

/* The frame the VI shows next (osViSwapBuffer): saved when PORT_FRAMES names a folder. */
void port_frame_shown(uint32_t fb, unsigned long n) {
    static const char *dir;
    static long every = -1;
    if (every < 0) {
        dir = getenv("PORT_FRAMES");
        every = getenv("PORT_FRAME_EVERY") ? atol(getenv("PORT_FRAME_EVERY")) : 1;
        if (every < 1) every = 1;
    }
    if (!dir || n % (unsigned long)every) return;
    R = port_memory();
    uint32_t addr = fb & 0x1FFFFFFF;
    int w = 320;
    for (int i = 0; i < 8; i++) if (images[i][0] == addr) w = (int)images[i][1];
    int h = w > 320 ? 480 : 240;
    uint8_t *rgb = malloc((size_t)w * h * 3);
    uint32_t base = 0x80000000u | (fb & 0x1FFFFFFF);
    for (int i = 0; i < w * h; i++) {
        Color c = unpack5551(rd16(base + (uint32_t)i * 2));
        rgb[i * 3] = (uint8_t)(c.r * 255); rgb[i * 3 + 1] = (uint8_t)(c.g * 255); rgb[i * 3 + 2] = (uint8_t)(c.b * 255);
    }
    char path[1024];
    snprintf(path, sizeof path, "%s/frame_%05lu.png", dir, n);
    save_png(path, rgb, w, h);
    free(rgb);
}
