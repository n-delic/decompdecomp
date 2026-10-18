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
    return not tools._guide(topic).startswith("Guides (call guide")


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
    assert {c.id for c in consoles.CONSOLES if c.status == consoles.PUBLIC} >= {"ps", "gba", "nds"}  # forks only


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


def test_mips_code_in_a_forks_object_isnt_judged_without_its_console():
    """A fork's objects don't say their console, and which registers carry arguments depends on it (the PS2's EABI
    passes them in t0-t3): a MIPS function there isn't set aside as hand-written, though its console would."""
    stub = struct.pack("<4I", 0x24030010, 0x0000000C, 0x03E00008, 0)  # li v1, 0x10; syscall; jr ra; nop
    symtab, strtab = _symbols([("stub", 0, len(stub), STB_GLOBAL, STT_FUNC, 1)])
    obj = _elf([(".text", SHT_PROGBITS, SHF_ALLOC | SHF_EXECINSTR, 0, stub, 0),
                (".symtab", SHT_SYMTAB, 0, 0, symtab, 3), (".strtab", SHT_STRTAB, 0, 0, strtab, 0)])
    assert object_prints(obj)["stub"].asm == ""
    assert "system call" in consoles.get("ps2").asm_reason(stub)
