"""Nintendo 64: the VR4300's MIPS (big-endian), KMC GCC, IDO and SN64 GCC, ROM mode.

- Its assets (textures, sound banks, models, animation, fonts, resource directories, RNC) are found and previewed
  by the decomp-assets library, `decompassets.n64`; the old module names here (textures.py, audio.py, ...) are
  aliases for its modules. The console gives it the MIPS code reader its audio and fonts use (`asset_regions`).
- rom.py: the ROM file: its byte order (.z64, .v64, .n64), header and a first guess at the compiler.
- code.py: code splat's own look misses: segments the game loads later, and code kept compressed in the ROM.
- uso.py / uso_match.py: bounded USO text and relocations, matching and cartridge reconstruction.
- loader_code.py: full-bank closed-body recovery with CPU call, callback and adjacency evidence.
- loader_mapping.py: bank mappings corroborated by constant arguments to independent loader helpers.
- indexed_overlays.py: shared RAM slots and DMA bounds recovered from loader arrays and cache calls.
- inferred_overlays.py: overlapping code-bank mappings supported by independently called local entries.
- inline_data.py: verified constants and alignment bytes embedded among code.
- boot_gzip.py: programs in paired gzip members placed at addresses supplied by the boot loader.
- boot_mapping.py: returning TLB boot stubs separated from code with a strongly supported later load mapping.
- sdk_assembly.py: exact self-contained libultra body fingerprints, classified before C claims.
- port.py: its port kit, libultraship and Torch: what they replace and which graphics microcode they render.
- emulator/: the reversing emulator (mupenRE, built from source), its `Console.emulator` part: the user's ROM run
  headless with memory dumps and breakpoints (watch_in_emulator).
- recomp.py: N64Recomp, its `Console.recompiler` part: the game's own instructions as C, from the split.
- guides/: what agents read about the N64 (n64.md) and its compilers (gcc-kmc.md, ido.md, sn64.md).

Its demo game is projects/demo-rom (the Demo Quest game in projects/demo-n64/original, built with KMC GCC).
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from ...rom import Picked, RomError
from .. import EXPORT, RECOMMENDED, ROM, Compiler, Console
from . import emulator, recomp, sdk_assembly, sdk_linked
from .port import KIT as PORT_KIT

# Store only a digest of the entire body, including the return delay slot.
_MUL64_LOW_FIRST_SHA256 = "3de77e7c110a869794ff2998de80fb3fd6e4a7fc046d21378dc764a11b1676d0"


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
    port_kit = PORT_KIT
    emulator = emulator
    recompiler = recomp
    as_flags = ("-EB", "-march=vr4300", "-mabi=32")
    reference_tools = ("binutils-mips", "mips-ps2-decompals-as", "mips-ps2-decompals-ld")
    image_ext = ".z64"
    asm_preamble = '.include "macro.inc"\n.set noat\n.set noreorder\n.set gp=64\n.section .text, "ax"\n'

    def asset_regions(self, image, *, context=None, **kwargs):
        from decompassets import n64

        from ...arch.mips_assets import read
        # Sound banks find their sample tables from the constant arguments of the game's alBnkfNew calls,
        # read with the app's MIPS reader (decomp-assets has none of its own).
        return n64.regions(image, context={'read_code': read, **(context or {})}, **kwargs)

    def asset_preview(self, data, asset, selection):
        from decompassets import n64
        return n64.preview(data, asset, selection)

    def asset_scene(self, image, asset, selection, assets):
        from decompassets import n64
        return n64.scene(image, asset, selection, assets)

    def asset_decode(self, data):
        from decompassets.n64.textures import decode
        return decode(data)

    def bss_start(self, image: bytes) -> int | None:
        from .rom import bss_start

        return bss_start(image)

    def sniff(self, head: bytes, file) -> bool:
        from .rom import MAGIC

        return head[:4] in MAGIC

    def release_key(self, rom: dict) -> str | None:
        # A game code is a media letter, the game's two letters and its region's (NVLP and NVLE: one game).
        code = rom.get("game_code") or ""
        return code[:3] if len(code) == 4 and code[:3].isalnum() and code[1:3] != "00" else None

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

    known_assembly_sizes = (52,)

    def known_assembly_name(self, code: bytes) -> str | None:
        if len(code) in self.known_assembly_sizes and hashlib.sha256(code).hexdigest() == _MUL64_LOW_FIRST_SHA256:
            return "mul64_low_first"
        return None

    def asm_reason(self, code: bytes) -> str | None:
        from .context_switch import saves_context

        if self.known_assembly_name(code):
            return "Exact signature of the low-word-first 64-bit multiply assembly helper."
        if saves_context(code):
            return "Handwritten context switch saves all floating-point registers and hands off to the shared restore entry."
        return super().asm_reason(code)

    sdk_assembly_sizes = sdk_assembly.SIZES
    sdk_entry_stub = True
    # libultra 2.0I-2.0L as decompals/ultralib rebuilds it, so a user without the SDK still has its library found
    sdk_table = str(Path(__file__).with_name("ultralib_signatures.tsv"))
    # n_audio (libgn_audio.a 2.0J-2.0L), which ultralib doesn't rebuild: the SDK archives' fingerprints and names
    # plus hand-written runtime code that cartridges share but no archive rebuilds (fingerprints of functions found
    # byte for byte, relocations aside, in unrelated games: runtime_signatures.tsv)
    sdk_tables = (str(Path(__file__).with_name("naudio_signatures.tsv")), str(Path(__file__).with_name("runtime_signatures.tsv")))

    def sdk_assembly_identity(self, code, big_endian=True):
        return sdk_assembly.identify(code, big_endian)

    sdk_linked_sizes = sdk_linked.SIZES

    def sdk_linked_identity(self, code: bytes, image_sha1: str) -> dict | None:
        return sdk_linked.identify(code, image_sha1)

    def import_table_ranges(self, image, segments):
        from .code import relocated_switch_ranges

        return relocated_switch_ranges(image, segments)

    def function_target_meta(self, fn):
        info = getattr(fn, 'uso', None)
        return {'uso': info} if info is not None else {}

    def target_from_row(self, fn, cfg):
        import json

        from .uso_match import Target

        target = super().target_from_row(fn, cfg)
        row = dict(fn)
        offset = row.get('rom_offset')
        banks = cfg.get('donor_overlays') or {}
        if not banks or offset is None or offset < min(banks.values()):
            return target
        info = json.loads(row.get('meta') or '{}').get('uso', {})
        if (banks.get(str(info.get('section'))) != offset - target.vram
                or info.get('start') != target.vram or info.get('size') != len(target.code)):
            info = {'section': None, 'relocs': None}
        return Target(**vars(target), uso=info)

    def target_object(self, fn, cfg, symbols):
        from ...rom.match import canonical_target
        from .uso_match import Target, symbolic_target

        target = self.target_from_row(fn, cfg)
        return symbolic_target(target) if isinstance(target, Target) else canonical_target(target, symbols)

    def link_function(self, obj, name, target, symbols, read):
        from . import uso_match

        if isinstance(target, uso_match.Target):
            return uso_match.link(obj, name, target.code, target.uso)
        return super().link_function(obj, name, target, symbols, read)

    def export_overlay(self, target):
        from .uso_match import Target

        return target.uso if isinstance(target, Target) and target.uso['relocs'] is not None else None

    def rebuild_overlays(self, image, entries):
        from .uso import rebuild

        return rebuild(image, entries)

    def function_asm(self, fn):
        from dataclasses import replace

        import rabbitizer

        from .uso_match import alias

        info = getattr(fn, 'uso', None)
        if not info or not info['relocs']:
            return super().function_asm(fn)
        lines = list(fn.lines)
        for at, kind, index in info['relocs']:
            line = lines[at // 4]
            name = alias(info['section'], index)
            if kind in (4, 5, 6):
                field = line.word & (0x3ffffff if kind == 4 else 0xffff)
                mask = {4: '', 5: '%hi', 6: '%lo'}[kind]
                immediate = name if not mask else f'{mask}({name})'
                if field:
                    # Keep the encoded addend visible when it is more than a symbol reference.
                    immediate = f'{immediate} /* encoded addend 0x{field:X} */'
                insn = rabbitizer.Instruction(line.word, line.vram)
                parts = insn.disassemble(immOverride=immediate).split(None, 1)
                if len(parts) == 2:
                    lines[at // 4] = replace(line, operands=parts[1])
        return super().function_asm(replace(fn, lines=lines))

    def split_game(self, workdir: Path, picked: Picked, *, symbols: list[str] | None = None, log=print):
        from .code import recover_terminal_display_lists
        from .uso import add_to_split

        result = super().split_game(workdir, picked, symbols=symbols, log=log)
        sdk_linked.split_scheduler(result, picked.info.get('sha1', ''))
        recover_terminal_display_lists(result, (workdir / result.image).read_bytes(), log)
        add_to_split(result, workdir, picked.data, log)
        return result

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
        from .inline_data import apply

        expanded = add_segments(config, workdir, image, log)
        apply(config, (workdir / expanded).read_bytes(), log)
        return expanded

    donor_layout = True

    def donor_text_regions(self, image: bytes) -> list:
        from .uso import read

        return read(image)

    def missed_functions(self, config: Path, image: bytes, log=print) -> list[tuple[int, int, int]] | None:
        from .code import missed_functions

        return missed_functions(config, image, log)

    def import_pointer_is_data(self, image, offset):
        from .packed_pointers import is_data

        return is_data(image, offset)

    def up_front_guide(self, compiler: str) -> str:
        compiler = compiler.lower()  # (a fork of a public decomp: "project", its own build's)
        if compiler in ("gcc-2.8.1-pm-sn-as", "gcc-2.8.1-pm", "gcc-egcs-2.91.66", "gcc-egcs-1.1.2-4", "gcc-4.4.0-n64"):
            return "gcc-variants"
        return "ido" if "ido" in compiler or "mipspro" in compiler else "sn64" if compiler.startswith("sn-n64") else "gcc-kmc" if "gcc" in compiler else ""

    def m2c_target(self, compiler: str = "") -> str:
        return "mips-ido" if "ido" in compiler.lower() or "mipspro" in compiler.lower() else "mips-gcc"

    def export_asm(self, text: str) -> str:
        text = super().export_asm(text)
        # Some inferred code ranges include data decoded as later MIPS hints
        # or conditional moves. Preserve their words for the VR4300 build.
        return re.sub(r"^(\s*/\* [0-9A-Fa-f]+ [0-9A-Fa-f]{8} ([0-9A-Fa-f]{8}) \*/\s*)(?:pref|movn|movz)\b[^\n]*",
                      lambda m: f"{m[1]}.word 0x{m[2]}", text, flags=re.M)


N64 = Nintendo64()
