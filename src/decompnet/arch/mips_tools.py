"""Expose installed MIPS binutils under the prefixes public Makefiles search for, only in their build PATH."""

from __future__ import annotations

import os
import re
import shutil
import sys
import threading
from pathlib import Path

from .. import toolchains

_LOCK = threading.Lock()
_PREFIXES = ("mips-linux-gnu-", "mips64-linux-gnu-", "mips64-elf-")
# macOS's iconv writes a backslash after a multi-byte character as JIS X 0208's full-width one (0xA1C0), so a "\\n" in
# an EUC-JP source (the strings of a racing game's Japanese text) turns into three bytes where glibc's iconv, the one a
# project's ROM was matched with, writes 0x5C 0x6E. The shim converts what a build asks for in EUC-JP with Python's
# codec, which passes ASCII through, and leaves every other conversion to the system's iconv.
_ICONV_SHIM = '''#!{python}
import os, sys
args, files, frm, to, i = sys.argv[1:], [], "UTF-8", None, 0
while i < len(args):
    a = args[i]
    if a in ("-f", "-t"):
        if a == "-f":
            frm = args[i + 1]
        else:
            to = args[i + 1]
        i += 1
    elif a.startswith("-f") and len(a) > 2:
        frm = a[2:]
    elif a.startswith("-t") and len(a) > 2:
        to = a[2:]
    elif a in ("-c", "-s"):
        pass
    else:
        files.append(a)
    i += 1
if (to or "").upper().replace("-", "").replace("_", "") in ("EUCJP", "UJIS") and (frm or "").upper().replace("-", "") == "UTF8":
    data = b"".join(open(f, "rb").read() for f in files) if files else sys.stdin.buffer.read()
    try:
        sys.stdout.buffer.write(data.decode("utf-8").encode("euc_jp"))
    except (UnicodeError, LookupError):
        os.execv("{iconv}", ["iconv", *sys.argv[1:]])
    sys.exit(0)
os.execv("{iconv}", ["iconv", *sys.argv[1:]])
'''
# macOS's head and tail lack GNU's long spellings (--bytes=+11) and head's "all but the last N" (head -c -8), which a
# project's compress script uses to cut gzip's header and trailer. The shim reads those spellings itself and leaves
# every other call to the system's program.
_HEAD_TAIL_SHIM = '''#!{python}
import os, sys
real, name, args = "{real}", "{name}", sys.argv[1:]
out, files, i = [], [], 0
count = None  # (flag, value) of the byte or line count
while i < len(args):
    a = args[i]
    if a.startswith(("--bytes=", "--lines=")):
        count = ("-c" if a.startswith("--bytes") else "-n", a.split("=", 1)[1])
    elif a in ("--bytes", "--lines") and i + 1 < len(args):
        count = ("-c" if a == "--bytes" else "-n", args[i + 1]); i += 1
    elif a in ("-c", "-n") and i + 1 < len(args):
        count = (a, args[i + 1]); i += 1
    elif a in ("-q", "--quiet", "--silent"):
        out.append("-q")
    elif a.startswith("-") and len(a) > 1 and not a[1:].isdigit():
        out.append(a)
    elif a.startswith("-") and len(a) > 1:
        count = ("-n", a[1:])
    else:
        files.append(a)
    i += 1
if count and count[1].startswith("-") and name == "head":
    data = b"".join(open(f, "rb").read() for f in files) if files else sys.stdin.buffer.read()
    drop = int(count[1][1:])
    if count[0] == "-c":
        sys.stdout.buffer.write(data[:max(len(data) - drop, 0)])
    else:
        lines = data.splitlines(True)
        sys.stdout.buffer.write(b"".join(lines[:max(len(lines) - drop, 0)]))
    sys.exit(0)
os.execv(real, [name, *out, *(count or ()), *files])
'''
_ASSEMBLER_DRIVER = '''#!{python}
import os, subprocess, sys, tempfile
args, out, sources, cpp, asm = sys.argv[1:], None, [], [], []
link, objects = [], []
# what the real cross gcc predefines for assembler sources (headers pick register names, endianness and ABI by them)
cpp += ["-D__ASSEMBLER__", "-D_LANGUAGE_ASSEMBLY", "-D__mips__", "-D_MIPS_SZINT=32", "-D_MIPS_SZPTR=32",
        "-D_MIPS_SZLONG=32", "-D_ABIO32=1", "-D_MIPS_SIM=_ABIO32", "-D_MIPS_ARCH_MIPS3", "-D__mips=3", "-Dmips=1"]
big = True
i = 0
while i < len(args):
    a = args[i]
    if a == "-o":
        out = args[i + 1]; i += 1
    elif a in ("-x",):
        i += 1
    elif a in ("-I", "-MF", "-MT", "-MQ", "-include", "-isystem", "-iquote", "-D", "-U"):
        cpp += [a, args[i + 1]]; i += 1
        if a == "-I":
            asm += ["-I", args[i]]
    elif a == "-G":
        asm += [a, args[i + 1]]; i += 1
    elif a.startswith(("-D", "-U", "-I", "-MD", "-MMD", "-MP", "-nostdinc", "-include")):
        cpp.append(a)
        if a.startswith("-I"):  # (the assembler reads .include files too)
            asm.append(a)
    elif a.startswith(("-march=", "-mabi=", "-mtune=", "-mips", "-EB", "-EL", "-G")):
        asm.append(a)
        if a == "-EL":
            big = False
    elif a.startswith("-Wl,"):
        link += [w for w in a[4:].split(",") if w]
    elif a.startswith("-"):
        pass
    elif a.endswith((".o", ".a")):
        objects.append(a)
    else:
        sources.append(a)
    i += 1
if out and not sources and (link or objects):
    # (the gcc driver as a linker: `gcc -Wl,-T,game.ld -Wl,-Map,game.map -o game.elf [objects]` is ld's job)
    sys.exit(subprocess.run(["{linker}", *link, "-o", out, *objects]).returncode)
if not out or len(sources) != 1:
    sys.exit("mips gcc shim: needs -o and one source")
cpp = (["-D_MIPSEB", "-D__MIPSEB__", "-D__MIPSEB", "-DMIPSEB"] if big else
       ["-D_MIPSEL", "-D__MIPSEL__", "-D__MIPSEL", "-DMIPSEL"]) + cpp
with tempfile.NamedTemporaryFile("w", suffix=".s", delete=False) as t:
    # a distribution's mips gas starts .text, .data and .bss at 16-byte alignment (and pads each to it); these
    # binutils use 4, which shortens every section a ROM layout counts
    t.write(".text\\n.balign 16\\n.data\\n.balign 16\\n.bss\\n.balign 16\\n.text\\n")
    t.flush()
    done = subprocess.run(["{cpp}", "-x", "assembler-with-cpp", *cpp, sources[0]], stdout=t)
    # (and pads the end of .data and .bss to it, which these binutils only do for code)
    t.write("\\n.data\\n.balign 16\\n.bss\\n.balign 16\\n.text\\n")
if done.returncode:
    sys.exit(done.returncode)
code = subprocess.run(["{assembler}", *asm, "-o", out, t.name]).returncode
os.unlink(t.name)
if code == 0:
    # these binutils name their section symbols (".text"); a distribution's leave the name empty, and asm-processor
    # merges the symbols of two objects by name, so a second ".text" there is "defined twice"
    import struct
    with open(out, "r+b") as f:
        d = bytearray(f.read())
        if d[:4] == b"\\x7fELF" and d[4] == 1:
            e = ">" if d[5] == 2 else "<"
            shoff = struct.unpack_from(e + "I", d, 32)[0]
            shentsize, shnum = struct.unpack_from(e + "HH", d, 46)
            for k in range(shnum):
                sh = shoff + k * shentsize
                if struct.unpack_from(e + "I", d, sh + 4)[0] == 2:
                    off, size = struct.unpack_from(e + "II", d, sh + 16)
                    entsize = struct.unpack_from(e + "I", d, sh + 36)[0] or 16
                    for sym in range(off, off + size, entsize):
                        if d[sym + 12] & 15 == 3:
                            d[sym:sym + 4] = b"\\0\\0\\0\\0"
            f.seek(0)
            f.write(d)
sys.exit(code)
'''


# The cross gcc's cpp: Makefiles hand it the compile flags of the same invocation (-G0, -mips3, -mgp32 ...), which the
# host's cpp rejects, and rely on the macros the cross compiler predefines for byte order
_CPP_DRIVER = '''#!{python}
import os, sys
args = [a for a in sys.argv[1:] if not (a.startswith("-m") and a != "-m32" or a in ("-EB", "-EL")
                                        or (a.startswith("-G") and a[2:].isdigit()))]
big = "-EL" not in sys.argv[1:]
predefined = (["-D_MIPSEB", "-D__MIPSEB__", "-D__MIPSEB", "-DMIPSEB"] if big else
              ["-D_MIPSEL", "-D__MIPSEL__", "-D__MIPSEL", "-DMIPSEL"]) + ["-D__mips__", "-Dmips=1", "-D_MIPS_SZINT=32", "-D_MIPS_SZPTR=32", "-D_ABIO32=1", "-D_MIPS_SIM=_ABIO32"]
if any(a.endswith((".s", ".S")) for a in args):  # (the cross gcc defines these for assembler input)
    predefined += ["-D_LANGUAGE_ASSEMBLY", "-D__ASSEMBLER__"]
os.execv("{cpp}", ["{cpp}", *predefined, *args])
'''

# GNU sha1sum under shasum's options; its --check also reads a line with one space between the hash and the file name
# (`echo "$(cat x.sha1) build/x.bin" | sha1sum --check`), which shasum rejects
_SHA1SUM = '''#!@PYTHON@
import os, re, subprocess, sys, tempfile
args = [{"--check": "-c", "--quiet": "-q"}.get(a, a) for a in sys.argv[1:]]
temporary = None
fix = lambda text: re.sub(r"(?m)^([0-9a-fA-F]{40}) (?=[^ *])", r"\\1  ", text)
if "-c" in args:
    names = [a for a in args if not a.startswith("-")]
    if names:
        with open(names[0], errors="replace") as f, tempfile.NamedTemporaryFile("w", delete=False) as t:
            t.write(fix(f.read()))
        temporary = t.name
        args[args.index(names[0])] = temporary
        code = subprocess.run(["@SHASUM@", "-a", "1", *args]).returncode
    else:
        code = subprocess.run(["@SHASUM@", "-a", "1", *args], input=fix(sys.stdin.read()).encode()).returncode
else:
    code = subprocess.run(["@SHASUM@", "-a", "1", *args]).returncode
if temporary:
    os.unlink(temporary)
sys.exit(code)
'''


def _assembles_through_gcc(project) -> bool:
    """Do the project's makefiles run the cross gcc as an assembler driver (`-x assembler-with-cpp`)? True too when
    there is no makefile to read."""
    names = [p for pattern in ("Makefile", "Makefile.*", "*.mk", "makefile") for p in sorted(project.root.glob(pattern))]
    text = ""
    for path in names:
        try:
            text += path.read_text(errors="replace", encoding="utf-8")
        except OSError:
            pass
    # (or the assembler variable is the cross gcc itself: `AS := $(CROSS)gcc`, which takes .s files by their suffix)
    return (not text or "assembler-with-cpp" in text or "-x assembler" in text
            or bool(re.search(r"^[ \t]*(?:AS|ASM|ASSEMBLER)[ \t]*[:?]?=[ \t]*\S*gcc[ \t]*(?:#.*)?$", text, re.M)))


def _is_n64(project) -> bool:
    manifest = getattr(project, "manifest", None) or {}
    return (manifest.get("platform") or getattr(project, "platform", None)) == "n64"


_HOST_LINK_SHIM = r"""#!/bin/sh
# host link flags Apple's ld doesn't have: a static executable and GNU ld's --gc-sections; and an assembly file's global
# symbols, which Mach-O spells with a leading underscore (the C++ side asks for _name)
temp=
for a; do
  shift
  case "$a" in
    -static) ;;
    -Wl,*) set -- "$@" "$(printf '%s' "$a" | sed 's/--gc-sections/-dead_strip/g')" ;;
    *.s)
      if [ -f "$a" ]; then
        temp="$(mktemp "${{TMPDIR:-/tmp}}/hostasm.XXXXXX")" && mv "$temp" "$temp.s" && temp="$temp.s"
        cp "$a" "$temp"
        for name in $(sed -n 's/^[[:space:]]*\.glob[a]*l[[:space:]]\{{1,\}}\([A-Za-z_][A-Za-z0-9_]*\).*/\1/p' "$a"); do
          sed -E -i.bak "s/(^|[^A-Za-z0-9_.])$name([^A-Za-z0-9_]|$)/\1_$name\2/g" "$temp"
        done
        set -- "$@" "$temp"
      else
        set -- "$@" "$a"
      fi ;;
    *) set -- "$@" "$a" ;;
  esac
done
"{real}" "$@"
status=$?
[ -n "$temp" ] && rm -f "$temp" "$temp.bak"
exit $status
"""


def _host_links_statically(root: Path) -> bool:
    """Whether a Makefile under the project's tools/ links its host program the Linux way (-static, --gc-sections)."""
    try:
        for makefile in sorted((root / "tools").glob("*/Makefile"))[:40]:
            if re.search(r"^\s*\w*LDFLAGS\s*[:?+]*=[^\n]*(?:-static\b|--gc-sections)", makefile.read_text(
                    errors="replace", encoding="utf-8"), re.M):
                return True
    except OSError:
        pass
    return False


def build_paths(project) -> list[Path]:
    # On Windows these pinned tools run inside WSL, not in a native process's PATH.
    if os.name == "nt" or toolchains.installed("binutils-mips") is None:
        return []
    source = toolchains.require("binutils-mips")
    binaries = list(source.glob("mips-ps2-decompals-*"))
    if not binaries:
        return []
    folder = project.root / ".decomp-wtf" / "build-tools" / "mips-binutils"
    with _LOCK:
        folder.mkdir(parents=True, exist_ok=True)
        (folder.parent / ".gitignore").write_text("*\n", encoding="utf-8")
        for binary in binaries:
            if not binary.is_file():
                continue
            tool = binary.name.removeprefix("mips-ps2-decompals-")
            for prefix in _PREFIXES:
                alias = folder / (prefix + tool)
                if tool in ("ld", "objcopy"):
                    # these binutils know the big-endian emulation as elf32ebmip; Makefiles written for a
                    # distribution's cross ld ask for elf32btsmip (the same o32 big-endian format). Likewise objcopy
                    # (`-O elf32-tradbigmips`, which they call elf32-bigmips)
                    names = ("elf32btsmip) a=elf32ebmip;;" if tool == "ld" else
                             "elf32-tradbigmips) a=elf32-bigmips;; elf32-tradlittlemips) a=elf32-littlemips;;")
                    wrapper = (f'#!/bin/sh\nfor a; do shift; case "$a" in {names} esac; '
                               f'set -- "$@" "$a"; done\nexec "{binary.resolve()}" "$@"\n')
                    if not alias.is_file() or alias.is_symlink() or alias.read_text(errors="replace") != wrapper:
                        alias.unlink(missing_ok=True)
                        alias.write_text(wrapper, encoding="utf-8")
                        alias.chmod(0o755)
                    continue
                if tool == "objcopy":
                    # a distribution's objcopy calls the o32 formats elf32-tradbigmips / elf32-tradlittlemips; these
                    # binutils spell them elf32-bigmips / elf32-littlemips
                    wrapper = ('#!/bin/sh\nfor a; do shift; case "$a" in *elf32-tradbigmips*) a=$(printf %s "$a" | '
                               'sed s/tradbigmips/bigmips/g);; *elf32-tradlittlemips*) a=$(printf %s "$a" | '
                               'sed s/tradlittlemips/littlemips/g);; esac; set -- "$@" "$a"; done\n'
                               f'exec "{binary.resolve()}" "$@"\n')
                    if not alias.is_file() or alias.is_symlink() or alias.read_text(errors="replace") != wrapper:
                        alias.unlink(missing_ok=True)
                        alias.write_text(wrapper, encoding="utf-8")
                        alias.chmod(0o755)
                    continue
                if tool == "as" and prefix == "mips-linux-gnu-" and _is_n64(project):
                    # A distribution's mips-linux-gnu-as assembles for the o32 ABI unless told otherwise; the pinned
                    # (PS2) one does not set it, and ld then refuses `ld -r -b binary` objects beside the code
                    # ("linking 32-bit code with 64-bit code")
                    wrapper = (f'#!/bin/sh\nfor a; do case "$a" in -mabi=*) exec "{binary.resolve()}" "$@";; esac; done\n'
                               f'exec "{binary.resolve()}" -mabi=32 "$@"\n')
                    if not alias.is_file() or alias.is_symlink() or alias.read_text(errors="replace") != wrapper:
                        alias.unlink(missing_ok=True)
                        alias.write_text(wrapper, encoding="utf-8")
                        alias.chmod(0o755)
                    continue
                if alias.is_symlink() and alias.resolve() == binary.resolve():
                    continue
                temporary = folder / ("." + alias.name + ".tmp")
                temporary.unlink(missing_ok=True)
                temporary.symlink_to(binary.resolve())
                temporary.replace(alias)
        # Projects preprocess with the cross gcc's cpp, which binutils don't include: the host's cpp (clang's on a Mac)
        # does the same job, under the same prefixes; a GNU cpp (Homebrew's gcc) is preferred, as projects pass it
        # gcc-only options such as -traditional.
        gnu = sorted((m for d in ("/opt/homebrew/bin", "/usr/local/bin") for m in Path(d).glob("cpp-[0-9]*")),
                     key=lambda m: m.name)
        if host_cpp := (str(gnu[-1]) if gnu else None) or shutil.which("cpp"):
            for prefix in _PREFIXES:
                shim = folder / (prefix + "cpp")
                text = _CPP_DRIVER.format(python=sys.executable, cpp=str(host_cpp))
                if not shim.exists() or shim.read_text(errors="replace") != text:
                    shim.write_text(text, encoding="utf-8")
                    shim.chmod(0o755)
            # A Mac's own /usr/bin/cpp answers "cc: error: no input files" to `cpp ... -MD -MF x.d src.c -o /dev/null`,
            # the dependency pass many projects run after each compile: a plain `cpp` that is GNU's accepts it.
            plain = folder / "cpp"
            if gnu and not plain.exists():
                plain.write_text(f'#!/bin/sh\nexec "{gnu[-1]}" "$@"\n', encoding="utf-8")
                plain.chmod(0o755)
        # `mips-linux-gnu-gcc -x assembler-with-cpp -c x.s`: the gcc driver's one job in these builds, which binutils
        # alone can't do: preprocess with the host's cpp, then assemble with the pinned `as`
        # (only for a project whose makefiles assemble through it: one that probes for the cross gcc to compile C
        # with, and falls back to another compiler without it, would be handed an assembler for its C files)
        wanted = _assembles_through_gcc(project)
        if host_cpp:
            for prefix in _PREFIXES:
                driver = folder / (prefix + "gcc")
                if not wanted and driver.is_file() and "mips gcc shim" in driver.read_text(errors="replace"):
                    driver.unlink()
                text = _ASSEMBLER_DRIVER.format(python=sys.executable, cpp=str(host_cpp),
                                                assembler=str(folder / (prefix + "as")),
                                                linker=str(folder / (prefix + "ld")))
                if wanted and (not driver.exists() or driver.read_text(errors="replace") != text):
                    driver.write_text(text, encoding="utf-8")
                    driver.chmod(0o755)
        # A Makefile that archives with a bare `ar` (ultralib's `AR := ar`) gets macOS's ar, which does not take ELF
        # members: the pinned GNU ar does that job for ELF objects, the host's for anything else
        if sys.platform == "darwin" and (host_ar := shutil.which("ar")) and (gnu_ar := folder / "mips-linux-gnu-ar").exists():
            tool = folder / "ar"
            text = ('#!/bin/sh\nfor a; do case "$a" in *.o) [ "$(head -c 4 "$a" 2>/dev/null | od -An -c | tr -d \' \')" = "177ELF" ] '
                    f'&& exec "{gnu_ar}" "$@"; break;; esac; done\nexec "{host_ar}" "$@"\n')
            if not tool.exists() or tool.read_text(errors="replace") != text:
                tool.write_text(text, encoding="utf-8")
                tool.chmod(0o755)
        # a Makefile that runs `make` itself (MAKE = make, in tools/) gets macOS's make 3.81: GNU make 4 instead
        if sys.platform == "darwin" and (gmake := shutil.which("gmake")):
            tool = folder / "make"
            if not tool.exists():
                tool.write_text(f'#!/bin/sh\nexec "{gmake}" "$@"\n', encoding="utf-8")
                tool.chmod(0o755)
        # A plain `ar` (a Makefile that passes AR=ar to a library's build) is Apple's on a Mac, which drops ELF
        # members ("not a mach-o file") and leaves an empty archive: the pinned one takes ELF objects, Apple's
        # everything else
        if sys.platform == "darwin":
            for name in ("ar", "ranlib"):
                pinned_tool = folder / ("mips-linux-gnu-" + name)
                tool = folder / name
                if pinned_tool.exists() and not tool.exists():
                    if name == "ar":
                        body = ('for a; do if [ -f "$a" ] && [ "$(head -c4 "$a" | od -An -c | tr -d \' \')" = 177ELF ]; '
                                f'then exec "{pinned_tool}" "$@"; fi; done\nexec /usr/bin/ar "$@"\n')
                    else:  # (an archive of ELF members: the pinned one answers; any other, Apple's)
                        body = f'"{pinned_tool}" "$@" 2>/dev/null && exit 0\nexec /usr/bin/ranlib "$@"\n'
                    tool.write_text("#!/bin/sh\n" + body, encoding="utf-8")
                    tool.chmod(0o755)
        # `wibo tools/bin/cc1n64.exe ...`: Makefiles and ninja files call the Windows-program runner by name
        if toolchains.installed("wibo") is not None and not shutil.which("wibo"):
            wibo = toolchains.require("wibo") / "wibo"
            tool = folder / "wibo"
            if wibo.is_file() and not tool.exists():
                tool.write_text(f'#!/bin/sh\nexec "{wibo}" "$@"\n', encoding="utf-8")
                tool.chmod(0o755)
        # GNU coreutils' nproc (a Mac has sysctl instead), which Makefiles use to pick their -j
        if not shutil.which("nproc"):
            tool = folder / "nproc"
            if not tool.exists():
                tool.write_text('#!/bin/sh\nexec sysctl -n hw.ncpu\n', encoding="utf-8")
                tool.chmod(0o755)
        # Makefiles check the ROM with GNU coreutils' sha1sum, which a Mac lacks (shasum takes the same -c file)
        # (macOS's own /sbin/sha1sum has no --check, so the shim goes first there too)
        if (not shutil.which("sha1sum") or sys.platform == "darwin") and (shasum := shutil.which("shasum")):
            tool = folder / "sha1sum"
            if not tool.exists():
                tool.write_text(_SHA1SUM.replace("@PYTHON@", sys.executable).replace("@SHASUM@", shasum), encoding="utf-8")
                tool.chmod(0o755)
        if sys.platform == "darwin" and (iconv := shutil.which("iconv")) and Path(iconv).parent != folder:
            tool = folder / "iconv"
            if not tool.exists():
                tool.write_text(_ICONV_SHIM.format(python=sys.executable, iconv=iconv), encoding="utf-8")
                tool.chmod(0o755)
        # GNU sed where Homebrew has it (`brew install gnu-sed`): a project's own sed scripts use its labels, \x27 escapes
        # and `-i` without a suffix, none of which macOS's sed reads
        if sys.platform == "darwin" and (gsed := shutil.which("gsed")):
            tool = folder / "sed"
            if not tool.exists():
                tool.write_text(f'#!/bin/sh\nexec "{gsed}" "$@"\n', encoding="utf-8")
                tool.chmod(0o755)
        if sys.platform == "darwin" and _host_links_statically(project.root):
            # a host tool's Makefile links with `-static` and `-Wl,--gc-sections` (Linux's ld): Apple's ld has neither a
            # static executable nor that spelling (its own is -dead_strip); the shims keep the rest of the command as is
            rest = os.pathsep.join(p for p in os.environ.get("PATH", "").split(os.pathsep) if Path(p) != folder)
            for name in ("g++", "gcc", "c++", "cc"):
                real = shutil.which(name, path=rest)
                tool = folder / name
                text = _HOST_LINK_SHIM.format(real=real)
                if real and (not tool.exists() or tool.read_text(errors="replace") != text):
                    tool.write_text(text, encoding="utf-8")
                    tool.chmod(0o755)
        if sys.platform == "darwin":
            for name in ("head", "tail"):
                real = shutil.which(name)
                tool = folder / name
                if real and Path(real).parent != folder and not tool.exists():
                    tool.write_text(_HEAD_TAIL_SHIM.format(python=sys.executable, real=real, name=name), encoding="utf-8")
                    tool.chmod(0o755)
    return [folder, source]
