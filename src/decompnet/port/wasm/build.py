"""Build a finished game as WebAssembly, translate it back to C and build it with the console's host (port/wasm).

Inputs, all from the game's save repository (the folder Save writes, under git) and the console's port kit:
- progress/src/*.c and progress/include/*.h: the game's matched C and its shared header, as the decomp has them;
- progress/decompdecomp.json: where every function and named datum lives (the layout and the symbols);
- port/wasm/exclude.txt: matched C the build leaves out, one file name a line (# comments): library code the host
  provides, or code the port doesn't run. Play and Export add the library code the project proves (`leave_out`:
  play.library), so a save that never wrote this list still leaves libultra to the host;
- port/wasm/src/*.c: C only the port has, for functions with no matched C yet (it needn't match, only behave);
- port/wasm/overrides/*.c: the port's version of a matched file, until the fix is in the matched C behind
  `#ifdef PORT` (Fix C for the PC);
- port/wasm/include/*.h: headers only the port's C includes;
- `library_c` (not from the save): library code set aside as SDK that the port builds from the user's own copy of
  ultralib (port/ultralib.units), one function a file, written to port/wasm/build/ultralib/ (git ignores it: that C
  is SGI's and Nintendo's, never part of a save) and compiled with ultralib's own headers and defines.

The build goes to port/wasm/build/ (generated, never committed: git ignores it). Its result is a program that runs
the game from the user's own ROM: `port/wasm/build/game ROM [seconds]`.

A rebuild redoes only what changed. Each file the build makes keeps a key beside it (`<file>.key`): the hash of
everything it was made from, the headers its C includes among them. Times aren't trusted: a Save
rewrites files whose text didn't change, a part's header changes without its C, and the tables every function's
placing reads (signatures, addresses) change on almost every Save, so a function is placed again only when its own
rows in them change.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from contextlib import nullcontext
from pathlib import Path

from ... import consoles, limits, toolchains
from ...network import NetworkError
from .. import ultralib
from . import library_data

HERE = Path(__file__).parent
# The game's C as wasm32 IR, unoptimised: LLVM's load combining would assume little-endian memory, which beir.py and
# bemem.py make big-endian after it.
IR_FLAGS = ["-target", "wasm32-freestanding", "-std=gnu17", "-DPORT_WASM=1", "-O0", "-g0",
            # WebAssembly has no return address to read: the heap's `#ifdef PORT` branch records 0 as its caller
            "-D__builtin_return_address(n)=((void *)0)",
            "-fno-sanitize=all", "-ffreestanding", "-fno-strict-aliasing", "-fwrapv", "-Wno-everything",
            # zig cc defines _DEBUG at -O0: the game's `#ifdef _DEBUG` code (libultra's debug heap, its thread
            # checks) was compiled into the port, against headers without its fields
            "-U_DEBUG"]
# The console's C, not the native port's: memory here is the console's own 32-bit layout, where the matched C is
# right, and a fix behind `#ifdef PORT` is for a 64-bit PC (one wrote a display list's address as 0 there: "no
# meaning on PC", and the racing game's menu text sampled the wrong glyphs). PORT is defined only for a file that
# needs it here: one using <stdarg.h> (its console branch walks the MIPS stack for its arguments with
# `__builtin_next_arg`), inline assembly, or one that doesn't compile without it. The other MIPS builtins
# (`__builtin_mips_read_status`, `__builtin_mips_debug_break`...) stay calls the host answers (port_wasm/host.c).
# An empty debug-print stub in K&R varargs (`void f(va_alist) va_dcl {}`, as GCC 2.7.2 takes it): clang refuses the
# `...` its va_dcl declares, so one that never reads its arguments is compiled as `void f()` (`kr_varargs`)
KR_VARARGS = re.compile(r"\(\s*va_alist\s*\)\s*va_dcl\b")
NEEDS_PORT = re.compile(r"\bva_start\b|\b__builtin_next_arg\b|\b__asm__\b|\basm\s*\(")
# The console's memory (4 GB, so every console address is in it), its data placed by the C's own addresses, a
# stack per thread below it (the host's), and every function exported: the host finds them by address.
LINK_FLAGS = ["--no-entry", "--allow-undefined", "--export-all", "--export-table", "--growable-table",
              "--emit-relocs", "--global-base=1048576", "-z", "stack-size=65536", "--stack-first",
              "--initial-memory=4294967296", "--max-memory=4294967296"]
TIMEOUT = 900
# What the progress bar counts, in order (play.py adds starting the game).
STEPS = ("Gathering the game's C from its save", "Compiling to WebAssembly IR", "Reading each function's signature",
         "Placing every symbol at its console address", "Linking", "Translating to C (wasm2c)", "Building the host")


def _write(path: Path, text: str) -> None:
    """Write only what changed, so a rebuild recompiles only the C that changed."""
    if not path.exists() or path.read_text(encoding="utf-8") != text:
        path.write_text(text, encoding="utf-8")


def _names(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    return {line.split("#", 1)[0].strip() for line in path.read_text(encoding="utf-8").splitlines()} - {""}


def _digest(*parts) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(part if isinstance(part, bytes) else str(part).encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()


_INCLUDE = re.compile(r'^\s*#\s*include\s*[<"]([^>"]+)[>"]', re.M)


class _Files:
    """The hashes of the files a build step reads and the headers each includes, each read once per build (2,000
    files share a header)."""

    def __init__(self):
        self.seen: dict[Path, str] = {}
        self.included: dict[Path, list[str]] = {}

    def __call__(self, path: Path) -> str:
        path = Path(path)
        if path not in self.seen:
            try:
                data = path.read_bytes()
            except OSError:
                data = None
            self.included[path] = _INCLUDE.findall(data.decode("utf-8", "replace")) if data else []
            # Parallel callers treat a published hash as proof that the include list is ready.
            self.seen[path] = _digest(data) if data is not None else "missing"
        return self.seen[path]

    def key(self, source: Path, dirs, *inputs) -> str:
        """The key of what's compiled from `source`: the inputs given, the source and every header it includes,
        found in its own folder or `dirs` as the compiler finds them. One the folders don't have (the compiler's
        own) comes with the compiler, which the inputs name. An include inside an #if counts too: at worst a file
        is compiled again for nothing. (Not the compiler's dependency list: zig's `-MD` dropped the IR it wrote.)"""
        seen, todo, parts = set(), [Path(source)], []
        while todo:
            path = todo.pop()
            if path in seen:
                continue
            seen.add(path)
            parts.append(f"{path}={self(path)}")
            for name in self.included[path]:
                for folder in (path.parent, *dirs):
                    if (found := Path(folder) / name).is_file():
                        todo.append(found)
                        break
        return _digest(*inputs, *sorted(parts))


def _fresh(made: Path, key: str) -> bool:
    stamp = made.with_name(made.name + ".key")
    try:
        return made.exists() and stamp.read_text(encoding="utf-8") == key
    except OSError:
        return False


def _stamp(made: Path, key: str) -> None:
    made.with_name(made.name + ".key").write_text(key, encoding="utf-8")


def _unstamp(made: Path) -> None:
    """A failed or redone file loses its key, so it's made again next time whatever its inputs say."""
    made.with_name(made.name + ".key").unlink(missing_ok=True)


def answered_names(library_names, hosted, sdk_group) -> set[str]:
    """The library names (the values of `library_names`) the host or the port's glue really answers: the host defines
    it, or the console says the port replaces it or supplies it ("replaced", "glue"). The others, as the synthesizer's
    own functions are, stay unprovided so ultralib's C for them is built (`_library`)."""
    return {n for n in (library_names or {}).values()
            if n in hosted or (sdk_group is not None and sdk_group(n) in ("replaced", "glue"))}


def reachable_library_names(library_names, library, provided) -> dict[str, str]:
    """`library_names` less the entries whose library name nothing defines: the host defines it or the game's C has
    it (`provided`), or ultralib's C was built for it (`library`: its file names). The port's "glue" group is every os*
    name, though the host defines only some. A call renamed to a name nothing defines reached a stub ("not
    implemented, skipped"); kept under the function's own name it runs the function's recompiled or interpreted
    code."""
    return {name: lib for name, lib in (library_names or {}).items() if lib in provided or f"{lib}.c" in library}


LOOP_BACK = re.compile(r"\bgoto (var_L\d+);")
LOOP_POLL = ("void port_safe_point(void);\n"
             "static inline void port_loop_poll(void) { static __thread unsigned n; if (!(++n & 0x3FF)) port_safe_point(); }\n")


def loop_safe_points(folder: Path) -> None:
    """A safe point (port_loop_poll) before each loop's next round in wasm2c's C (`goto var_L<n>`: wasm2c names a
    loop's label var_L and a block's var_B, so every goto to a var_L is a backward branch). The host switches threads
    only at safe points, and a game's idle thread spins on a flag in a loop that calls nothing: it lowers its own
    priority and waits, the scheduler thread the retrace woke outranks it but never got the CPU, and a game with no
    graphics task drew nothing. Every 1024th round a thread the retrace woke that outranks this one takes over."""
    for path in sorted(Path(folder).glob("game_*.c")):
        text = path.read_text(encoding="utf-8")
        patched = LOOP_BACK.sub(lambda m: f"{{ port_loop_poll(); goto {m.group(1)}; }}", text)
        if patched != text:
            path.write_text(LOOP_POLL + patched, encoding="utf-8")


def _sync(new: Path, old: Path) -> None:
    """Make folder `old` hold what `new` does, rewriting only the files that differ: an unchanged file keeps its key
    and isn't compiled again."""
    old.mkdir(exist_ok=True)
    names = {p.name for p in new.iterdir()}
    for p in old.iterdir():
        if p.name not in names:
            p.unlink()
    for p in new.iterdir():
        target = old / p.name
        if not target.exists() or target.read_bytes() != p.read_bytes():
            shutil.copyfile(p, target)
    shutil.rmtree(new)


_REF = re.compile(r"@([\w.$]+)(?![\w.$])")


def table_rows(text: str, sigs: dict, fixed: dict, functions: set, renamed: dict) -> list[str]:
    """The rows of the build's tables that placing one function's IR (beir.py) reads: those of the names it uses
    (and the names a renamed call goes to). The tables change with almost every Save; this IR's rows rarely do."""
    names = set(_REF.findall(text))
    names |= {renamed[n] for n in names if n in renamed}
    rows = []
    for n in sorted(names):
        rows.append(f"{n}|{sigs.get(n, '')}|{fixed.get(n, '')}|{n in functions}|{renamed.get(n, '')}")
    return rows


def _rows(path: Path, sep: str | None = None) -> dict[str, str]:
    out = {}
    for row in path.read_text(encoding="utf-8").splitlines():
        if row.strip():
            name, _, rest = row.partition(sep or " ")
            out[name.strip()] = rest
    return out


# A bitfield's declared type: its storage unit's size in bytes.
_FIELD_TYPES = {"char": 1, "u8": 1, "s8": 1, "short": 2, "u16": 2, "s16": 2, "int": 4, "long": 4, "u32": 4, "s32": 4,
                "unsigned": 4, "signed": 4, "long long": 8, "u64": 8, "s64": 8}
_BITFIELD = re.compile(r"^(\s*)((?:(?:unsigned|signed|const|volatile)\s+)*(\w+(?:\s+long)?)?)\s*(\w*)\s*:\s*(\d+)\s*;(.*)$")


def _unit_size(type_text: str) -> int | None:
    words = [w for w in type_text.split() if w not in ("const", "volatile")]
    if not words:
        return None
    if words[-1] in ("unsigned", "signed"):
        return 4
    return _FIELD_TYPES.get(" ".join(w for w in words if w not in ("unsigned", "signed")))


def _plain_bytes(unit) -> list[str] | None:
    """A partly used unit's fields as plain members, when each is a whole byte or halfword at its own alignment:
    the members declared after them then fill the unit's spare bytes, as IDO lays them out. `int cmd:8;` then three
    `unsigned char`s is libultra's Gsetcolor: closed with a zero-width field it took a word of its own, the struct
    grew to 12 bytes, Gfx to 16, and a packed shooter's text rectangles read padding as their texture coordinates."""
    out, at = [], 0
    for m in unit:
        width = int(m.group(5))
        if width not in (8, 16) or at % width:
            return None
        signed = not re.search(r"\bunsigned\b|\bu(8|16|32|64)\b", m.group(2))
        kind = "char" if width == 8 else "short"
        out.append(f"{m.group(1)}{'signed' if signed else 'unsigned'} {kind} {m.group(4) or f'_pad{at}'};{m.group(6)}")
        at += width
    return out


def big_endian_bitfields(text: str) -> str:
    """Bitfields laid out as the console's compiler does: a big-endian MIPS compiler gives a unit's first field its
    top bits, wasm32 its bottom ones. Each unit's fields are declared in reverse (a unit left part-empty gets its
    unused bits first) and closed with a zero-width field, so every field lands on the console's bits and is read
    with its unit's width from the big-endian memory. A racing game's track setup looked for the segment whose
    `b0 : 1` was set, read the top bit instead, ran off the end of its table and corrupted the next one."""
    lines = text.split("\n")
    result, i = [], 0
    while i < len(lines):
        run = []
        while (i + len(run) < len(lines) and ":" in lines[i + len(run)]
               and (m := _BITFIELD.match(lines[i + len(run)])) and int(m.group(5))):
            run.append(m)
        if not run:
            result.append(lines[i])
            i += 1
            continue
        units, unit, used, size = [], [], 0, 0
        for m in run:
            width, field_size = int(m.group(5)), _unit_size(m.group(2))
            if field_size is None:
                units = None
                break
            if unit and used + width > size * 8:
                units.append((unit, used, size))
                unit, used = [], 0
            if not unit:
                size = field_size
            if width > size * 8:
                units = None
                break
            unit.append(m)
            used += width
        if units is None:  # a type it can't size: left as it was
            result.extend(m.group(0) for m in run)
        else:
            units.append((unit, used, size))
            nxt = lines[i + len(run)] if i + len(run) < len(lines) else ""
            for unit, used, size in units:
                if (unit is units[-1][0] and used < size * 8 and not re.match(r"\s*}", nxt)
                        and (plain := _plain_bytes(unit))):
                    result.extend(plain)  # the members after it share its unit, as IDO packs them
                    continue
                if not any(m.group(4) for m in unit):  # only padding: nothing reads it, and it may start mid-unit
                    result.extend(m.group(0) for m in unit)
                    continue
                indent, type_text = unit[0].group(1), unit[0].group(2).strip()
                if used < size * 8:
                    result.append(f"{indent}{type_text} : {size * 8 - used};")
                result.extend(f"{m.group(1)}{m.group(2).strip()} {m.group(4)} : {m.group(5)};{m.group(6)}"
                              for m in reversed(unit))
                result.append(f"{indent}{type_text} : 0;")
        i += len(run)
    return "\n".join(result)


# m2c's declaration of a function it saw called but not typed: `M2C_UNK f(arg0);`, which IDO takes and clang
# doesn't ("a parameter list without types is only allowed in a function definition")
UNTYPED_DECLARATION = re.compile(r"^([ \t]*(?!return\b|else\b|case\b|goto\b)[A-Za-z_][\w \t*]*?[\s*]\w+)"
                                 r"\(\s*arg\d+(?:\s*,\s*arg\d+)*\s*\)(\s*;)", re.M)
# `0x2be-0x174`: one preprocessing number to clang (an `e` then a sign), a subtraction to IDO
HEX_EXPONENT_SIGN = re.compile(r"\b(0[xX][0-9a-fA-F]*[eE])([+-])(?=[0-9A-Za-z_(])")


# An endless loop that does nothing (an idle thread's `for (;;) {}`): on the console only an interrupt switches away
# from it, and the host switches threads only at a call, so a packed shooter's idle thread (priority 0) held the CPU
# and its scheduler, ready at priority 14, never ran. It parks instead, as the interpreter parks `j .` (mips.c).
EMPTY_FOREVER = re.compile(r"\b(?:for\s*\(\s*;\s*;\s*\)|while\s*\(\s*1\s*\))\s*(?:\{\s*\}|;)"
                           r"|^(?P<label>[ \t]*(?P<name>[A-Za-z_]\w*)[ \t]*:\s*)goto\s+(?P=name)\s*;", re.M)
IDLE = "void port_idle_forever(void);\n"
LOOP_TOKEN = re.compile(r"""//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\w+|[^\s]""", re.S)


def standalone_loops(text):
    """A do-while trailer belongs to its body, even when it reads `while (1);`."""
    depth = 0
    pending = {}
    previous = ""
    for match in LOOP_TOKEN.finditer(text):
        token = match[0]
        if token.startswith(("//", "/*")):
            continue
        if token in ("{", "("):
            depth += 1
        elif token in ("}", ")"):
            depth -= 1
        elif token == "do":
            pending[depth] = pending.get(depth, 0) + 1
        elif token == "while" and pending.get(depth) and previous in (";", "}"):
            pending[depth] -= 1
        elif token in ("while", "for"):
            yield match.start()
        previous = token


# gcc's `__builtin_next_arg(last)` (the address of a function's first variadic argument) is no clang builtin: the call
# linked to a logging stub that returned 0, so a variadic allocator read its extra argument from address 0. The same
# address is what va_start leaves in the va_list (a pointer in WebAssembly's calling convention), whose buffer the
# callers fill with the words o32 passed (adapt.py's varargs).
NEXT_ARG = ("#define __builtin_next_arg(last) ({ __builtin_va_list __next; __builtin_va_start(__next, last); "
            "(void *)__next; })\n")


def ido_c(text: str) -> str:
    """The game's C as clang reads what IDO took: m2c's untyped declarations unprototyped, a hex constant followed
    by a sign spaced apart, and an endless empty loop as the thread parking for good (EMPTY_FOREVER)."""
    text = HEX_EXPONENT_SIGN.sub(r"\1 \2 ", UNTYPED_DECLARATION.sub(r"\1()\2", text))
    if "__builtin_next_arg" in text:
        text = NEXT_ARG + text
    if not EMPTY_FOREVER.search(text):
        return text
    starts = set(standalone_loops(text))
    rewritten = EMPTY_FOREVER.sub(
        lambda loop: (loop["label"] or "") + "for (;;) port_idle_forever();"
        if loop["label"] or loop.start() in starts else loop[0], text)
    return IDLE + rewritten if rewritten != text else text


_PROTOTYPE = r"^([ \t]*)((?!static\b|extern\b|typedef\b|return\b)[A-Za-z_][\w \t*]*?[\s*]{name}\s*\([^;{{}}]*\)\s*;)"


def static_prototypes(text: str) -> str:
    """A part's header that declares a function, then defines it `static` (an N64 game's own source: IDO takes it,
    clang stops at "static declaration follows non-static declaration"): its declarations made static too."""
    for name in set(re.findall(r"^[ \t]*static\b[^;{}=]*?\b(\w+)\s*\([^;{}]*\)\s*\{", text, re.M)):
        text = re.sub(_PROTOTYPE.format(name=re.escape(name)), r"\1static \2", text, flags=re.M)
    return text


def kr_varargs(text: str) -> str:
    """A K&R varargs function that never reads its arguments, as an unprototyped one: callers pass what they pass."""
    if KR_VARARGS.search(text) and not re.search(r"\bva_(?:start|arg|end)\s*\(", text):
        return KR_VARARGS.sub("()", text)
    return text


def own_functions(chosen: dict[str, Path], texts: dict[str, str]) -> dict[str, str]:
    """Each file's text without the functions another file of the build is for: a save's file of matched C can define
    more than its own function (a racing game's three sound-player setters were each matched with C defining all
    three), and the port compiles whole files, so the copies collided at link. The other file's is that function's
    own matched C. Static helpers and the file's own function stay."""
    from ..csource import defined, only
    files = {name.casefold() for name in chosen}
    out = {}
    for name, text in texts.items():
        extra = {f for f in defined(text) if f != name[:-2] and f"{f}.c".casefold() in files}
        out[name] = only(text, extra) if extra else text
    return out


def _defines_left_out(path: Path, leave_out) -> bool:
    """Whether a save's file also defines a function the host provides: that file is the library's source, and its
    own function a static helper of it. A packed shooter's _Printf was matched in its helper's file too, so leaving
    _Printf.c out still compiled _Printf, with IDO's va_arg that clang can't (`__builtin_classof`)."""
    from ..csource import defined
    gone = {name.removesuffix(".c") for name in leave_out}
    return bool(gone) and bool(gone & (set(defined(path.read_text(encoding="utf-8"))) - {path.stem}))


EMPTY_UNIT = "port_no_game_c.c"


def _empty_unit(out: Path) -> Path:
    empty = out / "src" / EMPTY_UNIT
    _write(empty, "/* no game C: recompiled code or the interpreter runs the original functions */\n"
                  "void osCreateThread(int, int, int, int, int, int);\n"
                  "void port_no_game_c(void (*use)(char *)) {\n"
                  "    char buffer[16];\n    use(buffer);\n"
                  "    if (!use) osCreateThread(0, 0, 0, 0, 0, 0);\n}\n")
    return empty


def _save_manifest(repo: Path) -> dict:
    progress = repo / "progress"
    path = progress / "decompdecomp.json"
    if not (progress / "src").is_dir() or not path.is_file():
        raise NetworkError(f"{repo} has no progress/src and progress/decompdecomp.json: choose the game's save "
                           "repository (the folder Save writes)")
    return json.loads(path.read_text(encoding="utf-8"))


def original_code(host: Path, manifest: dict, fixed: list[str]) -> set[str]:
    """Functions that need the original instructions, keyed by ROM and address so renames still leave them out."""
    sha1 = (manifest.get("game") or {}).get("sha1") or ""
    if not re.fullmatch(r"[a-fA-F0-9]{40}", sha1):
        return set()
    addresses = {int(row, 16) for row in _names(host / "games" / f"{sha1.lower()}.original")}
    if not addresses:
        return set()
    return {name + ".c" for name, address, kind in (row.split() for row in fixed)
            if kind == "func" and int(address, 16) in addresses}


def gather(repo: Path, out: Path, leave_out=(), function_headers=None, *, ensure_host_import=False, manifest=None) -> dict[str, Path]:
    """The C the build compiles, in out/src (one file per function, the shared header included) and out/include.
    `leave_out`: more matched files to leave out than exclude.txt names (play.library). `function_headers`: the
    project's own {function: part} (play.function_headers), over the save's, which an older save lacks. Returns
    {file name: where it came from}."""
    progress, port = repo / "progress", repo / "port" / "wasm"
    if manifest is None:
        manifest = _save_manifest(repo)
    exclude = _names(port / "exclude.txt") | set(leave_out)
    chosen = {p.name: p for p in sorted((progress / "src").glob("*.c")) if p.name not in exclude}
    chosen = {name: path for name, path in chosen.items() if not _defines_left_out(path, leave_out)}
    for folder in (port / "src", port / "overrides"):
        chosen.update({p.name: p for p in sorted(folder.glob("*.c"))})
    (out / "src").mkdir(parents=True, exist_ok=True)
    (out / "include").mkdir(parents=True, exist_ok=True)
    # The decomp compiles every function with the shared header, or with its part's own when the save names one
    # (a function from the game's own source files: common_<part>.h, the manifest's function_headers).
    units = (manifest.get("function_headers") or {}) | (function_headers or {})
    headers = {h.name for folder in (progress / "include", port / "include") for h in folder.glob("*.h")}
    texts = own_functions(chosen, {name: kr_varargs(source.read_text(encoding="utf-8")) for name, source in chosen.items()})
    for name in chosen:
        text = texts[name]
        unit = units.get(name[:-2])
        # a part the save has no header for compiles with the shared one, as the app compiles it (_effective_unit):
        # older saves named such a part anyway, and the build stopped at the missing header
        header = f"common_{unit}.h" if unit and f"common_{unit}.h" in headers else "common.h"
        if not re.search(r'^#include "common(_\w+)?\.h"', text, re.M):
            text = f'#include "{header}"\n' + text
        _write(out / "src" / name, big_endian_bitfields(ido_c(text)))
    # By name in any case: on a case-insensitive disk (macOS, Windows) a file an old save wrote as SelectStateReset.c
    # is the one just written as selectStateReset.c, and deleting it as stale deleted that function's C
    if not chosen or ensure_host_import:  # standalone leaves may have no host imports of their own
        # Keep a stack and host import even when every game function runs from original code.
        chosen[EMPTY_UNIT] = _empty_unit(out)
    kept = {name.casefold() for name in chosen}
    for stale in (out / "src").glob("*.c"):
        if stale.name.casefold() not in kept:
            stale.unlink()
    for folder in (progress / "include", port / "include"):
        for header in folder.glob("*.h"):
            _write(out / "include" / header.name,
                   big_endian_bitfields(static_prototypes(ido_c(header.read_text(encoding="utf-8")))))
    return chosen


def _library(out: Path, units: dict, sources: dict, log, provided=None) -> dict[str, list[str]]:
    """Write the library units (`library_c`) to out/ultralib; {file name: flags}. The
    game's own C for a function wins: a unit whose function the save has C for is left out (it was matched after it
    was set aside). `provided(name)`: something else defines that function (the game's functions, the host, the
    port's recompiled and interpreted code); ultralib's other functions the units call are built too
    (ultralib.with_callees), so the link has them in place of stubs."""
    folder = out / "ultralib"
    if provided is not None and (ultralib_folder := ultralib.root()) is not None:
        units = ultralib.game_callees(units, ultralib_folder, provided, (
            path.read_text(encoding="utf-8", errors="replace") for path in sources.values() if Path(path).is_file()))
        units = ultralib.with_callees(units, ultralib_folder, provided)
    taken = {name.casefold() for name in sources}
    library = {name: list(flags) for name, (_, flags) in units.items() if name.casefold() not in taken}
    if library:
        folder.mkdir(parents=True, exist_ok=True)
        log(f"{len(library)} library functions from your copy of ultralib")
    for name in library:
        _write(folder / name, units[name][0])
    for stale in folder.glob("*.c") if folder.is_dir() else ():
        if stale.name not in library:
            stale.unlink()
    return library


def game_host(host: Path, repo: Path) -> list[Path]:
    """The game's own host file, if the console's host has one for it (games/<its ROM's SHA-1, 12 digits>.c): what
    only that game needs, such as functions it names by its own addresses. The N64 host was written on one racing game
    and held its bzero at 0x80091920 for every game, where another game has a function of its own."""
    try:
        sha1 = json.loads((repo / "progress" / "decompdecomp.json").read_text(encoding="utf-8"))["game"]["sha1"] or ""
    except (OSError, ValueError, KeyError, TypeError):
        return []
    own = host / "games" / f"{sha1[:12].lower()}.c"
    return [own] if len(sha1) >= 12 and own.is_file() else []


def cross_host(repo: Path, name: str, label: str, units: list, target: list, flags: list, link: list, recomp,
               log) -> Path:
    """The host compiled again for another computer with the pinned Zig, from the game's C the native build just
    made: the handhelds' exports (miyoo.py, steamdeck.py), into port/wasm/build/<name>/game. `units`: the host's
    files for that computer; the recompiled C goes in only when this build has it (out/recomp stays behind from an
    earlier one), with its warnings off. `target` (Zig's -target and -mcpu) builds and links for that computer,
    `flags` compile every unit, `link` links them."""
    repo = Path(repo).expanduser().resolve()
    zig = toolchains.binary("zig")
    wabt = toolchains.ensure("wabt", log=log)
    out = repo / "port" / "wasm" / "build"
    dest = out / name
    (dest / "obj").mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "ZIG_GLOBAL_CACHE_DIR": str(out / "zig-cache"), "ZIG_LOCAL_CACHE_DIR": str(out / "zig-cache")}
    rt = wabt / "share" / "wabt" / "wasm2c"
    recompiled = []
    if recomp and Path(recomp["folder"], "recomp.json").is_file():
        recompiled = [*sorted((out / "recomp").glob("rec_*.c")), out / "recomp" / "bridges.c"]
    units = [*units, *recompiled]
    dirs = [wabt / "include", rt, out / "c"]
    quiet = ["-w", "-I", out / "recomp"]  # thousands of generated functions: warnings off
    includes = [a for d in dirs for a in ("-I", d)]
    files = _Files()
    flags = [*target, *flags]

    def compile_unit(unit: Path) -> str | None | bool:
        obj = dest / "obj" / (unit.stem + ".o")
        extra = quiet if unit in recompiled else []
        key = files.key(unit, [*dirs, out / "recomp"], zig, *flags, *extra)
        if _fresh(obj, key):
            return False
        _unstamp(obj)
        done = _run([zig, "cc", *flags, *extra, *includes, "-c", unit, "-o", obj], log, env=env)
        if done.returncode:
            return (done.stderr.strip().splitlines() or ["failed"])[0]
        _stamp(obj, key)
        return None
    if failed := _parallel(units, compile_unit, log, f"files for {label}", None):
        raise NetworkError(f"building for {label} failed: " + "; ".join(failed[:3]))
    program = dest / "game"
    done = _run([zig, "cc", *target, "-o", program, *(dest / "obj" / (u.stem + ".o") for u in units), *link], log,
                env=env)
    if done.returncode:
        raise NetworkError(f"linking for {label} failed: {done.stderr.strip()[-600:]}")
    log(f"built {program} for {label} ({program.stat().st_size / 1e6:.1f} MB)")
    return program


def linked(objects: Path, sources) -> list[Path]:
    """The objects of the files the build compiled, by name in any case: on a case-insensitive disk the object an old
    save's SelectStateReset.c made keeps that name when selectStateReset.o is written over it, and leaving it out
    linked the game without that function (a racing game's menu then never finished opening)."""
    compiled = {name.casefold() for name in sources}
    return sorted(p for p in objects.glob("*.o") if (p.stem + ".c").casefold() in compiled)


def addresses(manifest: dict) -> tuple[list[str], set[str]]:
    """fixed.txt's rows (name, address, kind) and the functions' names, from the save's manifest."""
    rows, functions = {}, set()
    for f in manifest.get("layout") or []:
        rows[f["name"]] = (f["vram"], "func")
        functions.add(f["name"])
    for s in manifest.get("symbols") or []:
        func = s.get("kind") == "func"
        rows.setdefault(s["name"], (s["address"], "func" if func else s.get("kind", "data")))
        if func:
            functions.add(s["name"])
    return [f"{n} 0x{a:08X} {k}" for n, (a, k) in sorted(rows.items(), key=lambda r: (r[1][0], r[0]))], functions


def host_compiler(zig: Path) -> list:
    """The C compiler that builds the host, the one native program here. The pinned Zig on Linux and Windows. On
    macOS the system's own clang (Xcode's Command Line Tools), like wine an exception to "never from PATH": Zig
    0.14.1 can't link against a current macOS's system library (macOS 27 left `_strlen` and the rest undefined), and
    the host has to. The game's own C is compiled by the pinned Zig everywhere."""
    if sys.platform == "darwin":
        if shutil.which("xcrun") is None:
            raise NetworkError("building the host needs Xcode's Command Line Tools on macOS: install them with "
                               "`xcode-select --install`, then build again")
        return ["xcrun", "clang", "-mmacosx-version-min=11.0"]  # an exported app runs on Macs older than this one
    return [zig, "cc"]


# A Mac app that runs on both kinds of Mac: Apple Silicon and Intel. Only the export asks for it (export.py), since
# Play runs the game on this Mac and a second architecture would double its build for nothing.
UNIVERSAL = ["-arch", "arm64", "-arch", "x86_64"]


def host_import_flags(game_h: str) -> list:
    """`-DPORT_WASM_NO_IMPORTS=1` for a module that imports nothing from the host. A game with so little C that no
    function calls libultra gets `wasm2c_game_instantiate(w2c_game*)` with no import-state parameter, and the
    host's two-argument call stopped its build ("too many arguments to function call")."""
    if re.search(r"wasm2c_game_instantiate\(\s*w2c_game\s*\*\s*\)\s*;", game_h):
        return ["-DPORT_WASM_NO_IMPORTS=1"]
    return []


def host_arch_flags(universal: bool, platform: str = sys.platform) -> list:
    """The flags that make the host a universal program: only on macOS, whose clang builds both architectures into
    one file; elsewhere the program is for this computer, as before."""
    return list(UNIVERSAL) if universal and platform == "darwin" else []


def _run(argv: list, log, cwd: Path | None = None, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(limits.capped([str(a) for a in argv]), cwd=cwd, env=env, capture_output=True, text=True,
                          timeout=TIMEOUT)


def compile_ir(zig, src: Path, ll: Path, include: Path, env: dict, log, port=()):
    """Compile generated C, retrying a proven redundant scalar extern with the shared header's qualifiers.
    The original file is restored even on failure or cancellation; the saved and judged sources are never edited.
    """
    from .declarations import qualified_externs

    argv = [zig, "cc", *IR_FLAGS, *port, "-I", include, "-S", "-emit-llvm", src, "-o", ll]
    done = _run(argv, log, env=env)
    if not done.returncode:
        return done
    source = src.read_text(encoding="utf-8")
    prepared = qualified_externs(source, done.stderr, src)
    if prepared == source:
        return done
    try:
        src.write_text(prepared, encoding="utf-8")
        done = _run(argv, log, env=env)
    finally:
        src.write_text(source, encoding="utf-8")
    if not done.returncode:
        log(f"{src.name}: using the shared header's qualifiers for redundant scalar extern declarations")
    return done


def _parallel(jobs, work, log, what: str, step=None) -> list[str]:
    """Run work(job) for every job, a core each; the jobs that failed, with their first error line. work returns an
    error line, or None when it did the job, or False when the job was already done (its key matched). `step`
    counts the jobs done for the progress bar, and the log says how many were already done."""
    failed, done, cached = [], 0, 0
    jobs = list(jobs)
    if step:
        step(0, len(jobs))
    with concurrent.futures.ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as pool:
        futures = {pool.submit(work, job): job for job in jobs}
        try:
            for future in concurrent.futures.as_completed(futures):
                error = future.result()
                if error:
                    failed.append(f"{futures[future]}: {error}")
                elif error is False:
                    cached += 1
                done += 1
                if step:
                    step(done, len(jobs))
        except BaseException:  # cancelled (Stop) or failed: the queued jobs don't start
            for future in futures:
                future.cancel()
            raise
    if jobs:
        log(f"{len(jobs) - cached} {what} redone, {cached} unchanged")
    if failed:
        log(f"{len(failed)} {what} failed: " + "; ".join(failed[:5]))
    return failed


def fallback_failed_sources(failed: list[str], sources: dict[str, Path], repo: Path, out: Path,
                            fixed: list[str]) -> set[str]:
    """Saved game C with a known console address may run as original code if clang rejects it.

    Port overrides and library sources must compile: they may replace hardware behavior. A failed file may also
    contain other definitions; do not discard an unaddressed helper still needed by a successful compilation.
    """
    from ..csource import defined

    addresses = {n: int(a, 16) for n, a, kind in (row.split() for row in fixed) if kind == "func"}
    root = (repo / "progress" / "src").resolve()
    names = set()
    extra = set()
    for failure in failed:
        name, sep, _ = failure.partition(": ")
        path = sources.get(name)
        addr = addresses.get(name.removesuffix(".c"))
        if (not sep or path is None or Path(path).resolve().parent != root
                or addr is None or not 0x80000000 <= addr < 0x80800000):
            return set()
        code = (out / "src" / name).read_text(encoding="utf-8")
        own = defined(code)
        # C++ linkage declarations can reach this C-only compiler; the saved function still has original code.
        if name[:-2] not in own and not re.search(r'extern\s*"C"\s+[^;{}]*\b' + re.escape(name[:-2])
                                                   + r'\s*\([^;{}]*\)\s*\{', code):
            return set()
        extra.update(own - addresses.keys())
        names.add(name)
    if extra:
        defined_elsewhere, declared = set(), set()
        pattern = re.compile(r"^(define|declare) [^@\n]*@([\w.$]+)\(", re.M)
        for name in sources.keys() - names:
            ll = out / "ll" / (name[:-2] + ".ll")
            if not ll.is_file() or not ll.with_name(ll.name + ".key").is_file():
                continue
            for kind, symbol in pattern.findall(ll.read_text(encoding="utf-8", errors="replace")):
                if symbol in extra:
                    (defined_elsewhere if kind == "define" else declared).add(symbol)
        if declared - defined_elsewhere:
            return set()
    return names


class Progress:
    """The build's steps for the job's progress bar (the log's `progress`, where it has one): {phase, step, steps,
    done, total}; done and total count a step's files."""

    def __init__(self, log, steps=STEPS):
        self.report = getattr(log, "progress", None)
        self.steps, self.at = tuple(steps), 0

    def start(self, phase: str) -> None:
        self.at = self.steps.index(phase) + 1
        self.phase = phase
        self.files(None, None)

    def files(self, done, total) -> None:
        if self.report:
            value = {"phase": self.phase, "step": self.at, "steps": len(self.steps)}
            if total:
                value.update(done=done, total=total)
            self.report(value)


def recompiler_names(folder: Path | None) -> dict[str, int]:
    """{function name: address} from the recompiler's symbol table (syms.toml). It was written when the recompiler
    last ran, so it keeps the names the functions had then: C that still calls a function by a name polishing has
    since renamed (a port-only source, a recompiled body) names it there, with its address."""
    import tomllib
    try:
        table = tomllib.loads(Path(folder, "syms.toml").read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return {}
    found = {}
    for section in table.get("section", ()):
        for row in section.get("functions", ()):
            if isinstance(row, dict) and isinstance(row.get("name"), str) and isinstance(row.get("vram"), int):
                found[row["name"]] = row["vram"]
    return found


def renames(lls: list[Path], fixed: list[str], library_names=None, history: dict[str, int] | None = None) -> dict[str, str]:
    """{name a call uses: the defined function at its address}: C still calling a function by an older name (its
    automatic one, before polishing renamed it) links in the decomp through the symbol table; here it must name the
    function wasm-ld has. Only a name that resolves to exactly one defined function is renamed. `library_names`:
    {a function's name: its library name} for library code no file has C for (play.library_names): a call to it is
    to the library's name, which the host answers (a racing game's bzero was `func_8003DDC0`, stubbed to nothing).
    `history`: {an older name: its address} (recompiler_names): a call by a name the project has since changed, which
    no automatic name or current name says, goes to the function now at that address, so a renamed function's
    callers aren't left calling a stub."""
    defined, declared = set(), set()
    for ll in lls:
        for m in re.finditer(r"^(define|declare) [^@\n]*@([\w.$]+)\(", ll.read_text(encoding="utf-8"), re.M):
            (defined if m.group(1) == "define" else declared).add(m.group(2))
    where = {}
    for row in fixed:
        n, a, k = row.split()
        if k == "func":
            where[n] = int(a, 16)
    at = {}
    for n in defined:
        address = where.get(n)
        if address is None and (auto := re.fullmatch(r"func_([0-9A-Fa-f]{8})", n)):
            address = int(auto.group(1), 16)
        if address is not None:
            at.setdefault(address, set()).add(n)
    named = {}  # the project's name for each function's address (the layout's)
    for n, address in where.items():
        named.setdefault(address, set()).add(n)
    out = {}
    for n in declared - defined:
        address = where.get(n)
        # a function the C calls by an automatic name, `func_` or a data-style `D_` (a split that once took the
        # function for data: a racing game's HUD called D_8000A840 for the sprite drawer there)
        if address is None and (auto := re.fullmatch(r"(?:func|D)_([0-9A-Fa-f]{8})", n)):
            address = int(auto.group(1), 16)
        if address is None:
            address = (history or {}).get(n)
        here = at.get(address, set()) if address is not None else set()
        if len(here) > 1:
            # a port-only copy under the automatic name beside the matched function the project names now (a racing
            # game's sprite drawer, defined twice): the call goes to the project's own
            here = here & named.get(address, set()) or here
        if address is not None and len(here) == 1:
            out[n] = next(iter(here))
        elif n not in out and (library := (library_names or {}).get(n)) and library != n:
            out[n] = library
        elif address is not None and address not in at and len(named.get(address, ())) == 1 and n not in named[address]:
            # no C for it in the build, but the project names it now: a racing game's matched C still called
            # libultra's osCreateMesgQueue and osSpTaskStartGo by their automatic names, the host answers them by
            # name, and they were stubbed (its threads never got their queue, nothing was drawn)
            out[n] = next(iter(named[address]))
    return out


def function_table(header: str, fixed: list[str]) -> str:
    """funcs.c: the game's functions by console address, sorted, for calls through pointers (the host's port_func).
    A function the C declares void is listed through a wrapper that returns the $v0 word (adapt.py): called through
    a pointer for its value, it gives what its last call left there, as on the console (a racing game's heap calls
    its allocator through a table, and the allocator is a void function that ends with the call that allocates)."""
    from .adapt import REGS
    where = {}
    for row in fixed:
        n, a, k = row.split()
        if k == "func":
            where[n] = int(a, 16)
    entries, wrappers, invokers = [], [], []
    for m in re.finditer(r"/\* export: '([^']+)' \*/\n(\w+) (w2c_game_\w+)\(w2c_game\*([^)]*)\);", header):
        name, ret, c, params = m.groups()
        address = where.get(name)
        if address is None and (auto := re.fullmatch(r"func_([0-9A-Fa-f]{8})", name)):
            address = int(auto.group(1), 16)
        if address is None:
            continue
        invokers.append((address, invoker(c, ret, [t.strip() for t in params.split(",") if t.strip()])))
        if ret == "void":
            types = [t.strip() for t in params.split(",") if t.strip()]
            args = "".join(f", {t} a{i}" for i, t in enumerate(types))
            wrappers.append(f"static u32 port_v_{c}(w2c_game* g{args}) {{ {c}(g"
                            + "".join(f", a{i}" for i in range(len(types))) + "); return port_v0(g); }")
            c = f"port_v_{c}"
        entries.append((address, c, name))
    entries.sort()
    lines = ['#include "game.h"', "/* The game's functions by their console address (generated). */",
             "typedef struct { unsigned address; void *func; } PortFunc;",
             "static u32 port_v0(w2c_game* g) { u32 v; __builtin_memcpy(&v, g->w2c_memory.data + "
             f"0x{REGS + 16:X}u, 4); return __builtin_bswap32(v); }}", *wrappers, "const PortFunc port_funcs[] = {"]
    lines += [f"    {{0x{a:08X}u, (void *){c}}}, /* {n} */" for a, c, n in entries]
    lines += ["};", f"const unsigned port_func_count = {len(entries)};"]
    # one call from a recorded state (the host's PORT_CALL, app/behaviour.py): each function called with its own
    # types from MIPS's argument words, float registers and $v0 afterwards
    invokers.sort()
    lines += ["typedef void (*PortInvoke)(w2c_game*, const u32 *, const u32 *, u32 *);",
              "typedef struct { unsigned address; PortInvoke invoke; } PortCall;",
              "static u64 port_w64(const u32 *w, int i) { return (u64)w[i] << 32 | w[i + 1]; }",
              "static f32 port_f32(u32 b) { f32 v; __builtin_memcpy(&v, &b, 4); return v; }",
              "static f64 port_f64(u64 b) { f64 v; __builtin_memcpy(&v, &b, 8); return v; }",
              "static u32 port_b32(f32 v) { u32 b; __builtin_memcpy(&b, &v, 4); return b; }",
              "static u64 port_b64(f64 v) { u64 b; __builtin_memcpy(&b, &v, 8); return b; }",
              *(code for _, (_, code) in invokers), "const PortCall port_calls[] = {"]
    lines += [f"    {{0x{a:08X}u, {name}}}," for a, (name, _) in invokers]
    lines += ["};", f"const unsigned port_call_count = {len(invokers)};"]
    return "\n".join(lines) + "\n"


def invoker(c: str, ret: str, params: list[str]) -> tuple[str, str]:
    """(name, C) of a function that calls `c` as MIPS o32 passed its arguments: `w` the argument words ($a0-$a3, then
    the stack's), `f` the float registers as single words (FR=0: a double in an even and odd pair, low word first);
    leading float arguments in $f12 and $f14. `out`: $v0, $v1, $f0, $f1 afterwards, and what the function returns
    (0 nothing, 1 a word, 2 64 bits, 3 a float, 4 a double): only those registers mean anything after it."""
    args, slot, fpr, floats = [], 0, 12, True
    for i, t in enumerate(params):
        if t in ("f32", "f64") and floats and i < 2:
            if t == "f64":
                slot += slot % 2
                args.append(f"port_f64((u64)f[{fpr + 1}] << 32 | f[{fpr}])")
                slot += 2
            else:
                args.append(f"port_f32(f[{fpr}])")
                slot += 1
            fpr += 2
            continue
        floats = False
        if t in ("u64", "f64"):
            slot += slot % 2
            args.append(f"port_w64(w, {slot})" if t == "u64" else f"port_f64(port_w64(w, {slot}))")
            slot += 2
        else:
            args.append(f"w[{slot}]" if t == "u32" else f"port_f32(w[{slot}])")
            slot += 1
    call = f"{c}(g" + "".join(f", {a}" for a in args) + ")"
    body = {"void": f"{call}; out[0] = port_v0(g); out[4] = 0;",
            "u32": f"out[0] = {call}; out[4] = 1;",
            "u64": f"u64 r = {call}; out[0] = (u32)(r >> 32); out[1] = (u32)r; out[4] = 2;",
            "f32": f"out[2] = port_b32({call}); out[4] = 3;",
            "f64": f"u64 r = port_b64({call}); out[2] = (u32)r; out[3] = (u32)(r >> 32); out[4] = 4;"
            }.get(ret, f"{call};")
    name = f"port_i_{c}"
    return name, (f"static void {name}(w2c_game* g, const u32 *w, const u32 *f, u32 *out) {{ (void)w; (void)f; "
                  f"{body} }}")


def context_functions(lls: list[Path]) -> dict[Path, dict[str, str]]:
    """Private identities for repeated helper definitions saved as compilation context in other functions' files.

    Their bodies can differ between saved candidates. Keep each caller's own helper instead of selecting an
    arbitrary global winner or relaxing the linker. A helper's own source file retains its public definition.
    """
    modules = {}
    for ll in lls:
        for match in re.finditer(r"^define ([^@\n]*)@([\w.$]+)\(", ll.read_text(encoding="utf-8"), re.M):
            if not {"internal", "private"} & set(match[1].split()):
                modules.setdefault(match[2], []).append(ll)
    private = {}
    for name, files in modules.items():
        if len(files) < 2:
            continue
        for ll in files:
            if ll.stem != name:
                prefix = hashlib.sha256(ll.name.encode()).hexdigest()[:12]
                private.setdefault(ll, {})[name] = f"port_context_{prefix}_{name}"
    return private


def place_all(out: Path, lls: list[Path], functions: set, zig, env: dict, log, step=None, *, web: bool = False):
    """Every function's IR placed at its console address and compiled (beir.py): into out/o, or out/o-web for the
    browser build (`web`: webir.py too). Each is placed again only when its own rows in the tables change, or the
    tools do. The files that failed, with their first error line."""
    py, files = sys.executable, _Files()
    tables = (_rows(out / "sigs.txt", "\t"), _rows(out / "fixed.txt"), functions, _rows(out / "renames.txt"))
    tools = tuple(files(HERE / n) for n in ("beir.py", "adapt.py", *(("webir.py",) if web else ())))
    folder = out / ("o-web" if web else "o")
    folder.mkdir(exist_ok=True)
    contexts = context_functions(lls)

    def place(ll: Path) -> str | None | bool:
        be, obj = folder / (ll.stem + ".be.ll"), folder / (ll.stem + ".o")
        text = ll.read_text(encoding="utf-8")
        key = _digest(zig, *(("web",) if web else ()), *tools, text, *table_rows(text, *tables),
                      sorted(contexts.get(ll, {}).items()))
        if _fresh(obj, key):
            return False
        _unstamp(obj)
        done = _run([py, HERE / "beir.py", ll, be, "--functions", out / "functions.txt", "--renames", out / "renames.txt",
                     "--signatures", out / "sigs.txt", "--fixed", out / "fixed.txt",
                     *(arg for old, new in sorted(contexts.get(ll, {}).items()) for arg in ("--context-function", f"{old}={new}")),
                     *(["--web"] if web else [])], log)
        if done.returncode:
            return (done.stderr.strip().splitlines() or ["beir failed"])[-1]
        done = _run([zig, "cc", "-target", "wasm32-freestanding", "-O0", "-c", be, "-o", obj], log, env=env)
        if done.returncode:
            return (done.stderr.strip().splitlines() or ["failed"])[0]
        _stamp(obj, key)
        return None
    return _parallel(lls, place, log, "functions", step)


def build(repo: Path, *, console: str = "n64", log=print, steps=STEPS, leave_out=(), universal: bool = False,
          library_c=None, library_names=None, recomp=None, function_headers=None, ensure_host_import=False,
          runtime_image=None, code_offsets=None) -> Path:
    """Build the game in the save repository `repo` for the PC; returns the program (port/wasm/build/game). Only
    what changed since the last build is made again (the keys, above). `steps` are the progress bar's, when a
    caller adds its own after these (play.py); `leave_out`, library files the host provides (gather). A second
    build of one save (Play while compare_behaviour builds) waits for the first, then finds little left to do.
    `universal`: on macOS, a program for Apple Silicon and Intel Macs alike (port/wasm/build/game-universal), its
    objects kept apart from Play's so neither build undoes the other's. `library_c`: {file name: (C, flags)} of
    library functions built from ultralib's C (port/ultralib.units). `library_names`: library code's own names for
    functions with no C (renames). `recomp`: {"folder", "header"} of the console's recompiler's output, whose C the
    host runs for functions with no C yet (port/wasm/recomp.py). A standalone candidate comparison sets
    `ensure_host_import` because its C can define a single leaf with no imports of its own."""
    repo = Path(repo).expanduser().resolve()
    from ...app.recompile import build_turn
    recomp_turn = build_turn(recomp["folder"], log) if recomp else nullcontext()
    with build_turn(repo / "port" / "wasm" / "build", log, resource="native port build"), recomp_turn:
        return _build(repo, console=console, log=log, steps=steps, leave_out=leave_out, universal=universal,
                      library_c=library_c or {}, library_names=library_names or {}, recomp=recomp,
                      function_headers=function_headers, ensure_host_import=ensure_host_import, runtime_image=runtime_image,
                      code_offsets=code_offsets)


def _build(repo: Path, *, console: str, log, steps, leave_out, universal: bool = False, library_c=None,
           library_names=None, recomp=None, function_headers=None, ensure_host_import=False, runtime_image=None,
           code_offsets=None) -> Path:
    kit = consoles.get(console).port_kit
    if kit is None or kit.wasm is None:
        raise NetworkError(f"{consoles.get(console).name} has no WebAssembly build yet (its port kit has no host)")
    host = Path(kit.wasm)
    out = repo / "port" / "wasm" / "build"
    for name in ("ll", "o", "c", "obj"):
        (out / name).mkdir(parents=True, exist_ok=True)
    _write(repo / "port" / "wasm" / ".gitignore", "build/\n")
    progress = Progress(log, steps)
    progress.start(STEPS[0])
    toolchains.ensure("zig", log=log)
    zig = toolchains.binary("zig")
    wabt = toolchains.ensure("wabt", log=log)
    env = {**os.environ, "ZIG_GLOBAL_CACHE_DIR": str(out / "zig-cache"), "ZIG_LOCAL_CACHE_DIR": str(out / "zig-cache")}
    py = sys.executable
    files = _Files()

    log("gathering the game's C from its save")
    manifest = _save_manifest(repo)
    fixed, functions = addresses(manifest)
    original = original_code(host, manifest, fixed)
    if original:
        log(f"using the original game code for {len(original)} functions that depend on the console's "
            "stack layout or registers")
    leave_out = set(leave_out) | original
    sources = gather(repo, out, leave_out, function_headers, ensure_host_import=ensure_host_import, manifest=manifest)
    from . import recomp as recomp_c
    hosted = {recomp_c._demangle(c) for path in sorted(host.glob("*.c")) for c in
              re.findall(r"\bw2c_env_(\w+)\(struct w2c_env \*", path.read_text(encoding="utf-8", errors="replace"))}
    taken_names = {name.removesuffix(".c").casefold() for name in sources}
    # A library name given to code with no C (library_names) is provided only where the host or the port's glue
    # answers it by name. The synthesizer's own functions (alSynAllocFX, alSavePull, ...) are "native": nothing
    # answered them, so ultralib's C for them was never built and the link stubbed them ("not implemented,
    # skipped"); a game's audio thread then built a task from nothing and its graphics never started.
    provided_by = set(functions) | hosted | answered_names(library_names, hosted, kit.sdk_group)
    library = _library(
        out, library_c or {}, sources, log,
        lambda name: name.casefold() in taken_names or name in provided_by
        or (kit.sdk_group is not None and kit.sdk_group(name) in ("replaced", "glue")))
    callees = set(library) - set(library_c or {})  # built for the link, not because the project set them aside
    # A library name nothing defines (the host and the glue don't answer it, ultralib's C wasn't built for it: not
    # installed, or no unit to know the library's build from) would send the calls to a stub that logged "not
    # implemented, skipped": a bowling game's two copies of alSynNew, code with no C, stayed unreachable and its audio
    # never started. The calls keep the function's own name and run its recompiled or interpreted code instead.
    paired_names = library_names  # (the pairing of ultralib's data with the game's still wants every library name)
    library_names = reachable_library_names(library_names, library, set(functions) | hosted)
    _write(out / "fixed.txt", "\n".join(fixed) + "\n")
    _write(out / "functions.txt", "\n".join(sorted(functions)) + "\n")

    progress.start(STEPS[1])
    log(f"compiling {len(sources) + len(library)} files to WebAssembly IR")

    def ir(name: str) -> str | None | bool:
        if name in library:  # ultralib's C, with its own headers and the defines of the game's build of it
            src, ll = out / "ultralib" / name, out / "ll" / (name[:-2] + ".ll")
            key = files.key(src, [], zig, *IR_FLAGS, *library[name])
            if _fresh(ll, key):
                return False
            _unstamp(ll)
            done = _run([zig, "cc", *IR_FLAGS, *library[name], "-S", "-emit-llvm", src, "-o", ll], log, env=env)
            if done.returncode:
                if name in callees:  # a callee added for the link, not one the game needs by itself: stubbed as before
                    library.pop(name, None)
                    log(f"{name} (ultralib callee) left to a stub: " + (done.stderr.strip().splitlines() or ["failed"])[0])
                    return None
                return f"{name} (ultralib): " + (done.stderr.strip().splitlines() or ["failed"])[0]
            _stamp(ll, key)
            return None
        src, ll = out / "src" / name, out / "ll" / (name[:-2] + ".ll")
        key = files.key(src, [out / "include"], zig, *IR_FLAGS, files(HERE / "declarations.py"))
        if _fresh(ll, key):
            return False
        _unstamp(ll)
        port = ["-DPORT=1"] if NEEDS_PORT.search(src.read_text(encoding="utf-8")) else []
        done = compile_ir(zig, src, ll, out / "include", env, log, port)
        if done.returncode and not port:
            done = compile_ir(zig, src, ll, out / "include", env, log, ["-DPORT=1"])
        if done.returncode:
            return (done.stderr.strip().splitlines() or ["failed"])[0]
        _stamp(ll, key)
        return None
    if failed := _parallel(sorted({*sources, *library}), ir, log, "files", progress.files):
        all_sources = sources | {name: out / "ultralib" / name for name in library}
        fallback = fallback_failed_sources(failed, all_sources, repo, out, fixed)
        if not fallback:
            raise NetworkError(f"{len(failed)} of the game's files don't compile for the WebAssembly build: "
                               + "; ".join(failed[:3]))
        for name in fallback:
            sources.pop(name)
            # Browser and cross-builds enumerate this generated folder too; a failed file must not revive a stale object.
            (out / "src" / name).unlink(missing_ok=True)
        if not sources and not library:
            sources[EMPTY_UNIT] = _empty_unit(out)
            if error := ir(EMPTY_UNIT):
                raise NetworkError(f"Preparing the original-code fallback failed: {error}")
        log(f"{len(fallback)} saved C files could not compile for this port; their original functions use "
            "N64Recomp or the interpreter: " + ", ".join(sorted(fallback)[:8]))
    compiled = {name.casefold() for name in (*sources, *library)}
    for stale in (out / "ll").glob("*.ll"):
        if (stale.name[:-3] + ".c").casefold() not in compiled:
            for made in (stale, stale.with_name(stale.name + ".key")):
                made.unlink(missing_ok=True)

    if library and runtime_image:
        # the state ultralib's C shares (alGlobals) is the game's variable at the game's address
        try:
            shared, own = library_data.pair(out, set(library), manifest.get("layout") or [], paired_names or {},
                                       Path(runtime_image).read_bytes(),
                                       next((int(r.split()[1], 16) for r in fixed if r.split()[0] == "entrypoint"),
                                            0x80000400),
                                       {r.split()[0] for r in fixed},
                                       {int(r.split()[1], 16) for r in fixed
                                        if len(r.split()) > 2 and r.split()[2] == "func"},
                                       code_offsets)
        except (OSError, ImportError, ValueError) as error:
            shared, own = {}, {}
            log(f"the game's addresses for ultralib's shared variables weren't read: {error}")
        if shared or own:
            fixed = (fixed + [f"{name} 0x{address:08X} data" for name, address in sorted(shared.items())]
                     + [f"{name} 0x{address:08X} func" for name, address in sorted(own.items())])
            _write(out / "fixed.txt", "\n".join(fixed) + "\n")
            if shared:
                log("ultralib's " + ", ".join(sorted(shared)) + " are the game's own variables at "
                    + ", ".join(f"{a:08X}" for a in sorted(shared.values())))
            if own:
                log(f"ultralib's functions are at the game's addresses ({len(own)})")
    progress.start(STEPS[2])
    log("one signature per function")
    lls = sorted((out / "ll").glob("*.ll"))
    renamed = renames(lls, fixed, library_names, recompiler_names(recomp["folder"]) if recomp else None)
    _write(out / "renames.txt", "".join(f"{a} {b}\n" for a, b in sorted(renamed.items())))
    done = _run([py, HERE / "sigs.py", out / "sigs.txt", host / "libultra.sigs", out / "renames.txt", *lls], log)
    if done.returncode:
        raise NetworkError(f"reading the functions' signatures failed: {done.stderr.strip()[-400:]}")
    own = game_host(host, repo)
    # what only recompiled C or the interpreter runs (interp.py): MIPS's own shape
    from . import interp
    from . import recomp as recomp_c
    left_out = {n.removesuffix(".c") for n in _names(repo / "port" / "wasm" / "exclude.txt") | set(leave_out)}
    replaced = (lambda n: kit.sdk_group(n) in ("replaced", "glue")) if kit and kit.sdk_group else (lambda n: False)
    known = _rows(out / "sigs.txt", "\t")
    extra = []
    if recomp and Path(recomp["folder"], "recomp.json").is_file():
        extra = recomp_c.mips_signatures(Path(recomp["folder"]), lls, fixed, [*sorted(host.glob("*.c")), *own],
                                         library_names)
    extra += interp.mips_signatures(lls, fixed, [*sorted(host.glob("*.c")), *own], library_names, left_out, replaced,
                                    interp.by_value(lls, declared=True))
    extra = {renamed.get(n, n) for n in extra}
    extra = sorted(extra.difference(known))
    if extra:
        ret, params = recomp_c.MIPS_SIGNATURE
        _write(out / "sigs.txt", (out / "sigs.txt").read_text(encoding="utf-8")
               + "".join(f"{n}\t{ret}\t{params}\n" for n in extra))

    progress.start(STEPS[3])
    log("placing every symbol at its console address, data big-endian")
    if failed := place_all(out, lls, set(functions), zig, env, log, progress.files):
        raise NetworkError(f"{len(failed)} functions couldn't be placed at their addresses: " + "; ".join(failed[:3]))
    objects = linked(out / "o", {*sources, *library})
    _write(out / "objects.txt", "\n".join(str(p) for p in objects) + "\n")
    from . import calls
    suspicious = calls.summary(calls.write(out, objects))
    if suspicious["calls"]:
        log(f"{suspicious['calls']} calls in {suspicious['functions']} functions disagree with the function they call "
            "(matched, but only the original compiler sees them right): Port readiness lists them")

    progress.start(STEPS[4])
    log(f"linking {len(objects)} functions")
    done = _run([zig, "wasm-ld", "-o", out / "game.wasm", f"@{out / 'objects.txt'}", *LINK_FLAGS], log, env=env)
    if done.returncode:
        raise NetworkError(f"linking the WebAssembly build failed: {done.stderr.strip()[-600:]}")
    _run([py, HERE / "beslots.py", out / "game.wasm"], log)

    progress.start(STEPS[5])
    log("translating it to C (wasm2c)")
    fresh = out / "c.new"
    shutil.rmtree(fresh, ignore_errors=True)
    fresh.mkdir()
    done = _run([wabt / "bin" / "wasm2c", out / "game.wasm", "-o", fresh / "game.c", "-n", "game",
                 "--num-outputs", "8"], log)
    if done.returncode:
        raise NetworkError(f"wasm2c failed: {done.stderr.strip()[-600:]}")
    done = _run([py, HERE / "bemem.py", fresh / "game-impl.h"], log)
    if done.returncode:
        raise NetworkError(f"making memory big-endian failed: {done.stderr.strip()[-400:]}")
    loop_safe_points(fresh)
    _sync(fresh, out / "c")  # only the parts that changed are compiled again
    _write(out / "funcs.c", function_table((out / "c" / "game.h").read_text(encoding="utf-8"), fixed))
    recompiled = []
    if recomp and Path(recomp["folder"], "recomp.json").is_file():  # recompiled C for the functions with no C yet
        from . import recomp as recomp_c
        try:
            prepared = recomp_c.prepare(Path(recomp["folder"]), out / "recomp", (out / "c" / "game.h").read_text(
                encoding="utf-8"), fixed, [*sorted(host.glob("*.c")), *own], Path(recomp["header"]), library_names, log,
                renames=renamed, image=Path(runtime_image) if runtime_image else None)
        except (OSError, ValueError) as error:
            raise NetworkError(f"the recompiled C couldn't be prepared for the port: {error}") from error
        recompiled = prepared["units"]
    # the functions with no C yet, run by the host's interpreter from the player's ROM (interp.py, mips.c): what
    # recompiled C doesn't already bridge
    bridged = set()
    for u in recompiled:
        if u.name == "bridges.c":
            bridged = set(re.findall(r"\b(w2c_env_\w+)\(struct w2c_env \*", u.read_text(encoding="utf-8")))
    patterns = []
    image = Path(runtime_image) if runtime_image else repo / ((manifest.get("game") or {}).get("file") or "")
    runtime_sdk_patterns = getattr(kit, "runtime_sdk_patterns", None)
    if runtime_sdk_patterns and image.is_file():
        from .recomp import HOST, _demangle

        hosted = {_demangle(m.group(2)) for path in host.glob("*.c")
                  for m in HOST.finditer(path.read_text(encoding="utf-8", errors="replace"))}
        patterns = runtime_sdk_patterns(image.read_bytes(), hosted)
        if patterns:
            log(f"identified {len(patterns)} relocated SDK functions for the host")
    interp_c, counts = interp.generate((out / "c" / "game.h").read_text(encoding="utf-8"), fixed,
                                       [*sorted(host.glob("*.c")), *own], library_names, bridged, left_out, replaced,
                                       interp.variadic(lls), interp.by_value(lls), interp.by_value(lls, declared=True),
                                       interp.pointer_types(p.read_text(encoding="utf-8", errors="replace")
                                                            for p in sorted((out / "c").glob("game_*.c"))),
                                       renames=renamed, patterns=patterns)
    _write(out / "interp.c", interp_c)
    if counts["imports"]:
        log(f"{counts['imports']} functions the game's C calls have no C yet: the interpreter runs them from the ROM")
    _run([py, HERE / "stubs.py", out / "c" / "game.h", out / "stubs.c", *sorted(host.glob("*.c")), *own,
          *(u for u in recompiled if u.name == "bridges.c"), out / "interp.c"], log)

    progress.start(STEPS[6])
    log("building the host")
    rt = wabt / "share" / "wabt" / "wasm2c"
    units = [*sorted((out / "c").glob("game_*.c")), rt / "wasm-rt-impl.c", rt / "wasm-rt-mem-impl.c",
             rt / "wasm-rt-exceptions-impl.c", *sorted(host.glob("*.c")), *own, *sorted(host.glob("window/*.c")),
             out / "funcs.c", out / "stubs.c", out / "interp.c", *recompiled]  # window/: PORT_WINDOW's SDL2 window, opened only when asked
    includes = ["-I", wabt / "include", "-I", rt, "-I", out / "c"]
    quiet = ["-w", "-I", out / "recomp"]  # recompiled C: thousands of generated functions, warnings off
    arch = host_arch_flags(universal)
    cc = [*host_compiler(zig), *arch]
    host_flags = ["-O1", "-g", "-fno-inline"]
    host_flags += host_import_flags((out / "c" / "game.h").read_text(encoding="utf-8"))
    objs = out / ("obj-universal" if arch else "obj")
    objs.mkdir(parents=True, exist_ok=True)

    def compile_unit(unit: Path) -> str | None | bool:
        obj = objs / (unit.stem + ".o")
        extra = quiet if unit.parent == out / "recomp" else []
        key = files.key(unit, [wabt / "include", rt, out / "c", out / "recomp"], *cc, *host_flags, *extra)
        if _fresh(obj, key):
            return False
        _unstamp(obj)
        done = _run([*cc, *host_flags, *extra, *includes, "-c", unit, "-o", obj], log, env=env)
        if done.returncode:
            return (done.stderr.strip().splitlines() or ["failed"])[0]
        _stamp(obj, key)
        return None
    if failed := _parallel(units, compile_unit, log, "host files", progress.files):
        raise NetworkError("building the host failed: " + "; ".join(failed[:3]))
    program = out / ("game-universal" if arch else "game")
    libs = ["-lpthread"] + (["-lws2_32"] if sys.platform == "win32" else [])  # Winsock, for the live view (live.c)
    libs += ["-ldl"] if sys.platform.startswith("linux") else []  # dlopen, for SDL2 (window/sdl.c)
    done = _run([*cc, "-o", program, *(objs / (u.stem + ".o") for u in units), *libs], log, env=env)
    if done.returncode:
        raise NetworkError(f"linking the host failed: {done.stderr.strip()[-600:]}")
    log(f"built {program}: run it with the game's ROM, `{program} ROM [seconds]`")
    return program
