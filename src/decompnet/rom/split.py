"""Split a game's program (an N64 ROM, a PS2 ELF) into functions and data with splat (MIT) and read back what it found.

splat's own create_config finds the boot entry point, the main code segment, .bss and the compiler;
`splat split` then disassembles every function (spimdisasm, MIT) with symbols for every address it
can pair up (%hi/%lo, jal targets). We parse that output into plain records the rest of the app uses.
"""

from __future__ import annotations

import re
import struct
import subprocess
import time
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path

import rabbitizer

from ..hostenv import NO_WINDOW, console_python, stop_process_tree
from . import DisassemblyTimeout, Picked, RomError

_INSN = re.compile(r"^\s*/\* ([0-9A-F]+) ([0-9A-F]{8}) ([0-9A-F]{8}) \*/\s+(\S+)\s*(.*?)\s*$")
# A label may carry the name of the overlay or slot it lives in (`_overlay_001`, `_ovl_01`, `_raw_slot_03`,
# `_packed_program_02`, `_dma_overlay_01`): any lower-case word suffix, so a new kind of slot needs no change here.
_LOCAL_NAME = r"\.?L([0-9A-F]{8})(?:_[a-z][a-z0-9]*(?:_[A-Za-z0-9]+)*)?"  # (`_code_80115DF0_DFF10`: hex words too)
_LOCAL_REF = re.compile(r"(?<![\w.$])" + _LOCAL_NAME + r"(?![\w.$])")
_BRANCH = re.compile(r"(?:b(?!\w*al)\w*|j)$")  # branches and jumps, not calls (jal, jalr, bal and the other linking branches)
_FUNC_REF = re.compile(r"(?<![\w.$])func_([0-9A-Fa-f]{8})(?:_[a-z][a-z0-9_]*)?(?![\w.$])")
_LABEL = re.compile(r"^\s*(?:jlabel\s+)?(" + _LOCAL_NAME + r"):?\s*$")
_DATA = re.compile(r"^\s*/\* ([0-9A-F]+ )?([0-9A-F]{8})(?: [0-9A-F]+)? \*/\s+\.(\w+)\s*(.*?)\s*$")
_DLABEL = re.compile(r"^\s*dlabel\s+(\S+)")
_GLABEL = re.compile(r"^\s*glabel\s+(\S+)")
_JLABEL = re.compile(r"^\s*jlabel\s+(\S+)")  # a file that starts at a jump target: a piece of a function


@dataclass
class AsmLine:
    rom: int
    vram: int
    word: int
    mnemonic: str
    operands: str


@dataclass
class RomFunction:
    name: str
    vram: int
    rom: int
    segment: str
    lines: list[AsmLine]
    asm_path: Path
    labels: set[int] = field(default_factory=set)
    piece: bool = False  # splat filed a switch's case on its own (merge_switch_cases puts it back)
    entry_aliases: dict[int, str] = field(default_factory=dict)

    @property
    def size(self) -> int:
        return 4 * len(self.lines)


def split_function(fn: RomFunction, at: int, new_name: str) -> tuple[RomFunction, RomFunction]:
    """Cut a function in two at address `at`. The disassembler can't always see where a function ends
    (one that never returns runs straight into the next), so agents can split what it merged."""
    if not fn.vram < at < fn.vram + fn.size or (at - fn.vram) % 4:
        raise ValueError(f"0x{at:08X} is not an instruction boundary inside {fn.name}")
    k = (at - fn.vram) // 4
    head = RomFunction(fn.name, fn.vram, fn.rom, fn.segment, fn.lines[:k], fn.asm_path, {a for a in fn.labels if a < at},
                       entry_aliases={a:n for a,n in fn.entry_aliases.items() if a < at})
    _trim_padding(head.lines)
    tail = RomFunction(new_name, at, fn.lines[k].rom, fn.segment, fn.lines[k:], fn.asm_path, {a for a in fn.labels if a >= at},
                       entry_aliases={a:n for a,n in fn.entry_aliases.items() if a >= at and n != new_name})
    return head, tail


@dataclass
class DataSymbol:
    name: str
    vram: int
    rom: int | None  # None for .bss
    size: int
    kind: str  # data | string | jtbl | bss
    preview: str = ""
    boundary_proven: bool = False  # Reclassified as data by an evidence-backed boundary repair.


@dataclass
class Segment:
    name: str
    rom_start: int
    rom_end: int
    vram: int
    exclusive_ram_id: str = ""

    def contains(self, address: int, length: int = 1) -> bool:
        return self.vram <= address and address + length <= self.vram + (self.rom_end - self.rom_start)


def write_config_atomic(path: Path, text: str) -> None:
    """Replace a split configuration without exposing a partially written file."""
    import os
    import tempfile

    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.decomp-split-', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(text)
        os.chmod(temporary, path.stat().st_mode & 0o777)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@dataclass
class SplitResult:
    functions: list[RomFunction]
    data: list[DataSymbol]
    segments: list[Segment]
    named: dict[str, int]  # names splat gave addresses (entrypoint, main, ...)
    config_path: Path
    compiler: str
    log: str
    platform: str = "n64"
    gp: int = 0  # value of $gp for $gp-relative data (PlayStation), 0 if unused
    image: str = ""  # the flat image splat reads (the ROM itself, or one made from an ELF)
    coverage_notes: list[str] = field(default_factory=list)
    analysis_base: str = ""
    analysis_banks: dict[str, int] = field(default_factory=dict)
    retained_join_archives: set[str] = field(default_factory=set)


def segments_from_config(path: Path) -> list[Segment]:
    """Code segments (ROM range + load address) from a splat config."""
    import yaml

    return segments_from_config_data(yaml.safe_load(path.read_text(encoding="utf-8")))


def segments_from_config_data(cfg: dict) -> list[Segment]:
    """The same validated code-parent mappings from a staged split configuration."""
    raw = cfg.get("segments", [])

    def start_of(seg):
        if isinstance(seg, list):
            return seg[0]
        return seg.get("start")

    out = []
    for i, seg in enumerate(raw):
        if not isinstance(seg, dict) or "vram" not in seg or seg.get("type") != "code":
            continue
        nxt = next((start_of(s) for s in raw[i + 1:] if start_of(s) is not None), None)
        if nxt is None:
            continue
        out.append(Segment(seg.get("name", f"{seg['start']:X}"), int(seg["start"]), int(nxt), int(seg["vram"]),
                           seg.get("exclusive_ram_id") or ""))
    return out


def load_images(cfg: dict, rom: bytes | None = None, *, big_endian: bool = True) -> list[tuple[int, int, int]]:
    """(rom_start, vram, vram_end) of each loaded image a splat config describes: a code segment whose BSS has an
    address loads everything from its start to its BSS at one offset, its data included. The judge reads a
    candidate's own data (jump tables, strings, constants) from these when no code segment holds the address: the
    code segments alone stop where the code does, and a matched function's wrong jump table went unchecked.

    With the ROM, the data after code the import found later (`trailing_data`) is added too."""
    out = []
    for seg in cfg.get("segments", []):
        if not isinstance(seg, dict) or seg.get("type") != "code" or "vram" not in seg or "start" not in seg:
            continue
        bss = [sub["vram"] for sub in seg.get("subsegments", []) if isinstance(sub, dict) and sub.get("type") == "bss"
               and isinstance(sub.get("vram"), int)]
        if bss and bss[0] > seg["vram"]:
            out.append((int(seg["start"]), int(seg["vram"]), int(bss[0])))
    if rom is not None:
        out += [image for image in trailing_data(cfg, rom, big_endian=big_endian)
                if not any(v < image[2] and image[1] < e for _, v, e in out)]
    return out


def trailing_data(cfg: dict, rom: bytes, *, big_endian: bool = True) -> list[tuple[int, int, int]]:
    """(rom_start, vram, vram_end) of the raw bytes right after a run of code segments that share one ROM-to-RAM
    offset, mapped at that offset: the run's own .rodata and .data (jump tables, strings, constants).

    The import writes the code it finds the game loads later (insert_segments) as code segments of their own, each
    followed by a `bin` segment up to the next one, with no vram and no BSS: nothing said those bytes are loaded with
    the code before them. An N64 racing game's 113 KB of data after 1 MB of code was unreadable: two switches'
    jump tables "not in a loaded part of the ROM", their drafts without their cases, and the judge skipped checking
    every table there. A raw run counts only when its end is another segment (not the ROM's tail, where the assets
    are), it overlaps no segment's and no other run's addresses, and the switches prove it: the run's code has a jump
    table in it, and every table in it (whoever's code reads it) holds an address in that code's own segment. Raw
    bytes that are packed, or another overlay's at the same addresses, fail that and stay unread.

    A raw run with code at the same offset on both sides is inside one linear load, so any code's switches prove it:
    the run's own need not have a table. An N64 game's first code had no switch and its main code's 32 KB of .rodata
    sat between the two (eight jump tables, the float constants): unread, so the judge accepted any float literal a
    candidate wrote for one of them, and `float_literal_global` had no words to propose."""
    raw =[s for s in cfg.get("segments", []) if isinstance(s, (dict, list))]

    def start_of(seg):
        return seg[0] if isinstance(seg, list) else seg.get("start")

    entries = []
    for i, seg in enumerate(raw):
        start = start_of(seg)
        nxt = next((start_of(s) for s in raw[i + 1:] if start_of(s) is not None), None)
        if isinstance(seg, dict) and isinstance(start, int) and isinstance(nxt, int) and start < nxt:
            entries.append((seg, int(start), int(nxt), any(start_of(s) is not None for s in raw[i + 1:]
                                                            if isinstance(s, dict))))
    # (every segment's addresses, those past the end of this ROM included: an unpacked program's)
    codes = [(int(seg["vram"]), int(seg["vram"]) + end - start) for seg, start, end, _ in entries
             if seg.get("type") == "code" and isinstance(seg.get("vram"), int)]
    entries = [entry for entry in entries if entry[2] <= len(rom)]
    found = []  # (rom_start, vram, vram_end, the run's code as (rom_start, rom_end))
    between = set()  # rom_start of the runs whose next segment is code at the same offset
    run = None  # (delta, rom end, code ranges)
    for seg, start, end, bounded in entries:
        vram = seg.get("vram")
        if seg.get("type") == "code" and isinstance(vram, int):
            if run is not None and run[1] == start and run[0] == vram - start:
                run = (run[0], end, run[2] + [(start, end)])
            else:
                run = (vram - start, end, [(start, end)])
        elif seg.get("type") == "bin" and vram is None and run is not None and run[1] == start:
            if bounded:
                found.append((start, start + run[0], end + run[0], run[2]))
                if any(other.get("type") == "code" and begin == end and other.get("vram") == begin + run[0]
                       for other, begin, _, _ in entries):
                    between.add(start)
            run = (run[0], end, run[2])
        else:
            run = None
    tables = [(table, vram, vram + end - start, (start, end)) for seg, start, end, _ in entries
              if seg.get("type") == "code" and isinstance(vram := seg.get("vram"), int)
              for table in _jump_tables(rom, start, end, big_endian)]
    order = "big" if big_endian else "little"
    out = []
    for i, (start, lo, hi, code) in enumerate(found):
        if any(v < hi and lo < e for v, e in codes):
            continue
        if any(j != i and other[1] < hi and lo < other[2] for j, other in enumerate(found)):
            continue
        inside = [(table, v, e, where in code) for table, v, e, where in tables if lo <= table and table + 4 <= hi]
        # Every switch whose table lies here, the run's own and any other code's, must jump into its own segment:
        # the bytes of another overlay at these addresses, or packed data, fail that and the run stays unread.
        if inside and (start in between or any(own for *_, own in inside)) and all(
                v <= int.from_bytes(rom[start + table - lo:start + table - lo + 4], order) < e
                for table, v, e, _ in inside):
            out.append((start, lo, hi))
    return out


def _jump_tables(rom: bytes, start: int, end: int, big_endian: bool):
    """The addresses of the jump tables the code in rom[start:end] dispatches through: a `lui` of the table's upper
    half, a `lw` of the case's entry through it (`%lo`), and a `jr` to what it loaded, a few instructions apart."""
    order = ">" if big_endian else "<"
    uppers, loaded = {}, {}
    for i, w in enumerate(struct.unpack_from(f"{order}{(end - start) // 4}I", rom, start)):
        op, rs, rt = w >> 26, (w >> 21) & 31, (w >> 16) & 31
        if op == 15 and rt:
            uppers[rt] = ((w & 0xffff) << 16, i)
        elif op == 35 and (upper := uppers.get(rs)) and i - upper[1] <= 12:
            loaded[rt] = ((upper[0] + (((w & 0xffff) ^ 0x8000) - 0x8000)) & 0xffffffff, i)
        elif w & 0xfc1fffff == 8 and rs != 31 and (table := loaded.get(rs)) and i - table[1] <= 8:
            yield table[0]


def code_ranges(path: Path) -> list[tuple[int, int, int]]:
    """(rom_start, rom_end, vram) of what splat's config disassembles as code: the code subsegments, not the data
    after them (create_config can file a whole segment's tail as data: GoldenEye's game code is in there)."""
    import yaml

    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    out = []
    for seg in segments_from_config(path):
        raw = next((s for s in cfg.get("segments", []) if isinstance(s, dict) and s.get("start") == seg.rom_start
                    and s.get("type") == "code"), None)
        subs = [s for s in (raw or {}).get("subsegments") or [] if isinstance(s, list) and s]
        if not subs:
            out.append((seg.rom_start, seg.rom_end, seg.vram))
            continue
        for i, sub in enumerate(subs):
            if len(sub) > 1 and sub[1] in ("c", "asm", "hasm"):
                end = next((int(t[0]) for t in subs[i + 1:]), seg.rom_end)
                out.append((int(sub[0]), end, seg.vram + int(sub[0]) - seg.rom_start))
    return out


def source_units(path: Path) -> list[tuple[int, int, int, str]]:
    """Named C subsegments with their ROM and RAM bounds, for functions recovered inside an existing parent."""
    import yaml

    cfg = yaml.safe_load(path.read_text(encoding='utf-8'))
    out = []
    for seg in segments_from_config_data(cfg):
        raw = next(s for s in cfg['segments'] if isinstance(s, dict) and s.get('type') == 'code'
                   and s.get('start') == seg.rom_start)
        subs = [s for s in raw.get('subsegments', []) or [] if isinstance(s, list) and s]
        for i, sub in enumerate(subs):
            if len(sub) < 3 or sub[1] != 'c' or not isinstance(sub[2], str):
                continue
            end = int(subs[i+1][0]) if i+1 < len(subs) else seg.rom_end
            out.append((int(sub[0]), end, seg.vram + int(sub[0]) - seg.rom_start, sub[2]))
    return out


def named_symbols(workdir: Path) -> dict[str, int]:
    """The functions symbol_addrs.txt names (splat's entrypoint and main, a PS2 game's own symbols)."""
    out = {}
    path = workdir / "symbol_addrs.txt"
    if path.exists():
        for m in re.finditer(r"^\s*([A-Za-z_]\w*)\s*=\s*(0x[0-9A-Fa-f]+)\s*;.*type:func", path.read_text(encoding="utf-8"), re.M):
            out[m.group(1)] = int(m.group(2), 16)
    return out


_SYMBOL_LINE = re.compile(r"^\s*(\S+)\s*=\s*(0x[0-9A-Fa-f]+)\s*;", re.M)


def _add_symbols(path: Path, lines: list[str]) -> None:
    """More lines for splat's symbol_addrs.txt, leaving out a name or an address it has already (two names for one
    address, or one name twice, stop splat)."""
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    names = {m.group(1) for m in _SYMBOL_LINE.finditer(text)}
    addresses = {int(m.group(2), 16) for m in _SYMBOL_LINE.finditer(text)}
    keep = []
    for line in lines:
        m = _SYMBOL_LINE.match(line)
        if m and m.group(1) not in names and int(m.group(2), 16) not in addresses:
            names.add(m.group(1))
            addresses.add(int(m.group(2), 16))
            keep.append(line)
    path.write_text(text.rstrip("\n") + "\n" + "".join(f"{line}\n" for line in keep), encoding="utf-8")


MIN_SPLAT_VERSION = (0, 50, 1)


def _check_splat_runtime():
    """A pulled checkout can still be running an older, incompatible environment."""
    try:
        installed = metadata.version('splat64')
    except metadata.PackageNotFoundError:
        installed = 'not installed'
    parsed = re.match(r'^(\d+)\.(\d+)\.(\d+)', installed)
    if parsed is None or tuple(map(int, parsed.groups())) < MIN_SPLAT_VERSION:
        raise RomError(f'Installed Splat is {installed}; decompdecomp requires 0.50.1 or newer. '
                       'Close the app, update its dependencies (run uv sync in a development checkout), '
                       'then reopen it and retry. For a packaged app, use an updated app build.')


def _splat(args: list[str], cwd: Path, log=print, *, timeout: float | None = None, module: str = 'splat') -> str:
    _check_splat_runtime()
    check_cancel = getattr(log, "check_cancel", lambda: None)
    from ..limits import capped
    deadline=time.monotonic()+timeout if timeout is not None else None
    proc = subprocess.Popen(capped([console_python(), "-m", module, *args]), cwd=cwd, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    try:
        while True:
            check_cancel()
            if deadline is not None and time.monotonic()>=deadline:raise DisassemblyTimeout('Staged disassembly exceeded its time limit; no reviewed changes were published.')
            try:
                stdout, stderr = proc.communicate(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                continue
    except BaseException:
        stop_process_tree(proc)
        raise
    out = (stdout + stderr).strip()
    if proc.returncode != 0:
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        reason = next((ln for ln in reversed(lines) if re.match(r"\w*(Error|Exception)\b|error:", ln)), lines[-1] if lines else "")
        err = RomError(f"splat could not analyse this ROM: {reason[:300]}")
        err.details = out[-6000:]  # the whole story, for the log and a bug report
        raise err
    return out


REGINFO_BYTES = 24  # a MIPS .reginfo section: the registers the program uses and its $gp
ELF32_HEADER, SECTION_HEADER = 0x34, 40  # an ELF32's header and one section header, in bytes
PT_LOAD, PF_X, PF_W = 1, 1, 2  # a loaded program header, and its executable and writable flags
SHT_PROGBITS, SHT_STRTAB = 1, 3
SHF_WRITE, SHF_ALLOC, SHF_EXECINSTR = 1, 2, 4


def _readable_for_splat(program: Path, as_ps2: bool = False) -> bytes | None:
    """A program splat and objcopy can lay out, for the while they do (the caller puts the user's bytes back): its
    section headers made from its program headers when it has none (_with_section_headers), a section falsely
    marked MIPS_REGINFO hidden (_without_false_reginfo), and with `as_ps2` its flags saying the R5900
    (_as_r5900). Returns the program's bytes as they were, or None when nothing needed changing."""
    data = program.read_bytes()
    fixed = _with_section_headers(data) or data
    fixed = _without_false_reginfo(fixed) or fixed
    fixed = _as_r5900(fixed) if as_ps2 else fixed
    if fixed is data:
        return None
    program.write_bytes(fixed)
    return data


def _with_section_headers(data: bytes) -> bytes | None:
    """#44: a PS2 game's program without section headers (none, or a string table index past them) stopped splat's
    ELF reader ("IndexError: list index out of range"), and objcopy flattens sections, not segments. The program
    headers say the same: one section per loaded segment with bytes, and a string table for their names. None: it
    has its section headers, or isn't a 32-bit little-endian ELF."""
    if len(data) < ELF32_HEADER or data[:4] != b"\x7fELF" or data[4:6] != b"\x01\x01":
        return None
    phoff, = struct.unpack_from("<I", data, 28)
    phentsize, phnum, _, shnum, shstrndx = struct.unpack_from("<HHHHH", data, 42)
    if shnum and shstrndx < shnum:
        return None
    loads = []
    for i in range(phnum):
        kind, off, vaddr, _, filesz, _, flags, align = struct.unpack_from("<8I", data, phoff + phentsize * i)
        if kind == PT_LOAD and filesz:
            loads.append((off, vaddr, filesz, flags, align))
    if not loads:
        return None
    names = bytearray(b"\0.shstrtab\0")
    heads = [bytes(SECTION_HEADER)]
    for k, (off, vaddr, filesz, flags, align) in enumerate(loads):
        at = len(names)
        names += (b"main" if k == 0 else b"load%d" % k) + b"\0"
        sh_flags = SHF_ALLOC | (SHF_WRITE if flags & PF_W else 0) | (SHF_EXECINSTR if flags & PF_X else 0)
        heads.append(struct.pack("<10I", at, SHT_PROGBITS, sh_flags, vaddr, off, filesz, 0, 0, align or 1, 0))
    out = bytearray(data)
    out += bytes(-len(out) % 4)
    heads.append(struct.pack("<10I", 1, SHT_STRTAB, 0, 0, len(out), len(names), 0, 0, 1, 0))
    out += names
    out += bytes(-len(out) % 4)
    struct.pack_into("<I", out, 32, len(out))  # e_shoff
    struct.pack_into("<HHH", out, 46, SECTION_HEADER, len(heads), len(heads) - 1)  # e_shentsize, e_shnum, e_shstrndx
    return bytes(out + b"".join(heads))


EF_MIPS_MACH, EF_MIPS_MACH_5900 = 0x00FF0000, 0x00920000  # e_flags' machine variant, and the PS2's


def _as_r5900(data: bytes) -> bytes:
    """splat's create_config reads an ELF program only when its flags say the PS2's R5900; another console's MIPS
    program laid out the same way (the PSP's, once consoles/psp/prx.py fixed it) passes for one while it reads it, and
    its config then names that console's platform (Console.splat_platform)."""
    flags, = struct.unpack_from("<I", data, 36)
    return data[:36] + struct.pack("<I", (flags & ~EF_MIPS_MACH) | EF_MIPS_MACH_5900) + data[40:]


def _set_platform(config: Path, platform: str) -> None:
    text = config.read_text(encoding="utf-8")
    config.write_text(re.sub(r"^(\s*platform:\s*)\w+", rf"\g<1>{platform}", text, count=1, flags=re.M), encoding="utf-8")


def _without_false_reginfo(data: bytes) -> bytes | None:
    """objcopy refuses a whole program ("Unable to recognise the format") over a section marked MIPS_REGINFO that
    isn't one: a PS2 game's 1 MB `.dnas`, the encrypted DNAS block of its online play, loaded far above its code.
    Such a section is hidden (no type, not loaded) while splat and objcopy lay the program out; it's data, not code.
    None: no such section."""
    import io

    from elftools.elf.elffile import ELFFile

    elf = ELFFile(io.BytesIO(data))
    odd = [i for i, s in enumerate(elf.iter_sections())
           if s["sh_type"] == "SHT_MIPS_REGINFO" and s["sh_size"] != REGINFO_BYTES and s["sh_flags"]]
    if not odd:
        return None
    out = bytearray(data)
    for i in odd:
        struct.pack_into("<II" if elf.little_endian else ">II", out, elf["e_shoff"] + elf["e_shentsize"] * i + 4, 0, 0)
    return bytes(out)


def _drop_leading_gap(program: Path, image: Path, config: Path) -> None:
    """splat lays a program out from its first section with bytes, but the flat image it has objcopy make (with
    --gap-fill) starts at the lowest loaded section, empty ones too: a PS2 game whose CodeWarrior link left an empty
    section 1 MB below its code had every function read from the zeros before it (2,286 functions of nops, all set
    aside as not C). Cut the image to start at that first section, once its bytes are seen to be there, and give
    splat the new SHA-1. By address, not by removing sections: a 5.5 MB game whose sections have no names at all
    lost its whole code to `objcopy -R ""`."""
    import hashlib

    from elftools.elf.constants import SH_FLAGS
    from elftools.elf.elffile import ELFFile

    with open(program, "rb") as f:
        loaded = [(s["sh_addr"], s["sh_offset"], s["sh_size"]) for s in ELFFile(f).iter_sections()
                  if s["sh_flags"] & SH_FLAGS.SHF_ALLOC and s["sh_type"] != "SHT_NOBITS"]
    with_bytes = [s for s in loaded if s[2]]
    if not with_bytes:
        return
    first = min(with_bytes)
    gap = first[0] - min(addr for addr, _, _ in loaded)
    flat = image.read_bytes()
    head = program.read_bytes()[first[1]:first[1] + min(first[2], 256)]
    if gap <= 0 or flat[gap:gap + len(head)] != head:  # (not the image this expects: leave it as objcopy made it)
        return
    image.write_bytes(flat[gap:])
    sha1 = hashlib.sha1(flat[gap:]).hexdigest()
    config.write_text(re.sub(r"^sha1: \w+", f"sha1: {sha1}", config.read_text(encoding="utf-8"), count=1, flags=re.M),
                      encoding="utf-8")


LAYOUT_CHECK_BYTES = 256  # how much of each section must be where the image is thought to keep it
_SEGMENT = re.compile(r"^(?=  - )", re.M)  # each segment of splat's config, and its end (subsegments sit deeper)
_SEGMENT_START = re.compile(r"^(    start: )0x([0-9A-Fa-f]+)", re.M)
_SEGMENT_VRAM = re.compile(r"^    vram: 0x([0-9A-Fa-f]+)", re.M)
_SUBSEGMENT_START = re.compile(r"^(      - \[)0x([0-9A-Fa-f]+)", re.M)
_CONFIG_END = re.compile(r"^(  - \[)0x[0-9A-Fa-f]+(\])", re.M)


def _starts_by_address(program: Path, image: Path, config: Path) -> None:
    """splat's config places each section by its offset in the ELF file (from the first section), but the flat image
    objcopy makes lays the program out by address: the two agree only until a stretch of addresses with no bytes in
    the file. A PS2 game keeps its engine at 0x01000000, above a 4.6 MB .bss: its config sent splat into the image's
    zeros there (3.5 MB of engine split as three "functions" of nops), and every section after the .bss to the wrong
    bytes. Each segment and section starts where the image keeps it, once every section's bytes are seen there."""
    from elftools.elf.constants import SH_FLAGS
    from elftools.elf.elffile import ELFFile

    data = program.read_bytes()
    with open(program, "rb") as f:
        counted = [s for s in ELFFile(f).iter_sections() if s["sh_flags"] & SH_FLAGS.SHF_ALLOC and s["sh_size"]
                   and s["sh_type"] in ("SHT_PROGBITS", "SHT_NOBITS") and s.name != ".mwcats"]
    with_bytes = [(s["sh_addr"], s["sh_offset"], s["sh_size"]) for s in counted if s["sh_type"] == "SHT_PROGBITS"]
    if not with_bytes:
        return
    first_offset = min(s["sh_offset"] for s in counted)  # where splat counts from (ps2elfinfo)
    base = min(addr for addr, _, _ in with_bytes)  # the image's first byte (_drop_leading_gap)
    flat = image.read_bytes()
    for addr, offset, size in with_bytes:
        head = data[offset:offset + min(size, LAYOUT_CHECK_BYTES)]
        if flat[addr - base:addr - base + len(head)] != head:
            return  # (not an image laid out by address: leave the config as splat wrote it)
    by_offset = {offset - first_offset: addr - base for addr, offset, _ in with_bytes}
    text = config.read_text(encoding="utf-8")
    end = _CONFIG_END.search(text)
    pieces = _SEGMENT.split(text)
    moved = False
    places = []  # (piece index, start in the file's layout, start in the image's)
    for i, piece in enumerate(pieces):
        start, vram = _SEGMENT_START.search(piece), _SEGMENT_VRAM.search(piece)
        if start is None or vram is None:
            continue
        shift = int(vram[1], 16) - base - int(start[2], 16) if int(vram[1], 16) >= base else 0
        places.append((i, int(start[2], 16), int(start[2], 16) + shift))
        if shift == 0:
            continue

        def placed(m, shift=shift):
            old = int(m[2], 16)
            return f"{m[1]}0x{by_offset.get(old, old + shift):06X}"
        pieces[i] = _SUBSEGMENT_START.sub(placed, _SEGMENT_START.sub(lambda m, shift=shift: f"{m[1]}0x{int(m[2], 16) + shift:06X}", piece))
        moved = True
    if not moved or end is None:
        return
    # A segment is its sections' bytes, then its .bss; what lies between it and the next segment in the image is
    # padding. Left to the segment, the padding stretched its addresses over the next one's (the engine's first
    # functions taken for the .bss in front of it) and the zeros after the engine became "functions"; and a
    # .bss as long as its sections' sizes added up (splat's bss_size) overran the gaps between them into the next.
    sections = [(s["sh_addr"], s["sh_size"], s["sh_type"] == "SHT_NOBITS") for s in counted]
    for k, (i, _, new_start) in enumerate(places):
        vram = int(_SEGMENT_VRAM.search(pieces[i])[1], 16)
        limit = int(_SEGMENT_VRAM.search(pieces[places[k + 1][0]])[1], 16) if k + 1 < len(places) else 1 << 32
        inside = [s for s in sections if vram <= s[0] < limit]
        code_end = max((a + n for a, n, nobits in inside if not nobits), default=vram)
        bss_end = max((a + n for a, n, nobits in inside if nobits), default=code_end)
        pieces[i] = re.sub(r"^(    bss_size: )0x[0-9A-Fa-f]+", rf"\g<1>0x{max(0, bss_end - code_end):X}", pieces[i], flags=re.M)
        content_end = new_start + (code_end - vram)
        new_next = places[k + 1][2] if k + 1 < len(places) else len(flat)
        if content_end < new_next:
            pieces[i] += f"  - [0x{content_end:06X}, pad]\n"
    text = _CONFIG_END.sub(lambda m: f"{m[1]}0x{len(flat):X}{m[2]}", "".join(pieces))  # the image's end
    config.write_text(text, encoding="utf-8")


def _patch_config(path: Path) -> tuple[str, str, int]:
    """Disassemble code segments as one C file per segment, so every function gets its own .s file,
    and keep empty functions as asm too (we want every function to be a target).
    Returns (compiler guess, platform, gp value). A segment's second code section (a PSP program's import stubs
    after its .text) is a C file of its own: under the segment's name it found the first's C file there already and
    filed its functions as matched, so none of them became a target."""
    text = path.read_text(encoding="utf-8")
    compiler = (re.search(r"^\s*compiler:\s*(\w+)", text, re.M) or [None, "KMC"])[1]
    platform = (re.search(r"^\s*platform:\s*(\w+)", text, re.M) or [None, "n64"])[1]
    gp = re.search(r"^\s*gp_value:\s*(0x[0-9A-Fa-f]+)", text, re.M)
    out, segment, files = [], None, 0
    for line in text.splitlines():
        m = re.match(r"^\s*- name:\s*(\S+)", line)
        if m:
            segment, files = m.group(1), 0
        m = re.match(r"^(\s*- \[)(0x[0-9A-Fa-f]+), asm(?:, [^\]]*)?\]\s*(#.*)?$", line)
        if m and segment:
            name = segment if not files else f"{segment}_{int(m.group(2), 16):06X}"
            line = f"{m.group(1)}{m.group(2)}, c, {name}]"
            files += 1
        out.append(line)
    text = "\n".join(out) + "\n"
    extra = "  disassemble_all: True\n  auto_decompile_empty_functions: False\n"
    text = text.replace("options:\n", "options:\n" + extra, 1)
    path.write_text(text, encoding="utf-8")
    return compiler, platform, int(gp.group(1), 16) if gp else 0


def parse_function(path: Path, segment: str, big_endian: bool = True) -> RomFunction | None:
    """One function from a spimdisasm .s file. The 8-digit field is the instruction's bytes in file order."""
    order = "big" if big_endian else "little"
    name, lines, labels, piece = None, [], set(), False
    for raw in path.read_text(encoding="utf-8").splitlines():
        if name is None:
            m = _GLABEL.match(raw) or _JLABEL.match(raw)
            if m:
                name, piece = m.group(1), raw.lstrip().startswith("jlabel")
            continue
        m = _INSN.match(raw)
        if m:
            rom, vram, word, mnem, ops = m.groups()
            lines.append(AsmLine(int(rom, 16), int(vram, 16), int.from_bytes(bytes.fromhex(word), order), mnem, ops))
            continue
        m = _LABEL.match(raw)
        if m:
            labels.add(int(m.group(2), 16))
    if name is None or not lines:
        return None
    _trim_padding(lines)
    return RomFunction(name, lines[0].vram, lines[0].rom, segment, lines, path, labels, piece)


def function_asm(fn: RomFunction) -> str:
    """A function's stored assembly, as the project keeps and shows it (spimdisasm's lines with their words)."""
    out = [f"glabel {fn.name}"]
    for ln in fn.lines:
        if alias := fn.entry_aliases.get(ln.vram):
            out.append(f'glabel {alias}')
        if ln.vram in fn.labels:
            out.append(f".L{ln.vram:08X}:")
        # Overlay labels carry a suffix in splat's input. Keep their references consistent
        # with the address labels we emit, including jump-table case entries.
        operands = _LOCAL_REF.sub(lambda m: f".L{m[1]}" if int(m[1], 16) in fn.labels else m[0], ln.operands)
        out.append(f"/* {ln.rom:X} {ln.vram:08X} {ln.word:08X} */  {ln.mnemonic:<10} {operands}".rstrip())
    out.append(f"endlabel {fn.name}")
    return "\n".join(out)


def draft_asm(text: str, name: str, vram: int | None = None, data_asm: str = '') -> str:
    """Recover labels lost by older imports without changing an instruction or extending a target.

    Only recorded instruction addresses can define missing local labels. An outgoing
    branch remains unresolved: inventing its code would hide a boundary problem.
    The row owns this assembly, so its entry name also follows subsequent renames.
    """
    lines = text.splitlines()
    instructions = [(i, _INSN.match(line)) for i, line in enumerate(lines)]
    instructions = [(i, m) for i, m in instructions if m]
    if not instructions or (vram is not None and int(instructions[0][1][2], 16) != vram):
        return text
    if vram is None:
        entries = [m[1] for line in lines if (m := _GLABEL.match(line))]
        if entries != [name]:
            return text  # a project-mode file can contain several functions; do not rename its first one
    entry = next((m for line in lines[:instructions[0][0]] if (m := _GLABEL.match(line))), None)
    if entry and entry[1] != name:
        text = re.sub(r"(?<![\w.])" + re.escape(entry[1]) + r"(?!\w)", lambda _: name, text)
        lines = text.splitlines()
    defined = {m[1] for line in lines if (m := _LABEL.match(line))}
    # Case targets can occur only in the supplied jump table, not in an instruction.
    missing = {m[0]: int(m[1], 16) for m in _LOCAL_REF.finditer(text + '\n' + data_asm) if m[0] not in defined}
    by_address: dict[int, list[str]] = {}
    for label, address in missing.items():
        by_address.setdefault(address, []).append(label)
    # A branch inside the function to an address that is also named like a function (`func_80105044`): the
    # importer had a symbol there (a shared tail another entry jumps into) and left no label, so m2c reported
    # "Cannot find branch target" (it only takes `.L` names as local labels). Only a branch or jump (never a call)
    # to an instruction of this very function: its operand becomes the local label, which is defined there.
    own = {int(m[2], 16) for _, m in instructions}
    entry_names = {name} | {m[1] for line in lines if (m := _GLABEL.match(line))}
    for index, m in instructions:
        if not _BRANCH.match(m[4]):
            continue

        def local(ref):
            address = int(ref[1], 16)
            if ref[0] in entry_names or address not in own:
                return ref[0]
            label = f".L{address:08X}"
            if label not in defined and label not in by_address.setdefault(address, []):
                by_address[address].append(label)
            return label
        operands = _FUNC_REF.sub(local, m[5])
        if operands != m[5]:
            lines[index] = lines[index][:lines[index].rindex(m[5])] + operands
    out = []
    for line in lines:
        if m := _INSN.match(line):
            out.extend(f"{label}:" for label in sorted(by_address.pop(int(m[2], 16), [])))
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def function_from_text(name: str, text: str, segment: str) -> RomFunction:
    """A function from the assembly a project stored (function_asm's form): its lines, labels and words."""
    lines, labels, aliases = [], set(), {}
    alias = None
    for raw in text.splitlines():
        label = _GLABEL.match(raw)
        if label and label[1] != name:
            alias = label[1]
        m = _INSN.match(raw)
        if m:
            rom, vram, word, mnem, ops = m.groups()
            lines.append(AsmLine(int(rom, 16), int(vram, 16), int(word, 16), mnem, ops))
            if alias:
                aliases[int(vram,16)] = alias
                alias = None
            continue
        m = _LABEL.match(raw)
        if m:
            labels.add(int(m.group(2), 16))
    return RomFunction(name, lines[0].vram, lines[0].rom, segment, lines, Path(), labels, entry_aliases=aliases)


def function_from_bytes(name: str, raw: bytes, rom: int, vram: int, segment: str,
                        big_endian: bool = True) -> RomFunction:
    """A function disassembled from its bytes (raw), at `rom` and `vram`: what spimdisasm would write, its branches
    and jumps inside it labelled. For code found after the split (missed_functions)."""
    order = "big" if big_endian else "little"
    lines, labels = [], set()
    end = vram + len(raw)
    for i in range(0, len(raw) - len(raw) % 4, 4):
        word = int.from_bytes(raw[i:i + 4], order)
        insn = rabbitizer.Instruction(word, vram + i)
        target = None
        if insn.isBranch():
            target = insn.getBranchVramGeneric()
        elif insn.isJumpWithAddress() and not insn.isFunctionCall():
            target = insn.getInstrIndexAsVram()
        if target is not None and vram <= target < end:
            labels.add(target)
            text = insn.disassemble(immOverride=f".L{target:08X}")
        else:
            text = insn.disassemble()
        parts = text.split(None, 1)
        lines.append(AsmLine(rom + i, vram + i, word, parts[0] if parts else "nop", parts[1] if len(parts) > 1 else ""))
    return RomFunction(name, vram, rom, segment, lines, Path(), labels)


def _trim_padding(lines: list[AsmLine]) -> None:
    """Alignment padding between source files shows up as nops after the last function of a file.
    A nop in a delay slot belongs to the function; any nop after that is padding."""
    while len(lines) > 1 and lines[-1].word == 0 and not rabbitizer.Instruction(lines[-2].word).hasDelaySlot():
        lines.pop()


def _complete_delay_slot(fn: RomFunction, rom: bytes, big_endian: bool = True) -> None:
    """If the last instruction jumps, its delay slot is part of the function too. The code/data
    boundary guess can cut it off when the function is the last one before data."""
    last = fn.lines[-1]
    if not rabbitizer.Instruction(last.word).hasDelaySlot() or last.rom + 8 > len(rom):
        return
    word = int.from_bytes(rom[last.rom + 4:last.rom + 8], "big" if big_endian else "little")
    insn = rabbitizer.Instruction(word, last.vram + 4)
    fn.lines.append(AsmLine(last.rom + 4, last.vram + 4, word, insn.getOpcodeName(), insn.disassemble().split(None, 1)[1]
                            if " " in insn.disassemble().strip() else ""))


def unlisted_table_targets(name: str, fn: RomFunction, image: bytes, segments: list[Segment],
                           big_endian: bool) -> set[int]:
    """Where a jump table points when no splat segment lists the table (a boot image that keeps its data after its
    code, so `jtbl_800C28A8` is only an undefined symbol). Its bytes are at some segment's own ROM-to-address offset:
    the one whose words, read from the table's address, are 4-aligned addresses inside the function's own segment is
    the table. Words stored without the KSEG0 bit (`0x0042_0018` for `0x8042_0018`) count as the address with it. Three
    such words in a row are taken as the table, and the run ends at the first word that is not one."""
    own = next((s for s in segments if s.contains(fn.vram)), None)
    if own is None:
        return set()
    address, fmt = int(name[-8:], 16), ">I" if big_endian else "<I"
    low, high = own.vram, own.vram + (own.rom_end - own.rom_start)
    for delta in dict.fromkeys(s.rom_start - s.vram for s in segments):
        found: set[int] = set()
        at = address + delta
        while 0 <= at and at + 4 <= len(image):
            word = struct.unpack_from(fmt, image, at)[0]
            word |= 0x80000000 if word and not word & 0x80000000 else 0
            if word % 4 or not low <= word < high:
                break
            found.add(word)
            at += 4
        if len(found) >= 3 and sum(1 for w in found if w > fn.vram) >= 3:
            return {w for w in found if w > fn.vram}
    return set()


def merge_switch_cases(functions: list[RomFunction], data: list[DataSymbol], image: bytes,
                       big_endian: bool, segments: list[Segment] | None = None) -> list[RomFunction]:
    """spimdisasm can end a function with a switch at its first return and make each later case a function of its
    own: it does with EE-GCC 3.2, which loads the jump table with `addiu %lo(jtbl)` and then `lw 0(reg)`. A function
    or piece right after one whose jump table points at it is a case of that switch: put it back. Pieces nothing
    claims are left out, as before. `functions` by address."""
    tables = {d.name: d for d in data if d.kind == "jtbl" and d.rom is not None and d.size >= 4}
    fmt = ">I" if big_endian else "<I"
    out: list[RomFunction] = []
    targets: set[int] = set()  # where the last function's jump tables point
    for fn in functions:
        prev = out[-1] if out else None
        if prev and prev.segment == fn.segment and fn.vram == prev.vram + prev.size and fn.vram in targets:
            prev.lines += fn.lines
            prev.labels |= fn.labels | {fn.vram}
            continue
        if fn.piece:
            continue
        out.append(fn)
        targets = set()
        for name in {m for ln in fn.lines for m in re.findall(r"jtbl_[0-9A-Fa-f]{8}", ln.operands)}:
            t = tables.get(name)
            if t is not None:
                raw = image[t.rom:t.rom + t.size]
                targets |= {w for (w,) in struct.iter_unpack(fmt, raw[:len(raw) // 4 * 4]) if w > fn.vram}
            elif segments and not any(d.name == name for d in data):
                targets |= unlisted_table_targets(name, fn, image, segments, big_endian)
    return out


def parse_data(path: Path) -> list[DataSymbol]:
    """Data symbols from a spimdisasm data/rodata/bss file: name, address, size and a short preview."""
    syms: list[DataSymbol] = []
    current: DataSymbol | None = None
    end = 0

    def close():
        if current is not None:
            current.size = max(current.size, end - current.vram)
            syms.append(current)

    bss = path.name.endswith(".bss.s")
    for raw in path.read_text(encoding="utf-8").splitlines():
        m = _DLABEL.match(raw)
        if m:
            close()
            current, end = None, 0
            pending = m.group(1)
            current = DataSymbol(pending, 0, None, 0, "bss" if bss else "jtbl" if pending.startswith("jtbl_") else "data")
            continue
        m = _DATA.match(raw)
        if not m or current is None:
            continue
        rom, vram, directive, arg = m.groups()
        vram = int(vram, 16)
        if not current.vram:
            current.vram = vram
            current.rom = int(rom, 16) if rom else None
        if directive == "space":
            end = vram + int(arg, 0)
        elif directive == "asciz" or directive == "ascii":
            current.kind = "string"
            current.preview = current.preview or arg[:120]
            s = arg.strip('"').encode().decode("unicode_escape", errors="replace")
            end = vram + len(s) + (directive == "asciz")
        else:
            width = {"word": 4, "float": 4, "short": 2, "half": 2, "byte": 1, "double": 8, "dword": 8}.get(directive, 4)
            end = vram + width
    close()
    return [s for s in syms if s.vram]


def split_rom(workdir: Path, rom_name: str = "rom.z64", console=None, picked: Picked | None = None, log=print,
              symbols: list[str] | None = None) -> SplitResult:
    """Run splat on workdir/rom_name and return every function and data symbol it found. `console` (the one splat
    names, if not given) adds code splat's own look misses (Console.add_code: the N64's segments loaded later, a PS2
    disc's overlays in `picked`). `symbols`: symbol_addrs.txt lines for the names the program itself has (a PS2
    game's symbol table), which splat splits by."""
    import yaml

    from .. import consoles

    log("finding the code segments")
    args = ["create_config", rom_name]
    objcopy = None
    if (workdir / rom_name).read_bytes()[:4] == b"\x7fELF":  # splat flattens ELF programs with objcopy
        from .. import toolchains

        toolchains.ensure("binutils-mips", log=lambda *_: None)
        objcopy = toolchains.binary("binutils-mips").parent / "mips-ps2-decompals-objcopy"
        args[1:1] = ["--objcopy", str(objcopy)]
    platform = console.splat_platform if console is not None else ""
    original = _readable_for_splat(workdir / rom_name, as_ps2=bool(platform)) if objcopy else None
    try:
        out = console.splat_create_config(args, workdir, log) if console is not None else _splat(args, workdir, log)
    finally:
        if original is not None:  # the user's program, as it was
            (workdir / rom_name).write_bytes(original)
    configs = sorted(p for p in workdir.glob("*.yaml"))
    if not configs:
        raise RomError("splat did not produce a config for this ROM")
    config = configs[0]
    if objcopy and platform:
        _set_platform(config, platform)
    image = (yaml.safe_load(config.read_text(encoding="utf-8")).get("options") or {}).get("target_path") or rom_name
    if objcopy:
        _drop_leading_gap(workdir / rom_name, workdir / image, config)
        _starts_by_address(workdir / rom_name, workdir / image, config)
    compiler, platform, gp = _patch_config(config)
    console = console or consoles.get(platform)
    big_endian = console.big_endian
    if symbols:
        _add_symbols(workdir / "symbol_addrs.txt", symbols)
    image = console.add_code(config, workdir, image, picked or Picked(console.id, b"", rom_name, {}, big_endian), log)
    return _finish_split(workdir, rom_name, config, image, console, compiler, gp, out, symbols, log)


def _finish_split(workdir, rom_name, config, image, console, compiler, gp, out, symbols, log):
    """Disassemble a prepared config and build the usual repaired split result."""
    big_endian = console.big_endian
    log("disassembling every function")
    out += "\n" + _splat(["split", config.name], workdir, log)

    from . import mixed_code

    out += mixed_code.run(config, workdir, image, console, log)

    functions = []
    inline_data = set()
    for path in sorted((workdir / "asm" / "nonmatchings").glob("*/*.s")):
        fn = parse_function(path, path.parent.name, big_endian)
        if fn is not None:
            functions.append(fn)
        else:
            inline_data.update((item.rom, item.vram) for item in parse_data(path) if item.rom is not None and item.size)
    for path in sorted((workdir / "asm" / "nonmatchings").glob("*/*/*.s")):  # per-file subfolders
        fn = parse_function(path, path.parent.parent.name, big_endian)
        if fn is not None:
            functions.append(fn)
        else:
            inline_data.update((item.rom, item.vram) for item in parse_data(path) if item.rom is not None and item.size)
    seen: dict[str, int] = {}  # name -> where it is in the image
    unique = []
    rom = (workdir / image).read_bytes()
    for fn in sorted(functions, key=lambda f: (f.segment, f.vram)):
        if fn.name in seen:  # overlays loaded at the same address: the same name in another overlay
            if seen[fn.name] == fn.rom:
                continue
            fn.name = f"{fn.segment}_{fn.name}"
            if fn.name in seen:
                continue
        seen[fn.name] = fn.rom
        _complete_delay_slot(fn, rom, big_endian)
        unique.append(fn)
    data = []
    for path in sorted((workdir / "asm" / "data").glob("**/*.s")):
        data += parse_data(path)
    from .automatic_boundaries import repair

    segments = segments_from_config(config)
    unique = repair(unique, data, rom, console, segments, symbols=symbols or (), log=log, inline_data=inline_data)
    unique = merge_switch_cases(unique, data, rom, big_endian, segments)
    if not unique:
        raise RomError("splat found no functions; the code may be compressed or use an unusual boot layout")
    segments = segments_from_config(config)
    gp = gp or console.find_gp(rom, (workdir / rom_name).read_bytes(), segments)
    log(f"found {len(unique)} functions and {len(data)} data symbols")
    import json

    coverage = workdir / 'analysis-coverage.json'
    notes = json.loads(coverage.read_text(encoding='utf-8')) if coverage.is_file() else []
    return SplitResult(unique, data, segments, named_symbols(workdir), config, compiler, out, console.id, gp, image, notes)


def resplit_prepared(workdir: Path, rom_name: str, console, previous: SplitResult, *, symbols=None, log=print):
    """Re-read an import's verified config without recreating it or rediscovering its code segments."""
    import shutil

    # Splat may leave files for functions removed by a new boundary. The import workdir is private.
    shutil.rmtree(workdir / 'asm', ignore_errors=True)
    return _finish_split(workdir, rom_name, previous.config_path, previous.image, console,
                         previous.compiler, previous.gp, previous.log, symbols, log)


def _left_out_filler(lines: dict, address: int) -> bool:
    """One alignment word as automatic_boundaries.alignment_fill drops it: at an address ending in 4 or C, right after
    a `jr $ra` and its delay slot, right before the next stored piece."""
    previous = lines.get(address - 8)
    return (address % 8 == 4 and previous is not None and previous.word == 0x03E00008
            and address - 4 in lines and address + 4 in lines)


def join_saved_functions(parts: list[RomFunction], start: int, end: int, read) -> RomFunction:
    """Join stored functions back into the first one, filling any zero gap from the game's bytes (saves.py).

    A saved layout can be older than the project's splits: this undoes a split whose pieces are consecutive. Every
    place must be accounted for; a gap that isn't zero padding raises ValueError, so a layout that crosses a real
    gap is refused rather than joining the wrong bytes."""
    from dataclasses import replace

    lines = {line.vram: line for part in parts for line in part.lines if line.vram < end}
    missing = set(range(start, end, 4)) - lines.keys()
    if missing:
        for address in sorted(missing):
            # split_function trims padding from the head; undoing that split must put its original NOPs back.
            data = read(address, 4)
            previous = lines[max(at for at in lines if at < address)]
            rom = previous.rom + address - previous.vram
            if data == b"\0\0\0\0":
                lines[address] = AsmLine(rom, address, 0, "nop", "")
            elif _left_out_filler(lines, address):
                # automatic_boundaries leaves non-zero alignment filler out of the functions; a layout saved before
                # that kept it inside one, so it goes back as the game's own word.
                order = "big" if read(address - 8, 4) == (0x03E00008).to_bytes(4, "big") else "little"
                word = int.from_bytes(data, order)
                lines[address] = AsmLine(rom, address, word, ".word", f"0x{word:08X}")
            else:
                raise ValueError("the saved layout crosses a gap in this analysis")
    if sorted(lines) != list(range(start, end, 4)):
        raise ValueError("the saved layout crosses a gap in this analysis")
    return replace(parts[0], lines=[lines[address] for address in sorted(lines)],
                   labels=set().union(*(part.labels for part in parts)),
                   entry_aliases={a:n for part in parts for a,n in part.entry_aliases.items() if start <= a < end})
