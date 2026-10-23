"""Split a game's program (an N64 ROM, a PS2 ELF) into functions and data with splat (MIT) and read back what it found.

splat's own create_config finds the boot entry point, the main code segment, .bss and the compiler;
`splat split` then disassembles every function (spimdisasm, MIT) with symbols for every address it
can pair up (%hi/%lo, jal targets). We parse that output into plain records the rest of the app uses.
"""

from __future__ import annotations

import re
import struct
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import rabbitizer

from ..hostenv import NO_WINDOW, console_python, stop_process_tree
from . import Picked, RomError

_INSN = re.compile(r"^\s*/\* ([0-9A-F]+) ([0-9A-F]{8}) ([0-9A-F]{8}) \*/\s+(\S+)\s*(.*?)\s*$")
_LABEL = re.compile(r"^\s*(?:jlabel\s+)?(\.?L[0-9A-F]{8}):?\s*$")
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

    @property
    def size(self) -> int:
        return 4 * len(self.lines)


def split_function(fn: RomFunction, at: int, new_name: str) -> tuple[RomFunction, RomFunction]:
    """Cut a function in two at address `at`. The disassembler can't always see where a function ends
    (one that never returns runs straight into the next), so agents can split what it merged."""
    if not fn.vram < at < fn.vram + fn.size or (at - fn.vram) % 4:
        raise ValueError(f"0x{at:08X} is not an instruction boundary inside {fn.name}")
    k = (at - fn.vram) // 4
    head = RomFunction(fn.name, fn.vram, fn.rom, fn.segment, fn.lines[:k], fn.asm_path, {a for a in fn.labels if a < at})
    _trim_padding(head.lines)
    tail = RomFunction(new_name, at, fn.lines[k].rom, fn.segment, fn.lines[k:], fn.asm_path, {a for a in fn.labels if a >= at})
    return head, tail


@dataclass
class DataSymbol:
    name: str
    vram: int
    rom: int | None  # None for .bss
    size: int
    kind: str  # data | string | jtbl | bss
    preview: str = ""


@dataclass
class Segment:
    name: str
    rom_start: int
    rom_end: int
    vram: int

    def contains(self, address: int, length: int = 1) -> bool:
        return self.vram <= address and address + length <= self.vram + (self.rom_end - self.rom_start)


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


def segments_from_config(path: Path) -> list[Segment]:
    """Code segments (ROM range + load address) from a splat config."""
    import yaml

    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
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
        out.append(Segment(seg.get("name", f"{seg['start']:X}"), int(seg["start"]), int(nxt), int(seg["vram"])))
    return out


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


def _splat(args: list[str], cwd: Path, log=print) -> str:
    check_cancel = getattr(log, "check_cancel", lambda: None)
    proc = subprocess.Popen([console_python(), "-m", "splat", *args], cwd=cwd, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    try:
        while True:
            check_cancel()
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


def _readable_for_splat(program: Path) -> bytes | None:
    """A program splat and objcopy can lay out, for the while they do (the caller puts the user's bytes back): its
    section headers made from its program headers when it has none (_with_section_headers), and a section falsely
    marked MIPS_REGINFO hidden (_without_false_reginfo). Returns the program's bytes as they were, or None when
    nothing needed changing."""
    data = program.read_bytes()
    fixed = _with_section_headers(data) or data
    fixed = _without_false_reginfo(fixed) or fixed
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
    Returns (compiler guess, platform, gp value)."""
    text = path.read_text(encoding="utf-8")
    compiler = (re.search(r"^\s*compiler:\s*(\w+)", text, re.M) or [None, "KMC"])[1]
    platform = (re.search(r"^\s*platform:\s*(\w+)", text, re.M) or [None, "n64"])[1]
    gp = re.search(r"^\s*gp_value:\s*(0x[0-9A-Fa-f]+)", text, re.M)
    out, segment = [], None
    for line in text.splitlines():
        m = re.match(r"^\s*- name:\s*(\S+)", line)
        if m:
            segment = m.group(1)
        m = re.match(r"^(\s*- \[)(0x[0-9A-Fa-f]+), asm(?:, [^\]]*)?\]\s*(#.*)?$", line)
        if m and segment:
            line = f"{m.group(1)}{m.group(2)}, c, {segment}]"
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
            labels.add(int(m.group(1).lstrip(".L"), 16))
    if name is None or not lines:
        return None
    _trim_padding(lines)
    return RomFunction(name, lines[0].vram, lines[0].rom, segment, lines, path, labels, piece)


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


def merge_switch_cases(functions: list[RomFunction], data: list[DataSymbol], image: bytes,
                       big_endian: bool) -> list[RomFunction]:
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
    original = _readable_for_splat(workdir / rom_name) if objcopy else None
    try:
        out = _splat(args, workdir, log)
    finally:
        if original is not None:  # the user's program, as it was
            (workdir / rom_name).write_bytes(original)
    configs = sorted(p for p in workdir.glob("*.yaml"))
    if not configs:
        raise RomError("splat did not produce a config for this ROM")
    config = configs[0]
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
    log("disassembling every function")
    out += "\n" + _splat(["split", config.name], workdir, log)

    functions = []
    for path in sorted((workdir / "asm" / "nonmatchings").glob("*/*.s")):
        fn = parse_function(path, path.parent.name, big_endian)
        if fn is not None:
            functions.append(fn)
    for path in sorted((workdir / "asm" / "nonmatchings").glob("*/*/*.s")):  # per-file subfolders
        fn = parse_function(path, path.parent.parent.name, big_endian)
        if fn is not None:
            functions.append(fn)
    seen: dict[str, int] = {}  # name -> where it is in the image
    unique = []
    rom = (workdir / image).read_bytes()
    for fn in sorted(functions, key=lambda f: f.vram):
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
    unique = merge_switch_cases(unique, data, rom, big_endian)
    if not unique:
        raise RomError("splat found no functions; the code may be compressed or use an unusual boot layout")
    segments = segments_from_config(config)
    gp = gp or console.find_gp(rom, (workdir / rom_name).read_bytes(), segments)
    log(f"found {len(unique)} functions and {len(data)} data symbols")
    return SplitResult(unique, data, segments, named_symbols(workdir), config, compiler, out, console.id, gp, image)
