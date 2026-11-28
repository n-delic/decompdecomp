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


def test_audio_bootstrap_is_not_cpu_code_when_followed_by_verified_audio(tmp_path):
    import struct

    from decompnet.consoles.n64.port import RSPBOOT

    boot = 0x20
    image = bytearray(boot + 0xd0 + 0x1000)
    image[boot:boot + len(RSPBOOT)] = RSPBOOT
    image[boot + 0xd0:boot + 0xe0] = bytes.fromhex('2018036020170f908c3c00308c3b0034')
    for i in range(64):
        struct.pack_into('>I', image, boot + 0x100 + i * 4, 0x4a000000)
    for i in range(3):
        struct.pack_into('>I', image, boot + 0x200 + i * 4, 0x09000400 + i)
    config = tmp_path / 'game.yaml'
    end = boot + 0x180  # the guessed CPU range ends in the verified RSP program
    config.write_text(yaml.safe_dump(dict(segments=[
        dict(type='code', start=0, vram=0x80000000, subsegments=[[0, 'c', 'program']]),
        dict(type='bin', start=end), [len(image)],
    ])))
    inline_data.apply(config, image, lambda _: None)
    assert code_ranges(config) == [(0, boot, 0x80000000)]
    assert yaml.safe_load(config.read_text())['segments'][0]['subsegments'] == [
        [0, 'c', 'program'], [boot, 'rodata', 'program_data_20'],
    ]


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


def test_linker_markers_after_return_are_data():
    from decompnet.consoles.n64.inline_data import spans

    marker = bytes.fromhex('12345678') + bytes(12)
    image = bytes.fromhex('03e0000800000000ffffffff') + marker * 3 + bytes.fromhex('27bdffe0')
    assert (8, 60) in list(spans(image))
    assert (8, 60) not in list(spans(bytes(8) + image[8:]))


def test_all_one_padding_before_scheduled_frame():
    from decompnet.consoles.n64.inline_data import spans

    image = bytes.fromhex('03e00008ace20000ffffffffffffffff3c02800e8c422f2827bdffc8')
    assert (8, 16) in list(spans(image))
    assert (8, 16) not in list(spans(image[:-4] + bytes(4)))


def test_verified_tables_end_before_the_following_code(tmp_path, monkeypatch):
    data = b'table contents!!'
    monkeypatch.setattr(inline_data, '_TABLES', ((data[:8].hex(), len(data), hashlib.sha256(data).hexdigest()),))
    image = b'code' + data + b'next'
    config = tmp_path / 'game.yaml'
    config.write_text(yaml.safe_dump(dict(segments=[
        dict(type='code', start=0, vram=0x80000000, subsegments=[[0, 'c', 'program']]), [len(image)],
    ])))
    inline_data.apply(config, image, lambda _: None)
    assert code_ranges(config) == [(0, 4, 0x80000000), (20, 24, 0x80000014)]
    assert not list(inline_data.spans(b'code' + data[:-1] + b'?' + b'next'))


def test_startup_stack_zero_region_rejects_control_flow_into_it():
    import struct

    image = bytearray(b'\x44'*0x1400)
    struct.pack_into('>2I', image, 0x1000, 0x3c1d8000, 0x27bd1000)
    image[0x1100:0x1180] = bytes(128)
    ranges = [(0x1000, 0x1010, 0x80000400), (0x1100, 0x1200, 0x80001000)]
    assert list(inline_data._stack_padding(image, ranges)) == [(0x1100, 0x1180)]
    struct.pack_into('>I', image, 0x1008, 0x0c000400)
    assert not list(inline_data._stack_padding(image, ranges))


def test_verified_data_span_keeps_its_trailing_words_out_of_text(monkeypatch):
    context = b'precede!' + b'data' + bytes(12) + b'following code!!'
    signature = (context[:12].hex(), len(context), 16, hashlib.sha256(context).hexdigest())
    monkeypatch.setattr(inline_data, '_LITERALS', (signature,))
    assert list(inline_data.spans(context)) == [(8, 24)]
    changed = context[:20] + b'?' + context[21:]
    assert not list(inline_data.spans(changed))


def test_terminal_header_does_not_leave_a_code_fragment(tmp_path, monkeypatch):
    context = b'precede!' + b'data' * 3 + b'following bytes!'
    signature = (context[:12].hex(), len(context), 12, hashlib.sha256(context).hexdigest())
    monkeypatch.setattr(inline_data, '_LITERALS', (signature,))
    config = tmp_path/'game.yaml'
    config.write_text(yaml.safe_dump(dict(segments=[dict(type='code', start=0, vram=0x80000000,
        subsegments=[[0, 'c', 'program']]), [20], [len(context)]])))
    inline_data.apply(config, context, lambda _: None)
    assert code_ranges(config) == [(0, 8, 0x80000000)]


def test_leading_code_pointer_table_requires_mapped_targets_and_a_frame(tmp_path):
    import struct

    words = [0x80010000, 0x80010004, 0x80010000, 0, 0x27bdffe8, 0x03e00008, 0x27bd0018]
    image = struct.pack('>7I', *words) + bytes(0x100)
    ranges = [(0, 28, 0x80000000), (28, len(image), 0x80010000)]
    assert list(inline_data._leading_code_pointer_tables(image, ranges)) == [(0, 16)]
    assert not list(inline_data._leading_code_pointer_tables(image, ranges[:1]))
    changed = bytearray(image); struct.pack_into('>I', changed, 4, 0x80020000)
    assert not list(inline_data._leading_code_pointer_tables(bytes(changed), ranges))
    changed = bytearray(image); struct.pack_into('>I', changed, 16, 0)
    assert not list(inline_data._leading_code_pointer_tables(bytes(changed), ranges))


def test_unentered_call_word_between_return_and_frame_is_data():
    import struct

    image = bytearray(64)
    struct.pack_into('>4I', image, 16, 0x03e00008, 0x27bd0018, 0x0c000100, 0x27bdffe0)
    ranges = [(0, len(image), 0x80000000)]
    assert list(inline_data._unentered_call_words(image, ranges)) == [(24, 28)]
    struct.pack_into('>I', image, 0, 0x0c000006)
    assert not list(inline_data._unentered_call_words(image, ranges))
    struct.pack_into('>I', image, 0, 0x80000018)
    assert not list(inline_data._unentered_call_words(image, ranges))
    struct.pack_into('>2I', image, 0, 0x3c048000, 0x24840018)
    assert not list(inline_data._unentered_call_words(image, ranges))


def test_unentered_invalid_jump_before_closed_frame_is_data():
    import struct

    # The first word is not a viable RDRAM jump; the following framed leaf is closed.
    image = bytearray(128)
    struct.pack_into('>5I', image, 16, 0x0a7d2044, 0x27bdffe0, 0xafbf001c, 0x03e00008, 0x27bd0020)
    ranges = [(16, 36, 0x80001000)]
    assert list(inline_data._unentered_invalid_jump_prefixes(bytes(image), ranges)) == [(16, 20)]
    # An address reference, a call, a reachable jump or an unclosed successor retains it.
    struct.pack_into('>I', image, 0, 0x80001000)
    assert not list(inline_data._unentered_invalid_jump_prefixes(bytes(image), ranges))
    struct.pack_into('>I', image, 0, 0)
    struct.pack_into('>I', image, 16, 0x08000408)
    assert not list(inline_data._unentered_invalid_jump_prefixes(bytes(image), ranges))
    struct.pack_into('>I', image, 16, 0x0a7d2044)
    struct.pack_into('>I', image, 28, 0)
    assert not list(inline_data._unentered_invalid_jump_prefixes(bytes(image), ranges))


def test_verified_table_at_segment_start_is_not_code(tmp_path, monkeypatch):
    table = bytes(8) + b'pointer!' + bytes(16)
    monkeypatch.setattr(inline_data, '_TABLES', ((table[:16].hex(), len(table), hashlib.sha256(table).hexdigest()),))
    image = table + bytes.fromhex('27bdffe803e0000827bd0018')
    config = tmp_path / 'game.yaml'
    config.write_text(yaml.safe_dump(dict(segments=[
        dict(type='code', start=0, vram=0x80000000, subsegments=[[0, 'c', 'bank']]), [len(image)],
    ])))
    inline_data.apply(config, image, lambda _: None)
    assert code_ranges(config) == [(len(table), len(image), 0x80000000 + len(table))]
    assert (0, len(table)) not in list(inline_data.spans(table[:-1] + b'?' + image[len(table):]))
