"""Recover a compressed boot program only with independent loader evidence."""
import struct

import pytest

from decompnet.consoles.n64 import boot_gzip, boot_rnc2, code


@pytest.fixture
def program(monkeypatch):
    rom = bytearray(0x3000)
    struct.pack_into('>2I', rom, 0x1000, 0x3c080000, 0x25082000)
    rom[0x2000:0x2004] = b'RNC\x02'
    data = struct.pack('>6I', 0x27bdffe8, 0, 0, 0x03e00008, 0, 0x12345678)
    arg = dict(kind='constant', value=0x80002000)
    calls = [dict(kind='call', target=0x80002004, args=[arg]),
             dict(kind='call', target=0x80002008, args=[])]
    monkeypatch.setattr(boot_rnc2, 'read', lambda *_: {'events': calls})
    monkeypatch.setattr(boot_rnc2, 'unpack_m2', lambda *_args, **_kwargs: data)
    monkeypatch.setattr(code, '_looks_like_code', lambda _: True)
    return rom, data, calls, [(0x1000, 0x1100, 0x80000400)]


def test_boot_source_output_and_calls_identify_the_program(program):
    rom, data, _, ranges = program
    found = boot_rnc2.find(rom, ranges)
    assert found == ((0x80002000, data[:20], data[20:]),)
    assert boot_gzip.verify_analysis(rom, rom+data, ranges)
    assert not boot_gzip.verify_analysis(rom, rom+data[:-1]+b'\0', ranges)


@pytest.mark.parametrize('case', ['wrong_source', 'one_call', 'overlap', 'bad_checksum'])
def test_weak_or_invalid_evidence_is_not_a_boot_program(program, monkeypatch, case):
    rom, _, calls, ranges = program
    if case == 'wrong_source':
        rom[0x2000:0x2004] = b'BAD!'
    if case == 'one_call':
        calls.pop()
    if case == 'overlap':
        calls[0]['args'][0]['value'] = 0x80000400
        calls[0]['target'], calls[1]['target'] = 0x80000404, 0x80000408
    if case == 'bad_checksum':
        def invalid(*_args, **_kwargs):
            raise boot_rnc2.RncError('bad checksum')
        monkeypatch.setattr(boot_rnc2, 'unpack_m2', invalid)
    assert boot_rnc2.find(rom, ranges) is None
