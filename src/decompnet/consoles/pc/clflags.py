"""Every Visual C++ (cl.exe) flag that can change the code of a function or of its object, by the versions and targets
that take it: what search_flags may try on a source file of a PC or Xbox game started from scratch, by itself (the
sweep: one choice changed at a time, the choices real games differ in most first) or by name (`flags="/GS- /hotpatch"`).

Each flag is in one group, the choice it makes (the frame pointer, the optimisation level, ...): a variant that sets
one drops the file's other flags of that group. The project's own flags (tools/configure.py's CL_FLAGS) come first on
the compiler's command line and a file's after them, and cl.exe takes the last of two that disagree (D9025,
"overriding '/O2' with '/O1'"), so a file's flag always wins over the project's.

How each version boundary is known. `ran`: cl.exe of every Visual C++ the packs have that wibo runs (6.0, 7.0, 7.1,
8.0, 8.0 SP1, 9.0, 9.0 SP1, 10.0, 10.0 SP1) compiled a small C file with /O2 and the flag: a version that said
"ignoring unknown option" (D4002, D9002) doesn't take it, one that built it or said it's deprecated (D9035) does
(tests/test_pc_clflags.py runs this again where the packs are installed). `ran=False`: newer than 2010 (the
packs' 2017 needs wine, the user's own copies are theirs), from Microsoft's documentation of each release; cl.exe
only warns of a flag it doesn't know, so a wrong guess there costs a variant that builds as the file's own.
What the runs showed, beyond the documentation: 6.0 knows neither /GS nor /Zc (it reads /Zc:... as /Z options and
stops), 7.0 and 7.1 have /GS but no /GS-, /Gy- and /Zc:...- come with 2005, /vd2 too (6.0-7.1 build it as /vd1),
/arch:AVX is in 2010's first release, /RTC and 6.0's /GZ only build with /Od, and 6.0-7.1 take /openmp without a
word but build no OpenMP (`#pragma omp` stays an unknown pragma).

Not here: /GL (whole-program code generation): its objects hold the compiler's intermediate code, not machine code, so
objdiff has nothing to compare a file with until the linker generates it; the import counts a game's /GL files as
unmatchable, and Xbox games built with it have their own harness (tools/ltcg.py). Nor what only names or writes
files (/Fo /Fa /Fd /Fp /Yc /Yu /FI /I /D /U /B1 /Bx /link, response files), debug information (/Zi /Z7 /ZI: /ZI writes
a program database the per-file build has no place for) or what doesn't change code (/Gm /Gi /GD /W /nologo /Zl).
Undocumented options (/d1..., /d2...) aren't either. /Gt doesn't exist for 32-bit code (every version said unknown).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ...network import NetworkError

X86, X64, BOTH = frozenset({"x86"}), frozenset({"x64"}), frozenset({"x86", "x64"})


def version_key(compiler: str) -> tuple[int, int, int]:
    """A Visual C++ of the project ("MSVC/7.1", "10.0SP1", "14.16-x64", a copy of the user's "own-19.29.30137", an
    XDK's "xdk-5849") as (major, minor, service pack): 6.0 is (6, 0, 0), 2010 SP1 (10, 0, 1), 2019's 19.29 (14, 29, 0)."""
    name = compiler.removeprefix("MSVC/")
    if own := re.match(r"own-(\d+)\.(\d+)\.(\d+)", name):  # cl.exe's own version: 12.00 is 6.0, 13.10 7.1, 19.29 14.29
        major, minor, build = (int(g) for g in own.groups())
        if major >= 19:
            return 14, minor, 0
        if major == 13:
            return 7, minor // 10, 0
        return major - 6, 0, int(major == 16 and build >= 40219)
    if name.startswith("xdk-"):  # shortcut: an XDK's compiler counts as 7.0 (3911's is), 7.1's /G7 and /arch need naming
        return 7, 0, 0       # its version; read the XDK copy's cl.exe version once an XDK game needs them
    m = re.match(r"(\d+)\.(\d+)(SP1)?", name)
    if not m:
        raise NetworkError(f"decompdecomp doesn't know which flags Visual C++ {compiler!r} takes")
    return int(m.group(1)), int(m.group(2)), int(bool(m.group(3)))


@dataclass(frozen=True)
class Flag:
    flag: str
    group: str
    since: str = "6.0"
    until: str | None = None  # the last version that takes it
    targets: frozenset = BOTH
    ran: bool = True  # its boundaries were run (above); False: Microsoft's documentation
    default: tuple[str, str | None] | None = None  # versions where /O2, the compiler's defaults and the template's
    # CL_FLAGS (/GX, /EHsc from 2005) give it anyway
    needs: str = ""  # a flag it only builds with: the sweep tries it only where the file has that

    def takes(self, key: tuple[int, int, int], target: str) -> bool:
        return target in self.targets and version_key(self.since) <= key and \
            (self.until is None or key <= version_key(self.until))

    def is_default(self, key: tuple[int, int, int]) -> bool:
        return self.default is not None and version_key(self.default[0]) <= key and \
            (self.default[1] is None or key <= version_key(self.default[1]))


ALWAYS = ("6.0", None)
F = Flag
# The groups in the order the sweep tries them: what real games differ in most first (the frame pointer: about half of
# a 2010 game's code keeps it; size or speed: some of a game's libraries were built with /O1; inlining; then the
# security cookie, hot patching, function-level linking, intrinsics, floating point, the instruction set, ...)
CATALOGUE: tuple[Flag, ...] = (
    F("/Oy-", "frame pointer", targets=X86), F("/Oy", "frame pointer", targets=X86, default=ALWAYS),
    F("/O1", "optimisation"), F("/O2", "optimisation", default=ALWAYS), F("/Ox", "optimisation"),
    F("/Od", "optimisation"),
    F("/Ob1", "inlining"), F("/Ob0", "inlining"), F("/Ob2", "inlining", default=ALWAYS),
    F("/Ob3", "inlining", since="14.20", ran=False),
    F("/GS-", "security cookie", since="8.0"), F("/GS", "security cookie", since="7.0", default=("8.0", None)),
    F("/hotpatch", "hot patching", since="8.0"),
    F("/Gy-", "function-level linking", since="8.0"), F("/Gy", "function-level linking", default=ALWAYS),
    F("/Oi-", "intrinsics"), F("/Oi", "intrinsics", default=ALWAYS),
    F("/Os", "size or speed"), F("/Ot", "size or speed", default=ALWAYS),
    F("/Op", "floating point", until="7.1"), F("/Op-", "floating point", until="7.1", default=ALWAYS),
    F("/fp:fast", "floating point", since="8.0"), F("/fp:strict", "floating point", since="8.0"),
    F("/fp:precise", "floating point", since="8.0", default=ALWAYS),
    F("/fp:except", "floating-point exceptions", since="8.0"), F("/fp:except-", "floating-point exceptions", since="8.0",
                                                            default=ALWAYS),
    F("/arch:SSE", "instruction set", since="7.1", targets=X86), F("/arch:SSE2", "instruction set", since="7.1",
                                                                     targets=X86, default=("11.0", None)),
    F("/arch:IA32", "instruction set", since="11.0", targets=X86, ran=False),
    F("/arch:AVX", "instruction set", since="10.0"), F("/arch:AVX2", "instruction set", since="12.0", ran=False),
    F("/arch:AVX512", "instruction set", since="14.11", ran=False),
    F("/arch:SSE4.2", "instruction set", since="14.40", ran=False),
    F("/arch:AVX10.1", "instruction set", since="14.40", ran=False),
    F("/G6", "processor", until="7.1", targets=X86), F("/G5", "processor", until="7.1", targets=X86),
    F("/G7", "processor", since="7.1", until="7.1", targets=X86), F("/GB", "processor", until="7.1", targets=X86),
    F("/G4", "processor", until="7.1", targets=X86), F("/G3", "processor", until="7.1", targets=X86),
    F("/favor:blend", "processor", since="8.0", targets=X64, ran=False),
    F("/favor:AMD64", "processor", since="8.0", targets=X64, ran=False),
    F("/favor:INTEL64", "processor", since="9.0", targets=X64, ran=False),
    F("/favor:ATOM", "processor", since="11.0", ran=False),
    F("/Gz", "calling convention", targets=X86), F("/Gr", "calling convention", targets=X86),
    F("/Gd", "calling convention", targets=X86, default=ALWAYS),
    F("/Gv", "calling convention", since="12.0", ran=False),
    F("/GX-", "exceptions", until="7.1"), F("/GX", "exceptions", until="7.1", default=ALWAYS), F("/EHs-c-", "exceptions"),
    F("/EHsc", "exceptions", default=ALWAYS), F("/EHs", "exceptions"), F("/EHa", "exceptions"),
    F("/EHscr", "exceptions", since="14.0", ran=False),
    F("/GF-", "string pooling", since="7.0"), F("/Gf", "string pooling", until="7.1"),
    F("/GF", "string pooling", default=ALWAYS),
    F("/Og-", "global optimisation"), F("/Og", "global optimisation", default=ALWAYS),
    F("/Ge", "stack probes"), F("/Gs", "stack probes"),  # (/Gs<bytes>: STACK_PROBE, by name only)
    F("/Zp1", "struct packing"), F("/Zp2", "struct packing"), F("/Zp4", "struct packing"),
    F("/Zp8", "struct packing", default=ALWAYS), F("/Zp16", "struct packing"),
    F("/GR", "RTTI", default=("8.0", None)), F("/GR-", "RTTI", default=("6.0", "7.1")),
    F("/QIfist", "x87 rounding", targets=X86),
    F("/Qfast_transcendentals", "fast transcendentals", since="9.0"),
    F("/Qimprecise_fwaits", "imprecise fwaits", since="9.0", needs="/fp:except"),
    F("/Qsafe_fp_loads", "safe floating-point loads", since="10.0"),
    F("/Oa", "aliasing", until="7.1"), F("/Ow", "aliasing", until="7.1"),
    F("/Gw", "global data", since="12.0", ran=False), F("/Gw-", "global data", since="12.0", ran=False),
    F("/J", "unsigned char"),
    F("/Zc:wchar_t", "wchar_t", since="7.0", default=("8.0", None)), F("/Zc:wchar_t-", "wchar_t", since="8.0"),
    F("/Zc:forScope", "for scope", since="7.0", default=("8.0", None)), F("/Zc:forScope-", "for scope", since="8.0"),
    F("/TP", "language"), F("/TC", "language"),
    F("/Za", "language extensions"), F("/Ze", "language extensions", default=ALWAYS),
    F("/MT", "runtime library"), F("/MD", "runtime library"), F("/MTd", "runtime library"),
    F("/MDd", "runtime library"), F("/ML", "runtime library", until="7.1"), F("/MLd", "runtime library", until="7.1"),
    F("/GZ", "runtime checks", needs="/Od"), F("/RTC1", "runtime checks", since="7.0", needs="/Od"),
    F("/RTCs", "runtime checks", since="7.0", needs="/Od"), F("/RTCu", "runtime checks", since="7.0", needs="/Od"),
    F("/RTCc", "conversion checks", since="7.0", needs="/Od"),
    F("/Gh", "enter hook"), F("/GH", "exit hook", since="7.0"),
    F("/GT", "fiber-safe TLS"), F("/GA", "application TLS"),
    F("/QIfdiv", "FDIV fix", until="7.1", targets=X86), F("/QI0f", "0F fix", until="7.1", targets=X86),
    F("/vmb", "pointers to members"), F("/vmg", "pointers to members"),
    F("/vms", "member inheritance"), F("/vmm", "member inheritance"), F("/vmv", "member inheritance"),
    F("/vd0", "vtordisp"), F("/vd1", "vtordisp", default=ALWAYS), F("/vd2", "vtordisp", since="8.0"),
    F("/openmp", "OpenMP", since="8.0"),
    F("/Qpar", "auto-parallelizer", since="11.0", ran=False),
    F("/volatile:iso", "volatile", since="11.0", ran=False), F("/volatile:ms", "volatile", since="11.0", ran=False),
    F("/sdl", "SDL checks", since="11.0", ran=False), F("/sdl-", "SDL checks", since="11.0", ran=False),
    F("/kernel", "kernel mode", since="11.0", ran=False), F("/kernel-", "kernel mode", since="11.0", ran=False),
    F("/Zc:inline", "unreferenced inline data", since="12.0", ran=False),
    F("/Zc:inline-", "unreferenced inline data", since="12.0", ran=False),
    F("/homeparams", "home parameters", since="8.0", targets=X64, ran=False),
    F("/guard:cf", "control flow guard", since="14.0", ran=False),
    F("/guard:cf-", "control flow guard", since="14.0", ran=False),
    F("/Zc:threadSafeInit-", "thread-safe statics", since="14.0", ran=False),
    F("/Zc:threadSafeInit", "thread-safe statics", since="14.0", ran=False),
    F("/Zc:sizedDealloc-", "sized deallocation", since="14.0", ran=False),
    F("/Zc:sizedDealloc", "sized deallocation", since="14.0", ran=False),
    F("/Zc:throwingNew", "throwing new", since="14.0", ran=False),
    F("/Zc:throwingNew-", "throwing new", since="14.0", ran=False),
    F("/permissive-", "conformance", since="14.10", ran=False), F("/permissive", "conformance", since="14.10", ran=False),
    F("/Zc:alignedNew-", "aligned new", since="14.12", ran=False),
    F("/Zc:alignedNew", "aligned new", since="14.12", ran=False),
    F("/Qspectre", "Spectre mitigation", since="14.12", ran=False),
    F("/Qspectre-load", "Spectre mitigation", since="14.24", ran=False),
    F("/Qspectre-load-cf", "Spectre mitigation", since="14.24", ran=False),
    F("/Gu", "distinct functions", since="14.20", ran=False),
    F("/QIntel-jcc-erratum", "JCC erratum", since="14.25", ran=False),
    F("/guard:ehcont", "EH continuation", since="14.27", targets=X64, ran=False),
    F("/fp:contract", "floating-point contraction", since="14.30", ran=False),
    F("/jumptablerdata", "jump tables", since="14.37", targets=X64, ran=False),
)
BY_FLAG = {f.flag: f for f in CATALOGUE}
STACK_PROBE = re.compile(r"/Gs\d{1,7}")  # /Gs4096: probe the stack in functions with more locals than that
# What a game's files differ in most, tried before the rest: the frame pointer kept (/Oy-), optimised for size (/O1),
# inlining only what's marked inline (/Ob1), and size-optimised code that keeps its frame
FIRST = (("/Oy-",), ("/O1",), ("/Ob1",), ("/O1", "/Oy-"))
REFUSED = {"/GL": "/GL makes objects of the compiler's intermediate code, not machine code: objdiff can't judge a "
                  "file of it alone (its code is made when the program links), so search_flags never tries it"}
REFUSED["/GL-"] = REFUSED["/GL"]


def lookup(flag: str) -> Flag | None:
    """The catalogue's entry for a flag, /Gs<bytes> as the stack probes' /Gs."""
    found = BY_FLAG.get(flag)
    if found is None and STACK_PROBE.fullmatch(flag):
        return Flag(flag, "stack probes")
    return found


def available(compiler: str, target: str) -> list[Flag]:
    """The catalogue's flags a Visual C++ takes for a target ("x86", "x64"), in the sweep's order."""
    key = version_key(compiler)
    return [f for f in CATALOGUE if f.takes(key, target)]


def allowed_text(compiler: str, target: str) -> str:
    """What may be named for a compiler, by group: the sentence an agent reads when a flag isn't one of them."""
    groups: dict[str, list[str]] = {}
    for f in available(compiler, target):
        groups.setdefault(f.group, []).append(f.flag)
    groups.setdefault("stack probes", []).append("/Gs<bytes>")
    return "; ".join(f"{g}: {' '.join(fs)}" for g, fs in groups.items())


def parse(text: str, compiler: str, target: str) -> list[str]:
    """The flags an agent named (`flags="/GS- /hotpatch"`), checked against the catalogue for the file's compiler and
    target: a flag that isn't one exactly (case and all, as cl.exe reads them) is refused, so nothing that names a
    file, a path, a response file or another program reaches the compiler's command line. "-O1" is "/O1" (cl.exe
    takes either). Raises NetworkError saying what's allowed."""
    if not isinstance(text, str) or not text.strip():
        raise NetworkError("flags: name one or more Visual C++ flags, e.g. flags=\"/GS- /hotpatch\"")
    key, out, groups = version_key(compiler), [], {}
    for token in text.split():
        flag = "/" + token[1:] if token[:1] == "-" else token
        if flag in REFUSED:
            raise NetworkError(REFUSED[flag])
        entry = lookup(flag)
        if entry is None or not entry.takes(key, target):
            why = "isn't a flag search_flags may try" if entry is None else \
                f"isn't taken by Visual C++ {compiler.removeprefix('MSVC/')} ({target})"
            raise NetworkError(f"{token!r} {why}. Allowed, by group: {allowed_text(compiler, target)}")
        if entry.group in groups and groups[entry.group] != flag:
            raise NetworkError(f"{groups[entry.group]} and {flag} are one choice ({entry.group}): name one of them")
        groups[entry.group] = flag
        if flag not in out:
            out.append(flag)
    return out


def with_flag(flags: list[str], flag: str) -> list[str]:
    """`flags` with `flag` set: the file's other flags of its group gone, it last (as cl.exe reads them)."""
    if flag in flags:
        return list(flags)
    group = lookup(flag).group
    return [f for f in flags if (e := lookup(f)) is None or e.group != group] + [flag]


def variants(flags: list[str], compiler: str, target: str, named: list[str] = ()) -> list[list[str]]:
    """A file's flags (first) and the variants search_flags tries. Named flags: all of them together, then each alone
    when there are several. Otherwise the sweep: each group of the catalogue in its order, a file's flag of the group
    taken away (the project's then), and each of the group's other flags the compiler takes set instead, but for what
    /O2 and the compiler's defaults give anyway, after FIRST's. One change at a time: agents name combinations."""
    out = [list(flags)]

    def add(new):
        if new not in out:
            out.append(new)
    if named:
        together = list(flags)
        for flag in named:
            together = with_flag(together, flag)
        add(together)
        if len(named) > 1:
            for flag in named:
                add(with_flag(list(flags), flag))
        return out
    key = version_key(compiler)
    for toggle in FIRST:  # (a file that has them: without them)
        if all(lookup(f).takes(key, target) for f in toggle):
            if all(f in flags for f in toggle):
                add([f for f in flags if f not in toggle])
            else:
                new = list(flags)
                for f in toggle:
                    new = with_flag(new, f)
                add(new)
    order = list(dict.fromkeys(f.group for f in CATALOGUE))
    for group in order:
        members = [f for f in CATALOGUE if f.group == group]
        have = [f for f in flags if (e := lookup(f)) is not None and e.group == group]
        if have:
            add([f for f in flags if f not in have])
        for f in members:
            if not f.takes(key, target) or f.flag in have or (not have and f.is_default(key)):
                continue
            if f.needs and f.needs not in flags:
                continue
            add(with_flag(list(flags), f.flag))
    return out
