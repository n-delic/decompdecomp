"""Which of the names a unit's LTCG harness link left unresolved (/FORCE:UNRESOLVED) can change a function's code,
and which can't, for briefs, compile_and_diff and release_function (tools/dll2coff.py writes the judge's side).

What experiments on the LTCG demo and a retail game showed (the same function judged with each
reference left unresolved and with it defined in the link):
- Data (globals, pool constants, a global's fields or address): no difference on the demo's small functions, but
  not on a retail game's: under LTCG the compiler sees (or doesn't see) a global's definition, its type and whether it's
  const, and that can change the scheduling and code around its loads. So data counts as "may matter": define it
  with its real type in the unit or a harness file before tuning the code. Steering codegen instead (a `volatile`
  in the harness) is never the fix. (An unresolved D_<address> isn't checked either: one that names the wrong
  address only shows once something defines it.)
- A callee the original calls with a standard convention (__cdecl, __stdcall, __thiscall: `ecx` and the stack):
  no difference. The call is the same, and its target is written as the original's.
- A callee the original passes arguments in other registers (LTCG's own convention): the caller's code differs
  (pushes instead of register loads, other registers kept) until a definition is in the link, since LTCG picks a
  callee's convention from its definition. The demo's RunAccum: 83% with Accum::Add (this in esi) and
  AccumulateParticle (ecx, eax) unresolved, 100% with stand-ins for both and Accum::Mix (__thiscall) still
  unresolved.
- A callee the original inlines: the caller calls it instead until its body is in the link (RunFrame: 44% with
  its five callees unresolved).
- Library code (XDK, C runtime): leave it unresolved, declared as its header has it. A /GL stand-in of one changes
  the calls to it (FormatReport: 100% with sprintf unresolved, 89% with a stand-in for it in the link).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .convention import Program

# an identifier's address: D_004AE1DB, func_000115A0, g_3EAD58, Func_1318F0 (5-8 hex digits after an underscore)
_ADDRESS = re.compile(r"(?:^|_)([0-9A-Fa-f]{5,8})$")


@dataclass
class Unresolved:
    symbol: str  # the decorated name the link reported
    name: str  # the identifier as source writes it
    kind: str  # data | standard | register | inlined | library | unknown
    matters: bool | None  # whether it can change the function's code (None: can't tell)
    why: str
    addr: int | None = None

    def line(self) -> str:
        return f"{self.name} ({self.why})"


def identifier(symbol: str) -> str:
    """The name source code uses for a decorated symbol: `?func_000115A0@Accum@@QAEXPBUParticle@@H@Z` ->
    func_000115A0, `_D_004AE1DB` -> D_004AE1DB, `_func_00011130@8` -> func_00011130, `@Foo@8` -> Foo,
    `??0CFoo@@QAE@XZ` -> CFoo (a constructor), `??1CFoo@@...` -> ~CFoo."""
    s = symbol.strip()
    if s.startswith("??0") or s.startswith("??1"):
        cls = s[3:].split("@", 1)[0]
        return cls if s[2] == "0" else "~" + cls
    if s.startswith("?"):
        return s[1:].split("@", 1)[0]
    m = re.fullmatch(r"[_@]?([A-Za-z_$][\w$]*?)(?:@\d+)?", s)
    return m.group(1) if m else s


def is_data_symbol(symbol: str) -> bool | None:
    """True for a C++ global (`?g_x@@3MA`, a static member `?s@C@@2HA`), False for a function (C++ code, a __stdcall or
    __fastcall C name), None for a plain C name (`_D_00123456`, `_sprintf`), which doesn't say."""
    if symbol.startswith("?"):
        return bool(re.match(r"\?[^?@][^@]*(?:@[^@]+)*@@[23]", symbol))
    if re.search(r"@\d+$", symbol):
        return False
    return None


def _declared_registers(symbol: str) -> set[str]:
    """Registers the declaration itself passes arguments in: ecx for a member function (`this`), ecx and edx for a
    __fastcall one."""
    if symbol.startswith("@") or re.match(r"\?[^@]+(?:@[^@]+)*@@YI", symbol):
        return {"ecx", "edx"}
    if symbol.startswith("??0") or symbol.startswith("??1"):  # a constructor, a destructor
        return {"ecx"}
    # a class's non-static member function: after the scope, an access code other than Y (free), C/K/S (static)
    m = re.match(r"\?[^@?]+(?:@[^@]+)+@@([A-Z])", symbol)
    return {"ecx"} if m and m.group(1) in "ABEFIJMNQRUV" else set()


def address_of(prog: Program | None, symbol: str) -> int | None:
    """The original's address a name means: a function by its symbol or func_<address> name, else the address its
    identifier ends in."""
    name = identifier(symbol)
    if prog is not None:
        for what in (symbol, name):
            if what in prog.by_name:
                return prog.by_name[what]
    m = _ADDRESS.search(name)
    return int(m.group(1), 16) if m else None


def classify(prog: Program | None, caller: int | None, symbol: str, library: set[int] = frozenset()) -> Unresolved:
    """Whether the unresolved `symbol` can change the code of the original function at `caller` (see the module)."""
    name = identifier(symbol)
    data = is_data_symbol(symbol)
    addr = address_of(prog, symbol)
    if data is None and re.match(r"(?:D|g|s|DAT|dat)_", name):
        data = True  # the D_<address> / g_ names data gets
    function = prog is not None and addr is not None and addr in prog.functions
    if data or (data is None and addr is not None and not function):
        return Unresolved(symbol, name, "data", None, "data: may change this function's code under LTCG (the "
                          "compiler can't see its definition or type); define it with its real type in the unit or "
                          "harness/, never steer the code with volatile", addr)
    if addr is None or prog is None:
        if not symbol.startswith("?"):
            return Unresolved(symbol, name, "unknown", None, "a C name with no address: library code (XDK, C "
                              "runtime) is harmless, declared as its header has it; a function of the game's matters "
                              "if the target calls it with registers or inlines it", addr)
        return Unresolved(symbol, name, "unknown", None, "a function with no address in its name: it matters if the "
                          "target calls it with arguments in registers, or has no call there (inlined); give it the "
                          "original's func_<address> name or `// FUNCTION:` and the brief can tell", addr)
    if not function:
        return Unresolved(symbol, name, "unknown", None, f"no function starts at 0x{addr:X}", addr)
    if addr in library:
        return Unresolved(symbol, name, "library", False, "library code: harmless unresolved; never stand it in "
                          "with /GL (that changes the calls to it)", addr)
    if caller is not None and caller in prog.functions and not any(
            name_ and prog.by_name.get(name_) == addr for _, _, name_, _ in prog.functions[caller]["relocs"]):
        return Unresolved(symbol, name, "inlined", True, "the original has no call to it here: it's inlined (or not a "
                          "callee of the original's), so its body must be in the link for the code to match (written "
                          "in the unit, or a stand-in with the original's body)", addr)
    try:
        conv = prog.convention(addr)
    except Exception:  # a heuristic: never in the way of a brief
        return Unresolved(symbol, name, "unknown", None, "its convention couldn't be read", addr)
    extra = [r for r in conv.reads if r not in _declared_registers(symbol)] + list(conv.xmm)
    if extra:
        return Unresolved(symbol, name, "register", True, f"the original passes it {', '.join(extra)}: until a "
                          "definition is in the link the call is a standard one, so give it a stand-in shaped like "
                          "the original (standins_for)", addr)
    return Unresolved(symbol, name, "standard", False, "a standard call: harmless, the judge writes the original's "
                      "callee there", addr)


def used_by(code: str, symbols: list[str]) -> list[str]:
    """The symbols whose identifier the code uses."""
    out = []
    for s in symbols:
        name = identifier(s).lstrip("~")
        if name and re.search(rf"(?<![\w$]){re.escape(name)}(?![\w$])", code):
            out.append(s)
    return out


def summary(found: list[Unresolved], unit: str, others: int = 0) -> list[str]:
    """Lines for a brief or a compile result: what matters first (with what to do), then what doesn't."""
    if not found and not others:
        return []
    matters = [u for u in found if u.matters]
    unknown = [u for u in found if u.matters is None]
    harmless = [u for u in found if u.matters is False]
    out = []
    if matters:
        out.append(f"Unresolved in the harness link and changing this function's code (do it now, in this claim: "
                   f"edit_file harness/{unit}.<name>.cpp with a stand-in shaped like the original's, or write it in the "
                   "unit; standins_for writes a starting point):")
        out += [f"- {u.line()}" for u in matters]
    if unknown:
        out.append("Unresolved, may change this function's code:")
        out += [f"- {u.line()}" for u in unknown]
    if harmless:
        out.append("Unresolved and harmless (no reason to stop or release: the judge writes the original's reference in "
                   "their place): " + ", ".join(u.name for u in harmless[:16]) + (" ..." if len(harmless) > 16 else ""))
    if others:
        out.append(f"({others} more unresolved names are used by other functions of {unit}, not this one.)")
    return out
