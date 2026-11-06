"""What every link-and-compare judge shares: its verdict, and how much of the game's data a candidate must match.

The judge itself is the processor's (`rom/match.py` for MIPS, `arch/arm.py` for ARM and Thumb): it links a compiled
candidate at the function's address and compares the bytes. This module holds the result both return and the data
comparison both use, so a console can put its own judge behind the connector without importing another console's.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class LinkResult:
    candidate: Any | None  # canonical form, for the diff
    exact: bool
    problems: list[str] = field(default_factory=list)
    # data the candidate defines itself, as placed at the game's copy: section name -> {offset: address}
    placements: dict[str, dict[int, int]] = field(default_factory=dict)


def _text(data: bytes) -> bool:
    return bool(data) and all(32 <= c < 127 or c in (9, 10, 13, 27) or c >= 0x80 for c in data)


def significant(mine: bytes, addr: int, width: int | None, next_start: Callable[[int], int | None]) -> int:
    """How many bytes of a datum the candidate defines (placed at the game's `addr`) must equal the game's.

    `width`: the bytes the code loads from it, when it only loads (a float, a word); that's all that counts.
    Otherwise (its address is used: a string, a table) the whole datum counts except alignment padding: zeros
    at its end where the game already keeps its next datum (`next_start(a)`: the first labelled address >= a),
    or after a C string's terminator. A compiler pads the same data differently next to other data."""
    if width:
        return min(len(mine), width)
    k = len(mine.rstrip(b"\0"))
    if k + 1 >= len(mine):
        return len(mine)
    nxt = next_start(addr + k + 1)  # past the last non-zero byte and one zero (a string's terminator)
    if nxt is not None and nxt < addr + len(mine):
        return nxt - addr
    if _text(mine[:k]):
        return k + 1
    return len(mine)
