"""Nintendo 64: the VR4300's MIPS (big-endian), KMC GCC, IDO and SN64 GCC, ROM mode.

- Its assets (textures, sound banks, models, animation, fonts, resource directories, RNC) are found and previewed
  by the decomp-assets library, `decompassets.n64`; the old module names here (textures.py, audio.py, ...) are
  aliases for its modules. The console gives it the MIPS code reader its audio and fonts use (`asset_regions`).
- rom.py: the ROM file: its byte order (.z64, .v64, .n64), header and a first guess at the compiler.
- code.py: code splat's own look misses: segments the game loads later, and code kept compressed in the ROM.
- uso.py / uso_match.py: bounded USO text and relocations, matching and cartridge reconstruction.
- loader_code.py: full-bank closed-body recovery with CPU call, callback and adjacency evidence.
- runtime_scoped.py: called code corroborated in RAM despite an overlapping overlay address.
- reimport_preserve.py: stage existing byte-verified entries omitted by a replacement split.
- loader_mapping.py: bank mappings corroborated by constant arguments to independent loader helpers.
- stack_dma.py: stack-argument PI transfers that prove data mapped alongside independently placed code.
- indexed_overlays.py: shared RAM slots and DMA bounds recovered from loader arrays and cache calls.
- indexed_runtime_code.py: executable archive members mapped at their declared runtime addresses.
- indexed_tlb_code.py: loader-indexed compressed pages and data mapped by a TLB program.
- runtime_zlib.py, runtime_refpack.py, runtime_table_ring.py: compressed programs corroborated by loaded bytes and calls.
- runtime_overlays.py: overlapping code banks corroborated by runtime caller and target bytes.
- runtime_mio.py, runtime_mio_chunks.py: source-backed MIO0 programs corroborated against RAM.
- runtime_ring.py: a source-backed ring-buffer program identified by its loader and runtime bytes.
- runtime_textures.py: bounded emulator snapshots for texture loads whose pixels match the cartridge.
- level_overlay_table.py: shared-slot level banks and adjacent helpers verified from loader instructions.
- inferred_overlays.py: overlapping code-bank mappings supported by independently called local entries.
- reloc_fragments.py: text and linked addresses from consecutive relocation-section fragments.
- inline_data.py: verified constants and alignment bytes embedded among code.
- boot_flag_lz.py: an audited flag-byte boot decoder and its bounded program stream.
- boot_gzip.py, boot_compact_mio.py, boot_raw_deflate.py, boot_brs.py, boot_refpack.py, boot_rnc2.py, boot_rnx.py, boot_erz.py, boot_byte_lz.py, boot_variable_lz.py, boot_huffman.py, boot_chunked_bits.py, boot_paired_rle.py, boot_xor.py: encoded programs placed by the boot loader; packed_mapping.py recovers linked addresses in other compressed code blocks. runtime_byte_cipher.py recovers an audited in-place runtime decoder.
- boot_mapping.py: returning TLB boot stubs separated from code with a strongly supported later load mapping.
- sdk_assembly.py: exact self-contained libultra body fingerprints, classified before C claims.
- profile_probes.py: the per-function compiler profiles its open functions are judged under automatically (SN 2.7.2 builds, KMC for libultra).
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
from . import emulator, profile_probes, recomp, runtime_textures, sdk_assembly, sdk_linked, sdk_shapes
from .port import KIT as PORT_KIT
from .port import SCHEDULER

# Store only a digest of the entire body, including the return delay slot.
_MUL64_LOW_FIRST_SHA256 = "3de77e7c110a869794ff2998de80fb3fd6e4a7fc046d21378dc764a11b1676d0"


# a signature collection of the n_audio library: the built-in table or an indexed SDK archive
_N_AUDIO = re.compile(r"\bn_audio\b|\blibg?n_audio", re.I)


class Nintendo64(Console):
    id = "n64"
    name = "Nintendo 64"
    short = "N64"
    status = RECOMMENDED  # tested on real games (Super Mario 64, GoldenEye, Perfect Dark), rebuilt byte for byte
    arch = "mips"
    rust_desktop_judge = True
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
        Compiler("gcc-2.7.2-kmc", "KMC GCC 2.7.2", "many third-party N64 games", guess="KMC", released=1996),
        Compiler("ido-7.1", "IDO 7.1", "most later first-party N64 games", guess="IDO", released=1996),
        Compiler("ido-5.3", "IDO 5.3", "early N64 games (1996-97)", released=1995),
        Compiler("sn-n64-281", "SN64 GCC 2.8.1", "SN Systems toolchains used by some third-party games", released=1998),
        Compiler("sn-n64-272-0001", "SN64 GCC 2.7.2 (0001)", "earlier SN Systems N64 toolchains", released=1996),
        Compiler("sn-n64-272-0001-as0004", "SN64 GCC 2.7.2 (0001 + 0004 assembler)", "SN 2.7.2 games whose doubles load with ldc1", released=1996),
        Compiler("sn-n64-272-0004", "SN64 GCC 2.7.2 (0004)", "preserved SN compiler and assembler build", released=1996),
        Compiler("sn-n64-272-0006", "SN64 GCC 2.7.2 (0006)", "later SN 2.7.2 build used in some boot code", released=1996),
        Compiler("gcc-2.8.1-pm-sn-as", "GCC 2.8.1 + SN ASN64 2.56", "GCC code generation with original SN assembly", released=1998),
        Compiler("gcc-2.8.1-pm", "GCC 2.8.1 (PM matching)", "patched GCC with its paired GNU assembler", released=1998),
        Compiler("gcc-egcs-2.91.66", "EGCS 1.1.2 / GCC 2.91.66", "later iQue rebuild toolchains", released=1999),
        Compiler("sn-n64-272-970404", "Reconstructed SN64 GCC 2.7.2 (970404)", "reconstructed SN codegen with compatibility assembly", released=1997),
        Compiler("ido-5.3-cxx", "IDO 5.3 C++", "separate SGI C++ frontend; Linux, WSL or macOS VM", automatic=False, released=1995),
        Compiler("ido-5.2", "IDO 5.2", "experimental SGI comparison; Linux, WSL or macOS VM", automatic=False, released=1994),
        Compiler("ido-6.0", "IDO 6.0", "experimental SGI comparison; Linux, WSL or macOS VM", automatic=False, released=1995),
        Compiler("ido-4.1", "IDO 4.1", "experimental early SGI comparison; Linux, WSL or macOS VM", automatic=False, released=1993),
        Compiler("mipspro-7.4.4", "MIPSpro 7.4.4", "experimental; retail N64 use unconfirmed; Linux, WSL or macOS VM", automatic=False, released=2001),
        Compiler("gcc-egcs-1.1.2-4", "Original EGCS 1.1.2-4", "original Linux release; iQue toolchain comparison", released=1999),
        Compiler("gcc-4.4.0-n64", "GCC 4.4.0 (MIPS64 ELF)", "experimental modern toolchain; Linux, WSL or macOS VM", automatic=False, released=2009),
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
    runtime_asset_script = True
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
        evidence = context or {}
        assets = n64.regions(image, context={'read_code': read, **evidence}, **kwargs)
        if evidence.get('runtime_rom'):
            observed = runtime_textures.observe(image, evidence['runtime_rom'], evidence.get('runtime_sha1'),
                                                log=evidence.get('log', lambda _: None),
                                                status=evidence.get('runtime_observation'),
                                                context={'read_code': read, **evidence},
                                                progress=kwargs.get('progress', lambda _: None))
            banks = {a['bank_offset'] for a in observed if a.get('audio_kind') == 'sample bank'}
            assets = [a for a in assets if not (a.get('audio_kind') in ('unresolved bank', 'sample bank')
                                                and a['offset'] in banks)]
            assets += observed
        return assets

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
    # libultra as the SDK's own archives built it (SGI's cc for 2.0I, GCC for 2.0J-2.0L), beyond what the rebuild above
    # names (libultra_signatures.tsv: tools/naudio_signatures.py --library libultra)
    sdk_tables = (str(Path(__file__).with_name("naudio_signatures.tsv")), str(Path(__file__).with_name("runtime_signatures.tsv")),
                  str(Path(__file__).with_name("libultra_signatures.tsv")),
                  # libultra code the SDK folder lacks, as games that declare 2.0D link it (2.0D-2.0G have no archive)
                  str(Path(__file__).with_name("libultra_2_0d_signatures.tsv")))

    def profile_probes(self, facts):
        return profile_probes.probes(facts)

    def sdk_without_source(self, corpora):
        # n_audio (libn_audio / libgn_audio, the app's table or the user's archives): ultralib has none of its C. A
        # name the ultralib rebuild has too is libultra's own, and goes the usual way.
        return (any(_N_AUDIO.search(c) for c in corpora)
                and not any(c.startswith("built in / decompals/ultralib") for c in corpora))

    def sdk_sample_code(self, name):
        # libultra's scheduler (sched.c): the port runs the game's copy, but nobody needs to write it again
        return bool(SCHEDULER.match(name))

    def sdk_assembly_identity(self, code, big_endian=True):
        return sdk_assembly.identify(code, big_endian)

    def sdk_relocated_assembly_identity(self, code, relocations, machine):
        return sdk_assembly.identify_relocated_entry(code, relocations) if machine == 'EM_MIPS' else None

    sdk_shape_sizes = (sdk_shapes._MIN_ANY, sdk_shapes._MAX)

    def sdk_shape_identity(self, code, big_endian=True):
        return sdk_shapes.identify(code, big_endian)

    sdk_linked_sizes = sdk_linked.SIZES

    def sdk_linked_identity(self, code: bytes, image_sha1: str) -> dict | None:
        return sdk_linked.identify(code, image_sha1)

    def import_table_ranges(self, image, segments):
        from .code import relocated_switch_ranges

        return relocated_switch_ranges(image, segments)

    def function_target_meta(self, fn):
        info = getattr(fn, 'uso', None)
        return {'uso': info} if info is not None else {}

    def rebind_reimport_source(self, fn, source):
        from .uso_match import rebind_native_calls

        return rebind_native_calls(fn, source)

    def retry_reimport_source(self, net, project, fn, source):
        from .reimport_bindings import retry

        return retry(net, project, fn, source)

    def separate_overlay_reference(self, binding, number):
        from .uso_match import separate_reference

        return separate_reference(binding, number)

    def overlay_data_reference(self, binding, offset, number):
        from .uso_match import data_reference

        return data_reference(binding, offset, number)

    def overlay_cartridge_placement(self, fn, cfg):
        import json

        row = dict(fn)
        offset = row.get('rom_offset')
        if offset is None:
            return None
        info = json.loads(row.get('meta') or '{}').get('uso', {})
        section, start = info.get('section'), info.get('start')
        if not isinstance(section, int) or not isinstance(start, int) or start < 0 or info.get('size') != row['size']:
            return None
        if info.get('native'):
            if offset == section + start:
                return info
        elif (start == row['vram']
              and (cfg.get('donor_overlays') or {}).get(str(section)) == offset - start):
            return info
        return None

    def target_from_row(self, fn, cfg):
        from .uso_match import Target

        target = super().target_from_row(fn, cfg)
        if info := self.overlay_cartridge_placement(fn, cfg):
            if info.get('size') == len(target.code):
                return Target(**vars(target), uso=info)
        offset = dict(fn).get('rom_offset')
        banks = cfg.get('donor_overlays') or {}
        if banks and offset is not None and offset >= min(banks.values()):
            return Target(**vars(target), uso={'section': None, 'relocs': None})
        return target

    def target_object(self, fn, cfg, symbols):
        from ...rom.match import canonical_target
        from .uso_match import Target, symbolic_target

        target = self.target_from_row(fn, cfg)
        obj = symbolic_target(target) if isinstance(target, Target) else canonical_target(target, symbols)
        obj.native_judge = True
        return obj

    def link_function(self, obj, name, target, symbols, read):
        from ...rom import match
        from ...rom.desktop_judge import direct_link
        from . import uso_match

        if isinstance(target, uso_match.Target):
            return uso_match.verify(obj, name, target.code, target.uso)
        if type(target) is not match.RomTarget or match.rom_linker.get() is not None:
            return super().link_function(obj, name, target, symbols, read)
        # Exported source may use a different spelling from its assembly listing.
        if target.name != name:
            from dataclasses import replace

            target = replace(target, name=name)
        token = match.rom_linker.set(direct_link)
        try:
            return super().link_function(obj, name, target, symbols, read)
        finally:
            match.rom_linker.reset(token)

    def export_overlay(self, target):
        from .uso_match import Target

        return target.uso if isinstance(target, Target) and target.uso['relocs'] is not None else None

    def overlay_metadata_updates(self, image, cfg, rows):
        from .uso import metadata_updates

        return metadata_updates(image, cfg['donor_overlays'], rows)

    def rebuild_overlays(self, image, entries):
        from .uso import rebuild

        return rebuild(image, entries)

    def function_asm(self, fn):
        from dataclasses import replace

        import rabbitizer

        from .uso_match import alias, local_alias

        info = getattr(fn, 'uso', None)
        if not info or not info['relocs']:
            return super().function_asm(fn)
        lines = list(fn.lines)
        local_calls = {(at, index): offset for at, index, offset in info.get('local_calls', ())}
        for at, kind, index in info['relocs']:
            line = lines[at // 4]
            offset = local_calls.get((at, index)) if kind == 4 else None
            name = (local_alias(info['section'], index, offset) if offset is not None
                    else alias(info['section'], index))
            if kind in (4, 5, 6):
                field = line.word & (0x3ffffff if kind == 4 else 0xffff)
                mask = {4: '', 5: '%hi', 6: '%lo'}[kind]
                immediate = name if not mask else f'{mask}({name})'
                if field and offset is None:
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

    def splat_create_config(self, args, workdir, log):
        from .splat_config import create

        return create(args, workdir, log)

    def add_code(self, config: Path, workdir: Path, image: str, picked: Picked, log=print) -> str:
        from .code import add_segments
        from .inline_data import apply

        expanded = add_segments(config, workdir, image, log)
        if expanded == image:
            from .runtime_byte_cipher import add as decode_runtime_code

            expanded = decode_runtime_code(config, workdir, expanded, log)
        apply(config, (workdir / expanded).read_bytes(), log)
        return expanded

    def runtime_audit_candidates(self, root, cfg, image, segments, functions, log, candidates=(), checkpoint=None):
        from .runtime_scoped import observe

        return observe(root, cfg, image, segments, functions, log, candidates=candidates, checkpoint=checkpoint)

    donor_layout = True

    def donor_text_regions(self, image: bytes) -> list:
        from .uso import read

        return read(image)

    def donor_candidate_review(self, code: bytes, vram: int, *, region=None, offset=None) -> str | None:
        from ...arch.mips_code_recovery import MAX_BYTES, called_extent, leaf_extent

        # Donor object symbols can mark RSP microcode, data or a function's
        # epilogue as STT_FUNC. Identical bytes alone do not establish a CPU entry.
        if len(code) > MAX_BYTES:
            return 'The body exceeds the supported bounded control-flow review size.'
        if region is not None and region.relocations is None:
            return 'The module has no validated relocation records.'
        if (region is not None and type(offset) is int
                and any(offset < entry < offset + len(code) for entry in region.entries)):
            return 'The module records another entry inside this extent.'
        if reason := self.asm_reason(code):
            return 'Assembly or a partial entry needs review: ' + reason
        size = (called_extent(code, 0, len(code), vram, cpu=self.cpu_variant)
                or leaf_extent(code, 0, len(code), vram, cpu=self.cpu_variant))
        if not size and region is not None and offset in region.entries and region.data[offset:offset + len(code)] == code:
            # A module's own symbol and relocation tables independently identify
            # both this entry and every guarded switch destination.
            from . import uso
            relocs = uso.function_relocations(region, offset, len(code))
            tables = uso.function_jump_tables(region, offset, len(code), relocs)
            edges = {at + 4: targets for at, _, targets in tables}
            if edges:
                size = (called_extent(code, 0, len(code), vram, cpu=self.cpu_variant, resolved_indirect=edges)
                        or leaf_extent(code, 0, len(code), vram, cpu=self.cpu_variant, resolved_indirect=edges))
        if not size or any(code[size:]):
            return 'The entry, stack frame or control flow needs review.'
        return None

    def prepare_donor_function(self, fn, region, offset, *, native=False) -> dict:
        if region is None:
            return {}
        from . import uso

        code = region.data[offset:offset + fn.size]
        if len(code) != fn.size or b''.join(line.word.to_bytes(4, 'big') for line in fn.lines) != code:
            raise ValueError('The donor function no longer matches its module text.')
        relocs = uso.function_relocations(region, offset, fn.size)
        if relocs is None:
            raise ValueError('The module has no validated relocation records.')
        if native and not relocs:
            return {}  # The ordinary linked-byte judge already handles this native body.
        info = {'section': region.offset, 'start': offset, 'size': fn.size, 'relocs': relocs}
        if native:
            info['native'] = True
        info.update(uso._binding_metadata(region, offset, fn.size, relocs))
        fn.uso = info
        return {'uso': info}

    def missed_functions(self, config: Path, image: bytes, log=print, known=(), *, prepare_split=None,
                         checkpoint=None, expected_config=None) -> list[tuple[int, int, int]] | None:
        from .code import missed_functions

        return missed_functions(config, image, log, known, prepare_split=prepare_split, checkpoint=checkpoint,
                                expected_config=expected_config)

    def preserve_reimport_entries(self, net, project, result, image, rows, log=print):
        from .reimport_preserve import preserve

        return preserve(net, project, result, image, rows, log)

    def import_pointer_is_data(self, image, offset):
        from .packed_pointers import is_data

        return is_data(image, offset)

    def import_nonpointer_ranges(self, image, segments):
        from .packed_pointers import audio_payloads

        return audio_payloads(image, segments)

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
