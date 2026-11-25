"""The window host's use of recomp-ui's menu model (consoles/n64/port_wasm/window/recomp_ui.h): settings.h's rows become
recomp-ui's items, its navigation, pages and Quit confirmation drive them, and the panel draws its rows. The library is
built from the pinned source by tools/build_recomp_ui.py (it needs the network, so CI has none): these tests skip
without one (DECOMPNET_RECOMP_UI, or the toolchains folder's copy), and the code that never needs it always runs."""
import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
KIT = ROOT / "src" / "decompnet" / "consoles" / "n64" / "port_wasm"


def _builder():
    spec = importlib.util.spec_from_file_location("build_recomp_ui", ROOT / "tools" / "build_recomp_ui.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BUILDER = _builder()
LIBRARY = BUILDER.library()
needs_library = pytest.mark.skipif(LIBRARY is None or shutil.which("cc") is None,
                                   reason="needs recomp-ui's library (tools/build_recomp_ui.py) and a C compiler")

MAIN = r'''
#include "%s"
#include <assert.h>
static void rows(PsRow *r, int *n, int *current) {
    char title[64];
    *n = ps_rows(r, PS_MAX_ROWS, title, sizeof title);
    for (int i = 0; i < *n; i++) if (r[i].current) *current = i;
}
int main(int argc, char **argv) {
    (void)argc;
    assert(rui_start(argv[1]));
    PsRow r[PS_MAX_ROWS];
    int n, cur = -1;
    rows(r, &n, &cur);
    assert(!port_menu.open && n == 3 && !strcmp(r[0].label, "Video") && !strcmp(r[0].value, ">")); /* the sections */
    rui_toggle();
    assert(port_menu.open);                                  /* recomp-ui opens, and says so */
    rui_input(PS_ACCEPT);                                    /* into Video */
    rows(r, &n, &cur);
    assert(!strcmp(r[0].label, "Full screen") && !strcmp(r[0].value, "Off") && !strcmp(r[1].label, "Scaling"));
    port_menu.changed = 0;
    rui_input(PS_RIGHT);                                     /* a switch: the setting and the changed bit follow */
    assert(ps_value("fullscreen") == 1 && port_menu.changed == 1u);
    rui_input(PS_DOWN); rui_input(PS_RIGHT);                 /* a choice */
    assert(ps_value("smooth") == 1);
    rui_input(PS_BACK);                                      /* back to the sections */
    rows(r, &n, &cur);
    assert(!strcmp(r[0].label, "Video") && port_menu.open);
    rui_input(PS_DOWN); rui_input(PS_ACCEPT);                /* Audio: a number in tens */
    rui_input(PS_LEFT); rui_input(PS_LEFT);
    assert(ps_value("volume") == 8);
    rui_input(PS_RIGHT); rui_input(PS_RIGHT); rui_input(PS_RIGHT);
    assert(ps_value("volume") == 10);                        /* and stops at its end */
    rui_input(PS_BACK); rui_input(PS_DOWN); rui_input(PS_ACCEPT);   /* Game */
    assert(rui_input(PS_ACCEPT) == PS_NONE && !port_menu.open);     /* Resume closes it */
    rui_toggle();
    rui_input(PS_ACCEPT); rui_input(PS_DOWN);                /* it reopens on the sections, at Game: Quit asks twice */
    assert(rui_input(PS_ACCEPT) == PS_NONE && port_menu.open);
    assert(rui_input(PS_ACCEPT) == PS_QUIT);
    rui_toggle(); rui_toggle();
    static uint8_t rgba[640 * 480 * 4];                      /* the panel draws what recomp-ui lists */
    port_menu.open = 1;
    ps_draw(rgba, 640, 480);
    long bright = 0;
    for (int i = 0; i < 640 * 480; i++) bright += rgba[i * 4 + 3] == 0xff;
    assert(ps_panel.w > 0 && bright > 300);
    printf("ok\n");
    return 0;
}
'''


@needs_library
def test_recomp_uis_model_runs_the_window_hosts_rows_and_the_panel_draws_its_pages(tmp_path):
    main = tmp_path / "main.c"
    main.write_text(MAIN % (KIT / "window" / "recomp_ui.h"))
    program = tmp_path / "menu"
    done = subprocess.run(["cc", "-w", "-O1", "-o", str(program), str(main)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    done = subprocess.run([str(program), str(LIBRARY)], capture_output=True, text=True, timeout=30)
    assert done.returncode == 0 and done.stdout == "ok\n", done.stderr + done.stdout


@needs_library
def test_the_host_declares_recomp_uis_structures_as_the_pinned_header_does(tmp_path):
    """recomp_ui.h writes recomp-ui's C ABI out so no header is needed: each size, offset and value is checked
    against the pinned source's own header (found beside the built library)."""
    header = Path(LIBRARY).parent / ".build" / "recomp-ui" / "src"
    if not (header / "recomp_runtime_ui.h").is_file():
        pytest.skip("the pinned source isn't beside the library")
    check = tmp_path / "check.c"
    check.write_text(f'''#include "{header / "recomp_runtime_ui.h"}"
#include "{KIT / "window" / "recomp_ui.h"}"
_Static_assert(sizeof(RuiItem) == sizeof(RecompRuntimeUiItem), "item");
_Static_assert(offsetof(RuiItem, choice_values) == offsetof(RecompRuntimeUiItem, choice_values), "item layout");
_Static_assert(sizeof(RuiCallbacks) == sizeof(RecompRuntimeUiCallbacks), "callbacks");
_Static_assert(offsetof(RuiCallbacks, set_text) == offsetof(RecompRuntimeUiCallbacks, set_text), "callbacks layout");
_Static_assert(sizeof(RuiConfig) == sizeof(RecompRuntimeUiConfig), "config");
_Static_assert(offsetof(RuiConfig, navigation_flags) == offsetof(RecompRuntimeUiConfig, navigation_flags), "config layout");
_Static_assert(sizeof(RuiRowView) == sizeof(RecompRuntimeUiRowView), "row view");
_Static_assert(RUI_BOOL == RECOMP_RUNTIME_UI_BOOL && RUI_INT == RECOMP_RUNTIME_UI_INT && RUI_CHOICE == RECOMP_RUNTIME_UI_CHOICE
               && RUI_ACTION == RECOMP_RUNTIME_UI_ACTION, "item types");
_Static_assert(RUI_TOGGLE == RECOMP_RUNTIME_UI_INPUT_TOGGLE && RUI_BACK == RECOMP_RUNTIME_UI_INPUT_BACK
               && RUI_UP == RECOMP_RUNTIME_UI_INPUT_UP && RUI_DOWN == RECOMP_RUNTIME_UI_INPUT_DOWN
               && RUI_LEFT == RECOMP_RUNTIME_UI_INPUT_LEFT && RUI_RIGHT == RECOMP_RUNTIME_UI_INPUT_RIGHT
               && RUI_ACCEPT == RECOMP_RUNTIME_UI_INPUT_ACCEPT, "inputs");
int main(void) {{ return 0; }}
''')
    done = subprocess.run(["cc", "-w", "-I", str(header), "-o", str(tmp_path / "check"), str(check)],
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_the_window_asks_for_recomp_ui_only_when_told_and_falls_back_to_its_own_menu():
    window = (KIT / "window" / "sdl.c").read_text()
    assert 'getenv("PORT_RECOMP_UI")' in window and 'settings menu: the built-in one' in window
    assert "rui.menu ? rui_input(nav) : ps_input(nav)" in window and "if (rui.menu) rui_toggle();" in window
    # the pinned commit is one place, named in the build script and in the docs
    script = (ROOT / "tools" / "build_recomp_ui.py").read_text()
    revision = BUILDER.REVISION
    assert len(revision) == 40 and revision in script
    assert revision in (ROOT / "docs" / "PORTING.md").read_text()


def test_play_finds_the_built_library_only_where_told_or_in_the_toolchains_folder(tmp_path, monkeypatch):
    from decompnet.consoles.n64.port import recomp_ui_library

    library = tmp_path / "lib.dylib"
    library.write_text("x")
    monkeypatch.setenv("DECOMPNET_RECOMP_UI", str(library))
    assert recomp_ui_library() == library
    monkeypatch.setenv("DECOMPNET_RECOMP_UI", str(tmp_path / "missing.dylib"))
    assert recomp_ui_library() is None
    monkeypatch.delenv("DECOMPNET_RECOMP_UI")
    monkeypatch.setenv("DECOMPNET_TOOLCHAINS", str(tmp_path / "empty"))
    assert recomp_ui_library() is None  # nothing built: the built-in menu
    assert "PORT_RECOMP_UI" in (ROOT / "src" / "decompnet" / "port" / "wasm" / "play.py").read_text()
