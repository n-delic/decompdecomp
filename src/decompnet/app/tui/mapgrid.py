"""The map's layout and a cell's status: plain functions of (functions, width), no Textual (tests/test_tui_mapgrid.py),
so the TUI's one interesting decision — which functions share a character cell, and what that cell shows — is decided
and checked without a terminal.

The map fills what the screen has free: `width` columns by `rows` lines (ROWS before the screen is laid out), with
the functions in the API's order (address, then unit). layout() decides it (mapview.py draws it):

- More functions than characters (C = rows × width): one character a cell, cell i holding functions
  floor(i × n / C) to floor((i + 1) × n / C) − 1, every cell full (cells()).
- Fewer: one function a cell, each cell a block of characters, as big as lets them all fit (blocks()), so a small
  game fills the screen too. A block is about twice as wide as it's high (a terminal's characters are about twice as
  high as they're wide: it looks square); a column of space after it and, from 3 lines up, a line under it keep
  neighbours apart.

A function's status follows the window's cellColor (static/js/map.js), in its order: matched (polished apart), then
work in progress (a claim on a function not yet matched or set aside), then not C (set aside), tried, open
(status_of). A cell of several functions, which the window never has, shows the status most of them have; a tie goes
by ORDER, which puts tried (work still going on) before not C (set aside for good). The rows
come from /api/map, whose `status` says "working" for a function claimed when the map was built; claims that came or
went since (in /api/live) are laid onto the rows by the caller, as the window's applyClaims does (apply_claims).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

ROWS = 5  # the map's height before the screen has said how many lines it has free
# a cell's status when several tie for most: work done first, work happening now before what it happens to
ORDER = ("polished", "matched", "in progress", "tried", "not C", "open")


def status_of(row) -> str:
    """One function's status from its /api/map row: polished, matched, in progress, not C, tried or open."""
    if row.get("status") == "matched":
        return "polished" if row.get("polished") else "matched"
    if row.get("status") == "working":  # (a set-aside function an agent holds still shows as set aside)
        return "in progress"
    if row.get("status") == "asm":
        return "not C"
    return "tried" if row.get("best") is not None else "open"


def apply_claims(rows: list[dict], claims: list[dict]) -> None:
    """The claims /api/live brought, onto the map's rows in place: a claimed function not matched or set aside is
    "working", and one no longer claimed is open again (static/js/map.js, applyClaims)."""
    working = {c["name"]: c.get("agent") for c in claims}
    for fn in rows:
        if fn.get("status") not in ("matched", "asm"):
            fn["status"] = "working" if fn["name"] in working else "open"
        fn["agent"] = working.get(fn["name"])


def cells(n: int, width: int, rows: int = ROWS) -> list[range]:
    """Which functions each cell holds, in the API's order, on a map of `rows` lines: one each when they fit (the map
    shrinks to match), runs of neighbouring functions when they don't (every cell full)."""
    if n <= 0:
        return []
    rows = max(1, rows)
    if n <= rows * width:
        return [range(i, i + 1) for i in range(n)]
    c = rows * width
    return [range(i * n // c, (i + 1) * n // c) for i in range(c)]


@dataclass(frozen=True)
class Layout:
    """The map on the screen: its cells (which functions each holds), how many go across a line (`cols`: the cursor
    moves up and down by that), and how big each is drawn (`bw` characters wide, `bh` lines high, gaps included)."""

    cells: list[range]
    cols: int
    bw: int = 1
    bh: int = 1


def blocks(n: int, width: int, rows: int) -> tuple[int, int]:
    """The biggest block (width, height in characters) that fits `n` of them on a `width` × `rows` screen, between
    as wide as it's high and three times as wide: of the biggest, the one nearest twice as wide (it looks square)."""
    best = (1, 1)
    best_score = (1, 0)
    for bh in range(1, rows + 1):
        for bw in range(bh, min(width, 3 * bh) + 1):
            if (width // bw) * (rows // bh) >= n and (score := (bw * bh, -abs(bw - 2 * bh))) > best_score:
                best, best_score = (bw, bh), score
    return best


def layout(n: int, width: int, rows: int = ROWS) -> Layout:
    """How the map of `n` functions fills `width` × `rows`: one character a cell holding several functions when they
    don't fit one each, else one function a cell in blocks as big as fit."""
    width, rows = max(1, width), max(1, rows)
    if n > width * rows:
        return Layout(cells(n, width, rows), width)
    bw, bh = blocks(n, width, rows)
    return Layout([range(i, i + 1) for i in range(max(0, n))], width // bw, bw, bh)


def rows_used(n: int, width: int, rows: int = ROWS) -> int:
    """How many lines the map fills (as many as it has cells for; a wide terminal with few functions shows one)."""
    return (len(cells(n, width, rows)) + width - 1) // width


def cell_status(statuses) -> str:
    """What a cell shows: the status most of its functions hold, ties going to the first in ORDER."""
    counts = Counter(statuses)
    return max(counts, key=lambda s: (counts[s], -ORDER.index(s)))


def cell_of(layout: list[range], function: int) -> int:
    """The cell holding function `function` — where the cursor goes after a search, or a resize (the cursor stays on
    its function; which cell that is may change)."""
    for i, held in enumerate(layout):
        if function in held:
            return i
    return 0
