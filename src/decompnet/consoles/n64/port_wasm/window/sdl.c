/*
 * PORT_WINDOW=1: the game in a window of its own, through SDL3, in place of live.c's browser view. live.c hands its
 * calls (port_live_*) here when PORT_WINDOW is set and SDL3 opens; otherwise nothing here runs. The exported app "with
 * its own window" (port/wasm/export.py) is the game itself: SDL3 in Contents/Frameworks (toolchains.toml `sdl3`, built
 * for Apple Silicon and Intel Macs), its settings in Contents/Resources/port.env (live.c, port_live_app), and the ROM
 * chosen here, in SDL3's own file dialog (port_sdl_rom), so no script or AppleScript is involved.
 *
 * SDL3 is opened at run time (dlopen), as miyoo/miyoo.c opens SDL 1.2, so building the host needs no SDL and every
 * other build is unchanged. The calls, values and structure offsets below are SDL3's stable ABI (3.2 on);
 * tests/test_n64_port_host.py checks each against the pinned SDL3's headers. PORT_SDL names the library; otherwise
 * the app's Frameworks folder, then the system's.
 *
 * Threads: a Mac lets only the main thread touch a window. The game's threads only leave the newest frame here
 * (port_sdl_frame) and push the sound into SDL3's audio stream, which takes it from any thread; the host's main
 * thread, which keeps the console's 50 Hz retrace, calls port_sdl_pump every retrace to read the keyboard and
 * gamepads, draw the frame and present it.
 *
 * Keys as the browser view's: arrows steer (the stick and the d-pad), X is A, Z (or C) is B, Enter is Start, Space is
 * Z, Q and W are L and R, I/J/K/L are the C buttons; F or F11 is full screen, Escape leaves it. A gamepad works as in
 * the browser (SDL's gamepad layout): the left stick steers, the right stick is the C buttons, the triggers are Z.
 * Closing the window quits (the EEPROM is written as the game writes it, so its saves are kept).
 */
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef _WIN32 /* no SDL3 here yet (ROADMAP.md): PORT_WINDOW falls back to the browser view */
int port_sdl_start(void) { return 0; }
const char *port_sdl_rom(void) { return NULL; }
void port_sdl_pump(void) {}
void port_sdl_frame(const uint8_t *rgb, int w, int h) { (void)rgb; (void)w; (void)h; }
int port_sdl_input(uint16_t *buttons, int8_t *x, int8_t *y) { (void)buttons; (void)x; (void)y; return 0; }
void port_sdl_audio(const int16_t *lr, int frames, int hz) { (void)lr; (void)frames; (void)hz; }
#else
#include <dlfcn.h>

/* ---------------------------------------------------------------- SDL3's ABI, the parts used */

typedef struct { int format, channels, freq; } SdlAudioSpec;     /* SDL_AudioSpec */
typedef struct { const char *name, *pattern; } SdlFileFilter;    /* SDL_DialogFileFilter */
/* SDL_Event: a 128-byte union whose first field is its type. A key event's scancode is at byte 24 and its repeat
   flag at byte 37; a gamepad device event's joystick id at byte 16. */
typedef union { uint32_t type; uint8_t bytes[128]; } SdlEvent;
enum { EV_KEY_SCANCODE = 24, EV_KEY_REPEAT = 37, EV_PAD_WHICH = 16 };

enum { SDL_INIT_AUDIO = 0x10, SDL_INIT_VIDEO = 0x20, SDL_INIT_GAMEPAD = 0x2000, SDL_WINDOW_FULLSCREEN = 0x1,
       SDL_WINDOW_RESIZABLE = 0x20, SDL_WINDOW_HIGH_PIXEL_DENSITY = 0x2000, SDL_PIXELFORMAT_RGB24 = 0x17101803,
       SDL_TEXTUREACCESS_STREAMING = 1, SDL_SCALEMODE_NEAREST = 0, SDL_LOGICAL_PRESENTATION_LETTERBOX = 2,
       SDL_EVENT_QUIT = 0x100, SDL_EVENT_KEY_DOWN = 0x300, SDL_EVENT_GAMEPAD_ADDED = 0x653,
       SDL_EVENT_GAMEPAD_REMOVED = 0x654, SDL_AUDIO_S16LE = 0x8010, SDL_MESSAGEBOX_ERROR = 0x10 };
#define SDL_AUDIO_DEVICE_DEFAULT_PLAYBACK 0xFFFFFFFFu
/* scancodes (USB usage ids) */
enum { S_C = 6, S_F = 9, S_I = 12, S_J = 13, S_K = 14, S_L = 15, S_Q = 20, S_W = 26, S_X = 27, S_Z = 29,
       S_RETURN = 40, S_ESCAPE = 41, S_SPACE = 44, S_F11 = 68, S_RIGHT = 79, S_LEFT = 80, S_DOWN = 81, S_UP = 82 };
/* gamepad buttons and axes */
enum { G_SOUTH, G_EAST, G_WEST, G_NORTH, G_BACK, G_GUIDE, G_START, G_LSTICK, G_RSTICK, G_LSHOULDER, G_RSHOULDER,
       G_UP, G_DOWN, G_LEFT, G_RIGHT };
enum { A_LX, A_LY, A_RX, A_RY, A_LTRIGGER, A_RTRIGGER };

typedef void (*DialogCallback)(void *userdata, const char *const *files, int filter);

static struct {
    bool (*Init)(uint32_t); /* bool, as SDL3 returns it: only its low byte is defined */
    bool (*SetHint)(const char *, const char *);
    void *(*CreateWindow)(const char *, int, int, uint64_t);
    bool (*SetWindowTitle)(void *, const char *);
    uint64_t (*GetTicksNS)(void);
    void *(*CreateRenderer)(void *, const char *);
    bool (*SetRenderLogicalPresentation)(void *, int, int, int);
    void *(*CreateTexture)(void *, uint32_t, int, int, int);
    bool (*SetTextureScaleMode)(void *, int);
    void (*DestroyTexture)(void *);
    bool (*UpdateTexture)(void *, const void *, const void *, int);
    bool (*SetRenderDrawColor)(void *, uint8_t, uint8_t, uint8_t, uint8_t);
    bool (*RenderClear)(void *);
    bool (*RenderTexture)(void *, void *, const void *, const void *);
    bool (*RenderPresent)(void *);
    bool (*PollEvent)(SdlEvent *);
    bool (*WaitEventTimeout)(SdlEvent *, int32_t);
    const uint8_t *(*GetKeyboardState)(int *);
    uint64_t (*GetWindowFlags)(void *);
    bool (*SetWindowFullscreen)(void *, bool);
    void *(*OpenGamepad)(uint32_t);
    void (*CloseGamepad)(void *);
    uint32_t (*GetGamepadID)(void *);
    bool (*GetGamepadButton)(void *, int);
    int16_t (*GetGamepadAxis)(void *, int);
    void *(*OpenAudioDeviceStream)(uint32_t, const SdlAudioSpec *, void *, void *);
    bool (*ResumeAudioStreamDevice)(void *);
    bool (*PutAudioStreamData)(void *, const void *, int);
    int (*GetAudioStreamQueued)(void *);
    bool (*ClearAudioStream)(void *);
    void (*DestroyAudioStream)(void *);
    void (*ShowOpenFileDialog)(DialogCallback, void *, void *, const SdlFileFilter *, int, const char *, bool);
    bool (*ShowSimpleMessageBox)(uint32_t, const char *, const char *, void *);
    void (*Quit)(void);
    const char *(*GetError)(void);
} sdl;

static int load_sdl(void) {
    const char *names[] = {getenv("PORT_SDL"),
#ifdef __APPLE__
                           "@executable_path/../Frameworks/libSDL3.0.dylib", "libSDL3.0.dylib",
#else
                           "libSDL3.so.0", "libSDL3.so",
#endif
                           NULL};
    void *lib = NULL;
    for (unsigned i = 0; !lib && i < sizeof names / sizeof *names; i++)
        if (names[i]) lib = dlopen(names[i], RTLD_NOW | RTLD_LOCAL);
    if (!lib) return 0;
#define SYM(name) if (!(*(void **)&sdl.name = dlsym(lib, "SDL_" #name))) return 0
    SYM(SetWindowTitle); SYM(GetTicksNS);
    SYM(Init); SYM(SetHint); SYM(CreateWindow); SYM(CreateRenderer); SYM(SetRenderLogicalPresentation);
    SYM(CreateTexture); SYM(SetTextureScaleMode); SYM(DestroyTexture); SYM(UpdateTexture); SYM(SetRenderDrawColor);
    SYM(RenderClear); SYM(RenderTexture); SYM(RenderPresent); SYM(PollEvent); SYM(WaitEventTimeout);
    SYM(GetKeyboardState); SYM(GetWindowFlags); SYM(SetWindowFullscreen); SYM(OpenGamepad); SYM(CloseGamepad);
    SYM(GetGamepadID); SYM(GetGamepadButton); SYM(GetGamepadAxis); SYM(OpenAudioDeviceStream);
    SYM(ResumeAudioStreamDevice); SYM(PutAudioStreamData); SYM(GetAudioStreamQueued); SYM(ClearAudioStream);
    SYM(DestroyAudioStream);
    SYM(ShowOpenFileDialog); SYM(ShowSimpleMessageBox); SYM(Quit); SYM(GetError);
#undef SYM
    return 1;
}

/* ---------------------------------------------------------------- the window (the main thread) */

static void *window, *renderer, *texture, *pad;
static int texture_w, texture_h;
static uint64_t fps_since;
static unsigned fps_frames;
static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
static uint8_t *frame; /* the newest frame, RGB, from a game thread */
static int frame_w, frame_h, frame_new;
static uint16_t held_buttons;
static int8_t held_x, held_y;

static const char *title(void) {
    const char *t = getenv("PORT_TITLE");
    return t && *t ? t : "Game";
}

int port_sdl_start(void) {
    if (!load_sdl()) {
        fprintf(stderr, "PORT_WINDOW: SDL3 isn't here (PORT_SDL names it)\n");
        return 0;
    }
    sdl.SetHint("SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", "1");
    if (!sdl.Init(SDL_INIT_VIDEO | SDL_INIT_AUDIO | SDL_INIT_GAMEPAD)) {
        fprintf(stderr, "SDL_Init: %s\n", sdl.GetError());
        return 0;
    }
    window = sdl.CreateWindow(title(), 960, 720, SDL_WINDOW_RESIZABLE | SDL_WINDOW_HIGH_PIXEL_DENSITY);
    renderer = window ? sdl.CreateRenderer(window, NULL) : NULL; /* SDL3 presents without vsync unless asked */
    if (!renderer) {
        fprintf(stderr, "SDL: no window: %s\n", sdl.GetError());
        sdl.Quit();
        return 0;
    }
    sdl.SetRenderLogicalPresentation(renderer, 640, 480, SDL_LOGICAL_PRESENTATION_LETTERBOX); /* the console's 4:3 */
    sdl.SetRenderDrawColor(renderer, 0, 0, 0, 255);
    sdl.RenderClear(renderer);
    sdl.RenderPresent(renderer);
    /* PORT_FULLSCREEN=1: the whole screen from the start (the Steam Deck's launcher, steamdeck.py), for a handheld
       with no key to make it so; F still leaves it */
    const char *full = getenv("PORT_FULLSCREEN");
    if (full && *full && *full != '0') sdl.SetWindowFullscreen(window, true);
    fps_since = sdl.GetTicksNS();
    fps_frames = 0;
    fprintf(stderr, "playing in a window of its own (SDL3)\n");
    return 1;
}

static void quit(void) {
    fprintf(stderr, "the window was closed\n");
    sdl.Quit();
    exit(0);
}

/* ---------------------------------------------------------------- the ROM, chosen in SDL3's own file dialog */

static int is_rom(const char *path) {
    FILE *f = path && *path ? fopen(path, "rb") : NULL;
    uint8_t b[4] = {0};
    if (!f) return 0;
    size_t n = fread(b, 1, 4, f);
    fclose(f);
    uint32_t magic = (uint32_t)b[0] << 24 | (uint32_t)b[1] << 16 | (uint32_t)b[2] << 8 | b[3];
    return n == 4 && (magic == 0x80371240 || magic == 0x37804012 || magic == 0x40123780); /* .z64, .v64, .n64 */
}

static struct { int done; char path[4096]; } chosen;

static void picked(void *userdata, const char *const *files, int filter) {
    (void)userdata; (void)filter;
    if (!files) fprintf(stderr, "the file dialog failed: %s\n", sdl.GetError());
    snprintf(chosen.path, sizeof chosen.path, "%s", files && files[0] ? files[0] : "");
    chosen.done = 1;
}

/* The player's ROM: the one they chose before (PORT_ROM_MEMORY, a file holding its path), else one they choose now,
   asked again until it is an N64 ROM; cancelling quits. */
const char *port_sdl_rom(void) {
    static char path[4096];
    const char *memory = getenv("PORT_ROM_MEMORY");
    FILE *f = memory ? fopen(memory, "r") : NULL;
    if (f) {
        size_t n = fread(path, 1, sizeof path - 1, f);
        path[n] = 0;
        fclose(f);
        if (is_rom(path)) return path;
    }
    static const SdlFileFilter filters[] = {{"N64 ROM", "z64;v64;n64;rom;bin"}, {"All files", "*"}};
    for (;;) {
        chosen.done = 0;
        sdl.ShowOpenFileDialog(picked, NULL, window, filters, 2, NULL, false);
        while (!chosen.done) { /* the dialog answers on this thread, while its events are read */
            SdlEvent e;
            if (sdl.WaitEventTimeout(&e, 100) && e.type == SDL_EVENT_QUIT) quit();
        }
        if (!chosen.path[0]) quit(); /* cancelled */
        if (is_rom(chosen.path)) break;
        char message[512];
        snprintf(message, sizeof message, "Choose your ROM of %s: a .z64, .v64 or .n64 file.", title());
        sdl.ShowSimpleMessageBox(SDL_MESSAGEBOX_ERROR, "That file isn't an N64 ROM", message, window);
    }
    snprintf(path, sizeof path, "%s", chosen.path);
    if (memory && (f = fopen(memory, "w"))) { fputs(path, f); fclose(f); }
    return path;
}

/* ---------------------------------------------------------------- the controls and the picture */

static void toggle_fullscreen(int leave) {
    int full = (sdl.GetWindowFlags(window) & SDL_WINDOW_FULLSCREEN) != 0;
    if (leave && !full) return;
    sdl.SetWindowFullscreen(window, !full);
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
    if (pad) {
        static const struct { int button; uint16_t bit; } map[] = {
            {G_SOUTH, 0x8000}, {G_EAST, 0x4000}, {G_WEST, 0x4000}, {G_NORTH, 0x4000}, {G_START, 0x1000},
            {G_LSHOULDER, 0x0020}, {G_RSHOULDER, 0x0010}, {G_UP, 0x0800}, {G_DOWN, 0x0400}, {G_LEFT, 0x0200},
            {G_RIGHT, 0x0100}};
        for (unsigned i = 0; i < sizeof map / sizeof *map; i++)
            if (sdl.GetGamepadButton(pad, map[i].button)) b |= map[i].bit;
        if (sdl.GetGamepadAxis(pad, A_LTRIGGER) > 8000 || sdl.GetGamepadAxis(pad, A_RTRIGGER) > 8000) b |= 0x2000;
        int sx = axis(sdl.GetGamepadAxis(pad, A_LX)), sy = -axis(sdl.GetGamepadAxis(pad, A_LY));
        if (sx) x = sx;
        if (sy) y = sy;
        int cx = sdl.GetGamepadAxis(pad, A_RX), cy = sdl.GetGamepadAxis(pad, A_RY);
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
        uint32_t which;
        memcpy(&which, e.bytes + EV_PAD_WHICH, 4);
        if (e.type == SDL_EVENT_QUIT) quit();
        else if (e.type == SDL_EVENT_KEY_DOWN && !e.bytes[EV_KEY_REPEAT]) {
            int32_t code;
            memcpy(&code, e.bytes + EV_KEY_SCANCODE, 4);
            if (code == S_F || code == S_F11) toggle_fullscreen(0);
            if (code == S_ESCAPE) toggle_fullscreen(1);
        } else if (e.type == SDL_EVENT_GAMEPAD_ADDED && !pad) { /* SDL3 announces the ones already plugged in too */
            pad = sdl.OpenGamepad(which);
        } else if (e.type == SDL_EVENT_GAMEPAD_REMOVED && pad && sdl.GetGamepadID(pad) == which) {
            sdl.CloseGamepad(pad);
            pad = NULL;
        }
    }
    read_controls();
    int fresh = 0;
    pthread_mutex_lock(&lock);
    if (frame_new) {
        if (!texture || texture_w != frame_w || texture_h != frame_h) {
            if (texture) sdl.DestroyTexture(texture);
            texture = sdl.CreateTexture(renderer, SDL_PIXELFORMAT_RGB24, SDL_TEXTUREACCESS_STREAMING, frame_w, frame_h);
            if (texture) sdl.SetTextureScaleMode(texture, SDL_SCALEMODE_NEAREST); /* sharp, as the browser's */
            texture_w = frame_w; texture_h = frame_h;
        }
        if (texture) fresh = sdl.UpdateTexture(texture, NULL, frame, frame_w * 3);
        frame_new = 0;
    }
    pthread_mutex_unlock(&lock);
    sdl.RenderClear(renderer);
    if (texture) sdl.RenderTexture(renderer, texture, NULL, NULL);
    bool presented = sdl.RenderPresent(renderer);
    /* Count new pictures, not the retraces that redraw the previous picture. Window calls stay on this thread. */
    if (fresh && presented) fps_frames++;
    uint64_t now = sdl.GetTicksNS(), elapsed = now - fps_since;
    if (elapsed >= 1000000000ULL) {
        char caption[1024];
        snprintf(caption, sizeof caption, "%s — %.1f FPS", title(), fps_frames * 1e9 / elapsed);
        sdl.SetWindowTitle(window, caption);
        fps_since = now;
        fps_frames = 0;
    }
}

/* ---------------------------------------------------------------- what the game's threads leave */

void port_sdl_frame(const uint8_t *rgb, int w, int h) {
    if (w <= 0 || h <= 0) return;
    pthread_mutex_lock(&lock);
    if (!frame || frame_w * frame_h != w * h) { free(frame); frame = malloc((size_t)w * h * 3); }
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

/* The sound, pushed into an SDL3 audio stream as the game makes it (SDL3 converts to the device's rate). More than a
   quarter of a second waiting means the game ran ahead of the speakers: what waits is dropped, so the sound never
   lags the picture. */
void port_sdl_audio(const int16_t *lr, int frames, int hz) {
    static void *stream;
    static int failed, stream_hz;
    if (failed || frames <= 0) return;
    if (!stream || stream_hz != hz) { /* (a new rate: a new stream) */
        if (stream) sdl.DestroyAudioStream(stream);
        SdlAudioSpec spec = {SDL_AUDIO_S16LE, 2, hz};
        stream = sdl.OpenAudioDeviceStream(SDL_AUDIO_DEVICE_DEFAULT_PLAYBACK, &spec, NULL, NULL);
        if (!stream) {
            fprintf(stderr, "SDL_OpenAudioDeviceStream: %s: playing without sound\n", sdl.GetError());
            failed = 1;
            return;
        }
        stream_hz = hz;
        sdl.ResumeAudioStreamDevice(stream);
    }
    if (sdl.GetAudioStreamQueued(stream) > hz) sdl.ClearAudioStream(stream); /* hz bytes: 1/4 s of 16-bit stereo */
    sdl.PutAudioStreamData(stream, lr, frames * 4);
}
#endif
