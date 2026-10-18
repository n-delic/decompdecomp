"""PC (Windows) from scratch: a project generated for the user's .exe from Ghidra's analysis, worked on like a fork.

Demo Quest PC (projects/demo-pc) is built like a PC game of its time: Visual C++ 6.0 /O2, linked at 0x400000 without
relocations, with __stdcall and __fastcall functions, a jump table and a hand-written assembly helper. Importing it
must find every function with its exact extent, and agents' C must be judged by the project's own build and objdiff.
Visual C++ is proprietary, so the end-to-end part runs only where `msvc` was installed with its license accepted.
"""

import re
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from decompnet import toolchains
from decompnet.consoles.pc import scratch as scratch_pe
from decompnet.forks import csource, ghidra

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "projects" / "demo-pc"
EXE, MAP = DEMO / "build" / "demoquest.exe", DEMO / "build" / "demoquest.map"
GAME = ROOT / "projects" / "demo-n64" / "original" / "game.c"

needs_tools = pytest.mark.skipif(
    any(toolchains.installed(t) is None for t in ("msvc", "objdiff-cli")) or not ghidra.available()
    or (toolchains.get("wibo").supported and toolchains.installed("wibo") is None),
    reason="needs Visual C++ (msvc, proprietary), Ghidra and objdiff")


def test_decorated_names():
    assert scratch_pe.decorate("func_00401000", "__cdecl", 0, 2) == "_func_00401000"
    assert scratch_pe.decorate("func_00401000", "__stdcall", 0, 0) == "_func_00401000"  # Ghidra's default: nothing popped
    assert scratch_pe.decorate("func_00401000", "__stdcall", 8, 2) == "_func_00401000@8"
    assert scratch_pe.decorate("func_00401000", "__fastcall", 0, 2) == "@func_00401000@8"
    assert scratch_pe.decorate("func_00401000", "__fastcall", 4, 3) == "@func_00401000@12"
    assert scratch_pe.decorate("func_00401000", "__thiscall", 4, 2) == "@func_00401000@12"  # this, unused edx, one int


def test_only_32_bit_x86_programs():
    assert not scratch_pe.is_pe(b"MZ" + bytes(0x3E))
    head = bytearray(0x200)
    head[:2], head[0x3C:0x40], head[0x80:0x84] = b"MZ", struct.pack("<I", 0x80), b"PE\0\0"
    struct.pack_into("<H", head, 0x84, 0x14C)
    struct.pack_into("<H", head, 0x98, 0x10B)
    assert scratch_pe.is_pe(bytes(head))
    struct.pack_into("<H", head, 0x84, 0x8664)  # x64
    assert not scratch_pe.is_pe(bytes(head))


def _map() -> dict[str, int]:
    """Symbol -> address of every function and variable, from the linker's map of the demo."""
    return {m.group(1): int(m.group(2), 16)
            for m in re.finditer(r"^\s+\S+\s+(\S+)\s+([0-9a-f]{8}) ", MAP.read_text(), re.M)}


@pytest.fixture(scope="module")
def pc(tmp_path_factory):
    from decompnet.app.engine import RomNetwork

    if not EXE.exists():
        subprocess.run([sys.executable, str(DEMO / "build.py")], check=True)
    work = tmp_path_factory.mktemp("pc")
    net = RomNetwork(work / "app.db", work)
    slug = net.import_rom(EXE, confirmed=True, log=lambda *_: None)
    return net, slug


@needs_tools
def test_every_function_is_found_with_its_extent(pc):
    net, slug = pc
    ov = net.overview(slug)
    assert ov["kind"] == "fork" and ov["origin"] == "scratch" and ov["platform"] == "pc" and ov["ready"]
    syms = {a: s for s, a in _map().items() if not s.startswith("??") and a < 0x402000}
    rows = net.store.all("SELECT name, size FROM functions WHERE project_id=?", net.project_row(slug)["id"])
    assert len(rows) == len(syms) == 19  # clamp_s32, which nothing calls, too: Ghidra misses it, the gap doesn't
    for name, size in ((r["name"], r["size"]) for r in rows):
        addr = int(re.search(r"func_([0-9A-F]{8})", name).group(1), 16)
        assert addr in syms
        assert name.startswith("@") == syms[addr].startswith("@") and name.endswith("@8") == syms[addr].endswith("@8")
    root = Path(net.project_row(slug)["root"])
    files = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True).stdout.split()
    assert files and not [f for f in files if f.startswith(("orig/", "build/"))]
    assert "config/demoquest/functions.json" in files and any(f.startswith("src/auto/") and f.endswith(".cpp") for f in files)


@needs_tools
def test_agents_match_functions(pc):
    net, slug = pc
    syms = _map()
    agent, _ = net.local_agent("pc-agent")
    header = (GAME.parent / "game.h").read_text().split("#define NULL ((void *)0)", 1)[1].rsplit("#endif", 1)[0]
    rename = {re.sub(r"^[_@]|@\d+$", "", s): (f"func_{a:08X}" if a < 0x402000 else f"D_{a:08X}")
              for s, a in syms.items() if not s.startswith("??") and s != "__fltused"}
    pattern = re.compile(r"\b(" + "|".join(sorted(rename, key=len, reverse=True)) + r")\b")
    sub = lambda text: pattern.sub(lambda m: rename[m.group(1)], text)
    header += "extern s32 g_settings[6];\n"
    assert net.forks.edit_file(agent, slug, "include/common.h", "", sub(header), "the game's types").startswith("✅")
    source = GAME.read_text() + (DEMO / "pcmain.c").read_text()
    for name in ("clamp_s32", "player_take_damage", "entity_default_hp", "settings_get", "mix_score"):
        decorated = next(s for s in syms if re.sub(r"^[_@]|@\d+$", "", s) == name)
        res = net.attempt(agent, decorated.replace(name, rename[name]), sub(csource.get(source, name)), slug)
        assert res["matched"], res["text"]
    # C++: a class's method, whatever MSVC mangles its name to, pairs with the original's function
    abs_name = rename["abs_s32"]
    assert net.forks.edit_file(agent, slug, "include/common.h", "", f"struct Num {{ static s32 {abs_name}(s32 v); }};\n",
                               "a class").startswith("✅")
    res = net.attempt(agent, f"_{abs_name}", f"s32 Num::{abs_name}(s32 v) {{\n    return v < 0 ? -v : v;\n}}\n", slug)
    assert res["matched"], res["text"]
    other = f"_func_{syms['_entity_points']:08X}"
    wrong = net.attempt(agent, other, sub(csource.get(source, "entity_points")).replace("return 75;", "return 76;"),
                        slug)
    assert not wrong["matched"]
    net.release(agent, other, "", slug)
    root = Path(net.project_row(slug)["root"])
    assert "Match " in subprocess.run(["git", "log", "--oneline"], cwd=root, capture_output=True, text=True).stdout
