"""The symbol table's names (rom/symbols.py): an automatic name says its address."""

from decompnet.rom.symbols import Symbol, SymbolTable


def test_automatic_names_resolve_with_or_without_leading_zeros():
    """splat names data below 0x10000000 without leading zeros (an N64 segment's: D_B0069D8, D_145670). The judge
    didn't know those names, and C that loads one couldn't match any other way."""
    symbols = SymbolTable([Symbol("gPlayers", 0x80165D58)])
    assert symbols.resolve("D_80012340") == 0x80012340
    assert symbols.resolve("D_B0069D8") == 0x0B0069D8 and symbols.resolve("D_0B0069D8") == 0x0B0069D8
    assert symbols.resolve("D_145670") == 0x145670
    assert symbols.resolve("gPlayers") == 0x80165D58 and symbols.resolve("D_123456789") is None
    assert symbols.resolve("player_count") is None  # not an automatic name: unknown
