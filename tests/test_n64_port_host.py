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
    (TLB_MAP_ENTRY, "00200000 100000 00000000\n00300000 100000 00100000"),
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
        "10000000 400000 00000000", "10400000 400000 00400000", "80001050 80400010 20000000 80001050",
        "1 800010F8 80400010 00100000 20000000", "2"]


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
#define DPC_BASE 0xA4100000u
#define IO_SCRATCH 0x000A0000u
#define IO_FAR(a) (a)
static uint8_t M[0xA0010], rom[0x1000]; static size_t rom_size = sizeof rom;
static uint32_t cart_io_last; static int cart_io_busy;
static void wbe32(uint32_t a, uint32_t v) { M[a] = v >> 24; M[a + 1] = v >> 16; M[a + 2] = v >> 8; M[a + 3] = v; }
static int port_ai_register(uint32_t a, int w, uint32_t v, uint32_t *r) { (void)a; (void)w; (void)v; (void)r; return 0; }
static int si_register(uint32_t a, int w, uint32_t v, uint32_t *r) { (void)a; (void)w; (void)v; (void)r; return 0; }
static int pi_register(uint32_t a, int w, uint32_t v, uint32_t *r) { (void)a; (void)w; (void)v; (void)r; return 0; }
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
""" + _function(HOST.read_text(), "wbe32") + "\n" + _function(HOST.read_text(), "stack_end") + "\n"
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
enum { EV_PI = 8 };
static void post_event(int event) { assert(event == EV_PI); interrupts++; }
#define SHADOW_STACKS 0x10000000u
#define SHADOW_SIZE 0x100000u
#define BOOT_STACK_SIZE 0x10000u
#define BOOT_STACK_BUS 0x15000000u
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
    for name in ("sqrtf", "sinf", "cosf", "ldexpf"):
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
void port_tlb_write(uint32_t pm, uint32_t hi, uint32_t lo0, uint32_t lo1) { (void)pm; (void)hi; (void)lo0; (void)lo1; }
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
