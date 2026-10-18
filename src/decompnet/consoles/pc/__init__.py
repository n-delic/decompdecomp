"""Windows PC games: 32-bit x86 programs (.exe) built with Microsoft Visual C++ 6.0, 7.0 or 7.1, PROJECT mode. Only
the game is a Windows program: decompdecomp runs on Linux, and so do the compilers (through wibo).

- scratch.py: a project for the user's .exe: Ghidra finds its functions, decompdecomp cuts each one out of the
  program into a COFF object of its own (the target objdiff compares with), and which Visual C++ built it comes
  from the program's Rich header.
- project_template/: what that project starts as (tools/configure.py writes its ninja build).
- guides/x86.md: what agents read about x86 and matching Visual C++'s code.

Its demo game is projects/demo-pc (the Demo Quest game built with Visual C++ 6.0). Older projects' configs say
"win32" (decomp.dev's id): that's this console too.
"""

from __future__ import annotations

from pathlib import Path

from .. import BETA, PROJECT, Compiler, Console

MSVC_VERSIONS = ["MSVC/6.0", "MSVC/7.0", "MSVC/7.1"]  # the `msvc` pack


class WindowsPC(Console):
    id = "pc"
    name = "Windows PC"
    short = "PC"
    aliases = ("win32",)
    status = BETA
    arch = "x86"
    mode = PROJECT
    layout = "Ghidra + objdiff"
    compilers = tuple(Compiler(v, f"Visual C++ {v.removeprefix('MSVC/')}") for v in MSVC_VERSIONS)
    packs = (("msvc", "Visual C++ 6.0, 7.0 and 7.1, the compilers of PC games (proprietary)"),
             ("ghidra", "finds a PC game's functions, and drafts for platforms m2c can't read (540 MB, needs Java 21+)"))
    guides = ("x86", "cpp")
    compiler_guide = "x86"
    ram = (0x00400000, 0x80000000)  # a 32-bit program's address space above its usual image base
    demo = "demo-pc"
    demo_tools = ("msvc", "objdiff-cli", "wibo", "ghidra", "java")  # Ghidra needs Java 21 or newer

    def sniff(self, head: bytes, file) -> bool:
        from .scratch import is_pe

        return is_pe(head)

    def upload_name(self, data: bytes) -> str:
        return "game.exe"

    def default_compiler(self, info: dict, splat_guess: str) -> str:
        return ""  # the program's Rich header says which Visual C++ built it (scratch.guess_compiler)

    def create_project(self, root: Path, game: Path, *, compiler: str | None = None, log=print) -> dict:
        from . import scratch

        return scratch.create(root, game, compiler=compiler, log=log)

    def game_info(self, game: Path) -> dict:
        from . import scratch

        return scratch.game_info(game)

    def tool_args(self) -> list[str]:
        from . import scratch

        return scratch.tool_args()

    def read_data(self, fp, name: str, length: int) -> str | None:
        """The program's data isn't in the target objects: it's read from the user's copy."""
        from . import scratch

        return scratch.read_data(fp.root, fp.manifest or {}, name, length)


PC = WindowsPC()
