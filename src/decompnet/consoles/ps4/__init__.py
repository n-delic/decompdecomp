"""PS4 decrypted x86-64 ELF programs, imported as strict objdiff projects.

`scratch.py` reads loader segments, maps functions, relocations and data, and creates
projects from `project_template/`. `guides/ps4.md` describes the public compiler and
matching workflow. Encrypted SELF files and PS4 packages are not accepted.
"""

from __future__ import annotations

import re
from pathlib import Path

from ...forks.project import ForkError
from .. import BETA, PROJECT, Compiler, Console

# the EBoot's e_type: Sony's own ELF types, outside the standard ones
ET_SCE_EXEC, ET_SCE_DYNEXEC = 0xFE10, 0xFE04
EM_X86_64 = 62


class PS4(Console):
    id = "ps4"
    name = "PlayStation 4"
    short = "PS4"
    aliases = ("orbis",)
    status = BETA
    arch = "x86_64"
    big_endian = False
    mode = PROJECT
    layout = "Ghidra + objdiff"
    compilers = (Compiler("clang-5.0.2", "Clang 5.0.2 (Orbis era)",
                          "PS4-era x86-64 programs; verify the compiler for each game"),)
    packs = (("clang-5.0.2", "public compiler for PS4-era ELF projects (Linux x86-64)"),
             ("ghidra", "finds functions and provides machine drafts (needs Java 21+)"))
    guides = ("ps4",)
    compiler_guide = "ps4"
    m2c = ""  # m2c reads MIPS only
    ram = (0x0, 0x40000000)  # the image is linked at 0 and loaded wherever; words this low read as addresses
    demo = "demo-ps4"
    demo_tools = ("clang-5.0.2", "objdiff-cli")
    compiler_family = ""  # decomp-permuter has no clang weights; use its supported base set

    def sniff(self, head: bytes, file) -> bool:
        """An x86-64 ELF, of one of Sony's executable types (an eboot.elf, not a library)."""
        return (len(head) >= 64 and head[:4] == b"\x7fELF" and head[4] == 2 and head[5] == 1
                and int.from_bytes(head[16:18], "little") in (ET_SCE_EXEC, ET_SCE_DYNEXEC)
                and int.from_bytes(head[18:20], "little") == EM_X86_64)

    def upload_name(self, data: bytes) -> str:
        return "eboot.elf"

    def create_project(self, root: Path, game: Path, *, compiler: str | None = None, log=print) -> dict:
        from . import scratch

        return scratch.create(root, game, compiler=compiler, log=log)

    def game_info(self, game: Path) -> dict:
        from . import scratch

        return scratch.game_info(game)

    def tool_args(self) -> list[str]:
        from . import scratch

        clang = scratch.clang_path()
        if clang is None:
            raise ForkError("no clang 5.0.2 for PS4 matching: install the pinned "
                           "build, then try again")
        return ["--clang", str(clang)]

    def adapt_draft(self, text: str) -> str:
        """Ghidra's C in this project's type names. It writes `undefined8` and `undefined4` where common.h has `u64` and
        `u32`, and an agent handed those spends a turn translating instead of matching; its overlap warnings go too."""
        for theirs, ours in (("undefined8", "u64"), ("undefined4", "u32"), ("undefined2", "u16"),
                             ("undefined1", "u8"), ("undefined", "u8")):
            text = re.sub(rf"\b{theirs}\b", ours, text)
        return "\n".join(line for line in text.splitlines() if "WARNING: Globals starting with" not in line
                          and "WARNING: Removing unreachable" not in line)

    def rename_symbol(self, fp, version: str, old: str, new: str) -> dict:
        """A function's real name, in the config the delinker names the target object's symbols from."""
        from . import scratch

        return scratch.rename(fp, version, old, new)

    def read_data(self, fp, name: str, length: int) -> str | None:
        from . import scratch

        return scratch.read_data(fp.root, fp.manifest or {}, name, length)

    def data_bytes(self, fp, addr: int, length: int) -> bytes | None:
        from . import scratch

        return scratch.data_bytes(fp.root, fp.manifest or {}, addr, length)


PS4_CONSOLE = PS4()
