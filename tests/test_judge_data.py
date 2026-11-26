"""The ROM judge compares a candidate's own data (jump tables, strings, constants) with the game's: it reads them
through the loaded image when no code segment holds their address."""

from pathlib import Path

import yaml

from decompnet.app.engine import RomNetwork
from decompnet.rom import split

CONFIG = {"segments": [
    {"name": "header", "type": "header", "start": 0},
    {"name": "main", "type": "code", "start": 0x1050, "vram": 0x80000450,
     "subsegments": [[0x1050, "c", "main"], {"type": "bss", "vram": 0x80000850}]},
    {"name": "code_80000500", "type": "code", "start": 0x1100, "vram": 0x80000500,
     "subsegments": [[0x1100, "c", "code_80000500"]]},
    {"type": "bin", "start": 0x1200},
    [0x1400]]}


def test_a_code_segment_with_its_bss_loads_its_data_too():
    assert split.load_images(CONFIG) == [(0x1050, 0x80000450, 0x80000850)]
    assert split.load_images({"segments": [{"type": "code", "start": 0, "vram": 0x80000000}]}) == []


class _Engine:
    """The parts of the engine its ROM reader uses."""
    _roms: dict = {}

    def __init__(self, cfg):
        self._cfg = cfg

    def cfg(self, proj):
        return self._cfg

    def _rom(self, proj):
        return RomNetwork._rom(self, proj)


def test_the_judge_reads_data_after_the_code_and_nothing_in_bss(tmp_path: Path):
    """A racing game's code segments ended where its code did: the judge read no bytes for a candidate's jump table,
    skipped the check, and 19 switches matched with wrong case labels."""
    rom = bytes(range(256)) * 32
    (tmp_path / "rom.z64").write_bytes(rom)
    (tmp_path / "game.yaml").write_text(yaml.safe_dump(CONFIG))
    segs = [vars(s) for s in split.segments_from_config_data(CONFIG)]
    engine = _Engine({"segments": segs, "rom": {"file": "rom.z64"}})
    _Engine._roms = {}
    read = RomNetwork.reader(engine, {"id": 1, "root": str(tmp_path)})
    assert read(0x80000600, 4) == rom[0x1200:0x1204]  # data after the last code segment, in main's image
    assert read(0x80000460, 4) == rom[0x1060:0x1064]  # code reads as before
    assert read(0x80000900, 4) is None  # BSS: no bytes in the ROM


def _dispatch(table: int) -> bytes:
    """A switch's dispatch through `table` (lui at; addu at, at, v0; lw v0, %lo(at); jr v0; nop), then a return."""
    words = [0x3C010000 | (table + 0x8000) >> 16, 0x00220821, 0x8C220000 | table & 0xFFFF, 0x00400008, 0,
             0x03E00008, 0]
    return b"".join(w.to_bytes(4, "big") for w in words)


def _game(table_words, *, tail=True, next_vram=0x80300000):
    """A ROM whose code found later (code_80200000, ROM 0x1000) is followed by its data as a raw `bin`, the way the
    import writes it, with its jump table 0x40 into the data; then another code segment."""
    rom = bytearray(0x3000)
    code = _dispatch(0x80200140)
    rom[0x1000:0x1000 + len(code)] = code
    for i, w in enumerate(table_words):
        rom[0x1140 + 4 * i:0x1144 + 4 * i] = w.to_bytes(4, "big")
    segments = [{"name": "header", "type": "header", "start": 0},
                {"name": "code_80200000", "type": "code", "start": 0x1000, "vram": 0x80200000,
                 "subsegments": [[0x1000, "c", "code_80200000"]]},
                {"type": "bin", "start": 0x1100}]
    if tail:
        segments += [{"name": "code_80300000", "type": "code", "start": 0x2000, "vram": next_vram,
                      "subsegments": [[0x2000, "c", "code_80300000"]]}, {"type": "bin", "start": 0x2100}]
    return bytes(rom), {"segments": [*segments, [len(rom)]]}


def test_the_data_after_code_found_later_is_read_at_its_code_offset(tmp_path: Path):
    """An N64 racing game's later code had its 113 KB of data after it filed as raw bytes with no address: 345
    switches in 11 games had jump tables "not in a loaded part of the ROM", and the judge never checked them (five
    matches there had a wrong case). The switches prove the data is the code's: it is read at the code's offset."""
    cases = [0x80200014, 0x80200018, 0x80200014]
    rom, cfg = _game(cases)
    assert split.load_images(cfg) == []  # (without the ROM: what every older reader saw)
    assert split.load_images(cfg, rom) == [(0x1100, 0x80200100, 0x80201000)]
    (tmp_path / "rom.z64").write_bytes(rom)
    (tmp_path / "game.yaml").write_text(yaml.safe_dump(cfg))
    segs = [vars(s) for s in split.segments_from_config_data(cfg)]
    engine = _Engine({"segments": segs, "rom": {"file": "rom.z64"}})
    _Engine._roms = {}
    read = RomNetwork.reader(engine, {"id": 1, "root": str(tmp_path)})
    assert read(0x80200140, 12) == b"".join(w.to_bytes(4, "big") for w in cases)
    assert read(0x80201000, 4) is None  # past the raw bytes: unbacked, as before


def test_raw_bytes_the_switches_dont_prove_stay_unread():
    """Packed data, the ROM's tail (its assets) and bytes at another segment's addresses are never mapped: a wrong
    read would fail a right candidate where no bytes only skip the check."""
    rom, cfg = _game([0x12345678, 0x9ABCDEF0])  # a table that isn't one: packed bytes
    assert split.trailing_data(cfg, rom) == []
    rom, cfg = _game([0x80200014, 0x80200018], tail=False)  # the raw bytes run to the ROM's end
    assert split.trailing_data(cfg, rom) == []
    rom, cfg = _game([0x80200014, 0x80200018], next_vram=0x80200800)  # another segment's addresses
    assert split.trailing_data(cfg, rom) == []
