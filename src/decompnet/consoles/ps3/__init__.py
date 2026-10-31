"""PlayStation 3: the Cell's PowerPC core (the PPU: 64-bit PowerPC, big-endian, ELF64, function descriptors and a TOC),
ROM mode, from the user's own decrypted `EBOOT.ELF`.

- elf.py: the reader: is it a PS3 program, its program headers (the code is the lowest segment with a payload), the
  module descriptor at `e_entry` (a retail program lists its functions there), the ABI, `.comment`'s compiler stamp,
  and the refusal of a retail SELF (decompdecomp never decrypts a game: the user does that with their own tools).
- split.py: the splitter (no tool reads a linked PowerPC64 program): descriptor functions, `bl` targets and
  prologues; assembly with calls and data named by address; the functions' references the judge compares with.
- guides/ps3.md: what agents read about matching PS3 code.
- The judge is arch/ppc64.py's (`PpcTarget`, `link`): descriptors, the TOC and the linker's choices are judged by what
  the code refers to, then the game's bytes are compared. See its docstring for what a retail program must still
  confirm.

Its demo game is projects/demo-ps3 (Demo Quest built with the free ps3dev GCC; the free compiler makes the usual
64-bit ELFv1 program, where Sony's has 32-bit pointers and 8-byte descriptors: both are read).

Not yet (ROADMAP.md): Sony's period GCC and SN's compiler, the SDK kit, imports named by NID and kernel calls by number,
SPU programs and PRX modules, a rebuildable export, and C++ classes, struct layouts and the compiler map.
"""

from __future__ import annotations

import json
from pathlib import Path

from ...rom import Picked, RomError
from .. import BETA, ROM, Compiler, Console


class PlayStation3(Console):
    id = "ps3"
    name = "PlayStation 3"
    short = "PS3"
    status = BETA  # its demo builds and judges; no real game has been split and matched yet
    arch = "ppc64"
    big_endian = True
    mode = ROM
    layout = "descriptor + prologues"
    compilers = (
        Compiler("ps3dev-gcc", "PS3 GCC (ps3dev)", "homebrew and the demo game (the free PPU GCC; not the period "
                 "compiler retail games used)", guess="PS3DEV"),
    )
    guides = ("ps3",)
    m2c = ""  # m2c has no 64-bit PowerPC target
    ram = (0x00010000, 0xD0000000)  # a program's code and data live in the first 256 MiB; the rest is video and I/O
    demo = "demo-ps3"
    demo_tools = ("ps3dev-gcc",)
    as_flags = ("-mppc64", "-be")
    image_ext = ".elf"
    asm_preamble = ".text\n"

    def sniff(self, head: bytes, file) -> bool:
        from . import elf

        return elf.looks_like_self(head) or elf.is_ps3_elf(head)

    def read_game(self, file, name: str = "") -> Picked:
        """The decrypted ELF to split; a SELF is refused with what to do about it."""
        from . import elf

        if isinstance(file, (str, Path)):
            name = name or Path(file).name
            file = Path(file).read_bytes()
        program, info = elf.read_elf(bytes(file), name)
        return Picked(self.id, program, "game.elf", info, True)

    def default_flags(self, toolchain: str, gp: int) -> list[str]:
        """Sony's PPU GCC aligns functions to 4 bytes (-falign-functions=4); pointers are 32 bits by the compiler's
        default (-mlp32), so no flag asks for it. No $gp: the PS3 has no small data."""
        flags = super().default_flags(toolchain, gp)
        return flags + [f for f in ("-falign-functions=4",) if f not in flags]

    def split_game(self, workdir: Path, picked: Picked, *, symbols: list[str] | None = None, log=print):
        from . import split

        return split.split_result(picked.data, log=log)

    def function_insns(self, fn) -> int:
        return fn.n_insns if hasattr(fn, "n_insns") else fn.get("n_insns") or 1

    def function_asm(self, fn) -> str:
        return fn.asm if hasattr(fn, "asm") else fn["asm"]

    def function_meta(self, fn) -> dict:
        """What a function's row keeps besides its bytes: the pointer width and TOC base its references were read with."""
        from ...arch.ppc64 import slots_of

        return {"ptr": fn.ptr, "toc": fn.toc, "slots": slots_of(fn.refs)}

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

    def target_from_split(self, fn, resolve, big_endian: bool, gp: int):
        from ...arch.ppc64 import PpcTarget

        return PpcTarget(fn.name, fn.vram, fn.code, fn.refs, fn.ptr, fn.toc)

    def target_from_row(self, fn: dict, cfg: dict):
        from ...arch.ppc64 import PpcTarget

        meta = json.loads(fn["meta"] or "{}")
        return PpcTarget(fn["name"], fn["vram"], fn["target"], PpcTarget.refs_from_json(json.loads(fn["relocs"] or "[]"), meta.get("slots")),
                         meta.get("ptr", 4), meta.get("toc", 0))

    def target_object(self, fn: dict, cfg: dict, symbols):
        from ...arch.ppc64 import canonical_target

        return canonical_target(self.target_from_row(fn, cfg))

    def link_function(self, obj: bytes, name: str, target, symbols, read):
        from ...arch import ppc64

        return ppc64.link(obj, name, target, symbols, read)

    def export_project(self, net, slug: str, out: Path, log=print) -> dict:
        raise RomError("a PlayStation 3 project can't be exported as a rebuildable tree yet (ROADMAP.md): the matched "
                       "C is saved with the project")


PS3 = PlayStation3()
