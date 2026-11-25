/*
 * recomp-ui's menu model (MIT, github.com/RetroPortingToolKit/recomp-ui, built by tools/build_recomp_ui.py into
 * libdecompnet-recomp-ui), opened at run time like SDL3 and the RT64 adapter, so nothing here is linked and a build
 * without it is unchanged: PORT_RECOMP_UI names the library, and with it the menu's navigation, pages, confirmation
 * of Quit and persistence hooks come from recomp-ui, the community's shared model, while the rows stay the ones
 * settings.h declares (they are turned into recomp-ui's items here, once) and the panel stays settings.h's own, so
 * the same picture shows over SDL's renderer and over RT64.
 *
 * The structures below are recomp-ui's C ABI (src/recomp_runtime_ui.h) written out, so no header is needed;
 * tests/test_n64_port_recomp_ui.py compiles them against the pinned header's own sizes and values.
 */
#pragma once

#include <ctype.h>
#include <dlfcn.h>
#include <stddef.h>

#include "settings.h"

typedef struct RuiMenu RuiMenu;
enum { RUI_BOOL, RUI_INT, RUI_CHOICE, RUI_ACTION, RUI_TEXT };
enum { RUI_TOGGLE, RUI_BACK, RUI_UP, RUI_DOWN, RUI_LEFT, RUI_RIGHT, RUI_ACCEPT };

typedef struct {
    const char *key, *section, *label, *description;
    int type, minimum, maximum, step;
    const char *const *choices;
    size_t choice_count;
    const int *choice_values;
} RuiItem;

typedef struct {
    void *context;
    int (*get_value)(void *, const RuiItem *, int *);
    int (*set_value)(void *, const RuiItem *, int);
    int (*run_action)(void *, const RuiItem *);
    int (*is_enabled)(void *, const RuiItem *);
    void (*save)(void *);
    void (*visibility_changed)(void *, int);
    int (*get_text)(void *, const RuiItem *, char *, size_t);
    int (*set_text)(void *, const RuiItem *, const char *);
} RuiCallbacks;

typedef struct {
    const char *title, *subtitle;
    const RuiItem *items;
    size_t item_count;
    RuiCallbacks callbacks;
    const char *theme, *accept_label, *back_label;
    uint32_t presentation_flags, navigation_flags;
} RuiConfig;

typedef struct { const RuiItem *item; char label[96], value[96]; int enabled; } RuiRowView;

static struct {
    RuiMenu *menu;
    RuiMenu *(*create)(const RuiConfig *);
    int (*handle_input)(RuiMenu *, int, int, int);
    void (*close)(RuiMenu *);
    size_t (*row_count)(const RuiMenu *);
    size_t (*selected_row)(const RuiMenu *);
    int (*row)(RuiMenu *, size_t, RuiRowView *);
    int (*confirm)(RuiMenu *, const RuiItem *, const char *);
    int result;      /* PS_QUIT once a Quit was confirmed */
    int want_close;  /* Resume ran: close once handle_input has returned */
} rui;

static RuiItem rui_items[PS_ROWS];
static char rui_text[PS_ROWS][2 * 12][24];  /* labels, sections and capitalised choices */
static const char *rui_choices[PS_ROWS][12];

/* "FULL SCREEN" to "Full screen": recomp-ui's rows are sentence case. */
static const char *rui_case(char *out, size_t size, const char *text) {
    size_t i = 0;
    for (; text[i] && i + 1 < size; i++) out[i] = (char)(i ? tolower((unsigned char)text[i]) : toupper((unsigned char)text[i]));
    out[i] = 0;
    return out;
}

static int rui_index(const RuiItem *item) {
    long i = item - rui_items;
    return i >= 0 && i < PS_ROWS ? (int)i : -1;
}

static int rui_get(void *context, const RuiItem *item, int *out) {
    (void)context;
    int i = rui_index(item);
    if (i < 0 || port_settings[i].type != PS_CHOICE) return 0;
    PortSetting *s = &port_settings[i];
    *out = item->type == RUI_INT ? atoi(s->values[s->value]) : s->value;
    return 1;
}

static int rui_set(void *context, const RuiItem *item, int value) {
    (void)context;
    int i = rui_index(item);
    if (i < 0 || port_settings[i].type != PS_CHOICE) return 0;
    PortSetting *s = &port_settings[i];
    int chosen = -1;
    if (item->type == RUI_INT) {
        for (int v = 0; v < s->count; v++)
            if (atoi(s->values[v]) == value) chosen = v;
    } else if (value >= 0 && value < s->count) chosen = value;
    if (chosen < 0) return 0;
    s->value = chosen;
    port_menu.changed |= 1u << i;
    return 1;
}

static int rui_act(void *context, const RuiItem *item) {
    (void)context;
    int i = rui_index(item);
    if (i < 0) return 0;
    if (port_settings[i].action == PS_RESUME) { rui.want_close = 1; return 1; }
    if (port_settings[i].action == PS_QUIT) { /* recomp-ui's second press, for what can't be undone */
        if (!rui.confirm(rui.menu, item, "Press again to quit")) return 0;
        rui.result = PS_QUIT;
        return 1;
    }
    return 0;
}

static int rui_enabled(void *context, const RuiItem *item) { (void)context; (void)item; return 1; }
static void rui_saved(void *context) { (void)context; }
static void rui_visible(void *context, int open) { (void)context; port_menu.open = open; }

/* The rows recomp-ui's page lists now, as the panel's words. */
static int rui_rows(PsRow *out, int max, char *title, int title_size) {
    snprintf(title, (size_t)title_size, "SETTINGS");
    size_t count = rui.row_count(rui.menu), selected = rui.selected_row(rui.menu);
    int n = 0;
    for (size_t i = 0; i < count && n < max; i++) {
        RuiRowView view;
        if (!rui.row(rui.menu, i, &view)) break;
        snprintf(out[n].label, sizeof out[n].label, "%s", view.label);
        snprintf(out[n].value, sizeof out[n].value, "%s", view.item ? view.value : ">"); /* a section opens */
        out[n].heading[0] = 0;
        out[n].current = i == selected;
        n++;
    }
    return n;
}

/* Opens the library and makes recomp-ui's menu from settings.h's rows. 0 (with a line on stderr) if it can't: the
   built-in menu then runs as before. */
static int rui_start(const char *path) {
    void *lib = dlopen(path, RTLD_NOW | RTLD_LOCAL);
    if (!lib) { fprintf(stderr, "PORT_RECOMP_UI: can't open %s: %s\n", path, dlerror()); return 0; }
#define RUI(field, name) if (!(*(void **)&rui.field = dlsym(lib, "recomp_runtime_ui_" name))) { \
        fprintf(stderr, "PORT_RECOMP_UI: %s has no recomp_runtime_ui_%s (a different recomp-ui?)\n", path, name); dlclose(lib); return 0; }
    RUI(create, "create"); RUI(handle_input, "handle_input"); RUI(close, "close"); RUI(row_count, "view_row_count");
    RUI(selected_row, "view_selected_row"); RUI(row, "view_row"); RUI(confirm, "confirm");
#undef RUI
    for (int i = 0; i < PS_ROWS; i++) {
        PortSetting *s = &port_settings[i];
        RuiItem *it = &rui_items[i];
        memset(it, 0, sizeof *it);
        it->key = s->key ? s->key : s->action == PS_QUIT ? "system.quit" : "system.resume";
        if (s->key && !strcmp(s->key, "fullscreen")) it->key = "display.fullscreen"; /* recomp-ui's shared names */
        if (s->key && !strcmp(s->key, "volume")) it->key = "audio.volume";
        it->section = rui_case(rui_text[i][0], sizeof rui_text[i][0], s->section);
        it->label = rui_case(rui_text[i][1], sizeof rui_text[i][1], s->label);
        if (s->type == PS_ACTION) { it->type = RUI_ACTION; continue; }
        int numeric = 1;
        for (int v = 0; v < s->count; v++) numeric &= isdigit((unsigned char)s->values[v][0]) != 0;
        if (s->count == 2 && !strcmp(s->values[0], "OFF") && !strcmp(s->values[1], "ON")) {
            it->type = RUI_BOOL; it->minimum = 0; it->maximum = 1; it->step = 1;
        } else if (numeric && s->count > 1) {
            it->type = RUI_INT; it->minimum = atoi(s->values[0]); it->maximum = atoi(s->values[s->count - 1]);
            it->step = atoi(s->values[1]) - atoi(s->values[0]);
        } else {
            it->type = RUI_CHOICE;
            for (int v = 0; v < s->count && v < 12; v++)
                rui_choices[i][v] = rui_case(rui_text[i][2 + v], sizeof rui_text[i][2 + v], s->values[v]);
            it->choices = rui_choices[i];
            it->choice_count = (size_t)s->count;
            it->maximum = s->count - 1; it->step = 1;
        }
    }
    RuiConfig config;
    memset(&config, 0, sizeof config);
    config.title = "Settings";
    config.items = rui_items;
    config.item_count = PS_ROWS;
    config.callbacks = (RuiCallbacks){NULL, rui_get, rui_set, rui_act, rui_enabled, rui_saved, rui_visible, NULL, NULL};
    config.theme = "n64";
    *(void **)&rui.create = dlsym(lib, "recomp_runtime_ui_create");
    rui.menu = rui.create(&config);
    if (!rui.menu) { fprintf(stderr, "PORT_RECOMP_UI: recomp-ui refused the settings\n"); dlclose(lib); return 0; }
    ps_rows_hook = rui_rows;
    fprintf(stderr, "settings menu: recomp-ui (%s)\n", path);
    return 1;
}

/* One key or button, as recomp-ui's input (PS_* from settings.h); what the window should do comes back (PS_QUIT). */
static int rui_input(int nav) {
    static const int map[] = {[PS_UP] = RUI_UP, [PS_DOWN] = RUI_DOWN, [PS_LEFT] = RUI_LEFT, [PS_RIGHT] = RUI_RIGHT,
                              [PS_ACCEPT] = RUI_ACCEPT, [PS_BACK] = RUI_BACK};
    rui.result = PS_NONE;
    rui.want_close = 0;
    rui.handle_input(rui.menu, map[nav], 1, 0);
    if (rui.want_close) rui.close(rui.menu);
    return rui.result;
}

/* Tab or F1: recomp-ui opens or closes itself (and says so through rui_visible). */
static void rui_toggle(void) { rui.handle_input(rui.menu, RUI_TOGGLE, 1, 0); }
