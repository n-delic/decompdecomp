"""Nintendo 64: the VR4300's MIPS (big-endian), KMC GCC 2.7.2 and IDO 5.3 / 7.1, ROM mode.

- rom.py: the ROM file: its byte order (.z64, .v64, .n64), header and a first guess at the compiler.
- code.py: code splat's own look misses: segments the game loads later, and code kept compressed in the ROM.
- guides/: what agents read about the N64 (n64.md) and its compilers (gcc-kmc.md, ido.md).

Its demo game is projects/demo-rom (the Demo Quest game in projects/demo-n64/original, built with KMC GCC).
"""

from __future__ import annotations

from pathlib import Path

from ...rom import Picked, RomError
from .. import EXPORT, RECOMMENDED, ROM, Compiler, Console


class Nintendo64(Console):
    id = "n64"
    name = "Nintendo 64"
    short = "N64"
    status = RECOMMENDED  # tested on real games (Super Mario 64, GoldenEye, Perfect Dark), rebuilt byte for byte
    arch = "mips"
    big_endian = True
    mode = ROM
    layout = "splat"
    compilers = (
        Compiler("gcc-2.7.2-kmc", "KMC GCC 2.7.2", "many third-party N64 games", guess="KMC"),
        Compiler("ido-7.1", "IDO 7.1", "most later first-party N64 games", guess="IDO"),
        Compiler("ido-5.3", "IDO 5.3", "early N64 games (1996-97)"),
    )
    kits = ("own-n64",)  # another build of one of its compilers, the user's (kits/owncompiler.py)
    guides = ("mips",)
    m2c = "mips-gcc"
    ram = (0x80000400, 0x80800000)  # KSEG0 above the exception vectors, up to the Expansion Pak's 8 MiB
    demo = "demo-rom"
    demo_tools = ("gcc-2.7.2-kmc", "binutils-mips", "ld.lld", "llvm-objcopy")
    own_features = frozenset({EXPORT})
    as_flags = ("-EB", "-march=vr4300", "-mabi=32")
    image_ext = ".z64"
    asm_preamble = '.include "macro.inc"\n.set noat\n.set noreorder\n.set gp=64\n.section .text, "ax"\n'

    def sniff(self, head: bytes, file) -> bool:
        from .rom import MAGIC

        return head[:4] in MAGIC

    def identity(self, path: Path) -> str | None:
        from .rom import z64_file_sha1

        try:
            result = z64_file_sha1(path)
        except RomError:  # an incomplete cartridge is not an N64 game identity
            return None
        return result[0] if result else None

    def library_candidate(self, path: Path, size: int) -> bool:
        """Skip files outside cartridge sizes and without an N64 magic before hashing."""
        from .rom import MAGIC, MAX_SIZE, MIN_SIZE

        if not MIN_SIZE <= size <= MAX_SIZE or size % 4:
            return False
        with path.open('rb') as stream:
            return stream.read(4) in MAGIC

    def fork_game_help(self, target: str, sha1: str | None) -> str | None:
        if not target.lower().endswith(".z64"):
            return None
        check = ("Its normalized SHA-1 must match this project's hash."
                 if sha1 else "The project's build still checks that it is the right game.")
        return ("Supply your N64 ROM in .z64, .v64 or .n64 byte order. decompdecomp saves a .z64 copy for the "
                f"project. {check}")

    def prepare_fork_game(self, src: Path, dest: Path, sha1: str | None, *, move: bool = False) -> bool:
        from .rom import copy_z64_file, z64_file_sha1

        if dest.suffix.lower() != ".z64":
            return False
        normalized = z64_file_sha1(src)
        if normalized is None:
            return False
        digest, byte_order = normalized
        if sha1 and digest != sha1:
            raise RomError("that file isn't the version this project decompiles (its sha1 doesn't match the "
                           "project's); check the README for the exact version")
        if byte_order == "z64":
            return False  # the ordinary path checks its hash and can hard-link it
        dest.parent.mkdir(parents=True, exist_ok=True)
        copy_z64_file(src, dest)
        if move and src.resolve() != dest.resolve():
            src.unlink()
        return True

    def read_game(self, file, name: str = "") -> Picked:
        from .rom import read_info

        rom, info = read_info(Path(file).read_bytes() if isinstance(file, (str, Path)) else bytes(file))
        return Picked(self.id, rom, "rom.z64", info.to_dict() | {"platform": self.id, "gp": 0}, True)

    def default_compiler(self, info: dict, splat_guess: str) -> str:
        """splat's look at the code first (it says KMC or IDO), then the ROM reader's."""
        by_guess = {c.guess: c.id for c in self.compilers if c.guess}
        return by_guess.get(splat_guess) or by_guess.get(info.get("compiler_guess", "")) or self.compilers[0].id

    def add_code(self, config: Path, workdir: Path, image: str, picked: Picked, log=print) -> str:
        from .code import add_segments

        return add_segments(config, workdir, image, log)

    def up_front_guide(self, compiler: str) -> str:
        compiler = compiler.lower()  # (a fork of a public decomp: "project", its own build's)
        return "ido" if "ido" in compiler else "gcc-kmc" if "gcc" in compiler else ""

    def m2c_target(self, compiler: str = "") -> str:
        return "mips-ido" if "ido" in compiler.lower() else "mips-gcc"


N64 = Nintendo64()
