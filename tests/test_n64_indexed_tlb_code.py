"""A paged program needs the complete index and its boot-loader mapping."""
import struct
import zlib

import yaml

from decompnet.consoles.n64 import indexed_tlb_code as paged


def _words(*words):
    return struct.pack(f'>{len(words)}I', *words)


def _chunk(data):
    encoder = zlib.compressobj(wbits=-15)
    return struct.pack('>I', len(data)) + encoder.compress(data) + encoder.flush()


def _example(monkeypatch):
    start, key, data_vram, table_size = 0x20000, 0x8039ccca, 0x80080000, 32
    pages = [bytes([i])*0x1000 for i in range(4)]
    chunks = [_chunk(page) for page in pages]
    offsets, cursor = [], 0x20
    body = bytearray()
    for chunk in chunks:
        offsets.append(cursor)
        body.extend(chunk)
        cursor += len(chunk)
    offsets.append(cursor)
    data = b'data'*128
    archive = bytearray(struct.pack('>I', cursor))
    archive.extend(_words(*(offset ^ key for offset in offsets)))
    archive.extend(bytes(0x20-len(archive)))
    archive.extend(body)
    archive.extend(_chunk(data))
    rom = bytearray(start+len(archive))
    rom[start:] = archive
    # lui base; lui/ori end; subu; srl 12
    rom[0x1180:0x1198] = _words(0x3c0e1500, 0x3c0d1500, 0x35ad4000,
                                 0x01ae1023, 0x24420fff, 0x00027b02)
    # The second DMA is followed by the actual XOR of the loaded key.
    rom[0x1204:0x1210] = _words(0x3c058039, 0x34a5ccca, 0x01a57026)
    const = lambda v: {'kind':'constant', 'value':v}
    events = [
        {'kind':'call', 'instruction':0x80001080, 'target':0x80003000,
         'args':[const(start),const(data_vram),const(16)]},
        {'kind':'call', 'instruction':0x80001100, 'target':0x80004000,
         'args':[None,const(data_vram),None]},
        {'kind':'call', 'instruction':0x80001200, 'target':0x80003000,
         'args':[const(start+4),None,const(table_size)]},
    ]
    monkeypatch.setattr(paged, 'read', lambda *_: {'events':events})
    return bytes(rom), (0x1000, 0x1300, 0x80001000), b''.join(pages), data, events


def test_loader_proves_indexed_pages_and_data(monkeypatch):
    rom, boot, code, data, events = _example(monkeypatch)
    row = paged.find(rom, [boot])
    assert row and row['payload'] == (code, data)
    damaged = bytearray(rom);damaged[row['start']+0x30] ^= 0xff
    assert paged.find(bytes(damaged), [boot]) is None
    events[1]['args'][1] = {'kind':'constant', 'value':0x80090000}
    assert paged.find(rom, [boot]) is None


def test_paged_analysis_verifies_every_byte(monkeypatch):
    rom, boot, code, data, _ = _example(monkeypatch)
    image = rom + bytes(-len(rom)%16+16) + code
    image += bytes(-len(image)%16) + data
    image += bytes(-len(image)%16)
    assert paged.verify_analysis(rom, image, [boot])
    changed = bytearray(image);changed[-16] ^= 1
    assert not paged.verify_analysis(rom, changed, [boot])
    changed = bytearray(image);changed[len(rom)] = 1
    assert not paged.verify_analysis(rom, changed, [boot])


def test_added_program_and_data_have_separate_proven_mappings(tmp_path, monkeypatch):
    rom, boot, code, data, _ = _example(monkeypatch)
    config = tmp_path/'game.yaml'
    config.write_text(yaml.safe_dump({'options': {'target_path':'game.z64'}, 'segments': [
        {'name':'boot', 'type':'code', 'start':boot[0], 'vram':boot[2],
         'subsegments':[[boot[0], 'c', 'boot']]}, [len(rom)]]}))
    name = paged.add(config, tmp_path, 'game.z64', rom, [boot], lambda *_:None)
    image = (tmp_path/name).read_bytes()
    assert paged.verify_analysis(rom, image, [boot])
    cfg = yaml.safe_load(config.read_text())
    segments = {s['name']:s for s in cfg['segments'] if isinstance(s, dict) and 'name' in s}
    assert segments['paged_program']['vram'] == 0x15000000
    assert segments['paged_data']['vram'] == 0x80080000
    assert image[segments['paged_program']['start']:][:len(code)] == code
    assert image[segments['paged_data']['start']:][:len(data)] == data
    segments['boot']['vram'] = 0x80080000
    cfg['segments'][0] = segments['boot']
    config.write_text(yaml.safe_dump(cfg))
    assert paged.add(config, tmp_path, 'game.z64', rom, [boot], lambda *_:None) is None


def test_called_entry_after_isolated_trap_starts_a_new_source_unit():
    base = 0x15000000
    # Three direct calls name the instruction after the trap and its padding.
    jump = 0x0c000000 | ((base+28) & 0x0fffffff) >> 2
    code = _words(jump, jump, jump, 12, 0, 0, 0, 0x03e00008, 0)
    assert paged._after_traps(code, base) == [28]
    assert not paged._after_traps(_words(jump, jump, 0, 12, 0, 0, 0, 0x03e00008, 0), base)
