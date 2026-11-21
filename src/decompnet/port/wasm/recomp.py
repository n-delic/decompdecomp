"""Recompiled C where the decomp has none yet, in the port's native build (docs/N64RECOMP.md, phase 2).

A function the game has no C for stopped the port the first time it ran (a racing game: its first thread's code).
The console's static recompiler (N64: N64Recomp, Porting → Recompile) already writes every function as C that does
what its instructions do, so the host compiles that C beside the game's and runs it in their place. As functions
match, their decompiled C takes over; nothing else changes. For each function, by its console address:

1. the game's own C (the WebAssembly module exports it): `R_<name>` is a bridge into it (`port_recomp_decomp`,
   through funcs.c's invokers, which take o32's argument words);
2. else the host's own (osSendMesg, bzero... by name, or library detection's name for it): `R_<name>` calls it.
   Recompiled libultra would drive hardware and threads the host emulates;
3. else the recompiled function itself, renamed `R_<name>` so it never meets the game's C;
4. else a stub that says it was reached.

Recompiled C runs natively in the host, on the port's own memory: its MEM_ macros (recomp.h, rewritten here) read and
write the console's addresses big-endian, as the WebAssembly code does, with uncached RAM and the hardware's
registers through the host (port_io_load/port_io_store). N64Recomp's stores (`MEM_W(o, r) = v;`) become calls.

Calls between the two kinds of C:
- the game's C calling a function it has no C for already imports it (`env.func_X`): its definition here fills a
  register context as o32 passes those types and runs `R_func_X`;
- a function pointer the game calls (port_func) or a thread's entry finds `port_recomp_pointer`'s bridge, which
  takes integer arguments (a float one through a pointer is lost: said in the summary);
- recompiled code runs on a MIPS stack in the console's address space, one per host thread (`port_recomp_sp`, at
  STACKS), handed down when it calls into the game's C and back.

The native builds have it (Play, the app export, the Miyoo Mini's, compiled for its ARM chip with recompiled C's
MIPS stacks mapped by miyoo/mem32.c); the browser build takes the game's C alone.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .adapt import REGS  # MIPS's $a0-$a3 and $v0 as words of memory the game's C passes and reads

STACKS = 0x14000000  # each host thread's MIPS stack for recompiled code: 256 KB, above the shadow stacks
STACK_SIZE = 0x40000
STORE = re.compile(r"^(\s*)MEM_(W|H|B|HU|BU)\(([^()]*)\) = (.+);\s*$", re.M)
CALL = re.compile(r"\b([A-Za-z_]\w*)(?=\(rdram, ctx\))")
DEFINED = re.compile(r"^RECOMP_FUNC void ([A-Za-z_]\w*)\(uint8_t\* rdram, recomp_context\* ctx\)", re.M)
INSTRUCTION = re.compile(r"^\s*// 0x([0-9A-F]{8}): (\S.*)$", re.M)
# a recompiled function's whole definition, its C name (as N64Recomp ends each: a line of ";}")
FALLS = re.compile(r"^RECOMP_FUNC void R_([A-Za-z_]\w*)\(uint8_t\* rdram, recomp_context\* ctx\) \{\n.*?\n;\}", re.M | re.S)
ENTRY = re.compile(r"^RECOMP_FUNC void R_([A-Za-z_]\w*)\(uint8_t\* rdram, recomp_context\* ctx\) \{", re.M)
DECLARED = re.compile(r"^void ([A-Za-z_]\w*)\(uint8_t\* rdram, recomp_context\* ctx\);", re.M)
SYMBOL = re.compile(r'\{ name = ("(?:[^"\\]|\\.)*"), vram = 0x([0-9A-Fa-f]+), size = 0x[0-9A-Fa-f]+ \}')
IMPORT = re.compile(r"/\* import: 'env' '([^']+)' \*/\n(u32|u64|f32|f64|void) (w2c_env_\w+)\(struct w2c_env\*((?:, \w+)*)\);")
EXPORT = re.compile(r"/\* export: '([^']+)' \*/\n(\w+) (w2c_game_\w+)\(w2c_game\*([^)]*)\);")
HOST = re.compile(r"^(u32|u64|f32|f64|void) w2c_env_(\w+)\(struct w2c_env \*\w*((?:, (?:u32|u64|f32|f64) \w+)*)\)\s*\{",
                  re.M)


def _demangle(c: str) -> str:
    """wasm2c's name for an import, as the C names it (`0x5F_osSiRawStartDma` is `__osSiRawStartDma`)."""
    return re.sub(r"0x([0-9A-F]{2})", lambda m: chr(int(m.group(1), 16)), c)


# ---------------------------------------------------------------- recomp.h, for the port's memory

LOADS = '''
/* a loop's safe point (loops): every 1024th time round, a thread the retrace woke that outranks this one takes over,
   as the interpreter lets it every 64K instructions */
void port_safe_point(void);
static inline void port_recomp_poll(void) { static __thread unsigned n; if (!(++n & 0x3FF)) port_safe_point(); }
uint64_t port_io_load(uint64_t addr);
uint64_t port_io_store(uint64_t addr, uint64_t value);
/* the console's memory as the port keeps it: big-endian, at the console's own addresses from `rdram` (the host's
   memory base); uncached RAM and the hardware's registers through the host, as the WebAssembly code reaches them */
static inline uint8_t *port_at(uint8_t *rdram, gpr a, int store, uint64_t v) {
    uint32_t x = (uint32_t)a;
    if (x >= 0xA0000000u) x = (uint32_t)(store ? port_io_store(x, v) : port_io_load(x));
    return rdram + x;
}
static inline int32_t port_lw(uint8_t *rdram, gpr a) { uint32_t v; memcpy(&v, port_at(rdram, a, 0, 0), 4); return (int32_t)__builtin_bswap32(v); }
static inline int16_t port_lh(uint8_t *rdram, gpr a) { uint16_t v; memcpy(&v, port_at(rdram, a, 0, 0), 2); return (int16_t)__builtin_bswap16(v); }
static inline int8_t port_lb(uint8_t *rdram, gpr a) { return *(int8_t *)port_at(rdram, a, 0, 0); }
/* a hardware register gets the value itself, memory its bytes big-endian: a racing game's audio interface was given
   its buffer lengths byte-swapped (0xFC0 as 0xC00F0000), and the length the game read back overflowed its sample
   count and its audio command list */
static inline void port_sw(uint8_t *rdram, gpr a, uint32_t v) { uint32_t b = __builtin_bswap32(v); memcpy(port_at(rdram, a, 1, v), &b, 4); }
static inline void port_sh(uint8_t *rdram, gpr a, uint16_t v) { uint16_t b = __builtin_bswap16(v); memcpy(port_at(rdram, a, 1, v), &b, 2); }
static inline void port_sb(uint8_t *rdram, gpr a, uint8_t v) { *port_at(rdram, a, 1, v) = v; }
#define MEM_W(offset, reg) port_lw(rdram, (gpr)(reg) + (gpr)(offset))
#define MEM_H(offset, reg) port_lh(rdram, (gpr)(reg) + (gpr)(offset))
#define MEM_B(offset, reg) port_lb(rdram, (gpr)(reg) + (gpr)(offset))
#define MEM_HU(offset, reg) ((uint16_t)port_lh(rdram, (gpr)(reg) + (gpr)(offset)))
#define MEM_BU(offset, reg) ((uint8_t)port_lb(rdram, (gpr)(reg) + (gpr)(offset)))
#define STORE_W(offset, reg, v) port_sw(rdram, (gpr)(reg) + (gpr)(offset), (uint32_t)(v))
#define STORE_H(offset, reg, v) port_sh(rdram, (gpr)(reg) + (gpr)(offset), (uint16_t)(v))
#define STORE_B(offset, reg, v) port_sb(rdram, (gpr)(reg) + (gpr)(offset), (uint8_t)(v))
#define STORE_HU STORE_H
#define STORE_BU STORE_B
#define SD(val, offset, reg) { STORE_W((offset) + 4, reg, (gpr)(val) >> 0); STORE_W((offset) + 0, reg, (gpr)(val) >> 32); }
'''


LABEL = re.compile(r"^(L_[0-9A-Fa-f]+):", re.M)
GOTO = re.compile(r"\bgoto (L_[0-9A-Fa-f]+);")


def loops(text: str) -> str:
    """Each backward branch (a loop) of N64Recomp's C with a safe point (port_recomp_poll) before it. The host
    switches threads only at safe points, and recompiled C had none in its loops: a racing game's boot thread
    (priority 10) waits for a flag the higher threads set, and spinning in its recompiled C it never let them run, so
    the native build booted to its first audio task and drew nothing."""
    where = {m.group(1): m.start() for m in LABEL.finditer(text)}
    return GOTO.sub(lambda m: f"{{ port_recomp_poll(); goto {m.group(1)}; }}"
                    if where.get(m.group(1), len(text)) < m.start() else m.group(0), text)


_LINK_BRANCH = re.compile(r"^([ \t]*)// 0x([0-9A-F]{8}): (?:bal|b(?:gez|ltz)all?)\b[^\n]*", re.M)


def link_branches(text: str) -> str:
    """Keep the link register for assembly that uses a branch-and-link to get its own PC.

    N64Recomp models the branch as a C call and omits the architectural link. The R4300 writes $ra even
    when the condition is false, which code uses to locate a local jump target before the next call.
    """
    return _LINK_BRANCH.sub(
        lambda branch: f"{branch[0]}\n{branch[1]}ctx->r31 = (gpr)(int32_t)0x{int(branch[2], 16) + 8:08X}u;",
        text,
    )


def stores(text: str) -> str:
    """N64Recomp's stores, `MEM_W(o, r) = v;`, as calls: the port's memory is big-endian, not an lvalue."""
    return STORE.sub(lambda m: f"{m.group(1)}STORE_{m.group(2)}({m.group(3)}, {m.group(4)});", text)


MULTIPLY_32 = '''static inline void DMULTU(uint64_t a, uint64_t b, uint64_t* lo64, uint64_t* hi64) {
    uint64_t al = (uint32_t)a, ah = a >> 32, bl = (uint32_t)b, bh = b >> 32;
    uint64_t ll = al * bl, lh = al * bh, hl = ah * bl, hh = ah * bh;
    uint64_t mid = (ll >> 32) + (uint32_t)lh + (uint32_t)hl;
    *lo64 = (mid << 32) | (uint32_t)ll;
    *hi64 = hh + (lh >> 32) + (hl >> 32) + (mid >> 32);
}

static inline void DMULT(int64_t a, int64_t b, int64_t* lo64, int64_t* hi64) {
    uint64_t lo, hi;
    DMULTU((uint64_t)a, (uint64_t)b, &lo, &hi);
    /* the signed product's high half: the unsigned one less each operand where the other is negative */
    hi -= (a < 0 ? (uint64_t)b : 0) + (b < 0 ? (uint64_t)a : 0);
    *lo64 = (int64_t)lo;
    *hi64 = (int64_t)hi;
}'''


def header(original: str) -> str:
    """N64Recomp's recomp.h with the port's memory: its MEM_ and SD definitions replaced (LOADS), its helpers' stores
    rewritten. Everything else (the registers' context, the float macros, LOOKUP_FUNC) as it is."""
    text = re.sub(r"#define (MEM_W|MEM_H|MEM_B|MEM_HU|MEM_BU)\(offset, reg\) \\\n[^\n]*\n", "", original)
    text = re.sub(r"#define SD\(val, offset, reg\) \{ \\\n(?:[^\n]*\\\n)*\}\n", "", text)
    if "MEM_W(offset" in text or "#define SD(" in text:
        raise ValueError("N64Recomp's recomp.h has changed: its memory macros aren't where the port replaces them")
    at = text.index("typedef uint64_t gpr;") + len("typedef uint64_t gpr;")
    text = (text[:at] + "\n#include <string.h>\n" + LOADS
            + "\nextern int port_recomp_watch; /* PORT_RECOMP_CALLS: each listed function's entries (port_recomp_entered) */"
            "\nvoid port_recomp_entered(uint32_t vram, void *ctx);\n" + text[at:])
    text = stores(text)
    # a 32-bit chip (the Miyoo Mini's ARM) has no 128-bit integer for MIPS's 64-bit multiplies: the product from
    # 32-bit halves instead, where N64Recomp's header would stop the build
    text = text.replace('#else\n#error "128-bit integer type not found"', "#else\n" + MULTIPLY_32, 1)
    # N64Recomp asserts that no float operand is NaN, a check for its own users; the console computes with NaN as it
    # comes (a racing game's camera math meets one at its title screen), so the port says so once a function and goes on
    nan = "#define NAN_CHECK(val) \\\n    assert(val == val)\n"
    if nan not in text:
        raise ValueError("N64Recomp's recomp.h has changed: its NAN_CHECK isn't where the port replaces it")
    text = text.replace(nan, "void port_recomp_nan(const char *where);\n"
                             "#define NAN_CHECK(val) ((void)((val) == (val) || (port_recomp_nan(__func__), 0)))\n", 1)
    if re.search(r"MEM_[WHB]U?\([^;]*\) =", text) or "0xFFFFFFFF80000000" in text:
        raise ValueError("N64Recomp's recomp.h addresses memory where the port doesn't rewrite it")
    return text


# ---------------------------------------------------------------- o32 between C's types and the registers' context

def o32(types: list[str]) -> list[tuple]:
    """Where o32 passes each argument: ("fpr", n) a float in $f12/$f14 (leading floats), ("dfpr", n) a double there,
    ("gpr", slot) a word in $a0-$a3 or the stack's slot, ("gpr64", slot) 64 bits in an aligned pair, ("gprf", slot)
    a float in a word, ("gprd", slot) a double in a pair. As funcs.c's invokers (build.invoker) read them."""
    out, slot, fpr, floats = [], 0, 12, True
    for i, t in enumerate(types):
        if t in ("f32", "f64") and floats and i < 2:
            if t == "f64":
                slot += slot % 2
                out.append(("dfpr", fpr))
                slot += 2
            else:
                out.append(("fpr", fpr))
                slot += 1
            fpr += 2
            continue
        floats = False
        if t in ("u64", "f64"):
            slot += slot % 2
            out.append(("gpr64" if t == "u64" else "gprd", slot))
            slot += 2
        else:
            out.append(("gpr" if t == "u32" else "gprf", slot))
            slot += 1
    return out


def _put(where: tuple, value: str) -> str:
    """C that puts `value` where o32 passes it, into context `c` (the stack's slots through the MIPS stack)."""
    kind, n = where
    word = (lambda s, v: f"c.r{4 + s} = (gpr)(int32_t)({v});" if s < 4 else f"STORE_W({4 * s}, c.r29, {v});")
    if kind == "fpr":
        return f"c.f{n}.fl = {value};"
    if kind == "dfpr":
        return f"c.f{n}.d = {value};"
    if kind == "gpr":
        return word(n, value)
    if kind == "gprf":
        return word(n, f"port_rb32({value})")
    v = value if kind == "gpr64" else f"port_rb64({value})"
    return word(n, f"(uint32_t)((uint64_t)({v}) >> 32)") + " " + word(n + 1, f"(uint32_t)({v})")


def _get(where: tuple, t: str) -> str:
    """C that reads an argument of type `t` from where o32 passed it, from context `ctx`."""
    kind, n = where
    word = (lambda s: f"(uint32_t)ctx->r{4 + s}" if s < 4 else f"(uint32_t)MEM_W({4 * s}, ctx->r29)")
    if kind == "fpr":
        return f"ctx->f{n}.fl"
    if kind == "dfpr":
        return f"ctx->f{n}.d"
    if kind == "gpr":
        return word(n)
    if kind == "gprf":
        return f"port_rf32({word(n)})"
    pair = f"((uint64_t){word(n)} << 32 | {word(n + 1)})"
    return pair if kind == "gpr64" else f"port_rf64({pair})"


def _result(ret: str, value: str) -> str:
    """C that leaves a C function's result in the registers o32 returns it in."""
    return {"void": f"{value};",
            "u32": f"ctx->r2 = (gpr)(int32_t)({value});",
            "u64": f"{{ uint64_t r_ = {value}; ctx->r2 = (gpr)(int32_t)(r_ >> 32); ctx->r3 = (gpr)(int32_t)r_; }}",
            "f32": f"ctx->f0.fl = {value};",
            "f64": f"ctx->f0.d = {value};"}[ret]


def _returned(ret: str) -> str:
    return {"void": "", "u32": "return (u32)c.r2;", "u64": "return (u64)(uint32_t)c.r2 << 32 | (uint32_t)c.r3;",
            "f32": "return c.f0.fl;", "f64": "return c.f0.d;"}[ret]


# ---------------------------------------------------------------- the choice and the bridges

def table(folder: Path, entry: int | None = None) -> dict[int, tuple[str, str | None]]:
    """{console address: (the split's name, N64Recomp's C function for it or None)} from a Recompile's output."""
    funcs = Path(folder) / "funcs"
    defined = set()
    for f in sorted(funcs.glob("funcs_*.c")):
        defined.update(DEFINED.findall(f.read_text(encoding="utf-8", errors="replace")))
    out = {}
    for m in SYMBOL.finditer((Path(folder) / "syms.toml").read_text(encoding="utf-8")):
        name, vram = json.loads(m.group(1)), int(m.group(2), 16)
        c = next((n for n in (name, f"{name}_recomp", "recomp_entrypoint" if vram == entry else None)
                  if n and n in defined), None)
        if vram not in out or (c and not out[vram][1]):
            out[vram] = (name, c)
    return out


MIPS_SIGNATURE = ("i32", ", ".join(["i32"] * 8))  # $a0-$a3 and four stack words in, $v0 out


def mips_signatures(folder: Path, lls: list[Path], fixed: list[str], hosts: list[Path], library_names=None) -> list[str]:
    """The functions the game's C calls that only recompiled C has, and no prototype describes: each gets MIPS's own
    shape (`MIPS_SIGNATURE`) in the build's one signature per function (sigs.txt), so adapt.py's thunks fill what a
    call leaves out from the caller's arguments and the register words, as MIPS leaves them in the registers. A
    racing game's matched alInit calls `alSynNew()` with no arguments, its $a0 and $a1 still alInit's: the bridge
    into the recompiled alSynNew read whatever the register words last held, and built the synthesizer elsewhere."""
    library_names = library_names or {}
    defined, declared = set(), set()
    for ll in lls:
        for m in re.finditer(r"^(define|declare) [^@\n]*@([\w.$]+)\(", Path(ll).read_text(encoding="utf-8"), re.M):
            (defined if m.group(1) == "define" else declared).add(m.group(2))
    hosted = {_demangle(m.group(2)) for path in hosts
              for m in HOST.finditer(Path(path).read_text(encoding="utf-8", errors="replace"))}
    sources = [f.read_text(encoding="utf-8", errors="replace") for f in sorted((Path(folder) / "funcs").glob("funcs_*.c"))]
    try:
        refused = {r["name"] for r in json.loads((Path(folder) / "recomp.json").read_text(encoding="utf-8")).get("refused") or []}
    except (OSError, ValueError):
        refused = set()
    broken = unfinished(sources)  # (once: it reads every function)
    usable = {v for v, (name, c) in table(folder).items()
              if c and c not in broken and name not in refused and c not in refused}
    where = _addresses(fixed)
    return sorted(n for n in declared - defined if _vram(n, where) in usable
                  and library_names.get(n, n) not in hosted and not n.startswith("llvm."))


def _addresses(fixed: list[str]) -> dict[str, int]:
    where = {}
    for row in fixed:
        n, a, k = row.split()
        if k == "func":
            where[n] = int(a, 16)
    return where


def unfinished(texts) -> set[str]:
    """The recompiled functions whose C can't compile as N64Recomp wrote it: a branch to a label the function doesn't
    have (a racing game's func_800424C0 branched past its end in the split, to L_80042650). Each runs only as the
    game's C, or is a stub that says so."""
    from ..csource import items
    out = set()
    for text in texts:
        for chunk, name, _ in items(text):
            if name and set(re.findall(r"goto (L_[0-9A-Fa-f]+);", chunk)) - set(re.findall(r"^\s*(L_[0-9A-Fa-f]+):", chunk, re.M)):
                out.add(name)
    return out


def _vram(name: str, where: dict[str, int]) -> int | None:
    if name in where:
        return where[name]
    auto = re.fullmatch(r"(?:func|D)_([0-9A-Fa-f]{8})", name)
    return int(auto.group(1), 16) if auto else None


def prepare(folder: Path, out: Path, game_h: str, fixed: list[str], hosts: list[Path], recomp_h: Path,
            library_names=None, log=print) -> dict:
    """Write the port's recompiled C into `out` (recomp.h, funcs.h, rec_*.c and bridges.c, each only when it
    changed); returns {"units": [paths], "include": out, "counts": {...}, "pointers_lose_floats": bool}."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    library_names = library_names or {}
    where = _addresses(fixed)
    exported = {}  # console address: the game's C function there
    for m in EXPORT.finditer(game_h):
        name, ret, c, params = m.groups()
        if (v := _vram(name, where)) is not None:
            exported[v] = name
    hosted = {}  # a name the host answers: (its C, its return, its parameters)
    for path in hosts:
        for m in HOST.finditer(Path(path).read_text(encoding="utf-8", errors="replace")):
            ret, c, params = m.groups()
            hosted.setdefault(_demangle(c), (f"w2c_env_{c}", ret, re.findall(r"(u32|u64|f32|f64) \w+", params)))
    recompiled = table(folder)
    sources = {f: f.read_text(encoding="utf-8", errors="replace") for f in sorted((Path(folder) / "funcs").glob("funcs_*.c"))}
    broken = unfinished(sources.values())
    # what N64Recomp refused it writes as an empty function (its `stubs`): that is no translation, and running one
    # returned at once (a racing game's main thread, whose code calls into an overlay the split doesn't describe)
    try:
        refused = {r["name"] for r in json.loads((Path(folder) / "recomp.json").read_text(encoding="utf-8")).get("refused") or []}
    except (OSError, ValueError):
        refused = set()
    broken |= {c for name, c in recompiled.values() if c and (name in refused or c in refused)}
    choice, counts = {}, {"decomp": 0, "host": 0, "recomp": 0, "missing": 0}
    for vram, (name, c) in sorted(recompiled.items()):
        library = library_names.get(name.removesuffix("_recomp"), name.removesuffix("_recomp"))
        kind = ("decomp" if vram in exported else "host" if library in hosted
                else "recomp" if c and c not in broken else "missing")
        choice[vram] = (kind, name, c, library)
        counts[kind] += 1
    # every function the recompiled C calls, by name, is R_<name>: rename its definitions, declarations and calls
    names = {c for _, _, c, _ in choice.values() if c}

    at = {c: vram for vram, (_, _, c, _) in choice.items() if c}

    following = dict(zip(sorted(at.values()), sorted(at.values())[1:]))
    name_at = {vram: c for c, vram in at.items()}

    def falls_into_next(m: re.Match) -> str:
        """A function whose last instruction is no jump or return runs on into the next (hand-written assembly: a
        racing game's cosine adds a quarter turn and falls into its sine). N64Recomp stops at the function's end, so
        its C did nothing more: the next is called there, and returns for both."""
        body, name = m.group(0), m.group(1)
        nxt = name_at.get(following.get(at.get(name, -1), -1))
        # its instructions, as N64Recomp's comments name them: falls through unless the one before the last (the
        # last is a jump's delay slot) is a jump or return
        ops = dict(INSTRUCTION.findall(body))
        last_two = [ops[a].split()[0] for a in sorted(ops)[-2:]]
        if not nxt or not ops or any(op in ("jr", "j", "b", "eret") for op in last_two):
            return body
        return body[:-3] + f"\n    R_{nxt}(rdram, ctx); /* falls into the next function */\n;}}"

    def rename(text: str) -> str:
        text = DEFINED.sub(lambda m: f"RECOMP_FUNC void R_{m.group(1)}(uint8_t* rdram, recomp_context* ctx)", text)
        text = DECLARED.sub(lambda m: f"void R_{m.group(1)}(uint8_t* rdram, recomp_context* ctx);", text)
        text = CALL.sub(lambda m: f"R_{m.group(1)}", text)
        text = FALLS.sub(falls_into_next, text)
        # each function's start tells port_recomp_entered, when PORT_RECOMP_CALLS asks (one test of a flag otherwise)
        return ENTRY.sub(lambda m: m.group(0) if m.group(1) not in at else (
            f"static void R_{m.group(1)}_body(uint8_t* rdram, recomp_context* ctx);\n"
            f"RECOMP_FUNC void R_{m.group(1)}(uint8_t* rdram, recomp_context* ctx) {{ if (port_recomp_watch) "
            f"port_recomp_entered(0x{at[m.group(1)]:08X}u, ctx); R_{m.group(1)}_body(rdram, ctx); }}\n"
            f"static void R_{m.group(1)}_body(uint8_t* rdram, recomp_context* ctx) {{"), text)

    from ..csource import only
    keep = {c for kind, _, c, _ in choice.values() if kind == "recomp" and c}
    drop = names - keep
    units = []
    _write(out / "recomp.h", header(Path(recomp_h).read_text(encoding="utf-8")))
    declared_h = (Path(folder) / "funcs" / "funcs.h").read_text(encoding="utf-8", errors="replace")
    # every function the recompiled C calls, declared: N64Recomp leaves what its runtime provides undeclared
    # (`__osSiRawStartDma_recomp`), and each gets an R_ definition below
    seen = set(DECLARED.findall(declared_h)) | {n for text in sources.values() for n in CALL.findall(text)}
    _write(out / "funcs.h", rename(declared_h) + "".join(
        f"void R_{n}(uint8_t* rdram, recomp_context* ctx);\n" for n in sorted(seen - set(DECLARED.findall(declared_h)))))
    for f, text in sources.items():
        text = only(text, drop & set(DEFINED.findall(text)))
        target = out / f"rec_{f.stem}.c"
        _write(target, stores(rename(loops(link_branches(text)))))
        units.append(target)
    for f in list(out.glob("rec_*.c")):
        if f not in units:
            f.unlink()
    lines = ['#include "recomp.h"', '#include "funcs.h"', '#include <stdio.h>', '#include <stdlib.h>',
             "typedef uint32_t u32; typedef uint64_t u64; typedef float f32; typedef double f64;",
             "struct w2c_env; typedef struct w2c_game w2c_game; w2c_game *port_game(void); uint8_t *port_memory(void);",
             "typedef void (*PortInvoke)(w2c_game*, const u32 *, const u32 *, u32 *);",
             "typedef struct { unsigned address; PortInvoke invoke; } PortCall;",
             "extern const PortCall port_calls[]; extern const unsigned port_call_count;",
             "static u32 port_rb32(f32 v) { u32 b; memcpy(&b, &v, 4); return b; }",
             "static u64 port_rb64(f64 v) { u64 b; memcpy(&b, &v, 8); return b; }",
             "static f32 port_rf32(u32 b) { f32 v; memcpy(&v, &b, 4); return v; }",
             "static f64 port_rf64(u64 b) { f64 v; memcpy(&v, &b, 8); return v; }",
             BRIDGE_RUNTIME % {"stacks": STACKS, "size": STACK_SIZE, "regs": REGS}]
    used = set()  # the host's functions the bridges call, declared before them

    def call_host(library: str) -> str:
        hc, ret, params = hosted[library]
        used.add(library)
        ctx_args = "".join(f", {_get(w, t)}" for w, t in zip(o32(params), params))
        return _result(ret, f"{hc}(NULL{ctx_args})")
    first = len(lines)
    # 1, 2, 4: R_<name> for what isn't the recompiled function itself
    for vram, (kind, name, c, library) in sorted(choice.items()):
        r = f"R_{c or name}"
        if kind == "recomp":
            continue
        if c is None and name in seen:  # (declared by funcs.h under its own name)
            r = f"R_{name}"
        if kind == "decomp":
            body = f"port_recomp_decomp(0x{vram:08X}u, rdram, ctx);"
        elif kind == "host":
            body = call_host(library)
        else:
            body = f'port_recomp_missing(0x{vram:08X}u, "{name}", rdram, ctx);'
        lines.append(f"void {r}(uint8_t* rdram, recomp_context* ctx) {{ (void)rdram; {body} }}")
    # what N64Recomp declared but left to its runtime, and nothing above covers: the host's if it has it
    covered = {f"R_{c or name}" for _, name, c, _ in choice.values()}
    for n in sorted(seen - names - {c for _, _, c, _ in choice.values() if c}):
        if f"R_{n}" in covered:
            continue
        library = library_names.get(n.removesuffix("_recomp"), n.removesuffix("_recomp"))
        if library in hosted:
            body = call_host(library)
        else:
            body = f'port_recomp_missing(0, "{n}", rdram, ctx);'
        lines.append(f"void R_{n}(uint8_t* rdram, recomp_context* ctx) {{ (void)rdram; {body} }}")
    lines[first:first] = [f"{hosted[n][1]} {hosted[n][0]}(struct w2c_env *" + "".join(f", {t}" for t in hosted[n][2]) + ");"
                          for n in sorted(used)]
    # the game's C calling what only recompiled C has: its import, typed as the C declares it
    imported = 0
    for m in IMPORT.finditer(game_h):
        name, ret, c, params = m.groups()
        vram = _vram(name, where)
        if vram is None or vram not in choice or choice[vram][0] != "recomp" or _demangle(c[8:]) in hosted:
            continue
        types = [t.strip() for t in params.split(",") if t.strip()]
        args = "".join(f", {t} a{i}" for i, t in enumerate(types))
        puts = " ".join(_put(w, f"a{i}") for i, w in enumerate(o32(types)))
        lines.append(f"{ret} {c}(struct w2c_env *e{args}) {{ (void)e; uint8_t *rdram = port_memory(); recomp_context c; "
                     f"port_recomp_frame(&c, {max(4, len(types) * 2)}); port_recomp_args(rdram, &c); {puts} "
                     f"R_{choice[vram][2]}(rdram, &c); "
                     f"port_recomp_leave(&c); {_returned(ret)} }}")
        imported += 1
    # every function by its address, for LOOKUP_FUNC; and the recompiled ones for calls through the game's pointers
    lines.append("typedef struct { uint32_t vram; recomp_func_t *func; } PortRecomp;")
    lines.append("static const PortRecomp port_recomp_table[] = {")
    lines += [f"    {{0x{v:08X}u, R_{c or n}}}," for v, (_, n, c, _) in sorted(choice.items())]
    lines.append("};")
    pointers = [(v, c) for v, (kind, _, c, _) in sorted(choice.items()) if kind == "recomp"]
    for v, c in pointers:
        lines.append(f"static u64 G_{c}(w2c_game *g, u64 a0, u64 a1, u64 a2, u64 a3, u64 a4, u64 a5) {{ (void)g; "
                     "uint8_t *rdram = port_memory(); recomp_context c; port_recomp_frame(&c, 6); "
                     "c.r4 = (gpr)(int32_t)a0; c.r5 = (gpr)(int32_t)a1; c.r6 = (gpr)(int32_t)a2; c.r7 = (gpr)(int32_t)a3; "
                     f"STORE_W(16, c.r29, a4); STORE_W(20, c.r29, a5); R_{c}(rdram, &c); port_recomp_leave(&c); "
                     "return (u32)c.r2; }")
    lines.append("static const struct { uint32_t vram; void *func; } port_recomp_pointers[] = {")
    lines += [f"    {{0x{v:08X}u, (void *)G_{c}}}," for v, c in pointers] or ["    {0, 0},"]
    lines.append("};")
    lines.append(LOOKUPS % {"tables": len(choice), "pointers": len(pointers)})
    bridges = out / "bridges.c"
    _write(bridges, "\n".join(lines) + "\n")
    units.append(bridges)
    counts["imports"] = imported
    log(f"recompiled C for {counts['recomp']} functions with no C yet ({counts['decomp']} have the game's C, "
        f"{counts['host']} are the host's, {counts['missing']} N64Recomp couldn't translate)")
    return {"units": units, "include": out, "counts": counts}


def _write(path: Path, text: str) -> None:
    if not path.exists() or path.read_text(encoding="utf-8") != text:
        path.write_text(text, encoding="utf-8")


BRIDGE_RUNTIME = r'''
/* the MIPS stack recompiled code runs on: one per host thread, handed down as it calls into the game's C and back */
__thread uint32_t port_recomp_sp;
void port_recomp_thread(int number) { port_recomp_sp = 0x%(stacks)Xu + (uint32_t)(number + 2) * 0x%(size)Xu - 0x100u; }
static void port_recomp_frame(recomp_context *c, int words) {
    memset(c, 0, sizeof *c);
    if (!port_recomp_sp) port_recomp_thread(-1);
    c->r29 = (gpr)(int32_t)((port_recomp_sp - 16u - 4u * (uint32_t)words) & ~7u);
    c->r31 = 0;
    c->f_odd = &c->f0.u32h;
    c->mips3_float_mode = 0;
    c->status_reg = 0x2000FF01u;
}
/* the game's C passes the arguments its callee doesn't declare (an unprototyped `alSynNew()` called with two) as
   MIPS's $a0-$a3, four words of memory at REGS (adapt.py), and takes a void function's value from the $v0 word
   after them: the registers start from those words, the declared arguments go over them, $v0 goes back */
static void port_recomp_args(uint8_t *rdram, recomp_context *c) {
    for (int i = 0; i < 4; i++) (&c->r4)[i] = (gpr)MEM_W(4 * i, 0x%(regs)Xu);
}
static void port_recomp_leave(recomp_context *c) { uint8_t *rdram = port_memory(); STORE_W(16, 0x%(regs)Xu, c->r2); }
/* recompiled code calling the game's C, by its address: its invoker takes o32's words and float registers */
static void port_recomp_decomp(uint32_t vram, uint8_t *rdram, recomp_context *ctx) {
    unsigned lo = 0, hi = port_call_count;
    while (lo < hi) { unsigned mid = (lo + hi) / 2; if (port_calls[mid].address < vram) lo = mid + 1; else hi = mid; }
    if (lo >= port_call_count || port_calls[lo].address != vram) { fprintf(stderr, "no C to call at %%08X\n", vram); exit(3); }
    u32 w[16], f[16], out[5] = {0};
    for (int i = 0; i < 4; i++) w[i] = (u32)(&ctx->r4)[i];
    for (int i = 4; i < 16; i++) w[i] = (u32)MEM_W(4 * i, ctx->r29);
    f[12] = ctx->f12.u32l; f[13] = ctx->f12.u32h; f[14] = ctx->f14.u32l; f[15] = ctx->f14.u32h;
    /* $a0-$a3 as MIPS leaves them in the registers, for a callee whose C passes on what it doesn't declare (a racing
       game's `void heap_free_stub(void)` hands its caller's block on to heapFreeBlock through them: adapt.py's
       register words, which the game's own callers fill and a call from recompiled code left stale) */
    for (int i = 0; i < 4; i++) STORE_W(4 * i, 0x%(regs)Xu, w[i]);
    uint32_t saved = port_recomp_sp;
    port_recomp_sp = (uint32_t)ctx->r29;
    port_calls[lo].invoke(port_game(), w, f, out);
    port_recomp_sp = saved;
    static int trace = -1;
    if (trace < 0) trace = getenv("PORT_RECOMP_TRACE") != NULL;
    if (trace) fprintf(stderr, "recompiled -> C %%08X(%%08X, %%08X, %%08X, %%08X, %%08X) = %%08X (kind %%u)\n", vram,
                       w[0], w[1], w[2], w[3], w[4], out[0], out[4]);
    switch (out[4]) {
    case 0: case 1: ctx->r2 = (gpr)(int32_t)out[0]; break;
    case 2: ctx->r2 = (gpr)(int32_t)out[0]; ctx->r3 = (gpr)(int32_t)out[1]; break;
    case 3: ctx->f0.u32l = out[2]; break;
    case 4: ctx->f0.u32l = out[2]; ctx->f0.u32h = out[3]; break;
    }
}
/* the host's interpreter (mips.c), for a function the recompiler never saw or couldn't translate: one the split doesn't list (a racing
   game's, reached through a pointer, starts where the function before it was thought to end), so its recompiled
   code's call stopped the game. The interpreter runs it from the ROM with the registers as recompiled code left
   them, and gives them back. */
void port_mips_run(uint32_t entry, uint64_t *regs, uint32_t *fregs);
int port_mips_has_code(uint32_t address);
static __thread uint32_t port_recomp_unlisted;
static void port_recomp_interpret(uint8_t *rdram, recomp_context *ctx) {
    (void)rdram;
    uint32_t entry = port_recomp_unlisted;
    uint64_t r[32]; uint32_t f[32];
    gpr *g = &ctx->r0; fpr *fp = &ctx->f0;
    for (int i = 0; i < 32; i++) r[i] = (uint64_t)g[i];
    for (int i = 0; i < 32; i += 2) { f[i] = fp[i].u32l; f[i + 1] = fp[i].u32h; } /* (odd: the even's high half) */
    r[31] = (uint64_t)(int64_t)(int32_t)0xFFFFFFF0u; /* mips.c's RETURN: back here when it returns */
    uint64_t ra = g[31];
    port_mips_run(entry, r, f);
    for (int i = 1; i < 32; i++) g[i] = (gpr)r[i];
    g[31] = ra;
    for (int i = 0; i < 32; i += 2) { fp[i].u32l = f[i]; fp[i].u32h = f[i + 1]; }
}
static void port_recomp_missing(uint32_t vram, const char *name, uint8_t *rdram, recomp_context *ctx) {
    /* one N64Recomp couldn't translate: the interpreter runs it from the ROM, as the browser build does (skipped,
       a racing game's race clock ran but nothing on the track moved) */
    if (vram && port_mips_has_code(vram)) { port_recomp_unlisted = vram; port_recomp_interpret(rdram, ctx); return; }
    static int said;
    if (said++ < 20) fprintf(stderr, "%%s (%%08X): no C for it, and the recompiler couldn't translate it: skipped\n", name, vram);
}
/* what N64Recomp's output needs from a runtime (recomp.h) */
static int32_t no_sections[64];
int32_t *section_addresses = no_sections;
void cop0_status_write(recomp_context *ctx, gpr value) { ctx->status_reg = (uint32_t)value; }
gpr cop0_status_read(recomp_context *ctx) { return (gpr)(int32_t)ctx->status_reg; }
void switch_error(const char *func, uint32_t vram, uint32_t jtbl) {
    fprintf(stderr, "recompiled %%s: a switch out of its table at %%08X (table %%08X)\n", func, vram, jtbl); exit(5);
}
void do_break(uint32_t vram) { fprintf(stderr, "recompiled code reached a break at %%08X\n", vram); }
void recomp_syscall_handler(uint8_t *rdram, recomp_context *ctx, int32_t vram) {
    (void)rdram; (void)ctx; fprintf(stderr, "recompiled code reached a syscall at %%08X\n", (uint32_t)vram);
}
/* `j .`, the idle thread's end: parked by the host for good, as the interpreter's (host.c); the boot code has no
   thread to park and goes on */
void port_idle_forever(void);
void pause_self(uint8_t *rdram) { (void)rdram; port_idle_forever(); fprintf(stderr, "recompiled code paused its thread\n"); }
'''

LOOKUPS = r'''
/* a NaN operand (NAN_CHECK, which N64Recomp asserts): said once for each function that meets one */
void port_recomp_nan(const char *where) {
    static const char *said[64]; static int n;
    for (int i = 0; i < n; i++) if (said[i] == where) return;
    if (n < 64) { said[n++] = where; fprintf(stderr, "recompiled %%s computes with a NaN (as the console would)\n", where); }
}
/* PORT_RECOMP_CALLS=ADDR,ADDR...: each entry to those recompiled functions, with $a0-$a3, $ra and $sp, for finding
   where a game's state goes wrong (a racing game's audio synthesizer, its filter never set). -1: not read yet. */
int port_recomp_watch = -1;
static uint32_t port_recomp_watched[32];
static int port_recomp_watched_n;
static uint32_t port_recomp_changed; /* PORT_RECOMP_CHANGED's address */
void port_recomp_entered(uint32_t vram, void *context) {
    if (port_recomp_watch < 0) {
        const char *list = getenv("PORT_RECOMP_CALLS");
        for (char *p = (char *)list; p && *p && port_recomp_watched_n < 32; ) {
            port_recomp_watched[port_recomp_watched_n++] = (uint32_t)strtoul(p, &p, 16);
            while (*p == ',') p++;
        }
        const char *changed = getenv("PORT_RECOMP_CHANGED"); /* a RAM word, checked at every entry */
        if (changed) port_recomp_changed = (uint32_t)strtoul(changed, NULL, 16) | 0x80000000u;
        port_recomp_watch = port_recomp_watched_n > 0 || port_recomp_changed;
        if (!port_recomp_watch) return;
    }
    recomp_context *ctx = (recomp_context *)context;
    if (port_recomp_changed) {
        uint8_t *m = port_memory();
        uint32_t a = port_recomp_changed, now = (uint32_t)m[a] << 24 | m[a + 1] << 16 | m[a + 2] << 8 | m[a + 3];
        static uint32_t was; static int seen;
        if (!seen || now != was) fprintf(stderr, "%%08X is %%08X entering %%08X (from %%08X)\n", a, now, vram, (uint32_t)ctx->r31);
        was = now; seen = 1;
    }
    for (int i = 0; i < port_recomp_watched_n; i++)
        if (port_recomp_watched[i] == vram)
            fprintf(stderr, "recompiled %%08X(%%08X, %%08X, %%08X, %%08X) from %%08X, sp %%08X\n", vram, (uint32_t)ctx->r4,
                    (uint32_t)ctx->r5, (uint32_t)ctx->r6, (uint32_t)ctx->r7, (uint32_t)ctx->r31, (uint32_t)ctx->r29);
}
void port_safe_point(void); /* the host's (host.c): a thread the retrace woke that outranks this one takes over */
void port_trace(void);
recomp_func_t *get_function(int32_t vram) {
    port_safe_point();
    unsigned lo = 0, hi = %(tables)d;
    while (lo < hi) { unsigned mid = (lo + hi) / 2; if (port_recomp_table[mid].vram < (uint32_t)vram) lo = mid + 1; else hi = mid; }
    if (lo < %(tables)du && port_recomp_table[lo].vram == (uint32_t)vram) return port_recomp_table[lo].func;
    if (port_mips_has_code((uint32_t)vram)) {
        static int said;
        if (said++ < 8) fprintf(stderr, "recompiled code called %%08X, which no listed function starts: interpreted\n", (uint32_t)vram);
        port_recomp_unlisted = (uint32_t)vram;
        return port_recomp_interpret;
    }
    fprintf(stderr, "recompiled code called %%08X, where no function is\n", (uint32_t)vram);
    port_trace(); /* the host's backtrace: which recompiled function made the call */
    exit(4);
}
/* the host's port_func, for a function pointer to what only recompiled C has: integer arguments, its $v0 */
void *port_recomp_pointer(uint32_t vram) {
    unsigned lo = 0, hi = %(pointers)d;
    while (lo < hi) { unsigned mid = (lo + hi) / 2; if (port_recomp_pointers[mid].vram < vram) lo = mid + 1; else hi = mid; }
    return lo < %(pointers)du && port_recomp_pointers[lo].vram == vram ? port_recomp_pointers[lo].func : 0;
}
'''
