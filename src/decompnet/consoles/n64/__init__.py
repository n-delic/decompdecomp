"""Nintendo 64: the VR4300's MIPS (big-endian), KMC GCC, IDO and SN64 GCC, ROM mode.

- rom.py: the ROM file: its byte order (.z64, .v64, .n64), header and a first guess at the compiler.
- code.py: code splat's own look misses: segments the game loads later, and code kept compressed in the ROM.
- guides/: what agents read about the N64 and its compilers (KMC, IDO, SN64 and GCC variants).

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
        Compiler("sn-n64-272-0001", "SN64 GCC 2.7.2 (0001)", "earlier SN Systems N64 toolchains"),
        Compiler("sn-n64-272-0006", "SN64 GCC 2.7.2 (0006)", "later SN 2.7.2 build used in some boot code"),
        Compiler("gcc-2.8.1-pm-sn-as", "GCC 2.8.1 + SN ASN64 2.56", "GCC code generation with original SN assembly"),
        Compiler("gcc-2.8.1-pm", "GCC 2.8.1 (PM matching)", "patched GCC with its paired GNU assembler"),
        Compiler("gcc-egcs-2.91.66", "EGCS 1.1.2 / GCC 2.91.66", "later iQue rebuild toolchains"),
        Compiler("sn-n64-272-970404", "Reconstructed SN64 GCC 2.7.2 (970404)", "reconstructed SN codegen with compatibility assembly"),
        Compiler("ido-4.1", "IDO 4.1", "experimental early SGI comparison; Linux x86_64"),
        Compiler("gcc-egcs-1.1.2-4", "Original EGCS 1.1.2-4", "original Linux release; iQue toolchain comparison"),
        Compiler("gcc-4.4.0-n64", "GCC 4.4.0 (MIPS64 ELF)", "experimental modern toolchain; Linux or WSL"),
    )
    kit_only_compilers = ("sn-n64-281", "sn-n64-272-0004", "ido-5.2", "ido-5.3-cxx", "ido-6.0", "mipspro-7.4.4")
    kits = ("own-n64", *("own-" + profile for profile in kit_only_compilers))
    guides = ("mips",)
    m2c = "mips-gcc"
    ram = (0x80000400, 0x80800000)  # KSEG0 above the exception vectors, up to the Expansion Pak's 8 MiB
    demo = "demo-rom"
    demo_tools = ("gcc-2.7.2-kmc", "binutils-mips", "ld.lld", "llvm-objcopy")
    own_features = frozenset({EXPORT})
    as_flags = ("-EB", "-march=vr4300", "-mabi=32")
    reference_tools = ("binutils-mips", "mips-ps2-decompals-as", "mips-ps2-decompals-ld")
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

    def fork_game_versions(self, project) -> dict[str, str]:
        from .fork_versions import original_roms

        return original_roms(project.root)

    def fork_game_need(self, project, version: str | None) -> dict | None:
        originals = self.fork_game_versions(project)
        if not originals:
            return None
        selected = version if version in originals else self.default_fork_game_version(project)
        return {"what": f"your original game ROM (baserom.{selected}.z64)",
                "path": f"baserom.{selected}.z64", "sha1": originals[selected], "auto": True,
                "rom_versions": [{"version": v, "sha1": digest} for v, digest in originals.items()],
                "help": "Supply an original cartridge ROM in .z64, .v64 or .n64 byte order. "
                        "Its normalized SHA-1 selects the build version."}

    def default_fork_game_version(self, project) -> str | None:
        from .fork_versions import default_version

        return default_version(project.root, self.fork_game_versions(project))

    def match_fork_game_version(self, project, game: Path, log=print) -> str | None:
        from .fork_versions import match

        return match(game, self.fork_game_versions(project), log)

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
        from ... import kits

        own = kits.own_compiler(compiler)
        if own and own.like:
            compiler = own.like
        compiler = compiler.lower()  # (a fork of a public decomp: "project", its own build's)
        if compiler in ("gcc-2.8.1-pm-sn-as", "gcc-2.8.1-pm", "gcc-egcs-2.91.66", "gcc-egcs-1.1.2-4", "gcc-4.4.0-n64"):
            return "gcc-variants"
        return "ido" if "ido" in compiler or "mipspro" in compiler else "sn64" if compiler.startswith("sn-n64") else "gcc-kmc" if "gcc" in compiler else ""

    def m2c_target(self, compiler: str = "") -> str:
        return "mips-ido" if "ido" in compiler.lower() or "mipspro" in compiler.lower() else "mips-gcc"


N64 = Nintendo64()
