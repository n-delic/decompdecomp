"""The N64 WebAssembly port's host (consoles/n64/port_wasm/host.c): what it does in place of the game's own code."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HOST = Path(__file__).resolve().parents[1] / "src/decompnet/consoles/n64/port_wasm/host.c"


def _function(text: str, name: str) -> str:
    """One function's definition from the C, its braces matched."""
    start = re.search(rf"^\w+ {name}\(", text, re.M).start()
    depth, i = 0, text.index("{", start)
    while True:
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        i += 1
        if depth == 0:
            return text[start:i]


def test_the_64_bit_conversions_are_signed_as_the_games_own(tmp_path):
    """A racing game converts doubles to 64-bit integers and back through its own __fixdfdi (0x8009A310) and
    __floatdidf (0x8009A430), both signed: they negate a negative value, convert, and put the sign back. The host
    once did both unsigned, so -1.5 became 0 and -1 became 1.8e19."""
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if not cc:
        pytest.skip("no C compiler")
    funcs = [_function(HOST.read_text(), name) for name in ("w2c_env_func_8009A310", "w2c_env_func_8009A430")]
    source = tmp_path / "t.c"
    source.write_text("#include <stdint.h>\n#include <stdio.h>\ntypedef uint64_t u64; typedef double f64;\n"
                      "struct w2c_env;\n" + "\n".join(funcs) + """
int main(void) {
    printf("%lld %lld %lld %lld\\n", (long long)w2c_env_func_8009A310(0, -1.5), (long long)w2c_env_func_8009A310(0, 2.9),
           (long long)w2c_env_func_8009A310(0, -1e300), (long long)w2c_env_func_8009A310(0, 0.0 / 0.0));
    printf("%.1f %.1f\\n", w2c_env_func_8009A430(0, (u64)-1), w2c_env_func_8009A430(0, (u64)(int64_t)-123456789012));
    return 0;
}
""")
    program = tmp_path / "t"
    subprocess.run([cc, "-O1", "-o", str(program), str(source)], check=True, capture_output=True, timeout=60)
    out = subprocess.run([str(program)], check=True, capture_output=True, text=True, timeout=30).stdout.split("\n")
    assert out[0] == "-1 2 -9223372036854775808 -9223372036854775808"
    assert out[1] == "-1.0 -123456789012.0"


KIT = HOST.parent


@pytest.mark.skipif(shutil.which("cc") is None, reason="needs a C compiler")
def test_the_native_host_compiles_with_its_sdl3_window_and_only_opens_it_when_asked(tmp_path):
    """live.c hands its calls to window/sdl.c only with PORT_WINDOW: without it nothing opens, and with it but no
    SDL3 to be found (and no PORT_LIVE to fall back to) the game refuses to run unseen."""
    main = tmp_path / "main.c"
    main.write_text(r"""#include <stdint.h>
#include <stdio.h>
void port_live_start(void);
int port_live_active(void);
void port_live_pump(void);
int main(void) { port_live_start(); port_live_pump(); printf("active %d\n", port_live_active()); return 0; }
""")
    program = tmp_path / "host"
    libs = ["-lpthread"] + (["-ldl"] if sys.platform.startswith("linux") else [])
    done = subprocess.run(["cc", "-O1", "-o", str(program), str(main), str(KIT / "live.c"), str(KIT / "window" / "sdl.c"),
                           *libs], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    env = {"PATH": os.environ.get("PATH", ""), "PORT_SDL": str(tmp_path / "missing.dylib"), "HOME": str(tmp_path)}
    plain = subprocess.run([str(program)], capture_output=True, text=True, env=env, timeout=30)
    assert plain.stdout == "active 0\n"
    alone = subprocess.run([str(program)], capture_output=True, text=True, env={**env, "PORT_WINDOW": "1"}, timeout=30)
    if "SDL3 isn't here" in alone.stderr:  # (a system SDL3 would open a real window: not here)
        assert alone.returncode == 5 and "can't open its window" in alone.stderr



def _host_with_window(tmp_path, main_source: str) -> Path:
    main = tmp_path / "main.c"
    main.write_text(main_source)
    program = tmp_path / "host"
    libs = ["-lpthread"] + (["-ldl"] if sys.platform.startswith("linux") else [])
    done = subprocess.run(["cc", "-O1", "-o", str(program), str(main), str(KIT / "live.c"), str(KIT / "window" / "sdl.c"),
                           *libs], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return program


@pytest.mark.skipif(shutil.which("cc") is None or sys.platform == "win32", reason="needs a C compiler, not Windows")
def test_an_apps_game_opened_with_no_rom_takes_its_settings_from_beside_it(tmp_path):
    """An exported window app is the game itself, opened by the Finder with no ROM named: live.c reads
    ../Resources/port.env beside the program, never overriding the environment, and keeps the saves, the ROM's path
    and the log in the app's own folder, where it runs."""
    program = _host_with_window(tmp_path, r"""#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
void port_live_app(const char *argv0);
int main(int argc, char **argv) { (void)argc; port_live_app(argv[0]); char cwd[4096];
  printf("%s|%s|%s|%s|%s\n", getenv("PORT_TITLE"), getenv("PORT_SAVE"), getenv("PORT_ROM_MEMORY"),
         getenv("PORT_WINDOW") ? getenv("PORT_WINDOW") : "-", getcwd(cwd, sizeof cwd)); return 0; }
""")
    macos = tmp_path / "Racer.app" / "Contents" / "MacOS"
    macos.mkdir(parents=True)
    (macos.parent / "Resources").mkdir()
    (macos.parent / "Resources" / "port.env").write_text("PORT_TITLE=Rally Racer\nPORT_APP=com.example.racer\n")
    shutil.copy2(program, macos / "game")
    home = tmp_path / "home"
    (home / "Library" / "Application Support").mkdir(parents=True)
    (home / ".local" / "share").mkdir(parents=True)
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(home), "PORT_SAVE": "kept.eep"}
    done = subprocess.run([str(macos / "game")], capture_output=True, text=True, env=env, timeout=30)
    folder = home / ("Library/Application Support" if sys.platform == "darwin" else ".local/share") / "com.example.racer"
    title, save, memory, window, cwd = done.stdout.strip().split("|")
    assert (title, save, memory, window) == ("Rally Racer", "kept.eep", "rom-path", "-")  # the environment wins
    assert Path(cwd).resolve() == folder.resolve()
    assert "port.env" in (folder / "play.log").read_text()  # its log, not the terminal


SDL3_HEADERS = None
try:
    from decompnet import toolchains as _toolchains
    if _toolchains.installed("sdl3"):
        SDL3_HEADERS = _toolchains.path("sdl3") / "include"
except Exception:  # (no toolchains folder: the check below skips)
    pass


@pytest.mark.skipif(SDL3_HEADERS is None or shutil.which("cc") is None, reason="needs the pinned SDL3 (toolchains: sdl3)")
def test_the_window_code_agrees_with_sdl3s_own_headers(tmp_path):
    """window/sdl.c opens SDL3 at run time, so it writes SDL3's values and structure offsets itself: each is checked
    here against the pinned SDL3's headers, so a wrong one fails to compile rather than misbehaving in a window."""
    source = (KIT / "window" / "sdl.c").read_text()
    pairs = dict(re.findall(r"\b((?:SDL|EV)_[A-Z0-9_]+) = (0x[0-9A-Fa-f]+|\d+)", source))
    pairs["SDL_AUDIO_DEVICE_DEFAULT_PLAYBACK"] = re.search(r"#define SDL_AUDIO_DEVICE_DEFAULT_PLAYBACK (\w+)u", source)[1]
    offsets = {"EV_KEY_SCANCODE": "offsetof(SDL_KeyboardEvent, scancode)",
               "EV_KEY_REPEAT": "offsetof(SDL_KeyboardEvent, repeat)",
               "EV_PAD_WHICH": "offsetof(SDL_GamepadDeviceEvent, which)"}
    scancodes = dict(re.findall(r"\bS_([A-Z0-9]+) = (\d+)", source))
    checks = [f"_Static_assert({offsets.get(name, name)} == {value}, \"{name}\");" for name, value in pairs.items()]
    checks += [f"_Static_assert(SDL_SCANCODE_{name} == {value}, \"S_{name}\");" for name, value in scancodes.items()]
    checks += ["_Static_assert(sizeof(SDL_Event) <= 128, \"SDL_Event\");",
               "_Static_assert(sizeof(SDL_AudioSpec) == 12 && offsetof(SDL_AudioSpec, freq) == 8, \"SDL_AudioSpec\");",
               "_Static_assert(sizeof(SDL_DialogFileFilter) == 2 * sizeof(char *), \"SDL_DialogFileFilter\");",
               "_Static_assert(SDL_GAMEPAD_BUTTON_START == 6 && SDL_GAMEPAD_BUTTON_DPAD_RIGHT == 14, \"buttons\");",
               "_Static_assert(SDL_GAMEPAD_AXIS_RIGHT_TRIGGER == 5, \"axes\");"]
    check = tmp_path / "check.c"
    check.write_text("#include <SDL3/SDL.h>\n#include <stddef.h>\n" + "\n".join(checks) + "\n")
    assert len(checks) > 30
    done = subprocess.run(["cc", "-I", str(SDL3_HEADERS), "-c", str(check), "-o", str(tmp_path / "check.o")],
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
