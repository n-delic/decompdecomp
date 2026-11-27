"""Code an N64 game loads at run time, found by running it (consoles/n64/loaded_code.py): homemade ROMs and RAM, the
emulator stood in for.

A racing game's overlays are small, each loaded at its own address, and the static search placed none of 48
functions its known code calls (its main thread's code among them): RAM that is byte for byte a stretch of the ROM
gives each segment exactly.
"""
import struct
from pathlib import Path

from decompnet.consoles.n64 import emulator, loaded_code

FRAME = 0x27BDFFE8  # addiu $sp, $sp, -0x18
JR_RA = 0x03E00008


def _jal(target):
    return 0x0C000000 | (target >> 2) & 0x03FFFFFF


def _words(*ws):
    return b"".join(struct.pack(">I", w) for w in ws)


def _function(n=6):
    """A function: its frame, some work, its return."""
    return _words(FRAME, *range(0x24020001, 0x24020001 + n), JR_RA, 0x27BD0018)


def _setup(tmp_path, monkeypatch, ram):
    """The emulator stood in for: its dumps are `ram` (RDRAM from 0x80000000)."""
    monkeypatch.setattr(emulator, "installed", lambda: tmp_path)

    def run(rom, vis, *, dumps=None, **kw):
        result = emulator.Run()
        for vi, path in (dumps or {}).items():
            Path(path).write_bytes(ram)
            result.dumps[vi] = Path(path)
        return result
    monkeypatch.setattr(emulator, "run", run)


def _rom(size=0x8000):
    return bytearray(size)


def test_an_overlay_the_known_code_calls_is_found_where_the_game_loads_it(tmp_path, monkeypatch):
    rom = _rom()
    main = _words(FRAME, _jal(0x80201000), 0, JR_RA, 0x27BD0018)
    rom[0x1000:0x1000 + len(main)] = main
    overlay = _function() + _words(FRAME, _jal(0x80201100), 0, JR_RA, 0x27BD0018)  # (it calls a second overlay)
    rom[0x3000:0x3000 + len(overlay)] = overlay
    second = _function(4)
    rom[0x5000:0x5000 + len(second)] = second
    ram = bytearray(0x400000)
    ram[0x201000:0x201000 + len(overlay)] = overlay  # the game loaded both
    ram[0x201100:0x201100 + len(second)] = second
    _setup(tmp_path, monkeypatch, bytes(ram))
    (tmp_path / "rom.z64").write_bytes(rom)
    found = loaded_code.find(tmp_path / "rom.z64", bytes(rom), [(0x1000, 0x1000 + len(main), 0x80000400)],
                             log=lambda *a: None)
    segments = found["segments"]
    assert (0x3000, 0x3000 + len(overlay), 0x80201000) in segments
    assert any(s == 0x5000 and v == 0x80201100 for s, _, v in segments)  # followed through the first one's call
    assert found["decoded"] == 0 and found["moved"] == []


def test_a_leaf_function_before_the_first_frame_starts_the_segment(tmp_path, monkeypatch):
    rom = _rom()
    main = _words(FRAME, _jal(0x80201000), 0, JR_RA, 0x27BD0018)
    rom[0x1000:0x1000 + len(main)] = main
    leaf = _words(0x00851021, JR_RA, 0)  # addu $v0, $a0, $a1: no frame
    overlay = leaf + _function() + _function(5)
    rom[0x3000:0x3000 + len(overlay)] = overlay
    ram = bytearray(0x400000)
    ram[0x201000:0x201000 + len(overlay)] = overlay
    _setup(tmp_path, monkeypatch, bytes(ram))
    (tmp_path / "rom.z64").write_bytes(rom)
    found = loaded_code.find(tmp_path / "rom.z64", bytes(rom), [(0x1000, 0x1000 + len(main), 0x80000400)],
                             log=lambda *a: None)
    assert found["segments"] and found["segments"][0][0] == 0x3000 and found["segments"][0][2] == 0x80201000


def test_code_the_game_decodes_as_it_loads_is_reported_not_added(tmp_path, monkeypatch):
    rom = _rom()
    main = _words(FRAME, _jal(0x80201000), 0, JR_RA, 0x27BD0018)
    rom[0x1000:0x1000 + len(main)] = main
    ram = bytearray(0x400000)
    code = _function() + _function(11) + _function(13)
    ram[0x201000:0x201000 + len(code)] = code  # in RAM, nowhere in the ROM as it is
    _setup(tmp_path, monkeypatch, bytes(ram))
    (tmp_path / "rom.z64").write_bytes(rom)
    found = loaded_code.find(tmp_path / "rom.z64", bytes(rom), [(0x1000, 0x1000 + len(main), 0x80000400)],
                             log=lambda *a: None)
    assert found["segments"] == [] and found["decoded"] == 1


def test_an_overlay_the_split_has_part_of_adds_only_the_rest(tmp_path, monkeypatch):
    rom = _rom()
    main = _words(FRAME, _jal(0x80201000 + 0x40 * 4), 0, JR_RA, 0x27BD0018)
    rom[0x1000:0x1000 + len(main)] = main
    first, second = _function(0x38), _function(5)
    overlay = first + second
    rom[0x3000:0x3000 + len(overlay)] = overlay
    ram = bytearray(0x400000)
    ram[0x201000:0x201000 + len(overlay)] = overlay
    _setup(tmp_path, monkeypatch, bytes(ram))
    (tmp_path / "rom.z64").write_bytes(rom)
    taken = [(0x1000, 0x1000 + len(main), 0x80000400), (0x3000, 0x3000 + len(first), 0x80201000)]  # its first half
    found = loaded_code.find(tmp_path / "rom.z64", bytes(rom), taken, log=lambda *a: None)
    assert found["segments"] == [(0x3000 + len(first), 0x3000 + len(overlay), 0x80201000 + len(first))]


def test_without_the_emulator_nothing_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(emulator, "installed", lambda: None)
    found = loaded_code.find(tmp_path / "rom.z64", bytes(0x2000), [(0x1000, 0x1010, 0x80000400)], log=lambda *a: None)
    assert found["segments"] == [] and "isn't installed" in found["skipped"]


def test_runtime_moved_segment_exposes_its_byte_verified_range(tmp_path, monkeypatch):
    rom = _rom()
    main = _words(FRAME, _jal(0x80201000), 0, JR_RA, 0x27BD0018)
    rom[0x1000:0x1000 + len(main)] = main
    overlay = _function(42)
    rom[0x3000:0x3000 + len(overlay)] = overlay
    ram = bytearray(0x400000)
    ram[0x201000:0x201000 + len(overlay)] = overlay
    _setup(tmp_path, monkeypatch, bytes(ram))
    (tmp_path/'rom.z64').write_bytes(rom)
    found=loaded_code.find(tmp_path/'rom.z64',bytes(rom),
                           [(0x1000,0x1000+len(main),0x80000400)],
                           [(0x3000,0x3000+len(overlay),0x80301000)],log=lambda *_:None)
    assert found['segments']==[]
    assert found['moved']==[dict(rom=0x3000,split=0x80301000,runs_at=0x80201000)]
    (start,end,address),=found['moved_ranges']
    assert start<=0x3000<0x3000+len(overlay)<=end
    assert address+0x3000-start==0x80201000


def test_directly_called_ordered_trap_table_is_found_in_verified_ram(tmp_path, monkeypatch):
    rom = _rom()
    main = _words(FRAME, _jal(0x80201020), 0, JR_RA, 0x27BD0018)
    table = b''.join(_words(8, 0x24080000 | i) for i in range(24))
    rom[0x1000:0x1000 + len(main)] = main
    rom[0x3000:0x3000 + len(table)] = table
    ram = bytearray(0x400000)
    ram[0x201000:0x201000 + len(table)] = table
    _setup(tmp_path, monkeypatch, bytes(ram))
    (tmp_path / 'rom.z64').write_bytes(rom)
    found = loaded_code.find(tmp_path / 'rom.z64', bytes(rom),
                             [(0x1000, 0x1000 + len(main), 0x80000400)], log=lambda *_: None)
    assert found['segments'] == [(0x3000, 0x3000 + len(table), 0x80201000)]
    symbols = loaded_code.trap_symbols(bytes(rom), found['segments'])
    assert len(symbols) == 24
    assert symbols[0] == 'func_80201000 = 0x80201000; // type:func size:0x8'
    assert symbols[-1].startswith('func_802010B8 =')


def test_trap_table_requires_order_and_a_call_into_an_entry():
    table = b''.join(_words(8, 0x24080000 | i) for i in range(12))
    assert loaded_code._called_trap_table(table, 0, len(table), []) is None
    assert loaded_code._called_trap_table(table, 0, len(table), [4]) is None
    assert loaded_code._called_trap_table(table[:56], 0, 56, [0]) is None
    broken = table[:32] + _words(8, 0x24080001) + table[40:]
    assert loaded_code._called_trap_table(broken, 0, len(broken), [0]) is None
    assert loaded_code.trap_symbols(broken, [(0, len(broken), 0x80201000)]) == []


def test_trap_table_mapping_rejects_duplicate_or_different_rom_bytes():
    table = b''.join(_words(8, 0x24080000 | i) for i in range(24))
    dump = bytearray(0x400000)
    dump[0x201000:0x201000 + len(table)] = table
    rom = bytearray(0x8000)
    rom[0x3000:0x3000 + len(table)] = table
    rom[0x5000:0x5000 + len(table)] = table
    assert loaded_code._region(bytes(rom), bytes(dump), 0x80201000) is None
    rom[0x5000:0x5000 + len(table)] = bytes(len(table))
    rom[0x3000] = 1
    assert loaded_code._region(bytes(rom), bytes(dump), 0x80201000) is None


def test_repeated_full_bodies_at_a_coherent_other_mapping_defer_the_bank():
    rom = bytearray(0x2000)
    first = bytes(range(32))
    second = bytes(range(32, 64))
    rom[0x1000:0x1020] = first
    rom[0x1040:0x1060] = second
    ram = bytearray(0x4000)
    ram[0x2000:0x2020] = first
    ram[0x2040:0x2060] = second
    bank = (0x1000, 0x1100, 0x80001000)
    bodies = [(0x1000, 0x1020, 0x80001000), (0x1040, 0x1060, 0x80001040)]
    assert loaded_code.moved_banks(rom, [bank], bodies, [], [bytes(ram)] * 2) == [
        (bank, 0x80001000, 2)]
    assert not loaded_code.moved_banks(rom, [bank], bodies[:1], bodies[:1], [bytes(ram)] * 2)
    assert not loaded_code.moved_banks(rom, [bank], bodies, [], [bytes(ram)])
    ram[0x1000:0x1020] = first
    assert not loaded_code.moved_banks(rom, [bank], bodies, [], [bytes(ram)] * 2)
    third = bytes(range(64, 96))
    rom[0x1080:0x10a0] = third
    ram[0x2080:0x20a0] = third
    bodies.append((0x1080, 0x10a0, 0x80001080))
    assert loaded_code.moved_banks(rom, [bank], bodies, [], [bytes(ram)] * 2) == [(bank, 0x80001000, 2)]
