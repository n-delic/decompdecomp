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
    start = re.search(rf"^(?:static )?\w+ {name}\([^;{{]*\)\s*{{", text, re.M).start()  # (not a declaration)
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


def _entry_rom(words, entry=0x80000400, region=b"E", boot_crc=None, boot_words=None):
    """A homemade ROM: a header naming `entry` and `region`, and an entry routine of `words` at 0x1000. `boot_crc`:
    the boot code (0x40 to 0x1000) made to have that CRC-32, as a boot chip's has (its last word chosen so), with
    `boot_words` ({ROM offset: word}) in it."""
    import struct
    import zlib
    rom = bytearray(0x1000 + 4 * len(words) + 16)
    rom[8:12] = struct.pack(">I", entry)
    rom[0x3E:0x3F] = region
    for at, w in (boot_words or {}).items():
        rom[at:at + 4] = struct.pack(">I", w)
    for i, w in enumerate(words):
        rom[0x1000 + 4 * i:0x1004 + 4 * i] = struct.pack(">I", w)
    if boot_crc is not None:  # run the CRC back from the wanted state over the last four bytes
        state = zlib.crc32(bytes(rom[0x40:0xFFC])) ^ 0xFFFFFFFF
        want = boot_crc ^ 0xFFFFFFFF
        for _ in range(32):
            want = ((want ^ 0xEDB88320) << 1 | 1) & 0xFFFFFFFF if want & 0x80000000 else want << 1 & 0xFFFFFFFF
        rom[0xFFC:0x1000] = struct.pack("<I", want ^ state)
        assert zlib.crc32(bytes(rom[0x40:0x1000])) == boot_crc
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


# what the boot code needs around it: the ROM, and the console's RAM and RSP memories as two small arrays
_BOOT_HARNESS = """#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static uint8_t *rom; static size_t rom_size;
static uint8_t ram[0x400], sp[0x2000];
static uint8_t *at(uint32_t a) { return a >= 0xA4000000u ? sp + (a - 0xA4000000u) : ram + (a - 0x80000000u); }
static void wbe32(uint32_t a, uint32_t v) { uint8_t *p = at(a); p[0] = v >> 24; p[1] = v >> 16; p[2] = v >> 8; p[3] = v; }
static uint32_t be32(uint32_t a) { uint8_t *p = at(a); return (uint32_t)p[0] << 24 | p[1] << 16 | p[2] << 8 | p[3]; }
"""


@pytest.mark.parametrize("words,entry,region,want,crc", [
    (COUNT_DOWN, 0x80025C00, b"E", "80025C00 80026E00 8004AAA0 93840 1", None),
    (COUNT_DOWN, 0x80000400, b"P", "80000400 80026E00 8004AAA0 93840 0", None),
    (UP_TO_END, 0x80000400, b"B", "80000400 80001234 800B1000 F000 2", None),
    # made for CIC-6103, whose header names an entry 0x100000 above where the game runs (a racing game's 0x80100400)
    (COUNT_DOWN, 0x80100400, b"P", "80000400 80026E00 8004AAA0 93840 0", 0x0B050EE0),
    (COUNT_DOWN, 0x80200400, b"E", "80000400 80026E00 8004AAA0 93840 1", 0xACC8580A),  # CIC-6106: 0x200000
])
def test_any_games_boot_is_read_from_its_entry_routine(tmp_path, words, entry, region, want, crc):
    """The host named one racing game's first C function (boot_start) and where its image ended (0xA7FE0); another
    game loads at 0x80025C00 and starts at `main`. The entry routine says both, and the header the TV type. A game
    made for the CIC-6103 or 6106 boot chip runs below its header's entry: taken at its word, a racing game's code
    was loaded a megabyte off and its functions with no C had no instructions for the interpreter."""
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if not cc:
        pytest.skip("no C compiler")
    text = HOST.read_text()
    code = text[text.index("static struct { uint32_t entry, target, bss, bss_size; int tv; } boot;"):
                text.index("/* the console's RAM as the boot leaves it")]
    (tmp_path / "rom.bin").write_bytes(_entry_rom(words, entry, region, crc))
    source = tmp_path / "t.c"
    source.write_text(_BOOT_HARNESS + code + """
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


@pytest.mark.parametrize("crc,want", [
    # CIC-6103: its code from 0x4C0 copied to RAM 0, both RSP memories filled with -1, then 6103 in IMEM and osCicId
    (0x0B050EE0, "AC290000 240B17D7 FFFFFFFF FFFFFFFF 000017D7 000017D7"),
    (0x90BB6CB5, "AC290000 240B17D7 00000000 00000000 00000000 00000000"),  # CIC-6102: zeroes them, leaves no ID
    (0x12345678, "00000000 00000000 00000000 00000000 00000000 00000000"),  # a boot chip it doesn't know: nothing
])
def test_the_boot_leaves_what_the_boot_chips_code_leaves(tmp_path, crc, want):
    """A racing game made for the 6103 checks what the boot chip's code leaves: RAM 0x200 and 0x284 (copies of the boot
    code, which runs from there), the first words of the RSP's data and instruction memories, osCicId. With none of
    it, it mirrored its tracks, kept balloons from being collected and paused in its world."""
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if not cc:
        pytest.skip("no C compiler")
    text = HOST.read_text()
    code = text[text.index("static struct { uint32_t entry, target, bss, bss_size; int tv; } boot;"):
                text.index("/* the console's RAM as the boot leaves it")]
    (tmp_path / "rom.bin").write_bytes(_entry_rom(COUNT_DOWN, 0x80000400, b"P", crc,
                                                  boot_words={0x6C0: 0xAC290000, 0x744: 0x240B17D7}))
    source = tmp_path / "t.c"
    source.write_text(_BOOT_HARNESS + code + """
int main(int argc, char **argv) {
    FILE *f = fopen(argv[1], "rb"); rom = malloc(1 << 16); rom_size = fread(rom, 1, 1 << 16, f);
    boot_leftovers();
    printf("%08X %08X %08X %08X %08X %08X\\n", be32(0x80000200), be32(0x80000284), be32(0xA4000000), be32(0xA4001FFC),
           be32(0xA4001000), be32(0x80000310));
    return 0;
}
""")
    subprocess.run([cc, "-o", str(tmp_path / "t"), str(source)], check=True, capture_output=True)
    done = subprocess.run([str(tmp_path / "t"), str(tmp_path / "rom.bin")], capture_output=True, text=True, check=True)
    assert done.stdout.split() == want.split()


def test_the_cartridge_reads_as_the_rom(tmp_path):
    """The game reads its cartridge directly at 0xB0000000 (a racing game checks the word at 0x578 and turns its
    screen upside down when it isn't its own): word, half and byte loads there read the ROM."""
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if not cc:
        pytest.skip("no C compiler")
    source = tmp_path / "t.c"
    source.write_text("""#include <stdint.h>
#include <stdio.h>
#include <string.h>
#define SP_STATUS 0xA4040010u
#define IO_SCRATCH 0x000A0000u
#define IO_FAR(a) (a)
static uint8_t M[0xA0010], rom[0x1000]; static size_t rom_size = sizeof rom;
static void wbe32(uint32_t a, uint32_t v) { M[a] = v >> 24; M[a + 1] = v >> 16; M[a + 2] = v >> 8; M[a + 3] = v; }
static int port_ai_register(uint32_t a, int w, uint32_t v, uint32_t *r) { (void)a; (void)w; (void)v; (void)r; return 0; }
static int si_register(uint32_t a, int w, uint32_t v, uint32_t *r) { (void)a; (void)w; (void)v; (void)r; return 0; }
static int pi_register(uint32_t a, int w, uint32_t v, uint32_t *r) { (void)a; (void)w; (void)v; (void)r; return 0; }
""" + _function(HOST.read_text(), "rom32") + "\n" + _function(HOST.read_text(), "port_io_load") + """
int main(void) {
    rom[0x578] = 0x34; rom[0x579] = 0x21; rom[0x57A] = 0x89; rom[0x57B] = 0x65;
    uint64_t w = port_io_load(0xB0000578u), h = port_io_load(0xB000057Au), b = port_io_load(0xB0000579u);
    printf("%02X%02X%02X%02X %02X%02X %02X\\n", M[w], M[w + 1], M[w + 2], M[w + 3], M[h], M[h + 1], M[b]);
    return 0;
}
""")
    subprocess.run([cc, "-o", str(tmp_path / "t"), str(source)], check=True, capture_output=True)
    done = subprocess.run([str(tmp_path / "t")], capture_output=True, text=True, check=True)
    assert done.stdout.split() == ["34218965", "8965", "21"]


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


@pytest.mark.skipif(shutil.which("cc") is None, reason="needs a C compiler")
def test_window_fps_counts_new_presented_frames_and_decays_when_idle(tmp_path):
    """Exercise the real pump with SDL stubs: repeated retraces aren't new game frames."""
    source = tmp_path / "fps.c"
    source.write_text('#include "' + str(KIT / "window" / "sdl.c") + r'''"
#include <assert.h>
static uint64_t ticks;
static int captions, fail_present;
static char caption[1024];
static uint64_t clock_ns(void) { return ticks; }
static bool set_title(void *w, const char *text) {
    (void)w; captions++; snprintf(caption, sizeof caption, "%s", text); return true;
}
static bool poll(SdlEvent *e) { (void)e; return false; }
static const uint8_t *keys(int *n) { static uint8_t k[256]; (void)n; return k; }
static bool clear(void *r) { (void)r; return true; }
static bool draw(void *r, void *t, const void *a, const void *b) {
    (void)r; (void)t; (void)a; (void)b; return true;
}
static bool present(void *r) { (void)r; return !fail_present; }
static bool update(void *t, const void *r, const void *p, int pitch) {
    (void)t; (void)r; (void)p; (void)pitch; return true;
}
int main(void) {
    setenv("PORT_TITLE", "Demo", 1);
    sdl.GetTicksNS = clock_ns; sdl.SetWindowTitle = set_title;
    sdl.PollEvent = poll; sdl.GetKeyboardState = keys; sdl.RenderClear = clear;
    sdl.RenderTexture = draw; sdl.RenderPresent = present; sdl.UpdateTexture = update;
    renderer = (void *)1; texture = (void *)1; texture_w = texture_h = 1;
    uint8_t rgb[3] = {0};
    for (int i = 1; i <= 50; i++) {
        ticks = i * 20000000ULL;
        if (i % 2 == 0) port_sdl_frame(rgb, 1, 1);
        port_sdl_pump();
        if (i < 50) assert(captions == 0);
    }
    assert(captions == 1 && strcmp(caption, "Demo — 25.0 FPS") == 0);
    ticks += 2000000000ULL; port_sdl_pump();
    assert(captions == 2 && strcmp(caption, "Demo — 0.0 FPS") == 0);
    /* Divide by actual elapsed time, even when the host stalls. */
    port_sdl_frame(rgb, 1, 1); port_sdl_pump();
    ticks += 2000000000ULL; port_sdl_pump();
    assert(strcmp(caption, "Demo — 0.5 FPS") == 0);
    fail_present = 1; port_sdl_frame(rgb, 1, 1); port_sdl_pump();
    ticks += 1000000000ULL; port_sdl_pump();
    assert(strcmp(caption, "Demo — 0.0 FPS") == 0);
    return 0;
}
''')
    program = tmp_path / "fps"
    done = subprocess.run(["cc", "-o", str(program), str(source), "-lpthread",
                           *([] if sys.platform == "darwin" else ["-ldl"])], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    done = subprocess.run([str(program)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_the_eeprom_calls_read_and_write_the_chip_by_block(tmp_path):
    """osEepromLongRead/LongWrite and their 8-byte forms are the host's: the game's own copies go with the rest of
    what the host replaces (port/wasm/play.py), and a racing game's long read had become a stub that read nothing,
    so its default car broke its class's rules and never raced. A write of part of a block keeps the rest of it."""
    cc = shutil.which("cc") or shutil.which("clang")
    if not cc:
        pytest.skip("no C compiler")
    text = HOST.read_text()
    harness = tmp_path / "eeprom.c"
    harness.write_text("#include <stdint.h>\n#include <stdio.h>\n#include <string.h>\ntypedef uint32_t u32;\n"
                       "static uint8_t M[4096]; static uint8_t eeprom[512];\n"
                       "static void eeprom_load(void) {}\n"
                       "static void eeprom_write(int block, const uint8_t *data) { memcpy(eeprom + block * 8, data, 8); }\n"
                       + _function(text, "eeprom_read") + "\n" + _function(text, "eeprom_write_bytes") + "\n" + r"""
int main(void) {
    memset(eeprom, 0xEE, sizeof eeprom);
    for (int i = 0; i < 20; i++) M[100 + i] = (uint8_t)(i + 1);
    eeprom_write_bytes(3, 100, 20);            /* blocks 3, 4 and the first 4 bytes of 5 */
    eeprom_read(3, 200, 24);
    for (int i = 0; i < 24; i++) printf("%02X", M[200 + i]);
    printf(" %02X %02X\n", eeprom[3 * 8 - 1], eeprom[5 * 8 + 4]);
    return 0;
}
""")
    done = subprocess.run([cc, "-o", str(tmp_path / "eeprom"), str(harness)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    out = subprocess.run([str(tmp_path / "eeprom")], capture_output=True, text=True).stdout.split()
    assert out[0] == "".join(f"{i:02X}" for i in range(1, 21)) + "EEEEEEEE"  # the rest of block 5 kept
    assert out[1:] == ["EE", "EE"]  # blocks 2 and 5's tail untouched


# what the kit says the host provides but the host doesn't define as a function, with why that's right
NOT_FUNCTIONS = {"__osCurrentTime", "__osEventStateTab", "__osMaxControllers"}  # data
NEVER_RUN = {"viMgrMain", "corrupted", "corrupted_init"}  # threads and checks only libultra's managers start


def test_the_host_defines_every_function_the_kit_says_it_provides():
    """The build leaves out the game's own copy of each function the kit says the host provides (play.library), so
    one the host doesn't define becomes a stub that does nothing: a racing game's EEPROM reads, its audio
    interface's length and next buffer, and libultra's thread queries were all stubs."""
    from decompnet.consoles.n64 import port

    kit = HOST.parent
    text = "".join(p.read_text() for p in kit.glob("*.c"))
    defined = {re.sub(r"0x([0-9A-F]{2})", lambda m: chr(int(m.group(1), 16)), n)
               for n in re.findall(r"\bw2c_env_(\w+)\(struct w2c_env", text)}
    provided = set(port.LIBULTRASHIP) | set(port.GLUE_PROVIDES) | set(port._OS_INTERNAL)
    assert sorted(provided - defined - NOT_FUNCTIONS - NEVER_RUN) == []


def test_a_games_own_serial_code_moves_the_pif_through_the_registers(tmp_path):
    """A racing game whose __osSiRawStartDma library detection didn't name ran its own: it wrote SI_DRAM_ADDR and
    SI_PIF_ADDR_WR64B / _RD64B, nothing answered, and its main thread waited for the SI event for ever. The host's
    registers now move the PIF's 64 bytes as its own __osSiRawStartDma does (which raises the event)."""
    cc = shutil.which("cc") or shutil.which("clang")
    if not cc:
        pytest.skip("no C compiler")
    text = HOST.read_text()
    code = text[text.index("static uint32_t si_dram;"):text.index("u32 w2c_env_osContStartQuery")]
    harness = tmp_path / "si.c"
    harness.write_text("#include <stdint.h>\n#include <stdio.h>\ntypedef uint32_t u32;\nstruct w2c_env;\n"
                       "static int calls[2]; static u32 where[2];\n"
                       "u32 w2c_env_0x5F_osSiRawStartDma(struct w2c_env *e, u32 direction, u32 dram) {\n"
                       "    (void)e; calls[direction]++; where[direction] = dram; return 0;\n}\n" + code + r"""
int main(void) {
    uint32_t status = 1;
    int mine = si_register(0xA4800000u, 1, 0x801234C0u, NULL);        /* SI_DRAM_ADDR */
    si_register(0xA4800010u, 1, 0x1FC007C0u, NULL);                   /* SI_PIF_ADDR_WR64B: RAM to the PIF */
    si_register(0xA4800004u, 1, 0x1FC007C0u, NULL);                   /* SI_PIF_ADDR_RD64B: the PIF to RAM */
    si_register(0xA4800018u, 0, 0, &status);                          /* SI_STATUS */
    printf("%d %d %d %08X %08X %u %d\n", mine, calls[1], calls[0], where[1], where[0], status,
           si_register(0xA4500004u, 0, 0, &status));
    return 0;
}
""")
    done = subprocess.run([cc, "-o", str(tmp_path / "si"), str(harness)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    out = subprocess.run([str(tmp_path / "si")], capture_output=True, text=True).stdout.split()
    assert out == ["1", "1", "1", "801234C0", "801234C0", "0", "0"]  # not busy; the AI's registers aren't the SI's


def test_the_host_answers_libultras_underscored_labels():
    """A packed shooter's C called `_bzero` and `__osSetHWIntrRoutine`, libultra's own labels, and the host stubbed
    both to nothing: its clears didn't clear."""
    text = HOST.read_text()
    for name in ("0x5Fbzero", "0x5Fbcopy", "0x5Fbcmp", "0x5F_osSetHWIntrRoutine"):
        assert re.search(rf"\bw2c_env_{name}\(struct w2c_env", text), name


def test_the_sdks_sample_scheduler_runs_as_the_games_own_code():
    """A packed shooter's osCreateScheduler was left out as libultra glue the web host has none of, and its main
    loop waited for a scheduler that was never made: sched.c is the SDK's sample, compiled as the game's own."""
    from decompnet.consoles.n64 import port

    for name in ("osCreateScheduler", "osScAddClient", "osScGetCmdQ", "__scMain", "__scSchedule"):
        assert port._sdk_group(name) is None, name
    assert port._sdk_group("osCreateThread") is not None
    assert re.search(r"\bw2c_env_osSetIntMask\(struct w2c_env", HOST.read_text())


def test_the_host_answers_what_a_packed_shooters_boot_calls():
    """memset, strlen, the Controller Pak's file counts and osSpTaskLoad were each skipped in a packed shooter's boot."""
    text = HOST.read_text()
    for name in ("memset", "strlen", "osPfsNumFiles", "osPfsFreeBlocks", "osSpTaskLoad"):
        assert re.search(rf"\bw2c_env_{name}\(struct w2c_env", text), name


def test_ram_is_dumped_at_the_retraces_asked_for(tmp_path):
    """PORT_RAM_DUMPS="vi:file,...": the game's 8 MB of RAM, big-endian, written at those retraces, as the reversing
    emulator writes its own (EMU_DUMP), for tools/n64_frame_compare.py."""
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if not cc:
        pytest.skip("no C compiler")
    source = tmp_path / "t.c"
    source.write_text("""#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static uint8_t ram[0x800000], *M;
""" + _function(HOST.read_text(), "ram_dumps") + """
int main(void) {
    M = ram - 0x80000000u;
    for (unsigned long n = 1; n <= 5; n++) { ram[0] = (uint8_t)n; ram_dumps("PORT_RAM_DUMPS", n); }
    return 0;
}
""")
    subprocess.run([cc, "-o", str(tmp_path / "t"), str(source)], check=True, capture_output=True)
    env = {**os.environ, "PORT_RAM_DUMPS": f"2:{tmp_path / 'a.bin'},4:{tmp_path / 'b.bin'}"}
    subprocess.run([str(tmp_path / "t")], env=env, check=True)
    a, b = (tmp_path / "a.bin").read_bytes(), (tmp_path / "b.bin").read_bytes()
    assert len(a) == len(b) == 0x800000 and a[0] == 2 and b[0] == 4
    assert not (tmp_path / "c.bin").exists()


def test_scripted_input_can_be_timed_in_retraces(tmp_path):
    """PORT_INPUT counts frames shown; with PORT_INPUT_CLOCK=vi it counts retraces, as the reversing emulator's
    scripts do, so one script drives both: START from retrace 10 is held at retrace 12 though only 6 frames were shown.
    With PORT_INPUT_CLOCK=frames it counts the graphics tasks the game has handed over (9 here: START from task 9 is
    held), as the emulator's EMU_INPUT_CLOCK=frames does."""
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if not cc:
        pytest.skip("no C compiler")
    source = tmp_path / "t.c"
    source.write_text("""#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static uint16_t buttons; static int8_t stick_x, stick_y;
static unsigned long vi_count = 12, swaps = 6, gfx_tasks = 9;
""" + _function(HOST.read_text(), "script_input") + """
int main(void) { script_input(); printf("%04X\\n", buttons); return 0; }
""")
    subprocess.run([cc, "-o", str(tmp_path / "t"), str(source)], check=True, capture_output=True)
    for clock, want in (("vi", "1000"), ("", "0000"), ("frames", "0000")):
        env = {**os.environ, "PORT_INPUT": "10:START", "PORT_INPUT_CLOCK": clock}
        if clock == "frames":
            env["PORT_INPUT"] = "9:START"
            want = "1000"
        done = subprocess.run([str(tmp_path / "t")], env=env, check=True, capture_output=True, text=True)
        assert done.stdout.strip() == want


def test_cartridge_dma_accepts_offsets_and_bus_addresses(tmp_path):
    cc = shutil.which("cc") or shutil.which("clang")
    if not cc:
        pytest.skip("no C compiler")
    harness = tmp_path / "dma.c"
    harness.write_text(r"""
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef uint32_t u32;
struct w2c_env;
static uint8_t memory[128], cartridge[] = {0x10, 0x20, 0x30, 0x40};
static uint8_t *M = memory, *rom = cartridge;
static size_t rom_size = sizeof cartridge;
#define LOG(...) ((void)0)
static unsigned long dmas;
static void port_trace(void) {}
static int put(u32 mq, u32 msg, int flag, int jam) {
    (void)flag;
    (void)jam;
    assert(mq == 32 && msg == 64);
    return 0;
}
""" + _function(HOST.read_text(), "wbe32") + "\n"
        + _function(HOST.read_text(), "w2c_env_osPiStartDma") + r"""
int main(void) {
    w2c_env_osPiStartDma(NULL, 64, 0, 0, 1, 0, 2, 32);
    w2c_env_osPiStartDma(NULL, 64, 0, 0, 0xB0000001, 2, 2, 32);
    assert(memcmp(memory, "\x20\x30\x20\x30", 4) == 0);
    assert(memcmp(memory + 76, "\xB0\x00\x00\x01", 4) == 0);
    return 0;
}
""")
    program = tmp_path / "dma"
    subprocess.run([cc, str(harness), "-o", str(program)], check=True, capture_output=True, timeout=30)
    subprocess.run([str(program)], check=True, timeout=10)


@pytest.mark.skipif(shutil.which("cc") is None, reason="needs a C compiler")
def test_raw_pi_reads_rom_into_console_ram_and_compatibility_stack(tmp_path):
    source = tmp_path / "pi.c"
    host = HOST.read_text()
    source.write_text("""
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <assert.h>
static uint8_t *M;
static uint8_t rom[] = {0, 1, 2, 3, 4, 5};
static size_t rom_size = sizeof rom;
static int interrupts;
enum { EV_PI = 8 };
static void post_event(int event) { assert(event == EV_PI); interrupts++; }
#define SHADOW_STACKS 0x10000000u
#define SHADOW_SIZE 0x100000u
#define BOOT_STACK_SIZE 0x10000u
#define BOOT_STACK_BUS 0x15000000u
""" + _function(host, "stack_end") + "\n" + _function(host, "port_stack_bus") + "\n" + _function(host, "pi_register") + """
int main(void) {
    M = calloc(0x80800000u, 1);
    assert(M);
    uint32_t read;
    assert(pi_register(0xA4600004u, 1, 0x10000002u, NULL));
    assert(pi_register(0xA4600000u, 1, 0x400u, NULL));
    assert(pi_register(0xA460000Cu, 1, 3, NULL));
    assert(!memcmp(M + 0x80000400u, rom + 2, 4));
    assert(pi_register(0xA4600000u, 1, 0x1403FE90u, NULL));
    assert(pi_register(0xA460000Cu, 1, 3, NULL));
    assert(!memcmp(M + 0x1403FE90u, rom + 2, 4));
    assert(pi_register(0xA460000Cu, 1, 4, NULL));
    assert(M[0x1403FE94u] == 0);
    assert(pi_register(0xA4600010u, 0, 0, &read) && read == 0);
    assert(pi_register(0xA4600000u, 1, port_stack_bus(0xFF00u), NULL));
    assert(pi_register(0xA460000Cu, 1, 3, NULL));
    assert(!memcmp(M + 0xFF00u, rom + 2, 4));
    assert(interrupts == 4);
    free(M);
}
""")
    binary = tmp_path / "pi"
    subprocess.run(["cc", "-o", str(binary), str(source)], check=True, capture_output=True, timeout=60)
    subprocess.run([str(binary)], check=True, capture_output=True, timeout=30)
