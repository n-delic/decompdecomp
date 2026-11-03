"""Disassembly, instruction diffing and decomp.me/asm-differ style scoring.

score == 0 means a byte-for-byte (relocation-normalized) match. Lower is better.
"""

from __future__ import annotations

import difflib
import math
import re
from collections import Counter
from dataclasses import dataclass

import capstone

from . import arch
from .elfobj import FunctionObj, mask_relocs

PENALTY_STACKDIFF = 1
PENALTY_REGALLOC = 5
PENALTY_OPERAND = 10
PENALTY_REORDER = 60
PENALTY_INSERTION = 100
PENALTY_DELETION = 100

_CS = arch.capstone_modes()  # (arch, big-endian) -> capstone's (architecture, mode), from arch/

_REG = re.compile(
    r"^\$?(r[a-z0-9]+|e[a-z]{2}|[a-d][lhx]|[sd]il?|[bs]pl?|[xyz]mm\d+|r\d+[dwb]?"  # x86
    r"|zero|at|v\d+|a[0-3]|t\d|s[0-8]|k[01]|gp|fp|ra|f\d+|[SCRME]\d{3}"  # mips (the last: the Allegrex's VFPU)
    r"|cr\d|lr|pc|ip|sb|sl|d\d+|s\d+)$"  # ppc / arm
)
_NUM = re.compile(r"^-?(0x[0-9a-f]+|\d+)$", re.I)
_STACK = re.compile(r"\b(rsp|rbp|esp|ebp|\$?sp|\$?fp|r1)\b")
_TOKEN = re.compile(r"[\w.$<>+@-]+|\S")


@dataclass
class Insn:
    offset: int
    mnemonic: str
    operands: str
    text: str  # normalized "mnemonic operands" with reloc targets symbolized


def percent_text(p: float | None) -> str:
    """A match percentage that never reads 100% before the function really matches: 99.96 is 99.9%, not 100.0%."""
    if p is None:
        return "?"
    if p >= 100:
        return "100%"
    return f"{math.floor(max(0.0, p) * 10) / 10:.1f}%"


VARIANT_INSN_BYTES = 4  # what a variant's own reader decodes (arch.Arch.variants): fixed-width words


def disassemble(fn: FunctionObj, cpu: str | None = None) -> list[Insn]:
    """A function's instructions, with relocated fields masked and named after their symbols, so the target and a
    candidate compare by what they do, not by where they were linked. `cpu`: the processor's variant (fn.cpu by
    default); one capstone can't decode (the PSP's Allegrex) is read by its processor's own reader (arch/)."""
    code = mask_relocs(fn.code, fn.relocs, fn.arch, fn.big_endian)
    own = arch.get(fn.arch).variants.get(fn.cpu if cpu is None else cpu)
    if own:
        lines = [(at, VARIANT_INSN_BYTES, name, ops, None) for at, name, ops in own(code, fn.big_endian)]
    else:
        cs_arch, mode = _CS[(fn.arch, fn.big_endian)]
        md = capstone.Cs(cs_arch, mode)
        md.detail = True
        md.skipdata = True
        lines = [(ins.address, ins.size, ins.mnemonic, ins.op_str, ins) for ins in md.disasm(code, 0)]
    mips_names = _mips_reloc_names(fn.relocs) if fn.arch == "mips" else {}
    out: list[Insn] = []
    for address, size, mnemonic, ops, ins in lines:
        rel = next((r for r in fn.relocs if address <= r.offset < address + size), None)
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
        out.append(Insn(address, mnemonic, ops, f"{mnemonic} {ops}".strip()))
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
            out[r.offset] = r.symbol
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


def _classify(a: Insn, b: Insn) -> tuple[int, str]:
    if a.text == b.text:
        return 0, " "
    ta, tb = _TOKEN.findall(a.operands), _TOKEN.findall(b.operands)
    if len(ta) == len(tb):
        diffs = [(x, y) for x, y in zip(ta, tb) if x != y]
        if all(_REG.match(x) and _REG.match(y) for x, y in diffs):
            return PENALTY_REGALLOC, "r"
        if all(_NUM.match(x) and _NUM.match(y) for x, y in diffs) and _STACK.search(a.operands):
            return PENALTY_STACKDIFF, "s"
    return PENALTY_OPERAND, "i"


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

    def render(self, context: int = 2, max_rows: int = 60) -> str:
        """Compact side-by-side diff: a count of each kind of mismatch, then only mismatches with a little context.
        Long runs of missing or extra instructions are shortened (a half-written function would otherwise spend
        every row on code that isn't there yet), so each compile costs the agent little context."""
        return render_rows(self.rows, context, max_rows)


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
             "lo", "hi", "ha", "l", "h", "sda21", "sda2rel", "gp_rel", "got", "call16", "got_disp"}
_PADDING = {"nop", "int3", "lea R, [R]", "lea R, [R+0x0]"}
# a memory operand through the stack pointer: x86 [esp+0x18], MIPS 0x54($sp), PowerPC 0x10(r1), ARM [sp, #0x10]
_STACK_SLOT = re.compile(r"\[[^\]]*\b(?:esp|ebp|rsp|rbp|sp)\b[^\]]*\]|-?(?:0x[0-9a-fA-F]+|\d+)\((?:\$sp|\$fp|r1|sp)\)")
# a number in a stack slot, and what comes before it: an index's scale (`ecx*4`) or shift (ARM's `lsl #2`) is the
# element's size, not the frame's layout, and stays
_SLOT_NUMBER = re.compile(r"(\*\s*|\b(?:lsl|lsr|asr|ror)\s+#?)?(?<![\w$@?.])(-?(?:0x[0-9a-fA-F]+|\d+))(?![\w$@?])")
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
    mnemonic = mnemonic.lower()
    while mnemonic in _PREFIXES and ops.strip():
        word, _, ops = ops.strip().partition(" ")
        mnemonic = f"{mnemonic} {word.lower()}"

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
    if mnemonic.startswith(("j", "b", "loop")):
        ops = re.sub(r"(?<![\w$@?])0x[0-9a-fA-F]{3,}$", "J", ops)
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


def diff(target: FunctionObj, candidate: FunctionObj) -> DiffResult:
    """Compare a candidate with the target instruction by instruction: a score (0 is a match, lower is closer), the
    rows to show, and what's missing or extra. Equal bytes are a match whatever the score."""
    t_ins, c_ins = disassemble(target), disassemble(candidate, target.cpu)  # (a candidate is the target's processor)
    max_score = max(1, len(t_ins)) * PENALTY_INSERTION
    bytes_match = target.fingerprint() == candidate.fingerprint()

    sm = difflib.SequenceMatcher(a=[i.mnemonic for i in t_ins], b=[i.mnemonic for i in c_ins], autojunk=False)
    rows: list[tuple[str, str, str]] = []
    score = 0
    missing: Counter[str] = Counter()
    extra: Counter[str] = Counter()
    for op, a0, a1, b0, b1 in sm.get_opcodes():
        if op == "equal":
            for a, b in zip(t_ins[a0:a1], c_ins[b0:b1]):
                pen, mark = _classify(a, b)
                score += pen
                rows.append((mark, a.text, b.text))
            continue
        ta, cb = t_ins[a0:a1], c_ins[b0:b1]
        for k in range(max(len(ta), len(cb))):
            a = ta[k] if k < len(ta) else None
            b = cb[k] if k < len(cb) else None
            if a and b:
                rows.append(("|", a.text, b.text))
                missing[a.text] += 1
                extra[b.text] += 1
            elif a:
                rows.append(("<", a.text, ""))
                missing[a.text] += 1
            else:
                rows.append((">", "", b.text))
                extra[b.text] += 1
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
