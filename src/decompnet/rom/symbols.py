"""Names for addresses in a ROM project.

Every address splat found has an automatic name that carries its address (func_80001234,
D_80012340, jtbl_...). Users and agents rename them as they learn what things are; the table
maps every name, automatic or chosen, back to its address. Matching works on addresses, so a
rename never breaks a match.
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass

# Automatic names spell their address: splat/spimdisasm (func_, D_...), luvdis and pret (sub_, gUnknown_), IDA style.
# an automatic name says its address. splat writes one below 0x10000000 without leading zeros (an N64 segment's data:
# D_B0069D8 is 0x0B0069D8): such a name didn't resolve, and 255 functions of an N64 game that load one couldn't match
# (a number in their C is lui+ori, the game's symbol lui+addiu)
AUTO_NAME = re.compile(r"^(?:func|D|B|jtbl|L|RO|STR|FLT|DBL|bss|data|sub|gUnknown|unk|off|dword|word|byte|loc)_([0-9A-Fa-f]{1,8})$")
IDENT = re.compile(r"^[A-Za-z_]\w*$")


@dataclass
class Symbol:
    name: str
    address: int
    size: int = 0
    kind: str = "data"  # func | data | string | jtbl | bss


class SymbolTable:
    def __init__(self, symbols: list[Symbol] | None = None):
        self.by_name: dict[str, Symbol] = {}
        self.by_addr: dict[int, Symbol] = {}
        self.starts: list[int] = []  # every symbol's address, sorted (for bisect)
        for s in symbols or []:
            self.add(s, reindex=False)
        self.reindex_symbols()

    def reindex_symbols(self) -> None:
        self.starts = sorted(self.by_addr)

    def add(self, sym: Symbol, reindex: bool = True) -> None:
        old = self.by_addr.get(sym.address)
        if old is not None and old.name != sym.name:
            self.by_name.pop(old.name, None)
        self.by_name[sym.name] = sym
        self.by_addr[sym.address] = sym
        if reindex:
            self.reindex_symbols()

    def rename(self, old: str, new: str) -> Symbol:
        sym = self.by_name.pop(old)
        sym.name = new
        self.by_name[new] = sym
        return sym

    def resolve(self, name: str) -> int | None:
        """Address for a name: a known symbol, or an automatic name like D_80012340."""
        sym = self.by_name.get(name)
        if sym is not None:
            return sym.address
        m = AUTO_NAME.match(name)
        return int(m.group(1), 16) if m else None

    def name_at(self, address: int) -> tuple[str, int]:
        """(name, offset) for an address: an exact symbol, the symbol that contains it, or an automatic name."""
        sym = self.by_addr.get(address)
        if sym is not None:
            return sym.name, 0
        i = bisect.bisect_right(self.starts, address) - 1
        if i >= 0:
            start = self.starts[i]
            sym = self.by_addr[start]
            if address < start + sym.size:
                return sym.name, address - start
        return f"D_{address:08X}", 0

    def next_after(self, address: int) -> int | None:
        """The first symbol's address above `address` (where the ROM's next datum starts, as splat labels it)."""
        i = bisect.bisect_right(self.starts, address)
        return self.starts[i] if i < len(self.starts) else None

    def label(self, address: int) -> str:
        name, off = self.name_at(address)
        return f"{name}+0x{off:X}" if off else name
