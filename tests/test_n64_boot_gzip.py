"""Boot evidence binds compressed text and data to each program's link address."""

import gzip

from decompnet.consoles.n64 import boot_gzip, code


def test_boot_loader_places_both_gzip_programs(monkeypatch):
    text = b'code' * 32
    data = b'data' * 8
    monkeypatch.setattr(code, '_looks_like_code', lambda payload: payload == text)
    pair = gzip.compress(text) + gzip.compress(data)
    pair += bytes(-len(pair) % 16)
    source = 0x100
    midpoint = source + len(pair)
    limit = midpoint + len(pair)
    rom = bytes(source) + pair * 2

    def constant(value):
        return dict(kind='constant', value=value)

    def call(instruction, target, *args):
        return dict(kind='call', instruction=instruction, target=target, args=[constant(arg) for arg in args])

    def store(instruction, place, value):
        return dict(kind='store', instruction=instruction, block=1, width=4,
                    place=constant(place), value=constant(value))

    events = [
        call(0x80200000, 0x80200800, 0, source, 0x80000400, 0),
        call(0x80200020, 0x80200900, 0x80000400, 0x80300000, 0x80100000, 13),
        call(0x80200028, 0x80200900, 0, 0, 0, 10),
        store(0x80200050, 0x803ffff8, midpoint),
        store(0x80200060, 0x803ffffc, limit),
    ]
    monkeypatch.setattr(boot_gzip, 'read', lambda *_: dict(events=events))
    boot = [(0, source, 0x80200000)]

    assert boot_gzip.find(rom, boot) == ((0x80300000, text, data), (0x80100000, text, data))
    expanded = rom + (text + data) * 2
    assert boot_gzip.verify_analysis(rom, expanded, boot)
    assert not boot_gzip.verify_analysis(rom, expanded[:-1] + b'?', boot)
    damaged = bytearray(rom)
    damaged[midpoint] = 0
    assert boot_gzip.find(bytes(damaged), boot) is None
    events[-1]['place'] = constant(0x803ffff4)
    assert boot_gzip.find(rom, boot) is None


def test_verification_rejoins_only_contiguous_boot_mappings(monkeypatch):
    original = bytes(64)
    text, data = b'text', b'data'
    calls = []

    def find(rom, ranges):
        calls.append(ranges)
        return [(0x80001000,text,data)] if ranges == [(0,32,0x80000000)] else None

    monkeypatch.setattr(boot_gzip, 'find', find)
    assert boot_gzip.verify_analysis(original, original+text+data,
                                     [(16,32,0x80000010),(0,16,0x80000000)])
    assert not boot_gzip.verify_analysis(original, original+text+data,
                                         [(0,16,0x80000000),(16,32,0x80001010)])
    assert calls[-1] == [(0,16,0x80000000),(16,32,0x80001010)]
    assert not boot_gzip.verify_analysis(original, original+text+b'bad!',
                                         [(0,16,0x80000000),(16,32,0x80000010)])


def test_inline_tables_require_the_entire_audited_span(monkeypatch):
    import hashlib

    prefix = b'table signature'
    payload = prefix + bytes(range(17))
    digest = hashlib.sha256(payload).hexdigest()
    monkeypatch.setattr(boot_gzip, 'INLINE_TABLES', ((prefix, len(payload), digest),))

    assert boot_gzip.inline_tables(b'code' + payload + b'code') == [(4, 4 + len(payload))]
    assert boot_gzip.inline_tables(b'code' + payload[:-1] + b'?' + b'code') == []
    assert boot_gzip.inline_tables(b'code' + payload) == []


def test_boot_dma_pair_uses_the_inflater_output_pointer(monkeypatch):
    text, data = b'code' * 32, b'data' * 8
    monkeypatch.setattr(code, '_looks_like_code', lambda payload: payload == text)
    pair = gzip.compress(text) + gzip.compress(data)
    pair += bytes(-len(pair) % 16)
    source, scratch, output = 0x100, 0x80020000, 0x80500000
    rom = bytes(source) + pair

    def constant(value):
        return dict(kind='constant', value=value)

    def stack(offset):
        return dict(kind='stack', value='frame', offset=offset)

    dma = dict(kind='call', instruction=0x80001000, target=0x80002000, block=1,
               args=[constant(source), constant(len(rom)), constant(scratch), None],
               memory=[dict(place=stack(52), value=constant(scratch)),
                       dict(place=stack(48), value=constant(output))])
    inflate = dict(kind='call', instruction=0x8000100c, target=0x80002040, block=1,
                   args=[stack(52), stack(48), None, None])
    monkeypatch.setattr(boot_gzip, 'read', lambda *_: dict(events=[dma, inflate]))
    boot = [(0, source, 0x80001000)]
    assert boot_gzip.find(rom, boot) == ((output, text, data),)
    assert boot_gzip.verify_analysis(rom, rom + text + data, boot)
    assert not boot_gzip.verify_analysis(rom, rom + text + data[:-1] + b'?', boot)
    dma['memory'][1]['value'] = constant(scratch)
    assert boot_gzip.find(rom, boot) is None


def test_pointer_loader_follows_registered_shared_overlays(monkeypatch, tmp_path):
    text, data = b'code' * 32, b'data' * 8
    monkeypatch.setattr(code, '_looks_like_code', lambda payload: payload == text)
    pair = gzip.compress(text) + gzip.compress(data)
    pair += bytes(-len(pair) % 16)
    source, scratch, output, overlay = 0x100, 0x80020000, 0x80500000, 0x80024000
    rom = bytes(source) + pair * 3

    def constant(value):
        return dict(kind='constant', value=value)

    def stack(offset):
        return dict(kind='stack', value='frame', offset=offset)

    dma = dict(kind='call', instruction=0x80001000, target=0x80002000, block=1,
               args=[constant(source), constant(source + len(pair)), constant(scratch), None],
               memory=[dict(place=stack(52), value=constant(scratch)),
                       dict(place=stack(48), value=constant(output))])
    inflate = dict(kind='call', instruction=0x8000100c, target=0x80002040, block=1,
                   args=[stack(52), stack(48), None, None])
    stores = []
    for i in range(3):
        for field, value in ((0, source + i * len(pair)), (4, source + (i + 1) * len(pair)),
                             (8, output if i == 0 else overlay)):
            stores.append(dict(kind='store', width=4, place=constant(0x80008000 + 24 * i + field),
                               value=constant(value)))
    monkeypatch.setattr(boot_gzip, 'read', lambda *_: dict(events=[dma, inflate, *stores]))
    boot = [(0, source, 0x80001000)]
    programs = boot_gzip.find(rom, boot)
    assert programs == ((output, text, data), (overlay, text, data), (overlay, text, data))
    assert boot_gzip.verify_analysis(rom, rom + (text + data) * 3, boot)
    import yaml

    config = tmp_path / 'splat.yaml'
    config.write_text(yaml.safe_dump({'options': {}, 'segments': [[len(rom)]]}))
    filename = boot_gzip.add(config, tmp_path, 'image.z64', rom, boot, log=lambda _: None)
    assert (tmp_path / filename).read_bytes() == rom + (text + data) * 3
    segments = yaml.safe_load(config.read_text())['segments'][:-1]
    assert segments[0]['exclusive_ram_id'] != segments[1]['exclusive_ram_id']
    assert segments[1]['exclusive_ram_id'] == segments[2]['exclusive_ram_id']
    assert len({segment['name'] for segment in segments}) == 3
    stores[-1]['value'] = constant(overlay + 16)
    assert boot_gzip.find(rom, boot) == ((output, text, data),)


def test_boot_mapping_coalesces_adjacent_fragments_only():
    from decompnet.consoles.n64.boot_gzip import _coalesce_boot_ranges

    rows = [(0x1000,0x1010,0x80000400),(0x1010,0x1030,0x80000410),
            (0x1034,0x1040,0x80000434),(0x1040,0x1050,0x80001440)]
    assert _coalesce_boot_ranges(rows) == [(0x1000,0x1030,0x80000400),
                                           (0x1034,0x1040,0x80000434),
                                           (0x1040,0x1050,0x80001440)]


def test_inline_pointer_table_is_data():
    table = bytes.fromhex(
        '800838d000000000800c3e6800000000800efbf800000000'
        '800fb428000000008013a7b000000000'
    )
    assert boot_gzip.inline_tables(b'code' + table + b'code') == [(4, 44)]
    assert boot_gzip.inline_tables(b'code' + table[:-1] + b'?' + b'code') == []
