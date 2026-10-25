"""x86 (Windows PC games, Visual C++): what a function does with pointers, for the struct layouts of structs.py and
the fields of classes.py's classes.

One pass in address order, as arch/mips_fields.py does: branches aren't followed, so what a register holds is known
until an instruction overwrites it or a call clobbers it (eax, ecx, edx). A register holds a pointer (a structs term)
plus a constant (`lea esi, [ecx+0x40]` points 0x40 into ecx's struct). Where pointers come from:
- `this`: ecx at entry (a method, __thiscall; or __fastcall's first argument), the term (ARGUMENT, THIS);
- the arguments on the stack: [esp+4] at entry is the first, (ARGUMENT, 0), then (ARGUMENT, 1) at [esp+8]...; read
  through esp (followed through its pushes, pops and `sub esp`) or through ebp once `mov ebp, esp` makes a frame;
- a word loaded from a pointer (the field: the struct it points to), a global's address (`mov ecx, offset g`, a
  method called on a global object) and the words loaded from globals, what a call returns in eax.
What it records: every memory access through such a pointer, with its width and kind (movsx: signed, movzx:
unsigned, fld/movss: a float); the pointers handed to each call (pushed since the call before it, and ecx); what eax
holds at each `ret`; pointers stored into fields (a constructor's `mov dword ptr [esi], offset vtable` is one: the
vtable is a global); pointers kept in the frame and loaded back; walks (a pointer moved along inside a loop).

After a call the stack is where the callee leaves it: a __stdcall or __thiscall callee pops its arguments (`ret 8`),
which `pops` says by its address (from its decorated name); one it doesn't know (a virtual call, an import) pops
what was pushed for it, unless `add esp` follows (a __cdecl function pointer's).

It reads capstone's text (disasm_lite), not its operand details: those cost 28 µs an instruction, a minute and more
for a game of 25,000 functions; the text, a few seconds.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping

import capstone

from ..structs import ARGUMENT, CALL, FIELD, GLOBAL, RETURNED, THIS, Facts

WORD = 4
PAGE = 0x1000  # a frame this big is reserved by __chkstk (its size in eax), not by `sub esp`
MAX_ARGUMENTS = 16  # stack slots read as arguments at a call
_REG = {name: full for full, parts in (("eax", ("al", "ah", "ax")), ("ebx", ("bl", "bh", "bx")),
                                       ("ecx", ("cl", "ch", "cx")), ("edx", ("dl", "dh", "dx")), ("esi", ("si",)),
                                       ("edi", ("di",)), ("ebp", ("bp",)), ("esp", ("sp",)))
        for name in (full, *parts)}  # every general register's name -> its 32-bit register
CALLER_SAVED = ("eax", "ecx", "edx")
CALLEE_SAVED = ("ebx", "esi", "edi", "ebp")  # a push of one not yet set here saves it: no argument
SIZES = {"byte": 1, "word": 2, "dword": 4, "fword": 6, "qword": 8, "tbyte": 10, "xmmword": 16}
_MEMORY = re.compile(r"^(?:(\w+) ptr )?(?:(\w+):)?\[(.*)\]$")
NO_WRITE = {"cmp", "test", "bt", "push", "call", "jmp", "ret", "out", "nop"}  # their first operand is only read
# what an instruction writes besides its first operand: one-operand multiplies and divides, cdq, the string ones
IMPLICIT = {"cdq": ("edx",), "mul": ("eax", "edx"), "div": ("eax", "edx"), "idiv": ("eax", "edx"),
            "rdtsc": ("eax", "edx"), "cpuid": ("eax", "ebx", "ecx", "edx"), "cmpxchg": ("eax",)}
STRINGS = re.compile(r"^(movs|stos|lods|scas|cmps|ins|outs)[bwd]$")  # (movsd with xmm registers is SSE's)
FLOAT_CONTROL = ("fldcw", "fnstcw", "fstcw", "fnstsw", "fstsw", "fldenv", "fnstenv", "fnsave", "frstor", "fxsave")
TO_INTEGER = ("cvtss2si", "cvttss2si", "cvtsd2si", "cvttsd2si")

_READER = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)


def _kind(mnemonic: str, size: int) -> str:
    """What an instruction says a memory operand of `size` bytes is: "f32" (fld dword, movss), "s16" (movsx, fild
    word), "u8" (movzx); "" where it doesn't say (mov, add, cmp: an integer, signed or not)."""
    if mnemonic.startswith("f"):
        if mnemonic.startswith(FLOAT_CONTROL):
            return ""
        if mnemonic.startswith("fi"):  # fild, fistp, fiadd: an integer
            return f"s{8 * size}"
        return {4: "f32", 8: "f64"}.get(size, "")
    if mnemonic.startswith(TO_INTEGER) or mnemonic.endswith(("ss", "sd")) and not mnemonic.startswith("cvtsi2"):
        return {4: "f32", 8: "f64"}.get(size, "")
    if mnemonic == "movsx":
        return f"s{8 * size}"
    if mnemonic == "movzx":
        return f"u{8 * size}"
    return ""


def _number(text: str) -> int | None:
    try:
        return int(text, 0)
    except ValueError:
        return None


def _memory(text: str) -> tuple[int, str | None, str | None, int, bool] | None:
    """(bytes, base, index, displacement, through a segment) of a memory operand's text, or None for another."""
    m = _MEMORY.match(text)
    if not m:
        return None
    base = index = None
    disp = 0
    for token in m.group(3).replace(" - ", " + -").split(" + "):
        if "*" in token:
            index = token.split("*")[0]
        elif token in _REG or token.isalpha():
            if base is None:
                base = token
            else:
                index = token
        else:
            disp += _number(token) or 0
    return SIZES.get(m.group(1) or "", 0), base, index, disp, m.group(2) is not None


def read_facts(vram: int, code: bytes, relocs: list, big_endian: bool = False,
               datum: Callable[[int], int | None] = lambda a: None, pops: Mapping[int, int] | None = None) -> Facts:
    """What the function at `vram` does with pointers. `datum(address)`: where the global containing an address
    starts, None when it's no data (code, or a number); `pops`: function address -> the bytes it pops on return
    (__stdcall's arguments). `relocs` aren't needed: the code is read at its own address, so its addresses are in it."""
    instructions = [(at, mnemonic.split()[-1] if mnemonic else "", ops.split(", ") if ops else [])
                    for at, _, mnemonic, ops in _READER.disasm_lite(code, vram)]
    end = vram + len(code)
    loops = [(target, at) for at, mnemonic, ops in instructions if mnemonic.startswith("j") and len(ops) == 1
             and (target := _number(ops[0])) is not None and vram <= target <= at]
    pops = pops or {}

    def global_at(addr: int) -> tuple[tuple, int] | None:
        start = datum(addr & 0xFFFFFFFF)
        return None if start is None else ((GLOBAL, start), (addr & 0xFFFFFFFF) - start)

    def in_loop(at: int) -> bool:
        return any(first <= at <= last for first, last in loops)

    regs: dict[str, tuple[tuple, int]] = {"ecx": ((ARGUMENT, THIS), 0)}
    frames: dict[str, int | None] = {"esp": 0}  # registers pointing into the frame -> their offset from esp at entry
    stack: dict[int, tuple[tuple, int] | None] = {}  # offset from esp at entry -> what was put there
    pushed: set[int] = set()  # the slots written since the last call: its arguments
    unsaved = set(CALLEE_SAVED)  # the caller's registers not yet set here
    called = False
    out = Facts()

    def place(mem) -> tuple | None:
        """("stack", offset) or (term, offset) a memory operand names; None for what's neither."""
        _, base, index, disp, segment = mem
        if segment:  # fs:[0], the exception handler chain
            return None
        base, index = _REG.get(base, base), _REG.get(index, index)
        if base in frames:
            return None if index or frames[base] is None else ("stack", frames[base] + disp)
        if base is None and index is None:
            return global_at(disp)
        if base in regs:  # (with an index: p->items[i], read as the array's start, as MIPS does)
            term, delta = regs[base]
            return term, delta + disp
        return None

    def stack_value(offset: int) -> tuple[tuple, int] | None:
        if offset in stack:
            return stack[offset]
        if offset >= WORD and offset % WORD == 0:  # above the return address: the caller's arguments
            return (ARGUMENT, (offset - WORD) // WORD), 0
        return None

    def value_of(text: str) -> tuple[tuple, int] | None:
        """The pointer an operand holds or loads (a register, a global's address, a word loaded through a pointer)."""
        if text in _REG:
            return regs.get(_REG[text]) if _REG[text] == text else None
        if (number := _number(text)) is not None:
            return global_at(number)
        mem = _memory(text) if "[" in text else None
        if mem is None or mem[0] != WORD or (where := place(mem)) is None:
            return None
        if where[0] == "stack":
            return stack_value(where[1])
        return (FIELD, where[0], where[1]), 0

    def set_reg(dest: str, value) -> None:
        frames.pop(dest, None)
        unsaved.discard(dest)
        regs.pop(dest, None)
        if value is not None:
            regs[dest] = value

    for k, (at, mnemonic, ops) in enumerate(instructions):
        sp = frames.get("esp")
        first = ops[0] if ops else ""
        dest = _REG.get(first)
        mem = next(((n, m) for n, op in enumerate(ops) if "[" in op and (m := _memory(op)) is not None), None)
        where = place(mem[1]) if mem is not None and mnemonic != "lea" else None
        if where is not None and where[0] != "stack" and mnemonic != "nop":
            out.accesses.append((where[0], where[1], mem[1][0], _kind(mnemonic, mem[1][0])))
        if mnemonic == "lea":
            _, base, index, disp, _ = mem[1]
            base, index = _REG.get(base), _REG.get(index, index)
            if base in frames and not index:  # the address of a local (or ebp's frame, when it's esp's)
                frame = None if frames[base] is None else frames[base] + disp
                if dest != "esp":
                    set_reg(dest, None)
                frames[dest] = frame
            elif base in regs and not index:
                term, delta = regs[base]
                if dest == base and in_loop(at):
                    out.walked.add(term)
                set_reg(dest, (term, delta + disp))
            elif base is None and index is None:
                set_reg(dest, global_at(disp))
            else:
                set_reg(dest, None)
        elif mnemonic == "mov" and dest is not None:
            source = _REG.get(ops[1])
            if source in frames and dest == first:  # mov ebp, esp: a frame; mov esp, ebp: back to it
                frame = frames[source]
                if dest != "esp":
                    set_reg(dest, None)
                frames[dest] = frame
            elif dest == "esp":
                frames["esp"] = None
            else:
                set_reg(dest, value_of(ops[1]) if dest == first else None)
        elif mnemonic == "mov" and mem is not None:
            value = value_of(ops[1]) if mem[1][0] == WORD else None
            if where is not None and where[0] == "stack":
                stack[where[1]] = value
                pushed.add(where[1])
            elif where is not None and value is not None and value[1] == 0:
                out.stores.append((where[0], where[1], value[0]))
            elif where is None and value is not None and value[0][0] == GLOBAL and value[1] == 0 and \
                    (base := _REG.get(mem[1][1])) is not None and base not in frames and not mem[1][2]:
                # a global's address stored through a register this doesn't follow (an object from an import's
                # operator new, built inline in a function the analysis merged): the register points to an object
                # of its own from here, so a constructor's vtable store still names a class
                regs[base] = ((CALL, at - vram, None), 0)
                out.stores.append((regs[base][0], mem[1][3], value[0]))
        elif mnemonic == "push":
            if sp is not None:
                frames["esp"] = sp = sp - WORD
                stack[sp] = value_of(first)
                if dest not in unsaved:
                    pushed.add(sp)
        elif mnemonic == "pop":
            if sp is not None:
                frames["esp"] = sp + WORD
            if dest is not None and dest != "esp":
                set_reg(dest, stack.get(sp) if sp is not None else None)
                unsaved.discard(dest)  # (restored: a push after this one is an argument)
        elif mnemonic in ("add", "sub") and dest is not None:
            sign = 1 if mnemonic == "add" else -1
            number = _number(ops[1])
            if dest in frames:
                if number is None or frames[dest] is None:  # sub esp, eax: where to, nobody knows
                    frames[dest] = None
                else:
                    frames[dest] += sign * number
            elif number is not None and dest in regs:
                term, delta = regs[dest]
                if in_loop(at):
                    out.walked.add(term)
                set_reg(dest, (term, delta + sign * number))
            elif sign == 1 and _REG.get(ops[1]) is not None:  # a base and an index: the element, read as the first
                other = _REG[ops[1]]
                set_reg(dest, regs[dest] if dest in regs and other not in regs else
                        regs[other] if other in regs and dest not in regs else None)
            else:
                set_reg(dest, None)
        elif mnemonic in ("inc", "dec") and dest in regs:
            term, delta = regs[dest]
            if in_loop(at):
                out.walked.add(term)
            set_reg(dest, (term, delta + (1 if mnemonic == "inc" else -1)))
        elif mnemonic == "leave":  # mov esp, ebp; pop ebp
            frames["esp"] = None if frames.get("ebp") is None else frames["ebp"] + WORD
            set_reg("ebp", None)
        elif mnemonic == "call" or mnemonic == "jmp" and (target := _number(first)) is not None \
                and not vram <= target < end:
            tail = mnemonic == "jmp"
            callee = _number(first)
            if callee is not None:
                if "ecx" in regs and regs["ecx"][1] == 0:
                    out.calls.append((callee, THIS, regs["ecx"][0]))
                if tail:  # its arguments are this function's own, above the return address
                    arguments = [(n, v[0]) for n in range(MAX_ARGUMENTS) if sp is not None and
                                 (v := stack_value(sp + WORD + WORD * n)) is not None and v[1] == 0]
                else:
                    arguments = [] if sp is None else _arguments(stack, pushed, sp)
                out.calls += [(callee, n, term) for n, term in arguments]
                result = (CALL, at - vram, callee)
                out.calls.append((callee, RETURNED, result))
                if tail:
                    out.returns.append(result)
            if sp is not None and not tail:
                if not called and (probe := _stack_probe(instructions, k)):
                    frames["esp"] = sp = sp - probe  # __chkstk takes a big frame's size in eax, moves esp itself
                if callee in pops:
                    frames["esp"] = sp + pops[callee]
                elif not _cleans_up(instructions, k):
                    count = 0
                    while sp + WORD * count in pushed:
                        count += 1
                    frames["esp"] = sp + WORD * count
            pushed.clear()
            called = True
            for r in CALLER_SAVED:
                set_reg(r, None)
            if callee is not None and not tail:
                regs["eax"] = ((CALL, at - vram, callee), 0)
        elif mnemonic in ("ret", "retn"):
            if "eax" in regs and regs["eax"][1] == 0:
                out.returns.append(regs["eax"][0])
        elif mnemonic in ("pushal", "pushad", "popal", "popad"):
            frames["esp"] = None if sp is None else sp + (8 * WORD if mnemonic.startswith("popa") else -8 * WORD)
            if mnemonic.startswith("popa"):
                for r in (*CALLER_SAVED, *CALLEE_SAVED):
                    set_reg(r, None)
        else:
            # whatever else it writes holds no pointer we know (xor, imul, setcc, cdq's edx, rep movsd's esi...)
            if dest is not None and mnemonic not in NO_WRITE:
                if dest == "esp":
                    frames["esp"] = None
                else:
                    set_reg(dest, None)
            if mnemonic == "xchg" and len(ops) == 2 and _REG.get(ops[1]) is not None:
                set_reg(_REG[ops[1]], None)
            if len(ops) == 1 and mnemonic in ("imul", "mul", "div", "idiv") or mnemonic in IMPLICIT:
                for r in IMPLICIT.get(mnemonic, ("eax", "edx")):
                    set_reg(r, None)
            if STRINGS.match(mnemonic) and not any("xmm" in op for op in ops):
                for r in ("esi", "edi", "ecx", "eax"):
                    set_reg(r, None)
    return out


def _arguments(stack: dict, pushed: set[int], sp: int) -> list[tuple[int, tuple]]:
    """(n, pointer) for the arguments pushed for a call: the slots from esp up written since the call before."""
    found = []
    for n in range(MAX_ARGUMENTS):
        if sp + WORD * n not in pushed:
            break
        value = stack.get(sp + WORD * n)
        if value is not None and value[1] == 0:
            found.append((n, value[0]))
    return found


def _stack_probe(instructions: list, k: int) -> int:
    """The frame size a call at k is handed in eax by the `mov eax, n` right before it (Visual C++'s __chkstk, for a
    frame of a page or more), or 0."""
    if not k:
        return 0
    _, mnemonic, ops = instructions[k - 1]
    size = _number(ops[1]) if mnemonic == "mov" and len(ops) == 2 and ops[0] == "eax" else None
    return size if size is not None and size >= PAGE and size % WORD == 0 else 0


def _cleans_up(instructions: list, k: int) -> bool:
    """Whether the call at k is followed by `add esp, n` (a __cdecl callee: the caller pops)."""
    if k + 1 >= len(instructions):
        return False
    _, mnemonic, ops = instructions[k + 1]
    return mnemonic == "add" and len(ops) == 2 and ops[0] == "esp" and _number(ops[1]) is not None


def pops(symbol: str) -> int | None:
    """The bytes a function pops on return, from its symbol as Visual C++ decorates a C name: 0 for `_f`
    (__cdecl), 8 for `_f@8` (__stdcall), 4 for `@f@12` (__thiscall written as __fastcall: ecx and edx count; a
    __fastcall's first two arguments do too). None for a name that doesn't say (a C++ name)."""
    m = re.fullmatch(r"([_@])[A-Za-z_$][\w$]*?(?:@(\d+))?", symbol)
    if m is None:
        return None
    if m.group(1) == "_":
        return int(m.group(2) or 0)
    return max(0, int(m.group(2) or 0) - 2 * WORD)
