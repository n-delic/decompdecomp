"""Disassembly, instruction diffing and decomp.me/asm-differ style scoring.

score == 0 means a byte-for-byte (relocation-normalized) match. Lower is better.
"""

from __future__ import annotations

import math
import os
import re
import threading
from collections import Counter, OrderedDict
from contextvars import ContextVar
from dataclasses import dataclass
from functools import cache
from importlib.metadata import PackageNotFoundError, version
from typing import Callable

import capstone

from . import arch, compile_sessions, instruction_alignment
from .elfobj import FunctionObj, mask_relocs

PENALTY_STACKDIFF = 1
PENALTY_REGALLOC = 5
PENALTY_OPERAND = 10  # an operand that differs and changes nothing the program means (a register, a branch label)
# a memory access at another offset or symbol, another constant, a call to another function: a different program, so
# it costs more than a reorder (60). At 10, a saved best that wrote one field twice and never wrote the next outscored
# every correct source still one reorder away (COMPILER_MATCHING_LEARNINGS.md, "meaning-breaking saved bests").
PENALTY_MEANING = 150
PENALTY_REORDER = 60
PENALTY_INSERTION = 100
PENALTY_DELETION = 100
# Bumped whenever the penalties change, so scores saved under older ones aren't compared with new ones as equals
# (functions.score_version; network.Network._refresh_stale_best rescores a stale best before it can block one).
# 3: a branch whose target is the instruction its counterpart's target aligns with costs nothing (_aligned_branch).
# 4: the judge places a candidate's own float literals where the ROM holds those bytes (#3563, #3566, #3640): bests
# saved before were thousands off either way (one saved at 9660 judged 11530, another at 9846 judged 7616).
SCORE_VERSION = 4
# Off, the score is version 2's: tests and the measurement of the change read both from the same pair of objects.
ALIGN_BRANCH_TARGETS = True

_CS = arch.capstone_modes()  # (arch, big-endian) -> capstone's (architecture, mode), from arch/

_REG = re.compile(
    r"^\$?(r[a-z0-9]+|e[a-z]{2}|[a-d][lhx]|[sd]il?|[bs]pl?|[xyz]mm\d+|r\d+[dwb]?"  # x86
    r"|zero|at|v\d+|a[0-3]|t\d|s[0-8]|k[01]|gp|fp|ra|f\d+|[SCRME]\d{3}"  # mips (the last: the Allegrex's VFPU)
    r"|cr\d|lr|pc|ip|sb|sl|d\d+|s\d+)$"  # ppc / arm
)
_NUM = re.compile(r"^-?(0x[0-9a-f]+|\d+)$", re.I)
# The registers that hold a frame, per processor: `r1` is PowerPC's stack pointer but an ordinary register on ARM, so
# an ARM `[r1, #0x10]` is a field of whatever r1 points to, not a spill slot (ARM's `sp`, MIPS's `$sp`, x86's esp and
# ebp are named as they print).
_STACK = re.compile(r"\b(rsp|rbp|esp|ebp|\$?sp|\$?fp)\b")
_STACK_PPC = re.compile(r"\b(rsp|rbp|esp|ebp|\$?sp|\$?fp|r1)\b")
_STACK_BY_ARCH = {"ppc": _STACK_PPC, "ppc64": _STACK_PPC}
_TOKEN = re.compile(r"[\w.$<>+@-]+|\S")


@dataclass
class Insn:
    offset: int
    mnemonic: str
    operands: str
    text: str  # normalized "mnemonic operands" with reloc targets symbolized
    encoding: bytes = b""  # relocation-masked bytes; aliases can hide different machine instructions


def percent_text(p: float | None) -> str:
    """A match percentage that never reads 100% before the function really matches: 99.96 is 99.9%, not 100.0%."""
    if p is None:
        return "?"
    if p >= 100:
        return "100%"
    return f"{math.floor(max(0.0, p) * 10) / 10:.1f}%"


VARIANT_INSN_BYTES = 4  # what a variant's own reader decodes (arch.Arch.variants): fixed-width words


_decoders = threading.local()


def disassemble(fn: FunctionObj, cpu: str | None = None) -> list[Insn]:
    key = (_comparison_key(fn), cpu)
    cache = getattr(_decoders, "instructions", None)
    if cache is None:
        cache = _decoders.instructions = OrderedDict()
    if key in cache:
        cache.move_to_end(key)
        return [Insn(*values) for values in cache[key]]
    result = _disassemble(fn, cpu)
    if len(fn.code) <= 32768:
        cache[key] = tuple((ins.offset, ins.mnemonic, ins.operands, ins.text, ins.encoding) for ins in result)
        while len(cache) > 8:
            cache.popitem(last=False)
    return result


def _disassemble(fn: FunctionObj, cpu: str | None = None) -> list[Insn]:
    """A function's instructions, with relocated fields masked and named after their symbols, so the target and a
    candidate compare by what they do, not by where they were linked. `cpu`: the processor's variant (fn.cpu by
    default); one capstone can't decode (the PSP's Allegrex) is read by its processor's own reader (arch/)."""
    code = mask_relocs(fn.code, fn.relocs, fn.arch, fn.big_endian)
    own = arch.get(fn.arch).variants.get(fn.cpu if cpu is None else cpu)
    if own:
        lines = [(at, VARIANT_INSN_BYTES, name, ops, None) for at, name, ops in own(code, fn.big_endian)]
    else:
        cs_arch, mode = _CS[(fn.arch, fn.big_endian)]
        decoders = getattr(_decoders, "values", None)
        if decoders is None:
            decoders = _decoders.values = {}
        md = decoders.get((cs_arch, mode))
        if md is None:
            md = decoders[cs_arch, mode] = capstone.Cs(cs_arch, mode)
            md.detail = True
            md.skipdata = True
        lines = [(ins.address, ins.size, ins.mnemonic, ins.op_str, ins) for ins in md.disasm(code, 0)]
    mips_names = _mips_reloc_names(fn.relocs) if fn.arch == "mips" else {}
    # Preserve original relocation precedence, including duplicate offsets.
    relocation_order = {id(r): index for index, r in reversed(list(enumerate(fn.relocs)))}
    relocations = {}
    for relocation in fn.relocs:
        relocations.setdefault(relocation.offset, relocation)
    out: list[Insn] = []
    for address, size, mnemonic, ops, ins in lines:
        nearby = [relocations[offset] for offset in range(address, address + size) if offset in relocations]
        rel = min(nearby, key=lambda r: relocation_order[id(r)]) if nearby else None
        if rel is not None and rel.offset in mips_names:
            ops = _mips_symbolize(ops, mips_names[rel.offset])
        elif rel is not None:
            off = rel.addend
            if fn.arch == "x86_64" and rel.type in (2, 4):
                # PC-relative: addend = field_offset - (bytes from reloc field to end of insn)
                off += (address + size) - rel.offset
            target = f"<{rel.symbol}{f'{off:+#x}' if off else ''}>"
            ops = _symbolize(ins, ops, rel.offset - address, target, fn.arch)
        elif ins is not None and ins.id and ins.group(capstone.CS_GRP_JUMP) and _NUM.match(ops.strip()):
            ops = f".L{int(ops, 16):x}"  # intra-function branch: keep it position-relative
        out.append(Insn(address, mnemonic, ops, f"{mnemonic} {ops}".strip(), code[address:address + size]))
    return out


def _sext16(v: int) -> int:
    return v - 0x10000 if v & 0x8000 else v


def _mips_reloc_names(relocs) -> dict[int, str]:
    """GNU-as style operands for MIPS relocations: %hi(sym+off), %lo(sym+off), plain symbols for jal/j.
    MIPS objects use REL relocations, so each HI16/LO16 pair carries the symbol offset split across the
    two instruction fields; recombine it so both halves show the real offset (e.g. a struct field)."""
    out: dict[int, str] = {}
    for k, r in enumerate(relocs):
        if r.type == 4:  # R_MIPS_26
            # REL jump addends are instruction words, not bytes. Keeping the symbol alone hid
            # jumps to different labels within the same function even though the exact judge failed.
            off = r.addend << 2
            out[r.offset] = f"{r.symbol}{f'{off:+#x}' if off else ''}"
        elif r.type in (5, 6):  # R_MIPS_HI16 / R_MIPS_LO16
            if r.type == 5:
                pair = next((x for x in relocs[k + 1:] if x.type == 6 and x.symbol == r.symbol), None)
                hi, lo = r.addend, pair.addend if pair else 0
            else:
                pair = next((x for x in reversed(relocs[:k]) if x.type == 5 and x.symbol == r.symbol), None)
                hi, lo = (pair.addend if pair else 0), r.addend
            full = (hi << 16) + _sext16(lo) if max(hi, lo) <= 0xFFFF else r.addend
            off = f"+{full:#x}" if full > 0 else f"-{-full:#x}" if full < 0 else ""
            out[r.offset] = f"%{'hi' if r.type == 5 else 'lo'}({r.symbol}{off})"
        elif r.type == 7:  # R_MIPS_GPREL16
            out[r.offset] = f"%gp_rel({r.symbol})"
    return out


def _mips_symbolize(ops: str, name: str) -> str:
    mem = re.search(r"(-?0x[0-9a-f]+|-?\d+)?\((\$\w+)\)$", ops)
    if name.startswith("%") and mem:  # load/store: offset(base)
        return f"{ops[:mem.start()]}{name}({mem.group(2)})"
    nums = list(re.finditer(r"-?0x[0-9a-f]+|\b\d+\b", ops))
    if nums:
        return ops[: nums[-1].start()] + name + ops[nums[-1].end():]
    return f"{ops}, {name}" if ops else name


def _symbolize(ins, ops: str, rel_at: int, target: str, arch: str) -> str:
    """Put a relocation's symbol into the operand it actually patches (the field is zeroed)."""
    if arch in ("x86_64", "x86"):
        if ins.disp_size and ins.disp_offset == rel_at and "[" in ops:
            lb, rb = ops.index("["), ops.index("]")
            inner = re.sub(r"\s*[+-]\s*(0x[0-9a-f]+|\d+)$", "", ops[lb + 1 : rb])
            return f"{ops[:lb + 1]}{inner} + {target}{ops[rb:]}"
        if ins.imm_size and ins.imm_offset == rel_at:
            outside = re.sub(r"\[.*?\]", lambda m: " " * len(m.group()), ops)
            nums = list(re.finditer(r"-?0x[0-9a-f]+|\b\d+\b", outside))
            return ops[: nums[-1].start()] + target + ops[nums[-1].end() :] if nums else f"{ops} {target}"
    nums = list(re.finditer(r"-?0x[0-9a-f]+|\b\d+\b", ops))
    return ops[: nums[-1].start()] + target + ops[nums[-1].end() :] if nums else f"{ops} {target}".strip()


def asm_text(fn: FunctionObj) -> str:
    return "\n".join(f"{i.offset:5x}:  {i.text}" for i in disassemble(fn))


def _classify(a: Insn, b: Insn, arch: str | None = None) -> tuple[int, str]:
    stack = _STACK_BY_ARCH.get(arch, _STACK)
    if a.text == b.text:
        return 0, " "
    ta, tb = _TOKEN.findall(a.operands), _TOKEN.findall(b.operands)
    if len(ta) == len(tb):
        diffs = [(x, y) for x, y in zip(ta, tb) if x != y]
        if all(_REG.match(x) and _REG.match(y) for x, y in diffs):
            return PENALTY_REGALLOC, "r"
        if all(_NUM.match(x) and _NUM.match(y) for x, y in diffs) and stack.search(a.operands):
            return PENALTY_STACKDIFF, "s"
    if _changes_meaning(a, ta, b, tb, stack):
        return PENALTY_MEANING, "i"  # (the row keeps the operand mark its readers know)
    return PENALTY_OPERAND, "i"


def _values(tokens: list[str]) -> Counter[str]:
    """The operand words that say what an instruction does rather than where it keeps it: numbers, symbols and
    relocation names, keywords like x86's `dword`. Registers, punctuation and branch labels (`.L40`) are left out."""
    return Counter(t.lower() for t in tokens
                   if (t[0].isalnum() or t[0] in "_<$-") and not _REG.match(t.lower()) and not t.startswith(".L"))


def _meaning_key(ins: Insn) -> tuple:
    """An instruction as what it computes: its opcode and its values (_values), whatever registers hold them."""
    return ins.mnemonic.lower(), tuple(sorted(_values(_TOKEN.findall(ins.operands)).elements()))


def _changes_meaning(a: Insn, ta: list[str], b: Insn, tb: list[str], stack=_STACK) -> bool:
    """Whether two instructions with the same opcode differ in what they compute, not only in where: another offset
    or symbol in a load or store, another constant, another call. A branch's target moves with the code before it,
    and a stack slot's offset with the frame's layout, so neither counts."""
    mnemonic = a.mnemonic.lower()
    va, vb = _values(ta), _values(tb)
    if _BRANCH.match(mnemonic) and not _NOT_BRANCH.match(mnemonic):
        # its target, a number, moves with the code before it; a symbol (a call, a tail call) is another function
        va, vb = (Counter({t: n for t, n in v.items() if not _NUM.match(t.lstrip("#"))}) for v in (va, vb))
    if va == vb:
        return False
    if stack.search(a.operands) and stack.search(b.operands) and all(
            _NUM.match(t) for t in (va - vb) + (vb - va)):
        return False  # a spill slot moved: the frame's layout, not the program's
    return True


# a relative branch's last operand: ARM's `#0x20`, MIPS and PowerPC's `0x20`, a bare number, x86's `.L20` (disassemble)
_TARGET = re.compile(r"(?:^|[\s,])(?P<token>#?(?:\.L(?P<label>[0-9a-fA-F]+)|(?P<number>0x[0-9a-fA-F]+|\d+)))$")
_SELF_TARGET = re.compile(r"(?P<name>.+?)(?:(?P<sign>[+-])(?P<offset>0x[0-9a-fA-F]+|\d+))?")
_TARGET_MARK = ".Lbranch"


def _branch_target(ins: Insn, own: str = "") -> tuple[int, int] | None:
    """(the offset a position-relative branch goes to, where its operand starts in `ins.operands`), or None: not a
    branch, or its target is a symbol (a call, a tail call), which names another function and is compared as it is.
    A jump the assembler relocated against the function's own symbol (`j f+0x50c`, x86's `<f+0x1c>`) goes to an offset
    in it as well: `own` is the function's name."""
    mnemonic = ins.mnemonic.lower()
    if not _BRANCH.match(mnemonic) or _NOT_BRANCH.match(mnemonic):
        return None
    operands = ins.operands.strip()
    found = _TARGET.search(operands)
    if found is not None:
        return (int(found["label"], 16) if found["label"] else int(found["number"], 0)), found.start("token")
    last = re.split(r"[\s,]+", operands)[-1] if own else ""
    itself = _SELF_TARGET.fullmatch(last.strip("<>")) if last else None
    if itself is None or itself["name"] != own:
        return None
    offset = int(itself["offset"], 0) if itself["offset"] else 0
    return (-offset if itself["sign"] == "-" else offset), operands.rindex(last)


class _Alignment:
    """Where each target instruction's counterpart sits in the candidate: the pairs the alignment shows as rows, equal
    or different (an inserted or deleted instruction has none).

    One instruction added or removed shifts every later branch's offset, so on a large function dozens of rows differed
    only in a branch's number and the score swung with the function's length instead of with its flow: a candidate
    that fixed a whole loop scored 2,175 against a saved best of 340 because one instruction elsewhere moved every later
    branch. A branch whose target is the instruction its counterpart's target pairs with goes the same place, whatever
    the offsets say. Only the comparison of a branch with its counterpart changes: a match is still byte-identical."""

    def __init__(self, t_ins, c_ins, alignment, names=("", "")):
        self.names = names
        self.t_at = self._offsets(t_ins)
        self.c_at = self._offsets(c_ins)
        self.pair = {}
        for op, a0, a1, b0, b1 in alignment:
            if op in ("equal", "replace"):
                self.pair.update(zip(range(a0, a1), range(b0, b1)))
        self.pair[len(t_ins)] = len(c_ins)  # a branch to the end of the function

    @staticmethod
    def _offsets(insns):
        at = {ins.offset: index for index, ins in enumerate(insns)}
        if insns:
            at[insns[-1].offset + max(1, len(insns[-1].encoding))] = len(insns)
        return at

    def same_flow(self, a: Insn, b: Insn):
        """(a, b) with both targets replaced by one mark when they go to counterparts, else None."""
        ta, tb = _branch_target(a, self.names[0]), _branch_target(b, self.names[1])
        if ta is None or tb is None:
            return None
        ia, ib = self.t_at.get(ta[0]), self.c_at.get(tb[0])
        if ia is None or ib is None or self.pair.get(ia) != ib:
            return None
        return tuple(Insn(ins.offset, ins.mnemonic, f"{ins.operands[:start]}{_TARGET_MARK}",
                          f"{ins.mnemonic} {ins.operands[:start]}{_TARGET_MARK}".strip(), ins.encoding)
                     for ins, (_, start) in ((a, ta), (b, tb)))


@dataclass
class DiffResult:
    score: int
    max_score: int
    rows: list[tuple[str, str, str]]  # (marker, target, candidate)
    bytes_match: bool

    @property
    def percent(self) -> float:
        if self.bytes_match:
            return 100.0
        return max(0.0, 100.0 * (1 - self.score / self.max_score)) if self.max_score else 0.0

    def render(self, context: int = 2, max_rows: int = 60, more_hint: bool = True) -> str:
        """Compact side-by-side diff: a count of each kind of mismatch, then only mismatches with a little context.
        Long runs of missing or extra instructions are shortened (a half-written function would otherwise spend
        every row on code that isn't there yet), so each compile costs the agent little context."""
        from .diff_summary import text
        return text(self.rows)+"\n"+render_rows(self.rows, context, max_rows, more_hint=more_hint)


@dataclass
class ScoreEvidence:
    """The aligned instruction differences consumed by the verifier's Rust scorer."""

    operand_penalty: int
    missing: dict[str, int]
    extra: dict[str, int]
    bytes_match: bool


def first_mismatch(rows: list[tuple[str, str, str]]) -> tuple[int, int, tuple[str, str, str] | None]:
    """(how many target instructions match from the start, the target's length, the first row that differs): one
    small change per compile works best, and this says where the next one goes."""
    total = sum(1 for _, target, _ in rows if target)
    for row in rows:
        if row[0] != " ":
            return sum(1 for m, target, _ in rows[:rows.index(row)] if target), total, row
    return total, total, None


MARKERS = {"<": "missing", ">": "extra", "|": "different", "o": "reordered", "r": "regalloc", "s": "stack",
           "i": "operand"}
LEGEND = ("markers: r=regalloc s=stack-offset i=operand |=different insn <=missing (target only) >=extra (yours only) "
          "o=reordered")


_NOTE = re.compile(r"\s+;.*$")  # what the judge adds to a row (`; target's D_00412340 = 0.5f`): not code
# an operand's names: registers, symbols (a C++ one with its ?, @ and $), keywords; and numbers
_NAME = re.compile(r"(?<![\w$@?.])[$?.]?[A-Za-z_][\w$@?.]*")
_NUMBER = re.compile(r"(?<![\w$@?.])(?:0x[0-9a-fA-F]+|\d+)(?![\w$@?])")
_KEYWORDS = {"ptr", "byte", "word", "dword", "qword", "tbyte", "xmmword", "short", "near", "far", "offset", "st",
             "lo", "hi", "ha", "l", "h", "sda21", "sda2rel", "gp_rel", "got", "call16", "got_disp",
             # ARM's shifts and extends: `lsl #2` and `asr #2` are other instructions
             "lsl", "lsr", "asr", "ror", "rrx", "uxtb", "uxth", "uxtw", "uxtx", "sxtb", "sxth", "sxtw", "sxtx"}
_PADDING = {"nop", "int3", "lea R, [R]", "lea R, [R+0x0]"}
# a memory operand through the stack pointer: x86 [esp+0x18], MIPS 0x54($sp), PowerPC 0x10(r1), ARM [sp, #0x10]
_STACK_SLOT = re.compile(r"\[[^\]]*\b(?:esp|ebp|rsp|rbp|sp)\b[^\]]*\]|-?(?:0x[0-9a-fA-F]+|\d+)\((?:\$sp|\$fp|r1|sp)\)")
# a number in a stack slot, and what comes before it: an index's scale (`ecx*4`) or shift (ARM's `lsl #2`, `uxtw #2`)
# is the element's size, not the frame's layout, and stays
_SLOT_NUMBER = re.compile(r"(\*\s*|\b(?:lsl|lsr|asr|ror|[us]xt[bhwx])\s+#?)?(?<![\w$@?.])(-?(?:0x[0-9a-fA-F]+|\d+))"
                          r"(?![\w$@?])")
# a branch's mnemonic (x86 j*, loop*; b* elsewhere; ARM64's compare/test and branch), and those that start like one and
# aren't: ARM's bit clear, bit field ops, breakpoints and branch target marks with any condition (`biceq`, `bic.w`),
# x86's bit tests and scans, bswap, bound and bextr, MIPS's break (`bic r0, r1, #0x100` vs `#0x200` read as one jump)
_BRANCH = re.compile(r"j|b|loop|[ct]bn?z\b")
_NOT_BRANCH = re.compile(r"bic|bf[ic]|bfm|bfxil|bkpt|brk|bti|(?:bt[crs]?|bsf|bsr|bswap|bound|bextr|break)\b")
# x86 prefixes: `lock add` is an add, not a lock of something called add
_PREFIXES = {"lock", "rep", "repe", "repz", "repne", "repnz", "xacquire", "xrelease", "bnd", "notrack"}
RESIDUE_SHOWN = 5


def shape(text: str) -> str:
    """An instruction as the residue compares it: register names set aside (R), symbol names too (S: a reference
    to the same thing under another name is the judge's to check, by address), and a branch's target (J), which moves
    with the length of the code before it."""
    t = _NOTE.sub("", text).strip()
    if not t:
        return ""
    mnemonic, _, ops = t.partition(" ")
    mnemonic = opcode = mnemonic.lower()
    while opcode in _PREFIXES and ops.strip():  # (each word: `xacquire lock add` is an add)
        word, _, ops = ops.strip().partition(" ")
        opcode = word.lower()
        mnemonic = f"{mnemonic} {opcode}"

    def name(m: re.Match) -> str:
        word = m.group(0)
        if _REG.match(word.lower()):
            return "R"
        return word if word.lower() in _KEYWORDS else "S"

    def slot(m: re.Match) -> str:
        return _SLOT_NUMBER.sub(lambda n: n.group(0) if n.group(1) else "N", m.group(0))
    # a stack slot's offset follows the frame's layout (the frame's size, `sub esp, 0x10`, stays as it is)
    ops = _STACK_SLOT.sub(slot, ops.strip())
    ops = _NAME.sub(name, ops)
    ops = re.sub(r"\bS\s*[-+]\s*(?:0x[0-9a-fA-F]+|\d+)", "S", ops)  # a symbol and an offset: a place, like a symbol
    if _BRANCH.match(opcode) and not _NOT_BRANCH.match(opcode):  # (ARM's targets are `#0x...` too: `b #0x108`)
        ops = re.sub(r"(?<![\w$@?])(#?)0x[0-9a-fA-F]{3,}$", r"\1J", ops)
    return f"{mnemonic} {ops}".strip()


def residue(rows: list[tuple[str, str, str]]) -> tuple[list[str], list[str]]:
    """(the target's instructions yours lacks, yours the target lacks) among the differing rows, register and symbol
    names and order set aside: what a different register allocation or schedule doesn't explain. Each as the row's
    own text. Padding (`nop`, x86's `lea esi, [esi]`) is left out: it follows from the length of the code before it."""
    def side(k: int) -> tuple[Counter, dict[str, str]]:
        found, text = Counter(), {}
        for row in rows:
            if row[0] == " " or not (s := shape(row[k + 1])) or s in _PADDING:
                continue
            found[s] += 1
            text.setdefault(s, _NOTE.sub("", row[k + 1]).strip())
        return found, text
    (target, ttext), (yours, ytext) = side(0), side(1)
    return ([ttext[s] for s in (target - yours).elements()], [ytext[s] for s in (yours - target).elements()])


def residue_line(rows: list[tuple[str, str, str]]) -> str:
    """The residue in a line for an agent: on near misses of an Xbox racing game, more than half had real differences
    past the 60 rows a diff shows, among rows that only differ by register; and a near miss whose rows are all
    register or order differences wants another approach (declaration order, temporaries) than one missing code."""
    if not any(m != " " for m, _, _ in rows):
        return ""
    target, yours = residue(rows)
    if not target and not yours:
        return ("Registers, stack slots and order aside, your instructions are the target's: what differs is which "
                "registers hold what and in which order values are computed (try the order of statements and "
                "declarations, temporaries, a variable's type), not what the code does.")

    def listed(items: list[str]) -> str:
        found = Counter(items)
        shown = [f"`{s}`" + (f" x{n}" if n > 1 else "") for s, n in found.most_common(RESIDUE_SHOWN)]
        more = len(found) - RESIDUE_SHOWN
        return ", ".join(shown) + (f" and {more} more" if more > 0 else "") if shown else "nothing"
    return (f"Registers, stack slots and order aside, what still differs: the target has {listed(target)}; yours has "
            f"{listed(yours)}. Start there: the other differing rows are registers and order that follow from it.")


def render_rows(rows: list[tuple[str, str, str]], context: int = 2, max_rows: int = 60, run: int = 6,
                legend: str = LEGEND, more_hint: bool = True) -> str:
    """A diff's rows as text for an agent: the differing lines with `context` lines around them, at most `max_rows`,
    long equal stretches folded, and a count of each kind of difference with what's left once registers and order
    are set aside (residue_line). Where rows are cut, it says last_diff shows them all without another compile
    (`more_hint`)."""
    counts = Counter(m for m, _, _ in rows if m != " ")
    summary = ", ".join(f"{n} {MARKERS.get(m, m)}" for m, n in counts.most_common())
    if summary and (left := residue_line(rows)):
        summary += "\n" + left
    width = min(max((len(t) for _, t, _ in rows), default=10) + 2, 44)
    show = set()
    for i, (m, _, _) in enumerate(rows):
        if m != " ":
            show.update(range(max(0, i - context), min(len(rows), i + context + 1)))
    lines = [f"   {'TARGET':<{width}}| YOURS"]
    order = sorted(show)
    prev, k = -1, 0
    while k < len(order):
        i = order[k]
        if i != prev + 1:
            lines.append("   ...")
        m = rows[i][0]
        j = i
        while m in "<>" and j + 1 < len(rows) and rows[j + 1][0] == m:
            j += 1
        if j - i + 1 > run:  # a long run of one kind: its start and end, and how much is in between
            for a in (i, i + 1):
                lines.append(f" {m} {rows[a][1][:width - 1]:<{width}}| {rows[a][2]}")
            lines.append(f"   ... {j - i - 2} more {MARKERS[m]} ({'target only' if m == '<' else 'yours only'}) ...")
            lines.append(f" {m} {rows[j][1][:width - 1]:<{width}}| {rows[j][2]}")
            prev = j
            while k < len(order) and order[k] <= j:
                k += 1
        else:
            m, t, c = rows[i]
            lines.append(f" {m} {t[:width - 1]:<{width}}| {c}")
            prev = i
            k += 1
        if len(lines) >= max_rows and k < len(order):
            lines.append(f"   ... ({len(rows) - prev - 1} more rows not shown: fix these first"
                         + ("; last_diff(function) shows every row of this compile without compiling again)"
                            if more_hint else ")"))
            break
    else:
        if prev != len(rows) - 1:
            lines.append("   ...")
    return (f"{summary}\n" if summary else "") + legend + "\n" + "\n".join(lines)


_SPLAT_PREFIX = re.compile(r"^/\*\s*[0-9A-Fa-f]+\s+[0-9A-Fa-f]{8}\s+[0-9A-Fa-f]{8}\s*\*/\s*")


def compact_asm(text: str) -> str:
    """splat's listing without the per-line `/* offset address bytes */` comment and alignment padding: about half
    the size, so an agent's context holds more of the work. Labels (.L80012345) still carry the addresses."""
    out = []
    for line in text.splitlines():
        m = _SPLAT_PREFIX.match(line)
        if m:
            op, _, args = line[m.end():].strip().partition(" ")
            line = f"  {op} {args.strip()}" if args.strip() else f"  {op}"
            line = re.sub(r",\s+", ", ", line)
        out.append(line)
    return "\n".join(out)


def _prepared_target(target):
    session = compile_sessions.current()
    if session is None or len(target.code) > 65536:
        return disassemble(target), target.fingerprint()
    # Key all fields actually used by disassembly/fingerprinting, including relocation
    # symbol names and CPU variants. Never cache candidate instructions as target state.
    key = (target.name, target.arch, target.big_endian, target.cpu, bytes(target.code),
           tuple(r.key() for r in target.relocs))
    if key not in session.instructions:
        session.instructions[key] = (disassemble(target), target.fingerprint())
        while len(session.instructions) > 8:session.instructions.popitem(last=False)
    return session.instructions[key]


def _comparison_key(fn):
    return (fn.name, fn.arch, fn.big_endian, fn.cpu, bytes(fn.code), tuple(r.key() for r in fn.relocs))


def score(target: FunctionObj, candidate: FunctionObj) -> DiffResult:
    """Identical scoring without allocating diagnostic rows for ordinary misses."""
    return _diff(target, candidate, details=False)


def diff(target: FunctionObj, candidate: FunctionObj) -> DiffResult:
    # Advisory instruction comparisons only. Link verdicts and source locations are
    # computed by callers for the actual object, even when two C variants emit alike.
    session = compile_sessions.current()
    key = None
    if session is not None and len(target.code) + len(candidate.code) <= 32768:
        key = (_comparison_key(target), _comparison_key(candidate))
        cached = session.comparisons.get(key)
        if cached is not None:
            session.comparisons.move_to_end(key)
            from .compile_timing import add
            add('comparison_hits', 1)
            return DiffResult(cached.score, cached.max_score, list(cached.rows), cached.bytes_match)
    result = _diff(target, candidate)
    if key is not None and result.score > 0 and not result.bytes_match:
        # Bound both retained machine bytes and potentially large diagnostic rows.
        size = len(repr((key, result)).encode())
        if size <= 1024 * 1024:
            session.comparisons[key] = DiffResult(result.score, result.max_score, list(result.rows), result.bytes_match)
            session.comparison_bytes += size
            session.comparison_sizes[key] = size
            while len(session.comparisons) > 128 or session.comparison_bytes > 8 * 1024 * 1024:
                old, _ = session.comparisons.popitem(last=False)
                session.comparison_bytes -= session.comparison_sizes.pop(old)
    return result


mips_comparer: ContextVar[Callable[[FunctionObj, FunctionObj, bool], dict | None] | None] = ContextVar(
    "queued_mips_comparer", default=None)


@cache
def _native_mips_decoder_compatible():
    # The Python module's __version__ can lag the bundled C library in the wheel.
    try:
        return not os.environ.get("LIBCAPSTONE_PATH") and version("capstone") == "5.0.9"
    except PackageNotFoundError:
        return False


def score_evidence(target: FunctionObj, candidate: FunctionObj) -> ScoreEvidence:
    native = mips_comparer.get()
    if (native is not None and target.arch == candidate.arch == "mips" and _native_mips_decoder_compatible()
            and target.cpu not in arch.get("mips").variants
            and all(fn.name.isascii() and all(r.symbol.isascii() for r in fn.relocs) for fn in (target, candidate))):
        result = native(target, candidate, ALIGN_BRANCH_TARGETS)
        if result is not None:
            return ScoreEvidence(**result)
    return _diff(target, candidate, details=False, evidence=True)


def _diff(target: FunctionObj, candidate: FunctionObj, *, details=True, evidence=False):
    """Compare a candidate with the target instruction by instruction: a score (0 is a match, lower is closer), the
    rows to show, and what's missing or extra. Equal bytes are a match whatever the score."""
    t_ins, target_fingerprint = _prepared_target(target)
    c_ins = disassemble(candidate, target.cpu)  # a candidate is the target's processor
    max_score = max(1, len(t_ins)) * PENALTY_INSERTION
    bytes_match = target_fingerprint == candidate.fingerprint()

    alignment = instruction_alignment.opcodes([i.mnemonic for i in t_ins], [i.mnemonic for i in c_ins])
    rows: list[tuple[str, str, str]] = []
    score = 0
    # What each side computes that the other doesn't (registers set aside): a paired row that differs in an offset or
    # constant costs PENALTY_MEANING only when its instruction is one of these. Two stores in another order pair up
    # with each other's offsets, and that is a schedule, not another program: it stays an operand difference.
    t_keys, c_keys = Counter(map(_meaning_key, t_ins)), Counter(map(_meaning_key, c_ins))
    unmatched_t, unmatched_c = t_keys - c_keys, c_keys - t_keys
    missing: Counter[str] = Counter()
    extra: Counter[str] = Counter()
    flow = _Alignment(t_ins, c_ins, alignment, (target.name, candidate.name)) if ALIGN_BRANCH_TARGETS else None
    for op, a0, a1, b0, b1 in alignment:
        if op == "equal":
            for a, b in zip(t_ins[a0:a1], c_ins[b0:b1]):
                same = flow.same_flow(a, b) if flow is not None and a.text != b.text else None
                pen, mark = _classify(*same, target.arch) if same else _classify(a, b, target.arch)
                if pen == PENALTY_MEANING:
                    ka, kb = _meaning_key(a), _meaning_key(b)
                    if unmatched_t[ka] > 0 or unmatched_c[kb] > 0:
                        unmatched_t[ka] -= 1
                        unmatched_c[kb] -= 1
                    else:
                        pen = PENALTY_OPERAND
                score += pen
                if not details:
                    continue
                if not bytes_match and a.text == b.text and a.encoding != b.encoding:
                    # e.g. 32-bit addu and 64-bit daddu both disassemble as "move".
                    # Keep the existing score; make the strict judge's byte disagreement visible.
                    rows.append(("|", f"{a.text} [bytes {a.encoding.hex()}]",
                                 f"{b.text} [bytes {b.encoding.hex()}]"))
                else:
                    rows.append((mark, a.text, b.text))
            continue
        ta, cb = t_ins[a0:a1], c_ins[b0:b1]
        for k in range(max(len(ta), len(cb))):
            a = ta[k] if k < len(ta) else None
            b = cb[k] if k < len(cb) else None
            if a and b:
                if details:rows.append(("|", a.text, b.text))
                missing[a.text] += 1
                extra[b.text] += 1
            elif a:
                if details:rows.append(("<", a.text, ""))
                missing[a.text] += 1
            else:
                if details:rows.append((">", "", b.text))
                extra[b.text] += 1
    if evidence:
        return ScoreEvidence(score, dict(missing), dict(extra), bytes_match)

    # instructions that are merely in the wrong place are cheaper than real insert/delete
    reordered = sum((missing & extra).values())
    score += reordered * PENALTY_REORDER
    score += (sum(missing.values()) - reordered) * PENALTY_DELETION
    score += (sum(extra.values()) - reordered) * PENALTY_INSERTION
    if reordered:
        moved = missing & extra
        rows = [("o" if m in "|<>" and ((t and moved[t]) or (c and moved[c])) else m, t, c) for m, t, c in rows]

    if score == 0 and not bytes_match:
        score = 1  # textually identical but encodings/relocs differ (e.g. branch form, addend)
    if bytes_match:
        score = 0
    return DiffResult(score=score, max_score=max_score, rows=rows, bytes_match=bytes_match)
