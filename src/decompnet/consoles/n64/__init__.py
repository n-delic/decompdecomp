"""Nintendo 64: the VR4300's MIPS (big-endian), KMC GCC, IDO and SN64 GCC, ROM mode.

- audio.py: source-assisted B1 sound banks, sample tables and mono VADPCM previews.
- textures.py: source-assisted indexed texture banks and bounded RNC asset previews.
- rom.py: the ROM file: its byte order (.z64, .v64, .n64), header and a first guess at the compiler.
- code.py: code splat's own look misses: segments the game loads later, and code kept compressed in the ROM.
- loader_code.py: full-bank closed-body recovery with CPU call, callback and adjacency evidence.
- loader_mapping.py: bank mappings corroborated by constant arguments to independent loader helpers.
- boot_mapping.py: returning TLB boot stubs separated from code with a strongly supported later load mapping.
- sdk_assembly.py: exact self-contained libultra body fingerprints, classified before C claims.
- guides/: what agents read about the N64 (n64.md) and its compilers (gcc-kmc.md, ido.md, sn64.md).

Its demo game is projects/demo-rom (the Demo Quest game in projects/demo-n64/original, built with KMC GCC).
"""

from __future__ import annotations

import re
from pathlib import Path

from ...rom import Picked, RomError
from .. import EXPORT, RECOMMENDED, ROM, Compiler, Console


class Nintendo64(Console):
    id = "n64"
    name = "Nintendo 64"
    short = "N64"
    status = RECOMMENDED  # tested on real games (Super Mario 64, GoldenEye, Perfect Dark), rebuilt byte for byte
    arch = "mips"
    asset_calls = {
        'osPiRawStartDma': dict(role='dma',source=1,destination=2,length=3,domain='rom',direction=0,source_alignment=2,destination_alignment=8,length_alignment=2,max_length=16*1024*1024),
        'osPiStartDma': dict(role='dma',source=3,destination=4,length=5,domain='rom',direction=2,source_alignment=2,destination_alignment=8,length_alignment=2,max_length=16*1024*1024),
        # Only explicit stores to these OSIoMesg fields are read, never mutable ROM initializers.
        'osEPiStartDma': dict(role='dma',source=1,destination=1,length=1,domain='rom',direction=2,
                             fields={'source':12,'destination':8,'length':16},source_alignment=2,destination_alignment=8,length_alignment=2,max_length=16*1024*1024),
    }
    big_endian = True
    mode = ROM
    layout = "splat"
    compilers = (
        Compiler("gcc-2.7.2-kmc", "KMC GCC 2.7.2", "many third-party N64 games", guess="KMC"),
        Compiler("ido-7.1", "IDO 7.1", "most later first-party N64 games", guess="IDO"),
        Compiler("ido-5.3", "IDO 5.3", "early N64 games (1996-97)"),
        Compiler("sn-n64-281", "SN64 GCC 2.8.1", "SN Systems toolchains used by some third-party games"),
        Compiler("sn-n64-272-0001", "SN64 GCC 2.7.2 (0001)", "earlier SN Systems N64 toolchains"),
        Compiler("sn-n64-272-0004", "SN64 GCC 2.7.2 (0004)", "preserved SN compiler and assembler build"),
        Compiler("sn-n64-272-0006", "SN64 GCC 2.7.2 (0006)", "later SN 2.7.2 build used in some boot code"),
        Compiler("gcc-2.8.1-pm-sn-as", "GCC 2.8.1 + SN ASN64 2.56", "GCC code generation with original SN assembly"),
        Compiler("gcc-2.8.1-pm", "GCC 2.8.1 (PM matching)", "patched GCC with its paired GNU assembler"),
        Compiler("gcc-egcs-2.91.66", "EGCS 1.1.2 / GCC 2.91.66", "later iQue rebuild toolchains"),
        Compiler("sn-n64-272-970404", "Reconstructed SN64 GCC 2.7.2 (970404)", "reconstructed SN codegen with compatibility assembly"),
        Compiler("ido-5.3-cxx", "IDO 5.3 C++", "separate SGI C++ frontend; Linux, WSL or macOS VM", automatic=False),
        Compiler("ido-5.2", "IDO 5.2", "experimental SGI comparison; Linux, WSL or macOS VM", automatic=False),
        Compiler("ido-6.0", "IDO 6.0", "experimental SGI comparison; Linux, WSL or macOS VM", automatic=False),
        Compiler("ido-4.1", "IDO 4.1", "experimental early SGI comparison; Linux, WSL or macOS VM", automatic=False),
        Compiler("mipspro-7.4.4", "MIPSpro 7.4.4", "experimental; retail N64 use unconfirmed; Linux, WSL or macOS VM", automatic=False),
        Compiler("gcc-egcs-1.1.2-4", "Original EGCS 1.1.2-4", "original Linux release; iQue toolchain comparison"),
        Compiler("gcc-4.4.0-n64", "GCC 4.4.0 (MIPS64 ELF)", "experimental modern toolchain; Linux, WSL or macOS VM", automatic=False),
    )
    kits = ("own-n64",)  # another build of one of its compilers, the user's (kits/owncompiler.py)
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

    def asset_regions(self, image, *, context=None, **kwargs):
        from . import audio, textures
        return textures.regions(image, **kwargs)+audio.regions(image, context=context, **kwargs)

    def asset_preview(self, data, asset, selection):
        from . import audio, textures
        return (audio.preview if asset.get('media')=='audio' else textures.preview)(data, asset, selection)

    def asset_decode(self, data):
        from .textures import decode
        return decode(data)

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

    sdk_assembly_sizes = (160,272)

    def sdk_assembly_identity(self, code, big_endian=True):
        from .sdk_assembly import identify
        return identify(code,big_endian)

    def read_game(self, file, name: str = "") -> Picked:
        from .rom import read_info

        rom, info = read_info(Path(file).read_bytes() if isinstance(file, (str, Path)) else bytes(file))
        return Picked(self.id, rom, "rom.z64", info.to_dict() | {"platform": self.id, "gp": 0}, True)

    def library_sha1(self, path: Path) -> str:
        from .rom import file_hashes

        hashes = file_hashes(path)
        return hashes.normalized_sha1 or hashes.source_sha1

    def project_build_command(self, project, command: list[str]) -> list[str]:
        from .buildtools import command as prepare

        return prepare(project, command)

    def default_compiler(self, info: dict, splat_guess: str) -> str:
        """splat's look at the code first (it says KMC or IDO), then the ROM reader's."""
        by_guess = {c.guess: c.id for c in self.compilers if c.guess}
        return by_guess.get(splat_guess) or by_guess.get(info.get("compiler_guess", "")) or self.compilers[0].id

    def add_code(self, config: Path, workdir: Path, image: str, picked: Picked, log=print) -> str:
        from .code import add_segments

        return add_segments(config, workdir, image, log)

    def up_front_guide(self, compiler: str) -> str:
        compiler = compiler.lower()  # (a fork of a public decomp: "project", its own build's)
        if compiler in ("gcc-2.8.1-pm-sn-as", "gcc-2.8.1-pm", "gcc-egcs-2.91.66", "gcc-egcs-1.1.2-4", "gcc-4.4.0-n64"):
            return "gcc-variants"
        return "ido" if "ido" in compiler or "mipspro" in compiler else "sn64" if compiler.startswith("sn-n64") else "gcc-kmc" if "gcc" in compiler else ""

    def m2c_target(self, compiler: str = "") -> str:
        return "mips-ido" if "ido" in compiler.lower() or "mipspro" in compiler.lower() else "mips-gcc"

    def export_asm(self, text: str) -> str:
        text = super().export_asm(text)
        # Some inferred code ranges include data decoded as a later MIPS `pref`.
        # VR4300 cannot assemble it; retain its original non-relocating word.
        return re.sub(r"^(\s*/\* [0-9A-Fa-f]+ [0-9A-Fa-f]{8} ([0-9A-Fa-f]{8}) \*/\s*)pref\b[^\n]*",
                      lambda m: f"{m[1]}.word 0x{m[2]}", text, flags=re.M)


N64 = Nintendo64()
