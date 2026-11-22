"""Proven literal spans split text while retaining every original byte."""

import hashlib

import yaml

from decompnet.consoles.n64 import inline_data
from decompnet.rom.split import code_ranges


def test_only_an_unchanged_aligned_span_splits_executable_text(tmp_path, monkeypatch):
    context = b'precede!fakefollowing'
    signature = (context[:12].hex(), len(context), 4, hashlib.sha256(context).hexdigest())
    monkeypatch.setattr(inline_data, '_LITERALS', (signature,))
    image = b'lead' + context + bytes(8)
    config = tmp_path/'game.yaml'
    config.write_text(yaml.safe_dump(dict(segments=[
        dict(type='code', start=0, vram=0x80000000, subsegments=[[0, 'c', 'program']]),
        [len(image)],
    ])))
    assert not list(inline_data.spans(b'!' + image))
    assert not list(inline_data.spans(image[:-12] + b'?' + image[-11:]))
    inline_data.apply(config, image, lambda _: None)
    assert code_ranges(config) == [(0,12,0x80000000),(16,len(image),0x80000010)]
    segments = yaml.safe_load(config.read_text())['segments'][0]['subsegments']
    assert segments[1][:2] == [12, 'rodata']


def test_verified_microcode_tail_ends_cpu_text_at_the_same_rom_offset(tmp_path, monkeypatch):
    context = b'padding!' + bytes.fromhex('090004194a01086ce8411800')
    signature = (context[:12].hex(), len(context), len(context)-8, hashlib.sha256(context).hexdigest())
    monkeypatch.setattr(inline_data, '_LITERALS', (signature,))
    image = b'head' + context
    config = tmp_path / 'game.yaml'
    config.write_text(yaml.safe_dump(dict(segments=[
        dict(type='code', start=0, vram=0x80000000, subsegments=[[0, 'c', 'program']]),
        [len(image)],
    ])))
    inline_data.apply(config, image, lambda _: None)
    assert code_ranges(config) == [(0, 12, 0x80000000)]
    spans = yaml.safe_load(config.read_text())['segments'][0]['subsegments']
    assert spans[-1][:2] == [12, 'rodata']


def test_sine_cycle_is_split_out_of_mapped_text(tmp_path):
    image = bytes(64) + inline_data._SINE + bytes(4)
    end = len(image)-4
    config = tmp_path / 'splat.yaml'
    config.write_text(f'''segments:
- name: code
  type: code
  start: 0
  vram: 2147483648
  subsegments:
  - [0, c, text]
- [{len(image)}]
''')
    inline_data.apply(config, image, lambda _: None)
    assert yaml.safe_load(config.read_text())['segments'][0]['subsegments'] == [
        [0, 'c', 'text'], [64, 'rodata', 'text_data_40'], [end, 'c', f'text_text_{end:X}'],
    ]
    assert not list(inline_data.spans(image[:1024] + b'\0\0' + image[1026:]))


def test_audio_coprocessor_suffix_is_data_even_if_the_text_mapping_ends_inside_it(tmp_path):
    import struct

    from decompnet.consoles.n64.port import RSPBOOT

    start = 0x80
    transfers = [0x09000400, 0x0d000420, 0x09000480]
    audio = struct.pack('>3I', 0x40045800, 0x8c220030, 0x8c230034)
    audio += struct.pack('>67I', *([0x4a000037] * 64), *transfers)
    audio = audio.ljust((len(audio) + 15) & ~15, b'\0')
    image = bytes(start) + audio + RSPBOOT
    end = start + len(audio)
    assert list(inline_data.spans(image)) == [(start, end)]
    assert not list(inline_data.spans(image[:end]))
    assert not list(inline_data.spans(image[:start + 8] + bytes(4) + image[start + 12:]))
    config = tmp_path / 'game.yaml'
    config.write_text(yaml.safe_dump(dict(segments=[
        dict(type='code', start=0, vram=0x80000000, subsegments=[[0, 'c', 'program'], [start + 20, 'data']]),
        [len(image)],
    ])))
    inline_data.apply(config, image, lambda _: None)
    assert code_ranges(config) == [(0, start, 0x80000000)]
    segments = yaml.safe_load(config.read_text())['segments'][0]['subsegments']
    assert segments[1][:2] == [start, 'rodata']
    assert segments[2] == [start + 20, 'data']


def test_unentered_boot_padding_requires_call_and_terminal_break():
    import struct

    image = bytearray(0x80)
    struct.pack_into('>3I', image, 0x28, 0x0c000010, 0, 0x0001008d)
    struct.pack_into('>I', image, 0x40, 0x27bdffe0)
    ranges = [(0x20, 0x34, 0x80000020), (0x34, 0x80, 0x80000034)]
    assert list(inline_data._startup_padding(image, ranges)) == [(0x34, 0x40)]
    struct.pack_into('>I', image, 0x50, 0x0c00000d)
    assert not list(inline_data._startup_padding(image, ranges))


def test_unaligned_store_filler_after_return_is_data():
    import struct

    image = struct.pack('>2I', 0x03e00008, 0)+bytes.fromhex('ffffffff 00000000')*5+bytes.fromhex('27bdffe0')
    assert (8, 48) in list(inline_data.spans(image))
