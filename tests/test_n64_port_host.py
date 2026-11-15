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
    vrally = HOST.parent / "games" / "da3f262dfb86.c"  # (that racing game's own host file)
    funcs = [_function(vrally.read_text(), name) for name in ("w2c_env_func_8009A310", "w2c_env_func_8009A430")]
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


def _entry_rom(words, entry=0x80000400, region=b"E"):
    """A homemade ROM: a header naming `entry` and `region`, and an entry routine of `words` at 0x1000."""
    import struct
    rom = bytearray(0x1000 + 4 * len(words) + 16)
    rom[8:12] = struct.pack(">I", entry)
    rom[0x3E:0x3F] = region
    for i, w in enumerate(words):
        rom[0x1000 + 4 * i:0x1004 + 4 * i] = struct.pack(">I", w)
    return bytes(rom)


def _lui(rt, imm): return 0x0F << 26 | rt << 16 | imm & 0xFFFF
def _addiu(rt, rs, imm, op=9): return op << 26 | rs << 21 | rt << 16 | imm & 0xFFFF
def _sw0(off, base): return 0x2B << 26 | base << 21 | off & 0xFFFF
def _bne(rs, rt, off): return 5 << 26 | rs << 21 | rt << 16 | off & 0xFFFF
def _jr(rs): return rs << 21 | 8


T0, T1, T2, SP = 8, 9, 10, 29
# the SDK's entry routine, as two racing games have it: zero the BSS counting its size down, set sp, jump
COUNT_DOWN = [_lui(T0, 0x8005), _addiu(T0, T0, -0x5560), _lui(T1, 9), _addiu(T1, T1, 0x3840),
              _sw0(0, T0), _sw0(4, T0), _addiu(T0, T0, 8, op=8), _addiu(T1, T1, -8, op=8), _bne(T1, 0, -5), 0,
              _lui(T2, 0x8002), _addiu(T2, T2, 0x6E00), _lui(SP, 0x8007), _jr(T2), _addiu(SP, SP, 0x1F70)]
# another way to write it: up to an end address
UP_TO_END = [_lui(T0, 0x800B), _addiu(T0, T0, 0x1000), _lui(T1, 0x800C), _sw0(0, T0), _addiu(T0, T0, 4),
             _bne(T0, T1, -3), 0, 0x0C000000 | (0x80001234 >> 2) & 0x3FFFFFF, 0]


@pytest.mark.parametrize("words,entry,region,want", [
    (COUNT_DOWN, 0x80025C00, b"E", "80025C00 80026E00 8004AAA0 93840 1"),
    (COUNT_DOWN, 0x80000400, b"P", "80000400 80026E00 8004AAA0 93840 0"),
    (UP_TO_END, 0x80000400, b"B", "80000400 80001234 800B1000 F000 2"),
])
def test_any_games_boot_is_read_from_its_entry_routine(tmp_path, words, entry, region, want):
    """The host named one racing game's first C function (boot_start) and where its image ended (0xA7FE0); another
    game loads at 0x80025C00 and starts at `main`. The entry routine says both, and the header the TV type."""
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if not cc:
        pytest.skip("no C compiler")
    text = HOST.read_text()
    code = text[text.index("static struct { uint32_t entry, target, bss, bss_size; int tv; } boot;"):
                text.index("/* the console's RAM as the boot leaves it")]
    (tmp_path / "rom.bin").write_bytes(_entry_rom(words, entry, region))
    source = tmp_path / "t.c"
    source.write_text("#include <stdint.h>\n#include <stdio.h>\n#include <stdlib.h>\n#include <string.h>\n"
                      "static uint8_t *rom; static size_t rom_size;\n" + code + """
int main(int argc, char **argv) {
    FILE *f = fopen(argv[1], "rb"); rom = malloc(1 << 16); rom_size = fread(rom, 1, 1 << 16, f);
    int ok = read_boot();
    printf("%08X %08X %08X %X %d\\n", boot.entry, boot.target, boot.bss, boot.bss_size, boot.tv);
    return !ok;
}
""")
    subprocess.run([cc, "-o", str(tmp_path / "t"), str(source)], check=True, capture_output=True)
    done = subprocess.run([str(tmp_path / "t"), str(tmp_path / "rom.bin")], capture_output=True, text=True, check=True)
    assert done.stdout.split() == want.split()


def test_the_host_names_no_one_games_functions(tmp_path):
    """Functions named by one game's addresses (V-Rally's bzero at 0x80091920) were the host's for every game: they
    live in that game's own file now, which the build compiles for its ROM alone."""
    text = HOST.read_text()
    assert not re.search(r"w2c_env_(func|D)_[0-9A-F]{8}\b", text) and "boot_start" not in text and "IMAGE_END" not in text
    from decompnet.port.wasm.build import game_host
    host = HOST.parent
    repo = tmp_path / "save"
    (repo / "progress").mkdir(parents=True)
    (repo / "progress" / "decompdecomp.json").write_text('{"game": {"sha1": "DA3F262DFB86CFF1376C9A1AA081212E5A7AAD64"}}')
    assert game_host(host, repo) == [host / "games" / "da3f262dfb86.c"]
    (repo / "progress" / "decompdecomp.json").write_text('{"game": {"sha1": "0123456789abcdef"}}')
    assert game_host(host, repo) == []
