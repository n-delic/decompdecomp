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

    def data_bytes(self, fp, addr: int, length: int) -> bytes | None:
        from . import scratch

        return scratch.data_bytes(fp.root, fp.manifest or {}, addr, length)

    # The game's names are its addresses (func_0041B470, D_0066DDA4): objdiff pairs a function with the original's by
    # the symbol tools/names.py gives it, and the report keeps that symbol. A real name is kept by address in
    # functions.json's "names", which names.py reads too, so a renamed function still pairs (and so do its callers).
    renames_in_report = False

    def rename_symbol(self, fp, version: str, old: str, new: str) -> dict:
        """functions.json's "names": `old`'s address (func_0041B470, _func_0041B470@8, D_0066DDA4, or a real name
        given before) gets the name `new`."""
        import json

        from ...forks.project import ForkError

        path = fp.root / "config" / version / "functions.json"
        text = path.read_text(encoding="utf-8")
        info = json.loads(text)
        names = dict(info.get("names") or {})
        key = _address_key(old) or next((k for k, v in names.items() if v == old), None)
        functions = {f"func_{fn['addr']:08X}" for fns in info["units"].values() for fn in fns}
        if key is None or (key.startswith("func_") and key not in functions):
            raise ForkError(f"{old} isn't one of the game's functions or data (func_<address>, D_<address>)")
        if new in names.values() or _address_key(new):
            raise ForkError(f"{new} names another of the game's symbols already: pick another name")
        names[key] = new
        path.write_text(json.dumps({**info, "names": dict(sorted(names.items()))}, indent=1), encoding="utf-8")
        return {path: text}

    def real_names(self, fp, version: str) -> dict[str, str]:
        """(a function's report name is its entry's in functions.json: the target objects are cut out with it)"""
        import json

        try:
            info = json.loads((fp.root / "config" / version / "functions.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        names = info.get("names") or {}
        return {fn["name"]: names[key] for fns in info["units"].values() for fn in fns
                if (key := f"func_{fn['addr']:08X}") in names}

    def rename_pattern(self, old: str) -> str:
        """func_0041B470 however a source spells it (a report's _func_0041B470@8 is func_0041B470 there, a method's
        Town::func_0041B470 too); a real name given before as itself."""
        key = _address_key(old)
        return rf"\b_*{key}\b" if key else super().rename_pattern(old)


def _address_key(name: str) -> str | None:
    """func_XXXXXXXX / D_XXXXXXXX (upper case address) in a name, as tools/names.py keys them; None without one."""
    import re

    m = re.search(r"\b_*(func|D)_([0-9A-Fa-f]{8})(?![0-9A-Fa-f])", name)
    return f"{m.group(1)}_{m.group(2).upper()}" if m else None


PC = WindowsPC()
