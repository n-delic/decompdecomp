/*
 * The renderer's GPU path (included by render.c, which it shares its state with): with a GPU renderer on the page
 * (port_gpu_ready: the browser build's WebGL2, web/gl.js), each primitive the RSP side has worked out (a triangle
 * in screen space, a rectangle, a fill) is sent with the RDP's state to be drawn there, in place of render.c's
 * per-pixel raster. The display lists are read, transformed, lit and clipped as for the software path; only the
 * pixels move. Textures are decoded from TMEM here, as the sampler sees them (texel_wrapped), once per change of
 * what's loaded, and sent only when the page hasn't been sent the same texels (by their hash). The combiner, the
 * blender, alpha compare and depth are the page's (gl.js, from the same other modes and combine words).
 *
 * What only the software path does (docs/PORTING.md, "The browser build's renderers"): frames are drawn on the
 * GPU, not into RDRAM, so a game that reads its frame buffer back (a frame used as a texture, a screenshot for a
 * pause screen) sees what the CPU last wrote there; the VI's anti-aliasing, dedither and divot filters, the RDP's
 * coverage and its dither aren't drawn. A frame the CPU writes (a logo, a movie) is still shown from RDRAM: the
 * frame is taken from the GPU only while its RDRAM is as it was when the GPU last drew it.
 *
 * The page reads what's sent from the host's memory, little-endian: a list of commands (five u32 words each:
 * GPU_TEXTURE id, width, height, its first texel's index in the pixels; GPU_DRAW the draw's index; GPU_CLEAR_DEPTH
 * the z image), the draws (GpuDraw), their vertices (GPU_VERTEX floats each) and the decoded textures (RGBA, 8 bits
 * a channel).
 */

int port_gpu_ready(void);                                     /* web/live.c: the page's GPU renderer (0: none) */
/* web/live.c: the page runs the commands; draws and texels are named by their index in `draws` and `pixels` */
void port_gpu_run(const uint32_t *commands, uint32_t words, const void *draws, const float *vertices, const uint32_t *pixels);
int port_gpu_present(uint32_t addr, int w, int h, int flags); /* web/live.c: 1 when the page showed it */
/* Elsewhere (the native host, the Miyoo's) there's no GPU: every frame is drawn in software. */
__attribute__((weak)) int port_gpu_ready(void) { return 0; }
__attribute__((weak)) void port_gpu_run(const uint32_t *commands, uint32_t words, const void *draws, const float *vertices,
                                        const uint32_t *pixels) { (void)commands; (void)words; (void)draws; (void)vertices; (void)pixels; }
__attribute__((weak)) int port_gpu_present(uint32_t addr, int w, int h, int flags) { (void)addr; (void)w; (void)h; (void)flags; return 0; }

enum { GPU_TEXTURE = 1, GPU_DRAW = 2, GPU_CLEAR_DEPTH = 3 };
enum { GPU_KIND_COMBINE = 0, GPU_KIND_COPY = 1, GPU_KIND_FILL = 2 };
enum { GPU_DEPTH = 1, GPU_ONE_TEXEL = 2, GPU_POINT = 4, GPU_COPY_ALPHA = 8, GPU_TEXTURED = 16 };
#define GPU_VERTEX 10 /* x, y (the frame's pixels), z (0 to 1), w, s, t (texels before the tile's shift), r, g, b, a */

typedef struct {
    uint32_t tex;                   /* the texture's id (0: none) */
    int32_t tex_w, tex_h;           /* its size: the mask's, or the tile's without one */
    float uls, ult, scale_s, scale_t; /* the tile's corner and its shift as a factor */
    int32_t size_w, size_h, cms, cmt, masks, maskt;
} GpuTile;

/* One draw: what the page needs to draw its vertices as the RDP would. Equal draws in a row become one. */
typedef struct {
    uint32_t kind, flags, combine0, combine1, mode_h, mode_l;
    float prim[4], env[4], fog[4], blend[4], key_center[4], key_scale[4];
    float k4, k5, prim_lod_frac, prim_min_level, prim_z, fill[4];
    int32_t tex_tile, tex_levels;
    int32_t scissor[4];
    uint32_t cimg, cimg_width, cimg_height, cimg_siz, zimg;
    GpuTile tiles[8];
    uint32_t first, count; /* its vertices: the first and how many (not part of what's compared) */
} GpuDraw;
_Static_assert(sizeof(GpuDraw) == 156 * 4, "web/gl.js reads a GpuDraw as 156 words (DRAW_WORDS)");

#define GPU_MAX_COMMANDS 65536
#define GPU_MAX_DRAWS 8192
#define GPU_MAX_VERTICES 196608
#define GPU_MAX_PIXELS (2u << 20) /* the textures sent in one run: 8 MB of RGBA */
/* (made the first time a GPU renderer is there: the native and Miyoo hosts never have one) */
static uint32_t *gpu_commands;
static GpuDraw *gpu_draws;
static float *gpu_vertices;
static uint32_t *gpu_pixels;
static uint32_t gpu_ncommands, gpu_ndraws, gpu_nvertices, gpu_npixels;
static GpuDraw *gpu_open; /* the draw vertices are still added to */
static uint32_t gpu_frame;   /* bumped by every frame shown: the textures' last use */
static uint32_t gpu_uploads; /* textures sent: a draw after one is never joined to the open draw */

/* The frame buffers the GPU drew, and a sample of their RDRAM when it last did: while that's unchanged, the GPU's
   frame is the one to show; once the CPU has written there, RDRAM's is. */
static struct { uint32_t addr, sum; } gpu_drawn[16];
static uint32_t gpu_rdram_sum(uint32_t addr) { /* its first 320 x 240 16-bit pixels' worth, every word */
    uint32_t a = 0x80000000u | (addr & 0x1FFFFFFF), n = 320 * 240 * 2, sum = 2166136261u;
    if ((addr & 0x1FFFFFFF) + n > 0x800000) n = 0x800000 - (addr & 0x1FFFFFFF);
    for (uint32_t i = 0; i + 4 <= n; i += 4) { uint32_t v; memcpy(&v, R + a + i, 4); sum = (sum ^ v) * 16777619u; }
    return sum;
}

static void gpu_flush(void) {
    gpu_open = NULL;
    if (!gpu_ncommands) return;
    port_gpu_run(gpu_commands, gpu_ncommands, gpu_draws, gpu_vertices, gpu_pixels);
    /* the frame buffers drawn: remembered with what RDRAM held there */
    for (uint32_t i = 0; i < gpu_ndraws; i++) {
        uint32_t addr = gpu_draws[i].cimg;
        if (i && addr == gpu_draws[i - 1].cimg) continue;
        int k, free_slot = -1;
        for (k = 0; k < 16 && gpu_drawn[k].addr != addr; k++) if (free_slot < 0 && !gpu_drawn[k].addr) free_slot = k;
        if (k == 16) k = free_slot >= 0 ? free_slot : (int)(addr >> 6) & 15;
        gpu_drawn[k].addr = addr;
        gpu_drawn[k].sum = gpu_rdram_sum(addr);
    }
    gpu_ncommands = gpu_ndraws = gpu_nvertices = gpu_npixels = 0;
}
static void gpu_command(uint32_t op, uint32_t a, uint32_t b, uint32_t c, uint32_t d) {
    if (gpu_ncommands + 5 > GPU_MAX_COMMANDS) gpu_flush();
    uint32_t *p = gpu_commands + gpu_ncommands;
    p[0] = op; p[1] = a; p[2] = b; p[3] = c; p[4] = d;
    gpu_ncommands += 5;
}

/* ---------------------------------------------------------------- textures */

static uint64_t gpu_hash(uint64_t h, const uint8_t *p, uint32_t n) {
    for (uint32_t i = 0; i < n; i++) h = (h ^ p[i]) * 0x100000001B3ull;
    return h;
}
/* A tile's texture as the sampler sees it: its texels at every wrapped coordinate, which is the mask's size (or the
   tile's, without a mask), up to 1024 a side. */
static void gpu_tile_size(const Tile *t, int *w, int *h, int *size_w, int *size_h) {
    int sw = (int)(t->lrs - t->uls) + 1, sh = (int)(t->lrt - t->ult) + 1;
    if (sw < 1) sw = 1;
    if (sh < 1) sh = 1;
    *size_w = sw; *size_h = sh;
    int ms = t->masks > 10 ? 10 : t->masks, mt = t->maskt > 10 ? 10 : t->maskt;
    *w = ms ? 1 << ms : sw;
    *h = mt ? 1 << mt : sh;
    if (*w > 1024) *w = 1024;
    if (*h > 1024) *h = 1024;
}
/* What the tile's texels are made from: the TMEM its rows cover, the palette entries a colour-indexed one reads and
   how the palette is read. Equal hashes, equal texels. */
static uint64_t gpu_tile_hash(const Tile *t, int w, int h) {
    uint32_t row_bytes = t->siz == 0 ? (uint32_t)(w + 1) / 2 : t->siz == 1 ? (uint32_t)w : (uint32_t)w * 2;
    if (row_bytes > 4096) row_bytes = 4096;
    uint32_t shape[6] = {(uint32_t)t->fmt, (uint32_t)t->siz, (uint32_t)w, (uint32_t)h, (uint32_t)((mode_h >> 14) & 3) == 3,
                         (uint32_t)t->palette};
    uint64_t hash = gpu_hash(0xCBF29CE484222325ull, (const uint8_t *)shape, sizeof shape);
    uint32_t last_start = ~0u;
    for (int y = 0; y < h; y++) {
        uint32_t start = ((uint32_t)t->tmem * 8 + (uint32_t)y * (uint32_t)t->line * 8) & 4095;
        if (start == last_start) continue; /* a line of 0: every row the same */
        last_start = start;
        uint32_t first = start & ~7u, n = (row_bytes + (start - first) + 7) & ~7u;
        for (int half = 0; half < (t->siz == 3 ? 2 : 1); half++) {
            uint32_t from = (first + (uint32_t)half * 2048) & 4095;
            if (from + n <= 4096) hash = gpu_hash(hash, tmem + from, n);
            else { hash = gpu_hash(hash, tmem + from, 4096 - from); hash = gpu_hash(hash, tmem, from + n - 4096); }
        }
    }
    if (t->fmt == 2 && t->siz <= 1) {
        int first = t->siz == 0 ? t->palette * 16 : 0, n = t->siz == 0 ? 16 : 256;
        hash = gpu_hash(hash, (const uint8_t *)(tlut + first), (uint32_t)n * 2);
    }
    return hash;
}

/* The page's textures, by hash: 1024 sets of 4, the least recently used of a set replaced. An id is its place + 1. */
static struct { uint64_t hash; uint32_t used; } gpu_textures[4096];
static uint32_t gpu_texture(uint64_t hash, int w, int h, const uint32_t *rgba) {
    uint32_t set = (uint32_t)(hash ^ (hash >> 32)) & 1023, way = 0;
    for (uint32_t k = 0; k < 4; k++) {
        uint32_t i = set * 4 + k;
        if (gpu_textures[i].hash == hash) { gpu_textures[i].used = gpu_frame; return i + 1; }
        if (gpu_textures[i].used < gpu_textures[set * 4 + way].used) way = k;
    }
    if (!rgba) return 0;
    uint32_t i = set * 4 + way, n = (uint32_t)(w * h);
    if (n > GPU_MAX_PIXELS) return 0;
    if (gpu_npixels + n > GPU_MAX_PIXELS) gpu_flush();
    memcpy(gpu_pixels + gpu_npixels, rgba, n * 4);
    gpu_command(GPU_TEXTURE, i + 1, (uint32_t)w, (uint32_t)h, gpu_npixels);
    gpu_npixels += n;
    gpu_uploads++;
    gpu_textures[i].hash = hash;
    gpu_textures[i].used = gpu_frame;
    return i + 1;
}
static uint32_t gpu_rgba(Color c) {
    return (uint32_t)(clamp01(c.r) * 255 + 0.5f) | (uint32_t)(clamp01(c.g) * 255 + 0.5f) << 8
         | (uint32_t)(clamp01(c.b) * 255 + 0.5f) << 16 | (uint32_t)(clamp01(c.a) * 255 + 0.5f) << 24;
}

/* Each tile's texture, worked out again only when the tile or what's loaded changed since. */
typedef struct { Tile tile; uint32_t gen, tlut_type, id; uint64_t hash; } GpuTileMemo;
static GpuTileMemo gpu_tile_memo[8];
static uint32_t *gpu_scratch; /* a texture as it's decoded: up to 1024 x 1024 */
static void gpu_tile(int i, GpuTile *out) {
    const Tile *t = &tiles[i & 7];
    int w, h, size_w, size_h;
    gpu_tile_size(t, &w, &h, &size_w, &size_h);
    uint32_t type = ((mode_h >> 14) & 3) == 3;
    GpuTileMemo *m = &gpu_tile_memo[i & 7];
    uint32_t id = 0;
    if (m->gen == gpu_gen && m->tlut_type == type && !memcmp(&m->tile, t, sizeof *t)
        && (id = gpu_texture(m->hash, w, h, NULL))) {
        /* as before, and the page still has it */
    } else {
        uint64_t hash = gpu_tile_hash(t, w, h);
        id = gpu_texture(hash, w, h, NULL);
        if (!id) {
            for (int y = 0; y < h; y++)
                for (int x = 0; x < w; x++) gpu_scratch[y * w + x] = gpu_rgba(texel_wrapped(t, x, y));
            id = gpu_texture(hash, w, h, gpu_scratch);
        }
        m->tile = *t; m->gen = gpu_gen; m->tlut_type = type; m->hash = hash; m->id = id;
    }
    *out = (GpuTile){id, w, h, t->uls, t->ult, shifted(1.0f, t->shifts), shifted(1.0f, t->shiftt),
                     size_w, size_h, t->cms, t->cmt, t->masks > 10 ? 10 : t->masks, t->maskt > 10 ? 10 : t->maskt};
}

/* ---------------------------------------------------------------- draws */

#define GPU_COLOR(dst, c) do { (dst)[0] = (c).r; (dst)[1] = (c).g; (dst)[2] = (c).b; (dst)[3] = (c).a; } while (0)
/* The draw the next vertices belong to: the open one, if it's the same, or a new one. `tile`: the tile a rectangle
   names (-1: G_TEXTURE's, for triangles); `textured`: whether its texels are read. */
static GpuDraw *gpu_draw(uint32_t kind, uint32_t flags, int tile, int textured) {
    GpuDraw d;
    memset(&d, 0, sizeof d);
    d.kind = kind; d.flags = flags | (textured ? GPU_TEXTURED : 0);
    d.combine0 = combine0; d.combine1 = combine1; d.mode_h = mode_h; d.mode_l = mode_l;
    GPU_COLOR(d.prim, prim); GPU_COLOR(d.env, env); GPU_COLOR(d.fog, fog); GPU_COLOR(d.blend, blend);
    GPU_COLOR(d.key_center, key_center); GPU_COLOR(d.key_scale, key_scale);
    d.k4 = k4; d.k5 = k5; d.prim_lod_frac = prim_lod_frac; d.prim_min_level = prim_min_level / 32.0f;
    d.prim_z = prim_z / 262143.0f;
    if (kind == GPU_KIND_FILL) {
        Color c = cimg_siz == 3 ? unpack8888(fill_color) : cimg_siz == 1 ? grey(by255[fill_color >> 24])
                                : unpack5551((uint16_t)(fill_color >> 16));
        GPU_COLOR(d.fill, c);
    }
    d.tex_tile = tile < 0 ? tex_tile : tile;
    d.tex_levels = tex_levels;
    memcpy(d.scissor, scissor, sizeof d.scissor);
    d.cimg = cimg_addr; d.cimg_width = (uint32_t)cimg_width; d.cimg_height = (uint32_t)frame_height();
    d.cimg_siz = (uint32_t)cimg_siz; d.zimg = zimg_addr;
    uint32_t uploads = gpu_uploads;
    if (textured) {
        /* the tiles the texels come from: G_TEXTURE's and the next, or every level of detail's */
        int first = d.tex_tile, last = first + (uses_t1 ? 1 : 0); /* (combiner_uses: whether TEXEL1 is read) */
        if ((mode_h & 0x10000) && kind == GPU_KIND_COMBINE) last = first + tex_levels + 2;
        if (flags & GPU_ONE_TEXEL || kind == GPU_KIND_COPY) last = first;
        for (int i = first; i <= last && i < first + 8; i++) gpu_tile(i & 7, &d.tiles[i & 7]);
    }
    /* A texture sent just now may have taken the place of one the open draw shows: its command comes after the
       open draw's, which the page draws in order, so what follows is a draw of its own. */
    if (gpu_open && uploads == gpu_uploads && !memcmp(gpu_open, &d, offsetof(GpuDraw, first))) return gpu_open;
    if (gpu_ndraws >= GPU_MAX_DRAWS || gpu_ncommands + 5 > GPU_MAX_COMMANDS) gpu_flush();
    GpuDraw *out = &gpu_draws[gpu_ndraws++];
    *out = d;
    out->first = gpu_nvertices;
    out->count = 0;
    gpu_command(GPU_DRAW, (uint32_t)(out - gpu_draws), 0, 0, 0);
    gpu_open = out;
    return out;
}
/* Room for n more vertices in the open draw, which is made again after a flush. */
static GpuDraw *gpu_room(GpuDraw *d, uint32_t n, uint32_t kind, uint32_t flags, int tile, int textured) {
    if (gpu_nvertices + n <= GPU_MAX_VERTICES) return d;
    gpu_flush();
    return gpu_draw(kind, flags, tile, textured);
}
static void gpu_vertex(GpuDraw *d, float x, float y, float z, float w, float s, float t, Color c) {
    float *v = gpu_vertices + (size_t)gpu_nvertices * GPU_VERTEX;
    v[0] = x; v[1] = y; v[2] = z < 0 ? 0 : z > 1 ? 1 : z; v[3] = w; v[4] = s; v[5] = t;
    v[6] = c.r; v[7] = c.g; v[8] = c.b; v[9] = c.a;
    gpu_nvertices++;
    d->count++;
}

/* A triangle in screen space, as raster() takes it: its shade smooth or the last vertex's, its texture coordinates
   through 1/w with perspective correction, or at half scale without (as raster() takes them). */
static void gpu_triangle(const Vtx *a, const Vtx *b, const Vtx *c) {
    if (cimg_addr == zimg_addr && zimg_addr) return; /* drawing into the depth image: only fills reach it (a clear) */
    combiner_uses();
    uint32_t flags = (geometry & 1) ? GPU_DEPTH : 0;
    GpuDraw *d = gpu_draw(GPU_KIND_COMBINE, flags, -1, tex_on);
    d = gpu_room(d, 3, GPU_KIND_COMBINE, flags, -1, tex_on);
    int smooth = (geometry & 0x200000) != 0, persp = (mode_h & 0x80000) != 0;
    const Vtx *v[3] = {a, b, c};
    for (int i = 0; i < 3; i++) {
        float w = persp ? v[i]->w : 1.0f, k = persp ? 1.0f : 0.5f;
        gpu_vertex(d, v[i]->sx, v[i]->sy, v[i]->sz / 262143.0f, w, v[i]->s * k, v[i]->t * k, smooth ? v[i]->shade : c->shade);
    }
}

/* A rectangle with its corners' texture coordinates (a quad, two triangles), at no depth (G_ZS_PRIM's if set). */
static void gpu_quad(uint32_t kind, uint32_t flags, int tile, int textured, const float xy[4][2], const float st[4][2],
                     Color shade) {
    if (cimg_addr == zimg_addr && zimg_addr) return;
    GpuDraw *d = gpu_draw(kind, flags, tile, textured);
    d = gpu_room(d, 6, kind, flags, tile, textured);
    static const int order[6] = {0, 1, 2, 0, 2, 3};
    for (int i = 0; i < 6; i++) {
        int k = order[i];
        gpu_vertex(d, xy[k][0], xy[k][1], 0, 1, st[k][0], st[k][1], shade);
    }
}

/* tex_rect's rectangle: its pixels from (int)x0 to (int)x1 - 1, each pixel's texel at s + (x - x0) * dsdx (the
   page samples at a pixel's centre, x + 0.5). */
static void gpu_tex_rect(int tile, float x0, float y0, float x1, float y1, float s, float t, float dsdx, float dtdy, int flip) {
    combiner_uses();
    int copy = cycle_type() == 2;
    if (copy) { dsdx /= 4.0f; x1 += 1; y1 += 1; }
    float X0 = (float)(int)x0, Y0 = (float)(int)y0, X1 = (float)(int)x1, Y1 = (float)(int)y1;
    if (X1 <= X0 || Y1 <= Y0) return;
    float xy[4][2] = {{X0, Y0}, {X1, Y0}, {X1, Y1}, {X0, Y1}}, st[4][2];
    for (int k = 0; k < 4; k++) {
        float dx = xy[k][0] - 0.5f - x0, dy = xy[k][1] - 0.5f - y0;
        st[k][0] = s + (flip ? dy : dx) * dsdx;
        st[k][1] = t + (flip ? dx : dy) * dtdy;
    }
    gpu_quad(copy ? GPU_KIND_COPY : GPU_KIND_COMBINE, 0, tile, 1, xy, st, (Color){1, 1, 1, 1});
}

/* fill_rect's: in fill mode edges inclusive and the fill colour as it is (into the depth image, a clear of it);
   otherwise through the combiner with the primitive colour as the shade. */
static void gpu_fill_rect(int x0, int y0, int x1, int y1) {
    combiner_uses();
    if (cycle_type() == 3) {
        if (cimg_addr == zimg_addr) { gpu_open = NULL; gpu_command(GPU_CLEAR_DEPTH, zimg_addr, 0, 0, 0); return; }
        x1++; y1++;
    }
    if (x1 <= x0 || y1 <= y0) return;
    float xy[4][2] = {{(float)x0, (float)y0}, {(float)x1, (float)y0}, {(float)x1, (float)y1}, {(float)x0, (float)y1}};
    float st[4][2] = {{0}};
    gpu_quad(cycle_type() == 3 ? GPU_KIND_FILL : GPU_KIND_COMBINE, 0, 0, 0, xy, st, prim);
}

/* S2DEX's rotated sprite: its four corners through the 2D matrix, tile 7's texels across it. */
static void gpu_sprite_rotated(const Sprite *o, const float *m) {
    combiner_uses();
    float w = o->iw / o->sw, h = o->ih / o->sh;
    float ox[4] = {o->x, o->x + w, o->x + w, o->x}, oy[4] = {o->y, o->y, o->y + h, o->y + h}, xy[4][2], st[4][2];
    for (int k = 0; k < 4; k++) {
        xy[k][0] = m[0] * ox[k] + m[1] * oy[k] + m[4];
        xy[k][1] = m[2] * ox[k] + m[3] * oy[k] + m[5];
        st[k][0] = (ox[k] - o->x) * o->sw;
        st[k][1] = (oy[k] - o->y) * o->sh;
    }
    gpu_quad(GPU_KIND_COMBINE, GPU_ONE_TEXEL, 7, 1, xy, st, (Color){1, 1, 1, 1});
}

/* S2DEX's background: its pixels as s2d_bg reads them from RDRAM, made a texture of the screen's rectangle and
   drawn one texel a pixel (in tile 7's place, which the draw's own tile stands in for). */
static void gpu_bg(int fx, int fy, int fw, int fh, const uint32_t *rgba) {
    combiner_uses();
    if (fw <= 0 || fh <= 0 || fw > 1024 || fh > 1024) return;
    uint64_t hash = gpu_hash(0x9E3779B97F4A7C15ull ^ (uint64_t)(fw << 16 | fh), (const uint8_t *)rgba, (uint32_t)(fw * fh * 4));
    uint32_t id = gpu_texture(hash, fw, fh, rgba);
    Tile saved = tiles[7];
    tiles[7] = (Tile){.cms = 2, .cmt = 2, .lrs = (float)(fw - 1), .lrt = (float)(fh - 1)};
    float xy[4][2] = {{(float)fx, (float)fy}, {(float)(fx + fw), (float)fy}, {(float)(fx + fw), (float)(fy + fh)},
                      {(float)fx, (float)(fy + fh)}};
    float st[4][2] = {{0, 0}, {(float)fw, 0}, {(float)fw, (float)fh}, {0, (float)fh}};
    int copy = cycle_type() == 2;
    if (gpu_nvertices + 6 > GPU_MAX_VERTICES) gpu_flush();
    gpu_open = NULL; /* its own draw: its tile is set after the comparison */
    GpuDraw *d = gpu_draw(copy ? GPU_KIND_COPY : GPU_KIND_COMBINE, GPU_ONE_TEXEL | GPU_POINT | (copy ? GPU_COPY_ALPHA : 0), 7, 0);
    /* the background's own texture, in tile 7's place: not a tile of TMEM's */
    d->flags |= GPU_TEXTURED;
    d->tiles[7] = (GpuTile){id, fw, fh, 0, 0, 1, 1, fw, fh, 2, 2, 0, 0};
    gpu_open = NULL; /* nothing may join it */
    static const int order[6] = {0, 1, 2, 0, 2, 3};
    for (int i = 0; i < 6; i++) gpu_vertex(d, xy[order[i]][0], xy[order[i]][1], 0, 1, st[order[i]][0], st[order[i]][1], (Color){1, 1, 1, 1});
    tiles[7] = saved;
}

/* A renderer the page made anew (another resolution, or the player switched back to it) has none of the textures
   or frames sent to the one before: everything is sent again. */
static int gpu_renderer;
static int gpu_unavailable;
static void gpu_use(int renderer) {
    if (gpu_unavailable) { gpu = 0; return; }
    if (renderer && !gpu_commands) {
        gpu_commands = malloc(GPU_MAX_COMMANDS * sizeof *gpu_commands);
        gpu_draws = malloc(GPU_MAX_DRAWS * sizeof *gpu_draws);
        gpu_vertices = malloc((size_t)GPU_MAX_VERTICES * GPU_VERTEX * sizeof *gpu_vertices);
        gpu_pixels = malloc(GPU_MAX_PIXELS * sizeof *gpu_pixels);
        gpu_scratch = malloc(1024 * 1024 * sizeof *gpu_scratch);
        if (!gpu_commands || !gpu_draws || !gpu_vertices || !gpu_pixels || !gpu_scratch) {
            fprintf(stderr, "render: no memory for the GPU renderer's buffers; drawing in software\n");
            free(gpu_commands); free(gpu_draws); free(gpu_vertices); free(gpu_pixels); free(gpu_scratch);
            gpu_commands = NULL;
            gpu = 0;
            gpu_unavailable = 1;
            return;
        }
    }
    if (renderer && renderer != gpu_renderer) {
        gpu_ncommands = gpu_ndraws = gpu_nvertices = gpu_npixels = 0;
        gpu_open = NULL;
        memset(gpu_textures, 0, sizeof gpu_textures);
        memset(gpu_tile_memo, 0, sizeof gpu_tile_memo);
        memset(gpu_drawn, 0, sizeof gpu_drawn);
        gpu_renderer = renderer;
    }
    gpu = renderer != 0;
}

/* The frame the VI shows: the GPU's, while the frame buffer's RDRAM is as it was when the GPU last drew it (1),
   or RDRAM's for render.c to show (0). */
static int gpu_present(uint32_t addr, int w, int h, int gamma) {
    gpu_flush();
    gpu_frame++;
    for (int k = 0; k < 16; k++)
        if (gpu_drawn[k].addr == addr && gpu_drawn[k].sum == gpu_rdram_sum(addr))
            return port_gpu_present(addr, w, h, gamma);
    return 0;
}
