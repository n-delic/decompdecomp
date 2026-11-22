from types import SimpleNamespace

from decompnet import elfobj


class _Sym:
    def __init__(self, name, value, size, kind='STT_FUNC', bind='STB_GLOBAL', shndx=1):
        self.name = name
        self.fields = {'st_value': value, 'st_size': size, 'st_shndx': shndx,
                       'st_info': {'type': kind, 'bind': bind}}

    def __getitem__(self, key):
        return self.fields[key]


class _Table:
    def __init__(self, symbols):
        self.symbols = symbols
        self.reads = 0
        self.elffile = SimpleNamespace()

    def iter_symbols(self):
        self.reads += 1
        return iter(self.symbols)


def test_symbol_at_reads_the_symbol_table_once_and_keeps_its_choices(monkeypatch):
    monkeypatch.setattr(elfobj, 'metadata_functions', lambda elf: {})
    table = _Table([_Sym('gcc2_compiled.', 0, 0, 'STT_NOTYPE'), _Sym('.L1', 4, 0, 'STT_NOTYPE'),
                    _Sym('first', 0, 0), _Sym('label', 8, 0, 'STT_NOTYPE', 'STB_LOCAL'),
                    _Sym('second', 8, 0), _Sym('other_section', 0, 16, shndx=2)])
    # size 0 runs to the next symbol; an exact start beats a covering symbol; sections stay apart
    assert elfobj.symbol_at(table, 1, 4) == ('first', 4)
    assert elfobj.symbol_at(table, 1, 8) == ('second', 0)
    assert elfobj.symbol_at(table, 1, 12) == ('second', 4)
    assert elfobj.symbol_at(table, 2, 6) == ('other_section', 6)
    assert elfobj.symbol_at(table, 3, 0) is None
    assert table.reads == 1
