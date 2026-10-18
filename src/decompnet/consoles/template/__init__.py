"""A new console, to copy: `cp -r consoles/template consoles/<id>`, then fill in what's marked FILL IN. It isn't
registered, so the app never sees it until its console is in the list at the end of consoles/__init__.py.

Write in this docstring what the console is (processor, compilers, ROM or PROJECT mode) and one line per file of its
package, as consoles/ps2/__init__.py does: the next person who works on it starts here.

Its demo game is projects/demo-<id> (FILL IN: a homemade game built with the console's compiler; see
projects/demo-ps2/build.py, and never any part of a real game).

The parts below are the connector's (consoles/__init__.py, class Console, says what each one is for). A part this
console doesn't need can go: the default does nothing, or says the console can't do it. CONTRIBUTING.md has the
whole checklist.
"""

from __future__ import annotations

from pathlib import Path

from ...rom import Picked, RomError
from .. import BETA, ROM, Compiler, Console


class Example(Console):
    id = "example"  # FILL IN: what project configs store (keep it once projects exist; add old ids to aliases)
    name = "Example Console"  # what the app shows
    short = "Example"  # where space is short
    status = BETA  # BETA until its conformance test passes and one real game rebuilt byte for byte
    arch = "mips"  # its processor: an id in arch/ (add a module there for a new one)
    big_endian = False
    mode = ROM  # ROM: decompdecomp splits and links it (rom/); PROJECT: a community project judged by objdiff
    layout = "splat"  # what a game started from scratch becomes
    compilers = (
        # FILL IN: a pinned toolchain in toolchains.toml per compiler (PROJECT mode: the versions its build knows)
        Compiler("example-gcc", "Example GCC", "the console's games", guess="GCC"),
    )
    guides = ("mips",)  # guide topics worth reading first: app/guide/ or this package's guides/ folder
    compiler_guide = ""  # the guide every helper gets up front (a file in guides/: guides/example.md)
    m2c = ""  # m2c's target for drafts ("mipsel-gcc"), "" when m2c can't read the code
    ram = (0, 0)  # FILL IN: where its programs' code and data live (words in this range read as pointers)
    demo = "demo-example"  # FILL IN: its folder in projects/ (build.py makes the game and build/demo.json)
    demo_tools = ("example-gcc",)  # what building and importing the demo needs (toolchains, programs on PATH)

    def sniff(self, head: bytes, file) -> bool:
        """FILL IN: how sure this file is this console's game, from its first bytes (a magic number, a header that
        checks out). Be strict: the first console that says yes imports the file."""
        return False

    def read_game(self, file, name: str = "") -> Picked:
        """ROM mode, FILL IN: the program to split, with its header's facts (title, game_code, region, size, sha1,
        compiler_guess). Read a big file in place (see consoles/ps2/__init__.py)."""
        data = Path(file).read_bytes() if isinstance(file, (str, Path)) else bytes(file)
        raise RomError(f"reading {self.name} games isn't written yet ({len(data)} bytes)")

    # Also, as this console needs them (each is explained on Console):
    # default_compiler, default_flags, add_code, find_gp, debug_info, debug_brief, after_import, on_start,
    # asm_reason, up_front_guide, m2c_target; PROJECT mode: create_project, game_info, tool_args, identity,
    # check_rebuild, read_data, upload_name.


EXAMPLE = Example()
