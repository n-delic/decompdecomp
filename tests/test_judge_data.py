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
