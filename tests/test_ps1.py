"""PlayStation (consoles/ps1/): its program and discs, what's set aside as assembly, and Demo Quest PS1
(projects/demo-ps1) decompiled whole.

The demo is the Demo Quest game built the way PSY-Q-era games were: GCC 2.7.2 at -O2 -G8, its assembly through
maspsx (Sony's ASPSX) and GNU as. The program carries no symbols, so the last test does what an agent does: name the
addresses, declare the game's types, then write each function. Every one of them must match byte for byte.
"""

import json
import re
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from decompnet import consoles, toolchains
from decompnet.consoles.ps1 import exe
from decompnet.rom import RomError

from .test_overlays import _iso, _overlay

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "projects" / "demo-ps1"
GAME = ROOT / "projects" / "demo-n64" / "original"
PS1 = consoles.get("ps")
LOAD = 0x80010000
OVERLAY_AT = 0x80100000  # above the program, where a game would load one


def _exe(body: bytes = bytes(4096), load: int = LOAD, marker: bytes = b"Sony Computer Entertainment Inc. for Europe area") -> bytes:
    header = bytearray(exe.HEADER)
    header[:8] = exe.MAGIC
    struct.pack_into("<4I", header, exe.HEADER_FIELDS, load, 0, load, len(body))
    header[0x4C:0x4C + len(marker)] = marker
    return bytes(header) + body


def test_a_program_says_where_it_loads_and_its_disc_names_it():
    program, info = exe.read_exe(_exe() + b"padding after the program", "SLES_123.45")
    assert len(program) == exe.HEADER + 4096 and info["load"] == LOAD
    assert (info["title"], info["region"]) == ("SLES-12345", "Europe")
    assert exe.read_exe(_exe())[1]["region"] == "Europe"  # no name: the header's own words
    with pytest.raises(RomError, match="header"):
        exe.read_exe(_exe(load=0x00100000))  # not in its RAM: a PS2 address


def test_a_ps1_disc_is_the_ps1s_and_a_ps2_disc_the_ps2s(tmp_path):
    ps1 = _iso({"SYSTEM.CNF": b"BOOT = cdrom:\\SLUS_000.01;1\r\nTCB = 4\r\n", "SLUS_000.01": _exe()})
    ps2 = _iso({"SYSTEM.CNF": b"BOOT2 = cdrom0:\\SLUS_200.00;1\r\n", "SLUS_200.00": b"\x7fELF"})
    no_cnf = _iso({"PSX.EXE": _exe()})
    assert consoles.identify(ps1) is PS1 and consoles.identify(no_cnf) is PS1
    assert consoles.identify(ps2) is consoles.get("ps2")
    (tmp_path / "game.bin").write_bytes(ps1)  # read in place, as a disc of 700 MB is
    assert consoles.identify(tmp_path / "game.bin") is PS1
    picked = PS1.read_game(tmp_path / "game.bin")
    assert picked.info["game_code"] == "SLUS-00001" and picked.data[:8] == exe.MAGIC
    assert consoles.identify(_exe()) is PS1


def test_an_overlay_on_the_disc_is_placed_in_the_ps1s_ram():
    disc = _iso({"SYSTEM.CNF": b"BOOT = cdrom:\\MAIN.EXE;1\r\n", "MAIN.EXE": _exe(),
                 "BATTLE.BIN": bytes(64) + _overlay(OVERLAY_AT + 0x40, 40), "MUSIC.VAB": bytes(range(256)) * 32})
    picked = PS1.read_game(disc)
    assert [(name, at) for name, _, at, _ in picked.overlays] == [("battle", OVERLAY_AT)]


def test_a_loader_on_the_disc_leads_to_the_program_it_loads():
    """A disc can boot a loader (a logo, then `cdrom:\\GAME\\MAIN.EXE`): its own code is a few functions, and the
    game is the program it loads. That one is imported, named as the disc boots it."""
    loader = _exe(b"cdrom:\\GAME.EXE;1\0".ljust(4096, b"\0"))
    game = _exe(bytes(range(256)) * 64)
    disc = _iso({"SYSTEM.CNF": b"BOOT = cdrom:\\SLES_123.45;1\r\n", "SLES_123.45": loader, "GAME.EXE": game})
    picked = PS1.read_game(disc)
    assert picked.data == game and picked.info["program"] == "GAME.EXE"
    assert picked.info["game_code"] == "SLES-12345"
    plain = _iso({"SYSTEM.CNF": b"BOOT = cdrom:\\SLES_123.45;1\r\n", "SLES_123.45": game})
    assert PS1.read_game(plain).data == game and "program" not in PS1.read_game(plain).info


def test_the_code_is_found_where_its_returns_and_frames_are(tmp_path):
    """splat takes the first run of 33 varied instructions it can read for the start of the code: on a program that
    is mostly a picture (16-bit pixels read as loads) it put the code in the picture, and found one function. When
    its guess doesn't hold the entry point, .text is the clusters of returns, frames and BIOS stubs."""
    from decompnet.consoles.ps1 import _place_text

    words = lambda *ws: b"".join(w.to_bytes(4, "little") for w in ws)  # noqa: E731
    picture = words(0x80008000, 0x6F726463, 0x10101010) * 400  # pixels, and a stray frame-like word or none
    function = words(0x27BDFFE0, 0xAFBF0018, 0x0C000000, 0x00000000, 0x8FBF0018, 0x03E00008, 0x27BD0020)
    code = function * 8 + words(0x240A00A0, 0x01400008, 0x24090039)  # functions, then a BIOS stub
    body = picture + code + words(0x80060028, 0x80062AA8) * 8  # and a jump table
    program = bytearray(_exe(body))
    lo = exe.HEADER + len(picture)
    struct.pack_into("<I", program, exe.HEADER_FIELDS, LOAD + len(picture) + 4 * 7)  # the entry: the 2nd function
    assert exe.text_range(bytes(program)) == (lo, lo + len(code))
    config = tmp_path / "game.yaml"
    config.write_text("segments:\n- {name: header, type: header, start: 0}\n"
                      "- name: main\n  type: code\n  start: 0x800\n  vram: 0x80010000\n  subsegments:\n"
                      "  - [0x800, rodata, '800']\n  - [0x900, c, main]\n  - [0xA00, data, A00]\n"
                      f"- [{len(program):#x}]\n")
    _place_text(config, bytes(program), log=lambda *_: None)
    import yaml

    subs = yaml.safe_load(config.read_text())["segments"][1]["subsegments"]
    assert subs == [[0x800, "rodata", "800"], [lo, "c", "main"], [lo + len(code), "rodata", f"{lo + len(code):X}"]]
    _place_text(config, bytes(program), log=lambda *_: None)  # a guess that holds the entry point stays
    assert yaml.safe_load(config.read_text())["segments"][1]["subsegments"] == subs


def test_bios_calls_and_the_gte_are_set_aside_as_assembly():
    def code(*words):
        return struct.pack(f"<{len(words)}I", *words)

    puts = code(0x240A00B0, 0x01400008, 0x2409003F, 0)  # li t2, 0xB0; jr t2; li t1, 0x3F (puts)
    assert "BIOS" in PS1.asm_reason(puts)
    gte = code(0x4A180001, 0x03E00008, 0)  # RTPS; jr ra; nop
    assert "GTE" in PS1.asm_reason(gte)
    plain = code(0x00851021, 0x03E00008, 0)  # addu v0, a0, a1; jr ra; nop
    assert PS1.asm_reason(plain) is None


# ------------------------------------------------------------------------------------------------- the demo

needs_demo = pytest.mark.skipif(any(toolchains.installed(t) is None for t in PS1.demo_tools)
                                or not (ROOT / "projects" / "zlib-n64" / "original").exists(),
                                reason="needs gcc-2.7.2-psx, maspsx, binutils-mips and zlib to build the PS1 demo")
FUNCTIONS = ["clamp_s32", "abs_s32", "player_take_damage", "rng_next", "count_alive_entities", "entity_update_physics",
             "entity_default_hp", "entity_spawn", "add_score", "lerp", "checksum_save", "entity_collides", "game_tick"]
GLOBALS = ["g_player", "g_entities", "g_score", "g_frame_count", "g_rng_state"]
SOFT_FLOAT = ["__subsf3", "__mulsf3", "__addsf3"]


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    from decompnet.app.engine import RomNetwork

    key = DEMO / "build" / "demo.json"
    if not key.exists():
        subprocess.run([sys.executable, str(DEMO / "build.py")], check=True)
    built = json.loads(key.read_text())
    work = tmp_path_factory.mktemp("ps1")
    net = RomNetwork(work / "app.db", work)
    slug = net.import_rom(DEMO / "build" / built["game"], confirmed=True, log=lambda *_: None)
    return net, slug, built["symbols"]


@needs_demo
def test_the_demo_imports_with_gp_and_its_compiler(demo):
    net, slug, _ = demo
    ov = net.overview(slug)
    assert ov["platform"] == "ps" and ov["compiler"]["toolchain"] == "gcc-2.7.2-psx" and ov["functions"] > 60
    assert "-G8" in ov["compiler"]["flags"]
    assert "WITHOUT" in net.get_context(slug)  # the $gp-relative globals advice


@needs_demo
def test_every_game_function_matches(demo):
    from decompnet.forks import csource

    net, slug, syms = demo
    agent, _ = net.local_agent("ps1-agent")
    header = (GAME / "game.h").read_text()
    header = "\n".join(line for line in header.splitlines() if not line.startswith("#include")
                       and not re.match(r"typedef \w+( \w+)? [su]\d+;|typedef \w+ f\d+;", line))
    header = re.sub(r"^extern (s32|u32) (g_\w+);", r"\1 \2;", header, flags=re.M)  # small globals: no extern
    assert net.edit_context(agent, slug, "", header, "the game's types").startswith("✅")
    table = net.symbols(net.project_row(slug)["id"])
    for name in FUNCTIONS + GLOBALS + SOFT_FLOAT:
        current, offset = table.name_at(syms[name])
        if offset:  # inside a bigger data symbol (globals next to each other): name the address itself
            current = f"D_{syms[name]:08X}"
        if current != name:
            net.rename_symbol(agent, slug, current, name)
            table = net.symbols(net.project_row(slug)["id"])
    source = (GAME / "game.c").read_text()
    scores = {name: net.attempt(agent, name, csource.get(source, name), slug)["score"] for name in FUNCTIONS}
    assert all(score == 0 for score in scores.values()), scores
