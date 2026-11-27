"""Runtime-observed compressed programs retain a verifiable cartridge reconstruction."""
import random
import struct
import zlib

import yaml

from decompnet.consoles.n64 import runtime_zlib


def _example():
    rng = random.Random(42)
    payload = rng.randbytes(0x12000)
    packed = zlib.compress(payload)
    size = len(packed)+4
    rom = bytes(0x200) + struct.pack('>I', 0x80000000|size) + packed + bytes(0x100)
    base = 0x80010000
    ram = bytearray(0x30000)
    ram[base&0x1fffffff:(base&0x1fffffff)+len(payload)] = payload
    targets = {base+0x100,base+0x500,base+0x1000}
    return rom,payload,bytes(ram),targets,base,size


def test_runtime_program_needs_three_unique_calls_and_a_matching_prefix():
    rom,payload,ram,targets,base,size = _example()
    rows = runtime_zlib.find(rom,[ram],targets)
    assert rows == [dict(offset=0x200,size=size,vram=base,calls=sorted(targets))]
    assert runtime_zlib.find(rom,[ram],set(list(targets)[:2])) == []
    changed = bytearray(ram);changed[(base&0x1fffffff)+0x8000] ^= 1
    assert runtime_zlib.find(rom,[changed],targets) == []
    assert runtime_zlib.find(rom,[ram],targets,[(0,0x1000,base)]) == []
    duplicate = rom + rom[0x200:0x200+size]
    if len(rom)%2:
        duplicate = rom + b'\0' + rom[0x200:0x200+size]
    assert runtime_zlib.find(duplicate,[ram],targets) == []
    assert payload


def test_runtime_program_reconstruction_checks_every_byte_and_padding():
    rom,payload,ram,targets,base,size = _example()
    rows = runtime_zlib.find(rom,[ram],targets)
    expanded = rom + bytes(-len(rom)%16+16) + payload
    expanded += bytes(-len(expanded)%16)
    assert runtime_zlib.verify_analysis(rom,expanded,rows)
    altered = bytearray(expanded);altered[-16] ^= 1
    assert not runtime_zlib.verify_analysis(rom,altered,rows)
    altered = bytearray(rom);altered[0] ^= 1
    assert not runtime_zlib.verify_analysis(altered,expanded,rows)
    assert not runtime_zlib.verify_analysis(rom,expanded,[])
    assert not runtime_zlib.verify_analysis(rom,expanded,42)


def test_contiguous_tail_requires_multiple_complete_functions():
    leaf = bytes.fromhex('24020001 03e00008 00000000')
    data = bytes.fromhex('02020200 11223344')
    assert runtime_zlib._contiguous_tail(leaf+leaf+data,0,32,0x80001000) == len(leaf)*2
    assert runtime_zlib._contiguous_tail(leaf+data,0,20,0x80001000) == 0


def test_runtime_program_add_keeps_cartridge_and_records_verified_image(tmp_path, monkeypatch):
    from decompnet.consoles.n64 import loader_code

    rom,payload,ram,targets,base,_ = _example()
    rows = runtime_zlib.find(rom,[ram],targets)
    config = tmp_path/'game.yaml'
    config.write_text(yaml.safe_dump(dict(options={'target_path':'game.z64'},segments=[
        dict(name='main',type='code',start=0,vram=0x80000400,subsegments=[[0,'c','main']]),[len(rom)]])))
    (tmp_path/'game.z64').write_bytes(rom)
    def bodies(image, segments, banks, taken, log, **kwargs):
        start,_,vram = banks[0]
        return [(start+i*16,start+i*16+8,vram+i*16) for i in range(4)]
    monkeypatch.setattr(loader_code,'complete',bodies)
    result = runtime_zlib.add(config,tmp_path,'game.z64',rom,[(0,0x100,0x80000400)],rows,lambda *_:None)
    assert result and (tmp_path/'game.z64').read_bytes() == rom
    expanded = (tmp_path/result).read_bytes()
    assert runtime_zlib.verify_analysis(rom,expanded,rows)
    cfg = yaml.safe_load(config.read_text())
    program, = [s for s in cfg['segments'] if isinstance(s,dict) and s.get('name') == 'runtime_program_0000']
    assert program['vram'] == base
    assert expanded[program['start']:program['start']+len(payload)] == payload
    assert (tmp_path/runtime_zlib.MANIFEST).is_file()
