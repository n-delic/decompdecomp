"""Imported object caches must never retain stale symbol classifications."""
import os
from io import BytesIO

from elftools.elf.elffile import ELFFile

from decompnet.app.forkengine import _data_symbols
from tests.mips_elf_fixture import linked_object


def object_symbol(name):
    data = bytearray(linked_object(name, 0, bytes(8)))
    elf = ELFFile(BytesIO(data))
    table = elf.get_section_by_name('.symtab')
    data[table['sh_offset'] + 16 + 12] = 0x11  # a global STT_OBJECT, not a function
    return bytes(data)


def test_data_symbol_cache_detects_same_size_rewrite(tmp_path, monkeypatch):
    from elftools.elf import elffile

    target = tmp_path/'target.o'
    target.write_bytes(object_symbol('item'))
    parsed = []
    original = elffile.ELFFile
    def counted(stream):
        parsed.append(True)
        return original(stream)
    monkeypatch.setattr(elffile, 'ELFFile', counted)
    assert _data_symbols(target) == {'item'}
    assert _data_symbols(target) == {'item'} and len(parsed) == 1
    names = _data_symbols(target)
    names.clear()
    assert _data_symbols(target) == {'item'} and len(parsed) == 1
    stat = target.stat()
    target.write_bytes(object_symbol('next'))
    os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert target.stat().st_size == stat.st_size
    assert _data_symbols(target) == {'next'} and len(parsed) == 2
