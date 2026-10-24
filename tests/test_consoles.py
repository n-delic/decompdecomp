"""The connector (consoles/__init__.py) and every console that plugs into it.

First what holds for every console without any tool installed (CI checks it on every push): its facts are complete,
its compilers and packs are pinned toolchains, its guides exist, and the ids older projects stored still open them.
"""

import json
import struct
from pathlib import Path

import pytest

from decompnet import arch, consoles, toolchains
from decompnet.app import tools
from decompnet.app.engine import RomNetwork
from decompnet.app.twins import object_prints
from decompnet.rom import RomError

from .test_ps2_debug import (
    SHF_ALLOC,
    SHF_EXECINSTR,
    SHT_PROGBITS,
    SHT_STRTAB,
    SHT_SYMTAB,
    STB_GLOBAL,
    STT_FUNC,
    _elf,
    _symbols,
)

ROOT = Path(__file__).resolve().parent.parent
STARTABLE = consoles.startable()


def _guide_exists(topic: str) -> bool:
    return not tools.guide_text(topic).startswith("Guides (call guide")


@pytest.mark.parametrize("console", STARTABLE, ids=[c.id for c in STARTABLE])
def test_every_console_says_everything_the_app_asks(console):
    assert console.name and console.short and console.layout
    assert console.status in (consoles.RECOMMENDED, consoles.BETA) and console.mode in (consoles.ROM, consoles.PROJECT)
    assert arch.get(console.arch).id == console.arch, "its processor is one arch/ knows"
    assert console.compilers and console.default_compiler({}, "") in {c.id for c in console.compilers} | {""}
    if console.mode == consoles.ROM:  # each compiler is a pinned toolchain
        assert all(toolchains.get(c.id) and c.built for c in console.compilers)
        assert console.as_flags and console.image_ext and consoles.EXPORT in console.features
    for tool, use in console.packs:
        assert toolchains.get(tool) and use
    assert all(_guide_exists(t) for t in (*console.guides, console.compiler_guide) if t)
    assert not console.demo or (ROOT / "projects" / console.demo / "build.py").exists()
    low, high = console.ram
    assert low < high
    described = console.describe()
    assert described["id"] == console.id and described["judge"] == console.judge and described["compilers"]


def test_ids_are_unique_and_public_decomps_have_their_guides():
    ids = [key for c in consoles.CONSOLES for key in (c.id, *c.aliases)]
    assert len(ids) == len(set(ids))
    assert all(_guide_exists(t) for c in consoles.CONSOLES for t in (*c.guides, c.compiler_guide) if t)
    assert {c.id for c in consoles.CONSOLES if c.status == consoles.PUBLIC} >= {"gba"}  # forks only


def test_the_ids_older_projects_stored_still_open_them(tmp_path):
    """The owner's projects: a PC game started from scratch stored "win32" (decomp.dev's id), and ROM projects from
    before configs named their platform are N64 ones."""
    assert consoles.get("win32") is consoles.get("pc")
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    king = {"kind": "fork", "origin": "scratch", "platform": "win32", "version": "king", "ready": True}
    old_rom = {"kind": "rom", "compiler": {"toolchain": "gcc-2.7.2-kmc", "flags": []}, "rom": {}}
    with net.store.tx() as db:
        for slug, cfg in (("king", king), ("old", old_rom)):
            db.execute("INSERT INTO projects(slug, name, root, config) VALUES(?, ?, ?, ?)",
                       (slug, slug, str(tmp_path / slug), json.dumps(cfg)))
    row = net.summary("king")
    assert (row["platform"], row["platform_name"], row["platform_short"], row["layout"]) == \
        ("pc", "Windows PC", "PC", "Ghidra + objdiff")
    assert net.console(net.project_row("old")).id == "n64" and consoles.of(king) is consoles.get("pc")


def test_an_unknown_platform_is_a_console_with_only_its_name():
    other = consoles.get("dreamcast")
    assert other.name == "dreamcast" and not other.mode and not other.features and not other.m2c_target()
    with pytest.raises(RomError, match="public decomp"):
        other.import_game(None, b"")


def test_a_file_no_console_reads_says_which_ones_do():
    with pytest.raises(RomError, match="Nintendo 64.*PlayStation 2.*Windows PC"):
        consoles.identify(bytes(4096))


def test_a_psp_cso_is_not_taken_for_a_gamecube_disc():
    """Both start with CISO: a GameCube/Wii CISO goes on with its block size, a PSP .cso with its header's size."""
    from decompnet.consoles.gc_wii import scratch

    gc_ciso = b"CISO" + struct.pack("<I", 0x200000) + b"\x01\x01\x00" + bytes(0x100)
    psp_cso = b"CISO" + struct.pack("<IQIBB", 0x18, 0x4000000, 0x800, 1, 0) + bytes(0x100)
    assert scratch.disc_kind(gc_ciso) == "packed"
    assert scratch.disc_kind(psp_cso) is None
    assert consoles.identify(gc_ciso) is consoles.get("gc")


def test_mips_code_in_a_forks_object_isnt_judged_without_its_console():
    """A fork's objects don't say their console, and which registers carry arguments depends on it (the PS2's EABI
    passes them in t0-t3): a MIPS function there isn't set aside as hand-written, though its console would."""
    stub = struct.pack("<4I", 0x24030010, 0x0000000C, 0x03E00008, 0)  # li v1, 0x10; syscall; jr ra; nop
    symtab, strtab = _symbols([("stub", 0, len(stub), STB_GLOBAL, STT_FUNC, 1)])
    obj = _elf([(".text", SHT_PROGBITS, SHF_ALLOC | SHF_EXECINSTR, 0, stub, 0),
                (".symtab", SHT_SYMTAB, 0, 0, symtab, 3), (".strtab", SHT_STRTAB, 0, 0, strtab, 0)])
    assert object_prints(obj)["stub"].asm == ""
    assert "system call" in consoles.get("ps2").asm_reason(stub)


# ---------------------------------------------------------------------------------------------- conformance
# Then every console's whole path on its homemade demo game (projects/<demo>/): build it, tell its file is that
# console's, import it, judge a function's matching C (score 0) and wrong C (not 0), draft one, and rebuild the game
# byte for byte where the console can. A console's demo build writes build/demo.json: the game it made and every
# symbol's address, so the test knows which of the project's (unnamed) functions is which. It skips, saying what's
# missing, where a compiler or tool isn't installed: CI installs the free ones (N64's KMC GCC, the PS2's EE-GCC).

DEMO_GAME = ROOT / "projects" / "demo-n64" / "original" / "game.c"  # the Demo Quest game every demo is built from
ZLIB = ROOT / "projects" / "zlib-n64" / "original"  # downloaded by projects/zlib-n64/prepare.py
# needs nothing but s32, which every project's first header has (clamp_s32 before it runs into the N64's exit())
FUNCTION = "abs_s32"
WRONG = ("-v : v", "-v : v + 1")  # still compiles, other code
DEMOS = [c for c in STARTABLE if c.demo]


def _missing(console) -> list[str]:
    import shutil

    def there(tool: str) -> bool:
        try:
            return toolchains.installed(tool) is not None or not toolchains.get(tool).supported
        except toolchains.ToolchainError:  # a program on PATH, not a toolchain
            return shutil.which(tool) is not None
    return [t for t in console.demo_tools if not there(t)] + ([] if ZLIB.exists() else ["zlib (projects/zlib-n64/prepare.py)"])


@pytest.fixture(scope="module", params=DEMOS, ids=[c.id for c in DEMOS])
def demo(request, tmp_path_factory):
    import subprocess
    import sys

    console = request.param
    if (missing := _missing(console)):
        pytest.skip(f"{console.name}'s demo needs {', '.join(missing)}")
    folder = ROOT / "projects" / console.demo
    key = folder / "build" / "demo.json"
    if not key.exists():
        subprocess.run([sys.executable, str(folder / "build.py")], check=True)
    built = json.loads(key.read_text())
    game = folder / "build" / built["game"]
    work = tmp_path_factory.mktemp(console.id)
    net = RomNetwork(work / "app.db", work)
    slug = net.import_rom(game, confirmed=True, log=lambda *_: None)
    return console, net, slug, game, built["symbols"]


def _function(net, slug: str, symbols: dict, name: str) -> tuple[str, str]:
    """(the project's name of the demo's function `name`, its C under that name)."""
    import re

    from decompnet.forks import csource

    address = next(a for s, a in symbols.items() if re.sub(r"^[_@]|@\d+$", "", s) == name)
    pid = net.project_row(slug)["id"]
    row = net.store.one("SELECT name FROM functions WHERE project_id=? AND vram=?", pid, address) or next(
        r for r in net.store.all("SELECT name FROM functions WHERE project_id=?", pid) if f"{address:08x}" in r["name"].lower())
    plain = re.sub(r"^[_@]|@\d+$", "", row["name"])  # (a PC function's name says its calling convention)
    return row["name"], csource.get(DEMO_GAME.read_text(), name).replace(f"{name}(", f"{plain}(", 1)


def test_the_demo_is_its_consoles_and_imports(demo):
    console, net, slug, game, _ = demo
    assert consoles.identify(game) is console
    ov = net.overview(slug)
    assert ov["platform"] == console.id and ov["functions"] >= 15
    assert ov.get("ready", True), ov.get("needs")


def test_a_machine_draft(demo):
    from decompnet import seeds
    from decompnet.forks import ghidra

    console, net, slug, _, symbols = demo
    compiler = net.overview(slug)["compiler"]["toolchain"]
    if not (console.m2c_target(compiler) and seeds.available()) and not (console.mode == consoles.PROJECT and ghidra.available()):
        pytest.skip(f"no draft decompiler for {console.name} here (m2c, or Ghidra for PC)")
    name, _ = _function(net, slug, symbols, FUNCTION)
    agent, _ = net.local_agent("draft-agent")
    brief = net.claim(agent, slug, name)
    assert "draft" in brief.lower() or "m2c" in brief, brief[-1500:]
    net.release(agent, name, "", slug)


def test_matching_c_scores_zero_and_wrong_c_does_not(demo):
    console, net, slug, _, symbols = demo
    name, source = _function(net, slug, symbols, FUNCTION)
    agent, _ = net.local_agent("conformance-agent")
    net.claim(agent, slug, name)
    wrong = net.attempt(agent, name, source.replace(*WRONG), slug)
    assert not wrong["matched"] and wrong["score"] not in (None, 0), wrong["text"]
    right = net.attempt(agent, name, source, slug)
    assert right["matched"] and right["score"] == 0, right["text"]


def test_the_game_rebuilds_byte_for_byte(demo):
    console, net, slug, _, _ = demo
    if consoles.EXPORT in console.features:
        res = net.export_rom(slug, log=lambda *_: None)
    elif consoles.REBUILD_CHECK in console.features:
        res = net.forks.check_rom(slug, log=lambda *_: None)
    else:
        pytest.skip(f"{console.name} has no export yet (ROADMAP.md)")
    assert res["identical"], res.get("log") or res.get("detail")
