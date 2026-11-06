"""The local engine: ROM projects on top of the coordination core.

Network (claims, attempts, best attempts, notes, a regression-tested shared header) already does the
bookkeeping several agents need to work on one codebase. A ROM project adds:

- targets taken straight from the ROM (splat), matched by linking the candidate at the function's
  address (rom.match), so a match is byte-identical code;
- a symbol table, so agents can name things (rename_symbol) without breaking matches;
- tools for what ROMs need: split a function the disassembler merged, read data, see who calls what.

Everything stays on this computer.

The engine is one class, RomNetwork, put together from a module per topic:
- core.py: the ground floor (a project's rows, its ROM and symbols, finding functions, local agents)
- importing.py: importing a game
- judging.py: the judge, claims and attempts
- briefs.py: what agents read about a function
- layouts.py: struct layouts guessed from the code (guess_struct, and the brief's lines on them)
- compilers.py: which compiler builds which part of a game, suggestions, the compiler map
- editing.py: changing the project (names, the header, notes, splits, function ends)
- rooms.py: rooms (collab.py), the engine's side
- gui.py: what the GUI shows, and the export
- autopilot.py: autopilot, and what a match sets off

app/progress.py keeps ROM progress files authoritative; core.py checks them before serving a project and
judging.py rejects compiles made against progress that has since changed.
"""

from __future__ import annotations

from .autopilot import AutopilotMixin
from .briefs import BriefMixin
from .common import COMPILERS, TOOLS, parse_ranges
from .compilers import CompilerMixin
from .core import Core
from .editing import EditMixin
from .gui import GuiMixin
from .importing import ImportMixin
from .judging import JudgeMixin
from .layouts import LayoutMixin
from .rooms import RoomMixin

__all__ = ["RomNetwork", "COMPILERS", "TOOLS", "parse_ranges"]


class RomNetwork(ImportMixin, JudgeMixin, BriefMixin, LayoutMixin, CompilerMixin, EditMixin, RoomMixin, GuiMixin,
                 AutopilotMixin, Core):
    """A ROM project engine: Network's claims, attempts and notes, for games split from their ROM (see above)."""
