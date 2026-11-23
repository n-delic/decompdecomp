/*
 * The RSP and RDP, in software: each graphics task's display list drawn into the game's own frame buffer, as the
 * N64 does (port/wasm). Nintendo's microcodes of both GBI generations: Fast3D, F3DEX 1.x and F3DEX2 (3D), S2DEX
 * 1.x and S2DEX2 (2D backgrounds and sprites), told apart by the signature in the microcode's data, per task and at
 * each G_LOAD_UCODE; the RDP commands they all send. Textures filtered as the RDP filters them (point, its
 * three-texel bilinear, or average), two tiles and mip levels, one or two combiner cycles, the blender with fog,
 * depth in the game's z image, anti-aliasing (the RDP's coverage and the VI's filter), the RDP's dither, and the
 * VI's mode (size, gamma, its filters). Triangles go to the RDP as the RSP's microcode sends them (triangle commands
 * in fixed point, clipped at its guard band), S2DEX's objects as its texture loads, rectangles and triangles, and the
 * RDP draws them as the console does: edges walked in quarter scanlines, coverage from eight samples, combiner,
 * blender, filtering, dither and depth test in its integer arithmetic, two cycles pipelined pixel to pixel, copy mode
 * 64 bits at a time. Matched pixel for pixel against angrylion's renderer (tools/n64_rdp_compare.py) on test scenes
 * and on a racing game's recorded frames.
 *
 * PORT_RDPDUMP=folder, PORT_RDPDUMP_TASKS=first-last: those tasks' RDP commands and memory, for that comparison.
 *
 * Addresses in a display list are segmented (segment << 24 | offset) or physical; the segment table is the list's
 * own (G_MOVEWORD). The game's memory is big-endian at the console's addresses: physical p is 0x80000000 | p here.
 *
 * PORT_LIVE=port: played in a browser (live.c).
 * PORT_FRAMES=folder: every frame shown saved there as frame_NNNNN.png (PORT_FRAME_EVERY=n: every nth).
 * PORT_DLTRACE=n: the display lists of tasks n to n+5 printed.
 */
#include <math.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* The per-pixel functions, inlined whatever the build's flags (the native host is built with -fno-inline): compiled
   to WebAssembly, a call passes and returns each colour (four floats) through memory, on the stack in the module's
   memory, where a native build keeps them in registers. Inlined, a racing game's frames took a fifth less. */
#define HOT inline __attribute__((always_inline))

uint8_t *port_memory(void);
static uint8_t *R;
static HOT uint32_t rd32(uint32_t a) { uint32_t v; memcpy(&v, R + a, 4); return __builtin_bswap32(v); }
static HOT uint16_t rd16(uint32_t a) { uint16_t v; memcpy(&v, R + a, 2); return __builtin_bswap16(v); }
static HOT void wr16(uint32_t a, uint16_t v) { v = __builtin_bswap16(v); memcpy(R + a, &v, 2); }

/* PORT_RDPDUMP=folder with PORT_RDPDUMP_TASKS=first-last: those tasks' RDP commands recorded as the RDP would
   receive them (state commands with physical addresses, each triangle as the triangle command the RSP would send,
   rectangles), with the game's memory before and after, for tools/n64_rdp_compare.py to replay through angrylion's
   renderer and compare frame by frame. */
static FILE *dump_cmds;
static void record(const uint32_t *words, int n) {
    if (!dump_cmds) return;
    for (int i = 0; i < n; i++) { uint32_t v = __builtin_bswap32(words[i]); fwrite(&v, 4, 1, dump_cmds); }
}
static void record2(uint32_t w0, uint32_t w1) { uint32_t w[2] = {w0, w1}; record(w, 2); }

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
static uint32_t rsp_mode_h, rsp_mode_l; /* partial G_SETOTHERMODE commands update the microcode's own copy */
static int scissor[4] = {0, 0, 320, 240};
static int scissor_q[4] = {0, 0, 320 * 4, 240 * 4}; /* the scissor as G_SETSCISSOR gives it, in quarter pixels */
#define MAXW 640
#define MAXH 480
/* Each pixel's coverage (how many of its 8 sample points were covered, 1 to 8), kept as the RDP keeps it: for a
   16-bit pixel the top of its 3 bits in the pixel's own low bit and the other 2 in RDRAM's hidden bits (cvg_hole,
   one per 16-bit word, with 4 set once the blender wrote them); for a 32-bit pixel in its alpha's top 3 bits. A
   pixel the blender hasn't written (filled, copied, or put there by the CPU) has clean hidden bits (0) and takes
   its coverage from its low bit: full when it's set. Kept apart from the pixel, with its low bit always 1, the coverage games and the VI read was another. */
static uint8_t cvg_hole[4u << 20];
static HOT int cvg_px(uint32_t a, int bytes) {
    if (bytes == 4) return ((R[a + 3] >> 5) & 7) + 1;
    int low = bytes == 2 ? R[a + 1] & 1 : 1, h = cvg_hole[(a & 0x7FFFFF) >> 1];
    h = (h & 4) ? h & 3 : low ? 3 : 0;
    return ((low << 2) | h) + 1;
}
#define cvg_get(a) cvg_px((a), cimg_siz == 3 ? 4 : cimg_siz == 1 ? 1 : 2)
static HOT void cvg_set(uint32_t a, int c) { /* as the blender writes it */
    int f = (c < 1 ? 1 : c > 8 ? 8 : c) - 1;
    if (cimg_siz == 3) R[a + 3] = (uint8_t)(f << 5);
    else if (cimg_siz == 2) R[a + 1] = (uint8_t)((R[a + 1] & ~1) | (f >> 2));
    cvg_hole[(a & 0x7FFFFF) >> 1] = (uint8_t)(4 | (f & 3));
}
static HOT void cvg_clean(uint32_t a) { cvg_hole[(a & 0x7FFFFF) >> 1] = 0; } /* the pixel speaks for itself */
static uint32_t images[8][2]; /* colour images drawn to: address, width */
/* The lines a frame may be drawn on: the RDP knows none (its scissor says), so only the renderer's own buffers'
   (MAXH). Taken as 240 for any frame up to 320 wide, a PAL racing game's 264-line frames lost their last 24 lines. */
static int frame_height(void) { return MAXH; }
/* The lines a frame has, for the GPU's frame buffers (render_gpu.h): 240 (480 for a frame wider than 320), or more
   where the scissor reaches further (a PAL racing game's 264). */
static int frame_lines(void) {
    int lines = cimg_width > 320 ? 480 : 240;
    return scissor[3] > lines ? (scissor[3] < MAXH ? scissor[3] : MAXH) : lines;
}

#define cycle_type() ((int)((mode_h >> 20) & 3)) /* 0 one, 1 two, 2 copy, 3 fill */

/* The GPU path (render_gpu.h): on when the page draws on the GPU (the browser build's WebGL), where each primitive
   goes with the RDP's state instead of through the raster below. */
static int gpu;
static uint32_t gpu_gen = 1; /* bumped by every load into TMEM or the palette: the GPU's textures are made again */
static void gpu_fill_rect(int x0, int y0, int x1, int y1);
static void gpu_tex_rect(int tile, float x0, float y0, float x1, float y1, float s, float t, float dsdx, float dtdy, int flip);

/* A channel's n bits as a fraction, by table: i / 255.0f and the like, each worked out once with the same division.
   Divided per texel and per pixel, these were a large part of the renderer's time. make_tables() fills them; the
   renderer's entry points (port_render_task, port_frame_shown) call it first. */
static float by255[256], by31[32], by15[16], by7[8], by511[512]; /* by511: a 9-bit texel (YUV, converted) */
static void make_tables(void) {
    if (by255[255] != 0) return;
    for (int i = 0; i < 256; i++) by255[i] = i / 255.0f;
    for (int i = 0; i < 512; i++) by511[i] = i / 255.0f;
    /* a 5-bit texel channel widened as the RDP widens it, its top bits repeated below: (v << 3) | (v >> 2). Stretched
       by 255 / 31 instead, 3, 7, 24 and 28 came out a step off. */
    for (int i = 0; i < 32; i++) by31[i] = (float)(i << 3 | i >> 2) / 255.0f;
    for (int i = 0; i < 16; i++) by15[i] = i / 15.0f;
    for (int i = 0; i < 8; i++) by7[i] = i / 7.0f;
}
static HOT Color unpack5551(uint16_t c) {
    return (Color){by31[(c >> 11) & 31], by31[(c >> 6) & 31], by31[(c >> 1) & 31], (c & 1) ? 1.0f : 0.0f};
}
static HOT Color unpack8888(uint32_t c) {
    return (Color){by255[c >> 24], by255[(c >> 16) & 255], by255[(c >> 8) & 255], by255[c & 255]};
}
static int clamp31(float v) { int i = (int)(v * 31.0f + 0.5f); return i < 0 ? 0 : i > 31 ? 31 : i; }
static uint16_t pack5551(Color c) { return (uint16_t)(clamp31(c.r) << 11 | clamp31(c.g) << 6 | clamp31(c.b) << 1 | 1); }

/* A texel coordinate as the RDP takes it: clamped to the tile first (when its clamp bit is set, or it has no mask),
   then wrapped or mirrored inside its mask. A clamped, mirrored tile wider than its mask shows the mirrored copy
   across its whole width; taking the clamp bit as "clamp only" read the second half raw from TMEM (a decal mirrored
   both ways came out as half a decal and garbage). */
static int copying; /* copy mode: the RDP wraps by the mask but never clamps (it reads on through TMEM) */
#define WRAP_COORD(s, size, cm, mask) do { /* a macro: the host is built without inlining */ \
        if (!copying && (((cm) & 2) || !(mask))) (s) = (s) < 0 ? 0 : (s) >= (size) ? (size) - 1 : (s); \
        if (mask) { int m_ = 1 << (mask); \
            (s) = (((cm) & 1) && (((s) >> (mask)) & 1)) ? m_ - 1 - ((s) & (m_ - 1)) : (s) & (m_ - 1); } \
    } while (0)
static int wrap_coord(int s, int size, int cm, int mask) { WRAP_COORD(s, size, cm, mask); return s; }

/* A tile's shift on texture coordinates: 1 to 10 divide by 2^shift, 11 to 15 multiply by 2^(16 - shift). */
#define shifted(v, shift) (!(shift) ? (v) : (shift) > 10 ? (v) * (float)(1 << (16 - (shift))) : (v) * (1.0f / (float)(1 << (shift))))
/* (Times 2^-shift rather than divided by 2^shift: the same float, as scaling by a power of two is exact.) */

/* A palette entry as other mode H's TLUT type reads it: RGBA 5551, or IA16 (intensity, alpha). A game's text drawn
   with an IA16 palette came out as solid blocks when every palette was read as RGBA. */
static HOT Color tlut_colour(uint16_t v) {
    if (((mode_h >> 14) & 3) == 3) { float i = by255[v >> 8]; return (Color){i, i, i, by255[v & 255]}; }
    return unpack5551(v);
}

/* The palette as colours, for the type other mode H reads it as (tlut_colour): worked out again only after a load
   changes the palette or the type changes. Every colour-indexed texel was decoded from its entry before. */
static Color tlut_colours[256];
static int tlut_colours_type = -1; /* -1: to work out again */
static HOT Color palette_colour(int i) {
    int type = (int)((mode_h >> 14) & 3) == 3;
    if (tlut_colours_type != type) {
        for (int k = 0; k < 256; k++) tlut_colours[k] = tlut_colour(tlut[k]);
        tlut_colours_type = type;
    }
    return tlut_colours[i];
}

/* A texel coordinate from the tile's corner, wrapped as the tile asks (`size` its width or height) and never below
   0: once each for a bilinear sample's two columns and two rows, rather than per texel. */
static HOT int wrapped(int s, int size, int cm, int mask) {
    WRAP_COORD(s, size, cm, mask);
    return s < 0 ? 0 : s;
}
/* YUV: a 16-bit YUV tile's texels are loaded split (load_tile, load_block), each pair's U and V in TMEM's low half
   and its two Ys at the same place in the high half, a byte each; so a texel's Y is at its column and its U and V at
   its pair's, half the columns. `uvs` is the column whose pair gives U and V (a bilinear filter's second column is
   the next pair's). As the RDP returns them: U and V less 128 in red and green, Y in blue and alpha, each kept in 9
   bits (a negative U or V with its high bits set). A YUV image drawn as RGBA16 came out as noise. */
typedef struct { int r, g, b, a; } IC;
#define SIGN9(v) (((v) & 0x100) ? ((v) | ~0x1FF) : ((v) & 0x1FF))
static HOT IC yuv_texel(uint32_t row, int s, int uvs, uint32_t odd) {
    uint32_t uv = ((((row + (uint32_t)uvs) >> 1) << 1) ^ odd) & 0x7FE;
    int y = tmem[(((row + (uint32_t)s) ^ odd) & 0x7FF) | 0x800];
    return (IC){(tmem[uv] - 0x80) & 0x1FF, (tmem[uv + 1] - 0x80) & 0x1FF, y, y};
}
/* The texel at wrapped (s, t), from TMEM as the loads left it. */
static HOT Color texel_wrapped(const Tile *tile, int s, int t) {
    uint32_t row = (uint32_t)(tile->tmem * 8 + t * tile->line * 8);
    uint32_t odd = (t & 1) ? 4 : 0; /* TMEM keeps odd rows' 32-bit halves swapped: read them back */
    switch (tile->siz) {
    case 0: { /* 4-bit */
        uint8_t b = tmem[((row + s / 2) ^ odd) & 4095];
        int v = (s & 1) ? b & 15 : b >> 4;
        if (tile->fmt == 2) return palette_colour((tile->palette * 16 + v) & 255);
        if (tile->fmt == 3) { float i = by7[v >> 1]; return (Color){i, i, i, (v & 1) ? 1.0f : 0.0f}; }
        float i = by15[v];
        return (Color){i, i, i, i};
    }
    case 1: { /* 8-bit */
        uint8_t v = tmem[((row + s) ^ odd) & 4095];
        if (tile->fmt == 2) return palette_colour(v);
        if (tile->fmt == 3) { float i = by15[v >> 4]; return (Color){i, i, i, by15[v & 15]}; }
        float i = by255[v];
        return (Color){i, i, i, i};
    }
    case 2: { /* 16-bit */
        if (tile->fmt == 1) { IC c = yuv_texel(row, s, s, odd); return (Color){by511[c.r], by511[c.g], by511[c.b], by511[c.a]}; }
        uint32_t a = ((row + s * 2) ^ odd) & 4094;
        uint16_t v = (uint16_t)(tmem[a] << 8 | tmem[a + 1]);
        if (tile->fmt == 3) { float i = by255[v >> 8]; return (Color){i, i, i, by255[v & 255]}; }
        return unpack5551(v);
    }
    default: { /* 32-bit: red and green in TMEM's low half, blue and alpha at the same place in its high half */
        uint32_t a = ((row + s * 2) ^ odd) & 2046;
        return (Color){by255[tmem[a]], by255[tmem[a + 1]], by255[tmem[a + 2048]], by255[tmem[a + 2049]]};
    }
    }
}

/* A texel of a tile at integer coordinates (s, t). */
static Color texel(const Tile *tile, int s, int t) {
    int w = (int)(tile->lrs - tile->uls) + 1, h = (int)(tile->lrt - tile->ult) + 1;
    return texel_wrapped(tile, wrapped(s - (int)tile->uls, w, tile->cms, tile->masks),
                         wrapped(t - (int)tile->ult, h, tile->cmt, tile->maskt));
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
/* In the RDP's own arithmetic: texels as 8-bit channels, the point's place within the texel in 5 bits (s and t
   are s10.5), each neighbour weighted by it and the sum rounded at the 5th bit. G_TF_AVERAGE is bilinear but for a
   point exactly at a texel's centre, which takes the four texels' mean. Filtered in floats, a textured surface
   came out a step off the console's across a twentieth of its pixels; and the average filter always took the mean. */
#define C8(v) ((int)((v) * 255.0f + 0.5f))
#define TOI(c) ((IC){C8((c).r), C8((c).g), C8((c).b), C8((c).a)})
#define LERP3(a, b, c, fb, fc) ((a) + (((fb) * ((b) - (a)) + (fc) * ((c) - (a)) + 0x10) >> 5))
/* A bilinear sample of a YUV tile: Y filtered as any texel, U and V at half the columns (their fraction in the
   pair, `fr`; their own triangle of the square, `upr`); `bilerp` 0, the convert stage's corners unfiltered. */
static HOT IC yuv_sample(const Tile *tile, int cols[2], int rows[2], int fs, int ft, int s0, int filter, int bilerp) {
    uint32_t row0 = (uint32_t)(tile->tmem * 8 + rows[0] * tile->line * 8), row1 = (uint32_t)(tile->tmem * 8 + rows[1] * tile->line * 8);
    uint32_t odd0 = (rows[0] & 1) ? 4 : 0, odd1 = (rows[1] & 1) ? 4 : 0;
    int sd = cols[1] - cols[0], uv1 = cols[0] + 2 * sd;
    IC t0 = yuv_texel(row0, cols[0], cols[0], odd0), t1 = yuv_texel(row0, cols[1], uv1, odd0);
    IC t2 = yuv_texel(row1, cols[0], cols[0], odd1), t3 = yuv_texel(row1, cols[1], uv1, odd1);
    int fr = (fs >> 1) | ((s0 & 1) << 4), up = (fs + ft) & 0x20, upr = (fr + ft) & 0x20, o[4];
    if (!bilerp) { /* the convert stage takes the corner each half falls nearest */
        const IC *y = up ? &t3 : &t0, *c = upr ? &t3 : &t0;
        return (IC){c->r, c->g, y->b, y->a};
    }
    int mid = filter == 3, centre = mid && fs == 16 && ft == 16, centrerg = mid && fr == 16 && ft == 16;
    const int *A = &t0.r, *B = &t1.r, *C = &t2.r, *D = &t3.r;
    for (int k = 0; k < 4; k++) {
        int f = k < 2 ? fr : fs, u = k < 2 ? upr : up, c = k < 2 ? centrerg : centre;
        int a = SIGN9(A[k]), b = SIGN9(B[k]), cc_ = SIGN9(C[k]), d = SIGN9(D[k]);
        if (k >= 2) { a = A[k]; b = B[k]; cc_ = C[k]; d = D[k]; }
        if (c) o[k] = d + ((((b + cc_) << 6) - (d << 7) + ((~d + a) << 6) + 0xC0) >> 8);
        else if (u) o[k] = d + (((32 - f) * (cc_ - d) + (32 - ft) * (b - d) + 0x10) >> 5);
        else o[k] = a + ((f * (b - a) + ft * (cc_ - a) + 0x10) >> 5);
    }
    return (IC){o[0] & 0x1FF, o[1] & 0x1FF, o[2] & 0x1FF, o[3] & 0x1FF};
}
/* The convert stage (G_TC_CONV): YUV to RGB by G_SETCONVERT's K0 to K3, R = Y + K0 V, G = Y + K1 U + K2 V,
   B = Y + K3 U, alpha Y, each kept in 9 bits; the combiner finishes it with K4 and K5. A texel that isn't YUV goes
   through it as well (its red as U, green as V, blue as Y), as on the console. */
static int conv_k[4];
static HOT IC yuv_convert(IC t) {
    int u = SIGN9(t.r), v = SIGN9(t.g), y = SIGN9(t.b);
    return (IC){(y + ((conv_k[0] * v + 0x80) >> 8)) & 0x1FF, (y + ((conv_k[1] * u + conv_k[2] * v + 0x80) >> 8)) & 0x1FF,
                (y + ((conv_k[3] * u + 0x80) >> 8)) & 0x1FF, y & 0x1FF};
}
static HOT Color from_ic(IC c) { return (Color){by511[c.r & 0x1FF], by511[c.g & 0x1FF], by511[c.b & 0x1FF], by511[c.a & 0x1FF]}; }
static HOT Color sample_cycle(const Tile *tile, float s, float t, int bilerp);
/* A texel of a tile at (s, t) for the texture pipeline's cycle: TEXEL0 the first, TEXEL1 the second. Other mode H's
   texture convert bits say per cycle whether it filters (bi_lerp 0 and 1) or converts, and whether the second
   converts the first's result (convert_one: G_TC_FILTCONV, the first filtering a YUV image and the second making it
   RGB). G_TC_FILT, which nearly every game sets, filters in both. */
static HOT Color sample(const Tile *tile, float s, float t, int cycle, const Color *prev) {
    int bits = (int)((mode_h >> 9) & 7), bilerp = cycle ? (bits >> 1) & 1 : (bits >> 2) & 1;
    if (cycle && (bits & 1) && prev) {
        IC p = TOI(*prev);
        return bilerp ? from_ic((IC){p.b, p.b, p.b, p.b}) : from_ic(yuv_convert(p)); /* filtering by prev: unmodelled */
    }
    return sample_cycle(tile, s, t, bilerp);
}
static HOT Color sample_cycle(const Tile *tile, float s, float t, int bilerp) {
    int filter = (int)((mode_h >> 12) & 3);
    int s32 = (int)floorf(s * 32.0f), t32 = (int)floorf(t * 32.0f), s0 = s32 >> 5, t0 = t32 >> 5;
    int yuv = tile->fmt == 1 && tile->siz == 2;
    if (!(filter & 2) || cycle_type() >= 2) {
        Color c = texel(tile, s0, t0);
        return bilerp || cycle_type() >= 2 ? c : from_ic(yuv_convert(TOI(c)));
    }
    int fs = s32 & 31, ft = t32 & 31;
    /* the two columns and two rows, wrapped once each (as texel() wraps them) for the three or four texels */
    int w = (int)(tile->lrs - tile->uls) + 1, h = (int)(tile->lrt - tile->ult) + 1;
    int si = s0 - (int)tile->uls, ti = t0 - (int)tile->ult;
    int cols[2] = {wrapped(si, w, tile->cms, tile->masks), wrapped(si + 1, w, tile->cms, tile->masks)};
    int rows[2] = {wrapped(ti, h, tile->cmt, tile->maskt), wrapped(ti + 1, h, tile->cmt, tile->maskt)};
    if (yuv) { IC c = yuv_sample(tile, cols, rows, fs, ft, s0, filter, bilerp); return from_ic(bilerp ? c : yuv_convert(c)); }
    if (!bilerp) { /* converted unfiltered: the corner of the point's triangle */
        int up = fs + ft >= 32;
        return from_ic(yuv_convert(TOI(texel_wrapped(tile, cols[up], rows[up]))));
    }
    Color c10 = texel_wrapped(tile, cols[1], rows[0]), c01 = texel_wrapped(tile, cols[0], rows[1]);
    IC b = TOI(c10), c = TOI(c01), out;
    if ((filter & 1) && fs == 16 && ft == 16) { /* average, at the texel's centre: the four texels' mean */
        IC a = TOI(texel_wrapped(tile, cols[0], rows[0])), d = TOI(texel_wrapped(tile, cols[1], rows[1]));
        out = (IC){(a.r + b.r + c.r + d.r + 2) >> 2, (a.g + b.g + c.g + d.g + 2) >> 2,
                   (a.b + b.b + c.b + d.b + 2) >> 2, (a.a + b.a + c.a + d.a + 2) >> 2};
    } else if (fs + ft < 32) { /* bilinear reads only the corner on the point's side */
        IC a = TOI(texel_wrapped(tile, cols[0], rows[0]));
        out = (IC){LERP3(a.r, b.r, c.r, fs, ft), LERP3(a.g, b.g, c.g, fs, ft), LERP3(a.b, b.b, c.b, fs, ft), LERP3(a.a, b.a, c.a, fs, ft)};
    } else {
        IC d = TOI(texel_wrapped(tile, cols[1], rows[1]));
        int gs = 32 - fs, gt = 32 - ft;
        out = (IC){LERP3(d.r, c.r, b.r, gs, gt), LERP3(d.g, c.g, b.g, gs, gt), LERP3(d.b, c.b, b.b, gs, gt), LERP3(d.a, c.a, b.a, gs, gt)};
    }
    return (Color){by255[out.r], by255[out.g], by255[out.b], by255[out.a]};
}

/* ---------------------------------------------------------------- the colour combiner */

/* What one pixel's combiner sees: the texels of the two tiles (TEXEL0, TEXEL1), the level of detail's fraction,
   the shade, and the noise. */
typedef struct { Color t0, t1; float lod; } Texels;
typedef struct { Color sh; Texels tx; float z; } PixIn;
/* Two cycles, as the RDP pipelines them (angrylion's renderer models it): a pixel's second cycle runs alongside the
   next pixel's first, so its combiner's second cycle sees the next pixel's TEXEL0 as TEXEL1, its alpha compare
   the next pixel's first-cycle alpha, and its blender's first cycle the colour read from the previous pixel's place
   in memory (the second its own). The walker sets these for each pixel of a span; NULL outside it. */
static const PixIn *two_next;
static int two_next_cvg;
static Color two_mem_prev;
static Texels one_texel(Color c) { return (Texels){c, c, 0}; }
static float prim_lod_frac, k4, k5;
static int prim_min_level;
static Color key_center, key_scale;
static int key_width[3]; /* G_SETKEYR / G_SETKEYGB: each channel's width, 12 bits (4.8) */
static int key_alpha;    /* the chroma key's alpha for the pixel combiner() last made (other mode H's KEY_EN) */
static uint32_t noise_seed = 0x12345678u;
static HOT float noise(void) { noise_seed = noise_seed * 1664525u + 1013904223u; return by255[noise_seed >> 24]; }

/* The combiner's selectors, decoded once per change of its settings (combiner_uses): [cycle][A, B, C, D]. */
static int sel_rgb[2][4], sel_alpha[2][4];

/* Where each selected input is read from, also once per change of the settings: a slot of `cc` (RGB) or `cca`
   (alpha). The first slots are the pixel's own (filled per pixel, only those its settings read: `cc_need`), the
   rest the primitive's (prim, env, the key...), filled per primitive (combiner_uses). A switch per input per pixel
   picking them was an eighth of a racing game's frame. */
enum { CC_COMB, CC_T0, CC_T1, CC_SHADE, CC_NOISE, CC_COMB_A, CC_T0_A, CC_T1_A, CC_SHADE_A, CC_LOD, /* per pixel */
       CC_PRIM, CC_ENV, CC_KEY_CENTER, CC_KEY_SCALE, CC_ONE, CC_K4, CC_ZERO, CC_PRIM_A, CC_ENV_A, CC_PRIM_LOD, CC_K5,
       CC_SLOTS };
enum { CA_COMB, CA_T0, CA_T1, CA_SHADE, CA_LOD, /* per pixel */ CA_PRIM, CA_ENV, CA_PRIM_LOD, CA_ONE, CA_ZERO, CA_SLOTS };
/* The combiner works in the RDP's arithmetic: 8-bit inputs, the constant 1 being 256 (so 255 is 255/256 of it). */
#define IG(v) ((IC){(v), (v), (v), (v)})
static IC cc[CC_SLOTS];
static int cca[CA_SLOTS];
static uint8_t cc_slot[2][4], cca_slot[2][4];
static uint32_t cc_need[2]; /* per cycle: bit n, RGB slot n; bit 16 + n, alpha slot n */

/* The RGB inputs: A (4 bits), B (4), C (5) or D (3), as G_SETCOMBINE lays them out; 6 and 7 differ by position
   (1, the chroma key's centre or scale; noise, K4, the combined alpha, 0), and only C reaches the alphas and the
   LOD fractions. */
static int rgb_slot(int which, int sel) {
    static const uint8_t c_only[16] = {[8] = CC_T0_A, [9] = CC_T1_A, [10] = CC_PRIM_A, [11] = CC_SHADE_A, [12] = CC_ENV_A,
                                       [13] = CC_LOD, [14] = CC_PRIM_LOD, [15] = CC_K5};
    switch (sel) {
    case 0: return CC_COMB; case 1: return CC_T0; case 2: return CC_T1; case 3: return CC_PRIM;
    case 4: return CC_SHADE; case 5: return CC_ENV;
    case 6: return which == 1 ? CC_KEY_CENTER : which == 2 ? CC_KEY_SCALE : CC_ONE;
    case 7: return which == 0 ? CC_NOISE : which == 1 ? CC_K4 : which == 2 ? CC_COMB_A : CC_ZERO;
    }
    return sel < 16 && which == 2 ? c_only[sel] : CC_ZERO;
}
/* The alpha inputs: A, B and D share one list; C has the level of detail's fractions in place of the combined
   alpha and 1. */
static int alpha_slot(int which, int sel) {
    switch (sel) {
    case 0: return which == 2 ? CA_LOD : CA_COMB; case 1: return CA_T0; case 2: return CA_T1; case 3: return CA_PRIM;
    case 4: return CA_SHADE; case 5: return CA_ENV; case 6: return which == 2 ? CA_PRIM_LOD : CA_ONE;
    }
    return CA_ZERO;
}

/* What the combiner's settings read, worked out once per change of them: whether any input is TEXEL1 (or, in two
   cycles, the second cycle's TEXEL0, which is TEXEL1), and whether any is noise. Sampling a second tile and drawing
   noise for every pixel otherwise cost a racing game a third of its frame rate. Each primitive (the callers of
   this) also gets its constant inputs into their slots. */
static uint32_t uses_key[3] = {~0u, ~0u, ~0u};
static int uses_t1, uses_noise;
#define grey(v) ((Color){(v), (v), (v), (v)})
static void combiner_uses(void) {
    cc[CC_PRIM] = TOI(prim); cc[CC_ENV] = TOI(env); cc[CC_KEY_CENTER] = TOI(key_center); cc[CC_KEY_SCALE] = TOI(key_scale);
    cc[CC_ONE] = IG(256); cc[CC_K4] = IG(C8(k4)); cc[CC_ZERO] = IG(0); cc[CC_PRIM_A] = IG(C8(prim.a));
    cc[CC_ENV_A] = IG(C8(env.a)); cc[CC_PRIM_LOD] = IG(C8(prim_lod_frac)); cc[CC_K5] = IG(C8(k5));
    cca[CA_PRIM] = C8(prim.a); cca[CA_ENV] = C8(env.a); cca[CA_PRIM_LOD] = C8(prim_lod_frac); cca[CA_ONE] = 256; cca[CA_ZERO] = 0;
    uint32_t cyc = (uint32_t)cycle_type();
    if (uses_key[0] == combine0 && uses_key[1] == combine1 && uses_key[2] == cyc) return;
    uses_key[0] = combine0; uses_key[1] = combine1; uses_key[2] = cyc;
    uses_t1 = uses_noise = 0;
    for (int cycle = 0; cycle < 2; cycle++) {
        int a, b, c, d, aa, ab, ac, ad;
        if (cycle == 0) {
            a = (combine0 >> 20) & 15; c = (combine0 >> 15) & 31; aa = (combine0 >> 12) & 7; ac = (combine0 >> 9) & 7;
            b = (combine1 >> 28) & 15; d = (combine1 >> 15) & 7; ab = (combine1 >> 12) & 7; ad = (combine1 >> 9) & 7;
        } else {
            a = (combine0 >> 5) & 15; c = combine0 & 31; aa = (combine1 >> 21) & 7; ac = (combine1 >> 18) & 7;
            b = (combine1 >> 24) & 15; d = (combine1 >> 6) & 7; ab = (combine1 >> 3) & 7; ad = combine1 & 7;
        }
        sel_rgb[cycle][0] = a; sel_rgb[cycle][1] = b; sel_rgb[cycle][2] = c; sel_rgb[cycle][3] = d;
        sel_alpha[cycle][0] = aa; sel_alpha[cycle][1] = ab; sel_alpha[cycle][2] = ac; sel_alpha[cycle][3] = ad;
        cc_need[cycle] = 0;
        for (int w = 0; w < 4; w++) {
            cc_slot[cycle][w] = (uint8_t)rgb_slot(w, sel_rgb[cycle][w]);
            cca_slot[cycle][w] = (uint8_t)alpha_slot(w, sel_alpha[cycle][w]);
            if (cc_slot[cycle][w] < CC_PRIM) cc_need[cycle] |= 1u << cc_slot[cycle][w];
            if (cca_slot[cycle][w] < CA_PRIM) cc_need[cycle] |= 1u << (16 + cca_slot[cycle][w]);
        }
        /* one cycle runs only the second cycle's settings, two cycles both, the second with the texels swapped */
        if (cyc == 0 ? cycle == 0 : cyc != 1) continue;
        int t0_in_second = cyc == 1 && cycle == 1 && (a == 1 || b == 1 || c == 1 || c == 8 || d == 1 || aa == 1 || ab == 1 || ac == 1 || ad == 1);
        if (a == 2 || b == 2 || c == 2 || c == 9 || d == 2 || aa == 2 || ab == 2 || ac == 2 || ad == 2 || t0_in_second)
            uses_t1 = 1;
        if (a == 7) uses_noise = 1;
    }
}
#define clamp01(v) ((v) < 0 ? 0.0f : (v) > 1 ? 1.0f : (v)) /* macros: the host is built without inlining */
/* One cycle of the combiner, its pixel's inputs given: (A - B) * C + D for colour and alpha, as the RDP computes
   it: rounded at the 8th bit and kept in 9 bits, C a signed 9-bit input. Between two cycles the first's result
   passes on as it is; the last is clamped to 0..255, 9-bit values from 256 up to 383 as 255 and the rest
   (negatives) as 0. Computed in floats with 255 as 1, a combined colour was a step off the console's on most of a
   surface: a texel by a shade of 255 came out a step brighter. */
#define EXT9(v) ((((v) & 0x180) == 0x180) ? ((v) | ~0x1FF) : ((v) & 0x1FF))
#define CLAMP9(v) ((((v) & 0x1FF) & 0x100) ? ((((v) & 0x1FF) & 0x80) ? 0 : 255) : ((v) & 0xFF))
#define CC_EQ(a, b, c, d) ((((EXT9(a) - EXT9(b)) * SIGN9(c) + (EXT9(d) << 8) + 0x80) >> 8) & 0x1FF)
static HOT IC combine(int cycle, IC comb, IC t0, IC t1, IC shade, int lod, int nz) {
    uint32_t need = cc_need[cycle];
    if (need & (1u << CC_COMB)) cc[CC_COMB] = comb;
    if (need & (1u << CC_T0)) cc[CC_T0] = t0;
    if (need & (1u << CC_T1)) cc[CC_T1] = t1;
    if (need & (1u << CC_SHADE)) cc[CC_SHADE] = shade;
    if (need & (1u << CC_NOISE)) cc[CC_NOISE] = IG(nz);
    if (need & (1u << CC_COMB_A)) cc[CC_COMB_A] = IG(comb.a);
    if (need & (1u << CC_T0_A)) cc[CC_T0_A] = IG(t0.a);
    if (need & (1u << CC_T1_A)) cc[CC_T1_A] = IG(t1.a);
    if (need & (1u << CC_SHADE_A)) cc[CC_SHADE_A] = IG(shade.a);
    if (need & (1u << CC_LOD)) cc[CC_LOD] = IG(lod);
    cca[CA_COMB] = comb.a; cca[CA_T0] = t0.a; cca[CA_T1] = t1.a; cca[CA_SHADE] = shade.a; cca[CA_LOD] = lod;
    const uint8_t *r = cc_slot[cycle], *al = cca_slot[cycle];
    IC A = cc[r[0]], B = cc[r[1]], C = cc[r[2]], D = cc[r[3]];
    int aa = cca[al[0]], ab = cca[al[1]], ac = cca[al[2]], ad = cca[al[3]];
    return (IC){CC_EQ(A.r, B.r, C.r, D.r), CC_EQ(A.g, B.g, C.g, D.g), CC_EQ(A.b, B.b, C.b, D.b), CC_EQ(aa, ab, ac, ad)};
}
/* Both cycles as the RDP runs them. One cycle runs the second cycle's settings, as angrylion's renderer (built from
   the hardware) does: libultra's one-cycle modes set both alike, but a list that leaves the second cycle as another
   mode drew with the wrong one when the first was read here. (The blender's one cycle is its first.) In two cycles
   the second sees the texels swapped: its TEXEL0 is the first cycle's TEXEL1 and its TEXEL1 the next pixel's
   TEXEL0 (two_next; this one's outside a triangle's span). */
static HOT Color combiner(const Texels *tx, Color shade) {
    int nz = uses_noise ? C8(noise()) : 0, lod = C8(tx->lod);
    IC none = {0, 0, 0, 0}, t0 = TOI(tx->t0), t1 = uses_t1 ? TOI(tx->t1) : t0, sh = TOI(shade), out;
    if (cycle_type() != 1) out = combine(cycle_type() == 0, none, t0, t1, sh, lod, nz);
    else out = combine(1, combine(0, none, t0, t1, sh, lod, nz), t1, two_next ? TOI(two_next->tx.t0) : t0, sh, lod, nz);
    if (mode_h & 0x100) { /* chroma key: the last cycle's A passes as the colour, and how far (A - B) * C + D lies
        from the key (B its centre, C its scale) against each channel's width is the alpha, the least of the three */
        const uint8_t *r = cc_slot[1];
        IC A = cc[r[0]], B = cc[r[1]], C = cc[r[2]], D = cc[r[3]];
        int ch[3][4] = {{A.r, B.r, C.r, D.r}, {A.g, B.g, C.g, D.g}, {A.b, B.b, C.b, D.b}}, least = 0x7FFFFFFF;
        for (int k = 0; k < 3; k++) {
            int v = ((EXT9(ch[k][0]) - EXT9(ch[k][1])) * SIGN9(ch[k][2]) + (EXT9(ch[k][3]) << 8) + 0x80) & 0x1FFFF;
            if (v & 0x10000) v |= ~0x1FFFF; /* signed 17 bits */
            if (v > 0) v = (v & 15) == 8 ? 16 - v : -v;
            v += key_width[k] << 4;
            if (v < least) least = v;
        }
        key_alpha = least < 0 ? 0 : least > 255 ? 255 : least;
        out.r = A.r; out.g = A.g; out.b = A.b;
    }
    return (Color){by255[CLAMP9(out.r)], by255[CLAMP9(out.g)], by255[CLAMP9(out.b)], by255[CLAMP9(out.a)]};
}

/* ---------------------------------------------------------------- the frame and depth images */

static HOT int in_frame(int x, int y) {
    return x >= scissor[0] && y >= scissor[1] && x < scissor[2] && y < scissor[3] && x >= 0 && y >= 0 && x < cimg_width && y < frame_height();
}
static HOT uint32_t pixel_addr(int x, int y) {
    uint32_t bytes = cimg_siz == 3 ? 4 : cimg_siz == 1 ? 1 : 2;
    return 0x80000000u | ((cimg_addr + (uint32_t)(y * cimg_width + x) * bytes) & 0x1FFFFFFF);
}
/* A pixel as the blender reads it back: a 16-bit one's channels as 5 bits shifted up 3 (not stretched to 255),
   and its alpha the coverage kept with it (0 to 7, shifted up 5). */
static HOT Color read_pixel(uint32_t a) {
    if (cimg_siz == 3) { uint32_t v = rd32(a); return (Color){by255[v >> 24], by255[(v >> 16) & 255], by255[(v >> 8) & 255], by255[v & 0xE0]}; }
    if (cimg_siz == 1) return grey(by255[R[a]]);
    uint16_t v = rd16(a);
    return (Color){by255[((v >> 11) & 31) << 3], by255[((v >> 6) & 31) << 3], by255[((v >> 1) & 31) << 3],
                   by255[(cvg_get(a) - 1) << 5]};
}

/* The RDP's ordered dither, added to each 8-bit channel before it's cut to 5 bits: the magic square or Bayer
   matrix by the pixel's place, or noise, as other mode H's RGB dither asks (G_CD_*, bits 6 and 7). Without it,
   smooth gradients (skies, fades) showed bands the console doesn't. */
static const uint8_t magic_square[16] = {0, 6, 1, 7, 4, 2, 5, 3, 3, 5, 2, 4, 7, 1, 6, 0};
static const uint8_t bayer[16] = {0, 4, 1, 5, 4, 0, 5, 1, 3, 7, 2, 6, 7, 3, 6, 2};
static HOT int dither_at(int x, int y) {
    switch ((mode_h >> 6) & 3) {
    case 0: return magic_square[(y & 3) * 4 + (x & 3)];
    case 1: return bayer[(y & 3) * 4 + (x & 3)];
    case 2: return (int)(noise() * 7.99f);
    }
    return 0;
}
/* The alpha dither (other mode H bits 4 and 5) added to the combined alpha and to the shade alpha the blender
   sees, capped at 255: the colour's matrix value (the magic square's, or Bayer's when the colour is dithered by
   Bayer, noise or not at all), its inverse, noise, or none. libultra's default is the pattern, so most games blend
   by an alpha up to 7 above the combiner's. */
static HOT int alpha_dither_at(int x, int y) {
    int rgb = (int)(mode_h >> 6) & 3, al = (int)(mode_h >> 4) & 3;
    if (al == 3) return 0;
    if (al == 2) return (int)(noise() * 7.99f);
    int v = (rgb == 0 || rgb == 2 ? magic_square : bayer)[(y & 3) * 4 + (x & 3)];
    return al == 1 ? 7 - v : v;
}
/* To 5 bits as the RDP rounds: up to the next step when the 3 bits cut off are above the dither value (7, never,
   without dither). Adding the dither value and truncating rounded by the mirror image of the matrix. */
#define TO5(v, d) (C8(v) > 247 ? 31 : (C8(v) >> 3) + ((C8(v) & 7) > (d)))
static HOT void write_pixel(int x, int y, uint32_t a, Color c, int dither) {
    if (cimg_siz == 3) {
        uint32_t v = (uint32_t)(clamp01(c.r) * 255 + 0.5f) << 24 | (uint32_t)(clamp01(c.g) * 255 + 0.5f) << 16
                   | (uint32_t)(clamp01(c.b) * 255 + 0.5f) << 8 | (uint32_t)(clamp01(c.a) * 255 + 0.5f);
        R[a] = (uint8_t)(v >> 24); R[a + 1] = (uint8_t)(v >> 16); R[a + 2] = (uint8_t)(v >> 8); R[a + 3] = (uint8_t)v;
        return;
    }
    if (cimg_siz == 1) { R[a] = (uint8_t)(clamp01(c.r) * 255 + 0.5f); return; }
    int d = !dither || ((mode_h >> 6) & 3) == 3 ? 7 : ((mode_h >> 6) & 3) == 0 ? magic_square[(y & 3) * 4 + (x & 3)]
          : dither_at(x, y);
    uint16_t v = (uint16_t)(TO5(c.r, d) << 11 | TO5(c.g, d) << 6 | TO5(c.b, d) << 1 | 1);
    v = (uint16_t)(v >> 8 | v << 8);
    memcpy(R + a, &v, 2);
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
/* Delta z as the RDP stores it: the power of two's exponent, of the delta in its own depth units (an eighth of
   these 18-bit ones). Counted in these, every stored delta was three powers too large. */
static HOT int dz_code(float dz) {
    static float last_dz = -1; static int last_dzc; /* one triangle's pixels share their delta z */
    if (dz != last_dz) { /* its highest bit, in units of 8 (delta z 3 is code 1) */
        last_dz = dz; last_dzc = 0;
        int raw = (int)(dz / 8);
        while (last_dzc < 15 && (raw >> (last_dzc + 1))) last_dzc++;
    }
    return last_dzc;
}
static HOT uint16_t z_encode(float z, float dz) {
    uint32_t zi = z <= 0 ? 0 : z >= 0x3FFFF ? 0x3FFFF : (uint32_t)z;
    /* the exponent: the last range whose base it reaches, which is how many of its 18 bits lead with 1 (up to 7) */
    uint32_t e = (uint32_t)__builtin_clz(~(zi << 14));
    if (e > 7) e = 7;
    uint32_t m = ((zi - z_ranges[e].base) >> z_ranges[e].shift) & 0x7FF;
    return (uint16_t)(e << 13 | m << 2 | (uint32_t)(dz_code(dz) >> 2));
}
static float zbuf_private[MAXW * MAXH];
#define z_in_memory() (zimg_addr != 0)
#define z_addr(x, y) (0x80000000u | ((zimg_addr + (uint32_t)((y) * cimg_width + (x)) * 2) & 0x1FFFFFFF))
static HOT float z_read(int x, int y, float *dz) {
    if (!z_in_memory()) { *dz = 0; return zbuf_private[y * MAXW + x]; }
    uint32_t a = z_addr(x, y);
    uint16_t v = (uint16_t)(R[a] << 8 | R[a + 1]), e = v >> 13;
    *dz = (float)(8 << (((v & 3) << 2) | dz_hole[(a & 0x7FFFFF) >> 1]));
    return (float)(z_ranges[e].base + ((uint32_t)((v >> 2) & 0x7FF) << z_ranges[e].shift));
}
static HOT void z_write(int x, int y, float z, float dz) {
    if (!z_in_memory()) { zbuf_private[y * MAXW + x] = z; return; }
    uint32_t a = z_addr(x, y);
    wr16(a, z_encode(z, dz));
    dz_hole[(a & 0x7FFFFF) >> 1] = (uint8_t)(dz_code(dz) & 3);
}
static float prim_z, prim_dz; /* G_SETPRIMDEPTH, for G_ZS_PRIM */

/* ---------------------------------------------------------------- the blender */

/* The blender, one cycle of it: P * A + M * B, each picked by other mode L (the first cycle's at bits 30, 26, 22 and
   18, the second's at 28, 24, 20 and 16). P and M: the pixel coming in, the frame buffer's, the blend colour or the
   fog colour; A: the incoming alpha, the fog colour's, the shade's (the RSP's fog alpha under G_FOG) or 0; B: 1 - A,
   the frame buffer's alpha, 1 or 0. Alpha passes through. A racing game fogs most of a race in the first cycle
   (fog * shade alpha + in * (1 - shade alpha)); without it, every race was drawn without its haze. */
/* P or M: the pixel coming in, the frame buffer's, the blend colour or the fog colour. (Picked, not indexed in an
   array: compiled to WebAssembly, the array was built in memory for every pixel.) */
#define PICK(n, in, mem) ((n) == 0 ? (in) : (n) == 1 ? (mem) : (n) == 2 ? blend : fog)
/* In the RDP's arithmetic: 8-bit colours, A and B cut to 5 bits, B counted one more (1 - A being 255 - A, and 1
   being 255), the sum shifted down 5; or, blending an anti-aliased edge without force blend (`divide`), divided by
   the weights' sum (the RDP's own divider: blender_divide). In floats, a translucent surface was a step
   off the console's across a sixth of its pixels. */
/* The RDP's blender divider: n (11 bits) by d (4 bits), eight quotient bits worked out one at a time from a 3-bit
   running remainder, adding d or its complement by the last bit. For d up to 8 it's n / d; past that (two heavy
   weights) its own results, which an integer division didn't give. */
static uint8_t blend_div[16][0x800];
static HOT int blender_divide(int n, int d) {
    static int ready;
    if (!ready) {
        for (int dd = 0; dd < 16; dd++)
            for (int nn = 0; nn < 0x800; nn++) {
                int inv = ~dd & 15, q = 0, part = (inv + (nn >> 8) + 1) & 7;
                for (int k = 0; k < 8; k++) {
                    int bit = (nn >> (7 - k)) & 1;
                    int sum = (q & (0x100 >> k)) ? inv + (part << 1) + bit + 1 : dd + (part << 1) + bit;
                    part = sum & 7;
                    if (sum & 0x10) q |= 1 << (7 - k);
                }
                blend_div[dd][nn] = (uint8_t)q;
            }
        ready = 1;
    }
    return blend_div[d & 15][n & 0x7FF];
}
/* With the frame's alpha (its coverage) as B, the RDP first shifts the two weights by how the primitive's delta z
   compares with the stored one (blend_shift, set per pixel by plot): A down by how much larger the new one is, kept
   to 4-step multiples, and B down by how much larger the stored one is, its low bits set. */
static int blend_shift_a, blend_shift_b;
static HOT Color blender_cycle(int cycle, Color in, Color mem, Color shade, int divide) {
    int at = cycle == 0 ? 30 : 28;
    int p = (int)(mode_l >> at) & 3, a = (int)(mode_l >> (at - 4)) & 3;
    int m = (int)(mode_l >> (at - 8)) & 3, b = (int)(mode_l >> (at - 12)) & 3;
    int A = a == 0 ? C8(in.a) : a == 1 ? C8(fog.a) : a == 2 ? C8(shade.a) : 0;
    int B = b == 0 ? 255 - A : b == 1 ? C8(mem.a) : b == 2 ? 255 : 0;
    int a5 = A >> 3, b5 = B >> 3;
    if (b == 1) { a5 = (a5 >> blend_shift_a) & 0x3C; b5 = (b5 >> blend_shift_b) | 3; }
    int mulb = b5 + 1, sum = (a5 & ~3) + (b5 & ~3) + 4;
    Color P = PICK(p, in, mem), M = PICK(m, in, mem);
    int r = C8(P.r) * a5 + C8(M.r) * mulb, g = C8(P.g) * a5 + C8(M.g) * mulb, bl = C8(P.b) * a5 + C8(M.b) * mulb;
    if (divide) { r = blender_divide(r >> 2, sum >> 2); g = blender_divide(g >> 2, sum >> 2); bl = blender_divide(bl >> 2, sum >> 2); }
    else { r >>= 5; g >>= 5; bl >>= 5; }
    return (Color){by255[r & 0xFF], by255[g & 0xFF], by255[bl & 0xFF], in.a};
}
/* Both cycles as the RDP runs them: in two-cycle mode the first always blends (fog); the last blends when blending
   is on (force blend, or an anti-aliased edge on the same surface) and otherwise passes its P through, as it does
   when it would mix IN by its alpha with MEM by one minus it and that alpha is full (the RDP's partial reject: a
   full alpha's 31/32 weight would let a 32nd of MEM through). `shade1` is the shade the last cycle sees: in two
   cycles, the next pixel's (the pipeline has moved on; two_next). */
static HOT Color blender(Color in, Color mem, Color shade, Color shade1, int blend_en) {
    int two = cycle_type() == 1;
    Color out = two ? blender_cycle(0, in, two_next ? two_mem_prev : mem, shade, 0) : in;
    int last = two ? 1 : 0, at = last ? 28 : 30;
    int reject = ((mode_l >> (at - 4)) & 3) == 0 && ((mode_l >> (at - 12)) & 3) == 0 && C8(in.a) >= 255;
    if (blend_en && !reject) return blender_cycle(last, out, mem, two ? shade1 : shade, !(mode_l & 0x4000));
    Color passed = PICK((mode_l >> (last ? 28 : 30)) & 3, out, mem);
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
/* The RDP's depth test, by other mode L's z mode. Each side's delta z (a power of two) gives how far apart two depths
   may be and still be one surface; the stored one is widened where depths are finest (its exponent below 3). A
   pixel the primitive covers fully on top of a fully covered one (their coverages overflow) passes only when it's
   nearer; a partly covered one (an anti-aliased edge) passes when it's within delta z of nearer, and blends with
   what's there when it's that close (`same`). Opaque and interpenetrating surfaces pass by those rules or over the
   farthest depth; transparent ones when nearer; decals only within delta z on both sides (a shadow, a mark on a
   wall). Passing anything within delta z of nearer, as this did, let the farther of two crossing triangles show
   along their crossing. Interpenetrating surfaces differ from opaque ones where a fully covering pixel is in front
   but within delta z: it passes with its coverage scaled by how far in front it is (in delta z's units, as the
   console's 4 bits keep it), so where two surfaces cross the nearer one fades in. Taken as opaque, the edge of a
   racing game's kart drew one pixel the console leaves (its coverage scaled to none). */
static HOT int depth_passes(float z, float dz, float mem_z, float mem_dz, int overflow, int *same, int *cvg) {
    int e = 0;
    while (e < 7 && mem_z >= (float)z_ranges[e + 1].base) e++;
    int raw_mem = (int)(mem_dz / 8), coplanar = 0;
    if (raw_mem < 1) raw_mem = 1;
    if (e < 3) {
        if (raw_mem != 0x8000) { raw_mem <<= 1; if (raw_mem < (16 >> e)) raw_mem = 16 >> e; }
        else { coplanar = 1; raw_mem = 0xFFFF; }
    }
    int both = raw_mem | (int)(dz / 8), top = 1, exp = 0;
    while (top * 2 <= both) { top <<= 1; exp++; }
    float dzmax = (float)(top * 8);
    int farther = coplanar || z + dzmax >= mem_z, nearer = coplanar || z - dzmax <= mem_z, max = mem_z >= 0x3FFFF;
    *same = farther;
    switch ((mode_l >> 10) & 3) {
    case 1: /* interpenetrating */
        if (z < mem_z && farther && overflow) {
            int zi = z <= 0 ? 0 : z >= 0x3FFFF ? 0x3FFFF : (int)z, coeff = (((int)mem_z >> exp) - (zi >> exp)) & 15;
            *cvg = (coeff * *cvg >> 3) & 15;
            return 1;
        }
        return max || (overflow ? z < mem_z : nearer);
    case 2: return z < mem_z || max;              /* transparent */
    case 3: return farther && nearer && !max;     /* decal */
    default: return max || (overflow ? z < mem_z : nearer);
    }
}
/* The colour the blender reads at a pixel (black and fully covered without IMAGE_READ). Outside the scissor too: a
   two-cycle span's first pixel can lie past it (its right edge, the span drawn right to left), and the RDP reads
   memory there, the next row's first pixel, which the next pixel's first blender cycle mixes in. Read as black,
   the column at the scissor's edge came out a step off the console's. */
static HOT Color memory_at(int x, int y) {
    if (!(mode_l & 0x40)) return (Color){0, 0, 0, by255[224]};
    uint32_t a = pixel_addr(x, y);
    if ((a & 0x1FFFFFFF) + 4 > 0x800000) return (Color){0, 0, 0, by255[224]};
    return read_pixel(a);
}
static HOT void plot(int x, int y, float z, Color shade, const Texels *tx, int depth, int cvg, float dz) {
    if (!in_frame(x, y)) return;
    int zcmp = (mode_l & 0x10) != 0, zupd = (mode_l & 0x20) != 0;
    int aa = (mode_l & 0x8) != 0, force = (mode_l & 0x4000) != 0, same_surface = 1;
    if (mode_l & 4) { z = prim_z; dz = prim_dz; depth = 1; } /* G_ZS_PRIM */
    float mem_z = 0, mem_dz = 0;
    if (depth && (zcmp || zupd)) mem_z = z_read(x, y, &mem_dz);
    /* the frame's pixel and coverage, read only with IMAGE_READ (without it: black, fully covered) */
    uint32_t a = pixel_addr(x, y);
    int imrd = (mode_l & 0x40) != 0, memcvg = imrd ? cvg_get(a) - 1 : 7;
    /* in the RDP's order: the combiner, the alpha dither, the coverage by the alpha and as the alpha, then the
       depth test (with that coverage), then the alpha compare. Comparing alpha first and testing depth before the
       combiner, a cut-out's edge pixels (leaves, fences) came out unlike the console's. */
    Color c = combiner(tx, shade);
    /* the combined alpha, 255 counting as 256; the coverage by it (CVG_X_ALPHA) before the alpha dither is added
       (and capped at 255). Dithered first, and with 255 as 255, five samples by full alpha came out four. */
    int alpha = C8(c.a), adith = alpha_dither_at(x, y);
    if (alpha == 255) alpha = 256;
    int scaled = (alpha * cvg + 4) >> 3;
    if (mode_l & 0x1000) cvg = (scaled >> 5) & 15;
    if (!(mode_l & 0x2000)) c.a = (mode_h & 0x100) ? by255[key_alpha] : by255[alpha + adith > 255 ? 255 : alpha + adith];
    if (adith) { int sa = C8(shade.a) + adith; shade.a = by255[sa > 255 ? 255 : sa]; }
    /* Coverage, as the RDP keeps it with each pixel (CVG_DST: clamp, wrap, zap or save), and an anti-aliased edge
       blended with what's there when it's the same surface and the two coverages don't overflow: through the
       blender, divided by its weights. Weighting the two colours by their coverages instead, as this did, put an
       edge a step or more off the console's. */
    if (mode_l & 0x2000) /* ALPHA_CVG_SEL: the coverage as the alpha */
        c.a = by255[(mode_l & 0x1000) ? (scaled > 255 ? 255 : scaled) : cvg >= 8 ? 255 : cvg << 5];
    int overflow = memcvg + cvg >= 8; /* (before the depth test scales an interpenetrating pixel's coverage) */
    if (depth && zcmp && !depth_passes(z, dz, mem_z, mem_dz, overflow, &same_surface, &cvg)) return;
    int ac = (int)(mode_l & 3), compared = C8(c.a);
    if (ac && two_next) { /* two cycles: the next pixel's first-cycle alpha, by its coverage, this one's dither */
        IC nx = combine(0, (IC){0, 0, 0, 0}, TOI(two_next->tx.t0), TOI(two_next->tx.t1), TOI(two_next->sh), C8(two_next->tx.lod), 0);
        int na = CLAMP9(nx.a);
        if (na == 255) na = 256;
        if (!(mode_l & 0x2000)) { na += adith; if (na & 0x100) na = 255; }
        else { na = (mode_l & 0x1000) ? (na * two_next_cvg + 4) >> 3 : two_next_cvg << 5; if (na > 255) na = 255; }
        compared = na;
    }
    if (ac == 1 && compared < C8(blend.a)) return;   /* alpha compare: at or above the blend colour's alpha */
    if (ac == 3 && compared < C8(noise())) return;   /* or a random threshold (G_AC_DITHER) */
    if (aa && !cvg) return;
    Color mem = imrd ? read_pixel(a) : (Color){0, 0, 0, by255[224]};
    { /* the blender's delta-z shifts (blender_cycle) */
        int code = dz_code(dz), mem_code = 0;
        while (mem_code < 15 && (float)(8 << mem_code) < mem_dz) mem_code++;
        if (depth && zcmp) {
            blend_shift_a = code - mem_code < 0 ? 0 : code - mem_code > 4 ? 4 : code - mem_code;
            blend_shift_b = mem_code - code < 0 ? 0 : mem_code - code > 4 ? 4 : mem_code - code;
        } else { blend_shift_a = 0; blend_shift_b = code < 11 ? 4 : 15 - code; }
    }
    int blend_en = force || (!overflow && aa && same_surface), final;
    switch ((mode_l >> 8) & 3) {
    case 0: final = blend_en ? cvg + memcvg : cvg - 1; final = final >= 8 ? 7 : final & 7; break;
    case 1: final = (cvg + memcvg) & 7; break;
    case 2: final = 7; break;
    default: final = memcvg;
    }
    Color shade1 = shade;
    if (two_next) { int na = C8(two_next->sh.a) + adith; shade1.a = by255[na > 255 ? 255 : na]; }
    c = (mode_l & 0x80) && !overflow ? mem : blender(c, mem, shade, shade1, blend_en); /* colour on coverage */
    write_pixel(x, y, a, c, 1);
    cvg_set(a, final + 1);
    if (depth && zupd) z_write(x, y, z, dz);
}

/* Fill mode writes the fill colour as it is: two 16-bit pixels a word (four 8-bit, one 32-bit). */
static void fill_pixel(int x, int y) {
    uint32_t a = pixel_addr(x, y);
    if (cimg_siz == 3) { R[a] = (uint8_t)(fill_color >> 24); R[a + 1] = (uint8_t)(fill_color >> 16); R[a + 2] = (uint8_t)(fill_color >> 8); R[a + 3] = (uint8_t)fill_color; }
    else if (cimg_siz == 1) R[a] = (uint8_t)(fill_color >> (8 * (3 - (x & 3))));
    else wr16(a, (uint16_t)((x & 1) ? fill_color : fill_color >> 16));
    cvg_clean(a);
    if (cimg_addr == zimg_addr && !z_in_memory()) zbuf_private[y * MAXW + x] = 1e30f;
}
static void fill_rect(int x0, int y0, int x1, int y1) {
    if (gpu) { gpu_fill_rect(x0, y0, x1, y1); return; }
    combiner_uses();
    if (cycle_type() == 3) { /* fill mode: edges inclusive, cut to the frame once (in_frame) rather than per pixel */
        int xa = x0, xb = x1, ya = y0, yb = y1, fh = frame_height();
        if (xa < scissor[0]) xa = scissor[0];
        if (xa < 0) xa = 0;
        if (ya < scissor[1]) ya = scissor[1];
        if (ya < 0) ya = 0;
        /* the scissor's right edge included, as in copy mode: a racing game's scissor ends at 319 and the console
           fills that column (it was left unfilled, a line of last frame's pixels) */
        if (xb > scissor[2]) xb = scissor[2];
        if (xb > cimg_width - 1) xb = cimg_width - 1;
        if (yb > scissor[3] - 1) yb = scissor[3] - 1;
        if (yb > fh - 1) yb = fh - 1;
        for (int y = ya; y <= yb; y++) {
            /* a 16-bit frame's row, its pixels and coverage at consecutive addresses: as fill_pixel writes them */
            uint32_t first = pixel_addr(xa, y), n = (uint32_t)(xb - xa + 1);
            if (cimg_siz == 2 && xa <= xb && pixel_addr(xb, y) == first + (n - 1) * 2 && (first & 0x7FFFFF) + n * 2 <= 0x800000
                && !(cimg_addr == zimg_addr && !z_in_memory())) {
                for (uint32_t i = 0; i < n; i++) wr16(first + i * 2, (uint16_t)(((xa + (int)i) & 1) ? fill_color : fill_color >> 16));
                memset(cvg_hole + ((first & 0x7FFFFF) >> 1), 0, n); /* the fill colour's low bits say */
                continue;
            }
            for (int x = xa; x <= xb; x++) fill_pixel(x, y);
        }
        return;
    }
    Texels none = one_texel((Color){0, 0, 0, 0});
    for (int y = y0; y < y1; y++)
        for (int x = x0; x < x1; x++) plot(x, y, 0, prim, &none, 0, 8, 0);
}

/* The texels a pixel sees (TEXEL0, TEXEL1 and the level of detail's fraction), from the tile G_TEXTURE or the
   rectangle names, at (s, t) in texels before each tile's shift; `lod` is how many texels one pixel steps. */
static HOT Texels texels_at(int tile, float s, float t, float lod);

/* Copy mode moves texels as they are, without the combiner or blender: a 16-bit frame gets the texel's 16 bits
   (a palette's entry for colour-indexed ones), an 8-bit frame the texel's byte. */
static uint32_t texel_raw(const Tile *tile, int s, int t);

/* A textured rectangle from (x0, y0) to (x1, y1) in pixels, texture coordinates s, t at its corner and their steps
   per pixel. */
static void tex_rect(int tile, float x0, float y0, float x1, float y1, float s, float t, float dsdx, float dtdy, int flip) {
    if (gpu) { gpu_tex_rect(tile, x0, y0, x1, y1, s, t, dsdx, dtdy, flip); return; }
    combiner_uses();
    int copy = cycle_type() == 2;
    if (copy) { dsdx /= 4.0f; x1 += 1; y1 += 1; }
    copying = copy;
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
                cvg_clean(a);
                continue;
            }
            Texels tx = texels_at(tile, ss, tt, lod);
            plot(x, y, 0, (Color){1, 1, 1, 1}, &tx, 0, 8, 0);
        }
    copying = 0;
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
/* A 64-bit word of a 16-bit YUV image (U Y V Y U Y V Y) into TMEM as the RDP splits it: the two pairs' U and V at
   `at` in the low half, the four Ys at the same place in the high half. */
static void tmem_yuv(uint32_t at, uint32_t src, int odd) {
    uint32_t a = (at ^ (odd ? 4u : 0u)) & 2044;
    for (int k = 0; k < 4; k++) { tmem[a + (uint32_t)k] = R[src + (uint32_t)k * 2]; tmem[a + 2048 + (uint32_t)k] = R[src + (uint32_t)k * 2 + 1]; }
}
static void load_block(int tile, int uls, int ult, int lrs, int dxt) {
    Tile *tl = &tiles[tile];
    gpu_gen++;
    if (tl->fmt == 1 && timg_siz == 2) { /* YUV: each word's four texels a word's half in each half of TMEM */
        uint32_t src = phys(timg_addr) + (uint32_t)((ult * timg_width + uls) * 2), line = 0;
        uint32_t words = ((uint32_t)(lrs - uls + 1) * 2 + 7) / 8;
        for (uint32_t i = 0; i < words && i < 512; i++) { /* wrapping within each half, as a 32-bit load does */
            tmem_yuv(tl->tmem * 8 + i * 4, src + i * 8, (line >> 11) & 1);
            line += (uint32_t)dxt;
        }
        return;
    }
    if (timg_siz == 3) { /* each 64-bit word two texels; the line counter as below */
        uint32_t src = phys(timg_addr) + (uint32_t)((ult * timg_width + uls) * 4), line = 0;
        uint32_t words = (uint32_t)(lrs - uls + 2) / 2;
        /* the addresses wrap within each half of TMEM (tmem_texel32), from any start: a racing game loads its sky's
           second texture at 0x180 (byte 0xC00), which the load once skipped as past the low half */
        for (uint32_t i = 0; i < words && i < 512; i++) {
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
    gpu_gen++;
    int bpp4 = timg_siz == 0, bytes = bpp4 ? 1 : 1 << (timg_siz - 1);
    if (tl->fmt == 1 && timg_siz == 2) {
        for (int t = ult; t <= lrt; t++) {
            uint32_t src = phys(timg_addr) + (uint32_t)((t * timg_width + uls) * 2), dst = (uint32_t)(tl->tmem * 8 + (t - ult) * tl->line * 8);
            for (int i = 0; i < (lrs - uls + 1) * 2; i += 8) tmem_yuv(dst + (uint32_t)i / 2, src + (uint32_t)i, (t - ult) & 1);
        }
        return;
    }
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
    gpu_gen++;
    for (int i = 0; i < count; i++)
        if (base + i >= 0 && base + i < 256) tlut[base + i] = rd16(phys(timg_addr) + i * 2);
    tlut_colours_type = -1;
}

/* The RDP commands both microcodes send. */
/* Recorded (PORT_RDPDUMP) as the RDP receives them: image addresses physical, and F3DEX2's other-mode commands (an
   RSP command) as the RDP's whole other mode. */
static void record_rdp(uint8_t op, uint32_t w0, uint32_t w1) {
    if (op == 0xFD || op == 0xFE || op == 0xFF) record2(w0, phys(w1) & 0x1FFFFFFF);
    else if (op >= 0xE6 && op != 0xF1) record2(w0, w1); /* the RDP's own: 0xF1 is the microcode's G_RDPHALF_2 */
}
static void rdp_rect(int tile, uint32_t xl, uint32_t yl, uint32_t xh, uint32_t yh, int tex, int16_t s, int16_t t,
                     int16_t dsdx, int16_t dtdy, int flip);
/* A texture rectangle goes through the RDP's walker in one or two cycles and in copy mode (onto an 8 or 16-bit
   frame); the WebGL renderer and a 32-bit frame's copy (which the console's RDP doesn't do) draw it themselves. */
static int rdp_walks_rect(void) { return !gpu && (cycle_type() < 2 || (cycle_type() == 2 && cimg_siz != 3)); }
static void rdp(uint8_t op, uint32_t w0, uint32_t w1) {
    if (dump_cmds && op != 0xE2 && op != 0xE3) record_rdp(op, w0, w1);
    switch (op) {
    /* the colour and depth images' addresses through the segments, as the microcode resolves them: a racing game
       names its frame buffer by segment 1 (0x01000000), and drawing went to that address, outside RAM */
    case 0xFF: cimg_addr = phys(w1) & 0x1FFFFFFF; cimg_width = (w0 & 0xFFF) + 1; if (cimg_width > MAXW) cimg_width = MAXW;
        cimg_siz = (int)(w0 >> 19) & 3; if (!cimg_siz) cimg_siz = 1;
        for (int i = 0; i < 8; i++) /* each colour image's width, for showing it later */
            if (!images[i][0] || images[i][0] == cimg_addr) { images[i][0] = cimg_addr; images[i][1] = (uint32_t)cimg_width; break; }
        break;
    case 0xEE: prim_z = (float)((w1 >> 16) & 0x7FFF) * 8.0f; prim_dz = (float)(w1 & 0xFFFF) * 8.0f; break; /* G_SETPRIMDEPTH */
    case 0xEC: { /* G_SETCONVERT: K0 to K3 for the convert stage (signed 9 bits, doubled plus one), K4 and K5 */
        int k[4] = {(int)(w0 >> 13) & 0x1FF, (int)(w0 >> 4) & 0x1FF, (int)((w0 & 15) << 5 | w1 >> 27), (int)(w1 >> 18) & 0x1FF};
        for (int i = 0; i < 4; i++) conv_k[i] = SIGN9(k[i]) * 2 + 1;
        gpu_gen++; /* the GPU's converted YUV textures (render_gpu.h) */
        k4 = (float)((int32_t)(w1 << 14) >> 23) / 255.0f; k5 = (float)((int32_t)(w1 << 23) >> 23) / 255.0f; break;
    }
    case 0xEB: key_center.r = ((w1 >> 8) & 255) / 255.0f; key_scale.r = (w1 & 255) / 255.0f;            /* G_SETKEYR */
        key_width[0] = (int)(w1 >> 16) & 0xFFF; break;
    case 0xEA: key_center.g = (w1 >> 24) / 255.0f; key_scale.g = ((w1 >> 16) & 255) / 255.0f;          /* G_SETKEYGB */
        key_width[1] = (int)(w0 >> 12) & 0xFFF; key_width[2] = (int)w0 & 0xFFF;
        key_center.b = ((w1 >> 8) & 255) / 255.0f; key_scale.b = (w1 & 255) / 255.0f; break;
    case 0xFE: zimg_addr = phys(w1) & 0x1FFFFFFF; break;
    case 0xFD: timg_addr = w1; timg_siz = (w0 >> 19) & 3; timg_width = (w0 & 0xFFF) + 1; break;
    case 0xF7: fill_color = w1; break;
    case 0xFA: prim = unpack8888(w1); prim_min_level = (int)(w0 >> 8) & 0x1F; prim_lod_frac = (w0 & 0xFF) / 255.0f; break;
    case 0xFB: env = unpack8888(w1); break;
    case 0xF9: blend = unpack8888(w1); break;
    case 0xF8: fog = unpack8888(w1); break;
    case 0xFC: combine0 = w0 & 0xFFFFFF; combine1 = w1; break;
    case 0xED: scissor[0] = (int)((w0 >> 12) & 0xFFF) >> 2; scissor[1] = (int)(w0 & 0xFFF) >> 2;
               scissor[2] = (int)((w1 >> 12) & 0xFFF) >> 2; scissor[3] = (int)(w1 & 0xFFF) >> 2;
               scissor_q[0] = (int)((w0 >> 12) & 0xFFF); scissor_q[1] = (int)(w0 & 0xFFF);
               scissor_q[2] = (int)((w1 >> 12) & 0xFFF); scissor_q[3] = (int)(w1 & 0xFFF); break;
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
    case 0xF6: /* G_FILLRECT: fill and copy modes take its edges inclusive; in one or two cycles, the RDP's walker */
        if (cycle_type() < 2 && !gpu) rdp_rect(0, (w0 >> 12) & 0xFFF, w0 & 0xFFF, (w1 >> 12) & 0xFFF, w1 & 0xFFF, 0, 0, 0, 0, 0, 0);
        else fill_rect((int)((w1 >> 12) & 0xFFF) >> 2, (int)(w1 & 0xFFF) >> 2, (int)((w0 >> 12) & 0xFFF) >> 2, (int)(w0 & 0xFFF) >> 2);
        break;
    case 0xE2: case 0xE3: { /* set other mode, F3DEX2's form: length - 1 and 32 - shift - length */
        int len = (w0 & 0xFF) + 1, shift = 32 - (int)((w0 >> 8) & 0xFF) - len;
        if (shift < 0) shift = 0;
        uint32_t mask = (len >= 32 ? 0xFFFFFFFFu : ((1u << len) - 1)) << shift;
        if (op == 0xE2) rsp_mode_l = (rsp_mode_l & ~mask) | (w1 & mask);
        else rsp_mode_h = (rsp_mode_h & ~mask) | (w1 & mask);
        mode_h = rsp_mode_h;
        mode_l = rsp_mode_l;
        break; }
    case 0xEF: mode_h = rsp_mode_h = w0 & 0xFFFFFF; mode_l = rsp_mode_l = w1; break;
    }
    if (dump_cmds && (op == 0xE2 || op == 0xE3)) record2(0xEF000000u | mode_h, mode_l);
}

/* ---------------------------------------------------------------- F3DEX2: vertices, matrices, triangles */

/* A vertex: clip space, screen space once projected, texture coordinates in texels, shade; `fixed_xy`/`fixed_z`
   when G_MODIFYVTX gave its screen place directly. */
typedef struct { float x, y, z, w, sx, sy, sz, s, t; Color shade; int clip, fixed_xy, fixed_z; float fx, fy, fz;
                 float prev[4]; /* the GPU path's: x, y, z, w in the previous frame (render_gpu.h's gpu_match) */ } Vtx;
static Vtx vtx[64];
static void gpu_triangle(const Vtx *a, const Vtx *b, const Vtx *c);
static int gpu_line; /* the triangles are a line's, in screen space */
static void gpu_match(const uint8_t *raw, Vtx *v);
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
        if (gpu) gpu_match(R + a, v);
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
static HOT Texels texels_at(int tile, float s, float t, float lod) {
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
    out.t0 = sample(a, shifted(s, a->shifts), shifted(t, a->shiftt), 0, NULL);
    out.t1 = uses_t1 ? sample(b, shifted(s, b->shifts), shifted(t, b->shiftt), 1, &out.t0) : out.t0;
    return out;
}

/* An edge's value at (px, py): the same two vertices give exactly opposite values in either order (they're taken
   in one fixed order, then the sign flipped), so a pixel on an edge two triangles share is inside exactly one. */
/* ---------------------------------------------------------------- triangles as the RDP draws them */

/* An RDP triangle command (0x08 | shade 4 | texture 2 | depth 1) from three vertices in screen space, as the RSP's
   microcode builds it: the vertices sorted top to bottom, the major edge (top to bottom vertex) and the two minor
   ones as x positions and slopes per scanline (s15.16), and each attribute (shade, s/t/w, depth) at the top
   scanline on the major edge with its steps across (x), along the major edge (e) and down (y). Adapted from
   libdragon's rdpq_triangle (public domain). x and y in pixels; z in the RDP's 15.16 depth (0 to 0x7FFF); s and t
   in s10.5 texels, already multiplied by w when `persp`; w the vertex's 1/w; shade 0 to 255. */
typedef struct { float x, y, z, s, t, w, c[4]; } SetupV;
static int32_t fx16(float f) { return f >= 32768.f ? 0x7FFFFFFF : f < -32768.f ? (int32_t)0x80000000 : (int32_t)floorf(f * 65536.f); }
static int tri_setup(const SetupV *v1, const SetupV *v2, const SetupV *v3, int shade, int tex, int zb, int tile,
                     int levels, uint32_t *o) {
    const SetupV *t_;
    if (v1->y > v2->y) { t_ = v1; v1 = v2; v2 = t_; }
    if (v2->y > v3->y) { t_ = v2; v2 = v3; v3 = t_; }
    if (v1->y > v2->y) { t_ = v1; v1 = v2; v2 = t_; }
    float y1 = floorf(v1->y * 4) / 4, y2 = floorf(v2->y * 4) / 4, y3 = floorf(v3->y * 4) / 4;
    int y1f = (int)floorf(v1->y * 4), y2f = (int)floorf(v2->y * 4), y3f = (int)floorf(v3->y * 4);
    y1f = y1f < -16384 ? -16384 : y1f > 16380 ? 16380 : y1f;
    y2f = y2f < -16384 ? -16384 : y2f > 16380 ? 16380 : y2f;
    y3f = y3f < -16384 ? -16384 : y3f > 16380 ? 16380 : y3f;
    float hx = v3->x - v1->x, hy = y3 - y1, mx = v2->x - v1->x, my = y2 - y1, lx = v3->x - v2->x, ly = y3 - y2;
    float nz = hx * my - hy * mx, f = fabsf(nz) > 1e-30f ? -1.0f / nz : 0;
    float ish = fabsf(hy) > 1e-30f ? hx / hy : 0, ism = fabsf(my) > 1e-30f ? mx / my : 0, isl = fabsf(ly) > 1e-30f ? lx / ly : 0;
    float fy = floorf(y1) - y1;
    int n = 0;
    o[n++] = (uint32_t)(0x08 | shade << 2 | tex << 1 | zb) << 24 | (uint32_t)(nz < 0) << 23 | (uint32_t)(levels & 7) << 19
           | (uint32_t)(tile & 7) << 16 | ((uint32_t)y3f & 0x3FFF);
    o[n++] = ((uint32_t)y2f & 0x3FFF) << 16 | ((uint32_t)y1f & 0x3FFF);
    o[n++] = (uint32_t)fx16(v2->x); o[n++] = (uint32_t)fx16(isl);
    o[n++] = (uint32_t)fx16(v1->x + fy * ish); o[n++] = (uint32_t)fx16(ish);
    o[n++] = (uint32_t)fx16(v1->x + fy * ism); o[n++] = (uint32_t)fx16(ism);
    /* an attribute's start, and its steps across, along the major edge and down, each s15.16 */
    #define GRADIENT(a1, a2, a3, start, dx, de, dy) do { \
        float m_ = (a2) - (a1), h_ = (a3) - (a1), ddx = (hy * m_ - my * h_) * f, ddy = (mx * h_ - hx * m_) * f; \
        float dde = ddy + ddx * ish; \
        start = fx16((a1) + fy * dde); dx = fx16(ddx); de = fx16(dde); dy = fx16(ddy); } while (0)
    if (shade) {
        int32_t v[4], dx[4], de[4], dy[4];
        for (int i = 0; i < 4; i++) GRADIENT(v1->c[i], v2->c[i], v3->c[i], v[i], dx[i], de[i], dy[i]);
        #define HI(a, b) (((uint32_t)(a) & 0xFFFF0000u) | (((uint32_t)(b) >> 16) & 0xFFFF))
        #define LO(a, b) (((uint32_t)(a) << 16) | ((uint32_t)(b) & 0xFFFF))
        o[n++] = HI(v[0], v[1]); o[n++] = HI(v[2], v[3]); o[n++] = HI(dx[0], dx[1]); o[n++] = HI(dx[2], dx[3]);
        o[n++] = LO(v[0], v[1]); o[n++] = LO(v[2], v[3]); o[n++] = LO(dx[0], dx[1]); o[n++] = LO(dx[2], dx[3]);
        o[n++] = HI(de[0], de[1]); o[n++] = HI(de[2], de[3]); o[n++] = HI(dy[0], dy[1]); o[n++] = HI(dy[2], dy[3]);
        o[n++] = LO(de[0], de[1]); o[n++] = LO(de[2], de[3]); o[n++] = LO(dy[0], dy[1]); o[n++] = LO(dy[2], dy[3]);
    }
    if (tex) {
        /* w normalised so the largest is 0x7FFF, s and t by the same factor: the vertices' w and the start, which
           is w taken up to the top of the first scanline. A start past 0x7FFF wraps to a negative w on the RDP,
           and the texture came out as garbage there. */
        float wmax = fmaxf(v1->w, fmaxf(v2->w, v3->w)), k = wmax > 0 ? 1.0f / wmax : 1.0f;
        {
            float m_ = (v2->w - v1->w) * k, h_ = (v3->w - v1->w) * k;
            float ddx = (hy * m_ - my * h_) * f, ddy = (mx * h_ - hx * m_) * f, start = v1->w * k + fy * (ddy + ddx * ish);
            if (start > 1.0f) k /= start;
        }
        float w1 = v1->w * k, w2 = v2->w * k, w3 = v3->w * k;
        int32_t S, T, W, sx, tx, wx, se, te, we, sy, ty, wy;
        GRADIENT(v1->s * w1, v2->s * w2, v3->s * w3, S, sx, se, sy);
        GRADIENT(v1->t * w1, v2->t * w2, v3->t * w3, T, tx, te, ty);
        GRADIENT(w1 * 0x7FFF, w2 * 0x7FFF, w3 * 0x7FFF, W, wx, we, wy);
        o[n++] = HI(S, T); o[n++] = (uint32_t)W & 0xFFFF0000u; o[n++] = HI(sx, tx); o[n++] = (uint32_t)wx & 0xFFFF0000u;
        o[n++] = LO(S, T); o[n++] = (uint32_t)W << 16; o[n++] = LO(sx, tx); o[n++] = (uint32_t)wx << 16;
        o[n++] = HI(se, te); o[n++] = (uint32_t)we & 0xFFFF0000u; o[n++] = HI(sy, ty); o[n++] = (uint32_t)wy & 0xFFFF0000u;
        o[n++] = LO(se, te); o[n++] = (uint32_t)we << 16; o[n++] = LO(sy, ty); o[n++] = (uint32_t)wy << 16;
    }
    if (zb) {
        int32_t Z, zx, ze, zy;
        GRADIENT(v1->z, v2->z, v3->z, Z, zx, ze, zy);
        o[n++] = (uint32_t)Z; o[n++] = (uint32_t)zx; o[n++] = (uint32_t)ze; o[n++] = (uint32_t)zy;
    }
    #undef GRADIENT
    return n;
}

/* The RDP's perspective divide: s and t (s10.5 times w) by w, through its reciprocal of w. w is normalised to 15
   bits by a shift, and its reciprocal read from a table of 64 points with the line to the next between them: each
   point 2^20 / (64 + i), rounded, but for the RDP's 14979 at 6; the line's slope the difference to the next point
   (the last to 8192). The products are cut back by the shift to s10.5, with results past its range flagged as over
   (clamped high) or under (low), as a w at or below 0 is. Divided in floats, a perspective texture's texels landed
   one off the console's across a few percent of a racing game's road. */
static int rcp_point(int i) { return i == 64 ? 8192 : i == 6 ? 14979 : (int)((2097152 / (64 + i) + 1) / 2); }
static HOT void persp_divide(int32_t ss, int32_t st, int32_t sw, int *outs, int *outt) {
    static int16_t rcp[0x8000];
    static uint8_t shifts[0x8000];
    static int ready;
    if (!ready) {
        for (int i = 0; i < 0x8000; i++) {
            int k = 1;
            while (k <= 14 && !((i << k) & 0x8000)) k++;
            int shift = k - 1, norm = (i << shift) & 0x3FFF, frac = (norm & 0xFF) << 2, at = norm >> 8;
            rcp[i] = (int16_t)(((((rcp_point(at + 1) - rcp_point(at)) * frac) >> 10) + rcp_point(at)) & 0x7FFF);
            shifts[i] = (uint8_t)shift;
        }
        ready = 1;
    }
    int carry = (int16_t)sw <= 0;
    sw &= 0x7FFF;
    int shift = shifts[sw];
    int32_t sp = (int16_t)ss * rcp[sw], tp = (int16_t)st * rcp[sw];
    int32_t mask = ((1 << 30) - 1) & -((1 << 29) >> shift);
    int32_t os = sp & mask, ot = tp & mask;
    int32_t rs = shift != 14 ? sp >> (13 - shift) : sp << 1, rt = shift != 14 ? tp >> (13 - shift) : tp << 1;
    /* to s10.5 (17 bits); past them, the edge they went over: as far as texels go, high or low */
    int vs = (rs & 0x10000) ? (rs & 0x1FFFF) - 0x20000 : rs & 0x1FFFF, vt = (rt & 0x10000) ? (rt & 0x1FFFF) - 0x20000 : rt & 0x1FFFF;
    if (carry || (os != mask && os != 0)) vs = carry || !(sp & (1 << 29)) ? 0x7FFF : -0x8000;
    if (carry || (ot != mask && ot != 0)) vt = carry || !(tp & (1 << 29)) ? 0x7FFF : -0x8000;
    *outs = vs; *outt = vt;
}

/* An RDP triangle command drawn as the RDP draws it, in its own fixed point. The RDP walks the triangle in quarter
   scanlines from its top: the major edge's x and the minor edge's (the middle edge, then the low one from the middle
   vertex's y) step by their slopes, each kept in eighths of a pixel and clamped to the scissor. A pixel's coverage
   is 8 sample points, two on each quarter scanline (at 0 and 1/2 of the pixel on the even ones, 1/4 and 3/4 on the
   odd ones), a point inside when it's at or right of the left edge and left of the right one. Without
   anti-aliasing a pixel is drawn when its first point (its top-left corner) is inside; with it, when any is.
   Shade, depth and the texture's s, t and w are taken once a scanline on the major edge (moved along it by
   three quarters of the scanline when the edge leans away from the inside), at the whole pixel left of the edge,
   then stepped across the scanline pixel by pixel. Sampling each pixel's centre in floats instead put edges half a
   pixel from where the console draws them. */
static int dzpix_round(int sum) { /* the RDP's delta z: the power of two above the sum of the two steps */
    if (sum & 0xC000) return 0x8000;
    if (!(sum & 0xFFFF)) return 1;
    if (sum == 1) return 3; /* the RDP's own rule (angrylion's), not a power of two */
    int p = 0x2000;
    while (p && !(sum & p)) p >>= 1;
    return p << 1;
}
/* A slope's integer part as the RDP adds it into delta z: a negative one's complemented (-1 counts as 0, not 1) */
static int dz_magnitude(int32_t slope) { int v = (slope >> 16) & 0xFFFF; return (v & 0x8000) ? (~v & 0x7FFF) : v; }
/* s15.16 to eighths of a pixel, the last bit set if any remains, clamped to the scissor; `side` counts it past
   the scissor's left (1) or right (2) */
static int edge8(int32_t x, int lo, int hi, int *side) {
    if (x < 0) { *side = 1; return lo; }
    int e = ((x >> 13) & ~1) | (((x >> 1) & 0x1FFF) != 0);
    *side = e < lo ? 1 : e >= hi ? 2 : 0;
    return e < lo ? lo : e >= hi ? hi : e;
}
static int32_t signx(uint32_t v, int bits) { return (int32_t)(v << (32 - bits)) >> (32 - bits); }
static int32_t join_hi(uint32_t ints, uint32_t fracs) { return (int32_t)((ints & 0xFFFF0000u) | (fracs >> 16)); }
static int32_t join_lo(uint32_t ints, uint32_t fracs) { return (int32_t)((ints << 16) | (fracs & 0xFFFF)); }
#define SHADE9(v) ((((v) >> 16) & 0x100) ? ((((v) >> 16) & 0x80) ? 0 : 255) : (((v) >> 16) & 0xFF))
enum { AR, AG, AB, AA, AS, AT, AW, AZ, NATTR };
/* What the walker hands the pixel pipeline for one pixel of a span: its shade, texels and depth, from the
   attributes `a` there, corrected to the first covered sample of a partly covered pixel. */
static HOT void pixel_inputs(PixIn *o, const int32_t *a, const int32_t *step, const int32_t *dyc, int shade, int tex,
                             int zb, int tile, int persp, int lod_on, int cvg, int offx, int offy) {
    o->sh = (Color){0, 0, 0, 0};
    if (shade) {
        /* a partly covered pixel's shade (and depth) taken at its first covered sample, in quarter pixels from its
           corner, as the RDP corrects them */
        int ch[4];
        for (int q = 0; q < 4; q++) {
            int v = a[AR + q] >> 14;
            if (cvg < 8) v = ((v << 2) + offx * signx((uint32_t)(step[AR + q] >> 14), 13)
                              + offy * signx((uint32_t)(dyc[AR + q] >> 14), 13)) >> 4;
            else v >>= 2;
            v &= 0x1FF;
            ch[q] = (v & 0x100) ? ((v & 0x80) ? 0 : 255) : v;
        }
        o->sh = (Color){by255[ch[0]], by255[ch[1]], by255[ch[2]], by255[ch[3]]};
    }
    o->tx = (Texels){{1, 1, 1, 1}, {1, 1, 1, 1}, 0};
    if (tex) {
        /* s and t in texels: divided by w with perspective, as they are without */
        float st[3][2];
        for (int q = 0; q < (lod_on ? 3 : 1); q++) {
            int32_t S = a[AS], T = a[AT], W = a[AW];
            if (q == 1) { S += step[AS]; T += step[AT]; W += step[AW]; }
            if (q == 2) { S += dyc[AS]; T += dyc[AT]; W += dyc[AW]; }
            float ss = (float)(int16_t)(S >> 16), tt = (float)(int16_t)(T >> 16);
            if (persp) {
                int ds, dt;
                persp_divide(S >> 16, T >> 16, W >> 16, &ds, &dt);
                ss = (float)ds; tt = (float)dt;
            }
            st[q][0] = ss / 32.0f; st[q][1] = tt / 32.0f;
        }
        float lod = 0;
        if (lod_on)
            lod = fmaxf(fmaxf(fabsf(st[1][0] - st[0][0]), fabsf(st[1][1] - st[0][1])),
                        fmaxf(fabsf(st[2][0] - st[0][0]), fabsf(st[2][1] - st[0][1])));
        o->tx = texels_at(tile, st[0][0], st[0][1], lod);
    }
    int zi = (a[AZ] >> 10) & 0x3FFFFF;
    if (cvg < 8) zi = ((zi << 2) + offx * signx((uint32_t)(step[AZ] >> 10), 22)
                       + offy * signx((uint32_t)(dyc[AZ] >> 10), 22)) >> 5;
    else zi >>= 3;
    zi = ((zi >> 17) & 3) == 2 ? 0x3FFFF : ((zi >> 17) & 3) == 3 ? 0 : zi & 0x3FFFF;
    o->z = zb ? (float)zi : 0;
}
static void rdp_walk(const uint32_t *w, int shade, int tex, int zb, int rect) {
    combiner_uses();
    int flip = (w[0] >> 23) & 1, tile = (w[0] >> 16) & 7;
    int yl = signx(w[0], 14), ym = signx(w[1] >> 16, 14), yh = signx(w[1], 14);
    int32_t xl = signx(w[2], 28), dxldy = signx(w[3], 30), xh = signx(w[4], 28), dxhdy = signx(w[5], 30);
    int32_t xm = signx(w[6], 28), dxmdy = signx(w[7], 30);
    int32_t v[NATTR] = {0}, dx[NATTR] = {0}, de[NATTR] = {0}, dy[NATTR] = {0};
    const uint32_t *p = w + 8;
    if (shade) {
        for (int i = 0; i < 4; i++) {
            int hi = !(i & 1), at = i >> 1; /* r, b in the high halves; g, a in the low */
            v[i] = hi ? join_hi(p[at], p[4 + at]) : join_lo(p[at], p[4 + at]);
            dx[i] = hi ? join_hi(p[2 + at], p[6 + at]) : join_lo(p[2 + at], p[6 + at]);
            de[i] = hi ? join_hi(p[8 + at], p[12 + at]) : join_lo(p[8 + at], p[12 + at]);
            dy[i] = hi ? join_hi(p[10 + at], p[14 + at]) : join_lo(p[10 + at], p[14 + at]);
        }
        p += 16;
    }
    if (tex) {
        v[AS] = join_hi(p[0], p[4]); v[AT] = join_lo(p[0], p[4]); v[AW] = join_hi(p[1], p[5]);
        dx[AS] = join_hi(p[2], p[6]); dx[AT] = join_lo(p[2], p[6]); dx[AW] = join_hi(p[3], p[7]);
        de[AS] = join_hi(p[8], p[12]); de[AT] = join_lo(p[8], p[12]); de[AW] = join_hi(p[9], p[13]);
        dy[AS] = join_hi(p[10], p[14]); dy[AT] = join_lo(p[10], p[14]); dy[AW] = join_hi(p[11], p[15]);
        p += 16;
    }
    if (zb) { v[AZ] = (int32_t)p[0]; dx[AZ] = (int32_t)p[1]; de[AZ] = (int32_t)p[2]; dy[AZ] = (int32_t)p[3]; }
    /* the steps across a scanline, and down one, as the RDP keeps them */
    int32_t step[NATTR], dyc[NATTR], diff[NATTR] = {0};
    for (int i = 0; i < NATTR; i++) step[i] = i == AZ ? dx[i] : dx[i] & ~0x1F;
    int offset = !((((uint32_t)w[5] >> 31) & 1) ^ flip); /* the major edge leans away: sample 3/4 down it */
    for (int i = 0; i < NATTR; i++) {
        dyc[i] = dy[i];
        if (offset) { int32_t a = de[i] & ~0x1FF, b = dy[i] & ~0x1FF; diff[i] = a - (a >> 2) - b + (b >> 2); }
    }
    int sumdz = dz_magnitude(dx[AZ]) + dz_magnitude(dy[AZ]);
    float dz = (float)(dzpix_round(sumdz & 0xFFFF) * 8);
    int cycle = cycle_type(), aa = (mode_l & 0x8) != 0 && cycle < 2, persp = (mode_h & 0x80000) != 0 && !rect;
    int lod_on = (mode_h & 0x10000) != 0;
    int clip_l = scissor_q[0] * 2, clip_r = scissor_q[2] * 2; /* eighths */
    int yhlim = yh > scissor_q[1] ? yh : scissor_q[1], yllim = yl < scissor_q[3] ? yl : scissor_q[3];
    int32_t xmaj = xh & ~1, xmin = xm & ~1, inc_maj = (dxhdy >> 2) & ~1, inc_min = (dxmdy >> 2) & ~1;
    int ldflag = offset ? 3 : 0;
    int maj8[4], min8[4], valid[4], major_px = 0, all_over = 1, all_under = 1;
    int32_t at[NATTR];
    int ylast = yllim | 3;
    for (int k = yh & ~3; k <= ylast; k++) {
        if (k == ym) { xmin = xl & ~1; inc_min = (dxldy >> 2) & ~1; }
        int sub = k & 3, line = k >> 2;
        if (k >= (yhlim & ~3)) {
            int side_maj, side_min;
            maj8[sub] = edge8(xmaj, clip_l, clip_r, &side_maj);
            min8[sub] = edge8(xmin, clip_l, clip_r, &side_min);
            /* a scanline whose edges all lie past the scissor on one side, on each of its quarters, isn't drawn */
            if (sub == 0) all_over = all_under = 1;
            all_over &= side_maj == 2 && side_min == 2;
            all_under &= side_maj == 1 && side_min == 1;
            int crossed = flip ? (xmin >> 14) < (xmaj >> 14) : (xmaj >> 14) < (xmin >> 14);
            valid[sub] = !(k < yhlim || k >= yllim || crossed);
            if (sub == ldflag) {
                major_px = signx((uint32_t)xmaj >> 16, 12);
                int xfrac = (xmaj >> 8) & 0xFF;
                for (int i = 0; i < NATTR; i++) at[i] = ((v[i] & ~0x1FF) + diff[i] - xfrac * ((dx[i] >> 8) & ~1)) & ~0x3FF;
            }
            if (sub == 3 && line >= 0 && line < MAXH) {
                /* the scanline's pixels: from the major edge's side to the minor's */
                int lo = 0x7FFF, hi = -1, any = 0;
                for (int i = 0; i < 4; i++) {
                    if (!valid[i]) continue;
                    any = 1;
                    int l8 = flip ? maj8[i] : min8[i], r8 = flip ? min8[i] : maj8[i];
                    if ((l8 >> 3) < lo) lo = l8 >> 3;
                    if ((r8 >> 3) > hi) hi = r8 >> 3;
                }
                if (any && hi >= lo && !all_over && !all_under) {
                    int x0 = flip ? lo : hi, x1 = flip ? hi : lo, xinc = flip ? 1 : -1;
                    int32_t a[NATTR];
                    /* the major edge clipped: start further in (counted in 12 bits, as the RDP counts it: a sliver
                       whose major edge lies past the span's start wraps round, and its colour with it) */
                    int skip = (flip ? x0 - major_px : major_px - x0) & 0xFFF;
                    for (int i = 0; i < NATTR; i++) a[i] = at[i] + (flip ? step[i] : -step[i]) * skip;
                    /* a pixel's coverage: how many of its samples the span covers, its corner's, and its first */
                    #define COVERAGE(px, cvg, corner, offx, offy) do { \
                        cvg = 0; corner = 0; offx = -1; offy = -1; \
                        for (int i = 0; i < 4; i++) { \
                            if (!valid[i]) continue; \
                            int l8 = flip ? maj8[i] : min8[i], r8 = flip ? min8[i] : maj8[i]; \
                            int p0 = (px) * 8 + ((i & 1) ? 2 : 0), p1 = p0 + 4; \
                            int in0 = p0 >= l8 && p0 < r8, in1 = p1 >= l8 && p1 < r8; \
                            cvg += in0 + in1; \
                            if (i == 0) corner = in0; \
                            if (offy < 0 && (in0 || in1)) { offy = i; offx = ((i & 1) ? 1 : 0) + (in0 ? 0 : 2); } \
                        } } while (0)
                    if (cycle == 2) {
                        /* copy mode (rectangles): every pixel of the span, the right scissor edge's included (on a
                           frame as wide as the scissor it lands on the next line, as on the console), from TMEM 64
                           bits at a time: four 16-bit texels (eight 8-bit) for as many pixels, s and t stepping once
                           per group. Without alpha compare every texel is written, with it those whose alpha bit (a
                           16-bit texel's) or value (an 8-bit one's) passes. */
                        int per = cimg_siz == 1 ? 8 : 4;
                        const Tile *tl = &tiles[tile];
                        copying = 1;
                        for (int x = x0, j = 0;; x += xinc, j++) {
                            if (j && j % per == 0) for (int i = 0; i < NATTR; i++) a[i] += flip ? step[i] : -step[i];
                            int si = (int)floorf(shifted((float)(int16_t)(a[AS] >> 16) / 32.0f, tl->shifts)) + j % per;
                            int ti = (int)floorf(shifted((float)(int16_t)(a[AT] >> 16) / 32.0f, tl->shiftt));
                            uint32_t raw = texel_raw(tl, si, ti), at = pixel_addr(x, line);
                            int pass = !(mode_l & 1) || (cimg_siz == 1 ? (int)(raw & 0xFF) >= C8(blend.a) : (int)(raw & 1));
                            if (pass && x >= 0) {
                                if (cimg_siz == 1) R[at] = (uint8_t)raw; else wr16(at, (uint16_t)raw);
                                cvg_clean(at);
                            }
                            if (x == x1) break;
                        }
                        copying = 0;
                    } else if (cycle == 1) {
                        /* two cycles: the RDP's pipeline runs a pixel's second cycle with the next one's first, so
                           every pixel of the span goes through it (two_next) */
                        PixIn cur, nxt;
                        int cvg, corner, offx, offy, ncvg, ncorner, noffx, noffy;
                        COVERAGE(x0, cvg, corner, offx, offy);
                        pixel_inputs(&cur, a, step, dyc, shade, tex, zb, tile, persp, lod_on, cvg, offx, offy);
                        for (int x = x0;; x += xinc) {
                            int32_t an[NATTR];
                            for (int i = 0; i < NATTR; i++) an[i] = a[i] + (flip ? step[i] : -step[i]);
                            if (x != x1) COVERAGE(x + xinc, ncvg, ncorner, noffx, noffy);
                            else { ncvg = ncorner = 0; noffx = noffy = 0; }
                            pixel_inputs(&nxt, an, step, dyc, shade, tex, zb, tile, persp, lod_on, ncvg, noffx, noffy);
                            two_next = &nxt;
                            two_next_cvg = ncvg;
                            Color mem = memory_at(x, line);
                            if (aa ? cvg > 0 : corner) plot(x, line, cur.z, cur.sh, &cur.tx, zb, cvg, dz);
                            two_next = NULL;
                            two_mem_prev = mem;
                            cur = nxt; cvg = ncvg; corner = ncorner; offx = noffx; offy = noffy;
                            for (int i = 0; i < NATTR; i++) a[i] = an[i];
                            if (x == x1) break;
                        }
                    } else for (int x = x0;; x += xinc) {
                        int cvg, corner, offx, offy;
                        COVERAGE(x, cvg, corner, offx, offy);
                        if (aa ? cvg > 0 : corner) {
                            if (cycle == 3) { if (in_frame(x, line)) fill_pixel(x, line); }
                            else {
                                PixIn in;
                                pixel_inputs(&in, a, step, dyc, shade, tex, zb, tile, persp, lod_on, cvg, offx, offy);
                                plot(x, line, in.z, in.sh, &in.tx, zb, cvg, dz);
                            }
                        }
                        for (int i = 0; i < NATTR; i++) a[i] += flip ? step[i] : -step[i];
                        if (x == x1) break;
                    }
                    #undef COVERAGE
                }
            }
        }
        if (sub == 3) for (int i = 0; i < NATTR; i++) v[i] += de[i];
        xmaj += inc_maj;
        xmin += inc_min;
    }
}

static void rdp_triangle(const uint32_t *w) {
    int op = (int)(w[0] >> 24) & 0x3F;
    rdp_walk(w, (op & 4) != 0, (op & 2) != 0, (op & 1) != 0, 0);
}

/* A rectangle as the RDP draws it in one or two cycles: through the triangle walker, as a shape whose major edge
   is its left side (xh) and whose minor edges are its right (xl), its texture's s and t at its top-left corner
   stepping by dsdx across and dtdy down (swapped for G_TEXRECTFLIP). Corners in quarter pixels, s and t s10.5, the
   steps s5.10. Drawn pixel by pixel in floats from the corner, a scaled rectangle's texture started up to a texel
   off the console's at a fractional corner. Without a texture (G_FILLRECT in one or two cycles), no shade either. */
#define HI16(a, b) (((uint32_t)(a) & 0xFFFF0000u) | (((uint32_t)(b) >> 16) & 0xFFFF))
#define LO16(a, b) (((uint32_t)(a) << 16) | ((uint32_t)(b) & 0xFFFF))
static void rdp_rect(int tile, uint32_t xl, uint32_t yl, uint32_t xh, uint32_t yh, int tex, int16_t s, int16_t t,
                     int16_t dsdx, int16_t dtdy, int flip) {
    uint32_t w[24] = {0};
    if (cycle_type() >= 2) yl |= 3; /* fill and copy modes take the bottom edge's line whole */
    w[0] = 1u << 23 | (uint32_t)(tile & 7) << 16 | (yl & 0xFFF);
    w[1] = (yl & 0xFFF) << 16 | (yh & 0xFFF);
    w[2] = w[6] = (xl >> 2) << 16 | (xl & 3) << 14;
    w[4] = (xh >> 2) << 16 | (xh & 3) << 14;
    if (tex) {
        int32_t S = (int32_t)s << 16, T = (int32_t)t << 16, across = (int32_t)dsdx << 11, down = (int32_t)dtdy << 11;
        int32_t sx = flip ? 0 : across, tx = flip ? down : 0, sy = flip ? across : 0, ty = flip ? 0 : down;
        uint32_t *q = w + 8;
        q[0] = HI16(S, T); q[2] = HI16(sx, tx); q[4] = LO16(S, T); q[6] = LO16(sx, tx);
        q[8] = q[10] = HI16(sy, ty); q[12] = q[14] = LO16(sy, ty);
    }
    rdp_walk(w, 0, tex, 0, 1);
}

/* One triangle already in front of the eye, in screen space: the RDP command the RSP would send for it, drawn.
   Without perspective correction the RDP takes s and t as they are, at half the scale they have with it: a racing
   game's menu text (perspective off) gives each glyph twice the texel coordinates of its place in its font page. */
static void raster(const Vtx *a, const Vtx *b, const Vtx *c) {
    if (gpu) { gpu_triangle(a, b, c); return; }
    const Vtx *in[3] = {a, b, c};
    int persp = (mode_h & 0x80000) != 0, smooth = (geometry & 0x200000) != 0;
    SetupV sv[3];
    for (int i = 0; i < 3; i++) {
        const Vtx *q = in[i];
        const Color *col = smooth ? &q->shade : &c->shade;
        sv[i] = (SetupV){q->sx, q->sy, q->sz / 8.0f, q->s * 32.0f * (persp ? 1.0f : 0.5f), q->t * 32.0f * (persp ? 1.0f : 0.5f),
                         persp ? 1.0f / q->w : 1.0f, {col->r * 255.0f, col->g * 255.0f, col->b * 255.0f, col->a * 255.0f}};
    }
    uint32_t cmd[44];
    int n = tri_setup(&sv[0], &sv[1], &sv[2], 1, tex_on, (geometry & 1) != 0, tex_tile, tex_levels, cmd);
    record(cmd, n);
    rdp_triangle(cmd);
}

static int clip_ratio = 2; /* gSPClipRatio: the guard band's size in w */
static Vtx lerp(const Vtx *a, const Vtx *b, float f) {
    Vtx v;
    v.x = a->x + (b->x - a->x) * f; v.y = a->y + (b->y - a->y) * f; v.z = a->z + (b->z - a->z) * f;
    v.w = a->w + (b->w - a->w) * f; v.s = a->s + (b->s - a->s) * f; v.t = a->t + (b->t - a->t) * f;
    v.shade.r = a->shade.r + (b->shade.r - a->shade.r) * f; v.shade.g = a->shade.g + (b->shade.g - a->shade.g) * f;
    v.shade.b = a->shade.b + (b->shade.b - a->shade.b) * f; v.shade.a = a->shade.a + (b->shade.a - a->shade.a) * f;
    for (int k = 0; k < 4; k++) v.prev[k] = a->prev[k] + (b->prev[k] - a->prev[k]) * f;
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
    /* Clipped as the microcode clips: against the near plane, and against a guard band around the screen (x and y
       within clip_ratio times w; G_MW_CLIP sets it, 2 by default). The RDP's coordinates hold about +-2048
       pixels; a road triangle reaching 13,000 pixels off screen wrapped on the RDP, where the RSP would have cut
       it at the guard band. */
    Vtx poly[16], next[16];
    int n = 3, guard = !in[0]->fixed_xy && !in[1]->fixed_xy && !in[2]->fixed_xy;
    for (int i = 0; i < 3; i++) poly[i] = *in[i];
    for (int plane = 0; plane < (guard ? 5 : 1) && n >= 3; plane++) {
        int m = 0;
        float r = (float)clip_ratio;
        for (int i = 0; i < n; i++) {
            const Vtx *a = &poly[i], *b = &poly[(i + 1) % n];
            #define PLANE_D(v) (plane == 0 ? (v)->z + (v)->w : plane == 1 ? r * (v)->w - (v)->x : plane == 2 ? r * (v)->w + (v)->x \
                                : plane == 3 ? r * (v)->w - (v)->y : r * (v)->w + (v)->y)
            float da = PLANE_D(a), db = PLANE_D(b);
            #undef PLANE_D
            if (da >= 0 && m < 16) next[m++] = *a;
            if ((da >= 0) != (db >= 0) && m < 16) next[m++] = lerp(a, b, da / (da - db));
        }
        memcpy(poly, next, sizeof(Vtx) * (size_t)m);
        n = m;
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

/* A line (G_LINE3D, gSPLineW3D): the line microcodes (L3DEX, L3DEX2, and Fast3D's) draw it as a thin
   parallelogram, (3 + wd) / 2 pixels thick across its minor axis, as GLideN64 takes it. Clipped against the near
   plane like a triangle's edge. F3DEX2's 0x08 once went to the RDP as an unshaded triangle command. */
static void line3d(int i0, int i1, int wd) {
    Vtx a = vtx[i0 & 63], b = vtx[i1 & 63];
    float da = a.z + a.w, db = b.z + b.w;
    if (da < 0 && db < 0) return;
    if (da < 0) a = lerp(&a, &b, da / (da - db));
    else if (db < 0) b = lerp(&b, &a, db / (db - da));
    if (a.w < 1e-4f) a.w = 1e-4f;
    if (b.w < 1e-4f) b.w = 1e-4f;
    project(&a); project(&b);
    float half = (1.5f + (float)(wd & 0xFF) * 0.5f) / 2, dx = b.sx - a.sx, dy = b.sy - a.sy;
    float ox = fabsf(dx) >= fabsf(dy) ? 0 : half, oy = fabsf(dx) >= fabsf(dy) ? half : 0;
    Vtx q[4] = {a, a, b, b};
    q[0].sx -= ox; q[0].sy -= oy; q[1].sx += ox; q[1].sy += oy;
    q[2].sx += ox; q[2].sy += oy; q[3].sx -= ox; q[3].sy -= oy;
    gpu_line = 1; /* (widened on the screen: the GPU path takes its corners as they are there) */
    raster(&q[0], &q[1], &q[2]);
    raster(&q[0], &q[2], &q[3]);
    gpu_line = 0;
}

/* ---------------------------------------------------------------- what every microcode shares */

/* The microcode a task (or G_LOAD_UCODE) runs, from the signature in its data: "RSP Gfx ucode F3DEX  fifo 2.05" is
   GBI 2 (F3DEX2, F3DZEX, F3DLX2...), a 1.x version is GBI 1 (F3DEX, F3DLX, F3DLP); "S2DEX" the 2D microcode of
   either generation; "RSP SW Version" is Fast3D. Without a signature: F3DEX2, as before microcodes were told apart. */
enum { UC_F3DEX2, UC_S2DEX2, UC_F3D, UC_F3DEX, UC_S2DEX, UC_F3DDKR };
static int ucode, line_ucode; /* line_ucode: L3DEX or L3DEX2, whose G_LINE3D shares F3DEX's G_QUAD opcode */
static const char *const ucode_names[] = {"F3DEX2", "S2DEX2", "Fast3D", "F3DEX", "S2DEX", "F3DDKR"};
/* Microcodes with no signature in their data, known by the CRC-32 of it (the task's 2 KB): Rare's F3DDKR, Fast3D
   with its own matrices, vertices and triangles (f3ddkr()), as a racing game's European release has it. */
static const struct { uint32_t crc; int ucode; } unsigned_ucodes[] = {{0xC432D0EAu, UC_F3DDKR}};
static uint32_t data_crc(const uint8_t *p, size_t n) {
    uint32_t c = 0xFFFFFFFFu;
    while (n--) { c ^= *p++; for (int k = 0; k < 8; k++) c = (c >> 1) ^ (0xEDB88320u & -(c & 1)); }
    return ~c;
}
static uint32_t half1; /* G_RDPHALF_1's word: G_BRANCH_Z's list, G_LOAD_UCODE's data */
static int identify_ucode(uint32_t data) { /* data: a physical address */
    line_ucode = 0;
    const uint8_t *d = R + 0x80000000u + (data & 0x1FFFFFFF);
    if ((data & 0x1FFFFFFF) + 0x800 > 0x800000) return UC_F3DEX2;
    for (int i = 0; i + 40 < 0x800; i++) {
        if (d[i] == 'R' && !memcmp(d + i, "RSP SW Version", 14)) return UC_F3D;
        if (d[i] != 'R' || memcmp(d + i, "RSP Gfx ucode ", 14)) continue;
        const uint8_t *p = d + i + 14;
        line_ucode = !memcmp(p, "L3DEX", 5);
        int two_d = !memcmp(p, "S2DEX", 5);
        while (*p && !(p[0] >= '0' && p[0] <= '9' && p[1] == '.')) p++; /* the version: after the name and "fifo" */
        if (*p != '1') return two_d ? UC_S2DEX2 : UC_F3DEX2;
        return two_d ? UC_S2DEX : UC_F3DEX;
    }
    uint32_t c = data_crc(d, 0x800);
    for (size_t i = 0; i < sizeof unsigned_ucodes / sizeof *unsigned_ucodes; i++)
        if (unsigned_ucodes[i].crc == c) return unsigned_ucodes[i].ucode;
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
    case 4: if (offset == 4 && (int16_t)w1 > 0) clip_ratio = (int16_t)w1; break;      /* G_MW_CLIP: the guard band */
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
    case 0x08: line3d(((w0 >> 16) & 0xFF) / 2, ((w0 >> 8) & 0xFF) / 2, (int)(w0 & 0xFF)); break; /* G_LINE3D */
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
    int fast3d = ucode == UC_F3D || ucode == UC_F3DDKR, scale = fast3d ? 10 : 2;
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
    case 0xB5: if (fast3d || line_ucode) /* G_LINE3D: Fast3D's indices by ten (its flag in the top byte), L3DEX's by two */
            line3d((int)((w1 >> 16) & 0xFF) / scale, (int)((w1 >> 8) & 0xFF) / scale, (int)(w1 & 0xFF));
        else { /* F3DEX's G_QUAD */
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
        if (op == 0xB9) rsp_mode_l = (rsp_mode_l & ~mask) | (w1 & mask);
        else rsp_mode_h = (rsp_mode_h & ~mask) | (w1 & mask);
        mode_h = rsp_mode_h;
        mode_l = rsp_mode_l;
        if (dump_cmds) record2(0xEF000000u | mode_h, mode_l);
        break; }
    case 0xB7: geometry |= gbi1_geometry(w1); break;
    case 0xB6: geometry &= ~gbi1_geometry(w1); break;
    case 0xB4: half1 = w1; break;          /* G_RDPHALF_1 */
    case 0xAF: load_ucode(); break;         /* G_LOAD_UCODE */
    case 0x00: case 0xBE: case 0xB3: case 0xB8: case 0x06: break; /* no-op, G_CULLDL, halves; lists in the task loop */
    default: if (op >= 0xC0) rdp(op, w0, w1);
    }
}

/* ---------------------------------------------------------------- F3DDKR: Rare's Fast3D */

/* F3DDKR (a racing game's, and Rare's games of its time) is Fast3D with its own geometry commands, as the game's
   decomp documents them (f3ddkr.h) and GLideN64 reads them:
   - G_DMA_MTX (0x01): a whole model-view-projection matrix into one of four slots (w0's bits 22-23), which becomes
     the one vertices are transformed by; the projection is the identity;
   - G_DMA_VTX (0x04): vertices of 10 bytes (x, y, z, then r, g, b, a: no texture coordinates), n - 1 in bits
     19-23, their first slot in bits 9-13, after those loaded already when bit 16 (append) is set with billboarding;
     with billboarding on (G_MOVEWORD 0x02) each vertex but the first is moved by the first's place in clip space,
     which is how a sprite is drawn facing the camera at a point in the world;
   - G_DMA_TRI (0x05): triangles of 16 bytes, each its flags (0x40: drawn from behind too), three vertex slots and
     each vertex's s and t (s10.5); bit 16: textured;
   - G_DMA_DL (0x07): n commands from an address, then back (no G_ENDDL ends them);
   - 0xBF (Fast3D's G_TRI1): offsets added to the matrices' and vertices' addresses (w0 and w1's 24 bits);
   - G_MOVEWORD 0x02 billboarding on or off, 0x0A the matrix slot by w1's bits 6-7. */
static float dkr_mtx[4][16];
static int dkr_billboard, dkr_next; /* dkr_next: where an appended load starts */
static uint32_t dkr_mtx_offset, dkr_vtx_offset;
static void dkr_select(int i) {
    memcpy(mvp, dkr_mtx[i & 3], sizeof mvp);
    memcpy(mv[mv_depth], dkr_mtx[i & 3], sizeof mvp); /* what lighting turns normals by: the same matrix */
}
static void dkr_vertices(uint32_t addr, int n, int v0) {
    for (int i = 0; i < n && v0 + i < 64; i++) {
        uint32_t a = addr + (uint32_t)i * 10;
        float x = (int16_t)rd16(a), y = (int16_t)rd16(a + 2), z = (int16_t)rd16(a + 4);
        Vtx *v = &vtx[v0 + i];
        v->x = x * mvp[0] + y * mvp[4] + z * mvp[8] + mvp[12];
        v->y = x * mvp[1] + y * mvp[5] + z * mvp[9] + mvp[13];
        v->z = x * mvp[2] + y * mvp[6] + z * mvp[10] + mvp[14];
        v->w = x * mvp[3] + y * mvp[7] + z * mvp[11] + mvp[15];
        if (dkr_billboard && v0 + i > 0) { v->x += vtx[0].x; v->y += vtx[0].y; v->z += vtx[0].z; v->w += vtx[0].w; }
        v->s = v->t = 0;
        v->shade = (Color){R[a + 6] / 255.0f, R[a + 7] / 255.0f, R[a + 8] / 255.0f, R[a + 9] / 255.0f};
        if (geometry & 0x10000) { /* G_FOG, as for Fast3D's vertices */
            float f = (v->z / (v->w > 1e-4f ? v->w : 1e-4f)) * (float)fog_mul + (float)fog_off;
            v->shade.a = (f < 0 ? 0 : f > 255 ? 255 : f) / 255.0f;
        }
        v->clip = v->fixed_xy = v->fixed_z = 0;
        if (gpu) gpu_match(R + a, v);
    }
}
/* 1 when the command is F3DDKR's own (the rest are Fast3D's: gbi1) */
static int f3ddkr(uint8_t op, uint32_t w0, uint32_t w1) {
    switch (op) {
    case 0x01: { /* G_DMA_MTX */
        if ((w0 & 0xFFFF) != 64) return 1;
        int index = (int)(w0 >> 16) & 15, multiply = 0;
        if (index == 0) index = (int)(w0 >> 22) & 3; /* the racing game's form; a later game's names it in bits 16-19 */
        else multiply = (int)(w0 >> 23) & 1;
        float m[16];
        matrix_read(phys(w1) + dkr_mtx_offset, m);
        if (multiply) matrix_mul(dkr_mtx[0], m, dkr_mtx[index & 3]); else memcpy(dkr_mtx[index & 3], m, sizeof m);
        dkr_select(index);
        return 1; }
    case 0x04: { /* G_DMA_VTX */
        if (w0 & 0x10000) { if (dkr_billboard) dkr_next = 1; } else dkr_next = 0;
        int n = (int)((w0 >> 19) & 31) + 1;
        dkr_vertices(phys(w1) + dkr_vtx_offset, n, dkr_next + (int)((w0 >> 9) & 31));
        dkr_next += n;
        return 1; }
    case 0x05: { /* G_DMA_TRI */
        int n = (int)(w0 >> 4) & 0xFFF, textured = (w0 >> 16) & 1, was_on = tex_on;
        uint32_t keep = geometry, a = phys(w1);
        tex_on = textured; /* the batch says (the list's G_TEXTURE turns texturing off and names only the tile) */
        for (int i = 0; i < n; i++, a += 16) {
            int i0 = R[a + 1], i1 = R[a + 2], i2 = R[a + 3];
            if (i0 >= 64 || i1 >= 64 || i2 >= 64) continue;
            /* the microcode culls the back unless the triangle says otherwise (F3DEX2's G_CULL_BACK and _FRONT) */
            geometry = (geometry & ~0x600u) | (R[a] & 0x40 ? 0 : vp_scale[0] > 0 ? 0x400u : 0x200u);
            int idx[3] = {i0, i1, i2};
            for (int k = 0; k < 3; k++) {
                vtx[idx[k]].s = (int16_t)rd16(a + 4 + k * 4) / 32.0f;
                vtx[idx[k]].t = (int16_t)rd16(a + 6 + k * 4) / 32.0f;
            }
            triangle(i0, i1, i2);
        }
        geometry = keep; tex_on = was_on;
        dkr_next = 0;
        return 1; }
    case 0xBF: dkr_mtx_offset = w0 & 0xFFFFFF; dkr_vtx_offset = w1 & 0xFFFFFF; return 1; /* G_DMA_OFFSETS */
    case 0xBC:
        if ((w0 & 0xFF) == 0x02) { dkr_billboard = w1 & 1; return 1; }
        if ((w0 & 0xFF) == 0x0A) { dkr_select((int)(w1 >> 6) & 3); return 1; }
        return 0;
    }
    return 0;
}

/* ---------------------------------------------------------------- S2DEX: backgrounds and sprites */

/* S2DEX draws as the microcode does: it sends the RDP commands that load the texture, set up its tile and draw it
   (texture rectangles; triangles for a rotated sprite; a background in strips that fit TMEM), so its drawing goes
   through the same RDP as everything else and PORT_RDPDUMP records it. */
static void rdp_send(uint32_t w0, uint32_t w1) { rdp((uint8_t)(w0 >> 24), w0, w1); }
static void set_timage(int fmt, int siz, int width, uint32_t addr) {
    rdp_send(0xFD000000u | (uint32_t)fmt << 21 | (uint32_t)siz << 19 | (uint32_t)((width - 1) & 0xFFF), addr);
}
static void set_tile(int tile, int fmt, int siz, int line, int tmem, int pal, int cmt, int cms) {
    rdp_send(0xF5000000u | (uint32_t)fmt << 21 | (uint32_t)siz << 19 | (uint32_t)(line & 0x1FF) << 9 | (uint32_t)(tmem & 0x1FF),
             (uint32_t)tile << 24 | (uint32_t)(pal & 15) << 20 | (uint32_t)cmt << 18 | (uint32_t)cms << 8);
}
static uint32_t q12(int v) { return (uint32_t)(v < 0 ? 0 : v > 0xFFF ? 0xFFF : v); }
static void set_tile_size(int tile, int uls, int ult, int lrs, int lrt) { /* in quarter texels */
    rdp_send(0xF2000000u | q12(uls) << 12 | q12(ult), (uint32_t)tile << 24 | q12(lrs) << 12 | q12(lrt));
}
/* A texture rectangle's four words, recorded and drawn: the RDP's walker in one or two cycles, else copy or fill. */
static void texrect_words(uint32_t w0, uint32_t w1, uint32_t h1, uint32_t h2, int flip) {
    uint32_t rect[4] = {w0, w1, h1, h2};
    record(rect, 4);
    if (rdp_walks_rect()) /* the RDP's words: lower right in the first, tile and upper left in the second */
        rdp_rect((int)(w1 >> 24) & 7, (w0 >> 12) & 0xFFF, w0 & 0xFFF, (w1 >> 12) & 0xFFF, w1 & 0xFFF, 1,
                 (int16_t)(h1 >> 16), (int16_t)h1, (int16_t)(h2 >> 16), (int16_t)h2, flip);
    else tex_rect((w1 >> 24) & 7, ((w1 >> 12) & 0xFFF) / 4.0f, (w1 & 0xFFF) / 4.0f, ((w0 >> 12) & 0xFFF) / 4.0f,
                  (w0 & 0xFFF) / 4.0f, (int16_t)(h1 >> 16) / 32.0f, (int16_t)h1 / 32.0f,
                  (int16_t)(h2 >> 16) / 1024.0f, (int16_t)h2 / 1024.0f, flip);
}
/* A rectangle in quarter pixels (x0, y0 inclusive, x1, y1 exclusive), its texture from s, t (s10.5) in steps of
   dsdx, dtdy (s5.10) a pixel; cut at the screen's top and left edges as the microcode cuts it, the texture moved on
   by as much. In copy mode the RDP takes the lower right inclusive and four texels a step. */
static void s2d_rect(int tile, int x0, int y0, int x1, int y1, int s, int t, int dsdx, int dtdy) {
    if (x0 < 0) { s += (int)(((int64_t)-x0 * dsdx) >> 7); x0 = 0; }
    if (y0 < 0) { t += (int)(((int64_t)-y0 * dtdy) >> 7); y0 = 0; }
    if (x1 <= x0 || y1 <= y0) return;
    if (cycle_type() >= 2) { x1 -= 4; y1 -= 4; dsdx *= 4; if (x1 < x0 || y1 < y0) return; }
    dsdx = dsdx > 32767 ? 32767 : dsdx < -32768 ? -32768 : dsdx;
    dtdy = dtdy > 32767 ? 32767 : dtdy < -32768 ? -32768 : dtdy;
    texrect_words(0xE4000000u | q12(x1) << 12 | q12(y1), (uint32_t)tile << 24 | q12(x0) << 12 | q12(y0),
                  (uint32_t)(uint16_t)s << 16 | (uint16_t)t, (uint32_t)(uint16_t)dsdx << 16 | (uint16_t)dtdy, 0);
}

static void s2d_load(uint32_t txtr) { /* uObjTxtr: a block, a tile or a palette into TMEM, through tile 7 */
    uint32_t type = rd32(txtr), src = phys(rd32(txtr + 4));
    int tm = rd16(txtr + 8), b = rd16(txtr + 10), c = rd16(txtr + 12);
    if (type == 0x00000030) { /* a palette: its first entry (phead) and its count minus one (pnum) */
        set_timage(0, 2, 1, src);
        set_tile(7, 0, 0, 0, tm, 0, 0, 0); /* as gDPLoadTLUT sets it: the RDP loads a palette through a 4-bit tile */
        rdp_send(0xF0000000u, 7u << 24 | (uint32_t)(b & 0x3FF) << 14);
    } else if (type == 0x00001033) { /* a block: tsize 8-byte words minus one (four 16-bit texels each), tline the dxt */
        set_timage(0, 2, 1, src);
        set_tile(7, 0, 2, 0, tm, 0, 0, 0);
        rdp_send(0xF3000000u, 7u << 24 | (uint32_t)((((b + 1) << 2) - 1) & 0xFFF) << 12 | (uint32_t)(c & 0xFFF));
    } else { /* a tile: its width in 8-byte words times four, minus one, and its height in lines times four, minus one */
        int words = (b + 1) >> 2, lines = (c + 1) >> 2;
        if (words <= 0 || lines <= 0) return;
        set_timage(0, 2, words * 4, src);
        set_tile(7, 0, 2, words, tm, 0, 0, 0);
        rdp_send(0xF4000000u, 7u << 24 | (uint32_t)((words * 4 - 1) << 2) << 12 | (uint32_t)((lines - 1) << 2));
    }
}
/* A uObjSprite's place (objX, objY, s10.2), scale (u5.10) and image size in texels, its image set up as tile 0
   (the render tile; 7 is the load tile). */
typedef struct { float x, y, sw, sh; int iw, ih; } Sprite;
static Sprite s2d_sprite_tile(uint32_t sp) {
    Sprite o = {(int16_t)rd16(sp) / 4.0f, (int16_t)rd16(sp + 8) / 4.0f, rd16(sp + 2) / 1024.0f, rd16(sp + 10) / 1024.0f,
                rd16(sp + 4) >> 5, rd16(sp + 12) >> 5};
    if (o.sw == 0) o.sw = 1;
    if (o.sh == 0) o.sh = 1;
    set_tile(0, R[sp + 20], R[sp + 21], rd16(sp + 16), rd16(sp + 18), R[sp + 22], 2, 2);
    set_tile_size(0, 0, 0, (o.iw - 1) << 2, (o.ih - 1) << 2);
    return o;
}

#include "render_gpu.h"
static void s2d_sprite(uint32_t sp) { /* an unrotated rectangle, scaled by the sprite's own scale */
    Sprite o = s2d_sprite_tile(sp);
    int x = (int16_t)rd16(sp), y = (int16_t)rd16(sp + 8), sw = rd16(sp + 2), sh = rd16(sp + 10);
    int iw = rd16(sp + 4), ih = rd16(sp + 12), flags = R[sp + 23];
    if (!sw) sw = 1024;
    if (!sh) sh = 1024;
    int dsdx = sw, dtdy = sh, s = 0, t = 0; /* the scale is texels a pixel (u5.10), the rectangle's step (s5.10) */
    if (flags & 0x01) { s = (o.iw << 5) - 1; dsdx = -dsdx; } /* G_OBJ_FLAG_FLIPS */
    if (flags & 0x10) { t = (o.ih << 5) - 1; dtdy = -dtdy; } /* G_OBJ_FLAG_FLIPT */
    s2d_rect(0, x, y, x + (int)(((int64_t)iw << 7) / sw), y + (int)(((int64_t)ih << 7) / sh), s, t, dsdx, dtdy);
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
   the screen as (A x + B y + X, C x + D y + Y) and drawn as two textured triangles. A racing game's rev counter
   needle turns this way; drawn unrotated, it was a grey streak above the gauge. */
static void s2d_sprite_rotated(uint32_t sp) {
    Sprite o = s2d_sprite_tile(sp);
    float A = obj_mtx[0], B = obj_mtx[1], C = obj_mtx[2], D = obj_mtx[3], X = obj_mtx[4], Y = obj_mtx[5];
    float det = A * D - B * C, w = o.iw / o.sw, h = o.ih / o.sh;
    if (fabsf(det) < 1e-6f || w <= 0 || h <= 0) return;
    if (gpu) { gpu_sprite_rotated(&o, obj_mtx); return; }
    float cx[4] = {o.x, o.x + w, o.x + w, o.x}, cy[4] = {o.y, o.y, o.y + h, o.y + h};
    float ts[4] = {0, (float)o.iw, (float)o.iw, 0}, tt[4] = {0, 0, (float)o.ih, (float)o.ih};
    SetupV v[4];
    for (int i = 0; i < 4; i++)
        v[i] = (SetupV){A * cx[i] + B * cy[i] + X, C * cx[i] + D * cy[i] + Y, 0, ts[i] * 32.0f, tt[i] * 32.0f, 1, {255, 255, 255, 255}};
    static const int tri[2][3] = {{0, 1, 2}, {0, 2, 3}};
    for (int k = 0; k < 2; k++) {
        uint32_t cmd[44];
        int n = tri_setup(&v[tri[k][0]], &v[tri[k][1]], &v[tri[k][2]], 0, 1, 0, 0, 0, cmd);
        record(cmd, n);
        rdp_triangle(cmd);
    }
}
/* uObjBg: a background straight from memory, scaled (G_OBJ_BG1CYC) or not (G_OBJ_BGRECTCOPY). Like the microcode,
   it loads the image a strip of lines at a time, as many as fit TMEM (half of it for a palette's image), and draws
   each strip as rectangles, cut where the image wraps around to its start. */
static void s2d_bg(uint32_t bg, int scaled) {
    int ix = rd16(bg), iw = rd16(bg + 2) >> 2, fx = (int16_t)rd16(bg + 4), fw = rd16(bg + 6);
    int iy = rd16(bg + 8), ih = rd16(bg + 10) >> 2, fy = (int16_t)rd16(bg + 12), fh = rd16(bg + 14);
    uint32_t image = phys(rd32(bg + 16));
    int fmt = R[bg + 22], siz = R[bg + 23], pal = rd16(bg + 24);
    int scw = scaled ? rd16(bg + 28) : 1024, sch = scaled ? rd16(bg + 30) : 1024;
    if (!scw) scw = 1024;
    if (!sch) sch = 1024;
    if (iw <= 0 || ih <= 0 || fw <= 0 || fh <= 0) return;
    if (gpu) { /* the WebGL renderer takes the whole background as one texture */
        int w = fw >> 2, h = fh >> 2;
        if (w > 1024 || h > 1024) return;
        for (int y = 0; y < h; y++)
            for (int x = 0; x < w; x++) {
                int s = (int)((ix / 32.0f) + x * (scw / 1024.0f)) % iw, t = (int)((iy / 32.0f) + y * (sch / 1024.0f)) % ih;
                Color c;
                switch (siz) {
                case 0: { uint8_t b = R[image + (uint32_t)(t * iw + s) / 2]; int v = (s & 1) ? b & 15 : b >> 4;
                    c = fmt == 2 ? unpack5551(tlut[(pal * 16 + v) & 255]) : (Color){by15[v], by15[v], by15[v], 1}; break; }
                case 1: { uint8_t v = R[image + (uint32_t)(t * iw + s)];
                    c = fmt == 2 ? unpack5551(tlut[v]) : (Color){by255[v], by255[v], by255[v], 1}; break; }
                case 2: c = unpack5551(rd16(image + (uint32_t)(t * iw + s) * 2)); break;
                default: { uint32_t a = image + (uint32_t)(t * iw + s) * 4;
                    c = (Color){by255[R[a]], by255[R[a + 1]], by255[R[a + 2]], by255[R[a + 3]]}; }
                }
                gpu_scratch[y * w + x] = gpu_rgba(c);
            }
        gpu_bg(fx >> 2, fy >> 2, w, h, gpu_scratch);
        return;
    }
    /* a 4-bit image loads as 8-bit texels of half the width; a line is padded to whole 8-byte words */
    int load_siz = siz ? siz : 1, load_w = siz ? iw : (iw + 1) / 2;
    int line = ((load_w << load_siz >> 1) + 7) / 8;
    if (siz == 3) line = (load_w * 2 + 7) / 8; /* 32-bit texels: their halves in TMEM's two banks */
    int room = (fmt == 2 ? 256 : 512) / (line ? line : 1); /* lines that fit */
    if (room <= 0) return;
    int filtered = ((mode_h >> 12) & 3) != 0 && cycle_type() < 2; /* bilinear reads the line below too */
    int rows = fh >> 2;
    int64_t t0 = (int64_t)iy << 5; /* the image's line at the frame's top, in 1/1024 texels */
    int r = 0;
    while (r < rows) {
        int64_t ta = t0 + (int64_t)r * sch;
        int first = (int)((ta >> 10) % ih);
        /* the screen lines this strip draws: their image lines from `first` on fit, and don't pass the image's end */
        int r1 = r + 1;
        while (r1 < rows) {
            int64_t tb = t0 + (int64_t)r1 * sch;
            int last = first + (int)((tb >> 10) - (ta >> 10)) + filtered;
            if (last - first + 1 > room || ((tb >> 10) - (ta >> 10)) + first >= ih) break;
            r1++;
        }
        int64_t tl = t0 + (int64_t)(r1 - 1) * sch;
        int last = first + (int)((tl >> 10) - (ta >> 10)) + filtered;
        if (last >= ih) last = ih - 1;
        set_timage(fmt, load_siz, load_w, image);
        set_tile(7, fmt, load_siz, line, 0, 0, 0, 0);
        rdp_send(0xF4000000u | (uint32_t)(first << 2), 7u << 24 | (uint32_t)((load_w - 1) << 2) << 12 | (uint32_t)(last << 2));
        set_tile(0, fmt, siz, line, 0, pal, 2, 0);
        set_tile_size(0, 0, first << 2, (iw - 1) << 2, last << 2);
        int t = (int)((((ta >> 10) % ih) << 5) | ((ta & 1023) >> 5)); /* s10.5 */
        int y0 = fy + (r << 2), y1 = fy + (r1 << 2);
        /* across: the image wraps at its width, so the rectangle is cut there */
        int64_t sa = (int64_t)ix << 5;
        int x = 0, cols = fw >> 2;
        while (x < cols) {
            int64_t sx = sa + (int64_t)x * scw;
            int col = (int)((sx >> 10) % iw), x1 = x + 1;
            while (x1 < cols && col + (int)(((sa + (int64_t)x1 * scw) >> 10) - (sx >> 10)) < iw) x1++;
            s2d_rect(0, fx + (x << 2), y0, fx + (x1 << 2), y1, (col << 5) | (int)((sx & 1023) >> 5), t, scw, sch);
            x = x1;
        }
        r = r1;
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

static void dump_memory(const char *dir, unsigned long n, const char *what) {
    char path[1024];
    snprintf(path, sizeof path, "%s/%06lu.%s", dir, n, what);
    FILE *f = fopen(path, "wb");
    uint32_t size = rd32(0x80000318u); /* osMemSize: 4 or 8 MB */
    if (size != 0x800000) size = 0x400000;
    if (f) { fwrite(R + 0x80000000u, 1, size, f); fclose(f); }
}
static void render_list(uint32_t task);
void port_render_task(uint32_t task) {
    make_tables();
    R = port_memory();
    gpu_use(port_gpu_ready());
    if (!render_tasks) for (int i = 0; i < MAXW * MAXH; i++) zbuf_private[i] = 0x3FFFF; /* the farthest depth */
    render_tasks++;
    static const char *dump_dir;
    static unsigned long dump_first, dump_last;
    if (render_tasks == 1 && (dump_dir = getenv("PORT_RDPDUMP"))) {
        const char *r = getenv("PORT_RDPDUMP_TASKS");
        dump_first = r ? strtoul(r, NULL, 10) : 1;
        dump_last = r && strchr(r, '-') ? strtoul(strchr(r, '-') + 1, NULL, 10) : dump_first;
    }
    int dumping = dump_dir && !gpu && render_tasks >= dump_first && render_tasks <= dump_last;
    if (dumping) {
        char path[1024];
        dump_memory(dump_dir, render_tasks, "before");
        snprintf(path, sizeof path, "%s/%06lu.hidden", dump_dir, render_tasks); /* RDRAM's hidden bits: 0 to 3, */
        FILE *h = fopen(path, "wb");                                              /* or 4 where clean */
        if (h) {
            for (uint32_t i = 0, n = rd32(0x80000318u) == 0x800000 ? 0x400000 : 0x200000; i < n; i++)
                fputc((cvg_hole[i] & 4) ? cvg_hole[i] & 3 : 4, h);
            fclose(h);
        }
        snprintf(path, sizeof path, "%s/%06lu.cmds", dump_dir, render_tasks);
        dump_cmds = fopen(path, "wb");
        /* the state earlier tasks left, as commands: the replay starts from the RDP's power-on state */
        record2(0xEF000000u | mode_h, mode_l);
        record2(0xFC000000u | combine0, combine1);
        record2(0xED000000u | (uint32_t)scissor_q[0] << 12 | (uint32_t)scissor_q[1], (uint32_t)scissor_q[2] << 12 | (uint32_t)scissor_q[3]);
        record2(0xFF000000u | (uint32_t)(cimg_siz & 3) << 19 | (uint32_t)(cimg_width - 1), cimg_addr);
        record2(0xFE000000u, zimg_addr);
    }
    render_list(task);
    if (dumping) {
        if (dump_cmds) fclose(dump_cmds);
        dump_cmds = NULL;
        dump_memory(dump_dir, render_tasks, "after");
    }
}
/* A recorded RDP command stream (PORT_RDPDUMP's, big-endian words) drawn straight into the game's memory, without
   the RSP: the same input tools/n64_rdp_compare.py gives angrylion's renderer, so the two RDPs compare alone.
   `hidden`, RDRAM's hidden bits as recorded (0 to 3, or 4 where clean), may be NULL. */
void port_rdp_replay(const uint8_t *cmds, size_t bytes, const uint8_t *hidden, size_t hidden_n) {
    make_tables();
    R = port_memory();
    for (size_t i = 0; i < hidden_n && i < sizeof cvg_hole; i++) cvg_hole[i] = hidden[i] & 4 ? 0 : (uint8_t)(4 | (hidden[i] & 3));
    size_t n = bytes / 4, at = 0;
    uint32_t w[44];
    while (at < n) {
        uint32_t w0 = (uint32_t)cmds[at * 4] << 24 | (uint32_t)cmds[at * 4 + 1] << 16 | (uint32_t)cmds[at * 4 + 2] << 8 | cmds[at * 4 + 3];
        uint32_t id = (w0 >> 24) & 0x3F, words = 2;
        if (id >= 0x08 && id <= 0x0F) words = 8 + (id & 4 ? 16 : 0) + (id & 2 ? 16 : 0) + (id & 1 ? 4 : 0);
        else if (id == 0x24 || id == 0x25) words = 4;
        if (at + words > n) break;
        for (uint32_t k = 0; k < words; k++) {
            const uint8_t *b = cmds + (at + k) * 4;
            w[k] = (uint32_t)b[0] << 24 | (uint32_t)b[1] << 16 | (uint32_t)b[2] << 8 | b[3];
        }
        at += words;
        if (id >= 0x08 && id <= 0x0F) rdp_triangle(w);
        else if (id == 0x24 || id == 0x25) {
            if (rdp_walks_rect())
                rdp_rect((int)(w[1] >> 24) & 7, (w[0] >> 12) & 0xFFF, w[0] & 0xFFF, (w[1] >> 12) & 0xFFF, w[1] & 0xFFF, 1,
                         (int16_t)(w[2] >> 16), (int16_t)w[2], (int16_t)(w[3] >> 16), (int16_t)w[3], id == 0x25);
            else tex_rect((int)(w[1] >> 24) & 7, ((w[1] >> 12) & 0xFFF) / 4.0f, (w[1] & 0xFFF) / 4.0f, ((w[0] >> 12) & 0xFFF) / 4.0f,
                          (w[0] & 0xFFF) / 4.0f, (int16_t)(w[2] >> 16) / 32.0f, (int16_t)w[2] / 32.0f,
                          (int16_t)(w[3] >> 16) / 1024.0f, (int16_t)w[3] / 1024.0f, id == 0x25);
        } else rdp((uint8_t)(0xC0 | id), w[0], w[1]);
    }
}
static void render_list(uint32_t task) {
    ucode = identify_ucode(rd32(task + 24)); /* the microcode's data names it */
    /* The GBI 2 microcode starts each task with fresh other-mode registers. The RDP keeps its old mode until a
       G_SETOTHERMODE sends the new pair. In a racing game's HUD task, a partial update was inheriting alpha
       dither from the previous task. Dither raised transparent texels above the alpha threshold of 1. */
    rsp_mode_h = (ucode == UC_F3DEX2 || ucode == UC_S2DEX2) ? 0x080CFFu : mode_h;
    rsp_mode_l = (ucode == UC_F3DEX2 || ucode == UC_S2DEX2) ? 0 : mode_l;
    static long trace = -1;
    if (trace < 0) trace = getenv("PORT_DLTRACE") ? atol(getenv("PORT_DLTRACE")) : 0;
    tracing = trace > 0 && (long)render_tasks >= trace && (long)render_tasks < trace + 6;
    if (tracing) fprintf(stderr, "== task %lu, %s (code %08X + %X, data %08X + %X)\n", render_tasks, ucode_names[ucode],
                         rd32(task + 16), rd32(task + 20), rd32(task + 24), rd32(task + 28));
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
    int depth = 0, left[19]; /* left[depth]: commands to go in a list F3DDKR's G_DMA_DL counted (-1: to its end) */
    left[0] = -1;
    dkr_next = 0;
    uint32_t pc = phys(rd32(task + 48));
    for (long steps = 0; steps < 2000000; steps++) {
        if (left[depth] == 0) { pc = stack[--depth]; continue; } /* a counted list done: back */
        uint32_t w0 = rd32(pc), w1 = rd32(pc + 4);
        uint8_t op = w0 >> 24;
        if (tracing) fprintf(stderr, "%*s%08X %08X %08X\n", depth * 2, "", pc, w0, w1);
        pc += 8;
        if (left[depth] > 0) left[depth]--;
        if (ucode == UC_F3DDKR && op == 0x07) { /* G_DMA_DL: n commands from w1 */
            if (depth < 18) { stack[depth++] = pc; left[depth] = (int)(w0 >> 16) & 0xFF; pc = phys(w1); }
            continue;
        }
        if (ucode == UC_F3DDKR && f3ddkr(op, w0, w1)) continue;
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
            if (!((w0 >> 16) & 1) && depth < 18) { stack[depth++] = pc; left[depth] = -1; }
            pc = phys(w1);
        } else if (op == op_end) { /* G_ENDDL */
            if (!depth) break;
            pc = stack[--depth];
        } else if (op == 0xE4 || op == 0xE5) { /* a textured rectangle: two more commands carry s, t and steps */
            uint32_t h1 = rd32(pc + 4), h2 = rd32(pc + 12);
            pc += 16;
            texrect_words(w0, w1, h1, h2, op == 0xE5);
        } else if (ucode == UC_S2DEX || ucode == UC_S2DEX2) s2dex(op, w0, w1);
        else if (one) gbi1(op, w0, w1);
        else f3dex2(op, w0, w1);
        if (branch_to) { pc = phys(branch_to); branch_to = 0; } /* G_BRANCH_Z taken: the rest of the list replaced */
    }
    if (gpu) gpu_flush();
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
static struct { int known, width, bpp, lines; float yscale; int gamma, gamma_dither, divot, dedither, black; } vi;
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
/* osViBlack: libultra starts the VI blanked (osCreateViManager) and a game un-blanks it once its first frame is drawn,
   and blanks it again around a change of mode; while it's blanked the screen is black whatever the buffer holds. Not
   honoured, every frame the game handed over before it had drawn into the buffer showed the memory's leftovers
   (green, or the last screen's pixels in the new mode's layout) for a few frames at each change of screen. */
void port_vi_black(int on) { vi.black = on != 0; }
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
            int cvg = cvg_px(base + (uint32_t)(y * w + x) * (uint32_t)bytes, bytes);
            if (cvg >= 8) continue;
            int around[8], n = 0; /* the fully covered neighbours */
            for (int dy = -1; dy <= 1; dy++)
                for (int dx = -1; dx <= 1; dx++) {
                    int i = (y + dy) * w + x + dx;
                    if ((dx || dy) && cvg_px(base + (uint32_t)i * (uint32_t)bytes, bytes) >= 8) around[n++] = i;
                }
            if (n < 2) continue; /* nothing around it to go by */
            for (int ch = 0; ch < 3; ch++) {
                /* the second-smallest and second-largest of the pixel and those neighbours, found in one pass */
                int centre = src[(y * w + x) * 3 + ch], first = src[around[0] * 3 + ch];
                int lo = centre < first ? centre : first, lo2 = centre < first ? first : centre, hi = lo2, hi2 = lo;
                for (int k = 1; k < n; k++) {
                    int v = src[around[k] * 3 + ch];
                    if (v < lo) { lo2 = lo; lo = v; } else if (v < lo2) lo2 = v;
                    if (v > hi) { hi2 = hi; hi = v; } else if (v > hi2) hi2 = v;
                }
                int bg = hi2 + lo2 - centre;
                bg = bg < 0 ? 0 : bg > 255 ? 255 : bg;
                int out = centre + (bg - centre) * (8 - cvg) / 8;
                rgb[(y * w + x) * 3 + ch] = (uint8_t)out;
            }
        }
    free(src);
}

/* The VI's dedither filter (on fully covered pixels of a 16-bit frame): each 5-bit channel given back the 3 bits the
   RDP's dither cut, from how many of its eight neighbours are above or below it. The three channels are compared
   at once, one per byte of a word (their 5 bits, under a guard bit that keeps one byte's borrow from the next):
   compared one by one, this filter took a racing game's 640x480 frames 6 ms each in the browser. */
static void vi_dedither(uint8_t *rgb, int w, int h, uint32_t base) {
    size_t n = (size_t)w * h;
    uint32_t *pk = malloc(n * 4);
    for (size_t i = 0; i < n; i++) pk[i] = (uint32_t)(rgb[i * 3] >> 3) | (uint32_t)(rgb[i * 3 + 1] >> 3) << 8
                                          | (uint32_t)(rgb[i * 3 + 2] >> 3) << 16;
    const uint32_t guard = 0x808080u, one = 0x010101u;
    for (int y = 1; y < h - 1; y++)
        for (int x = 1; x < w - 1; x++) {
            int i = y * w + x;
            if (cvg_px(base + (uint32_t)i * 2, 2) < 8) continue;
            uint32_t c = pk[i], c1 = c + one, above = 0, at_least = 0; /* per byte: neighbours above, and not below */
            const uint32_t nb[8] = {pk[i - w - 1], pk[i - w], pk[i - w + 1], pk[i - 1], pk[i + 1], pk[i + w - 1],
                                    pk[i + w], pk[i + w + 1]};
            for (int k = 0; k < 8; k++) {
                uint32_t v = nb[k] | guard;
                above += ((v - c1) & guard) >> 7;
                at_least += ((v - c) & guard) >> 7;
            }
            for (int ch = 0; ch < 3; ch++) {
                int cc = (int)(c >> (8 * ch)) & 31;
                int sum = (int)(above >> (8 * ch) & 15) + (int)(at_least >> (8 * ch) & 15) - 8;
                int out = (cc << 3) + (cc << 3 >> 5) + sum;
                rgb[i * 3 + ch] = (uint8_t)(out < 0 ? 0 : out > 255 ? 255 : out);
            }
        }
    free(pk);
}

/* The divot filter: a partly covered pixel takes the median of itself and its two neighbours on the line, which
   removes the one-pixel notches anti-aliasing leaves along silhouettes. */
static void vi_divot(uint8_t *rgb, int w, int h, uint32_t base, int bytes) {
    uint8_t *src = malloc((size_t)w * h * 3);
    memcpy(src, rgb, (size_t)w * h * 3);
    for (int y = 0; y < h; y++)
        for (int x = 1; x < w - 1; x++) {
            if (cvg_px(base + (uint32_t)(y * w + x) * (uint32_t)bytes, bytes) >= 8) continue;
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
    static float curve[256];
    if (curve[255] == 0) for (int i = 0; i < 256; i++) curve[i] = sqrtf(i / 255.0f) * 255.0f;
    for (int i = 0; i < w * h * 3; i++) {
        float v = curve[rgb[i]] + (vi.gamma_dither ? noise() : 0.0f);
        rgb[i] = (uint8_t)(v > 255 ? 255 : v);
    }
}

int port_live_active(void); /* live.c */
void port_live_frame(const uint8_t *rgb, int w, int h);
/* The frame the VI shows next (osViSwapBuffer): saved when PORT_FRAMES names a folder, and shown live (PORT_LIVE). */
void port_frame_shown(uint32_t fb, unsigned long n) {
    make_tables();
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
    if (addr >= 0x800000) return; /* no frame in RDRAM to show (the subtraction below would wrap to a giant height) */
    if ((size_t)addr + (size_t)w * h * bytes > 0x800000) h = (int)((0x800000 - addr) / ((uint32_t)w * bytes));
    if (!vi.black && gpu && gpu_present(addr, w, h, vi.gamma)) return; /* the GPU's frame, shown by the page */
    uint8_t *rgb = malloc((size_t)w * h * 3);
    uint32_t base = 0x80000000u | addr;
    static uint8_t of5[32], of8[256]; /* each channel's value as unpack5551 and unpack8888 give it, times 255 */
    if (!of5[31]) {
        for (int i = 0; i < 32; i++) of5[i] = (uint8_t)(i / 31.0f * 255);
        for (int i = 0; i < 256; i++) of8[i] = (uint8_t)(i / 255.0f * 255);
    }
    for (int i = 0; i < w * h; i++) {
        if (bytes == 4) {
            uint32_t c = rd32(base + (uint32_t)i * 4);
            rgb[i * 3] = of8[c >> 24]; rgb[i * 3 + 1] = of8[(c >> 16) & 255]; rgb[i * 3 + 2] = of8[(c >> 8) & 255];
        } else {
            uint16_t c = rd16(base + (uint32_t)i * 2);
            rgb[i * 3] = of5[(c >> 11) & 31]; rgb[i * 3 + 1] = of5[(c >> 6) & 31]; rgb[i * 3 + 2] = of5[(c >> 1) & 31];
        }
    }
    if (vi.black) memset(rgb, 0, (size_t)w * h * 3);
    else {
        if (vi.dedither && bytes == 2) vi_dedither(rgb, w, h, base);
        vi_filter(rgb, w, h, base, bytes);
        if (vi.divot) vi_divot(rgb, w, h, base, bytes);
        if (vi.gamma) vi_gamma(rgb, w, h);
    }
    port_live_frame(rgb, w, h);
    if (save) {
        char path[1024];
        snprintf(path, sizeof path, "%s/frame_%05lu.png", dir, n);
        save_png(path, rgb, w, h);
    }
    free(rgb);
}
