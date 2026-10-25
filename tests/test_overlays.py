"""PlayStation 2 overlays (consoles/ps2/overlays.py): code in other files of a disc, placed at the address it's loaded at
from its own calls, and split with the program, as a segment of its own."""

import struct
import subprocess
import sys
from pathlib import Path

import pytest

from decompnet import consoles, toolchains
from decompnet.consoles.ps2 import overlays
from decompnet.rom import Picked, split

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "projects" / "demo-ps2"
EXE = DEMO / "build" / "SLUS_999.99"
NOP, JR_RA = 0, 0x03E00008
OVERLAY_AT = 0x00400000  # above the demo program (linked at 0x00100000), where a game would load one
needs_demo = pytest.mark.skipif(toolchains.installed("ee-gcc-2.96") is None or toolchains.installed("binutils-mips") is None,
                                reason="needs ee-gcc-2.96 and binutils-mips to build the PS2 demo")


@pytest.fixture(scope="module")
def exe() -> bytes:
    if not EXE.exists():
        subprocess.run([sys.executable, str(DEMO / "build.py")], check=True)
    return EXE.read_bytes()


def _overlay(base: int, count: int) -> bytes:
    """`count` little functions at `base` (little-endian), each calling the next."""
    words = []
    for i in range(count):
        callee = base + ((i + 1) % count) * 32
        words += [0x27BDFFE8, 0xAFBF0014, 0x0C000000 | ((callee >> 2) & 0x03FFFFFF), NOP, 0x8FBF0014, 0x27BD0018,
                  JR_RA, NOP]
    return struct.pack(f"<{len(words)}I", *words)


def test_an_overlay_is_placed_at_its_load_address():
    data = bytes(64) + _overlay(OVERLAY_AT + 0x40, 60)  # a little header before its code, as overlay files often have
    assert overlays.place(data, [(0x00100000, 0x00150000)]) == (OVERLAY_AT, 64)
    assert overlays.place(data, [(0x003F0000, 0x00420000)]) is None  # over the resident program: no


def test_overlays_keep_the_programs_padding(tmp_path):
    """The overlays go after the program's image; the padding between the program's segments stays (a game whose
    engine sits above its .bss has some: dropped, its segments ran over each other's addresses)."""
    import yaml

    segments = [{"name": "cod", "type": "code", "start": 0, "vram": 0x100000, "subsegments": [[0, "asm", "cod"]]},
                [0x10, "pad"], {"name": "far", "type": "code", "start": 0x20, "vram": 0x400000,
                                "subsegments": [[0x20, "asm", "far"]]}, [0x30]]
    config = tmp_path / "game.yaml"
    config.write_text(yaml.safe_dump({"sha1": "0", "options": {}, "segments": segments}, sort_keys=False))
    (tmp_path / "game.rom").write_bytes(bytes(0x30))
    out = overlays.add_to_config(config, tmp_path, "game.rom", [("battle", _overlay(OVERLAY_AT, 4), OVERLAY_AT, 0)],
                                 log=lambda *_: None)
    written = yaml.safe_load(config.read_text())["segments"]
    assert written[:3] == segments[:3] and written[4]["name"] == "ovl_battle"
    assert written[3] == [0x30, "pad"]  # the program's end: what follows up to the overlay is padding, not its data
    assert written[-1] == [len((tmp_path / out).read_bytes())] and [0x30] not in written  # one end: the new one


def test_ps2_elf_overlays_say_where_they_go():
    code = _overlay(0x00400000, 40)
    phoff = 52
    elf = bytearray(b"\x7fELF\x01\x01\x01" + bytes(9))
    elf += struct.pack("<HHIIIIIHHHHHH", 2, 8, 1, 0x00400000, phoff, 0, 0, 52, 32, 1, 0, 0, 0)
    elf += struct.pack("<8I", 1, 0x100, 0x00400000, 0x00400000, len(code), len(code), 5, 0x10)
    elf += bytes(0x100 - len(elf)) + code
    assert overlays.elf_segments(bytes(elf)) == [(0x00400000, code)]
    irx = bytearray(elf)
    irx[16:18] = struct.pack("<H", 0xFF80)  # an IOP module: relocatable, left out
    assert overlays.elf_segments(bytes(irx)) is None


@needs_demo
def test_overlays_are_split_with_the_program(tmp_path, exe):
    (tmp_path / "game.elf").write_bytes(exe)
    # two overlays that load at the same address (only one is in memory at a time)
    found = [("battle", _overlay(OVERLAY_AT, 30), OVERLAY_AT, 0), ("field", _overlay(OVERLAY_AT, 20), OVERLAY_AT, 0)]
    picked = Picked("ps2", exe, "game.elf", {}, False, overlays=found)
    res = split.split_rom(tmp_path, "game.elf", consoles.get("ps2"), picked, log=lambda *_: None)
    names = {f.name for f in res.functions}
    assert "func_00400000" in names or "ovl_battle_func_00400000" in names
    battle = [f for f in res.functions if f.segment == "ovl_battle"]
    field = [f for f in res.functions if f.segment == "ovl_field"]
    assert len(battle) == 30 and len(field) == 20
    assert len({f.name for f in battle + field}) == 50  # same addresses, names of their own
    assert ".overlays." in res.image  # a working copy of the image with the overlays after the program


def test_overlays_inside_an_archive_are_found_and_placed():
    """A game can keep its areas' code inside one big archive (models, sounds and code of each area, one after
    another): each stretch of code is placed by its own calls, and the same code a second time is kept once."""
    from decompnet.consoles.ps2 import disc

    filler = bytes(range(256)) * 512  # 128 KiB of an area's other files: no code
    code = _overlay(OVERLAY_AT, 80)
    archive = filler + code + filler + code + filler
    image = _iso({"SLUS_999.99": b"\x7fELF", "AREAS.DAT": archive})
    found = overlays.find(disc._Disc(image), "SLUS_999.99", (0x00100000, 0x00150000))
    assert [(name, at, len(data)) for name, data, at, _ in found] == [(f"areas_{len(filler):x}", OVERLAY_AT, len(code))]
    assert found[0][1] == code


def _iso(files: dict[str, bytes]) -> bytes:
    """The smallest ISO 9660 image the disc reader takes: a volume descriptor, a root directory, the files."""
    def both16(v):
        return struct.pack("<H", v) + struct.pack(">H", v)

    def both32(v):
        return struct.pack("<I", v) + struct.pack(">I", v)

    def record(name: bytes, lba: int, size: int, is_dir: bool) -> bytes:
        rec = bytes([0, 0]) + both32(lba) + both32(size) + bytes(7) + bytes([2 if is_dir else 0, 0, 0]) + both16(1) \
            + bytes([len(name)]) + name
        rec += bytes(len(rec) % 2)
        return bytes([len(rec)]) + rec[1:]

    lba = 19
    places = {}
    for name, data in files.items():
        places[name] = (lba, len(data))
        lba += -(-len(data) // 2048)
    root = record(b"\0", 18, 2048, True) + record(b"\1", 18, 2048, True)
    for name, (at, size) in places.items():
        root += record(f"{name};1".encode(), at, size, False)
    pvd = bytearray(2048)
    pvd[0:7] = b"\x01CD001\x01"
    pvd[156:156 + 34] = record(b"\0", 18, 2048, True)
    image = bytearray(16 * 2048) + pvd + bytearray(b"\xffCD001\x01".ljust(2048, b"\0")) + bytearray(root.ljust(2048, b"\0"))
    for name, data in files.items():
        image += data + bytes(-len(data) % 2048)
    return bytes(image)


@needs_demo
def test_a_disc_with_an_overlay_imports_both(tmp_path, exe):
    from decompnet.app.engine import RomNetwork

    disc = _iso({"SYSTEM.CNF": b"BOOT2 = cdrom0:\\SLUS_999.99;1\r\n", "SLUS_999.99": exe,
                 "BATTLE.BIN": bytes(64) + _overlay(OVERLAY_AT + 0x40, 40), "MUSIC.VAB": bytes(range(256)) * 32})
    (tmp_path / "game.iso").write_bytes(disc)
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    said = []
    slug = net.import_rom(tmp_path / "game.iso", confirmed=True, log=said.append)
    assert any("overlay on the disc: battle at 0x00400000" in s for s in said), said
    rows = net.store.all("SELECT name, vram FROM functions WHERE project_id=?", net.project_row(slug)["id"])
    in_overlay = [r for r in rows if OVERLAY_AT <= r["vram"] < OVERLAY_AT + 0x1000]
    assert len(in_overlay) == 40 and len(rows) > 40  # the program's functions and the overlay's
