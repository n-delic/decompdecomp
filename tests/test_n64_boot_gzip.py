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


def test_inline_tables_require_the_entire_audited_span(monkeypatch):
    import hashlib

    prefix = b'table signature'
    payload = prefix + bytes(range(17))
    digest = hashlib.sha256(payload).hexdigest()
    monkeypatch.setattr(boot_gzip, 'INLINE_TABLES', ((prefix, len(payload), digest),))

    assert boot_gzip.inline_tables(b'code' + payload + b'code') == [(4, 4 + len(payload))]
    assert boot_gzip.inline_tables(b'code' + payload[:-1] + b'?' + b'code') == []
    assert boot_gzip.inline_tables(b'code' + payload) == []
