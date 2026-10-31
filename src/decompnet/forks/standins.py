"""A starting point for an LTCG unit's harness (forks/ltcg.py): stand-ins for what the unit uses from the rest of the
game, and the calls HarnessEntry makes, from the original's references.

Under link-time code generation a unit's code depends on what it's linked with: the callees it calls (which
registers they read and clobber decide what the caller keeps where), the globals it reads, and its own callers
(which registers they load before the call decide its convention). So harness/<unit>.*.cpp define stand-ins for
the unit's references outside it, and harness/<unit>.drv.cpp calls its functions the way the original's callers
do. This writes both from functions.json: every relocation of the unit's functions to a function or data outside
the unit, how the instructions use each global (its size, float or integer, the offsets read), and each function's
convention and call sites (forks/convention.py).
"""

from __future__ import annotations

from capstone import x86

from .convention import Program, call_site, evidence, instructions

_FLOAT = ("fld", "fst", "fstp", "fadd", "fsub", "fsubr", "fmul", "fdiv", "fdivr", "fcom", "fcomp", "movss",
          "addss", "subss", "mulss", "divss", "comiss", "ucomiss", "cvtss2sd", "minss", "maxss", "sqrtss")


def _c_name(symbol: str) -> str:
    """func_XXXXXXXX / D_XXXXXXXX from a decorated name (_func_00012345@8 -> func_00012345)."""
    return symbol.lstrip("_@").split("@", 1)[0]


def _uses(prog: Program, addr: int) -> dict[int, list]:
    """Relocation offset -> the instruction holding it, for one function."""
    out = {}
    for ins in instructions(prog.code(addr), addr):
        for k in range(ins.size):
            out[ins.address - addr + k] = ins
    return out


def _type(reads: list[tuple[int, int, bool]]) -> tuple[str, str]:
    """(declaration with {name}, what the code does with it) from (offset, operand size, float) reads."""
    offsets = sorted({o for o, _, _ in reads})
    if not reads:
        return "u8 {name}[4]", "only its address is used"
    if offsets != [0] or len({s for _, s, _ in reads}) > 1:
        end = max(o + max(s, 1) for o, s, _ in reads)
        return f"u8 {{name}}[0x{end:X}]", ("read at " + ", ".join(f"+0x{o:X}" for o in offsets[:8]) +
                                           ": a struct or an array (give it its real type)")
    size, flt = reads[0][1], any(f for _, _, f in reads)
    if flt:
        return ("double {name}" if size == 8 else "float {name}"), "used as a float" if size != 8 else "a double"
    return {1: "u8 {name}", 2: "u16 {name}", 8: "u64 {name}"}.get(size, "int {name}"), \
        "1 byte" if size == 1 else f"{size or 4} bytes"


def skeleton(prog: Program, unit: str) -> dict[str, str]:
    """{"standins": harness/<unit>.standins.cpp, "driver": HarnessEntry to put in harness/<unit>.drv.cpp, "externs":
    declarations for src/auto/<unit>.cpp}."""
    mine = {a for a, fn in prog.functions.items() if fn["unit"] == unit}
    if not mine:
        raise KeyError(unit)
    data: dict[int, list[tuple[int, int, bool]]] = {}
    users: dict[int, list[str]] = {}
    callees: dict[int, list[tuple[int, int]]] = {}
    imports: set[str] = set()
    for a in sorted(mine):
        fn = prog.functions[a]
        at = None
        for off, rtype, name, addend in fn["relocs"]:
            if name is None:
                continue
            target = prog.by_name.get(name)
            if target is not None:
                if target not in mine:
                    callees.setdefault(target, []).append((a, off))
                continue
            if name.startswith("__imp__"):
                imports.add(name.removeprefix("__imp__"))
                continue
            if not name.startswith("_D_"):
                continue
            base = int(name[3:], 16)
            at = at or _uses(prog, a)
            ins = at.get(off)
            size, flt = 0, False
            if ins is not None:
                mem = [op for op in ins.operands if op.type == x86.X86_OP_MEM]
                size = mem[0].size if mem else 0
                flt = ins.mnemonic in _FLOAT or any(op.type == x86.X86_OP_REG and
                                                    ins.reg_name(op.reg).startswith("xmm") for op in ins.operands)
            data.setdefault(base, []).append((addend, size, flt))  # size 0: only its address (push, lea, mov r, imm)
            users.setdefault(base, [])
            if fn["name"] not in users[base]:
                users[base].append(fn["name"])

    lines = [f"// Stand-ins for {unit} (harness mode): what its functions use from the rest of the game, from the",
             "// original's references (standins_for). Give each the type and shape the real one has: a stand-in",
             "// function must read the registers the original reads and leave the ones its callers keep alive, or the",
             "// unit's code around the call changes. A callee from middleware or the XDK goes in a .nogl.cpp file.",
             '#include "common.h"', ""]
    externs = []
    if data:
        lines.append("// ---- data the unit reads (definitions here; the externs below go in its source)")
    for base in sorted(data):
        decl, why = _type([r for r in data[base] if r[1]])
        name = f"D_{base:08X}"
        lines.append(f"{decl.format(name=name)};  // {why}; used by {', '.join(users[base][:3])}")
        externs.append(f"extern {decl.format(name=name)};")
    if callees:
        lines += ["", "// ---- functions the unit calls (the original's convention for each, then a stub to shape)"]
    for target in sorted(callees):
        fn = prog.functions[target]
        name = _c_name(fn["name"])
        conv = prog.convention(target)
        sites = callees[target]
        lines.append(f"// {name} ({fn['unit']}): {conv.describe()}")
        caller, off = sites[0]
        setup = call_site(prog, caller, off)
        if setup:
            lines.append(f"//   called from {prog.functions[caller]['name']}: {'; '.join(setup)} -> call")
        args = [f"int {r}" for r in conv.register_params()]  # LTCG's own registers: say which by the names
        slots = sorted({n for n in conv.stack if 4 <= n <= 64 and n % 4 == 0} |
                       set(range(4, 4 + (conv.ret or 0), 4)))  # `ret N` pops N bytes of arguments
        args += [f"int arg{n // 4}" for n in slots]
        params = ", ".join(args) or "void"
        todo = "TODO: the signature your source declares"
        if "ecx" in conv.reads:
            todo += "; it takes ecx: `this` (a method) or __fastcall's first argument"
        lines.append(f"int {name}({params}) {{ return 0; }}  // {todo}")
        externs.append(f"int {name}({params});")
    if imports:
        lines += ["", f"// kernel or DLL imports (link them: // HARNESS-LINK: xboxkrnl.lib on Xbox): "
                      f"{', '.join(sorted(imports)[:12])}"]

    drv = ['extern "C" __declspec(dllexport) void HarnessEntry(void)', "{"]
    for a in sorted(mine):
        fn = prog.functions[a]
        name = _c_name(fn["name"])
        outside = [(c, off) for c, off in prog.calls.get(a, []) if c not in mine]
        found = evidence(prog, a, sites=2)
        drv.append(f"    // {name}: {found[0]}")
        drv += [f"    //   {line.strip()}" for line in found[1:] if line.startswith("  ")]
        if not outside and not prog.pointers.get(a):
            drv.append("    //   (only the unit's own functions call it: they keep it in the link)")
        drv.append(f"    // {name}(/* as the call sites above load it */);")
    drv.append("}")
    return {"standins": "\n".join(lines) + "\n", "driver": "\n".join(drv) + "\n", "externs": "\n".join(externs) + "\n"}


def render(prog: Program, unit: str) -> str:
    """What the standins_for tool shows."""
    s = skeleton(prog, unit)
    return (f"Starting points for {unit}'s harness, from the original's references. Write them into the files with "
            f"edit_file (old=\"\" makes a new file), then shape them by hand:\n\n"
            f"### harness/{unit}.standins.cpp\n```cpp\n{s['standins']}```\n\n"
            f"### HarnessEntry for harness/{unit}.drv.cpp (replace the commented-out one)\n```cpp\n{s['driver']}```\n\n"
            f"### Declarations for src/auto/{unit}.cpp (or a header it includes)\n```cpp\n{s['externs']}```")
