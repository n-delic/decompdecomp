/*
 * The Miyoo Mini family's screen, buttons and sound (port/wasm/miyoo.py), in place of live.c's browser view: the
 * same five calls (port_live_*), on the SDL 1.2 every Miyoo OS has (stock, Onion, MinUI, Allium, spruce).
 *
 * SDL is opened at run time (dlopen), so building needs no SDL: the few calls and structures below are SDL 1.2's ABI
 * on 32-bit ARM, which hasn't changed since 2012. Without SDL (a test under qemu) the game runs headless, as the PC
 * build does without PORT_LIVE.
 *
 * What the Mini needs, from the Miyoo64 ports (github.com/mr-finster/miyoo64, MIT), which found each on devices:
 * - the panel is mounted upside down: the picture is turned 180 degrees, unless PORT_ROTATE=0 (MinUI's SDL turns it
 *   itself);
 * - its buttons arrive as keys: the d-pad as arrows, A Space, B Left Ctrl, X Left Shift, Y Left Alt, L1 E, R1 T,
 *   L2 Tab, R2 Backspace, START Return, SELECT Right Ctrl, MENU Escape;
 * - sound is OSS through the OS's audioserver (launch.sh sets SDL_AUDIODRIVER=dsp and preloads libpadsp.so).
 *
 * Buttons as the N64's: A is A, B is B, the d-pad steers (the stick, and the d-pad's own bits), START is START, L1 is
 * L, R1 is R, L2 and R2 are Z, X is C-up, Y is C-left, SELECT is C-down. MENU, or START and SELECT together, quits
 * (the EEPROM is written as the game writes it, so its saves are kept).
 */
#include <dlfcn.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* ---------------------------------------------------------------- SDL 1.2's ABI, the parts used */

typedef struct {
    void *palette;
    uint8_t bits_per_pixel, bytes_per_pixel;
    uint8_t rloss, gloss, bloss, aloss;
    uint8_t rshift, gshift, bshift, ashift;
    uint32_t rmask, gmask, bmask, amask;
} SdlFormat;

typedef struct {
    uint32_t flags;
    SdlFormat *format;
    int w, h;
    uint16_t pitch;
    void *pixels;
} SdlSurface;

typedef struct {
    int freq;
    uint16_t format;
    uint8_t channels, silence;
    uint16_t samples, padding;
    uint32_t size;
    void (*callback)(void *userdata, uint8_t *stream, int len);
    void *userdata;
} SdlAudioSpec;

typedef union {
    uint8_t type;
    struct { uint8_t type, which, state; struct { uint8_t scancode; int sym; int mod; uint16_t unicode; } keysym; } key;
    uint8_t pad[64];
} SdlEvent;

enum { SDL_INIT_AUDIO = 0x10, SDL_INIT_VIDEO = 0x20, SDL_HWSURFACE = 1, SDL_DOUBLEBUF = 0x40000000,
       SDL_KEYDOWN = 2, SDL_KEYUP = 3, SDL_QUIT = 12, AUDIO_S16LSB = 0x8010 };
enum { K_BACKSPACE = 8, K_TAB = 9, K_RETURN = 13, K_ESCAPE = 27, K_SPACE = 32, K_E = 101, K_T = 116, K_UP = 273,
       K_DOWN = 274, K_RIGHT = 275, K_LEFT = 276, K_RSHIFT = 303, K_LSHIFT = 304, K_RCTRL = 305, K_LCTRL = 306,
       K_LALT = 308 };

static struct {
    int (*Init)(uint32_t);
    SdlSurface *(*SetVideoMode)(int, int, int, uint32_t);
    int (*Flip)(SdlSurface *);
    int (*LockSurface)(SdlSurface *);
    void (*UnlockSurface)(SdlSurface *);
    int (*PollEvent)(SdlEvent *);
    int (*ShowCursor)(int);
    int (*OpenAudio)(SdlAudioSpec *, SdlAudioSpec *);
    void (*PauseAudio)(int);
    void (*Quit)(void);
    char *(*GetError)(void);
} sdl;

static int load_sdl(void) {
    void *lib = dlopen("libSDL-1.2.so.0", RTLD_NOW | RTLD_GLOBAL);
    if (!lib) lib = dlopen("libSDL.so", RTLD_NOW | RTLD_GLOBAL);
    if (!lib) return 0;
#define SYM(name) if (!(*(void **)&sdl.name = dlsym(lib, "SDL_" #name))) return 0
    SYM(Init); SYM(SetVideoMode); SYM(Flip); SYM(LockSurface); SYM(UnlockSurface); SYM(PollEvent); SYM(ShowCursor);
    SYM(OpenAudio); SYM(PauseAudio); SYM(Quit); SYM(GetError);
#undef SYM
    return 1;
}

/* ---------------------------------------------------------------- the screen */

static SdlSurface *screen;
static int active, rotate = 1;
static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;

void port_live_start(void) {
    if (getenv("PORT_HEADLESS") || !load_sdl()) {
        fprintf(stderr, "no SDL 1.2: running without a screen\n");
        return;
    }
    if (getenv("PORT_ROTATE")) rotate = atoi(getenv("PORT_ROTATE")) != 0;
    if (sdl.Init(SDL_INIT_VIDEO | SDL_INIT_AUDIO) < 0) {
        fprintf(stderr, "SDL_Init: %s\n", sdl.GetError());
        return;
    }
    screen = sdl.SetVideoMode(640, 480, 32, SDL_HWSURFACE | SDL_DOUBLEBUF);
    if (!screen) screen = sdl.SetVideoMode(640, 480, 16, SDL_HWSURFACE | SDL_DOUBLEBUF);
    if (!screen) { fprintf(stderr, "SDL_SetVideoMode: %s\n", sdl.GetError()); return; }
    sdl.ShowCursor(0);
    active = 1;
    fprintf(stderr, "screen %dx%d, %d bits, %s\n", screen->w, screen->h, screen->format->bits_per_pixel,
            rotate ? "turned 180 degrees" : "as it is");
}

int port_live_active(void) { return active; }

void port_live_gate(unsigned long next, const uint8_t *ram) { (void)next; (void)ram; } /* no lockstep viewer on the Mini */
void port_live_pump(void) {} /* the Mini's SDL 1.2 is driven from the game's threads, as it always was */
void port_live_app(const char *argv0) { (void)argv0; } /* its launch.sh names the ROM */
const char *port_live_rom(void) { return NULL; }

/* The frame (RGB, w x h) scaled to fill the screen's height at the console's 4:3, nearest pixel, and centred. */
void port_live_frame(const uint8_t *rgb, int w, int h) {
    if (!active || w <= 0 || h <= 0) return;
    pthread_mutex_lock(&lock);
    if (sdl.LockSurface(screen) < 0) { pthread_mutex_unlock(&lock); return; }
    const SdlFormat *f = screen->format;
    int sw = screen->w, sh = screen->h, bpp = f->bytes_per_pixel;
    int dh = sh, dw = sh * 4 / 3;
    if (dw > sw) { dw = sw; dh = sw * 3 / 4; }
    int x0 = (sw - dw) / 2, y0 = (sh - dh) / 2;
    uint8_t *pixels = screen->pixels;
    static int *cols, cols_for = -1;
    if (cols_for != w * 4096 + dw) { /* which source column each screen column shows */
        free(cols); cols = malloc(sizeof *cols * (size_t)dw); cols_for = w * 4096 + dw;
        for (int x = 0; x < dw; x++) cols[x] = x * w / dw;
    }
    for (int y = 0; y < sh; y++) {
        int ty = rotate ? sh - 1 - y : y;
        uint8_t *row = pixels + (size_t)ty * screen->pitch;
        if (y < y0 || y >= y0 + dh) { memset(row, 0, (size_t)sw * bpp); continue; }
        const uint8_t *src = rgb + (size_t)((y - y0) * h / dh) * w * 3;
        for (int x = 0; x < sw; x++) {
            int tx = rotate ? sw - 1 - x : x;
            uint32_t p = 0;
            if (x >= x0 && x < x0 + dw) {
                const uint8_t *s = src + cols[x - x0] * 3;
                p = (uint32_t)(s[0] >> f->rloss) << f->rshift | (uint32_t)(s[1] >> f->gloss) << f->gshift
                    | (uint32_t)(s[2] >> f->bloss) << f->bshift;
            }
            if (bpp == 4) ((uint32_t *)row)[tx] = p;
            else ((uint16_t *)row)[tx] = (uint16_t)p;
        }
    }
    sdl.UnlockSurface(screen);
    sdl.Flip(screen);
    pthread_mutex_unlock(&lock);
}

/* ---------------------------------------------------------------- the buttons */

static uint32_t held; /* the Miyoo's buttons, one bit each, by the key's slot below */
static const int keys[] = {K_SPACE, K_LCTRL, K_LSHIFT, K_LALT, K_E, K_T, K_TAB, K_BACKSPACE, K_RETURN, K_RCTRL,
                           K_UP, K_DOWN, K_LEFT, K_RIGHT, K_ESCAPE};
enum { B_A, B_B, B_X, B_Y, B_L1, B_R1, B_L2, B_R2, B_START, B_SELECT, B_UP, B_DOWN, B_LEFT, B_RIGHT, B_MENU };
#define HELD(b) (held >> (b) & 1)

static void quit(void) {
    fprintf(stderr, "quit from the Miyoo's buttons\n");
    if (sdl.Quit) sdl.Quit(); /* the console back for the OS's menu (Miyoo64: a black screen otherwise) */
    exit(0);
}

int port_live_input(uint16_t *buttons, int8_t *x, int8_t *y) {
    if (!active) return 0;
    SdlEvent e;
    pthread_mutex_lock(&lock);
    while (sdl.PollEvent(&e)) {
        if (e.type == SDL_QUIT) { pthread_mutex_unlock(&lock); quit(); }
        if (e.type != SDL_KEYDOWN && e.type != SDL_KEYUP) continue;
        for (unsigned i = 0; i < sizeof keys / sizeof *keys; i++)
            if (e.key.keysym.sym == keys[i]) held = e.type == SDL_KEYDOWN ? held | 1u << i : held & ~(1u << i);
    }
    pthread_mutex_unlock(&lock);
    if (HELD(B_MENU) || (HELD(B_START) && HELD(B_SELECT))) quit();
    uint16_t b = 0;
    if (HELD(B_A)) b |= 0x8000;
    if (HELD(B_B)) b |= 0x4000;
    if (HELD(B_L2) || HELD(B_R2)) b |= 0x2000;
    if (HELD(B_START)) b |= 0x1000;
    if (HELD(B_UP)) b |= 0x0800;
    if (HELD(B_DOWN)) b |= 0x0400;
    if (HELD(B_LEFT)) b |= 0x0200;
    if (HELD(B_RIGHT)) b |= 0x0100;
    if (HELD(B_L1)) b |= 0x0020;
    if (HELD(B_R1)) b |= 0x0010;
    if (HELD(B_X)) b |= 0x0008;
    if (HELD(B_SELECT)) b |= 0x0004;
    if (HELD(B_Y)) b |= 0x0002;
    *buttons = b;
    *x = (int8_t)(HELD(B_LEFT) ? -80 : HELD(B_RIGHT) ? 80 : 0);
    *y = (int8_t)(HELD(B_DOWN) ? -80 : HELD(B_UP) ? 80 : 0);
    return 1;
}

/* ---------------------------------------------------------------- the sound */

#define RING 16384 /* stereo frames: about a third of a second at 44.1 kHz */
static int16_t ring[RING * 2];
static uint32_t ring_start, ring_end; /* frames written and read, ever */
static int audio_hz;

static void fill(void *userdata, uint8_t *stream, int len) {
    (void)userdata;
    int16_t *out = (int16_t *)stream;
    int frames = len / 4;
    pthread_mutex_lock(&lock);
    for (int i = 0; i < frames; i++) {
        if (ring_start != ring_end) {
            out[i * 2] = ring[(ring_start % RING) * 2];
            out[i * 2 + 1] = ring[(ring_start % RING) * 2 + 1];
            ring_start++;
        } else out[i * 2] = out[i * 2 + 1] = 0; /* the game fell behind: silence, not a stutter of old sound */
    }
    pthread_mutex_unlock(&lock);
}

void port_live_audio(const int16_t *lr, int frames, int hz) {
    if (!active || audio_hz < 0) return;
    if (!audio_hz) { /* the first buffer: open at the game's own rate (SDL converts to the device's) */
        SdlAudioSpec want = {0};
        want.freq = hz; want.format = AUDIO_S16LSB; want.channels = 2; want.samples = 1024; want.callback = fill;
        if (sdl.OpenAudio(&want, NULL) < 0) {
            fprintf(stderr, "SDL_OpenAudio: %s: playing without sound\n", sdl.GetError());
            audio_hz = -1;
            return;
        }
        audio_hz = hz;
        sdl.PauseAudio(0);
    }
    pthread_mutex_lock(&lock);
    for (int i = 0; i < frames; i++, ring_end++) {
        if (ring_end - ring_start >= RING) ring_start++; /* too far ahead: drop the oldest */
        ring[(ring_end % RING) * 2] = lr[i * 2];
        ring[(ring_end % RING) * 2 + 1] = lr[i * 2 + 1];
    }
    pthread_mutex_unlock(&lock);
}
