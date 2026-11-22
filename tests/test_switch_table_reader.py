from types import SimpleNamespace

import pytest

from decompnet.app import switch_tables


@pytest.mark.parametrize('byteorder', ['big', 'little'])
def test_table_scan_needs_no_rom_reader_for_a_return_only_function(byteorder):
    def open_rom(*args):
        pytest.fail('A return through ra cannot dispatch a jump table')

    net = SimpleNamespace(is_rom=lambda _: True, reader=open_rom)
    target = bytes(24) + (0x03E00008).to_bytes(4, byteorder) + bytes(4)
    proj = {'arch': 'mips', 'big_endian': byteorder == 'big'}
    fn = {'target': target, 'vram': 0x80001000, 'asm': ''}
    assert switch_tables.read(net, proj, fn) == []
