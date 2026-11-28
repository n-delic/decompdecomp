"""The boot page chain must remain bounded and reproduce its audited bytes."""

import hashlib
import struct

from decompnet.consoles.n64 import boot_gzip, boot_rnx


def fixture(monkeypatch):
    source = 0xb000
    page = b'RNX\1' + struct.pack('>II', 4, 2) + bytes(6) + b'xx' + bytes(4)
    chain = page + b'FILL' + struct.pack('>HBB', 4, 0x21, 0) + b'END!'
    rom = bytearray(source + len(chain))
    rom[0xa6d8:0xa980] = bytes([0x42]) * (0xa980 - 0xa6d8)
    rom[source:] = chain
    payload = b'code!!!!'
    monkeypatch.setattr(boot_rnx, 'SOURCE', source)
    monkeypatch.setattr(boot_rnx, 'END', len(rom))
    monkeypatch.setattr(boot_rnx, 'OUTPUT_SIZE', len(payload))
    monkeypatch.setattr(boot_rnx, 'CODE_START', 2)
    monkeypatch.setattr(boot_rnx, 'CODE_END', 4)
    monkeypatch.setattr(boot_rnx, 'LOADER_DIGEST', hashlib.sha256(rom[0xa6d8:0xa980]).hexdigest())
    monkeypatch.setattr(boot_rnx, 'OUTPUT_DIGEST', hashlib.sha256(payload).hexdigest())
    monkeypatch.setattr(boot_rnx.rnc, 'unpack', lambda data: b'code')
    return bytes(rom), [(0x6e50, 0x22c70, 0x80006250)], payload


def test_boot_rnx_verifies_expanded_program(monkeypatch):
    rom, ranges, payload = fixture(monkeypatch)
    assert boot_rnx.find(rom, ranges) == ((0x800b82b0, b'code', b'!!!!'),)
    assert boot_gzip.verify_analysis(rom, rom + payload, ranges)
    assert not boot_gzip.verify_analysis(rom, rom + payload[:-1] + b'?', ranges)


def test_boot_rnx_rejects_changed_loader_mapping_page_fill_and_terminator(monkeypatch):
    rom, ranges, _ = fixture(monkeypatch)
    for at in (0xa6d8, boot_rnx.SOURCE, boot_rnx.SOURCE + 20, boot_rnx.END - 1):
        changed = bytearray(rom)
        changed[at] ^= 1
        assert boot_rnx.find(bytes(changed), ranges) is None
    assert boot_rnx.find(rom, [(0x6e50, 0x22c70, 0x80016250)]) is None
    monkeypatch.setattr(boot_rnx.rnc, 'unpack', lambda data: b'bad!')
    assert boot_rnx.find(rom, ranges) is None


def test_boot_rnx_rejects_a_page_with_a_bad_checksum(monkeypatch):
    rom, ranges, _ = fixture(monkeypatch)
    def bad_page(data):
        raise boot_rnx.rnc.RncError('checksum')
    monkeypatch.setattr(boot_rnx.rnc, 'unpack', bad_page)
    assert boot_rnx.find(rom, ranges) is None
