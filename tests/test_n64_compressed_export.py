from decompnet.consoles.n64 import code


def test_verify_every_decoded_block_and_padding(monkeypatch):
    original = b'cartridge-data'
    monkeypatch.setattr(code, 'unpack_code', lambda _: [(1, b'first'), (8, b'second')])
    expanded = bytearray(original)
    for payload in (b'first', b'second'):
        expanded.extend(bytes(-len(expanded) % 16 + 0x2000))
        expanded.extend(payload)
    assert code.verify_unpacked_analysis(original, expanded)
    for index in (0, len(original), len(original)+0x2002, len(expanded)-1):
        changed = bytearray(expanded)
        changed[index] ^= 1
        assert not code.verify_unpacked_analysis(original, changed)
    assert not code.verify_unpacked_analysis(original, expanded + b'ignored')
    monkeypatch.setattr(code, 'unpack_code', lambda _: [])
    assert not code.verify_unpacked_analysis(original, expanded)
