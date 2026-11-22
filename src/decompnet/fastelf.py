"""pyelftools with the symbol table decoded in one pass by a small Cython module, when it is built.

A compiled candidate's relocations ask for their symbols one at a time, and pyelftools parses each through its generic
struct machinery; that was most of the time spent reading an object. `FastELFFile` answers the same questions
(`get_symbol`, `iter_symbols`, the entry's `st_*` fields with pyelftools' own enum names) from a table decoded
once. Anything it can't vouch for (ELF64, a stream that isn't in memory) keeps pyelftools' parser, and without the
built module (packaging/build_fastelf.py) `factory` returns what it was given.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import os
from functools import lru_cache
from pathlib import Path

from elftools.elf import enums
from elftools.elf.elffile import ELFFile
from elftools.elf.sections import Symbol, SymbolTableSection

_BIND = {value: name for name, value in enums.ENUM_ST_INFO_BIND.items() if name != '_default_'}
_TYPE = {value: name for name, value in enums.ENUM_ST_INFO_TYPE.items() if name != '_default_'}
_VISIBILITY = {value: name for name, value in enums.ENUM_ST_VISIBILITY.items() if name != '_default_'}
_SHNDX = {value: name for name, value in enums.ENUM_ST_SHNDX.items() if name != '_default_'}


@lru_cache(maxsize=1)
def _decoder():
    if os.environ.get('DECOMP_FAST_ELF') == '0':
        return None
    for path in sorted((Path(__file__).resolve().parent / 'bin').glob('decomp_fastelf.*')):
        if path.suffix not in importlib.machinery.EXTENSION_SUFFIXES and not any(
                path.name.endswith(suffix) for suffix in importlib.machinery.EXTENSION_SUFFIXES):
            continue
        try:
            spec = importlib.util.spec_from_file_location('decomp_fastelf', path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module.decode_symbols
        except (ImportError, OSError, ValueError):
            continue
    return None


def available() -> bool:
    return _decoder() is not None


class FastSymbolTable(SymbolTableSection):
    def _table(self):
        table = self.__dict__.get('_decoded_table')
        if table is None:
            decode = _decoder()
            raw = self.elffile._file_bytes()
            table = ()
            if decode is not None and raw is not None and self.elffile.elfclass == 32:
                try:
                    table = decode(raw, self['sh_offset'], self.num_symbols(), self['sh_entsize'],
                                   self.elffile.little_endian)
                except ValueError:
                    table = ()
            self.__dict__['_decoded_table'] = table
            self.__dict__['_decoded_symbols'] = {}
            self.__dict__['_strings'] = None
        return table

    def get_symbol(self, n):
        table = self._table()
        if not 0 <= n < len(table):
            return super().get_symbol(n)  # (not decoded, or out of range: pyelftools' answer, error included)
        symbols = self.__dict__['_decoded_symbols']
        symbol = symbols.get(n)
        if symbol is None:
            name_at, value, size, info, other, shndx = table[n]
            entry = {'st_name': name_at, 'st_value': value, 'st_size': size,
                     'st_info': {'bind': _BIND.get(info >> 4, info >> 4), 'type': _TYPE.get(info & 15, info & 15)},
                     'st_other': {'local': (other >> 5) & 7,
                                  'visibility': _VISIBILITY.get(other & 7, other & 7)},
                     'st_shndx': _SHNDX.get(shndx, shndx)}
            symbol = symbols[n] = Symbol(entry, self._name(name_at))
        return symbol

    def _name(self, offset):
        # the string table's own rule: what lies between the offset and the next NUL anywhere in the file
        raw = self.elffile._file_bytes()
        at = self.stringtable['sh_offset'] + offset
        end = raw.find(b'\x00', at)
        if end <= at:
            return ''
        return raw[at:end].decode('utf-8', errors='replace')

    def iter_symbols(self):
        for n in range(self.num_symbols()):
            yield self.get_symbol(n)


class FastELFFile(ELFFile):
    def _file_bytes(self):
        data = self.__dict__.get('_bytes')
        if data is None:
            getvalue = getattr(self.stream, 'getvalue', None)
            data = self.__dict__['_bytes'] = getvalue() if getvalue is not None else b''
        return data or None

    def _make_symbol_table_section(self, section_header, name):
        strtab_section = self._get_linked_strtab_section(section_header['sh_link'])
        return FastSymbolTable(section_header, name, elffile=self, stringtable=strtab_section)


def factory(base):
    """`FastELFFile` in place of plain `ELFFile` when the decoder is built; any other factory is returned as is."""
    return FastELFFile if base is ELFFile and available() else base
