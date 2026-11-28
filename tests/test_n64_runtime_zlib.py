"""Runtime-observed compressed programs retain a verifiable cartridge reconstruction."""
import random
import struct
import zlib

import pytest
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


@pytest.mark.parametrize('level', [0, 6])
def test_raw_deflate_program_requires_runtime_evidence_and_exact_source(level):
    base = 0x80010000
    rng = random.Random(85)
    payload = bytes(rng.randrange(32) for _ in range(0x12000))
    compressor = zlib.compressobj(level=level, wbits=-zlib.MAX_WBITS)
    packed = compressor.compress(payload) + compressor.flush()
    rom = bytes(0x200) + packed + bytes(0x20)
    ram = bytearray(0x40000)
    ram[0x10000:0x10000+len(payload)] = payload
    targets = {base+0x100, base+0x500, base+0x1000}
    rows = runtime_zlib.find(rom, [bytes(ram)], targets)
    assert rows == [dict(codec='raw_deflate', offset=0x200, size=len(packed),
                         expanded=len(payload), vram=base, calls=sorted(targets))]
    assert runtime_zlib.find(rom, [bytes(ram)], set(list(targets)[:2])) == []
    changed = bytearray(ram); changed[0x18000] ^= 1
    assert runtime_zlib.find(rom, [bytes(changed)], targets) == []
    assert runtime_zlib.find(rom, [bytes(ram)], targets, [(0, 0x1000, base)]) == []
    duplicate = rom + bytes(-len(rom)%16) + packed
    assert runtime_zlib.find(duplicate, [bytes(ram)], targets) == []
    expanded = rom + bytes(-len(rom)%16+16) + payload
    expanded += bytes(-len(expanded)%16)
    assert runtime_zlib.verify_analysis(rom, expanded, rows)
    bad = [dict(rows[0], size=rows[0]['size']-1)]
    assert not runtime_zlib.verify_analysis(rom, expanded, bad)
    bad = [dict(rows[0], expanded=len(payload)+1)]
    assert not runtime_zlib.verify_analysis(rom, expanded, bad)
    altered = bytearray(expanded); altered[-16] ^= 1
    assert not runtime_zlib.verify_analysis(rom, altered, rows)


def test_raw_deflate_rejects_oversized_or_incomplete_streams():
    compressor = zlib.compressobj(wbits=-zlib.MAX_WBITS)
    packed = compressor.compress(bytes(runtime_zlib._RAW_LIMIT+1)) + compressor.flush()
    assert runtime_zlib._raw_deflate(packed) is None
    compressor = zlib.compressobj(wbits=-zlib.MAX_WBITS)
    small = compressor.compress(b'code' * 100) + compressor.flush()
    assert runtime_zlib._raw_deflate(small[:-2]) is None


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
    (tmp_path/'analysis-coverage.json').write_text('["Existing coverage note."]')
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
    assert 'Existing coverage note.' in (tmp_path/'analysis-coverage.json').read_text()


def _refpack_literal(payload):
    out = bytearray(b'\x10\xfb' + len(payload).to_bytes(3, 'big'))
    at = 0
    while len(payload)-at >= 4:
        size = min(112, (len(payload)-at)//4*4)
        out.append(0xe0 + size//4-1)
        out.extend(payload[at:at+size])
        at += size
    rest = payload[at:]
    out.append(0xfc + len(rest))
    out.extend(rest)
    return bytes(out)


def _refpack_code(seed, size=0x12000):
    payload = bytearray(random.Random(seed).randbytes(size))
    for index, offset in enumerate((0x100, 0x500, 0x1000)):
        payload[offset:offset+12] = struct.pack('>III', 0x24020001+index, 0x03e00008, 0)
    return bytes(payload)


def test_refpack_program_requires_runtime_and_unique_source():
    from decompnet.consoles.n64 import runtime_refpack

    payload = _refpack_code(73)
    encoded = _refpack_literal(payload)
    rom = bytes(0x200) + encoded + bytes(0x100)
    base = 0x80010000
    ram = bytearray(0x40000)
    ram[base & 0x1fffffff:(base & 0x1fffffff)+len(payload)] = payload
    targets = {base+0x100, base+0x500, base+0x1000}
    rows = runtime_refpack.find(rom, [bytes(ram)], targets)
    assert rows == [dict(offset=0x200, size=len(encoded), vram=base, calls=sorted(targets),
                         codec='refpack', expanded=len(payload))]
    assert not runtime_refpack.find(rom, [bytes(ram)], set(list(targets)[:2]))
    changed = bytearray(ram);changed[(base & 0x1fffffff)+0x8000] ^= 1
    assert runtime_refpack.find(rom, [bytes(changed)], targets) == rows
    changed[(base & 0x1fffffff)+0x100] ^= 1
    assert not runtime_refpack.find(rom, [bytes(changed)], targets)
    duplicate = rom + (b'\0' if len(rom) % 2 else b'') + encoded
    assert not runtime_refpack.find(duplicate, [bytes(ram)], targets)
    expanded = rom + bytes(-len(rom)%16+16) + payload
    expanded += bytes(-len(expanded)%16)
    assert runtime_zlib.verify_analysis(rom, expanded, rows)
    bad = bytearray(expanded);bad[-16] ^= 1
    assert not runtime_zlib.verify_analysis(rom, bad, rows)
    assert not runtime_zlib.verify_analysis(rom, expanded, [dict(rows[0], codec='unknown')])


def test_runtime_program_may_begin_with_observed_data(tmp_path, monkeypatch):
    from decompnet.consoles.n64 import loader_code

    rom,payload,ram,targets,base,_ = _example()
    rows = runtime_zlib.find(rom,[ram],targets)
    config = tmp_path/'game.yaml'
    config.write_text(yaml.safe_dump(dict(options={'target_path':'game.z64'},segments=[
        dict(name='main',type='code',start=0,vram=0x80000400,subsegments=[[0,'c','main']]),[len(rom)]])))
    (tmp_path/'game.z64').write_bytes(rom)
    def bodies(image, segments, banks, taken, log, **kwargs):
        start,_,vram = banks[0]
        return [(start+16+i*16,start+24+i*16,vram+16+i*16) for i in range(4)]
    monkeypatch.setattr(loader_code,'complete',bodies)
    result = runtime_zlib.add(config,tmp_path,'game.z64',rom,[(0,0x100,0x80000400)],rows,lambda *_:None)
    assert result
    program, = [s for s in yaml.safe_load(config.read_text())['segments']
                if isinstance(s,dict) and s.get('name') == 'runtime_program_0000']
    assert program['subsegments'][0][1] == 'bin'
    assert program['subsegments'][1][0] == program['start']+16


def test_distinct_refpack_programs_can_share_one_analysis_image():
    from decompnet.consoles.n64 import runtime_refpack

    first = _refpack_code(1)
    second = _refpack_code(2)
    a, b = _refpack_literal(first), _refpack_literal(second)
    pad = bytes(-(0x200+len(a)) % 2)
    rom = bytes(0x200)+a+pad+b+bytes(16)
    bases = (0x80010000, 0x80030000)
    ram = bytearray(0x50000)
    for base, payload in zip(bases, (first, second)):
        at = base & 0x1fffffff
        ram[at:at+len(payload)] = payload
    targets = {base+offset for base in bases for offset in (0x100, 0x500, 0x1000)}
    rows = runtime_refpack.find(rom, [bytes(ram)], targets)
    assert [r['vram'] for r in rows] == list(bases)
    expanded = bytearray(rom + bytes(-len(rom)%16+16))
    for payload in (first, second):
        expanded.extend(bytes(-len(expanded)%16))
        expanded.extend(payload)
    expanded.extend(bytes(-len(expanded)%16))
    assert runtime_zlib.verify_analysis(rom, bytes(expanded), rows)


def test_small_refpack_requires_three_complete_bodies_in_one_snapshot():
    from decompnet.consoles.n64 import runtime_refpack

    payload = _refpack_code(4, 0x2000)
    encoded = _refpack_literal(payload)
    rom = bytes(0x200) + encoded
    base = 0x80010000
    ram = bytearray(0x20000)
    ram[0x10000:0x12000] = payload
    targets = {base+offset for offset in (0x100, 0x500, 0x1000)}
    assert len(runtime_refpack.find(rom, [bytes(ram)], targets)) == 1
    first, second = bytearray(ram), bytearray(ram)
    first[0x10100] ^= 1
    second[0x10500] ^= 1
    assert not runtime_refpack.find(rom, [bytes(first), bytes(second)], targets)


def test_runtime_program_can_extend_verified_existing_analysis(tmp_path, monkeypatch):
    from decompnet.consoles.n64 import boot_gzip, loader_code

    rom, payload, ram, targets, _, _ = _example()
    rows = runtime_zlib.find(rom, [ram], targets)
    old_program = bytes(range(16))
    monkeypatch.setattr(boot_gzip, 'find', lambda *_: [(0x80030000, old_program, b'')])
    base = rom + old_program
    boot_ranges = [(0, 16, 0x80000400)]
    config = tmp_path/'game.yaml'
    config.write_text(yaml.safe_dump(dict(options={'target_path':'game.unpacked.z64'}, segments=[
        dict(name='main',type='code',start=0,vram=0x80000400,subsegments=[[0,'c','main']]),
        dict(name='old',type='code',start=len(base)-16,vram=0x80030000,
             subsegments=[[len(base)-16,'c','old']]), [len(base)]])))
    def bodies(image, segments, banks, taken, log, **kwargs):
        start, _, vram = banks[0]
        return [(start+i*16, start+i*16+8, vram+i*16) for i in range(4)]
    monkeypatch.setattr(loader_code, 'complete', bodies)
    result = runtime_zlib.add(config,tmp_path,'game.z64',rom,[],rows,lambda *_:None,
                              base_image=base,boot_ranges=boot_ranges)
    assert result
    import json
    manifest = json.loads((tmp_path/runtime_zlib.MANIFEST).read_text())
    expanded = (tmp_path/result).read_bytes()
    assert runtime_zlib.verify_analysis(rom, expanded, manifest)
    changed = bytearray(expanded); changed[len(base)-1] ^= 1
    assert not runtime_zlib.verify_analysis(rom, changed, manifest)
    changed = bytearray(expanded); changed[-16] ^= 1
    assert not runtime_zlib.verify_analysis(rom, changed, manifest)
    assert expanded.count(payload) == 1


def test_reimport_retains_entries_when_a_verified_analysis_is_extended(tmp_path):
    import hashlib
    import json

    from decompnet.app.engine.importing import _same_or_extended_analysis

    old = b'old verified analysis'
    (tmp_path/'analysis.z64').write_bytes(old)
    project = {'root': str(tmp_path), 'config': json.dumps({'rom': {'file': 'analysis.z64',
               'analysis_sha1': hashlib.sha1(old).hexdigest()}})}
    assert _same_or_extended_analysis(project, old+b'new program', 'different')
    assert not _same_or_extended_analysis(project, b'changed'+old, 'different')
    (tmp_path/'analysis.z64').write_bytes(b'tampered')
    assert not _same_or_extended_analysis(project, b'tampered'+old, 'different')
