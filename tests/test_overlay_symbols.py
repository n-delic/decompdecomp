"""Room checkouts can shorten overlay suffixes without changing the address they spell."""
import pytest

from decompnet.rom.symbols import Symbol, SymbolTable


@pytest.mark.parametrize('prefix',['D','func','jtbl','RO','bss'])
@pytest.mark.parametrize('suffix',['overlay_001','ovl_01','ovl_001'])
def test_overlay_automatic_names_resolve_to_their_encoded_address(prefix,suffix):
    assert SymbolTable().resolve(f'{prefix}_80123456_{suffix}')==0x80123456


def test_explicit_names_take_precedence_and_malformed_suffixes_are_not_guessed():
    symbols=SymbolTable([Symbol('D_80123456_ovl_01',0x80123460)])
    assert symbols.resolve('D_80123456_ovl_01')==0x80123460
    for suffix in ('ovl_room','ovl_00001','overlay_01','ovl_01_other'):
        assert symbols.resolve('D_80123456_'+suffix) is None
