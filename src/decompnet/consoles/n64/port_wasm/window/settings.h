/*
 * The port's settings menu, drawn by no particular renderer. A port declares its settings here once (sections of
 * CHOICE and ACTION rows, the shape recomp-ui's runtime overlay takes: RecompRuntimeUiItem), keeps them in one
 * KEY=VALUE file, and asks for the menu to be painted into an RGBA buffer. The window code (sdl.c) shows that buffer
 * over the game; recomp-ui's ImGui overlay can take the same rows later (docs/PORTING.md, "The settings menu").
 *
 * Standard library only and header-only (static functions), so every build that includes it needs nothing new and a
 * test can compile it alone (tests/test_n64_port_settings.py).
 *
 * Keys: Tab or F1 opens and closes the menu; up and down choose a row, left and right change it, Enter acts on it,
 * Escape closes it. A gamepad: Back opens it, the d-pad moves, A acts, B closes. While it is open the game gets no
 * input (sdl.c), and it isn't paused: the console keeps its retrace.
 */
#pragma once

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

enum { PS_CHOICE, PS_ACTION };
enum { PS_UP, PS_DOWN, PS_LEFT, PS_RIGHT, PS_ACCEPT, PS_BACK };
enum { PS_NONE, PS_RESUME, PS_QUIT };

typedef struct {
    const char *section;      /* the heading it sits under (shown when it changes) */
    const char *key;          /* its name in the file; ACTIONs have none */
    const char *label;        /* uppercase: the built-in font has no lowercase */
    int type;
    int count;                /* a CHOICE's number of values */
    const char *values[12];
    int value;                /* the chosen one */
    int fallback;             /* the value with no file */
    int action;               /* an ACTION's result */
} PortSetting;

/* The rows. Order is the order on screen. */
static PortSetting port_settings[] = {
    {"VIDEO", "fullscreen", "FULL SCREEN", PS_CHOICE, 2, {"OFF", "ON"}, 0, 0, 0},
    {"VIDEO", "smooth", "SCALING", PS_CHOICE, 2, {"SHARP", "SMOOTH"}, 0, 0, 0},
    {"VIDEO", "fps", "SHOW FPS", PS_CHOICE, 2, {"OFF", "ON"}, 1, 1, 0},
    {"AUDIO", "volume", "VOLUME", PS_CHOICE, 11, {"0", "10", "20", "30", "40", "50", "60", "70", "80", "90", "100"},
     10, 10, 0},
    {"GAME", NULL, "RESUME", PS_ACTION, 0, {0}, 0, 0, PS_RESUME},
    {"GAME", NULL, "QUIT", PS_ACTION, 0, {0}, 0, 0, PS_QUIT},
};
#define PS_ROWS ((int)(sizeof port_settings / sizeof *port_settings))

static struct { int open, row; unsigned changed; } port_menu;

static PortSetting *ps_find(const char *key) {
    for (int i = 0; i < PS_ROWS; i++)
        if (port_settings[i].key && !strcmp(port_settings[i].key, key)) return &port_settings[i];
    return NULL;
}

/* A setting's chosen value by name (its fallback if the name is unknown). */
static int ps_value(const char *key) {
    PortSetting *s = ps_find(key);
    return s ? s->value : 0;
}

/* Read KEY=VALUE lines; a value that isn't one of the row's choices is ignored, so a hand-edited or newer file can't
   break the port. */
static void ps_load(const char *path) {
    FILE *f = path ? fopen(path, "r") : NULL;
    char line[256];
    if (!f) return;
    while (fgets(line, sizeof line, f)) {
        line[strcspn(line, "\r\n")] = 0;
        char *eq = strchr(line, '=');
        if (!eq) continue;
        *eq = 0;
        PortSetting *s = ps_find(line);
        if (!s || s->type != PS_CHOICE) continue;
        for (int v = 0; v < s->count; v++)
            if (!strcmp(s->values[v], eq + 1)) s->value = v;
    }
    fclose(f);
}

/* Written whole, to a temporary file first, so a crash never leaves half a file. */
static int ps_save(const char *path) {
    char tmp[4200];
    if (!path || snprintf(tmp, sizeof tmp, "%s.tmp", path) >= (int)sizeof tmp) return 0;
    FILE *f = fopen(tmp, "w");
    if (!f) return 0;
    for (int i = 0; i < PS_ROWS; i++)
        if (port_settings[i].key) fprintf(f, "%s=%s\n", port_settings[i].key, port_settings[i].values[port_settings[i].value]);
    if (fclose(f) != 0) return 0;
    return rename(tmp, path) == 0;
}

/* One key or button. Returns what the window should do (PS_RESUME closes the menu, PS_QUIT quits); port_menu.changed
   has a bit per row that changed so the window applies only those. */
static int ps_input(int nav) {
    PortSetting *s = &port_settings[port_menu.row];
    if (nav == PS_BACK) { port_menu.open = 0; return PS_RESUME; }
    if (nav == PS_UP) port_menu.row = (port_menu.row + PS_ROWS - 1) % PS_ROWS;
    else if (nav == PS_DOWN) port_menu.row = (port_menu.row + 1) % PS_ROWS;
    else if (s->type == PS_CHOICE && (nav == PS_LEFT || nav == PS_RIGHT || nav == PS_ACCEPT)) {
        int step = nav == PS_LEFT ? s->count - 1 : 1;
        s->value = (s->value + step) % s->count;
        port_menu.changed |= 1u << port_menu.row;
    } else if (s->type == PS_ACTION && nav == PS_ACCEPT) {
        if (s->action == PS_RESUME) port_menu.open = 0;
        return s->action;
    }
    return PS_NONE;
}

/* ---------------------------------------------------------------- the picture */

/* A 5x7 font, columns top bit first: capitals, digits and the few marks the rows use. */
static const struct { char c; uint8_t col[5]; } ps_font[] = {
    {' ', {0, 0, 0, 0, 0}}, {'.', {0x40, 0x60, 0, 0, 0}}, {':', {0, 0x36, 0x36, 0, 0}}, {'-', {8, 8, 8, 8, 8}},
    {'/', {0x40, 0x30, 8, 6, 1}}, {'%', {0x62, 0x64, 8, 0x13, 0x23}}, {'>', {0, 0x41, 0x22, 0x14, 8}},
    {'<', {8, 0x14, 0x22, 0x41, 0}},
    {'0', {0x3e, 0x51, 0x49, 0x45, 0x3e}}, {'1', {0, 0x42, 0x7f, 0x40, 0}}, {'2', {0x42, 0x61, 0x51, 0x49, 0x46}},
    {'3', {0x21, 0x41, 0x45, 0x4b, 0x31}}, {'4', {0x18, 0x14, 0x12, 0x7f, 0x10}}, {'5', {0x27, 0x45, 0x45, 0x45, 0x39}},
    {'6', {0x3c, 0x4a, 0x49, 0x49, 0x30}}, {'7', {1, 0x71, 9, 5, 3}}, {'8', {0x36, 0x49, 0x49, 0x49, 0x36}},
    {'9', {6, 0x49, 0x49, 0x29, 0x1e}},
    {'A', {0x7e, 0x11, 0x11, 0x11, 0x7e}}, {'B', {0x7f, 0x49, 0x49, 0x49, 0x36}}, {'C', {0x3e, 0x41, 0x41, 0x41, 0x22}},
    {'D', {0x7f, 0x41, 0x41, 0x22, 0x1c}}, {'E', {0x7f, 0x49, 0x49, 0x49, 0x41}}, {'F', {0x7f, 9, 9, 9, 1}},
    {'G', {0x3e, 0x41, 0x49, 0x49, 0x7a}}, {'H', {0x7f, 8, 8, 8, 0x7f}}, {'I', {0, 0x41, 0x7f, 0x41, 0}},
    {'J', {0x20, 0x40, 0x41, 0x3f, 1}}, {'K', {0x7f, 8, 0x14, 0x22, 0x41}}, {'L', {0x7f, 0x40, 0x40, 0x40, 0x40}},
    {'M', {0x7f, 2, 0xc, 2, 0x7f}}, {'N', {0x7f, 4, 8, 0x10, 0x7f}}, {'O', {0x3e, 0x41, 0x41, 0x41, 0x3e}},
    {'P', {0x7f, 9, 9, 9, 6}}, {'Q', {0x3e, 0x41, 0x51, 0x21, 0x5e}}, {'R', {0x7f, 9, 0x19, 0x29, 0x46}},
    {'S', {0x46, 0x49, 0x49, 0x49, 0x31}}, {'T', {1, 1, 0x7f, 1, 1}}, {'U', {0x3f, 0x40, 0x40, 0x40, 0x3f}},
    {'V', {0x1f, 0x20, 0x40, 0x20, 0x1f}}, {'W', {0x3f, 0x40, 0x38, 0x40, 0x3f}}, {'X', {0x63, 0x14, 8, 0x14, 0x63}},
    {'Y', {7, 8, 0x70, 8, 7}}, {'Z', {0x61, 0x51, 0x49, 0x45, 0x43}},
};

static void ps_px(uint8_t *rgba, int w, int h, int x, int y, uint32_t c) {
    if (x < 0 || y < 0 || x >= w || y >= h) return;
    uint8_t *p = rgba + ((size_t)y * w + x) * 4;
    p[0] = c >> 24; p[1] = c >> 16; p[2] = c >> 8; p[3] = c;
}

static void ps_rect(uint8_t *rgba, int w, int h, int x0, int y0, int rw, int rh, uint32_t c) {
    for (int y = y0; y < y0 + rh; y++)
        for (int x = x0; x < x0 + rw; x++) ps_px(rgba, w, h, x, y, c);
}

/* Text at scale `s` (a pixel of the font is s by s); returns the x after it. */
static int ps_text(uint8_t *rgba, int w, int h, int x, int y, int s, const char *text, uint32_t c) {
    for (; *text; text++, x += 6 * s) {
        char ch = *text >= 'a' && *text <= 'z' ? (char)(*text - 32) : *text;
        for (unsigned g = 0; g < sizeof ps_font / sizeof *ps_font; g++) {
            if (ps_font[g].c != ch) continue;
            for (int col = 0; col < 5; col++)
                for (int row = 0; row < 7; row++)
                    if (ps_font[g].col[col] >> row & 1) ps_rect(rgba, w, h, x + col * s, y + row * s, s, s, c);
            break;
        }
    }
    return x;
}

/* The menu over a w by h picture: everything outside the panel stays clear (alpha 0) so the game shows through. */
static void ps_draw(uint8_t *rgba, int w, int h) {
    memset(rgba, 0, (size_t)w * h * 4);
    int s = h >= 480 ? 2 : 1, line = 12 * s, pw = 200 * s;
    int heads = 0;
    for (int i = 0; i < PS_ROWS; i++)
        if (i == 0 || strcmp(port_settings[i].section, port_settings[i - 1].section)) heads++;
    int ph = (PS_ROWS + 3) * line + heads * (line * 3 / 2), x0 = (w - pw) / 2, y0 = (h - ph) / 2;
    ps_rect(rgba, w, h, x0, y0, pw, ph, 0x000000d8);
    ps_rect(rgba, w, h, x0, y0, pw, s, 0xffffffff);
    ps_rect(rgba, w, h, x0, y0 + ph - s, pw, s, 0xffffffff);
    int y = y0 + line;
    ps_text(rgba, w, h, x0 + 8 * s, y, s, "SETTINGS", 0xffffffff);
    y += line;
    for (int i = 0; i < PS_ROWS; i++) {
        PortSetting *r = &port_settings[i];
        if (i == 0 || strcmp(r->section, port_settings[i - 1].section)) {
            y += line / 2;
            ps_text(rgba, w, h, x0 + 8 * s, y, s, r->section, 0x8899aaff);
            y += line;
        }
        uint32_t c = i == port_menu.row ? 0xffe060ff : 0xe0e0e0ff;
        if (i == port_menu.row) ps_text(rgba, w, h, x0 + 8 * s, y, s, ">", c);
        ps_text(rgba, w, h, x0 + 20 * s, y, s, r->label, c);
        if (r->type == PS_CHOICE) {
            char value[32];
            snprintf(value, sizeof value, "< %s >", r->values[r->value]);
            ps_text(rgba, w, h, x0 + pw - 8 * s - (int)strlen(value) * 6 * s, y, s, value, c);
        }
        y += line;
    }
}
