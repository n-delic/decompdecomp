"""The port's settings menu (consoles/n64/port_wasm/window/settings.h): its rows, the file that keeps them, the keys
that change them and the picture it paints, compiled alone with the C compiler as every build that includes it does."""
import shutil
import subprocess
from pathlib import Path

import pytest

KIT = Path(__file__).resolve().parent.parent / "src" / "decompnet" / "consoles" / "n64" / "port_wasm"

pytestmark = pytest.mark.skipif(shutil.which("cc") is None, reason="needs a C compiler")

MAIN = r'''
#include "%s"
#include <assert.h>
int main(int argc, char **argv) {
    (void)argc;
    const char *file = argv[1];
    /* with no file the rows have their fallbacks */
    assert(ps_value("fps") == 1 && ps_value("volume") == 10 && ps_value("fullscreen") == 0);
    FILE *f = fopen(file, "w");
    fputs("fullscreen=ON\nvolume=30\nsmooth=NOPE\nunknown=1\n", f);
    fclose(f);
    ps_load(file);
    assert(ps_value("fullscreen") == 1);           /* read */
    assert(ps_value("volume") == 3);               /* the index of "30" */
    assert(ps_value("smooth") == 0);               /* a value that isn't a choice is ignored */
    /* navigate: down to "smooth", right changes it, left wraps back */
    port_menu.open = 1;
    ps_input(PS_DOWN);
    assert(port_menu.row == 1);
    port_menu.changed = 0;
    ps_input(PS_RIGHT);
    assert(ps_value("smooth") == 1 && port_menu.changed == 2u);
    ps_input(PS_LEFT);
    ps_input(PS_LEFT);
    assert(ps_value("smooth") == 1);               /* 0 -> 1 (wrap) */
    /* up from the first row wraps to the last action; Accept on Quit says so, Resume closes the menu */
    port_menu.row = 0;
    ps_input(PS_UP);
    assert(port_menu.row == PS_ROWS - 1 && ps_input(PS_ACCEPT) == PS_QUIT && port_menu.open);
    port_menu.row = PS_ROWS - 2;
    assert(ps_input(PS_ACCEPT) == PS_RESUME && !port_menu.open);
    /* kept whole, and read back the same */
    assert(ps_save(file));
    port_settings[ps_find("volume") - port_settings].value = 0;
    ps_load(file);
    assert(ps_value("volume") == 3);
    /* the picture: the game shows through outside the panel, and the panel has text */
    static uint8_t rgba[640 * 480 * 4];
    memset(rgba, 0xff, sizeof rgba);
    port_menu.open = 1;
    ps_draw(rgba, 640, 480);
    assert(rgba[3] == 0);                          /* the corner is clear */
    long solid = 0, bright = 0;
    for (int i = 0; i < 640 * 480; i++) { solid += rgba[i * 4 + 3] != 0; bright += rgba[i * 4 + 3] == 0xff; }
    assert(solid > 10000 && bright > 500);
    printf("ok\n");
    return 0;
}
'''


def _run(tmp_path):
    main = tmp_path / "main.c"
    main.write_text(MAIN % (KIT / "window" / "settings.h"))
    program = tmp_path / "menu"
    done = subprocess.run(["cc", "-Wall", "-Wextra", "-Werror", "-O1", "-o", str(program), str(main)],
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return subprocess.run([str(program), str(tmp_path / "settings.ini")], capture_output=True, text=True, timeout=30)


def test_the_menu_keeps_its_choices_moves_between_rows_and_paints_over_the_game(tmp_path):
    done = _run(tmp_path)
    assert done.returncode == 0 and done.stdout == "ok\n", done.stderr + done.stdout


def test_the_window_opens_the_menu_with_tab_or_f1_and_the_game_gets_no_input_while_it_is_open():
    text = (KIT / "window" / "sdl.c").read_text()
    assert '#include "settings.h"' in text and "S_TAB" in text and "S_F1" in text
    assert "if (port_menu.open) { b = 0;" in text  # the menu has the keys
    assert "renderer && (code == S_TAB" in text    # not offered while RT64 owns the window: nothing could draw it
