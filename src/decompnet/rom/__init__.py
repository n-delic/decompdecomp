"""ROM mode, shared by every console that uses it (N64, PS2): splat splits the game (split.py), the judge links each
compiled candidate at the function's real address and compares the bytes (match.py), and the export writes a tree
that rebuilds the game (export.py, export_build.py). What differs per console comes from its package in consoles/
(how to read its file, which compilers, more code to split), through the connector (consoles/__init__.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field


class RomError(ValueError):
    """A game file this app can't split, with a sentence for the user."""


@dataclass
class Picked:
    """The program a ROM-mode console read from the user's file, ready for splat (Console.read_game)."""
    platform: str  # the console's id
    data: bytes  # what we split: a z64 ROM, a PS2 ELF
    filename: str  # name to store it under in the project folder
    info: dict  # what its header says: title, game_code, region, size, sha1, compiler_guess...
    big_endian: bool
    overlays: list = field(default_factory=list)  # (name, bytes, load address, code offset): code in other files
