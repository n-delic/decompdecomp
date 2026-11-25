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


def _jal(addr): return 3 << 26 | (addr >> 2) & 0x3FFFFFF
def _jalr(rs): return rs << 21 | 31 << 11 | 9
def _sltu(rd, rs, rt): return rs << 21 | rt << 16 | rd << 11 | 0x2B
def _move_zero(rd): return rd << 11 | 0x21
BREAK = 0x0D
A0, A1, A2 = 4, 5, 6

# and the form a compiler gives a `while (p < end) *p++ = 0`: the store is the branch's delay slot, and the loop is
# skipped when the range is empty (a racing game's sibling, a tank game: its BSS was never zeroed, and the game ran on
# the ROM's bytes in it)
AT = 1
DELAY_SLOT_CLEAR = [_lui(T0, 0x8007), _addiu(T0, T0, 0x1940), _lui(T1, 0x8011), _addiu(T1, T1, -0x20),
                    4 << 26 | T0 << 21 | T1 << 16 | 5, 0, _addiu(T0, T0, 4), _sltu(AT, T0, T1), _bne(AT, 0, -3),
                    _sw0(-4, T0), 0x0C000000 | (0x80001234 >> 2) & 0x3FFFFFF, 0, BREAK, 0]
# an entry routine that fills a stack with a pattern and clears its BSS with one word-fill helper (`jal fill`, $a0 =
# start, $a1 = end, $a2 = the word, here set in the delay slot), then calls the game's first function and breaks
FILL_HELPER = [_addiu(A1, A1, -4, op=8), _sltu(T0, A0, A1), 4 << 26 | T0 << 16 | 3 & 0xFFFF, 0x2B << 26 | A0 << 21 | A2 << 16,
               _sltu(T0, A0, A1), _bne(0, T0, -3), _addiu(A0, A0, 4, op=8), _jr(31), 0]
FILL_ENTRY = [_lui(A0, 0x800A), _addiu(A0, A0, 0x1000), _lui(A1, 0x800B), _addiu(A1, A1, 0), _lui(A2, 0x5555),
              _addiu(A2, A2, 0x5555, op=0x0D), _jal(0x80000448), 0, _lui(A0, 0x800B), _addiu(A0, A0, 0x3210),
              _lui(A1, 0x800C), _addiu(A1, A1, 0x10), _jal(0x80000448), _move_zero(A2), _jal(0x80001234), 0, BREAK, 0,
              *FILL_HELPER]
# the SDK's entry routine as a compiler lays it out: the BSS zeroed by `sltu at, t0, t1; bnez at, loop` with the store
# in the branch's delay slot, then a second loop for a section with nothing in it (start = end)
def _zero_loop(start, end):
    return [_lui(T0, start >> 16), _addiu(T0, T0, start & 0xFFFF), _lui(T1, end >> 16), _addiu(T1, T1, end & 0xFFFF),
            4 << 26 | T0 << 21 | T1 << 16 | 5, 0, _addiu(T0, T0, 4), _sltu(1, T0, T1), 5 << 26 | 1 << 21 | (-3 & 0xFFFF),
            _sw0(-4, T0)]
DELAY_SLOT_ENTRY = [_lui(SP, 0x803F), _addiu(SP, SP, 0xFFF0, op=0x0D), *_zero_loop(0x80073F40, 0x80180330),
                    *_zero_loop(0x80073F40, 0x80073F40), _jal(0x80014510), 0, BREAK, 0]
# a game that sets up the TLB through a helper and starts at the virtual address its code is mapped at
TLB_ENTRY = [_lui(SP, 0x803F), _jal(0x80000420), 0, _lui(A0, 0x29), _addiu(A0, A0, 0x2E00), _jalr(A0), 0, BREAK,
             _jr(31), 0]

# what the boot code needs around it: the ROM, and the console's RAM and RSP memories as two small arrays
_BOOT_HARNESS = """#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static uint8_t *rom; static size_t rom_size;
static uint8_t ram[0x800000], sp[0x2000];
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
    # the first call is a fill helper, not the game: its zero fill is the BSS, and the call before a `break` starts the game
    (FILL_ENTRY, 0x80000400, b"E", "80000400 80001234 800B3210 CE00 1", None),
    # the clear's store sits in the branch's delay slot, up to the register the loop compares with
    (DELAY_SLOT_CLEAR, 0x80000400, b"E", "80000400 80001234 80071940 9E6A0 1", None),
    # the zeroing loop's store sits in the branch's delay slot (a racing game's fishing sibling: the BSS was left as the
    # ROM's bytes and the game's pointers came out wild); the empty second loop must not stand for it
    (DELAY_SLOT_ENTRY, 0x80000400, b"E", "80000400 80014510 80073F40 10C3F0 1", None),
    # a start address below KSEG0 (a `jalr` to a TLB-mapped 0x00292E00) is the same code at its KSEG0 address
    (TLB_ENTRY, 0x80000400, b"E", "80000400 80292E00 00000000 0 1", None),
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


def _sw(rt, off, base): return 0x2B << 26 | base << 21 | rt << 16 | off & 0xFFFF
A3, T0_ = 7, 8
# an entry routine that maps its code through the TLB the way libultra's osMapTLB takes it (index in $a0, page mask in
# $a1, the virtual address in $a2, the even page's physical address in $a3, the odd page's and the ASID on the stack),
# then jumps to the virtual address: 1 MB pages at 0x200000 (even: physical 0, odd: physical 0x100000)
TLB_MAP_ENTRY = [_lui(SP, 0x803F), _addiu(A0, 0, 0x1F), _lui(A1, 0x1F), _addiu(A1, A1, 0xE000, op=0x0D),
                 _lui(A2, 0x20), _addiu(A3, 0, 0), _lui(T1, 0x10), _sw(T1, 0x10, SP), _addiu(T0_, 0, 7),
                 _jal(0x80000440), _sw(T0_, 0x14, SP), _lui(A0, 0x29), _addiu(A0, A0, 0x2E00), _jalr(A0), 0, BREAK,
                 _jr(31), 0]
# the same with the even page unmapped (-1), its physical address left out: only the odd page is a window
TLB_ODD_ONLY = TLB_MAP_ENTRY[:5] + [_addiu(A3, 0, -1), _lui(T1, 0), *TLB_MAP_ENTRY[7:]]


@pytest.mark.parametrize("words,want", [
    (TLB_MAP_ENTRY, "00200000 200000 00000000"),  # (the pair's two pages continue each other: one window)
    (TLB_ODD_ONLY, "00300000 100000 00000000"),
])
def test_the_entry_routines_tlb_setup_gives_the_windows_the_game_runs_through(tmp_path, words, want):
    """Some games map their code at a virtual address (a 1 MB page pair) and the split names it `0x80000000 | va`:
    the interpreter read zeros at the virtual address and stopped. The host reads the pages from the entry routine's
    own call, gives an address in one its KSEG0 spelling, and finds the code's bytes at the physical alias."""
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if not cc:
        pytest.skip("no C compiler")
    text = HOST.read_text()
    code = text[text.index("static struct { uint32_t entry, target, bss, bss_size; int tv; } boot;"):
                text.index("/* the console's RAM as the boot leaves it")]
    (tmp_path / "rom.bin").write_bytes(_entry_rom(words, 0x80000400, b"E"))
    source = tmp_path / "t.c"
    source.write_text(_BOOT_HARNESS + code + """
int main(int argc, char **argv) {
    FILE *f = fopen(argv[1], "rb"); rom = malloc(1 << 16); rom_size = fread(rom, 1, 1 << 16, f);
    int ok = read_boot();
    for (unsigned i = 0; i < port_tlb_n; i++) printf("%08X %X %08X\\n", tlb_win[i].lo, tlb_win[i].size, tlb_win[i].phys);
    /* a mapped address is read at its KSEG0 spelling, the code's bytes live at the physical alias, others stay */
    printf("%08X %08X %08X %08X\\n", port_tlb_canon(0x00292E00), port_tlb_canon(0x80292E00), port_tlb_canon(0x00500000),
           port_tlb_alias(0x80292E00));
    return !ok;
}
""")
    subprocess.run([cc, "-o", str(tmp_path / "t"), str(source)], check=True, capture_output=True)
    done = subprocess.run([str(tmp_path / "t"), str(tmp_path / "rom.bin")], capture_output=True, text=True, check=True)
    *windows, probes = done.stdout.strip().splitlines()
    assert "\n".join(windows) == want
    canon_even = "80292E00" if "00200000" in want else "00292E00"
    alias = "80092E00" if "00200000" in want else "00000000"
    assert probes.split() == [canon_even, "80292E00", "00500000", alias]


def test_a_tlb_window_a_game_writes_with_tlbwi_is_named_by_its_physical_address(tmp_path):
    """A game's first C function can set the TLB up itself (mtc0 to PageMask, EntryHi, EntryLo0/1, then tlbwi) and
    jump into a window high in the virtual space (0x10000000, two 4 MB pages onto physical 0 and 0x400000). The split
    names that code by its physical address in KSEG0: an address in the window is read there, a low one stays."""
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if not cc:
        pytest.skip("no C compiler")
    text = HOST.read_text()
    code = text[text.index("static struct { uint32_t entry, target, bss, bss_size; int tv; } boot;"):
                text.index("/* the console's RAM as the boot leaves it")]
    source = tmp_path / "t.c"
    source.write_text(_BOOT_HARNESS + code + """
int main(void) {
    port_tlb_write(0x7FE000, 0x10000000, 0x1F, 0x1001F); /* the EntryLo words: valid, page frames 0 and 0x400 */
    for (unsigned i = 0; i < port_tlb_n; i++) printf("%08X %X %08X\\n", tlb_win[i].lo, tlb_win[i].size, tlb_win[i].phys);
    printf("%08X %08X %08X %08X\\n", port_tlb_canon(0x10001050), port_tlb_canon(0x10400010), port_tlb_canon(0x20000000),
           port_tlb_canon(0x80001050));
    printf("%u %08X %08X %08X %08X\\n", port_tlb_hi, port_tlb_wasm(0x100010F8), port_tlb_wasm(0x10400010), port_tlb_wasm(0x00100000),
           port_tlb_wasm(0x20000000));
    port_tlb_write(0x7FE000, 0x30000000, 0x1D, 0x1D); /* not valid (bit 1 clear): no window */
    printf("%u\\n", port_tlb_n);
    return 0;
}
""")
    subprocess.run([cc, "-o", str(tmp_path / "t"), str(source)], check=True, capture_output=True)
    done = subprocess.run([str(tmp_path / "t")], capture_output=True, text=True, check=True)
    assert done.stdout.strip().splitlines() == [
        "10000000 800000 00000000", "80001050 80400010 20000000 80001050",
        "1 800010F8 80400010 00100000 20000000", "1"]


def test_a_low_window_shows_its_physical_words_at_the_spelling_where_that_holds_nothing(tmp_path):
    """A jump table a game reads at virtual 0x00102330 (a window of 1 MB at 0x100000 onto physical 0) was copied to the
    boot image at 0x2330 and never written at the port's spelling, 0x80102330: the read gave 0 and the function
    jumped to address 0x100. The words come across once, where the spelling is empty; words already there stay."""
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if not cc:
        pytest.skip("no C compiler")
    text = HOST.read_text()
    code = text[text.index("static struct { uint32_t entry, target, bss, bss_size; int tv; } boot;"):
                text.index("/* the console's RAM as the boot leaves it")]
    source = tmp_path / "t.c"
    source.write_text(_BOOT_HARNESS + code + """
int main(void) {
    wbe32(0x80002330u, 0x80103060u);  /* the jump table's entry, in the boot image at the physical address */
    port_tlb_write(0x1FE000, 0x00000000, 0u, (0u >> 12 << 6) | 2); /* the odd page of the pair at 0: virtual 0x100000 -> 0 */
    printf("%08X %08X\\n", be32(0x80102330u), be32(0x80002330u));
    return 0;
}
""")
    subprocess.run([cc, "-o", str(tmp_path / "t"), str(source)], check=True, capture_output=True)
    done = subprocess.run([str(tmp_path / "t")], capture_output=True, text=True, check=True)
    assert done.stdout.split() == ["80103060", "80103060"]


def test_a_window_whose_spelling_holds_the_games_data_is_kept_at_its_physical_address(tmp_path):
    """A game's second stage under a window at virtual 0x200000 (physical 0x600000) was read at 0x80200000, where its
    boot loader's image and compressed program are: the library it brought in never ran, and a call spelled
    `jal 0x00347080` went to the loader's bytes. A window whose spelling holds anything is read at the physical
    address it maps; a window over empty spelling stays spelled, and switches once a transfer puts data at the
    spelling (a table read through the window gave the strings written there); the project's name for code in such
    a window (0x80000000 | virtual) is told back by port_tlb_spelled, and a call spelled at the virtual address goes
    to the window by port_tlb_call."""
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if not cc:
        pytest.skip("no C compiler")
    text = HOST.read_text()
    code = text[text.index("static struct { uint32_t entry, target, bss, bss_size; int tv; } boot;"):
                text.index("/* the console's RAM as the boot leaves it")]
    source = tmp_path / "t.c"
    source.write_text(_BOOT_HARNESS + code + """
int main(void) {
    wbe32(0x80300000u, 0x12345678u);  /* the loader's own bytes where a window's spelling would be */
    port_tlb_write(0x1FE000, 0x00200000, (0x600000u >> 12 << 6) | 2, (0x700000u >> 12 << 6) | 2); /* 0x200000 -> 0x600000 */
    /* a window over empty spelling: spelled, until a transfer puts data at the spelling */
    port_tlb_write(0x1FE000, 0x00400000, (0x100000u >> 12 << 6) | 2, 1u);
    printf("%08X %08X\\n", port_tlb_canon(0x00347080u), port_tlb_canon(0x00400010u));
    port_tlb_physical_use(0x80400000u, 0x1000u);
    printf("%08X\\n", port_tlb_canon(0x00400010u));
    printf("%08X %08X %08X\\n", port_tlb_call(0x80347080u), port_tlb_spelled(0x80747080u), port_tlb_call(0x80500000u));
    return 0;
}
""")
    subprocess.run([cc, "-o", str(tmp_path / "t"), str(source)], check=True, capture_output=True)
    done = subprocess.run([str(tmp_path / "t")], capture_output=True, text=True, check=True)
    # physical 0x747080; the empty-spelling window spelled; then kept at its physical address (0x100010);
    # the call and the name told back through the window
    assert done.stdout.split() == ["80747080", "80400010", "80100010", "80747080", "80347080", "80500000"]


def test_a_store_of_the_games_own_into_a_windows_spelling_keeps_the_window_at_its_physical_address(tmp_path):
    """A loader that copies its tables with stores (not a transfer) to 0x80200000, under a window at virtual 0x200000 onto
    physical 0, destroyed the copy of the window's code made at the spelling and the interpreter then ran the tables as
    code (a break and garbage at 0x802012E0). The store, made through the KSEG0 name, keeps the window at its physical
    address like a transfer does; the span of such spellings (port_spell_*) names where to look, and the span of the
    kept windows (port_real_*) is where a jump goes to the physical alias."""
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if not cc:
        pytest.skip("no C compiler")
    text = HOST.read_text()
    code = text[text.index("static struct { uint32_t entry, target, bss, bss_size; int tv; } boot;"):
                text.index("/* the console's RAM as the boot leaves it")]
    source = tmp_path / "t.c"
    source.write_text(_BOOT_HARNESS + code + """
int main(void) {
    port_tlb_write(0x1FE000, 0x00200000, 2u, 1u); /* 0x200000 -> physical 0, spelled at 0x80200000 */
    printf("%08X %08X %08X %08X\\n", port_spell_lo, port_spell_len, port_real_lo, port_real_len);
    printf("%08X\\n", port_tlb_canon(0x002012A0u));
    port_tlb_spell_store(0x802012A0u);
    printf("%08X %08X %08X\\n", port_spell_len, port_real_lo, port_real_len);
    printf("%08X %08X\\n", port_tlb_canon(0x002012A0u), port_tlb_call(0x80201CB0u));
    return 0;
}
""")
    subprocess.run([cc, "-o", str(tmp_path / "t"), str(source)], check=True, capture_output=True)
    done = subprocess.run([str(tmp_path / "t")], capture_output=True, text=True, check=True)
    assert done.stdout.split() == ["80200000", "00100000", "FFFFFFFF", "00000000",   # spelled: watched, none kept
                                   "802012A0",                                     # read at the spelling
                                   "00000000", "80200000", "00100000",             # kept: nothing watched, the span kept
                                   "800012A0", "80001CB0"]                         # read at, and a jump goes to, the alias


def test_a_call_spelled_in_kseg0_beyond_the_ram_names_the_high_windows_virtual_address(tmp_path):
    """A game that pages its code in at 0x15xxxxxx (4 KB pages) has calls the project spelled `func_85016170`: the
    `jal 0x05016170` made from KSEG0 code took its top four bits from there. No console has RAM at 0x85016170, and
    the interpreter ran zeros ("no instructions"). With one window base below 8 (here 1) such a spelling names
    the virtual address (0x15016170), so a mapped page translates it and an evicted one takes the game's refill
    handler. Two bases are ambiguous and an address inside the RAM is left alone."""
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if not cc:
        pytest.skip("no C compiler")
    text = HOST.read_text()
    code = text[text.index("static struct { uint32_t entry, target, bss, bss_size; int tv; } boot;"):
                text.index("/* the console's RAM as the boot leaves it")]
    source = tmp_path / "t.c"
    source.write_text(_BOOT_HARNESS + code + """
int main(void) {
    printf("%08X ", port_tlb_virtual(0x85016170u));  /* no window yet: the address as given */
    port_tlb_write(0, 0x15016000, (0x2CE000u >> 12 << 6) | 2, 1u);
    printf("%08X %08X %08X ", port_tlb_virtual(0x85016170u), port_tlb_virtual(0x802CE170u), port_tlb_virtual(0xB5016170u));
    port_tlb_write(0, 0x25006000, (0x2CF000u >> 12 << 6) | 2, 1u);  /* a second base: ambiguous */
    printf("%08X\\n", port_tlb_virtual(0x85016170u));
    return 0;
}
""")
    subprocess.run([cc, "-o", str(tmp_path / "t"), str(source)], check=True, capture_output=True)
    done = subprocess.run([str(tmp_path / "t")], capture_output=True, text=True, check=True)
    assert done.stdout.split() == ["85016170", "15016170", "802CE170", "B5016170", "85016170"]


def test_code_reached_by_its_frames_name_gets_the_virtual_page_that_maps_it(tmp_path):
    """A paging game maps consecutive virtual code pages onto frames far apart (virtual 0x15082000 on physical 0x2FA000,
    the next page 0x15083000 on 0x300000). The project names that code by the frame (`func_802FA...`), so the
    interpreter entered it with no virtual pc and ran on into the next FRAME at the page's end: another function's
    bytes, and a loop around the call spun for ever. The frame's virtual address is told by the page that maps it; a
    frame the game also maps inside a large window of all of RAM (virtual 0x10000000 onto physical 0) is told by the
    page, a frame no high window maps is not told, and two pages of one size mapping it are ambiguous."""
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if not cc:
        pytest.skip("no C compiler")
    text = HOST.read_text()
    code = text[text.index("static struct { uint32_t entry, target, bss, bss_size; int tv; } boot;"):
                text.index("/* the console's RAM as the boot leaves it")]
    source = tmp_path / "t.c"
    source.write_text(_BOOT_HARNESS + code + """
int main(void) {
    printf("%08X ", port_tlb_virtual_of(0x802FAD80u));                  /* no window yet */
    /* a big window: two 4 MB pages, virtual 0x10000000 -> physical 0 and 0x400000 */
    port_tlb_write(0x007FE000u, 0x10000000, (0u << 6) | 2, (0x400000u >> 12 << 6) | 2);
    port_tlb_write(0, 0x15082000, (0x2FA000u >> 12 << 6) | 2, 1u);       /* a code page onto frame 0x2FA000 */
    printf("%08X %08X ", port_tlb_virtual_of(0x802FAD80u), port_tlb_virtual_of(0x80300010u));
    port_tlb_write(0, 0x25082000, (0x2FA000u >> 12 << 6) | 2, 1u);       /* a second page of the same size: ambiguous */
    printf("%08X %08X\\n", port_tlb_virtual_of(0x802FAD80u), port_tlb_virtual_of(0x90000000u));
    return 0;
}
""")
    subprocess.run([cc, "-o", str(tmp_path / "t"), str(source)], check=True, capture_output=True)
    done = subprocess.run([str(tmp_path / "t")], capture_output=True, text=True, check=True)
    # (a frame only the big window maps is told by it: the window is contiguous, so the code sees no difference)
    assert done.stdout.split() == ["00000000", "15082D80", "10300010", "00000000", "00000000"]


def test_a_page_mapped_at_virtual_zero_is_read_at_its_physical_address(tmp_path):
    """A game keeps its own call dispatcher at virtual address 0 (a 4 KB page onto physical 0x42000; its `jr $zero` thunks
    jump there). Spelled `0x80000000 | virtual` that is where libultra's osInitialize writes the exception vectors, so
    the dispatcher was overwritten by the vector stubs and every call went to the error routine. A page at 0 is kept at
    the physical address its bytes are at; the vectors are left alone."""
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if not cc:
        pytest.skip("no C compiler")
    text = HOST.read_text()
    code = text[text.index("static struct { uint32_t entry, target, bss, bss_size; int tv; } boot;"):
                text.index("/* the console's RAM as the boot leaves it")]
    source = tmp_path / "t.c"
    source.write_text(_BOOT_HARNESS + code + """
int main(void) {
    wbe32(0x80042000u, 0x01004825u);  /* the dispatcher's first word, at the physical address */
    wbe32(0x80000000u, 0x3C1A8000u);  /* the vector stub osInitialize wrote at the exception vector */
    port_tlb_write(0, 0x00000000, (0x42000u >> 12 << 6) | 2, 1); /* virtual 0 -> physical 0x42000, 4 KB, the odd page invalid */
    printf("%08X %08X %08X\\n", port_tlb_canon(0x0), port_tlb_canon(0xFFC), port_tlb_canon(0x1000));
    printf("%08X %08X\\n", be32(0x80000000u), be32(0x80042000u));
    return 0;
}
""")
    subprocess.run([cc, "-o", str(tmp_path / "t"), str(source)], check=True, capture_output=True)
    done = subprocess.run([str(tmp_path / "t")], capture_output=True, text=True, check=True)
    assert done.stdout.splitlines() == ["80042000 80042FFC 00001000", "3C1A8000 01004825"]


def test_pages_a_refill_handler_brings_in_one_by_one_make_one_window(tmp_path):
    """A refill handler writes a 4 KB page pair per exception: a program of a megabyte is 128 of them, which filled
    the window table. A pair that continues a window (the next virtual pages onto the next physical ones) extends it.
    Past the 32 slots a tlbwr evicts what its slot mapped: the first nine pairs (the 40 written and the odd page after
    them took slots 0 to 8 again) are gone from the windows, as they are from the console's TLB."""
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if not cc:
        pytest.skip("no C compiler")
    text = HOST.read_text()
    code = text[text.index("static struct { uint32_t entry, target, bss, bss_size; int tv; } boot;"):
                text.index("/* the console's RAM as the boot leaves it")]
    source = tmp_path / "t.c"
    source.write_text(_BOOT_HARNESS + code + """
int main(void) {
    for (unsigned n = 0; n < 40; n++) /* 40 pairs: virtual 0x7F000000 up onto physical 0x300000 up */
        port_tlb_write(0, 0x7F000000u + n * 0x2000u, ((0x300000u + n * 0x2000u) >> 12 << 6) | 2,
                       ((0x301000u + n * 0x2000u) >> 12 << 6) | 2);
    port_tlb_write(0, 0x7F200000u, (0x500000u >> 12 << 6) | 2, 1); /* a page that continues nothing: a window of its own */
    for (unsigned i = 0; i < port_tlb_n; i++) printf("%08X %X %08X\\n", tlb_win[i].lo, tlb_win[i].size, tlb_win[i].phys);
    printf("%08X %08X\\n", port_tlb_canon(0x7F0000F8), port_tlb_canon(0x7F04F000));
    return 0;
}
""")
    subprocess.run([cc, "-o", str(tmp_path / "t"), str(source)], check=True, capture_output=True)
    done = subprocess.run([str(tmp_path / "t")], capture_output=True, text=True, check=True)
    assert done.stdout.splitlines() == ["7F012000 3E000 00312000", "7F200000 1000 00500000", "7F0000F8 8034F000"]


def test_the_tlb_keeps_the_entries_the_game_wrote_and_tells_an_invalid_page_from_a_missing_one(tmp_path):
    """tlbp and tlbr read back the entries the game's own code wrote; a page an entry covers with its valid bit clear is
    a TLB Invalid exception (the general vector) and a page no entry covers is a refill; a tlbwi over a slot takes the
    pages it mapped before out of the windows; a tlbwr takes a free slot."""
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if not cc:
        pytest.skip("no C compiler")
    text = HOST.read_text()
    code = text[text.index("static struct { uint32_t entry, target, bss, bss_size; int tv; } boot;"):
                text.index("/* the console's RAM as the boot leaves it")]
    source = tmp_path / "t.c"
    source.write_text(_BOOT_HARNESS + code + """
int main(void) {
    uint32_t pm, hi, lo0, lo1;
    /* slot 3: the page at 0x7F000000 invalid, its pair 0x7F001000 valid onto physical 0x300000 */
    port_tlb_set(3, 0, 0x7F000000u, 0x1u, (0x300000u >> 12 << 6) | 2);
    printf("%X %X\\n", port_tlb_probe(0x7F000800u), port_tlb_probe(0x7F100000u));
    printf("%d %d %d\\n", port_tlb_invalid(0x7F000010u), port_tlb_invalid(0x7F001010u), port_tlb_invalid(0x7E000000u));
    printf("%08X %08X\\n", port_tlb_canon(0x7F001010u), port_tlb_canon(0x7F000010u));
    /* the same slot again, elsewhere: what it mapped before is gone */
    port_tlb_set(3, 0, 0x7E000000u, (0x400000u >> 12 << 6) | 2, 1);
    printf("%08X %08X %d\\n", port_tlb_canon(0x7F001010u), port_tlb_canon(0x7E000010u), port_tlb_invalid(0x7E001010u));
    printf("%d ", port_tlb_read(3, &pm, &hi, &lo0, &lo1));
    printf("%08X %08X %08X\\n", hi, lo0, lo1);
    /* tlbwr: a free slot (0), and a page that continues another extends its window */
    port_tlb_write(0, 0x7D000000u, (0x500000u >> 12 << 6) | 2, (0x501000u >> 12 << 6) | 2);
    printf("%X %08X\\n", port_tlb_probe(0x7D000000u), port_tlb_canon(0x7D001010u));
    return 0;
}
""")
    subprocess.run([cc, "-o", str(tmp_path / "t"), str(source)], check=True, capture_output=True)
    done = subprocess.run([str(tmp_path / "t")], capture_output=True, text=True, check=True)
    assert done.stdout.splitlines() == ["3 80000000", "1 0 0", "80300010 7F000010", "7F001010 80400010 1",
                                        "1 7E000000 00010002 00000001", "0 80501010"]


def test_a_window_in_kernel_mapped_space_names_the_ram_it_maps_and_unmapped_space_is_left_alone(tmp_path):
    """A game maps its 8 MB of RAM at 0xE0000000 (KSEG3: two 4 MB pages onto physical 0 and 0x400000, by osMapTLB) and
    keeps its queues, threads and decompression buffers there. Windows at 0x80000000 up were refused and the lookups
    only looked below 0x80000000, so the host read a queue at 0xE010D628 from one place and the interpreter wrote it
    to another: a loader's decompressed program never reached the physical bytes it jumped into ("no instructions at
    80002950"). KSEG0 and KSEG1 (0x80000000 to 0xBFFFFFFF) are not mapped by the TLB and stay as they are."""
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if not cc:
        pytest.skip("no C compiler")
    text = HOST.read_text()
    code = text[text.index("static struct { uint32_t entry, target, bss, bss_size; int tv; } boot;"):
                text.index("/* the console's RAM as the boot leaves it")]
    source = tmp_path / "t.c"
    source.write_text(_BOOT_HARNESS + code + """
int main(void) {
    port_tlb_write(0x7FE000, 0xE0000000, 0x1F, 0x1001F); /* the EntryLo words: valid, page frames 0 and 0x400 */
    port_tlb_write(0x7FE000, 0xA0000000, 0x1F, 0x1001F); /* KSEG1 is not mapped: no window */
    for (unsigned i = 0; i < port_tlb_n; i++) printf("%08X %X %08X\\n", tlb_win[i].lo, tlb_win[i].size, tlb_win[i].phys);
    printf("%08X %08X %08X %08X\\n", port_tlb_canon(0xE010D628), port_tlb_canon(0xE0400010), port_tlb_canon(0xA0001000),
           port_tlb_canon(0x80001050));
    printf("%08X\\n", port_tlb_wasm(0xE0170240));
    return 0;
}
""")
    subprocess.run([cc, "-o", str(tmp_path / "t"), str(source)], check=True, capture_output=True)
    done = subprocess.run([str(tmp_path / "t")], capture_output=True, text=True, check=True)
    assert done.stdout.strip().splitlines() == [
        "E0000000 800000 00000000", "8010D628 80400010 A0001000 80001050", "80170240"]
    # the host's own reads and writes of the game's structures, and a DMA's destination, go the same way
    assert "static inline uint32_t gv(uint32_t a)" in text and "memcpy(&v, M + gv(a), 4)" in text
    assert "static u32 dma_ram(u32 dram)" in text and "dram >= 0xC0000000u" in text and "dma_ram(dram_given)" in text


def test_tlbwr_leaves_the_wired_slots_alone_and_osgettlbhi_and_osunmaptlb_read_and_clear_a_slot(tmp_path):
    """A paging game wires its permanent mappings (the Wired register) and lets tlbwr replace the rest: a wired slot
    is never the one a tlbwr takes. Its pager finds the slot of a page by reading every slot's EntryHi
    (__osGetTLBHi: `mtc0 i, Index; tlbr; mfc0 EntryHi`) and unmaps it (osUnmapTLB), so the next access refills."""
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if not cc:
        pytest.skip("no C compiler")
    text = HOST.read_text()
    code = text[text.index("static struct { uint32_t entry, target, bss, bss_size; int tv; } boot;"):
                text.index("/* the console's RAM as the boot leaves it")]
    source = tmp_path / "t.c"
    source.write_text(_BOOT_HARNESS + code + """
int main(void) {
    uint32_t pm, hi, lo0, lo1;
    port_tlb_set(0, 0x007FE000u, 0x70000000u, 0x1Fu, 1); /* a wired 4 MB page at 0x70000000 onto physical 0 */
    port_tlb_set(1, 0, 0x60000000u, (0x300000u >> 12 << 6) | 2, 1); /* a wired page at 0x60000000 */
    printf("%u ", port_tlb_wired(1, 2));
    for (unsigned n = 0; n < 70; n++) /* more pages than the 30 slots left: the wired two must survive */
        port_tlb_write(0, 0x7F000000u + n * 0x2000u, ((0x400000u + n * 0x2000u) >> 12 << 6) | 2, 1);
    printf("%d %d\\n", port_tlb_read(0, &pm, &hi, &lo0, &lo1) && hi == 0x70000000u, port_tlb_read(1, &pm, &hi, &lo0, &lo1) && hi == 0x60000000u);
    printf("%08X %08X\\n", port_tlb_canon(0x70000100u), port_tlb_canon(0x60000010u));
    printf("%08X %08X\\n", w2c_env_0x5F_osGetTLBHi(0, 1), w2c_env_0x5F_osGetTLBHi(0, 31) == 0x80000000u ? 0x80000000u : 0);
    w2c_env_osUnmapTLB(0, 1);
    printf("%08X %08X\\n", w2c_env_0x5F_osGetTLBHi(0, 1), port_tlb_canon(0x60000010u));
    return 0;
}
""")
    subprocess.run([cc, "-o", str(tmp_path / "t"), str(source)], check=True, capture_output=True)
    done = subprocess.run([str(tmp_path / "t")], capture_output=True, text=True, check=True)
    lines = done.stdout.splitlines()
    assert lines[0].startswith("2 1 1")
    assert lines[1] == "80000100 80300010"
    assert lines[2].split()[0] == "60000000"  # the readback of the wired slot
    assert lines[3] == "80000000 60000010"  # unmapped: the slot reads as no program's address and the window is gone


def test_the_faulted_thread_variable_is_found_in_the_games_own_exception_handler(tmp_path):
    """The host delivers a TLB fault by filling the faulting OSThread and pointing __osFaultedThread at it, a variable it
    finds in the game's libultra handler: the preamble at the general vector (`lui k0; addiu k0,k0,lo; jr k0`) leads to
    __osException, whose panic block stores the faulted thread (`sw k0, var`) and marks it faulted (`sh x, 18(k0)`).
    A handler linked in a TLB window is read through the window."""
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if not cc:
        pytest.skip("no C compiler")
    text = HOST.read_text()
    code = text[text.index("static uint32_t fault_variable(void) {"):text.index("/* A TLB fault the game's own pager answers.")]
    source = tmp_path / "t.c"
    source.write_text(_BOOT_HARNESS + """
static int verbose;
#define LOG(...) do { } while (0)
uint32_t port_tlb_canon(uint32_t a) { return a >= 0x70000000u && a < 0x70010000u ? a - 0x70000000u + 0x80300000u : a; }
""" + code + """
int main(void) {
    /* the preamble: lui k0,0x7000; addiu k0,k0,0x1A0; jr k0 (the handler at virtual 0x700001A0, onto physical 0x3001A0) */
    wbe32(0x80000180u, 0x3C1A7000u); wbe32(0x80000184u, 0x275A01A0u); wbe32(0x80000188u, 0x03400008u);
    uint32_t h = 0x803001A0u;
    wbe32(h + 0x00, 0x3C018002u);       /* lui at, 0x8002 */
    wbe32(h + 0x04, 0x3C018002u);       /* lui at, 0x8002 */
    wbe32(h + 0x08, 0xAC3A7734u);       /* sw k0, 0x7734(at): __osFaultedThread = 0x80027734 */
    wbe32(h + 0x0C, 0x24090001u);       /* li t1, 1 */
    wbe32(h + 0x10, 0xA7490010u);       /* sh t1, 16(k0) */
    wbe32(h + 0x14, 0x24090002u);       /* li t1, 2 */
    wbe32(h + 0x18, 0xA7490012u);       /* sh t1, 18(k0) */
    printf("%08X\\n", fault_variable());
    return 0;
}
""")
    subprocess.run([cc, "-o", str(tmp_path / "t"), str(source)], check=True, capture_output=True)
    done = subprocess.run([str(tmp_path / "t")], capture_output=True, text=True, check=True)
    assert done.stdout.split()[-1] == "80027734"


def test_a_jal_in_code_linked_at_a_windows_virtual_base_takes_the_windows_top_bits(tmp_path):
    """A game links its program at 0x70000400 (one 4 MB page onto physical 0, set by its own tlbwi) and the port
    keeps that code at its KSEG0 spelling, so a `jal` took its top four bits from 0x8...: `jal 0x7F000CD0` went to
    0x8F000CD0 ("no instructions"). The window's top bits are used when the target is itself in a mapped window;
    a jump whose window-spelled target is not mapped, and code outside any window, keep the plain computation."""
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if not cc:
        pytest.skip("no C compiler")
    text = HOST.read_text()
    code = text[text.index("static struct { uint32_t entry, target, bss, bss_size; int tv; } boot;"):
                text.index("/* the console's RAM as the boot leaves it")]
    source = tmp_path / "t.c"
    source.write_text(_BOOT_HARNESS + code + """
int main(void) {
    uint32_t imm = 0x0F000CD0u;
    printf("%08X\\n", port_tlb_jump(0x80006020u, imm));              /* no window yet: the plain top bits */
    port_tlb_write(0x7FE000, 0x70000000, 0x1F, 1);                  /* 4 MB at 0x70000000 onto physical 0 */
    printf("%08X\\n", port_tlb_jump(0x80006020u, imm));              /* 0x7F000CD0 is not mapped yet, but 0x8F000CD0 is no */
                                                                    /* RAM: the window's bits (a refill brings the page in) */
    port_tlb_write(0x7FE000, 0x7F000000, 0x1F | (0x400 << 6), 1);   /* the game segment, onto physical 0x400000 */
    printf("%08X\\n", port_tlb_jump(0x80006020u, imm));              /* both mapped: the window's top bits */
    printf("%08X\\n", port_tlb_jump(0x80900020u, imm));              /* code outside every window: plain */
    printf("%08X\\n", port_tlb_jump(0x70006020u, imm));              /* already a virtual spelling: its own bits */
    printf("%08X\\n", port_tlb_jump(0x80006020u, 0x00500000u));      /* a target inside RAM the windows do not cover: plain */
    return 0;
}
""")
    subprocess.run([cc, "-o", str(tmp_path / "t"), str(source)], check=True, capture_output=True)
    done = subprocess.run([str(tmp_path / "t")], capture_output=True, text=True, check=True)
    assert done.stdout.split() == ["8F000CD0", "7F000CD0", "7F000CD0", "8F000CD0", "7F000CD0", "80500000"]


def test_the_games_c_reads_a_high_tlb_window_as_ram_and_no_stack_sits_in_one():
    """A game's compiled C is handed pointers into its windows (0x100010F8): wasm2c's loads and stores ask the host
    once a window high in the virtual space exists. The threads' shadow stacks were at 0x10000000, where a game maps
    such a window; they live in KSEG0 above the MIPS stacks now."""
    host = HOST.read_text()
    stacks = int(re.search(r"#define SHADOW_STACKS (0x[0-9A-Fa-f]+)u", host)[1], 16)
    assert 0x82000000 <= stacks and stacks + 32 * 0x100000 <= 0xA0000000  # KSEG0, above the 8 MB and the MIPS stacks
    bemem = (Path(__file__).resolve().parents[1] / "src/decompnet/port/wasm/bemem.py").read_text()
    assert "port_tlb_hi && addr < 0x80000000u" in bemem and bemem.count("port_tlb_wasm((u32)addr)") == 1


@pytest.mark.parametrize("crc,want", [
    # CIC-6103: its code from 0x4C0 copied to RAM 0, both RSP memories filled with -1, then 6103 in IMEM and osCicId
    (0x0B050EE0, "AC290000 240B17D7 FFFFFFFF FFFFFFFF 000017D7 000017D7 00000000 00000000 00000000"),
    (0x90BB6CB5, "AC290000 240B17D7 00000000 00000000 00000000 00000000 00000000 00000000 00000000"),  # CIC-6102: zeroes them, leaves no ID
    (0x12345678, "00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000"),  # a boot chip it doesn't know: nothing
    # CIC-6106: no copy of its (scrambled) code, the RSP's memories as the 6103's with ID 6104, and the decoded words
    # a racing game's seven boot self-tests read back (RAM 0x164 and 0x2B8 shown): it parked its main thread on purpose
    (0xACC8580A, "00000000 00000000 FFFFFFFF FFFFFFFF 000017D8 000017D8 00000000 01EC6021 8941680C"),
    # CIC-6105: its boot code keeps a constant at ROM 0x750 (copied to RAM 0x200) and leaves it at 0x2FE1C0 too: a
    # platformer's first function hangs for ever when the word isn't there
    (0x98BC2C86, "AD170014 00000000 A4002000 A4002000 000017D9 000017D9 AD170014 00000000 00000000"),
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
                                                  boot_words={0x6C0: 0xAC290000, 0x744: 0x240B17D7, 0x750: 0xAD170014}))
    source = tmp_path / "t.c"
    source.write_text(_BOOT_HARNESS + code + """
int main(int argc, char **argv) {
    FILE *f = fopen(argv[1], "rb"); rom = malloc(1 << 16); rom_size = fread(rom, 1, 1 << 16, f);
    boot_leftovers();
    printf("%08X %08X %08X %08X %08X %08X %08X %08X %08X\\n", be32(0x80000200), be32(0x80000284), be32(0xA4000000),
           be32(0xA4001FFC), be32(0xA4001000), be32(0x80000310), be32(0x802FE1C0), be32(0x80000164), be32(0x800002B8));
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
#define DPC_BASE 0xA4100000u
#define IO_SCRATCH 0x000A0000u
#define IO_FAR(a) (a)
static uint8_t M[0xA0010], rom[0x1000]; static size_t rom_size = sizeof rom;
static uint32_t cart_io_last; static int cart_io_busy;
static void wbe32(uint32_t a, uint32_t v) { M[a] = v >> 24; M[a + 1] = v >> 16; M[a + 2] = v >> 8; M[a + 3] = v; }
static int port_ai_register(uint32_t a, int w, uint32_t v, uint32_t *r) { (void)a; (void)w; (void)v; (void)r; return 0; }
static int si_register(uint32_t a, int w, uint32_t v, uint32_t *r) { (void)a; (void)w; (void)v; (void)r; return 0; }
static int pi_register(uint32_t a, int w, uint32_t v, uint32_t *r) { (void)a; (void)w; (void)v; (void)r; return 0; }
static int flash_in_use; static uint32_t save_offset(uint32_t b) { return (b & 0x1FFFFFFFu) - 0x08000000u; }
static uint32_t port_flash_read(uint32_t o) { (void)o; return 0; } static void port_flash_command(uint32_t c) { (void)c; }
static uint32_t dpc_start, dpc_end, dpc_cur, dpc_start_word, dpc_end_word; static int dpc_start_valid; static void dpc_run(void) {}
""" + _function(HOST.read_text(), "rom32") + "\n" + _function(HOST.read_text(), "open_bus") + "\n" + _function(HOST.read_text(), "port_io_load") + "\n" + _function(HOST.read_text(), "port_io_store") + """
int main(void) {
    rom[0x578] = 0x34; rom[0x579] = 0x21; rom[0x57A] = 0x89; rom[0x57B] = 0x65;
    uint64_t w = port_io_load(0xB0000578u), h = port_io_load(0xB000057Au), b = port_io_load(0xB0000579u);
    printf("%02X%02X%02X%02X %02X%02X %02X\\n", M[w], M[w + 1], M[w + 2], M[w + 3], M[h], M[h + 1], M[b]);
    port_io_store(0xB0000580u, 0xE4CD7BFDu);
    w = port_io_load(0xB0000578u);
    printf("%02X%02X%02X%02X ", M[w], M[w+1], M[w+2], M[w+3]);
    cart_io_busy = 0;
    w = port_io_load(0xB0000578u);
    printf("%02X%02X%02X%02X\\n", M[w], M[w+1], M[w+2], M[w+3]);
    return 0;
}
""")
    subprocess.run([cc, "-o", str(tmp_path / "t"), str(source)], check=True, capture_output=True)
    done = subprocess.run([str(tmp_path / "t")], capture_output=True, text=True, check=True)
    assert done.stdout.split() == ["34218965", "8965", "21", "E4CD7BFD", "34218965"]


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
    provided = set(port.LIBULTRASHIP) | set(port.GLUE_PROVIDES) | set(port._OS_INTERNAL) | (set(port._LIBC) - set(port._MATH))
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
    for (unsigned long n = 1; n <= 5; n++) { ram[0] = (uint8_t)n; ram_dumps("PORT_RAM_DUMPS", n, 0); }
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
static int traces;
static void port_trace(void) { traces++; }
#define SHADOW_STACKS 0x10000000u
#define SHADOW_SIZE 0x100000u
#define BOOT_STACK_SIZE 0x10000u
#define BOOT_STACK_BUS 0x15000000u
static int put(u32 mq, u32 msg, int flag, int jam) {
    (void)flag;
    (void)jam;
    assert(mq == 32 && msg == 64);
    return 0;
}
static unsigned port_tlb_n;
uint32_t port_tlb_canon(uint32_t a) { return a; }
static inline uint32_t gv(uint32_t a) { return a >= 0xC0000000u ? port_tlb_canon(a) : a; }
void port_tlb_physical_use(uint32_t a, uint32_t size) { (void)a; (void)size; }
""" + _function(HOST.read_text(), "wbe32") + "\n" + _function(HOST.read_text(), "stack_end") + "\n"
        + _function(HOST.read_text(), "dma_ram") + "\n"
        + _function(HOST.read_text(), "w2c_env_osPiStartDma") + r"""
int main(void) {
    w2c_env_osPiStartDma(NULL, 64, 0, 0, 1, 0, 2, 32);
    w2c_env_osPiStartDma(NULL, 64, 0, 0, 0xB0000001, 2, 2, 32);
    assert(memcmp(memory, "\x20\x30\x20\x30", 4) == 0);
    assert(memcmp(memory + 76, "\xB0\x00\x00\x01", 4) == 0);
    int invalid = traces;
    w2c_env_osPiStartDma(NULL, 0, 0, 1, 0, 0x8080FFF0u, 8, 0);
    assert(traces == invalid);
    w2c_env_osPiStartDma(NULL, 0, 0, 1, 0, 0x81FFFFFCu, 8, 0);
    assert(traces == invalid + 1);
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
static int cart_io_busy;
static int interrupts;
static unsigned long dmas, vi_count;
#define LOG(...) ((void)0)
enum { EV_PI = 8 };
static void post_event(int event) { assert(event == EV_PI); interrupts++; }
#define SHADOW_STACKS 0x10000000u
#define SHADOW_SIZE 0x100000u
#define BOOT_STACK_SIZE 0x10000u
#define BOOT_STACK_BUS 0x15000000u
static int save_window(uint32_t bus) { (void)bus; return 0; }
static int save_dma(uint32_t d, uint32_t c, uint32_t n, uint32_t w) { (void)d; (void)c; (void)n; (void)w; return 0; }
""" + _function(host, "stack_end") + "\n" + _function(host, "port_stack_bus") + "\n" + _function(host, "pi_register") + """
int main(void) {
    M = calloc(0x82000000u, 1);
    assert(M);
    uint32_t read;
    assert(pi_register(0xA4600004u, 1, 0x10000002u, NULL));
    assert(pi_register(0xA4600000u, 1, 0x400u, NULL));
    assert(pi_register(0xA460000Cu, 1, 3, NULL));
    assert(!memcmp(M + 0x80000400u, rom + 2, 4));
    /* a KSEG0 stack's physical address (osVirtualToPhysical of 0x8083FE90) is where the game's raw PI code points the DMA */
    assert(pi_register(0xA4600000u, 1, 0x0083FE90u, NULL));
    assert(pi_register(0xA460000Cu, 1, 3, NULL));
    assert(!memcmp(M + 0x8083FE90u, rom + 2, 4));
    assert(pi_register(0xA460000Cu, 1, 4, NULL));
    assert(M[0x8083FE94u] == 0);
    assert(pi_register(0xA4600010u, 0, 0, &read) && read == 0);
    cart_io_busy = 1;
    assert(pi_register(0xA4600010u, 0, 0, &read) && read == 2);
    cart_io_busy = 0;
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


@pytest.mark.skipif(shutil.which("c++") is None, reason="needs a C++ compiler")
def test_rt64_vi_fields_features_and_incomplete_framebuffers(tmp_path):
    source = tmp_path / "present.cpp"
    source.write_text(r"""
#include "presentation.h"
#include <cassert>

int main() {
    assert(port_rt64_vi_features(0x3202, 0x55) == 0x1321e);
    assert(port_rt64_vi_features(0x1321e, 0xaa) == 0x3202);
    assert(port_rt64_vi_features(0x321e, 0x03) == 0x3216);

    assert(port_rt64_incomplete_framebuffer(320, 140, 2, 320, 240, 2));
    assert(port_rt64_incomplete_framebuffer(640, 480, 2, 320, 240, 2));
    assert(port_rt64_incomplete_framebuffer(320, 240, 3, 320, 240, 2));
    assert(!port_rt64_incomplete_framebuffer(640, 480, 2, 640, 476, 2));

    // A high-resolution interlaced mode advances by one more row for its second field.
    const uint32_t mode[] = {0x324e, 640, 0, 0, 0, 0, 0, 0, 0,
                             1280, 0x2000800, 0x5d0237, 0x9026b, 2,
                             2560, 0x3000800, 0x5f0239, 0xd0269, 4};
    const auto even = port_rt64_vi_field(mode, 0);
    assert(even.origin == 1280 && even.y_scale == 0x2000800 && even.v_start == 0x5d0237);
    assert(even.v_burst == 0x9026b && even.interrupt == 2);
    const auto odd = port_rt64_vi_field(mode, 1);
    assert(odd.origin == 2560 && odd.y_scale == 0x3000800 && odd.v_start == 0x5f0239);
    assert(odd.v_burst == 0xd0269 && odd.interrupt == 4);
}
""")
    program = tmp_path / "present"
    subprocess.run(["c++", "-std=c++17", "-I", str(KIT / "rt64"), str(source), "-o", str(program)],
                   check=True, capture_output=True, timeout=60)
    subprocess.run([str(program)], check=True, capture_output=True, timeout=30)


def test_extended_pi_routes_save_chip_handles_to_the_save_device(tmp_path):
    cc = shutil.which('cc') or shutil.which('clang') or shutil.which('gcc')
    if not cc:
        pytest.skip('no C compiler')
    function = _function(HOST.read_text(), 'w2c_env_osEPiStartDma')
    source = tmp_path / 'test.c'
    source.write_text(r'''
#include <stdint.h>
#include <stdio.h>
typedef uint32_t u32;
struct w2c_env;
static uint8_t memory[1024], *M = memory;
static int sram_calls, pi_calls, flash_calls, messages;
static uint32_t fields[32];
static uint32_t be32(uint32_t a) { return fields[a / 4]; }
static uint32_t stack_end(uint32_t a) { return a >= 0x1000 && a < 0x2000 ? 0x2000 : 0; }
static void put(uint32_t q, uint32_t msg, int a, int b) { (void)q; (void)msg; (void)a; (void)b; messages++; }
int port_sram_dma(uint32_t dram, uint32_t offset, uint32_t size, uint32_t write) {
    sram_calls++; return !(dram == 0x1800 && offset == 0x123 && size == 4 && write == 1);
}
int port_flash_dma(uint32_t dram, uint32_t offset, uint32_t size, uint32_t write) {
    flash_calls++; return !(dram == 0x1800 && offset == 0x123 && size == 4 && write == 1);
}
#define PI_TYPE_FLASH 8
static int flash_handle(u32 handle) { return handle && M[handle + 4] == PI_TYPE_FLASH; }
static uint32_t flash_offset(u32 dev) { uint32_t bus = dev & 0x1FFFFFFFu; return bus >= 0x08000000u ? bus - 0x08000000u : bus; }
u32 w2c_env_osPiStartDma(struct w2c_env *e, u32 mb, u32 pri, u32 dir, u32 dev, u32 dram, u32 size, u32 mq) {
    (void)e; (void)mb; (void)pri; (void)dir; (void)dev; (void)dram; (void)size; (void)mq; pi_calls++; return 0;
}
''' + function + r'''
int main(void) {
    fields[(64 + 4) / 4] = 20; fields[(64 + 8) / 4] = 0x1800;
    fields[(64 + 12) / 4] = 0x08000123; fields[(64 + 16) / 4] = 4;
    memory[36] = 3; if (w2c_env_osEPiStartDma(0, 32, 64, 1)) return 2;
    memory[36] = 2; if (w2c_env_osEPiStartDma(0, 32, 64, 1)) return 3; /* the cartridge's own handle: plain PI */
    memory[36] = 8; if (w2c_env_osEPiStartDma(0, 32, 64, 1)) return 5;  /* FlashRAM: its chip, not the ROM */
    fields[(64 + 8) / 4] = 0x1fff; memory[36] = 3;
    if (w2c_env_osEPiStartDma(0, 32, 64, 1) != (u32)-1) return 4;
    printf("%d %d %d %d\n", sram_calls, pi_calls, flash_calls, messages);
    return 0;
}
''')
    binary = tmp_path / 'test'
    built = subprocess.run([cc, '-O1', '-o', str(binary), str(source)], capture_output=True, text=True, timeout=60)
    assert built.returncode == 0, built.stderr
    result = subprocess.run([str(binary)], capture_output=True, text=True, check=True, timeout=30)
    assert result.stdout.strip() == '1 1 1 2'


def test_the_host_defines_the_libm_functions_the_port_says_it_provides():
    """port.py classes sinf and cosf as the host's C library, but the host defined only sqrtf: an import of either was
    built as an empty stub ("sinf: not implemented, skipped"), and a game's rotation matrices came out as NaN. Each
    libm name the host is said to provide has a definition and the prototype the link uses."""
    from decompnet.consoles.n64 import port

    text = HOST.read_text()
    sigs = {line.split("\t")[0] for line in (HOST.parent / "libultra.sigs").read_text().splitlines()}
    for name in ("sqrtf", "sinf", "cosf", "ldexpf", "frexpf"):
        assert port._sdk_group(name) == "replaced", name
        assert re.search(rf"\bw2c_env_{name}\(struct w2c_env", text), name
        assert name in sigs, name


def test_the_vi_shows_the_buffer_at_its_24_bit_origin():
    """A shooter hands osViSwapBuffer 0x847B4F80 (a flag bit above the address): the VI's origin holds 24 bits, so the
    buffer shown is at 0x7B4F80. The renderer took 0x047B4F80, past RDRAM, its height wrapped to millions of rows and
    every frame allocated gigabytes (RT64 got one frame in twelve seconds)."""
    text = HOST.read_text()
    assert "fb & 0x00FFFFFFu" in text
    render = (HOST.parent / "render.c").read_text()
    assert "if (addr >= 0x800000) return;" in render


def test_the_interpreter_runs_a_branch_in_a_taken_branchs_delay_slot(tmp_path):
    """A copy loop ends in two `bnez $a3` in a row: the second sits in the first's delay slot. The interpreter took
    that for an instruction it doesn't run and skipped it (20 lines of 'isn't one it runs' and a loop that never went
    on). A taken branch in a taken branch's delay slot runs the first one's target once, then goes where the second
    goes; one not taken in the slot, or in a slot of a branch not taken, is an ordinary instruction."""
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if not cc:
        pytest.skip("no C compiler")
    mips = HOST.parent / "mips.c"
    source = tmp_path / "t.c"
    source.write_text(r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include "MIPS_C"
static uint8_t *mem;
uint8_t *port_memory(void) { return mem; }
uint64_t port_io_load(uint64_t a) { return a; }
uint64_t port_io_store(uint64_t a, uint64_t v) { (void)v; return a; }
uint32_t port_stack_bus(uint32_t a) { (void)a; return 0; }
void port_safe_point(void) {}
void port_boot_spin(void) {}
int port_thread_number(void) { return 0; }
unsigned port_tlb_n;
uint32_t port_tlb_canon(uint32_t a) { return a; }
uint32_t port_tlb_alias(uint32_t a) { (void)a; return 0; }
uint32_t port_tlb_jump(uint32_t slot, uint32_t imm) { return (slot & 0xF0000000u) | imm; }
uint32_t port_tlb_call(uint32_t a) { return a; }
uint32_t port_tlb_spelled(uint32_t a) { return a; }
uint32_t port_spell_lo = 0xFFFFFFFFu, port_spell_len, port_real_lo = 0xFFFFFFFFu, port_real_len; /* (no window to watch or keep here) */
void port_tlb_spell_store(uint32_t a) { (void)a; }
uint32_t port_tlb_virtual(uint32_t a) { return a; }
uint32_t port_tlb_virtual_of(uint32_t a) { (void)a; return 0; }
void port_tlb_write(uint32_t pm, uint32_t hi, uint32_t lo0, uint32_t lo1) { (void)pm; (void)hi; (void)lo0; (void)lo1; }
void port_tlb_set(unsigned i, uint32_t pm, uint32_t hi, uint32_t lo0, uint32_t lo1) { (void)i; (void)pm; (void)hi; (void)lo0; (void)lo1; }
uint32_t port_tlb_probe(uint32_t hi) { (void)hi; return 0x80000000u; }
int port_tlb_read(unsigned i, uint32_t *pm, uint32_t *hi, uint32_t *lo0, uint32_t *lo1) { (void)i; (void)pm; (void)hi; (void)lo0; (void)lo1; return 0; }
int port_tlb_invalid(uint32_t a) { (void)a; return 0; }
int port_tlb_fault(uint32_t e, uint32_t b, uint32_t c) { (void)e; (void)b; (void)c; return 0; }
uint32_t port_tlb_wired(int set, uint32_t v) { (void)set; (void)v; return 0; }
uint32_t w2c_env_0x5F_builtin_mips_read_count(struct w2c_env *e) { (void)e; return 0; }
static void put(uint32_t at, const uint32_t *w, int n) {
    for (int i = 0; i < n; i++) { uint32_t b = __builtin_bswap32(w[i]); memcpy(mem + at + 4 * i, &b, 4); }
}
int main(void) {
    mem = mmap(0, 0x80100000u, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON | MAP_NORESERVE, -1, 0);
    /* 1000: addiu a3,a3,-1   the first branch's target: runs once for each pass through the pair
       1004: addiu t1,t1,1    the loop body, the second branch's target
       1008: bnez a3,1000     100C: bnez a3,1004, in the first's delay slot
       1010: addiu t0,t0,1    reached only when neither is taken      1014: jr ra */
    uint32_t code[] = {0x24E7FFFF, 0x25290001, 0x14E0FFFD, 0x14E0FFFD, 0x25080001, 0x03E00008, 0};
    put(0x80001000u, code, 7);
    uint64_t r[32] = {0}; uint32_t f[32] = {0};
    r[7] = 3; r[31] = 0xFFFFFFF0u;
    port_mips_run(0x80001004u, r, f);
    printf("%d %d %d\n", (int)r[8], (int)r[9], (int)r[7]);
    return 0;
}
'''.replace("MIPS_C", str(mips)))
    subprocess.run([cc, "-O1", "-w", "-o", str(tmp_path / "t"), str(source)], check=True, capture_output=True)
    done = subprocess.run([str(tmp_path / "t")], capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    assert "isn't one it runs" not in done.stderr
    assert done.stdout.split() == ["1", "4", "0"]  # t0 once, the body four times, a3 counted down by the target


def test_recompiled_and_interpreted_stacks_are_in_kseg0_above_the_ram():
    """A game's loader told a RAM pointer from a ROM offset by its sign (`if ((s32)p >= 0)` means a cartridge address):
    recompiled C's stack at 0x14000000 made a local's address a ROM offset, so it DMA'd from it and recursed (a racing
    game's `osPiStartDma: 13A3AE48+4 is past the ROM's end`, no frames). Stacks sit where a console's do, just above
    the 8 MB of RAM, with the host's DMA ranges and the 32-bit chip's mapping following them."""
    from decompnet.port.wasm import recomp

    top = recomp.STACKS + 33 * recomp.STACK_SIZE  # port_recomp_thread: up to thread 31's, (31 + 2) stacks
    assert 0x80800000 <= recomp.STACKS and top <= 0x81100000
    interp = (KIT / "mips.c").read_text()
    at = int(re.search(r"#define MIPS_STACKS (0x[0-9A-Fa-f]+)u", interp).group(1), 16)
    mips_stack = int(re.search(r"#define MIPS_STACK (0x[0-9A-Fa-f]+)u", interp).group(1), 16)
    assert 0x80800000 <= at and at + 33 * mips_stack <= 0x82000000 and at >= top
    host = HOST.read_text()
    assert "addr >= 0x80800000u && addr < 0x82000000u" in host  # stack_end: DMAs into them are allowed
    mem32 = (KIT / "miyoo" / "mem32.c").read_text()
    assert f"{{0x{recomp.STACKS:08X}u," in mem32 and f"{{0x{at:08X}u," in mem32


def test_a_boot_stack_local_has_its_bus_address_as_its_physical_address():
    """A game's file reader keeps its DMA buffer in a local of the bootstrap wasm stack (the first 64 KiB). Masked as a
    KSEG address, osVirtualToPhysical gave the offset, which the raw PI transfer reads as console RAM, so the DMA
    overwrote the game's own low memory and the reader looped on headers for ever (found once the host answered the
    routine by name for a matched copy, #3083). The physical address is the stack's bus address, as the raw PI path
    expects (BOOT_STACK_BUS)."""
    host = HOST.read_text()
    body = re.search(r"u32 w2c_env_osVirtualToPhysical\(struct w2c_env \*e, u32 a\) \{(.*?)\n\}", host, re.S).group(1)
    assert "a >= 0x1000u && a < BOOT_STACK_SIZE" in body and "return BOOT_STACK_BUS + a;" in body
    assert "return a & 0x1FFFFFFF;" in body  # every other address, as before
    raw = re.search(r"case 0xA460000Cu:.*?post_event\(EV_PI\)", host, re.S).group(0)
    assert "bus >= BOOT_STACK_BUS" in raw  # the PI path that reads that address back to the stack


def test_the_host_ends_on_its_time_limit_and_lets_an_idle_boot_hand_over():
    """A first function that never returns but ends in an idle loop (osSetThreadPri(NULL, 0) over and over, or an empty
    for(;;) that becomes port_idle_forever) left the host spinning in the boot with no game thread ever running, and
    the retrace loop that checks the time limit needs the scheduler's lock: the host ran for an hour past its seconds.
    Many idle calls from the boot code now end the boot as a return would, and a watchdog thread ends the process at
    its time limit (plus a grace) even when no game code reaches a safe point."""
    text = HOST.read_text()
    assert "static void boot_idle(unsigned *calls" in text and "longjmp(boot_jmp, 1)" in text
    assert "if (!setjmp(boot_jmp)) boot_call();" in text
    # both idle shapes reach it: the osSetThreadPri loop and the rewritten empty loop
    assert 'boot_idle(&boot_calls, "osSetThreadPri(NULL)")' in text
    assert 'boot_idle(&idle_calls, "an empty loop")' in text
    # the watchdog is started before the boot runs, only when there is a limit
    assert text.index("pthread_create(&guard, NULL, watchdog, NULL)") < text.index("if (!setjmp(boot_jmp))")
    assert "watchdog_seconds = seconds;" in text and "_exit(0);" in text


def test_the_host_can_log_its_threads_messages_and_who_runs():
    """When none of a game's threads gets to draw, what they say to each other and which one has the CPU is the
    first thing to look at: PORT_MSGLOG prints each send and receive, and PORT_THREADS names the running thread."""
    text = HOST.read_text()
    assert 'static int msg_log(void)' in text and 'getenv("PORT_MSGLOG")' in text
    assert text.count("if (msg_log()) fprintf") == 2  # a send (put) and a receive start
    assert 'fprintf(stderr, "  running: %d\\n"' in text


def test_the_64dd_registers_read_as_open_bus_on_a_console_with_none(tmp_path):
    """A game's libleo reads the 64DD status register at 0x05000508 and returns "no 64DD" at once when the word is
    nonzero (a console with no 64DD leaves the bus floating: the low 16 bits of the address in both halves). The
    host answered 0, so a racing game built its disk manager and waited for its reply for ever, never drawing past
    its boot logo. The two helpers are compiled from host.c and checked, and both read paths use them."""
    text = HOST.read_text()
    helpers = re.search(r"static int dd_range\(uint32_t bus\).*?\n(static uint32_t open_bus\(uint32_t bus\).*?\n)", text, re.S)
    assert helpers, "dd_range and open_bus are in host.c"
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if not cc:
        pytest.skip("no C compiler")
    source = ("#include <stdint.h>\n#include <stdio.h>\n" + helpers.group(0) +
              "int main(void) {\n"
              "  printf(\"%d %d %d %d\\n\", dd_range(0x05000508u), dd_range(0xA5000508u), dd_range(0x04FFFFFCu), dd_range(0x08000000u));\n"
              "  printf(\"%08X %08X\\n\", open_bus(0x05000508u), open_bus(0x06001234u));\n"
              "  return 0;\n}\n")
    (tmp_path / "t.c").write_text(source)
    subprocess.run([cc, "-o", str(tmp_path / "t"), str(tmp_path / "t.c")], check=True, capture_output=True, text=True)
    done = subprocess.run([str(tmp_path / "t")], check=True, capture_output=True, text=True)
    assert done.stdout.split("\n")[:2] == ["1 1 0 0", "05080508 12341234"]
    # both ways the game reaches it: osEPiReadIo and a direct read of the uncached alias
    assert "if (dd_range(dev)) { wbe32(data, open_bus(dev & ~3u)); return 0; }" in text
    assert "if (addr >= 0xA5000000u && addr < 0xA8000000u) { wbe32(IO_SCRATCH, open_bus((uint32_t)addr & ~3u))" in text


def test_virtual_time_advances_a_retrace_at_a_time_and_reads_move_it_a_microsecond(tmp_path):
    """PORT_VTIME: the clock the game reads (osGetTime, the count register, the audio interface) is the retrace count's,
    not the wall clock's, so a run is the same on a loaded machine; reads within one retrace still advance it, by a
    microsecond each and never into the next retrace, so a loop waiting for the time to pass ends."""
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if not cc:
        pytest.skip("no C compiler")
    source = tmp_path / "t.c"
    source.write_text("""#include <stdint.h>
#include <stdio.h>
#include <time.h>
static int vtime = 1;
static uint64_t vclock_us = 1000000;
static unsigned vsub;
static int retrace_us(void) { return 20000; }
""" + _function(HOST.read_text(), "port_clock_seconds") + """
int main(void) {
    printf("%.6f %.6f\\n", port_clock_seconds(), port_clock_seconds());
    for (int i = 0; i < 50000; i++) port_clock_seconds();
    printf("%.6f\\n", port_clock_seconds());          /* the next retrace's start is 1.020000: never reached */
    vclock_us += 20000; vsub = 0;
    printf("%.6f\\n", port_clock_seconds());
    return 0;
}
""")
    subprocess.run([cc, "-o", str(tmp_path / "t"), str(source)], check=True, capture_output=True)
    out = subprocess.run([str(tmp_path / "t")], check=True, capture_output=True, text=True).stdout.split()
    assert out == ["1.000001", "1.000002", "1.019999", "1.020001"]


def test_the_rdp_command_status_reads_idle_so_a_game_polling_it_gets_on():
    """A game's own osDp routine writes 1 to DPC_STATUS (0xA410000C, clear XBUS DMEM DMA) and polls bit 0 until it
    drops; the register was plain memory that kept the bit, so the thread spun for ever (a higher-priority thread
    starved the rest: audio tasks stopped after a few seconds). The host draws a task when it starts, so the RDP
    always reads idle: only CBUF_READY, counters 0, and writes to the status are dropped."""
    text = HOST.read_text()
    assert "#define DPC_BASE 0xA4100000u" in text
    load = text[text.index("uint64_t port_io_load("):text.index("uint64_t port_io_store(")]
    assert "addr >= DPC_BASE + 0x0Cu && addr < DPC_BASE + 0x20u" in load
    assert "addr == DPC_BASE + 0x0Cu ? 0x80u : 0u" in load  # status: CBUF_READY alone; clock and busy counters 0
    store = text[text.index("uint64_t port_io_store("):text.index("/* ---------------------------------------------------------------- VI */")]
    assert "addr == DPC_BASE + 0x0Cu" in store  # the status write is a command, nothing to keep


def test_a_command_list_the_game_gives_the_rdp_is_drawn_and_raises_the_dp_event():
    """A scheduler that draws without an RSP task calls osDpSetNextBuffer: DPC_START, then DPC_END (0xA4100000/04).
    The registers were plain memory, so the list was never drawn and the DP event the scheduler waited for never came:
    a game's second list stalled it for ever (one graphics task, then nothing). Writing DPC_END now draws
    [current, end) with the software RDP and raises the DP interrupt at a full sync (command 0x29)."""
    text = HOST.read_text()
    store = text[text.index("uint64_t port_io_store("):text.index("/* ---------------------------------------------------------------- VI */")]
    assert "addr == DPC_BASE) { dpc_start" in store and "addr == DPC_BASE + 4u) { dpc_end" in store and "dpc_run();" in store
    load = text[text.index("uint64_t port_io_load("):text.index("uint64_t port_io_store(")]
    assert "addr >= DPC_BASE && addr < DPC_BASE + 0x0Cu" in load  # start, end and current read back
    run = text[text.index("static void dpc_run(void)"):text.index("static int dd_range(")]
    assert "port_render_rdp_list(cmds, bytes)" in run and "if (sync) rcp_raise(EV_DP)" in run
    assert "if (id == 0x29) sync = 1;" in run
    render = (HOST.parent / "render.c").read_text()
    assert "void port_render_rdp_list(const uint8_t *cmds, size_t bytes)" in render
    assert "port_rdp_replay(cmds, bytes, NULL, 0)" in render


def test_a_first_function_that_loops_in_the_games_own_code_after_starting_threads_is_the_idle_thread():
    """A boot function that starts the game's threads and then loops around a call to its own code (not the two idle
    shapes boot_idle knew) never returned, so the host never gave its threads the CPU: a game's retraces did not
    even tick. The interpreter's periodic safe point reports it, and a thousand of them (65 million instructions)
    with threads started end the boot as a return would."""
    host = HOST.read_text()
    assert "void port_boot_spin(void)" in host
    body = host[host.index("void port_boot_spin(void)"):]
    body = body[:body.index("}\n")]
    assert "!self && !booted && thread_count > 0" in body and "boot_idle(" in body
    mips = (HOST.parent / "mips.c").read_text()
    assert "port_boot_spin();" in mips[mips.index("still running, at"):]


def test_a_serial_interface_transfer_ends_from_the_hosts_tick_not_inside_the_call():
    """A racing game's controller thread (priority 90) sent itself the SI message in the same instant and received it at
    once, so it never blocked and starved every lower-priority thread (one posted the queue it polled): the SI event
    is held and raised from the tick like the RSP's, for a raw SI DMA."""
    text = HOST.read_text()
    assert "post_event(EV_SI)" not in _function(text, "w2c_env_0x5F_osSiRawStartDma")
    assert "rcp_raise(EV_SI)" in _function(text, "w2c_env_0x5F_osSiRawStartDma")
    # a controller read through the host's own osContStartReadData posts at once (a startup that blocks there ran out
    # of order), and with no SI event registered the game waits on the queue it gave the call
    assert "post_event(EV_SI)" in _function(text, "si_done") and "put(mq, 0, 0, 0)" in _function(text, "si_done")




def test_the_host_answers_the_controller_pak_and_accessory_api_of_a_console_with_none():
    """osContSetCh, osPfsInit and the other pak calls were reported "not implemented, skipped" (their C left out as
    the host's while the host defined none): every one is defined, with the shape libultra.sigs gives its callers."""
    text, sigs = HOST.read_text(), HOST.with_name("libultra.sigs").read_text().splitlines()
    names = ["osContSetCh", "osContReset", "osPfsInit", "osPfsReFormat", "osPfsChecker", "osPfsFileState",
             "osPfsFindFile", "osPfsAllocateFile", "osPfsDeleteFile", "osPfsReadWriteFile", "osPfsGetLabel",
             "osPfsSetLabel", "osGbpakInit", "osGbpakReadWrite", "osVoiceInit", "osVoiceControlGain", "osSpTaskYield"]
    for name in names:
        assert re.search(rf"\bw2c_env_{name}\(struct w2c_env \*e", text), name
        assert any(row.split("\t")[0] == name for row in sigs), name
    assert re.search(r"\bw2c_env_0x5F_osPfsGetStatus\(", text) and re.search(r"\bw2c_env_0x5F_osSetWatchLo\(", text)
    # a pak function with no pak says PFS_ERR_NOPACK (1), the accessories PFS_ERR_DEVICE (11)
    assert "return 1;" in _function(text, "w2c_env_osPfsInit") and "return 11;" in _function(text, "w2c_env_osGbpakInit")
    for row in sigs:  # the sigs' parameter count is the C's
        name, _, params = row.split("\t")
        if name in ("osPfsFindFile", "osPfsAllocateFile", "osPfsReadWriteFile"):
            count = len([p for p in params.split(",") if p.strip()])
            assert len(re.search(rf"w2c_env_{name}\(struct w2c_env \*e, ([^)]*)\)", text)[1].split(",")) == count, name


def test_a_tasks_sp_and_dp_interrupts_arrive_after_the_call_that_started_it():
    """A fighting game's scheduler has a thread for the SP interrupt (priority 126) and one for DP (125). The host posted
    both inside osSpTaskStartGo, so the SP thread ran before the caller had recorded the task as the RSP's and the DP
    thread found no RDP task: 'NULL RDP Task!' on screen, the DP thread parked, two graphics tasks in all. On the
    console the RSP takes far longer than the call, so the interrupts come after it returns: held back, SP then DP in
    order, and raised from the host's tick, when every thread waits in virtual time, and at the retrace at the latest."""
    text = HOST.read_text()
    start = text.index("void w2c_env_osSpTaskStartGo(")
    body = text[start:text.index("u32 w2c_env_osSpTaskYielded")]
    assert "post_event(EV_SP)" not in body and "post_event(EV_DP)" not in body
    assert body.index("rcp_raise(EV_SP)") < body.index("rcp_raise(EV_DP)")  # in that order
    # the held events are delivered from the tick, when the CPU is idle in virtual time, and by the retrace
    assert "        dp_deliver(0);\n        port_audio_interrupts();" in text
    assert "if (none) dp_deliver(1);" in text
    retrace = text[text.index("static void retrace(void) {"):]
    assert retrace[:retrace.index("vi_count++")].count("dp_deliver(1)") == 1
    # the web build, which has no tick, raises them at once as before
    web = text[text.index("static void rcp_raise(int event) {"):text.index("static void dp_deliver(int force) {")]
    assert "#ifdef PORT_WEB\n    post_event(event);" in web


def test_the_rt64_frame_readback_writes_pngs_a_reader_decodes(tmp_path):
    """PORT_RT64_FRAMES saves what RT64 presented (the window can't be captured from outside): the adapter's PNG
    writer (rt64/png_dump.h, standard library only) turns BGRA pixels into 8-bit RGB, and the adapter wires the
    variable to RT64's draw hook."""
    import struct
    import zlib

    cxx = shutil.which("c++") or shutil.which("clang++") or shutil.which("g++")
    if not cxx:
        pytest.skip("no C++ compiler")
    folder = Path(__file__).resolve().parents[1] / "src/decompnet/consoles/n64/port_wasm/rt64"
    source = tmp_path / "png.cpp"
    source.write_text('#include "png_dump.h"\n#include <cstdint>\n'
                      "int main(int, char **argv) {\n"
                      "  uint8_t px[2 * 2 * 4] = {10, 20, 30, 255,   40, 50, 60, 255,   70, 80, 90, 255,   0, 0, 255, 255};\n"
                      "  return png_dump::write(argv[1], px, 2, 2, 8) ? 0 : 1;\n}\n")
    program = tmp_path / "png"
    built = subprocess.run([cxx, "-std=c++17", f"-I{folder}", str(source), "-o", str(program)],
                           capture_output=True, text=True, timeout=120)
    assert built.returncode == 0, built.stderr
    out = tmp_path / "frame.png"
    subprocess.run([str(program), str(out)], check=True, timeout=30)
    data = out.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat, size = 8, b"", None
    while pos < len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        assert struct.unpack(">I", data[pos + 8 + length:pos + 12 + length])[0] == zlib.crc32(kind + body)
        if kind == b"IHDR":
            size = struct.unpack(">II", body[:8]) + tuple(body[8:10])
        if kind == b"IDAT":
            idat += body
        pos += 12 + length
    assert size == (2, 2, 8, 2)  # 2x2, 8 bits, RGB
    raw = zlib.decompress(idat)
    # a filter byte per row, then R G B from the BGRA input
    assert raw == bytes([0, 30, 20, 10, 60, 50, 40, 0, 90, 80, 70, 255, 0, 0])
    adapter = (folder / "adapter.cpp").read_text()
    assert "PORT_RT64_FRAMES" in adapter and "SetRenderHooks" in adapter and "setFramebufferOnly" in adapter


def test_the_rt64_adapter_leaves_a_microcode_rt64_does_not_know_to_the_hosts_rdp():
    """A microcode outside RT64's database (a game's own Fast3D build, a second microcode some tasks switch to) gives
    no GBI or one with no commands, and RT64's display-list loop then ran on past the list until the host faulted or
    hung: Glover, Frogger 2, Flying Dragon and F-Zero X stopped a game thread on a bogus 'out-of-bounds' trap and
    drew nothing. The task is skipped before RT64 walks it, and RT64's view of RDRAM spans what its 24-bit address
    mask reaches, with a zero-filled reservation behind it."""
    folder = Path(__file__).resolve().parents[1] / "src/decompnet/consoles/n64/port_wasm/rt64"
    adapter = (folder / "adapter.cpp").read_text()
    task = adapter[adapter.index("void task(const uint8_t *rdram"):adapter.index("void features(")]
    guard = task.index("RT64::GBIUCode::Unknown")
    assert guard < task.index("processDisplayLists(") and "return;" in task[guard:task.index("processDisplayLists(")]
    assert "constexpr uint32_t snapshot_size = 0x1000000;" in adapter  # RT64's RDP_ADDRESS_MASK is 0xFFFFFF
    assert "MAP_NORESERVE" in adapter and "__builtin_bswap32" in adapter


def test_the_console_reports_the_expansion_pak_unless_the_project_says_otherwise(tmp_path):
    """A game that needs the Expansion Pak checks osMemSize and shows 'N64 Expansion Pak not installed' on 4 MB (a
    platform game then never ran its first frame); the host maps 8 MB for every game, so 8 MB is the default, and
    PORT_MEMSIZE (Play passes the project's rom.memory_size) gives a 4 MB game the console it was built for."""
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if not cc:
        pytest.skip("no C compiler")
    text = HOST.read_text()
    helper = re.search(r"static uint32_t console_memory\(void\) \{.*?\n\}\n", text, re.S)
    assert helper, "console_memory is in host.c"
    source = ("#include <stdint.h>\n#include <stdio.h>\n#include <stdlib.h>\n" + helper.group(0) +
              "int main(void) { printf(\"%X\\n\", console_memory()); return 0; }\n")
    (tmp_path / "t.c").write_text(source)
    subprocess.run([cc, "-o", str(tmp_path / "t"), str(tmp_path / "t.c")], check=True, capture_output=True, text=True)

    def run(value):
        env = {k: v for k, v in os.environ.items() if k != "PORT_MEMSIZE"}
        if value is not None:
            env["PORT_MEMSIZE"] = value
        return subprocess.run([str(tmp_path / "t")], check=True, capture_output=True, text=True, env=env).stdout.strip()
    assert [run(None), run("8"), run("4"), run("0x400000"), run("garbage")] == ["800000", "800000", "400000", "400000", "800000"]
    assert "wbe32(0x80000318, console_memory());" in text
    # the boot chip's RAM test leaves the same total at 0x800003F0 too (a real console's RAM word at that address
    # matched the reference emulator's dump; the port left it 0)
    assert "wbe32(0x800003F0, console_memory());" in text


def test_the_host_includes_setjmp_only_outside_the_browser_build():
    """The browser (wasm32-wasi) host can't include <setjmp.h> (the toolchain's header refuses without exception-handling
    support), so every browser export failed to build once the boot hand-over (boot_idle) added it unguarded."""
    text = HOST.read_text()
    include = text.index("#include <setjmp.h>")
    assert text[:include].rstrip().endswith("#ifndef PORT_WEB")
    assert "setjmp(" not in text[:text.index("#ifndef PORT_WEB")]  # (and nothing uses it before the guarded boot code)


def test_the_hosts_string_functions_do_what_the_c_library_does(tmp_path):
    """The kit leaves the game's own strcmp, strncmp, strcpy... out of the build as the host's, so a host without them
    stubbed them to nothing (every strncmp said "equal"): each answers as the C library does, on the game's memory."""
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if not cc:
        pytest.skip("no C compiler")
    names = ["memmove", "memcmp", "strcmp", "strncmp", "strcpy", "strncpy", "strcat", "strchr"]
    source = tmp_path / "t.c"
    source.write_text("""#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef uint32_t u32;
struct w2c_env { int unused; };
static uint8_t ram[0x1000], *M;
""" + "\n".join(_function(HOST.read_text(), f"w2c_env_{n}") for n in names) + """
int main(void) {
    M = ram;
    strcpy((char *)M + 0x10, "alpha"); strcpy((char *)M + 0x20, "alpine");
    printf("%d %d %d\\n", (int32_t)w2c_env_strcmp(0, 0x10, 0x20) < 0, (int32_t)w2c_env_strncmp(0, 0x10, 0x20, 3), (int32_t)w2c_env_memcmp(0, 0x20, 0x10, 4) > 0);
    w2c_env_strcpy(0, 0x40, 0x10); w2c_env_strcat(0, 0x40, 0x20);
    printf("%s %u %u\\n", (char *)M + 0x40, w2c_env_strchr(0, 0x40, 'p'), w2c_env_strchr(0, 0x40, 'z'));
    w2c_env_strncpy(0, 0x80, 0x10, 3); w2c_env_memmove(0, 0x82, 0x80, 3);
    printf("%.5s\\n", (char *)M + 0x80);
    return 0;
}
""")
    subprocess.run([cc, "-o", str(tmp_path / "t"), str(source)], check=True, capture_output=True)
    out = subprocess.run([str(tmp_path / "t")], check=True, capture_output=True, text=True).stdout.splitlines()
    assert out == ["1 0 1", "alphaalpine 66 0", "alalp"]


def test_the_consoles_retrace_per_task_is_read_by_task_number(tmp_path):
    """PORT_VTIME_TRACE: the emulator's EMU_TASK_LOG lines ("TASK VI") kept by task number, so the port's virtual time can
    reach each graphics task at the retrace the console did."""
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if not cc:
        pytest.skip("no C compiler")
    trace = tmp_path / "trace.txt"
    trace.write_text("1 0\n2 0\n3 1\n4 2\n5000 2299\n")
    source = tmp_path / "t.c"
    source.write_text("""#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
static unsigned *vtrace;
static unsigned vtrace_n;
""" + _function(HOST.read_text(), "vtrace_load") + """
int main(int argc, char **argv) {
    vtrace_load(argv[1]);
    printf("%u %u %u %u %u %u\\n", vtrace[3], vtrace[4], vtrace[5000], vtrace[4999], vtrace_n > 5000, vtrace[0]);
    return 0;
}
""")
    subprocess.run([cc, "-o", str(tmp_path / "t"), str(source)], check=True, capture_output=True)
    out = subprocess.run([str(tmp_path / "t"), str(trace)], check=True, capture_output=True, text=True).stdout.split()
    assert out == ["1", "2", "2299", "0", "1", "0"]


def test_the_interpreter_can_trace_chosen_addresses_and_say_where_it_is():
    """PORT_PC_TRACE=addr,... prints the registers each time the interpreter executes one of the addresses (a game's
    coroutine switch, its dispatcher), and PORT_SAMPLE prints the interpreter's pc: how a game's own control flow is
    followed when it can't be stepped."""
    mips = (Path(__file__).resolve().parents[1] / "src/decompnet/consoles/n64/port_wasm/mips.c").read_text()
    host = (Path(__file__).resolve().parents[1] / "src/decompnet/consoles/n64/port_wasm/host.c").read_text()
    assert "PORT_PC_TRACE" in mips and "pc_trace(&c, pc)" in mips and "port_interp_pc = pc" in mips
    assert "interpreter pc: %08X" in host


def test_the_compiled_c_reads_a_low_window_above_the_modules_data_as_ram(tmp_path):
    """A game maps a page pair at virtual 0x200000 (1 MB pages onto physical 0 and 0x100000) and keeps a class table at
    virtual 0x43CAE0 in another window: its compiled C read the raw wasm address and got zeros, so a method slot read
    as null and the call died ('called 00000000'). The loads and stores ask the window table for a window above the
    module's own data (port_wasm_floor, 1 MB: the stack and the data sit below it), and get the RAM it maps."""
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if not cc:
        pytest.skip("no C compiler")
    text = HOST.read_text()
    code = text[text.index("static struct { uint32_t entry, target, bss, bss_size; int tv; } boot;"):
                text.index("/* the console's RAM as the boot leaves it")]
    source = tmp_path / "t.c"
    source.write_text(_BOOT_HARNESS + code + """
int main(void) {
    port_tlb_write(0x1FE000, 0x200000, 0x1F, 0x401F); /* 1 MB pages, valid: physical 0 and 0x100000 */
    printf("%u %08X %08X %08X %08X %08X\\n", port_tlb_hi, port_tlb_wasm(0x200010), port_tlb_wasm(0x300010),
           port_tlb_wasm(0x0FFFF0), port_tlb_wasm(0x400000), port_tlb_wasm(0x9000C));
    return 0;
}
""")
    subprocess.run([cc, "-o", str(tmp_path / "t"), str(source)], check=True, capture_output=True)
    done = subprocess.run([str(tmp_path / "t")], capture_output=True, text=True, check=True)
    # the window's two halves at their spelling (the bytes are copied there), nothing below the floor or past the windows
    assert done.stdout.split() == ["1", "80200010", "80300010", "000FFFF0", "00400000", "0009000C"]


def test_seeds_the_console_used_are_replayed_in_order_then_the_game_s_own(tmp_path):
    """PORT_SEED_REPLAY: a port-only override of a game's seeding function passes its seed through port_seed_replay, which gives the
    next number of the recorded file (the seeds the console's game was given) while there are any and the seed passed in after them."""
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if not cc:
        pytest.skip("no C compiler")
    source = tmp_path / "t.c"
    source.write_text("""#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
typedef uint32_t u32;
struct w2c_env { int unused; };
static unsigned long seed_calls; /* host.c's counter for PORT_SEED_LOG, defined before the function */
""" + _function(HOST.read_text(), "w2c_env_port_seed_replay") + """
int main(void) {
    printf("%u %u %u %u\\n", w2c_env_port_seed_replay(0, 7), w2c_env_port_seed_replay(0, 8), w2c_env_port_seed_replay(0, 9),
           w2c_env_port_seed_replay(0, 10));
    return 0;
}
""")
    subprocess.run([cc, "-o", str(tmp_path / "t"), str(source)], check=True, capture_output=True)
    seeds = tmp_path / "seeds.txt"
    seeds.write_text("4000000000\n123\n")
    env = {**os.environ, "PORT_SEED_REPLAY": str(seeds)}
    assert subprocess.run([str(tmp_path / "t")], env=env, check=True, capture_output=True, text=True).stdout.split() == [
        "4000000000", "123", "9", "10"]
    env.pop("PORT_SEED_REPLAY")
    assert subprocess.run([str(tmp_path / "t")], env=env, check=True, capture_output=True, text=True).stdout.split() == [
        "7", "8", "9", "10"]


def test_the_rdp_start_and_end_registers_read_back_the_word_a_game_wrote():
    """A game's startup writes 0x12345678 to DPC_START and reads it back: a register self-test. The registers keep
    only 24 bits when the RDP runs a list, but a read gave the masked word, so the test failed and the game stopped
    after its first graphics task (a military shooter: 2 graphics tasks, then hundreds without the fix). The words
    are kept as written for the readback; the masked copies run the list."""
    text = HOST.read_text()
    load = text[text.index("uint64_t port_io_load("):text.index("uint64_t port_io_store(")]
    assert "addr == DPC_BASE ? dpc_start_word : addr == DPC_BASE + 4u ? dpc_end_word : dpc_cur" in load
    store = text[text.index("uint64_t port_io_store("):text.index("/* ---------------------------------------------------------------- VI */")]
    assert "dpc_start_word = (uint32_t)value;" in store and "dpc_end_word = (uint32_t)value;" in store
    assert "dpc_start = (uint32_t)value & 0x00FFFFF8u;" in store  # the list still runs from the masked address


def test_the_games_window_raises_itself_when_it_opens():
    """The game program isn't an app bundle, so its window opened behind the app that started it (and a capture or
    a player saw the app, not the game): the window code asks SDL to raise it, forcing the raise on macOS."""
    text = (KIT / "window" / "sdl.c").read_text()
    assert "SDL_FORCE_RAISEWINDOW" in text and "SYM(RaiseWindow)" in text
    # raised right after each of the two places the window is created
    assert text.count("sdl.RaiseWindow(window)") >= 2


def test_a_game_that_stops_on_a_call_to_nothing_still_writes_the_memory_dump_it_was_asked_for():
    """PORT_DUMPBIN is how a port's memory is set beside the reference emulator's. It was written only when the run
    ended in its time limit, so a game that died (port_trace) left no dump to look at."""
    text = HOST.read_text(encoding="utf-8")
    assert 'getenv("PORT_DUMPBIN")' in _function(text, "dump_memory_if_asked")
    assert "dump_memory_if_asked();" in _function(text, "main")
    unix_trace = text[text.index("void port_trace(void) {\n    dump_memory_if_asked();"):]
    assert unix_trace.startswith("void port_trace(void) {\n    dump_memory_if_asked();")

def test_a_game_that_reads_the_cartridge_through_the_pi_registers_is_counted():
    """A game's own PI manager starts its transfers by writing the PI registers: the summary said '0 DMAs' of one that
    read its file table that way, which looked like a game that never loaded anything. They are counted and logged
    (PORT_DMA_LOG) as osPiStartDma's are, and a transfer the host could not do says so."""
    text = HOST.read_text()
    start = text.index("case 0xA460000Cu:")
    body = text[start:start + 2500]
    assert "dmas++;" in body and "NOT DONE" in body
