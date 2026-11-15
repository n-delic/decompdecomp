/*
 * PORT_WINDOW=1: the game in a window of its own, through SDL2, in place of live.c's browser view. live.c hands its
 * five calls (port_live_*) here when PORT_WINDOW is set and SDL2 opens; otherwise nothing here runs. The exported app
 * "with its own window" (port/wasm/export.py) ships SDL2 in Contents/Frameworks (toolchains.toml `sdl2`, built for
 * Apple Silicon and Intel Macs) and sets PORT_WINDOW.
 *
 * SDL2 is opened at run time (dlopen), as miyoo/miyoo.c opens SDL 1.2, so building the host needs no SDL and every
 * other build is unchanged: the calls and structures below are SDL2's stable ABI (2.0.0 on). PORT_SDL names the
 * library; otherwise the app's Frameworks folder, then the system's.
 *
 * Threads: a Mac lets only the main thread touch a window. The game's threads only leave the newest frame and the
 * sound here (port_sdl_frame, port_sdl_audio); the host's main thread, which keeps the console's 50 Hz retrace, calls
 * port_sdl_pump every retrace to read the keyboard and gamepads, draw the frame and present it.
 *
 * Keys as the browser view's: arrows steer (the stick and the d-pad), X is A, Z (or C) is B, Enter is Start, Space is
 * Z, Q and W are L and R, I/J/K/L are the C buttons; F or F11 is full screen, Escape leaves it. A gamepad works as in
 * the browser (SDL's game controller layout): the left stick steers, the right stick is the C buttons, the triggers
 * are Z. Closing the window quits (the EEPROM is written as the game writes it, so its saves are kept).
 */
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef _WIN32 /* no SDL2 here yet (ROADMAP.md): PORT_WINDOW falls back to the browser view */
int port_sdl_start(void) { return 0; }
void port_sdl_pump(void) {}
void port_sdl_frame(const uint8_t *rgb, int w, int h) { (void)rgb; (void)w; (void)h; }
int port_sdl_input(uint16_t *buttons, int8_t *x, int8_t *y) { (void)buttons; (void)x; (void)y; return 0; }
void port_sdl_audio(const int16_t *lr, int frames, int hz) { (void)lr; (void)frames; (void)hz; }
#else
#include <dlfcn.h>

/* ---------------------------------------------------------------- SDL2's ABI, the parts used */

typedef struct {
    int freq;
    uint16_t format;
    uint8_t channels, silence;
    uint16_t samples, padding;
    uint32_t size;
    void (*callback)(void *userdata, uint8_t *stream, int len);
    void *userdata;
} SdlAudioSpec;

/* SDL_Event is a 56-byte union whose first field is its type; a key event's repeat flag is at byte 13 and its
   scancode at byte 16. */
typedef union { uint32_t type; uint8_t bytes[64]; } SdlEvent;

enum { SDL_INIT_AUDIO = 0x10, SDL_INIT_VIDEO = 0x20, SDL_INIT_GAMECONTROLLER = 0x2000,
       SDL_WINDOW_RESIZABLE = 0x20, SDL_WINDOW_ALLOW_HIGHDPI = 0x2000, SDL_WINDOW_FULLSCREEN_DESKTOP = 0x1001,
       SDL_WINDOWPOS_CENTERED = 0x2FFF0000, SDL_RENDERER_ACCELERATED = 2, SDL_PIXELFORMAT_RGB24 = 0x17101803,
       SDL_TEXTUREACCESS_STREAMING = 1, SDL_QUIT = 0x100, SDL_KEYDOWN = 0x300, AUDIO_S16LSB = 0x8010 };
/* scancodes (USB usage ids) */
enum { S_C = 6, S_F = 9, S_I = 12, S_J = 13, S_K = 14, S_L = 15, S_Q = 20, S_W = 26, S_X = 27, S_Z = 29,
       S_RETURN = 40, S_ESCAPE = 41, S_SPACE = 44, S_F11 = 68, S_RIGHT = 79, S_LEFT = 80, S_DOWN = 81, S_UP = 82 };
/* game controller buttons and axes */
enum { G_A, G_B, G_X, G_Y, G_BACK, G_GUIDE, G_START, G_LSTICK, G_RSTICK, G_LSHOULDER, G_RSHOULDER, G_UP, G_DOWN,
       G_LEFT, G_RIGHT };
enum { A_LX, A_LY, A_RX, A_RY, A_LTRIGGER, A_RTRIGGER };

static struct {
    int (*Init)(uint32_t);
    int (*SetHint)(const char *, const char *);
    void *(*CreateWindow)(const char *, int, int, int, int, uint32_t);
    void *(*CreateRenderer)(void *, int, uint32_t);
    int (*RenderSetLogicalSize)(void *, int, int);
    void *(*CreateTexture)(void *, uint32_t, int, int, int);
    void (*DestroyTexture)(void *);
    int (*UpdateTexture)(void *, const void *, const void *, int);
    int (*SetRenderDrawColor)(void *, uint8_t, uint8_t, uint8_t, uint8_t);
    int (*RenderClear)(void *);
    int (*RenderCopy)(void *, void *, const void *, const void *);
    void (*RenderPresent)(void *);
    int (*PollEvent)(SdlEvent *);
    const uint8_t *(*GetKeyboardState)(int *);
    uint32_t (*GetWindowFlags)(void *);
    int (*SetWindowFullscreen)(void *, uint32_t);
    int (*NumJoysticks)(void);
    int (*IsGameController)(int);
    void *(*GameControllerOpen)(int);
    int (*GameControllerGetAttached)(void *);
    void (*GameControllerClose)(void *);
    uint8_t (*GameControllerGetButton)(void *, int);
    int16_t (*GameControllerGetAxis)(void *, int);
    int (*OpenAudio)(SdlAudioSpec *, SdlAudioSpec *);
    void (*PauseAudio)(int);
    void (*Quit)(void);
    const char *(*GetError)(void);
} sdl;

static int load_sdl(void) {
    const char *names[] = {getenv("PORT_SDL"),
#ifdef __APPLE__
                           "@executable_path/../Frameworks/libSDL2-2.0.0.dylib", "libSDL2-2.0.0.dylib",
#else
                           "libSDL2-2.0.so.0", "libSDL2.so",
#endif
                           NULL};
    void *lib = NULL;
    for (unsigned i = 0; !lib && i < sizeof names / sizeof *names; i++)
        if (names[i]) lib = dlopen(names[i], RTLD_NOW | RTLD_LOCAL);
    if (!lib) return 0;
#define SYM(name) if (!(*(void **)&sdl.name = dlsym(lib, "SDL_" #name))) return 0
    SYM(Init); SYM(SetHint); SYM(CreateWindow); SYM(CreateRenderer); SYM(RenderSetLogicalSize); SYM(CreateTexture);
    SYM(DestroyTexture); SYM(UpdateTexture); SYM(SetRenderDrawColor); SYM(RenderClear); SYM(RenderCopy);
    SYM(RenderPresent); SYM(PollEvent); SYM(GetKeyboardState); SYM(GetWindowFlags); SYM(SetWindowFullscreen);
    SYM(NumJoysticks); SYM(IsGameController); SYM(GameControllerOpen); SYM(GameControllerGetAttached);
    SYM(GameControllerClose); SYM(GameControllerGetButton); SYM(GameControllerGetAxis); SYM(OpenAudio);
    SYM(PauseAudio); SYM(Quit); SYM(GetError);
#undef SYM
    return 1;
}

/* ---------------------------------------------------------------- the window (the main thread) */

static void *window, *renderer, *texture, *pad;
static int texture_w, texture_h, pad_check;
static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
static uint8_t *frame; /* the newest frame, RGB, from a game thread */
static int frame_w, frame_h, frame_new;
static uint16_t held_buttons;
static int8_t held_x, held_y;

int port_sdl_start(void) {
    if (!load_sdl()) {
        fprintf(stderr, "PORT_WINDOW: SDL2 isn't here (PORT_SDL names it)\n");
        return 0;
    }
    sdl.SetHint("SDL_RENDER_SCALE_QUALITY", "0");                  /* sharp pixels, as the browser view's */
    sdl.SetHint("SDL_MAC_BACKGROUND_APP", "0");
    sdl.SetHint("SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", "1");
    if (sdl.Init(SDL_INIT_VIDEO | SDL_INIT_AUDIO | SDL_INIT_GAMECONTROLLER) < 0) {
        fprintf(stderr, "SDL_Init: %s\n", sdl.GetError());
        return 0;
    }
    const char *title = getenv("PORT_TITLE");
    window = sdl.CreateWindow(title && *title ? title : "Game", SDL_WINDOWPOS_CENTERED, SDL_WINDOWPOS_CENTERED,
                              960, 720, SDL_WINDOW_RESIZABLE | SDL_WINDOW_ALLOW_HIGHDPI);
    /* no vsync: presenting must never hold up the main thread's retrace, which paces the game */
    renderer = window ? sdl.CreateRenderer(window, -1, SDL_RENDERER_ACCELERATED) : NULL;
    if (!renderer && window) renderer = sdl.CreateRenderer(window, -1, 0);
    if (!renderer) {
        fprintf(stderr, "SDL: no window: %s\n", sdl.GetError());
        sdl.Quit();
        return 0;
    }
    sdl.RenderSetLogicalSize(renderer, 640, 480); /* the console's 4:3, letterboxed in any window */
    sdl.SetRenderDrawColor(renderer, 0, 0, 0, 255);
    sdl.RenderClear(renderer);
    sdl.RenderPresent(renderer);
    fprintf(stderr, "playing in a window of its own (SDL2)\n");
    return 1;
}

static void quit(void) {
    fprintf(stderr, "the window was closed\n");
    sdl.Quit();
    exit(0);
}

static void toggle_fullscreen(int leave) {
    int full = (sdl.GetWindowFlags(window) & SDL_WINDOW_FULLSCREEN_DESKTOP) == SDL_WINDOW_FULLSCREEN_DESKTOP;
    if (leave && !full) return;
    sdl.SetWindowFullscreen(window, full ? 0 : SDL_WINDOW_FULLSCREEN_DESKTOP);
}

static int axis(int16_t v) { return v > 32767 * 15 / 100 || v < -32767 * 15 / 100 ? v * 80 / 32767 : 0; }

static void read_controls(void) {
    const uint8_t *k = sdl.GetKeyboardState(NULL);
    uint16_t b = 0;
    int x = 0, y = 0;
    if (k[S_UP]) { b |= 0x0800; y += 80; }
    if (k[S_DOWN]) { b |= 0x0400; y -= 80; }
    if (k[S_LEFT]) { b |= 0x0200; x -= 80; }
    if (k[S_RIGHT]) { b |= 0x0100; x += 80; }
    if (k[S_X]) b |= 0x8000;
    if (k[S_Z] || k[S_C]) b |= 0x4000;
    if (k[S_RETURN]) b |= 0x1000;
    if (k[S_SPACE]) b |= 0x2000;
    if (k[S_Q]) b |= 0x0020;
    if (k[S_W]) b |= 0x0010;
    if (k[S_I]) b |= 0x0008;
    if (k[S_K]) b |= 0x0004;
    if (k[S_J]) b |= 0x0002;
    if (k[S_L]) b |= 0x0001;
    if (pad && !sdl.GameControllerGetAttached(pad)) { sdl.GameControllerClose(pad); pad = NULL; }
    if (!pad && pad_check-- <= 0) { /* a gamepad plugged in later: look once a second */
        pad_check = 50;
        for (int i = 0; !pad && i < sdl.NumJoysticks(); i++)
            if (sdl.IsGameController(i)) pad = sdl.GameControllerOpen(i);
    }
    if (pad) {
        static const struct { int button; uint16_t bit; } map[] = {
            {G_A, 0x8000}, {G_B, 0x4000}, {G_X, 0x4000}, {G_Y, 0x4000}, {G_START, 0x1000}, {G_LSHOULDER, 0x0020},
            {G_RSHOULDER, 0x0010}, {G_UP, 0x0800}, {G_DOWN, 0x0400}, {G_LEFT, 0x0200}, {G_RIGHT, 0x0100}};
        for (unsigned i = 0; i < sizeof map / sizeof *map; i++)
            if (sdl.GameControllerGetButton(pad, map[i].button)) b |= map[i].bit;
        if (sdl.GameControllerGetAxis(pad, A_LTRIGGER) > 8000 || sdl.GameControllerGetAxis(pad, A_RTRIGGER) > 8000)
            b |= 0x2000;
        int sx = axis(sdl.GameControllerGetAxis(pad, A_LX)), sy = -axis(sdl.GameControllerGetAxis(pad, A_LY));
        if (sx) x = sx;
        if (sy) y = sy;
        int cx = sdl.GameControllerGetAxis(pad, A_RX), cy = sdl.GameControllerGetAxis(pad, A_RY);
        if (cx > 16384) b |= 0x0001;
        if (cx < -16384) b |= 0x0002;
        if (cy > 16384) b |= 0x0004;
        if (cy < -16384) b |= 0x0008;
    }
    pthread_mutex_lock(&lock);
    held_buttons = b;
    held_x = (int8_t)(x > 80 ? 80 : x < -80 ? -80 : x);
    held_y = (int8_t)(y > 80 ? 80 : y < -80 ? -80 : y);
    pthread_mutex_unlock(&lock);
}

void port_sdl_pump(void) {
    SdlEvent e;
    while (sdl.PollEvent(&e)) {
        if (e.type == SDL_QUIT) quit();
        if (e.type == SDL_KEYDOWN && !e.bytes[13]) {
            int32_t code;
            memcpy(&code, e.bytes + 16, 4);
            if (code == S_F || code == S_F11) toggle_fullscreen(0);
            if (code == S_ESCAPE) toggle_fullscreen(1);
        }
    }
    read_controls();
    pthread_mutex_lock(&lock);
    if (frame_new) {
        if (!texture || texture_w != frame_w || texture_h != frame_h) {
            if (texture) sdl.DestroyTexture(texture);
            texture = sdl.CreateTexture(renderer, SDL_PIXELFORMAT_RGB24, SDL_TEXTUREACCESS_STREAMING, frame_w, frame_h);
            texture_w = frame_w; texture_h = frame_h;
        }
        if (texture) sdl.UpdateTexture(texture, NULL, frame, frame_w * 3);
        frame_new = 0;
    }
    pthread_mutex_unlock(&lock);
    sdl.RenderClear(renderer);
    if (texture) sdl.RenderCopy(renderer, texture, NULL, NULL);
    sdl.RenderPresent(renderer);
}

/* ---------------------------------------------------------------- what the game's threads leave */

void port_sdl_frame(const uint8_t *rgb, int w, int h) {
    if (w <= 0 || h <= 0) return;
    pthread_mutex_lock(&lock);
    if (frame_w * frame_h != w * h) { free(frame); frame = malloc((size_t)w * h * 3); }
    frame_w = w; frame_h = h;
    if (frame) { memcpy(frame, rgb, (size_t)w * h * 3); frame_new = 1; }
    pthread_mutex_unlock(&lock);
}

int port_sdl_input(uint16_t *buttons, int8_t *x, int8_t *y) {
    pthread_mutex_lock(&lock);
    *buttons = held_buttons; *x = held_x; *y = held_y;
    pthread_mutex_unlock(&lock);
    return 1;
}

/* ---------------------------------------------------------------- the sound, as miyoo.c plays it */

#define RING 16384 /* stereo frames: about a third of a second at 44.1 kHz */
static int16_t ring[RING * 2];
static uint32_t ring_start, ring_end; /* frames read and written, ever */
static int audio_hz;
static pthread_mutex_t sound = PTHREAD_MUTEX_INITIALIZER;

static void fill(void *userdata, uint8_t *stream, int len) {
    (void)userdata;
    int16_t *out = (int16_t *)stream;
    pthread_mutex_lock(&sound);
    for (int i = 0; i < len / 4; i++) {
        if (ring_start != ring_end) {
            out[i * 2] = ring[(ring_start % RING) * 2];
            out[i * 2 + 1] = ring[(ring_start % RING) * 2 + 1];
            ring_start++;
        } else out[i * 2] = out[i * 2 + 1] = 0; /* the game fell behind: silence, not a stutter of old sound */
    }
    pthread_mutex_unlock(&sound);
}

void port_sdl_audio(const int16_t *lr, int frames, int hz) {
    if (audio_hz < 0) return;
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
    pthread_mutex_lock(&sound);
    for (int i = 0; i < frames; i++, ring_end++) {
        if (ring_end - ring_start >= RING) ring_start++; /* too far ahead: drop the oldest */
        ring[(ring_end % RING) * 2] = lr[i * 2];
        ring[(ring_end % RING) * 2 + 1] = lr[i * 2 + 1];
    }
    pthread_mutex_unlock(&sound);
}
#endif
