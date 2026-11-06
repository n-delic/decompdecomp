"""Split a Game Boy Advance ROM into functions with luvdis (MIT), and name what its own look misses.

luvdis follows the code from the entry point and guesses functions called from known code. Code nothing calls (or
reaches only through pointers) is found by a second pass: Thumb prologues (`push {..., lr}`) in what's still
unclaimed bytes are handed back to luvdis as function starts, until nothing new turns up (candidates.py).

The result is the ROM mode's shape: every function's bytes, its luvdis assembly, its processor mode (Thumb or ARM)
and the addresses its code refers to. The judge (`arch/arm.py`) compares a candidate with those bytes.
"""

from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from ... import toolchains
from ...arch.arm import arm_b_dest, thumb_bl_dest
from ...limits import capped
from ...rom import RomError
from ...rom.split import Segment, SplitResult
from . import candidates

BASE = 0x08000000
LUVDis_SECONDS = 3600
SEARCH_SECONDS = 1800


@dataclass
class GbaFunction:
    """One function in the ROM: its code, luvdis's assembly of it, its mode and the addresses it refers to."""
    name: str
    vram: int
    rom: int
    code: bytes
    asm: str
    n_insns: int
    thumb: bool = True
    refs: list[tuple[int, int, int]] = field(default_factory=list)  # (offset, kind, address)
    asm_only: bool = False  # a known hand-written helper (libgcc's _call_via_rN)
    segment: str = "rom"

    @property
    def size(self) -> int:
        return len(self.code)


def _luvdis(rom: Path, workdir: Path, cfg_in: Path | None) -> tuple[str, list[int]]:
    """Run luvdis (in its own pinned environment) on the ROM and return (assembly, function addresses)."""
    python = toolchains.venv_bin(toolchains.ensure("luvdis", log=lambda *_: None) / "venv", "python")
    out, cfg = workdir / "rom.s", workdir / "functions.cfg"
    args = [str(python), "-I", str(Path(__file__).with_name("luvdis_run.py")), "disasm", str(rom), "--min-calls", "1",
            "--min-length", "2", "-o", str(out), "-co", str(cfg)]
    if cfg_in is not None:
        args[5:5] = ["-c", str(cfg_in)]
    try:
        res = subprocess.run(capped(args), capture_output=True, text=True, timeout=LUVDis_SECONDS,
                             encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        raise RomError("luvdis (the GBA disassembler) took more than an hour on this ROM and was stopped. Please "
                       "report it with the log: the game's size and title help") from None
    if res.returncode != 0 or not out.exists():
        raise RomError("luvdis couldn't disassemble the ROM: " + (res.stderr or res.stdout)[-400:])
    found = sorted({int(m.group(1), 16) for m in re.finditer(r"^(?:[a-z_]+[ \t]+)?(0x[0-9A-Fa-f]+)",
                                                             cfg.read_text(encoding="utf-8"), re.M)})
    return out.read_text(encoding="utf-8"), found


def find_functions(rom_path: Path, workdir: Path, rounds: int = 4, log=lambda *_: None,
                   budget: float = SEARCH_SECONDS) -> tuple[str, list[int]]:
    """(assembly, function addresses) for the ROM, with prologue hunting in what luvdis left as data. The extra
    rounds stop once `budget` seconds have gone by (a 32 MiB ROM takes minutes a round)."""
    rom = rom_path.read_bytes()
    started = time.time()
    asm, found = _luvdis(rom_path, workdir, None)
    log(f"luvdis followed the code from the start: {len(found)} functions ({time.time() - started:.0f}s)")
    for k in range(rounds):
        if time.time() - started > budget:
            log("stopping the search for more here: it has run half an hour (agents can split what's left)")
            break
        extra = [a for a in candidates.candidates(rom, found) if a not in found]
        if not extra:
            break
        log(f"round {k + 2}: {len(extra)} more function starts in code nothing calls directly")
        cfg = workdir / "candidates.cfg"
        cfg.write_text("".join(f"thumb_func 0x{a:X}\n" for a in sorted(set(found) | set(extra))), encoding="utf-8")
        asm, found2 = _luvdis(rom_path, workdir, cfg)
        if set(found2) == set(found):
            break
        found = found2
    return asm, found


def call_via(code: bytes) -> str | None:
    """libgcc's `_call_via_rN` thunks (`bx rN` and a nop): hand-written assembly no C compiles to."""
    if len(code) in (2, 4) and code[1] == 0x47 and code[0] & 0x87 == 0 and code[2:] in (b"", b"\xc0\x46", b"\0\0"):
        reg = (code[0] >> 3) & 0xF
        if reg < 13:  # `bx lr` alone is also an empty C function
            return f"_call_via_r{reg}"
    return None


def _asm_blocks(asm: str) -> dict[int, tuple[str, bool]]:
    """address -> (luvdis's assembly for that function, is Thumb)."""
    out: dict[int, tuple[str, bool]] = {}
    current, lines, thumb = None, [], True
    for line in asm.splitlines():
        m = re.match(r"\s*(thumb|arm|non_word_aligned_thumb)_func_start\s+\w*?([0-9A-F]{7,8})\b", line)
        if m:
            if current is not None:
                out[current] = ("\n".join(lines).rstrip() + "\n", thumb)
            current, lines, thumb = int(m.group(2), 16), [line], m.group(1) != "arm"
        elif current is not None:
            lines.append(line)
    if current is not None:
        out[current] = ("\n".join(lines).rstrip() + "\n", thumb)
    return out


def _refs(code: bytes, addr: int, literals: set[int], off: int, thumb: bool) -> list[tuple[int, int, int]]:
    """Calls and data pointers (literal-pool words holding RAM/ROM addresses) in a function."""
    out = []
    if not thumb:
        for at in range(0, len(code) - 3, 4):
            dest = arm_b_dest(code, at, addr + at)
            if dest is not None:
                word = int.from_bytes(code[at:at + 4], "little")
                out.append((at, 28 if word & (1 << 24) else 29, dest & ~1))
            if off + at in literals:
                value = int.from_bytes(code[at:at + 4], "little")
                if 0x02000000 <= value < 0x0A000000:
                    out.append((at, 2, value & ~1 if value & 1 and value >= BASE else value))
        return out
    at = 0
    while at + 4 <= len(code):
        if off + at in literals and at % 4 == 0:
            value = int.from_bytes(code[at:at + 4], "little")
            if 0x02000000 <= value < 0x0A000000:
                out.append((at, 2, value & ~1 if value & 1 and value >= BASE else value))
            at += 4
            continue
        dest = thumb_bl_dest(code, at, addr + at)
        if dest is not None:
            out.append((at, 10, dest & ~1))
            at += 4
            continue
        at += 2
    return out


def split(rom_path: Path, workdir: Path, log=print) -> list[GbaFunction]:
    """Every function in the ROM: its bytes (code and the literal pool it loads), assembly, mode and size."""
    rom = rom_path.read_bytes()
    big = len(rom) > 8 << 20
    log("finding the functions (luvdis, then a pass for code nothing calls)"
        + (f": a {len(rom) >> 20} MiB ROM takes several minutes" if big else ""))
    asm, found = find_functions(rom_path, workdir, log=log)
    blocks = _asm_blocks(asm)
    starts = sorted({a & ~1 for a in found if BASE <= (a & ~1) < BASE + len(rom)})
    literals = candidates.literal_words(rom, starts)
    ends = {start + BASE: end for start, end, _ in candidates.bodies(rom, starts, literals)}
    out = []
    names: set[str] = set()
    for k, addr in enumerate(starts):
        off = addr - BASE
        limit = (starts[k + 1] - BASE) if k + 1 < len(starts) else len(rom)
        end = min(ends.get(addr, limit), limit)
        while end + 4 <= limit and (end in literals or (end % 4 == 2 and end + 2 in literals and
                                                       rom[end:end + 2] in (b"\0\0", b"\xc0\x46"))):
            end += 2 if end % 4 == 2 and end not in literals else 4  # the literal pool after the code
        text, thumb = blocks.get(addr, (f"\tthumb_func_start sub_{addr:08X}\n", True))
        code = rom[off:end]
        helper = call_via(code)
        name = helper or f"sub_{addr:08X}"
        if name in names:  # a game can link a helper twice (two copies of _call_via_r3)
            name = f"{name}_{addr:08X}"
        names.add(name)
        out.append(GbaFunction(name, addr, off, code, text, max(1, len(code) // (2 if thumb else 4)), thumb,
                               _refs(code, addr, literals, off, thumb), helper is not None))
    log(f"found {len(out)} functions")
    return out


def split_result(rom_path: Path, workdir: Path, log=print) -> SplitResult:
    """The split in the shape every ROM console's import takes."""
    rom = rom_path.read_bytes()
    functions = split(rom_path, workdir, log=log)
    return SplitResult(functions, [], [Segment("rom", 0, len(rom), BASE)], {}, workdir / "functions.cfg", "", "", "gba",
                       0, rom_path.name)


def _stored_mode(row: dict) -> bool:
    """Whether a stored function row is Thumb (its meta, or the luvdis macro in its assembly)."""
    import json

    meta = json.loads(row.get("meta") or "{}")
    if "thumb" in meta:
        return bool(meta["thumb"])
    m = re.search(r"(thumb|arm|non_word_aligned_thumb)_func_start", row.get("asm") or "")
    return not m or m.group(1) != "arm"


def function_from_row(row) -> GbaFunction:
    """A stored function row as the splitter's function object, code and mode included."""
    import json

    row = dict(row)
    thumb = _stored_mode(row)
    refs = [tuple(r) for r in json.loads(row.get("relocs") or "[]")]
    return GbaFunction(row["name"], row["vram"], row.get("rom_offset") or 0, row.get("target") or b"",
                       row.get("asm") or "", row.get("n_insns") or max(1, len(row.get("target") or b"") // (2 if thumb else 4)),
                       thumb, refs, segment=row.get("unit") or "rom")


def parse_function(name: str, text: str, segment: str) -> GbaFunction:
    """A function from its stored luvdis assembly (the code lives in the row; this is for text-only callers)."""
    m = re.search(r"(thumb|arm|non_word_aligned_thumb)_func_start\s+\w*?([0-9A-F]{7,8})", text)
    vram, thumb = (int(m.group(2), 16), m.group(1) != "arm") if m else (BASE, True)
    return GbaFunction(name, vram, 0, b"", text, max(1, sum(1 for line in text.splitlines() if line.strip())),
                       thumb, [], segment=segment)


def _asm_split(text: str, code: bytes, off: int, thumb: bool, new_name: str) -> tuple[str, str]:
    """Split luvdis assembly at byte offset `off`, counting instructions and .byte/.word data."""
    lines = text.splitlines()
    consumed, cut = 0, None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith(("@", "#", "/*")) or stripped.endswith(":") or "_func_start" in stripped:
            continue
        if stripped.startswith(".byte"):
            size = len(re.findall(r"0x[0-9A-Fa-f]{2}", stripped))
        elif stripped.startswith((".short", ".hword")):
            size = 2 * len(re.findall(r"0x[0-9A-Fa-f]+", stripped))
        elif stripped.startswith(".word"):
            size = 4 * len(re.findall(r"0x[0-9A-Fa-f]+", stripped))
        else:
            size = 2 if thumb and thumb_bl_dest(code, consumed, 0) is None else 4
        if consumed == off:
            cut = i
            break
        consumed += size
    if consumed != off or cut is None:
        head = text.rstrip()
        macro = "thumb_func_start" if thumb else "arm_func_start"
        return head, f"\t{macro} {new_name}\n@ split from {text.splitlines()[0].strip() if text else new_name}\n"
    head = "\n".join(lines[:cut]).rstrip()
    tail = "\n".join(lines[cut:])
    macro = "thumb_func_start" if thumb else "arm_func_start"
    return head, f"\t{macro} {new_name}\n{tail}"


def split_function(row, at: int, new_name: str) -> tuple[GbaFunction, GbaFunction]:
    """Cut a stored GBA function at `at`: a 2-byte Thumb boundary or a 4-byte ARM one."""
    row = dict(row)
    fn = function_from_row(row)
    off = at - fn.vram
    step = 2 if fn.thumb else 4
    if not 0 < off < len(fn.code) or off % step:
        raise ValueError(f"0x{at:08X} isn't an instruction boundary inside {fn.name}")
    code, refs = fn.code, fn.refs
    head_asm, tail_asm = _asm_split(fn.asm, code, off, fn.thumb, new_name)
    head_refs = [r for r in refs if r[0] < off]
    tail_refs = [(r[0] - off, r[1], r[2]) for r in refs if r[0] >= off]
    head = GbaFunction(fn.name, fn.vram, fn.rom, code[:off], head_asm, max(1, off // step), fn.thumb,
                       head_refs, fn.asm_only, fn.segment)
    tail = GbaFunction(new_name, at, fn.rom + off, code[off:], tail_asm, max(1, (len(code) - off) // step),
                       fn.thumb, tail_refs, False, fn.segment)
    return head, tail


def _return_end(rom: bytes, off: int, limit: int) -> int | None:
    """The offset just past the first return in Thumb code from `off` (a small function's real end)."""
    md = candidates._md()
    pos = off
    while pos < limit:
        ins = next(md.disasm(rom[pos:pos + 4], BASE + pos, 1), None)
        if ins is None:
            return None
        pos += ins.size
        if candidates._is_return(ins):
            return pos
    return None


def function_at(rom: bytes, addr: int, limit: int, name: str) -> GbaFunction | None:
    """A function the splitter missed at `addr`: a Thumb prologue run to its return, with its literal pool."""
    off = addr - BASE
    if not BASE <= addr < BASE + len(rom):
        return None
    literals = candidates.literal_words(rom, [addr])
    body = next(candidates.bodies(rom, [addr], literals), None)
    if candidates.plausible_function(rom, off):
        end = _return_end(rom, off, min(limit - BASE, len(rom))) or (body[1] if body else None)
    else:
        end = body[1] if body else None
    if end is None:
        return None
    end = min(end, limit - BASE)
    while end + 4 <= limit - BASE and (end in literals or (end % 4 == 2 and end + 2 in literals and
                                                          rom[end:end + 2] in (b"\0\0", b"\xc0\x46"))):
        end += 2 if end % 4 == 2 and end not in literals else 4
    code = rom[off:end]
    helper = call_via(code)
    return GbaFunction(name if not helper else helper, addr, off, code, f"\tthumb_func_start {name}\n@ found by an agent\n",
                       max(1, len(code) // 2), True, _refs(code, addr, literals, off, True), helper is not None, "rom")


def join_saved_functions(rows: list[dict], start: int, end: int, read) -> GbaFunction:
    """Join saved pieces back into one GBA function, filling only zero padding from the game's bytes."""
    import json

    rows = [dict(row) for row in rows]
    first = function_from_row(rows[0])
    step = 2 if first.thumb else 4
    pieces, refs, pos = [], [], start
    for row in rows:
        code = row.get("target") or b""
        addr = row["vram"]
        if addr > pos:
            gap = read(pos, addr - pos)
            if gap is None or gap.strip(b"\0"):
                raise ValueError("the saved layout crosses a gap in this analysis")
            pieces.append(gap)
            pos = addr
        pieces.append(code)
        base = addr - start
        refs += [(int(o) + base, int(k), int(a)) for o, k, a in json.loads(row.get("relocs") or "[]")]
        pos = addr + len(code)
    if pos < end:
        gap = read(pos, end - pos)
        if gap is None or gap.strip(b"\0"):
            raise ValueError("the saved layout crosses a gap in this analysis")
        pieces.append(gap)
    code = b"".join(pieces)[:end - start]
    asm = first.asm + "\n@ joined from saved pieces\n" + "\n".join(row.get("asm") or "" for row in rows[1:])
    return GbaFunction(first.name, start, first.rom, code, asm, max(1, len(code) // step), first.thumb, refs,
                       first.asm_only, first.segment)
