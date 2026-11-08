"""Android native ARMv7 libraries, in APK/XAPK packages or as a shared ELF, in ROM mode.

package.py reads bounded archive members without extracting paths. elf.py uses the library's
own symbol sizes as function boundaries. split.py provides
assembly and editing for ARM/Thumb functions. The shared ARM judge rejects unsupported
relocations; the NDK's compiler versions are pinned in toolchains.toml.

The largest armeabi-v7a library is the initial scope. DEX, other libraries, assets, APK signing,
AArch64, machine drafts and a rebuildable package remain planned work in ROADMAP.md.
"""

from __future__ import annotations

from pathlib import Path

from ...rom import RomError
from .. import BETA, ROM, Compiler
from ..arm import ArmConsole


class Android(ArmConsole):
    id = "android"
    name = "Android (native ARMv7)"
    short = "Android"
    status = BETA
    arch = "arm"
    mode = ROM
    layout = "ARM ELF function symbols"
    compilers = (
        Compiler("android-clang-3.5", "Android Clang 3.5", "NDK r10e, ARMv7 native libraries", guess="CLANG"),
        Compiler("android-gcc-4.8", "Android GCC 4.8", "NDK r10e, ARMv7 native libraries", guess="GCC"),
        Compiler("android-gcc-4.9", "Android GCC 4.9", "NDK r10e, ARMv7 native libraries"),
    )
    packs = (("ghidra", "machine drafts for native Android functions; Java 21+ is installed if needed"),)
    draft_provider = "Ghidra"
    draft_version = 5
    guides = ("android",)
    compiler_guide = "android"
    ram = (0, 0xFFFFFFFF)
    as_flags = ("-march=armv7-a",)
    image_ext = ".so"
    asm_preamble = ".syntax unified\n.text\n"
    demo = "demo-android"
    demo_tools = ("android-clang-3.5",)

    def sniff(self, head: bytes, file) -> bool:
        from . import package

        return package.sniff(head, file)

    def read_game(self, file, name: str = ""):
        from . import package

        return package.read(file, name)

    def split_game(self, workdir: Path, picked, *, symbols=None, log=print):
        from . import elf

        log(f"Selected {picked.info['library']} from {picked.info['native_libraries']} ARMv7 libraries.")
        log(picked.info["scope"])
        return elf.split(picked.data, workdir, log)

    def default_compiler(self, info: dict, splat_guess: str) -> str:
        info["compiler_why"] = ("compiler stamps can include several linked libraries; Clang 3.5 is the starting "
                                "choice, not proof of the compiler of every function. Try the GCC versions too.")
        return super().default_compiler(info, splat_guess)

    def target_from_row(self, fn, cfg):
        target = super().target_from_row(fn, cfg)
        target.thumb2 = True
        return target

    def target_from_split(self, fn, resolve, big_endian, gp):
        target = super().target_from_split(fn, resolve, big_endian, gp)
        target.thumb2 = True
        return target

    def drafts_ready(self) -> bool:
        from .drafts import ready

        return ready()

    def draft_help(self) -> str:
        return "Android machine drafts need Ghidra: install it in Setup → Compilers & tools (Java 21+ is included when needed)."

    def machine_draft(self, net, proj, fn, context, *, valid=False):
        from .drafts import generate

        return generate(net, proj, fn, context, valid)

    def link_target(self, target, program):
        from ...arch.arm_image import image

        target.image = image(program)
        return target

    def exact_rom_drafts(self, fn, context):
        from ...arch.arm_drafts import exact

        return exact(fn['target'], fn['vram'], self.function_meta(fn)['thumb'], fn['name'], context)

    def function_flags(self, fn, flags: list[str]) -> list[str]:
        return [f for f in flags if f not in ("-marm", "-mthumb")] + [
            "-mthumb" if self.function_meta(fn)["thumb"] else "-marm"]

    def asm_reason(self, code: bytes, **facts) -> str | None:
        # ARMv7 adds instructions and literal-pool forms the older ARM piece heuristics don't recognise.
        # Until those rules are checked on ARMv7, don't mark valid game functions as impossible C.
        return None

    def function_from_row(self, row):
        from .split import from_row

        return from_row(row)

    def parse_function(self, name: str, text: str, segment: str):
        raise RomError("Android functions need their original ELF bytes; reimport the APK to recover missing analysis.")

    def split_function(self, row, at: int, new_name: str):
        from .split import cut

        return cut(row, at, new_name)

    def function_at(self, rom: bytes, addr: int, limit: int, name: str):
        from .elf import function_at

        return function_at(rom, addr, limit, name)

    def join_saved_functions(self, rows, start, end, read):
        from .split import join

        return join(rows, start, end, read)

    def export_project(self, net, slug: str, out: Path, log=print) -> dict:
        raise RomError("Android rebuildable export isn't available yet: matched C is saved in progress/. "
                       "APK repackaging and signing are planned in ROADMAP.md.")


ANDROID = Android()
