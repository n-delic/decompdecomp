"""Game Boy Advance: the ARM7TDMI (ARM and Thumb, little-endian), agbcc (pret's rebuild of the GCC 2.95-era compiler
most GBA games used), ROM mode.

- header.py: the cartridge's header: the ARM branch at 0, the fixed 0x96 at 0xB2 and the complement checksum at
  0xBD; its title, game code, region and version.
- guides/gba.md: what agents read about matching GBA code, `guides/agbcc.md` about the compiler's habits.

Its demo game is projects/demo-gba (the Demo Quest game built with agbcc, with a blank logo and a computed header
checksum; its built ROM isn't committed).

- split.py: luvdis (with luvdis_run.py and candidates.py) finds the functions, and names the code nothing calls
  directly; a function's assembly, bytes, Thumb/ARM mode and references come from it, and it cuts/adds functions at
  Thumb's 2-byte boundaries.
- export.py, export_build.py: luvdis assembly for the functions still in assembly, the matched C built with agbcc, and
  a tree whose build rebuilds the cartridge byte for byte.
- The ARM/Thumb judge is arch/arm.py's (`ArmTarget`, `link`): each candidate is linked at its ROM address and compared
  byte for byte.

Not yet (ROADMAP.md): old_agbcc/agbcc_arm as selectable compilers and compiler ranges, ARM look-alikes/twins, the
permuter's GBA weights, C++ classes and struct layouts, and GBA debug symbols.
"""

from __future__ import annotations

from pathlib import Path

from ...rom import Picked, RomError
from .. import BETA, EXPORT, ROM, Compiler
from ..arm import ArmConsole

MAX_SIZE = 32 << 20  # the cartridge address space (0x08000000-0x0A000000)


class GameBoyAdvance(ArmConsole):
    id = "gba"
    name = "Game Boy Advance"
    short = "GBA"
    status = BETA  # its demo builds; no real game has been split and rebuilt byte for byte yet
    arch = "arm"
    big_endian = False
    mode = ROM
    layout = "luvdis"
    compilers = (
        Compiler("agbcc", "agbcc", "most Game Boy Advance games (pret's rebuild of GCC 2.95)", guess="AGBCC"),
    )
    kits = ("own-gba",)  # another build of one of its compilers, the user's (kits/owncompiler.py)
    guides = ("arm", "gba")
    compiler_guide = "agbcc"
    m2c = "gba-gcc"  # m2c's GBA target, whose compiler defaults to gcc (seeds.m2c_target adds "-c")
    # ROM (0x08000000), EWRAM and IWRAM: words in this range are read as addresses
    ram = (0x02000000, 0x0A000000)
    demo = "demo-gba"
    demo_tools = ("agbcc", "arm-gnu", "luvdis")
    own_features = frozenset({EXPORT})
    as_flags = ("-mcpu=arm7tdmi",)
    image_ext = ".gba"
    asm_preamble = '.syntax unified\n.text\n'

    def sniff(self, head: bytes, file) -> bool:
        from . import header

        return header.is_gba(head)

    def read_game(self, file, name: str = "") -> Picked:
        """The cartridge to split; a GBA ROM is at most 32 MiB, so it's read whole."""
        data = Path(file).read_bytes() if isinstance(file, (str, Path)) else bytes(file)
        if len(data) > MAX_SIZE:
            raise RomError(f"this file is {len(data) // (1 << 20)} MiB, larger than any Game Boy Advance cartridge "
                           "(32 MiB): is it another console's ROM or a padded dump?")
        from . import header

        return Picked(self.id, data, "rom.gba", header.read_info(data), False)

    def split_game(self, workdir: Path, picked: Picked, *, symbols: list[str] | None = None, log=print):
        """luvdis finds the functions; the candidate pass adds the code nothing calls directly (split.py)."""
        from . import split

        return split.split_result(workdir / picked.filename, workdir, log=log)

    def function_from_row(self, row):
        from .split import function_from_row

        return function_from_row(row)

    def parse_function(self, name: str, text: str, segment: str):
        from .split import parse_function

        return parse_function(name, text, segment)

    def split_function(self, fn, at: int, new_name: str):
        from .split import split_function

        return split_function(fn, at, new_name)

    def function_at(self, rom: bytes, addr: int, limit: int, name: str):
        from .split import function_at

        return function_at(rom, addr, limit, name)

    def join_saved_functions(self, rows: list[dict], start: int, end: int, read):
        from .split import join_saved_functions

        return join_saved_functions(rows, start, end, read)

    def export_project(self, net, slug: str, out: Path, log=print) -> dict:
        from .export import export_gba

        return export_gba(net, slug, out, log=log)


GBA = GameBoyAdvance()
